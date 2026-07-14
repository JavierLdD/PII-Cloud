from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Callable, Mapping


PROJECT_DIR = Path(__file__).resolve().parents[4]
ENTITY_EXTRACT_DIR = PROJECT_DIR / "Entity_Text_Extract"
COMMON_DIR = PROJECT_DIR / "Cloud" / "Text-Extract-Job-Common" / "src"
for path in (PROJECT_DIR, COMMON_DIR, ENTITY_EXTRACT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cloud_text_extract_job.errors import MessageScopeError  # noqa: E402
from cloud_text_extract_job.pubsub import (  # noqa: E402
    PulledMessage,
    PubSubPuller,
    validate_message_scope,
)
from cloud_text_extract_job.runner import drain_subscription  # noqa: E402
from cloud_text_extract_job.timeout import per_file_timeout  # noqa: E402
from models import ChunksReadyMessage, EntityExtractionRecord  # noqa: E402


LOGGER = logging.getLogger("cloud_entity_text_extract_job")
DEFAULT_IDLE_TIMEOUT_SECONDS = 60
DEFAULT_PULL_TIMEOUT_SECONDS = 5
DEFAULT_PER_FILE_TIMEOUT_SECONDS = 540
DEFAULT_MAX_MESSAGES = 0
DEFAULT_OUTPUT_DIR = "/tmp/pii-entity-output"
DEFAULT_ZERO_SHOT_LOCAL_DIR = "/tmp/pii-models/zero-shot"
DEFAULT_MODEL_BATCH_SIZE = 8
DEFAULT_ZERO_SHOT_BATCH_SIZE = 8

ProcessFileCallable = Callable[..., Any]


@dataclass(frozen=True)
class EntityTextExtractJobConfig:
    subscription_id: str
    database_url: str
    expected_user_id: str
    expected_run_id: str
    gcs_output_uri: str
    save_raw_results: bool = False
    zero_shot_model_uri: str | None = None
    zero_shot_local_dir: str = DEFAULT_ZERO_SHOT_LOCAL_DIR
    zero_shot_enabled: bool = True
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    pull_timeout_seconds: int = DEFAULT_PULL_TIMEOUT_SECONDS
    per_file_timeout_seconds: int = DEFAULT_PER_FILE_TIMEOUT_SECONDS
    max_messages: int = DEFAULT_MAX_MESSAGES
    output_dir: str = DEFAULT_OUTPUT_DIR
    model_batch_size: int = DEFAULT_MODEL_BATCH_SIZE
    zero_shot_batch_size: int = DEFAULT_ZERO_SHOT_BATCH_SIZE
    model_device: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "EntityTextExtractJobConfig":
        return cls(
            subscription_id=_required_env(env, "SUBSCRIPTION_ID"),
            database_url=_required_env(env, "DATABASE_URL"),
            expected_user_id=_required_env(env, "EXPECTED_USER_ID"),
            expected_run_id=_required_env(env, "EXPECTED_RUN_ID"),
            gcs_output_uri=_required_env(env, "PII_ENTITY_GCS_OUTPUT_URI"),
            save_raw_results=_bool_env(env, "PII_ENTITY_SAVE_RAW_RESULTS", False),
            zero_shot_model_uri=_optional_env(env, "PII_ENTITY_ZERO_SHOT_MODEL_URI"),
            zero_shot_local_dir=(
                _optional_env(env, "PII_ENTITY_ZERO_SHOT_LOCAL_DIR")
                or DEFAULT_ZERO_SHOT_LOCAL_DIR
            ),
            zero_shot_enabled=_bool_env(env, "PII_ENTITY_ENABLE_ZERO_SHOT", True),
            idle_timeout_seconds=_int_env(
                env,
                "PUBSUB_IDLE_TIMEOUT_SECONDS",
                DEFAULT_IDLE_TIMEOUT_SECONDS,
            ),
            pull_timeout_seconds=_int_env(
                env,
                "PUBSUB_PULL_TIMEOUT_SECONDS",
                DEFAULT_PULL_TIMEOUT_SECONDS,
            ),
            per_file_timeout_seconds=_int_env(
                env,
                "PER_FILE_TIMEOUT_SECONDS",
                DEFAULT_PER_FILE_TIMEOUT_SECONDS,
            ),
            max_messages=_int_env(env, "MAX_MESSAGES", DEFAULT_MAX_MESSAGES),
            output_dir=_optional_env(env, "PII_ENTITY_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR,
            model_batch_size=_positive_int_env(
                env,
                "PII_ENTITY_MODEL_BATCH_SIZE",
                DEFAULT_MODEL_BATCH_SIZE,
            ),
            zero_shot_batch_size=_positive_int_env(
                env,
                "PII_ENTITY_ZERO_SHOT_BATCH_SIZE",
                DEFAULT_ZERO_SHOT_BATCH_SIZE,
            ),
            model_device=_optional_env(env, "PII_ENTITY_MODEL_DEVICE"),
        )

    def apply_runtime_defaults(self) -> None:
        os.environ.setdefault("PII_ENTITY_OUTPUT_DIR", self.output_dir)
        os.environ.setdefault(
            "PII_ENTITY_ENABLE_ZERO_SHOT",
            "true" if self.zero_shot_enabled else "false",
        )
        os.environ.setdefault("PII_ENTITY_MODEL_BATCH_SIZE", str(self.model_batch_size))
        os.environ.setdefault(
            "PII_ENTITY_ZERO_SHOT_BATCH_SIZE",
            str(self.zero_shot_batch_size),
        )
        if self.model_device:
            os.environ.setdefault("PII_ENTITY_MODEL_DEVICE", self.model_device)


def main() -> int:
    _configure_logging()
    config = EntityTextExtractJobConfig.from_env(os.environ)
    config.apply_runtime_defaults()
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)

    storage_client = _default_storage_client()
    prepare_zero_shot_model(config, storage_client=storage_client)

    from detector import RawEntityDetector  # noqa: WPS433
    from repository import PostgresEntityRepository  # noqa: WPS433
    from worker import process_file_id  # noqa: WPS433

    detector = RawEntityDetector.from_env()
    gcs_writer = GcsEntityResultWriter(config.gcs_output_uri, storage_client)

    with PostgresEntityRepository(config.database_url) as repository:
        puller = PubSubPuller()
        processed = drain_subscription(
            config=config,
            puller=puller,
            handle_message=lambda message: handle_entity_message(
                message=message,
                config=config,
                repository=repository,
                detector=detector,
                gcs_writer=gcs_writer,
                process_file=process_file_id,
            ),
        )

    LOGGER.info("entity_text_extract_job_finished processed=%s", processed)
    return 0


def handle_entity_message(
    *,
    message: PulledMessage,
    config: EntityTextExtractJobConfig,
    repository: Any,
    detector: Any,
    gcs_writer: "GcsEntityResultWriter",
    process_file: ProcessFileCallable | None = None,
) -> None:
    try:
        validate_message_scope(
            message.payload,
            message.attributes,
            expected_user_id=config.expected_user_id,
            expected_run_id=config.expected_run_id,
        )
    except MessageScopeError as exc:
        LOGGER.error(
            "entity_message_scope_mismatch reason=%s message_id=%s payload=%s",
            exc,
            message.message_id,
            _safe_log_payload(message.payload),
        )
        return

    try:
        chunks_ready = ChunksReadyMessage.from_payload(message.payload)
    except ValueError as exc:
        LOGGER.error(
            "unsupported_entity_message reason=%s message_id=%s payload=%s",
            exc,
            message.message_id,
            _safe_log_payload(message.payload),
        )
        return

    process_file = process_file or _load_process_file_id()
    with per_file_timeout(config.per_file_timeout_seconds):
        written = process_file(
            chunks_ready.file_id,
            repository=repository,
            detector=detector,
            output_dir=config.output_dir,
            mask_text=False,
        )

    raw_gcs_uri, filtered_gcs_uri = gcs_writer.upload_written_results(
        written,
        save_raw_results=config.save_raw_results,
        run_id=chunks_ready.run_id,
    )
    _persist_cloud_result_paths(
        repository=repository,
        written=written,
        raw_gcs_uri=raw_gcs_uri,
        filtered_gcs_uri=filtered_gcs_uri,
        run_id=chunks_ready.run_id,
    )
    LOGGER.info(
        "processed_entity_file file_id=%s run_id=%s raw_entities=%s accepted=%s raw_uri=%s filtered_uri=%s",
        chunks_ready.file_id,
        chunks_ready.run_id,
        _raw_entity_count(written),
        _accepted_entity_count(written),
        raw_gcs_uri,
        filtered_gcs_uri,
    )


class GcsEntityResultWriter:
    def __init__(self, base_uri: str, storage_client: Any | None = None) -> None:
        self.base_uri = _normalize_gs_uri(base_uri)
        self.storage_client = storage_client or _default_storage_client()

    def upload_written_results(
        self,
        written: Any,
        *,
        save_raw_results: bool,
        run_id: str | None = None,
    ) -> tuple[str | None, str]:
        source_file = written.raw_result.source_file
        output_run_id = run_id or source_file.run_id
        raw_uri = self.artifact_uri(
            run_id=output_run_id,
            kind="raw",
            relative_path=source_file.relative_path,
            file_name=source_file.file_name,
            file_id=source_file.file_id,
        )
        filtered_uri = self.artifact_uri(
            run_id=output_run_id,
            kind="filters",
            relative_path=source_file.relative_path,
            file_name=source_file.file_name,
            file_id=source_file.file_id,
        )
        raw_uri_to_record = raw_uri if save_raw_results else None

        filtered_payload = _filtered_result_payload(
            written.filtered_result,
            raw_gcs_uri=raw_uri_to_record,
            filtered_gcs_uri=filtered_uri,
        )
        self.upload_json(filtered_uri, filtered_payload)

        if save_raw_results:
            raw_payload = _raw_result_payload(
                written.raw_result,
                raw_gcs_uri=raw_uri,
                filtered_gcs_uri=filtered_uri,
            )
            self.upload_json(raw_uri, raw_payload)

        return raw_uri_to_record, filtered_uri

    def artifact_uri(
        self,
        *,
        run_id: str,
        kind: str,
        relative_path: str,
        file_name: str,
        file_id: str,
    ) -> str:
        bucket_name, base_prefix = _parse_gs_uri(self.base_uri)
        safe_path = _safe_relative_artifact_path(relative_path, file_name)
        unique_path = _with_file_id_suffix(safe_path, file_id)
        if kind == "raw":
            artifact_path = unique_path.with_name(f"{unique_path.name}.json")
        elif kind == "filters":
            artifact_path = unique_path.with_name(f"{unique_path.name}_filtrado.json")
        else:
            raise ValueError(f"Unsupported entity artifact kind: {kind}")
        blob_name = _join_gcs_path(
            base_prefix,
            run_id,
            kind,
            artifact_path.as_posix(),
        )
        return f"gs://{bucket_name}/{blob_name}"

    def upload_json(self, uri: str, payload: Mapping[str, Any]) -> None:
        bucket_name, blob_name = _parse_gs_uri(uri)
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.storage_client.bucket(bucket_name).blob(blob_name).upload_from_string(
            data,
            content_type="application/json",
        )


def prepare_zero_shot_model(
    config: EntityTextExtractJobConfig,
    *,
    storage_client: Any | None = None,
) -> str | None:
    if not config.zero_shot_enabled:
        LOGGER.info("zero_shot_disabled")
        return None
    if not config.zero_shot_model_uri:
        raise RuntimeError(
            "PII_ENTITY_ZERO_SHOT_MODEL_URI is required when Zero-Shot is enabled"
        )

    local_dir = Path(config.zero_shot_local_dir)
    copied = copy_gcs_prefix_to_local(
        config.zero_shot_model_uri,
        local_dir,
        storage_client=storage_client,
    )
    if copied == 0:
        raise RuntimeError(
            "PII_ENTITY_ZERO_SHOT_MODEL_URI did not contain model files: "
            f"{config.zero_shot_model_uri}"
        )
    os.environ["PII_ENTITY_ZERO_SHOT_MODEL"] = str(local_dir)
    LOGGER.info(
        "zero_shot_model_ready source=%s local_dir=%s files=%s",
        config.zero_shot_model_uri,
        local_dir,
        copied,
    )
    return str(local_dir)


def copy_gcs_prefix_to_local(
    gcs_uri: str,
    local_dir: str | Path,
    *,
    storage_client: Any | None = None,
) -> int:
    storage_client = storage_client or _default_storage_client()
    bucket_name, prefix = _parse_gs_uri(gcs_uri)
    base_prefix = prefix.rstrip("/")
    local_root = Path(local_dir)
    local_root.mkdir(parents=True, exist_ok=True)

    list_prefix = f"{base_prefix}/" if base_prefix else ""
    blobs = list(storage_client.list_blobs(bucket_name, prefix=list_prefix))
    if not blobs and base_prefix:
        blobs = list(storage_client.list_blobs(bucket_name, prefix=base_prefix))

    copied = 0
    for blob in blobs:
        blob_name = str(blob.name)
        if blob_name.endswith("/"):
            continue
        relative_name = _blob_relative_name(blob_name, base_prefix)
        if not relative_name:
            continue
        destination = local_root / PurePosixPath(relative_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(destination))
        copied += 1
    return copied


def _persist_cloud_result_paths(
    *,
    repository: Any,
    written: Any,
    raw_gcs_uri: str | None,
    filtered_gcs_uri: str,
    run_id: str | None = None,
) -> None:
    save = getattr(repository, "save_entity_extraction_record", None)
    if not callable(save):
        return

    raw_result = written.raw_result
    source_file = raw_result.source_file
    save(
        EntityExtractionRecord(
            file_id=source_file.file_id,
            run_id=run_id or source_file.run_id,
            status="entity_extraction_completed",
            started_at=raw_result.entity_started_at or datetime.now(timezone.utc),
            completed_at=raw_result.entity_completed_at,
            processing_seconds=raw_result.entity_processing_seconds,
            cpu_user_seconds=raw_result.cpu_user_seconds,
            cpu_system_seconds=raw_result.cpu_system_seconds,
            cpu_total_seconds=raw_result.cpu_total_seconds,
            peak_memory_mb=raw_result.peak_memory_mb,
            raw_entity_count=_raw_entity_count(written),
            accepted_entity_count=_accepted_entity_count(written),
            raw_json_path=raw_gcs_uri,
            filtered_json_path=filtered_gcs_uri,
        )
    )


def _raw_result_payload(
    raw_result: Any,
    *,
    raw_gcs_uri: str | None,
    filtered_gcs_uri: str,
) -> dict[str, Any]:
    with_paths = raw_result.with_output_paths(
        raw_json_path=raw_gcs_uri,
        filtered_json_path=filtered_gcs_uri,
    )
    return dict(with_paths.to_dict(mask_text=False))


def _filtered_result_payload(
    filtered_result: Any,
    *,
    raw_gcs_uri: str | None,
    filtered_gcs_uri: str,
) -> dict[str, Any]:
    filtered_result.raw_json_path = raw_gcs_uri
    filtered_result.filtered_json_path = filtered_gcs_uri
    filtered_result.source_json_path = raw_gcs_uri
    return dict(filtered_result.to_dict(mask_text=False))


def _raw_entity_count(written: Any) -> int:
    return int(getattr(written.raw_result, "entity_count", 0))


def _accepted_entity_count(written: Any) -> int:
    accepted = getattr(written.filtered_result, "accepted_entities", [])
    return len(accepted) if isinstance(accepted, list) else 0


def _safe_relative_artifact_path(relative_path: str, file_name: str) -> PurePosixPath:
    fallback_name = str(file_name or "entities.json").strip() or "entities.json"
    normalized = str(relative_path or "").replace("\\", "/").strip()
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or not candidate.parts:
        return PurePosixPath(fallback_name)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        return PurePosixPath(fallback_name)
    return candidate


def _with_file_id_suffix(path: PurePosixPath, file_id: str) -> PurePosixPath:
    safe_file_id = str(file_id or "unknown-file").replace("/", "_")
    return path.with_name(f"{path.name}__{safe_file_id}")


def _normalize_gs_uri(uri: str) -> str:
    bucket_name, prefix = _parse_gs_uri(uri)
    if prefix:
        return f"gs://{bucket_name}/{prefix.rstrip('/')}"
    return f"gs://{bucket_name}"


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected GCS URI starting with gs://, got: {uri!r}")
    without_scheme = uri[len("gs://") :]
    bucket_name, separator, prefix = without_scheme.partition("/")
    if not bucket_name:
        raise ValueError(f"Missing bucket in GCS URI: {uri!r}")
    return bucket_name, prefix.strip("/") if separator else ""


def _join_gcs_path(*parts: str) -> str:
    return "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))


def _blob_relative_name(blob_name: str, base_prefix: str) -> str:
    if blob_name == base_prefix:
        return PurePosixPath(blob_name).name
    prefix = f"{base_prefix}/" if base_prefix else ""
    if prefix and blob_name.startswith(prefix):
        return blob_name[len(prefix) :]
    return PurePosixPath(blob_name).name


def _load_process_file_id() -> ProcessFileCallable:
    from worker import process_file_id  # noqa: WPS433

    return process_file_id


def _default_storage_client() -> Any:
    from google.cloud import storage

    return storage.Client()


def _safe_log_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version",
        "event_type",
        "run_id",
        "file_id",
        "routing_decision_id",
        "source_type",
        "destination_queue_name",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _optional_env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed < 0:
        raise RuntimeError(f"{name} must be greater than or equal to zero")
    return parsed


def _positive_int_env(env: Mapping[str, str], name: str, default: int) -> int:
    parsed = _int_env(env, name, default)
    if parsed < 1:
        raise RuntimeError(f"{name} must be greater than or equal to one")
    return parsed


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s %(message)s",
    )


if __name__ == "__main__":
    raise SystemExit(main())
