-- 115_audit_readonly_views_rollback.sql
-- P472W2-N01 回滚脚本：撤销审计只读视图层
--
-- 回滚顺序：权限 → 视图 → 表 → 角色
-- 幂等：所有 DROP 均带 IF EXISTS。
-- 回滚后 C2 恢复原状（audit_reader 角色不存在，视图不存在）。

BEGIN;

-- 1. 撤销 RBAC 应用层绑定
DELETE FROM rbac_role_permissions
WHERE role_code = 'audit_reader' AND permission_code = 'audit_views:read';

-- 2. 撤销 RBAC 权限注册
DELETE FROM rbac_permissions WHERE code = 'audit_views:read';

-- 3. 撤销 RBAC 角色注册
DELETE FROM rbac_roles WHERE code = 'audit_reader';

-- 4. 撤销数据库级权限（动态获取当前数据库名）
DO $$
DECLARE
    db_name TEXT;
BEGIN
    SELECT current_database() INTO db_name;
    EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM audit_reader', db_name);
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Could not revoke CONNECT (role may not exist): %', SQLERRM;
END
$$;

-- 5. 撤销视图权限
REVOKE ALL ON
    v_audit_campaign_plans,
    v_audit_campaign_nodes,
    v_audit_missions,
    v_audit_reports,
    v_audit_capability_deltas,
    v_audit_capability_registry
FROM audit_reader;

REVOKE ALL ON audit_access_events FROM audit_reader;
REVOKE USAGE ON SEQUENCE audit_access_events_id_seq FROM audit_reader;

-- 6. 撤销 schema 权限
REVOKE ALL ON SCHEMA public FROM audit_reader;

-- 7. 删除视图
DROP VIEW IF EXISTS v_audit_campaign_plans;
DROP VIEW IF EXISTS v_audit_campaign_nodes;
DROP VIEW IF EXISTS v_audit_missions;
DROP VIEW IF EXISTS v_audit_reports;
DROP VIEW IF EXISTS v_audit_capability_deltas;
DROP VIEW IF EXISTS v_audit_capability_registry;

-- 8. 删除审计事件表触发器和函数
DROP TRIGGER IF EXISTS audit_access_events_append_only ON audit_access_events;
DROP FUNCTION IF EXISTS audit_access_events_append_only_guard();

-- 9. 删除审计事件表
DROP TABLE IF EXISTS audit_access_events;

-- 10. 终止 audit_reader 的活跃连接并删除角色
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE usename = 'audit_reader' AND pid <> pg_backend_pid();

DROP ROLE IF EXISTS audit_reader;

COMMIT;
