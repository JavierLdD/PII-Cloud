from __future__ import annotations

from pathlib import Path


CLOUD_DIR = Path(__file__).resolve().parents[2]
SCHEMA = (CLOUD_DIR / "Database" / "schema.sql").read_text(encoding="utf-8")
JOB_DIR = Path(__file__).resolve().parents[1]


def test_cloud_schema_defines_additive_database_discovery_contract() -> None:
    required_columns = {
        "database_discovery_runs": (
            "run_id",
            "user_id",
            "run_name",
            "database_type",
            "artifact_uri",
            "status",
            "generated_at",
            "started_at",
            "completed_at",
            "processing_seconds",
            "peak_memory_mb",
            "schema_count",
            "table_count",
            "view_count",
            "column_count",
            "finding_count",
            "affected_schema_count",
            "affected_table_count",
            "affected_column_count",
            "pii_type_count",
        ),
        "database_discovery_tables": (
            "schema_name",
            "table_name",
            "table_type",
            "row_count",
            "column_count",
            "finding_count",
        ),
        "database_discovery_findings": (
            "schema_name",
            "table_name",
            "column_name",
            "pii_type",
            "confidence",
            "confidence_level",
            "detection_method",
            "sampled_count",
            "matched_count",
            "is_primary_key",
            "foreign_key",
            "propagated_from",
        ),
    }

    for table, columns in required_columns.items():
        marker = f"CREATE TABLE IF NOT EXISTS {table} ("
        assert marker in SCHEMA
        block = SCHEMA.split(marker, 1)[1].split("\n);", 1)[0]
        for column in columns:
            assert f"    {column} " in block


def test_database_result_tables_do_not_have_secret_or_raw_value_columns() -> None:
    section = SCHEMA.split(
        "CREATE TABLE IF NOT EXISTS database_discovery_runs (",
        1,
    )[1].split("-- Upgrade path", 1)[0]

    assert "connection_uri" not in section
    assert "source_uri" not in section
    assert "evidence_summary" not in section
    assert "sample_values" not in section
    assert "raw_values" not in section


def test_deploy_uses_separate_results_database_secret() -> None:
    deploy_script = (JOB_DIR / "scripts" / "deploy_job.sh").read_text(
        encoding="utf-8"
    )
    cloud_deploy_script = (
        JOB_DIR / "scripts" / "cloud_deploy_release.sh"
    ).read_text(encoding="utf-8")
    env_sample = (JOB_DIR / "config" / "env.sample.yaml").read_text(
        encoding="utf-8"
    )

    assert "BBDD_RESULTS_DATABASE_URL_SECRET" in deploy_script
    assert "BBDD_RESULTS_DATABASE_URL=" in deploy_script
    assert "BBDD_RESULTS_DATABASE_URL_SECRET" in cloud_deploy_script
    assert "GCS_OUTPUT_URI" in cloud_deploy_script
    assert "BBDD_RESULTS_DATABASE_URL:" not in env_sample
