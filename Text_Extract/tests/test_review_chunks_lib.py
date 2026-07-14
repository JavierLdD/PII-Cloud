from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notebooks"))

from review_chunks_lib import (  # noqa: E402
    ReviewConfig,
    _validate_select_sql,
    flatten_source_map_segments,
    load_config,
    resolve_text_extract_dir,
)


def test_resolve_text_extract_dir_from_notebooks_folder(monkeypatch, tmp_path):
    text_extract_dir = tmp_path / "Text_Extract"
    notebooks_dir = text_extract_dir / "notebooks"
    notebooks_dir.mkdir(parents=True)
    (text_extract_dir / "schema.sql").write_text("", encoding="utf-8")
    monkeypatch.chdir(notebooks_dir)

    assert resolve_text_extract_dir() == text_extract_dir.resolve()


def test_load_config_reads_local_env_without_overriding_existing(monkeypatch, tmp_path):
    text_extract_dir = tmp_path / "Text_Extract"
    text_extract_dir.mkdir()
    (text_extract_dir / ".env").write_text(
        "DATABASE_URL=postgresql://from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    config = load_config(text_extract_dir)

    assert config.database_url == "postgresql://from-file"
    assert config.text_extract_dir == text_extract_dir.resolve()


def test_validate_select_sql_rejects_mutating_queries():
    _validate_select_sql("SELECT * FROM text_chunks_staging")

    try:
        _validate_select_sql("DELETE FROM text_chunks_staging")
    except ValueError as exc:
        assert "Only read-only SELECT" in str(exc)
    else:
        raise AssertionError("Expected mutating SQL to be rejected")


def test_flatten_source_map_segments_json_encodes_bbox():
    rows = flatten_source_map_segments(
        {
            "segments": [
                {
                    "source_block_id": "block-1",
                    "page_number": 1,
                    "bbox": [1, 2, 3, 4],
                }
            ]
        }
    )

    assert rows == [
        {
            "source_block_id": "block-1",
            "page_number": 1,
            "bbox": "[1, 2, 3, 4]",
        }
    ]
