from __future__ import annotations

import os
from pathlib import Path


DEFAULT_RESULTS_DIR = Path(
    os.getenv("PII_ENTITY_OUTPUT_DIR", "/tmp/pii-entity-results")
).expanduser()

SCHEMA_VERSION = "1.0"
PIPELINE_STAGE = "entity_text_filter"

VERY_CONFIDENT = "VERY_CONFIDENT"
CONFIDENT = "CONFIDENT"
PROBABLE = "PROBABLE"
CONFIDENCE_LEVELS = frozenset({VERY_CONFIDENT, CONFIDENT, PROBABLE})

ZERO_SHOT_MODEL_NAME = os.getenv(
    "PII_ENTITY_ZERO_SHOT_MODEL",
    "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
)
ZERO_SHOT_ENABLED = (
    os.getenv("PII_ENTITY_ENABLE_ZERO_SHOT", "true").strip().casefold()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)
ZERO_SHOT_DEVICE = os.getenv("PII_ENTITY_ZERO_SHOT_DEVICE", "auto")
ZERO_SHOT_BATCH_SIZE = int(os.getenv("PII_ENTITY_ZERO_SHOT_BATCH_SIZE", "8"))
ZERO_SHOT_OVERLAP_TOP_K = max(
    1,
    int(os.getenv("PII_ENTITY_ZERO_SHOT_OVERLAP_TOP_K", "5")),
)

ZERO_SHOT_MIN_MODEL_SCORE_THRESHOLD = 0.50
ZERO_SHOT_CONFIDENT_THRESHOLD = 0.85
ZERO_SHOT_PROBABLE_THRESHOLD = 0.50
MODEL_SCORE_PROBABLE_THRESHOLD = 0.90

SOURCE_PRIORITY = {
    "presidio": 1,
    "regex": 2,
    "deny_list": 2,
    "gliner2": 3,
    "medical_model": 4,
    "humadex": 4,
}

UNKNOWN_SOURCE_PRIORITY = 9
