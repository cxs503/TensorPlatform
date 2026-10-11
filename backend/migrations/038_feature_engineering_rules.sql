CREATE TABLE IF NOT EXISTS feature_engineering_rules (
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
    CONSTRAINT ck_feature_engineering_rule_mode
        CHECK (source_mode IN ('guided', 'code'))
);

CREATE INDEX IF NOT EXISTS idx_feature_engineering_rules_org
    ON feature_engineering_rules(organization_id, created_at DESC);
