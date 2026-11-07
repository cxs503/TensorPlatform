-- 105 eval_cohorts（评价队列 cohort 汇总表，IN38-N09 评价运维成熟化）
--
-- 目标：把「覆盖率」从**窗口相关量**改成**队列不变量**。
--   旧口径：覆盖率 = 最近 N 张 succeeded mission 里有评价的比例（N 一变数就变）。
--   新口径：mission.finished_at 落在哪个 ISO 周，就属于哪个 cohort（不可变队列）；
--           覆盖率 = 该 cohort 内已判定 mission 数 / 该 cohort mission 总数，
--           与 7/30/90 天视图无关（视图只决定显示哪些 cohort）。
--
-- 本迁移**只加表/索引**，不改任何既有表、不动 dispatcher 调度面。
--
-- 列语义（与 tools/evaluation_ops/cohorts.py 的 METRIC_FIELDS 一一对应，改口径必须同改）：
--   cohort_key            'YYYY-Www'（ISO 周，UTC，周一 00:00 起），主键
--   week_start/week_end   cohort 的有效区间 [周一, 下周一)（半开）
--   missions_total        该 cohort 内 succeeded 且 finished_at 可解析的 mission 数（分母）
--   missions_decided      有 confirmed|candidate 记录的 mission 数（分子）
--   missions_pending      只有 pending 记录的 mission 数（积压，不进分子）
--   missions_unrecorded   完全没有有效记录的 mission 数（覆盖缺口，不进分子）
--   coverage              missions_decided / missions_total；分母为 0 时 NULL（不折成 0%）
--   p90_latency_hours     已判定记录的 (min(decided_at) - finished_at) 的 P90；无样本 NULL
--   backlog_rows          pending 记录**行数**（SLO 口径：≤20 行）
--   backlog_age_max_hours pending 记录年龄上限，起点为 mission.finished_at（不是 created_at）
--   frozen                ISO 周已在计算时刻之前结束 → 成员固定
--   frozen_at             首次置 frozen 的时间
--   revision              行版本；迟到评价导致输入变化时 +1（旧值留在 payload.previous）
--   inputs_fingerprint    mission 集合 + 记录状态/判定时刻的内容哈希，用于区分
--                         「评分器漂移（指纹不变但数变，必须当缺陷）」与「数据迟到（指纹变）」
--   payload               整行计算上下文（含 excluded / undefined_metrics / previous 等）
--
-- 幂等（IF NOT EXISTS），可重复执行。

BEGIN;

CREATE TABLE IF NOT EXISTS eval_cohorts (
    cohort_key            TEXT PRIMARY KEY,
    week_start            TEXT NOT NULL,
    week_end              TEXT NOT NULL,
    repo                  TEXT,
    missions_total        INTEGER NOT NULL DEFAULT 0,
    missions_decided      INTEGER NOT NULL DEFAULT 0,
    missions_pending      INTEGER NOT NULL DEFAULT 0,
    missions_unrecorded   INTEGER NOT NULL DEFAULT 0,
    coverage              DOUBLE PRECISION,
    p90_latency_hours     DOUBLE PRECISION,
    backlog_rows          INTEGER NOT NULL DEFAULT 0,
    backlog_age_max_hours DOUBLE PRECISION,
    frozen                BOOLEAN NOT NULL DEFAULT FALSE,
    frozen_at             TEXT,
    revision              INTEGER NOT NULL DEFAULT 1,
    inputs_fingerprint    TEXT NOT NULL,
    computed_at           TEXT NOT NULL,
    payload               TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_eval_cohorts_repo ON eval_cohorts (repo, cohort_key);

-- 覆盖率的分母/分子一致性：分子不得超过分母，积压与缺口不得超过分母
DO $$ BEGIN
  ALTER TABLE eval_cohorts ADD CONSTRAINT ck_eval_cohorts_counts
    CHECK (missions_decided >= 0
           AND missions_pending >= 0
           AND missions_unrecorded >= 0
           AND missions_decided + missions_pending + missions_unrecorded <= missions_total);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE eval_cohorts ADD CONSTRAINT ck_eval_cohorts_coverage
    CHECK (coverage IS NULL OR (coverage >= 0 AND coverage <= 1));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;