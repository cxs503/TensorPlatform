-- 114_withheld_closure_binding.sql
-- WITHHELD 欠账闭环成熟化（IN51-W5-N09）：登记库↔门禁双向绑定 + 不确定度预算台账
--
-- 落地口径（与 tools/vv/withheld_closure.py 的 SQLite DDL 同构；
-- tests/vv/test_withheld_closure.py 里有列级 parity 断言防止两份结构漂移）：
--   * tensor_reference_bindings：绑定是**一等对象**（gate_id → dataset_id + 契约指纹 + 状态）。
--     两侧各自独立成边（门账本 / 登记册反向视图），覆盖率按边集合对称差计算。
--   * 结构层自动化：门写入即建边、数据集登记即 PENDING→BOUND、门删除即撤边；
--     API 侧 reconcile_bindings() 仍是权威修复手段（幂等），触发器只兜"绕过 API 的直接写入"。
--   * tensor_uncertainty_budgets：预算台账 append-only（UPDATE/DELETE 被触发器拒绝），
--     每行含自动路径 u_c、手工台账 u_c、双路相对误差与内容摘要 digest。
--   * 零领域语义：表里没有任何物理量/船型/算例词条，名称由调用方工件注入。

BEGIN;

-- --------------------------------------------------------------------------- --
-- 1. 双向绑定索引
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS tensor_reference_bindings (
    gate_id          TEXT PRIMARY KEY,
    dataset_id       TEXT NOT NULL,
    artifact_sha256  CHAR(64) NOT NULL,
    state            VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    bound_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_reference_binding_state CHECK (state IN ('PENDING','BOUND'))
);

COMMENT ON TABLE tensor_reference_bindings IS
    '登记库↔门禁双向绑定索引：门→数据集 + 门工件契约指纹；登记册反查门的唯一入口';
COMMENT ON COLUMN tensor_reference_bindings.state IS
    'PENDING=数据集尚未登记/不可作判据（债在数据集侧）；BOUND=已登记可作判据';
COMMENT ON COLUMN tensor_reference_bindings.artifact_sha256 IS
    '门工件契约指纹：数据集绑定或容差/k/实测值改动都会使该边陈旧（STALE）';

CREATE INDEX IF NOT EXISTS ix_reference_bindings_dataset
    ON tensor_reference_bindings (dataset_id, state);

-- --------------------------------------------------------------------------- --
-- 2. 不确定度预算台账（append-only）
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS tensor_uncertainty_budgets (
    gate_id           TEXT NOT NULL,
    dataset_id        TEXT NOT NULL DEFAULT '',
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    k_factor          DOUBLE PRECISION NOT NULL,
    u_combined        DOUBLE PRECISION NOT NULL,
    u_expanded        DOUBLE PRECISION NOT NULL,
    denominator       DOUBLE PRECISION NOT NULL,
    band_uncertainty  DOUBLE PRECISION NOT NULL,
    band              DOUBLE PRECISION NOT NULL,
    tolerance         DOUBLE PRECISION NOT NULL,
    resolution_ok     BOOLEAN NOT NULL,
    components_json   JSONB NOT NULL,
    correlations_json JSONB NOT NULL DEFAULT '{}',
    manual_u_combined DOUBLE PRECISION NOT NULL,
    parity_rel_error  DOUBLE PRECISION NOT NULL,
    digest            CHAR(64) NOT NULL,
    PRIMARY KEY (gate_id, computed_at)
);

COMMENT ON TABLE tensor_uncertainty_budgets IS
    '数字化不确定度预算台账：u_c=sqrt(Σu²+2Σρuu)、U=k·u_c、band=max(tol,U/D)，逐次判决追加';
COMMENT ON COLUMN tensor_uncertainty_budgets.resolution_ok IS
    'band_uncertainty<=tolerance：false 表示试验无分辨力，对应门原因码 '
    'INSUFFICIENT_RESOLUTION（只能 WITHHELD，不得 PASS；IN50 的 WITHIN_UNCERTAINTY⇒PASS 已废止）';
COMMENT ON COLUMN tensor_uncertainty_budgets.parity_rel_error IS
    '自动路径(浮点 fsum) vs 手工台账(Decimal,3 位有效数字) 的相对误差，判据 <1%';

CREATE INDEX IF NOT EXISTS ix_uncertainty_budgets_gate
    ON tensor_uncertainty_budgets (gate_id, computed_at);

-- --------------------------------------------------------------------------- --
-- 3. 绑定自动化触发器（结构层兜底）
-- --------------------------------------------------------------------------- --
CREATE OR REPLACE FUNCTION tensor_reference_binding_upsert() RETURNS trigger AS $$
BEGIN
    INSERT INTO tensor_reference_bindings
        (gate_id, dataset_id, artifact_sha256, state, bound_at, updated_at)
    VALUES (NEW.gate_id, NEW.required_dataset_id, NEW.artifact_sha256,
            CASE WHEN EXISTS (SELECT 1 FROM tensor_reference_datasets d
                               WHERE d.dataset_id = NEW.required_dataset_id
                                 AND d.usable_as_gate = 1) THEN 'BOUND' ELSE 'PENDING' END,
            now(), now())
    ON CONFLICT (gate_id) DO UPDATE SET
        dataset_id = EXCLUDED.dataset_id,
        artifact_sha256 = EXCLUDED.artifact_sha256,
        state = EXCLUDED.state,
        updated_at = EXCLUDED.updated_at;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_withheld_gate_bind_ins
AFTER INSERT ON tensor_withheld_gates
FOR EACH ROW EXECUTE FUNCTION tensor_reference_binding_upsert();

CREATE OR REPLACE FUNCTION tensor_reference_binding_drop() RETURNS trigger AS $$
BEGIN
    DELETE FROM tensor_reference_bindings WHERE gate_id = OLD.gate_id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_withheld_gate_bind_del
AFTER DELETE ON tensor_withheld_gates
FOR EACH ROW EXECUTE FUNCTION tensor_reference_binding_drop();

CREATE OR REPLACE FUNCTION tensor_reference_binding_refresh() RETURNS trigger AS $$
BEGIN
    UPDATE tensor_reference_bindings
       SET state = CASE WHEN NEW.usable_as_gate = 1 THEN 'BOUND' ELSE 'PENDING' END,
           updated_at = now()
     WHERE dataset_id = NEW.dataset_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_reference_dataset_bind_state_ins
AFTER INSERT ON tensor_reference_datasets
FOR EACH ROW EXECUTE FUNCTION tensor_reference_binding_refresh();

CREATE TRIGGER trg_reference_dataset_bind_state_upd
AFTER UPDATE ON tensor_reference_datasets
FOR EACH ROW EXECUTE FUNCTION tensor_reference_binding_refresh();

-- --------------------------------------------------------------------------- --
-- 4. 预算台账 append-only（审计防线）
-- --------------------------------------------------------------------------- --
CREATE OR REPLACE FUNCTION tensor_uncertainty_budgets_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'uncertainty budget rows are append-only (% refused)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_uncertainty_budgets_no_update
BEFORE UPDATE ON tensor_uncertainty_budgets
FOR EACH ROW EXECUTE FUNCTION tensor_uncertainty_budgets_append_only();

CREATE TRIGGER trg_uncertainty_budgets_no_delete
BEFORE DELETE ON tensor_uncertainty_budgets
FOR EACH ROW EXECUTE FUNCTION tensor_uncertainty_budgets_append_only();

-- --------------------------------------------------------------------------- --
-- 5. 绑定漂移视图（SQL 侧独立复算，供巡检/报表用）
-- --------------------------------------------------------------------------- --
CREATE OR REPLACE VIEW v_reference_binding_drift AS
SELECT 'UNBOUND'::text AS drift, g.gate_id, g.required_dataset_id AS dataset_id
  FROM tensor_withheld_gates g
  LEFT JOIN tensor_reference_bindings b ON b.gate_id = g.gate_id
 WHERE b.gate_id IS NULL
UNION ALL
SELECT 'GHOST'::text, b.gate_id, b.dataset_id
  FROM tensor_reference_bindings b
  LEFT JOIN tensor_withheld_gates g ON g.gate_id = b.gate_id
 WHERE g.gate_id IS NULL
UNION ALL
SELECT 'STALE'::text, g.gate_id, b.dataset_id
  FROM tensor_withheld_gates g
  JOIN tensor_reference_bindings b ON b.gate_id = g.gate_id
 WHERE b.dataset_id <> g.required_dataset_id
    OR b.artifact_sha256 <> g.artifact_sha256;

COMMENT ON VIEW v_reference_binding_drift IS
    '双向绑定三类漂移（UNBOUND/GHOST/STALE）；空集等价于覆盖率 100%';

COMMIT;