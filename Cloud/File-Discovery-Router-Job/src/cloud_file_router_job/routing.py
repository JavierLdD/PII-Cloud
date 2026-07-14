from __future__ import annotations

from cloud_file_router_job.models import RoutePlan, StoredFile


SOURCE_QUEUE_NAME = "Queue-Archivos"
QUEUE_PDF = "Queue-PDF"
QUEUE_OCR = "Queue-OCR"
QUEUE_TABLES = "Queue-Tables"
QUEUE_DOC = "Queue-Doc"
QUEUE_UNSUPPORTED = "Queue-Unsupported"

SCHEMA_VERSION = "2.0"
ROUTED_EVENT_TYPE = "file.routed"
ROUTER_VERSION = "cloud-drive-router-v1"

ROUTE_PDF = "pdf"
ROUTE_OCR = "ocr"
ROUTE_TABLE = "table"
ROUTE_DOC = "doc"
ROUTE_UNSUPPORTED = "unsupported"

ROUTED_STATUS = "routed"
UNSUPPORTED_STATUS = "unsupported"

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
TABLE_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}
DOC_EXTENSIONS = {".txt", ".docx"}
LEGACY_EXCEL_EXTENSIONS = {".xls"}
LEGACY_WORD_EXTENSIONS = {".doc"}

PDF_MIME_TYPES = {"application/pdf"}
IMAGE_MIME_PREFIX = "image/"
GOOGLE_DOCUMENT_MIME_TYPE = "application/vnd.google-apps.document"
GOOGLE_SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_PRESENTATION_MIME_TYPE = "application/vnd.google-apps.presentation"
TABLE_MIME_TYPES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    GOOGLE_SPREADSHEET_MIME_TYPE,
}
DOC_MIME_TYPES = {
    "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    GOOGLE_DOCUMENT_MIME_TYPE,
    GOOGLE_PRESENTATION_MIME_TYPE,
}


def normalize_extension(extension: str | None) -> str:
    if not extension:
        return ""
    normalized = extension.strip().lower()
    if normalized and not normalized.startswith("."):
        return f".{normalized}"
    return normalized


def normalize_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return ""
    return mime_type.strip().lower()


def classify_file(extension: str | None, mime_type: str | None) -> RoutePlan:
    normalized_mime = normalize_mime_type(mime_type)
    if normalized_mime.startswith("application/vnd.google-apps."):
        return classify_mime_type(normalized_mime)

    normalized_extension = normalize_extension(extension)
    if normalized_extension:
        return classify_extension(normalized_extension)
    return classify_mime_type(normalized_mime)


def classify_mime_type(mime_type: str | None) -> RoutePlan:
    normalized = normalize_mime_type(mime_type)
    if not normalized:
        return RoutePlan(
            ROUTE_UNSUPPORTED,
            QUEUE_UNSUPPORTED,
            "missing_extension",
            UNSUPPORTED_STATUS,
        )
    if normalized in PDF_MIME_TYPES:
        return RoutePlan(ROUTE_PDF, QUEUE_PDF, "pdf_mime", ROUTED_STATUS)
    if normalized.startswith(IMAGE_MIME_PREFIX):
        return RoutePlan(ROUTE_OCR, QUEUE_OCR, "image_mime", ROUTED_STATUS)
    if normalized == GOOGLE_DOCUMENT_MIME_TYPE:
        return RoutePlan(ROUTE_DOC, QUEUE_DOC, "google_document_mime", ROUTED_STATUS)
    if normalized == GOOGLE_SPREADSHEET_MIME_TYPE:
        return RoutePlan(
            ROUTE_TABLE,
            QUEUE_TABLES,
            "google_spreadsheet_mime",
            ROUTED_STATUS,
        )
    if normalized == GOOGLE_PRESENTATION_MIME_TYPE:
        return RoutePlan(
            ROUTE_DOC,
            QUEUE_DOC,
            "google_presentation_mime",
            ROUTED_STATUS,
        )
    if normalized in TABLE_MIME_TYPES:
        return RoutePlan(ROUTE_TABLE, QUEUE_TABLES, "tabular_mime", ROUTED_STATUS)
    if normalized in DOC_MIME_TYPES:
        return RoutePlan(ROUTE_DOC, QUEUE_DOC, "document_mime", ROUTED_STATUS)
    return RoutePlan(
        ROUTE_UNSUPPORTED,
        QUEUE_UNSUPPORTED,
        "unsupported_mime_type",
        UNSUPPORTED_STATUS,
    )


def classify_extension(extension: str | None) -> RoutePlan:
    normalized = normalize_extension(extension)
    if normalized in PDF_EXTENSIONS:
        return RoutePlan(ROUTE_PDF, QUEUE_PDF, "pdf_extension", ROUTED_STATUS)
    if normalized in IMAGE_EXTENSIONS:
        return RoutePlan(ROUTE_OCR, QUEUE_OCR, "image_extension", ROUTED_STATUS)
    if normalized in TABLE_EXTENSIONS:
        return RoutePlan(ROUTE_TABLE, QUEUE_TABLES, "tabular_extension", ROUTED_STATUS)
    if normalized in DOC_EXTENSIONS:
        return RoutePlan(ROUTE_DOC, QUEUE_DOC, "document_extension", ROUTED_STATUS)
    if normalized in LEGACY_EXCEL_EXTENSIONS:
        return RoutePlan(
            ROUTE_UNSUPPORTED,
            QUEUE_UNSUPPORTED,
            "legacy_excel_not_supported",
            UNSUPPORTED_STATUS,
        )
    if normalized in LEGACY_WORD_EXTENSIONS:
        return RoutePlan(
            ROUTE_UNSUPPORTED,
            QUEUE_UNSUPPORTED,
            "legacy_word_not_supported",
            UNSUPPORTED_STATUS,
        )
    if not normalized:
        return RoutePlan(
            ROUTE_UNSUPPORTED,
            QUEUE_UNSUPPORTED,
            "missing_extension",
            UNSUPPORTED_STATUS,
        )
    return RoutePlan(
        ROUTE_UNSUPPORTED,
        QUEUE_UNSUPPORTED,
        "unsupported_extension",
        UNSUPPORTED_STATUS,
    )


def build_routed_payload(
    run_id: str,
    routing_decision_id: str,
    stored_file: StoredFile,
    route_plan: RoutePlan,
    source_queue_name: str = SOURCE_QUEUE_NAME,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": ROUTED_EVENT_TYPE,
        "run_id": run_id,
        "file_id": stored_file.file_id,
        "routing_decision_id": routing_decision_id,
        "source_type": stored_file.source_type,
        "source_uri": stored_file.source_uri,
        "external_id": stored_file.external_id,
        "file_name": stored_file.file_name,
        "relative_path": stored_file.relative_path,
        "extension": normalize_extension(stored_file.extension),
        "mime_type": stored_file.mime_type,
        "checksum_sha256": stored_file.checksum_sha256,
        "content_hash": stored_file.content_hash,
        "etag": stored_file.etag,
        "size_bytes": stored_file.size_bytes,
        "source_queue_name": source_queue_name,
        "destination_queue_name": route_plan.destination_queue_name,
        "route_type": route_plan.route_type,
        "reason": route_plan.reason,
    }


def build_idempotency_key(
    run_id: str,
    stored_file: StoredFile,
    route_plan: RoutePlan,
) -> str:
    revision = (
        stored_file.content_hash
        or stored_file.etag
        or stored_file.checksum_sha256
        or str(stored_file.size_bytes or "")
        or "unknown"
    )
    return (
        f"file.routed:{run_id}:{stored_file.file_id}:"
        f"{revision}:{route_plan.route_type}:{ROUTER_VERSION}"
    )
