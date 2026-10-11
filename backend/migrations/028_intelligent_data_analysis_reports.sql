CREATE TABLE IF NOT EXISTS intelligent_data_analysis_reports (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NULL REFERENCES organizations(id) ON DELETE SET NULL,
    dataset_id INTEGER NULL REFERENCES datasets(id) ON DELETE SET NULL,
    source_name VARCHAR(255) NOT NULL,
    profile JSONB NOT NULL,
    narrative TEXT NOT NULL,
    llm_provider VARCHAR(64),
    sample_shared BOOLEAN NOT NULL DEFAULT FALSE,
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_intelligent_analysis_org_created
    ON intelligent_data_analysis_reports (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intelligent_analysis_dataset
    ON intelligent_data_analysis_reports (dataset_id);
