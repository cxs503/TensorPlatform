-- Bind users and legacy core resources to an organization so API tenant filters
-- can fail closed for non-admin users without hiding the existing single-tenant data.
ALTER TABLE users ADD COLUMN IF NOT EXISTS organization_id INTEGER;

DO $$
DECLARE
    only_org INTEGER;
BEGIN
    SELECT MIN(id) INTO only_org FROM organizations WHERE status = 'active';
    IF only_org IS NOT NULL AND (SELECT COUNT(*) FROM organizations WHERE status = 'active') = 1 THEN
        UPDATE users SET organization_id = only_org WHERE organization_id IS NULL;
        UPDATE datasets SET organization_id = only_org WHERE organization_id IS NULL;
        UPDATE models SET organization_id = only_org WHERE organization_id IS NULL;
        UPDATE training_jobs SET organization_id = only_org WHERE organization_id IS NULL;
    END IF;
END $$;

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT fk_users_organization
        FOREIGN KEY (organization_id) REFERENCES organizations(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS ix_users_organization_id ON users(organization_id);
CREATE INDEX IF NOT EXISTS ix_datasets_organization_id ON datasets(organization_id);
CREATE INDEX IF NOT EXISTS ix_models_organization_id ON models(organization_id);
CREATE INDEX IF NOT EXISTS ix_training_jobs_organization_id ON training_jobs(organization_id);
