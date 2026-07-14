from __future__ import annotations

import json
from typing import Any, Callable

from models import QueueConsumer


class RabbitMQConsumer(QueueConsumer):
    def __init__(self, rabbitmq_url: str) -> None:
        self._rabbitmq_url = rabbitmq_url
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

        self._connection = pika.BlockingConnection(pika.URLParameters(self._rabbitmq_url))
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
            try:
                payload = _decode_payload(body)
                handle_payload(payload)
            except ValueError:
                outcomes.append((method, "invalid"))
            except Exception:
                outcomes.append((method, "error"))
            else:
                outcomes.append((method, "success"))

        for method, outcome in outcomes:
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
