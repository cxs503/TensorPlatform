-- tensor_planner_decisions 表
-- Append-only 触发器同 events 纪律
-- 纯新增，不改 tensor_missions.py / approvals 热区

CREATE TABLE IF NOT EXISTS tensor_planner_decisions (
    -- 主键
    id              BIGSERIAL PRIMARY KEY,
    decision_id     VARCHAR(64) NOT NULL UNIQUE,
    
    -- 时间戳
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- 输入快照
    candidate_id    VARCHAR(128) NOT NULL,
    candidate_title TEXT,
    candidate_description TEXT,
    candidate_source VARCHAR(32),
    candidate_labels JSONB DEFAULT '[]',
    
    -- 上下文快照
    context_snapshot JSONB DEFAULT '{}',
    
    -- 输出决策
    verdict         VARCHAR(16) NOT NULL CHECK (verdict IN ('dispatch', 'reject', 'mechanical', 'merge')),
    confidence      FLOAT NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    target_objective VARCHAR(128),
    reasoning_trace TEXT,
    
    -- 任务书（verdict=dispatch 时）
    task_spec       JSONB,
    
    -- 成本收益
    cost_benefit    JSONB,
    
    -- 分支决策
    reject_reason   TEXT,
    mechanical_fix  TEXT,
    merge_target    VARCHAR(128),
    
    -- 对照方
    machine_gate_result JSONB,
    human_verdict   VARCHAR(16),
    
    -- 后续结果（回填位）
    actual_outcome  JSONB,
    credit_score    FLOAT,
    
    -- 完整性
    integrity_hash  VARCHAR(32),
    
    -- 元数据
    schema_version  VARCHAR(16) DEFAULT '1.0.0',
    runner          VARCHAR(32) DEFAULT 'planner',
    mode            VARCHAR(16) DEFAULT 'shadow' CHECK (mode IN ('shadow', 'live'))
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_planner_decisions_verdict ON tensor_planner_decisions(verdict);
CREATE INDEX IF NOT EXISTS idx_planner_decisions_created ON tensor_planner_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_planner_decisions_candidate ON tensor_planner_decisions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_planner_decisions_mode ON tensor_planner_decisions(mode);

-- Append-only 触发器：禁止 UPDATE，只允许 INSERT
CREATE OR REPLACE FUNCTION prevent_planner_decision_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'tensor_planner_decisions is append-only; UPDATE not allowed. Use INSERT for backfill records.';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_planner_decision_update ON tensor_planner_decisions;
CREATE TRIGGER trg_prevent_planner_decision_update
    BEFORE UPDATE ON tensor_planner_decisions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_planner_decision_update();

-- 回填记录表（用于 human_verdict / actual_outcome 回填）
CREATE TABLE IF NOT EXISTS tensor_planner_decision_backfills (
    id              BIGSERIAL PRIMARY KEY,
    decision_id     VARCHAR(64) NOT NULL REFERENCES tensor_planner_decisions(decision_id),
    backfill_type   VARCHAR(32) NOT NULL CHECK (backfill_type IN ('human_verdict', 'actual_outcome', 'credit_score')),
    backfill_value  JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    operator        VARCHAR(64) DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_planner_backfills_decision ON tensor_planner_decision_backfills(decision_id);

-- 视图：最新决策状态（含回填）
CREATE OR REPLACE VIEW v_planner_decisions_latest AS
SELECT 
    d.*,
    hv.backfill_value->>'human_verdict' AS latest_human_verdict,
    ao.backfill_value AS latest_actual_outcome,
    cs.backfill_value->>'credit_score' AS latest_credit_score
FROM tensor_planner_decisions d
LEFT JOIN LATERAL (
    SELECT backfill_value 
    FROM tensor_planner_decision_backfills 
    WHERE decision_id = d.decision_id AND backfill_type = 'human_verdict'
    ORDER BY created_at DESC LIMIT 1
) hv ON true
LEFT JOIN LATERAL (
    SELECT backfill_value 
    FROM tensor_planner_decision_backfills 
    WHERE decision_id = d.decision_id AND backfill_type = 'actual_outcome'
    ORDER BY created_at DESC LIMIT 1
) ao ON true
LEFT JOIN LATERAL (
    SELECT backfill_value 
    FROM tensor_planner_decision_backfills 
    WHERE decision_id = d.decision_id AND backfill_type = 'credit_score'
    ORDER BY created_at DESC LIMIT 1
) cs ON true;

-- 注释
COMMENT ON TABLE tensor_planner_decisions IS '规划师决策记录（append-only）';
COMMENT ON COLUMN tensor_planner_decisions.verdict IS '决策类型: dispatch/reject/mechanical/merge';
COMMENT ON COLUMN tensor_planner_decisions.confidence IS '决策置信度 0-1';
COMMENT ON COLUMN tensor_planner_decisions.mode IS '运行模式: shadow(影子)/live(实际)';
COMMENT ON COLUMN tensor_planner_decisions.integrity_hash IS '完整性哈希（防篡改）';
