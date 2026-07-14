from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models import RawEntity, SourceFile, TextChunk
from worker import build_file_entity_result, build_trace, process_file_id


class FakeRepository:
    def __init__(self, source_file: SourceFile, chunks: list[TextChunk]):
        self.source_file = source_file
        self.chunks = chunks
        self.records = []
        self.accepted_entities = []
        self.released_paths = []

    def get_file(self, file_id: str) -> SourceFile | None:
        if file_id == self.source_file.file_id:
            return self.source_file
        return None

    def list_ready_chunks(self, file_id: str) -> list[TextChunk]:
        return list(self.chunks)

    def save_entity_extraction_record(self, record) -> None:
        self.records.append(record)

    def save_accepted_entities(
        self,
        *,
        file_id: str,
        run_id: str,
        accepted_entities: list[object],
    ) -> None:
        self.accepted_entities.append(
            {
                "file_id": file_id,
                "run_id": run_id,
                "accepted_entities": list(accepted_entities),
            }
        )

    def release_materialization_lease(self, file_id: str) -> list[str]:
        return list(self.released_paths)


class FakeDetector:
    def __init__(self, entities: list[RawEntity]):
        self.entities = entities

    def detect(self, text: str) -> list[RawEntity]:
        return list(self.entities)


class FakeBatchDetector:
    def __init__(self):
        self.calls = []

    def detect(self, text: str) -> list[RawEntity]:
        raise AssertionError("detect should not be called when detect_many is available")

    def detect_many(self, texts: list[str]) -> list[list[RawEntity]]:
        self.calls.append(list(texts))
        return [
            [
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
            for text in texts
        ]


def source_file(relative_path: str = "subdir/documento.pdf") -> SourceFile:
    return SourceFile(
        file_id="file-1",
        run_id="run-1",
        source_type="local",
        source_uri="local:///tmp/documento.pdf",
        external_id="/tmp/documento.pdf",
        file_name="documento.pdf",
        relative_path=relative_path,
        extension=".pdf",
        mime_type="application/pdf",
        size_bytes=123,
        checksum_sha256="a" * 64,
        content_hash=None,
        etag=None,
        text_extraction_status="text_extraction_completed",
        expected_chunk_count=1,
    )


def chunk() -> TextChunk:
    return TextChunk(
        chunk_id="file-1:c000001",
        run_id="run-1",
        file_id="file-1",
        chunk_index=1,
        page_start=1,
        page_end=1,
        text="Nombre: Javier\nRUN 12.378.895-8",
        text_hash_sha256="b" * 64,
        source_map={
            "segments": [
                {
                    "source_block_id": "p1-b1",
                    "page_number": 1,
                    "page_index": 0,
                    "block_index": 1,
                    "block_type": "text",
                    "bbox": [1, 2, 3, 4],
                    "method": "pymupdf",
                    "routing_reason": "embedded_text",
                    "source_text_start": 0,
                    "source_text_end": 14,
                    "chunk_text_start": 0,
                    "chunk_text_end": 14,
                    "is_overlap": False,
                },
                {
                    "source_block_id": "p1-b2",
                    "page_number": 1,
                    "page_index": 0,
                    "block_index": 2,
                    "block_type": "text",
                    "bbox": [5, 6, 7, 8],
                    "method": "pymupdf",
                    "routing_reason": "embedded_text",
                    "source_text_start": 0,
                    "source_text_end": 16,
                    "chunk_text_start": 15,
                    "chunk_text_end": 31,
                    "is_overlap": True,
                },
            ]
        },
        method="pymupdf",
    )


def test_process_file_id_uses_filtered_output_path(tmp_path: Path):
    repository = FakeRepository(
        source_file(),
        [
            TextChunk(
                chunk_id="file-1:c000001",
                run_id="run-1",
                file_id="file-1",
                chunk_index=1,
                page_start=1,
                page_end=1,
                text="persona@example.com",
                text_hash_sha256="b" * 64,
                source_map={"segments": []},
                method="pymupdf",
            )
        ],
    )
    entity = RawEntity(
        entity_type="EMAIL",
        raw_entity_type="EMAIL_REGEX",
        source="regex",
        text="persona@example.com",
        start=0,
        end=19,
        score=0.99,
        normalized_value="persona@example.com",
    )

    written = process_file_id(
        "file-1",
        repository=repository,
        detector=FakeDetector([entity]),
        output_dir=tmp_path,
    )

    assert Path(written.raw_output_path) == tmp_path / "subdir" / "documento.pdf.json"
    assert Path(written.output_path) == (
        tmp_path / "subdir" / "documento.pdf_filtrado.json"
    )
    assert Path(written.raw_output_path).exists()
    assert Path(written.output_path).exists()
    assert repository.records[0].raw_entity_count == 1
    assert repository.records[0].accepted_entity_count == 1
    assert repository.accepted_entities[0]["file_id"] == "file-1"
    assert len(repository.accepted_entities[0]["accepted_entities"]) == 1


def test_build_file_entity_result_keeps_raw_entities_in_memory():
    entity = RawEntity(
        entity_type="EMAIL",
        raw_entity_type="EMAIL_REGEX",
        source="regex",
        text="persona@example.com",
        start=0,
        end=19,
        score=0.99,
        normalized_value="persona@example.com",
    )
    repository = FakeRepository(source_file(), [chunk()])

    result = build_file_entity_result(
        "file-1",
        repository=repository,
        detector=FakeDetector([entity]),
    )

    assert result.entity_count == 1
    assert result.chunks[0].entities[0].text == "persona@example.com"
    assert result.chunks[0].entities[0].trace[0]["source_block_id"] == "p1-b1"


def test_build_file_entity_result_batches_chunks_without_mixing_files():
    chunks = [
        TextChunk(
            chunk_id="file-1:c000001",
            run_id="run-1",
            file_id="file-1",
            chunk_index=1,
            page_start=1,
            page_end=1,
            text="uno@example.com",
            text_hash_sha256="b" * 64,
            source_map={"segments": []},
            method="pymupdf",
        ),
        TextChunk(
            chunk_id="file-1:c000002",
            run_id="run-1",
            file_id="file-1",
            chunk_index=2,
            page_start=2,
            page_end=2,
            text="dos@example.com",
            text_hash_sha256="c" * 64,
            source_map={"segments": []},
            method="pymupdf",
        ),
    ]
    detector = FakeBatchDetector()

    result = build_file_entity_result(
        "file-1",
        repository=FakeRepository(source_file(), chunks),
        detector=detector,
    )

    assert detector.calls == [["uno@example.com", "dos@example.com"]]
    assert [chunk.entities[0].text for chunk in result.chunks] == [
        "uno@example.com",
        "dos@example.com",
    ]


def test_process_file_id_writes_filtered_json(tmp_path: Path):
    entity = RawEntity(
        entity_type="RUT",
        raw_entity_type="RUT_REGEX",
        source="regex",
        text="12.378.895-8",
        start=19,
        end=31,
        score=0.99,
        normalized_value="123788958",
    )
    repository = FakeRepository(source_file(), [chunk()])

    written = process_file_id(
        "file-1",
        repository=repository,
        detector=FakeDetector([entity]),
        output_dir=tmp_path,
    )

    payload = json.loads(Path(written.output_path).read_text(encoding="utf-8"))
    raw_payload = json.loads(Path(written.raw_output_path).read_text(encoding="utf-8"))
    assert payload["file_id"] == "file-1"
    assert payload["raw_entity_count"] == 1
    assert payload["accepted_entity_count"] == 1
    assert payload["entity_processing_seconds"] is not None
    assert payload["cpu_total_seconds"] is not None
    assert payload["peak_memory_mb"] is not None
    assert raw_payload["entity_count"] == 1
    assert raw_payload["entity_processing_seconds"] is not None
    assert raw_payload["cpu_total_seconds"] is not None
    assert raw_payload["peak_memory_mb"] is not None
    assert repository.records[0].cpu_total_seconds is not None
    assert repository.records[0].peak_memory_mb is not None
    assert len(repository.accepted_entities[0]["accepted_entities"]) == 1
    assert "chunks" not in payload
    assert "chunks" in raw_payload
    accepted = payload["accepted_entities"][0]
    assert accepted["entity_type"] == "RUT"
    assert accepted["evidence_count"] == 1
    assert accepted["evidence"][0]["trace"][0]["source_block_id"] == "p1-b2"
    assert not (tmp_path / "subdir" / "documento.pdf.entities.json").exists()


def test_process_file_id_ignores_mask_text_when_requested(tmp_path: Path):
    entity = RawEntity(
        entity_type="EMAIL",
        raw_entity_type="EMAIL_REGEX",
        source="regex",
        text="persona@example.com",
        start=0,
        end=18,
        score=0.99,
    )
    repository = FakeRepository(source_file(), [chunk()])

    written = process_file_id(
        "file-1",
        repository=repository,
        detector=FakeDetector([entity]),
        output_dir=tmp_path,
        mask_text=True,
    )

    payload = json.loads(Path(written.output_path).read_text(encoding="utf-8"))
    raw_payload = json.loads(Path(written.raw_output_path).read_text(encoding="utf-8"))
    assert payload["accepted_entities"][0]["text"] == "persona@example.com"
    assert payload["accepted_entities"][0]["evidence"][0]["text"] == (
        "persona@example.com"
    )
    assert raw_payload["chunks"][0]["entities"][0]["text"] == "persona@example.com"


def test_process_file_id_writes_valid_json_without_entities(tmp_path: Path):
    repository = FakeRepository(source_file(), [chunk()])

    written = process_file_id(
        "file-1",
        repository=repository,
        detector=FakeDetector([]),
        output_dir=tmp_path,
    )

    payload = json.loads(Path(written.output_path).read_text(encoding="utf-8"))
    assert payload["raw_entity_count"] == 0
    assert payload["accepted_entity_count"] == 0
    assert payload["accepted_entities"] == []


def test_build_trace_handles_entity_crossing_segments():
    entity = RawEntity(
        entity_type="SPAN",
        raw_entity_type="SPAN",
        source="fake",
        text="Javier\nRUN",
        start=8,
        end=18,
        score=0.5,
    )

    trace = build_trace(entity, chunk())

    assert [item["source_block_id"] for item in trace] == ["p1-b1", "p1-b2"]
    assert trace[0]["entity_chunk_start"] == 8
    assert trace[1]["entity_chunk_end"] == 18
    assert trace[1]["is_overlap"] is True
