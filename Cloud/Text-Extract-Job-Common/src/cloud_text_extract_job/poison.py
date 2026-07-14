from __future__ import annotations

from typing import Any, Mapping

from common.models import (
    QUEUE_TEXT_POISON,
    StoredFile,
    build_text_extract_poison_payload,
)

from .outbox import PendingOutboxRecord, PubSubOutboxRepository
from .pubsub import PubSubJsonPublisher, build_pubsub_attributes


def build_poison_payload_from_stored_file(
    *,
    run_id: str,
    file_id: str,
    routing_decision_id: str,
    stored_file: StoredFile,
    source_queue_name: str,
    stage: str,
    reason: str,
    error: str,
) -> dict[str, Any]:
    return build_text_extract_poison_payload(
        run_id=run_id,
        file_id=file_id,
        routing_decision_id=routing_decision_id,
        stored_file=stored_file,
        source_queue_name=source_queue_name,
        stage=stage,
        reason=reason,
        error=error,
    )


def build_poison_payload_from_message(
    *,
    payload: Mapping[str, Any],
    stage: str,
    reason: str,
    error: str,
) -> dict[str, Any]:
    return {
        "schema_version": str(payload.get("schema_version") or "2.0"),
        "event_type": "file.text_extract_poisoned",
        "run_id": str(payload.get("run_id") or ""),
        "file_id": str(payload.get("file_id") or ""),
        "routing_decision_id": str(payload.get("routing_decision_id") or ""),
        "source_type": str(payload.get("source_type") or ""),
        "source_uri": str(payload.get("source_uri") or ""),
        "external_id": payload.get("external_id"),
        "file_name": str(payload.get("file_name") or ""),
        "relative_path": str(payload.get("relative_path") or ""),
        "extension": str(payload.get("extension") or ""),
        "mime_type": payload.get("mime_type"),
        "checksum_sha256": payload.get("checksum_sha256"),
        "content_hash": payload.get("content_hash"),
        "etag": payload.get("etag"),
        "size_bytes": payload.get("size_bytes"),
        "source_queue_name": str(payload.get("destination_queue_name") or ""),
        "destination_queue_name": QUEUE_TEXT_POISON,
        "stage": stage,
        "reason": reason,
        "error": error,
    }


def record_and_publish_poison(
    *,
    outbox_repository: PubSubOutboxRepository,
    publisher: PubSubJsonPublisher,
    topic_name: str,
    payload: Mapping[str, Any],
    user_id: str,
    run_id: str,
) -> str | None:
    attributes = build_pubsub_attributes(payload, user_id=user_id, run_id=run_id)
    idempotency_key = _poison_idempotency_key(payload)
    record = outbox_repository.insert_pending(
        run_id=str(payload.get("run_id") or run_id),
        file_id=str(payload.get("file_id") or "00000000-0000-0000-0000-000000000000"),
        queue_name=QUEUE_TEXT_POISON,
        payload=payload,
        idempotency_key=idempotency_key,
        attributes=attributes,
    )
    if record.status == "published":
        return None
    try:
        message_id = publisher.publish_json(topic_name, payload, attributes)
    except Exception as exc:
        outbox_repository.record_error(record.outbox_id, str(exc))
        raise
    outbox_repository.mark_published(
        record.outbox_id,
        pubsub_message_id=message_id,
        attributes=attributes,
    )
    return message_id


def _poison_idempotency_key(payload: Mapping[str, Any]) -> str:
    return (
        "file.text_extract_poisoned:"
        f"{payload.get('run_id')}:"
        f"{payload.get('file_id')}:"
        f"{payload.get('routing_decision_id')}:"
        f"{payload.get('stage')}:"
        f"{payload.get('reason')}"
    )
