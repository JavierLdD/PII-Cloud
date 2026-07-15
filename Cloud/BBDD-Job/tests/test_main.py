from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import cloud_bbdd_job.main as job_main
from cloud_bbdd_job.scan_request import ScanRequest


RUN_ID = "86ca6e73-ea37-4c1f-812d-7b71dcb771bb"


def _request(**overrides) -> ScanRequest:
    payload = {
        "scan_id": RUN_ID,
        "user_id": "ana",
        "run_name": "Clientes Q3",
        "database_type": "postgresql",
        "connection_uri": "postgresql://user:password@target/db",
        "confirm_full_scan": True,
        "output_uri": "gs://pii-results/database/",
        "output_local_path": "/tmp/bbdd-result.json",
        "disable_zero_shot": True,
    }
    payload.update(overrides)
    return ScanRequest.from_mapping(payload)


class FakeDownloadBlob:
    def __init__(self, name: str, content: str) -> None:
        self.name = name
        self.content = content

    def download_to_filename(self, destination: str) -> None:
        Path(destination).write_text(self.content, encoding="utf-8")


class FakeStorageClient:
    def __init__(self, blobs: list[FakeDownloadBlob]) -> None:
        self.blobs = blobs
        self.calls: list[tuple[str, str]] = []

    def list_blobs(self, bucket_name: str, *, prefix: str):
        self.calls.append((bucket_name, prefix))
        return [blob for blob in self.blobs if blob.name.startswith(prefix)]


def _artifact() -> dict[str, object]:
    return {
        "artifact_type": "table_extract.discovery",
        "schema_version": "1.0",
        "generated_at": "2026-07-13T12:00:00Z",
        "run_id": RUN_ID,
        "profile": {
            "source_name": "Clientes Q3",
            "source_type": "database",
            "dialect": "postgresql",
            "tables": [],
        },
        "findings": [],
    }


def test_main_uploads_artifact_before_persisting_results(monkeypatch) -> None:
    request = _request()
    order: list[str] = []
    monkeypatch.setattr(job_main, "load_scan_request_from_env", lambda env: request)
    monkeypatch.setitem(
        sys.modules,
        "main",
        SimpleNamespace(main=lambda argv: order.append("extract") or 0),
    )
    monkeypatch.setattr(
        job_main,
        "_upload_output_if_requested",
        lambda path, scan_request: order.append("gcs")
        or f"gs://pii-results/{RUN_ID}.json",
    )
    monkeypatch.setattr(
        job_main,
        "_persist_results_if_configured",
        lambda path, scan_request, uri: order.append("cloud_sql"),
    )

    assert job_main.main() == 0
    assert order == ["extract", "gcs", "cloud_sql"]


def test_main_does_not_persist_when_gcs_upload_fails(monkeypatch) -> None:
    request = _request()
    persisted = False
    monkeypatch.setattr(job_main, "load_scan_request_from_env", lambda env: request)
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(main=lambda argv: 0))

    def fail_upload(path, scan_request):
        raise RuntimeError("gcs unavailable")

    def persist(path, scan_request, uri):
        nonlocal persisted
        persisted = True

    monkeypatch.setattr(job_main, "_upload_output_if_requested", fail_upload)
    monkeypatch.setattr(job_main, "_persist_results_if_configured", persist)

    with pytest.raises(RuntimeError, match="gcs unavailable"):
        job_main.main()
    assert persisted is False


def test_main_propagates_results_database_failure(monkeypatch) -> None:
    request = _request()
    monkeypatch.setattr(job_main, "load_scan_request_from_env", lambda env: request)
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(main=lambda argv: 0))
    monkeypatch.setattr(
        job_main,
        "_upload_output_if_requested",
        lambda path, scan_request: f"gs://pii-results/{RUN_ID}.json",
    )

    def fail_persistence(path, scan_request, uri):
        raise RuntimeError("results database unavailable")

    monkeypatch.setattr(job_main, "_persist_results_if_configured", fail_persistence)

    with pytest.raises(RuntimeError, match="results database unavailable"):
        job_main.main()


def test_prepare_zero_shot_model_downloads_snapshot_and_uses_local_path(tmp_path) -> None:
    storage = FakeStorageClient(
        [
            FakeDownloadBlob("models/zero/config.json", "{}"),
            FakeDownloadBlob("models/zero/tokenizer.json", "{}"),
            FakeDownloadBlob("models/zero/spm.model", "sentencepiece"),
            FakeDownloadBlob("models/zero/onnx/model.onnx", "onnx"),
        ]
    )
    local_dir = tmp_path / "zero-shot"

    prepared = job_main.prepare_zero_shot_model(
        _request(disable_zero_shot=False),
        env={
            "TABLE_EXTRACT_ZERO_SHOT_MODEL_URI": "gs://pii-models/models/zero",
            "TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR": str(local_dir),
        },
        storage_client=storage,
    )

    assert prepared.zero_shot_model_name == str(local_dir)
    assert storage.calls == [("pii-models", "models/zero/")]
    assert (local_dir / "tokenizer.json").exists()
    assert (local_dir / "spm.model").exists()
    assert (local_dir / "onnx" / "model.onnx").exists()


def test_prepare_zero_shot_model_requires_gcs_uri_when_enabled() -> None:
    with pytest.raises(RuntimeError, match="TABLE_EXTRACT_ZERO_SHOT_MODEL_URI"):
        job_main.prepare_zero_shot_model(
            _request(disable_zero_shot=False),
            env={},
            storage_client=FakeStorageClient([]),
        )


def test_prepare_zero_shot_model_skips_gcs_when_disabled() -> None:
    request = _request(disable_zero_shot=True)

    assert job_main.prepare_zero_shot_model(
        request,
        env={},
        storage_client=FakeStorageClient([]),
    ) is request


class FakeRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.calls = []

    def persist_discovery(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_persist_results_reads_exact_artifact_and_calls_repository(tmp_path) -> None:
    output_path = tmp_path / "result.json"
    content = json.dumps(_artifact(), indent=2).encode("utf-8")
    output_path.write_bytes(content)
    repositories: list[FakeRepository] = []

    def factory(database_url: str) -> FakeRepository:
        repository = FakeRepository(database_url)
        repositories.append(repository)
        return repository

    job_main._persist_results_if_configured(
        output_path,
        _request(output_local_path=str(output_path)),
        f"gs://pii-results/{RUN_ID}.json",
        env={"BBDD_RESULTS_DATABASE_URL": "postgresql://results-secret"},
        repository_factory=factory,
    )

    assert repositories[0].database_url == "postgresql://results-secret"
    call = repositories[0].calls[0]
    assert call["artifact"]["run_id"] == RUN_ID
    assert call["artifact_size_bytes"] == len(content)
    assert len(call["artifact_sha256"]) == 64


def test_persist_results_requires_results_database_url(tmp_path) -> None:
    output_path = tmp_path / "result.json"
    output_path.write_text(json.dumps(_artifact()), encoding="utf-8")

    with pytest.raises(RuntimeError, match="BBDD_RESULTS_DATABASE_URL"):
        job_main._persist_results_if_configured(
            output_path,
            _request(output_local_path=str(output_path)),
            f"gs://pii-results/{RUN_ID}.json",
            env={},
        )


def test_persist_results_requires_gcs_artifact(tmp_path) -> None:
    output_path = tmp_path / "result.json"
    output_path.write_text(json.dumps(_artifact()), encoding="utf-8")

    with pytest.raises(RuntimeError, match="GCS_OUTPUT_URI"):
        job_main._persist_results_if_configured(
            output_path,
            _request(output_local_path=str(output_path)),
            None,
            env={"BBDD_RESULTS_DATABASE_URL": "postgresql://results-secret"},
        )


def test_persist_results_rejects_profile_only(tmp_path) -> None:
    output_path = tmp_path / "result.json"
    output_path.write_text(json.dumps(_artifact()), encoding="utf-8")

    with pytest.raises(RuntimeError, match="profile_only"):
        job_main._persist_results_if_configured(
            output_path,
            _request(output_local_path=str(output_path), profile_only=True),
            f"gs://pii-results/{RUN_ID}.json",
            env={"BBDD_RESULTS_DATABASE_URL": "postgresql://results-secret"},
        )
