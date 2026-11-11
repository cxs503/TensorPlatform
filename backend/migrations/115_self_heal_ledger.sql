-- 115_self_heal_ledger.sql
-- 任务级自愈台账（p393w2-n04）：停滞任务检测 → 根因分类 → 有界恢复 → 审计链
--
-- 落地口径（与 backend/api/tensor_self_heal.py 的 SCHEMA_DDL 同构；
-- tests/test_self_heal.py 里有列级 parity 断言防止两份结构漂移）：
--   * 一行 = 一次自愈动作。hash = sha256(canonical(entry 除 hash 外全部字段) + prev_hash)，
--     链可机械校验；删除/改序/改字段都会使 hash 断裂。
--   * idempotency_key 唯一：同一 window_id + mission_id + action + attempt 只允许一行，
--     因此同一窗口重跑不会重复动作，台账摘要（digest）保持不变。
--   * append-only：UPDATE/DELETE 被触发器拒绝（审计链不能被"修正"）。
--   * 零领域语义：表内只有机制字段（状态/原因/动作/参数/摘要），无任何应用域词条。

BEGIN;

CREATE TABLE IF NOT EXISTS self_heal_ledger (
    seq               BIGINT PRIMARY KEY,
    window_id         VARCHAR(48) NOT NULL,
    mission_id        BIGINT NOT NULL,
    detection_reason  VARCHAR(32) NOT NULL,
    cause             VARCHAR(32) NOT NULL,
    confidence        DOUBLE PRECISION NOT NULL,
    action            VARCHAR(32) NOT NULL,
    params            TEXT NOT NULL,
    attempt           INTEGER NOT NULL,
    outcome           VARCHAR(32) NOT NULL,
    detail            TEXT NOT NULL,
    detected_at       DOUBLE PRECISION NOT NULL,
    created_at        DOUBLE PRECISION NOT NULL,
    idempotency_key   VARCHAR(64) NOT NULL,
    prev_hash         VARCHAR(64) NOT NULL,
    hash              VARCHAR(64) NOT NULL,
    CONSTRAINT uq_self_heal_idempotency UNIQUE (idempotency_key)
);

COMMENT ON TABLE self_heal_ledger IS
    '任务级自愈审计链：detect→classify→recover→ledger，一行一次动作，hash 链防篡改';
COMMENT ON COLUMN self_heal_ledger.window_id IS
    '自愈窗口标识（ISO 时刻，字典序即时间序），同一窗口重跑幂等';
COMMENT ON COLUMN self_heal_ledger.cause IS
    '六类根因：infra_lost/timeout/criteria_infeasible/duplicate_seed/resource_contention/unknown';
COMMENT ON COLUMN self_heal_ledger.outcome IS
    'applied/deferred/escalated/archived/failed/skipped';
COMMENT ON COLUMN self_heal_ledger.params IS
    '动作参数 JSON 文本（跨方言 TEXT，读侧解析）';
COMMENT ON COLUMN self_heal_ledger.detail IS
    '检测快照 + 分类证据 + 执行器回执 JSON 文本';
COMMENT ON COLUMN self_heal_ledger.prev_hash IS
    '前一行 hash；首行全 0（GENESIS）';
COMMENT ON COLUMN self_heal_ledger.hash IS
    'sha256(canonical(除 hash 外全部字段，含 prev_hash))';
COMMENT ON COLUMN self_heal_ledger.idempotency_key IS
    'sha256(window_id, mission_id, action, attempt)：唯一约束即幂等闸';

CREATE INDEX IF NOT EXISTS ix_self_heal_ledger_window
    ON self_heal_ledger (window_id, seq);
CREATE INDEX IF NOT EXISTS ix_self_heal_ledger_mission
    ON self_heal_ledger (mission_id, seq);
CREATE INDEX IF NOT EXISTS ix_self_heal_ledger_cause
    ON self_heal_ledger (cause, created_at);

-- append-only：审计链不允许被"修正"
CREATE OR REPLACE FUNCTION self_heal_ledger_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'self_heal_ledger is append-only (% rejected)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_self_heal_ledger_no_update ON self_heal_ledger;
CREATE TRIGGER trg_self_heal_ledger_no_update
    BEFORE UPDATE OR DELETE ON self_heal_ledger
    FOR EACH ROW EXECUTE FUNCTION self_heal_ledger_append_only();

COMMIT;