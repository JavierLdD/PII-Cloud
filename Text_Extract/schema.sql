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
    mineru_command_count INTEGER NOT NULL DEFAULT 0 CHECK (mineru_command_count >= 0),
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
