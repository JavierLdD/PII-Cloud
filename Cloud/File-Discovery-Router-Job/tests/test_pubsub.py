from __future__ import annotations

from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_file_router_job.pubsub import (  # noqa: E402
    PubSubConfigError,
    TopicMap,
    build_pubsub_attributes,
)
from cloud_file_router_job.routing import QUEUE_PDF  # noqa: E402


def test_topic_map_requires_all_topics() -> None:
    with pytest.raises(PubSubConfigError, match="TOPIC_UNSUPPORTED"):
        TopicMap.from_env(
            {
                "TOPIC_PDF": "topic-pdf",
                "TOPIC_OCR": "topic-ocr",
                "TOPIC_DOC": "topic-doc",
                "TOPIC_TABLES": "topic-tables",
            }
        )


def test_topic_map_resolves_destination_queue() -> None:
    topic_map = TopicMap(
        pdf="topic-pdf",
        ocr="topic-ocr",
        doc="topic-doc",
        tables="topic-tables",
        unsupported="topic-unsupported",
    )

    assert topic_map.topic_for_destination(QUEUE_PDF) == "topic-pdf"


def test_pubsub_attributes_include_filter_fields() -> None:
    attributes = build_pubsub_attributes(
        {
            "schema_version": "2.0",
            "event_type": "file.routed",
            "file_id": "file-001",
            "source_type": "drive",
            "route_type": "pdf",
            "destination_queue_name": "Queue-PDF",
            "routing_decision_id": "decision-001",
        },
        user_id="user-001",
        run_id="run-001",
    )

    assert attributes["user_id"] == "user-001"
    assert attributes["run_id"] == "run-001"
    assert attributes["event_type"] == "file.routed"
    assert attributes["route_type"] == "pdf"


def test_pubsub_attributes_reject_missing_required_field() -> None:
    with pytest.raises(PubSubConfigError, match="routing_decision_id"):
        build_pubsub_attributes(
            {
                "schema_version": "2.0",
                "event_type": "file.routed",
                "file_id": "file-001",
                "source_type": "drive",
                "route_type": "pdf",
                "destination_queue_name": "Queue-PDF",
            },
            user_id="user-001",
            run_id="run-001",
        )
