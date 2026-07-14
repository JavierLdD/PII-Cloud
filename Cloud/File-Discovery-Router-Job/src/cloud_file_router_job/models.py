from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DiscoveredFile:
    source_type: str
    source_uri: str
    external_id: str | None
    file_name: str
    relative_path: str
    extension: str
    mime_type: str | None
    size_bytes: int | None
    checksum_sha256: str | None = None
    content_hash: str | None = None
    etag: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredFile:
    file_id: str
    run_id: str
    source_type: str
    source_uri: str
    external_id: str | None
    file_name: str
    relative_path: str
    extension: str
    mime_type: str | None
    size_bytes: int | None
    checksum_sha256: str | None
    content_hash: str | None
    etag: str | None


@dataclass(frozen=True)
class FileRegistration:
    file_id: str
    should_route: bool
    status: str
    stored_file: StoredFile
    snapshot_state: str = "new"
    revision_key: str | None = None
    previous_file_id: str | None = None
    reused_from_file_id: str | None = None


@dataclass(frozen=True)
class RoutePlan:
    route_type: str
    destination_queue_name: str
    reason: str
    status: str


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: str
    topic_name: str
    payload: dict[str, Any]
    attributes: dict[str, str]
    status: str
    pubsub_message_id: str | None = None


@dataclass(frozen=True)
class JobSummary:
    run_id: str
    status: str
    discovered_count: int
    routed_count: int
    skipped_count: int
    published_count: int
    failed_publish_count: int = 0
    new_file_count: int = 0
    modified_file_count: int = 0
    reused_file_count: int = 0
    reprocessed_file_count: int = 0
    deleted_file_count: int = 0
    snapshot_completed: bool = False


@dataclass(frozen=True)
class SnapshotCounters:
    new_file_count: int = 0
    modified_file_count: int = 0
    reused_file_count: int = 0
    reprocessed_file_count: int = 0
    deleted_file_count: int = 0
