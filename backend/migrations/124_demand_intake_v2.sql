-- 124: 需求入榜 v2 —— 提案即单子（FEAT-DEMAND-AUTOINTAKE，m17134）
--
-- 背景（owner 2026-11-13 主体令 v2）：agent 的建议要能自动变单子，agent 与协调者同权，
-- agent 是独立思考判断的主体而不是被调用的工具。v1（m17133）把每个候选锚先扣在
-- feature_ledger.status='pending_review' 等采认——建议停在半路、单子不产生，等价于把
-- 「独立思考」记为零产出。v2 把门搬到事后：
--
--   ① 拆事前门：铸造即 feature_ledger.status='open'，并同步 POST /tensor/missions；
--   ② 审计改事后：抽样帽 20/日（agent-provided 锚），不过则 dropped + 关联未终态单
--      revoked + chronicle_snapshots 留痕；
--   ③ 机器门：帽 10/agent/日，tensor_agent_integrity.strikes >= 3 降级 1/日，并写
--      worker_credit_tiers 联动（派发准入与铸造帽看到同一事实）；
--   ④ 对照计量：周报给出 agent 铸造 vs 协调者铸造的过审率/单子成功率。
--
-- 幂等：全部 IF NOT EXISTS / ADD COLUMN IF NOT EXISTS；重复执行是空操作。
-- 只加不删：v1 的列（confidence / src_field / ledger_id / raw）保留，历史可回溯。
-- 零领域语义：只存 slug 形状、量化验收、状态与计数，不约束任何域词条。

BEGIN;

-- ── 1. 铸造轨迹表（v2 全量定义；已存在则只补列） ─────────────────────────
CREATE TABLE IF NOT EXISTS demand_intake_log (
  id              bigserial PRIMARY KEY,
  cand_key        text NOT NULL UNIQUE,
  source_kind     text NOT NULL DEFAULT 'finish',
  source_ref      text NOT NULL DEFAULT '',
  slug            text NOT NULL DEFAULT '',
  title           text NOT NULL DEFAULT '',
  acceptance      text NOT NULL DEFAULT '',
  domain          text NOT NULL DEFAULT 'platform',
  repo            text NOT NULL DEFAULT '',
  confidence      text NOT NULL DEFAULT '',
  src_field       text NOT NULL DEFAULT '',
  status          text NOT NULL,
  reason          text NOT NULL DEFAULT '',
  ledger_id       integer,
  mission_id      bigint,
  mint_kind       text NOT NULL DEFAULT 'agent',
  minted_by       text NOT NULL DEFAULT '',
  org_id          bigint NOT NULL DEFAULT 0,
  cap_applied     integer NOT NULL DEFAULT 0,
  audit_verdict   text NOT NULL DEFAULT '',
  audit_reason    text NOT NULL DEFAULT '',
  audited_at      timestamptz,
  raw             jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now());

ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS repo          text NOT NULL DEFAULT '';
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS mission_id    bigint;
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS mint_kind     text NOT NULL DEFAULT 'agent';
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS minted_by     text NOT NULL DEFAULT '';
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS org_id        bigint NOT NULL DEFAULT 0;
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS cap_applied   integer NOT NULL DEFAULT 0;
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS audit_verdict text NOT NULL DEFAULT '';
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS audit_reason  text NOT NULL DEFAULT '';
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS audited_at    timestamptz;

CREATE INDEX IF NOT EXISTS demand_intake_log_status_idx ON demand_intake_log (status);
CREATE INDEX IF NOT EXISTS demand_intake_log_slug_idx   ON demand_intake_log (slug);
CREATE INDEX IF NOT EXISTS demand_intake_log_mint_idx   ON demand_intake_log (mint_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS demand_intake_log_audit_idx  ON demand_intake_log (audited_at)
    WHERE audit_verdict <> '';

-- ── 2. 铸造帽计数器（自增 + 上限必须单行原子：并发超发在这里被唯一键挡住） ──
CREATE TABLE IF NOT EXISTS demand_mint_quota (
  day        date NOT NULL,
  agent      text NOT NULL,
  minted     integer NOT NULL DEFAULT 0,
  cap        integer NOT NULL DEFAULT 10,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, agent));

-- ── 3. 拆事前门：v1 的 pending_review 一律放行为 open（建议即单子） ────────
-- 只动锚面与轨迹面；既有终态（done/verified/dropped）与其它来源的行不受影响。
UPDATE feature_ledger
   SET status = 'open'
 WHERE status = 'pending_review';

UPDATE demand_intake_log
   SET status = 'minted', updated_at = now()
 WHERE status = 'pending_review';

-- 审计口径：v1 已入榜但未审计的行视为「待审」（audit_verdict 空串 = 未审，天然如此）。

-- ── 4. 报告视图：铸造对照（agent vs coordinator），数字唯一来源 ─────────────
CREATE OR REPLACE VIEW demand_intake_mint_daily AS
SELECT date_trunc('day', l.created_at)::date AS day,
       l.mint_kind,
       count(*) FILTER (WHERE l.status IN ('minted','dispatch_failed')) AS minted,
       count(*) FILTER (WHERE l.status = 'cap_exceeded')                AS cap_exceeded,
       count(*) FILTER (WHERE l.status = 'duplicate')                   AS duplicate,
       count(*) FILTER (WHERE l.audit_verdict = 'pass')                 AS audit_pass,
       count(*) FILTER (WHERE l.audit_verdict = 'fail')                 AS audit_fail,
       count(*) FILTER (WHERE l.mission_id IS NOT NULL)                 AS missions_dispatched
  FROM demand_intake_log l
 GROUP BY 1, 2;

COMMIT;