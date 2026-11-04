-- 098 tensor_capability_deltas（AOG SPEC §7 能力增量实体，PR-5）
-- 每个 succeeded mission 恰好一行（UNIQUE mission_id）：组织能力账的增量流水。
-- 类别六值 = SPEC v1.0 §7 的六个 Delta 类；无实质增量合法但必须给 reason
-- （「不得强制每个 Mission 虚构 Capability Delta」→ 未申报落 no_substantive_delta）。
BEGIN;

CREATE TABLE IF NOT EXISTS tensor_capability_deltas (
  id              BIGSERIAL PRIMARY KEY,
  organization_id BIGINT NOT NULL,
  mission_id      BIGINT NOT NULL REFERENCES tensor_dev_missions(id),
  category        TEXT NOT NULL,
  summary         TEXT NOT NULL,
  reason          TEXT,
  artifacts       JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence        JSONB NOT NULL DEFAULT '{}'::jsonb,
  card_id         BIGINT,
  reviewer        TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

DO $$ BEGIN
  ALTER TABLE tensor_capability_deltas ADD CONSTRAINT ck_capability_delta_category
    CHECK (category IN ('new_capability',          -- 新增能力
                        'capability_extension',    -- 既有能力扩展或性能/可靠性改善
                        'defect_fix',              -- 缺陷修复和风险降低
                        'reusable_knowledge',      -- 可复用知识或工具
                        'falsified_hypothesis',    -- 被否定的假设（负面结果）
                        'no_substantive_delta'));  -- 无实质增量（reason 必填）
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 幂等：同一 mission 的验收只留一行（重放 decide 不产生重复流水）
CREATE UNIQUE INDEX IF NOT EXISTS uq_capability_delta_mission
  ON tensor_capability_deltas (mission_id);
CREATE INDEX IF NOT EXISTS ix_capability_delta_org_cat
  ON tensor_capability_deltas (organization_id, category);

COMMIT;
