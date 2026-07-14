from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
import os
import sys
from typing import Protocol

from cloud_file_router_job.drive_source import DriveDiscoveryAdapter
from cloud_file_router_job.models import (
    DiscoveredFile,
    FileRegistration,
    JobSummary,
    OutboxRecord,
    RoutePlan,
    SnapshotCounters,
    StoredFile,
)
from cloud_file_router_job.pubsub import PubSubPublisher, TopicMap
from cloud_file_router_job.repository import (
    SNAPSHOT_MODIFIED,
    SNAPSHOT_NEW,
    SNAPSHOT_REPROCESSED,
    SNAPSHOT_REUSED,
    PostgresFileRouterRepository,
)
from cloud_file_router_job.request import DiscoveryRouterRequest, load_request_from_env
from cloud_file_router_job.routing import ROUTER_VERSION, classify_file


LOGGER = logging.getLogger("cloud_file_router_job")


class Repository(Protocol):
    def check_connection(self) -> None:
        ...

    def ensure_cloud_schema(self) -> None:
        ...

    def create_run(
        self,
        request: DiscoveryRouterRequest,
        execution_id: str | None,
    ) -> str:
        ...

    def register_file(
        self,
        run_id: str,
        discovered_file: DiscoveredFile,
        force_enqueue: bool,
    ) -> FileRegistration:
        ...

    def route_file(
        self,
        request: DiscoveryRouterRequest,
        stored_file: StoredFile,
        route_plan: RoutePlan,
        topic_map: TopicMap,
        execution_id: str | None,
    ) -> OutboxRecord:
        ...

    def mark_outbox_published(
        self,
        outbox_id: str,
        pubsub_message_id: str,
        attributes: dict[str, str],
    ) -> None:
        ...

    def record_outbox_error(self, outbox_id: str, error: str) -> None:
        ...

    def finalize_snapshot(self, run_id: str, expected_file_count: int) -> int:
        ...

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
        ...


class Publisher(Protocol):
    def validate_topics(self, topic_names: Iterable[str]) -> None:
        ...

    def publish(self, record: OutboxRecord) -> str:
        ...


def main() -> int:
    _configure_logging()
    request = load_request_from_env(os.environ)
    database_url = _required_env("DATABASE_URL")
    execution_id = _execution_id_from_env(os.environ)
    topic_map = TopicMap.from_env(os.environ)
    publisher = PubSubPublisher()
    source = DriveDiscoveryAdapter.from_environment(os.environ).iter_files(
        request.drive_folder_id
    )

    pipeline_revision = _pipeline_revision_from_env(os.environ)
    with PostgresFileRouterRepository(
        database_url,
        pipeline_revision=pipeline_revision,
    ) as repository:
        summary = run_job(
            request=request,
            repository=repository,
            publisher=publisher,
            topic_map=topic_map,
            discovered_files=source,
            execution_id=execution_id,
        )

    LOGGER.info(
        "file_router_job_finished run_id=%s status=%s discovered=%s routed=%s "
        "skipped=%s published=%s failed_publish=%s new=%s modified=%s "
        "reused=%s reprocessed=%s deleted=%s snapshot_completed=%s",
        summary.run_id,
        summary.status,
        summary.discovered_count,
        summary.routed_count,
        summary.skipped_count,
        summary.published_count,
        summary.failed_publish_count,
        summary.new_file_count,
        summary.modified_file_count,
        summary.reused_file_count,
        summary.reprocessed_file_count,
        summary.deleted_file_count,
        summary.snapshot_completed,
    )
    return 0 if summary.failed_publish_count == 0 and summary.status != "failed" else 1


def run_job(
    *,
    request: DiscoveryRouterRequest,
    repository: Repository,
    publisher: Publisher,
    topic_map: TopicMap,
    discovered_files: Iterable[DiscoveredFile],
    execution_id: str | None = None,
) -> JobSummary:
    repository.check_connection()
    publisher.validate_topics(topic_map.all_topics())

    if request.dry_run:
        return _run_dry_run(request, topic_map, discovered_files)

    repository.ensure_cloud_schema()
    run_id = repository.create_run(request, execution_id)
    discovered_count = 0
    routed_count = 0
    skipped_count = 0
    published_count = 0
    failed_publish_count = 0
    new_file_count = 0
    modified_file_count = 0
    reused_file_count = 0
    reprocessed_file_count = 0
    deleted_file_count = 0

    try:
        for discovered_file in _limit_files(discovered_files, request.max_files):
            discovered_count += 1
            registration = repository.register_file(
                run_id,
                discovered_file,
                request.force_enqueue,
            )
            if registration.snapshot_state == SNAPSHOT_NEW:
                new_file_count += 1
            elif registration.snapshot_state == SNAPSHOT_MODIFIED:
                modified_file_count += 1
            elif registration.snapshot_state == SNAPSHOT_REUSED:
                reused_file_count += 1
            elif registration.snapshot_state == SNAPSHOT_REPROCESSED:
                reprocessed_file_count += 1
            if not registration.should_route:
                skipped_count += 1
                continue

            route_plan = classify_file(
                registration.stored_file.extension,
                registration.stored_file.mime_type,
            )
            outbox_record = repository.route_file(
                request,
                registration.stored_file,
                route_plan,
                topic_map,
                execution_id,
            )
            routed_count += 1
            if outbox_record.status == "published":
                continue

            try:
                pubsub_message_id = publisher.publish(outbox_record)
            except Exception as exc:
                failed_publish_count += 1
                repository.record_outbox_error(outbox_record.outbox_id, str(exc))
                LOGGER.exception(
                    "pubsub_publish_failed run_id=%s file_id=%s outbox_id=%s",
                    run_id,
                    registration.file_id,
                    outbox_record.outbox_id,
                )
                continue

            repository.mark_outbox_published(
                outbox_record.outbox_id,
                pubsub_message_id,
                outbox_record.attributes,
            )
            published_count += 1

        snapshot_completed = request.max_files is None and failed_publish_count == 0
        if snapshot_completed:
            deleted_file_count = repository.finalize_snapshot(
                run_id,
                discovered_count,
            )
        snapshot_counters = SnapshotCounters(
            new_file_count=new_file_count,
            modified_file_count=modified_file_count,
            reused_file_count=reused_file_count,
            reprocessed_file_count=reprocessed_file_count,
            deleted_file_count=deleted_file_count,
        )
        status = "partial_failed" if failed_publish_count else "completed"
        repository.finish_run(
            run_id,
            status,
            discovered_count,
            routed_count,
            skipped_count,
            snapshot_counters,
            snapshot_completed,
        )
        return JobSummary(
            run_id=run_id,
            status=status,
            discovered_count=discovered_count,
            routed_count=routed_count,
            skipped_count=skipped_count,
            published_count=published_count,
            failed_publish_count=failed_publish_count,
            new_file_count=new_file_count,
            modified_file_count=modified_file_count,
            reused_file_count=reused_file_count,
            reprocessed_file_count=reprocessed_file_count,
            deleted_file_count=deleted_file_count,
            snapshot_completed=snapshot_completed,
        )
    except Exception as exc:
        snapshot_counters = SnapshotCounters(
            new_file_count=new_file_count,
            modified_file_count=modified_file_count,
            reused_file_count=reused_file_count,
            reprocessed_file_count=reprocessed_file_count,
            deleted_file_count=0,
        )
        repository.finish_run(
            run_id,
            "failed",
            discovered_count,
            routed_count,
            skipped_count,
            snapshot_counters,
            False,
            str(exc),
        )
        raise


def _run_dry_run(
    request: DiscoveryRouterRequest,
    topic_map: TopicMap,
    discovered_files: Iterable[DiscoveredFile],
) -> JobSummary:
    discovered_count = 0
    routed_count = 0
    unsupported_count = 0
    for discovered_file in _limit_files(discovered_files, request.max_files):
        discovered_count += 1
        route_plan = classify_file(discovered_file.extension, discovered_file.mime_type)
        topic_map.topic_for_destination(route_plan.destination_queue_name)
        routed_count += 1
        if route_plan.route_type == "unsupported":
            unsupported_count += 1

    LOGGER.info(
        "dry_run_completed run_id=%s user_id=%s discovered=%s would_route=%s "
        "would_unsupported=%s",
        request.run_id,
        request.user_id,
        discovered_count,
        routed_count,
        unsupported_count,
    )
    return JobSummary(
        run_id=request.run_id,
        status="completed_dry_run",
        discovered_count=discovered_count,
        routed_count=routed_count,
        skipped_count=0,
        published_count=0,
        failed_publish_count=0,
    )


def _limit_files(
    discovered_files: Iterable[DiscoveredFile],
    max_files: int | None,
) -> Iterable[DiscoveredFile]:
    for index, discovered_file in enumerate(discovered_files):
        if max_files is not None and index >= max_files:
            break
        yield discovered_file


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _execution_id_from_env(env: dict[str, str]) -> str | None:
    execution = env.get("CLOUD_RUN_EXECUTION")
    if not execution:
        return None
    task_index = env.get("CLOUD_RUN_TASK_INDEX")
    return f"{execution}-task-{task_index}" if task_index else execution


def _pipeline_revision_from_env(env: Mapping[str, str]) -> str:
    for name in ("VISOR_PIPELINE_REVISION", "PIPELINE_REVISION"):
        value = env.get(name)
        if value and value.strip():
            return value.strip()
    return ROUTER_VERSION


if __name__ == "__main__":
    sys.exit(main())
