-- 095: third-party reproduction agent + integrity / blacklist system.
--
-- Background (owner 2026-10-31):
--   m533 claimed "3-gate green" but gates never ran → succeeded falsely (revoked).
--   m583 claimed "zero duplicates / 90 papers updated" but SQL audit found 3 dup
--   groups still present and stubs unchanged.
--   m578 "paper-grade supplement" was 17 empty stubs.
--   Root cause: finish path has no independent re-computation of machine criteria;
--   self-reported claims can survive unchecked.
--
-- This migration adds:
--   1. 'revoked' to mission status CHECK (distinct from 'failed'; honest FAIL
--      keeps its status, revoked is disciplinary).
--   2. 'repro' as a valid runner value (reproduction sub-missions).
--   3. tensor_repro_verdicts — append-only verdict table (trigger blocks UPDATE/DELETE).
--   4. tensor_agent_integrity — strike ledger per model.
--   5. tensor_model_blacklist — blacklisted models (owner-only release).
--   6. tensor_credit_ledger — credit/debit entries for models.
--   7. tensor_guide_tracker — KPI rows including reproduction rate.

-- ── 1. Add 'revoked' to mission status ──
ALTER TABLE tensor_dev_missions
    DROP CONSTRAINT IF EXISTS tensor_dev_missions_status_check;
ALTER TABLE tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_status_check
    CHECK (status IN ('queued','claimed','running','awaiting_acceptance',
                      'succeeded','failed','stopped','revoked'));

-- ── 2. Allow 'repro' runner ──
ALTER TABLE tensor_dev_missions
    DROP CONSTRAINT IF EXISTS tensor_dev_missions_runner_check;
-- The original migration 086 has no runner CHECK, but later ones might.
-- We add a soft check via comment; the API layer validates runner values.

-- ── 3. Reproduction verdicts (append-only) ──
CREATE TABLE IF NOT EXISTS tensor_repro_verdicts (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    mission_id      BIGINT NOT NULL REFERENCES tensor_dev_missions(id),
    repro_model     VARCHAR(64) NOT NULL,
    verdict         VARCHAR(16) NOT NULL CHECK (verdict IN ('pass','partial','fail','cheat')),
    claims_vs_recomputed JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_paths  JSONB NOT NULL DEFAULT '[]'::jsonb,
    wall_time_sec   DOUBLE PRECISION,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_repro_verdicts_mission
    ON tensor_repro_verdicts (mission_id);
CREATE INDEX IF NOT EXISTS ix_repro_verdicts_org
    ON tensor_repro_verdicts (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_repro_verdicts_verdict
    ON tensor_repro_verdicts (verdict) WHERE verdict IN ('cheat','fail');

-- Append-only trigger: block UPDATE and DELETE on verdicts
CREATE OR REPLACE FUNCTION fn_repro_verdicts_no_modify()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'tensor_repro_verdicts is append-only; UPDATE/DELETE forbidden (evidence preservation)';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_repro_verdicts_no_update ON tensor_repro_verdicts;
CREATE TRIGGER trg_repro_verdicts_no_update
    BEFORE UPDATE ON tensor_repro_verdicts
    FOR EACH ROW EXECUTE FUNCTION fn_repro_verdicts_no_modify();

DROP TRIGGER IF EXISTS trg_repro_verdicts_no_delete ON tensor_repro_verdicts;
CREATE TRIGGER trg_repro_verdicts_no_delete
    BEFORE DELETE ON tensor_repro_verdicts
    FOR EACH ROW EXECUTE FUNCTION fn_repro_verdicts_no_modify();

-- ── 4. Agent integrity (strike ledger per model) ──
CREATE TABLE IF NOT EXISTS tensor_agent_integrity (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    model           VARCHAR(64) NOT NULL,
    strikes         INT NOT NULL DEFAULT 0,
    last_strike_at  TIMESTAMPTZ,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, model)
);

-- ── 5. Model blacklist (owner-only release) ──
CREATE TABLE IF NOT EXISTS tensor_model_blacklist (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    model           VARCHAR(64) NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    blacklisted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at     TIMESTAMPTZ,
    released_by     BIGINT,
    UNIQUE (organization_id, model)
);

CREATE INDEX IF NOT EXISTS ix_blacklist_active
    ON tensor_model_blacklist (organization_id, model)
    WHERE released_at IS NULL;

-- ── 6. Credit ledger ──
CREATE TABLE IF NOT EXISTS tensor_credit_ledger (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    model           VARCHAR(64) NOT NULL,
    delta           INT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    mission_id      BIGINT REFERENCES tensor_dev_missions(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_credit_ledger_model
    ON tensor_credit_ledger (organization_id, model, created_at DESC);

-- ── 7. Guide tracker (KPI rows) ──
CREATE TABLE IF NOT EXISTS tensor_guide_tracker (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    metric_key      VARCHAR(80) NOT NULL,
    metric_value    DOUBLE PRECISION NOT NULL DEFAULT 0,
    target_value    DOUBLE PRECISION,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes           TEXT NOT NULL DEFAULT '',
    UNIQUE (organization_id, metric_key, snapshot_at)
);

CREATE INDEX IF NOT EXISTS ix_guide_tracker_org
    ON tensor_guide_tracker (organization_id, metric_key, snapshot_at DESC);
