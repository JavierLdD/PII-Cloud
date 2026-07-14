from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from Entity_Text_Filter.resolver import filter_raw_result


def raw_entity(
    entity_type: str,
    text: str,
    start: int,
    end: int,
    *,
    source: str = "gliner2",
    raw_entity_type: str | None = None,
    score: float = 0.7,
    normalized_value: str | None = None,
) -> dict[str, object]:
    return {
        "entity_type": entity_type,
        "raw_entity_type": raw_entity_type or entity_type,
        "source": source,
        "text": text,
        "start": start,
        "end": end,
        "score": score,
        "normalized_value": normalized_value,
        "trace": [
            {
                "source_block_id": "p1-b1",
                "page_number": 1,
                "bbox": [1, 2, 3, 4],
            }
        ],
    }


def raw_result(chunks: list[list[dict[str, object]]]) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "run_id": "run-1",
        "file_id": "file-1",
        "source_type": "local",
        "source_uri": "local:///tmp/documento.pdf",
        "external_id": "/tmp/documento.pdf",
        "file_name": "documento.pdf",
        "relative_path": "subdir/documento.pdf",
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "checksum_sha256": "a" * 64,
        "content_hash": None,
        "etag": None,
        "chunk_count": len(chunks),
        "entity_count": sum(len(chunk) for chunk in chunks),
        "generated_at": "2026-06-11T00:00:00+00:00",
        "chunks": [
            {
                "chunk_id": f"file-1:c{idx:06d}",
                "chunk_index": idx,
                "page_start": idx,
                "page_end": idx,
                "method": "pymupdf",
                "text_hash_sha256": "b" * 64,
                "entity_count": len(entities),
                "entities": entities,
            }
            for idx, entities in enumerate(chunks, start=1)
        ],
    }


def zero_shot_scorer(scores: dict[tuple[str, str], float]):
    def score(pairs: list[tuple[str, str]]) -> list[float]:
        return [scores[(text, label)] for text, label in pairs]

    return score


def test_valid_base_is_very_confident_and_keeps_overlapping_evidence():
    payload = raw_result(
        [
            [
                raw_entity(
                    "RUT",
                    "12.378.895-8",
                    0,
                    12,
                    source="regex",
                    raw_entity_type="RUT_REGEX",
                    score=0.99,
                ),
                raw_entity(
                    "GLINER2_NATIONAL_ID_NUMBER",
                    "12.378.895-8",
                    0,
                    12,
                    source="gliner2",
                    score=0.99,
                ),
            ]
        ]
    )

    result = filter_raw_result(payload)

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.entity_type == "RUT"
    assert accepted.is_base is True
    assert accepted.confidence_level == "VERY_CONFIDENT"
    assert accepted.decision_method == "base_validation"
    assert accepted.normalized_value == "12.378.895-8"
    assert len(accepted.evidence) == 2


def test_invalid_base_candidate_is_discarded():
    payload = raw_result(
        [
            [
                raw_entity(
                    "RUT",
                    "12.378.895-9",
                    0,
                    12,
                    source="regex",
                    raw_entity_type="RUT_REGEX",
                    score=0.99,
                )
            ]
        ]
    )

    result = filter_raw_result(payload)

    assert result.accepted_entities == []


def test_zero_shot_thresholds_assign_confident_probable_and_discard_low_score():
    payload = raw_result(
        [
            [
                raw_entity("GLINER2_PERSON", "Ana Torres", 0, 10, score=0.95),
                raw_entity("GLINER2_PERSON", "Luis Soto", 20, 29, score=0.50),
                raw_entity("GLINER2_PERSON", "texto comun", 40, 51, score=0.99),
            ]
        ]
    )

    result = filter_raw_result(
        payload,
        zero_shot_scorer=zero_shot_scorer(
            {
                ("Ana Torres", "NAME"): 0.86,
                ("Luis Soto", "NAME"): 0.50,
                ("texto comun", "NAME"): 0.49,
            }
        ),
    )

    accepted_by_text = {entity.text: entity for entity in result.accepted_entities}
    assert set(accepted_by_text) == {"Ana Torres", "Luis Soto"}
    assert accepted_by_text["Ana Torres"].entity_type == "NAME"
    assert accepted_by_text["Ana Torres"].confidence_level == "CONFIDENT"
    assert accepted_by_text["Ana Torres"].zero_shot_score == 0.86
    assert accepted_by_text["Luis Soto"].score == 0.50
    assert accepted_by_text["Luis Soto"].confidence_level == "PROBABLE"


def test_zero_shot_skips_model_scores_below_half_before_scoring():
    payload = raw_result(
        [
            [
                raw_entity("GLINER2_PERSON", "Descartada", 0, 10, score=0.49),
                raw_entity("GLINER2_PERSON", "Limite", 20, 26, score=0.50),
                raw_entity("GLINER2_PERSON", "Zero Bajo", 40, 49, score=0.99),
            ]
        ]
    )
    scored_pairs: list[tuple[str, str]] = []

    def scorer(pairs: list[tuple[str, str]]) -> list[float]:
        scored_pairs.extend(pairs)
        scores = {
            ("Limite", "NAME"): 0.50,
            ("Zero Bajo", "NAME"): 0.49,
        }
        return [scores[pair] for pair in pairs]

    result = filter_raw_result(payload, zero_shot_scorer=scorer)

    assert scored_pairs == [("Limite", "NAME"), ("Zero Bajo", "NAME")]
    assert [entity.text for entity in result.accepted_entities] == ["Limite"]
    accepted = result.accepted_entities[0]
    assert accepted.score == 0.50
    assert accepted.zero_shot_score == 0.50
    assert accepted.confidence_level == "PROBABLE"


def test_zero_shot_scores_top_five_by_overlap_group():
    entities = [
        raw_entity(
            "GLINER2_PERSON",
            f"Persona {index}",
            index,
            index + 20,
            score=0.95 - (index * 0.01),
        )
        for index in range(6)
    ]
    scored_pairs: list[tuple[str, str]] = []

    def scorer(pairs: list[tuple[str, str]]) -> list[float]:
        scored_pairs.extend(pairs)
        return [0.90 for _pair in pairs]

    filter_raw_result(raw_result([entities]), zero_shot_scorer=scorer)

    assert scored_pairs == [
        ("Persona 0", "NAME"),
        ("Persona 1", "NAME"),
        ("Persona 2", "NAME"),
        ("Persona 3", "NAME"),
        ("Persona 4", "NAME"),
    ]


def test_zero_shot_scores_best_candidate_per_type_outside_top_five():
    entities = [
        raw_entity(
            "GLINER2_PERSON",
            f"Persona {index}",
            index,
            index + 20,
            score=0.95 - (index * 0.01),
        )
        for index in range(5)
    ]
    entities.append(raw_entity("GLINER2_CITY", "Santiago", 2, 10, score=0.60))
    scored_pairs: list[tuple[str, str]] = []

    def scorer(pairs: list[tuple[str, str]]) -> list[float]:
        scored_pairs.extend(pairs)
        return [0.50 for _pair in pairs]

    filter_raw_result(raw_result([entities]), zero_shot_scorer=scorer)

    assert len(scored_pairs) == 6
    assert ("Santiago", "LOCATION") in scored_pairs


def test_non_base_overlap_uses_decision_score_across_entity_types():
    payload = raw_result(
        [
            [
                raw_entity("GLINER2_PERSON", "Santiago", 0, 8, score=0.99),
                raw_entity("GLINER2_CITY", "Santiago", 0, 8, score=0.70),
            ]
        ]
    )

    result = filter_raw_result(
        payload,
        zero_shot_scorer=zero_shot_scorer(
            {
                ("Santiago", "NAME"): 0.70,
                ("Santiago", "LOCATION"): 0.92,
            }
        ),
    )

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.entity_type == "LOCATION"
    assert accepted.confidence_level == "CONFIDENT"
    assert accepted.decision_score == 0.92
    assert len(accepted.evidence) == 2


def test_zero_shot_shorter_span_can_win_overlap_when_score_is_higher():
    payload = raw_result(
        [
            [
                raw_entity("GLINER2_FULL_NAME", "Juan Perez Soto", 0, 15),
                raw_entity("GLINER2_LAST_NAME", "Perez Soto", 5, 15),
            ]
        ]
    )

    result = filter_raw_result(
        payload,
        zero_shot_scorer=zero_shot_scorer(
            {
                ("Juan Perez Soto", "NAME"): 0.86,
                ("Perez Soto", "NAME"): 0.91,
            }
        ),
    )

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.entity_type == "NAME"
    assert accepted.text == "Perez Soto"
    assert len(accepted.evidence) == 2


def test_threshold_only_entities_need_model_score_above_point_nine():
    payload = raw_result(
        [
            [
                raw_entity("GLINER2_CARD_CVV", "123", 0, 3, score=0.91),
                raw_entity("GLINER2_CARD_EXPIRY", "12/29", 20, 25, score=0.90),
            ]
        ]
    )

    result = filter_raw_result(payload)

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.entity_type == "CARD_CVV"
    assert accepted.confidence_level == "PROBABLE"
    assert accepted.decision_method == "model_score_threshold"


def test_local_validation_entities_are_probable_when_valid():
    payload = raw_result(
        [
            [
                raw_entity("AGE", "45", 0, 2, source="presidio", score=0.40),
                raw_entity("DATE_TIME", "31/12/25", 10, 18, source="presidio", score=0.60),
                raw_entity("IP_ADDRESS", "192.168.0.1", 30, 41, source="presidio", score=0.91),
                raw_entity("URL", "not a url", 50, 59, source="presidio", score=0.99),
                raw_entity("MAC_ADDRESS", "aa:bb:cc:dd:ee:ff", 70, 87, source="presidio", score=0.90),
            ]
        ]
    )

    result = filter_raw_result(payload)

    accepted_by_type = {entity.entity_type: entity for entity in result.accepted_entities}
    assert set(accepted_by_type) == {"AGE", "DATE", "IP_ADDRESS"}
    assert accepted_by_type["AGE"].confidence_level == "PROBABLE"
    assert accepted_by_type["DATE"].normalized_value == "2025-12-31"
    assert accepted_by_type["IP_ADDRESS"].decision_method == "local_validation"


def test_sensitive_deny_list_categories_are_base_entities():
    payload = raw_result(
        [
            [
                raw_entity(
                    "GENDER_IDENTITY",
                    "femenino",
                    0,
                    8,
                    source="deny_list",
                    raw_entity_type="GENDER_IDENTITY_DENY_LIST",
                    score=0.98,
                ),
                raw_entity(
                    "RELIGION_OR_BELIEF",
                    "catolica",
                    20,
                    28,
                    source="deny_list",
                    raw_entity_type="RELIGION_OR_BELIEF_DENY_LIST",
                    score=0.98,
                ),
                raw_entity(
                    "MARITAL_STATUS",
                    "soltera",
                    40,
                    47,
                    source="deny_list",
                    raw_entity_type="MARITAL_STATUS_DENY_LIST",
                    score=0.98,
                ),
            ]
        ]
    )

    result = filter_raw_result(payload)

    accepted_by_type = {entity.entity_type: entity for entity in result.accepted_entities}
    assert accepted_by_type["GENDER"].is_base is True
    assert accepted_by_type["RELIGION_OR_BELIEF"].is_base is True
    assert accepted_by_type["MARITAL_STATUS"].is_base is True
    assert {entity.confidence_level for entity in result.accepted_entities} == {
        "VERY_CONFIDENT"
    }


def test_sensitive_base_entity_wins_overlap_and_keeps_evidence():
    payload = raw_result(
        [
            [
                raw_entity(
                    "GENDER_IDENTITY",
                    "femenino",
                    0,
                    8,
                    source="deny_list",
                    raw_entity_type="GENDER_IDENTITY_DENY_LIST",
                    score=0.98,
                ),
                raw_entity(
                    "GLINER2_PERSON",
                    "femenino",
                    0,
                    8,
                    source="gliner2",
                    score=0.99,
                ),
            ]
        ]
    )

    result = filter_raw_result(payload)

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.entity_type == "GENDER"
    assert accepted.is_base is True
    assert len(accepted.evidence) == 2


def test_contextual_gender_regex_is_base_entity():
    payload = raw_result(
        [
            [
                raw_entity(
                    "GENDER_IDENTITY",
                    "H",
                    6,
                    7,
                    source="regex",
                    raw_entity_type="GENDER_IDENTITY_CONTEXT_REGEX",
                    score=0.96,
                )
            ]
        ]
    )

    result = filter_raw_result(payload)

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.entity_type == "GENDER"
    assert accepted.is_base is True
    assert accepted.normalized_value == "masculino"


def test_gender_deny_list_wins_overlapping_contextual_regex():
    payload = raw_result(
        [
            [
                raw_entity(
                    "GENDER_IDENTITY",
                    "hombre",
                    6,
                    12,
                    source="regex",
                    raw_entity_type="GENDER_IDENTITY_CONTEXT_REGEX",
                    score=0.96,
                ),
                raw_entity(
                    "GENDER_IDENTITY",
                    "hombre",
                    6,
                    12,
                    source="deny_list",
                    raw_entity_type="GENDER_IDENTITY_DENY_LIST",
                    score=0.98,
                ),
            ]
        ]
    )

    result = filter_raw_result(payload)

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.source == "deny_list"
    assert accepted.raw_entity_type == "GENDER_IDENTITY_DENY_LIST"
    assert accepted.normalized_value == "masculino"
    assert len(accepted.evidence) == 2


def test_contextual_gender_regex_dedupes_with_deny_list_value():
    payload = raw_result(
        [
            [
                raw_entity(
                    "GENDER_IDENTITY",
                    "hombre",
                    0,
                    6,
                    source="deny_list",
                    raw_entity_type="GENDER_IDENTITY_DENY_LIST",
                    score=0.98,
                )
            ],
            [
                raw_entity(
                    "GENDER_IDENTITY",
                    "H",
                    20,
                    21,
                    source="regex",
                    raw_entity_type="GENDER_IDENTITY_CONTEXT_REGEX",
                    score=0.96,
                )
            ],
        ]
    )

    result = filter_raw_result(payload)

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.source == "deny_list"
    assert accepted.normalized_value == "masculino"
    assert len(accepted.evidence) == 2


def test_dedupe_does_not_merge_different_entity_types_with_same_text():
    payload = raw_result(
        [
            [raw_entity("GLINER2_PERSON", "Santiago", 0, 8)],
            [raw_entity("GLINER2_CITY", "Santiago", 20, 28)],
        ]
    )

    result = filter_raw_result(
        payload,
        zero_shot_scorer=zero_shot_scorer(
            {
                ("Santiago", "NAME"): 0.91,
                ("Santiago", "LOCATION"): 0.92,
            }
        ),
    )

    assert [entity.entity_type for entity in result.accepted_entities] == [
        "NAME",
        "LOCATION",
    ]


def test_same_base_value_dedupes_by_normalized_value():
    payload = raw_result(
        [
            [
                raw_entity(
                    "RELIGION_OR_BELIEF",
                    "Catolica",
                    0,
                    8,
                    source="deny_list",
                    raw_entity_type="RELIGION_OR_BELIEF_DENY_LIST",
                    score=0.98,
                )
            ],
            [
                raw_entity(
                    "RELIGION_OR_BELIEF",
                    "catolica",
                    20,
                    28,
                    source="deny_list",
                    raw_entity_type="RELIGION_OR_BELIEF_DENY_LIST",
                    score=0.98,
                )
            ],
        ]
    )

    result = filter_raw_result(payload)

    assert len(result.accepted_entities) == 1
    accepted = result.accepted_entities[0]
    assert accepted.entity_type == "RELIGION_OR_BELIEF"
    assert accepted.normalized_value == "catolica"
    assert len(accepted.evidence) == 2


def test_file_without_entities_generates_valid_empty_result():
    result = filter_raw_result(raw_result([[]]))

    payload = result.to_dict()

    assert payload["raw_entity_count"] == 0
    assert payload["accepted_entity_count"] == 0
    assert payload["accepted_entities"] == []
