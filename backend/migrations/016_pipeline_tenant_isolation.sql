-- Bind saved pipelines and their runs to organizations. Existing owner-bound
-- rows inherit the owner's organization; legacy single-tenant rows inherit the
-- sole active organization. Multi-tenant ambiguous rows remain NULL and are
-- deliberately hidden from non-admin users.
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS organization_id INTEGER;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS organization_id INTEGER;
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS validation_context JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE pipelines p
SET organization_id = u.organization_id
FROM users u
WHERE p.owner_id = u.id
  AND p.organization_id IS NULL
  AND u.organization_id IS NOT NULL;

DO $$
DECLARE
    only_org INTEGER;
BEGIN
    SELECT MIN(id) INTO only_org FROM organizations WHERE status = 'active';
    IF only_org IS NOT NULL
       AND (SELECT COUNT(*) FROM organizations WHERE status = 'active') = 1 THEN
        UPDATE pipelines SET organization_id = only_org WHERE organization_id IS NULL;
    END IF;
END $$;

UPDATE pipeline_runs r
SET organization_id = p.organization_id
FROM pipelines p
WHERE r.pipeline_id = p.id
  AND r.organization_id IS NULL;

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

CREATE INDEX IF NOT EXISTS ix_pipelines_organization_id ON pipelines(organization_id);
CREATE INDEX IF NOT EXISTS ix_pipelines_owner_id ON pipelines(owner_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_organization_id ON pipeline_runs(organization_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_pipeline_created
    ON pipeline_runs(pipeline_id, created_at DESC);
