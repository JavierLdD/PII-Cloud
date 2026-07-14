from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from Entity_Text_Filter.zero_shot import (
    ZeroShotModelError,
    _current_zero_shot_settings,
    _load_zero_shot_classifier,
)


def test_missing_zero_shot_model_fails_clearly(tmp_path: Path):
    _load_zero_shot_classifier.cache_clear()

    with pytest.raises(ZeroShotModelError, match="not available locally"):
        _load_zero_shot_classifier(str(tmp_path / "missing-model"), "cpu")


def test_zero_shot_settings_are_read_from_current_environment(monkeypatch):
    monkeypatch.setenv("PII_ENTITY_ZERO_SHOT_MODEL", "local/xnli")
    monkeypatch.setenv("PII_ENTITY_ZERO_SHOT_DEVICE", "cuda")
    monkeypatch.setenv("PII_ENTITY_ZERO_SHOT_BATCH_SIZE", "16")

    assert _current_zero_shot_settings() == ("local/xnli", "cuda", 16)
