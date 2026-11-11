-- 115_audit_readonly_views.sql
-- P472W2-N01: 审计取证最小权限只读视图层
--
-- 目标：把审计取数从 superuser 便利变成受限角色的契约能力。
-- 幂等：所有 CREATE 均带 IF NOT EXISTS；GRANT 天然幂等；
--       角色创建用 DO-block 守卫。可重放、可回滚（见 rollback 脚本）。
--
-- 六表视图（字段白名单化，去 PII/无关列）：
--   v_audit_campaign_plans       ← campaign_plans
--   v_audit_campaign_nodes       ← campaign_nodes
--   v_audit_missions             ← tensor_dev_missions
--   v_audit_reports              ← tensor_reports
--   v_audit_capability_deltas    ← tensor_capability_deltas
--   v_audit_capability_registry  ← capability_registry
--
-- 零领域语义：视图/角色/权限不含任何域词条。
--
-- 依赖：095 (campaign_plans/nodes), 086 (tensor_dev_missions),
--       090 (tensor_reports), 098 (tensor_capability_deltas),
--       099 (capability_registry)
--
-- 部署说明：
--   1. 以 superuser 执行本脚本
--   2. 修改 audit_reader 密码: ALTER ROLE audit_reader PASSWORD 'xxx';
--   3. 如需连接权限: GRANT CONNECT ON DATABASE <dbname> TO audit_reader;

BEGIN;

-- ═══════════════════════════════════════════════════════════════
-- 1. 创建 audit_reader 角色（PostgreSQL 数据库角色）
-- ═══════════════════════════════════════════════════════════════
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_reader') THEN
        CREATE ROLE audit_reader LOGIN PASSWORD 'audit_reader_changeme';
        RAISE NOTICE 'Created role audit_reader';
    END IF;
END
$$;

-- 确保角色无危险权限
ALTER ROLE audit_reader NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

-- ═══════════════════════════════════════════════════════════════
-- 2. 审计事件表（越权留痕 C5）
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS audit_access_events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL
                    CHECK (event_type IN ('denied_select', 'denied_insert',
                                          'denied_update', 'denied_delete',
                                          'denied_ddl', 'unauthorized_role')),
    role_name       TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    sql_hash        CHAR(64) NOT NULL,
    sql_summary     TEXT NOT NULL DEFAULT '',
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_audit_access_events_type
    ON audit_access_events (event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_access_events_role
    ON audit_access_events (role_name, occurred_at DESC);

-- 审计事件表也是 append-only
CREATE OR REPLACE FUNCTION audit_access_events_append_only_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_access_events is append-only: % on row id=% rejected',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'audit_access_events_append_only'
          AND tgrelid = 'audit_access_events'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER audit_access_events_append_only
        BEFORE UPDATE OR DELETE ON audit_access_events
        FOR EACH ROW EXECUTE FUNCTION audit_access_events_append_only_guard();
    END IF;
END;
$$;

-- ═══════════════════════════════════════════════════════════════
-- 3. 六表只读视图（字段白名单化）
-- ═══════════════════════════════════════════════════════════════

-- 3.1 战役计划视图
-- 排除: budget_planned, budget_actual (内部预算), created_by (PII)
CREATE OR REPLACE VIEW v_audit_campaign_plans AS
SELECT
    id,
    campaign_id,
    name,
    status,
    metadata,
    created_at,
    updated_at
FROM campaign_plans;

-- 3.2 战役节点视图
-- 排除: dependencies (内部实现), planned_start/end, actual_start/end (调度细节),
--        milestone_id, mission_id (内部关联 ID)
CREATE OR REPLACE VIEW v_audit_campaign_nodes AS
SELECT
    id,
    plan_id,
    name,
    node_type,
    status,
    reproduction_verdict,
    consecutive_fails,
    block_reason,
    metadata,
    created_at,
    updated_at
FROM campaign_nodes;

-- 3.3 任务视图
-- 排除: claim_token (敏感), session_id (内部), created_by (PII),
--        organization_id (租户隔离), budget (内部)
CREATE OR REPLACE VIEW v_audit_missions AS
SELECT
    id,
    goal,
    repo,
    base_ref,
    status,
    claimed_by,
    result,
    created_at,
    heartbeat_at,
    finished_at
FROM tensor_dev_missions;

-- 3.4 报告视图
-- 排除: markdown (大文本), figures (二进制), created_by (PII), organization_id (租户)
CREATE OR REPLACE VIEW v_audit_reports AS
SELECT
    id,
    title,
    summary,
    scenario_key,
    mission_id,
    stage,
    tags,
    status,
    created_at
FROM tensor_reports;

-- 3.5 能力增量视图（能力台账）
-- 排除: organization_id (租户), card_id (内部)
CREATE OR REPLACE VIEW v_audit_capability_deltas AS
SELECT
    id,
    mission_id,
    category,
    summary,
    reason,
    artifacts,
    evidence,
    reviewer,
    created_at
FROM tensor_capability_deltas;

-- 3.6 能力注册视图（能力声明）
-- 排除: metadata (内部)
CREATE OR REPLACE VIEW v_audit_capability_registry AS
SELECT
    capability_id,
    claimed_level,
    expected_level,
    registered_at,
    updated_at
FROM capability_registry;

-- ═══════════════════════════════════════════════════════════════
-- 4. 权限授予（仅 SELECT on 视图 + 审计事件表 INSERT）
-- ═══════════════════════════════════════════════════════════════

-- Schema usage
GRANT USAGE ON SCHEMA public TO audit_reader;

-- 视图 SELECT
GRANT SELECT ON
    v_audit_campaign_plans,
    v_audit_campaign_nodes,
    v_audit_missions,
    v_audit_reports,
    v_audit_capability_deltas,
    v_audit_capability_registry
TO audit_reader;

-- 审计事件表：仅允许 INSERT（写审计日志），不允许 SELECT/UPDATE/DELETE
GRANT INSERT ON audit_access_events TO audit_reader;
GRANT USAGE ON SEQUENCE audit_access_events_id_seq TO audit_reader;

-- 显式拒绝基表访问（防御性，即使角色无默认权限）
REVOKE ALL ON
    campaign_plans,
    campaign_nodes,
    tensor_dev_missions,
    tensor_reports,
    tensor_capability_deltas,
    capability_registry
FROM audit_reader;

-- ═══════════════════════════════════════════════════════════════
-- 5. 在 RBAC 应用层注册 audit_reader 角色
-- ═══════════════════════════════════════════════════════════════
INSERT INTO rbac_roles (code, name, description, status, is_builtin)
VALUES ('audit_reader', '审计只读角色',
        '数据库级只读角色，仅可 SELECT 审计视图，不可修改任何数据',
        'active', TRUE)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = NOW();

-- 注册审计视图读权限
INSERT INTO rbac_permissions (code, name, resource_type, action, description, is_builtin)
VALUES ('audit_views:read', '读取审计视图', 'audit_views', 'read',
        '通过只读视图查询审计数据（战役/节点/任务/报告/能力台账/能力声明）', TRUE)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    resource_type = EXCLUDED.resource_type,
    action = EXCLUDED.action,
    description = EXCLUDED.description,
    updated_at = NOW();

-- 绑定角色-权限
INSERT INTO rbac_role_permissions (role_code, permission_code)
VALUES ('audit_reader', 'audit_views:read')
ON CONFLICT DO NOTHING;

COMMIT;
