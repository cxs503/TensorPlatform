-- 095: campaign_plans — 主计划入库（里程碑/节点/判据/依赖/进度度量）
-- 兼容 SIR-MASTER 产物格式，mission 通过 campaign_id 聚合

CREATE TABLE IF NOT EXISTS campaign_plans (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    campaign_id     BIGINT NOT NULL REFERENCES factory_strategy_campaigns(id),
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    -- SIR-MASTER 兼容字段
    sir_master_ref  TEXT,
    plan_version    TEXT NOT NULL DEFAULT '1.0',
    -- 计划时间窗
    planned_start   TIMESTAMPTZ,
    planned_end     TIMESTAMPTZ,
    -- 预算（计划 vs 实耗）
    budget_planned  JSONB NOT NULL DEFAULT '{"max_tool_calls":0,"max_wall_seconds":0,"max_tokens":0}'::jsonb,
    budget_actual   JSONB NOT NULL DEFAULT '{"max_tool_calls":0,"max_wall_seconds":0,"max_tokens":0}'::jsonb,
    -- 状态
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','active','paused','completed','archived')),
    -- 元数据
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by      BIGINT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(campaign_id, name)
);

CREATE INDEX IF NOT EXISTS ix_campaign_plans_org
    ON campaign_plans (organization_id, campaign_id);

-- 里程碑表
CREATE TABLE IF NOT EXISTS campaign_milestones (
    id              BIGSERIAL PRIMARY KEY,
    plan_id         BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    -- 完成判据（复现判决条件）
    criteria        JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- 依赖（上游里程碑 ID 列表）
    dependencies    INT[] DEFAULT '{}',
    -- 计划时间
    planned_start   TIMESTAMPTZ,
    planned_end     TIMESTAMPTZ,
    -- 实际时间
    actual_start    TIMESTAMPTZ,
    actual_end      TIMESTAMPTZ,
    -- 状态: pending / in-flight / done / blocked / skipped
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','in-flight','done','blocked','skipped')),
    -- 完成度 0-100
    progress_pct    INT NOT NULL DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),
    -- 连败计数（用于阻塞检测）
    consecutive_fails INT NOT NULL DEFAULT 0,
    -- 根因升级单 ID（如有）
    escalation_id   BIGINT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_campaign_milestones_plan
    ON campaign_milestones (plan_id, status);

-- 节点表（细粒度任务节点，挂 mission）
CREATE TABLE IF NOT EXISTS campaign_nodes (
    id              BIGSERIAL PRIMARY KEY,
    plan_id         BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    milestone_id    BIGINT REFERENCES campaign_milestones(id) ON DELETE SET NULL,
    mission_id      BIGINT REFERENCES tensor_dev_missions(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    node_type       TEXT NOT NULL DEFAULT 'task'
                    CHECK (node_type IN ('task','review','gate','checkpoint')),
    -- 依赖（上游节点 ID 列表）
    dependencies    INT[] DEFAULT '{}',
    -- 计划时间
    planned_start   TIMESTAMPTZ,
    planned_end     TIMESTAMPTZ,
    -- 实际时间
    actual_start    TIMESTAMPTZ,
    actual_end      TIMESTAMPTZ,
    -- 状态: pending / in-flight / done / blocked / failed / orphan
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','in-flight','done','blocked','failed','orphan')),
    -- 复现判决结果（只有 pass 才算 done）
    reproduction_verdict TEXT,  -- 'pass' / 'fail' / null
    -- 连败计数
    consecutive_fails INT NOT NULL DEFAULT 0,
    -- 阻塞原因
    block_reason    TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_campaign_nodes_plan
    ON campaign_nodes (plan_id, status);
CREATE INDEX IF NOT EXISTS ix_campaign_nodes_mission
    ON campaign_nodes (mission_id) WHERE mission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_campaign_nodes_milestone
    ON campaign_nodes (milestone_id) WHERE milestone_id IS NOT NULL;

-- 进度快照表（30min 级）
CREATE TABLE IF NOT EXISTS campaign_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    plan_id         BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 聚合统计
    total_nodes     INT NOT NULL DEFAULT 0,
    done_nodes      INT NOT NULL DEFAULT 0,
    in_flight_nodes INT NOT NULL DEFAULT 0,
    blocked_nodes   INT NOT NULL DEFAULT 0,
    failed_nodes    INT NOT NULL DEFAULT 0,
    orphan_nodes    INT NOT NULL DEFAULT 0,
    -- 里程碑完成 %
    milestone_progress_pct INT NOT NULL DEFAULT 0,
    -- 预算
    budget_planned  JSONB NOT NULL DEFAULT '{}'::jsonb,
    budget_actual   JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- 阻塞检测
    blocked_chains  JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 打转检测（连败 ≥2 的节点）
    spinning_nodes  JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 游击系数（计划外单占比）
    guerrilla_ratio FLOAT NOT NULL DEFAULT 0.0,
    -- 完整快照数据
    snapshot_data   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_campaign_snapshots_plan
    ON campaign_snapshots (plan_id, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS ix_campaign_snapshots_org
    ON campaign_snapshots (organization_id, snapshot_at DESC);

-- 事件流表（用于总管 agent 输入）
CREATE TABLE IF NOT EXISTS campaign_events (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    plan_id         BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL
                    CHECK (event_type IN ('node_status_change','milestone_status_change',
                                          'block_detected','block_resolved',
                                          'spinning_detected','escalation_created',
                                          'snapshot_taken','plan_updated',
                                          'mission_dispatched','mission_completed',
                                          'guerrilla_detected')),
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_campaign_events_plan
    ON campaign_events (plan_id, created_at DESC);
