-- 103: mission gradient & exploration budget mechanism (IN19-Q6)
--
-- Five-step task gradient S1..S5 with a versioned seeding-mix registry, an
-- exploration-budget marker confined to S4/S5, an append-only negative-result
-- (S-N) track, and worker credit tiers L1..L5 used for dispatch admission.
--
-- Zero domain semantics: no vocabulary of any tenant domain is stored or
-- constrained here — only tiers, ratios, credit levels and outcome evidence.
--
-- Idempotent: every statement is IF NOT EXISTS / guarded, so re-running on a
-- partially migrated database is a no-op.

BEGIN;

-- ── 1. gradient fields on the mission row ───────────────────────────────
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS gradient_tier VARCHAR(2) NOT NULL DEFAULT 'S1';

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS exploration_budget JSONB;

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS gradient_self_declared BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS honest_fail BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS fail_root_cause TEXT;

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS retrospective TEXT;

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS negative_result_ref VARCHAR(120);

DO $$ BEGIN
    ALTER TABLE tensor_dev_missions
        ADD CONSTRAINT ck_tensor_dev_missions_gradient_tier
        CHECK (gradient_tier IN ('S1','S2','S3','S4','S5'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- An honest FAIL is only meaningful together with its diagnosis: the check is a
-- guard rail, not a replacement for services.mission_gradient.classify_outcome.
DO $$ BEGIN
    ALTER TABLE tensor_dev_missions
        ADD CONSTRAINT ck_tensor_dev_missions_honest_fail_evidence
        CHECK (
            honest_fail = FALSE
            OR (fail_root_cause IS NOT NULL AND length(btrim(fail_root_cause)) >= 8
                AND retrospective IS NOT NULL AND length(btrim(retrospective)) >= 20)
        );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Exploration budget marker only exists on S4/S5.
DO $$ BEGIN
    ALTER TABLE tensor_dev_missions
        ADD CONSTRAINT ck_tensor_dev_missions_exploration_scope
        CHECK (exploration_budget IS NULL OR gradient_tier IN ('S4','S5'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS ix_tensor_dev_missions_gradient
    ON tensor_dev_missions (organization_id, gradient_tier, created_at DESC);

-- ── 2. seeding-mix registry (adjustable, versioned, auditable) ──────────
CREATE TABLE IF NOT EXISTS mission_gradient_mix (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT,
    version         INTEGER NOT NULL,
    -- {"S1":0.60,"S2":0.25,"S3":0.10,"S4":0.04,"S5":0.01}
    mix             JSONB NOT NULL,
    signature       CHAR(16) NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    note            TEXT DEFAULT '',
    created_by      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_mission_gradient_mix_sum CHECK (
        abs( (mix->>'S1')::numeric + (mix->>'S2')::numeric + (mix->>'S3')::numeric
           + (mix->>'S4')::numeric + (mix->>'S5')::numeric - 1.0) <= 0.000001
        AND (mix->>'S1')::numeric >= 0 AND (mix->>'S2')::numeric >= 0
        AND (mix->>'S3')::numeric >= 0 AND (mix->>'S4')::numeric >= 0
        AND (mix->>'S5')::numeric >= 0
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mission_gradient_mix_version
    ON mission_gradient_mix (COALESCE(organization_id, 0), version);

-- exactly one active mix per organisation
CREATE UNIQUE INDEX IF NOT EXISTS uq_mission_gradient_mix_active
    ON mission_gradient_mix (COALESCE(organization_id, 0)) WHERE active;

-- ── 3. worker credit tiers L1..L5 (dispatch admission input) ────────────
-- organization_id = 0 means platform-global scope.  A plain UNIQUE constraint
-- (not a COALESCE expression index) keeps this table upsertable via
-- ``ON CONFLICT ON CONSTRAINT uq_worker_credit_tiers_worker`` — and NULL org ids
-- would never conflict, so the sentinel is what makes the upsert meaningful.
CREATE TABLE IF NOT EXISTS worker_credit_tiers (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL DEFAULT 0,
    worker_id       VARCHAR(120) NOT NULL,
    credit_level    SMALLINT NOT NULL,
    rationale       TEXT DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_worker_credit_tiers_level CHECK (credit_level BETWEEN 1 AND 5),
    CONSTRAINT uq_worker_credit_tiers_worker UNIQUE (organization_id, worker_id)
);

-- ── 4. negative-result track S-N (append-only) ──────────────────────────
CREATE TABLE IF NOT EXISTS mission_negative_results (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT,
    mission_id      BIGINT NOT NULL REFERENCES tensor_dev_missions(id) ON DELETE RESTRICT,
    tier            VARCHAR(2) NOT NULL,
    root_cause      TEXT NOT NULL,
    retrospective   TEXT NOT NULL,
    evidence_ref    TEXT DEFAULT '',
    reuse_tags      TEXT[] DEFAULT '{}',
    supersedes      BIGINT REFERENCES mission_negative_results(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_mission_negative_results_tier CHECK (tier IN ('S4','S5')),
    CONSTRAINT ck_mission_negative_results_root_cause CHECK (length(btrim(root_cause)) >= 8),
    CONSTRAINT ck_mission_negative_results_retrospective CHECK (length(btrim(retrospective)) >= 20)
);

CREATE INDEX IF NOT EXISTS ix_mission_negative_results_mission
    ON mission_negative_results (mission_id);

-- honest FAIL is append-only: no UPDATE, no DELETE.  A negative result that can
-- be edited later is not a negative result.
CREATE OR REPLACE FUNCTION mission_negative_results_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'mission_negative_results is append-only (attempted %) — append a new row instead', TG_OP;
END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_mission_negative_results_append_only
        BEFORE UPDATE OR DELETE ON mission_negative_results
        FOR EACH ROW EXECUTE FUNCTION mission_negative_results_append_only();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── 5. failure-rate view: quarantined honest FAILs are excluded ─────────
CREATE OR REPLACE VIEW worker_failure_rate_v1 AS
SELECT
    m.organization_id,
    m.claimed_by                                   AS worker_id,
    count(*)                                       AS attempts,
    count(*) FILTER (WHERE m.status = 'succeeded') AS successes,
    count(*) FILTER (WHERE m.status IN ('failed','stopped')
                       AND m.honest_fail = FALSE)  AS failures,
    count(*) FILTER (WHERE m.status IN ('failed','stopped')
                       AND m.honest_fail = TRUE)   AS quarantined_honest_fails,
    count(*) FILTER (WHERE m.honest_fail = TRUE)   AS honest_fails_total,
    CASE
        WHEN count(*) FILTER (WHERE m.honest_fail = FALSE) = 0 THEN NULL
        ELSE (count(*) FILTER (WHERE m.status IN ('failed','stopped') AND m.honest_fail = FALSE))::numeric
             / count(*) FILTER (WHERE m.honest_fail = FALSE)
    END                                            AS disciplined_failure_rate,
    CASE
        WHEN count(*) = 0 THEN NULL
        ELSE (count(*) FILTER (WHERE m.status IN ('failed','stopped')))::numeric / count(*)
    END                                            AS naive_failure_rate
FROM tensor_dev_missions m
WHERE m.status IN ('succeeded','failed','stopped')
  AND m.claimed_by IS NOT NULL
GROUP BY m.organization_id, m.claimed_by;

COMMIT;