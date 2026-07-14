from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from typing import Any
from uuid import UUID, uuid5

from cloud_bbdd_job.scan_request import ScanRequest


DISCOVERY_ARTIFACT_TYPE = "table_extract.discovery"


class ResultsPersistenceError(ValueError):
    """Raised when a discovery artifact cannot be persisted safely."""


@dataclass(frozen=True)
class _TableRow:
    table_id: UUID
    schema_name: str | None
    table_name: str
    table_type: str
    row_count: int | None
    column_count: int
    finding_count: int


@dataclass(frozen=True)
class _FindingRow:
    finding_id: UUID
    finding_index: int
    schema_name: str | None
    table_name: str
    column_name: str
    pii_type: str
    confidence: float | None
    confidence_level: str | None
    detection_method: str | None
    sampled_count: int | None
    matched_count: int | None
    is_primary_key: bool
    foreign_key: str | None
    propagated_from: str | None


@dataclass(frozen=True)
class _DiscoveryRows:
    run_id: UUID
    generated_at: datetime
    started_at: datetime
    completed_at: datetime
    processing_seconds: float | None
    peak_memory_mb: float | None
    schema_count: int
    table_count: int
    view_count: int
    column_count: int
    finding_count: int
    affected_schema_count: int
    affected_table_count: int
    affected_column_count: int
    pii_type_count: int
    artifact_schema_version: str
    tables: tuple[_TableRow, ...] = field(default_factory=tuple)
    findings: tuple[_FindingRow, ...] = field(default_factory=tuple)


class DatabaseResultsRepository:
    """Persists whitelisted BBDD discovery metadata in one transaction."""

    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        database_url = database_url.strip()
        if not database_url:
            raise ResultsPersistenceError("BBDD_RESULTS_DATABASE_URL is required")
        self._database_url = database_url
        self._connect = connect or _psycopg_connect

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        connect: Callable[[str], Any] | None = None,
    ) -> "DatabaseResultsRepository":
        values = os.environ if env is None else env
        return cls(values.get("BBDD_RESULTS_DATABASE_URL", ""), connect=connect)

    def persist_discovery(
        self,
        *,
        scan_request: ScanRequest,
        artifact: Mapping[str, object],
        artifact_uri: str,
        artifact_size_bytes: int,
        artifact_sha256: str,
    ) -> None:
        artifact_uri = _required_text(artifact_uri, "artifact_uri")
        if not artifact_uri.startswith("gs://"):
            raise ResultsPersistenceError("artifact_uri must use gs://")
        if artifact_size_bytes < 0:
            raise ResultsPersistenceError("artifact_size_bytes must be non-negative")
        if not _is_sha256(artifact_sha256):
            raise ResultsPersistenceError("artifact_sha256 must contain 64 hex characters")

        rows = _artifact_rows(scan_request, artifact)
        with self._connect(self._database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _UPSERT_RUN_SQL,
                    (
                        rows.run_id,
                        scan_request.user_id,
                        scan_request.run_name,
                        scan_request.database_type,
                        scan_request.source_name,
                        artifact_uri,
                        rows.artifact_schema_version,
                        artifact_size_bytes,
                        artifact_sha256.lower(),
                        "completed",
                        rows.generated_at,
                        rows.started_at,
                        rows.completed_at,
                        rows.processing_seconds,
                        rows.peak_memory_mb,
                        rows.schema_count,
                        rows.table_count,
                        rows.view_count,
                        rows.column_count,
                        rows.finding_count,
                        rows.affected_schema_count,
                        rows.affected_table_count,
                        rows.affected_column_count,
                        rows.pii_type_count,
                    ),
                )
                if cursor.fetchone() is None:
                    raise ResultsPersistenceError(
                        "run_id already exists with different immutable metadata"
                    )
                cursor.execute(
                    "DELETE FROM database_discovery_findings WHERE run_id = %s",
                    (rows.run_id,),
                )
                cursor.execute(
                    "DELETE FROM database_discovery_tables WHERE run_id = %s",
                    (rows.run_id,),
                )
                for table in rows.tables:
                    cursor.execute(
                        _INSERT_TABLE_SQL,
                        (
                            table.table_id,
                            rows.run_id,
                            table.schema_name,
                            table.table_name,
                            table.table_type,
                            table.row_count,
                            table.column_count,
                            table.finding_count,
                        ),
                    )
                for finding in rows.findings:
                    cursor.execute(
                        _INSERT_FINDING_SQL,
                        (
                            finding.finding_id,
                            rows.run_id,
                            finding.finding_index,
                            finding.schema_name,
                            finding.table_name,
                            finding.column_name,
                            finding.pii_type,
                            finding.confidence,
                            finding.confidence_level,
                            finding.detection_method,
                            finding.sampled_count,
                            finding.matched_count,
                            finding.is_primary_key,
                            finding.foreign_key,
                            finding.propagated_from,
                        ),
                    )


def artifact_metadata(content: bytes) -> tuple[dict[str, object], int, str]:
    """Parse an uploaded artifact and return exact byte metadata."""

    try:
        parsed = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResultsPersistenceError("Discovery artifact is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ResultsPersistenceError("Discovery artifact must be a JSON object")
    return parsed, len(content), hashlib.sha256(content).hexdigest()


def _artifact_rows(
    scan_request: ScanRequest,
    artifact: Mapping[str, object],
) -> _DiscoveryRows:
    if artifact.get("artifact_type") != DISCOVERY_ARTIFACT_TYPE:
        raise ResultsPersistenceError(
            f"artifact_type must be {DISCOVERY_ARTIFACT_TYPE!r}"
        )
    try:
        run_id = UUID(scan_request.scan_id)
    except ValueError as exc:
        raise ResultsPersistenceError("scan_id must be a UUID") from exc
    if _required_text(artifact.get("run_id"), "run_id") != scan_request.scan_id:
        raise ResultsPersistenceError("Artifact run_id does not match ScanRequest")

    profile = _required_mapping(artifact.get("profile"), "profile")
    artifact_dialect = _optional_text(profile.get("dialect"))
    if (
        artifact_dialect
        and _normalize_database_type(artifact_dialect) != scan_request.database_type
    ):
        raise ResultsPersistenceError("Artifact dialect does not match database_type")

    raw_tables = _required_list(profile.get("tables"), "profile.tables")
    raw_findings = _required_list(artifact.get("findings"), "findings")
    findings = tuple(
        _finding_row(run_id, index, _required_mapping(value, f"findings[{index}]"))
        for index, value in enumerate(raw_findings)
    )

    findings_per_table: dict[tuple[str | None, str], int] = {}
    for finding in findings:
        key = (finding.schema_name, finding.table_name)
        findings_per_table[key] = findings_per_table.get(key, 0) + 1

    tables: list[_TableRow] = []
    table_keys: set[tuple[str | None, str, str]] = set()
    for index, value in enumerate(raw_tables):
        table = _required_mapping(value, f"profile.tables[{index}]")
        schema_name = _optional_text(table.get("schema_name"))
        table_name = _required_text(table.get("table_name"), "table_name")
        table_type = (_optional_text(table.get("table_type")) or "table").casefold()
        key = (schema_name, table_name, table_type)
        if key in table_keys:
            raise ResultsPersistenceError("Artifact contains duplicate table metadata")
        table_keys.add(key)
        columns = _required_list(table.get("columns"), "table.columns")
        table_id = uuid5(run_id, f"table:{index}:{schema_name or ''}:{table_name}:{table_type}")
        tables.append(
            _TableRow(
                table_id=table_id,
                schema_name=schema_name,
                table_name=table_name,
                table_type=table_type,
                row_count=_optional_non_negative_int(table.get("row_count"), "row_count"),
                column_count=len(columns),
                finding_count=findings_per_table.get((schema_name, table_name), 0),
            )
        )

    generated_at = _timestamp(artifact.get("generated_at"), "generated_at")
    started_at = _optional_timestamp(artifact.get("table_started_at")) or generated_at
    completed_at = _optional_timestamp(artifact.get("table_completed_at")) or generated_at
    if completed_at < started_at:
        raise ResultsPersistenceError("table_completed_at cannot precede table_started_at")

    schemas = {table.schema_name for table in tables if table.schema_name}
    affected_schemas = {finding.schema_name for finding in findings if finding.schema_name}
    affected_tables = {(finding.schema_name, finding.table_name) for finding in findings}
    affected_columns = {
        (finding.schema_name, finding.table_name, finding.column_name)
        for finding in findings
    }
    pii_types = {finding.pii_type for finding in findings}
    table_count = sum(table.table_type != "view" for table in tables)
    view_count = sum(table.table_type == "view" for table in tables)

    return _DiscoveryRows(
        run_id=run_id,
        generated_at=generated_at,
        started_at=started_at,
        completed_at=completed_at,
        processing_seconds=_optional_non_negative_float(
            artifact.get("table_processing_seconds"),
            "table_processing_seconds",
        ),
        peak_memory_mb=_optional_non_negative_float(
            artifact.get("peak_memory_mb"),
            "peak_memory_mb",
        ),
        schema_count=len(schemas),
        table_count=table_count,
        view_count=view_count,
        column_count=sum(table.column_count for table in tables),
        finding_count=len(findings),
        affected_schema_count=len(affected_schemas),
        affected_table_count=len(affected_tables),
        affected_column_count=len(affected_columns),
        pii_type_count=len(pii_types),
        artifact_schema_version=_required_text(
            artifact.get("schema_version"),
            "schema_version",
        ),
        tables=tuple(tables),
        findings=findings,
    )


def _finding_row(
    run_id: UUID,
    index: int,
    finding: Mapping[str, object],
) -> _FindingRow:
    schema_name = _optional_text(finding.get("schema_name"))
    table_name = _required_text(finding.get("table_name"), "table_name")
    column_name = _required_text(finding.get("column_name"), "column_name")
    pii_type = _required_text(finding.get("pii_type"), "pii_type")
    confidence = _optional_probability(finding.get("confidence"), "confidence")
    sampled_count = _optional_non_negative_int(
        finding.get("sampled_count"),
        "sampled_count",
    )
    matched_count = _optional_non_negative_int(
        finding.get("matched_count"),
        "matched_count",
    )
    if sampled_count is not None and matched_count is not None and matched_count > sampled_count:
        raise ResultsPersistenceError("matched_count cannot exceed sampled_count")
    return _FindingRow(
        finding_id=uuid5(
            run_id,
            f"finding:{index}:{schema_name or ''}:{table_name}:{column_name}:{pii_type}",
        ),
        finding_index=index,
        schema_name=schema_name,
        table_name=table_name,
        column_name=column_name,
        pii_type=pii_type,
        confidence=confidence,
        confidence_level=_optional_text(finding.get("confidence_level")),
        detection_method=_optional_text(finding.get("detection_method")),
        sampled_count=sampled_count,
        matched_count=matched_count,
        is_primary_key=_bool_value(finding.get("is_primary_key"), default=False),
        foreign_key=_optional_text(finding.get("foreign_key")),
        propagated_from=_optional_text(finding.get("propagated_from")),
    )


def _psycopg_connect(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Missing psycopg dependency required by BBDD_RESULTS_DATABASE_URL"
        ) from exc
    return psycopg.connect(database_url)


def _required_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ResultsPersistenceError(f"{name} must be a JSON object")
    return value


def _required_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ResultsPersistenceError(f"{name} must be a JSON array")
    return value


def _required_text(value: object, name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ResultsPersistenceError(f"{name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResultsPersistenceError("Expected a string value")
    text = value.strip()
    return text or None


def _timestamp(value: object, name: str) -> datetime:
    text = _required_text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResultsPersistenceError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, "timestamp")


def _optional_non_negative_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResultsPersistenceError(f"{name} must be a non-negative integer")
    return value


def _optional_non_negative_float(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResultsPersistenceError(f"{name} must be a non-negative number")
    result = float(value)
    if result < 0:
        raise ResultsPersistenceError(f"{name} must be a non-negative number")
    return result


def _optional_probability(value: object, name: str) -> float | None:
    result = _optional_non_negative_float(value, name)
    if result is not None and result > 1:
        raise ResultsPersistenceError(f"{name} must be between 0 and 1")
    return result


def _bool_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ResultsPersistenceError("Expected a boolean value")


def _normalize_database_type(value: str) -> str:
    value = value.casefold()
    if value in {"postgres", "postgresql", "postgresql+psycopg"}:
        return "postgresql"
    if value in {"oracle", "oracle+oracledb"}:
        return "oracle"
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


_UPSERT_RUN_SQL = """
    INSERT INTO database_discovery_runs (
        run_id, user_id, run_name, database_type, source_name,
        artifact_uri, artifact_schema_version, artifact_size_bytes,
        artifact_sha256, status, generated_at, started_at, completed_at,
        processing_seconds, peak_memory_mb, schema_count, table_count,
        view_count, column_count, finding_count, affected_schema_count,
        affected_table_count, affected_column_count, pii_type_count,
        created_at, updated_at
    ) VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, now(), now()
    )
    ON CONFLICT (run_id) DO UPDATE SET
        user_id = EXCLUDED.user_id,
        run_name = EXCLUDED.run_name,
        database_type = EXCLUDED.database_type,
        source_name = EXCLUDED.source_name,
        artifact_uri = EXCLUDED.artifact_uri,
        artifact_schema_version = EXCLUDED.artifact_schema_version,
        artifact_size_bytes = EXCLUDED.artifact_size_bytes,
        artifact_sha256 = EXCLUDED.artifact_sha256,
        status = EXCLUDED.status,
        generated_at = EXCLUDED.generated_at,
        started_at = EXCLUDED.started_at,
        completed_at = EXCLUDED.completed_at,
        processing_seconds = EXCLUDED.processing_seconds,
        peak_memory_mb = EXCLUDED.peak_memory_mb,
        schema_count = EXCLUDED.schema_count,
        table_count = EXCLUDED.table_count,
        view_count = EXCLUDED.view_count,
        column_count = EXCLUDED.column_count,
        finding_count = EXCLUDED.finding_count,
        affected_schema_count = EXCLUDED.affected_schema_count,
        affected_table_count = EXCLUDED.affected_table_count,
        affected_column_count = EXCLUDED.affected_column_count,
        pii_type_count = EXCLUDED.pii_type_count,
        updated_at = now()
    WHERE database_discovery_runs.user_id = EXCLUDED.user_id
      AND database_discovery_runs.run_name = EXCLUDED.run_name
      AND database_discovery_runs.database_type = EXCLUDED.database_type
    RETURNING user_id
"""


_INSERT_TABLE_SQL = """
    INSERT INTO database_discovery_tables (
        table_id, run_id, schema_name, table_name, table_type,
        row_count, column_count, finding_count
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


_INSERT_FINDING_SQL = """
    INSERT INTO database_discovery_findings (
        finding_id, run_id, finding_index, schema_name, table_name,
        column_name, pii_type, confidence, confidence_level,
        detection_method, sampled_count, matched_count, is_primary_key,
        foreign_key, propagated_from
    ) VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s
    )
"""
