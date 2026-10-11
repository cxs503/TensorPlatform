CREATE TABLE IF NOT EXISTS automl_selection_records (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    dataset_id INTEGER,
    dataset_name VARCHAR(255),
    target_column VARCHAR(255) NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    optimization_metric VARCHAR(32) NOT NULL,
    candidate_algorithms JSONB NOT NULL DEFAULT '[]'::jsonb,
    results JSONB NOT NULL DEFAULT '[]'::jsonb,
    best_algorithm VARCHAR(128),
    best_score DOUBLE PRECISION,
    best_metrics JSONB,
    organization_id INTEGER REFERENCES organizations(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_automl_selection_records_created
    ON automl_selection_records(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_automl_selection_records_org
    ON automl_selection_records(organization_id, created_at DESC);
