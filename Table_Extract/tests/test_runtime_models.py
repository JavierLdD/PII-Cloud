import pytest

from table_extract.runtime import GOOGLE_SPREADSHEET_MIME_TYPE, TableRoutedMessage


def table_payload(**overrides):
    payload = {
        "schema_version": "2.0",
        "event_type": "file.routed",
        "run_id": "run-001",
        "file_id": "file-001",
        "routing_decision_id": "route-001",
        "source_type": "local",
        "source_uri": "/tmp/clientes.csv",
        "external_id": None,
        "file_name": "clientes.csv",
        "relative_path": "clientes.csv",
        "extension": ".csv",
        "mime_type": "text/csv",
        "checksum_sha256": None,
        "content_hash": None,
        "etag": None,
        "size_bytes": 123,
        "source_queue_name": "Queue-Archivos",
        "destination_queue_name": "Queue-Tables",
        "route_type": "table",
        "reason": "tabular_extension",
    }
    payload.update(overrides)
    return payload


def test_table_routed_message_accepts_valid_queue_tables_payload() -> None:
    message = TableRoutedMessage.from_payload(table_payload())

    assert message.schema_version == "2.0"
    assert message.event_type == "file.routed"
    assert message.destination_queue_name == "Queue-Tables"
    assert message.route_type == "table"
    assert message.extension == ".csv"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("schema_version", "1.0", "Unsupported schema_version"),
        ("event_type", "file.discovered", "Unsupported event_type"),
        ("destination_queue_name", "Queue-PDF", "Unsupported destination_queue_name"),
        ("route_type", "pdf", "Unsupported route_type"),
    ],
)
def test_table_routed_message_rejects_wrong_router_contract(
    field: str,
    value: str,
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        TableRoutedMessage.from_payload(table_payload(**{field: value}))


@pytest.mark.parametrize("extension", [".csv", ".xlsx", ".xlsm", "CSV"])
def test_table_routed_message_accepts_supported_extensions(extension: str) -> None:
    message = TableRoutedMessage.from_payload(table_payload(extension=extension))

    assert message.extension.startswith(".")


def test_table_routed_message_accepts_google_spreadsheet_mime_without_extension() -> None:
    message = TableRoutedMessage.from_payload(
        table_payload(
            source_type="drive",
            source_uri="drive://file/google-sheet-id",
            external_id="google-sheet-id",
            file_name="clientes",
            relative_path="clientes",
            extension="",
            mime_type=GOOGLE_SPREADSHEET_MIME_TYPE,
            reason="google_spreadsheet_mime",
        )
    )

    assert message.extension == ""
    assert message.mime_type == GOOGLE_SPREADSHEET_MIME_TYPE


def test_table_routed_message_rejects_unsupported_extension() -> None:
    with pytest.raises(ValueError, match="Unsupported table extension"):
        TableRoutedMessage.from_payload(table_payload(extension=".pdf"))
