-- 086: dev missions — autonomous agent development tasks with human acceptance.
-- Agent claims a mission, runs an LLM tool loop on the host daemon, streams
-- tool_call/tool_result events into the session audit trail, and stops at
-- an ADMIN-only dev_acceptance approval.

CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    goal            TEXT NOT NULL,
    repo            VARCHAR(120) NOT NULL,
    base_ref        VARCHAR(120) NOT NULL DEFAULT 'main',
    budget          JSONB NOT NULL
                    DEFAULT '{"max_tool_calls":80,"max_wall_seconds":7200,"max_tokens":3000000}'::jsonb,
    status          VARCHAR(24) NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','claimed','running','awaiting_acceptance',
                                      'succeeded','failed','stopped')),
    claim_token     UUID,
    claimed_by      VARCHAR(120),
    session_id      BIGINT REFERENCES tensor_agent_sessions(id) ON DELETE SET NULL,
    result          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by      BIGINT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at    TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_tensor_missions_queued
    ON tensor_dev_missions (id) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS ix_tensor_missions_org
    ON tensor_dev_missions (organization_id, created_at DESC);

-- widen event kinds for the agent tool loop (transcript audit plane)
ALTER TABLE tensor_agent_session_events
    DROP CONSTRAINT IF EXISTS tensor_agent_session_events_kind_check;
ALTER TABLE tensor_agent_session_events
    ADD CONSTRAINT tensor_agent_session_events_kind_check
    CHECK (kind IN ('plan','execution_started','run_registered','validation_seen',
                    'decision','approval_requested','approval_decided','note','error',
                    'tool_call','tool_result','web_search','code_commit'));

-- widen approval kinds for mission acceptance; mission rows carry no scenario
ALTER TABLE tensor_approval_requests
    DROP CONSTRAINT IF EXISTS tensor_approval_requests_kind_check;
ALTER TABLE tensor_approval_requests
    ADD CONSTRAINT tensor_approval_requests_kind_check
    CHECK (kind IN ('stage_promotion','stop_condition','dev_acceptance'));
ALTER TABLE tensor_approval_requests
    ALTER COLUMN scenario_id DROP NOT NULL;
ALTER TABLE tensor_approval_requests
    ADD COLUMN IF NOT EXISTS mission_id BIGINT REFERENCES tensor_dev_missions(id) ON DELETE SET NULL;
