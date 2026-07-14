from __future__ import annotations

import json
from pathlib import Path

try:
    from .config import DEFAULT_RESULTS_DIR
    from .models import FilteredFileResult
except ImportError:  # pragma: no cover - script execution fallback
    from config import DEFAULT_RESULTS_DIR
    from models import FilteredFileResult


def write_filtered_result_json(
    result: FilteredFileResult,
    output_dir: str | Path | None = None,
    mask_text: bool = False,
) -> Path:
    destination = filtered_result_output_path(result, output_dir=output_dir)
    result.filtered_json_path = str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
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


def filtered_result_output_path(
    result: FilteredFileResult,
    output_dir: str | Path | None = None,
) -> Path:
    root = Path(output_dir).expanduser() if output_dir else DEFAULT_RESULTS_DIR
    source = result.source_result
    relative_path = _safe_relative_path(
        str(source.get("relative_path") or ""),
        str(source.get("file_name") or "entities.json"),
    )
    return root / relative_path.with_name(f"{relative_path.name}_filtrado.json")


def _safe_relative_path(relative_path: str, file_name: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return Path(file_name)
    if not candidate.parts:
        return Path(file_name)
    return candidate
