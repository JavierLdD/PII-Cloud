from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_environment, require_env
from detector import RawEntityDetector
from messaging.rabbitmq import RabbitMQConsumer
from models import QUEUE_ENTITY
from repository import PostgresEntityRepository
from worker import process_file_id, run_entity_worker


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract and filter PII entities from staged text chunks."
    )
    parser.add_argument(
        "--file-id",
        default=None,
        help="Process a single file_id from text_chunks_staging without RabbitMQ.",
    )
    parser.add_argument(
        "--source-queue-name",
        default=QUEUE_ENTITY,
        help=f"RabbitMQ source queue name. Default: {QUEUE_ENTITY}",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env path. Default: Entity_Text_Extract/.env",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output directory for raw and filtered JSON files. "
            "Default: /tmp/pii-entity-results."
        ),
    )
    parser.add_argument(
        "--mask-text",
        action="store_true",
        help="Deprecated; outputs are always written without masking.",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Process at most this many Queue-Entity messages.",
    )
    parser.add_argument(
        "--dev-mode",
        action="store_true",
        help="Read up to --max-messages messages and requeue them after writing JSON.",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default=None,
        help=(
            "Device for entity models and filter zero-shot. "
            "Use auto to prefer CUDA/MPS when available."
        ),
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Alias for --device auto.",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    repository_factory=PostgresEntityRepository,
    detector_factory=RawEntityDetector.from_env,
    consumer_factory=RabbitMQConsumer,
) -> int:
    args = parse_args(argv)
    if args.dev_mode and args.max_messages is None:
        raise RuntimeError("--dev-mode requires --max-messages to avoid a requeue loop")

    load_environment(args.env_file)
    _configure_model_device(args.device, args.gpu)
    database_url = require_env("DATABASE_URL")
    detector = detector_factory()

    with repository_factory(database_url) as repository:
        if args.file_id:
            written = process_file_id(
                args.file_id,
                repository=repository,
                detector=detector,
                output_dir=args.output_dir,
                mask_text=args.mask_text,
            )
            print(
                "processed_entities "
                f"file_id={written.result.source_result.get('file_id')} "
                f"raw={written.result.raw_entity_count} "
                f"accepted={len(written.result.accepted_entities)} "
                f"raw_output={written.raw_output_path} "
                f"filtered_output={written.filtered_output_path} "
                f"processing_seconds={written.result.entity_processing_seconds} "
                f"cpu_total_seconds={written.result.cpu_total_seconds} "
                f"peak_memory_mb={written.result.peak_memory_mb}"
            )
            return 0

        rabbitmq_url = require_env("RABBITMQ_URL")
        consumer = consumer_factory(rabbitmq_url)
        try:
            run_entity_worker(
                repository=repository,
                detector=detector,
                consumer=consumer,
                source_queue_name=args.source_queue_name,
                output_dir=args.output_dir,
                mask_text=args.mask_text,
                max_messages=args.max_messages,
                requeue_messages=args.dev_mode,
            )
        finally:
            consumer.close()

    return 0


def _configure_model_device(device: str | None, use_gpu: bool) -> None:
    requested_device = _requested_device(device, use_gpu)
    if requested_device is None:
        return
    os.environ["PII_ENTITY_MODEL_DEVICE"] = requested_device
    os.environ["PII_ENTITY_ZERO_SHOT_DEVICE"] = requested_device
    os.environ["PII_ENTITY_GLINER2_USE_GPU"] = (
        "false" if requested_device == "cpu" else "true"
    )


def _requested_device(device: str | None, use_gpu: bool) -> str | None:
    if use_gpu and device and device != "auto":
        raise RuntimeError("--gpu cannot be combined with --device other than auto")
    if use_gpu:
        return "auto"
    return device


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
