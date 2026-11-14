-- 123_z_demand_intake_base.sql
-- Ensure the production PostgreSQL schema exists before migration 124 adds
-- audit columns. The CLI's runtime self-heal DDL remains useful for isolated
-- scratch schemas, but cannot be a prerequisite for numbered DB migrations.
BEGIN;

CREATE TABLE IF NOT EXISTS demand_intake_log (
    id BIGSERIAL PRIMARY KEY,
    cand_key TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT 'platform',
    confidence TEXT NOT NULL DEFAULT '',
    src_field TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    ledger_id INTEGER,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS demand_intake_log_status_idx
    ON demand_intake_log (status);
CREATE INDEX IF NOT EXISTS demand_intake_log_slug_idx
    ON demand_intake_log (slug);

CREATE TABLE IF NOT EXISTS demand_backlog (
    id BIGSERIAL PRIMARY KEY,
    source_kind TEXT NOT NULL DEFAULT 'tensor_report',
    source_ref TEXT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    item TEXT NOT NULL,
    item_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS demand_backlog_status_idx
    ON demand_backlog (status);

COMMIT;
