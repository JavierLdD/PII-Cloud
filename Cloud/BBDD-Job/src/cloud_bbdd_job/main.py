from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cloud_bbdd_job.results_repository import (
    DatabaseResultsRepository,
    artifact_metadata,
)
from cloud_bbdd_job.scan_request import ScanRequest, load_scan_request_from_env


LOGGER = logging.getLogger("cloud_bbdd_job")


def main() -> int:
    _configure_logging()
    scan_request = load_scan_request_from_env(os.environ)
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
