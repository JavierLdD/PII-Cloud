from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


SCHEMA_VERSION = "2.0"
QUEUE_ENTITY = "Queue-Entity"
CHUNKS_READY_EVENT_TYPE = "file.chunks_ready"
CHUNK_READY_STATUS = "ready"
SOURCE_LOCAL = "local"


@dataclass(frozen=True)
class ChunksReadyMessage:
    schema_version: str
    event_type: str
    run_id: str
    file_id: str
    routing_decision_id: str
    source_type: str
    source_uri: str
    external_id: str | None
    file_name: str
    relative_path: str
    extension: str
    mime_type: str | None
    checksum_sha256: str | None
    content_hash: str | None
    etag: str | None
    size_bytes: int | None
    source_queue_name: str
    destination_queue_name: str
    chunk_count: int
    page_count: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ChunksReadyMessage":
        required_fields = (
            "schema_version",
            "event_type",
            "run_id",
            "file_id",
            "routing_decision_id",
            "file_name",
            "relative_path",
            "extension",
            "source_queue_name",
            "destination_queue_name",
            "chunk_count",
            "page_count",
        )
        for field_name in required_fields:
            if payload.get(field_name) is None or (
                field_name != "extension" and payload.get(field_name) == ""
            ):
                raise ValueError(f"Missing required message field: {field_name}")

        schema_version = str(payload["schema_version"])
        event_type = str(payload["event_type"])
        destination_queue_name = str(payload["destination_queue_name"])
        chunk_count = int(payload["chunk_count"])
        page_count = int(payload["page_count"])
        source_type = _optional_text(payload.get("source_type")) or SOURCE_LOCAL
        source_uri = _payload_source_uri(payload)

        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version: {schema_version}")
        if event_type != CHUNKS_READY_EVENT_TYPE:
            raise ValueError(f"Unsupported event_type: {event_type}")
        if destination_queue_name != QUEUE_ENTITY:
            raise ValueError(
                f"Unsupported destination_queue_name: {destination_queue_name}"
            )
        if chunk_count < 0:
            raise ValueError(f"Invalid chunk_count: {chunk_count}")
        if page_count < 0:
            raise ValueError(f"Invalid page_count: {page_count}")
        if not source_uri:
            raise ValueError("Missing required message field: source_uri")

        size_value = payload.get("size_bytes")
        return cls(
            schema_version=schema_version,
            event_type=event_type,
            run_id=str(payload["run_id"]),
            file_id=str(payload["file_id"]),
            routing_decision_id=str(payload["routing_decision_id"]),
            source_type=source_type,
            source_uri=source_uri,
            external_id=_optional_text(payload.get("external_id")),
            file_name=str(payload["file_name"]),
            relative_path=str(payload["relative_path"]),
            extension=str(payload.get("extension") or ""),
            mime_type=_optional_text(payload.get("mime_type")),
            checksum_sha256=_optional_text(payload.get("checksum_sha256")),
            content_hash=_optional_text(payload.get("content_hash")),
            etag=_optional_text(payload.get("etag")),
            size_bytes=int(size_value) if size_value is not None else None,
            source_queue_name=str(payload["source_queue_name"]),
            destination_queue_name=destination_queue_name,
            chunk_count=chunk_count,
            page_count=page_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceFile:
    file_id: str
    run_id: str
    source_type: str
    source_uri: str
    external_id: str | None
    file_name: str
    relative_path: str
    extension: str
    mime_type: str | None
    size_bytes: int | None
    checksum_sha256: str | None
    content_hash: str | None = None
    etag: str | None = None
    text_extraction_status: str | None = None
    expected_chunk_count: int | None = None

    @property
    def materialized_path(self) -> str | None:
        return local_path_from_source_uri(self.source_uri)

    @property
    def original_path(self) -> str:
        return self.materialized_path or self.source_uri


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    run_id: str
    file_id: str
    chunk_index: int
    page_start: int
    page_end: int
    text: str
    text_hash_sha256: str
    source_map: dict[str, Any]
    method: str
    status: str = CHUNK_READY_STATUS


@dataclass(frozen=True)
class RawEntity:
    entity_type: str
    raw_entity_type: str
    source: str
    text: str
    start: int
    end: int
    score: float
    normalized_value: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)

    def with_trace(self, trace: list[dict[str, Any]]) -> "RawEntity":
        return replace(self, trace=trace)

    def to_dict(self, mask_text: bool = False) -> dict[str, Any]:
        data = {
            "entity_type": self.entity_type,
            "raw_entity_type": self.raw_entity_type,
            "source": self.source,
            "text": mask_entity_text(self.text) if mask_text else self.text,
            "start": self.start,
            "end": self.end,
            "score": round(float(self.score), 6),
            "normalized_value": self.normalized_value,
            "trace": self.trace,
        }
        return data


@dataclass(frozen=True)
class ChunkEntityResult:
    chunk: TextChunk
    entities: list[RawEntity]

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def to_dict(self, mask_text: bool = False) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "chunk_index": self.chunk.chunk_index,
            "page_start": self.chunk.page_start,
            "page_end": self.chunk.page_end,
            "method": self.chunk.method,
            "text_hash_sha256": self.chunk.text_hash_sha256,
            "entity_count": self.entity_count,
            "entities": [
                entity.to_dict(mask_text=mask_text) for entity in self.entities
            ],
        }


@dataclass(frozen=True)
class FileEntityResult:
    source_file: SourceFile
    chunks: list[ChunkEntityResult]
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    entity_started_at: datetime | None = None
    entity_completed_at: datetime | None = None
    entity_processing_seconds: float | None = None
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    cpu_total_seconds: float | None = None
    peak_memory_mb: float | None = None
    raw_json_path: str | None = None
    filtered_json_path: str | None = None

    @property
    def entity_count(self) -> int:
        return sum(chunk.entity_count for chunk in self.chunks)

    def with_metrics(
        self,
        *,
        entity_started_at: datetime,
        entity_completed_at: datetime,
        entity_processing_seconds: float,
        cpu_user_seconds: float | None = None,
        cpu_system_seconds: float | None = None,
        cpu_total_seconds: float | None = None,
        peak_memory_mb: float | None = None,
    ) -> "FileEntityResult":
        return replace(
            self,
            entity_started_at=entity_started_at,
            entity_completed_at=entity_completed_at,
            entity_processing_seconds=entity_processing_seconds,
            cpu_user_seconds=cpu_user_seconds,
            cpu_system_seconds=cpu_system_seconds,
            cpu_total_seconds=cpu_total_seconds,
            peak_memory_mb=peak_memory_mb,
        )

    def with_output_paths(
        self,
        *,
        raw_json_path: str | None = None,
        filtered_json_path: str | None = None,
    ) -> "FileEntityResult":
        return replace(
            self,
            raw_json_path=raw_json_path if raw_json_path is not None else self.raw_json_path,
            filtered_json_path=(
                filtered_json_path
                if filtered_json_path is not None
                else self.filtered_json_path
            ),
        )

    def to_dict(self, mask_text: bool = False) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.source_file.run_id,
            "file_id": self.source_file.file_id,
            "source_type": self.source_file.source_type,
            "source_uri": self.source_file.source_uri,
            "external_id": self.source_file.external_id,
            "file_name": self.source_file.file_name,
            "relative_path": self.source_file.relative_path,
            "extension": self.source_file.extension,
            "mime_type": self.source_file.mime_type,
            "size_bytes": self.source_file.size_bytes,
            "checksum_sha256": self.source_file.checksum_sha256,
            "content_hash": self.source_file.content_hash,
            "etag": self.source_file.etag,
            "chunk_count": len(self.chunks),
            "entity_count": self.entity_count,
            "generated_at": self.generated_at.isoformat(),
            "entity_started_at": (
                self.entity_started_at.isoformat()
                if self.entity_started_at is not None
                else None
            ),
            "entity_completed_at": (
                self.entity_completed_at.isoformat()
                if self.entity_completed_at is not None
                else None
            ),
            "entity_processing_seconds": self.entity_processing_seconds,
            "cpu_user_seconds": self.cpu_user_seconds,
            "cpu_system_seconds": self.cpu_system_seconds,
            "cpu_total_seconds": self.cpu_total_seconds,
            "peak_memory_mb": self.peak_memory_mb,
            "raw_json_path": self.raw_json_path,
            "filtered_json_path": self.filtered_json_path,
            "chunks": [chunk.to_dict(mask_text=mask_text) for chunk in self.chunks],
        }


@dataclass(frozen=True)
class EntityExtractionRecord:
    file_id: str
    run_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    processing_seconds: float | None
    cpu_user_seconds: float | None
    cpu_system_seconds: float | None
    cpu_total_seconds: float | None
    peak_memory_mb: float | None
    raw_entity_count: int
    accepted_entity_count: int
    raw_json_path: str | None
    filtered_json_path: str | None
    error: str | None = None


@dataclass(frozen=True)
class WrittenEntityResults:
    raw_result: FileEntityResult
    filtered_result: object
    raw_output_path: str
    filtered_output_path: str

    @property
    def result(self) -> object:
        return self.filtered_result

    @property
    def output_path(self) -> str:
        return self.filtered_output_path


class EntityRepository(Protocol):
    def get_file(self, file_id: str) -> SourceFile | None:
        ...

    def list_ready_chunks(self, file_id: str) -> list[TextChunk]:
        ...

    def save_entity_extraction_record(self, record: EntityExtractionRecord) -> None:
        ...

    def save_accepted_entities(
        self,
        *,
        file_id: str,
        run_id: str,
        accepted_entities: list[object],
    ) -> None:
        ...

    def release_materialization_lease(self, file_id: str) -> list[str]:
        ...


class EntityDetector(Protocol):
    def detect(self, text: str) -> list[RawEntity]:
        ...


class QueueConsumer(Protocol):
    def consume(
        self,
        queue_name: str,
        handle_payload: Callable[[dict[str, Any]], None],
        max_messages: int | None = None,
        requeue_messages: bool = False,
    ) -> None:
        ...


def mask_entity_text(text: str) -> str:
    if not text:
        return text
    if len(text) <= 2:
        return "*" * len(text)
    return f"{text[0]}{'*' * (len(text) - 2)}{text[-1]}"


def local_path_from_source_uri(source_uri: str | None) -> str | None:
    if not source_uri:
        return None
    if source_uri.startswith("local://"):
        path = source_uri[len("local://") :]
        return path or None
    if "://" not in source_uri:
        return source_uri
    return None


def source_uri_from_local_path(path: object) -> str | None:
    text = _optional_text(path)
    if not text:
        return None
    return f"local://{text}"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_source_uri(payload: dict[str, Any]) -> str | None:
    return _optional_text(payload.get("source_uri")) or source_uri_from_local_path(
        payload.get("original_path")
    )
