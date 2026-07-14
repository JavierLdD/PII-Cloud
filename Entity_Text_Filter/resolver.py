from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from .config import (
        CONFIDENT,
        MODEL_SCORE_PROBABLE_THRESHOLD,
        PROBABLE,
        SOURCE_PRIORITY,
        UNKNOWN_SOURCE_PRIORITY,
        VERY_CONFIDENT,
        ZERO_SHOT_CONFIDENT_THRESHOLD,
        ZERO_SHOT_ENABLED,
        ZERO_SHOT_MIN_MODEL_SCORE_THRESHOLD,
        ZERO_SHOT_OVERLAP_TOP_K,
        ZERO_SHOT_PROBABLE_THRESHOLD,
    )
    from .models import EntityEvidence, FilteredEntity, FilteredFileResult
    from .validators import (
        canonical_entity_type,
        is_base_entity_type,
        is_local_validation_entity_type,
        is_score_threshold_entity_type,
        is_zero_shot_entity_type,
        normalize_base_value,
        normalize_local_validation_value,
        normalize_non_base_value,
        value_key,
    )
    from .zero_shot import ZeroShotScorer, get_default_zero_shot_scorer
except ImportError:  # pragma: no cover - script execution fallback
    from config import (
        CONFIDENT,
        MODEL_SCORE_PROBABLE_THRESHOLD,
        PROBABLE,
        SOURCE_PRIORITY,
        UNKNOWN_SOURCE_PRIORITY,
        VERY_CONFIDENT,
        ZERO_SHOT_CONFIDENT_THRESHOLD,
        ZERO_SHOT_ENABLED,
        ZERO_SHOT_MIN_MODEL_SCORE_THRESHOLD,
        ZERO_SHOT_OVERLAP_TOP_K,
        ZERO_SHOT_PROBABLE_THRESHOLD,
    )
    from models import EntityEvidence, FilteredEntity, FilteredFileResult
    from validators import (
        canonical_entity_type,
        is_base_entity_type,
        is_local_validation_entity_type,
        is_score_threshold_entity_type,
        is_zero_shot_entity_type,
        normalize_base_value,
        normalize_local_validation_value,
        normalize_non_base_value,
        value_key,
    )
    from zero_shot import ZeroShotScorer, get_default_zero_shot_scorer


@dataclass
class Candidate:
    entity_type: str
    raw_entity_type: str
    source: str
    text: str
    start: int
    end: int
    score: float
    normalized_value: str | None
    value_key: str
    is_base: bool
    validation_status: str
    validation_reason: str | None
    chunk_id: str
    chunk_index: int | None
    page_start: int | None
    page_end: int | None
    trace: list[dict[str, Any]]
    confidence_level: str | None = None
    decision_score: float | None = None
    decision_method: str | None = None
    zero_shot_score: float | None = None
    zero_shot_label: str | None = None
    evidence: list[EntityEvidence] = field(default_factory=list)

    @property
    def span_length(self) -> int:
        return max(0, self.end - self.start)

    def add_evidence(self, items: list[EntityEvidence]) -> None:
        seen = {item.key() for item in self.evidence}
        for item in items:
            if item.key() in seen:
                continue
            self.evidence.append(item)
            seen.add(item.key())

    def to_filtered_entity(self) -> FilteredEntity:
        return FilteredEntity(
            entity_type=self.entity_type,
            text=self.text,
            normalized_value=self.normalized_value,
            value_key=self.value_key,
            source=self.source,
            raw_entity_type=self.raw_entity_type,
            score=self.score,
            is_base=self.is_base,
            validation_status=self.validation_status,
            validation_reason=self.validation_reason,
            confidence_level=self.confidence_level,
            decision_score=self.decision_score,
            decision_method=self.decision_method,
            zero_shot_score=self.zero_shot_score,
            zero_shot_label=self.zero_shot_label,
            primary_location={
                "chunk_id": self.chunk_id,
                "chunk_index": self.chunk_index,
                "page_start": self.page_start,
                "page_end": self.page_end,
                "start": self.start,
                "end": self.end,
                "trace": self.trace,
            },
            evidence=self.evidence,
        )


def filter_raw_result(
    raw_result: dict[str, Any],
    source_json_path: str | None = None,
    zero_shot_scorer: ZeroShotScorer | None = None,
) -> FilteredFileResult:
    candidates = _build_candidates(raw_result)
    bases, non_bases = _resolve_base_stage(candidates)
    scored_non_bases = _score_non_base_candidates(
        non_bases,
        zero_shot_scorer=zero_shot_scorer,
    )
    span_filtered = bases + _resolve_non_base_overlaps(scored_non_bases)
    value_filtered = _dedupe_by_value(span_filtered)
    accepted = [
        candidate.to_filtered_entity()
        for candidate in sorted(value_filtered, key=_stable_location_key)
    ]
    return FilteredFileResult(
        source_result=raw_result,
        accepted_entities=accepted,
        source_json_path=source_json_path,
    )


def _build_candidates(raw_result: dict[str, Any]) -> list[Candidate]:
    chunks = raw_result.get("chunks", [])
    if not isinstance(chunks, list):
        raise ValueError("Raw entity result must contain a chunks list")

    candidates: list[Candidate] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        entities = chunk.get("entities", [])
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            candidate = _candidate_from_raw_entity(chunk, entity)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _candidate_from_raw_entity(
    chunk: dict[str, Any],
    entity: dict[str, Any],
) -> Candidate | None:
    text = str(entity.get("text") or "")
    start = _int_or_none(entity.get("start"))
    end = _int_or_none(entity.get("end"))
    if start is None or end is None or end <= start:
        return None

    raw_entity_type = str(entity.get("raw_entity_type") or entity.get("entity_type"))
    original_type = str(entity.get("entity_type") or raw_entity_type)
    entity_type = canonical_entity_type(original_type, raw_entity_type)
    source = str(entity.get("source") or "unknown")
    score = _float_or_default(entity.get("score"), 0.0)
    raw_normalized = _optional_str(entity.get("normalized_value"))
    base_type = is_base_entity_type(entity_type)

    if base_type:
        normalized_value = normalize_base_value(entity_type, text)
        if normalized_value is None:
            return None
        candidate_value_key = value_key(normalized_value)
        validation_status = "validated"
        validation_reason = None
        confidence_level = VERY_CONFIDENT
        decision_score = 1.0
        decision_method = "base_validation"
        is_base = True
    else:
        normalized_value = normalize_non_base_value(
            entity_type,
            text,
            raw_normalized,
        )
        candidate_value_key = value_key(normalized_value or text)
        validation_status = "pending"
        validation_reason = None
        confidence_level = None
        decision_score = None
        decision_method = None
        is_base = False

    trace = entity.get("trace")
    if not isinstance(trace, list):
        trace = []

    evidence = EntityEvidence(
        chunk_id=str(chunk.get("chunk_id") or ""),
        chunk_index=_int_or_none(chunk.get("chunk_index")),
        page_start=_int_or_none(chunk.get("page_start")),
        page_end=_int_or_none(chunk.get("page_end")),
        entity_type=entity_type,
        raw_entity_type=raw_entity_type,
        source=source,
        text=text,
        start=start,
        end=end,
        score=score,
        normalized_value=normalized_value,
        trace=trace,
    )

    return Candidate(
        entity_type=entity_type,
        raw_entity_type=raw_entity_type,
        source=source,
        text=text,
        start=start,
        end=end,
        score=score,
        normalized_value=normalized_value,
        value_key=candidate_value_key,
        is_base=is_base,
        validation_status=validation_status,
        validation_reason=validation_reason,
        chunk_id=evidence.chunk_id,
        chunk_index=evidence.chunk_index,
        page_start=evidence.page_start,
        page_end=evidence.page_end,
        trace=trace,
        confidence_level=confidence_level,
        decision_score=decision_score,
        decision_method=decision_method,
        evidence=[evidence],
    )


def _resolve_base_stage(candidates: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
    by_chunk: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_chunk.setdefault(candidate.chunk_id, []).append(candidate)

    all_bases: list[Candidate] = []
    remaining_non_bases: list[Candidate] = []
    for chunk_candidates in by_chunk.values():
        bases = _resolve_base_overlaps(
            [candidate for candidate in chunk_candidates if candidate.is_base]
        )
        non_bases = [candidate for candidate in chunk_candidates if not candidate.is_base]

        for candidate in non_bases:
            overlapping_bases = [base for base in bases if overlaps(base, candidate)]
            if not overlapping_bases:
                remaining_non_bases.append(candidate)
                continue
            winner = _best_overlap_target(candidate, overlapping_bases)
            winner.add_evidence(candidate.evidence)

        all_bases.extend(bases)

    return all_bases, remaining_non_bases


def _resolve_base_overlaps(candidates: list[Candidate]) -> list[Candidate]:
    kept: list[Candidate] = []
    for candidate in sorted(candidates, key=_decision_resolution_key):
        winner = next(
            (
                kept_candidate
                for kept_candidate in kept
                if _gender_deny_list_wins(kept_candidate, candidate)
            ),
            None,
        )
        if winner is not None:
            winner.add_evidence(candidate.evidence)
            continue

        losers = [
            kept_candidate
            for kept_candidate in kept
            if _gender_deny_list_wins(candidate, kept_candidate)
        ]
        for loser in losers:
            candidate.add_evidence(loser.evidence)
            kept.remove(loser)

        overlapping_kept = [kept_candidate for kept_candidate in kept if overlaps(kept_candidate, candidate)]
        if overlapping_kept:
            winner = _best_overlap_target(candidate, overlapping_kept)
            winner.add_evidence(candidate.evidence)
            continue

        kept.append(candidate)

    return kept


def _score_non_base_candidates(
    candidates: list[Candidate],
    zero_shot_scorer: ZeroShotScorer | None,
) -> list[Candidate]:
    output: list[Candidate] = []
    zero_shot_candidates: list[Candidate] = []

    for candidate in candidates:
        if is_local_validation_entity_type(candidate.entity_type):
            normalized_value = normalize_local_validation_value(
                candidate.entity_type,
                candidate.text,
            )
            if normalized_value is None:
                continue
            if (
                candidate.entity_type in {"IP_ADDRESS", "MAC_ADDRESS", "URL"}
                and candidate.score <= MODEL_SCORE_PROBABLE_THRESHOLD
            ):
                continue
            candidate.normalized_value = normalized_value
            candidate.value_key = value_key(normalized_value)
            candidate.confidence_level = PROBABLE
            candidate.decision_score = candidate.score
            candidate.decision_method = "local_validation"
            candidate.validation_status = "validated"
            output.append(candidate)
            continue

        if is_score_threshold_entity_type(candidate.entity_type):
            if candidate.score <= MODEL_SCORE_PROBABLE_THRESHOLD:
                continue
            candidate.confidence_level = PROBABLE
            candidate.decision_score = candidate.score
            candidate.decision_method = "model_score_threshold"
            candidate.validation_status = "accepted_by_model_score"
            output.append(candidate)
            continue

        if is_zero_shot_entity_type(candidate.entity_type):
            if not ZERO_SHOT_ENABLED:
                continue
            if candidate.score < ZERO_SHOT_MIN_MODEL_SCORE_THRESHOLD:
                continue
            zero_shot_candidates.append(candidate)

    selected_zero_shot_candidates = _select_zero_shot_candidates(zero_shot_candidates)
    if selected_zero_shot_candidates:
        scorer = zero_shot_scorer or get_default_zero_shot_scorer()
        pairs = [
            (candidate.text, _zero_shot_label(candidate.entity_type))
            for candidate in selected_zero_shot_candidates
        ]
        scores = scorer(pairs)
        if len(scores) != len(selected_zero_shot_candidates):
            raise ValueError("Zero-shot scorer returned an unexpected score count")

        for candidate, score in zip(selected_zero_shot_candidates, scores):
            score = float(score)
            if score >= ZERO_SHOT_CONFIDENT_THRESHOLD:
                confidence_level = CONFIDENT
            elif score >= ZERO_SHOT_PROBABLE_THRESHOLD:
                confidence_level = PROBABLE
            else:
                continue

            candidate.zero_shot_score = score
            candidate.zero_shot_label = _zero_shot_label(candidate.entity_type)
            candidate.confidence_level = confidence_level
            candidate.decision_score = score
            candidate.decision_method = "zero_shot"
            candidate.validation_status = "validated_by_zero_shot"
            output.append(candidate)

    return output


def _select_zero_shot_candidates(candidates: list[Candidate]) -> list[Candidate]:
    by_chunk: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_chunk.setdefault(candidate.chunk_id, []).append(candidate)

    selected: list[Candidate] = []
    for chunk_candidates in by_chunk.values():
        for group in _overlap_groups(chunk_candidates):
            selected.extend(_select_zero_shot_overlap_group(group))
    return selected


def _overlap_groups(candidates: list[Candidate]) -> list[list[Candidate]]:
    ordered = sorted(candidates, key=_stable_location_key)
    visited: set[int] = set()
    groups: list[list[Candidate]] = []
    for candidate in ordered:
        candidate_id = id(candidate)
        if candidate_id in visited:
            continue
        visited.add(candidate_id)
        group: list[Candidate] = []
        stack = [candidate]
        while stack:
            current = stack.pop()
            group.append(current)
            for other in ordered:
                other_id = id(other)
                if other_id in visited:
                    continue
                if overlaps(current, other):
                    visited.add(other_id)
                    stack.append(other)
        groups.append(group)
    return groups


def _select_zero_shot_overlap_group(group: list[Candidate]) -> list[Candidate]:
    ranked = sorted(group, key=_zero_shot_prefilter_key)
    selected = ranked[:ZERO_SHOT_OVERLAP_TOP_K]
    selected_ids = {id(candidate) for candidate in selected}

    by_type: dict[str, list[Candidate]] = {}
    for candidate in ranked:
        by_type.setdefault(candidate.entity_type, []).append(candidate)
    for type_candidates in by_type.values():
        best_for_type = type_candidates[0]
        if id(best_for_type) not in selected_ids:
            selected.append(best_for_type)
            selected_ids.add(id(best_for_type))

    return sorted(selected, key=_zero_shot_prefilter_key)


def _resolve_non_base_overlaps(candidates: list[Candidate]) -> list[Candidate]:
    by_chunk: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_chunk.setdefault(candidate.chunk_id, []).append(candidate)

    output: list[Candidate] = []
    for chunk_candidates in by_chunk.values():
        kept: list[Candidate] = []
        for candidate in sorted(chunk_candidates, key=_decision_resolution_key):
            overlapping_kept = [kept_candidate for kept_candidate in kept if overlaps(kept_candidate, candidate)]
            if not overlapping_kept:
                kept.append(candidate)
                continue
            winner = _best_overlap_target(candidate, overlapping_kept)
            winner.add_evidence(candidate.evidence)
        output.extend(kept)
    return output


def _dedupe_by_value(candidates: list[Candidate]) -> list[Candidate]:
    by_value: dict[tuple[str, str], list[Candidate]] = {}
    for candidate in candidates:
        key = (candidate.entity_type, candidate.value_key or _fallback_value_key(candidate))
        by_value.setdefault(key, []).append(candidate)

    winners: list[Candidate] = []
    for group in by_value.values():
        if len(group) == 1:
            winners.append(group[0])
            continue
        winner = sorted(group, key=_decision_resolution_key)[0]
        for candidate in group:
            if candidate is winner:
                continue
            winner.add_evidence(candidate.evidence)
        winners.append(winner)

    return winners


def overlaps(first: Candidate, second: Candidate) -> bool:
    return first.chunk_id == second.chunk_id and max(first.start, second.start) < min(
        first.end,
        second.end,
    )


def _best_overlap_target(
    loser: Candidate,
    targets: list[Candidate],
) -> Candidate:
    return sorted(
        targets,
        key=lambda target: (
            -_overlap_size(loser, target),
            _decision_resolution_key(target),
        ),
    )[0]


def _overlap_size(first: Candidate, second: Candidate) -> int:
    return max(0, min(first.end, second.end) - max(first.start, second.start))


def _gender_deny_list_wins(winner: Candidate, loser: Candidate) -> bool:
    return (
        winner.entity_type == "GENDER"
        and loser.entity_type == "GENDER"
        and winner.raw_entity_type == "GENDER_IDENTITY_DENY_LIST"
        and loser.raw_entity_type == "GENDER_IDENTITY_CONTEXT_REGEX"
        and overlaps(winner, loser)
    )


def _source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source, UNKNOWN_SOURCE_PRIORITY)


def _decision_resolution_key(candidate: Candidate) -> tuple[object, ...]:
    return (
        -_confidence_rank(candidate.confidence_level),
        -(candidate.decision_score or 0.0),
        -candidate.score,
        -candidate.span_length,
        _source_rank(candidate.source),
        candidate.chunk_index if candidate.chunk_index is not None else 10**9,
        candidate.start,
        candidate.end,
        candidate.entity_type,
        candidate.raw_entity_type,
    )


def _zero_shot_prefilter_key(candidate: Candidate) -> tuple[object, ...]:
    return (
        -candidate.score,
        -candidate.span_length,
        _source_rank(candidate.source),
        candidate.chunk_index if candidate.chunk_index is not None else 10**9,
        candidate.start,
        candidate.end,
        candidate.entity_type,
        candidate.raw_entity_type,
        candidate.text,
    )


def _stable_location_key(candidate: Candidate) -> tuple[object, ...]:
    return (
        candidate.chunk_index if candidate.chunk_index is not None else 10**9,
        candidate.start,
        candidate.end,
        candidate.entity_type,
        candidate.raw_entity_type,
    )


def _confidence_rank(confidence_level: str | None) -> int:
    if confidence_level == VERY_CONFIDENT:
        return 3
    if confidence_level == CONFIDENT:
        return 2
    if confidence_level == PROBABLE:
        return 1
    return 0


def _zero_shot_label(entity_type: str) -> str:
    return entity_type


def _fallback_value_key(candidate: Candidate) -> str:
    return "|".join(
        [
            candidate.chunk_id,
            str(candidate.start),
            str(candidate.end),
            candidate.entity_type,
        ]
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: object, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
