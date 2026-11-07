-- PROPOSAL ONLY — not applied. Placed outside backend/migrations/ on purpose:
-- backend/migrations/run_migration.py auto-applies every pending NNN_*.sql, so a
-- schema change of this kind must be reviewed by the platform owner first
-- (TensorPlatform is frozen for platform development; see bootstrap ECOSYSTEM-MAP).
--
-- Target: PostgreSQL. Rollback statements are at the bottom.
--
-- Companion code: src/api/routes/batch_missions.py (auto-detects presence and
-- degrades to sequential single-row submit while this is not applied).

BEGIN;

-- 1) batch identity on the mission row
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS batch_id UUID;

CREATE INDEX IF NOT EXISTS ix_tensor_missions_batch
    ON tensor_dev_missions (batch_id, id);

-- 2) batch registry: one row per accepted POST /tensor/missions/batch call.
--    idempotency_key is UNIQUE so a replay of the same array is rejected by the
--    database itself, not by an application-level check (race safe).
CREATE TABLE IF NOT EXISTS tensor_mission_batches (
    batch_id        UUID PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    requested_count INTEGER NOT NULL CHECK (requested_count > 0),
    mission_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    mode            VARCHAR(24) NOT NULL DEFAULT 'atomic'
                    CHECK (mode IN ('atomic','degraded_sequential')),
    created_by      VARCHAR(120),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tensor_mission_batches_org
    ON tensor_mission_batches (organization_id, created_at DESC);

COMMIT;

-- Rollback (owner/maintenance window):
--   DROP TABLE IF EXISTS tensor_mission_batches;
--   DROP INDEX IF EXISTS ix_tensor_missions_batch;
--   ALTER TABLE tensor_dev_missions DROP COLUMN IF EXISTS batch_id;