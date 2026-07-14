from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import shutil
import time

from chunking.chunker import ChunkingConfig, build_text_chunks
from common.models import (
    IMAGE_MIME_PREFIX,
    OCR_IMAGE_EXTENSIONS,
    OCR_METHOD,
    PAGE_COMPLETED_STATUS,
    PAGE_FAILED_STATUS,
    PDF_MIME_TYPES,
    OcrProcessingResult,
    OcrPageWork,
    OcrWorkMessage,
    PdfPageResult,
    SourceBlock,
    StoredFile,
    normalize_extension,
)
from common.resource_metrics import capture_resource_usage, resource_usage_delta
from ocr.mineru import (
    MINERU_BACKEND,
    MINERU_LANG,
    MinerUBlock,
    MinerUConfig,
    mineru_artifact_directory,
    parse_mineru_artifacts,
    run_mineru_image,
    run_mineru_pdf_page,
    run_mineru_pdf_range,
)


@dataclass(frozen=True)
class OcrBatchExtractionResult:
    results: list[OcrProcessingResult]
    started_at: datetime
    completed_at: datetime
    wall_seconds: float
    mineru_command_count: int
    fallback_level: str
    error: str | None = None


def extract_ocr_work(
    message: OcrWorkMessage,
    stored_file: StoredFile,
    mineru_config: MinerUConfig | None = None,
    chunking_config: ChunkingConfig | None = None,
) -> OcrProcessingResult:
    started_at = datetime.now(UTC)
    started_perf = time.perf_counter()
    started_resources = capture_resource_usage()
    config = mineru_config or MinerUConfig()

    try:
        _validate_stored_file(message, stored_file)
        with mineru_artifact_directory(config, message) as artifact_dir:
            input_path = Path(str(stored_file.materialized_path))
            if message.is_pdf_page:
                run_mineru_pdf_page(
                    pdf_path=input_path,
                    page_index=message.page_index,
                    output_dir=artifact_dir,
                    timeout_seconds=config.timeout_seconds,
                    device=config.device,
                    config=config,
                )
            else:
                run_mineru_image(
                    image_path=input_path,
                    output_dir=artifact_dir,
                    timeout_seconds=config.timeout_seconds,
                    device=config.device,
                    config=config,
                )

            mineru_blocks = parse_mineru_artifacts(
                artifact_dir,
                page_index=message.page_index,
                allow_unpaged_fallback=True,
            )
            if not mineru_blocks:
                raise RuntimeError(f"MinerU produced no text artifacts in {artifact_dir}")

            source_blocks = _build_source_blocks(
                message=message,
                stored_file=stored_file,
                mineru_blocks=mineru_blocks,
                artifact_dir=artifact_dir,
            )

        chunks = build_text_chunks(source_blocks, chunking_config)
        text = "\n".join(block.text for block in source_blocks)
        resources = resource_usage_delta(started_resources)
        page = _build_page_result(
            message=message,
            status=PAGE_COMPLETED_STATUS,
            text=text,
            chunk_count=len(chunks),
        ).with_ocr_metrics(
            requested_at=message.ocr_requested_at,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            processing_seconds=_elapsed_seconds(started_perf),
            cpu_user_seconds=resources.cpu_user_seconds,
            cpu_system_seconds=resources.cpu_system_seconds,
            cpu_total_seconds=resources.cpu_total_seconds,
            peak_memory_mb=resources.peak_memory_mb,
        )
        return OcrProcessingResult(
            message=message,
            stored_file=stored_file,
            page=page,
            chunks=chunks,
            started_at=started_at,
            completed_at=page.ocr_completed_at or datetime.now(UTC),
            processing_seconds=page.ocr_processing_seconds or 0.0,
        )
    except Exception as exc:
        page = _build_page_result(
            message=message,
            status=PAGE_FAILED_STATUS,
            text="",
            chunk_count=0,
            error=str(exc),
        )
        resources = resource_usage_delta(started_resources)
        page = page.with_ocr_metrics(
            requested_at=message.ocr_requested_at,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            processing_seconds=_elapsed_seconds(started_perf),
            cpu_user_seconds=resources.cpu_user_seconds,
            cpu_system_seconds=resources.cpu_system_seconds,
            cpu_total_seconds=resources.cpu_total_seconds,
            peak_memory_mb=resources.peak_memory_mb,
        )
        return OcrProcessingResult(
            message=message,
            stored_file=stored_file,
            page=page,
            chunks=[],
            started_at=started_at,
            completed_at=page.ocr_completed_at or datetime.now(UTC),
            processing_seconds=page.ocr_processing_seconds or 0.0,
            error=str(exc),
        )


def extract_ocr_batch_work(
    message: OcrWorkMessage,
    stored_file: StoredFile,
    mineru_config: MinerUConfig | None = None,
    chunking_config: ChunkingConfig | None = None,
) -> OcrBatchExtractionResult:
    if not message.is_pdf_batch:
        raise ValueError("extract_ocr_batch_work requires a PDF OCR batch message")

    batch_started_at = datetime.now(UTC)
    batch_started_perf = time.perf_counter()
    config = mineru_config or MinerUConfig()
    context = _BatchContext(
        message=message,
        stored_file=stored_file,
        config=config,
        chunking_config=chunking_config,
    )
    results: list[OcrProcessingResult] = []

    try:
        _validate_stored_file(message, stored_file)
        with mineru_artifact_directory(config, message) as artifact_dir:
            input_path = Path(str(stored_file.materialized_path))
            for pages in _group_consecutive_pages(message.pages):
                results.extend(
                    _extract_pdf_page_range(
                        context=context,
                        input_path=input_path,
                        artifact_root=artifact_dir,
                        pages=pages,
                        allow_split=True,
                    )
                )
    except Exception as exc:
        error = str(exc)
        results = [
            _failed_batch_page_result(
                message=message.for_page(page),
                stored_file=stored_file,
                error=error,
                started_at=batch_started_at,
                completed_at=datetime.now(UTC),
                processing_seconds=_elapsed_seconds(batch_started_perf),
            )
            for page in message.pages
        ]
        context.error_messages.append(error)

    completed_at = datetime.now(UTC)
    error_text = "; ".join(dict.fromkeys(context.error_messages)) or None
    return OcrBatchExtractionResult(
        results=sorted(results, key=lambda result: result.page.page_number),
        started_at=batch_started_at,
        completed_at=completed_at,
        wall_seconds=round(max(0.0, (completed_at - batch_started_at).total_seconds()), 6),
        mineru_command_count=context.command_count,
        fallback_level=context.fallback_level,
        error=error_text,
    )


def _validate_stored_file(message: OcrWorkMessage, stored_file: StoredFile) -> None:
    if stored_file.materialized_path is None:
        raise ValueError(
            "File source is not locally materialized: "
            f"{stored_file.source_type} {stored_file.source_uri}"
        )
    extension = normalize_extension(stored_file.extension)
    if message.is_pdf_page:
        if extension and extension != ".pdf":
            raise ValueError(f"Stored file is not a PDF: {stored_file.extension}")
        if not extension and stored_file.mime_type not in PDF_MIME_TYPES:
            raise ValueError(f"Stored file is not a PDF: {stored_file.mime_type}")
    if message.is_image_file:
        if extension and extension not in OCR_IMAGE_EXTENSIONS:
            raise ValueError(
                f"Stored file is not an OCR image: {stored_file.extension}"
            )
        if not extension and not (stored_file.mime_type or "").startswith(
            IMAGE_MIME_PREFIX
        ):
            raise ValueError(
                f"Stored file is not an OCR image: {stored_file.mime_type}"
            )


def _build_source_blocks(
    message: OcrWorkMessage,
    stored_file: StoredFile,
    mineru_blocks: list[MinerUBlock],
    artifact_dir: Path,
) -> list[SourceBlock]:
    source_blocks: list[SourceBlock] = []
    for block_index, block in enumerate(mineru_blocks, start=1):
        metadata = dict(block.metadata)
        metadata.update(
            {
                "ocr_engine": "mineru",
                "mineru_backend": MINERU_BACKEND,
                "mineru_lang": MINERU_LANG,
                "artifact_dir": str(artifact_dir),
                "input_kind": message.input_kind,
                "routing_char_count": message.routing_char_count,
                "routing_word_count": message.routing_word_count,
                "routing_image_ratio": message.routing_image_ratio,
                "routing_largest_image_ratio": message.routing_largest_image_ratio,
            }
        )
        source_blocks.append(
            SourceBlock(
                source_block_id=(
                    f"{message.file_id}:p{message.page_number}:"
                    f"ocr-b{block_index:04d}"
                ),
                run_id=message.run_id,
                file_id=message.file_id,
                source_type=stored_file.source_type,
                source_uri=stored_file.source_uri,
                file_name=stored_file.file_name,
                original_path=stored_file.original_path,
                page_number=message.page_number,
                page_index=message.page_index,
                block_index=block_index,
                method=OCR_METHOD,
                routing_reason=message.reason,
                block_type=block.block_type,
                text=block.text,
                bbox=block.bbox,
                metadata=metadata,
            )
        )
    return source_blocks


def _build_page_result(
    message: OcrWorkMessage,
    status: str,
    text: str,
    chunk_count: int,
    error: str | None = None,
) -> PdfPageResult:
    if status == PAGE_COMPLETED_STATUS:
        char_count = len(text.strip())
        word_count = len(re.findall(r"\S+", text))
    else:
        char_count = message.routing_char_count
        word_count = message.routing_word_count

    return PdfPageResult(
        file_id=message.file_id,
        run_id=message.run_id,
        page_number=message.page_number,
        page_index=message.page_index,
        method=OCR_METHOD,
        status=status,
        reason=message.reason,
        char_count=char_count,
        word_count=word_count,
        total_image_ratio=message.routing_image_ratio,
        largest_image_ratio=message.routing_largest_image_ratio,
        chunk_count=chunk_count,
        error=error,
    )


def _elapsed_seconds(started_perf: float) -> float:
    return round(time.perf_counter() - started_perf, 6)


class _BatchContext:
    def __init__(
        self,
        message: OcrWorkMessage,
        stored_file: StoredFile,
        config: MinerUConfig,
        chunking_config: ChunkingConfig | None,
    ) -> None:
        self.message = message
        self.stored_file = stored_file
        self.config = config
        self.chunking_config = chunking_config
        self.command_count = 0
        self._fallback_rank = 0
        self.error_messages: list[str] = []

    @property
    def fallback_level(self) -> str:
        return ("batch", "split", "page")[self._fallback_rank]

    def mark_split_fallback(self) -> None:
        self._fallback_rank = max(self._fallback_rank, 1)

    def mark_page_fallback(self) -> None:
        self._fallback_rank = max(self._fallback_rank, 2)


def _group_consecutive_pages(
    pages: tuple[OcrPageWork, ...],
) -> list[list[OcrPageWork]]:
    if not pages:
        return []

    ordered = sorted(pages, key=lambda page: page.page_index)
    groups: list[list[OcrPageWork]] = []
    current = [ordered[0]]
    for page in ordered[1:]:
        previous = current[-1]
        if page.page_index == previous.page_index + 1:
            current.append(page)
        else:
            groups.append(current)
            current = [page]
    groups.append(current)
    return groups


def _extract_pdf_page_range(
    context: _BatchContext,
    input_path: Path,
    artifact_root: Path,
    pages: list[OcrPageWork],
    allow_split: bool,
) -> list[OcrProcessingResult]:
    if not pages:
        return []
    if len(pages) == 1 and not allow_split:
        context.mark_page_fallback()

    first_page = pages[0]
    last_page = pages[-1]
    artifact_dir = (
        artifact_root
        / f"range_{first_page.page_number:04d}_{last_page.page_number:04d}"
    )
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(UTC)
    started_perf = time.perf_counter()
    started_resources = capture_resource_usage()
    try:
        context.command_count += 1
        run_mineru_pdf_range(
            pdf_path=input_path,
            start_page_index=first_page.page_index,
            end_page_index=last_page.page_index,
            output_dir=artifact_dir,
            timeout_seconds=context.config.timeout_seconds,
            device=context.config.device,
            config=context.config,
        )
        return _build_successful_range_results(
            context=context,
            artifact_dir=artifact_dir,
            pages=pages,
            range_start_page_index=first_page.page_index,
            started_at=started_at,
            started_perf=started_perf,
            started_resources=started_resources,
        )
    except Exception as exc:
        if len(pages) > 1:
            context.mark_split_fallback()
            middle = len(pages) // 2
            return [
                *_extract_pdf_page_range(
                    context=context,
                    input_path=input_path,
                    artifact_root=artifact_root,
                    pages=pages[:middle],
                    allow_split=True,
                ),
                *_extract_pdf_page_range(
                    context=context,
                    input_path=input_path,
                    artifact_root=artifact_root,
                    pages=pages[middle:],
                    allow_split=True,
                ),
            ]

        context.mark_page_fallback()
        error = str(exc)
        context.error_messages.append(
            f"page {first_page.page_number}: {error}"
        )
        resources = resource_usage_delta(started_resources)
        completed_at = datetime.now(UTC)
        return [
            _failed_batch_page_result(
                message=context.message.for_page(first_page),
                stored_file=context.stored_file,
                error=error,
                started_at=started_at,
                completed_at=completed_at,
                processing_seconds=_elapsed_seconds(started_perf),
                cpu_user_seconds=resources.cpu_user_seconds,
                cpu_system_seconds=resources.cpu_system_seconds,
                cpu_total_seconds=resources.cpu_total_seconds,
                peak_memory_mb=resources.peak_memory_mb,
            )
        ]


def _build_successful_range_results(
    context: _BatchContext,
    artifact_dir: Path,
    pages: list[OcrPageWork],
    range_start_page_index: int,
    started_at: datetime,
    started_perf: float,
    started_resources: object,
) -> list[OcrProcessingResult]:
    results: list[OcrProcessingResult] = []
    missing_pages: list[OcrPageWork] = []
    page_count = max(1, len(pages))

    for page in pages:
        blocks = parse_mineru_artifacts(
            artifact_dir,
            page_index=page.page_index,
            fallback_page_index=page.page_index - range_start_page_index,
            allow_unpaged_fallback=page_count == 1,
        )
        if not blocks:
            missing_pages.append(page)
            continue

        page_message = context.message.for_page(page)
        source_blocks = _build_source_blocks(
            message=page_message,
            stored_file=context.stored_file,
            mineru_blocks=blocks,
            artifact_dir=artifact_dir,
        )
        chunks = build_text_chunks(source_blocks, context.chunking_config)
        text = "\n".join(block.text for block in source_blocks)
        resources = resource_usage_delta(started_resources)
        completed_at = datetime.now(UTC)
        processing_seconds = round(_elapsed_seconds(started_perf) / page_count, 6)
        page_result = _build_page_result(
            message=page_message,
            status=PAGE_COMPLETED_STATUS,
            text=text,
            chunk_count=len(chunks),
        ).with_ocr_metrics(
            requested_at=page_message.ocr_requested_at,
            started_at=started_at,
            completed_at=completed_at,
            processing_seconds=processing_seconds,
            cpu_user_seconds=_divide_metric(resources.cpu_user_seconds, page_count),
            cpu_system_seconds=_divide_metric(resources.cpu_system_seconds, page_count),
            cpu_total_seconds=_divide_metric(resources.cpu_total_seconds, page_count),
            peak_memory_mb=resources.peak_memory_mb,
        )
        results.append(
            OcrProcessingResult(
                message=page_message,
                stored_file=context.stored_file,
                page=page_result,
                chunks=chunks,
                started_at=started_at,
                completed_at=completed_at,
                processing_seconds=processing_seconds,
            )
        )

    if missing_pages:
        context.mark_page_fallback()
        for page in missing_pages:
            results.extend(
                _extract_pdf_page_range(
                    context=context,
                    input_path=Path(str(context.stored_file.materialized_path)),
                    artifact_root=artifact_dir.parent,
                    pages=[page],
                    allow_split=False,
                )
            )

    return results


def _failed_batch_page_result(
    message: OcrWorkMessage,
    stored_file: StoredFile,
    error: str,
    started_at: datetime,
    completed_at: datetime,
    processing_seconds: float,
    cpu_user_seconds: float | None = None,
    cpu_system_seconds: float | None = None,
    cpu_total_seconds: float | None = None,
    peak_memory_mb: float | None = None,
) -> OcrProcessingResult:
    page = _build_page_result(
        message=message,
        status=PAGE_FAILED_STATUS,
        text="",
        chunk_count=0,
        error=error,
    ).with_ocr_metrics(
        requested_at=message.ocr_requested_at,
        started_at=started_at,
        completed_at=completed_at,
        processing_seconds=processing_seconds,
        cpu_user_seconds=cpu_user_seconds,
        cpu_system_seconds=cpu_system_seconds,
        cpu_total_seconds=cpu_total_seconds,
        peak_memory_mb=peak_memory_mb,
    )
    return OcrProcessingResult(
        message=message,
        stored_file=stored_file,
        page=page,
        chunks=[],
        started_at=started_at,
        completed_at=completed_at,
        processing_seconds=processing_seconds,
        error=error,
    )


def _divide_metric(value: float | None, divisor: int) -> float | None:
    if value is None:
        return None
    return round(value / max(1, divisor), 6)
