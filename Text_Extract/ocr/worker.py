from __future__ import annotations

from datetime import UTC, datetime
import sys
from typing import Iterable
import uuid

from chunking.chunker import ChunkingConfig
from common.models import (
    OCR_METHOD,
    PAGE_FAILED_STATUS,
    QUEUE_ENTITY,
    QUEUE_OCR,
    QUEUE_OCR_URGENT,
    OcrBatchMetrics,
    OcrProcessingResult,
    OcrWorkMessage,
    PdfPageResult,
    QueueConsumer,
    QueuePublisher,
    StoredFile,
    TextExtractionRepository,
)
from materialization.models import PermanentMaterializationError
from materialization.service import FileMaterializer
from ocr.extractor import OcrBatchExtractionResult, extract_ocr_batch_work, extract_ocr_work
from ocr.mineru import MinerUConfig, resolve_mineru_device_info


def process_ocr_payload(
    payload: dict[str, object],
    repository: TextExtractionRepository,
    publish_downstream: bool = True,
    mineru_config: MinerUConfig | None = None,
    chunking_config: ChunkingConfig | None = None,
    materializer: FileMaterializer | None = None,
) -> OcrProcessingResult:
    message = OcrWorkMessage.from_payload(payload)
    stored_file = repository.get_file(message.file_id)
    if stored_file is None:
        raise ValueError(f"File not found in database: {message.file_id}")

    if message.is_pdf_batch:
        return _process_ocr_batch_payload(
            message=message,
            stored_file=stored_file,
            repository=repository,
            publish_downstream=publish_downstream,
            mineru_config=mineru_config,
            chunking_config=chunking_config,
            materializer=materializer,
        )

    effective_file = stored_file
    try:
        if materializer is not None:
            effective_file = materializer.materialize(stored_file).stored_file
        result = extract_ocr_work(
            message=message,
            stored_file=effective_file,
            mineru_config=mineru_config,
            chunking_config=chunking_config,
        )
    except PermanentMaterializationError as exc:
        result = _failed_ocr_result(message, stored_file, str(exc))

    saved = repository.save_ocr_result(
        result,
        publish_downstream=publish_downstream,
    )
    if materializer is not None:
        materializer.release_if_final(stored_file, saved.file_status)
    return saved


def _process_ocr_batch_payload(
    message: OcrWorkMessage,
    stored_file: StoredFile,
    repository: TextExtractionRepository,
    publish_downstream: bool,
    mineru_config: MinerUConfig | None,
    chunking_config: ChunkingConfig | None,
    materializer: FileMaterializer | None,
) -> OcrProcessingResult:
    config = mineru_config or MinerUConfig()
    batch_started_at = datetime.now(UTC)
    effective_file = stored_file
    try:
        if materializer is not None:
            effective_file = materializer.materialize(stored_file).stored_file
        batch_result = extract_ocr_batch_work(
            message=message,
            stored_file=effective_file,
            mineru_config=config,
            chunking_config=chunking_config,
        )
    except PermanentMaterializationError as exc:
        completed_at = datetime.now(UTC)
        error = str(exc)
        results = [
            _failed_ocr_result(message.for_page(page), stored_file, error)
            for page in message.pages
        ]
        batch_result = OcrBatchExtractionResult(
            results=results,
            started_at=batch_started_at,
            completed_at=completed_at,
            wall_seconds=round(
                max(0.0, (completed_at - batch_started_at).total_seconds()),
                6,
            ),
            mineru_command_count=0,
            fallback_level="batch",
            error=error,
        )

    saved_results: list[OcrProcessingResult] = []
    for result in batch_result.results:
        saved_results.append(
            repository.save_ocr_result(
                result,
                publish_downstream=publish_downstream,
            )
        )

    if not saved_results:
        raise RuntimeError("OCR batch produced no page results")

    saved = saved_results[-1]
    if materializer is not None:
        materializer.release_if_final(stored_file, saved.file_status)
    _save_batch_metrics(
        repository=repository,
        message=message,
        mineru_config=config,
        batch_result=batch_result,
    )
    return saved


def _save_batch_metrics(
    repository: TextExtractionRepository,
    message: OcrWorkMessage,
    mineru_config: MinerUConfig,
    batch_result: OcrBatchExtractionResult,
) -> None:
    save_metrics = getattr(repository, "save_ocr_batch_metrics", None)
    if save_metrics is None:
        return

    device_info = resolve_mineru_device_info(mineru_config.device)
    save_metrics(
        OcrBatchMetrics(
            batch_id=str(uuid.uuid4()),
            file_id=message.file_id,
            run_id=message.run_id,
            page_numbers=tuple(page.page_number for page in message.pages),
            requested_device=device_info.requested_device,
            effective_device=device_info.effective_device,
            cuda_available=device_info.cuda_available,
            gpu_name=device_info.gpu_name,
            cuda_visible_devices=device_info.cuda_visible_devices,
            mineru_device=device_info.mineru_device,
            started_at=batch_result.started_at,
            completed_at=batch_result.completed_at,
            wall_seconds=batch_result.wall_seconds,
            mineru_command_count=batch_result.mineru_command_count,
            fallback_level=batch_result.fallback_level,
            error=batch_result.error,
        )
    )


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
            "Could not publish all OCR outbox messages. "
            "They remain pending in queue_outbox. "
            + " | ".join(failures)
        )

    return published_count


def run_ocr_worker(
    repository: TextExtractionRepository,
    publisher: QueuePublisher,
    consumer: QueueConsumer,
    source_queue_name: str = QUEUE_OCR,
    urgent_source_queue_name: str = QUEUE_OCR_URGENT,
    publish_downstream: bool = True,
    max_messages: int | None = None,
    requeue_messages: bool = False,
    mineru_config: MinerUConfig | None = None,
    materializer: FileMaterializer | None = None,
) -> None:
    def handle_payload(payload: dict[str, object]) -> None:
        message = OcrWorkMessage.from_payload(payload)
        page_count = len(message.pages) if message.is_pdf_batch else 1
        print(
            "processing_ocr_start "
            f"file_id={message.file_id} "
            f"input={message.input_kind} "
            f"page={message.page_number} "
            f"pages={page_count} "
            f"source_queue={urgent_source_queue_name},{source_queue_name}",
            flush=True,
        )
        result = process_ocr_payload(
            payload,
            repository,
            publish_downstream=publish_downstream,
            mineru_config=mineru_config,
            materializer=materializer,
        )
        print(
            "processed_ocr "
            f"file_id={result.message.file_id} "
            f"input={result.message.input_kind} "
            f"page={result.page.page_number} "
            f"page_status={result.page.status} "
            f"file_status={result.file_status} "
            f"chunks={result.chunk_count} "
            f"file_chunks={result.file_chunk_count} "
            f"processing_seconds={result.processing_seconds} "
            f"cpu_total_seconds={result.page.cpu_total_seconds} "
            f"peak_memory_mb={result.page.peak_memory_mb}"
            f"{_format_error(result.error)}"
        )

        if publish_downstream:
            try:
                published_count = publish_pending_outbox(repository, publisher)
            except Exception as exc:
                print(f"WARN: {exc}", file=sys.stderr)
            else:
                print(f"published={published_count}")

    consumer.consume_in_priority_order(
        (urgent_source_queue_name, source_queue_name),
        handle_payload,
        max_messages=max_messages,
        requeue_messages=requeue_messages,
    )


def _format_error(error: str | None) -> str:
    if not error:
        return ""
    one_line_error = error.replace("\r", "\\r").replace("\n", "\\n")
    return f" error={one_line_error}"


def _failed_ocr_result(
    message: OcrWorkMessage,
    stored_file: StoredFile,
    error: str,
) -> OcrProcessingResult:
    now = datetime.now(UTC)
    page = PdfPageResult(
        file_id=message.file_id,
        run_id=message.run_id,
        page_number=message.page_number,
        page_index=message.page_index,
        method=OCR_METHOD,
        status=PAGE_FAILED_STATUS,
        reason="materialization_failed",
        char_count=0,
        word_count=0,
        total_image_ratio=0.0,
        largest_image_ratio=0.0,
        chunk_count=0,
        error=error,
    )
    return OcrProcessingResult(
        message=message,
        stored_file=stored_file,
        page=page,
        chunks=[],
        started_at=now,
        completed_at=now,
        processing_seconds=0.0,
        error=error,
    )
