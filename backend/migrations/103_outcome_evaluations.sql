-- 103 outcome_evaluations（AOG SPEC v1.2 §7.1 成果价值分级 S×E 落机器，m5018）
--
-- 一张表承载两种记录：
--   1. pending   — succeeded mission 完结时自动生成的待评价记录（等级留空，禁预填）
--   2. confirmed / candidate / rejected — 评价写入结果（candidate 登记不计入分布；
--      rejected 是被判据拒收的负例留痕）
--
-- 判据（等级合法性、E0 不可登记、S-N 须 E≥2、理由≥50 字、证据引用、独立审查、
-- 基线/检索范围、适用边界）由 tools/outcome_evaluations.decide 执行；本表只兜
-- 取值域与状态—等级一致性（防御性 CHECK，防绕过 API 直写脏数据）。
BEGIN;

CREATE TABLE IF NOT EXISTS outcome_evaluations (
  evaluation_id     TEXT PRIMARY KEY,
  artifact_id       TEXT NOT NULL,                 -- 成果 ID
  mission_id        BIGINT,                        -- 关联 mission
  contributor       TEXT NOT NULL,                 -- 贡献 agent/团队
  grade             TEXT,                          -- S1..S5 | S-N | NULL(待评价)
  e_level           TEXT,                          -- E0..E4 | NULL(待评价)
  grade_reason      TEXT,                          -- 等级理由（写入时 ≥50 字符）
  evidence_refs     TEXT NOT NULL DEFAULT '[]',    -- JSON 数组 [{kind, ref}]
  reviewer          TEXT,                          -- 独立审查者
  reviewer_kind     TEXT,                          -- independent | auto_scan
  review_status     TEXT NOT NULL DEFAULT 'unreviewed',
  baseline_or_scope TEXT,                          -- 对照基线 / 新颖性检索范围
  boundary          TEXT,                          -- 适用边界
  status            TEXT NOT NULL,                 -- pending|confirmed|candidate|rejected
  reason_codes      TEXT NOT NULL DEFAULT '[]',    -- 判据命中码（JSON 数组）
  reason_source     TEXT NOT NULL DEFAULT 'manual',-- manual|rule_derived|auto_pending
  repo              TEXT,
  organization_id   BIGINT,
  window_finished_at TEXT,                         -- 所属 mission 完结时间（统计窗口）
  created_at        TEXT NOT NULL,
  decided_at        TEXT
);

DO $$ BEGIN
  ALTER TABLE outcome_evaluations ADD CONSTRAINT ck_outcome_eval_status
    CHECK (status IN ('pending','confirmed','candidate','rejected'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE outcome_evaluations ADD CONSTRAINT ck_outcome_eval_grade
    CHECK (grade IS NULL OR grade IN ('S1','S2','S3','S4','S5','S-N'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE outcome_evaluations ADD CONSTRAINT ck_outcome_eval_e_level
    CHECK (e_level IS NULL OR e_level IN ('E0','E1','E2','E3','E4'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE outcome_evaluations ADD CONSTRAINT ck_outcome_eval_reviewer_kind
    CHECK (reviewer_kind IS NULL OR reviewer_kind IN ('independent','auto_scan'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 待评价记录不得预填等级；反之非 pending/rejected 行必须带完整 S×E
DO $$ BEGIN
  ALTER TABLE outcome_evaluations ADD CONSTRAINT ck_outcome_eval_pending_blank
    CHECK (status <> 'pending' OR (grade IS NULL AND e_level IS NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE outcome_evaluations ADD CONSTRAINT ck_outcome_eval_decided_graded
    CHECK (status IN ('pending','rejected') OR (grade IS NOT NULL AND e_level IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 幂等：同一成果只允许一条有效（非 rejected）记录；被拒尝试可多行留痕
CREATE UNIQUE INDEX IF NOT EXISTS uq_outcome_eval_artifact_active
  ON outcome_evaluations (artifact_id) WHERE status <> 'rejected';
CREATE INDEX IF NOT EXISTS idx_outcome_eval_mission
  ON outcome_evaluations (mission_id, status);
CREATE INDEX IF NOT EXISTS idx_outcome_eval_grade
  ON outcome_evaluations (status, grade, e_level);

COMMIT;