from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_bbdd_job.main import _redacted_argv, _resolve_gcs_target
from cloud_bbdd_job.scan_request import (
    ScanRequest,
    ScanRequestError,
    load_scan_request_from_env,
)


def _valid_payload(**overrides):
    payload = {
        "scan_id": "scan-local-001",
        "user_id": "ana",
        "run_name": "Clientes producción",
        "database_type": "postgresql",
        "source_id": "clientes-prod",
        "source_name": "clientes-prod",
        "source_type": "database",
        "dialect": "postgresql",
        "connection_uri": "postgresql+psycopg://user:pass@host:5432/db",
        "include_schemas": ["public"],
        "include_tables": [],
        "exclude_schemas": [],
        "exclude_tables": [],
        "include_views": True,
        "allow_full_database_scan": False,
        "profile_only": False,
        "disable_zero_shot": True,
        "zero_shot_model_name": None,
        "device": "cpu",
        "use_gpu": False,
        "output_local_path": "/tmp/table_extract_discovery.json",
        "output_uri": None,
    }
    payload.update(overrides)
    return payload


def test_scan_request_json_valid() -> None:
    request = ScanRequest.from_json(json.dumps(_valid_payload()))

    assert request.scan_id == "scan-local-001"
    assert request.user_id == "ana"
    assert request.run_name == "Clientes producción"
    assert request.database_type == "postgresql"
    assert request.source_name == "clientes-prod"
    assert request.dialect == "postgresql"
    assert request.include_schemas == ("public",)
    assert request.disable_zero_shot is True


def test_scan_request_json_invalid() -> None:
    with pytest.raises(ScanRequestError, match="Invalid SCAN_REQUEST_JSON"):
        ScanRequest.from_json("{not-json")


def test_scan_request_rejects_missing_connection_uri() -> None:
    with pytest.raises(ScanRequestError, match="connection_uri is required"):
        ScanRequest.from_mapping(_valid_payload(connection_uri=""))


@pytest.mark.parametrize("field", ["user_id", "run_name", "database_type"])
def test_scan_request_rejects_missing_run_metadata(field: str) -> None:
    with pytest.raises(ScanRequestError, match=field):
        ScanRequest.from_mapping(_valid_payload(**{field: "", "dialect": ""}))


def test_scan_request_rejects_run_name_over_120_characters() -> None:
    with pytest.raises(ScanRequestError, match="at most 120"):
        ScanRequest.from_mapping(_valid_payload(run_name="x" * 121))


def test_scan_request_normalizes_default_postgresql_driver() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(connection_uri="postgresql://user:pass@host:5432/db"),
    )

    assert request.connection_uri == "postgresql+psycopg://user:pass@host:5432/db"


def test_scan_request_normalizes_postgres_alias_driver() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(connection_uri="postgres://user:pass@host:5432/db"),
    )

    assert request.connection_uri == "postgresql+psycopg://user:pass@host:5432/db"


def test_scan_request_normalizes_oracle_driver() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(
            database_type="oracle",
            dialect="oracle",
            connection_uri="oracle://user:pass@host:1521/service",
        ),
    )

    assert request.database_type == "oracle"
    assert request.connection_uri == "oracle+oracledb://user:pass@host:1521/service"


def test_scan_request_rejects_database_type_uri_mismatch() -> None:
    with pytest.raises(ScanRequestError, match="scheme must match"):
        ScanRequest.from_mapping(
            _valid_payload(
                database_type="oracle",
                dialect="oracle",
            )
        )


def test_scan_request_rejects_missing_explicit_scope() -> None:
    with pytest.raises(ScanRequestError, match="Scan scope is required"):
        ScanRequest.from_mapping(
            _valid_payload(include_schemas=[], include_tables=[]),
        )


def test_scan_request_allows_table_scope_without_schemas() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(include_schemas=[], include_tables=["clientes"]),
    )

    assert request.include_schemas == ()
    assert request.include_tables == ("clientes",)


def test_scan_request_allows_full_database_scan_when_explicit() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(
            include_schemas=[],
            include_tables=[],
            allow_full_database_scan=True,
        ),
    )

    assert request.allow_full_database_scan is True


def test_scan_request_accepts_confirm_full_scan_contract() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(
            include_schemas=[],
            include_tables=[],
            allow_full_database_scan=False,
            confirm_full_scan=True,
        ),
    )

    assert request.confirm_full_scan is True
    assert request.allow_full_database_scan is True


def test_scan_request_translates_to_table_extract_argv() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(
            include_schemas=["public", "rrhh"],
            include_tables=["personas"],
            exclude_schemas=["audit"],
            exclude_tables=["tmp_personas"],
            include_views=False,
            profile_only=True,
            zero_shot_model_name="local/model",
            output_local_path="/tmp/out.json",
        ),
    )

    argv = request.to_table_extract_argv()

    assert argv == [
        "--database-url",
        "postgresql+psycopg://user:pass@host:5432/db",
        "--source-name",
        "clientes-prod",
        "--output",
        "/tmp/out.json",
        "--run-id",
        "scan-local-001",
        "--database-dialect",
        "postgresql",
        "--zero-shot-model-name",
        "local/model",
        "--device",
        "cpu",
        "--include-schema",
        "public",
        "--include-schema",
        "rrhh",
        "--exclude-schema",
        "audit",
        "--include-table",
        "personas",
        "--exclude-table",
        "tmp_personas",
        "--exclude-views",
        "--profile-only",
        "--disable-zero-shot",
    ]


def test_scan_request_gpu_suppresses_cpu_device_flag() -> None:
    request = ScanRequest.from_mapping(
        _valid_payload(use_gpu=True, device="cpu"),
    )

    argv = request.to_table_extract_argv()

    assert "--gpu" in argv
    assert "--device" not in argv
    assert "cpu" not in argv


def test_redacted_argv_hides_connection_uri() -> None:
    argv = [
        "--database-url",
        "postgresql+psycopg://user:pass@host:5432/db",
        "--source-name",
        "clientes-prod",
    ]

    assert _redacted_argv(argv) == [
        "--database-url",
        "<redacted>",
        "--source-name",
        "clientes-prod",
    ]


def test_legacy_env_fallback_still_builds_scan_request() -> None:
    request = load_scan_request_from_env(
        {
            "BBDD_RUN_ID": "env-run",
            "BBDD_USER_ID": "ana",
            "BBDD_RUN_NAME": "Clientes legacy",
            "BBDD_DATABASE_URL": "postgresql+psycopg://user:pass@host:5432/db",
            "BBDD_SOURCE_ID": "clientes",
            "BBDD_SOURCE_NAME": "clientes-env",
            "BBDD_DATABASE_DIALECT": "postgresql",
            "BBDD_INCLUDE_TABLES": "clientes, empleados",
            "BBDD_PROFILE_ONLY": "true",
            "BBDD_DISABLE_ZERO_SHOT": "true",
            "TABLE_EXTRACT_ZERO_SHOT_DEVICE": "cpu",
            "GCS_OUTPUT_URI": "gs://bucket/prefix/",
        }
    )

    assert request.scan_id == "env-run"
    assert request.user_id == "ana"
    assert request.run_name == "Clientes legacy"
    assert request.source_id == "clientes"
    assert request.source_name == "clientes-env"
    assert request.include_tables == ("clientes", "empleados")
    assert request.profile_only is True
    assert request.output_uri == "gs://bucket/prefix/"


def test_scan_request_json_takes_precedence_over_legacy_env() -> None:
    request = load_scan_request_from_env(
        {
            "SCAN_REQUEST_JSON": json.dumps(_valid_payload(scan_id="json-run")),
            "BBDD_RUN_ID": "env-run",
            "BBDD_DATABASE_URL": "postgresql+psycopg://other:pass@host:5432/db",
        }
    )

    assert request.scan_id == "json-run"
    assert request.connection_uri == "postgresql+psycopg://user:pass@host:5432/db"


def test_scan_request_json_uses_internal_output_and_model_defaults() -> None:
    request = load_scan_request_from_env(
        {
            "SCAN_REQUEST_JSON": json.dumps(
                _valid_payload(output_uri=None, disable_zero_shot=False)
            ),
            "GCS_OUTPUT_URI": "gs://results/prefix/",
            "BBDD_DISABLE_ZERO_SHOT": "true",
            "TABLE_EXTRACT_ZERO_SHOT_DEVICE": "cpu",
        }
    )

    # Explicit request fields win; deployment defaults only fill omitted fields.
    assert request.output_uri is None
    assert request.disable_zero_shot is False


def test_minimal_web_contract_uses_internal_job_configuration() -> None:
    request = load_scan_request_from_env(
        {
            "SCAN_REQUEST_JSON": json.dumps(
                {
                    "run_id": "86ca6e73-ea37-4c1f-812d-7b71dcb771bb",
                    "user_id": "ana",
                    "run_name": "Clientes Q3",
                    "database_type": "postgresql",
                    "connection_uri": "postgresql://user:pass@host/db",
                    "confirm_full_scan": True,
                }
            ),
            "GCS_OUTPUT_URI": "gs://results/prefix/",
            "BBDD_DISABLE_ZERO_SHOT": "true",
            "TABLE_EXTRACT_ZERO_SHOT_DEVICE": "cpu",
        }
    )

    assert request.scan_id == "86ca6e73-ea37-4c1f-812d-7b71dcb771bb"
    assert request.source_name == "Clientes Q3"
    assert request.output_uri == "gs://results/prefix/"
    assert request.disable_zero_shot is True
    assert request.profile_only is False


def test_gcs_output_uri_directory_uses_scan_request_artifact_name(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "3")
    request = ScanRequest.from_mapping(_valid_payload())

    bucket_name, blob_name = _resolve_gcs_target(
        "gs://bucket/prefix/",
        Path("/tmp/out.json"),
        request,
    )

    assert bucket_name == "bucket"
    assert blob_name == "prefix/clientes-prod-scan-local-001-task-3.json"
