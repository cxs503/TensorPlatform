-- Campaign Plans: ingest master plan phases, nodes, criteria, DAG edges
-- m612 mechanism: phases/dependencies/acceptance criteria into queryable table

CREATE TABLE IF NOT EXISTS campaign_plans (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    source_mission_id TEXT NOT NULL,          -- e.g. "m611"
    schema_version TEXT NOT NULL DEFAULT '2.0.0',
    title TEXT NOT NULL,
    created_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner TEXT,
    physics_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
    coupling_framework_requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    validation_standards JSONB NOT NULL DEFAULT '[]'::jsonb,
    statistics JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(organization_id, source_mission_id)
);

CREATE TABLE IF NOT EXISTS campaign_plan_milestones (
    id BIGSERIAL PRIMARY KEY,
    campaign_plan_id BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    milestone_id TEXT NOT NULL,               -- e.g. "M0", "M1"
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    gate_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_date TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    UNIQUE(campaign_plan_id, milestone_id)
);

CREATE TABLE IF NOT EXISTS campaign_plan_nodes (
    id BIGSERIAL PRIMARY KEY,
    campaign_plan_id BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,                    -- e.g. "F01", "I01"
    source TEXT NOT NULL,                     -- e.g. "m605", "m611"
    layer TEXT NOT NULL,                      -- e.g. "L1_foundation", "L2_validation", "L3_integration"
    title TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    depends_on JSONB NOT NULL DEFAULT '[]'::jsonb,
    milestone TEXT NOT NULL,                  -- e.g. "M0"
    budget_hours NUMERIC NOT NULL DEFAULT 0,
    risk TEXT NOT NULL DEFAULT 'medium',
    physics_domain TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    UNIQUE(campaign_plan_id, node_id)
);

CREATE TABLE IF NOT EXISTS campaign_plan_dag_edges (
    id BIGSERIAL PRIMARY KEY,
    campaign_plan_id BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    UNIQUE(campaign_plan_id, from_node, to_node)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_cpn_campaign ON campaign_plan_nodes(campaign_plan_id);
CREATE INDEX IF NOT EXISTS idx_cpn_milestone ON campaign_plan_nodes(campaign_plan_id, milestone);
CREATE INDEX IF NOT EXISTS idx_cpm_campaign ON campaign_plan_milestones(campaign_plan_id);
CREATE INDEX IF NOT EXISTS idx_cpde_campaign ON campaign_plan_dag_edges(campaign_plan_id);
