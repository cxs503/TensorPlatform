-- ============================================================
-- 数字化底座 基础环境优化 DB 迁移 (2026-07-15)
-- 用于全新部署 / 重建镜像后执行(运行中的库已应用,无需重跑)
-- ============================================================

-- 1) 模型生命周期 stage 列(模型晋升 development→staging→production)
--    models/core.py 的 Model 已加该列;此处保证 DB schema 同步(alembic 未覆盖)
ALTER TABLE models ADD COLUMN IF NOT EXISTS stage VARCHAR(32) DEFAULT 'development';

-- 2) MLflow Model Registry 后端库(从 FileStore 切 PostgreSQL,启用 Registry)
--    需超级用户执行;MLflow 首次连接会自动建表
CREATE DATABASE mlflow OWNER foundation;
GRANT ALL PRIVILEGES ON DATABASE mlflow TO foundation;

-- 3) training_jobs 表 schema 重建(2026-07-15 损坏后重建的版本)
--    ORM(models/core.py TrainingJob)create_all 已含此定义;
--    若用 alembic 且表缺失,用以下 DDL 兜底:
-- CREATE TABLE IF NOT EXISTS training_jobs (
--   id SERIAL PRIMARY KEY,
--   name VARCHAR(128) NOT NULL,
--   model_id INTEGER REFERENCES models(id),
--   dataset_id INTEGER REFERENCES datasets(id),
--   training_job_id VARCHAR(64) UNIQUE,
--   status VARCHAR(32) DEFAULT 'PENDING',
--   hyperparameters JSON,
--   metrics JSON,
--   started_at TIMESTAMP,
--   completed_at TIMESTAMP,
--   error_message TEXT,
--   organization_id INTEGER,
--   created_at TIMESTAMP DEFAULT NOW()
-- );

-- 注意:
-- - zero_damaged_pages 仅 DB 损坏恢复时临时开启,正常应为 off(已重置)。
-- - Label Studio 用户(admin/foundation123)是运行时通过 `label-studio init` 创建的,
--   不在 SQL 里;新部署的 LS 实例需重新 init(见 IMAGE_PERSIST_PATCH.md)。
