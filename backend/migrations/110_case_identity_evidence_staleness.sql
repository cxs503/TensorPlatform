-- 110_case_identity_evidence_staleness.sql
-- 算例身份键 / 版本钉扎 / 证据失效检测 / 回滚 —— 机制层表结构
-- 本文件由 ops/taskplane/case_identity.py:generate_ddl('postgres') 生成，
-- 请勿手改；drift 由 tests/test_case_identity.py 的 DDL 一致性用例守护。
-- schema_version=1.0.0 dialect=postgres
CREATE TABLE IF NOT EXISTS case_registry (
    id BIGSERIAL NOT NULL,
    ext_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    case_key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    active_version TEXT,
    status TEXT NOT NULL DEFAULT 'registered',
    status_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (ext_id)
);
CREATE INDEX IF NOT EXISTS idx_case_registry_namespace ON case_registry (namespace);
CREATE TABLE IF NOT EXISTS case_version_pins (
    id BIGSERIAL NOT NULL,
    case_id BIGINT NOT NULL,
    version_label TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    git_ref TEXT NOT NULL DEFAULT '',
    dep_manifest TEXT NOT NULL DEFAULT '[]',
    dep_digest TEXT NOT NULL,
    pin_digest TEXT NOT NULL,
    pinned_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (case_id, version_label),
    UNIQUE (case_id, pin_digest),
    FOREIGN KEY (case_id) REFERENCES case_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_case_version_pins_git_sha ON case_version_pins (git_sha);
CREATE TABLE IF NOT EXISTS case_evidence (
    id BIGSERIAL NOT NULL,
    case_id BIGINT NOT NULL,
    pin_id BIGINT NOT NULL,
    node_ref TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'valid',
    status_reason TEXT NOT NULL DEFAULT '',
    stale_unit TEXT NOT NULL DEFAULT '',
    detected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (case_id, pin_id, node_ref, artifact_path),
    FOREIGN KEY (pin_id) REFERENCES case_version_pins(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_case_evidence_status ON case_evidence (status);
CREATE TABLE IF NOT EXISTS upstream_state (
    id BIGSERIAL NOT NULL,
    unit TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'code',
    version TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (unit)
);
CREATE TABLE IF NOT EXISTS stale_events (
    id BIGSERIAL NOT NULL,
    case_id BIGINT NOT NULL,
    pin_id BIGINT NOT NULL,
    evidence_id BIGINT NOT NULL,
    upstream_unit TEXT NOT NULL,
    old_fingerprint TEXT NOT NULL,
    new_fingerprint TEXT NOT NULL,
    upstream_changed_at TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    latency_hours DOUBLE PRECISION NOT NULL,
    sla_hours DOUBLE PRECISION NOT NULL DEFAULT 24.0,
    sla_met INTEGER NOT NULL,
    action TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (id),
    UNIQUE (evidence_id, new_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_stale_events_detected ON stale_events (detected_at);
CREATE TABLE IF NOT EXISTS recompute_tasks (
    id BIGSERIAL NOT NULL,
    case_id BIGINT NOT NULL,
    pin_id BIGINT NOT NULL,
    evidence_id BIGINT NOT NULL,
    task_key TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (task_key)
);
CREATE INDEX IF NOT EXISTS idx_recompute_tasks_status ON recompute_tasks (status);
