from __future__ import annotations

from datetime import UTC, datetime
import logging
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[4]
TEXT_EXTRACT_DIR = PROJECT_DIR / "Text_Extract"
COMMON_DIR = PROJECT_DIR / "Cloud" / "Text-Extract-Job-Common" / "src"
for path in (TEXT_EXTRACT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cloud_text_extract_job.config import (  # noqa: E402
    TextExtractJobConfig,
    ensure_scratch_dir,
)
from cloud_text_extract_job.entities import publish_pending_entity_outbox  # noqa: E402
from cloud_text_extract_job.errors import (  # noqa: E402
    MessageScopeError,
    TransientProcessingError,
)
from cloud_text_extract_job.outbox import PubSubOutboxRepository  # noqa: E402
from cloud_text_extract_job.poison import (  # noqa: E402
    build_poison_payload_from_message,
    build_poison_payload_from_stored_file,
    record_and_publish_poison,
)
from cloud_text_extract_job.pubsub import (  # noqa: E402
    PulledMessage,
    PubSubJsonPublisher,
    PubSubPuller,
    validate_message_scope,
)
from cloud_text_extract_job.runner import drain_subscription  # noqa: E402
from cloud_text_extract_job.timeout import (  # noqa: E402
    FileProcessingTimeout,
    per_file_timeout,
)
from common.models import (  # noqa: E402
    DOC_METHOD,
    PAGE_FAILED_STATUS,
    TEXT_EXTRACTION_COMPLETED_STATUS,
    TEXT_EXTRACTION_FAILED_STATUS,
    DocProcessingResult,
    DocRoutedMessage,
    PdfPageResult,
)
from docs.worker import process_doc_payload  # noqa: E402
from materialization.models import MaterializationDeferred  # noqa: E402
from materialization.service import build_file_materializer  # noqa: E402
from staging.adapters import PostgresTextExtractionRepository  # noqa: E402


LOGGER = logging.getLogger("cloud_text_docs_extract_job")


def main() -> int:
    _configure_logging()
    config = TextExtractJobConfig.from_env(os.environ)
    config.apply_runtime_defaults()
    ensure_scratch_dir(config.scratch_dir)

    with PostgresTextExtractionRepository(config.database_url) as repository:
        with PubSubOutboxRepository(config.database_url) as outbox_repository:
            materializer = build_file_materializer(repository)
            publisher = PubSubJsonPublisher()
            puller = PubSubPuller()

            processed = drain_subscription(
                config=config,
                puller=puller,
                handle_message=lambda message: handle_doc_message(
                    message=message,
                    config=config,
                    repository=repository,
                    outbox_repository=outbox_repository,
                    publisher=publisher,
                    materializer=materializer,
                ),
            )

    LOGGER.info("docs_extract_job_finished processed=%s", processed)
    return 0


def handle_doc_message(
    *,
    message: PulledMessage,
    config: TextExtractJobConfig,
    repository: PostgresTextExtractionRepository,
    outbox_repository: PubSubOutboxRepository,
    publisher: PubSubJsonPublisher,
    materializer,
) -> None:
    try:
        validate_message_scope(
            message.payload,
            message.attributes,
            expected_user_id=config.expected_user_id,
            expected_run_id=config.expected_run_id,
        )
    except MessageScopeError as exc:
        _publish_direct_poison(
            config=config,
            publisher=publisher,
            payload=build_poison_payload_from_message(
                payload=message.payload,
                stage="docs",
                reason="message_scope_mismatch",
                error=str(exc),
            ),
        )
        return

    try:
        DocRoutedMessage.from_payload(message.payload)
    except ValueError as exc:
        _publish_direct_poison(
            config=config,
            publisher=publisher,
            payload=build_poison_payload_from_message(
                payload=message.payload,
                stage="docs",
                reason="unsupported_message_for_docs_job",
                error=str(exc),
            ),
        )
        return

    try:
        with per_file_timeout(config.per_file_timeout_seconds):
            result = process_doc_payload(
                message.payload,
                repository=repository,
                publish_downstream=True,
                materializer=materializer,
            )
    except MaterializationDeferred as exc:
        raise TransientProcessingError(str(exc)) from exc
    except Exception as exc:
        result = _persist_doc_failure(
            payload=message.payload,
            repository=repository,
            materializer=materializer,
            reason=_reason_for_exception(exc),
            error=str(exc),
        )

    if result.status == TEXT_EXTRACTION_COMPLETED_STATUS:
        published = publish_pending_entity_outbox(
            repository=repository,
            outbox_repository=outbox_repository,
            publisher=publisher,
            topic_name=config.topic_pii_entities,
            user_id=config.expected_user_id,
            run_id=config.expected_run_id,
            file_id=result.message.file_id,
        )
        LOGGER.info(
            "processed_doc file_id=%s status=%s chunks=%s published=%s",
            result.message.file_id,
            result.status,
            result.chunk_count,
            published,
        )
        return

    poison_payload = build_poison_payload_from_stored_file(
        run_id=result.message.run_id,
        file_id=result.message.file_id,
        routing_decision_id=result.message.routing_decision_id,
        stored_file=result.stored_file,
        source_queue_name=result.message.destination_queue_name,
        stage="docs",
        reason=_reason_for_doc_result(result),
        error=result.error or "text_extraction_failed",
    )
    record_and_publish_poison(
        outbox_repository=outbox_repository,
        publisher=publisher,
        topic_name=config.topic_text_poison,
        payload=poison_payload,
        user_id=config.expected_user_id,
        run_id=config.expected_run_id,
    )
    LOGGER.info(
        "poisoned_doc file_id=%s status=%s error=%s",
        result.message.file_id,
        result.status,
        result.error,
    )


def _persist_doc_failure(
    *,
    payload: dict[str, object],
    repository: PostgresTextExtractionRepository,
    materializer,
    reason: str,
    error: str,
) -> DocProcessingResult:
    routed = DocRoutedMessage.from_payload(payload)
    stored_file = repository.get_file(routed.file_id)
    if stored_file is None:
        raise ValueError(f"File not found in database: {routed.file_id}")
    now = datetime.now(UTC)
    page = PdfPageResult(
        file_id=routed.file_id,
        run_id=routed.run_id,
        page_number=1,
        page_index=0,
        method=DOC_METHOD,
        status=PAGE_FAILED_STATUS,
        reason=reason,
        char_count=0,
        word_count=0,
        total_image_ratio=0.0,
        largest_image_ratio=0.0,
        chunk_count=0,
        error=error,
    )
    result = DocProcessingResult(
        message=routed,
        stored_file=stored_file,
        status=TEXT_EXTRACTION_FAILED_STATUS,
        pages=[page],
        chunks=[],
        started_at=now,
        completed_at=now,
        processing_seconds=0.0,
        error=f"{reason}: {error}",
    )
    saved = repository.save_doc_result(result, publish_downstream=False)
    materializer.release_if_final(stored_file, saved.status)
    return saved


def _reason_for_exception(exc: Exception) -> str:
    if isinstance(exc, FileProcessingTimeout):
        return "per_file_timeout_exceeded"
    return "doc_processing_failed"


def _reason_for_doc_result(result: DocProcessingResult) -> str:
    error_reason = _reason_from_error(result.error)
    if error_reason:
        return error_reason
    for page in result.pages:
        if page.status == PAGE_FAILED_STATUS and page.reason:
            return page.reason
    return "text_extraction_failed"


def _reason_from_error(error: str | None) -> str | None:
    if not error:
        return None
    if error.startswith("per_file_timeout_exceeded"):
        return "per_file_timeout_exceeded"
    if "file_size_exceeds_limit" in error:
        return "file_size_exceeds_limit"
    if "materialization_failed" in error:
        return "materialization_failed"
    return None


def _publish_direct_poison(
    *,
    config: TextExtractJobConfig,
    publisher: PubSubJsonPublisher,
    payload: dict[str, object],
) -> None:
    from cloud_text_extract_job.pubsub import build_pubsub_attributes

    attributes = build_pubsub_attributes(
        payload,
        user_id=config.expected_user_id,
        run_id=config.expected_run_id,
    )
    publisher.publish_json(config.topic_text_poison, payload, attributes)


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s %(message)s",
    )


if __name__ == "__main__":
    raise SystemExit(main())
