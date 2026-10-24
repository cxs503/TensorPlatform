-- 094: Tensor Soul Runtime v0.1（RFC #72 Phase 1 MVP）。
-- 复用既有 resident community / mission / event 机制；不另建任务引擎。
-- 与 091 的差别：091 的 factory_resident_workers 把一次注册（会话）当成身份，
-- 本迁移引入独立的 factory_agent_identities（跨 session、跨模型稳定），
-- factory_soul_sessions 只表示一次交互 episode。
-- 幂等：全部 IF NOT EXISTS / IF EXISTS 风格，版本与 sha256 由
-- services/soul_migrations.py 登记进 factory_soul_schema_versions。

CREATE TABLE IF NOT EXISTS factory_soul_schema_versions (
 version TEXT PRIMARY KEY,
 checksum TEXT NOT NULL CHECK (length(checksum) = 64),
 applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- AgentIdentity：稳定 ID、角色、目标/约束版本、模型无关的能力/权限引用。
-- 刻意不含任何 model/provider 列——模型不是身份键（契约不变量 1/6）。
CREATE TABLE IF NOT EXISTS factory_agent_identities (
 agent_id UUID PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 owner_user_id BIGINT NOT NULL REFERENCES users(id),
 worker_id TEXT NOT NULL,
 role TEXT NOT NULL,
 goal TEXT NOT NULL DEFAULT '',
 goal_revision INTEGER NOT NULL DEFAULT 1 CHECK (goal_revision >= 1),
 constraints JSONB NOT NULL DEFAULT '[]'::jsonb,
 constraint_revision INTEGER NOT NULL DEFAULT 1 CHECK (constraint_revision >= 1),
 policy_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
 capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
 -- 历史迁移入口：把 091 时代的会话身份挂到稳定身份上（可空，不做数据搬移）。
 legacy_session_id UUID REFERENCES factory_resident_workers(agent_session_id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(organization_id, worker_id)
);

-- ModelBinding：版本化配置；可切换，永不作为身份主键。
CREATE TABLE IF NOT EXISTS factory_soul_model_bindings (
 binding_id UUID PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
 provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 80),
 model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 120),
 adapter JSONB NOT NULL DEFAULT '{}'::jsonb,
 capability_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
 version INTEGER NOT NULL CHECK (version >= 1),
 created_by BIGINT REFERENCES users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(organization_id, name, version)
);

-- SessionState：一次交互/工作 episode；只引用 agent_id，不承载身份。
CREATE TABLE IF NOT EXISTS factory_soul_sessions (
 session_id UUID PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 agent_id UUID NOT NULL REFERENCES factory_agent_identities(agent_id),
 episode INTEGER NOT NULL CHECK (episode >= 1),
 status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
 current_task_id BIGINT REFERENCES tensor_dev_missions(id) ON DELETE SET NULL,
 task_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
 active_plan JSONB NOT NULL DEFAULT '{}'::jsonb,
 model_binding_id UUID REFERENCES factory_soul_model_bindings(binding_id),
 checkpoint_version INTEGER NOT NULL DEFAULT 0 CHECK (checkpoint_version >= 0),
 last_restored_version INTEGER NOT NULL DEFAULT 0 CHECK (last_restored_version >= 0),
 opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 closed_at TIMESTAMPTZ,
 UNIQUE(agent_id, episode)
);

-- MemoryEvent：append-only、带来源/敏感级/验证状态的事件历史。
-- sensitivity='shared' 必须已 validated：未验证内容不可能以共享知识落库。
CREATE TABLE IF NOT EXISTS factory_memory_events (
 event_id BIGSERIAL PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 agent_id UUID NOT NULL REFERENCES factory_agent_identities(agent_id),
 session_id UUID REFERENCES factory_soul_sessions(session_id) ON DELETE SET NULL,
 task_id BIGINT REFERENCES tensor_dev_missions(id) ON DELETE SET NULL,
 event_type TEXT NOT NULL CHECK (event_type IN
   ('observation','decision','outcome','feedback','lesson','fact')),
 source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 200),
 provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
 payload JSONB NOT NULL DEFAULT '{}'::jsonb,
 sensitivity TEXT NOT NULL DEFAULT 'private'
   CHECK (sensitivity IN ('private','organization','shared')),
 verification_status TEXT NOT NULL DEFAULT 'observed'
   CHECK (verification_status IN ('observed','validated','rejected','superseded')),
 validator JSONB,
 superseded_by BIGINT REFERENCES factory_memory_events(event_id),
 idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(organization_id, idempotency_key),
 CHECK (sensitivity <> 'shared' OR verification_status = 'validated'),
 CHECK (verification_status <> 'validated' OR validator IS NOT NULL),
 CHECK (event_type <> 'fact' OR verification_status IN ('validated','superseded','rejected'))
);
CREATE INDEX IF NOT EXISTS factory_memory_events_agent
 ON factory_memory_events(organization_id, agent_id, event_id DESC);
CREATE INDEX IF NOT EXISTS factory_memory_events_shared
 ON factory_memory_events(organization_id, event_id DESC)
 WHERE sensitivity IN ('organization','shared') AND verification_status = 'validated';

-- append-only：内容不可改，仅允许验证状态迁移（observed→validated/rejected/superseded）。
CREATE OR REPLACE FUNCTION factory_memory_events_guard() RETURNS trigger AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'factory_memory_events is append-only';
 END IF;
 IF (NEW.event_id, NEW.organization_id, NEW.agent_id, NEW.session_id, NEW.task_id,
     NEW.event_type, NEW.source, NEW.provenance, NEW.payload, NEW.sensitivity,
     NEW.idempotency_key, NEW.created_at)
    IS DISTINCT FROM
    (OLD.event_id, OLD.organization_id, OLD.agent_id, OLD.session_id, OLD.task_id,
     OLD.event_type, OLD.source, OLD.provenance, OLD.payload, OLD.sensitivity,
     OLD.idempotency_key, OLD.created_at) THEN
  RAISE EXCEPTION 'factory_memory_events content is append-only; only verification status may transition';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_factory_memory_events_guard ON factory_memory_events;
CREATE TRIGGER trg_factory_memory_events_guard
 BEFORE UPDATE OR DELETE ON factory_memory_events
 FOR EACH ROW EXECUTE FUNCTION factory_memory_events_guard();

-- Checkpoint：版本 + 校验和；(session_id, version) 唯一保证重复提交幂等。
CREATE TABLE IF NOT EXISTS factory_soul_checkpoints (
 session_id UUID NOT NULL REFERENCES factory_soul_sessions(session_id) ON DELETE CASCADE,
 agent_id UUID NOT NULL REFERENCES factory_agent_identities(agent_id),
 version INTEGER NOT NULL CHECK (version >= 1),
 checksum TEXT NOT NULL CHECK (length(checksum) = 64),
 state JSONB NOT NULL,
 state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version >= 1),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(session_id, version)
);
CREATE INDEX IF NOT EXISTS factory_soul_checkpoints_agent
 ON factory_soul_checkpoints(agent_id, created_at DESC);

-- 091 时代会话 → 稳定身份映射：同一 worker_id 的多次会话注册映射到同一个 agent_id。
CREATE TABLE IF NOT EXISTS factory_soul_legacy_sessions (
 agent_session_id UUID PRIMARY KEY REFERENCES factory_resident_workers(agent_session_id) ON DELETE CASCADE,
 agent_id UUID NOT NULL REFERENCES factory_agent_identities(agent_id),
 linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 模型绑定切换历史：只记"换过什么"，不改身份、不动会话状态。
CREATE TABLE IF NOT EXISTS factory_soul_binding_history (
 session_id UUID NOT NULL REFERENCES factory_soul_sessions(session_id) ON DELETE CASCADE,
 agent_id UUID NOT NULL REFERENCES factory_agent_identities(agent_id),
 binding_id UUID NOT NULL REFERENCES factory_soul_model_bindings(binding_id),
 switched_by BIGINT REFERENCES users(id),
 switched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(session_id, switched_at, binding_id)
);

-- LearningCandidate：个体经验 → 共享知识的晋级门。
-- 只有 validated 且已批准（promoted）的候选才能进共享知识；其余状态保留审计。
CREATE TABLE IF NOT EXISTS factory_learning_candidates (
 candidate_id UUID PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 agent_id UUID NOT NULL REFERENCES factory_agent_identities(agent_id),
 source_event_ids BIGINT[] NOT NULL CHECK (array_length(source_event_ids, 1) >= 1),
 proposed_generalization TEXT NOT NULL CHECK (length(proposed_generalization) BETWEEN 1 AND 4000),
 scope TEXT NOT NULL DEFAULT 'organization'
   CHECK (scope IN ('agent','organization','shared')),
 evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
 evaluation JSONB NOT NULL DEFAULT '{}'::jsonb,
 promotion_status TEXT NOT NULL DEFAULT 'proposed'
   CHECK (promotion_status IN ('proposed','approved','rejected','superseded')),
 decided_by BIGINT REFERENCES users(id),
 decided_reason TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 decided_at TIMESTAMPTZ,
 CHECK (promotion_status <> 'approved' OR (evaluation->>'passed')::boolean),
 CHECK (promotion_status <> 'approved' OR scope <> 'shared' OR evidence->>'validator' IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS factory_learning_candidates_org
 ON factory_learning_candidates(organization_id, promotion_status, created_at DESC);

-- EvidenceRecord：代码 SHA / 任务 / 环境 / 校验器 / 产物哈希与证据等级。
CREATE TABLE IF NOT EXISTS factory_evidence_records (
 evidence_id UUID PRIMARY KEY,
 organization_id BIGINT NOT NULL REFERENCES organizations(id),
 agent_id UUID REFERENCES factory_agent_identities(agent_id),
 session_id UUID REFERENCES factory_soul_sessions(session_id) ON DELETE SET NULL,
 task_id BIGINT REFERENCES tensor_dev_missions(id) ON DELETE SET NULL,
 category TEXT NOT NULL CHECK (category IN ('unit_test','integration_test','deployment','scientific')),
 code_sha TEXT CHECK (code_sha ~ '^[0-9a-f]{7,40}$'),
 environment JSONB NOT NULL DEFAULT '{}'::jsonb,
 validator JSONB NOT NULL DEFAULT '{}'::jsonb,
 metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
 artifact_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
 evidence_level TEXT NOT NULL DEFAULT 'L0' CHECK (evidence_level IN ('L0','L1','L2','L3','L4')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);