-- repro_pool —— 主线锚「论文复现」锚源表（FEAT-ANCHOR-FEEDER 验收①）
-- 权威定义在 ops/taskplane/anchor_feeder.py:REPRO_POOL_DDL（守护进程每轮 IF NOT EXISTS 自建，
-- 本文件供 DBA/评审预建与审阅，二者内容保持一致）。
-- 播种来源（anchor_feeder.seed_rows，纯函数、幂等 ON CONFLICT）：
--   a) tensor_repro_papers             —— 文献清单（paper_key/title/ref_source/target_repo/quantitative_targets）
--   b) tensor_benchmark_cases          —— anchor_slug ~ '^REPRO-' OR ref_source 含年份（paper 类）
-- 覆盖账回流：feature_ledger 中该 anchor_slug 消账（done/verified）→ status='passed'

CREATE TABLE IF NOT EXISTS repro_pool (
    id           serial PRIMARY KEY,
    paper_key    text UNIQUE NOT NULL,      -- 幂等键（论文 key 或 benchmark anchor_slug）
    kind         text NOT NULL DEFAULT 'paper',   -- paper | bench-paper
    title        text NOT NULL,
    ref_source   text NOT NULL,
    target_repo  text,
    tol_policy   text,                      -- 容差判据（来自论文定量目标 jsonb，零手抄）
    anchor_slug  text,                      -- REPRO-*
    status       text NOT NULL DEFAULT 'pending',  -- pending | dispatched | passed
    evidence     text,
    created_at   timestamptz DEFAULT now(),
    updated_at   timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS repro_pool_status_idx ON repro_pool (status, target_repo);