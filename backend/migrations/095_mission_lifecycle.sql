-- 095: Mission lifecycle — suspend/resume, write-off, necessity gate, orphan detection.
-- Addresses owner 2026-10-31 two-line directive:
--   1) "需要有任务领取挂起和销账机制"
--   2) "任务的必要性没有把关"
-- Plus the four concrete symptoms: zombie queue, stop-not-effective, triple-duplicate
-- dispatch, invisible debt.

-- ── 1. Extend status CHECK to include 'suspended', 'orphan', 'revoked' ──
ALTER TABLE tensor_dev_missions
    DROP CONSTRAINT IF EXISTS tensor_dev_missions_status_check;
ALTER TABLE tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_status_check
    CHECK (status IN ('queued','claimed','running','awaiting_acceptance',
                      'succeeded','failed','stopped','revoked',
                      'suspended','orphan'));

-- ── 2. Suspend metadata columns ──
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS suspend_reason TEXT;
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS suspended_by BIGINT REFERENCES users(id);
-- Budget snapshot at suspend time: {wall_used_s, tool_calls_used, tokens_used}
-- Resume continues from this baseline (no zero, no re-count).
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS budget_frozen JSONB;
-- Cumulative usage tracker (updated by heartbeat/complete, frozen by suspend)
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS budget_used JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ── 3. Write-off / debt columns ──
-- fail_stage: mandatory for failed missions (e.g. 'tool_loop', 'build', 'test', 'acceptance')
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS fail_stage VARCHAR(60);
-- report_owed: TRUE when terminal state reached without a report
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS report_owed BOOLEAN NOT NULL DEFAULT FALSE;
-- worktree_recyclable: TRUE after write-off clears claim_token
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS worktree_recyclable BOOLEAN NOT NULL DEFAULT FALSE;
-- write_off_at: timestamp of terminal settlement
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS write_off_at TIMESTAMPTZ;

-- ── 4. Credit ledger — every terminal state writes an entry ──
CREATE TABLE IF NOT EXISTS credit_ledger (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    mission_id      BIGINT NOT NULL REFERENCES tensor_dev_missions(id) ON DELETE CASCADE,
    session_id      BIGINT REFERENCES tensor_agent_sessions(id) ON DELETE SET NULL,
    entry_type      VARCHAR(30) NOT NULL
                    CHECK (entry_type IN ('succeeded','failed','stopped','revoked','suspended','resumed','orphan','mechanical_fix')),
    budget_used     JSONB NOT NULL DEFAULT '{}'::jsonb,
    budget_limit    JSONB NOT NULL DEFAULT '{}'::jsonb,
    wall_seconds_used INT,
    tool_calls_used INT,
    tokens_used     BIGINT,
    fail_stage      VARCHAR(60),
    report_owed     BOOLEAN NOT NULL DEFAULT FALSE,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_credit_ledger_mission
    ON credit_ledger (mission_id);
CREATE INDEX IF NOT EXISTS ix_credit_ledger_org
    ON credit_ledger (organization_id, created_at DESC);

-- ── 5. Suspend/resume audit log ──
CREATE TABLE IF NOT EXISTS mission_lifecycle_log (
    id              BIGSERIAL PRIMARY KEY,
    mission_id      BIGINT NOT NULL REFERENCES tensor_dev_missions(id) ON DELETE CASCADE,
    action          VARCHAR(20) NOT NULL CHECK (action IN ('suspend','resume','write_off','orphan_detect','reclaim','necessity_gate')),
    actor_user_id   BIGINT REFERENCES users(id),
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_lifecycle_log_mission
    ON mission_lifecycle_log (mission_id);

-- ── 6. Necessity gate decisions ──
CREATE TABLE IF NOT EXISTS necessity_gate_log (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    task_key        VARCHAR(120),
    goal_hash       VARCHAR(64),
    repo            VARCHAR(120),
    decision        VARCHAR(20) NOT NULL CHECK (decision IN ('pass','reject_duplicate','reject_covered','reject_inflight','downgrade_mechanical')),
    reason          TEXT,
    matched_mission_id BIGINT,
    mechanical_fix_id  BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_necessity_gate_org
    ON necessity_gate_log (organization_id, created_at DESC);

-- ── 7. Mechanical fix log (auto-repair without LLM dispatch) ──
CREATE TABLE IF NOT EXISTS mechanical_fix_log (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    mission_id      BIGINT REFERENCES tensor_dev_missions(id) ON DELETE SET NULL,
    fix_type        VARCHAR(40) NOT NULL CHECK (fix_type IN ('dedup_report','typo_fix','link_patch','format_normalize')),
    source_mission_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    executed_by     VARCHAR(60) NOT NULL DEFAULT 'system',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mech_fix_org
    ON mechanical_fix_log (organization_id, created_at DESC);

-- ── 8. Indexes for orphan detection queries ──
CREATE INDEX IF NOT EXISTS ix_missions_orphan_candidate
    ON tensor_dev_missions (status, created_at)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS ix_missions_heartbeat_stale
    ON tensor_dev_missions (status, heartbeat_at)
    WHERE status IN ('claimed', 'running');
