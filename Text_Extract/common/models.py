from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Protocol


SCHEMA_VERSION = "2.0"
ROUTER_SCHEMA_VERSION = "2.0"

SOURCE_LOCAL = "local"
SOURCE_DRIVE = "drive"

QUEUE_PDF = "Queue-PDF"
QUEUE_OCR = "Queue-OCR"
QUEUE_OCR_URGENT = "Queue-OCR-Urgente"
QUEUE_DOC = "Queue-Doc"
QUEUE_ENTITY = "Queue-Entity"
QUEUE_TEXT_POISON = "Queue-Text-Poison"
OCR_QUEUE_NAMES = (QUEUE_OCR_URGENT, QUEUE_OCR)
DESTINATION_QUEUE_NAMES = (*OCR_QUEUE_NAMES, QUEUE_ENTITY)

ROUTED_EVENT_TYPE = "file.routed"
OCR_REQUESTED_EVENT_TYPE = "pdf.page_ocr_requested"
OCR_BATCH_REQUESTED_EVENT_TYPE = "pdf.ocr_batch_requested"
CHUNKS_READY_EVENT_TYPE = "file.chunks_ready"
TEXT_EXTRACT_POISONED_EVENT_TYPE = "file.text_extract_poisoned"

ROUTE_PDF = "pdf"
ROUTE_OCR = "ocr"
ROUTE_DOC = "doc"

OCR_INPUT_PDF_PAGE = "pdf_page"
OCR_INPUT_PDF_BATCH = "pdf_batch"
OCR_INPUT_IMAGE_FILE = "image_file"
PDF_MIME_TYPES = {"application/pdf"}
IMAGE_MIME_PREFIX = "image/"
OCR_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
DOC_EXTENSIONS = {".txt", ".docx"}
GOOGLE_NATIVE_DOC_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
}

PYMUPDF_METHOD = "pymupdf"
OCR_METHOD = "ocr"
BOTH_METHOD = "both"
DOC_METHOD = "doc"
SUPPORTED_PAGE_METHODS = (PYMUPDF_METHOD, OCR_METHOD, BOTH_METHOD, DOC_METHOD)

PAGE_COMPLETED_STATUS = "completed"
PAGE_PENDING_OCR_STATUS = "pending_ocr"
PAGE_FAILED_STATUS = "failed"

TEXT_EXTRACTION_COMPLETED_STATUS = "text_extraction_completed"
WAITING_OCR_STATUS = "waiting_ocr"
TEXT_EXTRACTION_FAILED_STATUS = "text_extraction_failed"

CHUNK_READY_STATUS = "ready"

PDF_ATTEMPT_ACTIVE_STATUS = "active"
PDF_ATTEMPT_COMPLETED_STATUS = "completed"
PDF_ATTEMPT_QUARANTINED_STATUS = "quarantined"

SOURCE_QUEUE_NAME = QUEUE_PDF


@dataclass(frozen=True)
class RoutedFileMessage:
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
    def from_payload(cls, payload: dict[str, Any]) -> "RoutedFileMessage":
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
        if destination_queue_name != QUEUE_PDF:
            raise ValueError(
                f"Unsupported destination_queue_name: {destination_queue_name}"
            )
        if route_type != ROUTE_PDF:
            raise ValueError(f"Unsupported route_type: {route_type}")
        if extension and extension != ".pdf":
            raise ValueError(f"Unsupported PDF extension: {extension}")
        if not extension and mime_type not in PDF_MIME_TYPES:
            raise ValueError(f"Unsupported PDF MIME type: {mime_type}")
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
    def is_google_native(self) -> bool:
        mime_type = _optional_text(self.mime_type)
        return bool(
            mime_type and mime_type.startswith("application/vnd.google-apps.")
        )

    def with_materialized_path(self, path: str) -> "StoredFile":
        return replace(self, materialized_local_path=path)


@dataclass(frozen=True)
class OcrPageWork:
    page_number: int
    page_index: int
    reason: str
    page_method: str
    routing_char_count: int
    routing_word_count: int
    routing_image_ratio: float
    routing_largest_image_ratio: float
    ocr_requested_at: datetime | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OcrPageWork":
        if payload.get("page_number") is None or payload.get("page_index") is None:
            raise ValueError("OCR page entries require page_number and page_index")
        page_number = int(payload["page_number"])
        page_index = int(payload["page_index"])
        if page_number < 1:
            raise ValueError(f"Invalid page_number: {page_number}")
        if page_index < 0:
            raise ValueError(f"Invalid page_index: {page_index}")
        return cls(
            page_number=page_number,
            page_index=page_index,
            reason=str(payload.get("reason") or ""),
            page_method=str(payload.get("page_method") or OCR_METHOD),
            routing_char_count=int(payload.get("routing_char_count") or 0),
            routing_word_count=int(payload.get("routing_word_count") or 0),
            routing_image_ratio=float(payload.get("routing_image_ratio") or 0.0),
            routing_largest_image_ratio=float(
                payload.get("routing_largest_image_ratio") or 0.0
            ),
            ocr_requested_at=_optional_datetime(payload.get("ocr_requested_at")),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "page_index": self.page_index,
            "reason": self.reason,
            "page_method": self.page_method,
            "routing_char_count": self.routing_char_count,
            "routing_word_count": self.routing_word_count,
            "routing_image_ratio": self.routing_image_ratio,
            "routing_largest_image_ratio": self.routing_largest_image_ratio,
            "ocr_requested_at": (
                self.ocr_requested_at.isoformat()
                if self.ocr_requested_at is not None
                else None
            ),
        }


@dataclass(frozen=True)
class OcrWorkMessage:
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
    input_kind: str
    page_number: int
    page_index: int
    page_method: str
    routing_char_count: int
    routing_word_count: int
    routing_image_ratio: float
    routing_largest_image_ratio: float
    ocr_requested_at: datetime | None = None
    pages: tuple[OcrPageWork, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OcrWorkMessage":
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
        if destination_queue_name not in OCR_QUEUE_NAMES:
            raise ValueError(
                f"Unsupported destination_queue_name: {destination_queue_name}"
            )
        if route_type != ROUTE_OCR:
            raise ValueError(f"Unsupported route_type: {route_type}")

        pages: tuple[OcrPageWork, ...] = ()
        if event_type == OCR_REQUESTED_EVENT_TYPE:
            input_kind = OCR_INPUT_PDF_PAGE
            if extension and extension != ".pdf":
                raise ValueError(f"Unsupported OCR PDF extension: {extension}")
            if not extension and mime_type not in PDF_MIME_TYPES:
                raise ValueError(f"Unsupported OCR PDF MIME type: {mime_type}")
            if payload.get("page_number") is None or payload.get("page_index") is None:
                raise ValueError("OCR PDF messages require page_number and page_index")
            page_number = int(payload["page_number"])
            page_index = int(payload["page_index"])
            pages = (
                OcrPageWork.from_payload(
                    {
                        **payload,
                        "reason": payload.get("reason") or "",
                        "page_method": payload.get("page_method") or OCR_METHOD,
                    }
                ),
            )
        elif event_type == OCR_BATCH_REQUESTED_EVENT_TYPE:
            input_kind = OCR_INPUT_PDF_BATCH
            if extension and extension != ".pdf":
                raise ValueError(f"Unsupported OCR PDF extension: {extension}")
            if not extension and mime_type not in PDF_MIME_TYPES:
                raise ValueError(f"Unsupported OCR PDF MIME type: {mime_type}")
            raw_pages = payload.get("pages")
            if not isinstance(raw_pages, list) or not raw_pages:
                raise ValueError("OCR batch messages require a non-empty pages list")
            pages = tuple(
                OcrPageWork.from_payload(item)
                for item in raw_pages
                if isinstance(item, dict)
            )
            if len(pages) != len(raw_pages):
                raise ValueError("OCR batch pages must be objects")
            pages = tuple(sorted(pages, key=lambda page: page.page_index))
            first_page = pages[0]
            page_number = first_page.page_number
            page_index = first_page.page_index
        elif event_type == ROUTED_EVENT_TYPE:
            input_kind = OCR_INPUT_IMAGE_FILE
            if extension and extension not in OCR_IMAGE_EXTENSIONS:
                raise ValueError(f"Unsupported OCR image extension: {extension}")
            if not extension and not (mime_type or "").startswith(IMAGE_MIME_PREFIX):
                raise ValueError(f"Unsupported OCR image MIME type: {mime_type}")
            page_number = 1
            page_index = 0
            pages = (
                OcrPageWork(
                    page_number=page_number,
                    page_index=page_index,
                    reason=str(payload.get("reason") or ""),
                    page_method=OCR_METHOD,
                    routing_char_count=0,
                    routing_word_count=0,
                    routing_image_ratio=0.0,
                    routing_largest_image_ratio=0.0,
                    ocr_requested_at=_optional_datetime(payload.get("ocr_requested_at")),
                ),
            )
        else:
            raise ValueError(f"Unsupported OCR event_type: {event_type}")

        if page_number < 1:
            raise ValueError(f"Invalid page_number: {page_number}")
        if page_index < 0:
            raise ValueError(f"Invalid page_index: {page_index}")
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
            input_kind=input_kind,
            page_number=page_number,
            page_index=page_index,
            page_method=str(payload.get("page_method") or OCR_METHOD),
            routing_char_count=int(payload.get("routing_char_count") or 0),
            routing_word_count=int(payload.get("routing_word_count") or 0),
            routing_image_ratio=float(payload.get("routing_image_ratio") or 0.0),
            routing_largest_image_ratio=float(
                payload.get("routing_largest_image_ratio") or 0.0
            ),
            ocr_requested_at=_optional_datetime(payload.get("ocr_requested_at")),
            pages=pages,
        )

    @property
    def is_pdf_page(self) -> bool:
        return self.input_kind in {OCR_INPUT_PDF_PAGE, OCR_INPUT_PDF_BATCH}

    @property
    def is_pdf_batch(self) -> bool:
        return self.input_kind == OCR_INPUT_PDF_BATCH

    @property
    def is_image_file(self) -> bool:
        return self.input_kind == OCR_INPUT_IMAGE_FILE

    def for_page(self, page: OcrPageWork) -> "OcrWorkMessage":
        return replace(
            self,
            event_type=OCR_REQUESTED_EVENT_TYPE,
            input_kind=OCR_INPUT_PDF_PAGE,
            reason=page.reason,
            page_number=page.page_number,
            page_index=page.page_index,
            page_method=page.page_method,
            routing_char_count=page.routing_char_count,
            routing_word_count=page.routing_word_count,
            routing_image_ratio=page.routing_image_ratio,
            routing_largest_image_ratio=page.routing_largest_image_ratio,
            ocr_requested_at=page.ocr_requested_at,
            pages=(page,),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocRoutedMessage:
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
    size_bytes: int | None
    source_queue_name: str
    destination_queue_name: str
    route_type: str
    reason: str
    mime_type: str | None = None
    checksum_sha256: str | None = None
    content_hash: str | None = None
    etag: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DocRoutedMessage":
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

        if schema_version != ROUTER_SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version: {schema_version}")
        if event_type != ROUTED_EVENT_TYPE:
            raise ValueError(f"Unsupported event_type: {event_type}")
        if destination_queue_name != QUEUE_DOC:
            raise ValueError(
                f"Unsupported destination_queue_name: {destination_queue_name}"
            )
        if route_type != ROUTE_DOC:
            raise ValueError(f"Unsupported route_type: {route_type}")
        if extension not in DOC_EXTENSIONS and mime_type not in GOOGLE_NATIVE_DOC_MIME_TYPES:
            raise ValueError(f"Unsupported document extension: {extension}")
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
            size_bytes=int(size_value) if size_value is not None else None,
            source_queue_name=str(payload["source_queue_name"]),
            destination_queue_name=destination_queue_name,
            route_type=route_type,
            reason=str(payload["reason"]),
            mime_type=mime_type,
            checksum_sha256=_optional_text(payload.get("checksum_sha256")),
            content_hash=_optional_text(payload.get("content_hash")),
            etag=_optional_text(payload.get("etag")),
        )

    @property
    def is_google_native(self) -> bool:
        return self.mime_type in GOOGLE_NATIVE_DOC_MIME_TYPES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PageRoutingDecision:
    page_number: int
    page_index: int
    method: str
    reason: str
    char_count: int
    word_count: int
    total_image_ratio: float
    largest_image_ratio: float


@dataclass(frozen=True)
class SourceBlock:
    source_block_id: str
    run_id: str
    file_id: str
    source_type: str
    source_uri: str
    file_name: str
    original_path: str
    page_number: int
    page_index: int
    block_index: int
    method: str
    routing_reason: str
    block_type: str
    text: str
    bbox: list[float] | None
    metadata: dict[str, Any] = field(default_factory=dict)


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
class PdfPageResult:
    file_id: str
    run_id: str
    page_number: int
    page_index: int
    method: str
    status: str
    reason: str
    char_count: int
    word_count: int
    total_image_ratio: float
    largest_image_ratio: float
    chunk_count: int = 0
    error: str | None = None
    ocr_outbox_id: str | None = None
    embedded_started_at: datetime | None = None
    embedded_completed_at: datetime | None = None
    embedded_processing_seconds: float | None = None
    ocr_requested_at: datetime | None = None
    ocr_started_at: datetime | None = None
    ocr_completed_at: datetime | None = None
    ocr_queue_wait_seconds: float | None = None
    ocr_processing_seconds: float | None = None
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    cpu_total_seconds: float | None = None
    peak_memory_mb: float | None = None

    @property
    def needs_ocr(self) -> bool:
        return self.status == PAGE_PENDING_OCR_STATUS

    def with_ocr_outbox_id(
        self,
        outbox_id: str | None,
        ocr_requested_at: datetime | None = None,
    ) -> "PdfPageResult":
        return replace(
            self,
            ocr_outbox_id=outbox_id,
            ocr_requested_at=ocr_requested_at or self.ocr_requested_at,
        )

    def with_ocr_metrics(
        self,
        requested_at: datetime | None,
        started_at: datetime,
        completed_at: datetime,
        processing_seconds: float,
        cpu_user_seconds: float | None = None,
        cpu_system_seconds: float | None = None,
        cpu_total_seconds: float | None = None,
        peak_memory_mb: float | None = None,
    ) -> "PdfPageResult":
        return replace(
            self,
            ocr_requested_at=requested_at or self.ocr_requested_at,
            ocr_started_at=started_at,
            ocr_completed_at=completed_at,
            ocr_queue_wait_seconds=_elapsed_between(requested_at, started_at),
            ocr_processing_seconds=processing_seconds,
            cpu_user_seconds=(
                cpu_user_seconds
                if cpu_user_seconds is not None
                else self.cpu_user_seconds
            ),
            cpu_system_seconds=(
                cpu_system_seconds
                if cpu_system_seconds is not None
                else self.cpu_system_seconds
            ),
            cpu_total_seconds=(
                cpu_total_seconds
                if cpu_total_seconds is not None
                else self.cpu_total_seconds
            ),
            peak_memory_mb=(
                peak_memory_mb if peak_memory_mb is not None else self.peak_memory_mb
            ),
        )


@dataclass(frozen=True)
class PdfProcessingResult:
    message: RoutedFileMessage
    stored_file: StoredFile
    status: str
    pages: list[PdfPageResult]
    chunks: list[TextChunk]
    started_at: datetime
    completed_at: datetime | None
    processing_seconds: float | None
    error: str | None = None
    entity_outbox_id: str | None = None

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def completed_pages(self) -> int:
        return sum(1 for page in self.pages if page.status == PAGE_COMPLETED_STATUS)

    @property
    def pending_ocr_pages(self) -> int:
        return sum(1 for page in self.pages if page.status == PAGE_PENDING_OCR_STATUS)

    @property
    def failed_pages(self) -> int:
        return sum(1 for page in self.pages if page.status == PAGE_FAILED_STATUS)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def embedded_text_seconds(self) -> float:
        return _sum_seconds(page.embedded_processing_seconds for page in self.pages)

    @property
    def ocr_queue_wait_seconds(self) -> float:
        return _sum_seconds(page.ocr_queue_wait_seconds for page in self.pages)

    @property
    def ocr_processing_seconds(self) -> float:
        return _sum_seconds(page.ocr_processing_seconds for page in self.pages)

    @property
    def ocr_processing_wall_seconds(self) -> float:
        return _ocr_wall_seconds(self.pages)

    @property
    def cpu_user_seconds(self) -> float:
        return _sum_seconds(page.cpu_user_seconds for page in self.pages)

    @property
    def cpu_system_seconds(self) -> float:
        return _sum_seconds(page.cpu_system_seconds for page in self.pages)

    @property
    def cpu_total_seconds(self) -> float:
        return _sum_seconds(page.cpu_total_seconds for page in self.pages)

    @property
    def peak_memory_mb(self) -> float:
        return _max_value(page.peak_memory_mb for page in self.pages)

    @property
    def is_ready_for_entity(self) -> bool:
        return self.status == TEXT_EXTRACTION_COMPLETED_STATUS

    def with_outbox_ids(
        self,
        pages: list[PdfPageResult],
        entity_outbox_id: str | None,
    ) -> "PdfProcessingResult":
        return replace(self, pages=pages, entity_outbox_id=entity_outbox_id)


@dataclass(frozen=True)
class DocProcessingResult:
    message: DocRoutedMessage
    stored_file: StoredFile
    status: str
    pages: list[PdfPageResult]
    chunks: list[TextChunk]
    started_at: datetime
    completed_at: datetime
    processing_seconds: float
    error: str | None = None
    entity_outbox_id: str | None = None

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def completed_pages(self) -> int:
        return sum(1 for page in self.pages if page.status == PAGE_COMPLETED_STATUS)

    @property
    def pending_ocr_pages(self) -> int:
        return 0

    @property
    def failed_pages(self) -> int:
        return sum(1 for page in self.pages if page.status == PAGE_FAILED_STATUS)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def embedded_text_seconds(self) -> float:
        return 0.0

    @property
    def ocr_queue_wait_seconds(self) -> float:
        return 0.0

    @property
    def ocr_processing_seconds(self) -> float:
        return 0.0

    @property
    def ocr_processing_wall_seconds(self) -> float:
        return 0.0

    @property
    def cpu_user_seconds(self) -> float:
        return _sum_seconds(page.cpu_user_seconds for page in self.pages)

    @property
    def cpu_system_seconds(self) -> float:
        return _sum_seconds(page.cpu_system_seconds for page in self.pages)

    @property
    def cpu_total_seconds(self) -> float:
        return _sum_seconds(page.cpu_total_seconds for page in self.pages)

    @property
    def peak_memory_mb(self) -> float:
        return _max_value(page.peak_memory_mb for page in self.pages)

    @property
    def is_ready_for_entity(self) -> bool:
        return self.status == TEXT_EXTRACTION_COMPLETED_STATUS

    def with_outbox_ids(
        self,
        pages: list[PdfPageResult],
        entity_outbox_id: str | None,
    ) -> "DocProcessingResult":
        return replace(self, pages=pages, entity_outbox_id=entity_outbox_id)


@dataclass(frozen=True)
class OcrProcessingResult:
    message: OcrWorkMessage
    stored_file: StoredFile
    page: PdfPageResult
    chunks: list[TextChunk]
    started_at: datetime
    completed_at: datetime
    processing_seconds: float
    error: str | None = None
    file_status: str | None = None
    total_pages: int = 0
    completed_pages: int = 0
    pending_ocr_pages: int = 0
    failed_pages: int = 0
    file_chunk_count: int = 0
    entity_outbox_id: str | None = None

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def succeeded(self) -> bool:
        return self.page.status == PAGE_COMPLETED_STATUS

    def with_persisted_state(
        self,
        file_status: str,
        total_pages: int,
        completed_pages: int,
        pending_ocr_pages: int,
        failed_pages: int,
        file_chunk_count: int,
        entity_outbox_id: str | None,
    ) -> "OcrProcessingResult":
        return replace(
            self,
            file_status=file_status,
            total_pages=total_pages,
            completed_pages=completed_pages,
            pending_ocr_pages=pending_ocr_pages,
            failed_pages=failed_pages,
            file_chunk_count=file_chunk_count,
            entity_outbox_id=entity_outbox_id,
        )


@dataclass(frozen=True)
class OcrBatchMetrics:
    batch_id: str
    file_id: str
    run_id: str
    page_numbers: tuple[int, ...]
    requested_device: str
    effective_device: str
    cuda_available: bool
    gpu_name: str | None
    cuda_visible_devices: str | None
    mineru_device: str | None
    started_at: datetime
    completed_at: datetime
    wall_seconds: float
    mineru_command_count: int
    fallback_level: str
    error: str | None = None


@dataclass(frozen=True)
class OutboxMessage:
    outbox_id: str
    queue_name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PdfAttemptState:
    file_id: str
    attempts: int
    max_attempts: int
    status: str
    first_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_type: str | None = None
    last_error_message: str | None = None
    last_error_traceback: str | None = None
    quarantined_at: datetime | None = None
    last_result_status: str | None = None

    @property
    def is_quarantined(self) -> bool:
        return self.status == PDF_ATTEMPT_QUARANTINED_STATUS

    @property
    def exceeded_max_attempts(self) -> bool:
        return self.attempts > self.max_attempts

    @property
    def exhausted_max_attempts(self) -> bool:
        return self.attempts >= self.max_attempts


class TextExtractionRepository(Protocol):
    def get_file(self, file_id: str) -> StoredFile | None:
        ...

    def record_pdf_attempt_start(
        self,
        message: RoutedFileMessage,
        max_attempts: int,
    ) -> PdfAttemptState:
        ...

    def record_pdf_attempt_error(
        self,
        file_id: str,
        error_type: str,
        error_message: str,
        error_traceback: str,
    ) -> PdfAttemptState:
        ...

    def record_pdf_attempt_completed(
        self,
        file_id: str,
        result_status: str,
    ) -> None:
        ...

    def record_pdf_attempt_quarantined(
        self,
        file_id: str,
    ) -> PdfAttemptState:
        ...

    def save_pdf_result(
        self,
        result: PdfProcessingResult,
        publish_downstream: bool,
    ) -> PdfProcessingResult:
        ...

    def save_ocr_result(
        self,
        result: OcrProcessingResult,
        publish_downstream: bool,
    ) -> OcrProcessingResult:
        ...

    def save_ocr_batch_metrics(self, metrics: OcrBatchMetrics) -> None:
        ...

    def save_doc_result(
        self,
        result: DocProcessingResult,
        publish_downstream: bool,
    ) -> DocProcessingResult:
        ...

    def list_pending_outbox(self, queue_name: str) -> list[OutboxMessage]:
        ...

    def mark_outbox_published(self, outbox_id: str) -> None:
        ...

    def record_outbox_error(self, outbox_id: str, error: str) -> None:
        ...


class QueuePublisher(Protocol):
    def publish(self, queue_name: str, payload: dict[str, Any]) -> None:
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

    def consume_in_priority_order(
        self,
        queue_names: Iterable[str],
        handle_payload: Callable[[dict[str, Any]], None],
        max_messages: int | None = None,
        requeue_messages: bool = False,
    ) -> None:
        ...


def normalize_extension(extension: str | None) -> str:
    if not extension:
        return ""
    normalized = extension.strip().lower()
    if normalized and not normalized.startswith("."):
        return f".{normalized}"
    return normalized


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


def _optional_datetime(value: object) -> datetime | None:
    text = _optional_text(value)
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _sum_seconds(values: object) -> float:
    total = 0.0
    for value in values:
        if value is not None:
            total += float(value)
    return round(total, 6)


def _max_value(values: object) -> float:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return 0.0
    return round(max(numeric_values), 6)


def _elapsed_between(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if started_at is None or completed_at is None:
        return None
    return round(max(0.0, (completed_at - started_at).total_seconds()), 6)


def _ocr_wall_seconds(pages: list[PdfPageResult]) -> float:
    started = [
        page.ocr_started_at
        for page in pages
        if page.ocr_started_at is not None and page.ocr_completed_at is not None
    ]
    completed = [
        page.ocr_completed_at
        for page in pages
        if page.ocr_started_at is not None and page.ocr_completed_at is not None
    ]
    if not started or not completed:
        return 0.0
    return round(max(0.0, (max(completed) - min(started)).total_seconds()), 6)


def _source_reference_payload(stored_file: StoredFile) -> dict[str, Any]:
    return {
        "source_type": stored_file.source_type,
        "source_uri": stored_file.source_uri,
        "external_id": stored_file.external_id,
        "mime_type": stored_file.mime_type,
        "checksum_sha256": stored_file.checksum_sha256,
        "content_hash": stored_file.content_hash,
        "etag": stored_file.etag,
        "size_bytes": stored_file.size_bytes,
    }


def build_ocr_request_payload(
    message: RoutedFileMessage,
    stored_file: StoredFile,
    page: PdfPageResult,
    ocr_requested_at: datetime | None = None,
) -> dict[str, Any]:
    requested_at = ocr_requested_at or datetime.now(UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": OCR_REQUESTED_EVENT_TYPE,
        "run_id": message.run_id,
        "file_id": message.file_id,
        "routing_decision_id": message.routing_decision_id,
        "file_name": stored_file.file_name,
        "relative_path": stored_file.relative_path,
        "extension": normalize_extension(stored_file.extension),
        **_source_reference_payload(stored_file),
        "source_queue_name": QUEUE_PDF,
        "destination_queue_name": QUEUE_OCR_URGENT,
        "route_type": ROUTE_OCR,
        "reason": page.reason,
        "page_number": page.page_number,
        "page_index": page.page_index,
        "page_method": page.method,
        "routing_char_count": page.char_count,
        "routing_word_count": page.word_count,
        "routing_image_ratio": page.total_image_ratio,
        "routing_largest_image_ratio": page.largest_image_ratio,
        "ocr_requested_at": requested_at.isoformat(),
    }


def build_ocr_batch_request_payload(
    message: RoutedFileMessage,
    stored_file: StoredFile,
    pages: list[PdfPageResult],
    ocr_requested_at: datetime | None = None,
) -> dict[str, Any]:
    requested_at = ocr_requested_at or datetime.now(UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": OCR_BATCH_REQUESTED_EVENT_TYPE,
        "run_id": message.run_id,
        "file_id": message.file_id,
        "routing_decision_id": message.routing_decision_id,
        "file_name": stored_file.file_name,
        "relative_path": stored_file.relative_path,
        "extension": normalize_extension(stored_file.extension),
        **_source_reference_payload(stored_file),
        "source_queue_name": QUEUE_PDF,
        "destination_queue_name": QUEUE_OCR_URGENT,
        "route_type": ROUTE_OCR,
        "reason": "pdf_ocr_batch",
        "total_pages": len(pages),
        "pages": [
            {
                "page_number": page.page_number,
                "page_index": page.page_index,
                "reason": page.reason,
                "page_method": page.method,
                "routing_char_count": page.char_count,
                "routing_word_count": page.word_count,
                "routing_image_ratio": page.total_image_ratio,
                "routing_largest_image_ratio": page.largest_image_ratio,
                "ocr_requested_at": requested_at.isoformat(),
            }
            for page in pages
        ],
        "ocr_requested_at": requested_at.isoformat(),
    }


def build_chunks_ready_payload(
    result: PdfProcessingResult,
) -> dict[str, Any]:
    message = result.message
    stored_file = result.stored_file
    return build_chunks_ready_payload_for_file(
        run_id=message.run_id,
        file_id=message.file_id,
        routing_decision_id=message.routing_decision_id,
        stored_file=stored_file,
        source_queue_name=QUEUE_PDF,
        chunk_count=result.chunk_count,
        page_count=result.total_pages,
    )


def build_chunks_ready_payload_for_file(
    run_id: str,
    file_id: str,
    routing_decision_id: str,
    stored_file: StoredFile,
    source_queue_name: str,
    chunk_count: int,
    page_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": CHUNKS_READY_EVENT_TYPE,
        "run_id": run_id,
        "file_id": file_id,
        "routing_decision_id": routing_decision_id,
        "file_name": stored_file.file_name,
        "relative_path": stored_file.relative_path,
        "extension": normalize_extension(stored_file.extension),
        **_source_reference_payload(stored_file),
        "source_queue_name": source_queue_name,
        "destination_queue_name": QUEUE_ENTITY,
        "chunk_count": chunk_count,
        "page_count": page_count,
    }


def build_text_extract_poison_payload(
    *,
    run_id: str,
    file_id: str,
    routing_decision_id: str,
    stored_file: StoredFile,
    source_queue_name: str,
    stage: str,
    reason: str,
    error: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": TEXT_EXTRACT_POISONED_EVENT_TYPE,
        "run_id": run_id,
        "file_id": file_id,
        "routing_decision_id": routing_decision_id,
        "file_name": stored_file.file_name,
        "relative_path": stored_file.relative_path,
        "extension": normalize_extension(stored_file.extension),
        **_source_reference_payload(stored_file),
        "source_queue_name": source_queue_name,
        "destination_queue_name": QUEUE_TEXT_POISON,
        "stage": stage,
        "reason": reason,
        "error": error,
    }
