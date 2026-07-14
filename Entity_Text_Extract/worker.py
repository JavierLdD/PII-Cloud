from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import time

from config import DEFAULT_TEMP_DIR
from models import (
    QUEUE_ENTITY,
    ChunkEntityResult,
    ChunksReadyMessage,
    EntityDetector,
    EntityExtractionRecord,
    EntityRepository,
    FileEntityResult,
    QueueConsumer,
    RawEntity,
    TextChunk,
    WrittenEntityResults,
)
from resource_metrics import capture_resource_usage, resource_usage_delta


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.append(str(PROJECT_DIR))

from Entity_Text_Filter.resolver import filter_raw_result
from Entity_Text_Filter.writer import (
    filtered_result_output_path,
    write_filtered_result_json,
)
from writer import raw_result_output_path, write_raw_result_json


def process_entity_payload(
    payload: dict[str, object],
    repository: EntityRepository,
    detector: EntityDetector,
    output_dir: str | Path | None = None,
    mask_text: bool = False,
) -> WrittenEntityResults:
    message = ChunksReadyMessage.from_payload(payload)
    return process_file_id(
        message.file_id,
        repository=repository,
        detector=detector,
        output_dir=output_dir,
        mask_text=mask_text,
    )


def process_file_id(
    file_id: str,
    repository: EntityRepository,
    detector: EntityDetector,
    output_dir: str | Path | None = None,
    mask_text: bool = False,
) -> WrittenEntityResults:
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    started_resources = capture_resource_usage()
    result = build_file_entity_result(file_id, repository, detector)
    filtered = filter_raw_result(result.to_dict(mask_text=False))
    completed_at = datetime.now(timezone.utc)
    processing_seconds = round(time.perf_counter() - started_perf, 6)
    resources = resource_usage_delta(started_resources)

    raw_output_path = raw_result_output_path(result, output_dir=output_dir)
    filtered_output_path = filtered_result_output_path(filtered, output_dir=output_dir)
    result = result.with_metrics(
        entity_started_at=started_at,
        entity_completed_at=completed_at,
        entity_processing_seconds=processing_seconds,
        cpu_user_seconds=resources.cpu_user_seconds,
        cpu_system_seconds=resources.cpu_system_seconds,
        cpu_total_seconds=resources.cpu_total_seconds,
        peak_memory_mb=resources.peak_memory_mb,
    ).with_output_paths(
        raw_json_path=str(raw_output_path),
        filtered_json_path=str(filtered_output_path),
    )
    filtered.entity_started_at = started_at
    filtered.entity_completed_at = completed_at
    filtered.entity_processing_seconds = processing_seconds
    filtered.cpu_user_seconds = resources.cpu_user_seconds
    filtered.cpu_system_seconds = resources.cpu_system_seconds
    filtered.cpu_total_seconds = resources.cpu_total_seconds
    filtered.peak_memory_mb = resources.peak_memory_mb
    filtered.raw_json_path = str(raw_output_path)
    filtered.filtered_json_path = str(filtered_output_path)

    raw_output_path = write_raw_result_json(result, output_dir=output_dir)
    filtered_output_path = write_filtered_result_json(
        filtered,
        output_dir=output_dir,
        mask_text=False,
    )
    _save_entity_record(
        repository,
        EntityExtractionRecord(
            file_id=result.source_file.file_id,
            run_id=result.source_file.run_id,
            status="entity_extraction_completed",
            started_at=started_at,
            completed_at=completed_at,
            processing_seconds=processing_seconds,
            cpu_user_seconds=resources.cpu_user_seconds,
            cpu_system_seconds=resources.cpu_system_seconds,
            cpu_total_seconds=resources.cpu_total_seconds,
            peak_memory_mb=resources.peak_memory_mb,
            raw_entity_count=result.entity_count,
            accepted_entity_count=len(filtered.accepted_entities),
            raw_json_path=str(raw_output_path),
            filtered_json_path=str(filtered_output_path),
        ),
    )
    _save_accepted_entities(repository, result, filtered.accepted_entities)
    _cleanup_released_paths(_release_materialization_lease(repository, file_id))
    return WrittenEntityResults(
        raw_result=result.with_output_paths(raw_json_path=str(raw_output_path)),
        filtered_result=filtered,
        raw_output_path=str(raw_output_path),
        filtered_output_path=str(filtered_output_path),
    )


def build_file_entity_result(
    file_id: str,
    repository: EntityRepository,
    detector: EntityDetector,
) -> FileEntityResult:
    source_file = repository.get_file(file_id)
    if source_file is None:
        raise ValueError(f"File not found in database: {file_id}")

    chunk_results: list[ChunkEntityResult] = []
    chunks = repository.list_ready_chunks(file_id)
    detect_many = getattr(detector, "detect_many", None)
    if callable(detect_many):
        entities_by_chunk = detect_many([chunk.text for chunk in chunks])
        if len(entities_by_chunk) != len(chunks):
            raise ValueError("Detector returned an unexpected chunk result count")
    else:
        entities_by_chunk = [detector.detect(chunk.text) for chunk in chunks]

    for chunk, raw_entities in zip(chunks, entities_by_chunk):
        entities = [
            entity.with_trace(build_trace(entity, chunk))
            for entity in raw_entities
        ]
        chunk_results.append(ChunkEntityResult(chunk=chunk, entities=entities))

    return FileEntityResult(source_file=source_file, chunks=chunk_results)


def run_entity_worker(
    repository: EntityRepository,
    detector: EntityDetector,
    consumer: QueueConsumer,
    source_queue_name: str = QUEUE_ENTITY,
    output_dir: str | Path | None = None,
    mask_text: bool = False,
    max_messages: int | None = None,
    requeue_messages: bool = False,
) -> None:
    def handle_payload(payload: dict[str, object]) -> None:
        written = process_entity_payload(
            payload,
            repository=repository,
            detector=detector,
            output_dir=output_dir,
            mask_text=mask_text,
        )
        print(
            "processed_entities "
            f"file_id={written.result.source_result.get('file_id')} "
            f"raw={written.result.raw_entity_count} "
            f"accepted={len(written.result.accepted_entities)} "
            f"raw_output={written.raw_output_path} "
            f"filtered_output={written.filtered_output_path} "
            f"processing_seconds={written.result.entity_processing_seconds} "
            f"cpu_total_seconds={written.result.cpu_total_seconds} "
            f"peak_memory_mb={written.result.peak_memory_mb}"
        )

    consumer.consume(
        source_queue_name,
        handle_payload,
        max_messages=max_messages,
        requeue_messages=requeue_messages,
    )


def build_trace(entity: RawEntity, chunk: TextChunk) -> list[dict[str, object]]:
    segments = chunk.source_map.get("segments", [])
    if not isinstance(segments, list):
        return []

    trace: list[dict[str, object]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        chunk_start = _int_or_none(segment.get("chunk_text_start"))
        chunk_end = _int_or_none(segment.get("chunk_text_end"))
        source_start = _int_or_none(segment.get("source_text_start"))
        if chunk_start is None or chunk_end is None or source_start is None:
            continue

        overlap_start = max(entity.start, chunk_start)
        overlap_end = min(entity.end, chunk_end)
        if overlap_start >= overlap_end:
            continue

        trace.append(
            {
                "source_block_id": segment.get("source_block_id"),
                "page_number": segment.get("page_number"),
                "page_index": segment.get("page_index"),
                "block_index": segment.get("block_index"),
                "block_type": segment.get("block_type"),
                "bbox": segment.get("bbox"),
                "method": segment.get("method"),
                "routing_reason": segment.get("routing_reason"),
                "is_overlap": bool(segment.get("is_overlap", False)),
                "chunk_text_start": chunk_start,
                "chunk_text_end": chunk_end,
                "entity_chunk_start": overlap_start,
                "entity_chunk_end": overlap_end,
                "source_text_start": source_start,
                "source_text_end": segment.get("source_text_end"),
                "entity_source_start": source_start + (overlap_start - chunk_start),
                "entity_source_end": source_start + (overlap_end - chunk_start),
            }
        )
    return trace


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _save_entity_record(
    repository: EntityRepository,
    record: EntityExtractionRecord,
) -> None:
    save = getattr(repository, "save_entity_extraction_record", None)
    if callable(save):
        save(record)


def _save_accepted_entities(
    repository: EntityRepository,
    result: FileEntityResult,
    accepted_entities: list[object],
) -> None:
    save = getattr(repository, "save_accepted_entities", None)
    if callable(save):
        save(
            file_id=result.source_file.file_id,
            run_id=result.source_file.run_id,
            accepted_entities=accepted_entities,
        )


def _release_materialization_lease(
    repository: EntityRepository,
    file_id: str,
) -> list[str]:
    release = getattr(repository, "release_materialization_lease", None)
    if not callable(release):
        return []
    return list(release(file_id))


def _cleanup_released_paths(raw_paths: list[str]) -> None:
    for raw_path in raw_paths:
        path = Path(raw_path)
        try:
            if path.exists():
                path.unlink()
                _remove_empty_parents(path.parent, DEFAULT_TEMP_DIR)
        except OSError:
            continue


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    stop = stop_at.resolve()
    current = path
    while current.exists() and current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
