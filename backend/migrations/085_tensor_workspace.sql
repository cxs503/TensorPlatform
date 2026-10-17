-- 085: tensor_eco 六库工作区快照（executor 上报，最新一份即全景）
CREATE TABLE IF NOT EXISTS tensor_workspace_state (
    executor_id  VARCHAR(120) PRIMARY KEY REFERENCES tensor_executors(executor_id),
    -- [{name, branch, head, head_short, commit_at, subject, dirty_files, error?}]
    repos        JSONB NOT NULL DEFAULT '[]'::jsonb,
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
