CREATE TABLE IF NOT EXISTS data_analysis_runs (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NULL REFERENCES organizations(id) ON DELETE SET NULL,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL,
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_data_analysis_runs_dataset ON data_analysis_runs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_data_analysis_runs_org ON data_analysis_runs(organization_id);
