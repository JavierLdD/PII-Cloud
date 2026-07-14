from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from Entity_Text_Filter import main as filter_main
from Entity_Text_Filter.resolver import filter_raw_result
from Entity_Text_Filter.writer import (
    filtered_result_output_path,
    write_filtered_result_json,
)


def raw_result_with_email() -> dict[str, object]:
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
        "chunk_count": 1,
        "entity_count": 1,
        "generated_at": "2026-06-11T00:00:00+00:00",
        "chunks": [
            {
                "chunk_id": "file-1:c000001",
                "chunk_index": 1,
                "page_start": 1,
                "page_end": 1,
                "method": "pymupdf",
                "text_hash_sha256": "b" * 64,
                "entity_count": 1,
                "entities": [
                    {
                        "entity_type": "EMAIL",
                        "raw_entity_type": "EMAIL_REGEX",
                        "source": "regex",
                        "text": "persona@example.com",
                        "start": 0,
                        "end": 19,
                        "score": 0.99,
                        "normalized_value": "persona@example.com",
                        "trace": [],
                    }
                ],
            }
        ],
    }


def test_filtered_result_output_path_uses_mirrored_relative_path(tmp_path: Path):
    result = filter_raw_result(raw_result_with_email())

    path = filtered_result_output_path(result, tmp_path)

    assert path == tmp_path / "subdir" / "documento.pdf_filtrado.json"


def test_write_filtered_result_json_ignores_mask_text_when_requested(tmp_path: Path):
    result = filter_raw_result(raw_result_with_email())

    path = write_filtered_result_json(result, tmp_path, mask_text=True)
    payload = json.loads(path.read_text(encoding="utf-8"))

    accepted = payload["accepted_entities"][0]
    assert payload["source_schema_version"] == "2.0"
    assert payload["source_type"] == "local"
    assert payload["source_uri"] == "local:///tmp/documento.pdf"
    assert accepted["text"] == "persona@example.com"
    assert accepted["evidence"][0]["text"] == "persona@example.com"


def test_main_input_json_writes_filtered_json(tmp_path: Path, capsys):
    input_path = tmp_path / "raw.entities.json"
    input_path.write_text(
        json.dumps(raw_result_with_email()),
        encoding="utf-8",
    )

    status = filter_main.main(
        [
            "--input-json",
            str(input_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    assert "filtered_entities" in capsys.readouterr().out
    output_path = tmp_path / "out" / "subdir" / "documento.pdf_filtrado.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["accepted_entity_count"] == 1
    assert payload["accepted_entities"][0]["entity_type"] == "EMAIL"


def test_main_gpu_flag_sets_zero_shot_device(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("PII_ENTITY_ZERO_SHOT_DEVICE", raising=False)
    input_path = tmp_path / "raw.entities.json"
    input_path.write_text(
        json.dumps(raw_result_with_email()),
        encoding="utf-8",
    )

    status = filter_main.main(
        [
            "--input-json",
            str(input_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--gpu",
        ]
    )

    assert status == 0
    assert "filtered_entities" in capsys.readouterr().out
    assert os.environ["PII_ENTITY_ZERO_SHOT_DEVICE"] == "auto"


def test_filter_entity_result_accepts_dict_input(tmp_path: Path):
    written = filter_main.filter_entity_result(
        raw_result_with_email(),
        output_dir=tmp_path,
    )

    assert Path(written.output_path).exists()
    assert written.result.accepted_entities[0].entity_type == "EMAIL"
