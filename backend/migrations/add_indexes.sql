-- 开放数字化底座 - 数据库索引优化
-- 执行前请确保数据库备份

-- 用户表索引
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

-- 项目表索引
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects(created_at);

-- 数据集表索引
CREATE INDEX IF NOT EXISTS idx_datasets_user_id ON datasets(user_id);
CREATE INDEX IF NOT EXISTS idx_datasets_status ON datasets(status);
CREATE INDEX IF NOT EXISTS idx_datasets_data_type ON datasets(data_type);

-- 模型表索引
CREATE INDEX IF NOT EXISTS idx_models_user_id ON models(user_id);
CREATE INDEX IF NOT EXISTS idx_models_project_id ON models(project_id);
CREATE INDEX IF NOT EXISTS idx_models_status ON models(status);
CREATE INDEX IF NOT EXISTS idx_models_algorithm_type ON models(algorithm_type);

-- 训练任务表索引
CREATE INDEX IF NOT EXISTS idx_training_jobs_model_id ON training_jobs(model_id);
CREATE INDEX IF NOT EXISTS idx_training_jobs_status ON training_jobs(status);
CREATE INDEX IF NOT EXISTS idx_training_jobs_created_at ON training_jobs(created_at);

-- 部署表索引
CREATE INDEX IF NOT EXISTS idx_deployments_model_id ON deployments(model_id);
CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments(status);

-- 数据质量规则表索引
CREATE INDEX IF NOT EXISTS idx_data_quality_rules_rule_type ON data_quality_rules(rule_type);
CREATE INDEX IF NOT EXISTS idx_data_quality_rules_is_active ON data_quality_rules(is_active);

-- 数据质量结果表索引
CREATE INDEX IF NOT EXISTS idx_data_quality_results_rule_id ON data_quality_results(rule_id);
CREATE INDEX IF NOT EXISTS idx_data_quality_results_check_time ON data_quality_results(check_time);
CREATE INDEX IF NOT EXISTS idx_data_quality_results_status ON data_quality_results(status);

-- 复合索引
CREATE INDEX IF NOT EXISTS idx_models_user_status ON models(user_id, status);
CREATE INDEX IF NOT EXISTS idx_training_jobs_status_created ON training_jobs(status, created_at DESC);

-- 分析索引使用情况
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan as index_scans,
    idx_tup_read as tuples_read,
    idx_tup_fetch as tuples_fetched
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC;
