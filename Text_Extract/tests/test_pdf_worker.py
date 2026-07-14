from __future__ import annotations

from dataclasses import replace
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.models import (  # noqa: E402
    BOTH_METHOD,
    CHUNKS_READY_EVENT_TYPE,
    OCR_BATCH_REQUESTED_EVENT_TYPE,
    PAGE_COMPLETED_STATUS,
    PAGE_FAILED_STATUS,
    PAGE_PENDING_OCR_STATUS,
    PDF_ATTEMPT_ACTIVE_STATUS,
    PDF_ATTEMPT_COMPLETED_STATUS,
    PDF_ATTEMPT_QUARANTINED_STATUS,
    QUEUE_ENTITY,
    QUEUE_OCR,
    QUEUE_OCR_URGENT,
    SUPPORTED_PAGE_METHODS,
    TEXT_EXTRACTION_COMPLETED_STATUS,
    TEXT_EXTRACTION_FAILED_STATUS,
    WAITING_OCR_STATUS,
    OutboxMessage,
    PdfAttemptState,
    StoredFile,
)
from materialization.models import MaterializationDeferred  # noqa: E402
from pdf import worker as pdf_worker  # noqa: E402
from pdf.page_router import route_page  # noqa: E402
from pdf.worker import process_pdf_payload, run_pdf_worker  # noqa: E402


class FakeRect:
    def __init__(self, width: float = 100.0, height: float = 100.0):
        self.width = width
        self.height = height


class FakePage:
    def __init__(
        self,
        text: str,
        blocks: list[tuple[float, float, float, float, str, int, int]] | None = None,
        image_bboxes: list[tuple[float, float, float, float]] | None = None,
    ):
        self._text = text
        self._blocks = blocks or []
        self._image_bboxes = image_bboxes or []
        self.rect = FakeRect()

    def get_text(self, mode: str, **kwargs):
        if mode == "text":
            return self._text
        if mode == "blocks":
            return self._blocks
        raise ValueError(mode)

    def get_image_info(self, xrefs: bool = False):
        assert xrefs is False
        return [{"bbox": bbox} for bbox in self._image_bboxes]


class FakeDocument:
    def __init__(self, pages: list[FakePage]):
        self._pages = pages
        self.closed = False

    def __iter__(self):
        return iter(self._pages)

    def close(self):
        self.closed = True


class FakeRepository:
    def __init__(self, stored_file: StoredFile | None = None):
        self.files = {stored_file.file_id: stored_file} if stored_file else {}
        self.saved_results = []
        self.outbox_by_key: dict[tuple[object, ...], OutboxMessage] = {}
        self.pdf_attempts: dict[str, PdfAttemptState] = {}

    def get_file(self, file_id: str) -> StoredFile | None:
        return self.files.get(file_id)

    def record_pdf_attempt_start(self, message, max_attempts: int) -> PdfAttemptState:
        now = datetime.now(UTC)
        existing = self.pdf_attempts.get(message.file_id)
        if existing is not None and existing.is_quarantined:
            return existing
        if existing is None or existing.status == PDF_ATTEMPT_COMPLETED_STATUS:
            state = PdfAttemptState(
                file_id=message.file_id,
                attempts=1,
                max_attempts=max_attempts,
                status=PDF_ATTEMPT_ACTIVE_STATUS,
                first_attempt_at=now,
                last_attempt_at=now,
            )
        else:
            state = replace(
                existing,
                attempts=existing.attempts + 1,
                max_attempts=max_attempts,
                status=PDF_ATTEMPT_ACTIVE_STATUS,
                last_attempt_at=now,
            )
        self.pdf_attempts[message.file_id] = state
        return state

    def record_pdf_attempt_error(
        self,
        file_id: str,
        error_type: str,
        error_message: str,
        error_traceback: str,
    ) -> PdfAttemptState:
        existing = self.pdf_attempts[file_id]
        state = replace(
            existing,
            last_error_at=datetime.now(UTC),
            last_error_type=error_type,
            last_error_message=error_message,
            last_error_traceback=error_traceback,
        )
        self.pdf_attempts[file_id] = state
        return state

    def record_pdf_attempt_completed(self, file_id: str, result_status: str) -> None:
        self.pdf_attempts[file_id] = replace(
            self.pdf_attempts[file_id],
            status=PDF_ATTEMPT_COMPLETED_STATUS,
            last_result_status=result_status,
        )

    def record_pdf_attempt_quarantined(self, file_id: str) -> PdfAttemptState:
        state = replace(
            self.pdf_attempts[file_id],
            status=PDF_ATTEMPT_QUARANTINED_STATUS,
            quarantined_at=datetime.now(UTC),
        )
        self.pdf_attempts[file_id] = state
        return state

    def save_pdf_result(self, result, publish_downstream: bool):
        pages = []
        entity_outbox_id = None

        if publish_downstream:
            ocr_pages = [page for page in result.pages if page.needs_ocr]
            batch_message = None
            batch_requested_at = None
            if ocr_pages:
                key = ("ocr_batch", result.message.file_id)
                batch_message = self.outbox_by_key.get(key)
                if batch_message is None:
                    batch_requested_at = datetime.now(UTC)
                    batch_message = OutboxMessage(
                        outbox_id="ocr-batch-1",
                        queue_name=QUEUE_OCR_URGENT,
                        payload={
                            "event_type": OCR_BATCH_REQUESTED_EVENT_TYPE,
                            "file_id": result.message.file_id,
                            "destination_queue_name": QUEUE_OCR_URGENT,
                            "ocr_requested_at": batch_requested_at.isoformat(),
                            "pages": [
                                {
                                    "page_number": page.page_number,
                                    "page_index": page.page_index,
                                }
                                for page in ocr_pages
                            ],
                        },
                    )
                    self.outbox_by_key[key] = batch_message
                else:
                    batch_requested_at = datetime.fromisoformat(
                        str(batch_message.payload["ocr_requested_at"])
                    )

            for page in result.pages:
                if page.needs_ocr:
                    pages.append(
                        page.with_ocr_outbox_id(
                            batch_message.outbox_id if batch_message else None,
                            batch_requested_at,
                        )
                    )
                else:
                    pages.append(page)

            if result.is_ready_for_entity:
                key = ("entity", result.message.file_id)
                message = self.outbox_by_key.get(key)
                if message is None:
                    message = OutboxMessage(
                        outbox_id="entity-1",
                        queue_name=QUEUE_ENTITY,
                        payload={
                            "event_type": CHUNKS_READY_EVENT_TYPE,
                            "file_id": result.message.file_id,
                            "chunk_count": result.chunk_count,
                        },
                    )
                    self.outbox_by_key[key] = message
                entity_outbox_id = message.outbox_id
        else:
            pages = result.pages

        saved = result.with_outbox_ids(
            pages=pages,
            entity_outbox_id=entity_outbox_id,
        )
        self.saved_results.append(saved)
        return saved

    def list_pending_outbox(self, queue_name: str) -> list[OutboxMessage]:
        return [
            message
            for message in self.outbox_by_key.values()
            if message.queue_name == queue_name
        ]

    def mark_outbox_published(self, outbox_id: str) -> None:
        pass

    def record_outbox_error(self, outbox_id: str, error: str) -> None:
        pass


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, queue_name: str, payload: dict[str, object]) -> None:
        self.messages.append((queue_name, payload))


class FakeConsumer:
    def __init__(self, payloads: list[dict[str, object]]):
        self.payloads = payloads
        self.calls = []

    def consume(
        self,
        queue_name: str,
        handle_payload,
        max_messages: int | None = None,
        requeue_messages: bool = False,
    ) -> None:
        self.calls.append(
            {
                "queue_name": queue_name,
                "max_messages": max_messages,
                "requeue_messages": requeue_messages,
            }
        )
        payloads = (
            self.payloads[:max_messages]
            if max_messages is not None
            else self.payloads
        )
        for payload in payloads:
            handle_payload(payload)


class FakeMaterializer:
    def __init__(
        self,
        materialized_path: Path | None = None,
        defer: bool = False,
    ) -> None:
        self.materialized_path = materialized_path
        self.defer = defer
        self.materialized = []
        self.released = []

    def materialize(self, stored_file: StoredFile):
        if self.defer:
            raise MaterializationDeferred("small_budget_unavailable")
        self.materialized.append(stored_file.file_id)
        return SimpleNamespace(
            stored_file=stored_file.with_materialized_path(str(self.materialized_path))
        )

    def release_if_final(self, stored_file: StoredFile, status: str | None) -> None:
        if status in {TEXT_EXTRACTION_COMPLETED_STATUS, TEXT_EXTRACTION_FAILED_STATUS}:
            self.released.append((stored_file.file_id, status))


def make_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "2.0",
        "event_type": "file.routed",
        "run_id": "11111111-1111-1111-1111-111111111111",
        "file_id": "22222222-2222-2222-2222-222222222222",
        "routing_decision_id": "33333333-3333-3333-3333-333333333333",
        "source_type": "local",
        "source_uri": "local:///tmp/boleta.pdf",
        "external_id": "/tmp/boleta.pdf",
        "file_name": "boleta.pdf",
        "relative_path": "boleta.pdf",
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "checksum_sha256": "a" * 64,
        "content_hash": None,
        "etag": None,
        "size_bytes": 123,
        "source_queue_name": "Queue-Archivos",
        "destination_queue_name": "Queue-PDF",
        "route_type": "pdf",
        "reason": "pdf_extension",
    }
    payload.update(overrides)
    return payload


def make_stored_file(payload: dict[str, object] | None = None) -> StoredFile:
    payload = payload or make_payload()
    return StoredFile(
        file_id=str(payload["file_id"]),
        run_id=str(payload["run_id"]),
        source_type=str(payload["source_type"]),
        source_uri=str(payload["source_uri"]),
        external_id=str(payload["external_id"]),
        file_name=str(payload["file_name"]),
        relative_path=str(payload["relative_path"]),
        extension=str(payload["extension"]),
        mime_type=str(payload["mime_type"]),
        size_bytes=int(payload["size_bytes"]) if payload["size_bytes"] is not None else None,
        checksum_sha256=str(payload["checksum_sha256"]),
        content_hash=None,
        etag=None,
    )


def patch_fitz(monkeypatch, document_factory):
    monkeypatch.setitem(
        sys.modules,
        "fitz",
        SimpleNamespace(open=lambda path: document_factory()),
    )


def test_route_page_uses_pymupdf_when_embedded_text_is_enough():
    page = FakePage(" ".join(["palabra"] * 12))

    decision = route_page(1, page)

    assert decision.method == "pymupdf"
    assert decision.reason == "embedded_text"
    assert decision.word_count == 12


def test_route_page_uses_ocr_when_text_is_insufficient():
    page = FakePage("folio")

    decision = route_page(1, page)

    assert decision.method == "ocr"
    assert decision.reason == "insufficient_text"


def test_route_page_uses_ocr_when_image_is_dominant():
    page = FakePage(
        " ".join(["palabra"] * 20),
        image_bboxes=[(0, 0, 100, 60)],
    )

    decision = route_page(1, page)

    assert decision.method == "ocr"
    assert decision.reason == "image_dominant"
    assert decision.largest_image_ratio == 0.60


def test_both_method_is_reserved_but_not_emitted_by_v1():
    page = FakePage(" ".join(["palabra"] * 20))

    decision = route_page(1, page)

    assert BOTH_METHOD in SUPPORTED_PAGE_METHODS
    assert decision.method != BOTH_METHOD


def test_pdf_worker_publishes_file_chunks_ready_when_all_pages_use_pymupdf(monkeypatch):
    def document_factory():
        return FakeDocument(
            [
                FakePage(
                    text=" ".join(["palabra"] * 12),
                    blocks=[
                        (
                            1,
                            2,
                            3,
                            4,
                            "Texto embebido con RUN 19.915.845-7",
                            0,
                            0,
                        )
                    ],
                )
            ]
        )

    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file())

    result = process_pdf_payload(make_payload(), repository)

    assert result.status == TEXT_EXTRACTION_COMPLETED_STATUS
    assert result.total_pages == 1
    assert result.chunk_count == 1
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.completed_at >= result.started_at
    assert result.processing_seconds is not None
    assert result.processing_seconds >= 0
    assert result.embedded_text_seconds >= 0
    assert result.ocr_queue_wait_seconds == 0.0
    assert result.ocr_processing_seconds == 0.0
    assert result.cpu_user_seconds >= 0
    assert result.cpu_system_seconds >= 0
    assert result.cpu_total_seconds >= 0
    assert result.peak_memory_mb >= 0
    assert result.pages[0].embedded_started_at is not None
    assert result.pages[0].embedded_completed_at is not None
    assert result.pages[0].embedded_processing_seconds is not None
    assert result.pages[0].cpu_total_seconds is not None
    assert result.pages[0].peak_memory_mb is not None
    assert result.entity_outbox_id == "entity-1"
    assert result.pages[0].status == PAGE_COMPLETED_STATUS
    assert result.pages[0].chunk_count == 1
    assert len(repository.list_pending_outbox(QUEUE_ENTITY)) == 1
    assert repository.list_pending_outbox(QUEUE_OCR_URGENT) == []
    assert repository.list_pending_outbox(QUEUE_OCR) == []
    segment = result.chunks[0].source_map["segments"][0]
    assert segment["page_number"] == 1
    assert segment["bbox"] == [1.0, 2.0, 3.0, 4.0]


def test_pdf_worker_accepts_pdf_mime_when_extension_is_missing(monkeypatch):
    def document_factory():
        return FakeDocument(
            [
                FakePage(
                    text=" ".join(["palabra"] * 12),
                    blocks=[(1, 2, 3, 4, "Texto embebido", 0, 0)],
                )
            ]
        )

    payload = make_payload(
        file_name="boleta",
        relative_path="boleta",
        extension="",
        reason="pdf_mime",
    )
    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file(payload))

    result = process_pdf_payload(payload, repository)

    assert result.status == TEXT_EXTRACTION_COMPLETED_STATUS
    assert result.chunk_count == 1


def test_pdf_worker_leaves_mixed_pdf_waiting_for_ocr(monkeypatch):
    def document_factory():
        return FakeDocument(
            [
                FakePage(
                    text=" ".join(["palabra"] * 12),
                    blocks=[
                        (
                            1,
                            2,
                            3,
                            4,
                            "Texto pagina uno",
                            0,
                            0,
                        )
                    ],
                ),
                FakePage(text=""),
            ]
        )

    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file())

    result = process_pdf_payload(make_payload(), repository)

    assert result.status == WAITING_OCR_STATUS
    assert result.total_pages == 2
    assert result.chunk_count == 1
    assert result.started_at is not None
    assert result.completed_at is None
    assert result.processing_seconds is None
    assert result.entity_outbox_id is None
    assert [page.status for page in result.pages] == [
        PAGE_COMPLETED_STATUS,
        PAGE_PENDING_OCR_STATUS,
    ]
    assert result.embedded_text_seconds >= 0
    assert result.pages[0].embedded_processing_seconds is not None
    assert result.pages[0].cpu_total_seconds is not None
    assert result.pages[1].ocr_outbox_id == "ocr-batch-1"
    assert result.pages[1].ocr_requested_at is not None
    assert len(repository.list_pending_outbox(QUEUE_OCR_URGENT)) == 1
    urgent_payload = repository.list_pending_outbox(QUEUE_OCR_URGENT)[0].payload
    assert urgent_payload["event_type"] == OCR_BATCH_REQUESTED_EVENT_TYPE
    assert urgent_payload["destination_queue_name"] == QUEUE_OCR_URGENT
    assert urgent_payload["ocr_requested_at"]
    assert urgent_payload["pages"] == [{"page_number": 2, "page_index": 1}]
    assert repository.list_pending_outbox(QUEUE_OCR) == []
    assert repository.list_pending_outbox(QUEUE_ENTITY) == []


def test_pdf_worker_marks_corrupt_pdf_failed_without_downstream(monkeypatch):
    def fail_open(path: str) -> Any:
        raise ValueError("bad pdf")

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=fail_open))
    repository = FakeRepository(make_stored_file())

    result = process_pdf_payload(make_payload(), repository)

    assert result.status == TEXT_EXTRACTION_FAILED_STATUS
    assert result.error == "bad pdf"
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.completed_at >= result.started_at
    assert result.processing_seconds is not None
    assert result.processing_seconds >= 0
    assert result.chunks == []
    assert repository.list_pending_outbox(QUEUE_OCR_URGENT) == []
    assert repository.list_pending_outbox(QUEUE_OCR) == []
    assert repository.list_pending_outbox(QUEUE_ENTITY) == []


def test_pdf_worker_reprocess_does_not_duplicate_outbox(monkeypatch):
    def document_factory():
        return FakeDocument([FakePage(text="")])

    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file())

    first = process_pdf_payload(make_payload(), repository)
    second = process_pdf_payload(make_payload(), repository)

    assert first.pages[0].ocr_outbox_id == "ocr-batch-1"
    assert second.pages[0].ocr_outbox_id == "ocr-batch-1"
    assert len(repository.list_pending_outbox(QUEUE_OCR_URGENT)) == 1
    assert repository.list_pending_outbox(QUEUE_OCR) == []


def test_dev_mode_does_not_create_downstream_outbox(monkeypatch):
    def document_factory():
        return FakeDocument([FakePage(text="")])

    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file())

    result = process_pdf_payload(
        make_payload(),
        repository,
        publish_downstream=False,
    )

    assert result.status == WAITING_OCR_STATUS
    assert result.pages[0].ocr_outbox_id is None
    assert repository.list_pending_outbox(QUEUE_OCR_URGENT) == []
    assert repository.list_pending_outbox(QUEUE_OCR) == []


def test_remote_pdf_completed_releases_materialization(monkeypatch, tmp_path: Path):
    def document_factory():
        return FakeDocument(
            [
                FakePage(
                    text=" ".join(["palabra"] * 12),
                    blocks=[(1, 2, 3, 4, "Texto embebido", 0, 0)],
                )
            ]
        )

    materialized_path = tmp_path / "remote.pdf"
    materialized_path.write_bytes(b"%PDF")
    payload = make_payload(
        source_type="drive",
        source_uri="drive://file/pdf-1",
        external_id="pdf-1",
    )
    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file(payload))
    materializer = FakeMaterializer(materialized_path)

    result = process_pdf_payload(payload, repository, materializer=materializer)

    assert result.status == TEXT_EXTRACTION_COMPLETED_STATUS
    assert materializer.materialized == [str(payload["file_id"])]
    assert materializer.released == [
        (str(payload["file_id"]), TEXT_EXTRACTION_COMPLETED_STATUS)
    ]
    assert result.stored_file.source_uri == "drive://file/pdf-1"
    assert result.chunks[0].source_map["source_uri"] == "drive://file/pdf-1"
    assert result.chunks[0].source_map["original_path"] == "drive://file/pdf-1"


def test_remote_pdf_waiting_ocr_keeps_materialization(monkeypatch, tmp_path: Path):
    def document_factory():
        return FakeDocument([FakePage(text="")])

    materialized_path = tmp_path / "remote.pdf"
    materialized_path.write_bytes(b"%PDF")
    payload = make_payload(
        source_type="drive",
        source_uri="drive://file/pdf-1",
        external_id="pdf-1",
    )
    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file(payload))
    materializer = FakeMaterializer(materialized_path)

    result = process_pdf_payload(payload, repository, materializer=materializer)

    assert result.status == WAITING_OCR_STATUS
    assert materializer.released == []


def test_cloud_pdf_ocr_policy_marks_ocr_required_as_failed_and_releases_temp(
    monkeypatch,
    tmp_path: Path,
):
    def document_factory():
        return FakeDocument([FakePage(text="")])

    materialized_path = tmp_path / "remote.pdf"
    materialized_path.write_bytes(b"%PDF")
    payload = make_payload(
        source_type="drive",
        source_uri="drive://file/pdf-1",
        external_id="pdf-1",
    )
    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file(payload))
    materializer = FakeMaterializer(materialized_path)

    result = process_pdf_payload(
        payload,
        repository,
        materializer=materializer,
        ocr_policy=pdf_worker.OCR_POLICY_POISON,
    )

    assert result.status == TEXT_EXTRACTION_FAILED_STATUS
    assert result.error == "pdf_requires_ocr_but_ocr_is_disabled"
    assert result.pages[0].status == PAGE_FAILED_STATUS
    assert result.pages[0].reason == "ocr_required_but_disabled"
    assert result.chunks == []
    assert repository.list_pending_outbox(QUEUE_OCR_URGENT) == []
    assert repository.list_pending_outbox(QUEUE_ENTITY) == []
    assert materializer.released == [
        (str(payload["file_id"]), TEXT_EXTRACTION_FAILED_STATUS)
    ]


def test_materialization_deferred_requeues_without_saving(monkeypatch, tmp_path: Path):
    def document_factory():
        return FakeDocument([FakePage(text="")])

    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file())
    materializer = FakeMaterializer(tmp_path / "unused.pdf", defer=True)

    try:
        process_pdf_payload(make_payload(), repository, materializer=materializer)
    except MaterializationDeferred as exc:
        assert "small_budget_unavailable" in str(exc)
    else:
        raise AssertionError("expected MaterializationDeferred")

    assert repository.saved_results == []


def test_pdf_worker_does_not_quarantine_materialization_deferred(
    monkeypatch,
    tmp_path: Path,
):
    def document_factory():
        return FakeDocument([FakePage(text="")])

    patch_fitz(monkeypatch, document_factory)
    repository = FakeRepository(make_stored_file())
    materializer = FakeMaterializer(tmp_path / "unused.pdf", defer=True)
    log_path = tmp_path / "pdf_quarantine.jsonl"

    for _ in range(2):
        try:
            run_pdf_worker(
                repository=repository,
                publisher=FakePublisher(),
                consumer=FakeConsumer([make_payload()]),
                max_messages=1,
                max_attempts=1,
                quarantine_log_path=log_path,
                materializer=materializer,
            )
        except MaterializationDeferred:
            pass
        else:
            raise AssertionError("expected MaterializationDeferred")

    state = repository.pdf_attempts[str(make_payload()["file_id"])]
    assert state.status == PDF_ATTEMPT_COMPLETED_STATUS
    assert state.last_result_status == "materialization_deferred"
    assert state.attempts == 1
    assert repository.saved_results == []
    assert not log_path.exists()


def test_pdf_worker_records_real_error_before_retrying(monkeypatch, tmp_path, capsys):
    def fail_extract(**kwargs):
        raise RuntimeError("PyMuPDF exploded")

    monkeypatch.setattr(pdf_worker, "extract_pdf_document", fail_extract)
    repository = FakeRepository(make_stored_file())
    consumer = FakeConsumer([make_payload()])

    try:
        run_pdf_worker(
            repository=repository,
            publisher=FakePublisher(),
            consumer=consumer,
            max_messages=1,
            max_attempts=2,
            quarantine_log_path=tmp_path / "pdf_quarantine.jsonl",
        )
    except RuntimeError as exc:
        assert "PyMuPDF exploded" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    state = repository.pdf_attempts[str(make_payload()["file_id"])]
    assert state.attempts == 1
    assert state.last_error_type == "RuntimeError"
    assert state.last_error_message == "PyMuPDF exploded"
    assert "RuntimeError: PyMuPDF exploded" in (state.last_error_traceback or "")
    assert repository.saved_results == []
    assert not (tmp_path / "pdf_quarantine.jsonl").exists()
    assert "ERROR pdf_processing_failed" in capsys.readouterr().err


def test_pdf_worker_quarantines_after_retry_limit(monkeypatch, tmp_path, capsys):
    def fail_extract(**kwargs):
        raise RuntimeError("PyMuPDF exploded")

    monkeypatch.setattr(pdf_worker, "extract_pdf_document", fail_extract)
    payload = make_payload(
        source_type="drive",
        source_uri="drive://file/pdf-1",
        external_id="pdf-1",
        relative_path="Drive/CVEU.pdf",
    )
    repository = FakeRepository(make_stored_file(payload))
    log_path = tmp_path / "pdf_quarantine.jsonl"

    try:
        run_pdf_worker(
            repository=repository,
            publisher=FakePublisher(),
            consumer=FakeConsumer([payload]),
            max_messages=1,
            max_attempts=2,
            quarantine_log_path=log_path,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected first attempt to be requeued")

    run_pdf_worker(
        repository=repository,
        publisher=FakePublisher(),
        consumer=FakeConsumer([payload]),
        max_messages=1,
        max_attempts=2,
        quarantine_log_path=log_path,
    )

    state = repository.pdf_attempts[str(payload["file_id"])]
    assert state.status == PDF_ATTEMPT_QUARANTINED_STATUS
    assert state.attempts == 2
    assert len(repository.saved_results) == 1
    saved = repository.saved_results[0]
    assert saved.status == TEXT_EXTRACTION_FAILED_STATUS
    assert "pdf_quarantined reason=max_attempts_exhausted_after_error" in (
        saved.error or ""
    )
    assert "PyMuPDF exploded" in (saved.error or "")

    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["file_id"] == payload["file_id"]
    assert records[0]["source_uri"] == "drive://file/pdf-1"
    assert records[0]["external_id"] == "pdf-1"
    assert records[0]["relative_path"] == "Drive/CVEU.pdf"
    assert records[0]["attempts"] == 2
    assert records[0]["last_error_type"] == "RuntimeError"
    assert records[0]["last_error_message"] == "PyMuPDF exploded"
    assert "RuntimeError: PyMuPDF exploded" in records[0]["last_error_traceback"]
    output = capsys.readouterr()
    assert "ERROR pdf_processing_failed" in output.err
    assert "pdf_quarantined" in output.out
