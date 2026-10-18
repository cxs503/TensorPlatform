-- 089: mission tree for cross-project orchestration — an orchestrator
-- mission dispatches specialist sub-missions (agent account stays VIEWER;
-- the dispatch endpoint enforces the envelope server-side).
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS parent_mission_id INT
    REFERENCES tensor_dev_missions(id) ON DELETE SET NULL;
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS co_repos TEXT[];

-- children lookups per parent + queued routing already covered by
-- ix_tensor_missions_queued_runner (model pinning scans the same partial
-- index); the tree edge gets its own partial index:
CREATE INDEX IF NOT EXISTS ix_tensor_missions_parent
    ON tensor_dev_missions (parent_mission_id) WHERE parent_mission_id IS NOT NULL;
