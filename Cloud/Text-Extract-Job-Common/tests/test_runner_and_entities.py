from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(PROJECT / "Text_Extract"))

from cloud_text_extract_job.config import TextExtractJobConfig  # noqa: E402
from cloud_text_extract_job.entities import publish_pending_entity_outbox  # noqa: E402
from cloud_text_extract_job.errors import TransientProcessingError  # noqa: E402
from cloud_text_extract_job.pubsub import PulledMessage  # noqa: E402
from cloud_text_extract_job.runner import drain_subscription  # noqa: E402
from common.models import OutboxMessage, QUEUE_ENTITY  # noqa: E402


class FakePuller:
    def __init__(self, messages):
        self.messages = list(messages)
        self.acked = []
        self.nacked = []

    def pull_one(self, subscription_id: str, timeout_seconds: int):
        if self.messages:
            return self.messages.pop(0)
        return None

    def ack(self, subscription_id: str, ack_id: str) -> None:
        self.acked.append((subscription_id, ack_id))

    def nack(self, subscription_id: str, ack_id: str) -> None:
        self.nacked.append((subscription_id, ack_id))


class FakeTextRepository:
    def __init__(self, messages):
        self.messages = messages

    def list_pending_outbox(self, queue_name: str):
        assert queue_name == QUEUE_ENTITY
        return self.messages


class FakeOutboxRepository:
    def __init__(self):
        self.published = []
        self.errors = []

    def mark_published(self, outbox_id, *, pubsub_message_id=None, attributes=None):
        self.published.append((outbox_id, pubsub_message_id, attributes))

    def record_error(self, outbox_id, error):
        self.errors.append((outbox_id, error))


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish_json(self, topic_name, payload, attributes):
        self.messages.append((topic_name, payload, attributes))
        return f"message-{len(self.messages)}"


def config() -> TextExtractJobConfig:
    return TextExtractJobConfig(
        subscription_id="subscriptions/text",
        database_url="postgresql://example",
        topic_pii_entities="projects/pii/topics/pii-entities",
        topic_text_poison="projects/pii/topics/text-poison",
        expected_user_id="user-1",
        expected_run_id="run-1",
        idle_timeout_seconds=0,
    )


def test_runner_acks_after_successful_handler():
    message = PulledMessage(
        ack_id="ack-1",
        payload={"run_id": "run-1"},
        attributes={"user_id": "user-1", "run_id": "run-1"},
    )
    puller = FakePuller([message])
    handled = []

    processed = drain_subscription(
        config=config(),
        puller=puller,
        handle_message=lambda pulled: handled.append(pulled.ack_id),
    )

    assert processed == 1
    assert handled == ["ack-1"]
    assert puller.acked == [("subscriptions/text", "ack-1")]
    assert puller.nacked == []


def test_runner_nacks_transient_failure_without_counting_message():
    message = PulledMessage(
        ack_id="ack-1",
        payload={"run_id": "run-1"},
        attributes={"user_id": "user-1", "run_id": "run-1"},
    )
    puller = FakePuller([message])

    def fail_transient(pulled):
        raise TransientProcessingError("temporary database outage")

    processed = drain_subscription(
        config=config(),
        puller=puller,
        handle_message=fail_transient,
    )

    assert processed == 0
    assert puller.acked == []
    assert puller.nacked == [("subscriptions/text", "ack-1")]


def test_publish_pending_entity_outbox_filters_to_current_run_and_file():
    current = OutboxMessage(
        outbox_id="entity-current",
        queue_name=QUEUE_ENTITY,
        payload={
            "event_type": "file.chunks_ready",
            "run_id": "run-1",
            "file_id": "file-1",
            "destination_queue_name": QUEUE_ENTITY,
        },
    )
    other = OutboxMessage(
        outbox_id="entity-other",
        queue_name=QUEUE_ENTITY,
        payload={
            "event_type": "file.chunks_ready",
            "run_id": "run-1",
            "file_id": "file-2",
            "destination_queue_name": QUEUE_ENTITY,
        },
    )
    repository = FakeTextRepository([current, other])
    outbox_repository = FakeOutboxRepository()
    publisher = FakePublisher()

    published = publish_pending_entity_outbox(
        repository=repository,
        outbox_repository=outbox_repository,
        publisher=publisher,
        topic_name="projects/pii/topics/pii-entities",
        user_id="user-1",
        run_id="run-1",
        file_id="file-1",
    )

    assert published == 1
    assert publisher.messages[0][1]["file_id"] == "file-1"
    assert outbox_repository.published == [
        (
            "entity-current",
            "message-1",
            {
                "user_id": "user-1",
                "run_id": "run-1",
                "event_type": "file.chunks_ready",
                "file_id": "file-1",
                "destination_queue_name": QUEUE_ENTITY,
            },
        )
    ]
