-- 039: base tables for raw-SQL consumers without ORM models or self-healing DDL.
--
-- Defect class: several API modules address their tables through raw SQL but the
-- tables were never registered as ORM models, so Base.metadata.create_all() never
-- creates them and no numbered migration ever did either. On a freshly migrated
-- database every endpoint touching these tables fails with UndefinedTable (and
-- migration 083, which ALTERs model_evaluation_results, cannot even run).
--
-- Consumers that self-heal with inline CREATE TABLE IF NOT EXISTS (audit_logs,
-- inference_logs, research_runs, dataset_field_roles, data_source_configs,
-- data_analysis_parameter_settings, realtime_sensor_monitors,
-- realtime_sensor_alerts) are intentionally NOT duplicated here.
--
-- Column sets are derived from every INSERT/SELECT/UPDATE statement in:
--   api/model_evaluation.py, api/model_monitoring.py, api/feature_store_v2.py,
--   api/model_governance.py, api/hyperopt.py, api/model_training.py,
--   tasks/pipeline_tasks.py, api/models.py, api/rootcause.py

-- 模型评估结果（model_evaluation.py 写入/查询；pipeline_tasks.py 写入；
-- rootcause.py 读取趋势；083 随后追加 name 列）
CREATE TABLE IF NOT EXISTS model_evaluation_results (
    id SERIAL PRIMARY KEY,
    model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    task_type VARCHAR(64),
    metrics JSONB,
    cross_validation JSONB,
    cv_folds INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_eval_results_model ON model_evaluation_results (model_id);
CREATE INDEX IF NOT EXISTS idx_model_eval_results_dataset ON model_evaluation_results (dataset_id);

-- 模型监控日志（model_monitoring.py 读写与漂移更新；model_evaluation.py 写入）
CREATE TABLE IF NOT EXISTS monitoring_logs (
    id BIGSERIAL PRIMARY KEY,
    model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    predictions JSONB,
    actuals JSONB,
    metrics JSONB,
    drift_score DOUBLE PRECISION,
    drift_detected BOOLEAN NOT NULL DEFAULT FALSE,
    sample_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_monitoring_logs_model ON monitoring_logs (model_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_logs_drift ON monitoring_logs (model_id, drift_detected);

-- 特征组与在线特征值（feature_store_v2.py；group_id 为业务主键）
CREATE TABLE IF NOT EXISTS feature_groups (
    id SERIAL PRIMARY KEY,
    group_id VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    entity_name VARCHAR(255),
    features JSONB NOT NULL DEFAULT '[]'::jsonb,
    online_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feature_values (
    id BIGSERIAL PRIMARY KEY,
    group_id VARCHAR(64) NOT NULL REFERENCES feature_groups(group_id) ON DELETE CASCADE,
    entity_key VARCHAR(256) NOT NULL,
    values_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feature_values_entity ON feature_values (entity_key);
CREATE INDEX IF NOT EXISTS idx_feature_values_group ON feature_values (group_id);

-- 模型治理审批与审计（model_governance.py；models.py 删除模型时摘除审计归属）
CREATE TABLE IF NOT EXISTS governance_approvals (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL UNIQUE,
    model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    action VARCHAR(64),
    requester VARCHAR(128),
    approver VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    comments TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_governance_approvals_status ON governance_approvals (status);

CREATE TABLE IF NOT EXISTS governance_audit (
    id SERIAL PRIMARY KEY,
    model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    action VARCHAR(128),
    user_name VARCHAR(128),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_governance_audit_model ON governance_audit (model_id);

-- 超参搜索研究与试验（hyperopt.py；study_id 为业务主键，试验按 study 级联删除）
CREATE TABLE IF NOT EXISTS hyperopt_studies (
    id SERIAL PRIMARY KEY,
    study_id VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255),
    dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    objective_metric VARCHAR(64),
    direction VARCHAR(32),
    search_algorithm VARCHAR(64),
    n_trials INTEGER,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    best_value DOUBLE PRECISION,
    best_params JSONB,
    best_model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hyperopt_studies_status ON hyperopt_studies (status);

CREATE TABLE IF NOT EXISTS hyperopt_trials (
    id SERIAL PRIMARY KEY,
    study_id VARCHAR(64) NOT NULL REFERENCES hyperopt_studies(study_id) ON DELETE CASCADE,
    trial_number INTEGER NOT NULL,
    parameters JSONB,
    objective_value DOUBLE PRECISION,
    metrics JSONB,
    job_id VARCHAR(64),
    status VARCHAR(32),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hyperopt_trials_study ON hyperopt_trials (study_id, trial_number);

-- 简化训练任务（model_training.py 的演示训练与 WebSocket 进度推送；
-- dataset_id 来自原始 JSON dict，不加外键以避免非整数传入破坏写入）
CREATE TABLE IF NOT EXISTS model_training_jobs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    dataset_id INTEGER,
    model_type VARCHAR(64),
    status VARCHAR(32) DEFAULT 'running',
    model_path TEXT,
    metrics JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_training_jobs_status ON model_training_jobs (status);
