-- 计算调度计量账本（owner 2026-11-13 计算调度令）
-- 幂等：可重复执行（CREATE ... IF NOT EXISTS / OR REPLACE）
-- 一行 = 一次可审计的计算消耗：cpu_seconds × placement（local|cluster）× node

CREATE TABLE IF NOT EXISTS compute_ledger (
    id             bigserial PRIMARY KEY,
    source_ref     text        NOT NULL,          -- 幂等键：cj<id> / local-<uuid> / ab-<arm>-<n>
    mission_id     bigint,
    repo           text,
    cmd_kind       text,
    placement      text        NOT NULL CHECK (placement IN ('local', 'cluster')),
    node           text,
    priority_class text,
    priority_rank  integer,
    cores          integer,
    cpu_seconds    numeric(18, 3) NOT NULL CHECK (cpu_seconds >= 0),
    wall_seconds   numeric(18, 3),
    exit_code      integer,
    policy_version text,
    decision_reason text,
    started_at     timestamptz,
    finished_at    timestamptz,
    ingested_at    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS compute_ledger_source_ref_uq
    ON compute_ledger (source_ref);
CREATE INDEX IF NOT EXISTS compute_ledger_placement_time_idx
    ON compute_ledger (placement, coalesce(finished_at, started_at, ingested_at));
CREATE INDEX IF NOT EXISTS compute_ledger_mission_idx
    ON compute_ledger (mission_id);

-- 每单烧了多少、烧在哪（验收①的直查面）
CREATE OR REPLACE VIEW compute_ledger_mission_v AS
SELECT carbon.mission_id,
       count(*)                                        AS jobs,
       round(sum(carbon.cpu_seconds), 1)               AS cpu_seconds,
       round(sum(carbon.cpu_seconds) / 3600.0, 4)      AS cpu_hours,
       round(sum(carbon.cpu_seconds) FILTER (WHERE carbon.placement = 'local'), 1)   AS local_cpu_seconds,
       round(sum(carbon.cpu_seconds) FILTER (WHERE carbon.placement = 'cluster'), 1) AS cluster_cpu_seconds,
       round((sum(carbon.cpu_seconds) FILTER (WHERE carbon.placement = 'local')
              / nullif(sum(carbon.cpu_seconds), 0))::numeric, 4) AS local_share,
       array_agg(DISTINCT coalesce(carbon.node, '-'))  AS nodes
FROM compute_ledger carbon
GROUP BY carbon.mission_id;

-- 放置分布（验收③的直查面）
CREATE OR REPLACE VIEW compute_ledger_placement_v AS
SELECT placement,
       coalesce(node, '-')                        AS node,
       count(*)                                   AS jobs,
       round(sum(cpu_seconds), 1)                 AS cpu_seconds,
       round(sum(cpu_seconds) / 3600.0, 4)        AS cpu_hours
FROM compute_ledger
GROUP BY 1, 2;