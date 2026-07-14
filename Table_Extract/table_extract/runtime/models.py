from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from table_extract.models._validation import require_non_blank


SCHEMA_VERSION = "2.0"
QUEUE_TABLES = "Queue-Tables"
ROUTED_EVENT_TYPE = "file.routed"
ROUTE_TABLE = "table"

SOURCE_LOCAL = "local"
SOURCE_DRIVE = "drive"

CSV_EXTENSIONS = {".csv"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
SUPPORTED_EXTENSIONS = CSV_EXTENSIONS | EXCEL_EXTENSIONS

GOOGLE_SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
TABLE_MIME_TYPES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    GOOGLE_SPREADSHEET_MIME_TYPE,
}


@dataclass(frozen=True)
class TableRoutedMessage:
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
    route_type: str
    reason: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TableRoutedMessage":
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
            "route_type",
            "reason",
        )
        for field_name in required_fields:
            if payload.get(field_name) is None or (
                field_name != "extension" and payload.get(field_name) == ""
            ):
                raise ValueError(f"Missing required message field: {field_name}")

        schema_version = str(payload["schema_version"])
        event_type = str(payload["event_type"])
        destination_queue_name = str(payload["destination_queue_name"])
        route_type = str(payload["route_type"])
        extension = normalize_extension(str(payload.get("extension") or ""))
        mime_type = _optional_text(payload.get("mime_type"))
        source_type = _optional_text(payload.get("source_type")) or SOURCE_LOCAL
        source_uri = _payload_source_uri(payload)

        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version: {schema_version}")
        if event_type != ROUTED_EVENT_TYPE:
            raise ValueError(f"Unsupported event_type: {event_type}")
        if destination_queue_name != QUEUE_TABLES:
            raise ValueError(
                f"Unsupported destination_queue_name: {destination_queue_name}"
            )
        if route_type != ROUTE_TABLE:
            raise ValueError(f"Unsupported route_type: {route_type}")
        if extension and extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported table extension: {extension}")
        if not extension and normalize_mime_type(mime_type) not in TABLE_MIME_TYPES:
            raise ValueError(
                f"Unsupported table extension or MIME type: {extension} {mime_type}"
            )
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
            extension=extension,
            mime_type=mime_type,
            checksum_sha256=_optional_text(payload.get("checksum_sha256")),
            content_hash=_optional_text(payload.get("content_hash")),
            etag=_optional_text(payload.get("etag")),
            size_bytes=int(size_value) if size_value is not None else None,
            source_queue_name=str(payload["source_queue_name"]),
            destination_queue_name=destination_queue_name,
            route_type=route_type,
            reason=str(payload["reason"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StoredFile:
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
    materialized_local_path: str | None = None

    def __post_init__(self) -> None:
        require_non_blank(self.file_id, "file_id")
        require_non_blank(self.run_id, "run_id")
        require_non_blank(self.source_type, "source_type")
        require_non_blank(self.source_uri, "source_uri")
        require_non_blank(self.file_name, "file_name")
        require_non_blank(self.relative_path, "relative_path")

    @property
    def materialized_path(self) -> str | None:
        return local_path_from_source_uri(self.source_uri) or self.materialized_local_path

    @property
    def original_path(self) -> str:
        return local_path_from_source_uri(self.source_uri) or self.source_uri

    @property
    def is_materialized_local(self) -> bool:
        return self.materialized_path is not None

    @property
    def is_google_native_spreadsheet(self) -> bool:
        return normalize_mime_type(self.mime_type) == GOOGLE_SPREADSHEET_MIME_TYPE

    def with_materialized_path(self, path: str) -> "StoredFile":
        return StoredFile(
            file_id=self.file_id,
            run_id=self.run_id,
            source_type=self.source_type,
            source_uri=self.source_uri,
            external_id=self.external_id,
            file_name=self.file_name,
            relative_path=self.relative_path,
            extension=self.extension,
            mime_type=self.mime_type,
            size_bytes=self.size_bytes,
            checksum_sha256=self.checksum_sha256,
            content_hash=self.content_hash,
            etag=self.etag,
            materialized_local_path=path,
        )


@dataclass(frozen=True)
class FileScanContext:
    run_id: str
    stored_file: StoredFile
    local_path: str
    source_uri: str
    is_temporary: bool
    message: TableRoutedMessage | None = None
    lease_id: str | None = None
    routing_decision_id: str | None = None

    def __post_init__(self) -> None:
        require_non_blank(self.run_id, "run_id")
        require_non_blank(self.local_path, "local_path")
        require_non_blank(self.source_uri, "source_uri")

    @property
    def file_id(self) -> str:
        return self.stored_file.file_id

    @property
    def file_name(self) -> str:
        return self.stored_file.file_name

    @property
    def relative_path(self) -> str:
        return self.stored_file.relative_path

    @property
    def extension(self) -> str:
        return normalize_extension(self.stored_file.extension)

    @property
    def mime_type(self) -> str | None:
        return self.stored_file.mime_type


@dataclass(frozen=True)
class TableExtractionRecord:
    file_id: str
    run_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    processing_seconds: float | None
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    cpu_total_seconds: float | None = None
    peak_memory_mb: float | None = None
    routing_decision_id: str | None = None
    table_count: int | None = None
    column_count: int | None = None
    finding_count: int | None = None
    profile_json_path: str | None = None
    discovery_json_path: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        require_non_blank(self.file_id, "file_id")
        require_non_blank(self.run_id, "run_id")
        require_non_blank(self.status, "status")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be before started_at")
        for field_name in (
            "processing_seconds",
            "cpu_user_seconds",
            "cpu_system_seconds",
            "cpu_total_seconds",
            "peak_memory_mb",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        for field_name in ("table_count", "column_count", "finding_count"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")


def normalize_extension(extension: str | None) -> str:
    if not extension:
        return ""
    normalized = extension.strip().casefold()
    if normalized and not normalized.startswith("."):
        return f".{normalized}"
    return normalized


def normalize_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return ""
    return mime_type.strip().casefold()


def local_path_from_source_uri(source_uri: str | None) -> str | None:
    if not source_uri:
        return None
    if source_uri.startswith("local://"):
        path = source_uri[len("local://") :]
        return path or None
    if "://" not in source_uri:
        return source_uri
    return None


def _payload_source_uri(payload: dict[str, Any]) -> str:
    source_uri = _optional_text(payload.get("source_uri"))
    if source_uri:
        return source_uri
    original_path = _optional_text(payload.get("original_path"))
    if original_path:
        return original_path
    return ""


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def safe_file_stem(file_name: str, fallback: str) -> str:
    stem = Path(file_name).stem.strip()
    return stem or fallback
