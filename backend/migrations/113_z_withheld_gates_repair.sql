-- 113_z_withheld_gates_repair.sql
-- Forward-only replacement for 113_withheld_gates.sql. The original migration
-- has an invalid PL/pgSQL RAISE placeholder count; this idempotent replay also
-- repairs databases where the original DDL was already applied.
-- WITHHELD 三态清偿闭环（IN50-W4-N09）：门声明 ↔ 参考数据登记 ↔ 自动重判 ↔ 派单 + KPI
--
-- 落地口径（与 tools/vv/withheld_gates.py 的 SQLite DDL 同构，tests/test_withheld_gate_clearing.py
-- 里有列级 parity 断言防止两份结构漂移）：
--   * 门的判决契约字段（tolerance / k_factor / required_dataset_id / artifact_sha256 /
--     artifact_json）在**结构层**不可就地更新：放宽容差 = 篡改，触发器直接 ABORT。
--   * 审计表 append-only：UPDATE/DELETE 被触发器拒绝；行内 prev_hash/row_hash 组成哈希链。
--   * KPI（withheld_gate_share / withheld_mean_age_days）复用 095 已建的
--     tensor_guide_tracker，这里只补索引。

BEGIN;

CREATE TABLE IF NOT EXISTS tensor_withheld_gates (
    gate_id              TEXT PRIMARY KEY,
    mission_id           BIGINT,
    node                 TEXT NOT NULL DEFAULT '',
    repo                 TEXT NOT NULL DEFAULT '',
    quantity             TEXT NOT NULL DEFAULT '',
    unit                 TEXT NOT NULL DEFAULT '',
    reason_code          TEXT NOT NULL DEFAULT '',
    required_dataset_id  TEXT NOT NULL,
    tolerance            DOUBLE PRECISION NOT NULL,
    k_factor             DOUBLE PRECISION NOT NULL DEFAULT 2.0,
    artifact_sha256      CHAR(64) NOT NULL,
    artifact_json        JSONB NOT NULL,
    verdict              VARCHAR(16) NOT NULL DEFAULT 'WITHHELD',
    verdict_reason       TEXT NOT NULL DEFAULT '',
    verdict_reason_code  TEXT NOT NULL DEFAULT '',
    cleared_at           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_judged_at       TIMESTAMPTZ,
    CONSTRAINT ck_withheld_gate_verdict CHECK (verdict IN ('PASS','FAIL','WITHHELD')),
    CONSTRAINT ck_withheld_gate_tol CHECK (tolerance > 0 AND tolerance <= 0.5),
    CONSTRAINT ck_withheld_gate_k CHECK (k_factor > 0)
);

COMMENT ON TABLE tensor_withheld_gates IS
    'WITHHELD 门账本：每门指认其欠的参考数据集（required_dataset_id）与判决契约基线哈希';
COMMENT ON COLUMN tensor_withheld_gates.artifact_sha256 IS
    '门工件契约字段（gate/数据集/tol/k/实测值/声明时刻）的规范化 sha256，不可变基线';
COMMENT ON COLUMN tensor_withheld_gates.verdict_reason_code IS
    '当前判决原因码（NO_REFERENCE/REFERENCE_INCOMPLETE/REFERENCE_OUT_OF_RANGE/TOLERANCE_TAMPERED/...），机器可读，供 KPI 与派单分类';
COMMENT ON COLUMN tensor_withheld_gates.tolerance IS
    '容差只来自门原始工件基线；触发器禁止 UPDATE，防止“登记后放宽 tol”洗白 FAIL';

CREATE INDEX IF NOT EXISTS ix_withheld_gates_verdict
    ON tensor_withheld_gates (verdict, required_dataset_id);
CREATE INDEX IF NOT EXISTS ix_withheld_gates_repo
    ON tensor_withheld_gates (repo, required_dataset_id);
CREATE INDEX IF NOT EXISTS ix_withheld_gates_created
    ON tensor_withheld_gates (created_at) WHERE cleared_at IS NULL;

CREATE TABLE IF NOT EXISTS tensor_reference_datasets (
    dataset_id            TEXT PRIMARY KEY,
    doi                   TEXT NOT NULL DEFAULT '',
    url                   TEXT NOT NULL DEFAULT '',
    citation              TEXT NOT NULL DEFAULT '',
    original_file_sha256  CHAR(64) NOT NULL,
    digitisation_method   TEXT NOT NULL,
    u_dig                 DOUBLE PRECISION NOT NULL,
    u_dig_kind            VARCHAR(16) NOT NULL DEFAULT 'relative',
    usable_as_gate        BOOLEAN NOT NULL DEFAULT FALSE,
    validation_json       JSONB NOT NULL,
    payload_json          JSONB NOT NULL,
    registered_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_by         TEXT NOT NULL DEFAULT ''
);

COMMENT ON TABLE tensor_reference_datasets IS
    '已登记的数字化参考数据集（含 DOI/原始文件 sha256/数字化方法/数字化不确定度 u_dig）';
COMMENT ON COLUMN tensor_reference_datasets.u_dig IS
    '数字化不确定度（标准不确定度）：u_dig_kind=relative 时按参考值折算为绝对量';
COMMENT ON COLUMN tensor_reference_datasets.usable_as_gate IS
    '出处/尺度不全 ⇒ FALSE：对应门保持 WITHHELD，不得据此判 PASS/FAIL';

CREATE TABLE IF NOT EXISTS tensor_withheld_audit (
    id          BIGSERIAL PRIMARY KEY,
    gate_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event       VARCHAR(32) NOT NULL,
    verdict     VARCHAR(16) NOT NULL,
    payload     JSONB NOT NULL,
    prev_hash   CHAR(64) NOT NULL,
    row_hash    CHAR(64) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (gate_id, seq)
);

COMMENT ON TABLE tensor_withheld_audit IS
    'WITHHELD 审计链：GATE_DECLARED / DATASET_REGISTERED / REJUDGED / TAMPER_BLOCKED，append-only';

-- 契约字段不可就地更新（放宽 tol = 篡改）
CREATE OR REPLACE FUNCTION tensor_withheld_gates_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.tolerance IS DISTINCT FROM OLD.tolerance
       OR NEW.k_factor IS DISTINCT FROM OLD.k_factor
       OR NEW.required_dataset_id IS DISTINCT FROM OLD.required_dataset_id
       OR NEW.artifact_sha256 IS DISTINCT FROM OLD.artifact_sha256
       OR NEW.artifact_json IS DISTINCT FROM OLD.artifact_json THEN
        RAISE EXCEPTION
            'withheld gate contract fields are immutable (tol widening is tampering): gate_id=%',
            OLD.gate_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_withheld_gates_contract_immutable ON tensor_withheld_gates;
CREATE TRIGGER trg_withheld_gates_contract_immutable
    BEFORE UPDATE ON tensor_withheld_gates
    FOR EACH ROW EXECUTE FUNCTION tensor_withheld_gates_guard();

-- 审计 append-only
CREATE OR REPLACE FUNCTION tensor_withheld_audit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'withheld audit rows are append-only (% refused on gate_id=%)',
        TG_OP, OLD.gate_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_withheld_audit_no_update ON tensor_withheld_audit;
CREATE TRIGGER trg_withheld_audit_no_update
    BEFORE UPDATE ON tensor_withheld_audit
    FOR EACH ROW EXECUTE FUNCTION tensor_withheld_audit_append_only();

DROP TRIGGER IF EXISTS trg_withheld_audit_no_delete ON tensor_withheld_audit;
CREATE TRIGGER trg_withheld_audit_no_delete
    BEFORE DELETE ON tensor_withheld_audit
    FOR EACH ROW EXECUTE FUNCTION tensor_withheld_audit_append_only();

-- KPI（tensor_guide_tracker 在 095 已建表）
CREATE INDEX IF NOT EXISTS ix_guide_tracker_withheld_keys
    ON tensor_guide_tracker (metric_key, snapshot_at DESC)
    WHERE metric_key IN ('withheld_gate_share', 'withheld_mean_age_days');

-- 未清偿门视图：派单/KPI 的唯一读取口径（只读，三态保留）
CREATE OR REPLACE VIEW v_withheld_gates_open AS
SELECT gate_id, mission_id, node, repo, quantity, unit, reason_code,
       required_dataset_id, tolerance, created_at,
       ROUND(EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0, 4) AS age_days
  FROM tensor_withheld_gates
 WHERE verdict = 'WITHHELD' AND cleared_at IS NULL;

COMMENT ON VIEW v_withheld_gates_open IS
    '未清偿 WITHHELD 门（age_days 用于 (repo,dataset_id,age) 派单与 withheld_mean_age_days）';

COMMIT;
