from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Iterable

from common.models import QueuePublisher


DEFAULT_RABBITMQ_HEARTBEAT_SECONDS = 1800
DEFAULT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS = 1800


class RabbitMQPublisher(QueuePublisher):
    def __init__(
        self,
        rabbitmq_url: str,
        *,
        heartbeat_seconds: int | None = None,
        blocked_connection_timeout_seconds: int | None = None,
    ) -> None:
        self._rabbitmq_url = rabbitmq_url
        self._heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else _int_env(
                "TEXT_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS",
                DEFAULT_RABBITMQ_HEARTBEAT_SECONDS,
            )
        )
        self._blocked_connection_timeout_seconds = (
            blocked_connection_timeout_seconds
            if blocked_connection_timeout_seconds is not None
            else _int_env(
                "TEXT_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS",
                DEFAULT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS,
            )
        )
        self._connection: Any = None
        self._channel: Any = None

    def close(self) -> None:
        _close_connection(self._connection)

    def _ensure_channel(self, queue_name: str) -> Any:
        if self._channel and self._channel.is_open:
            return self._channel

        try:
            import pika
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: install pika with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        self._connection = pika.BlockingConnection(
            _connection_parameters(
                pika,
                self._rabbitmq_url,
                self._heartbeat_seconds,
                self._blocked_connection_timeout_seconds,
            )
        )
        self._channel = self._connection.channel()
        self._channel.queue_declare(queue=queue_name, durable=True)
        return self._channel

    def publish(self, queue_name: str, payload: dict[str, Any]) -> None:
        try:
            import pika
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: install pika with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        channel = self._ensure_channel(queue_name)
        channel.queue_declare(queue=queue_name, durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=json.dumps(payload, sort_keys=True).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
            ),
        )


class RabbitMQConsumer:
    def __init__(
        self,
        rabbitmq_url: str,
        *,
        heartbeat_seconds: int | None = None,
        blocked_connection_timeout_seconds: int | None = None,
    ) -> None:
        self._rabbitmq_url = rabbitmq_url
        self._heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else _int_env(
                "TEXT_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS",
                DEFAULT_RABBITMQ_HEARTBEAT_SECONDS,
            )
        )
        self._blocked_connection_timeout_seconds = (
            blocked_connection_timeout_seconds
            if blocked_connection_timeout_seconds is not None
            else _int_env(
                "TEXT_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS",
                DEFAULT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS,
            )
        )
        self._connection: Any = None
        self._channel: Any = None

    def close(self) -> None:
        _close_connection(self._connection)

    def consume(
        self,
        queue_name: str,
        handle_payload: Callable[[dict[str, Any]], None],
        max_messages: int | None = None,
        requeue_messages: bool = False,
    ) -> None:
        try:
            import pika
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: install pika with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        self._connection = pika.BlockingConnection(
            _connection_parameters(
                pika,
                self._rabbitmq_url,
                self._heartbeat_seconds,
                self._blocked_connection_timeout_seconds,
            )
        )
        self._channel = self._connection.channel()
        self._channel.queue_declare(queue=queue_name, durable=True)
        self._channel.basic_qos(prefetch_count=1)

        if max_messages is not None:
            self._consume_batch(queue_name, handle_payload, max_messages, requeue_messages)
            return

        def on_message(channel: Any, method: Any, properties: Any, body: bytes) -> None:
            try:
                payload = _decode_payload(body)
                handle_payload(payload)
            except ValueError:
                channel.basic_nack(
                    delivery_tag=method.delivery_tag,
                    requeue=False,
                )
                return
            except Exception:
                channel.basic_nack(
                    delivery_tag=method.delivery_tag,
                    requeue=True,
                )
                return

            channel.basic_ack(delivery_tag=method.delivery_tag)

        self._channel.basic_consume(
            queue=queue_name,
            on_message_callback=on_message,
        )
        self._channel.start_consuming()

    def consume_in_priority_order(
        self,
        queue_names: Iterable[str],
        handle_payload: Callable[[dict[str, Any]], None],
        max_messages: int | None = None,
        requeue_messages: bool = False,
    ) -> None:
        try:
            import pika
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: install pika with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        ordered_queue_names = tuple(dict.fromkeys(queue_names))
        if not ordered_queue_names:
            raise ValueError("At least one queue_name is required")

        self._connection = pika.BlockingConnection(
            _connection_parameters(
                pika,
                self._rabbitmq_url,
                self._heartbeat_seconds,
                self._blocked_connection_timeout_seconds,
            )
        )
        self._channel = self._connection.channel()
        for queue_name in ordered_queue_names:
            self._channel.queue_declare(queue=queue_name, durable=True)
        self._channel.basic_qos(prefetch_count=1)

        if max_messages is not None:
            self._consume_ordered_batch(
                ordered_queue_names,
                handle_payload,
                max_messages,
                requeue_messages,
            )
            return

        while True:
            consumed = self._consume_next_available(
                ordered_queue_names,
                handle_payload,
                requeue_messages,
            )
            if not consumed:
                time.sleep(1.0)

    def _consume_batch(
        self,
        queue_name: str,
        handle_payload: Callable[[dict[str, Any]], None],
        max_messages: int,
        requeue_messages: bool,
    ) -> None:
        deliveries: list[tuple[Any, bytes]] = []
        for _ in range(max(max_messages, 0)):
            method, properties, body = self._channel.basic_get(
                queue=queue_name,
                auto_ack=False,
            )
            if method is None:
                break
            deliveries.append((method, body))

        outcomes: list[tuple[Any, str]] = []
        for method, body in deliveries:
            outcomes.append((method, self._handle_body(handle_payload, body)))

        for method, outcome in outcomes:
            self._settle_delivery(method, outcome, requeue_messages)

    def _consume_ordered_batch(
        self,
        queue_names: tuple[str, ...],
        handle_payload: Callable[[dict[str, Any]], None],
        max_messages: int,
        requeue_messages: bool,
    ) -> None:
        deliveries: list[tuple[Any, bytes]] = []
        for _ in range(max(max_messages, 0)):
            delivery = self._get_next_available_delivery(queue_names)
            if delivery is None:
                break
            deliveries.append(delivery)

        outcomes: list[tuple[Any, str]] = []
        for method, body in deliveries:
            outcomes.append((method, self._handle_body(handle_payload, body)))

        for method, outcome in outcomes:
            self._settle_delivery(method, outcome, requeue_messages)

    def _consume_next_available(
        self,
        queue_names: tuple[str, ...],
        handle_payload: Callable[[dict[str, Any]], None],
        requeue_messages: bool,
    ) -> bool:
        delivery = self._get_next_available_delivery(queue_names)
        if delivery is None:
            return False

        method, body = delivery
        outcome = self._handle_body(handle_payload, body)
        self._settle_delivery(method, outcome, requeue_messages)
        return True

    def _get_next_available_delivery(
        self,
        queue_names: tuple[str, ...],
    ) -> tuple[Any, bytes] | None:
        for queue_name in queue_names:
            method, properties, body = self._channel.basic_get(
                queue=queue_name,
                auto_ack=False,
            )
            if method is not None:
                return method, body
        return None

    def _handle_body(
        self,
        handle_payload: Callable[[dict[str, Any]], None],
        body: bytes,
    ) -> str:
        try:
            payload = _decode_payload(body)
            handle_payload(payload)
        except ValueError:
            return "invalid"
        except Exception:
            return "error"
        return "success"

    def _settle_delivery(
        self,
        method: Any,
        outcome: str,
        requeue_messages: bool,
    ) -> None:
        if requeue_messages:
            self._channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=True,
            )
        elif outcome == "success":
            self._channel.basic_ack(delivery_tag=method.delivery_tag)
        elif outcome == "invalid":
            self._channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=False,
            )
        else:
            self._channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=True,
            )


def _decode_payload(body: bytes) -> dict[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Message body must be a JSON object")
    return payload


def _connection_parameters(
    pika_module: Any,
    rabbitmq_url: str,
    heartbeat_seconds: int,
    blocked_connection_timeout_seconds: int,
) -> Any:
    parameters = pika_module.URLParameters(rabbitmq_url)
    parameters.heartbeat = heartbeat_seconds
    parameters.blocked_connection_timeout = blocked_connection_timeout_seconds
    return parameters


def _close_connection(connection: Any) -> None:
    if not connection:
        return
    try:
        if connection.is_open:
            connection.close()
    except Exception:
        # Cleanup should not mask KeyboardInterrupt or the original worker failure.
        return


def _int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")
    return value
