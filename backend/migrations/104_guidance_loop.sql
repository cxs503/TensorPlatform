-- 104: 引导回路（Guidance Loop）实体补齐 —— 评价→派发牵引的落库面。
--
-- 背景：评价体系此前只有「记账」面（credit_ledger 记录终态预算用量），缺少
-- 「牵引」面：派发梯度、准入集合、探索预算、创新提案、审查预算四类账目都没有
-- 可查询的库内实体。本迁移补齐下列五张实体表 + 一张周期快照表：
--   review_budget_ledger  审查/豁免/生产账目（sha256 链，只增不改）
--   innovation_proposals  创新提案登记与升级闸门
--   task_gradient         每轮派发梯度快照（基线份额 vs 校准份额 vs 实际派单数）
--   exploration_budget    探索预算：账户 / 结算 / S-N 轨（honest FAIL 豁免留痕）
--   guidance_outcome      带档位的任务结论账本（评价统计的基座）
--   guidance_cycle        每轮周期快照（统计指纹 + 公开载荷）
--
-- 可移植性：本文件被单测当作建库脚本直接执行（SQLite），因此只使用
-- TEXT / INTEGER / DOUBLE PRECISION / BOOLEAN / TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP、
-- IF NOT EXISTS 与标准 CHECK —— Postgres 与 SQLite 均接受，保证「迁移 ⇔ 代码」不漂。
-- organization_id 为软引用（不加外键）：引导回路是多租户共享的度量面，
-- 允许在无组织上下文的部署验证/回放中写入（本仓惯例：度量表不阻塞租户生命周期）。

-- ── 1. 审查预算账本（四类审查 + 豁免 + 生产计量，sha256 链） ──
CREATE TABLE IF NOT EXISTS review_budget_ledger (
    entry_id        TEXT PRIMARY KEY,
    seq             INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('review','exemption','production')),
    review_type     TEXT NOT NULL CHECK (review_type IN ('execution','technical','outcome','innovation')),
    subject         TEXT NOT NULL,
    conclusion      TEXT NOT NULL,
    reviewer        TEXT NOT NULL,
    duration_s      DOUBLE PRECISION,
    duration_source TEXT NOT NULL DEFAULT 'not_observable',
    source          TEXT NOT NULL DEFAULT 'caller',
    note            TEXT,
    prev_hash       TEXT NOT NULL,
    entry_hash      TEXT NOT NULL UNIQUE,
    organization_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_review_budget_ledger_seq ON review_budget_ledger (seq);
CREATE INDEX IF NOT EXISTS ix_review_budget_ledger_subject ON review_budget_ledger (subject);

-- ── 2. 创新提案（未过创新审查不得升级） ──
CREATE TABLE IF NOT EXISTS innovation_proposals (
    proposal_id     TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    hypothesis      TEXT,
    status          TEXT NOT NULL DEFAULT 'submitted'
                    CHECK (status IN ('submitted','under_review','approved','rejected','parked')),
    review_entry_id TEXT,
    evidence        TEXT,
    organization_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_innovation_proposals_status ON innovation_proposals (status);

-- ── 3. 派发梯度快照（一轮 × 一档位 一行） ──
CREATE TABLE IF NOT EXISTS task_gradient (
    cycle_id         TEXT NOT NULL,
    tier             TEXT NOT NULL CHECK (tier IN ('S1','S2','S3','S4','S5')),
    base_share       DOUBLE PRECISION NOT NULL,
    calibrated_share DOUBLE PRECISION NOT NULL,
    dispatched_count INTEGER NOT NULL,
    tier_yield       DOUBLE PRECISION,
    yield_bar        DOUBLE PRECISION,
    multiplier       DOUBLE PRECISION,
    stats_digest     TEXT NOT NULL,
    total_pool       INTEGER NOT NULL,
    reason           TEXT,
    organization_id  BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cycle_id, tier)
);

CREATE INDEX IF NOT EXISTS ix_task_gradient_digest ON task_gradient (stats_digest);

-- ── 4. 探索预算账目（account / settlement / s_n 三类行） ──
CREATE TABLE IF NOT EXISTS exploration_budget (
    entry_id        TEXT PRIMARY KEY,
    cycle_id        TEXT NOT NULL,
    worker          TEXT NOT NULL,
    tier            TEXT,
    track           TEXT NOT NULL,
    entry_type      TEXT NOT NULL CHECK (entry_type IN ('account','settlement','s_n')),
    allocated       DOUBLE PRECISION NOT NULL DEFAULT 0,
    consumed        DOUBLE PRECISION NOT NULL DEFAULT 0,
    refunded        DOUBLE PRECISION NOT NULL DEFAULT 0,
    remaining       DOUBLE PRECISION NOT NULL DEFAULT 0,
    multiplier      DOUBLE PRECISION NOT NULL DEFAULT 1,
    mission_key     TEXT,
    outcome         TEXT,
    exempt          BOOLEAN NOT NULL DEFAULT FALSE,
    organization_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_exploration_budget_worker ON exploration_budget (worker, cycle_id);
CREATE INDEX IF NOT EXISTS ix_exploration_budget_track ON exploration_budget (track);

-- ── 5. 带档位的任务结论账本（评价统计基座；只增不改） ──
CREATE TABLE IF NOT EXISTS guidance_outcome (
    mission_key     TEXT PRIMARY KEY,
    worker          TEXT NOT NULL,
    tier            TEXT NOT NULL CHECK (tier IN ('S1','S2','S3','S4','S5')),
    outcome         TEXT NOT NULL CHECK (outcome IN ('success','mechanical_fail','honest_fail','stopped')),
    exempt          BOOLEAN NOT NULL DEFAULT FALSE,
    cycle_id        TEXT,
    organization_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_guidance_outcome_worker ON guidance_outcome (worker);
CREATE INDEX IF NOT EXISTS ix_guidance_outcome_tier ON guidance_outcome (tier, outcome);

-- ── 6. 周期快照（统计指纹 → 幂等键） ──
CREATE TABLE IF NOT EXISTS guidance_cycle (
    cycle_id          TEXT PRIMARY KEY,
    stats_digest      TEXT NOT NULL,
    total_pool        INTEGER NOT NULL,
    yield_bar         DOUBLE PRECISION,
    exploration_share DOUBLE PRECISION,
    payload           TEXT,
    organization_id   BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);