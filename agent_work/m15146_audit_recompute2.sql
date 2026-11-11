-- m15146 独立复算（续）：阶段切分（按日切，切点=2026-11-07，与 measure.py:313-315 同口径但独立书写）
\pset border 2
SELECT (l.created_at::date <= DATE '2026-11-07') AS pre_cut,
       count(*) AS merged_prs,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM outcome_evaluations e
           WHERE e.mission_id = l.mission_id
             AND e.reviewer_kind='independent' AND e.review_status='reviewed' AND e.status='confirmed'
       )) AS indep_passed
FROM (
    SELECT DISTINCT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr, l.created_at
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
) l
GROUP BY 1 ORDER BY 1;

-- pending 积压（独立写法）
SELECT count(*) AS pending_rows, count(DISTINCT mission_id) AS pending_missions
FROM outcome_evaluations
WHERE review_status='unreviewed' AND status='pending';

-- 复核时延（独立写法）：合并→首次复核记录
SELECT count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (e.created_at::timestamptz - m.merged_at) * 24), 2) AS median_h,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY (e.created_at::timestamptz - m.merged_at) * 24), 2) AS p90_h
FROM (
    SELECT DISTINCT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr, min(l.created_at) AS merged_at
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
    GROUP BY 1,2,3
) m
JOIN outcome_evaluations e ON e.mission_id = m.mission_id
WHERE e.created_at::timestamptz >= m.merged_at;

-- 分仓（独立写法，前 5 仓）
SELECT m.repo, count(*) AS merged_prs,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id=m.mission_id)) AS reviewed_any,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id=m.mission_id AND e.reviewer_kind='independent' AND e.review_status='reviewed' AND e.status='confirmed')) AS indep_passed
FROM (
    SELECT DISTINCT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
) m
GROUP BY 1 ORDER BY 2 DESC LIMIT 5;

-- 分母对账：TensorPlatform 仓，重叠窗口 11-07~11-11（pr-gate 侧）
SELECT count(*) AS tp_pr_gate_merged
FROM (
    SELECT DISTINCT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged'
      AND l.detail->>'repo'='TensorPlatform'
      AND l.created_at >= now() - interval '30 days'
) t;
