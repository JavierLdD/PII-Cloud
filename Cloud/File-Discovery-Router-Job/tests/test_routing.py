from __future__ import annotations

from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_file_router_job.models import StoredFile  # noqa: E402
from cloud_file_router_job.routing import (  # noqa: E402
    QUEUE_DOC,
    QUEUE_OCR,
    QUEUE_PDF,
    QUEUE_TABLES,
    QUEUE_UNSUPPORTED,
    ROUTE_DOC,
    ROUTE_OCR,
    ROUTE_PDF,
    ROUTE_TABLE,
    ROUTE_UNSUPPORTED,
    build_routed_payload,
    classify_file,
)


def test_classifies_extensions_and_google_mimes() -> None:
    cases = [
        (".pdf", "application/pdf", ROUTE_PDF, QUEUE_PDF),
        (".png", "image/png", ROUTE_OCR, QUEUE_OCR),
        (".xlsx", None, ROUTE_TABLE, QUEUE_TABLES),
        (".txt", "text/plain", ROUTE_DOC, QUEUE_DOC),
        ("", "application/vnd.google-apps.document", ROUTE_DOC, QUEUE_DOC),
        ("", "application/vnd.google-apps.presentation", ROUTE_DOC, QUEUE_DOC),
        ("", "application/vnd.google-apps.spreadsheet", ROUTE_TABLE, QUEUE_TABLES),
        (".doc", None, ROUTE_UNSUPPORTED, QUEUE_UNSUPPORTED),
        (".xls", None, ROUTE_UNSUPPORTED, QUEUE_UNSUPPORTED),
        (".zip", None, ROUTE_UNSUPPORTED, QUEUE_UNSUPPORTED),
    ]

    for extension, mime_type, route_type, destination in cases:
        route_plan = classify_file(extension, mime_type)

        assert route_plan.route_type == route_type
        assert route_plan.destination_queue_name == destination


def test_routed_payload_is_router_compatible() -> None:
    stored_file = StoredFile(
        file_id="file-001",
        run_id="run-001",
        source_type="drive",
        source_uri="drive://file/abc",
        external_id="abc",
        file_name="documento.pdf",
        relative_path="documento.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        size_bytes=123,
        checksum_sha256=None,
        content_hash="hash",
        etag="1",
    )
    route_plan = classify_file(stored_file.extension, stored_file.mime_type)

    payload = build_routed_payload("run-001", "decision-001", stored_file, route_plan)

    assert payload["schema_version"] == "2.0"
    assert payload["event_type"] == "file.routed"
    assert payload["run_id"] == "run-001"
    assert payload["file_id"] == "file-001"
    assert payload["routing_decision_id"] == "decision-001"
    assert payload["source_type"] == "drive"
    assert payload["destination_queue_name"] == QUEUE_PDF
    assert payload["route_type"] == ROUTE_PDF
