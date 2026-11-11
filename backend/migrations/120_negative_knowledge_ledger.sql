-- 120_negative_knowledge_ledger.sql
-- 否定性知识台账（Negative Knowledge Ledger）
--
-- 用途：登记被证伪假设/失败方法/被阻塞路径，供派单前检索门（gate）和
--       prompt 注入消费端使用。零领域语义——schema 通用，域数据经配置注入。
--
-- 机制（代码）：tools/negative_knowledge/（store/search/config）
--             backend/api/negative_knowledge.py（API 端点）
--
-- 纪律：
-- 1. 每条必须有 falsification_evidence（数字/工件路径/日志摘要）
-- 2. 同一 (mission_id, node_id) 不重复登记（唯一约束）
-- 3. 状态变更（superseded/retracted）必须写原因
-- 4. 派单前检索门：proposal 与台账条目相似度 > reject_threshold → 必须声明差异化

BEGIN;

CREATE TABLE IF NOT EXISTS negative_knowledge_ledger (
    id                        SERIAL PRIMARY KEY,
    entry_id                  VARCHAR(64) NOT NULL UNIQUE,
    mission_id                BIGINT,
    plan_id                   BIGINT,
    node_id                   VARCHAR(128),
    title                     VARCHAR(500) NOT NULL,
    hypothesis                TEXT NOT NULL,
    falsification_evidence    TEXT,
    failure_reason            TEXT,
    failure_kind              VARCHAR(64) NOT NULL,
    applicable_conditions     TEXT NOT NULL DEFAULT '{}',
    keywords                  TEXT NOT NULL DEFAULT '[]',
    domain_tags               TEXT NOT NULL DEFAULT '[]',
    differentiation_required  TEXT NOT NULL DEFAULT '[]',
    source_artifacts          TEXT NOT NULL DEFAULT '[]',
    confidence                FLOAT NOT NULL DEFAULT 0.8,
    schema_version            VARCHAR(16) NOT NULL DEFAULT '1.0',
    registered_by             VARCHAR(128),
    registered_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    status                    VARCHAR(24) NOT NULL DEFAULT 'active',
    CONSTRAINT nk_failure_kind_check
        CHECK (failure_kind IN ('hypothesis_falsified','method_failed','blocked','infeasible','regression')),
    CONSTRAINT nk_status_check
        CHECK (status IN ('active','superseded','retracted')),
    CONSTRAINT nk_confidence_range
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CONSTRAINT nk_mission_node_uq UNIQUE (mission_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_nk_status ON negative_knowledge_ledger (status);
CREATE INDEX IF NOT EXISTS idx_nk_failure_kind ON negative_knowledge_ledger (failure_kind);
CREATE INDEX IF NOT EXISTS idx_nk_mission ON negative_knowledge_ledger (mission_id);
CREATE INDEX IF NOT EXISTS idx_nk_registered_at ON negative_knowledge_ledger (registered_at);

-- 全文检索支持（PostgreSQL tsvector）
ALTER TABLE negative_knowledge_ledger ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(title, '') || ' ' ||
            coalesce(hypothesis, '') || ' ' ||
            coalesce(failure_reason, '') || ' ' ||
            coalesce(keywords, '')
        )
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_nk_search_vector ON negative_knowledge_ledger USING GIN (search_vector);

COMMIT;

-- 回滚（手工）：
--   DROP TABLE IF EXISTS negative_knowledge_ledger;
