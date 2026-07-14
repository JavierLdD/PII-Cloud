from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from .errors import MessageScopeError


@dataclass(frozen=True)
class PulledMessage:
    ack_id: str
    payload: dict[str, Any]
    attributes: dict[str, str]
    message_id: str | None = None


class PubSubPuller:
    def __init__(self, subscriber: Any | None = None) -> None:
        if subscriber is None:
            from google.cloud import pubsub_v1

            subscriber = pubsub_v1.SubscriberClient()
        self._subscriber = subscriber

    def pull_one(
        self,
        subscription_id: str,
        timeout_seconds: int,
    ) -> PulledMessage | None:
        response = self._subscriber.pull(
            request={
                "subscription": subscription_id,
                "max_messages": 1,
                "return_immediately": True,
            },
            timeout=timeout_seconds,
        )
        if not response.received_messages:
            return None
        received = response.received_messages[0]
        message = received.message
        try:
            payload = json.loads(message.data.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid Pub/Sub JSON payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Pub/Sub payload must be a JSON object")
        return PulledMessage(
            ack_id=received.ack_id,
            payload=payload,
            attributes=dict(message.attributes),
            message_id=getattr(message, "message_id", None),
        )

    def ack(self, subscription_id: str, ack_id: str) -> None:
        self._subscriber.acknowledge(
            request={"subscription": subscription_id, "ack_ids": [ack_id]}
        )

    def nack(self, subscription_id: str, ack_id: str) -> None:
        self._subscriber.modify_ack_deadline(
            request={
                "subscription": subscription_id,
                "ack_ids": [ack_id],
                "ack_deadline_seconds": 0,
            }
        )


class PubSubJsonPublisher:
    def __init__(self, publisher: Any | None = None, timeout_seconds: int = 60) -> None:
        if publisher is None:
            from google.cloud import pubsub_v1

            publisher = pubsub_v1.PublisherClient()
        self._publisher = publisher
        self._timeout_seconds = timeout_seconds

    def publish_json(
        self,
        topic_name: str,
        payload: Mapping[str, Any],
        attributes: Mapping[str, str],
    ) -> str:
        future = self._publisher.publish(
            topic_name,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            **{key: str(value) for key, value in attributes.items()},
        )
        return str(future.result(timeout=self._timeout_seconds))


def validate_message_scope(
    payload: Mapping[str, Any],
    attributes: Mapping[str, str],
    *,
    expected_user_id: str,
    expected_run_id: str,
) -> None:
    attr_user_id = str(attributes.get("user_id") or "").strip()
    attr_run_id = str(attributes.get("run_id") or "").strip()
    payload_run_id = str(payload.get("run_id") or "").strip()
    if attr_user_id != expected_user_id:
        raise MessageScopeError(
            f"Unexpected user_id: attributes.user_id={attr_user_id!r}"
        )
    if attr_run_id != expected_run_id:
        raise MessageScopeError(
            f"Unexpected run_id: attributes.run_id={attr_run_id!r}"
        )
    if payload_run_id != expected_run_id:
        raise MessageScopeError(f"Unexpected payload run_id: {payload_run_id!r}")


def build_pubsub_attributes(
    payload: Mapping[str, Any],
    *,
    user_id: str,
    run_id: str,
) -> dict[str, str]:
    attributes = {
        "user_id": user_id,
        "run_id": run_id,
    }
    for key in (
        "schema_version",
        "event_type",
        "file_id",
        "source_type",
        "destination_queue_name",
        "routing_decision_id",
    ):
        value = payload.get(key)
        if value is not None and str(value).strip():
            attributes[key] = str(value)
    return attributes
