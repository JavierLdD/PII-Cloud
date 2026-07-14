from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import traceback
from typing import Iterable

from chunking.chunker import ChunkingConfig
from common.models import (
    DESTINATION_QUEUE_NAMES,
    PAGE_FAILED_STATUS,
    PdfAttemptState,
    TEXT_EXTRACTION_FAILED_STATUS,
    QUEUE_PDF,
    PdfProcessingResult,
    QueueConsumer,
    QueuePublisher,
    RoutedFileMessage,
    TextExtractionRepository,
)
from materialization.models import MaterializationDeferred, PermanentMaterializationError
from materialization.service import FileMaterializer
from pdf.extractor import extract_pdf_document


DEFAULT_MAX_PDF_ATTEMPTS = 3
OCR_POLICY_QUEUE = "queue"
OCR_POLICY_POISON = "poison"
DEFAULT_QUARANTINE_LOG_PATH = (
    Path(__file__).resolve().parents[1] / "logs" / "pdf_quarantine.jsonl"
)
MAX_STORED_TRACEBACK_CHARS = 12000
MAX_CONSOLE_ERROR_CHARS = 500


def process_pdf_payload(
    payload: dict[str, object],
    repository: TextExtractionRepository,
    publish_downstream: bool = True,
    chunking_config: ChunkingConfig | None = None,
    materializer: FileMaterializer | None = None,
    ocr_policy: str = OCR_POLICY_QUEUE,
) -> PdfProcessingResult:
    if ocr_policy not in {OCR_POLICY_QUEUE, OCR_POLICY_POISON}:
        raise ValueError(f"Unsupported PDF OCR policy: {ocr_policy}")

    message = RoutedFileMessage.from_payload(payload)
    stored_file = repository.get_file(message.file_id)
    if stored_file is None:
        raise ValueError(f"File not found in database: {message.file_id}")

    effective_file = stored_file
    try:
        if materializer is not None:
            effective_file = materializer.materialize(stored_file).stored_file
        result = extract_pdf_document(
            message=message,
            stored_file=effective_file,
            chunking_config=chunking_config,
        )
        if ocr_policy == OCR_POLICY_POISON and result.pending_ocr_pages:
            result = _fail_pdf_requiring_ocr(result)
    except PermanentMaterializationError as exc:
        result = _failed_pdf_result(message, stored_file, str(exc))

    saved = repository.save_pdf_result(
        result,
        publish_downstream=publish_downstream,
    )
    if materializer is not None:
        materializer.release_if_final(stored_file, saved.status)
    return saved


def _fail_pdf_requiring_ocr(result: PdfProcessingResult) -> PdfProcessingResult:
    now = datetime.now(UTC)
    pages = [
        replace(
            page,
            status=PAGE_FAILED_STATUS,
            reason=(
                "ocr_required_but_disabled"
                if page.needs_ocr
                else page.reason
            ),
            error=(
                "PDF page requires OCR, but OCR is disabled for this Cloud job."
                if page.needs_ocr
                else page.error
            ),
        )
        for page in result.pages
    ]
    return replace(
        result,
        status=TEXT_EXTRACTION_FAILED_STATUS,
        pages=pages,
        chunks=[],
        completed_at=now,
        processing_seconds=(
            result.processing_seconds if result.processing_seconds is not None else 0.0
        ),
        error="pdf_requires_ocr_but_ocr_is_disabled",
    )


def publish_pending_outbox(
    repository: TextExtractionRepository,
    publisher: QueuePublisher,
    queue_names: Iterable[str] = DESTINATION_QUEUE_NAMES,
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
            "Could not publish all Text_Extract outbox messages. "
            "They remain pending in queue_outbox. "
            + " | ".join(failures)
        )

    return published_count


def run_pdf_worker(
    repository: TextExtractionRepository,
    publisher: QueuePublisher,
    consumer: QueueConsumer,
    source_queue_name: str = QUEUE_PDF,
    publish_downstream: bool = True,
    max_messages: int | None = None,
    requeue_messages: bool = False,
    materializer: FileMaterializer | None = None,
    max_attempts: int = DEFAULT_MAX_PDF_ATTEMPTS,
    quarantine_log_path: str | Path | None = None,
) -> None:
    log_path = (
        Path(quarantine_log_path)
        if quarantine_log_path
        else DEFAULT_QUARANTINE_LOG_PATH
    )

    def handle_payload(payload: dict[str, object]) -> None:
        message = RoutedFileMessage.from_payload(payload)
        print(
            "processing_pdf_start "
            f"file_id={message.file_id} "
            f"file_name={message.file_name} "
            f"source_type={message.source_type} "
            f"source_queue={source_queue_name}",
            flush=True,
        )
        attempt_state = repository.record_pdf_attempt_start(
            message,
            max_attempts=max_attempts,
        )
        if attempt_state.is_quarantined:
            print(
                "pdf_quarantine_skipped "
                f"file_id={message.file_id} "
                f"attempts={attempt_state.attempts} "
                f"max_attempts={attempt_state.max_attempts}"
            )
            return

        if attempt_state.exceeded_max_attempts:
            stored_file = _require_stored_file(repository, message.file_id)
            result = _quarantine_pdf(
                repository=repository,
                message=message,
                stored_file=stored_file,
                attempt_state=attempt_state,
                reason="max_attempts_exceeded_before_processing",
                quarantine_log_path=log_path,
                materializer=materializer,
            )
            _print_processed_pdf(result)
            return

        try:
            result = process_pdf_payload(
                payload,
                repository,
                publish_downstream=publish_downstream,
                materializer=materializer,
            )
        except MaterializationDeferred:
            repository.record_pdf_attempt_completed(
                message.file_id,
                "materialization_deferred",
            )
            raise
        except Exception as exc:
            error_type, error_message, error_traceback = _exception_details(exc)
            attempt_state = repository.record_pdf_attempt_error(
                message.file_id,
                error_type=error_type,
                error_message=error_message,
                error_traceback=error_traceback,
            )
            _print_pdf_processing_error(
                message,
                attempt_state,
                error_type,
                error_message,
            )
            if attempt_state.exhausted_max_attempts:
                stored_file = _require_stored_file(repository, message.file_id)
                result = _quarantine_pdf(
                    repository=repository,
                    message=message,
                    stored_file=stored_file,
                    attempt_state=attempt_state,
                    reason="max_attempts_exhausted_after_error",
                    quarantine_log_path=log_path,
                    materializer=materializer,
                    error_type=error_type,
                    error_message=error_message,
                    error_traceback=error_traceback,
                )
                _print_processed_pdf(result)
                return
            raise

        repository.record_pdf_attempt_completed(message.file_id, result.status)
        _print_processed_pdf(result)

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


def _print_processed_pdf(result: PdfProcessingResult) -> None:
    print(
        "processed_pdf "
        f"file_id={result.message.file_id} "
        f"status={result.status} "
        f"pages={result.total_pages} "
        f"chunks={result.chunk_count} "
        f"pending_ocr={result.pending_ocr_pages} "
        f"processing_seconds={result.processing_seconds} "
        f"cpu_total_seconds={result.cpu_total_seconds} "
        f"peak_memory_mb={result.peak_memory_mb}"
    )


def _quarantine_pdf(
    repository: TextExtractionRepository,
    message: RoutedFileMessage,
    stored_file,
    attempt_state: PdfAttemptState,
    reason: str,
    quarantine_log_path: Path,
    materializer: FileMaterializer | None,
    error_type: str | None = None,
    error_message: str | None = None,
    error_traceback: str | None = None,
) -> PdfProcessingResult:
    result = _failed_pdf_result(
        message,
        stored_file,
        _quarantine_error(
            reason=reason,
            attempt_state=attempt_state,
            error_type=error_type,
            error_message=error_message,
        ),
    )
    saved = repository.save_pdf_result(result, publish_downstream=False)
    attempt_state = repository.record_pdf_attempt_quarantined(message.file_id)
    if materializer is not None:
        materializer.release_if_final(stored_file, saved.status)
    _write_quarantine_log(
        quarantine_log_path,
        message=message,
        stored_file=stored_file,
        attempt_state=attempt_state,
        reason=reason,
        error_type=error_type or attempt_state.last_error_type,
        error_message=error_message or attempt_state.last_error_message,
        error_traceback=error_traceback or attempt_state.last_error_traceback,
    )
    print(
        "pdf_quarantined "
        f"file_id={message.file_id} "
        f"attempts={attempt_state.attempts} "
        f"max_attempts={attempt_state.max_attempts} "
        f"reason={reason} "
        f"log_path={quarantine_log_path}"
    )
    return saved


def _require_stored_file(
    repository: TextExtractionRepository,
    file_id: str,
):
    stored_file = repository.get_file(file_id)
    if stored_file is None:
        raise ValueError(f"File not found in database: {file_id}")
    return stored_file


def _exception_details(exc: Exception) -> tuple[str, str, str]:
    error_type = type(exc).__name__
    error_message = str(exc)
    error_traceback = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    if len(error_traceback) > MAX_STORED_TRACEBACK_CHARS:
        error_traceback = error_traceback[-MAX_STORED_TRACEBACK_CHARS:]
    return error_type, error_message, error_traceback


def _print_pdf_processing_error(
    message: RoutedFileMessage,
    attempt_state: PdfAttemptState,
    error_type: str,
    error_message: str,
) -> None:
    safe_error = error_message.replace("\n", " ")
    if len(safe_error) > MAX_CONSOLE_ERROR_CHARS:
        safe_error = safe_error[:MAX_CONSOLE_ERROR_CHARS] + "..."
    print(
        "ERROR pdf_processing_failed "
        f"file_id={message.file_id} "
        f"attempts={attempt_state.attempts} "
        f"max_attempts={attempt_state.max_attempts} "
        f"error_type={error_type} "
        f"error={safe_error}",
        file=sys.stderr,
    )


def _quarantine_error(
    reason: str,
    attempt_state: PdfAttemptState,
    error_type: str | None,
    error_message: str | None,
) -> str:
    detail_type = error_type or attempt_state.last_error_type or "unknown"
    detail_message = error_message or attempt_state.last_error_message or "unknown"
    return (
        f"pdf_quarantined reason={reason} "
        f"attempts={attempt_state.attempts}/{attempt_state.max_attempts} "
        f"last_error_type={detail_type} "
        f"last_error={detail_message}"
    )


def _write_quarantine_log(
    path: Path,
    message: RoutedFileMessage,
    stored_file,
    attempt_state: PdfAttemptState,
    reason: str,
    error_type: str | None,
    error_message: str | None,
    error_traceback: str | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event": "pdf_quarantined",
        "quarantined_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "file_id": message.file_id,
        "run_id": message.run_id,
        "routing_decision_id": message.routing_decision_id,
        "attempts": attempt_state.attempts,
        "max_attempts": attempt_state.max_attempts,
        "first_attempt_at": _datetime_to_text(attempt_state.first_attempt_at),
        "last_attempt_at": _datetime_to_text(attempt_state.last_attempt_at),
        "last_error_at": _datetime_to_text(attempt_state.last_error_at),
        "source_type": stored_file.source_type,
        "source_uri": stored_file.source_uri,
        "external_id": stored_file.external_id,
        "file_name": stored_file.file_name,
        "relative_path": stored_file.relative_path,
        "extension": stored_file.extension,
        "mime_type": stored_file.mime_type,
        "size_bytes": stored_file.size_bytes,
        "last_error_type": error_type,
        "last_error_message": error_message,
        "last_error_traceback": error_traceback,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _failed_pdf_result(
    message: RoutedFileMessage,
    stored_file,
    error: str,
) -> PdfProcessingResult:
    now = datetime.now(UTC)
    return PdfProcessingResult(
        message=message,
        stored_file=stored_file,
        status=TEXT_EXTRACTION_FAILED_STATUS,
        pages=[],
        chunks=[],
        started_at=now,
        completed_at=now,
        processing_seconds=0.0,
        error=error,
    )
