-- TensorPhysics run registry: metadata only; bulk artifacts remain in artifact/object storage.
CREATE TABLE IF NOT EXISTS tensorphysics_runs (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    run_id VARCHAR(200) NOT NULL,
    status VARCHAR(24) NOT NULL CHECK (
        status IN ('planned','validating','queued','running','succeeded','failed','cancelled','preempted')
    ),
    validation_status VARCHAR(16) NOT NULL CHECK (
        validation_status IN ('not_run','passed','failed','incomplete')
    ),
    manifest JSONB NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL,
    created_by BIGINT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tensorphysics_runs_org_run UNIQUE (organization_id, run_id)
);

CREATE INDEX IF NOT EXISTS ix_tensorphysics_runs_org_created
    ON tensorphysics_runs (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_tensorphysics_runs_org_status
    ON tensorphysics_runs (organization_id, status, validation_status);

CREATE OR REPLACE FUNCTION tensorphysics_runs_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tensorphysics_runs_updated_at ON tensorphysics_runs;
CREATE TRIGGER trg_tensorphysics_runs_updated_at
BEFORE UPDATE ON tensorphysics_runs
FOR EACH ROW EXECUTE FUNCTION tensorphysics_runs_set_updated_at();
