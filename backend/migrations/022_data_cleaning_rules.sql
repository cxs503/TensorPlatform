CREATE TABLE IF NOT EXISTS data_cleaning_rules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    source_mode VARCHAR(20) NOT NULL DEFAULT 'guided',
    definition JSONB NOT NULL,
    code_text TEXT,
    file_name VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    organization_id INTEGER REFERENCES organizations(id),
    created_by INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_data_cleaning_rule_mode
        CHECK (source_mode IN ('guided', 'code'))
);

CREATE TABLE IF NOT EXISTS data_cleaning_runs (
    id VARCHAR(36) PRIMARY KEY,
    rule_id INTEGER REFERENCES data_cleaning_rules(id) ON DELETE SET NULL,
    source_dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    output_dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    summary JSONB,
    error TEXT,
    organization_id INTEGER REFERENCES organizations(id),
    created_by INTEGER,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    CONSTRAINT ck_data_cleaning_run_status
        CHECK (status IN ('running', 'success', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_data_cleaning_rules_org
    ON data_cleaning_rules(organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_cleaning_runs_org
    ON data_cleaning_runs(organization_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_cleaning_runs_rule
    ON data_cleaning_runs(rule_id, started_at DESC);

