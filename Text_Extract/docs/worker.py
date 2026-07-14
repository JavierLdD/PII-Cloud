from __future__ import annotations

from datetime import UTC, datetime
import sys
from typing import Iterable

from chunking.chunker import ChunkingConfig
from common.models import (
    DOC_METHOD,
    PAGE_FAILED_STATUS,
    QUEUE_DOC,
    QUEUE_ENTITY,
    DocProcessingResult,
    DocRoutedMessage,
    PdfPageResult,
    QueueConsumer,
    QueuePublisher,
    StoredFile,
    TEXT_EXTRACTION_FAILED_STATUS,
    TextExtractionRepository,
)
from docs.extractor import extract_doc_document
from materialization.models import PermanentMaterializationError
from materialization.service import FileMaterializer


def process_doc_payload(
    payload: dict[str, object],
    repository: TextExtractionRepository,
    publish_downstream: bool = True,
    chunking_config: ChunkingConfig | None = None,
    materializer: FileMaterializer | None = None,
) -> DocProcessingResult:
    message = DocRoutedMessage.from_payload(payload)
    stored_file = repository.get_file(message.file_id)
    if stored_file is None:
        raise ValueError(f"File not found in database: {message.file_id}")

    effective_file = stored_file
    try:
        if materializer is not None:
            effective_file = materializer.materialize(stored_file).stored_file
        result = extract_doc_document(
            message=message,
            stored_file=effective_file,
            chunking_config=chunking_config,
        )
    except PermanentMaterializationError as exc:
        result = _failed_doc_result(message, stored_file, str(exc))

    saved = repository.save_doc_result(
        result,
        publish_downstream=publish_downstream,
    )
    if materializer is not None:
        materializer.release_if_final(stored_file, saved.status)
    return saved


def publish_pending_outbox(
    repository: TextExtractionRepository,
    publisher: QueuePublisher,
    queue_names: Iterable[str] = (QUEUE_ENTITY,),
) -> int:
    published_count = 0
    failures: list[str] = []

    for queue_name in queue_names:
        for message in repository.list_pending_outbox(queue_name):
            try:
                publisher.publish(message.queue_name, message.payload)
            except Exception as exc:
                error = str(exc)
                repository.record_outbox_error(message.outbox_id, error)
                failures.append(f"{message.outbox_id}: {error}")
                continue

            repository.mark_outbox_published(message.outbox_id)
            published_count += 1

    if failures:
        raise RuntimeError(
            "Could not publish all Queue-Doc outbox messages. "
            "They remain pending in queue_outbox. "
            + " | ".join(failures)
        )

    return published_count


def run_doc_worker(
    repository: TextExtractionRepository,
    publisher: QueuePublisher,
    consumer: QueueConsumer,
    source_queue_name: str = QUEUE_DOC,
    publish_downstream: bool = True,
    max_messages: int | None = None,
    requeue_messages: bool = False,
    materializer: FileMaterializer | None = None,
) -> None:
    def handle_payload(payload: dict[str, object]) -> None:
        result = process_doc_payload(
            payload,
            repository,
            publish_downstream=publish_downstream,
            materializer=materializer,
        )
        print(
            "processed_doc "
            f"file_id={result.message.file_id} "
            f"status={result.status} "
            f"pages={result.total_pages} "
            f"chunks={result.chunk_count} "
            f"processing_seconds={result.processing_seconds} "
            f"cpu_total_seconds={result.cpu_total_seconds} "
            f"peak_memory_mb={result.peak_memory_mb}"
            f"{_format_error(result.error)}"
        )

        if publish_downstream:
            try:
                published_count = publish_pending_outbox(repository, publisher)
            except Exception as exc:
                print(f"WARN: {exc}", file=sys.stderr)
            else:
                print(f"published={published_count}")

    consumer.consume(
        source_queue_name,
        handle_payload,
        max_messages=max_messages,
        requeue_messages=requeue_messages,
    )


def _format_error(error: str | None) -> str:
    if not error:
        return ""
    one_line_error = error.replace("\r", "\\r").replace("\n", "\\n")
    return f" error={one_line_error}"


def _failed_doc_result(
    message: DocRoutedMessage,
    stored_file: StoredFile,
    error: str,
) -> DocProcessingResult:
    now = datetime.now(UTC)
    page = PdfPageResult(
        file_id=message.file_id,
        run_id=message.run_id,
        page_number=1,
        page_index=0,
        method=DOC_METHOD,
        status=PAGE_FAILED_STATUS,
        reason="materialization_failed",
        char_count=0,
        word_count=0,
        total_image_ratio=0.0,
        largest_image_ratio=0.0,
        chunk_count=0,
        error=error,
    )
    return DocProcessingResult(
        message=message,
        stored_file=stored_file,
        status=TEXT_EXTRACTION_FAILED_STATUS,
        pages=[page],
        chunks=[],
        started_at=now,
        completed_at=now,
        processing_seconds=0.0,
        error=error,
    )
