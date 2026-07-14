from __future__ import annotations

import json
import os
from typing import Any, Callable, Protocol

from table_extract.operational import (
    OperationalErrorInfo,
    classify_operational_exception,
    emit_operational_log,
)


DEFAULT_RABBITMQ_HEARTBEAT_SECONDS = 1800
DEFAULT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS = 1800


class QueueConsumer(Protocol):
    def consume(
        self,
        queue_name: str,
        handle_payload: Callable[[dict[str, Any]], None],
        max_messages: int | None = None,
        requeue_messages: bool = False,
    ) -> None:
        ...


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
                "TABLE_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS",
                DEFAULT_RABBITMQ_HEARTBEAT_SECONDS,
            )
        )
        self._blocked_connection_timeout_seconds = (
            blocked_connection_timeout_seconds
            if blocked_connection_timeout_seconds is not None
            else _int_env(
                "TABLE_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS",
                DEFAULT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS,
            )
        )
        self._connection: Any = None
        self._channel: Any = None

    def close(self) -> None:
        if self._connection and self._connection.is_open:
            self._connection.close()

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

        parameters = pika.URLParameters(self._rabbitmq_url)
        parameters.heartbeat = self._heartbeat_seconds
        parameters.blocked_connection_timeout = self._blocked_connection_timeout_seconds
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()
        self._channel.queue_declare(queue=queue_name, durable=True)
        self._channel.basic_qos(prefetch_count=1)

        if max_messages is not None:
            self._consume_batch(queue_name, handle_payload, max_messages, requeue_messages)
            return

        def on_message(channel: Any, method: Any, properties: Any, body: bytes) -> None:
            self._consume_stream_message(
                channel,
                queue_name,
                handle_payload,
                method,
                body,
            )

        self._channel.basic_consume(queue=queue_name, on_message_callback=on_message)
        self._channel.start_consuming()

    def _consume_stream_message(
        self,
        channel: Any,
        queue_name: str,
        handle_payload: Callable[[dict[str, Any]], None],
        method: Any,
        body: bytes,
    ) -> None:
        payload: dict[str, Any] | None = None
        try:
            payload = decode_payload(body)
            handle_payload(payload)
        except Exception as exc:
            info = classify_operational_exception(
                exc,
                default_component="rabbitmq",
                safe_context=_delivery_context(queue_name, method, payload),
            )
            requeue = info.retryable
            emit_operational_log(
                "queue_message_failed",
                info,
                safe_context={"outcome": "retryable" if requeue else "non_retryable"},
            )
            channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=requeue,
            )
            return

        _emit_queue_success(queue_name, method, payload, outcome="success")
        channel.basic_ack(delivery_tag=method.delivery_tag)

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

        outcomes: list[tuple[Any, dict[str, Any] | None, str, OperationalErrorInfo | None]] = []
        for method, body in deliveries:
            payload = None
            try:
                payload = decode_payload(body)
                handle_payload(payload)
            except Exception as exc:
                info = classify_operational_exception(
                    exc,
                    default_component="rabbitmq",
                    safe_context=_delivery_context(queue_name, method, payload),
                )
                if info.retryable:
                    outcomes.append((method, payload, "retryable", info))
                else:
                    outcomes.append((method, payload, "non_retryable", info))
            else:
                outcomes.append((method, payload, "success", None))

        for method, payload, outcome, info in outcomes:
            if outcome == "success":
                if requeue_messages:
                    _emit_queue_success(
                        queue_name,
                        method,
                        payload,
                        outcome="dev_requeued",
                    )
                    self._channel.basic_nack(
                        delivery_tag=method.delivery_tag,
                        requeue=True,
                    )
                else:
                    _emit_queue_success(queue_name, method, payload, outcome="success")
                    self._channel.basic_ack(delivery_tag=method.delivery_tag)
            elif outcome == "non_retryable":
                emit_operational_log(
                    "queue_message_failed",
                    info,
                    safe_context={"outcome": "non_retryable"},
                )
                self._channel.basic_nack(
                    delivery_tag=method.delivery_tag,
                    requeue=False,
                )
            else:
                emit_operational_log(
                    "queue_message_failed",
                    info,
                    safe_context={"outcome": "retryable"},
                )
                self._channel.basic_nack(
                    delivery_tag=method.delivery_tag,
                    requeue=True,
                )


def decode_payload(body: bytes) -> dict[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Message body must be a JSON object")
    return payload


def is_non_retryable_exception(exc: Exception) -> bool:
    return not classify_operational_exception(exc).retryable


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


def _delivery_context(
    queue_name: str,
    method: Any,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    context = {
        "queue": queue_name,
        "delivery_tag": getattr(method, "delivery_tag", None),
    }
    if payload is not None:
        file_id = payload.get("file_id")
        if file_id is not None:
            context["file_id"] = file_id
    return context


def _emit_queue_success(
    queue_name: str,
    method: Any,
    payload: dict[str, Any] | None,
    *,
    outcome: str,
) -> None:
    emit_operational_log(
        "queue_message_processed",
        OperationalErrorInfo(
            component="rabbitmq",
            category="success",
            retryable=False,
            message="Queue message processed.",
        ),
        safe_context={
            **_delivery_context(queue_name, method, payload),
            "outcome": outcome,
        },
    )
