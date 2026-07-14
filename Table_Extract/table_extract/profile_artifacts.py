from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
from typing import Any

from table_extract.models import (
    DataSourceProfile,
    DiscoveryResult,
    TableProcessingMetrics,
)


PROFILE_ARTIFACT_TYPE = "table_extract.profile"
PROFILE_ARTIFACT_SCHEMA_VERSION = "1.0"
DISCOVERY_ARTIFACT_TYPE = "table_extract.discovery"
DISCOVERY_ARTIFACT_SCHEMA_VERSION = "1.0"


def profile_to_artifact(
    profile: DataSourceProfile,
    generated_at: datetime | None = None,
    metrics: TableProcessingMetrics | None = None,
) -> dict[str, Any]:
    timestamp = _utc_timestamp(generated_at)
    artifact = {
        "artifact_type": PROFILE_ARTIFACT_TYPE,
        "schema_version": PROFILE_ARTIFACT_SCHEMA_VERSION,
        "generated_at": timestamp.isoformat().replace("+00:00", "Z"),
        "summary": _profile_summary(profile),
        "profile": asdict(profile),
    }
    if metrics is not None:
        artifact.update(_metrics_payload(metrics))
    return artifact


def profile_artifact_json(
    profile: DataSourceProfile,
    pretty: bool = False,
    metrics: TableProcessingMetrics | None = None,
) -> str:
    return json.dumps(
        profile_to_artifact(profile, metrics=metrics),
        ensure_ascii=True,
        indent=2 if pretty else None,
        sort_keys=pretty,
    )


def discovery_to_artifact(
    result: DiscoveryResult,
    generated_at: datetime | None = None,
    metrics: TableProcessingMetrics | None = None,
) -> dict[str, Any]:
    timestamp = _utc_timestamp(generated_at)
    artifact = {
        "artifact_type": DISCOVERY_ARTIFACT_TYPE,
        "schema_version": DISCOVERY_ARTIFACT_SCHEMA_VERSION,
        "generated_at": timestamp.isoformat().replace("+00:00", "Z"),
        "run_id": result.run_id,
        "summary": _discovery_summary(result),
        "profile": asdict(result.profile),
        "findings": [asdict(finding) for finding in result.findings],
    }
    if metrics is not None:
        artifact.update(_metrics_payload(metrics))
    return artifact


def discovery_artifact_json(
    result: DiscoveryResult,
    pretty: bool = False,
    metrics: TableProcessingMetrics | None = None,
) -> str:
    return json.dumps(
        discovery_to_artifact(result, metrics=metrics),
        ensure_ascii=True,
        indent=2 if pretty else None,
        sort_keys=pretty,
    )


def _utc_timestamp(generated_at: datetime | None) -> datetime:
    if generated_at is None:
        return datetime.now(UTC)
    if generated_at.tzinfo is None:
        return generated_at.replace(tzinfo=UTC)
    return generated_at.astimezone(UTC)


def _metrics_payload(metrics: TableProcessingMetrics) -> dict[str, Any]:
    return {
        "table_started_at": _utc_timestamp(metrics.started_at)
        .isoformat()
        .replace("+00:00", "Z"),
        "table_completed_at": _utc_timestamp(metrics.completed_at)
        .isoformat()
        .replace("+00:00", "Z"),
        "table_processing_seconds": metrics.processing_seconds,
        "cpu_user_seconds": metrics.cpu_user_seconds,
        "cpu_system_seconds": metrics.cpu_system_seconds,
        "cpu_total_seconds": metrics.cpu_total_seconds,
        "peak_memory_mb": metrics.peak_memory_mb,
    }


def _profile_summary(profile: DataSourceProfile) -> dict[str, Any]:
    table_count = sum(1 for table in profile.tables if table.table_type != "view")
    view_count = sum(1 for table in profile.tables if table.table_type == "view")
    column_count = sum(len(table.columns) for table in profile.tables)
    return {
        "source_name": profile.source_name,
        "source_type": profile.source_type,
        "dialect": profile.dialect,
        "table_count": table_count,
        "view_count": view_count,
        "column_count": column_count,
    }


def _discovery_summary(result: DiscoveryResult) -> dict[str, Any]:
    summary = _profile_summary(result.profile)
    by_pii_type: dict[str, int] = {}
    by_confidence_level: dict[str, int] = {}
    for finding in result.findings:
        by_pii_type[finding.pii_type] = by_pii_type.get(finding.pii_type, 0) + 1
        confidence_level = finding.confidence_level or "UNKNOWN"
        by_confidence_level[confidence_level] = (
            by_confidence_level.get(confidence_level, 0) + 1
        )
    summary.update(
        {
            "finding_count": len(result.findings),
            "findings_by_pii_type": dict(sorted(by_pii_type.items())),
            "findings_by_confidence_level": dict(
                sorted(by_confidence_level.items())
            ),
        }
    )
    return summary
