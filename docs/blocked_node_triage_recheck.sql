\pset pager off
SELECT 'T1_blocked_count' AS k, count(*)::text AS v FROM campaign_nodes WHERE status='blocked';
WITH cls AS (
  SELECT n.id,
         CASE
           WHEN lower(regexp_replace(n.block_reason, '\s+', ' ', 'g')) LIKE 'infra x%'            THEN 'INFRA_RETRYABLE'
           WHEN lower(regexp_replace(n.block_reason, '\s+', ' ', 'g')) LIKE 'mission failed x%'   THEN 'SUBSTANTIVE_FAIL'
           WHEN lower(regexp_replace(n.block_reason, '\s+', ' ', 'g')) LIKE 'mission stopped x%'  THEN 'STOPPED'
           WHEN lower(regexp_replace(n.block_reason, '\s+', ' ', 'g')) LIKE 'hollow success x%'   THEN 'HOLLOW'
           ELSE 'MANUAL_GATE'
         END AS cls
    FROM campaign_nodes n WHERE n.status='blocked')
SELECT 'T2_class_' || cls AS k, count(*)::text AS v FROM cls GROUP BY cls
UNION ALL SELECT 'T2_class_sum', count(*)::text FROM cls
UNION ALL SELECT 'T3_fingerprint_md5', md5(string_agg(id || ':' || cls, ',' ORDER BY id)) FROM cls
UNION ALL SELECT 'T4_revivals_gt0', count(*)::text FROM campaign_nodes WHERE status='blocked'
     AND coalesce((metadata->>'infra_revivals')::int,0) > 0
UNION ALL SELECT 'T5_block_events_total', count(*)::text FROM campaign_events
     WHERE event_type IN ('block_detected','block_resolved')
UNION ALL SELECT 'T6_outflow_30d', count(*)::text FROM campaign_events
     WHERE event_type='node_status_change' AND detail->>'from'='blocked'
       AND created_at > now() - interval '30 days'
UNION ALL SELECT 'T7_blocked_created_7d', count(*)::text FROM campaign_nodes
     WHERE status='blocked' AND created_at > now() - interval '7 days'
UNION ALL SELECT 'T8_blocked_updated_7d', count(*)::text FROM campaign_nodes
     WHERE status='blocked' AND updated_at > now() - interval '7 days'
UNION ALL SELECT 'T9_now_utc', (now() AT TIME ZONE 'UTC')::text
ORDER BY 1;
SELECT 'T10_blocked_after' AS k, count(*)::text AS v FROM campaign_nodes WHERE status='blocked';
