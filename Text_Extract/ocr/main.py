from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import load_environment, optional_int_env, require_env
from common.models import QUEUE_OCR, QUEUE_OCR_URGENT
from materialization.service import build_file_materializer
from messaging.rabbitmq import RabbitMQConsumer, RabbitMQPublisher
from ocr.mineru import (
    DEFAULT_MINERU_API_POLL_INTERVAL_SECONDS,
    DEFAULT_MINERU_API_SUBMIT_TIMEOUT_SECONDS,
    DEFAULT_MINERU_TIMEOUT_SECONDS,
    MINERU_API_POLL_INTERVAL_ENV,
    MINERU_API_RESULT_TIMEOUT_ENV,
    MINERU_API_SUBMIT_TIMEOUT_ENV,
    MINERU_API_URL_ENV,
    MinerUConfig,
)
from ocr.worker import publish_pending_outbox, run_ocr_worker
from staging.adapters import PostgresTextExtractionRepository


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Consume Queue-OCR-Urgente/Queue-OCR messages and stage OCR text chunks."
        )
    )
    parser.add_argument(
        "--source-queue-name",
        default=QUEUE_OCR,
        help=f"RabbitMQ normal source queue name. Default: {QUEUE_OCR}",
    )
    parser.add_argument(
        "--urgent-source-queue-name",
        default=QUEUE_OCR_URGENT,
        help=(
            "RabbitMQ urgent source queue name checked before "
            f"--source-queue-name. Default: {QUEUE_OCR_URGENT}"
        ),
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env path. Default: Text_Extract/.env",
    )
    parser.add_argument(
        "--publish-pending-only",
        action="store_true",
        help="Publish pending Queue-Entity outbox messages and exit.",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Process at most this many OCR messages across urgent and normal queues.",
    )
    parser.add_argument(
        "--dev-mode",
        action="store_true",
        help=(
            "Read up to --max-messages messages, requeue them, and avoid "
            "creating downstream outbox messages."
        ),
    )
    parser.add_argument(
        "--mineru-timeout",
        type=int,
        default=None,
        help=(
            "MinerU API result timeout in seconds. Default: "
            f"{DEFAULT_MINERU_TIMEOUT_SECONDS}"
        ),
    )
    parser.add_argument(
        "--mineru-api-url",
        default=None,
        help=f"MinerU FastAPI base URL. Default: ${MINERU_API_URL_ENV}",
    )
    parser.add_argument(
        "--mineru-api-poll-interval",
        type=float,
        default=None,
        help=(
            "Seconds between MinerU task-status polls. Default: "
            f"{DEFAULT_MINERU_API_POLL_INTERVAL_SECONDS}"
        ),
    )
    parser.add_argument(
        "--mineru-api-submit-timeout",
        type=float,
        default=None,
        help=(
            "MinerU task submission timeout in seconds. Default: "
            f"{DEFAULT_MINERU_API_SUBMIT_TIMEOUT_SECONDS}"
        ),
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep MinerU artifacts for debugging instead of deleting them.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="Optional root directory for kept MinerU artifacts.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dev_mode and args.max_messages is None:
        raise RuntimeError("--dev-mode requires --max-messages to avoid a requeue loop")

    load_environment(args.env_file)
    database_url = require_env("DATABASE_URL")
    rabbitmq_url = require_env("RABBITMQ_URL")
    chunk_ttl_hours = optional_int_env("TEXT_CHUNK_TTL_HOURS", 24)
    timeout_seconds = _resolve_result_timeout(args.mineru_timeout)
    api_url = _optional_text_arg_or_env(args.mineru_api_url, MINERU_API_URL_ENV)
    poll_interval_seconds = _optional_float_arg_or_env(
        args.mineru_api_poll_interval,
        MINERU_API_POLL_INTERVAL_ENV,
    )
    submit_timeout_seconds = _optional_float_arg_or_env(
        args.mineru_api_submit_timeout,
        MINERU_API_SUBMIT_TIMEOUT_ENV,
    )
    mineru_config = MinerUConfig(
        timeout_seconds=timeout_seconds,
        keep_artifacts=args.keep_artifacts,
        artifact_root=args.artifact_root,
        api_url=api_url,
        poll_interval_seconds=poll_interval_seconds,
        submit_timeout_seconds=submit_timeout_seconds,
    )
    print(
        "ocr_mineru_api "
        f"api_url={api_url or '<unset>'} "
        f"result_timeout_seconds={timeout_seconds} "
        f"poll_interval_seconds={poll_interval_seconds or '<env/default>'} "
        f"submit_timeout_seconds={submit_timeout_seconds or '<env/default>'}"
    )

    publisher = RabbitMQPublisher(rabbitmq_url)
    consumer = RabbitMQConsumer(rabbitmq_url)
    try:
        with PostgresTextExtractionRepository(
            database_url,
            chunk_ttl_hours=chunk_ttl_hours,
        ) as repository:
            if args.publish_pending_only:
                published_count = publish_pending_outbox(repository, publisher)
                print(f"published={published_count}")
                return 0

            materializer = build_file_materializer(repository)
            run_ocr_worker(
                repository=repository,
                publisher=publisher,
                consumer=consumer,
                source_queue_name=args.source_queue_name,
                urgent_source_queue_name=args.urgent_source_queue_name,
                publish_downstream=not args.dev_mode,
                max_messages=args.max_messages,
                requeue_messages=args.dev_mode,
                mineru_config=mineru_config,
                materializer=materializer,
            )
    finally:
        publisher.close()
        consumer.close()

    return 0


def _optional_text_arg_or_env(value: str | None, name: str) -> str | None:
    if value is not None and value.strip():
        return value.strip()
    env_value = os.environ.get(name)
    if env_value is None or env_value.strip() == "":
        return None
    return env_value.strip()


def _optional_float_arg_or_env(value: float | None, name: str) -> float | None:
    if value is not None:
        return value
    env_value = os.environ.get(name)
    if env_value is None or env_value.strip() == "":
        return None
    try:
        return float(env_value)
    except ValueError:
        raise RuntimeError(f"{name} must be a number")


def _resolve_result_timeout(argument_value: int | None) -> int:
    if argument_value is not None:
        return argument_value
    api_timeout = os.environ.get(MINERU_API_RESULT_TIMEOUT_ENV)
    if api_timeout is not None and api_timeout.strip():
        try:
            return int(api_timeout)
        except ValueError:
            raise RuntimeError(f"{MINERU_API_RESULT_TIMEOUT_ENV} must be an integer")
    return optional_int_env("MINERU_TIMEOUT_SECONDS", DEFAULT_MINERU_TIMEOUT_SECONDS)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
