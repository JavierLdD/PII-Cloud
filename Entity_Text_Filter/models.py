from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from .config import (
        CONFIDENT,
        MODEL_SCORE_PROBABLE_THRESHOLD,
        PIPELINE_STAGE,
        PROBABLE,
        SCHEMA_VERSION,
        VERY_CONFIDENT,
        ZERO_SHOT_CONFIDENT_THRESHOLD,
        ZERO_SHOT_ENABLED,
        ZERO_SHOT_MIN_MODEL_SCORE_THRESHOLD,
        ZERO_SHOT_MODEL_NAME,
        ZERO_SHOT_OVERLAP_TOP_K,
        ZERO_SHOT_PROBABLE_THRESHOLD,
    )
except ImportError:  # pragma: no cover - script execution fallback
    from config import (
        CONFIDENT,
        MODEL_SCORE_PROBABLE_THRESHOLD,
        PIPELINE_STAGE,
        PROBABLE,
        SCHEMA_VERSION,
        VERY_CONFIDENT,
        ZERO_SHOT_CONFIDENT_THRESHOLD,
        ZERO_SHOT_ENABLED,
        ZERO_SHOT_MIN_MODEL_SCORE_THRESHOLD,
        ZERO_SHOT_MODEL_NAME,
        ZERO_SHOT_OVERLAP_TOP_K,
        ZERO_SHOT_PROBABLE_THRESHOLD,
    )


@dataclass
class EntityEvidence:
    chunk_id: str
    chunk_index: int | None
    page_start: int | None
    page_end: int | None
    entity_type: str
    raw_entity_type: str
    source: str
    text: str
    start: int
    end: int
    score: float
    normalized_value: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def span_length(self) -> int:
        return max(0, self.end - self.start)

    def key(self) -> tuple[object, ...]:
        return (
            self.chunk_id,
            self.start,
            self.end,
            self.source,
            self.raw_entity_type,
            self.text,
        )

    def to_dict(self, mask_text: bool = False) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "entity_type": self.entity_type,
            "raw_entity_type": self.raw_entity_type,
            "source": self.source,
            "text": mask_entity_text(self.text) if mask_text else self.text,
            "start": self.start,
            "end": self.end,
            "score": round(float(self.score), 6),
            "normalized_value": self.normalized_value,
            "trace": self.trace,
        }


@dataclass
class FilteredEntity:
    entity_type: str
    text: str
    normalized_value: str | None
    value_key: str
    source: str
    raw_entity_type: str
    score: float
    is_base: bool
    validation_status: str
    validation_reason: str | None
    confidence_level: str | None
    decision_score: float | None
    decision_method: str | None
    zero_shot_score: float | None
    zero_shot_label: str | None
    primary_location: dict[str, Any]
    evidence: list[EntityEvidence]

    @property
    def entity_id(self) -> str:
        source = "|".join(
            [
                self.entity_type,
                self.value_key,
                str(self.primary_location.get("chunk_id", "")),
                str(self.primary_location.get("start", "")),
                str(self.primary_location.get("end", "")),
                self.source,
                self.raw_entity_type,
            ]
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    def to_dict(self, mask_text: bool = False) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "text": mask_entity_text(self.text) if mask_text else self.text,
            "normalized_value": self.normalized_value,
            "value_key": self.value_key,
            "source": self.source,
            "raw_entity_type": self.raw_entity_type,
            "score": round(float(self.score), 6),
            "is_base": self.is_base,
            "validation_status": self.validation_status,
            "validation_reason": self.validation_reason,
            "confidence_level": self.confidence_level,
            "decision_score": (
                round(float(self.decision_score), 6)
                if self.decision_score is not None
                else None
            ),
            "decision_method": self.decision_method,
            "zero_shot_score": (
                round(float(self.zero_shot_score), 6)
                if self.zero_shot_score is not None
                else None
            ),
            "zero_shot_label": self.zero_shot_label,
            "primary_location": self.primary_location,
            "evidence_count": len(self.evidence),
            "evidence": [
                item.to_dict(mask_text=mask_text) for item in self.evidence
            ],
        }


@dataclass
class FilteredFileResult:
    source_result: dict[str, Any]
    accepted_entities: list[FilteredEntity]
    source_json_path: str | None = None
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    entity_started_at: datetime | None = None
    entity_completed_at: datetime | None = None
    entity_processing_seconds: float | None = None
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    cpu_total_seconds: float | None = None
    peak_memory_mb: float | None = None
    raw_json_path: str | None = None
    filtered_json_path: str | None = None

    @property
    def raw_entity_count(self) -> int:
        chunks = self.source_result.get("chunks", [])
        if not isinstance(chunks, list):
            return 0
        return sum(
            len(chunk.get("entities", []))
            for chunk in chunks
            if isinstance(chunk, dict) and isinstance(chunk.get("entities", []), list)
        )

    def to_dict(self, mask_text: bool = False) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "pipeline_stage": PIPELINE_STAGE,
            "source_schema_version": self.source_result.get("schema_version"),
            "run_id": self.source_result.get("run_id"),
            "file_id": self.source_result.get("file_id"),
            "source_type": self.source_result.get("source_type"),
            "source_uri": self.source_result.get("source_uri"),
            "external_id": self.source_result.get("external_id"),
            "file_name": self.source_result.get("file_name"),
            "relative_path": self.source_result.get("relative_path"),
            "extension": self.source_result.get("extension"),
            "mime_type": self.source_result.get("mime_type"),
            "size_bytes": self.source_result.get("size_bytes"),
            "checksum_sha256": self.source_result.get("checksum_sha256"),
            "content_hash": self.source_result.get("content_hash"),
            "etag": self.source_result.get("etag"),
            "chunk_count": self.source_result.get("chunk_count", 0),
            "raw_entity_count": self.raw_entity_count,
            "accepted_entity_count": len(self.accepted_entities),
            "generated_at": self.generated_at.isoformat(),
            "entity_started_at": (
                self.entity_started_at.isoformat()
                if self.entity_started_at is not None
                else None
            ),
            "entity_completed_at": (
                self.entity_completed_at.isoformat()
                if self.entity_completed_at is not None
                else None
            ),
            "entity_processing_seconds": self.entity_processing_seconds,
            "cpu_user_seconds": self.cpu_user_seconds,
            "cpu_system_seconds": self.cpu_system_seconds,
            "cpu_total_seconds": self.cpu_total_seconds,
            "peak_memory_mb": self.peak_memory_mb,
            "raw_json_path": self.raw_json_path,
            "filtered_json_path": self.filtered_json_path,
            "source_json_path": self.source_json_path,
            "filtering_policy": {
                "base_overlap_wins": True,
                "invalid_base_action": "discard",
                "confidence_levels": [VERY_CONFIDENT, CONFIDENT, PROBABLE],
                "base_confidence_level": VERY_CONFIDENT,
                "zero_shot_enabled": ZERO_SHOT_ENABLED,
                "zero_shot_model": ZERO_SHOT_MODEL_NAME,
                "zero_shot_hypothesis_template": "{}",
                "zero_shot_min_model_score_threshold": (
                    ZERO_SHOT_MIN_MODEL_SCORE_THRESHOLD
                ),
                "zero_shot_overlap_top_k": ZERO_SHOT_OVERLAP_TOP_K,
                "zero_shot_keep_best_per_entity_type": True,
                "zero_shot_confident_threshold": ZERO_SHOT_CONFIDENT_THRESHOLD,
                "zero_shot_probable_threshold": ZERO_SHOT_PROBABLE_THRESHOLD,
                "model_score_probable_threshold": MODEL_SCORE_PROBABLE_THRESHOLD,
                "dedupe_priority": [
                    "base",
                    "presidio",
                    "regex",
                    "deny_list",
                    "gliner2",
                    "medical_model",
                ],
            },
            "accepted_entities": [
                entity.to_dict(mask_text=mask_text)
                for entity in self.accepted_entities
            ],
        }


@dataclass
class WrittenFilteredResult:
    result: FilteredFileResult
    output_path: str


def mask_entity_text(text: str) -> str:
    if not text:
        return text
    if len(text) <= 2:
        return "*" * len(text)
    return f"{text[0]}{'*' * (len(text) - 2)}{text[-1]}"
