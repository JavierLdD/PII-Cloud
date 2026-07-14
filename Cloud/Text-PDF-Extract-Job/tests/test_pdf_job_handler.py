from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT / "Text_Extract"))
sys.path.insert(0, str(PROJECT / "Cloud" / "Text-Extract-Job-Common" / "src"))
sys.path.insert(0, str(ROOT / "src"))

from cloud_text_extract_job.config import TextExtractJobConfig  # noqa: E402
from cloud_text_extract_job.outbox import PendingOutboxRecord  # noqa: E402
from cloud_text_extract_job.pubsub import PulledMessage  # noqa: E402
from cloud_text_pdf_extract_job import main as pdf_job  # noqa: E402
from common.models import (  # noqa: E402
    CHUNKS_READY_EVENT_TYPE,
    PAGE_COMPLETED_STATUS,
    PAGE_FAILED_STATUS,
    PYMUPDF_METHOD,
    QUEUE_ENTITY,
    QUEUE_PDF,
    TEXT_EXTRACTION_COMPLETED_STATUS,
    TEXT_EXTRACTION_FAILED_STATUS,
    OutboxMessage,
    PdfPageResult,
    PdfProcessingResult,
    RoutedFileMessage,
    StoredFile,
)


RUN_ID = "run-1"
USER_ID = "user-1"
FILE_ID = "file-1"
ROUTING_DECISION_ID = "route-1"


class FakeRepository:
    def __init__(self, stored_file: StoredFile):
        self.stored_file = stored_file
        self.saved = []
        self.outbox_messages = []

    def get_file(self, file_id: str):
        if file_id == self.stored_file.file_id:
            return self.stored_file
        return None

    def save_pdf_result(self, result, publish_downstream: bool):
        self.saved.append((result, publish_downstream))
        return result

    def list_pending_outbox(self, queue_name: str):
        assert queue_name == QUEUE_ENTITY
        return self.outbox_messages


class FakeOutboxRepository:
    def __init__(self):
        self.inserted = []
        self.published = []
        self.errors = []

    def insert_pending(
        self,
        *,
        run_id,
        file_id,
        queue_name,
        payload,
        idempotency_key,
        attributes,
    ):
        self.inserted.append(
            {
                "run_id": run_id,
                "file_id": file_id,
                "queue_name": queue_name,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
                "attributes": dict(attributes),
            }
        )
        return PendingOutboxRecord(
            outbox_id=f"outbox-{len(self.inserted)}",
            queue_name=queue_name,
            payload=dict(payload),
            status="pending",
        )

    def mark_published(self, outbox_id, *, pubsub_message_id=None, attributes=None):
        self.published.append((outbox_id, pubsub_message_id, attributes))

    def record_error(self, outbox_id, error):
        self.errors.append((outbox_id, error))


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish_json(self, topic_name, payload, attributes):
        self.messages.append((topic_name, dict(payload), dict(attributes)))
        return f"message-{len(self.messages)}"


class FakeMaterializer:
    def __init__(self):
        self.released = []

    def release_if_final(self, stored_file: StoredFile, status: str | None):
        self.released.append((stored_file.file_id, status))


def config() -> TextExtractJobConfig:
    return TextExtractJobConfig(
        subscription_id="subscriptions/text-pdf",
        database_url="postgresql://example",
        topic_pii_entities="projects/pii/topics/pii-entities",
        topic_text_poison="projects/pii/topics/text-poison",
        expected_user_id=USER_ID,
        expected_run_id=RUN_ID,
        per_file_timeout_seconds=0,
    )


def payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": "2.0",
        "event_type": "file.routed",
        "run_id": RUN_ID,
        "file_id": FILE_ID,
        "routing_decision_id": ROUTING_DECISION_ID,
        "source_type": "drive",
        "source_uri": "drive://file/drive-file-1",
        "external_id": "drive-file-1",
        "file_name": "sample.pdf",
        "relative_path": "sample.pdf",
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "checksum_sha256": None,
        "content_hash": None,
        "etag": "etag-1",
        "size_bytes": 123,
        "source_queue_name": "Queue-Archivos",
        "destination_queue_name": QUEUE_PDF,
        "route_type": "pdf",
        "reason": "pdf_extension",
    }
    data.update(overrides)
    return data


def stored_file() -> StoredFile:
    return StoredFile(
        file_id=FILE_ID,
        run_id=RUN_ID,
        source_type="drive",
        source_uri="drive://file/drive-file-1",
        external_id="drive-file-1",
        file_name="sample.pdf",
        relative_path="sample.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        size_bytes=123,
        checksum_sha256=None,
        content_hash=None,
        etag="etag-1",
    )


def pulled_message(message_payload: dict[str, object] | None = None) -> PulledMessage:
    return PulledMessage(
        ack_id="ack-1",
        payload=message_payload or payload(),
        attributes={"user_id": USER_ID, "run_id": RUN_ID},
    )


def page(status: str = PAGE_COMPLETED_STATUS, reason: str = "embedded_text"):
    return PdfPageResult(
        file_id=FILE_ID,
        run_id=RUN_ID,
        page_number=1,
        page_index=0,
        method=PYMUPDF_METHOD,
        status=status,
        reason=reason,
        char_count=20,
        word_count=4,
        total_image_ratio=0.0,
        largest_image_ratio=0.0,
    )


def result(status: str, *, error: str | None = None) -> PdfProcessingResult:
    now = datetime.now(UTC)
    return PdfProcessingResult(
        message=RoutedFileMessage.from_payload(payload()),
        stored_file=stored_file(),
        status=status,
        pages=[
            page(
                PAGE_FAILED_STATUS if status == TEXT_EXTRACTION_FAILED_STATUS else PAGE_COMPLETED_STATUS,
                "ocr_required_but_disabled"
                if error == "pdf_requires_ocr_but_ocr_is_disabled"
                else "embedded_text",
            )
        ],
        chunks=[],
        started_at=now,
        completed_at=now,
        processing_seconds=0.01,
        error=error,
    )


def test_pdf_job_publishes_only_current_file_chunks_ready(monkeypatch):
    repository = FakeRepository(stored_file())
    repository.outbox_messages = [
        OutboxMessage(
            outbox_id="entity-current",
            queue_name=QUEUE_ENTITY,
            payload={
                "event_type": CHUNKS_READY_EVENT_TYPE,
                "run_id": RUN_ID,
                "file_id": FILE_ID,
                "destination_queue_name": QUEUE_ENTITY,
            },
        ),
        OutboxMessage(
            outbox_id="entity-other",
            queue_name=QUEUE_ENTITY,
            payload={
                "event_type": CHUNKS_READY_EVENT_TYPE,
                "run_id": RUN_ID,
                "file_id": "file-2",
                "destination_queue_name": QUEUE_ENTITY,
            },
        ),
    ]
    outbox_repository = FakeOutboxRepository()
    publisher = FakePublisher()
    monkeypatch.setattr(
        pdf_job,
        "process_pdf_payload",
        lambda *args, **kwargs: result(TEXT_EXTRACTION_COMPLETED_STATUS),
    )

    pdf_job.handle_pdf_message(
        message=pulled_message(),
        config=config(),
        repository=repository,
        outbox_repository=outbox_repository,
        publisher=publisher,
        materializer=FakeMaterializer(),
    )

    assert publisher.messages == [
        (
            "projects/pii/topics/pii-entities",
            {
                "event_type": CHUNKS_READY_EVENT_TYPE,
                "run_id": RUN_ID,
                "file_id": FILE_ID,
                "destination_queue_name": QUEUE_ENTITY,
            },
            {
                "user_id": USER_ID,
                "run_id": RUN_ID,
                "event_type": CHUNKS_READY_EVENT_TYPE,
                "file_id": FILE_ID,
                "destination_queue_name": QUEUE_ENTITY,
            },
        )
    ]
    assert outbox_repository.published[0][0] == "entity-current"
    assert outbox_repository.inserted == []


def test_pdf_job_publishes_poison_when_pdf_requires_ocr(monkeypatch):
    repository = FakeRepository(stored_file())
    outbox_repository = FakeOutboxRepository()
    publisher = FakePublisher()
    monkeypatch.setattr(
        pdf_job,
        "process_pdf_payload",
        lambda *args, **kwargs: result(
            TEXT_EXTRACTION_FAILED_STATUS,
            error="pdf_requires_ocr_but_ocr_is_disabled",
        ),
    )

    pdf_job.handle_pdf_message(
        message=pulled_message(),
        config=config(),
        repository=repository,
        outbox_repository=outbox_repository,
        publisher=publisher,
        materializer=FakeMaterializer(),
    )

    poison = publisher.messages[0][1]
    assert publisher.messages[0][0] == "projects/pii/topics/text-poison"
    assert poison["event_type"] == "file.text_extract_poisoned"
    assert poison["reason"] == "ocr_required"
    assert poison["stage"] == "pdf"
    assert poison["source_queue_name"] == QUEUE_PDF
    assert outbox_repository.inserted[0]["queue_name"] == "Queue-Text-Poison"


def test_pdf_job_timeout_is_persisted_and_published_as_poison(monkeypatch):
    repository = FakeRepository(stored_file())
    outbox_repository = FakeOutboxRepository()
    publisher = FakePublisher()
    materializer = FakeMaterializer()

    def timeout(*args, **kwargs):
        raise pdf_job.FileProcessingTimeout("manual timeout")

    monkeypatch.setattr(pdf_job, "process_pdf_payload", timeout)

    pdf_job.handle_pdf_message(
        message=pulled_message(),
        config=config(),
        repository=repository,
        outbox_repository=outbox_repository,
        publisher=publisher,
        materializer=materializer,
    )

    saved, publish_downstream = repository.saved[0]
    assert saved.status == TEXT_EXTRACTION_FAILED_STATUS
    assert "per_file_timeout_exceeded" in (saved.error or "")
    assert publish_downstream is False
    assert materializer.released == [(FILE_ID, TEXT_EXTRACTION_FAILED_STATUS)]
    assert publisher.messages[0][0] == "projects/pii/topics/text-poison"
    assert publisher.messages[0][1]["reason"] == "per_file_timeout_exceeded"


def test_pdf_job_poison_unsupported_message_without_database_write():
    repository = FakeRepository(stored_file())
    outbox_repository = FakeOutboxRepository()
    publisher = FakePublisher()
    message_payload = payload(
        destination_queue_name="Queue-Doc",
        route_type="doc",
        extension=".txt",
        mime_type="text/plain",
        file_name="sample.txt",
        relative_path="sample.txt",
    )

    pdf_job.handle_pdf_message(
        message=pulled_message(message_payload),
        config=config(),
        repository=repository,
        outbox_repository=outbox_repository,
        publisher=publisher,
        materializer=FakeMaterializer(),
    )

    assert repository.saved == []
    assert outbox_repository.inserted == []
    assert publisher.messages[0][0] == "projects/pii/topics/text-poison"
    assert publisher.messages[0][1]["reason"] == "unsupported_message_for_pdf_job"
