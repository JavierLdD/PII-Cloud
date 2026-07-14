import json
import sys
from types import SimpleNamespace

import pytest

from table_extract.materialization import (
    DrivePermissionError,
    MaterializationDeferred,
    PermanentMaterializationError,
)
from table_extract.messaging import (
    RabbitMQConsumer,
    decode_payload,
    is_non_retryable_exception,
)


class FakeMethod:
    def __init__(self, delivery_tag: int) -> None:
        self.delivery_tag = delivery_tag


class FakeChannel:
    def __init__(self, deliveries=()) -> None:
        self.deliveries = list(deliveries)
        self.acks: list[int] = []
        self.nacks: list[tuple[int, bool]] = []

    def basic_get(self, queue, auto_ack):
        if not self.deliveries:
            return None, None, None
        return self.deliveries.pop(0)

    def basic_ack(self, delivery_tag):
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        self.nacks.append((delivery_tag, requeue))

    def queue_declare(self, queue, durable):
        self.declared = (queue, durable)

    def basic_qos(self, prefetch_count):
        self.prefetch_count = prefetch_count


def test_decode_payload_requires_json_object() -> None:
    assert decode_payload(json.dumps({"ok": True}).encode("utf-8")) == {"ok": True}

    with pytest.raises(ValueError, match="JSON object"):
        decode_payload(json.dumps(["not", "object"]).encode("utf-8"))


def test_consumer_applies_table_rabbitmq_connection_tuning(monkeypatch) -> None:
    captured = {}

    class FakeParameters:
        def __init__(self, url):
            self.url = url
            self.heartbeat = None
            self.blocked_connection_timeout = None

    class FakeConnection:
        def __init__(self, parameters):
            captured["parameters"] = parameters
            self.is_open = True

        def channel(self):
            return FakeChannel()

        def close(self):
            self.is_open = False

    monkeypatch.setitem(
        sys.modules,
        "pika",
        SimpleNamespace(
            URLParameters=FakeParameters,
            BlockingConnection=FakeConnection,
        ),
    )
    monkeypatch.setenv("TABLE_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS", "1200")
    monkeypatch.setenv(
        "TABLE_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS",
        "1500",
    )

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume("Queue-Tables", lambda payload: None, max_messages=0)

    parameters = captured["parameters"]
    assert parameters.url == "amqp://example"
    assert parameters.heartbeat == 1200
    assert parameters.blocked_connection_timeout == 1500


def test_non_retryable_exception_classification() -> None:
    assert is_non_retryable_exception(ValueError("bad payload"))
    assert is_non_retryable_exception(PermanentMaterializationError("bad source"))
    assert is_non_retryable_exception(DrivePermissionError("appNotAuthorizedToFile"))
    assert not is_non_retryable_exception(RuntimeError("temporary failure"))
    assert not is_non_retryable_exception(MaterializationDeferred("retry later"))


def test_streaming_message_success_acks_and_logs(capsys) -> None:
    consumer = RabbitMQConsumer("amqp://example")
    channel = FakeChannel()
    method = FakeMethod(101)
    body = json.dumps({"file_id": "file-001"}).encode("utf-8")

    consumer._consume_stream_message(
        channel,
        "Queue-Tables",
        lambda payload: None,
        method,
        body,
    )

    log = json.loads(capsys.readouterr().err)
    assert channel.acks == [101]
    assert channel.nacks == []
    assert log["event"] == "queue_message_processed"
    assert log["safe_context"]["outcome"] == "success"
    assert log["safe_context"]["file_id"] == "file-001"


@pytest.mark.parametrize(
    ("exc", "expected_requeue", "expected_category"),
    [
        (MaterializationDeferred("budget unavailable"), True, "materialization_deferred"),
        (ValueError("bad payload"), False, "invalid_payload"),
        (PermanentMaterializationError("bad source"), False, "materialization_permanent"),
        (DrivePermissionError("appNotAuthorizedToFile secret-token"), False, "drive_permission_denied"),
    ],
)
def test_streaming_message_failure_nacks_and_logs(
    exc,
    expected_requeue,
    expected_category,
    capsys,
) -> None:
    consumer = RabbitMQConsumer("amqp://example")
    channel = FakeChannel()
    method = FakeMethod(102)
    body = json.dumps({"file_id": "file-001"}).encode("utf-8")

    def fail(payload):
        raise exc

    consumer._consume_stream_message(channel, "Queue-Tables", fail, method, body)

    log = json.loads(capsys.readouterr().err)
    assert channel.acks == []
    assert channel.nacks == [(102, expected_requeue)]
    assert log["event"] == "queue_message_failed"
    assert log["category"] == expected_category
    assert log["retryable"] is expected_requeue
    assert log["safe_context"]["file_id"] == "file-001"
    assert "secret-token" not in json.dumps(log)


def test_batch_success_dev_mode_requeues_and_logs(capsys) -> None:
    consumer = RabbitMQConsumer("amqp://example")
    method = FakeMethod(201)
    body = json.dumps({"file_id": "file-001"}).encode("utf-8")
    channel = FakeChannel([(method, None, body)])
    consumer._channel = channel

    consumer._consume_batch(
        "Queue-Tables",
        lambda payload: None,
        max_messages=1,
        requeue_messages=True,
    )

    log = json.loads(capsys.readouterr().err)
    assert channel.acks == []
    assert channel.nacks == [(201, True)]
    assert log["event"] == "queue_message_processed"
    assert log["safe_context"]["outcome"] == "dev_requeued"


def test_batch_retryable_failure_requeues_and_logs(capsys) -> None:
    consumer = RabbitMQConsumer("amqp://example")
    method = FakeMethod(202)
    body = json.dumps({"file_id": "file-001"}).encode("utf-8")
    channel = FakeChannel([(method, None, body)])
    consumer._channel = channel

    def fail(payload):
        raise MaterializationDeferred("retry later")

    consumer._consume_batch(
        "Queue-Tables",
        fail,
        max_messages=1,
        requeue_messages=False,
    )

    log = json.loads(capsys.readouterr().err)
    assert channel.acks == []
    assert channel.nacks == [(202, True)]
    assert log["event"] == "queue_message_failed"
    assert log["safe_context"]["outcome"] == "retryable"
