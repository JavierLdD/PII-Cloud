from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_text_extract_job.errors import MessageScopeError  # noqa: E402
from cloud_text_extract_job.pubsub import (  # noqa: E402
    PubSubJsonPublisher,
    PubSubPuller,
    build_pubsub_attributes,
    validate_message_scope,
)


class FakeSubscriber:
    def __init__(self, responses):
        self.responses = list(responses)
        self.pull_requests = []
        self.acked = []
        self.nacked = []

    def pull(self, request, timeout):
        self.pull_requests.append((request, timeout))
        return self.responses.pop(0)

    def acknowledge(self, request):
        self.acked.append(request)

    def modify_ack_deadline(self, request):
        self.nacked.append(request)


class FakeFuture:
    def __init__(self, message_id: str):
        self.message_id = message_id
        self.timeouts = []

    def result(self, timeout):
        self.timeouts.append(timeout)
        return self.message_id


class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, topic_name, data, **attributes):
        self.published.append((topic_name, json.loads(data.decode("utf-8")), attributes))
        return FakeFuture("pubsub-message-1")


def response_for(received_messages):
    return SimpleNamespace(received_messages=received_messages)


def received_message(
    *,
    ack_id: str = "ack-1",
    payload: dict[str, object] | None = None,
    attributes: dict[str, str] | None = None,
):
    payload = payload or {"event_type": "file.routed", "run_id": "run-1"}
    message = SimpleNamespace(
        data=json.dumps(payload).encode("utf-8"),
        attributes=attributes or {},
        message_id="message-1",
    )
    return SimpleNamespace(ack_id=ack_id, message=message)


def test_pull_empty_returns_none():
    subscriber = FakeSubscriber([response_for([])])
    puller = PubSubPuller(subscriber)

    assert puller.pull_one("subscriptions/text", timeout_seconds=5) is None
    assert subscriber.pull_requests == [
        (
            {
                "subscription": "subscriptions/text",
                "max_messages": 1,
                "return_immediately": True,
            },
            5,
        )
    ]


def test_pull_decodes_json_payload_and_attributes_then_ack_nack():
    subscriber = FakeSubscriber(
        [
            response_for(
                [
                    received_message(
                        ack_id="ack-42",
                        payload={"event_type": "file.routed", "run_id": "run-1"},
                        attributes={"user_id": "user-1", "run_id": "run-1"},
                    )
                ]
            )
        ]
    )
    puller = PubSubPuller(subscriber)

    pulled = puller.pull_one("subscriptions/text", timeout_seconds=5)
    assert pulled is not None
    assert pulled.ack_id == "ack-42"
    assert pulled.payload["event_type"] == "file.routed"
    assert pulled.attributes == {"user_id": "user-1", "run_id": "run-1"}
    assert pulled.message_id == "message-1"

    puller.ack("subscriptions/text", pulled.ack_id)
    puller.nack("subscriptions/text", pulled.ack_id)

    assert subscriber.acked == [
        {"subscription": "subscriptions/text", "ack_ids": ["ack-42"]}
    ]
    assert subscriber.nacked == [
        {
            "subscription": "subscriptions/text",
            "ack_ids": ["ack-42"],
            "ack_deadline_seconds": 0,
        }
    ]


def test_publisher_sends_json_payload_with_attributes():
    publisher = FakePublisher()
    adapter = PubSubJsonPublisher(publisher, timeout_seconds=7)

    message_id = adapter.publish_json(
        "projects/pii/topics/pii-entities",
        {"event_type": "file.chunks_ready", "run_id": "run-1"},
        {"user_id": "user-1", "run_id": "run-1"},
    )

    assert message_id == "pubsub-message-1"
    assert publisher.published == [
        (
            "projects/pii/topics/pii-entities",
            {"event_type": "file.chunks_ready", "run_id": "run-1"},
            {"user_id": "user-1", "run_id": "run-1"},
        )
    ]


def test_validate_message_scope_checks_attributes_and_payload_run():
    validate_message_scope(
        {"run_id": "run-1"},
        {"user_id": "user-1", "run_id": "run-1"},
        expected_user_id="user-1",
        expected_run_id="run-1",
    )

    try:
        validate_message_scope(
            {"run_id": "run-2"},
            {"user_id": "user-1", "run_id": "run-1"},
            expected_user_id="user-1",
            expected_run_id="run-1",
        )
    except MessageScopeError as exc:
        assert "Unexpected payload run_id" in str(exc)
    else:
        raise AssertionError("expected MessageScopeError")


def test_build_pubsub_attributes_includes_routing_fields():
    attributes = build_pubsub_attributes(
        {
            "schema_version": "2.0",
            "event_type": "file.chunks_ready",
            "file_id": "file-1",
            "routing_decision_id": "route-1",
            "source_type": "drive",
            "destination_queue_name": "Queue-Entity",
        },
        user_id="user-1",
        run_id="run-1",
    )

    assert attributes == {
        "user_id": "user-1",
        "run_id": "run-1",
        "schema_version": "2.0",
        "event_type": "file.chunks_ready",
        "file_id": "file-1",
        "source_type": "drive",
        "destination_queue_name": "Queue-Entity",
        "routing_decision_id": "route-1",
    }
