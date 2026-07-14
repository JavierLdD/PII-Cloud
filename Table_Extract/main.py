from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar
from uuid import uuid4

from table_extract.config import load_environment, require_env
from table_extract.materialization import FileMaterializer, MaterializationConfig
from table_extract.messaging import RabbitMQConsumer
from table_extract.models import (
    DataSourceProfile,
    DiscoveryResult,
    ScanConfig,
    TableProcessingMetrics,
)
from table_extract.orchestration import (
    DatabaseProfileRequest,
    OrdsProfileRequest,
    FileProfileRequest,
    discover_table_source,
    profile_table_source,
)
from table_extract.operational import (
    classify_operational_exception,
    emit_operational_log,
    sanitize_text,
)
from table_extract.profile_artifacts import (
    discovery_artifact_json,
    profile_artifact_json,
)
from table_extract.resource_metrics import (
    capture_resource_usage,
    resource_usage_delta,
)
from table_extract.runtime import (
    QUEUE_TABLES,
    FileScanContext,
    TableExtractionRecord,
    process_file_id,
    run_table_listener,
)
from table_extract.runtime.repository import PostgresTableExtractRepository
from table_extract.sources import (
    DatabaseScanRequest,
    OrdsScanRequest,
)


T = TypeVar("T")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile tabular sources from Queue-Tables, BBDD, or ORDS."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--file-id",
        default=None,
        help="Prepare a file scan context directly from a stored file_id.",
    )
    mode_group.add_argument(
        "--database-url",
        default=None,
        help="Profile a database source directly with SQLAlchemy.",
    )
    mode_group.add_argument(
        "--ords-url",
        default=None,
        help="Profile an ORDS REST Enabled SQL endpoint directly.",
    )
    parser.add_argument(
        "--source-queue-name",
        default=QUEUE_TABLES,
        help=f"RabbitMQ source queue name. Default: {QUEUE_TABLES}",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Process at most this many Queue-Tables messages.",
    )
    parser.add_argument(
        "--dev-mode",
        action="store_true",
        help=(
            "Queue mode: requeue successfully processed messages and require "
            "--max-messages. Direct modes: print a profile summary to stderr."
        ),
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Only emit the structural profile artifact and skip PII Discovery.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env path. Default: .env next to Table_Extract.",
    )
    parser.add_argument(
        "--source-name",
        default=None,
        help="Source name for direct BBDD/ORDS profiling.",
    )
    parser.add_argument(
        "--include-schema",
        action="append",
        default=[],
        dest="include_schemas",
        help="Schema/owner to include. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-schema",
        action="append",
        default=[],
        dest="exclude_schemas",
        help="Schema/owner to exclude. Can be repeated.",
    )
    parser.add_argument(
        "--include-table",
        action="append",
        default=[],
        dest="include_tables",
        help="Table/view name to include. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-table",
        action="append",
        default=[],
        dest="exclude_tables",
        help="Table/view name to exclude. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-views",
        action="store_true",
        help="Exclude views from direct BBDD/ORDS profiling.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path for single-source artifact JSON output.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for Queue-Tables discovery artifacts, one JSON per message.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run id for direct BBDD/ORDS discovery. Defaults to a generated UUID.",
    )
    parser.add_argument(
        "--disable-zero-shot",
        action="store_true",
        help="Disable semantic zero-shot discovery for this run.",
    )
    parser.add_argument(
        "--zero-shot-model-name",
        default=None,
        help="Override the local Hugging Face model name/path for zero-shot discovery.",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default=None,
        help=(
            "Device for Zero-Shot discovery. Overrides "
            "TABLE_EXTRACT_ZERO_SHOT_DEVICE. Use auto to prefer CUDA/MPS."
        ),
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Alias for --device auto.",
    )
    parser.add_argument(
        "--database-dialect",
        default=None,
        help="Optional database dialect override for --database-url.",
    )
    parser.add_argument(
        "--ords-auth-mode",
        choices=("none", "basic", "bearer"),
        default=None,
        help="ORDS auth mode. Falls back to TABLE_EXTRACT_ORDS_AUTH_MODE.",
    )
    parser.add_argument(
        "--ords-username",
        default=None,
        help="ORDS Basic auth username. Falls back to TABLE_EXTRACT_ORDS_USERNAME.",
    )
    parser.add_argument(
        "--ords-password",
        default=None,
        help="ORDS Basic auth password. Falls back to TABLE_EXTRACT_ORDS_PASSWORD.",
    )
    parser.add_argument(
        "--ords-bearer-token",
        default=None,
        help="ORDS Bearer token. Falls back to TABLE_EXTRACT_ORDS_BEARER_TOKEN.",
    )
    parser.add_argument(
        "--ords-timeout-seconds",
        type=float,
        default=None,
        help="ORDS HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--ords-page-size",
        type=int,
        default=None,
        help="ORDS REST Enabled SQL page size.",
    )
    parser.add_argument(
        "--ords-max-pages",
        type=int,
        default=None,
        help="Maximum ORDS pages to fetch per SQL statement.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_environment(args.env_file)

    direct_source_mode = bool(args.file_id or args.database_url or args.ords_url)
    if args.dev_mode and args.max_messages is None and not direct_source_mode:
        raise ValueError("--dev-mode requires --max-messages")
    queue_mode = not direct_source_mode
    if queue_mode and not args.output_dir:
        args.output_dir = os.environ.get("TABLE_EXTRACT_OUTPUT_DIR")
    if queue_mode and not args.profile_only:
        if not args.output_dir:
            raise ValueError(
                "--output-dir or TABLE_EXTRACT_OUTPUT_DIR is required "
                "for Queue-Tables discovery"
            )
        if args.output:
            raise ValueError("Queue-Tables discovery uses --output-dir, not --output")

    if args.database_url:
        return _profile_database(args)
    if args.ords_url:
        return _profile_ords(args)

    database_url = require_env("TABLE_EXTRACT_DATABASE_URL", "DATABASE_URL")

    with PostgresTableExtractRepository(database_url) as repository:
        materializer = FileMaterializer(
            repository,
            MaterializationConfig.from_env(),
        )

        if args.file_id:
            context = process_file_id(
                args.file_id,
                repository=repository,
                materializer=materializer,
            )
            try:
                if args.profile_only:
                    profile, metrics = _timed_table_operation(
                        lambda: profile_table_source(FileProfileRequest(context))
                    )
                    output_path = _emit_profile(
                        profile,
                        output_path=args.output,
                        dev_mode=args.dev_mode,
                        metrics=metrics,
                    )
                    _save_table_extraction_record(
                        repository,
                        context=context,
                        status="table_profile_completed",
                        metrics=metrics,
                        profile=profile,
                        output_path=output_path,
                        profile_only=True,
                    )
                else:
                    result, metrics = _timed_table_operation(
                        lambda: discover_table_source(
                            FileProfileRequest(context),
                            config=_scan_config_from_args(args),
                        )
                    )
                    output_path = _emit_discovery(
                        result,
                        output_path=args.output,
                        dev_mode=args.dev_mode,
                        metrics=metrics,
                    )
                    _save_table_extraction_record(
                        repository,
                        context=context,
                        status="table_discovery_completed",
                        metrics=metrics,
                        profile=result.profile,
                        finding_count=len(result.findings),
                        output_path=output_path,
                        profile_only=False,
                    )
            finally:
                if context.is_temporary:
                    materializer.release_context(context.file_id)
            return 0

        rabbitmq_url = require_env("TABLE_EXTRACT_RABBITMQ_URL", "RABBITMQ_URL")
        consumer = RabbitMQConsumer(rabbitmq_url)
        try:
            handle_context = None
            if not args.profile_only:
                output_dir = Path(args.output_dir)

                def handle_context(context):
                    result, metrics = _timed_table_operation(
                        lambda: discover_table_source(
                            FileProfileRequest(context),
                            config=_scan_config_from_args(args),
                        )
                    )
                    output_path = _queue_discovery_output_path(output_dir, context)
                    written_path = _emit_discovery(
                        result,
                        output_path=str(output_path),
                        dev_mode=args.dev_mode,
                        metrics=metrics,
                    )
                    _save_table_extraction_record(
                        repository,
                        context=context,
                        status="table_discovery_completed",
                        metrics=metrics,
                        profile=result.profile,
                        finding_count=len(result.findings),
                        output_path=written_path,
                        profile_only=False,
                    )

            run_table_listener(
                repository=repository,
                materializer=materializer,
                consumer=consumer,
                source_queue_name=args.source_queue_name,
                max_messages=args.max_messages,
                requeue_messages=args.dev_mode,
                handle_context=handle_context,
            )
        finally:
            consumer.close()

    return 0


def _profile_database(args: argparse.Namespace) -> int:
    request = DatabaseScanRequest(
        connection_uri=args.database_url,
        source_name=args.source_name or "database",
        dialect=args.database_dialect,
        include_schemas=tuple(args.include_schemas),
        exclude_schemas=tuple(args.exclude_schemas),
        include_tables=tuple(args.include_tables),
        exclude_tables=tuple(args.exclude_tables),
        include_views=not args.exclude_views,
    )
    try:
        if args.profile_only:
            profile, metrics = _timed_table_operation(
                lambda: profile_table_source(DatabaseProfileRequest(request))
            )
        else:
            result, metrics = _timed_table_operation(
                lambda: discover_table_source(
                    DatabaseProfileRequest(request),
                    run_id=args.run_id or _new_run_id(),
                    config=_scan_config_from_args(args),
                )
            )
    except Exception as exc:
        emit_operational_log(
            "profile_failed" if args.profile_only else "discovery_failed",
            classify_operational_exception(
                exc,
                default_component="database",
                safe_context={
                    "source_name": request.source_name,
                    "source_type": "database",
                    "dialect": request.dialect,
                },
            ),
        )
        raise
    if args.profile_only:
        _emit_profile(
            profile,
            output_path=args.output,
            dev_mode=args.dev_mode,
            metrics=metrics,
        )
    else:
        _emit_discovery(
            result,
            output_path=args.output,
            dev_mode=args.dev_mode,
            metrics=metrics,
        )
    return 0


def _profile_ords(args: argparse.Namespace) -> int:
    request = OrdsScanRequest(
        rest_sql_url=args.ords_url,
        source_name=args.source_name or "ords",
        auth_mode=_value_or_env(args.ords_auth_mode, "TABLE_EXTRACT_ORDS_AUTH_MODE") or "none",
        username=_value_or_env(args.ords_username, "TABLE_EXTRACT_ORDS_USERNAME"),
        password=_value_or_env(args.ords_password, "TABLE_EXTRACT_ORDS_PASSWORD"),
        bearer_token=_value_or_env(
            args.ords_bearer_token,
            "TABLE_EXTRACT_ORDS_BEARER_TOKEN",
        ),
        timeout_seconds=(
            args.ords_timeout_seconds
            if args.ords_timeout_seconds is not None
            else 30.0
        ),
        page_size=args.ords_page_size if args.ords_page_size is not None else 500,
        max_pages=args.ords_max_pages if args.ords_max_pages is not None else 100,
        include_schemas=tuple(args.include_schemas),
        exclude_schemas=tuple(args.exclude_schemas),
        include_tables=tuple(args.include_tables),
        exclude_tables=tuple(args.exclude_tables),
        include_views=not args.exclude_views,
    )
    try:
        if args.profile_only:
            profile, metrics = _timed_table_operation(
                lambda: profile_table_source(OrdsProfileRequest(request))
            )
        else:
            result, metrics = _timed_table_operation(
                lambda: discover_table_source(
                    OrdsProfileRequest(request),
                    run_id=args.run_id or _new_run_id(),
                    config=_scan_config_from_args(args),
                )
            )
    except Exception as exc:
        emit_operational_log(
            "profile_failed" if args.profile_only else "discovery_failed",
            classify_operational_exception(
                exc,
                default_component="ords",
                safe_context={
                    "source_name": request.source_name,
                    "source_type": "ords",
                    "auth_mode": request.auth_mode,
                },
            ),
        )
        raise
    if args.profile_only:
        _emit_profile(
            profile,
            output_path=args.output,
            dev_mode=args.dev_mode,
            metrics=metrics,
        )
    else:
        _emit_discovery(
            result,
            output_path=args.output,
            dev_mode=args.dev_mode,
            metrics=metrics,
        )
    return 0


def _scan_config_from_args(args: argparse.Namespace) -> ScanConfig:
    defaults = ScanConfig()
    requested_device = _requested_device(args.device, args.gpu)
    zero_shot_model_name = (
        args.zero_shot_model_name
        or os.environ.get("TABLE_EXTRACT_ZERO_SHOT_MODEL")
        or os.environ.get("PII_ENTITY_ZERO_SHOT_MODEL")
        or defaults.zero_shot_model_name
    )
    return ScanConfig(
        sample_limit=defaults.sample_limit,
        max_value_length=defaults.max_value_length,
        sample_only_when_needed=defaults.sample_only_when_needed,
        zero_shot_enabled=not args.disable_zero_shot,
        zero_shot_model_name=zero_shot_model_name,
        zero_shot_device=requested_device or os.environ.get(
            "TABLE_EXTRACT_ZERO_SHOT_DEVICE",
            defaults.zero_shot_device,
        ),
        zero_shot_initial_sample_limit=defaults.zero_shot_initial_sample_limit,
        zero_shot_expanded_sample_limit=defaults.zero_shot_expanded_sample_limit,
        zero_shot_positive_threshold=defaults.zero_shot_positive_threshold,
        zero_shot_continue_threshold=defaults.zero_shot_continue_threshold,
        zero_shot_batch_size=_env_int(
            "TABLE_EXTRACT_ZERO_SHOT_BATCH_SIZE",
            defaults.zero_shot_batch_size,
        ),
    )


def _new_run_id() -> str:
    return f"table-extract-{uuid4()}"


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _requested_device(device: str | None, use_gpu: bool) -> str | None:
    if use_gpu and device and device != "auto":
        raise ValueError("--gpu cannot be combined with --device other than auto")
    if use_gpu:
        return "auto"
    return device


def _timed_table_operation(operation: Callable[[], T]) -> tuple[T, TableProcessingMetrics]:
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    started_resources = capture_resource_usage()
    result = operation()
    completed_at = datetime.now(timezone.utc)
    resources = resource_usage_delta(started_resources)
    return (
        result,
        TableProcessingMetrics(
            started_at=started_at,
            completed_at=completed_at,
            processing_seconds=round(time.perf_counter() - started_perf, 6),
            cpu_user_seconds=resources.cpu_user_seconds,
            cpu_system_seconds=resources.cpu_system_seconds,
            cpu_total_seconds=resources.cpu_total_seconds,
            peak_memory_mb=resources.peak_memory_mb,
        ),
    )


def _save_table_extraction_record(
    repository: object,
    *,
    context: FileScanContext,
    status: str,
    metrics: TableProcessingMetrics,
    profile: DataSourceProfile,
    finding_count: int | None = None,
    output_path: str | None = None,
    profile_only: bool,
) -> None:
    save = getattr(repository, "save_table_extraction_record", None)
    if save is None:
        return
    table_count, column_count = _profile_counts(profile)
    save(
        TableExtractionRecord(
            file_id=context.file_id,
            run_id=context.run_id,
            routing_decision_id=context.routing_decision_id,
            status=status,
            started_at=metrics.started_at,
            completed_at=metrics.completed_at,
            processing_seconds=metrics.processing_seconds,
            cpu_user_seconds=metrics.cpu_user_seconds,
            cpu_system_seconds=metrics.cpu_system_seconds,
            cpu_total_seconds=metrics.cpu_total_seconds,
            peak_memory_mb=metrics.peak_memory_mb,
            table_count=table_count,
            column_count=column_count,
            finding_count=finding_count,
            profile_json_path=output_path if profile_only else None,
            discovery_json_path=None if profile_only else output_path,
        )
    )


def _profile_counts(profile: DataSourceProfile) -> tuple[int, int]:
    table_count = len(profile.tables)
    column_count = sum(len(table.columns) for table in profile.tables)
    return table_count, column_count


def _emit_profile(
    profile: DataSourceProfile,
    *,
    output_path: str | None,
    dev_mode: bool,
    metrics: TableProcessingMetrics | None = None,
) -> str | None:
    if dev_mode:
        _print_profile_summary(profile)

    content = _profile_json(profile, pretty=dev_mode, metrics=metrics)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")
        return str(path)
    print(content)
    return None


def _emit_discovery(
    result: DiscoveryResult,
    *,
    output_path: str | None,
    dev_mode: bool,
    metrics: TableProcessingMetrics | None = None,
) -> str | None:
    if dev_mode:
        _print_discovery_summary(result)

    content = discovery_artifact_json(result, pretty=dev_mode, metrics=metrics)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")
        return str(path)
    print(content)
    return None


def _profile_json(
    profile: DataSourceProfile,
    *,
    pretty: bool,
    metrics: TableProcessingMetrics | None = None,
) -> str:
    return profile_artifact_json(profile, pretty=pretty, metrics=metrics)


def _queue_discovery_output_path(output_dir: Path, context) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{_safe_filename(context.run_id)}_"
        f"{_safe_filename(context.file_id)}.discovery.json"
    )
    return output_dir / filename


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe or "unknown"


def _print_profile_summary(profile: DataSourceProfile) -> None:
    table_count = sum(1 for table in profile.tables if table.table_type != "view")
    view_count = sum(1 for table in profile.tables if table.table_type == "view")
    column_count = sum(len(table.columns) for table in profile.tables)
    print(
        "Profile summary: "
        f"source_name={profile.source_name} "
        f"source_type={profile.source_type} "
        f"dialect={profile.dialect or 'unknown'} "
        f"tables={table_count} "
        f"views={view_count} "
        f"columns={column_count}",
        file=sys.stderr,
    )


def _print_discovery_summary(result: DiscoveryResult) -> None:
    profile = result.profile
    finding_count = len(result.findings)
    print(
        "Discovery summary: "
        f"run_id={result.run_id} "
        f"source_name={profile.source_name} "
        f"source_type={profile.source_type} "
        f"dialect={profile.dialect or 'unknown'} "
        f"findings={finding_count}",
        file=sys.stderr,
    )


def _value_or_env(value: str | None, env_name: str) -> str | None:
    return value if value is not None else os.environ.get(env_name)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {sanitize_text(exc)}", file=sys.stderr)
        raise SystemExit(1)
