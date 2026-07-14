from __future__ import annotations

import json
from pathlib import Path

from config import DEFAULT_RESULTS_DIR
from models import FileEntityResult


def write_raw_result_json(
    result: FileEntityResult,
    output_dir: str | Path | None = None,
) -> Path:
    destination = raw_result_output_path(result, output_dir=output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = result.with_output_paths(raw_json_path=str(destination))
    destination.write_text(
        json.dumps(
            result.to_dict(mask_text=False),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def raw_result_output_path(
    result: FileEntityResult,
    output_dir: str | Path | None = None,
) -> Path:
    root = Path(output_dir).expanduser() if output_dir else DEFAULT_RESULTS_DIR
    relative_path = _safe_relative_path(
        result.source_file.relative_path,
        result.source_file.file_name or "entities.json",
    )
    return root / relative_path.with_name(f"{relative_path.name}.json")


def _safe_relative_path(relative_path: str, file_name: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return Path(file_name)
    if not candidate.parts:
        return Path(file_name)
    return candidate
