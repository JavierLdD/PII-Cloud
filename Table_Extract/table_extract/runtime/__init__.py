from table_extract.runtime.models import (
    GOOGLE_SPREADSHEET_MIME_TYPE,
    QUEUE_TABLES,
    ROUTE_TABLE,
    ROUTED_EVENT_TYPE,
    SCHEMA_VERSION,
    SOURCE_DRIVE,
    SOURCE_LOCAL,
    FileScanContext,
    StoredFile,
    TableExtractionRecord,
    TableRoutedMessage,
    local_path_from_source_uri,
    normalize_extension,
)

__all__ = [
    "FileScanContext",
    "GOOGLE_SPREADSHEET_MIME_TYPE",
    "QUEUE_TABLES",
    "ROUTE_TABLE",
    "ROUTED_EVENT_TYPE",
    "SCHEMA_VERSION",
    "SOURCE_DRIVE",
    "SOURCE_LOCAL",
    "StoredFile",
    "TableExtractionRecord",
    "TableRoutedMessage",
    "default_file_scan_callback",
    "local_path_from_source_uri",
    "normalize_extension",
    "process_file_id",
    "process_table_payload",
    "run_table_listener",
]


def __getattr__(name: str):
    if name in {
        "default_file_scan_callback",
        "process_file_id",
        "process_table_payload",
        "run_table_listener",
    }:
        from table_extract.runtime import listener

        return getattr(listener, name)
    raise AttributeError(f"module 'table_extract.runtime' has no attribute {name!r}")
