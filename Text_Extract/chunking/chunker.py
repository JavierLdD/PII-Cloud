from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable

from common.models import PYMUPDF_METHOD, SourceBlock, TextChunk


TARGET_CHARS = 1500
MAX_CHARS = 2500
MIN_CHARS = 400
OVERLAP_CHARS = 200
TEXT_SEPARATOR = " "
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class ChunkingConfig:
    target_chars: int = TARGET_CHARS
    max_chars: int = MAX_CHARS
    min_chars: int = MIN_CHARS
    overlap_chars: int = OVERLAP_CHARS


@dataclass(frozen=True)
class _Span:
    block: SourceBlock
    source_start: int
    source_end: int
    text: str
    is_overlap: bool = False


def build_text_chunks(
    source_blocks: Iterable[SourceBlock],
    config: ChunkingConfig | None = None,
) -> list[TextChunk]:
    cfg = config or ChunkingConfig()
    blocks_by_page: dict[int, list[SourceBlock]] = {}
    for block in source_blocks:
        if block.text.strip():
            blocks_by_page.setdefault(block.page_number, []).append(block)

    chunks: list[TextChunk] = []
    for page_number in sorted(blocks_by_page):
        page_blocks = sorted(
            blocks_by_page[page_number],
            key=lambda block: (block.page_index, block.block_index),
        )
        page_spans = _blocks_to_spans(page_blocks, cfg)
        groups = _group_spans(page_spans, cfg)
        for group_index, group in enumerate(groups):
            overlap = _overlap_from_group(groups, group_index, cfg.overlap_chars)
            chunks.append(_build_chunk([*overlap, *group], len(chunks) + 1))

    return chunks


def _blocks_to_spans(
    blocks: list[SourceBlock],
    config: ChunkingConfig,
) -> list[_Span]:
    spans: list[_Span] = []
    for block in blocks:
        text = normalize_chunk_text(block.text)
        if not text:
            continue

        start = 0
        while start < len(text):
            end = min(start + config.max_chars, len(text))
            if end < len(text):
                end = _find_split_boundary(text, start, end, config.min_chars)
            span_text = text[start:end].strip()
            if span_text:
                leading_trim = len(text[start:end]) - len(text[start:end].lstrip())
                adjusted_start = start + leading_trim
                spans.append(
                    _Span(
                        block=block,
                        source_start=adjusted_start,
                        source_end=adjusted_start + len(span_text),
                        text=span_text,
                    )
                )
            start = max(end, start + 1)
    return spans


def normalize_chunk_text(text: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", text).casefold().strip()


def _find_split_boundary(text: str, start: int, end: int, min_chars: int) -> int:
    minimum = start + min_chars
    candidates = [
        text.rfind("\n\n", start, end),
        text.rfind("\n", start, end),
        text.rfind(". ", start, end),
        text.rfind("; ", start, end),
        text.rfind(", ", start, end),
        text.rfind(" ", start, end),
    ]
    boundary = max(candidates)
    if boundary >= minimum:
        return boundary + 1
    return end


def _group_spans(spans: list[_Span], config: ChunkingConfig) -> list[list[_Span]]:
    groups: list[list[_Span]] = []
    current: list[_Span] = []

    for span in spans:
        proposed_length = _joined_length([*current, span])
        current_length = _joined_length(current)
        should_close = (
            current
            and proposed_length > config.target_chars
            and current_length >= config.min_chars
        )
        if should_close:
            groups.append(current)
            current = [span]
        else:
            current.append(span)

    if current:
        groups.append(current)

    if len(groups) > 1 and _joined_length(groups[-1]) < config.min_chars:
        previous = groups[-2]
        last = groups[-1]
        if _joined_length([*previous, *last]) <= config.max_chars:
            groups[-2] = [*previous, *last]
            groups.pop()
        else:
            groups[-2], groups[-1] = _rebalance_groups(previous, last, config)

    return groups


def _rebalance_groups(
    previous: list[_Span],
    last: list[_Span],
    config: ChunkingConfig,
) -> tuple[list[_Span], list[_Span]]:
    combined = [*previous, *last]
    if len(combined) < 2:
        return previous, last

    candidates: list[tuple[int, int, int]] = []
    for split_index in range(1, len(combined)):
        left = combined[:split_index]
        right = combined[split_index:]
        left_len = _joined_length(left)
        right_len = _joined_length(right)
        if right_len >= config.min_chars and left_len <= config.max_chars:
            score = abs(left_len - right_len)
            candidates.append((score, split_index, right_len))

    if not candidates:
        return previous, last

    _, split_index, _ = min(candidates)
    return combined[:split_index], combined[split_index:]


def _overlap_from_group(
    groups: list[list[_Span]],
    group_index: int,
    overlap_chars: int,
) -> list[_Span]:
    if group_index == 0 or overlap_chars <= 0:
        return []

    previous = groups[group_index - 1]
    overlap: list[_Span] = []
    remaining = overlap_chars
    for span in reversed(previous):
        if remaining <= 0:
            break
        if len(span.text) <= remaining:
            overlap.append(
                _Span(
                    block=span.block,
                    source_start=span.source_start,
                    source_end=span.source_end,
                    text=span.text,
                    is_overlap=True,
                )
            )
            remaining -= len(span.text)
            continue

        source_start = span.source_end - remaining
        overlap.append(
            _Span(
                block=span.block,
                source_start=source_start,
                source_end=span.source_end,
                text=span.text[-remaining:],
                is_overlap=True,
            )
        )
        remaining = 0

    return list(reversed(overlap))


def _build_chunk(spans: list[_Span], chunk_index: int) -> TextChunk:
    first = spans[0].block
    page_numbers = [span.block.page_number for span in spans]
    text_parts: list[str] = []
    segments: list[dict[str, object]] = []
    offset = 0
    methods = {span.block.method for span in spans if not span.is_overlap}

    for span in spans:
        if text_parts:
            offset += len(TEXT_SEPARATOR)
        text_parts.append(span.text)
        text_start = offset
        text_end = text_start + len(span.text)
        offset = text_end

        segments.append(
            {
                "source_block_id": span.block.source_block_id,
                "page_number": span.block.page_number,
                "page_index": span.block.page_index,
                "block_index": span.block.block_index,
                "block_type": span.block.block_type,
                "bbox": span.block.bbox,
                "method": span.block.method,
                "routing_reason": span.block.routing_reason,
                "metadata": span.block.metadata,
                "source_text_start": span.source_start,
                "source_text_end": span.source_end,
                "chunk_text_start": text_start,
                "chunk_text_end": text_end,
                "is_overlap": span.is_overlap,
            }
        )

    text = TEXT_SEPARATOR.join(text_parts)
    source_map = {
        "file_id": first.file_id,
        "source_type": first.source_type,
        "source_uri": first.source_uri,
        "file_name": first.file_name,
        "original_path": first.original_path,
        "page_start": min(page_numbers),
        "page_end": max(page_numbers),
        "segments": segments,
    }
    method = next(iter(methods), PYMUPDF_METHOD)
    return TextChunk(
        chunk_id=f"{first.file_id}:c{chunk_index:06d}",
        run_id=first.run_id,
        file_id=first.file_id,
        chunk_index=chunk_index,
        page_start=min(page_numbers),
        page_end=max(page_numbers),
        text=text,
        text_hash_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_map=source_map,
        method=method,
    )


def _joined_length(spans: list[_Span]) -> int:
    if not spans:
        return 0
    return sum(len(span.text) for span in spans) + (
        len(TEXT_SEPARATOR) * (len(spans) - 1)
    )
