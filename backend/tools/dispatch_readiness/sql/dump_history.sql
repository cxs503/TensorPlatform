-- Read-only export of the dispatch history used by the P233W2-N08 backtest.
-- Every column is copied verbatim from the platform database; no derivation
-- happens in SQL so that the JSON artefact stays traceable to the source rows.
WITH node_rows AS (
    SELECT
        n.id,
        n.plan_id,
        COALESCE(n.metadata->>'ext_id', n.id::text) AS ext_id,
        n.node_type,
        n.status,
        RIGHT(COALESCE(n.block_reason, ''), 120)  AS block_reason,
        COALESCE(n.dependencies, '{}'::integer[]) AS dependencies,
        n.consecutive_fails,
        n.mission_id,
        n.created_at,
        n.actual_start,
        n.actual_end,
        n.updated_at,
        (n.metadata ? 'upstream_contract')         AS has_upstream_contract,
        (n.metadata ? 'input_contract')            AS has_input_contract,
        n.metadata->'input_contract'               AS input_contract
    FROM campaign_nodes n
    WHERE n.status IN ('done', 'blocked')
),
mission_rows AS (
    SELECT
        m.id,
        m.status,
        m.created_at,
        m.finished_at,
        COALESCE((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(m.result) k), '{}') AS result_keys
    FROM tensor_dev_missions m
    WHERE m.id IN (
        SELECT n.mission_id FROM campaign_nodes n
        WHERE n.status IN ('done', 'blocked') AND n.mission_id IS NOT NULL
        UNION
        SELECT u.mission_id FROM campaign_nodes u
        WHERE u.id IN (
            SELECT unnest(COALESCE(n2.dependencies, '{}'::integer[]))
            FROM campaign_nodes n2 WHERE n2.status IN ('done', 'blocked')
        )
    )
)
SELECT json_build_object(
    'nodes', COALESCE((SELECT json_agg(json_build_object(
        'id', nr.id,
        'plan_id', nr.plan_id,
        'key', nr.id::text,
        'ext_id', nr.ext_id,
        'node_type', nr.node_type,
        'status', nr.status,
        'block_reason', nr.block_reason,
        'dependencies', nr.dependencies,
        'consecutive_fails', nr.consecutive_fails,
        'mission_id', nr.mission_id,
        'created_at', nr.created_at,
        'actual_start', nr.actual_start,
        'actual_end', nr.actual_end,
        'updated_at', nr.updated_at,
        'has_upstream_contract', nr.has_upstream_contract,
        'has_input_contract', nr.has_input_contract,
        'input_contract', nr.input_contract
    ) ORDER BY nr.id) FROM node_rows nr), '[]'::json),
    'missions', COALESCE((SELECT json_agg(json_build_object(
        'id', mr.id,
        'status', mr.status,
        'created_at', mr.created_at,
        'finished_at', mr.finished_at,
        'result_keys', mr.result_keys
    ) ORDER BY mr.id) FROM mission_rows mr), '[]'::json)
)::text;