from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.models import (  # noqa: E402
    CHUNKS_READY_EVENT_TYPE,
    OCR_BATCH_REQUESTED_EVENT_TYPE,
    OCR_METHOD,
    PAGE_COMPLETED_STATUS,
    PAGE_FAILED_STATUS,
    PAGE_PENDING_OCR_STATUS,
    PYMUPDF_METHOD,
    QUEUE_ENTITY,
    QUEUE_OCR,
    QUEUE_OCR_URGENT,
    TEXT_EXTRACTION_COMPLETED_STATUS,
    TEXT_EXTRACTION_FAILED_STATUS,
    WAITING_OCR_STATUS,
    OcrWorkMessage,
    OutboxMessage,
    PdfPageResult,
    StoredFile,
    TextChunk,
)
from ocr.mineru import MinerUExecutionError  # noqa: E402
from ocr.worker import process_ocr_payload, run_ocr_worker  # noqa: E402


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
        payloads = self.payloads[:max_messages] if max_messages is not None else self.payloads
        for payload in payloads:
            handle_payload(payload)

    def consume_in_priority_order(
        self,
        queue_names,
        handle_payload,
        max_messages: int | None = None,
        requeue_messages: bool = False,
    ) -> None:
        self.calls.append(
            {
                "queue_names": tuple(queue_names),
                "max_messages": max_messages,
                "requeue_messages": requeue_messages,
            }
        )
        payloads = self.payloads[:max_messages] if max_messages is not None else self.payloads
        for payload in payloads:
            handle_payload(payload)


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, queue_name: str, payload: dict[str, object]) -> None:
        self.messages.append((queue_name, payload))


class FakeRepository:
    def __init__(
        self,
        stored_file: StoredFile,
        pages: list[PdfPageResult] | None = None,
        chunks: list[TextChunk] | None = None,
        total_pages: int | None = None,
    ):
        self.files = {stored_file.file_id: stored_file}
        self.pages = {page.page_number: page for page in pages or []}
        self.chunks = chunks or []
        self.total_pages = total_pages or max([*self.pages.keys(), 1])
        self.outbox_by_key: dict[tuple[object, ...], OutboxMessage] = {}
        self.saved_results = []
        self.batch_metrics = []

    def get_file(self, file_id: str) -> StoredFile | None:
        return self.files.get(file_id)

    def save_ocr_result(self, result, publish_downstream: bool):
        page = result.page
        existing_page = self.pages.get(page.page_number)
        page = page.with_ocr_outbox_id(
            existing_page.ocr_outbox_id if existing_page else page.ocr_outbox_id
        )
        self.pages[page.page_number] = page
        self.chunks = [
            chunk
            for chunk in self.chunks
            if not (
                chunk.page_start == page.page_number
                and chunk.page_end == page.page_number
            )
        ]
        if page.status == PAGE_COMPLETED_STATUS:
            self.chunks.extend(result.chunks)
        self.chunks = self._reindex_chunks(self.chunks)

        completed_pages = sum(
            1 for item in self.pages.values() if item.status == PAGE_COMPLETED_STATUS
        )
        pending_ocr_pages = sum(
            1 for item in self.pages.values() if item.status == PAGE_PENDING_OCR_STATUS
        )
        failed_pages = sum(
            1 for item in self.pages.values() if item.status == PAGE_FAILED_STATUS
        )
        if failed_pages:
            file_status = TEXT_EXTRACTION_FAILED_STATUS
        elif pending_ocr_pages:
            file_status = WAITING_OCR_STATUS
        else:
            file_status = TEXT_EXTRACTION_COMPLETED_STATUS

        entity_outbox_id = None
        if file_status == TEXT_EXTRACTION_COMPLETED_STATUS and publish_downstream:
            key = ("entity", result.message.file_id)
            message = self.outbox_by_key.get(key)
            if message is None:
                message = OutboxMessage(
                    outbox_id="entity-1",
                    queue_name=QUEUE_ENTITY,
                    payload={
                        "event_type": CHUNKS_READY_EVENT_TYPE,
                        "file_id": result.message.file_id,
                        "source_queue_name": result.message.destination_queue_name,
                        "chunk_count": len(self.chunks),
                    },
                )
                self.outbox_by_key[key] = message
            entity_outbox_id = message.outbox_id

        saved = result.with_persisted_state(
            file_status=file_status,
            total_pages=self.total_pages,
            completed_pages=completed_pages,
            pending_ocr_pages=pending_ocr_pages,
            failed_pages=failed_pages,
            file_chunk_count=len(self.chunks),
            entity_outbox_id=entity_outbox_id,
        )
        self.saved_results.append(saved)
        return saved

    def save_ocr_batch_metrics(self, metrics):
        self.batch_metrics.append(metrics)

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

    def _reindex_chunks(self, chunks: list[TextChunk]) -> list[TextChunk]:
        ordered = sorted(chunks, key=lambda item: (item.page_start, item.chunk_index))
        return [
            replace(
                chunk,
                chunk_id=f"{chunk.file_id}:c{index:06d}",
                chunk_index=index,
            )
            for index, chunk in enumerate(ordered, start=1)
        ]


class FakeMaterializer:
    def __init__(self, materialized_path: Path) -> None:
        self.materialized_path = materialized_path
        self.materialized = []
        self.released = []

    def materialize(self, stored_file: StoredFile):
        self.materialized.append(stored_file.file_id)
        return SimpleNamespace(
            stored_file=stored_file.with_materialized_path(str(self.materialized_path))
        )

    def release_if_final(self, stored_file: StoredFile, status: str | None) -> None:
        if status in {TEXT_EXTRACTION_COMPLETED_STATUS, TEXT_EXTRACTION_FAILED_STATUS}:
            self.released.append((stored_file.file_id, status))


def make_stored_file(extension: str = ".pdf") -> StoredFile:
    return StoredFile(
        file_id="22222222-2222-2222-2222-222222222222",
        run_id="11111111-1111-1111-1111-111111111111",
        source_type="local",
        source_uri=f"local:///tmp/sample{extension}",
        external_id=f"/tmp/sample{extension}",
        file_name=f"sample{extension}",
        relative_path=f"sample{extension}",
        extension=extension,
        mime_type="application/pdf" if extension == ".pdf" else "image/png",
        size_bytes=123,
        checksum_sha256="a" * 64,
        content_hash=None,
        etag=None,
    )


def make_pdf_ocr_payload(
    page_number: int = 2,
    destination_queue_name: str = QUEUE_OCR_URGENT,
) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "event_type": "pdf.page_ocr_requested",
        "run_id": "11111111-1111-1111-1111-111111111111",
        "file_id": "22222222-2222-2222-2222-222222222222",
        "routing_decision_id": "33333333-3333-3333-3333-333333333333",
        "source_type": "local",
        "source_uri": "local:///tmp/sample.pdf",
        "external_id": "/tmp/sample.pdf",
        "file_name": "sample.pdf",
        "relative_path": "sample.pdf",
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "checksum_sha256": "a" * 64,
        "content_hash": None,
        "etag": None,
        "size_bytes": 123,
        "source_queue_name": "Queue-PDF",
        "destination_queue_name": destination_queue_name,
        "route_type": "ocr",
        "reason": "insufficient_text",
        "page_number": page_number,
        "page_index": page_number - 1,
        "page_method": "ocr",
        "routing_char_count": 0,
        "routing_word_count": 0,
        "routing_image_ratio": 1.0,
        "routing_largest_image_ratio": 1.0,
        "ocr_requested_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    }


def make_pdf_ocr_batch_payload(page_numbers: list[int]) -> dict[str, object]:
    first_page = page_numbers[0]
    payload = make_pdf_ocr_payload(page_number=first_page)
    payload.update(
        {
            "event_type": OCR_BATCH_REQUESTED_EVENT_TYPE,
            "reason": "pdf_ocr_batch",
            "total_pages": len(page_numbers),
            "pages": [
                {
                    "page_number": page_number,
                    "page_index": page_number - 1,
                    "reason": "insufficient_text",
                    "page_method": "ocr",
                    "routing_char_count": 0,
                    "routing_word_count": 0,
                    "routing_image_ratio": 1.0,
                    "routing_largest_image_ratio": 1.0,
                    "ocr_requested_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                }
                for page_number in page_numbers
            ],
        }
    )
    return payload


def make_image_payload() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "event_type": "file.routed",
        "run_id": "11111111-1111-1111-1111-111111111111",
        "file_id": "22222222-2222-2222-2222-222222222222",
        "routing_decision_id": "33333333-3333-3333-3333-333333333333",
        "source_type": "local",
        "source_uri": "local:///tmp/sample.png",
        "external_id": "/tmp/sample.png",
        "file_name": "sample.png",
        "relative_path": "sample.png",
        "extension": ".png",
        "mime_type": "image/png",
        "checksum_sha256": "a" * 64,
        "content_hash": None,
        "etag": None,
        "size_bytes": 123,
        "source_queue_name": "Queue-Archivos",
        "destination_queue_name": QUEUE_OCR,
        "route_type": "ocr",
        "reason": "image_extension",
    }


def make_page(
    page_number: int,
    status: str,
    method: str = OCR_METHOD,
    chunk_count: int = 0,
) -> PdfPageResult:
    return PdfPageResult(
        file_id="22222222-2222-2222-2222-222222222222",
        run_id="11111111-1111-1111-1111-111111111111",
        page_number=page_number,
        page_index=page_number - 1,
        method=method,
        status=status,
        reason="embedded_text" if method == PYMUPDF_METHOD else "insufficient_text",
        char_count=100 if status == PAGE_COMPLETED_STATUS else 0,
        word_count=20 if status == PAGE_COMPLETED_STATUS else 0,
        total_image_ratio=0.0,
        largest_image_ratio=0.0,
        chunk_count=chunk_count,
        ocr_outbox_id=f"ocr-{page_number}" if status == PAGE_PENDING_OCR_STATUS else None,
        ocr_requested_at=(
            datetime(2026, 1, 1, tzinfo=UTC)
            if status == PAGE_PENDING_OCR_STATUS
            else None
        ),
    )


def make_existing_chunk(page_number: int) -> TextChunk:
    return TextChunk(
        chunk_id="old",
        run_id="11111111-1111-1111-1111-111111111111",
        file_id="22222222-2222-2222-2222-222222222222",
        chunk_index=1,
        page_start=page_number,
        page_end=page_number,
        text=f"Texto pagina {page_number}",
        text_hash_sha256="b" * 64,
        source_map={"page_start": page_number, "page_end": page_number, "segments": []},
        method=PYMUPDF_METHOD,
    )


def test_ocr_work_message_accepts_urgent_pdf_queue():
    message = OcrWorkMessage.from_payload(make_pdf_ocr_payload(page_number=2))

    assert message.destination_queue_name == QUEUE_OCR_URGENT
    assert message.is_pdf_page is True
    assert message.is_pdf_batch is False


def test_ocr_work_message_accepts_legacy_normal_pdf_queue():
    message = OcrWorkMessage.from_payload(
        make_pdf_ocr_payload(
            page_number=2,
            destination_queue_name=QUEUE_OCR,
        )
    )

    assert message.destination_queue_name == QUEUE_OCR
    assert message.is_pdf_page is True


def test_ocr_work_message_accepts_pdf_batch_queue():
    message = OcrWorkMessage.from_payload(make_pdf_ocr_batch_payload([2, 3]))

    assert message.destination_queue_name == QUEUE_OCR_URGENT
    assert message.is_pdf_page is True
    assert message.is_pdf_batch is True
    assert [page.page_number for page in message.pages] == [2, 3]
    assert message.page_number == 2


def patch_mineru_success(monkeypatch, text: str = "OCR Rut: 12378895-8"):
    import ocr.extractor as extractor

    def fake_run_pdf_page(
        pdf_path,
        page_index,
        output_dir,
        timeout_seconds,
        device="auto",
        config=None,
    ):
        write_mineru_payload(output_dir, text)

    def fake_run_pdf_range(
        pdf_path,
        start_page_index,
        end_page_index,
        output_dir,
        timeout_seconds,
        device="auto",
        config=None,
    ):
        write_mineru_payload(
            output_dir,
            text,
            page_count=end_page_index - start_page_index + 1,
        )

    def fake_run_image(
        image_path,
        output_dir,
        timeout_seconds,
        device="auto",
        config=None,
    ):
        write_mineru_payload(output_dir, text)

    monkeypatch.setattr(extractor, "run_mineru_pdf_page", fake_run_pdf_page)
    monkeypatch.setattr(extractor, "run_mineru_pdf_range", fake_run_pdf_range)
    monkeypatch.setattr(extractor, "run_mineru_image", fake_run_image)


def patch_mineru_failure(monkeypatch):
    import ocr.extractor as extractor

    def fail(*args, **kwargs):
        raise MinerUExecutionError("mineru exploded")

    monkeypatch.setattr(extractor, "run_mineru_pdf_page", fail)
    monkeypatch.setattr(extractor, "run_mineru_pdf_range", fail)
    monkeypatch.setattr(extractor, "run_mineru_image", fail)


def write_mineru_payload(output_dir: Path, text: str, page_count: int = 1) -> None:
    def block(page_offset: int) -> dict[str, object]:
        page_text = text if page_count == 1 else f"{text} page {page_offset + 1}"
        return {
            "type": "paragraph",
            "content": {
                "paragraph_content": [{"type": "text", "content": page_text}]
            },
            "bbox": [1, 2, 3, 4],
        }

    payload = [
        [block(page_offset)]
        for page_offset in range(page_count)
    ]
    (output_dir / "sample_content_list_v2.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_pdf_ocr_page_completes_without_entity_when_other_pages_are_pending(monkeypatch):
    patch_mineru_success(monkeypatch)
    repository = FakeRepository(
        make_stored_file(),
        pages=[
            make_page(1, PAGE_COMPLETED_STATUS, method=PYMUPDF_METHOD, chunk_count=1),
            make_page(2, PAGE_PENDING_OCR_STATUS),
            make_page(3, PAGE_PENDING_OCR_STATUS),
        ],
        chunks=[make_existing_chunk(1)],
        total_pages=3,
    )

    result = process_ocr_payload(make_pdf_ocr_payload(page_number=2), repository)

    assert result.page.status == PAGE_COMPLETED_STATUS
    assert result.page.ocr_requested_at is not None
    assert result.page.ocr_started_at is not None
    assert result.page.ocr_completed_at is not None
    assert result.page.ocr_queue_wait_seconds is not None
    assert result.page.ocr_queue_wait_seconds >= 0
    assert result.page.ocr_processing_seconds is not None
    assert result.page.ocr_processing_seconds >= 0
    assert result.page.cpu_user_seconds is not None
    assert result.page.cpu_system_seconds is not None
    assert result.page.cpu_total_seconds is not None
    assert result.page.cpu_total_seconds >= 0
    assert result.page.peak_memory_mb is not None
    assert result.file_status == WAITING_OCR_STATUS
    assert result.entity_outbox_id is None
    assert result.file_chunk_count == 2
    assert [chunk.page_start for chunk in repository.chunks] == [1, 2]
    assert repository.chunks[1].method == OCR_METHOD
    segment = repository.chunks[1].source_map["segments"][0]
    assert segment["bbox"] == [1.0, 2.0, 3.0, 4.0]
    assert segment["metadata"]["ocr_engine"] == "mineru"
    assert repository.list_pending_outbox(QUEUE_ENTITY) == []


def test_last_pdf_ocr_page_publishes_single_chunks_ready(monkeypatch):
    patch_mineru_success(monkeypatch)
    repository = FakeRepository(
        make_stored_file(),
        pages=[
            make_page(1, PAGE_COMPLETED_STATUS, method=PYMUPDF_METHOD, chunk_count=1),
            make_page(2, PAGE_PENDING_OCR_STATUS),
        ],
        chunks=[make_existing_chunk(1)],
        total_pages=2,
    )

    result = process_ocr_payload(make_pdf_ocr_payload(page_number=2), repository)
    second = process_ocr_payload(make_pdf_ocr_payload(page_number=2), repository)

    assert result.file_status == TEXT_EXTRACTION_COMPLETED_STATUS
    assert result.entity_outbox_id == "entity-1"
    assert result.page.ocr_queue_wait_seconds is not None
    assert result.page.ocr_processing_seconds is not None
    assert result.page.cpu_total_seconds is not None
    assert second.entity_outbox_id == "entity-1"
    assert len(repository.list_pending_outbox(QUEUE_ENTITY)) == 1
    chunks_ready_payload = repository.list_pending_outbox(QUEUE_ENTITY)[0].payload
    assert chunks_ready_payload["source_queue_name"] == QUEUE_OCR_URGENT
    assert chunks_ready_payload["chunk_count"] == 2
    assert [chunk.chunk_index for chunk in repository.chunks] == [1, 2]


def test_pdf_ocr_batch_completes_multiple_pages_and_records_metrics(monkeypatch):
    patch_mineru_success(monkeypatch)
    repository = FakeRepository(
        make_stored_file(),
        pages=[
            make_page(1, PAGE_COMPLETED_STATUS, method=PYMUPDF_METHOD, chunk_count=1),
            make_page(2, PAGE_PENDING_OCR_STATUS),
            make_page(3, PAGE_PENDING_OCR_STATUS),
        ],
        chunks=[make_existing_chunk(1)],
        total_pages=3,
    )

    result = process_ocr_payload(make_pdf_ocr_batch_payload([2, 3]), repository)

    assert result.file_status == TEXT_EXTRACTION_COMPLETED_STATUS
    assert result.page.page_number == 3
    assert [repository.pages[number].status for number in (2, 3)] == [
        PAGE_COMPLETED_STATUS,
        PAGE_COMPLETED_STATUS,
    ]
    assert [chunk.page_start for chunk in repository.chunks] == [1, 2, 3]
    assert len(repository.list_pending_outbox(QUEUE_ENTITY)) == 1
    assert len(repository.saved_results) == 2
    assert len(repository.batch_metrics) == 1
    metrics = repository.batch_metrics[0]
    assert metrics.page_numbers == (2, 3)
    assert metrics.mineru_command_count == 1
    assert metrics.fallback_level == "batch"
    assert metrics.wall_seconds >= 0


def test_pdf_ocr_batch_falls_back_to_page_and_keeps_partial_success(monkeypatch):
    import ocr.extractor as extractor

    def fake_run_pdf_range(
        pdf_path,
        start_page_index,
        end_page_index,
        output_dir,
        timeout_seconds,
        device="auto",
        config=None,
    ):
        if start_page_index != end_page_index:
            raise MinerUExecutionError("range exploded")
        if start_page_index == 2:
            raise MinerUExecutionError("page 3 exploded")
        write_mineru_payload(output_dir, "OCR page recovered")

    monkeypatch.setattr(extractor, "run_mineru_pdf_range", fake_run_pdf_range)
    repository = FakeRepository(
        make_stored_file(),
        pages=[
            make_page(1, PAGE_COMPLETED_STATUS, method=PYMUPDF_METHOD, chunk_count=1),
            make_page(2, PAGE_PENDING_OCR_STATUS),
            make_page(3, PAGE_PENDING_OCR_STATUS),
        ],
        chunks=[make_existing_chunk(1)],
        total_pages=3,
    )

    result = process_ocr_payload(make_pdf_ocr_batch_payload([2, 3]), repository)

    assert result.file_status == TEXT_EXTRACTION_FAILED_STATUS
    assert repository.pages[2].status == PAGE_COMPLETED_STATUS
    assert repository.pages[3].status == PAGE_FAILED_STATUS
    assert [chunk.page_start for chunk in repository.chunks] == [1, 2]
    assert len(repository.saved_results) == 2
    assert len(repository.batch_metrics) == 1
    metrics = repository.batch_metrics[0]
    assert metrics.mineru_command_count == 3
    assert metrics.fallback_level == "page"
    assert "page 3 exploded" in (metrics.error or "")


def test_remote_pdf_ocr_reuses_materialization_and_releases_on_final(
    monkeypatch,
    tmp_path: Path,
):
    patch_mineru_success(monkeypatch)
    materialized_path = tmp_path / "remote.pdf"
    materialized_path.write_bytes(b"%PDF")
    stored_file = replace(
        make_stored_file(),
        source_type="drive",
        source_uri="drive://file/pdf-1",
        external_id="pdf-1",
    )
    repository = FakeRepository(
        stored_file,
        pages=[
            make_page(1, PAGE_COMPLETED_STATUS, method=PYMUPDF_METHOD, chunk_count=1),
            make_page(2, PAGE_PENDING_OCR_STATUS),
        ],
        chunks=[make_existing_chunk(1)],
        total_pages=2,
    )
    payload = make_pdf_ocr_payload(page_number=2)
    payload.update(
        {
            "source_type": "drive",
            "source_uri": "drive://file/pdf-1",
            "external_id": "pdf-1",
        }
    )
    materializer = FakeMaterializer(materialized_path)

    result = process_ocr_payload(payload, repository, materializer=materializer)

    assert result.file_status == TEXT_EXTRACTION_COMPLETED_STATUS
    assert materializer.materialized == [stored_file.file_id]
    assert materializer.released == [
        (stored_file.file_id, TEXT_EXTRACTION_COMPLETED_STATUS)
    ]
    assert repository.chunks[-1].source_map["source_uri"] == "drive://file/pdf-1"
    assert repository.chunks[-1].source_map["original_path"] == "drive://file/pdf-1"


def test_image_routed_message_creates_single_page_and_publishes_entity(monkeypatch):
    patch_mineru_success(monkeypatch, text="Texto desde imagen")
    repository = FakeRepository(make_stored_file(extension=".png"))

    result = process_ocr_payload(make_image_payload(), repository)

    assert result.message.is_image_file is True
    assert result.page.page_number == 1
    assert result.file_status == TEXT_EXTRACTION_COMPLETED_STATUS
    assert result.file_chunk_count == 1
    assert result.entity_outbox_id == "entity-1"
    assert repository.chunks[0].text == "texto desde imagen"
    assert len(repository.list_pending_outbox(QUEUE_ENTITY)) == 1
    chunks_ready_payload = repository.list_pending_outbox(QUEUE_ENTITY)[0].payload
    assert chunks_ready_payload["source_queue_name"] == QUEUE_OCR


def test_mineru_failure_marks_page_and_file_failed_without_downstream(monkeypatch):
    patch_mineru_failure(monkeypatch)
    repository = FakeRepository(
        make_stored_file(),
        pages=[make_page(1, PAGE_PENDING_OCR_STATUS)],
        total_pages=1,
    )

    result = process_ocr_payload(make_pdf_ocr_payload(page_number=1), repository)

    assert result.page.status == PAGE_FAILED_STATUS
    assert result.file_status == TEXT_EXTRACTION_FAILED_STATUS
    assert result.error == "mineru exploded"
    assert result.entity_outbox_id is None
    assert repository.list_pending_outbox(QUEUE_ENTITY) == []
    assert repository.chunks == []


def test_ocr_worker_logs_exact_error_when_page_fails(monkeypatch, capsys):
    patch_mineru_failure(monkeypatch)
    repository = FakeRepository(
        make_stored_file(),
        pages=[make_page(1, PAGE_PENDING_OCR_STATUS)],
        total_pages=1,
    )
    consumer = FakeConsumer([make_pdf_ocr_payload(page_number=1)])
    publisher = FakePublisher()

    run_ocr_worker(
        repository=repository,
        publisher=publisher,
        consumer=consumer,
        max_messages=1,
    )

    captured = capsys.readouterr()
    assert "processed_ocr" in captured.out
    assert "page_status=failed" in captured.out
    assert "file_status=text_extraction_failed" in captured.out
    assert "error=mineru exploded" in captured.out
    assert consumer.calls == [
        {
            "queue_names": (QUEUE_OCR_URGENT, QUEUE_OCR),
            "max_messages": 1,
            "requeue_messages": False,
        }
    ]
