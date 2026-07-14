from __future__ import annotations

import re
import unicodedata
from typing import Any

from config import (
    DENY_LISTS,
    GLINER_LABEL_MAPPING,
    GLINER_LABELS,
    REGEX_PATTERNS,
    EntityDetectionConfig,
)
from models import RawEntity


class RawEntityDetector:
    def __init__(
        self,
        config: EntityDetectionConfig | None = None,
        *,
        analyzer: Any | None = None,
        gliner_model: Any | None = None,
        medical_pipeline: Any | None = None,
    ) -> None:
        self.config = config or EntityDetectionConfig.from_env()
        self._analyzer = analyzer
        self._gliner_model = gliner_model
        self._medical_pipeline = medical_pipeline

    @classmethod
    def from_env(cls) -> "RawEntityDetector":
        return cls(EntityDetectionConfig.from_env())

    def detect(self, text: str) -> list[RawEntity]:
        return self.detect_many([text])[0]

    def detect_many(self, texts: list[str]) -> list[list[RawEntity]]:
        output: list[list[RawEntity]] = [[] for _ in texts]
        for index, text in enumerate(texts):
            if self.config.enable_presidio:
                output[index].extend(self._detect_presidio(text))
            if self.config.enable_deterministic:
                output[index].extend(self._detect_regex(text))
                output[index].extend(self._detect_deny_lists(text))
        if self.config.enable_gliner2:
            for index, entities in enumerate(self._detect_gliner2_many(texts)):
                output[index].extend(entities)
        if self.config.enable_medical:
            for index, entities in enumerate(self._detect_medical_many(texts)):
                output[index].extend(entities)
        return output

    @property
    def analyzer(self) -> Any:
        if self._analyzer is None:
            self._analyzer = self._create_analyzer()
        return self._analyzer

    @property
    def gliner_model(self) -> Any:
        if self._gliner_model is None:
            self._gliner_model = self._create_gliner_model()
        return self._gliner_model

    @property
    def medical_pipeline(self) -> Any:
        if self._medical_pipeline is None:
            self._medical_pipeline = self._create_medical_pipeline()
        return self._medical_pipeline

    def _create_analyzer(self) -> Any:
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
        except ImportError as exc:
            raise RuntimeError(
                "Presidio support requires presidio-analyzer and spacy. "
                "Install them in the PII_entity environment."
            ) from exc

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [
                    {
                        "lang_code": self.config.language,
                        "model_name": self.config.spacy_model,
                    }
                ],
            }
        )
        return AnalyzerEngine(
            nlp_engine=provider.create_engine(),
            supported_languages=[self.config.language],
        )

    def _create_gliner_model(self) -> Any:
        try:
            import torch
            from gliner2 import GLiNER2
        except ImportError as exc:
            raise RuntimeError(
                "GLiNER2 support requires torch, transformers and gliner2. "
                "Install them in the PII_entity environment."
            ) from exc

        model = GLiNER2.from_pretrained(self.config.gliner2_model)
        device = _model_device(
            self.config.model_device,
            torch,
            legacy_use_gpu=self.config.gliner2_use_gpu,
            legacy_auto=False,
        )
        model = model.to(device)
        model.eval()
        return model

    def _create_medical_pipeline(self) -> Any:
        try:
            import torch
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError(
                "Medical NER support requires torch and transformers. "
                "Install them in the PII_entity environment."
            ) from exc

        return pipeline(
            task="token-classification",
            model=self.config.medical_model,
            tokenizer=self.config.medical_model,
            aggregation_strategy="simple",
            device=_pipeline_device(
                self.config.model_device,
                torch,
                legacy_auto=True,
            ),
        )

    def _detect_presidio(self, text: str) -> list[RawEntity]:
        results = self.analyzer.analyze(
            text=text,
            language=self.config.language,
            score_threshold=0.0,
        )
        return [
            RawEntity(
                entity_type=str(result.entity_type),
                raw_entity_type=str(result.entity_type),
                source="presidio",
                text=text[int(result.start) : int(result.end)],
                start=int(result.start),
                end=int(result.end),
                score=float(result.score),
                normalized_value=text[int(result.start) : int(result.end)],
            )
            for result in results
        ]

    def _detect_regex(self, text: str) -> list[RawEntity]:
        output: list[RawEntity] = []
        for spec in REGEX_PATTERNS:
            for match in re.finditer(spec.regex, text):
                start, end = match.span(spec.group)
                value = text[start:end]
                output.append(
                    RawEntity(
                        entity_type=spec.entity_type,
                        raw_entity_type=spec.raw_entity_type,
                        source="regex",
                        text=value,
                        start=start,
                        end=end,
                        score=spec.score,
                        normalized_value=_normalize_value(spec.entity_type, value),
                    )
                )
        return output

    def _detect_deny_lists(self, text: str) -> list[RawEntity]:
        output: list[RawEntity] = []
        normalized_text = _strip_accents(text).casefold()
        for entity_type, terms in DENY_LISTS.items():
            seen_spans: set[tuple[int, int, str]] = set()
            for term in sorted(terms, key=len, reverse=True):
                normalized_term = _strip_accents(_normalize_spaces(term)).casefold()
                if not normalized_term:
                    continue
                pattern = _term_pattern(normalized_term)
                for match in re.finditer(pattern, normalized_text):
                    start, end = match.span()
                    key = (start, end, entity_type)
                    if key in seen_spans:
                        continue
                    seen_spans.add(key)
                    value = text[start:end]
                    output.append(
                        RawEntity(
                            entity_type=entity_type,
                            raw_entity_type=f"{entity_type}_DENY_LIST",
                            source="deny_list",
                            text=value,
                            start=start,
                            end=end,
                            score=0.98,
                            normalized_value=_normalize_key(value),
                        )
                    )
        return output

    def _detect_gliner2(self, text: str) -> list[RawEntity]:
        result = self.gliner_model.extract_entities(
            text,
            list(GLINER_LABELS),
            threshold=0.0,
            include_confidence=True,
            include_spans=True,
        )
        return _raw_entities_from_gliner_result(result, text)

    def _detect_gliner2_many(self, texts: list[str]) -> list[list[RawEntity]]:
        if not texts:
            return []
        batch_size = self.config.model_batch_size
        output: list[list[RawEntity]] = []
        for batch in _batched(texts, batch_size):
            if len(batch) == 1:
                output.append(self._detect_gliner2(batch[0]))
                continue
            try:
                result = self.gliner_model.extract_entities(
                    batch,
                    list(GLINER_LABELS),
                    threshold=0.0,
                    include_confidence=True,
                    include_spans=True,
                )
            except (TypeError, ValueError, AttributeError):
                output.extend(self._detect_gliner2(text) for text in batch)
                continue
            if not isinstance(result, list) or len(result) != len(batch):
                output.extend(self._detect_gliner2(text) for text in batch)
                continue
            output.extend(
                _raw_entities_from_gliner_result(item, text)
                for item, text in zip(result, batch)
            )
        return output

    def _detect_medical(self, text: str) -> list[RawEntity]:
        return _raw_entities_from_medical_items(self.medical_pipeline(text), text)

    def _detect_medical_many(self, texts: list[str]) -> list[list[RawEntity]]:
        if not texts:
            return []
        batch_size = self.config.model_batch_size
        output: list[list[RawEntity]] = []
        for batch in _batched(texts, batch_size):
            if len(batch) == 1:
                output.append(self._detect_medical(batch[0]))
                continue
            try:
                result = self.medical_pipeline(batch, batch_size=batch_size)
            except (TypeError, ValueError):
                output.extend(self._detect_medical(text) for text in batch)
                continue
            if not isinstance(result, list) or len(result) != len(batch):
                output.extend(self._detect_medical(text) for text in batch)
                continue
            output.extend(
                _raw_entities_from_medical_items(items, text)
                for items, text in zip(result, batch)
            )
        return output


def _raw_entities_from_gliner_result(result: Any, text: str) -> list[RawEntity]:
    if not isinstance(result, dict):
        return []
    raw_entities = result.get("entities", {})
    if not isinstance(raw_entities, dict):
        return []
    output: list[RawEntity] = []
    for label, values in raw_entities.items():
        raw_entity_type = GLINER_LABEL_MAPPING.get(str(label), str(label))
        if not isinstance(values, list):
            continue
        for value in values:
            parsed = _parse_model_value(value, text, default_score=0.0)
            if parsed is None:
                continue
            start, end, score = parsed
            output.append(
                RawEntity(
                    entity_type=raw_entity_type,
                    raw_entity_type=raw_entity_type,
                    source="gliner2",
                    text=text[start:end],
                    start=start,
                    end=end,
                    score=score,
                    normalized_value=text[start:end],
                )
            )
    return output


def _raw_entities_from_medical_items(items: Any, text: str) -> list[RawEntity]:
    label_mapping = {
        "PROBLEM": "MEDICAL_PROBLEM",
        "TEST": "MEDICAL_TEST",
        "TREATMENT": "MEDICAL_TREATMENT",
    }
    if not isinstance(items, list):
        return []
    output: list[RawEntity] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_label = item.get("entity_group")
        raw_entity_type = label_mapping.get(str(raw_label))
        if raw_entity_type is None:
            continue
        start = int(item["start"])
        end = int(item["end"])
        output.append(
            RawEntity(
                entity_type=raw_entity_type,
                raw_entity_type=raw_entity_type,
                source="medical_model",
                text=text[start:end],
                start=start,
                end=end,
                score=float(item["score"]),
                normalized_value=text[start:end],
            )
        )
    return output


def _batched(items: list[str], batch_size: int) -> list[list[str]]:
    batch_size = max(1, batch_size)
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _parse_model_value(
    value: str | dict[str, Any],
    text: str,
    default_score: float,
) -> tuple[int, int, float] | None:
    if isinstance(value, str):
        start = text.find(value)
        if start == -1:
            return None
        return start, start + len(value), default_score

    if not isinstance(value, dict):
        return None

    entity_text = (
        value.get("text")
        or value.get("span")
        or value.get("value")
        or value.get("entity")
    )
    score = float(
        value.get("score")
        or value.get("confidence")
        or value.get("probability")
        or default_score
    )
    start = value.get("start")
    end = value.get("end")
    if start is not None and end is not None:
        return int(start), int(end), score
    if entity_text:
        found = text.find(str(entity_text))
        if found == -1:
            return None
        return found, found + len(str(entity_text)), score
    return None


def _normalize_value(entity_type: str, value: str) -> str:
    if entity_type in {"RUT", "LICENSE_PLATE", "PAYMENT_CARD", "DOCUMENT_NUMBER"}:
        return re.sub(r"[^0-9KkA-Za-z]", "", value).upper()
    if entity_type == "PHONE_CL":
        digits = re.sub(r"\D", "", value)
        if digits.startswith("0056"):
            digits = "56" + digits[4:]
        if digits.startswith("56"):
            return f"+{digits}"
        return f"+56{digits}" if len(digits) == 9 else digits
    if entity_type == "EMAIL":
        return value.strip().lower()
    if entity_type == "GENDER_IDENTITY":
        return _normalize_gender_identity(value)
    return _normalize_spaces(value)


def _normalize_gender_identity(value: str) -> str:
    normalized = _strip_accents(_normalize_spaces(value)).casefold()
    if normalized in {"m", "h", "hombre", "masculino"}:
        return "masculino"
    if normalized in {"f", "mujer", "femenino"}:
        return "femenino"
    return normalized


def _term_pattern(normalized_term: str) -> str:
    escaped = r"\s+".join(re.escape(part) for part in normalized_term.split())
    return rf"(?<![0-9A-Za-z_]){escaped}(?![0-9A-Za-z_])"


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_key(text: str) -> str:
    return _normalize_spaces(_strip_accents(text)).casefold()


def _model_device(
    requested_device: str | None,
    torch_module: Any,
    *,
    legacy_use_gpu: bool,
    legacy_auto: bool,
) -> str:
    pipeline_device = _pipeline_device(
        requested_device,
        torch_module,
        legacy_auto=legacy_auto,
        legacy_use_gpu=legacy_use_gpu,
    )
    if pipeline_device == -1:
        return "cpu"
    if isinstance(pipeline_device, int):
        return "cuda"
    return str(pipeline_device)


def _pipeline_device(
    requested_device: str | None,
    torch_module: Any,
    *,
    legacy_auto: bool,
    legacy_use_gpu: bool = False,
) -> object:
    if requested_device:
        normalized = requested_device.strip().casefold()
        if normalized == "auto":
            return _auto_pipeline_device(torch_module)
        if normalized == "cpu":
            return -1
        if normalized == "cuda":
            return 0
        if normalized == "mps":
            return torch_module.device("mps")
        if normalized.isdigit():
            return int(normalized)
        raise RuntimeError(f"Unsupported entity model device: {requested_device!r}")

    if legacy_use_gpu:
        cuda = getattr(torch_module, "cuda", None)
        if cuda is not None and cuda.is_available():
            return 0
        return -1
    if legacy_auto:
        return _auto_pipeline_device(torch_module)
    return -1


def _auto_pipeline_device(torch_module: Any) -> object:
    cuda = getattr(torch_module, "cuda", None)
    if cuda is not None and cuda.is_available():
        return 0
    backends = getattr(torch_module, "backends", None)
    mps = getattr(backends, "mps", None) if backends is not None else None
    if mps is not None and mps.is_available():
        return torch_module.device("mps")
    return -1
