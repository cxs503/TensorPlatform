-- 111_mission_acceptance_criteria_freeze.sql
BEGIN;
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS acceptance_criteria_sha256 CHAR(64);
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS acceptance_criteria_frozen_at TIMESTAMPTZ;
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS acceptance_criteria_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS acceptance_criteria_freeze_source VARCHAR(32) NOT NULL DEFAULT 'legacy_unfrozen'
    CHECK (acceptance_criteria_freeze_source IN ('mission_created', 'legacy_start', 'legacy_unfrozen'));

CREATE OR REPLACE FUNCTION protect_frozen_mission_acceptance_criteria()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.acceptance_criteria_frozen_at IS NOT NULL AND (
        NEW.acceptance_criteria IS DISTINCT FROM OLD.acceptance_criteria
        OR NEW.acceptance_criteria_sha256 IS DISTINCT FROM OLD.acceptance_criteria_sha256
        OR NEW.acceptance_criteria_frozen_at IS DISTINCT FROM OLD.acceptance_criteria_frozen_at
        OR NEW.acceptance_criteria_version IS DISTINCT FROM OLD.acceptance_criteria_version
        OR NEW.acceptance_criteria_freeze_source IS DISTINCT FROM OLD.acceptance_criteria_freeze_source
    ) THEN
        RAISE EXCEPTION 'mission % acceptance criteria are frozen and immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_protect_frozen_mission_acceptance_criteria ON tensor_dev_missions;
CREATE TRIGGER trg_protect_frozen_mission_acceptance_criteria
BEFORE UPDATE ON tensor_dev_missions FOR EACH ROW
EXECUTE FUNCTION protect_frozen_mission_acceptance_criteria();
CREATE INDEX IF NOT EXISTS ix_tensor_missions_acceptance_frozen ON tensor_dev_missions (acceptance_criteria_frozen_at);
COMMIT;
