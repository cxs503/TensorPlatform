CREATE TABLE IF NOT EXISTS llm_call_traces (
    id BIGSERIAL PRIMARY KEY,
    organization_id INTEGER NULL REFERENCES organizations(id) ON DELETE SET NULL,
    user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    username VARCHAR(128),
    provider VARCHAR(64) NOT NULL,
    model VARCHAR(255) NOT NULL,
    source VARCHAR(128) NOT NULL,
    operation VARCHAR(64) NOT NULL DEFAULT 'chat',
    status VARCHAR(32) NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    error_message VARCHAR(500),
    request_path VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_call_traces_org_created
    ON llm_call_traces (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_call_traces_source_created
    ON llm_call_traces (source, created_at DESC);

CREATE TABLE IF NOT EXISTS llm_prompt_templates (
    id BIGSERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    template TEXT NOT NULL,
    variables JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, name, version)
);

CREATE INDEX IF NOT EXISTS idx_llm_prompts_org_name
    ON llm_prompt_templates (organization_id, name, version DESC);

CREATE TABLE IF NOT EXISTS llm_rag_documents (
    id BIGSERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    source VARCHAR(255) NOT NULL DEFAULT 'manual',
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_rag_documents_org_created
    ON llm_rag_documents (organization_id, created_at DESC);
