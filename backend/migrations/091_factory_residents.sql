-- Durable agent identity. Task tensor_agent_sessions remain separate audit traces.
CREATE TABLE IF NOT EXISTS factory_resident_workers (
 agent_session_id UUID PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(id),
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 worker_id TEXT NOT NULL UNIQUE,
 role TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 state TEXT NOT NULL DEFAULT 'offline',
 model TEXT NOT NULL,
 model_history JSONB NOT NULL DEFAULT '[]'::jsonb,
 memory JSONB NOT NULL DEFAULT '[]'::jsonb,
 heartbeat_at TIMESTAMPTZ,
 current_mission_id BIGINT REFERENCES tensor_dev_missions(id),
 last_error TEXT,
 started_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS worker_role TEXT;
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS task_key TEXT;
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS agent_session_id UUID REFERENCES factory_resident_workers(agent_session_id);
CREATE UNIQUE INDEX IF NOT EXISTS factory_mission_task_key
 ON tensor_dev_missions(organization_id, task_key) WHERE task_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS factory_session_one_active_mission
 ON tensor_dev_missions(agent_session_id) WHERE agent_session_id IS NOT NULL AND status IN ('claimed','running');
