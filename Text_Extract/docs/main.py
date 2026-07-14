from __future__ import annotations

import argparse
import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import load_environment, optional_int_env, require_env
from common.models import QUEUE_DOC
from docs.worker import publish_pending_outbox, run_doc_worker
from materialization.service import build_file_materializer
from messaging.rabbitmq import RabbitMQConsumer, RabbitMQPublisher
from staging.adapters import PostgresTextExtractionRepository


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consume Queue-Doc messages and stage document text chunks."
    )
    parser.add_argument(
        "--source-queue-name",
        default=QUEUE_DOC,
        help=f"RabbitMQ source queue name. Default: {QUEUE_DOC}",
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
        help="Process at most this many Queue-Doc messages.",
    )
    parser.add_argument(
        "--dev-mode",
        action="store_true",
        help=(
            "Read up to --max-messages messages, requeue them, and avoid "
            "creating downstream outbox messages."
        ),
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
            run_doc_worker(
                repository=repository,
                publisher=publisher,
                consumer=consumer,
                source_queue_name=args.source_queue_name,
                publish_downstream=not args.dev_mode,
                max_messages=args.max_messages,
                requeue_messages=args.dev_mode,
                materializer=materializer,
            )
    finally:
        publisher.close()
        consumer.close()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
