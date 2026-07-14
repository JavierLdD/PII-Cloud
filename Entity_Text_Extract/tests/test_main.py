from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as entity_main
from models import RawEntity, SourceFile, TextChunk


class FakeRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.records = []

    def __enter__(self) -> "FakeRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def get_file(self, file_id: str) -> SourceFile | None:
        return SourceFile(
            file_id=file_id,
            run_id="run-1",
            source_type="local",
            source_uri="local:///tmp/sample.pdf",
            external_id="/tmp/sample.pdf",
            file_name="sample.pdf",
            relative_path="sample.pdf",
            extension=".pdf",
            mime_type="application/pdf",
            size_bytes=10,
            checksum_sha256="a" * 64,
            content_hash=None,
            etag=None,
            text_extraction_status="text_extraction_completed",
            expected_chunk_count=1,
        )

    def list_ready_chunks(self, file_id: str) -> list[TextChunk]:
        return [
            TextChunk(
                chunk_id=f"{file_id}:c000001",
                run_id="run-1",
                file_id=file_id,
                chunk_index=1,
                page_start=1,
                page_end=1,
                text="persona@example.com",
                text_hash_sha256="b" * 64,
                source_map={"segments": []},
                method="pymupdf",
            )
        ]

    def save_entity_extraction_record(self, record) -> None:
        self.records.append(record)

    def release_materialization_lease(self, file_id: str) -> list[str]:
        return []


class FakeDetector:
    def detect(self, text: str) -> list[RawEntity]:
        return [
            RawEntity(
                entity_type="EMAIL",
                raw_entity_type="EMAIL_REGEX",
                source="regex",
                text=text,
                start=0,
                end=len(text),
                score=0.99,
                normalized_value=text,
            )
        ]


def test_main_file_id_writes_json_with_injected_dependencies(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    status = entity_main.main(
        [
            "--file-id",
            "file-1",
            "--output-dir",
            str(tmp_path),
            "--mask-text",
        ],
        repository_factory=FakeRepository,
        detector_factory=lambda: FakeDetector(),
    )

    assert status == 0
    output = capsys.readouterr().out
    assert "processed_entities" in output
    assert "raw=1" in output
    assert "accepted=1" in output
    assert "raw_output=" in output
    assert "filtered_output=" in output
    assert not (tmp_path / "sample.pdf.entities.json").exists()
    raw_payload = json.loads((tmp_path / "sample.pdf.json").read_text())
    payload = json.loads((tmp_path / "sample.pdf_filtrado.json").read_text())
    assert raw_payload["chunks"][0]["entities"][0]["text"] == "persona@example.com"
    assert payload["accepted_entities"][0]["text"] == "persona@example.com"
    assert payload["entity_processing_seconds"] is not None
    assert payload["cpu_total_seconds"] is not None
    assert payload["peak_memory_mb"] is not None
    assert raw_payload["cpu_total_seconds"] is not None


def test_main_gpu_flag_sets_entity_model_device(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.delenv("PII_ENTITY_MODEL_DEVICE", raising=False)
    monkeypatch.delenv("PII_ENTITY_ZERO_SHOT_DEVICE", raising=False)

    status = entity_main.main(
        [
            "--file-id",
            "file-1",
            "--output-dir",
            str(tmp_path),
            "--gpu",
        ],
        repository_factory=FakeRepository,
        detector_factory=lambda: FakeDetector(),
    )

    assert status == 0
    assert os.environ["PII_ENTITY_MODEL_DEVICE"] == "auto"
    assert os.environ["PII_ENTITY_ZERO_SHOT_DEVICE"] == "auto"
    assert os.environ["PII_ENTITY_GLINER2_USE_GPU"] == "true"


def test_main_device_cpu_sets_cpu_for_entity_models(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    status = entity_main.main(
        [
            "--file-id",
            "file-1",
            "--output-dir",
            str(tmp_path),
            "--device",
            "cpu",
        ],
        repository_factory=FakeRepository,
        detector_factory=lambda: FakeDetector(),
    )

    assert status == 0
    assert os.environ["PII_ENTITY_MODEL_DEVICE"] == "cpu"
    assert os.environ["PII_ENTITY_ZERO_SHOT_DEVICE"] == "cpu"
    assert os.environ["PII_ENTITY_GLINER2_USE_GPU"] == "false"
