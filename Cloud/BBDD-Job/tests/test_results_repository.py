from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_bbdd_job.results_repository import (
    DatabaseResultsRepository,
    ResultsPersistenceError,
    artifact_metadata,
)
from cloud_bbdd_job.scan_request import ScanRequest


RUN_ID = "86ca6e73-ea37-4c1f-812d-7b71dcb771bb"
SOURCE_SECRET = "postgresql+psycopg://target_user:target_password@target/db"
RESULTS_SECRET = "postgresql://results_user:results_password@results/db"


def _request(**overrides) -> ScanRequest:
    payload = {
        "scan_id": RUN_ID,
        "user_id": "ana",
        "run_name": "Clientes Q3",
        "database_type": "postgresql",
        "connection_uri": SOURCE_SECRET,
        "confirm_full_scan": True,
        "source_name": "clientes",
    }
    payload.update(overrides)
    return ScanRequest.from_mapping(payload)


def _artifact(**overrides):
    artifact = {
        "artifact_type": "table_extract.discovery",
        "schema_version": "1.0",
        "generated_at": "2026-07-13T12:00:03Z",
        "run_id": RUN_ID,
        "table_started_at": "2026-07-13T12:00:00Z",
        "table_completed_at": "2026-07-13T12:00:02Z",
        "table_processing_seconds": 2.0,
        "peak_memory_mb": 64.5,
        "profile": {
            "source_name": "clientes",
            "source_type": "database",
            "dialect": "postgresql",
            "source_uri": SOURCE_SECRET,
            "tables": [
                {
                    "schema_name": "public",
                    "table_name": "customers",
                    "table_type": "table",
                    "row_count": 30,
                    "columns": [
                        {"column_name": "id"},
                        {"column_name": "email"},
                    ],
                },
                {
                    "schema_name": "public",
                    "table_name": "customer_view",
                    "table_type": "view",
                    "row_count": None,
                    "columns": [{"column_name": "email"}],
                },
            ],
        },
        "findings": [
            {
                "schema_name": "public",
                "table_name": "customers",
                "column_name": "email",
                "pii_type": "EMAIL",
                "confidence": 0.98,
                "confidence_level": "VERY_CONFIDENT",
                "detection_method": "regex",
                "sampled_count": 10,
                "matched_count": 9,
                "is_primary_key": False,
                "foreign_key": None,
                "propagated_from": None,
                "evidence_summary": "raw-user@example.com",
                "values": ["raw-user@example.com"],
            },
            {
                "schema_name": "public",
                "table_name": "customer_view",
                "column_name": "email",
                "pii_type": "EMAIL",
                "confidence": 0.7,
                "confidence_level": "CONFIDENT",
                "detection_method": "propagation",
                "sampled_count": 0,
                "matched_count": 0,
                "is_primary_key": False,
                "foreign_key": "public.customers.email",
                "propagated_from": "public.customers.email",
            },
        ],
    }
    artifact.update(overrides)
    return artifact


class FakeCursor:
    def __init__(
        self,
        executions,
        *,
        fail_on: str | None = None,
        upsert_result=("ana",),
    ):
        self.executions = executions
        self.fail_on = fail_on
        self.upsert_result = upsert_result
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self._last_sql = normalized
        self.executions.append((normalized, tuple(params)))
        if self.fail_on and self.fail_on in normalized:
            raise RuntimeError("simulated database error")

    def fetchone(self):
        if "INSERT INTO database_discovery_runs" in self._last_sql:
            return self.upsert_result
        return None


class FakeConnection:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        upsert_result=("ana",),
    ):
        self.executions = []
        self.fail_on = fail_on
        self.upsert_result = upsert_result
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        return False

    def cursor(self):
        return FakeCursor(
            self.executions,
            fail_on=self.fail_on,
            upsert_result=self.upsert_result,
        )


def _repository(connection: FakeConnection) -> DatabaseResultsRepository:
    return DatabaseResultsRepository(
        RESULTS_SECRET,
        connect=lambda database_url: connection,
    )


def _persist(repository: DatabaseResultsRepository, artifact=None) -> None:
    repository.persist_discovery(
        scan_request=_request(),
        artifact=artifact or _artifact(),
        artifact_uri=f"gs://pii-results/{RUN_ID}.json",
        artifact_size_bytes=1234,
        artifact_sha256="a" * 64,
    )


def test_persist_discovery_writes_whitelisted_summary_tables_and_findings() -> None:
    connection = FakeConnection()

    _persist(_repository(connection))

    assert connection.committed is True
    upsert = next(item for item in connection.executions if "database_discovery_runs" in item[0])
    params = upsert[1]
    assert params[1:6] == (
        "ana",
        "Clientes Q3",
        "postgresql",
        "clientes",
        f"gs://pii-results/{RUN_ID}.json",
    )
    assert params[15:24] == (1, 1, 1, 3, 2, 1, 2, 2, 1)

    table_inserts = [
        item for item in connection.executions if "INSERT INTO database_discovery_tables" in item[0]
    ]
    finding_inserts = [
        item
        for item in connection.executions
        if "INSERT INTO database_discovery_findings" in item[0]
    ]
    assert len(table_inserts) == 2
    assert table_inserts[0][1][2:] == (
        "public",
        "customers",
        "table",
        30,
        2,
        1,
    )
    assert len(finding_inserts) == 2
    assert finding_inserts[0][1][3:] == (
        "public",
        "customers",
        "email",
        "EMAIL",
        0.98,
        "VERY_CONFIDENT",
        "regex",
        10,
        9,
        False,
        None,
        None,
    )


def test_persist_discovery_never_persists_source_uri_evidence_or_values() -> None:
    connection = FakeConnection()

    _persist(_repository(connection))

    persisted = repr(connection.executions)
    assert SOURCE_SECRET not in persisted
    assert "raw-user@example.com" not in persisted
    assert "evidence_summary" not in persisted
    assert '"values"' not in persisted


def test_persist_discovery_is_idempotent_and_uses_stable_ids() -> None:
    first = FakeConnection()
    second = FakeConnection()

    _persist(_repository(first))
    _persist(_repository(second))

    assert first.executions == second.executions
    assert any("ON CONFLICT (run_id) DO UPDATE" in sql for sql, _ in first.executions)
    assert any("DELETE FROM database_discovery_findings" in sql for sql, _ in first.executions)
    assert any("DELETE FROM database_discovery_tables" in sql for sql, _ in first.executions)


def test_persist_discovery_rolls_back_entire_refresh_on_child_failure() -> None:
    connection = FakeConnection(fail_on="INSERT INTO database_discovery_findings")

    with pytest.raises(RuntimeError, match="simulated database error"):
        _persist(_repository(connection))

    assert connection.committed is False
    assert connection.rolled_back is True


def test_persist_discovery_cannot_reassign_existing_run_identity() -> None:
    connection = FakeConnection(upsert_result=None)

    with pytest.raises(ResultsPersistenceError, match="immutable metadata"):
        _persist(_repository(connection))

    assert connection.rolled_back is True
    assert not any(
        "DELETE FROM database_discovery" in sql for sql, _ in connection.executions
    )


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ({**_artifact(), "artifact_type": "table_extract.profile"}, "artifact_type"),
        ({**_artifact(), "run_id": "5ff3d988-67f2-46f8-b6cb-260d8eef6680"}, "does not match"),
        (
            {
                **_artifact(),
                "profile": {**_artifact()["profile"], "dialect": "oracle"},
            },
            "dialect does not match",
        ),
    ],
)
def test_persist_discovery_rejects_incompatible_artifacts(artifact, message) -> None:
    with pytest.raises(ResultsPersistenceError, match=message):
        _persist(_repository(FakeConnection()), artifact)


def test_persist_discovery_rejects_invalid_artifact_metadata() -> None:
    repository = _repository(FakeConnection())

    with pytest.raises(ResultsPersistenceError, match="artifact_uri"):
        repository.persist_discovery(
            scan_request=_request(),
            artifact=_artifact(),
            artifact_uri="https://bucket/result.json",
            artifact_size_bytes=1,
            artifact_sha256="a" * 64,
        )
    with pytest.raises(ResultsPersistenceError, match="artifact_sha256"):
        repository.persist_discovery(
            scan_request=_request(),
            artifact=_artifact(),
            artifact_uri="gs://bucket/result.json",
            artifact_size_bytes=1,
            artifact_sha256="invalid",
        )


def test_artifact_metadata_hashes_exact_uploaded_bytes() -> None:
    content = json.dumps(_artifact(), ensure_ascii=False, indent=2).encode("utf-8")

    artifact, size, digest = artifact_metadata(content)

    assert artifact["run_id"] == RUN_ID
    assert size == len(content)
    assert len(digest) == 64


def test_artifact_metadata_rejects_non_object_json() -> None:
    with pytest.raises(ResultsPersistenceError, match="JSON object"):
        artifact_metadata(b"[]")
