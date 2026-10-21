-- Bind execution claims to authenticated accounts rather than caller-supplied names.
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS claimed_user_id BIGINT REFERENCES users(id);
CREATE INDEX IF NOT EXISTS ix_tensor_missions_org_claim
    ON tensor_dev_missions (organization_id, runner, id) WHERE status = 'queued';
