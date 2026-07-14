from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
for path in (
    PROJECT,
    PROJECT / "Cloud" / "Text-Extract-Job-Common" / "src",
    PROJECT / "Entity_Text_Extract",
    ROOT / "src",
):
    sys.path.insert(0, str(path))

from cloud_entity_text_extract_job import main as entity_job  # noqa: E402
from cloud_text_extract_job.pubsub import PulledMessage  # noqa: E402
from Entity_Text_Filter.models import (  # noqa: E402
    EntityEvidence,
    FilteredEntity,
    FilteredFileResult,
)
from models import (  # noqa: E402
    ChunkEntityResult,
    FileEntityResult,
    RawEntity,
    SourceFile,
    TextChunk,
    WrittenEntityResults,
)


RUN_ID = "run-1"
USER_ID = "user-1"
FILE_ID = "file-1"


class FakeUploadBlob:
    def __init__(self, client: "FakeStorageClient", bucket_name: str, blob_name: str):
        self.client = client
        self.bucket_name = bucket_name
        self.name = blob_name

    def upload_from_string(self, data: str, content_type: str) -> None:
        self.client.uploads[(self.bucket_name, self.name)] = {
            "data": data,
            "content_type": content_type,
        }


class FakeDownloadBlob:
    def __init__(self, name: str, content: str = "{}"):
        self.name = name
        self.content = content

    def download_to_filename(self, destination: str) -> None:
        Path(destination).write_text(self.content, encoding="utf-8")


class FakeBucket:
    def __init__(self, client: "FakeStorageClient", bucket_name: str):
        self.client = client
        self.bucket_name = bucket_name

    def blob(self, blob_name: str) -> FakeUploadBlob:
        return FakeUploadBlob(self.client, self.bucket_name, blob_name)


class FakeStorageClient:
    def __init__(self, blobs: list[FakeDownloadBlob] | None = None):
        self.uploads = {}
        self.blobs = blobs or []

    def bucket(self, bucket_name: str) -> FakeBucket:
        return FakeBucket(self, bucket_name)

    def list_blobs(self, bucket_name: str, prefix: str):
        return [blob for blob in self.blobs if blob.name.startswith(prefix)]


class FakeRepository:
    def __init__(self):
        self.records = []

    def save_entity_extraction_record(self, record) -> None:
        self.records.append(record)


class FakeDetector:
    pass


def config(**overrides: object) -> entity_job.EntityTextExtractJobConfig:
    data = {
        "subscription_id": "projects/pii/subscriptions/entity",
        "database_url": "postgresql://example",
        "expected_user_id": USER_ID,
        "expected_run_id": RUN_ID,
        "gcs_output_uri": "gs://bucket/RutaGCP",
        "save_raw_results": False,
        "zero_shot_enabled": False,
        "per_file_timeout_seconds": 0,
    }
    data.update(overrides)
    return entity_job.EntityTextExtractJobConfig(**data)


def chunks_ready_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "2.0",
        "event_type": "file.chunks_ready",
        "run_id": RUN_ID,
        "file_id": FILE_ID,
        "routing_decision_id": "route-1",
        "source_type": "drive",
        "source_uri": "drive://file/abc",
        "external_id": "abc",
        "file_name": "documento.pdf",
        "relative_path": "subdir/documento.pdf",
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "checksum_sha256": None,
        "content_hash": None,
        "etag": "etag-1",
        "size_bytes": 123,
        "source_queue_name": "Queue-PDF",
        "destination_queue_name": "Queue-Entity",
        "chunk_count": 1,
        "page_count": 1,
    }
    payload.update(overrides)
    return payload


def pulled_message(payload: dict[str, object] | None = None, **attrs: str) -> PulledMessage:
    attributes = {"user_id": USER_ID, "run_id": RUN_ID}
    attributes.update(attrs)
    return PulledMessage(
        ack_id="ack-1",
        payload=payload or chunks_ready_payload(),
        attributes=attributes,
        message_id="message-1",
    )


def written_results(
    relative_path: str = "subdir/documento.pdf",
    file_id: str = FILE_ID,
    run_id: str = RUN_ID,
) -> WrittenEntityResults:
    source = SourceFile(
        file_id=file_id,
        run_id=run_id,
        source_type="drive",
        source_uri="drive://file/abc",
        external_id="abc",
        file_name="documento.pdf",
        relative_path=relative_path,
        extension=".pdf",
        mime_type="application/pdf",
        size_bytes=123,
        checksum_sha256=None,
        content_hash=None,
        etag="etag-1",
    )
    chunk = TextChunk(
        chunk_id="file-1:c000001",
        run_id=run_id,
        file_id=file_id,
        chunk_index=1,
        page_start=1,
        page_end=1,
        text="persona@example.com",
        text_hash_sha256="b" * 64,
        source_map={"segments": []},
        method="pymupdf",
    )
    raw_entity = RawEntity(
        entity_type="EMAIL",
        raw_entity_type="EMAIL_REGEX",
        source="regex",
        text="persona@example.com",
        start=0,
        end=19,
        score=0.99,
        normalized_value="persona@example.com",
    )
    now = datetime.now(timezone.utc)
    raw = FileEntityResult(
        source_file=source,
        chunks=[ChunkEntityResult(chunk=chunk, entities=[raw_entity])],
        entity_started_at=now,
        entity_completed_at=now,
        entity_processing_seconds=0.1,
        cpu_user_seconds=0.01,
        cpu_system_seconds=0.02,
        cpu_total_seconds=0.03,
        peak_memory_mb=100.0,
        raw_json_path="/tmp/raw.json",
        filtered_json_path="/tmp/filtered.json",
    )
    evidence = EntityEvidence(
        chunk_id=chunk.chunk_id,
        chunk_index=chunk.chunk_index,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        entity_type="EMAIL",
        raw_entity_type="EMAIL_REGEX",
        source="regex",
        text="persona@example.com",
        start=0,
        end=19,
        score=0.99,
        normalized_value="persona@example.com",
        trace=[{"source_block_id": "p1-b1"}],
    )
    filtered = FilteredFileResult(
        source_result=raw.to_dict(mask_text=False),
        accepted_entities=[
            FilteredEntity(
                entity_type="EMAIL",
                text="persona@example.com",
                normalized_value="persona@example.com",
                value_key="persona@example.com",
                source="regex",
                raw_entity_type="EMAIL_REGEX",
                score=0.99,
                is_base=True,
                validation_status="validated",
                validation_reason=None,
                confidence_level="VERY_CONFIDENT",
                decision_score=1.0,
                decision_method="base_validation",
                zero_shot_score=None,
                zero_shot_label=None,
                primary_location={
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "page_start": 1,
                    "page_end": 1,
                    "start": 0,
                    "end": 19,
                    "trace": [{"source_block_id": "p1-b1"}],
                },
                evidence=[evidence],
            )
        ],
        raw_json_path="/tmp/raw.json",
        filtered_json_path="/tmp/filtered.json",
    )
    return WrittenEntityResults(
        raw_result=raw,
        filtered_result=filtered,
        raw_output_path="/tmp/raw.json",
        filtered_output_path="/tmp/filtered.json",
    )


def test_gcs_writer_uploads_filters_only_by_default():
    storage = FakeStorageClient()
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", storage)

    raw_uri, filtered_uri = writer.upload_written_results(
        written_results(),
        save_raw_results=False,
    )

    assert raw_uri is None
    assert filtered_uri == "gs://bucket/RutaGCP/run-1/filters/subdir/documento.pdf__file-1_filtrado.json"
    assert list(storage.uploads) == [
        ("bucket", "RutaGCP/run-1/filters/subdir/documento.pdf__file-1_filtrado.json")
    ]
    assert '"raw_json_path": null' in storage.uploads[
        ("bucket", "RutaGCP/run-1/filters/subdir/documento.pdf__file-1_filtrado.json")
    ]["data"]


def test_gcs_writer_uploads_raw_when_enabled():
    storage = FakeStorageClient()
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", storage)

    raw_uri, filtered_uri = writer.upload_written_results(
        written_results(),
        save_raw_results=True,
    )

    assert raw_uri == "gs://bucket/RutaGCP/run-1/raw/subdir/documento.pdf__file-1.json"
    assert filtered_uri == "gs://bucket/RutaGCP/run-1/filters/subdir/documento.pdf__file-1_filtrado.json"
    assert set(storage.uploads) == {
        ("bucket", "RutaGCP/run-1/filters/subdir/documento.pdf__file-1_filtrado.json"),
        ("bucket", "RutaGCP/run-1/raw/subdir/documento.pdf__file-1.json"),
    }


def test_gcs_writer_falls_back_to_file_name_for_unsafe_paths():
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", FakeStorageClient())

    assert writer.artifact_uri(
        run_id=RUN_ID,
        kind="filters",
        relative_path="../secret/documento.pdf",
        file_name="documento.pdf",
        file_id=FILE_ID,
    ) == "gs://bucket/RutaGCP/run-1/filters/documento.pdf__file-1_filtrado.json"


def test_gcs_writer_keeps_same_named_files_from_overwriting_each_other():
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", FakeStorageClient())

    first_uri = writer.artifact_uri(
        run_id=RUN_ID,
        kind="filters",
        relative_path="documento.pdf",
        file_name="documento.pdf",
        file_id="file-1",
    )
    second_uri = writer.artifact_uri(
        run_id=RUN_ID,
        kind="filters",
        relative_path="documento.pdf",
        file_name="documento.pdf",
        file_id="file-2",
    )

    assert first_uri.endswith("/documento.pdf__file-1_filtrado.json")
    assert second_uri.endswith("/documento.pdf__file-2_filtrado.json")
    assert first_uri != second_uri


def test_handle_entity_message_persists_gcs_uris_after_upload():
    repository = FakeRepository()
    storage = FakeStorageClient()
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", storage)
    processed = []

    def process_file(file_id, **kwargs):
        processed.append((file_id, kwargs["output_dir"]))
        return written_results()

    entity_job.handle_entity_message(
        message=pulled_message(),
        config=config(output_dir="/tmp/pii-entity-test"),
        repository=repository,
        detector=FakeDetector(),
        gcs_writer=writer,
        process_file=process_file,
    )

    assert processed == [(FILE_ID, "/tmp/pii-entity-test")]
    assert repository.records[-1].raw_json_path is None
    assert repository.records[-1].filtered_json_path == (
        "gs://bucket/RutaGCP/run-1/filters/subdir/documento.pdf__file-1_filtrado.json"
    )
    assert repository.records[-1].accepted_entity_count == 1


def test_handle_entity_message_uses_message_run_id_for_cloud_paths():
    repository = FakeRepository()
    storage = FakeStorageClient()
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", storage)

    entity_job.handle_entity_message(
        message=pulled_message(chunks_ready_payload(run_id="cloud-run"), run_id="cloud-run"),
        config=config(expected_run_id="cloud-run"),
        repository=repository,
        detector=FakeDetector(),
        gcs_writer=writer,
        process_file=lambda *args, **kwargs: written_results(run_id="db-run"),
    )

    assert list(storage.uploads) == [
        (
            "bucket",
            "RutaGCP/cloud-run/filters/subdir/documento.pdf__file-1_filtrado.json",
        )
    ]
    assert repository.records[-1].run_id == "cloud-run"
    assert repository.records[-1].filtered_json_path == (
        "gs://bucket/RutaGCP/cloud-run/filters/subdir/documento.pdf__file-1_filtrado.json"
    )


def test_handle_entity_message_rejects_non_chunks_ready_payload():
    repository = FakeRepository()
    storage = FakeStorageClient()
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", storage)
    called = False

    def process_file(*args, **kwargs):
        nonlocal called
        called = True

    entity_job.handle_entity_message(
        message=pulled_message(chunks_ready_payload(event_type="file.routed")),
        config=config(),
        repository=repository,
        detector=FakeDetector(),
        gcs_writer=writer,
        process_file=process_file,
    )

    assert called is False
    assert repository.records == []
    assert storage.uploads == {}


def test_handle_entity_message_rejects_scope_mismatch():
    repository = FakeRepository()
    storage = FakeStorageClient()
    writer = entity_job.GcsEntityResultWriter("gs://bucket/RutaGCP", storage)

    entity_job.handle_entity_message(
        message=pulled_message(run_id="other-run"),
        config=config(),
        repository=repository,
        detector=FakeDetector(),
        gcs_writer=writer,
        process_file=lambda *args, **kwargs: written_results(),
    )

    assert repository.records == []
    assert storage.uploads == {}


def test_prepare_zero_shot_model_copies_gcs_prefix_to_local(tmp_path: Path, monkeypatch):
    storage = FakeStorageClient(
        [
            FakeDownloadBlob("models/zero/config.json", "{}"),
            FakeDownloadBlob("models/zero/tokenizer.json", "{}"),
        ]
    )
    monkeypatch.delenv("PII_ENTITY_ZERO_SHOT_MODEL", raising=False)

    local_dir = entity_job.prepare_zero_shot_model(
        config(
            zero_shot_enabled=True,
            zero_shot_model_uri="gs://bucket/models/zero",
            zero_shot_local_dir=str(tmp_path / "zero-shot"),
        ),
        storage_client=storage,
    )

    assert local_dir == str(tmp_path / "zero-shot")
    assert (tmp_path / "zero-shot" / "config.json").exists()
    assert (tmp_path / "zero-shot" / "tokenizer.json").exists()
    assert os.environ["PII_ENTITY_ZERO_SHOT_MODEL"] == str(tmp_path / "zero-shot")


def test_prepare_zero_shot_model_fails_early_without_uri():
    try:
        entity_job.prepare_zero_shot_model(config(zero_shot_enabled=True))
    except RuntimeError as exc:
        assert "PII_ENTITY_ZERO_SHOT_MODEL_URI is required" in str(exc)
    else:
        raise AssertionError("Expected missing zero-shot URI to fail")


def test_cloud_requirements_include_zero_shot_tokenizer_runtime():
    requirements = (ROOT / "requirements-cloud.txt").read_text(encoding="utf-8")

    assert "sentencepiece" in requirements
