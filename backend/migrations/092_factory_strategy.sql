CREATE TABLE IF NOT EXISTS factory_strategy_campaigns (
 id BIGSERIAL PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 agent_session_id UUID NOT NULL REFERENCES factory_resident_workers(agent_session_id),
 name TEXT NOT NULL,
 objective TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 phase TEXT NOT NULL DEFAULT 'research',
 round INTEGER NOT NULL DEFAULT 1,
 max_rounds INTEGER NOT NULL DEFAULT 4 CHECK(max_rounds BETWEEN 1 AND 4),
 next_review_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_mission_id BIGINT REFERENCES tensor_dev_missions(id),
 plan JSONB NOT NULL DEFAULT '{}'::jsonb,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(organization_id,name)
);
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS campaign_id BIGINT REFERENCES factory_strategy_campaigns(id);
ALTER TABLE tensor_dev_missions ADD COLUMN IF NOT EXISTS campaign_phase TEXT;
ALTER TABLE factory_strategy_campaigns ADD COLUMN IF NOT EXISTS feedback JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE TABLE IF NOT EXISTS factory_harness_incidents (
 id BIGSERIAL PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 campaign_id BIGINT NOT NULL REFERENCES factory_strategy_campaigns(id),
 mission_id BIGINT REFERENCES tensor_dev_missions(id),
 kind TEXT NOT NULL,
 detail TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(campaign_id,mission_id,kind)
);
