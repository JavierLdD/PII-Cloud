from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Callable

try:
    from .config import ZERO_SHOT_BATCH_SIZE, ZERO_SHOT_DEVICE, ZERO_SHOT_MODEL_NAME
except ImportError:  # pragma: no cover - script execution fallback
    from config import ZERO_SHOT_BATCH_SIZE, ZERO_SHOT_DEVICE, ZERO_SHOT_MODEL_NAME


ZeroShotScorer = Callable[[list[tuple[str, str]]], list[float]]


class ZeroShotModelError(RuntimeError):
    pass


class LocalZeroShotScorer:
    def __init__(self, model_name: str, device: str, batch_size: int) -> None:
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._classifier = _load_zero_shot_classifier(model_name, device)

    def __call__(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []

        scores_by_index: dict[int, float] = {}
        by_label: dict[str, list[tuple[int, str]]] = {}
        for idx, (text, label) in enumerate(pairs):
            by_label.setdefault(label, []).append((idx, text))

        for label, indexed_texts in by_label.items():
            texts = [text for _, text in indexed_texts]
            raw_outputs = self._classifier(
                texts,
                candidate_labels=[label],
                hypothesis_template="{}",
                multi_label=True,
                batch_size=self.batch_size,
                truncation=True,
            )
            outputs = [raw_outputs] if isinstance(raw_outputs, dict) else list(raw_outputs)
            for (idx, _), output in zip(indexed_texts, outputs):
                scores_by_index[idx] = float(output["scores"][0])

        return [scores_by_index[idx] for idx in range(len(pairs))]


def get_default_zero_shot_scorer() -> ZeroShotScorer:
    model_name, device, batch_size = _current_zero_shot_settings()
    return _get_zero_shot_scorer(model_name, device, batch_size)


@lru_cache(maxsize=4)
def _get_zero_shot_scorer(
    model_name: str,
    device: str,
    batch_size: int,
) -> ZeroShotScorer:
    return LocalZeroShotScorer(
        model_name=model_name,
        device=device,
        batch_size=batch_size,
    )


def _current_zero_shot_settings() -> tuple[str, str, int]:
    return (
        os.getenv("PII_ENTITY_ZERO_SHOT_MODEL", ZERO_SHOT_MODEL_NAME),
        os.getenv("PII_ENTITY_ZERO_SHOT_DEVICE", ZERO_SHOT_DEVICE),
        int(os.getenv("PII_ENTITY_ZERO_SHOT_BATCH_SIZE", str(ZERO_SHOT_BATCH_SIZE))),
    )


@lru_cache(maxsize=4)
def _load_zero_shot_classifier(model_name: str, device: str) -> object:
    if _looks_like_local_path(model_name) and not Path(model_name).exists():
        raise ZeroShotModelError(
            "Zero-shot model is not available locally. "
            f"model={model_name!r} device={device!r}"
        )
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline
    except Exception as exc:  # pragma: no cover - exercised through tests by monkeypatch.
        raise ZeroShotModelError(
            "Zero-shot dependencies are not available in the PII_entity environment."
        ) from exc

    try:
        pipeline_device = _pipeline_device(device, torch)
        tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(
            model_name,
            local_files_only=True,
        )
        return pipeline(
            "zero-shot-classification",
            model=model,
            tokenizer=tokenizer,
            device=pipeline_device,
        )
    except ZeroShotModelError:
        raise
    except Exception as exc:
        raise ZeroShotModelError(
            "Zero-shot model is not available locally. "
            f"model={model_name!r} device={device!r}"
        ) from exc


def _pipeline_device(device: str, torch_module: object) -> object:
    normalized = device.strip().lower()
    if normalized == "auto":
        cuda = getattr(torch_module, "cuda", None)
        if cuda is not None and cuda.is_available():
            return 0
        backends = getattr(torch_module, "backends", None)
        mps = getattr(backends, "mps", None) if backends is not None else None
        if mps is not None and mps.is_available():
            return torch_module.device("mps")
        return -1
    if normalized == "cpu":
        return -1
    if normalized == "cuda":
        return 0
    if normalized == "mps":
        return torch_module.device("mps")
    if normalized.isdigit():
        return int(normalized)
    raise ZeroShotModelError(f"Unsupported zero-shot device: {device!r}")


def _looks_like_local_path(value: str) -> bool:
    return value.startswith(("/", "./", "../", "~"))
