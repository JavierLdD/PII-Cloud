from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from models import WrittenFilteredResult
    from resolver import filter_raw_result
    from writer import write_filtered_result_json
else:
    from .models import WrittenFilteredResult
    from .resolver import filter_raw_result
    from .writer import write_filtered_result_json


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter raw entity JSON files produced by Entity_Text_Extract."
    )
    parser.add_argument(
        "--input-json",
        required=True,
        help="Path to a legacy/debug raw .entities.json file.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for filtered dev JSON files.",
    )
    parser.add_argument(
        "--mask-text",
        action="store_true",
        help="Deprecated; outputs are always written without masking.",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default=None,
        help="Device for Zero-Shot filtering. Use auto to prefer CUDA/MPS when available.",
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Alias for --device auto.",
    )
    return parser.parse_args(argv)


def filter_entity_result(
    raw_result_or_path: dict[str, Any] | str | Path,
    output_dir: str | Path | None = None,
    mask_text: bool = False,
) -> WrittenFilteredResult:
    raw_result, source_json_path = _load_raw_result(raw_result_or_path)
    result = filter_raw_result(raw_result, source_json_path=source_json_path)
    output_path = write_filtered_result_json(
        result,
        output_dir=output_dir,
        mask_text=mask_text,
    )
    return WrittenFilteredResult(result=result, output_path=str(output_path))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_zero_shot_device(args.device, args.gpu)
    written = filter_entity_result(
        args.input_json,
        output_dir=args.output_dir,
        mask_text=args.mask_text,
    )
    print(
        "filtered_entities "
        f"file_id={written.result.source_result.get('file_id')} "
        f"raw={written.result.raw_entity_count} "
        f"accepted={len(written.result.accepted_entities)} "
        f"output={written.output_path}"
    )
    return 0


def _configure_zero_shot_device(device: str | None, use_gpu: bool) -> None:
    requested_device = _requested_device(device, use_gpu)
    if requested_device is not None:
        os.environ["PII_ENTITY_ZERO_SHOT_DEVICE"] = requested_device


def _requested_device(device: str | None, use_gpu: bool) -> str | None:
    if use_gpu and device and device != "auto":
        raise RuntimeError("--gpu cannot be combined with --device other than auto")
    if use_gpu:
        return "auto"
    return device


def _load_raw_result(
    raw_result_or_path: dict[str, Any] | str | Path,
) -> tuple[dict[str, Any], str | None]:
    if isinstance(raw_result_or_path, dict):
        return raw_result_or_path, None

    path = Path(raw_result_or_path).expanduser()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Input JSON must contain an object")
    return payload, str(path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
