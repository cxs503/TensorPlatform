-- 099_audit_logs_append_only.sql
-- PR-8（AOG P1）：审计日志 append-only 化——数据库层拒绝 UPDATE/DELETE。
--
-- 背景（P0 架构审计，报告 #3554）：audit_logs 是平台唯一审计面，但任何拿到
-- 写连接的代码或人工 SQL 都能改写/抹除历史行。本迁移把"只增不改"下沉为
-- 触发器：直连 UPDATE/DELETE 即 RAISE。同批在 api/audit.py 移除
-- /audit/security 无条件前置的两条捏造事件（假事件另案）。
--
-- 幂等：函数 CREATE OR REPLACE；触发器经 pg_trigger 存在性守卫，可重复执行。

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    action VARCHAR(64),
    entity_type VARCHAR(64),
    entity_id VARCHAR(64),
    detail TEXT,
    user_name VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION audit_logs_append_only_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % on row id=% rejected',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'audit_logs_append_only'
          AND tgrelid = 'audit_logs'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only_guard();
    END IF;
END;
$$;
