-- 013: models 表增加 mlflow_name 列
-- 打通 训练 → models 表 → MLflow Registry → 部署 → 推理 的 model_id 全链路一致性
-- 命名约定: mlflow_name = foundation_model_{models.id}
-- 这样 models.id 与 MLflow registered model name 存在确定映射(双向可查)。

ALTER TABLE models ADD COLUMN IF NOT EXISTS mlflow_name VARCHAR(128);

-- 便于按 mlflow_name 反查 models.id
CREATE INDEX IF NOT EXISTS idx_models_mlflow_name ON models (mlflow_name) WHERE mlflow_name IS NOT NULL;
