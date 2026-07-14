from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models import CHUNKS_READY_EVENT_TYPE, QUEUE_ENTITY, ChunksReadyMessage


def chunks_ready_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "2.0",
        "event_type": CHUNKS_READY_EVENT_TYPE,
        "run_id": "run-1",
        "file_id": "file-1",
        "routing_decision_id": "route-1",
        "source_type": "local",
        "source_uri": "local:///tmp/documento.pdf",
        "external_id": "/tmp/documento.pdf",
        "file_name": "documento.pdf",
        "relative_path": "subdir/documento.pdf",
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "checksum_sha256": "a" * 64,
        "content_hash": None,
        "etag": None,
        "size_bytes": 123,
        "source_queue_name": "Queue-PDF",
        "destination_queue_name": QUEUE_ENTITY,
        "chunk_count": 2,
        "page_count": 1,
    }
    payload.update(overrides)
    return payload


def test_chunks_ready_message_parses_valid_payload():
    message = ChunksReadyMessage.from_payload(chunks_ready_payload())

    assert message.file_id == "file-1"
    assert message.destination_queue_name == QUEUE_ENTITY
    assert message.chunk_count == 2


def test_chunks_ready_message_rejects_wrong_event_type():
    payload = chunks_ready_payload(event_type="file.routed")

    try:
        ChunksReadyMessage.from_payload(payload)
    except ValueError as exc:
        assert "Unsupported event_type" in str(exc)
    else:
        raise AssertionError("expected ValueError")
