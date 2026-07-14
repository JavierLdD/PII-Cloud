-- Central Cloud SQL/Postgres schema for the deployed PII pipeline.
--
-- This file is the single schema entrypoint for cloud deployments. Keep it
-- additive and idempotent: it must be safe to run on an empty database and on a
-- database that already has older Proyecto tables.
--
-- Current coverage:
--   - File Discovery base tables
--   - Router routing decisions
--   - Cloud File Discovery + Router Job metadata/outbox fields
--   - Text Extract/PDF/Docs staging, page, chunk and materialization tables
--   - Table Extract materialization and file-summary tables
--   - Entity Text Extract summaries and accepted entities

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id UUID PRIMARY KEY,
    parent_run_id UUID REFERENCES ingestion_runs(run_id),
    source_type TEXT NOT NULL,
    source_root TEXT NOT NULL,
    source_scope_key TEXT,
    source_config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    pipeline_revision TEXT NOT NULL DEFAULT 'legacy-v1',
    status TEXT NOT NULL,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    enqueued_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    new_file_count INTEGER NOT NULL DEFAULT 0 CHECK (new_file_count >= 0),
    modified_file_count INTEGER NOT NULL DEFAULT 0 CHECK (modified_file_count >= 0),
    reused_file_count INTEGER NOT NULL DEFAULT 0 CHECK (reused_file_count >= 0),
    reprocessed_file_count INTEGER NOT NULL DEFAULT 0 CHECK (reprocessed_file_count >= 0),
    deleted_file_count INTEGER NOT NULL DEFAULT 0 CHECK (deleted_file_count >= 0),
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    snapshot_completed_at TIMESTAMPTZ,
    user_id TEXT,
    execution_id TEXT
);

CREATE TABLE IF NOT EXISTS files (
    file_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    external_id TEXT,
    file_name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    extension TEXT,
    mime_type TEXT,
    size_bytes BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
    checksum_sha256 CHAR(64),
    content_hash TEXT,
    etag TEXT,
    revision_key TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    snapshot_state TEXT NOT NULL DEFAULT 'new',
    previous_file_id UUID REFERENCES files(file_id),
    reused_from_file_id UUID REFERENCES files(file_id),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

CREATE TABLE IF NOT EXISTS queue_outbox (
    outbox_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    file_id UUID NOT NULL REFERENCES files(file_id),
    queue_name TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    idempotency_key TEXT,
    pubsub_message_id TEXT,
    pubsub_attributes JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS routing_decisions (
    routing_decision_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    file_id UUID NOT NULL REFERENCES files(file_id),
    source_queue_name TEXT NOT NULL,
    destination_queue_name TEXT,
    route_type TEXT NOT NULL,
    file_extension TEXT,
    file_mime_type TEXT,
    reason TEXT NOT NULL,
    router_version TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT,
    execution_id TEXT,
    idempotency_key TEXT
);

CREATE TABLE IF NOT EXISTS text_extraction_files (
    file_id UUID PRIMARY KEY REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    routing_decision_id UUID REFERENCES routing_decisions(routing_decision_id),
    status TEXT NOT NULL,
    total_pages INTEGER NOT NULL DEFAULT 0 CHECK (total_pages >= 0),
    completed_pages INTEGER NOT NULL DEFAULT 0 CHECK (completed_pages >= 0),
    pending_ocr_pages INTEGER NOT NULL DEFAULT 0 CHECK (pending_ocr_pages >= 0),
    failed_pages INTEGER NOT NULL DEFAULT 0 CHECK (failed_pages >= 0),
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    entity_outbox_id UUID REFERENCES queue_outbox(outbox_id),
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    processing_seconds DOUBLE PRECISION CHECK (
        processing_seconds IS NULL OR processing_seconds >= 0
    ),
    embedded_text_seconds DOUBLE PRECISION CHECK (
        embedded_text_seconds IS NULL OR embedded_text_seconds >= 0
    ),
    ocr_queue_wait_seconds DOUBLE PRECISION CHECK (
        ocr_queue_wait_seconds IS NULL OR ocr_queue_wait_seconds >= 0
    ),
    ocr_processing_seconds DOUBLE PRECISION CHECK (
        ocr_processing_seconds IS NULL OR ocr_processing_seconds >= 0
    ),
    ocr_processing_wall_seconds DOUBLE PRECISION CHECK (
        ocr_processing_wall_seconds IS NULL OR ocr_processing_wall_seconds >= 0
    ),
    cpu_user_seconds DOUBLE PRECISION CHECK (
        cpu_user_seconds IS NULL OR cpu_user_seconds >= 0
    ),
    cpu_system_seconds DOUBLE PRECISION CHECK (
        cpu_system_seconds IS NULL OR cpu_system_seconds >= 0
    ),
    cpu_total_seconds DOUBLE PRECISION CHECK (
        cpu_total_seconds IS NULL OR cpu_total_seconds >= 0
    ),
    peak_memory_mb DOUBLE PRECISION CHECK (
        peak_memory_mb IS NULL OR peak_memory_mb >= 0
    )
);

CREATE TABLE IF NOT EXISTS text_extraction_pages (
    file_id UUID NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    method TEXT NOT NULL CHECK (method IN ('pymupdf', 'ocr', 'both', 'doc')),
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0 CHECK (char_count >= 0),
    word_count INTEGER NOT NULL DEFAULT 0 CHECK (word_count >= 0),
    total_image_ratio DOUBLE PRECISION NOT NULL DEFAULT 0,
    largest_image_ratio DOUBLE PRECISION NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    ocr_outbox_id UUID REFERENCES queue_outbox(outbox_id),
    error TEXT,
    embedded_started_at TIMESTAMPTZ,
    embedded_completed_at TIMESTAMPTZ,
    embedded_processing_seconds DOUBLE PRECISION CHECK (
        embedded_processing_seconds IS NULL OR embedded_processing_seconds >= 0
    ),
    ocr_requested_at TIMESTAMPTZ,
    ocr_started_at TIMESTAMPTZ,
    ocr_completed_at TIMESTAMPTZ,
    ocr_queue_wait_seconds DOUBLE PRECISION CHECK (
        ocr_queue_wait_seconds IS NULL OR ocr_queue_wait_seconds >= 0
    ),
    ocr_processing_seconds DOUBLE PRECISION CHECK (
        ocr_processing_seconds IS NULL OR ocr_processing_seconds >= 0
    ),
    cpu_user_seconds DOUBLE PRECISION CHECK (
        cpu_user_seconds IS NULL OR cpu_user_seconds >= 0
    ),
    cpu_system_seconds DOUBLE PRECISION CHECK (
        cpu_system_seconds IS NULL OR cpu_system_seconds >= 0
    ),
    cpu_total_seconds DOUBLE PRECISION CHECK (
        cpu_total_seconds IS NULL OR cpu_total_seconds >= 0
    ),
    peak_memory_mb DOUBLE PRECISION CHECK (
        peak_memory_mb IS NULL OR peak_memory_mb >= 0
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (file_id, page_number)
);

CREATE TABLE IF NOT EXISTS text_ocr_batches (
    batch_id UUID PRIMARY KEY,
    file_id UUID NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    page_numbers JSONB NOT NULL,
    requested_device TEXT NOT NULL,
    effective_device TEXT NOT NULL,
    cuda_available BOOLEAN NOT NULL DEFAULT false,
    gpu_name TEXT,
    cuda_visible_devices TEXT,
    mineru_device TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    wall_seconds DOUBLE PRECISION NOT NULL CHECK (wall_seconds >= 0),
    mineru_command_count INTEGER NOT NULL DEFAULT 0 CHECK (
        mineru_command_count >= 0
    ),
    fallback_level TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS text_chunks_staging (
    chunk_id TEXT PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    file_id UUID NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 1),
    page_start INTEGER NOT NULL CHECK (page_start >= 1),
    page_end INTEGER NOT NULL CHECK (page_end >= page_start),
    text TEXT NOT NULL,
    text_hash_sha256 CHAR(64) NOT NULL,
    source_map JSONB NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (file_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS text_materialization_leases (
    lease_id UUID PRIMARY KEY,
    file_id UUID NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    worker_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    local_path TEXT,
    expected_bytes BIGINT CHECK (expected_bytes IS NULL OR expected_bytes >= 0),
    actual_bytes BIGINT NOT NULL DEFAULT 0 CHECK (actual_bytes >= 0),
    is_oversize BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL,
    reason TEXT,
    error TEXT,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS text_pdf_processing_attempts (
    file_id UUID PRIMARY KEY REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    routing_decision_id UUID REFERENCES routing_decisions(routing_decision_id),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'quarantined')),
    first_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_attempt_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error_type TEXT,
    last_error_message TEXT,
    last_error_traceback TEXT,
    last_result_status TEXT,
    quarantined_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS table_materialization_leases (
    lease_id UUID PRIMARY KEY,
    file_id UUID NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    worker_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    local_path TEXT,
    expected_bytes BIGINT CHECK (expected_bytes IS NULL OR expected_bytes >= 0),
    actual_bytes BIGINT NOT NULL DEFAULT 0 CHECK (actual_bytes >= 0),
    is_oversize BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL,
    reason TEXT,
    error TEXT,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS table_extraction_files (
    file_id UUID PRIMARY KEY REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    routing_decision_id UUID REFERENCES routing_decisions(routing_decision_id),
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    processing_seconds DOUBLE PRECISION CHECK (
        processing_seconds IS NULL OR processing_seconds >= 0
    ),
    cpu_user_seconds DOUBLE PRECISION CHECK (
        cpu_user_seconds IS NULL OR cpu_user_seconds >= 0
    ),
    cpu_system_seconds DOUBLE PRECISION CHECK (
        cpu_system_seconds IS NULL OR cpu_system_seconds >= 0
    ),
    cpu_total_seconds DOUBLE PRECISION CHECK (
        cpu_total_seconds IS NULL OR cpu_total_seconds >= 0
    ),
    peak_memory_mb DOUBLE PRECISION CHECK (
        peak_memory_mb IS NULL OR peak_memory_mb >= 0
    ),
    table_count INTEGER CHECK (table_count IS NULL OR table_count >= 0),
    column_count INTEGER CHECK (column_count IS NULL OR column_count >= 0),
    finding_count INTEGER CHECK (finding_count IS NULL OR finding_count >= 0),
    profile_json_path TEXT,
    discovery_json_path TEXT,
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entity_extraction_files (
    file_id UUID PRIMARY KEY REFERENCES files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    status TEXT NOT NULL,
    raw_entity_count INTEGER NOT NULL DEFAULT 0 CHECK (raw_entity_count >= 0),
    accepted_entity_count INTEGER NOT NULL DEFAULT 0 CHECK (
        accepted_entity_count >= 0
    ),
    raw_json_path TEXT,
    filtered_json_path TEXT,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    processing_seconds DOUBLE PRECISION CHECK (
        processing_seconds IS NULL OR processing_seconds >= 0
    ),
    cpu_user_seconds DOUBLE PRECISION CHECK (
        cpu_user_seconds IS NULL OR cpu_user_seconds >= 0
    ),
    cpu_system_seconds DOUBLE PRECISION CHECK (
        cpu_system_seconds IS NULL OR cpu_system_seconds >= 0
    ),
    cpu_total_seconds DOUBLE PRECISION CHECK (
        cpu_total_seconds IS NULL OR cpu_total_seconds >= 0
    ),
    peak_memory_mb DOUBLE PRECISION CHECK (
        peak_memory_mb IS NULL OR peak_memory_mb >= 0
    ),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entity_extraction_entities (
    file_id UUID NOT NULL REFERENCES entity_extraction_files(file_id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    entity_id TEXT NOT NULL,
    entity_index INTEGER NOT NULL CHECK (entity_index >= 0),
    entity_type TEXT NOT NULL,
    text TEXT NOT NULL,
    normalized_value TEXT,
    value_key TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_entity_type TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL CHECK (score >= 0),
    is_base BOOLEAN NOT NULL,
    validation_status TEXT NOT NULL,
    confidence_level TEXT,
    decision_score DOUBLE PRECISION CHECK (
        decision_score IS NULL OR decision_score >= 0
    ),
    decision_method TEXT,
    zero_shot_score DOUBLE PRECISION CHECK (
        zero_shot_score IS NULL OR zero_shot_score >= 0
    ),
    zero_shot_label TEXT,
    primary_location JSONB NOT NULL,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (file_id, entity_id),
    UNIQUE (file_id, entity_index)
);

-- BBDD Discovery results are deliberately separate from ingestion_runs/files.
-- These tables persist only structural metadata and aggregate detector output;
-- target connection strings, source URIs, evidence text and sampled values are
-- never stored here.
CREATE TABLE IF NOT EXISTS database_discovery_runs (
    run_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    run_name VARCHAR(120) NOT NULL,
    database_type TEXT NOT NULL CHECK (database_type IN ('postgresql', 'oracle')),
    source_name TEXT NOT NULL,
    artifact_uri TEXT NOT NULL CHECK (artifact_uri LIKE 'gs://%'),
    artifact_schema_version TEXT NOT NULL,
    artifact_size_bytes BIGINT NOT NULL CHECK (artifact_size_bytes >= 0),
    artifact_sha256 CHAR(64) NOT NULL,
    status TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    processing_seconds DOUBLE PRECISION CHECK (
        processing_seconds IS NULL OR processing_seconds >= 0
    ),
    peak_memory_mb DOUBLE PRECISION CHECK (
        peak_memory_mb IS NULL OR peak_memory_mb >= 0
    ),
    schema_count INTEGER NOT NULL DEFAULT 0 CHECK (schema_count >= 0),
    table_count INTEGER NOT NULL DEFAULT 0 CHECK (table_count >= 0),
    view_count INTEGER NOT NULL DEFAULT 0 CHECK (view_count >= 0),
    column_count INTEGER NOT NULL DEFAULT 0 CHECK (column_count >= 0),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    affected_schema_count INTEGER NOT NULL DEFAULT 0 CHECK (
        affected_schema_count >= 0
    ),
    affected_table_count INTEGER NOT NULL DEFAULT 0 CHECK (
        affected_table_count >= 0
    ),
    affected_column_count INTEGER NOT NULL DEFAULT 0 CHECK (
        affected_column_count >= 0
    ),
    pii_type_count INTEGER NOT NULL DEFAULT 0 CHECK (pii_type_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (completed_at >= started_at)
);

CREATE TABLE IF NOT EXISTS database_discovery_tables (
    table_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES database_discovery_runs(run_id) ON DELETE CASCADE,
    schema_name TEXT,
    table_name TEXT NOT NULL,
    table_type TEXT NOT NULL,
    row_count BIGINT CHECK (row_count IS NULL OR row_count >= 0),
    column_count INTEGER NOT NULL DEFAULT 0 CHECK (column_count >= 0),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS database_discovery_findings (
    finding_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES database_discovery_runs(run_id) ON DELETE CASCADE,
    finding_index INTEGER NOT NULL CHECK (finding_index >= 0),
    schema_name TEXT,
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    pii_type TEXT NOT NULL,
    confidence DOUBLE PRECISION CHECK (
        confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
    ),
    confidence_level TEXT,
    detection_method TEXT,
    sampled_count INTEGER CHECK (sampled_count IS NULL OR sampled_count >= 0),
    matched_count INTEGER CHECK (matched_count IS NULL OR matched_count >= 0),
    is_primary_key BOOLEAN NOT NULL DEFAULT false,
    foreign_key TEXT,
    propagated_from TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, finding_index),
    CHECK (
        sampled_count IS NULL
        OR matched_count IS NULL
        OR matched_count <= sampled_count
    )
);

-- Upgrade path for databases created with the older local schemas. These are
-- intentionally repeated after CREATE TABLE IF NOT EXISTS because existing
-- tables are not altered by CREATE TABLE IF NOT EXISTS.
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

-- Conservative backfill: old runs receive a stable scope but retain a legacy
-- pipeline revision, so their results are never assumed compatible with a new
-- pipeline revision automatically.
UPDATE ingestion_runs
SET source_scope_key = source_root
WHERE source_scope_key IS NULL;

ALTER TABLE ingestion_runs
    ALTER COLUMN source_scope_key SET NOT NULL;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS execution_id TEXT;

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

-- Older schemas made a source URI globally unique and the discovery code moved
-- that row between runs. Snapshots need one immutable row per run instead.
ALTER TABLE files
    DROP CONSTRAINT IF EXISTS files_source_type_source_uri_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_files_run_source_uri
    ON files(run_id, source_type, source_uri);

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

ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS processing_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS embedded_text_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS ocr_queue_wait_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS ocr_processing_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS ocr_processing_wall_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_user_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_system_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_total_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_files
    ADD COLUMN IF NOT EXISTS peak_memory_mb DOUBLE PRECISION;

ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS embedded_started_at TIMESTAMPTZ;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS embedded_completed_at TIMESTAMPTZ;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS embedded_processing_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS ocr_requested_at TIMESTAMPTZ;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS ocr_started_at TIMESTAMPTZ;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS ocr_completed_at TIMESTAMPTZ;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS ocr_queue_wait_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS ocr_processing_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS cpu_user_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS cpu_system_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS cpu_total_seconds DOUBLE PRECISION;
ALTER TABLE text_extraction_pages
    ADD COLUMN IF NOT EXISTS peak_memory_mb DOUBLE PRECISION;

ALTER TABLE text_extraction_pages
    DROP CONSTRAINT IF EXISTS text_extraction_pages_method_check;
ALTER TABLE text_extraction_pages
    ADD CONSTRAINT text_extraction_pages_method_check
    CHECK (method IN ('pymupdf', 'ocr', 'both', 'doc'));

ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS routing_decision_id UUID;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_user_seconds DOUBLE PRECISION;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_system_seconds DOUBLE PRECISION;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_total_seconds DOUBLE PRECISION;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS peak_memory_mb DOUBLE PRECISION;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS table_count INTEGER;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS column_count INTEGER;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS finding_count INTEGER;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS profile_json_path TEXT;
ALTER TABLE table_extraction_files
    ADD COLUMN IF NOT EXISTS discovery_json_path TEXT;

ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS raw_entity_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS accepted_entity_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS raw_json_path TEXT;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS filtered_json_path TEXT;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS processing_seconds DOUBLE PRECISION;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_user_seconds DOUBLE PRECISION;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_system_seconds DOUBLE PRECISION;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS cpu_total_seconds DOUBLE PRECISION;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS peak_memory_mb DOUBLE PRECISION;
ALTER TABLE entity_extraction_files
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source_type
    ON ingestion_runs(source_type);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_user_id
    ON ingestion_runs(user_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_execution_id
    ON ingestion_runs(execution_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_parent_run_id
    ON ingestion_runs(parent_run_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_user_scope_snapshot
    ON ingestion_runs(
        user_id,
        source_type,
        source_scope_key,
        snapshot_completed_at
    );

CREATE INDEX IF NOT EXISTS idx_files_run_id
    ON files(run_id);

CREATE INDEX IF NOT EXISTS idx_files_status
    ON files(status);

CREATE INDEX IF NOT EXISTS idx_files_source_type
    ON files(source_type);

CREATE INDEX IF NOT EXISTS idx_files_external_id
    ON files(external_id);

CREATE INDEX IF NOT EXISTS idx_files_checksum_sha256
    ON files(checksum_sha256);

CREATE INDEX IF NOT EXISTS idx_files_content_hash
    ON files(content_hash);

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

CREATE INDEX IF NOT EXISTS idx_file_snapshot_tombstones_previous_file_id
    ON file_snapshot_tombstones(previous_file_id);

CREATE INDEX IF NOT EXISTS idx_queue_outbox_status
    ON queue_outbox(status);

CREATE INDEX IF NOT EXISTS idx_queue_outbox_queue_name
    ON queue_outbox(queue_name);

CREATE INDEX IF NOT EXISTS idx_queue_outbox_run_id
    ON queue_outbox(run_id);

CREATE INDEX IF NOT EXISTS idx_queue_outbox_file_id
    ON queue_outbox(file_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_outbox_idempotency_key
    ON queue_outbox(idempotency_key);

CREATE INDEX IF NOT EXISTS idx_queue_outbox_pubsub_message_id
    ON queue_outbox(pubsub_message_id);

CREATE INDEX IF NOT EXISTS idx_routing_decisions_file_id
    ON routing_decisions(file_id);

CREATE INDEX IF NOT EXISTS idx_routing_decisions_run_id
    ON routing_decisions(run_id);

CREATE INDEX IF NOT EXISTS idx_routing_decisions_destination_queue_name
    ON routing_decisions(destination_queue_name);

CREATE INDEX IF NOT EXISTS idx_routing_decisions_status
    ON routing_decisions(status);

CREATE INDEX IF NOT EXISTS idx_routing_decisions_user_id
    ON routing_decisions(user_id);

CREATE INDEX IF NOT EXISTS idx_routing_decisions_execution_id
    ON routing_decisions(execution_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_routing_decisions_idempotency_key
    ON routing_decisions(idempotency_key);

CREATE INDEX IF NOT EXISTS idx_text_extraction_files_run_id
    ON text_extraction_files(run_id);

CREATE INDEX IF NOT EXISTS idx_text_extraction_files_status
    ON text_extraction_files(status);

CREATE INDEX IF NOT EXISTS idx_text_extraction_pages_run_id
    ON text_extraction_pages(run_id);

CREATE INDEX IF NOT EXISTS idx_text_extraction_pages_status
    ON text_extraction_pages(status);

CREATE INDEX IF NOT EXISTS idx_text_extraction_pages_ocr_outbox_id
    ON text_extraction_pages(ocr_outbox_id);

CREATE INDEX IF NOT EXISTS idx_text_ocr_batches_file_id
    ON text_ocr_batches(file_id);

CREATE INDEX IF NOT EXISTS idx_text_ocr_batches_run_id
    ON text_ocr_batches(run_id);

CREATE INDEX IF NOT EXISTS idx_text_chunks_staging_file_id
    ON text_chunks_staging(file_id);

CREATE INDEX IF NOT EXISTS idx_text_chunks_staging_status
    ON text_chunks_staging(status);

CREATE INDEX IF NOT EXISTS idx_text_chunks_staging_expires_at
    ON text_chunks_staging(expires_at);

CREATE INDEX IF NOT EXISTS idx_text_materialization_leases_file_id
    ON text_materialization_leases(file_id);

CREATE INDEX IF NOT EXISTS idx_text_materialization_leases_status
    ON text_materialization_leases(status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_text_materialization_one_active_per_file
    ON text_materialization_leases(file_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_text_pdf_processing_attempts_status
    ON text_pdf_processing_attempts(status);

CREATE INDEX IF NOT EXISTS idx_table_materialization_leases_file_id
    ON table_materialization_leases(file_id);

CREATE INDEX IF NOT EXISTS idx_table_materialization_leases_status
    ON table_materialization_leases(status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_table_materialization_one_active_per_file
    ON table_materialization_leases(file_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_table_extraction_files_run_id
    ON table_extraction_files(run_id);

CREATE INDEX IF NOT EXISTS idx_table_extraction_files_status
    ON table_extraction_files(status);

CREATE INDEX IF NOT EXISTS idx_entity_extraction_files_run_id
    ON entity_extraction_files(run_id);

CREATE INDEX IF NOT EXISTS idx_entity_extraction_files_status
    ON entity_extraction_files(status);

CREATE INDEX IF NOT EXISTS idx_entity_extraction_entities_run_id
    ON entity_extraction_entities(run_id);

CREATE INDEX IF NOT EXISTS idx_entity_extraction_entities_entity_type
    ON entity_extraction_entities(entity_type);

CREATE INDEX IF NOT EXISTS idx_database_discovery_runs_user_id
    ON database_discovery_runs(user_id);

CREATE INDEX IF NOT EXISTS idx_database_discovery_runs_user_completed_at
    ON database_discovery_runs(user_id, completed_at DESC);

CREATE INDEX IF NOT EXISTS idx_database_discovery_runs_database_type
    ON database_discovery_runs(database_type);

CREATE INDEX IF NOT EXISTS idx_database_discovery_tables_run_id
    ON database_discovery_tables(run_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_database_discovery_tables_identity
    ON database_discovery_tables(
        run_id,
        COALESCE(schema_name, ''),
        table_name,
        table_type
    );

CREATE INDEX IF NOT EXISTS idx_database_discovery_tables_schema_name
    ON database_discovery_tables(run_id, schema_name);

CREATE INDEX IF NOT EXISTS idx_database_discovery_findings_run_id
    ON database_discovery_findings(run_id);

CREATE INDEX IF NOT EXISTS idx_database_discovery_findings_pii_type
    ON database_discovery_findings(run_id, pii_type);

CREATE INDEX IF NOT EXISTS idx_database_discovery_findings_schema_table
    ON database_discovery_findings(run_id, schema_name, table_name);

CREATE INDEX IF NOT EXISTS idx_database_discovery_findings_confidence_level
    ON database_discovery_findings(run_id, confidence_level);

CREATE INDEX IF NOT EXISTS idx_database_discovery_findings_detection_method
    ON database_discovery_findings(run_id, detection_method);
