CREATE TABLE IF NOT EXISTS dataset_governance_reports (
    id VARCHAR(36) PRIMARY KEY,
    dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    profile VARCHAR(32) NOT NULL,
    quality_rule_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(20) NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    summary JSONB NOT NULL,
    organization_id INTEGER REFERENCES organizations(id),
    created_by INTEGER,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_dataset_governance_report_status
        CHECK (status IN ('passed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_dataset_governance_reports_dataset
    ON dataset_governance_reports(dataset_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_dataset_governance_reports_org
    ON dataset_governance_reports(organization_id, checked_at DESC);

