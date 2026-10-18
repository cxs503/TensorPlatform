-- 090: research progress reports — published per completed mission/stage.
-- Markdown body plus figure gallery: PNG bytes live on disk under
-- TENSOR_REPORT_FIGURE_DIR/<report_id>/<name>.png, metadata in figures JSONB
-- ([{name, caption}]). List view omits the markdown body (summary only).
CREATE TABLE IF NOT EXISTS tensor_reports (
    id SERIAL PRIMARY KEY,
    organization_id INT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    scenario_key TEXT,
    mission_id INT,
    stage TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    markdown TEXT NOT NULL,
    figures JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'published',
    created_by INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tensor_reports_org_created
    ON tensor_reports (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_tensor_reports_scenario
    ON tensor_reports (scenario_key) WHERE scenario_key IS NOT NULL;
