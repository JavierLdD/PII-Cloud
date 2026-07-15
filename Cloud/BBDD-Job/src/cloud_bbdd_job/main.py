from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from cloud_bbdd_job.results_repository import (
    DatabaseResultsRepository,
    artifact_metadata,
)
from cloud_bbdd_job.scan_request import ScanRequest, load_scan_request_from_env


LOGGER = logging.getLogger("cloud_bbdd_job")
DEFAULT_ZERO_SHOT_LOCAL_DIR = "/tmp/pii-models/zero-shot"


def main() -> int:
    _configure_logging()
    scan_request = load_scan_request_from_env(os.environ)
    scan_request = prepare_zero_shot_model(scan_request)
    output_path = Path(scan_request.output_local_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    argv = scan_request.to_table_extract_argv(str(output_path))
    LOGGER.info("starting_bbdd_pii_job argv=%s", _redacted_argv(argv))

    from main import main as table_extract_main

    exit_code = table_extract_main(argv)
    if exit_code == 0:
        artifact_uri = _upload_output_if_requested(output_path, scan_request)
        _persist_results_if_configured(output_path, scan_request, artifact_uri)
    return exit_code


def prepare_zero_shot_model(
    scan_request: ScanRequest,
    *,
    env: Mapping[str, str] | None = None,
    storage_client: Any | None = None,
) -> ScanRequest:
    if scan_request.disable_zero_shot:
        LOGGER.info("zero_shot_disabled")
        return scan_request

    values = os.environ if env is None else env
    model_uri = values.get("TABLE_EXTRACT_ZERO_SHOT_MODEL_URI", "").strip()
    if not model_uri:
        raise RuntimeError(
            "TABLE_EXTRACT_ZERO_SHOT_MODEL_URI is required when Zero-Shot is enabled"
        )
    local_dir = Path(
        values.get("TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR", "").strip()
        or DEFAULT_ZERO_SHOT_LOCAL_DIR
    )
    copied = copy_gcs_prefix_to_local(
        model_uri,
        local_dir,
        storage_client=storage_client,
    )
    if copied == 0:
        raise RuntimeError(
            "TABLE_EXTRACT_ZERO_SHOT_MODEL_URI did not contain model files: "
            f"{model_uri}"
        )
    LOGGER.info(
        "zero_shot_model_ready source=%s local_dir=%s files=%s",
        model_uri,
        local_dir,
        copied,
    )
    return replace(scan_request, zero_shot_model_name=str(local_dir))


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


def _default_storage_client() -> Any:
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "Missing google-cloud-storage dependency required by Zero-Shot model URI."
        ) from exc
    return storage.Client()


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc:
        raise ValueError("Zero-Shot model URI must have the form gs://bucket/path")
    return parsed.netloc, parsed.path.lstrip("/")


def _blob_relative_name(blob_name: str, base_prefix: str) -> str:
    if not base_prefix:
        return blob_name.lstrip("/")
    if blob_name == base_prefix:
        return Path(blob_name).name
    prefix = f"{base_prefix}/"
    if not blob_name.startswith(prefix):
        return ""
    return blob_name[len(prefix) :]


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def _table_extract_argv(output_path: Path) -> list[str]:
    scan_request = load_scan_request_from_env(os.environ)
    return scan_request.to_table_extract_argv(str(output_path))


def _upload_output_if_requested(
    output_path: Path,
    scan_request: ScanRequest,
) -> str | None:
    gcs_uri = scan_request.output_uri
    if not gcs_uri:
        LOGGER.info("output_written path=%s", output_path)
        return None
    if not output_path.exists():
        raise RuntimeError(f"Expected output artifact was not written: {output_path}")

    bucket_name, blob_name = _resolve_gcs_target(gcs_uri, output_path, scan_request)

    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "Missing google-cloud-storage dependency required by output_uri."
        ) from exc

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(output_path), content_type="application/json")
    artifact_uri = f"gs://{bucket_name}/{blob_name}"
    LOGGER.info("output_uploaded uri=%s", artifact_uri)
    return artifact_uri


def _persist_results_if_configured(
    output_path: Path,
    scan_request: ScanRequest,
    artifact_uri: str | None,
    *,
    env: Mapping[str, str] | None = None,
    repository_factory: Any = DatabaseResultsRepository,
) -> None:
    values = os.environ if env is None else env
    results_database_url = values.get("BBDD_RESULTS_DATABASE_URL", "").strip()
    if not results_database_url:
        raise RuntimeError("BBDD_RESULTS_DATABASE_URL is required")
    if artifact_uri is None:
        raise RuntimeError(
            "GCS_OUTPUT_URI is required when BBDD_RESULTS_DATABASE_URL is configured"
        )
    if scan_request.profile_only:
        raise RuntimeError(
            "profile_only cannot be persisted as a BBDD discovery result"
        )
    if not output_path.exists():
        raise RuntimeError(f"Expected output artifact was not written: {output_path}")

    artifact, artifact_size_bytes, artifact_sha256 = artifact_metadata(
        output_path.read_bytes()
    )
    repository = repository_factory(results_database_url)
    repository.persist_discovery(
        scan_request=scan_request,
        artifact=artifact,
        artifact_uri=artifact_uri,
        artifact_size_bytes=artifact_size_bytes,
        artifact_sha256=artifact_sha256,
    )
    LOGGER.info("results_persisted run_id=%s", scan_request.scan_id)


def _resolve_gcs_target(
    gcs_uri: str,
    output_path: Path,
    scan_request: ScanRequest,
) -> tuple[str, str]:
    parsed = urlparse(gcs_uri)
    if parsed.scheme != "gs" or not parsed.netloc:
        raise ValueError("output_uri must have the form gs://bucket/path")

    bucket_name = parsed.netloc
    blob_name = parsed.path.lstrip("/")
    if not blob_name or blob_name.endswith("/"):
        blob_name = f"{blob_name}{_artifact_name(output_path, scan_request)}"
    return bucket_name, blob_name


def _artifact_name(output_path: Path, scan_request: ScanRequest) -> str:
    explicit = os.environ.get("BBDD_ARTIFACT_NAME")
    if explicit:
        return explicit

    source_name = _slug(scan_request.source_name)
    run_id = _slug(scan_request.scan_id or output_path.stem)
    task_index = _slug(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    return f"{source_name}-{run_id}-task-{task_index}.json"


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return slug.strip("-") or "artifact"


def _redacted_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for arg in argv:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        redacted.append(arg)
        if arg == "--database-url":
            hide_next = True
    return redacted


if __name__ == "__main__":
    sys.exit(main())
