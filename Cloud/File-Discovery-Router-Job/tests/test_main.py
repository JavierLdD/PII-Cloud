from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_file_router_job.main import (  # noqa: E402
    _pipeline_revision_from_env,
    run_job,
)
from cloud_file_router_job.models import (  # noqa: E402
    DiscoveredFile,
    FileRegistration,
    OutboxRecord,
    RoutePlan,
    SnapshotCounters,
    StoredFile,
)
from cloud_file_router_job.pubsub import TopicMap, build_pubsub_attributes  # noqa: E402
from cloud_file_router_job.request import DiscoveryRouterRequest  # noqa: E402
from cloud_file_router_job.repository import (  # noqa: E402
    SNAPSHOT_MODIFIED,
    SNAPSHOT_NEW,
    SNAPSHOT_REPROCESSED,
    SNAPSHOT_REUSED,
    build_revision_key,
)
from cloud_file_router_job.routing import build_routed_payload  # noqa: E402


class FakeRepository:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.run_files: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        self.latest_files: dict[tuple[str, str], dict[str, Any]] = {}
        self.created_runs: list[str] = []
        self.finished_runs: list[dict[str, Any]] = []
        self.outbox: dict[str, dict[str, Any]] = {}
        self.checked = False
        self.schema_ensured = False
        self.finalized_runs: list[str] = []

    def check_connection(self) -> None:
        self.checked = True

    def ensure_cloud_schema(self) -> None:
        self.schema_ensured = True

    def create_run(
        self,
        request: DiscoveryRouterRequest,
        execution_id: str | None,
    ) -> str:
        self.created_runs.append(request.run_id)
        return request.run_id

    def register_file(
        self,
        run_id: str,
        discovered_file: DiscoveredFile,
        force_enqueue: bool,
    ) -> FileRegistration:
        source_key = (discovered_file.source_type, discovered_file.source_uri)
        key = (run_id, *source_key)
        identity = (
            discovered_file.file_name,
            discovered_file.relative_path,
            discovered_file.extension,
            discovered_file.mime_type,
            discovered_file.size_bytes,
            discovered_file.checksum_sha256,
            discovered_file.content_hash,
            discovered_file.etag,
        )
        existing = self.files.get(key)
        prior = self.latest_files.get(source_key)
        revision_key = build_revision_key(discovered_file)
        if existing:
            file_id = existing["file_id"]
            snapshot_state = existing["snapshot_state"]
            status = existing["status"]
            should_route = snapshot_state != SNAPSHOT_REUSED
        else:
            if prior is None:
                snapshot_state = SNAPSHOT_NEW
                status = "discovered"
            elif revision_key is None or prior["revision_key"] != revision_key:
                snapshot_state = SNAPSHOT_MODIFIED
                status = "changed"
            elif force_enqueue:
                snapshot_state = SNAPSHOT_REPROCESSED
                status = "requeued"
            else:
                snapshot_state = SNAPSHOT_REUSED
                status = "unchanged"
            should_route = snapshot_state != SNAPSHOT_REUSED
            file_id = f"file-{len(self.files) + 1}"
            existing = {
                "file_id": file_id,
                "identity": identity,
                "revision_key": revision_key,
                "snapshot_state": snapshot_state,
                "status": status,
                "previous_file_id": prior["file_id"] if prior else None,
                "reused_from_file_id": (
                    prior.get("reused_from_file_id") or prior["file_id"]
                    if prior and snapshot_state == SNAPSHOT_REUSED
                    else None
                ),
            }
            self.files[key] = existing
            self.run_files.setdefault(run_id, {})[source_key] = existing

        stored_file = StoredFile(
            file_id=file_id,
            run_id=run_id,
            source_type=discovered_file.source_type,
            source_uri=discovered_file.source_uri,
            external_id=discovered_file.external_id,
            file_name=discovered_file.file_name,
            relative_path=discovered_file.relative_path,
            extension=discovered_file.extension,
            mime_type=discovered_file.mime_type,
            size_bytes=discovered_file.size_bytes,
            checksum_sha256=discovered_file.checksum_sha256,
            content_hash=discovered_file.content_hash,
            etag=discovered_file.etag,
        )
        return FileRegistration(
            file_id,
            should_route,
            status,
            stored_file,
            snapshot_state=snapshot_state,
            revision_key=revision_key,
            previous_file_id=existing.get("previous_file_id"),
            reused_from_file_id=existing.get("reused_from_file_id"),
        )

    def route_file(
        self,
        request: DiscoveryRouterRequest,
        stored_file: StoredFile,
        route_plan: RoutePlan,
        topic_map: TopicMap,
        execution_id: str | None,
    ) -> OutboxRecord:
        routing_decision_id = f"decision-{len(self.outbox) + 1}"
        payload = build_routed_payload(
            request.run_id,
            routing_decision_id,
            stored_file,
            route_plan,
        )
        attributes = build_pubsub_attributes(
            payload,
            user_id=request.user_id,
            run_id=request.run_id,
        )
        outbox_id = f"outbox-{len(self.outbox) + 1}"
        record = OutboxRecord(
            outbox_id=outbox_id,
            topic_name=topic_map.topic_for_destination(
                route_plan.destination_queue_name
            ),
            payload=payload,
            attributes=attributes,
            status="pending",
        )
        self.outbox[outbox_id] = {"record": record, "published": False, "errors": []}
        return record

    def mark_outbox_published(
        self,
        outbox_id: str,
        pubsub_message_id: str,
        attributes: dict[str, str],
    ) -> None:
        self.outbox[outbox_id]["published"] = True
        self.outbox[outbox_id]["message_id"] = pubsub_message_id

    def record_outbox_error(self, outbox_id: str, error: str) -> None:
        self.outbox[outbox_id]["errors"].append(error)

    def finalize_snapshot(self, run_id: str, expected_file_count: int) -> int:
        self.finalized_runs.append(run_id)
        current = self.run_files.get(run_id, {})
        assert len(current) == expected_file_count
        return len(set(self.latest_files) - set(current))

    def finish_run(
        self,
        run_id: str,
        status: str,
        discovered_count: int,
        routed_count: int,
        skipped_count: int,
        snapshot_counters: SnapshotCounters,
        snapshot_completed: bool,
        error: str | None = None,
    ) -> None:
        self.finished_runs.append(
            {
                "run_id": run_id,
                "status": status,
                "discovered_count": discovered_count,
                "routed_count": routed_count,
                "skipped_count": skipped_count,
                "snapshot_counters": snapshot_counters,
                "snapshot_completed": snapshot_completed,
                "error": error,
            }
        )
        if snapshot_completed:
            self.latest_files = dict(self.run_files.get(run_id, {}))


class FakePublisher:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.validated_topics: tuple[str, ...] = ()
        self.published: list[OutboxRecord] = []

    def validate_topics(self, topic_names) -> None:
        self.validated_topics = tuple(topic_names)

    def publish(self, record: OutboxRecord) -> str:
        if self.fail:
            raise RuntimeError("publisher unavailable")
        self.published.append(record)
        return f"message-{len(self.published)}"


def topic_map() -> TopicMap:
    return TopicMap(
        pdf="topic-pdf",
        ocr="topic-ocr",
        doc="topic-doc",
        tables="topic-tables",
        unsupported="topic-unsupported",
    )


def request(**overrides) -> DiscoveryRouterRequest:
    payload = {
        "user_id": "user-001",
        "run_id": "run-001",
        "source_type": "drive",
        "drive_folder_id": "folder-001",
    }
    payload.update(overrides)
    return DiscoveryRouterRequest.from_mapping(payload)


def drive_file(file_id: str = "drive-001", extension: str = ".pdf") -> DiscoveredFile:
    return DiscoveredFile(
        source_type="drive",
        source_uri=f"drive://file/{file_id}",
        external_id=file_id,
        file_name=f"sample{extension}",
        relative_path=f"sample{extension}",
        extension=extension,
        mime_type="application/pdf" if extension == ".pdf" else None,
        size_bytes=10,
        content_hash="hash-1",
        etag="1",
    )


def test_run_job_publishes_new_file() -> None:
    repo = FakeRepository()
    publisher = FakePublisher()

    summary = run_job(
        request=request(),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file()],
        execution_id="execution-1",
    )

    assert summary.status == "completed"
    assert summary.discovered_count == 1
    assert summary.routed_count == 1
    assert summary.published_count == 1
    assert summary.new_file_count == 1
    assert summary.snapshot_completed is True
    assert repo.schema_ensured is True
    assert repo.finished_runs[0]["status"] == "completed"
    assert publisher.published[0].attributes["user_id"] == "user-001"
    assert publisher.published[0].attributes["run_id"] == "run-001"


def test_pipeline_revision_uses_visor_name_and_keeps_legacy_alias() -> None:
    assert (
        _pipeline_revision_from_env(
            {
                "VISOR_PIPELINE_REVISION": "visor-v2",
                "PIPELINE_REVISION": "legacy-v1",
            }
        )
        == "visor-v2"
    )
    assert _pipeline_revision_from_env({"PIPELINE_REVISION": "legacy-v1"}) == (
        "legacy-v1"
    )


def test_run_job_skips_unchanged_file_on_second_run() -> None:
    repo = FakeRepository()
    publisher = FakePublisher()
    file_obj = drive_file()

    run_job(
        request=request(),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[file_obj],
    )
    second = run_job(
        request=request(run_id="run-002"),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[file_obj],
    )

    assert second.routed_count == 0
    assert second.skipped_count == 1
    assert second.reused_file_count == 1
    assert second.snapshot_completed is True
    assert len(publisher.published) == 1
    first_file_id = repo.files[("run-001", "drive", "drive://file/drive-001")][
        "file_id"
    ]
    second_file_id = repo.files[("run-002", "drive", "drive://file/drive-001")][
        "file_id"
    ]
    assert first_file_id != second_file_id


def test_run_job_force_enqueue_republishes_unchanged_file() -> None:
    repo = FakeRepository()
    publisher = FakePublisher()
    file_obj = drive_file()

    run_job(
        request=request(),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[file_obj],
    )
    second = run_job(
        request=request(run_id="run-002", force_enqueue=True),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[file_obj],
    )

    assert second.routed_count == 1
    assert second.skipped_count == 0
    assert second.reprocessed_file_count == 1
    assert len(publisher.published) == 2


def test_dry_run_does_not_persist_or_publish() -> None:
    repo = FakeRepository()
    publisher = FakePublisher()

    summary = run_job(
        request=request(dry_run=True),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file()],
    )

    assert summary.status == "completed_dry_run"
    assert summary.discovered_count == 1
    assert repo.created_runs == []
    assert repo.outbox == {}
    assert publisher.published == []
    assert repo.checked is True
    assert repo.schema_ensured is False


def test_run_job_records_publish_failure_and_finishes_partial_failed() -> None:
    repo = FakeRepository()
    publisher = FakePublisher(fail=True)

    summary = run_job(
        request=request(),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file()],
    )

    assert summary.status == "partial_failed"
    assert summary.failed_publish_count == 1
    assert summary.snapshot_completed is False
    assert repo.finalized_runs == []
    assert repo.finished_runs[0]["status"] == "partial_failed"
    assert repo.outbox["outbox-1"]["errors"] == ["publisher unavailable"]


def test_run_job_routes_modified_file_as_new_snapshot_row() -> None:
    repo = FakeRepository()
    publisher = FakePublisher()

    run_job(
        request=request(),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file()],
    )
    changed = drive_file()
    changed = DiscoveredFile(
        **{
            **changed.__dict__,
            "content_hash": "hash-2",
            "etag": "2",
        }
    )
    second = run_job(
        request=request(run_id="run-002"),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[changed],
    )

    assert second.modified_file_count == 1
    assert second.routed_count == 1
    assert len(repo.files) == 2


def test_run_job_records_deleted_files_only_after_complete_enumeration() -> None:
    repo = FakeRepository()
    publisher = FakePublisher()

    run_job(
        request=request(),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file("one"), drive_file("two")],
    )
    second = run_job(
        request=request(run_id="run-002"),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file("one")],
    )

    assert second.deleted_file_count == 1
    assert second.snapshot_completed is True


def test_limited_run_never_declares_deletions_or_becomes_reuse_parent() -> None:
    repo = FakeRepository()
    publisher = FakePublisher()

    run_job(
        request=request(),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file("one"), drive_file("two")],
    )
    limited = run_job(
        request=request(run_id="run-002", max_files=1),
        repository=repo,
        publisher=publisher,
        topic_map=topic_map(),
        discovered_files=[drive_file("one"), drive_file("two")],
    )

    assert limited.snapshot_completed is False
    assert limited.deleted_file_count == 0
    assert repo.finalized_runs == ["run-001"]
