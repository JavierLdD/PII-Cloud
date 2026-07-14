from __future__ import annotations

from datetime import UTC, datetime
import json

import table_extract
from table_extract.models import (
    ColumnProfile,
    DataSourceProfile,
    DiscoveredPII,
    DiscoveryResult,
    TableProcessingMetrics,
    TableProfile,
)
from table_extract.profile_artifacts import (
    DISCOVERY_ARTIFACT_SCHEMA_VERSION,
    DISCOVERY_ARTIFACT_TYPE,
    PROFILE_ARTIFACT_SCHEMA_VERSION,
    PROFILE_ARTIFACT_TYPE,
    discovery_artifact_json,
    discovery_to_artifact,
    profile_artifact_json,
    profile_to_artifact,
)


def sample_profile() -> DataSourceProfile:
    return DataSourceProfile(
        source_name="inventory",
        source_type="database",
        dialect="postgresql",
        source_uri="postgresql://user:***@localhost/db",
        tables=(
            TableProfile(
                schema_name="public",
                table_name="contacts",
                row_count=12,
                columns=(
                    ColumnProfile(
                        column_name="id",
                        data_type="integer",
                        nullable=False,
                        ordinal_position=1,
                    ),
                    ColumnProfile(
                        column_name="email",
                        data_type="text",
                        nullable=True,
                        ordinal_position=2,
                    ),
                    ColumnProfile(
                        column_name="fecha_nacimiento",
                        data_type="date",
                        nullable=True,
                        ordinal_position=3,
                    ),
                ),
            ),
            TableProfile(
                schema_name="public",
                table_name="contact_view",
                table_type="view",
                row_count=None,
                columns=(
                    ColumnProfile(
                        column_name="email",
                        data_type="text",
                        nullable=True,
                        ordinal_position=1,
                    ),
                ),
            ),
        ),
    )


def sample_discovery_result() -> DiscoveryResult:
    return DiscoveryResult(
        run_id="run-001",
        profile=sample_profile(),
        findings=(
            DiscoveredPII(
                source_name="inventory",
                source_type="database",
                schema_name="public",
                table_name="contacts",
                column_name="email",
                pii_type="EMAIL",
                confidence=0.95,
                confidence_level="VERY_CONFIDENT",
                detection_method="regex",
                sampled_count=10,
                matched_count=10,
                evidence_summary="method=regex sampled=10 matched=10",
            ),
            DiscoveredPII(
                source_name="inventory",
                source_type="database",
                schema_name="public",
                table_name="contact_view",
                column_name="rut",
                pii_type="RUT",
                confidence=0.55,
                confidence_level="PROBABLE",
                detection_method="name",
                sampled_count=0,
                matched_count=0,
                evidence_summary="method=name values_hidden",
            ),
            DiscoveredPII(
                source_name="inventory",
                source_type="database",
                schema_name="public",
                table_name="contacts",
                column_name="fecha_nacimiento",
                pii_type="DATE",
                confidence=0.97,
                confidence_level="VERY_CONFIDENT",
                detection_method="header_and_value",
                sampled_count=10,
                matched_count=10,
                evidence_summary="method=header_and_value sampled=10 matched=10",
            ),
        ),
    )


def sample_metrics() -> TableProcessingMetrics:
    return TableProcessingMetrics(
        started_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        completed_at=datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC),
        processing_seconds=1.25,
        cpu_user_seconds=0.75,
        cpu_system_seconds=0.25,
        cpu_total_seconds=1.0,
        peak_memory_mb=128.5,
    )


def test_profile_to_artifact_contains_versioned_wrapper_and_summary() -> None:
    generated_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    artifact = profile_to_artifact(sample_profile(), generated_at=generated_at)

    assert artifact["artifact_type"] == PROFILE_ARTIFACT_TYPE
    assert artifact["schema_version"] == PROFILE_ARTIFACT_SCHEMA_VERSION
    assert artifact["generated_at"] == "2026-01-02T03:04:05Z"
    assert artifact["summary"] == {
        "source_name": "inventory",
        "source_type": "database",
        "dialect": "postgresql",
        "table_count": 1,
        "view_count": 1,
        "column_count": 4,
    }


def test_profile_artifact_preserves_profile_metadata_tables_columns_and_row_count() -> None:
    artifact = profile_to_artifact(sample_profile())
    profile = artifact["profile"]

    assert profile["source_name"] == "inventory"
    assert profile["source_type"] == "database"
    assert profile["dialect"] == "postgresql"
    assert profile["source_uri"] == "postgresql://user:***@localhost/db"
    assert profile["tables"][0]["table_name"] == "contacts"
    assert profile["tables"][0]["row_count"] == 12
    assert profile["tables"][0]["columns"][1]["column_name"] == "email"
    assert profile["tables"][1]["table_type"] == "view"


def test_profile_artifact_can_include_processing_metrics() -> None:
    artifact = profile_to_artifact(sample_profile(), metrics=sample_metrics())

    assert artifact["table_started_at"] == "2026-01-02T03:04:05Z"
    assert artifact["table_completed_at"] == "2026-01-02T03:04:06Z"
    assert artifact["table_processing_seconds"] == 1.25
    assert artifact["cpu_total_seconds"] == 1.0
    assert artifact["peak_memory_mb"] == 128.5


def test_profile_artifact_json_does_not_include_sample_fields() -> None:
    content = profile_artifact_json(sample_profile())

    assert "sampled_count" not in content
    assert "non_null_count" not in content
    assert "max_value_length" not in content
    assert '"values"' not in content


def test_profile_artifact_json_pretty_is_stable_json() -> None:
    content = profile_artifact_json(sample_profile(), pretty=True)
    parsed = json.loads(content)

    assert content.startswith("{\n")
    assert '\n  "artifact_type": "table_extract.profile"' in content
    assert '\n  "profile": {' in content
    assert parsed["summary"]["column_count"] == 4
    assert parsed["profile"]["tables"][0]["columns"][0]["ordinal_position"] == 1


def test_profile_artifact_helpers_are_public_exports() -> None:
    assert table_extract.profile_to_artifact is profile_to_artifact
    assert table_extract.profile_artifact_json is profile_artifact_json
    assert table_extract.PROFILE_ARTIFACT_TYPE == PROFILE_ARTIFACT_TYPE
    assert (
        table_extract.PROFILE_ARTIFACT_SCHEMA_VERSION
        == PROFILE_ARTIFACT_SCHEMA_VERSION
    )


def test_discovery_to_artifact_contains_versioned_wrapper_summary_and_findings() -> None:
    generated_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    artifact = discovery_to_artifact(
        sample_discovery_result(),
        generated_at=generated_at,
    )

    assert artifact["artifact_type"] == DISCOVERY_ARTIFACT_TYPE
    assert artifact["schema_version"] == DISCOVERY_ARTIFACT_SCHEMA_VERSION
    assert artifact["generated_at"] == "2026-01-02T03:04:05Z"
    assert artifact["run_id"] == "run-001"
    assert artifact["summary"]["source_name"] == "inventory"
    assert artifact["summary"]["source_type"] == "database"
    assert artifact["summary"]["dialect"] == "postgresql"
    assert artifact["summary"]["table_count"] == 1
    assert artifact["summary"]["view_count"] == 1
    assert artifact["summary"]["column_count"] == 4
    assert artifact["summary"]["finding_count"] == 3
    assert artifact["summary"]["findings_by_pii_type"] == {
        "DATE": 1,
        "EMAIL": 1,
        "RUT": 1,
    }
    assert artifact["summary"]["findings_by_confidence_level"] == {
        "PROBABLE": 1,
        "VERY_CONFIDENT": 2,
    }
    assert artifact["profile"]["tables"][0]["row_count"] == 12
    assert artifact["findings"][0]["pii_type"] == "EMAIL"


def test_discovery_artifact_can_include_processing_metrics() -> None:
    artifact = discovery_to_artifact(
        sample_discovery_result(),
        metrics=sample_metrics(),
    )

    assert artifact["table_started_at"] == "2026-01-02T03:04:05Z"
    assert artifact["table_completed_at"] == "2026-01-02T03:04:06Z"
    assert artifact["table_processing_seconds"] == 1.25
    assert artifact["cpu_user_seconds"] == 0.75
    assert artifact["cpu_system_seconds"] == 0.25


def test_discovery_artifact_json_does_not_include_samples_or_raw_values() -> None:
    content = discovery_artifact_json(sample_discovery_result(), pretty=True)
    parsed = json.loads(content)

    assert content.startswith("{\n")
    assert '\n  "artifact_type": "table_extract.discovery"' in content
    assert "12.345.678-5" not in content
    assert "a@example.com" not in content
    assert '"values"' not in content
    assert "sampled_count" in content
    assert parsed["findings"][0]["column_name"] == "email"
    assert '"pii_type": "DATE"' in content
    assert "BIRTH_DATE" not in content
    assert parsed["summary"]["finding_count"] == 3


def test_discovery_artifact_helpers_are_public_exports() -> None:
    assert table_extract.discovery_to_artifact is discovery_to_artifact
    assert table_extract.discovery_artifact_json is discovery_artifact_json
    assert table_extract.DISCOVERY_ARTIFACT_TYPE == DISCOVERY_ARTIFACT_TYPE
    assert (
        table_extract.DISCOVERY_ARTIFACT_SCHEMA_VERSION
        == DISCOVERY_ARTIFACT_SCHEMA_VERSION
    )
