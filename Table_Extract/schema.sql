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

CREATE INDEX IF NOT EXISTS idx_table_materialization_leases_file_id
    ON table_materialization_leases(file_id);

CREATE INDEX IF NOT EXISTS idx_table_materialization_leases_status
    ON table_materialization_leases(status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_table_materialization_one_active_per_file
    ON table_materialization_leases(file_id)
    WHERE status = 'active';

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

CREATE INDEX IF NOT EXISTS idx_table_extraction_files_run_id
    ON table_extraction_files(run_id);

CREATE INDEX IF NOT EXISTS idx_table_extraction_files_status
    ON table_extraction_files(status);
