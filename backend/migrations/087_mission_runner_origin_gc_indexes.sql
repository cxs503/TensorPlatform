-- 087: mission runner/origin (native harness A/B + agent self-proposed tasks)
-- plus the index gaps found in the 2026-10-18 console audit.

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS runner VARCHAR(16) NOT NULL DEFAULT 'openhands'
    CHECK (runner IN ('openhands', 'native'));
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'owner'
    CHECK (origin IN ('owner', 'agent'));

-- claim filters by runner among queued missions (two daemons, disjoint sets)
CREATE INDEX IF NOT EXISTS ix_tensor_missions_queued_runner
    ON tensor_dev_missions (runner, id) WHERE status = 'queued';

-- audit: sessions were queried by org and execution with only a PK
CREATE INDEX IF NOT EXISTS ix_tensor_agent_sessions_org
    ON tensor_agent_sessions (organization_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_tensor_agent_sessions_execution
    ON tensor_agent_sessions (execution_id) WHERE execution_id IS NOT NULL;

-- approvals: mission-scoped lookups and org+status counts were seq scans
CREATE INDEX IF NOT EXISTS ix_tensor_approvals_org_status
    ON tensor_approval_requests (organization_id, status);
CREATE INDEX IF NOT EXISTS ix_tensor_approvals_mission
    ON tensor_approval_requests (mission_id) WHERE mission_id IS NOT NULL;

-- data fix: sessions of already-terminal missions that leaked 'active'
-- (early stop paths predated the closed_at sweep — audit 2026-10-18 found 7)
UPDATE tensor_agent_sessions s
SET status = CASE m.status WHEN 'succeeded' THEN 'completed' ELSE m.status END,
    closed_at = now()
FROM tensor_dev_missions m
WHERE m.session_id = s.id
  AND m.status IN ('succeeded', 'failed', 'stopped')
  AND s.status = 'active' AND s.closed_at IS NULL;
