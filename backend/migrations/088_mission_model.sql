-- 088: per-mission LLM model — the multi-model bake-off queues the same
-- goal against each relay model, so the model belongs to the mission row
-- (NULL = daemon env default).
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS model VARCHAR(64);
