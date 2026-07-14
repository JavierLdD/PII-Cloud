from __future__ import annotations

from collections.abc import Mapping
import uuid

from cloud_file_router_job.models import (
    DiscoveredFile,
    FileRegistration,
    OutboxRecord,
    RoutePlan,
    SnapshotCounters,
    StoredFile,
)
from cloud_file_router_job.pubsub import TopicMap, build_pubsub_attributes
from cloud_file_router_job.request import DiscoveryRouterRequest
from cloud_file_router_job.routing import (
    ROUTER_VERSION,
    build_idempotency_key,
    build_routed_payload,
)


SNAPSHOT_NEW = "new"
SNAPSHOT_MODIFIED = "modified"
SNAPSHOT_REUSED = "reused"
SNAPSHOT_REPROCESSED = "reprocessed"

_FILE_STATUS_BY_SNAPSHOT_STATE = {
    SNAPSHOT_NEW: "discovered",
    SNAPSHOT_MODIFIED: "changed",
    SNAPSHOT_REUSED: "unchanged",
    SNAPSHOT_REPROCESSED: "requeued",
}


class ActiveSourceRunError(RuntimeError):
    """Raised when another run is already inventorying the same user source."""


class ImmutableSnapshotError(RuntimeError):
    """Raised when a completed or internally inconsistent snapshot is reused."""


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def build_revision_key(discovered_file: DiscoveredFile) -> str | None:
    """Return a reliable content/version key, or None when none is available."""
    candidates = (
        ("sha256", discovered_file.checksum_sha256),
        ("content", discovered_file.content_hash),
        ("etag", discovered_file.etag),
    )
    for prefix, raw_value in candidates:
        value = _normalize_optional_text(raw_value)
        if value:
            return f"{prefix}:{value}"
    return None


def determine_snapshot_state(
    *,
    prior_exists: bool,
    current_revision_key: str | None,
    prior_revision_key: str | None,
    pipeline_compatible: bool,
    prior_result_reusable: bool,
    force_enqueue: bool,
) -> str:
    if not prior_exists:
        return SNAPSHOT_NEW
    if current_revision_key is None or prior_revision_key is None:
        return SNAPSHOT_MODIFIED
    if current_revision_key != prior_revision_key:
        return SNAPSHOT_MODIFIED
    if force_enqueue or not pipeline_compatible or not prior_result_reusable:
        return SNAPSHOT_REPROCESSED
    return SNAPSHOT_REUSED


def result_is_reusable(
    *,
    route_type: str | None,
    route_status: str | None,
    text_status: str | None,
    table_status: str | None,
    entity_status: str | None,
) -> bool:
    if route_type == "unsupported":
        return route_status == "unsupported"
    if route_type == "table":
        return table_status in {
            "table_profile_completed",
            "table_discovery_completed",
        }
    if route_type in {"pdf", "doc", "ocr"}:
        return (
            text_status == "text_extraction_completed"
            and entity_status == "entity_extraction_completed"
        )
    return False


def _file_identity_values(discovered_file: DiscoveredFile) -> tuple[object, ...]:
    return (
        discovered_file.file_name,
        discovered_file.relative_path,
        _normalize_optional_text(discovered_file.extension),
        _normalize_optional_text(discovered_file.mime_type),
        discovered_file.size_bytes,
        _normalize_optional_text(discovered_file.checksum_sha256),
        _normalize_optional_text(discovered_file.content_hash),
        _normalize_optional_text(discovered_file.etag),
    )


class PostgresFileRouterRepository:
    def __init__(
        self,
        database_url: str,
        *,
        pipeline_revision: str = ROUTER_VERSION,
    ) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Missing psycopg dependency.") from exc

        normalized_revision = _normalize_optional_text(pipeline_revision)
        if not normalized_revision:
            raise ValueError("pipeline_revision cannot be empty")
        self._pipeline_revision = normalized_revision
        self._conn = psycopg.connect(database_url)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PostgresFileRouterRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def check_connection(self) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    def ensure_cloud_schema(self) -> None:
        statements = (
            "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS user_id TEXT",
            "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS execution_id TEXT",
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "parent_run_id UUID REFERENCES ingestion_runs(run_id)"
            ),
            "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS source_scope_key TEXT",
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "pipeline_revision TEXT NOT NULL DEFAULT 'legacy-v1'"
            ),
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "snapshot_completed_at TIMESTAMPTZ"
            ),
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "new_file_count INTEGER NOT NULL DEFAULT 0"
            ),
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "modified_file_count INTEGER NOT NULL DEFAULT 0"
            ),
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "reused_file_count INTEGER NOT NULL DEFAULT 0"
            ),
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "reprocessed_file_count INTEGER NOT NULL DEFAULT 0"
            ),
            (
                "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS "
                "deleted_file_count INTEGER NOT NULL DEFAULT 0"
            ),
            (
                "UPDATE ingestion_runs SET source_scope_key = source_root "
                "WHERE source_scope_key IS NULL"
            ),
            "ALTER TABLE files ADD COLUMN IF NOT EXISTS revision_key TEXT",
            (
                "ALTER TABLE files ADD COLUMN IF NOT EXISTS "
                "snapshot_state TEXT NOT NULL DEFAULT 'legacy'"
            ),
            (
                "ALTER TABLE files ADD COLUMN IF NOT EXISTS "
                "previous_file_id UUID REFERENCES files(file_id)"
            ),
            (
                "ALTER TABLE files ADD COLUMN IF NOT EXISTS "
                "reused_from_file_id UUID REFERENCES files(file_id)"
            ),
            (
                "UPDATE files SET revision_key = CASE "
                "WHEN checksum_sha256 IS NOT NULL THEN 'sha256:' || checksum_sha256 "
                "WHEN content_hash IS NOT NULL THEN 'content:' || content_hash "
                "WHEN etag IS NOT NULL THEN 'etag:' || etag "
                "ELSE NULL END WHERE revision_key IS NULL"
            ),
            (
                "ALTER TABLE files DROP CONSTRAINT IF EXISTS "
                "files_source_type_source_uri_key"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_files_run_source_uri "
                "ON files(run_id, source_type, source_uri)"
            ),
            "ALTER TABLE queue_outbox ADD COLUMN IF NOT EXISTS idempotency_key TEXT",
            "ALTER TABLE queue_outbox ADD COLUMN IF NOT EXISTS pubsub_message_id TEXT",
            (
                "ALTER TABLE queue_outbox ADD COLUMN IF NOT EXISTS "
                "pubsub_attributes JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            "ALTER TABLE routing_decisions ADD COLUMN IF NOT EXISTS user_id TEXT",
            "ALTER TABLE routing_decisions ADD COLUMN IF NOT EXISTS execution_id TEXT",
            (
                "ALTER TABLE routing_decisions ADD COLUMN IF NOT EXISTS "
                "idempotency_key TEXT"
            ),
            (
                "CREATE TABLE IF NOT EXISTS file_snapshot_tombstones ("
                "tombstone_id UUID PRIMARY KEY, "
                "run_id UUID NOT NULL REFERENCES ingestion_runs(run_id), "
                "previous_file_id UUID NOT NULL REFERENCES files(file_id), "
                "source_type TEXT NOT NULL, "
                "source_uri TEXT NOT NULL, "
                "external_id TEXT, "
                "file_name TEXT NOT NULL, "
                "relative_path TEXT NOT NULL, "
                "revision_key TEXT, "
                "deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "UNIQUE (run_id, source_type, source_uri))"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_user_scope_snapshot "
                "ON ingestion_runs("
                "user_id, source_type, source_scope_key, snapshot_completed_at)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_parent_run_id "
                "ON ingestion_runs(parent_run_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_files_previous_file_id "
                "ON files(previous_file_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_files_reused_from_file_id "
                "ON files(reused_from_file_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_files_snapshot_state "
                "ON files(snapshot_state)"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_queue_outbox_idempotency_key "
                "ON queue_outbox(idempotency_key)"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_routing_decisions_idempotency_key "
                "ON routing_decisions(idempotency_key)"
            ),
        )
        try:
            with self._conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def create_run(
        self,
        request: DiscoveryRouterRequest,
        execution_id: str | None,
    ) -> str:
        from psycopg.types.json import Json

        source_config_json = {
            "folder_id": request.drive_folder_id,
            "recursive": True,
            "source_name": request.source_name,
            "force_enqueue": request.force_enqueue,
            "dry_run": request.dry_run,
            "max_files": request.max_files,
        }
        lock_key = (
            f"{len(request.user_id)}:{request.user_id}:{request.source_type}:"
            f"{request.source_scope_key}"
        )
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
                cursor.execute(
                    """
                    SELECT
                        user_id,
                        source_type,
                        source_scope_key,
                        pipeline_revision,
                        snapshot_completed_at
                    FROM ingestion_runs
                    WHERE run_id = %s
                    FOR UPDATE
                    """,
                    (request.run_id,),
                )
                existing_run = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT run_id
                    FROM ingestion_runs
                    WHERE user_id = %s
                      AND source_type = %s
                      AND source_scope_key = %s
                      AND finished_at IS NULL
                      AND run_id <> %s
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (
                        request.user_id,
                        request.source_type,
                        request.source_scope_key,
                        request.run_id,
                    ),
                )
                active_run = cursor.fetchone()
                if active_run is not None:
                    raise ActiveSourceRunError(
                        "another active run already exists for this user and source"
                    )

                if existing_run is not None:
                    expected_identity = (
                        request.user_id,
                        request.source_type,
                        request.source_scope_key,
                        self._pipeline_revision,
                    )
                    actual_identity = tuple(
                        _normalize_optional_text(value) for value in existing_run[:4]
                    )
                    if actual_identity != expected_identity:
                        raise ImmutableSnapshotError(
                            f"run_id {request.run_id} already belongs to "
                            "another snapshot"
                        )
                    if existing_run[4] is not None:
                        raise ImmutableSnapshotError(
                            f"run_id {request.run_id} is already a completed snapshot"
                        )
                    cursor.execute(
                        """
                        UPDATE ingestion_runs
                        SET
                            status = 'running',
                            execution_id = %s,
                            error = NULL,
                            finished_at = NULL
                        WHERE run_id = %s
                        """,
                        (execution_id, request.run_id),
                    )
                    self._conn.commit()
                    return request.run_id

                cursor.execute(
                    """
                    SELECT run_id
                    FROM ingestion_runs
                    WHERE user_id = %s
                      AND source_type = %s
                      AND source_scope_key = %s
                      AND snapshot_completed_at IS NOT NULL
                    ORDER BY snapshot_completed_at DESC, started_at DESC
                    LIMIT 1
                    """,
                    (
                        request.user_id,
                        request.source_type,
                        request.source_scope_key,
                    ),
                )
                parent_row = cursor.fetchone()
                parent_run_id = str(parent_row[0]) if parent_row else None

                cursor.execute(
                    """
                    INSERT INTO ingestion_runs (
                        run_id,
                        source_type,
                        source_root,
                        source_config_json,
                        status,
                        user_id,
                        execution_id,
                        parent_run_id,
                        source_scope_key,
                        pipeline_revision
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.run_id,
                        request.source_type,
                        request.drive_folder_id,
                        Json(source_config_json),
                        "running",
                        request.user_id,
                        execution_id,
                        parent_run_id,
                        request.source_scope_key,
                        self._pipeline_revision,
                    ),
                )
            self._conn.commit()
            return request.run_id
        except Exception:
            self._conn.rollback()
            raise

    def register_file(
        self,
        run_id: str,
        discovered_file: DiscoveredFile,
        force_enqueue: bool,
    ) -> FileRegistration:
        try:
            from psycopg.types.json import Json

            revision_key = build_revision_key(discovered_file)
            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        file_id, source_type, source_uri, external_id, file_name,
                        relative_path, extension, mime_type, size_bytes,
                        checksum_sha256, content_hash, etag, snapshot_state,
                        revision_key, previous_file_id, reused_from_file_id
                    FROM files
                    WHERE run_id = %s AND source_type = %s AND source_uri = %s
                    FOR UPDATE
                    """,
                    (run_id, discovered_file.source_type, discovered_file.source_uri),
                )
                current_file = cursor.fetchone()
                if current_file is not None:
                    stored_file = _stored_file_from_row(run_id, current_file)
                    stored_revision_key = _normalize_optional_text(current_file[13])
                    stored_identity = (
                        stored_file.file_name,
                        stored_file.relative_path,
                        _normalize_optional_text(stored_file.extension),
                        _normalize_optional_text(stored_file.mime_type),
                        stored_file.size_bytes,
                        _normalize_optional_text(stored_file.checksum_sha256),
                        _normalize_optional_text(stored_file.content_hash),
                        _normalize_optional_text(stored_file.etag),
                    )
                    if (
                        stored_revision_key != revision_key
                        or (
                            revision_key is None
                            and stored_identity
                            != _file_identity_values(discovered_file)
                        )
                    ):
                        raise ImmutableSnapshotError(
                            "a file changed while retrying the same snapshot run"
                        )
                    snapshot_state = str(current_file[12])
                    self._conn.commit()
                    return FileRegistration(
                        file_id=stored_file.file_id,
                        should_route=snapshot_state != SNAPSHOT_REUSED,
                        status=_FILE_STATUS_BY_SNAPSHOT_STATE.get(
                            snapshot_state,
                            snapshot_state,
                        ),
                        stored_file=stored_file,
                        snapshot_state=snapshot_state,
                        revision_key=stored_revision_key,
                        previous_file_id=(
                            str(current_file[14])
                            if current_file[14] is not None
                            else None
                        ),
                        reused_from_file_id=(
                            str(current_file[15])
                            if current_file[15] is not None
                            else None
                        ),
                    )

                cursor.execute(
                    """
                    SELECT user_id, source_scope_key, pipeline_revision
                    FROM ingestion_runs
                    WHERE run_id = %s
                    FOR SHARE
                    """,
                    (run_id,),
                )
                current_run = cursor.fetchone()
                if current_run is None:
                    raise RuntimeError(f"Unknown ingestion run: {run_id}")
                user_id = str(current_run[0])
                source_scope_key = str(current_run[1])
                pipeline_revision = str(current_run[2])

                cursor.execute(
                    """
                    SELECT
                        f.file_id,
                        f.revision_key,
                        f.reused_from_file_id,
                        previous_run.pipeline_revision
                    FROM files AS f
                    JOIN ingestion_runs AS previous_run
                      ON previous_run.run_id = f.run_id
                    WHERE previous_run.user_id = %s
                      AND previous_run.source_type = %s
                      AND previous_run.source_scope_key = %s
                      AND previous_run.snapshot_completed_at IS NOT NULL
                      AND f.run_id <> %s
                      AND f.source_type = %s
                      AND f.source_uri = %s
                    ORDER BY
                        previous_run.snapshot_completed_at DESC,
                        f.discovered_at DESC
                    LIMIT 1
                    """,
                    (
                        user_id,
                        discovered_file.source_type,
                        source_scope_key,
                        run_id,
                        discovered_file.source_type,
                        discovered_file.source_uri,
                    ),
                )
                previous_file = cursor.fetchone()

                previous_file_id: str | None = None
                reused_from_file_id: str | None = None
                prior_revision_key: str | None = None
                pipeline_compatible = False
                prior_result_reusable = False
                if previous_file is not None:
                    previous_file_id = str(previous_file[0])
                    prior_revision_key = _normalize_optional_text(previous_file[1])
                    effective_previous_file_id = str(
                        previous_file[2] or previous_file[0]
                    )
                    pipeline_compatible = str(previous_file[3]) == pipeline_revision
                    revisions_match = (
                        revision_key is not None
                        and prior_revision_key is not None
                        and revision_key == prior_revision_key
                    )
                    if revisions_match and pipeline_compatible and not force_enqueue:
                        prior_result_reusable = self._prior_result_is_reusable(
                            cursor,
                            effective_previous_file_id,
                        )
                    snapshot_state = determine_snapshot_state(
                        prior_exists=True,
                        current_revision_key=revision_key,
                        prior_revision_key=prior_revision_key,
                        pipeline_compatible=pipeline_compatible,
                        prior_result_reusable=prior_result_reusable,
                        force_enqueue=force_enqueue,
                    )
                    if snapshot_state == SNAPSHOT_REUSED:
                        reused_from_file_id = effective_previous_file_id
                else:
                    snapshot_state = SNAPSHOT_NEW

                file_id = str(uuid.uuid4())
                file_status = _FILE_STATUS_BY_SNAPSHOT_STATE[snapshot_state]
                cursor.execute(
                    """
                    INSERT INTO files (
                        file_id,
                        run_id,
                        source_type,
                        source_uri,
                        external_id,
                        file_name,
                        relative_path,
                        extension,
                        mime_type,
                        size_bytes,
                        checksum_sha256,
                        content_hash,
                        etag,
                        metadata_json,
                        status,
                        revision_key,
                        snapshot_state,
                        previous_file_id,
                        reused_from_file_id
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        file_id,
                        run_id,
                        discovered_file.source_type,
                        discovered_file.source_uri,
                        discovered_file.external_id,
                        discovered_file.file_name,
                        discovered_file.relative_path,
                        discovered_file.extension,
                        discovered_file.mime_type,
                        discovered_file.size_bytes,
                        discovered_file.checksum_sha256,
                        discovered_file.content_hash,
                        discovered_file.etag,
                        Json(discovered_file.metadata_json),
                        file_status,
                        revision_key,
                        snapshot_state,
                        previous_file_id,
                        reused_from_file_id,
                    ),
                )

            self._conn.commit()
            return FileRegistration(
                file_id=file_id,
                should_route=snapshot_state != SNAPSHOT_REUSED,
                status=file_status,
                stored_file=_stored_file_from_discovered(
                    run_id,
                    file_id,
                    discovered_file,
                ),
                snapshot_state=snapshot_state,
                revision_key=revision_key,
                previous_file_id=previous_file_id,
                reused_from_file_id=reused_from_file_id,
            )
        except Exception:
            self._conn.rollback()
            raise

    @staticmethod
    def _prior_result_is_reusable(cursor: object, file_id: str) -> bool:
        cursor.execute(
            """
            SELECT
                (
                    SELECT route_type
                    FROM routing_decisions
                    WHERE file_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                ),
                (
                    SELECT status
                    FROM routing_decisions
                    WHERE file_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                ),
                (SELECT status FROM text_extraction_files WHERE file_id = %s),
                (SELECT status FROM table_extraction_files WHERE file_id = %s),
                (SELECT status FROM entity_extraction_files WHERE file_id = %s)
            """,
            (file_id, file_id, file_id, file_id, file_id),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        route_type, route_status, text_status, table_status, entity_status = row
        return result_is_reusable(
            route_type=route_type,
            route_status=route_status,
            text_status=text_status,
            table_status=table_status,
            entity_status=entity_status,
        )

    def route_file(
        self,
        request: DiscoveryRouterRequest,
        stored_file: StoredFile,
        route_plan: RoutePlan,
        topic_map: TopicMap,
        execution_id: str | None,
    ) -> OutboxRecord:
        try:
            from psycopg.types.json import Json

            topic_name = topic_map.topic_for_destination(
                route_plan.destination_queue_name
            )
            idempotency_key = build_idempotency_key(
                request.run_id,
                stored_file,
                route_plan,
            )

            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO routing_decisions (
                        routing_decision_id,
                        run_id,
                        file_id,
                        source_queue_name,
                        destination_queue_name,
                        route_type,
                        file_extension,
                        file_mime_type,
                        reason,
                        router_version,
                        status,
                        error,
                        user_id,
                        execution_id,
                        idempotency_key
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (idempotency_key)
                    DO UPDATE SET
                        destination_queue_name = EXCLUDED.destination_queue_name,
                        route_type = EXCLUDED.route_type,
                        file_extension = EXCLUDED.file_extension,
                        file_mime_type = EXCLUDED.file_mime_type,
                        reason = EXCLUDED.reason,
                        router_version = EXCLUDED.router_version,
                        status = EXCLUDED.status,
                        error = NULL,
                        user_id = EXCLUDED.user_id,
                        execution_id = EXCLUDED.execution_id
                    RETURNING routing_decision_id
                    """,
                    (
                        str(uuid.uuid4()),
                        request.run_id,
                        stored_file.file_id,
                        "Queue-Archivos",
                        route_plan.destination_queue_name,
                        route_plan.route_type,
                        stored_file.extension,
                        stored_file.mime_type,
                        route_plan.reason,
                        ROUTER_VERSION,
                        route_plan.status,
                        None,
                        request.user_id,
                        execution_id,
                        idempotency_key,
                    ),
                )
                routing_decision_id = str(cursor.fetchone()[0])
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

                cursor.execute(
                    """
                    INSERT INTO queue_outbox (
                        outbox_id,
                        run_id,
                        file_id,
                        queue_name,
                        payload,
                        status,
                        idempotency_key,
                        pubsub_attributes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key)
                    DO UPDATE SET
                        queue_name = EXCLUDED.queue_name,
                        payload = EXCLUDED.payload,
                        pubsub_attributes = EXCLUDED.pubsub_attributes,
                        status = CASE
                            WHEN queue_outbox.status = 'published'
                            THEN queue_outbox.status
                            ELSE 'pending'
                        END,
                        last_error = CASE
                            WHEN queue_outbox.status = 'published'
                            THEN queue_outbox.last_error
                            ELSE NULL
                        END,
                        updated_at = now()
                    RETURNING outbox_id, status, pubsub_message_id
                    """,
                    (
                        str(uuid.uuid4()),
                        request.run_id,
                        stored_file.file_id,
                        topic_name,
                        Json(payload),
                        "pending",
                        idempotency_key,
                        Json(attributes),
                    ),
                )
                outbox_id, outbox_status, pubsub_message_id = cursor.fetchone()

                cursor.execute(
                    """
                    UPDATE files
                    SET status = %s, updated_at = now()
                    WHERE file_id = %s AND run_id = %s
                    """,
                    (route_plan.status, stored_file.file_id, request.run_id),
                )

            self._conn.commit()
            return OutboxRecord(
                outbox_id=str(outbox_id),
                topic_name=topic_name,
                payload=payload,
                attributes=attributes,
                status=str(outbox_status),
                pubsub_message_id=(
                    str(pubsub_message_id) if pubsub_message_id is not None else None
                ),
            )
        except Exception:
            self._conn.rollback()
            raise

    def mark_outbox_published(
        self,
        outbox_id: str,
        pubsub_message_id: str,
        attributes: Mapping[str, str],
    ) -> None:
        from psycopg.types.json import Json

        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE queue_outbox
                SET
                    status = %s,
                    pubsub_message_id = %s,
                    pubsub_attributes = %s,
                    published_at = now(),
                    updated_at = now(),
                    last_error = NULL
                WHERE outbox_id = %s
                """,
                ("published", pubsub_message_id, Json(dict(attributes)), outbox_id),
            )
        self._conn.commit()

    def record_outbox_error(self, outbox_id: str, error: str) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE queue_outbox
                SET
                    attempts = attempts + 1,
                    last_error = %s,
                    updated_at = now()
                WHERE outbox_id = %s
                """,
                (error[:2000], outbox_id),
            )
        self._conn.commit()

    def finalize_snapshot(self, run_id: str, expected_file_count: int) -> int:
        """Persist deletions only after the caller completed a full enumeration."""
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM files WHERE run_id = %s",
                    (run_id,),
                )
                stored_file_count = int(cursor.fetchone()[0])
                if stored_file_count != expected_file_count:
                    raise ImmutableSnapshotError(
                        "the retried snapshot no longer matches its first enumeration"
                    )
                cursor.execute(
                    """
                    SELECT parent_run_id
                    FROM ingestion_runs
                    WHERE run_id = %s
                    FOR UPDATE
                    """,
                    (run_id,),
                )
                run_row = cursor.fetchone()
                if run_row is None:
                    raise RuntimeError(f"Unknown ingestion run: {run_id}")
                parent_run_id = run_row[0]
                if parent_run_id is not None:
                    cursor.execute(
                        """
                        SELECT
                            previous.file_id,
                            previous.source_type,
                            previous.source_uri,
                            previous.external_id,
                            previous.file_name,
                            previous.relative_path,
                            previous.revision_key
                        FROM files AS previous
                        LEFT JOIN files AS current
                          ON current.run_id = %s
                         AND current.source_type = previous.source_type
                         AND current.source_uri = previous.source_uri
                        WHERE previous.run_id = %s
                          AND current.file_id IS NULL
                        ORDER BY previous.source_uri
                        """,
                        (run_id, parent_run_id),
                    )
                    deleted_files = list(cursor.fetchall())
                    for deleted_file in deleted_files:
                        cursor.execute(
                            """
                            INSERT INTO file_snapshot_tombstones (
                                tombstone_id,
                                run_id,
                                previous_file_id,
                                source_type,
                                source_uri,
                                external_id,
                                file_name,
                                relative_path,
                                revision_key
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (run_id, source_type, source_uri)
                            DO NOTHING
                            """,
                            (
                                str(uuid.uuid4()),
                                run_id,
                                *deleted_file,
                            ),
                        )
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM file_snapshot_tombstones
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )
                deleted_count = int(cursor.fetchone()[0])
            self._conn.commit()
            return deleted_count
        except Exception:
            self._conn.rollback()
            raise

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
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ingestion_runs
                SET
                    status = %s,
                    discovered_count = %s,
                    enqueued_count = %s,
                    skipped_count = %s,
                    new_file_count = %s,
                    modified_file_count = %s,
                    reused_file_count = %s,
                    reprocessed_file_count = %s,
                    deleted_file_count = %s,
                    error = %s,
                    snapshot_completed_at = CASE
                        WHEN %s THEN now()
                        ELSE NULL
                    END,
                    finished_at = now()
                WHERE run_id = %s
                """,
                (
                    status,
                    discovered_count,
                    routed_count,
                    skipped_count,
                    snapshot_counters.new_file_count,
                    snapshot_counters.modified_file_count,
                    snapshot_counters.reused_file_count,
                    snapshot_counters.reprocessed_file_count,
                    snapshot_counters.deleted_file_count,
                    error[:2000] if error else None,
                    snapshot_completed,
                    run_id,
                ),
            )
        self._conn.commit()


def _stored_file_from_discovered(
    run_id: str,
    file_id: str,
    discovered_file: DiscoveredFile,
) -> StoredFile:
    return StoredFile(
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


def _stored_file_from_row(run_id: str, row: tuple[object, ...]) -> StoredFile:
    return StoredFile(
        file_id=str(row[0]),
        run_id=run_id,
        source_type=str(row[1]),
        source_uri=str(row[2]),
        external_id=_normalize_optional_text(row[3]),
        file_name=str(row[4]),
        relative_path=str(row[5]),
        extension=str(row[6] or ""),
        mime_type=_normalize_optional_text(row[7]),
        size_bytes=int(row[8]) if row[8] is not None else None,
        checksum_sha256=_normalize_optional_text(row[9]),
        content_hash=_normalize_optional_text(row[10]),
        etag=_normalize_optional_text(row[11]),
    )
