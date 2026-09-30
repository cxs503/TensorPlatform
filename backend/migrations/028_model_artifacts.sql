CREATE TABLE IF NOT EXISTS model_artifacts (
    id BIGSERIAL PRIMARY KEY,
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    model_version_id INTEGER REFERENCES model_versions(id) ON DELETE SET NULL,
    format VARCHAR(24) NOT NULL,
    file_name VARCHAR(512) NOT NULL,
    file_path TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256 CHAR(64) NOT NULL,
    validation_status VARCHAR(24) NOT NULL DEFAULT 'generated',
    validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    benchmark_result JSONB,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    validated_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_model_artifacts_org_created
    ON model_artifacts(organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_artifacts_model_created
    ON model_artifacts(model_id, created_at DESC);
