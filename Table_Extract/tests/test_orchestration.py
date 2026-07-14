from __future__ import annotations

import pytest

import table_extract.orchestration as orchestration
from table_extract.models import (
    ColumnProfile,
    DataSourceProfile,
    DiscoveredPII,
    ScanConfig,
    TableProfile,
)
from table_extract.orchestration import (
    DatabaseProfileRequest,
    FileProfileRequest,
    OrdsProfileRequest,
    discover_table_source,
    profile_table_source,
)
from table_extract.runtime import FileScanContext, StoredFile
from table_extract.sources import DatabaseScanRequest, OrdsScanRequest


class FakeAdapter:
    source_name = "fake_source"
    source_type = "database"
    dialect = "fake"
    source_uri = "fake://source"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False

    def iter_tables(self):
        if self.fail:
            raise RuntimeError("profiling failed")
        return iter((TableProfile(table_name="contacts"),))

    def iter_columns(self, table):
        return iter((ColumnProfile(column_name="email", ordinal_position=1),))

    def close(self) -> None:
        self.closed = True


def file_context(path) -> FileScanContext:
    stored_file = StoredFile(
        file_id="file-001",
        run_id="run-001",
        source_type="local",
        source_uri=f"local://{path}",
        external_id=None,
        file_name=path.name,
        relative_path=path.name,
        extension=".csv",
        mime_type="text/csv",
        size_bytes=None,
        checksum_sha256=None,
    )
    return FileScanContext(
        run_id="run-001",
        stored_file=stored_file,
        local_path=str(path),
        source_uri=f"local://{path}",
        is_temporary=False,
    )


def test_file_profile_request_profiles_materialized_file(tmp_path) -> None:
    csv_path = tmp_path / "contacts.csv"
    csv_path.write_text("email,score\na@example.com,10\n", encoding="utf-8")

    profile = profile_table_source(FileProfileRequest(file_context(csv_path)))

    assert profile.source_name == "contacts.csv"
    assert profile.source_type == "csv"
    assert profile.tables[0].table_name == "contacts.csv"
    assert [column.column_name for column in profile.tables[0].columns] == [
        "email",
        "score",
    ]


def test_database_profile_request_builds_profiles_and_closes_adapter(monkeypatch) -> None:
    captured = {}

    def fake_build_database_source_adapter(request):
        captured["request"] = request
        captured["adapter"] = FakeAdapter()
        return captured["adapter"]

    monkeypatch.setattr(
        orchestration,
        "build_database_source_adapter",
        fake_build_database_source_adapter,
    )
    request = DatabaseScanRequest(
        connection_uri="sqlite:///source.db",
        source_name="db_source",
    )

    profile = profile_table_source(DatabaseProfileRequest(request))

    assert captured["request"] is request
    assert profile == DataSourceProfile(
        source_name="fake_source",
        source_type="database",
        dialect="fake",
        source_uri="fake://source",
        tables=(
            TableProfile(
                table_name="contacts",
                columns=(ColumnProfile(column_name="email", ordinal_position=1),),
            ),
        ),
    )
    assert captured["adapter"].closed


def test_ords_profile_request_builds_profiles_and_closes_adapter(monkeypatch) -> None:
    captured = {}

    def fake_build_ords_source_adapter(request):
        captured["request"] = request
        captured["adapter"] = FakeAdapter()
        captured["adapter"].source_type = "ords"
        captured["adapter"].dialect = "oracle"
        return captured["adapter"]

    monkeypatch.setattr(
        orchestration,
        "build_ords_source_adapter",
        fake_build_ords_source_adapter,
    )
    request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        source_name="ords_source",
    )

    profile = profile_table_source(OrdsProfileRequest(request))

    assert captured["request"] is request
    assert profile.source_type == "ords"
    assert profile.dialect == "oracle"
    assert captured["adapter"].closed


def test_profile_table_source_rejects_unknown_request() -> None:
    with pytest.raises(TypeError, match="Unsupported profile request"):
        profile_table_source(object())


def test_profile_table_source_closes_database_adapter_when_profiling_fails(
    monkeypatch,
) -> None:
    captured = {}

    def fake_build_database_source_adapter(request):
        captured["adapter"] = FakeAdapter(fail=True)
        return captured["adapter"]

    monkeypatch.setattr(
        orchestration,
        "build_database_source_adapter",
        fake_build_database_source_adapter,
    )
    request = DatabaseScanRequest(
        connection_uri="sqlite:///source.db",
        source_name="db_source",
    )

    with pytest.raises(RuntimeError, match="profiling failed"):
        profile_table_source(DatabaseProfileRequest(request))

    assert captured["adapter"].closed


def fake_finding(source_name: str) -> DiscoveredPII:
    return DiscoveredPII(
        source_name=source_name,
        source_type="database",
        schema_name=None,
        table_name="contacts",
        column_name="email",
        pii_type="EMAIL",
        confidence=0.95,
        confidence_level="VERY_CONFIDENT",
        detection_method="regex",
        sampled_count=1,
        matched_count=1,
    )


def test_file_discovery_request_creates_session_and_returns_result(
    monkeypatch,
    tmp_path,
) -> None:
    csv_path = tmp_path / "contacts.csv"
    csv_path.write_text("email\na@example.com\n", encoding="utf-8")
    captured = {}

    def fake_discover_pii(session):
        captured["session"] = session
        return [fake_finding(session.profile.source_name)]

    monkeypatch.setattr(orchestration, "discover_pii", fake_discover_pii)

    result = discover_table_source(
        FileProfileRequest(file_context(csv_path)),
        config=ScanConfig(sample_limit=10, zero_shot_enabled=False),
    )

    assert result.run_id == "run-001"
    assert result.profile.source_type == "csv"
    assert result.findings == (fake_finding("contacts.csv"),)
    assert captured["session"].source is not None
    assert captured["session"].config.zero_shot_enabled is False


def test_database_discovery_builds_session_discovers_and_closes_adapter(
    monkeypatch,
) -> None:
    captured = {}

    def fake_build_database_source_adapter(request):
        captured["request"] = request
        captured["adapter"] = FakeAdapter()
        return captured["adapter"]

    def fake_discover_pii(session):
        captured["session"] = session
        return [fake_finding(session.profile.source_name)]

    monkeypatch.setattr(
        orchestration,
        "build_database_source_adapter",
        fake_build_database_source_adapter,
    )
    monkeypatch.setattr(orchestration, "discover_pii", fake_discover_pii)
    request = DatabaseScanRequest(
        connection_uri="sqlite:///source.db",
        source_name="db_source",
    )

    result = discover_table_source(
        DatabaseProfileRequest(request),
        run_id="run-db",
        config=ScanConfig(zero_shot_enabled=False),
    )

    assert captured["request"] is request
    assert captured["session"].run_id == "run-db"
    assert captured["session"].source is captured["adapter"]
    assert result.run_id == "run-db"
    assert result.profile.source_name == "fake_source"
    assert result.findings == (fake_finding("fake_source"),)
    assert captured["adapter"].closed


def test_ords_discovery_builds_session_discovers_and_closes_adapter(
    monkeypatch,
) -> None:
    captured = {}

    def fake_build_ords_source_adapter(request):
        captured["request"] = request
        captured["adapter"] = FakeAdapter()
        captured["adapter"].source_type = "ords"
        captured["adapter"].dialect = "oracle"
        return captured["adapter"]

    def fake_discover_pii(session):
        captured["session"] = session
        return [fake_finding(session.profile.source_name)]

    monkeypatch.setattr(
        orchestration,
        "build_ords_source_adapter",
        fake_build_ords_source_adapter,
    )
    monkeypatch.setattr(orchestration, "discover_pii", fake_discover_pii)
    request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        source_name="ords_source",
    )

    result = discover_table_source(
        OrdsProfileRequest(request),
        run_id="run-ords",
        config=ScanConfig(zero_shot_enabled=False),
    )

    assert captured["request"] is request
    assert captured["session"].run_id == "run-ords"
    assert result.run_id == "run-ords"
    assert result.profile.source_type == "ords"
    assert result.profile.dialect == "oracle"
    assert captured["adapter"].closed


def test_discover_table_source_requires_run_id_for_direct_sources(monkeypatch) -> None:
    captured = {}

    def fake_build_database_source_adapter(request):
        captured["adapter"] = FakeAdapter()
        return captured["adapter"]

    monkeypatch.setattr(
        orchestration,
        "build_database_source_adapter",
        fake_build_database_source_adapter,
    )
    request = DatabaseScanRequest(
        connection_uri="sqlite:///source.db",
        source_name="db_source",
    )

    with pytest.raises(ValueError, match="run_id is required"):
        discover_table_source(DatabaseProfileRequest(request))

    assert captured["adapter"].closed


def test_discover_table_source_closes_database_adapter_when_discovery_fails(
    monkeypatch,
) -> None:
    captured = {}

    def fake_build_database_source_adapter(request):
        captured["adapter"] = FakeAdapter()
        return captured["adapter"]

    def fail_discovery(session):
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(
        orchestration,
        "build_database_source_adapter",
        fake_build_database_source_adapter,
    )
    monkeypatch.setattr(orchestration, "discover_pii", fail_discovery)
    request = DatabaseScanRequest(
        connection_uri="sqlite:///source.db",
        source_name="db_source",
    )

    with pytest.raises(RuntimeError, match="discovery failed"):
        discover_table_source(DatabaseProfileRequest(request), run_id="run-db")

    assert captured["adapter"].closed


def test_discover_table_source_rejects_unknown_request() -> None:
    with pytest.raises(TypeError, match="Unsupported discovery request"):
        discover_table_source(object())
