from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import EntityDetectionConfig
from detector import RawEntityDetector, _model_device, _pipeline_device


class FakePresidioResult:
    def __init__(self, entity_type: str, start: int, end: int, score: float):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


class FakeAnalyzer:
    def analyze(self, text: str, language: str, score_threshold: float):
        return [FakePresidioResult("PERSON", 0, 6, 0.85)]


class FakeGLiNER:
    def extract_entities(
        self,
        text: str,
        labels: list[str],
        threshold: float,
        include_confidence: bool,
        include_spans: bool,
    ):
        return {
            "entities": {
                "email": [
                    {"text": "persona@example.com", "start": 24, "end": 43, "score": 0.77}
                ]
            }
        }


class FakeBatchGLiNER:
    def __init__(self):
        self.calls = []

    def extract_entities(
        self,
        text: str | list[str],
        labels: list[str],
        threshold: float,
        include_confidence: bool,
        include_spans: bool,
    ):
        if isinstance(text, list):
            self.calls.append(list(text))
            return [
                {
                    "entities": {
                        "email": [
                            {
                                "text": item,
                                "start": 0,
                                "end": len(item),
                                "score": 0.81,
                            }
                        ]
                    }
                }
                for item in text
            ]
        return {"entities": {}}


class FakeBatchMedicalPipeline:
    def __init__(self):
        self.calls = []

    def __call__(self, text: str | list[str], batch_size: int | None = None):
        if isinstance(text, list):
            self.calls.append((list(text), batch_size))
            return [
                [
                    {
                        "entity_group": "PROBLEM",
                        "start": 0,
                        "end": len(item),
                        "score": 0.74,
                    }
                ]
                for item in text
            ]
        return []


def test_detector_combines_sources_without_resolving_overlaps():
    text = "Javier tiene RUT 12.378.895-8 y correo persona@example.com en Fonasa."
    detector = RawEntityDetector(
        EntityDetectionConfig(enable_medical=False),
        analyzer=FakeAnalyzer(),
        gliner_model=FakeGLiNER(),
    )

    entities = detector.detect(text)
    shapes = [(entity.source, entity.raw_entity_type, entity.text) for entity in entities]

    assert ("presidio", "PERSON", "Javier") in shapes
    assert any(shape[0] == "regex" and shape[1] == "RUT_REGEX" for shape in shapes)
    assert any(shape[0] == "deny_list" and shape[2] == "Fonasa" for shape in shapes)
    assert any(shape[0] == "gliner2" and shape[1] == "GLINER2_EMAIL" for shape in shapes)


def test_detector_batches_gliner2_results_per_text():
    model = FakeBatchGLiNER()
    detector = RawEntityDetector(
        EntityDetectionConfig(
            enable_presidio=False,
            enable_deterministic=False,
            enable_medical=False,
            model_batch_size=8,
        ),
        gliner_model=model,
    )

    result = detector.detect_many(["uno@example.com", "dos@example.com"])

    assert model.calls == [["uno@example.com", "dos@example.com"]]
    assert [[entity.text for entity in entities] for entities in result] == [
        ["uno@example.com"],
        ["dos@example.com"],
    ]


def test_detector_batches_medical_results_per_text():
    pipeline = FakeBatchMedicalPipeline()
    detector = RawEntityDetector(
        EntityDetectionConfig(
            enable_presidio=False,
            enable_deterministic=False,
            enable_gliner2=False,
            enable_medical=True,
            model_batch_size=8,
        ),
        medical_pipeline=pipeline,
    )

    result = detector.detect_many(["asma", "diabetes"])

    assert pipeline.calls == [(["asma", "diabetes"], 8)]
    assert [[entity.raw_entity_type for entity in entities] for entities in result] == [
        ["MEDICAL_PROBLEM"],
        ["MEDICAL_PROBLEM"],
    ]


def test_detector_finds_sensitive_deny_list_entities():
    text = (
        "Paciente femenino, estado civil soltera, religion catolica, "
        "Fonasa y AFP Modelo."
    )
    detector = RawEntityDetector(
        EntityDetectionConfig(
            enable_presidio=False,
            enable_gliner2=False,
            enable_medical=False,
        )
    )

    entities = detector.detect(text)
    shapes = {(entity.entity_type, entity.raw_entity_type, entity.text) for entity in entities}

    assert ("HEALTH_SYSTEM", "HEALTH_SYSTEM_DENY_LIST", "Fonasa") in shapes
    assert ("PENSION_SYSTEM", "PENSION_SYSTEM_DENY_LIST", "AFP Modelo") in shapes
    assert ("RELIGION_OR_BELIEF", "RELIGION_OR_BELIEF_DENY_LIST", "catolica") in shapes
    assert ("MARITAL_STATUS", "MARITAL_STATUS_DENY_LIST", "soltera") in shapes
    assert ("GENDER_IDENTITY", "GENDER_IDENTITY_DENY_LIST", "femenino") in shapes


def test_detector_finds_contextual_gender_regex():
    text = "Genero: M. Sexo: H. Sexo: F. Identidad de género: mujer."
    detector = RawEntityDetector(
        EntityDetectionConfig(
            enable_presidio=False,
            enable_gliner2=False,
            enable_medical=False,
        )
    )

    entities = detector.detect(text)
    regex_shapes = {
        (entity.text, entity.normalized_value)
        for entity in entities
        if entity.raw_entity_type == "GENDER_IDENTITY_CONTEXT_REGEX"
    }
    deny_list_shapes = {
        (entity.text, entity.normalized_value)
        for entity in entities
        if entity.raw_entity_type == "GENDER_IDENTITY_DENY_LIST"
    }

    assert ("M", "masculino") in regex_shapes
    assert ("H", "masculino") in regex_shapes
    assert ("F", "femenino") in regex_shapes
    assert ("mujer", "femenino") in regex_shapes
    assert ("mujer", "mujer") in deny_list_shapes


def test_detector_does_not_find_isolated_gender_letters():
    detector = RawEntityDetector(
        EntityDetectionConfig(
            enable_presidio=False,
            enable_gliner2=False,
            enable_medical=False,
        )
    )

    entities = detector.detect("M H F")

    assert not any(
        entity.raw_entity_type == "GENDER_IDENTITY_CONTEXT_REGEX"
        for entity in entities
    )


class FakeCuda:
    def __init__(self, available: bool):
        self._available = available

    def is_available(self) -> bool:
        return self._available


class FakeMps:
    def __init__(self, available: bool):
        self._available = available

    def is_available(self) -> bool:
        return self._available


class FakeTorch:
    def __init__(self, *, cuda: bool = False, mps: bool = False):
        self.cuda = FakeCuda(cuda)
        self.backends = SimpleNamespace(mps=FakeMps(mps))

    def device(self, name: str) -> str:
        return name


def test_pipeline_device_auto_prefers_cuda_then_mps_then_cpu():
    assert _pipeline_device("auto", FakeTorch(cuda=True), legacy_auto=False) == 0
    assert _pipeline_device("auto", FakeTorch(mps=True), legacy_auto=False) == "mps"
    assert _pipeline_device("auto", FakeTorch(), legacy_auto=False) == -1


def test_model_device_respects_legacy_gpu_flag():
    assert (
        _model_device(
            None,
            FakeTorch(cuda=True),
            legacy_use_gpu=True,
            legacy_auto=False,
        )
        == "cuda"
    )
    assert (
        _model_device(
            None,
            FakeTorch(cuda=False),
            legacy_use_gpu=True,
            legacy_auto=False,
        )
        == "cpu"
    )
