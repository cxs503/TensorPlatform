-- 108_campaign_edge_conditions.sql
-- Campaign DAG conditional branching & adaptive research loops
-- Adds condition JSONB to campaign_nodes and creates campaign_edge_conditions table

-- Add condition column to campaign_nodes
ALTER TABLE campaign_nodes ADD COLUMN IF NOT EXISTS condition JSONB;
ALTER TABLE campaign_nodes ADD COLUMN IF NOT EXISTS convergence_check JSONB;
ALTER TABLE campaign_nodes ADD COLUMN IF NOT EXISTS iteration_count INT NOT NULL DEFAULT 0;
ALTER TABLE campaign_nodes ADD COLUMN IF NOT EXISTS parent_node_id BIGINT REFERENCES campaign_nodes(id);

-- Edge conditions table for explicit conditional routing
CREATE TABLE IF NOT EXISTS campaign_edge_conditions (
    id              BIGSERIAL PRIMARY KEY,
    plan_id         BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    source_node_id  BIGINT NOT NULL REFERENCES campaign_nodes(id) ON DELETE CASCADE,
    condition_expr  JSONB NOT NULL,
    -- condition_expr format:
    -- {
    --   "type": "gate",
    --   "metric": "max_rel_err",
    --   "operator": "<",
    --   "threshold": 0.05,
    --   "on_true": <node_id>,
    --   "on_false": <node_id>
    -- }
    priority        INT NOT NULL DEFAULT 0,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(plan_id, source_node_id, priority)
);

CREATE INDEX IF NOT EXISTS ix_campaign_edge_conditions_plan
    ON campaign_edge_conditions (plan_id);
CREATE INDEX IF NOT EXISTS ix_campaign_edge_conditions_source
    ON campaign_edge_conditions (source_node_id);

-- Add routing_evaluated event type to campaign_events
-- (SQLite doesn't support ALTER CHECK, so we handle this in application logic)
