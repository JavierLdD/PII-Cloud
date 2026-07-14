from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from messaging.rabbitmq import RabbitMQConsumer, RabbitMQPublisher  # noqa: E402


class FakeMethod:
    def __init__(self, delivery_tag: int):
        self.delivery_tag = delivery_tag


class FakeChannel:
    def __init__(self, bodies: list[dict] | dict[str, list[dict]]):
        self._next_delivery_tag = 1
        self.default_bodies = None
        self.bodies_by_queue = {}
        if isinstance(bodies, dict):
            self.bodies_by_queue = {
                queue_name: self._encode_bodies(queue_bodies)
                for queue_name, queue_bodies in bodies.items()
            }
        else:
            self.default_bodies = self._encode_bodies(bodies)
        self.acked: list[int] = []
        self.nacked: list[tuple[int, bool]] = []
        self.declared: list[tuple[str, bool]] = []
        self.published: list[tuple[str, str, bytes, object]] = []

    def _encode_bodies(self, bodies: list[dict]) -> list[tuple[FakeMethod, bytes]]:
        encoded = []
        for body in bodies:
            encoded.append(
                (
                    FakeMethod(self._next_delivery_tag),
                    json.dumps(body).encode("utf-8"),
                )
            )
            self._next_delivery_tag += 1
        return encoded

    def queue_declare(self, queue: str, durable: bool) -> None:
        self.declared.append((queue, durable))

    def basic_qos(self, prefetch_count: int) -> None:
        self.prefetch_count = prefetch_count

    def basic_get(self, queue: str, auto_ack: bool):
        bodies = self.bodies_by_queue.get(queue, self.default_bodies)
        if not bodies:
            return None, None, None
        method, body = bodies.pop(0)
        return method, None, body

    def basic_ack(self, delivery_tag: int) -> None:
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag: int, requeue: bool) -> None:
        self.nacked.append((delivery_tag, requeue))

    def basic_publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: object,
    ) -> None:
        self.published.append((exchange, routing_key, body, properties))


class FakeParameters:
    def __init__(self, url: str):
        self.url = url
        self.heartbeat = None
        self.blocked_connection_timeout = None


class FakeConnection:
    def __init__(self, channel: FakeChannel, close_raises: bool = False):
        self._channel = channel
        self._close_raises = close_raises
        self.is_open = True

    def channel(self) -> FakeChannel:
        return self._channel

    def close(self) -> None:
        if self._close_raises:
            raise RuntimeError("Transport indicated EOF")
        self.is_open = False


def install_fake_pika(
    monkeypatch,
    channel: FakeChannel,
    *,
    captured: dict | None = None,
    close_raises: bool = False,
) -> None:
    def blocking_connection(params):
        if captured is not None:
            captured["parameters"] = params
        return FakeConnection(channel, close_raises=close_raises)

    monkeypatch.setitem(
        sys.modules,
        "pika",
        SimpleNamespace(
            URLParameters=FakeParameters,
            BlockingConnection=blocking_connection,
            BasicProperties=lambda **kwargs: kwargs,
        ),
    )


def test_consumer_applies_text_rabbitmq_connection_tuning(monkeypatch):
    channel = FakeChannel([])
    captured = {}
    install_fake_pika(monkeypatch, channel, captured=captured)
    monkeypatch.setenv("TEXT_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS", "1200")
    monkeypatch.setenv(
        "TEXT_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS",
        "1500",
    )

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume("Queue-PDF", lambda payload: None, max_messages=0)

    parameters = captured["parameters"]
    assert parameters.url == "amqp://example"
    assert parameters.heartbeat == 1200
    assert parameters.blocked_connection_timeout == 1500


def test_publisher_applies_text_rabbitmq_connection_tuning(monkeypatch):
    channel = FakeChannel([])
    captured = {}
    install_fake_pika(monkeypatch, channel, captured=captured)
    monkeypatch.setenv("TEXT_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS", "1200")
    monkeypatch.setenv(
        "TEXT_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS",
        "1500",
    )

    publisher = RabbitMQPublisher("amqp://example")
    publisher.publish("Queue-Entity", {"ok": True})

    parameters = captured["parameters"]
    assert parameters.url == "amqp://example"
    assert parameters.heartbeat == 1200
    assert parameters.blocked_connection_timeout == 1500


def test_close_ignores_broken_connection(monkeypatch):
    channel = FakeChannel([])
    install_fake_pika(monkeypatch, channel, close_raises=True)

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume("Queue-PDF", lambda payload: None, max_messages=0)

    consumer.close()


def test_consume_batch_acks_successful_messages(monkeypatch):
    channel = FakeChannel([{"ok": True}])
    install_fake_pika(monkeypatch, channel)
    seen = []

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume(
        "Queue-PDF",
        lambda payload: seen.append(payload),
        max_messages=1,
        requeue_messages=False,
    )

    assert seen == [{"ok": True}]
    assert channel.acked == [1]
    assert channel.nacked == []


def test_dev_mode_requeues_batch_after_reading_n_messages(monkeypatch):
    channel = FakeChannel([{"n": 1}, {"n": 2}])
    install_fake_pika(monkeypatch, channel)
    seen = []

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume(
        "Queue-PDF",
        lambda payload: seen.append(payload),
        max_messages=2,
        requeue_messages=True,
    )

    assert seen == [{"n": 1}, {"n": 2}]
    assert channel.acked == []
    assert channel.nacked == [(1, True), (2, True)]


def test_consume_in_priority_order_reads_urgent_before_normal(monkeypatch):
    channel = FakeChannel(
        {
            "Queue-OCR-Urgente": [{"id": "urgent"}],
            "Queue-OCR": [{"id": "normal"}],
        }
    )
    install_fake_pika(monkeypatch, channel)
    seen = []

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume_in_priority_order(
        ("Queue-OCR-Urgente", "Queue-OCR"),
        lambda payload: seen.append(payload),
        max_messages=2,
        requeue_messages=False,
    )

    assert seen == [{"id": "urgent"}, {"id": "normal"}]
    assert channel.acked == [1, 2]
    assert channel.nacked == []


def test_consume_in_priority_order_reads_normal_when_urgent_is_empty(monkeypatch):
    channel = FakeChannel(
        {
            "Queue-OCR-Urgente": [],
            "Queue-OCR": [{"id": "normal"}],
        }
    )
    install_fake_pika(monkeypatch, channel)
    seen = []

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume_in_priority_order(
        ("Queue-OCR-Urgente", "Queue-OCR"),
        lambda payload: seen.append(payload),
        max_messages=1,
        requeue_messages=False,
    )

    assert seen == [{"id": "normal"}]
    assert channel.acked == [1]
    assert channel.nacked == []


def test_consume_in_priority_order_dev_mode_requeues_after_batch(monkeypatch):
    channel = FakeChannel(
        {
            "Queue-OCR-Urgente": [{"id": "urgent"}],
            "Queue-OCR": [{"id": "normal"}],
        }
    )
    install_fake_pika(monkeypatch, channel)
    seen = []

    consumer = RabbitMQConsumer("amqp://example")
    consumer.consume_in_priority_order(
        ("Queue-OCR-Urgente", "Queue-OCR"),
        lambda payload: seen.append(payload),
        max_messages=2,
        requeue_messages=True,
    )

    assert seen == [{"id": "urgent"}, {"id": "normal"}]
    assert channel.acked == []
    assert channel.nacked == [(1, True), (2, True)]
