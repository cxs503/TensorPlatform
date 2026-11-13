-- 124: 需求入榜 v1.1 —— 事后抽样审计（FEAT-DEMAND-AUTOINTAKE，m17298）
--
-- 背景：v1（m17133）把「提出 → 入榜 → 事前门 → open（可领面）」做成了机器链；但锚一旦
-- 过了事前门就再没人回头看——可领面只有入口、没有出口，噪声锚只能靠人肉发现。本次补的
-- 是出口：按日帽抽样重审已上架锚，不合格则 dropped，并回撤它带出来的未终态单。
--
-- 幂等：全部 IF NOT EXISTS / CREATE OR REPLACE；重复执行是空操作。
-- 只加不删：不改 v1 任何列定义，审计列缺省空串 = 未审（v1 已上架行天然进入待审面）。
-- 零领域语义：只存裁决词、原因码与时间，不约束任何域词条。
--
-- 与代码的对应：本文件 = 生产迁移路径；tools/demand_intake/store.py:audit_ddl() =
-- 测试/红队与 `cli audit` 的自愈路径。两处同形状（同一份语句清单），避免「测试绿、生产缺列」。

BEGIN;

-- ── 1. 审计列（demand_intake_log 上就地加列） ──────────────────────────────
-- mission_id：锚 → 产出它的单（v1 只存了 ledger_id，回撤时要能直连撤单）。
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS mission_id    bigint;
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS audit_verdict text NOT NULL DEFAULT '';
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS audit_reason  text NOT NULL DEFAULT '';
ALTER TABLE demand_intake_log ADD COLUMN IF NOT EXISTS audited_at    timestamptz;

-- 待审面查询（日志 join 账本 where status='open' and audit_verdict=''）的支撑索引；
-- 部分索引只覆盖已审行，用于「今日已审数」计数与覆盖率统计，不膨胀全表。
CREATE INDEX IF NOT EXISTS demand_intake_log_audit_idx
    ON demand_intake_log (audited_at) WHERE audit_verdict <> '';

-- ── 2. 审计口径视图：数字唯一来源（报告/周报/红队共用，禁止手抄） ──────────
CREATE OR REPLACE VIEW demand_intake_audit_daily AS
SELECT date_trunc('day', audited_at)::date                AS day,
       count(*) FILTER (WHERE audit_verdict = 'pass')     AS audit_pass,
       count(*) FILTER (WHERE audit_verdict = 'fail')     AS audit_fail,
       count(*)                                           AS audited
  FROM demand_intake_log
 WHERE audit_verdict <> '' AND audited_at IS NOT NULL
 GROUP BY 1;

COMMENT ON VIEW demand_intake_audit_daily IS
    '需求入榜 v1.1 事后审计日度口径（pass/fail/audited）。';

COMMIT;