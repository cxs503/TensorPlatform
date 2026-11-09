-- 109_proposal_gate_ledger.sql
-- 提案→战役播种供给链机器门账本（三道门：schema 契约 / 跨提案去重 / 播种覆盖率）
--
-- 四态（quarantine 含两类：契约失败 + 复核带近似重复）
--   proposal       通过契约与去重门、进池待采纳
--   adopted        已被战役播种采纳
--   dedup_rejected 重复 / 孪生被自动拒（dup_of 指向首次）
--   quarantined    不进仲裁（schema 失败 或 相似度落复核带待人工/下游复核）
--
-- 幂等：UNIQUE (mission_id, sha256) —— 同一 mission 同内容重复提交不新增行。
-- 零领域语义：本表不含任何域词/域枚举，词表与阈值在 config/proposal_gate.json。

CREATE TABLE IF NOT EXISTS proposal_gate_ledger (
    id              BIGSERIAL PRIMARY KEY,
    mission_id      BIGINT NOT NULL,
    plan_id         BIGINT,
    sha256          CHAR(64) NOT NULL,
    schema_version  TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    state           TEXT NOT NULL
                    CHECK (state IN ('proposal','adopted','dedup_rejected','quarantined')),
    dup_of          CHAR(64),
    score           DOUBLE PRECISION,
    reasons         TEXT NOT NULL DEFAULT '[]',
    source_path     TEXT,
    blob            TEXT,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_proposal_gate_ledger_mission_sha UNIQUE (mission_id, sha256)
);

CREATE INDEX IF NOT EXISTS ix_proposal_gate_ledger_state
    ON proposal_gate_ledger (state, at DESC);

CREATE INDEX IF NOT EXISTS ix_proposal_gate_ledger_sha
    ON proposal_gate_ledger (sha256);

CREATE INDEX IF NOT EXISTS ix_proposal_gate_ledger_mission
    ON proposal_gate_ledger (mission_id);

-- 审计对账：四态计数 + 重复率（与 GET /proposals/gate/stats 同口径）
-- SELECT state, count(*) AS n FROM proposal_gate_ledger GROUP BY state ORDER BY state;
-- SELECT count(*) FILTER (WHERE state = 'dedup_rejected')::float
--        / NULLIF(count(*), 0) AS dup_rate FROM proposal_gate_ledger;