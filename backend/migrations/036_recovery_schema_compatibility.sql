-- Recovery compatibility for databases restored from legacy snapshots.
-- Earlier migration versions may already be recorded while their schema changes
-- were not present in the restored physical backup.

ALTER TABLE users ADD COLUMN IF NOT EXISTS organization_id INTEGER;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS organization_id INTEGER;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS organization_id INTEGER;
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS validation_context JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE lineage_nodes ADD COLUMN IF NOT EXISTS resource_type VARCHAR(64);
ALTER TABLE lineage_nodes ADD COLUMN IF NOT EXISTS resource_id INTEGER;

DO $$
DECLARE
    only_org INTEGER;
BEGIN
    SELECT MIN(id) INTO only_org FROM organizations WHERE status = 'active';
    IF only_org IS NOT NULL AND (SELECT COUNT(*) FROM organizations WHERE status = 'active') = 1 THEN
        UPDATE users SET organization_id = only_org WHERE organization_id IS NULL;
        UPDATE pipelines SET organization_id = only_org WHERE organization_id IS NULL;
    END IF;
END $$;

UPDATE pipelines p
SET organization_id = u.organization_id
FROM users u
WHERE p.owner_id = u.id AND p.organization_id IS NULL AND u.organization_id IS NOT NULL;

UPDATE pipeline_runs r
SET organization_id = p.organization_id
FROM pipelines p
WHERE r.pipeline_id = p.id AND r.organization_id IS NULL;

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT fk_users_organization
        FOREIGN KEY (organization_id) REFERENCES organizations(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE pipelines ADD CONSTRAINT fk_pipelines_organization
        FOREIGN KEY (organization_id) REFERENCES organizations(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE pipeline_runs ADD CONSTRAINT fk_pipeline_runs_organization
        FOREIGN KEY (organization_id) REFERENCES organizations(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS ix_users_organization_id ON users(organization_id);
CREATE INDEX IF NOT EXISTS ix_pipelines_organization_id ON pipelines(organization_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_organization_id ON pipeline_runs(organization_id);
