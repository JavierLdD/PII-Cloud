from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Any, Iterable

from chunking.chunker import ChunkingConfig, build_text_chunks
from common.models import (
    OCR_METHOD,
    PAGE_COMPLETED_STATUS,
    PAGE_FAILED_STATUS,
    PAGE_PENDING_OCR_STATUS,
    PDF_MIME_TYPES,
    PYMUPDF_METHOD,
    PdfPageResult,
    PdfProcessingResult,
    RoutedFileMessage,
    SourceBlock,
    StoredFile,
    TEXT_EXTRACTION_COMPLETED_STATUS,
    TEXT_EXTRACTION_FAILED_STATUS,
    WAITING_OCR_STATUS,
    normalize_extension,
)
from common.resource_metrics import capture_resource_usage, resource_usage_delta
from pdf.page_router import route_page


def extract_pdf_document(
    message: RoutedFileMessage,
    stored_file: StoredFile,
    chunking_config: ChunkingConfig | None = None,
) -> PdfProcessingResult:
    started_at = datetime.now(UTC)
    started_perf = time.perf_counter()
    extension = normalize_extension(stored_file.extension)
    if extension and extension != ".pdf":
        raise ValueError(f"Stored file is not a PDF: {stored_file.extension}")
    if not extension and stored_file.mime_type not in PDF_MIME_TYPES:
        raise ValueError(f"Stored file is not a PDF: {stored_file.mime_type}")

    input_path = stored_file.materialized_path
    if input_path is None:
        return PdfProcessingResult(
            message=message,
            stored_file=stored_file,
            status=TEXT_EXTRACTION_FAILED_STATUS,
            pages=[],
            chunks=[],
            started_at=started_at,
            completed_at=datetime.now(UTC),
            processing_seconds=_elapsed_seconds(started_perf),
            error=(
                "File source is not locally materialized: "
                f"{stored_file.source_type} {stored_file.source_uri}"
            ),
        )

    try:
        fitz = _import_fitz()
        document = fitz.open(input_path)
    except Exception as exc:
        return PdfProcessingResult(
            message=message,
            stored_file=stored_file,
            status=TEXT_EXTRACTION_FAILED_STATUS,
            pages=[],
            chunks=[],
            started_at=started_at,
            completed_at=datetime.now(UTC),
            processing_seconds=_elapsed_seconds(started_perf),
            error=str(exc),
        )

    source_blocks: list[SourceBlock] = []
    page_results: list[PdfPageResult] = []

    try:
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_started_at = datetime.now(UTC)
            page_started_perf = time.perf_counter()
            page_started_resources = capture_resource_usage()
            try:
                decision = route_page(page_number, page)
                if decision.method == PYMUPDF_METHOD:
                    page_blocks = extract_pymupdf_source_blocks(
                        message=message,
                        stored_file=stored_file,
                        page=page,
                        page_number=page_number,
                        routing_reason=decision.reason,
                    )
                    source_blocks.extend(page_blocks)
                    page_resources = resource_usage_delta(page_started_resources)
                    page_results.append(
                        PdfPageResult(
                            file_id=message.file_id,
                            run_id=message.run_id,
                            page_number=page_number,
                            page_index=page_index,
                            method=PYMUPDF_METHOD,
                            status=PAGE_COMPLETED_STATUS,
                            reason=decision.reason,
                            char_count=decision.char_count,
                            word_count=decision.word_count,
                            total_image_ratio=decision.total_image_ratio,
                            largest_image_ratio=decision.largest_image_ratio,
                            chunk_count=0,
                            embedded_started_at=page_started_at,
                            embedded_completed_at=datetime.now(UTC),
                            embedded_processing_seconds=_elapsed_seconds(
                                page_started_perf
                            ),
                            cpu_user_seconds=page_resources.cpu_user_seconds,
                            cpu_system_seconds=page_resources.cpu_system_seconds,
                            cpu_total_seconds=page_resources.cpu_total_seconds,
                            peak_memory_mb=page_resources.peak_memory_mb,
                        )
                    )
                else:
                    page_results.append(
                        PdfPageResult(
                            file_id=message.file_id,
                            run_id=message.run_id,
                            page_number=page_number,
                            page_index=page_index,
                            method=OCR_METHOD,
                            status=PAGE_PENDING_OCR_STATUS,
                            reason=decision.reason,
                            char_count=decision.char_count,
                            word_count=decision.word_count,
                            total_image_ratio=decision.total_image_ratio,
                            largest_image_ratio=decision.largest_image_ratio,
                            chunk_count=0,
                        )
                    )
            except Exception as exc:
                page_results.append(
                    PdfPageResult(
                        file_id=message.file_id,
                        run_id=message.run_id,
                        page_number=page_number,
                        page_index=page_index,
                        method=PYMUPDF_METHOD,
                        status=PAGE_FAILED_STATUS,
                        reason="page_extraction_failed",
                        char_count=0,
                        word_count=0,
                        total_image_ratio=0.0,
                        largest_image_ratio=0.0,
                        chunk_count=0,
                        error=str(exc),
                    )
                )
    finally:
        document.close()

    if any(page.status == PAGE_FAILED_STATUS for page in page_results):
        status = TEXT_EXTRACTION_FAILED_STATUS
        chunks = []
    else:
        chunks = build_text_chunks(source_blocks, chunking_config)
        chunk_count_by_page: dict[int, int] = {}
        for chunk in chunks:
            chunk_count_by_page[chunk.page_start] = (
                chunk_count_by_page.get(chunk.page_start, 0) + 1
            )
        page_results = [
            _page_with_chunk_count(page, chunk_count_by_page)
            for page in page_results
        ]
        status = (
            WAITING_OCR_STATUS
            if any(page.status == PAGE_PENDING_OCR_STATUS for page in page_results)
            else TEXT_EXTRACTION_COMPLETED_STATUS
        )

    return PdfProcessingResult(
        message=message,
        stored_file=stored_file,
        status=status,
        pages=page_results,
        chunks=chunks,
        started_at=started_at,
        completed_at=(
            datetime.now(UTC)
            if status in {TEXT_EXTRACTION_COMPLETED_STATUS, TEXT_EXTRACTION_FAILED_STATUS}
            else None
        ),
        processing_seconds=(
            _elapsed_seconds(started_perf)
            if status in {TEXT_EXTRACTION_COMPLETED_STATUS, TEXT_EXTRACTION_FAILED_STATUS}
            else None
        ),
    )


def extract_pymupdf_source_blocks(
    message: RoutedFileMessage,
    stored_file: StoredFile,
    page: object,
    page_number: int,
    routing_reason: str,
) -> list[SourceBlock]:
    blocks = _get_pymupdf_blocks(page)
    source_blocks: list[SourceBlock] = []
    for block_index, block in enumerate(_iter_text_blocks(blocks), start=1):
        source_blocks.append(
            SourceBlock(
                source_block_id=(
                    f"{message.file_id}:p{page_number}:b{block_index:04d}"
                ),
                run_id=message.run_id,
                file_id=message.file_id,
                source_type=stored_file.source_type,
                source_uri=stored_file.source_uri,
                file_name=stored_file.file_name,
                original_path=stored_file.original_path,
                page_number=page_number,
                page_index=page_number - 1,
                block_index=block_index,
                method=PYMUPDF_METHOD,
                routing_reason=routing_reason,
                block_type="text",
                text=block["text"],
                bbox=block["bbox"],
                metadata={"source": "pymupdf_blocks"},
            )
        )
    return source_blocks


def _page_with_chunk_count(
    page: PdfPageResult,
    chunk_count_by_page: dict[int, int],
) -> PdfPageResult:
    if page.status != PAGE_COMPLETED_STATUS:
        return page
    return PdfPageResult(
        file_id=page.file_id,
        run_id=page.run_id,
        page_number=page.page_number,
        page_index=page.page_index,
        method=page.method,
        status=page.status,
        reason=page.reason,
        char_count=page.char_count,
        word_count=page.word_count,
        total_image_ratio=page.total_image_ratio,
        largest_image_ratio=page.largest_image_ratio,
        chunk_count=chunk_count_by_page.get(page.page_number, 0),
        error=page.error,
        ocr_outbox_id=page.ocr_outbox_id,
        embedded_started_at=page.embedded_started_at,
        embedded_completed_at=page.embedded_completed_at,
        embedded_processing_seconds=page.embedded_processing_seconds,
        ocr_requested_at=page.ocr_requested_at,
        ocr_started_at=page.ocr_started_at,
        ocr_completed_at=page.ocr_completed_at,
        ocr_queue_wait_seconds=page.ocr_queue_wait_seconds,
        ocr_processing_seconds=page.ocr_processing_seconds,
        cpu_user_seconds=page.cpu_user_seconds,
        cpu_system_seconds=page.cpu_system_seconds,
        cpu_total_seconds=page.cpu_total_seconds,
        peak_memory_mb=page.peak_memory_mb,
    )


def _get_pymupdf_blocks(page: object) -> Any:
    get_text = getattr(page, "get_text")
    try:
        return get_text("blocks", sort=True)
    except TypeError:
        return get_text("blocks")


def _iter_text_blocks(blocks: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(blocks, list):
        return []

    parsed_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, (tuple, list)) or len(block) < 5:
            continue
        text = str(block[4]).strip()
        if not text:
            continue
        bbox = _normalize_bbox(block[:4])
        if bbox is None:
            continue
        parsed_blocks.append({"text": text, "bbox": bbox})
    return parsed_blocks


def _normalize_bbox(value: Any) -> list[float] | None:
    try:
        bbox = [float(number) for number in value]
    except (TypeError, ValueError):
        return None
    if len(bbox) != 4:
        return None
    return bbox


def _import_fitz() -> object:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required for Queue-PDF extraction. "
            "Install Text_Extract dependencies first."
        ) from exc
    return fitz


def _elapsed_seconds(started_perf: float) -> float:
    return round(time.perf_counter() - started_perf, 6)
