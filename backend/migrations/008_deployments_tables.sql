-- 008: deployment fact tables.
--
-- The deployment APIs (api/deployment.py, api/model_deployment.py, api/abtest.py,
-- api/frontend_compat.py, api/dashboard.py) address these tables through raw SQL
-- and there is no ORM model for them, so Base.metadata.create_all() never created
-- them. Without this migration every deployment read degrades to empty/zero and
-- migration 014 (model_version_id link + is_deployed backfill) cannot run.
--
-- deployments is the canonical deployment registry consumed by the platform.
-- model_deployments keeps the legacy lightweight deploy registry that
-- api/model_deployment.py and api/models.py still read for backward compatibility.
-- ab_experiments is the A/B experiment registry that api/abtest.py drives through
-- raw SQL; migration 026 later adds deployment_a_id/deployment_b_id/
-- organization_id/updated_at via ADD COLUMN IF NOT EXISTS, so the base columns
-- created here stay free of those and the backfill in 026 remains authoritative.

-- 部署登记表（平台部署管理的事实来源）
CREATE TABLE IF NOT EXISTS deployments (
    id SERIAL PRIMARY KEY,
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    model_version_id INTEGER REFERENCES model_versions(id) ON DELETE SET NULL,
    name VARCHAR(255),
    version VARCHAR(64),
    status VARCHAR(32) DEFAULT 'running',
    config JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deployments_model_id ON deployments (model_id);
CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments (status);

-- 旧版轻量部署登记表（向后兼容 api/model_deployment.py 与 api/models.py 的统计）
CREATE TABLE IF NOT EXISTS model_deployments (
    id SERIAL PRIMARY KEY,
    model_id INTEGER REFERENCES models(id) ON DELETE CASCADE,
    model_path TEXT,
    status VARCHAR(32) DEFAULT 'running',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_deployments_model_id ON model_deployments (model_id);

-- A/B 实验登记表（api/abtest.py 通过原始 SQL 读写；026 追加部署/组织关联列）
CREATE TABLE IF NOT EXISTS ab_experiments (
    id BIGSERIAL PRIMARY KEY,
    experiment_id VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(256) NOT NULL,
    model_a_id INTEGER REFERENCES models(id) ON DELETE CASCADE,
    model_b_id INTEGER REFERENCES models(id) ON DELETE CASCADE,
    metric VARCHAR(32) NOT NULL DEFAULT 'accuracy',
    traffic_split_a DOUBLE PRECISION NOT NULL DEFAULT 50.0,
    status VARCHAR(32) NOT NULL DEFAULT 'created',
    winner VARCHAR(16),
    results_a JSONB NOT NULL DEFAULT '[]'::jsonb,
    results_b JSONB NOT NULL DEFAULT '[]'::jsonb,
    analysis JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ab_experiments_status ON ab_experiments (status);
