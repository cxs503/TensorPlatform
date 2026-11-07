-- 105 结论五元证据链（IN59-A3）
--
-- 每个 S2+ 成果声明（outcome_evaluations 行）可追溯五元：
--   theory  理论或文献依据（doi/url/derivation）
--   code    代码版本（commit sha / repo@sha）
--   config  实验配置（sha256 摘要）
--   data    原始计算结果（数据文件 / 图锚 / 摘要）
--   report  分析报告（report_id）
--
-- 判据（tools.evidence_chain_link.analyze_chain + tools.outcome_evaluations.decide）：
--   链不全（缺件或形态非法）→ E 级封顶 E1；封顶后低于该 S 级 E 门槛 → candidate。
--   本迁移只兜取值域与完整度范围（防绕过 API 直写脏数据），判据本体在 Python 侧。
--
-- 另建视图 outcome_eval_chain_audit：结算面（信用结算）按 chain_blocked 拦付，
--   保证「链不完整的 S3+ 不按申报等级入账」在 SQL 层也可复核。
BEGIN;

CREATE TABLE IF NOT EXISTS outcome_evidence_chains (
  chain_id          TEXT PRIMARY KEY,
  evaluation_id     TEXT NOT NULL,                 -- 关联 outcome_evaluations.evaluation_id
  artifact_id       TEXT NOT NULL,
  mission_id        BIGINT,
  contributor       TEXT,
  conclusion_ref    TEXT,                          -- 结论/声明 ID（result 内成果声明锚）
  grade             TEXT,
  e_level_requested TEXT,                          -- 申报 E
  e_level_granted   TEXT,                          -- 封顶后 E（只降不升）
  theory_ref        TEXT,
  code_ref          TEXT,
  config_ref        TEXT,
  data_ref          TEXT,
  report_ref        TEXT,
  present_elements  TEXT NOT NULL DEFAULT '[]',
  missing_elements  TEXT NOT NULL DEFAULT '[]',
  invalid_elements  TEXT NOT NULL DEFAULT '[]',
  completeness      REAL NOT NULL,                 -- 五元可解析元数 / 5
  chain_status      TEXT NOT NULL,                 -- complete|partial|missing
  capped            BOOLEAN NOT NULL DEFAULT FALSE,
  codes             TEXT NOT NULL DEFAULT '[]',
  repo              TEXT,
  organization_id   BIGINT,
  created_at        TEXT NOT NULL
);

DO $$ BEGIN
  ALTER TABLE outcome_evidence_chains ADD CONSTRAINT ck_outcome_chain_status
    CHECK (chain_status IN ('complete','partial','missing'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE outcome_evidence_chains ADD CONSTRAINT ck_outcome_chain_completeness
    CHECK (completeness >= 0.0 AND completeness <= 1.0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE outcome_evidence_chains ADD CONSTRAINT ck_outcome_chain_e_requested
    CHECK (e_level_requested IS NULL OR e_level_requested IN ('E0','E1','E2','E3','E4'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE outcome_evidence_chains ADD CONSTRAINT ck_outcome_chain_e_granted
    CHECK (e_level_granted IS NULL OR e_level_granted IN ('E0','E1','E2','E3','E4'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 一条评价一行（重判幂等：写链先删同键再插）
CREATE UNIQUE INDEX IF NOT EXISTS uq_outcome_chain_evaluation
  ON outcome_evidence_chains (evaluation_id);
CREATE INDEX IF NOT EXISTS idx_outcome_chain_status
  ON outcome_evidence_chains (chain_status, capped);
CREATE INDEX IF NOT EXISTS idx_outcome_chain_mission
  ON outcome_evidence_chains (mission_id);

DROP VIEW IF EXISTS outcome_eval_chain_audit;
CREATE VIEW outcome_eval_chain_audit AS
SELECT
  e.evaluation_id,
  e.artifact_id,
  e.mission_id,
  e.contributor,
  e.grade,
  e.e_level,
  e.status,
  e.organization_id,
  coalesce(c.chain_status, 'missing') AS chain_status,
  coalesce(c.completeness, 0.0) AS chain_completeness,
  coalesce(c.capped, FALSE) AS chain_capped,
  CASE
    WHEN e.status = 'confirmed'
     AND e.grade IN ('S3','S4','S5','S-N')
     AND coalesce(c.chain_status, 'missing') <> 'complete' THEN TRUE
    ELSE FALSE
  END AS chain_blocked
FROM outcome_evaluations e
LEFT JOIN outcome_evidence_chains c ON c.evaluation_id = e.evaluation_id;

COMMIT;