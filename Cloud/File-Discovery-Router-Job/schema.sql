-- Cloud-only additive schema for File Discovery + Router Job.
-- Apply after File_Discovery/schema.sql and Router/schema.sql.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS execution_id TEXT;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS parent_run_id UUID REFERENCES ingestion_runs(run_id);

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS source_scope_key TEXT;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS pipeline_revision TEXT NOT NULL DEFAULT 'legacy-v1';

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS snapshot_completed_at TIMESTAMPTZ;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS new_file_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS modified_file_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS reused_file_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS reprocessed_file_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS deleted_file_count INTEGER NOT NULL DEFAULT 0;

UPDATE ingestion_runs
SET source_scope_key = source_root
WHERE source_scope_key IS NULL;

ALTER TABLE ingestion_runs
    ALTER COLUMN source_scope_key SET NOT NULL;

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS revision_key TEXT;

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS snapshot_state TEXT NOT NULL DEFAULT 'legacy';

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS previous_file_id UUID REFERENCES files(file_id);

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS reused_from_file_id UUID REFERENCES files(file_id);

UPDATE files
SET revision_key = CASE
    WHEN checksum_sha256 IS NOT NULL THEN 'sha256:' || checksum_sha256
    WHEN content_hash IS NOT NULL THEN 'content:' || content_hash
    WHEN etag IS NOT NULL THEN 'etag:' || etag
    ELSE NULL
END
WHERE revision_key IS NULL;

ALTER TABLE files
    DROP CONSTRAINT IF EXISTS files_source_type_source_uri_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_files_run_source_uri
    ON files(run_id, source_type, source_uri);

CREATE TABLE IF NOT EXISTS file_snapshot_tombstones (
    tombstone_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    previous_file_id UUID NOT NULL REFERENCES files(file_id),
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    external_id TEXT,
    file_name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    revision_key TEXT,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, source_type, source_uri)
);

ALTER TABLE queue_outbox
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

ALTER TABLE queue_outbox
    ADD COLUMN IF NOT EXISTS pubsub_message_id TEXT;

ALTER TABLE queue_outbox
    ADD COLUMN IF NOT EXISTS pubsub_attributes JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE routing_decisions
    ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE routing_decisions
    ADD COLUMN IF NOT EXISTS execution_id TEXT;

ALTER TABLE routing_decisions
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_outbox_idempotency_key
    ON queue_outbox(idempotency_key);

CREATE UNIQUE INDEX IF NOT EXISTS idx_routing_decisions_idempotency_key
    ON routing_decisions(idempotency_key);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_parent_run_id
    ON ingestion_runs(parent_run_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_user_scope_snapshot
    ON ingestion_runs(
        user_id,
        source_type,
        source_scope_key,
        snapshot_completed_at
    );

CREATE INDEX IF NOT EXISTS idx_files_revision_key
    ON files(revision_key);

CREATE INDEX IF NOT EXISTS idx_files_snapshot_state
    ON files(snapshot_state);

CREATE INDEX IF NOT EXISTS idx_files_previous_file_id
    ON files(previous_file_id);

CREATE INDEX IF NOT EXISTS idx_files_reused_from_file_id
    ON files(reused_from_file_id);

CREATE INDEX IF NOT EXISTS idx_file_snapshot_tombstones_run_id
    ON file_snapshot_tombstones(run_id);
