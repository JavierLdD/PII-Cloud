from table_extract.discovery import discover_pii
from table_extract.models import (
    ColumnProfile,
    ColumnSample,
    ScanConfig,
    ScanSession,
    TableProfile,
)
from table_extract.profiling import create_scan_session, profile_source
from table_extract.sources import SourceAdapter


class FakeSourceAdapter:
    source_name = "fake_db"
    source_type = "database"
    dialect = "postgresql"
    source_uri = "postgresql://example"

    def __init__(self) -> None:
        self._tables = (
            TableProfile(table_name="customers", schema_name="public"),
            TableProfile(table_name="payments", schema_name="public"),
        )
        self._columns = {
            "customers": (
                ColumnProfile(column_name="email", data_type="varchar"),
                ColumnProfile(column_name="rut", data_type="varchar"),
            ),
            "payments": (
                ColumnProfile(column_name="card_number", data_type="varchar"),
            ),
        }
        self.sample_calls = []
        self.closed = False

    def iter_tables(self):
        return iter(self._tables)

    def iter_columns(self, table):
        return iter(self._columns[table.table_name])

    def get_column_sample(self, table, column, *, limit=1000, max_value_length=256):
        self.sample_calls.append((table.table_name, column.column_name, limit, max_value_length))
        return ColumnSample(
            table_name=table.table_name,
            schema_name=table.schema_name,
            column_name=column.column_name,
            values=("sample",),
            max_value_length=max_value_length,
        )

    def close(self) -> None:
        self.closed = True


def test_profile_source_consumes_common_source_adapter_interface() -> None:
    source = FakeSourceAdapter()

    assert isinstance(source, SourceAdapter)

    profile = profile_source(source)

    assert profile.source_name == "fake_db"
    assert profile.source_type == "database"
    assert profile.dialect == "postgresql"
    assert len(profile.tables) == 2
    assert [column.column_name for column in profile.tables[0].columns] == [
        "email",
        "rut",
    ]


def test_create_scan_session_profiles_source_without_closing_adapter() -> None:
    source = FakeSourceAdapter()
    config = ScanConfig(sample_limit=25, max_value_length=16)

    session = create_scan_session(
        source,
        run_id="run-001",
        config=config,
    )

    assert session.run_id == "run-001"
    assert session.source is source
    assert session.profile.source_name == "fake_db"
    assert len(session.profile.tables) == 2
    assert session.config is config
    assert source.closed is False
    assert source.sample_calls == []


def test_discover_pii_contract_uses_live_source_adapter_for_sampling() -> None:
    source = FakeSourceAdapter()
    profile = profile_source(source)
    session = ScanSession(run_id="run-001", source=source, profile=profile)

    findings = discover_pii(session)

    assert [finding.pii_type for finding in findings] == [
        "EMAIL",
        "RUT",
        "PAYMENT_CARD",
    ]
    assert {finding.confidence_level for finding in findings} == {"PROBABLE"}
    assert source.sample_calls == [
        ("customers", "email", 50, 256),
        ("customers", "rut", 50, 256),
        ("payments", "card_number", 50, 256),
    ]
    assert source.closed is False
