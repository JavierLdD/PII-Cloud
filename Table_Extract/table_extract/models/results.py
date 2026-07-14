from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from table_extract.models._validation import (
    require_non_blank,
    require_non_negative_int,
    require_optional_probability,
)
from table_extract.models.profiles import DataSourceProfile

CONFIDENCE_LEVELS = frozenset({"VERY_CONFIDENT", "CONFIDENT", "PROBABLE"})


@dataclass(frozen=True)
class TableProcessingMetrics:
    started_at: datetime
    completed_at: datetime
    processing_seconds: float
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    cpu_total_seconds: float | None = None
    peak_memory_mb: float | None = None

    def __post_init__(self) -> None:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be before started_at")
        if self.processing_seconds < 0:
            raise ValueError("processing_seconds must be non-negative")
        for field_name in (
            "cpu_user_seconds",
            "cpu_system_seconds",
            "cpu_total_seconds",
            "peak_memory_mb",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True)
class DiscoveredPII:
    source_name: str
    table_name: str
    column_name: str
    pii_type: str
    source_type: str | None = None
    schema_name: str | None = None
    confidence: float | None = None
    detection_method: str | None = None
    evidence_summary: str | None = None
    sampled_count: int | None = None
    matched_count: int | None = None
    confidence_level: str | None = None
    is_primary_key: bool = False
    foreign_key: str | None = None
    propagated_from: str | None = None

    def __post_init__(self) -> None:
        require_non_blank(self.source_name, "source_name")
        require_non_blank(self.table_name, "table_name")
        require_non_blank(self.column_name, "column_name")
        require_non_blank(self.pii_type, "pii_type")
        require_optional_probability(self.confidence, "confidence")
        if self.confidence_level is not None and self.confidence_level not in CONFIDENCE_LEVELS:
            raise ValueError("confidence_level must be VERY_CONFIDENT, CONFIDENT, or PROBABLE")
        if self.sampled_count is not None:
            require_non_negative_int(self.sampled_count, "sampled_count")
        if self.matched_count is not None:
            require_non_negative_int(self.matched_count, "matched_count")
        if (
            self.sampled_count is not None
            and self.matched_count is not None
            and self.matched_count > self.sampled_count
        ):
            raise ValueError("matched_count cannot be greater than sampled_count")


@dataclass(frozen=True)
class DiscoveryResult:
    run_id: str
    profile: DataSourceProfile
    findings: tuple[DiscoveredPII, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        require_non_blank(self.run_id, "run_id")
        object.__setattr__(self, "findings", tuple(self.findings))
