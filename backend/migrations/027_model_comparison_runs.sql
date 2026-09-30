CREATE TABLE IF NOT EXISTS model_comparison_runs (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    model_ids JSONB NOT NULL,
    dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    task_type VARCHAR(32) NOT NULL,
    target_column VARCHAR(256) NOT NULL,
    feature_columns JSONB NOT NULL,
    primary_metric VARCHAR(32) NOT NULL,
    results JSONB NOT NULL DEFAULT '[]'::jsonb,
    winner_model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'completed',
    organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_comparison_runs_org_created
    ON model_comparison_runs(organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_comparison_runs_dataset
    ON model_comparison_runs(dataset_id, created_at DESC);
