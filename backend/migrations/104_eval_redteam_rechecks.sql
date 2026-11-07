-- 104: 评价真实性红队抽样复算记录（IN24-E5）
-- 每条被抽中的 confirmed 评价一行：抽样分层、盲序号、申报/重算值、判定、动作、数字对照。
-- 记录与动作分离：记录永久留档（含 0 发现即 MATCH），动作只对 confirmed 行生效。
CREATE TABLE IF NOT EXISTS eval_redteam_rechecks (
    recheck_id        TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL,
    evaluation_id     TEXT NOT NULL,
    artifact_id       TEXT,
    mission_id        BIGINT,
    repo              TEXT,
    stratum           TEXT NOT NULL,          -- 抽样层 = 申报 S/E
    blind_order       INTEGER NOT NULL,       -- 盲序（sha256(seed:id) 排序后的第 n 单）
    declared_grade    TEXT,                   -- 申报等级
    declared_e        TEXT,                   -- 申报证据级
    recomputed_grade  TEXT,                   -- 独立重算的等级天花板
    recomputed_e      TEXT,                   -- 独立重算的证据级
    verdict           TEXT NOT NULL,          -- MATCH/UNDERCLAIM/DOWNGRADE_*/REJECT_*
    action            TEXT NOT NULL,          -- none/downgrade/reject
    mismatch_codes    TEXT NOT NULL DEFAULT '[]',
    numbers_declared  TEXT NOT NULL DEFAULT '{}',   -- grade_reason 中的数字/标识
    numbers_recomputed TEXT NOT NULL DEFAULT '{}',  -- 按平台事实复算值
    blind_payload     TEXT NOT NULL DEFAULT '{}',   -- 复算器实际吃到的盲序输入
    reviewer          TEXT NOT NULL,          -- eval-redteam-recompute
    applied           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_eval_redteam_recheck
    ON eval_redteam_rechecks (evaluation_id, run_id);
CREATE INDEX IF NOT EXISTS idx_eval_redteam_verdict
    ON eval_redteam_rechecks (run_id, verdict);