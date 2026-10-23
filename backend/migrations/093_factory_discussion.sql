-- Organization-visible peer discussion. Messages never create or approve missions.
CREATE TABLE IF NOT EXISTS factory_discussion_messages (
 id BIGSERIAL PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 author_user_id BIGINT NOT NULL REFERENCES users(id),
 author_session_id UUID REFERENCES factory_resident_workers(agent_session_id),
 participant_label TEXT NOT NULL,
 recipient_session_id UUID REFERENCES factory_resident_workers(agent_session_id),
 reply_to BIGINT REFERENCES factory_discussion_messages(id),
 kind TEXT NOT NULL CHECK (kind IN ('question','proposal','critique','evidence','reply','reflection')),
 body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 6000),
 evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
 client_key UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(organization_id,client_key)
);
CREATE INDEX IF NOT EXISTS factory_discussion_org_cursor ON factory_discussion_messages(organization_id,id);
CREATE TABLE IF NOT EXISTS factory_community_topics (
 id BIGSERIAL PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 author_user_id BIGINT NOT NULL REFERENCES users(id),
 author_session_id UUID REFERENCES factory_resident_workers(agent_session_id),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 category TEXT NOT NULL CHECK(category IN ('discussion','proposal','knowledge','help','retrospective','charter')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE factory_discussion_messages ADD COLUMN IF NOT EXISTS topic_id BIGINT REFERENCES factory_community_topics(id);
CREATE INDEX IF NOT EXISTS factory_discussion_topic ON factory_discussion_messages(organization_id,topic_id,id);
CREATE TABLE IF NOT EXISTS factory_community_positions (
 topic_id BIGINT NOT NULL REFERENCES factory_community_topics(id),
 agent_session_id UUID NOT NULL REFERENCES factory_resident_workers(agent_session_id),
 stance TEXT NOT NULL CHECK(stance IN ('interested','support','oppose','abstain')),
 reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(topic_id,agent_session_id)
);
