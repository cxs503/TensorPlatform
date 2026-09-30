ALTER TABLE ab_experiments
    ADD COLUMN IF NOT EXISTS deployment_a_id INTEGER REFERENCES deployments(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS deployment_b_id INTEGER REFERENCES deployments(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();

UPDATE ab_experiments experiment
SET organization_id = model.organization_id
FROM models model
WHERE model.id = experiment.model_a_id
  AND experiment.organization_id IS NULL;

CREATE TABLE IF NOT EXISTS ab_experiment_requests (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL UNIQUE,
    experiment_id VARCHAR(64) NOT NULL REFERENCES ab_experiments(experiment_id) ON DELETE CASCADE,
    subject_id VARCHAR(256) NOT NULL,
    assigned_variant VARCHAR(1) NOT NULL CHECK (assigned_variant IN ('A', 'B')),
    served_variant VARCHAR(1) CHECK (served_variant IN ('A', 'B')),
    assigned_model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    served_model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    assigned_deployment_id INTEGER REFERENCES deployments(id) ON DELETE SET NULL,
    served_deployment_id INTEGER REFERENCES deployments(id) ON DELETE SET NULL,
    features JSONB NOT NULL,
    prediction JSONB,
    actual_value JSONB,
    metric_value DOUBLE PRECISION,
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms DOUBLE PRECISION,
    status VARCHAR(24) NOT NULL CHECK (status IN ('success', 'failed')),
    error TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    feedback_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_ab_requests_experiment_created
    ON ab_experiment_requests(experiment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ab_requests_experiment_feedback
    ON ab_experiment_requests(experiment_id, feedback_at)
    WHERE feedback_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ab_experiments_organization
    ON ab_experiments(organization_id, created_at DESC);
