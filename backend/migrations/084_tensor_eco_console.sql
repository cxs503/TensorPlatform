-- 084: TensorPlatform 科研运行台操作面（tensor_eco console）
-- 场景/执行/agent 会话(append-only)/审批/executor 心跳 五表。
-- 全部 IF NOT EXISTS / ON CONFLICT DO NOTHING，可安全重放。

CREATE TABLE IF NOT EXISTS tensor_scenarios (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    scenario_key    VARCHAR(120) NOT NULL,
    display_name    VARCHAR(200),
    description     TEXT,
    -- 科研阶梯 S0(可复现基线) -> S1(物理引入) -> S2(标定) -> S3(外推) -> S4(工程交付)
    current_stage   VARCHAR(8) NOT NULL DEFAULT 'S0'
                    CHECK (current_stage IN ('S0','S1','S2','S3','S4')),
    -- {"S0": {"state": "completed|pending|failed", "evidence_run_id": ...,
    --         "checks_passed": 12, "checks_total": 12}}
    stage_status    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tensor_scenarios_org_key UNIQUE (organization_id, scenario_key)
);

CREATE TABLE IF NOT EXISTS tensor_executions (
    id                 BIGSERIAL PRIMARY KEY,
    organization_id    BIGINT NOT NULL REFERENCES organizations(id),
    -- {scenario_key}-{stage}-{UTC:%Y%m%d-%H%M%S}：全局唯一，永不与 run_id 409 冲突
    execution_key      VARCHAR(200) NOT NULL,
    scenario_key       VARCHAR(120) NOT NULL,
    stage              VARCHAR(8) NOT NULL
                       CHECK (stage IN ('S0','S1','S2','S3','S4')),
    status             VARCHAR(24) NOT NULL DEFAULT 'queued' CHECK (status IN (
                       'queued','claimed','running','cancel_requested',
                       'succeeded','failed','cancelled')),
    claim_token        VARCHAR(64),
    claimed_by         VARCHAR(120),
    triggered_by       BIGINT REFERENCES users(id),
    -- {"max_wall_seconds": 900}
    budget             JSONB NOT NULL DEFAULT '{}'::jsonb,
    heartbeat_at       TIMESTAMPTZ,
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    log_tail           TEXT,
    registered_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_summary     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tensor_executions_key UNIQUE (execution_key)
);

-- 活跃态部分索引 = executor claim 扫描面
CREATE INDEX IF NOT EXISTS ix_tensor_executions_active
    ON tensor_executions (organization_id, id)
    WHERE status IN ('queued','claimed','running','cancel_requested');
CREATE INDEX IF NOT EXISTS ix_tensor_executions_scenario
    ON tensor_executions (scenario_key, created_at DESC);

CREATE TABLE IF NOT EXISTS tensor_agent_sessions (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    execution_id    BIGINT REFERENCES tensor_executions(id) ON DELETE SET NULL,
    agent_name      VARCHAR(120) NOT NULL DEFAULT 'tensor-executor',
    status          VARCHAR(24) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','completed','failed','stopped')),
    budget          JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_by      BIGINT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tensor_agent_session_events (
    id         BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES tensor_agent_sessions(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    kind       VARCHAR(40) NOT NULL CHECK (kind IN (
               'plan','execution_started','run_registered','validation_seen',
               'decision','approval_requested','approval_decided','note','error')),
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tensor_events_session_seq UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_tensor_events_session
    ON tensor_agent_session_events (session_id, seq);

-- append-only 审计链：触发器硬禁 UPDATE/DELETE
CREATE OR REPLACE FUNCTION tensor_events_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'tensor_agent_session_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tensor_events_no_update ON tensor_agent_session_events;
CREATE TRIGGER trg_tensor_events_no_update
    BEFORE UPDATE OR DELETE ON tensor_agent_session_events
    FOR EACH ROW EXECUTE FUNCTION tensor_events_forbid_mutation();

CREATE TABLE IF NOT EXISTS tensor_approval_requests (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    scenario_id     BIGINT NOT NULL REFERENCES tensor_scenarios(id),
    kind            VARCHAR(32) NOT NULL
                    CHECK (kind IN ('stage_promotion','stop_condition')),
    from_stage      VARCHAR(8),
    to_stage        VARCHAR(8),
    reason          TEXT,
    evidence        JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected')),
    requested_by    VARCHAR(120),
    decided_by      BIGINT REFERENCES users(id),
    decided_at      TIMESTAMPTZ,
    decision_note   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每场景每类审批同时只允许一个 pending
CREATE UNIQUE INDEX IF NOT EXISTS uq_tensor_approvals_pending
    ON tensor_approval_requests (scenario_id, kind)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS tensor_executors (
    executor_id  VARCHAR(120) PRIMARY KEY,
    hostname     VARCHAR(200),
    version      VARCHAR(40),
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    heartbeat_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 种子（fail-closed）：场景行只从已注册且 validation=passed 的 run
-- 实值提取 checks 数字；run 不存在则不插行（宁缺毋假）。
-- ============================================================
INSERT INTO tensor_scenarios
    (organization_id, scenario_key, display_name, description,
     current_stage, stage_status)
SELECT r.organization_id,
       'wedge-icebreaking',
       'Wedge 破冰 S0 基线',
       'TensorPhysics wedge_icebreaking 阶梯基线；S0 检查数字取自已注册 run 实值。',
       'S0',
       jsonb_build_object(
           'S0', jsonb_build_object(
               'state', 'completed',
               'evidence_run_id', r.run_id,
               'checks_passed', COALESCE((r.manifest #>> '{metrics,checks_passed}')::int, 0),
               'checks_total',  COALESCE((r.manifest #>> '{metrics,checks_total}')::int, 0)
           )
       )
FROM tensorphysics_runs r
WHERE r.run_id = 'wedge-icebreaking-s0-001'
  AND r.validation_status = 'passed'
  AND r.status = 'succeeded'
ON CONFLICT (organization_id, scenario_key) DO NOTHING;
