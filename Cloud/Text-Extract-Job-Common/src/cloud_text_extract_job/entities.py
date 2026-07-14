from __future__ import annotations

from common.models import QUEUE_ENTITY, TextExtractionRepository

from .outbox import PubSubOutboxRepository
from .pubsub import PubSubJsonPublisher, build_pubsub_attributes


def publish_pending_entity_outbox(
    *,
    repository: TextExtractionRepository,
    outbox_repository: PubSubOutboxRepository,
    publisher: PubSubJsonPublisher,
    topic_name: str,
    user_id: str,
    run_id: str,
    file_id: str,
) -> int:
    published_count = 0
    for message in repository.list_pending_outbox(QUEUE_ENTITY):
        payload_run_id = str(message.payload.get("run_id") or "")
        payload_file_id = str(message.payload.get("file_id") or "")
        if payload_run_id != run_id or payload_file_id != file_id:
            continue
        attributes = build_pubsub_attributes(
            message.payload,
            user_id=user_id,
            run_id=run_id,
        )
        try:
            pubsub_message_id = publisher.publish_json(
                topic_name,
                message.payload,
                attributes,
            )
        except Exception as exc:
            outbox_repository.record_error(message.outbox_id, str(exc))
            raise
        outbox_repository.mark_published(
            message.outbox_id,
            pubsub_message_id=pubsub_message_id,
            attributes=attributes,
        )
        published_count += 1
    return published_count
