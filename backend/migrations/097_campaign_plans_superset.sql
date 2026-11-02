-- 095_campaign_plans 超集 ALTER（owner 2026-10-31 点名批准）
-- 既有 campaign_plans = m611 SIR-MASTER 产物 schema，保留全部列，叠加 m612 消费面所需列
BEGIN;
ALTER TABLE campaign_plans
  ADD COLUMN IF NOT EXISTS campaign_id   BIGINT,
  ADD COLUMN IF NOT EXISTS name          TEXT,
  ADD COLUMN IF NOT EXISTS description   TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS sir_master_ref TEXT,
  ADD COLUMN IF NOT EXISTS plan_version  TEXT DEFAULT '1.0',
  ADD COLUMN IF NOT EXISTS planned_start TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS planned_end   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS budget_planned JSONB NOT NULL DEFAULT '{"max_tool_calls":0,"max_wall_seconds":0,"max_tokens":0}'::jsonb,
  ADD COLUMN IF NOT EXISTS budget_actual  JSONB NOT NULL DEFAULT '{"max_tool_calls":0,"max_wall_seconds":0,"max_tokens":0}'::jsonb,
  ADD COLUMN IF NOT EXISTS status        TEXT DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS created_by    BIGINT,
  ADD COLUMN IF NOT EXISTS created_at    TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at    TIMESTAMPTZ DEFAULT now();

-- 回填：name 取既有 title；时间戳锚既有 created_utc
-- 仅当库中确实存在 m611 旧列（title/created_utc/source_mission_id）时才回填：
-- 全新安装走 095_campaign_plans.sql 建表，没有这些旧列，无条件 UPDATE 会直接报
-- column "title" does not exist，导致整条迁移链在空库上断裂（m1625 修）。
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'campaign_plans' AND column_name = 'title') THEN
    EXECUTE 'UPDATE campaign_plans SET name = COALESCE(name, title) WHERE name IS NULL';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'campaign_plans' AND column_name = 'created_utc') THEN
    EXECUTE 'UPDATE campaign_plans SET created_at = COALESCE(created_at, created_utc) WHERE created_utc IS NOT NULL';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'campaign_plans' AND column_name = 'source_mission_id') THEN
    EXECUTE 'UPDATE campaign_plans SET sir_master_ref = COALESCE(sir_master_ref, ''m611:'' || source_mission_id::text) WHERE sir_master_ref IS NULL AND source_mission_id IS NOT NULL';
  END IF;
END $$;

ALTER TABLE campaign_plans
  ALTER COLUMN name SET NOT NULL,
  ALTER COLUMN plan_version SET NOT NULL,
  ALTER COLUMN status SET NOT NULL;

-- 约束与索引（与迁移文件语义等价）
DO $$ BEGIN
  ALTER TABLE campaign_plans ADD CONSTRAINT ck_campaign_plans_status
    CHECK (status IN ('draft','active','paused','completed','archived'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_plans_cid_name ON campaign_plans (campaign_id, name);
CREATE INDEX IF NOT EXISTS ix_campaign_plans_org ON campaign_plans (organization_id, campaign_id);
COMMIT;
