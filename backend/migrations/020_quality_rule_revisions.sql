CREATE TABLE IF NOT EXISTS quality_rule_revisions (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES quality_rules(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    note TEXT,
    created_by INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    UNIQUE(rule_id, version)
);

CREATE INDEX IF NOT EXISTS idx_quality_rule_revisions_rule ON quality_rule_revisions(rule_id, version DESC);
