-- 工作空间隔离迁移脚本
-- 添加 workspace 列到 organizations 表
-- 添加 organization_id 外键到 datasets, models, training_jobs 表

-- 1. organizations 表：添加 workspace 列
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS workspace VARCHAR(32) DEFAULT 'personal' NOT NULL;

-- 2. datasets 表：添加 organization_id
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS organization_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_datasets_organization_id ON datasets(organization_id);

-- 3. models 表：添加 organization_id
ALTER TABLE models ADD COLUMN IF NOT EXISTS organization_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_models_organization_id ON models(organization_id);

-- 4. training_jobs 表：添加 organization_id
ALTER TABLE training_jobs ADD COLUMN IF NOT EXISTS organization_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_training_jobs_organization_id ON training_jobs(organization_id);
