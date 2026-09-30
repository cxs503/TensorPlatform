-- 用户可管理的模型分类文件夹；训练产物可选归入其中。
CREATE TABLE IF NOT EXISTS model_folders (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_model_folders_organization_name UNIQUE (organization_id, name)
);

ALTER TABLE models
    ADD COLUMN IF NOT EXISTS folder_id INTEGER REFERENCES model_folders(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_model_folders_organization_id ON model_folders(organization_id);
CREATE INDEX IF NOT EXISTS idx_models_folder_id ON models(folder_id);
