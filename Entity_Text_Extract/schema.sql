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

CREATE INDEX IF NOT EXISTS idx_entity_extraction_files_run_id
    ON entity_extraction_files(run_id);
CREATE INDEX IF NOT EXISTS idx_entity_extraction_files_status
    ON entity_extraction_files(status);
CREATE INDEX IF NOT EXISTS idx_entity_extraction_entities_run_id
    ON entity_extraction_entities(run_id);
CREATE INDEX IF NOT EXISTS idx_entity_extraction_entities_entity_type
    ON entity_extraction_entities(entity_type);
