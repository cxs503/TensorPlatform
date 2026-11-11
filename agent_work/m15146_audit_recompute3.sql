-- m15146 独立复算（修正版 v2）：阶段切分，正确去重 (mission_id, repo, pr)
\pset border 2
SELECT (first_merged_at::date <= DATE '2026-11-07') AS pre_cut,
       count(*) AS merged_prs,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM outcome_evaluations e
           WHERE e.mission_id = m.mission_id
             AND e.reviewer_kind='independent' AND e.review_status='reviewed' AND e.status='confirmed'
       )) AS indep_passed
FROM (
    SELECT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr, min(l.created_at) AS first_merged_at
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
    GROUP BY 1,2,3
) m
GROUP BY 1 ORDER BY 1;

-- 复核时延（修正 interval 转换）
SELECT count(*) AS n,
       round(extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY (e.created_at::timestamptz - m.first_merged_at)) / 3600.0, 2) AS median_h,
       round(extract(epoch FROM percentile_cont(0.9) WITHIN GROUP (ORDER BY (e.created_at::timestamptz - m.first_merged_at)) / 3600.0, 2) AS p90_h
FROM (
    SELECT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr, min(l.created_at) AS first_merged_at
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
    GROUP BY 1,2,3
) m
JOIN outcome_evaluations e ON e.mission_id = m.mission_id
WHERE e.created_at::timestamptz >= m.first_merged_at;
