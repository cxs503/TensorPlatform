-- 104 evaluation_wave（评价波执行器：席位/批次/共识审计列 + 波次表 + 引导回路表，m5180）
--
-- 迁移 103 建了 S×E 记录流（pending → confirmed/candidate/rejected）与判据执行器。
-- 本迁移**只加列/加表**，不改 103 的语义与约束：
--   1. outcome_evaluations 增补「谁评的、怎么评的、是否跨族、批次/波次」的审计列，
--      用于把「独立席位评价」从口头声明变成可 SQL 复算的事实。
--   2. evaluation_waves  — 一波（一次全量评价）的窗口/批次/席位/指标快照，可重放对账。
--   3. evaluation_guidance — 评价统计**反向**落成的派发梯度（准入/预算/优先级），
--      让「统计改变派发」有机器可读的落地物，而不是报告里的一句话。
--
-- 全部语句幂等（IF NOT EXISTS），可重复执行。

ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS wave_id TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS batch_id TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS seat_ids TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS seat_families TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS seat_class TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS contributor_family TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS cross_model_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS consensus TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS citation_check TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS value_flags TEXT;
ALTER TABLE outcome_evaluations ADD COLUMN IF NOT EXISTS artifact_check TEXT;

CREATE INDEX IF NOT EXISTS idx_outcome_eval_wave
    ON outcome_evaluations (wave_id, status);
CREATE INDEX IF NOT EXISTS idx_outcome_eval_seat
    ON outcome_evaluations (seat_class, status);

CREATE TABLE IF NOT EXISTS evaluation_waves (
    wave_id            TEXT PRIMARY KEY,
    window_hours       INTEGER NOT NULL,
    window_start       TIMESTAMP WITH TIME ZONE,
    window_end         TIMESTAMP WITH TIME ZONE,
    missions_in_window INTEGER NOT NULL DEFAULT 0,
    batches            INTEGER NOT NULL DEFAULT 0,
    batch_size         INTEGER NOT NULL DEFAULT 0,
    seats              TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'planned',
    metrics            TEXT NOT NULL DEFAULT '{}',
    created_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    finished_at        TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS evaluation_guidance (
    guidance_id        BIGSERIAL PRIMARY KEY,
    wave_id            TEXT NOT NULL,
    subject_kind       TEXT NOT NULL,
    subject            TEXT NOT NULL,
    tier               TEXT NOT NULL,
    budget_multiplier  NUMERIC(6, 3) NOT NULL DEFAULT 1.000,
    admission          TEXT NOT NULL DEFAULT 'open',
    priority_delta     INTEGER NOT NULL DEFAULT 0,
    rows_counted       INTEGER NOT NULL DEFAULT 0,
    evidence           TEXT NOT NULL DEFAULT '{}',
    created_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_evaluation_guidance_subject
    ON evaluation_guidance (wave_id, subject_kind, subject);