from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Any, Iterable

from chunking.chunker import ChunkingConfig, build_text_chunks
from common.models import (
    DOC_EXTENSIONS,
    DOC_METHOD,
    PAGE_COMPLETED_STATUS,
    PAGE_FAILED_STATUS,
    DocProcessingResult,
    DocRoutedMessage,
    PdfPageResult,
    SourceBlock,
    StoredFile,
    TEXT_EXTRACTION_COMPLETED_STATUS,
    TEXT_EXTRACTION_FAILED_STATUS,
    normalize_extension,
)
from common.resource_metrics import capture_resource_usage, resource_usage_delta


LOGICAL_PAGE_NUMBER = 1
LOGICAL_PAGE_INDEX = 0


def extract_doc_document(
    message: DocRoutedMessage,
    stored_file: StoredFile,
    chunking_config: ChunkingConfig | None = None,
) -> DocProcessingResult:
    started_at = datetime.now(UTC)
    started_perf = time.perf_counter()
    started_resources = capture_resource_usage()
    extension = normalize_extension(stored_file.extension or message.extension)
    input_path = (
        Path(stored_file.materialized_path)
        if stored_file.materialized_path is not None
        else None
    )

    if message.is_google_native and input_path is None:
        return _failed_result(
            message=message,
            stored_file=stored_file,
            started_at=started_at,
            started_perf=started_perf,
            started_resources=started_resources,
            reason="google_native_not_materialized",
            error=(
                "Queue-Doc Google native documents require prior export or "
                "local materialization before text extraction."
            ),
        )

    if input_path is None:
        return _failed_result(
            message=message,
            stored_file=stored_file,
            started_at=started_at,
            started_perf=started_perf,
            started_resources=started_resources,
            reason="source_not_materialized",
            error=(
                "Queue-Doc remote documents require prior download/export before "
                "text extraction."
            ),
        )

    if message.is_google_native:
        extension = ".txt"
    elif not extension:
        extension = normalize_extension(input_path.suffix)

    if extension not in DOC_EXTENSIONS:
        return _failed_result(
            message=message,
            stored_file=stored_file,
            started_at=started_at,
            started_perf=started_perf,
            started_resources=started_resources,
            reason="unsupported_document_extension",
            error=f"Unsupported Queue-Doc extension: {extension}",
        )

    if not input_path.is_file():
        return _failed_result(
            message=message,
            stored_file=stored_file,
            started_at=started_at,
            started_perf=started_perf,
            started_resources=started_resources,
            reason="document_path_missing",
            error=f"Document path does not exist or is not a file: {input_path}",
        )

    try:
        if extension == ".txt":
            source_blocks = _extract_txt_source_blocks(message, stored_file, input_path)
        else:
            source_blocks = _extract_docx_source_blocks(message, stored_file, input_path)
    except Exception as exc:
        return _failed_result(
            message=message,
            stored_file=stored_file,
            started_at=started_at,
            started_perf=started_perf,
            started_resources=started_resources,
            reason="document_extraction_failed",
            error=str(exc),
        )

    chunks = build_text_chunks(source_blocks, chunking_config)
    text = "\n".join(block.text for block in source_blocks)
    resources = resource_usage_delta(started_resources)
    page = PdfPageResult(
        file_id=message.file_id,
        run_id=message.run_id,
        page_number=LOGICAL_PAGE_NUMBER,
        page_index=LOGICAL_PAGE_INDEX,
        method=DOC_METHOD,
        status=PAGE_COMPLETED_STATUS,
        reason="empty_document" if not source_blocks else "document_text_extracted",
        char_count=len(text),
        word_count=len(text.split()),
        total_image_ratio=0.0,
        largest_image_ratio=0.0,
        chunk_count=len(chunks),
        cpu_user_seconds=resources.cpu_user_seconds,
        cpu_system_seconds=resources.cpu_system_seconds,
        cpu_total_seconds=resources.cpu_total_seconds,
        peak_memory_mb=resources.peak_memory_mb,
    )

    return DocProcessingResult(
        message=message,
        stored_file=stored_file,
        status=TEXT_EXTRACTION_COMPLETED_STATUS,
        pages=[page],
        chunks=chunks,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        processing_seconds=_elapsed_seconds(started_perf),
    )


def _extract_txt_source_blocks(
    message: DocRoutedMessage,
    stored_file: StoredFile,
    input_path: Path,
) -> list[SourceBlock]:
    raw = input_path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        encoding = "latin-1"

    return _build_source_blocks(
        message=message,
        stored_file=stored_file,
        text_blocks=_paragraph_blocks(text),
        block_type="text",
        metadata={"source": "txt", "encoding": encoding},
    )


def _extract_docx_source_blocks(
    message: DocRoutedMessage,
    stored_file: StoredFile,
    input_path: Path,
) -> list[SourceBlock]:
    Document, Paragraph, Table, CT_P, CT_Tbl = _import_docx()
    document = Document(str(input_path))
    text_blocks: list[tuple[str, str, dict[str, Any]]] = []

    for block in document.element.body.iterchildren():
        if isinstance(block, CT_P):
            paragraph = Paragraph(block, document)
            text = paragraph.text.strip()
            if text:
                text_blocks.append(("paragraph", text, {"source": "docx_paragraph"}))
        elif isinstance(block, CT_Tbl):
            table = Table(block, document)
            text = _table_text(table)
            if text:
                text_blocks.append(
                    (
                        "table",
                        text,
                        {
                            "source": "docx_table",
                            "row_count": len(table.rows),
                        },
                    )
                )

    return _build_source_blocks(
        message=message,
        stored_file=stored_file,
        text_blocks=text_blocks,
        block_type=None,
        metadata={"source": "docx"},
    )


def _build_source_blocks(
    message: DocRoutedMessage,
    stored_file: StoredFile,
    text_blocks: Iterable[tuple[str, str, dict[str, Any]]],
    block_type: str | None,
    metadata: dict[str, Any],
) -> list[SourceBlock]:
    source_blocks: list[SourceBlock] = []
    for block_index, item in enumerate(text_blocks, start=1):
        item_block_type, text, item_metadata = item
        merged_metadata = {**metadata, **item_metadata}
        source_blocks.append(
            SourceBlock(
                source_block_id=f"{message.file_id}:p1:b{block_index:04d}",
                run_id=message.run_id,
                file_id=message.file_id,
                source_type=stored_file.source_type,
                source_uri=stored_file.source_uri,
                file_name=stored_file.file_name,
                original_path=stored_file.original_path,
                page_number=LOGICAL_PAGE_NUMBER,
                page_index=LOGICAL_PAGE_INDEX,
                block_index=block_index,
                method=DOC_METHOD,
                routing_reason=message.reason,
                block_type=block_type or item_block_type,
                text=text,
                bbox=None,
                metadata=merged_metadata,
            )
        )
    return source_blocks


def _paragraph_blocks(text: str) -> list[tuple[str, str, dict[str, Any]]]:
    blocks: list[tuple[str, str, dict[str, Any]]] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            current.append(stripped)
            continue
        if current:
            blocks.append(("text", "\n".join(current), {"source": "txt_block"}))
            current = []
    if current:
        blocks.append(("text", "\n".join(current), {"source": "txt_block"}))
    return blocks


def _table_text(table: Any) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _failed_result(
    message: DocRoutedMessage,
    stored_file: StoredFile,
    started_at: datetime,
    started_perf: float,
    started_resources: object,
    reason: str,
    error: str,
) -> DocProcessingResult:
    resources = resource_usage_delta(started_resources)
    page = PdfPageResult(
        file_id=message.file_id,
        run_id=message.run_id,
        page_number=LOGICAL_PAGE_NUMBER,
        page_index=LOGICAL_PAGE_INDEX,
        method=DOC_METHOD,
        status=PAGE_FAILED_STATUS,
        reason=reason,
        char_count=0,
        word_count=0,
        total_image_ratio=0.0,
        largest_image_ratio=0.0,
        chunk_count=0,
        error=error,
        cpu_user_seconds=resources.cpu_user_seconds,
        cpu_system_seconds=resources.cpu_system_seconds,
        cpu_total_seconds=resources.cpu_total_seconds,
        peak_memory_mb=resources.peak_memory_mb,
    )
    return DocProcessingResult(
        message=message,
        stored_file=stored_file,
        status=TEXT_EXTRACTION_FAILED_STATUS,
        pages=[page],
        chunks=[],
        started_at=started_at,
        completed_at=datetime.now(UTC),
        processing_seconds=_elapsed_seconds(started_perf),
        error=error,
    )


def _import_docx() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required for Queue-Doc .docx extraction. "
            "Install Text_Extract dependencies first."
        ) from exc
    return Document, Paragraph, Table, CT_P, CT_Tbl


def _elapsed_seconds(started_perf: float) -> float:
    return round(time.perf_counter() - started_perf, 6)
