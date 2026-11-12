-- 121_knowledge_factory_work_packages.sql
-- Knowledge Factory 试点调度器（TensorPlatform 侧）持久面。
-- 零领域语义：本迁移只承载「工序 / 工包 / 门结果 / 成本 / 入库外发」这类共性机制，
-- 任务类型枚举(01-05)与泳道(A/B/C)由 ops/knowledge_factory/config.json 注入，域内容不进表结构。
-- 幂等：可重复执行。

CREATE TABLE IF NOT EXISTS tk_work_packages (
    package_id     text        PRIMARY KEY,
    report_id      bigint      NOT NULL,
    task_type      text        NOT NULL CHECK (task_type IN ('01','02','03','04','05')),
    lane           text        NOT NULL DEFAULT 'C' CHECK (lane IN ('A','B','C')),
    status         text        NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','dispatched','returned','rework',
                                     'validated','review','ingested','failed','blocked')),
    mission_id     bigint,
    reviewer_mission_id bigint,
    skill_id       text,
    stratum        text,
    attempt        integer     NOT NULL DEFAULT 0,
    gates          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    gates_ok       boolean,
    cost           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    contract       jsonb,
    result_report_id bigint,
    fail_reason    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tk_wp_status      ON tk_work_packages (status, task_type);
CREATE INDEX IF NOT EXISTS idx_tk_wp_report      ON tk_work_packages (report_id, task_type);
CREATE INDEX IF NOT EXISTS idx_tk_wp_mission     ON tk_work_packages (mission_id);
CREATE INDEX IF NOT EXISTS idx_tk_wp_lane        ON tk_work_packages (lane, status);

-- 入库外发（outbox）：平台只产出 draft 信封，KE 侧消费落库。
-- verification_status 结构性锁定为 unverified：平台侧永不写 verified（三重状态分列）。
CREATE TABLE IF NOT EXISTS tk_ingest_drafts (
    ko_id            text        PRIMARY KEY,
    package_id       text        NOT NULL,
    report_id        bigint      NOT NULL,
    lane             text        NOT NULL DEFAULT 'C',
    claim_text       text,
    payload          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    verification_status text     NOT NULL DEFAULT 'unverified'
                     CHECK (verification_status = 'unverified'),
    ko_review_status text        NOT NULL DEFAULT 'unreviewed'
                     CHECK (ko_review_status IN ('unreviewed','passed','needs-fix','failed','skipped')),
    science_verification_status text NOT NULL DEFAULT 'not_verified'
                     CHECK (science_verification_status IN ('not_verified','ticketed','verified','refuted')),
    mission_status   text        NOT NULL DEFAULT 'done',
    consumed_by      text,
    consumed_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tk_draft_pkg  ON tk_ingest_drafts (package_id);
CREATE INDEX IF NOT EXISTS idx_tk_draft_open ON tk_ingest_drafts (consumed_at) WHERE consumed_at IS NULL;

-- 知识复用事件：KE/求解器/报告侧引用某 KO 时登记，看板「知识复用次数」的唯一来源。
CREATE TABLE IF NOT EXISTS tk_knowledge_uses (
    id         bigserial   PRIMARY KEY,
    ko_id      text        NOT NULL,
    consumer   text,
    context    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    ts         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tk_uses_ko ON tk_knowledge_uses (ko_id);

-- 生产看板快照：owner 八指标逐轮落表（log + 表双写）。
CREATE TABLE IF NOT EXISTS tk_metric_snapshots (
    id      bigserial   PRIMARY KEY,
    ts      timestamptz NOT NULL DEFAULT now(),
    round   text,
    metrics jsonb       NOT NULL
);