from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import os
from typing import Any

from cloud_file_router_job.models import OutboxRecord
from cloud_file_router_job.routing import (
    QUEUE_DOC,
    QUEUE_OCR,
    QUEUE_PDF,
    QUEUE_TABLES,
    QUEUE_UNSUPPORTED,
)


class PubSubConfigError(ValueError):
    """Raised when Pub/Sub topics are missing or invalid."""


@dataclass(frozen=True)
class TopicMap:
    pdf: str
    ocr: str
    doc: str
    tables: str
    unsupported: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "TopicMap":
        return cls(
            pdf=_required_env(env, "TOPIC_PDF"),
            ocr=_required_env(env, "TOPIC_OCR"),
            doc=_required_env(env, "TOPIC_DOC"),
            tables=_required_env(env, "TOPIC_TABLES"),
            unsupported=_required_env(env, "TOPIC_UNSUPPORTED"),
        )

    def topic_for_destination(self, destination_queue_name: str) -> str:
        if destination_queue_name == QUEUE_PDF:
            return self.pdf
        if destination_queue_name == QUEUE_OCR:
            return self.ocr
        if destination_queue_name == QUEUE_DOC:
            return self.doc
        if destination_queue_name == QUEUE_TABLES:
            return self.tables
        if destination_queue_name == QUEUE_UNSUPPORTED:
            return self.unsupported
        raise PubSubConfigError(f"Unsupported destination_queue_name: {destination_queue_name}")

    def all_topics(self) -> tuple[str, ...]:
        return (self.pdf, self.ocr, self.doc, self.tables, self.unsupported)


class PubSubPublisher:
    def __init__(self, timeout_seconds: float | None = None) -> None:
        try:
            from google.cloud import pubsub_v1
        except ImportError as exc:
            raise RuntimeError("Missing google-cloud-pubsub dependency.") from exc

        self._client = pubsub_v1.PublisherClient()
        self._timeout_seconds = timeout_seconds or float(
            os.environ.get("PUBSUB_PUBLISH_TIMEOUT_SECONDS", "60")
        )

    def validate_topics(self, topic_names: Iterable[str]) -> None:
        for topic_name in topic_names:
            self._client.get_topic(request={"topic": topic_name})

    def publish(self, record: OutboxRecord) -> str:
        future = self._client.publish(
            record.topic_name,
            json.dumps(record.payload, sort_keys=True).encode("utf-8"),
            **record.attributes,
        )
        return str(future.result(timeout=self._timeout_seconds))


def build_pubsub_attributes(
    payload: Mapping[str, Any],
    *,
    user_id: str,
    run_id: str,
) -> dict[str, str]:
    required_keys = (
        "schema_version",
        "event_type",
        "file_id",
        "source_type",
        "route_type",
        "destination_queue_name",
        "routing_decision_id",
    )
    attributes = {
        "user_id": user_id,
        "run_id": run_id,
    }
    for key in required_keys:
        value = payload.get(key)
        if value is None or str(value).strip() == "":
            raise PubSubConfigError(f"Missing payload field for Pub/Sub attribute: {key}")
        attributes[key] = str(value)
    return attributes


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        raise PubSubConfigError(f"Missing required environment variable: {name}")
    return value.strip()
