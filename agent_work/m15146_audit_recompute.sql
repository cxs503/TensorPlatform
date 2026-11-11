-- m15146 独立复算（审计员自写，非 m15116 脚本）：KPI-QC-RECHECK
-- 口径：分母=近30天 pr_gate merged 去重(mission_id,repo,pr)；分子=该 mission 存在 independent+reviewed+confirmed 复判
\pset border 2
SELECT count(*) AS merged_prs,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM outcome_evaluations e
           WHERE e.mission_id = m.mission_id
             AND e.reviewer_kind = 'independent'
             AND e.review_status = 'reviewed'
             AND e.status = 'confirmed')) AS indep_passed_prs,
       round(100.0 * count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM outcome_evaluations e
           WHERE e.mission_id = m.mission_id
             AND e.reviewer_kind = 'independent'
             AND e.review_status = 'reviewed'
             AND e.status = 'confirmed')) / count(*), 2) AS rate_pct
FROM (
    SELECT DISTINCT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr
    FROM mission_lifecycle_log l
    WHERE l.action = 'pr_gate'
      AND l.detail->>'state' = 'merged'
      AND l.created_at >= now() - interval '30 days'
) m;

-- 定义阶梯（独立写法）
SELECT
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id=m.mission_id)) AS d0_any,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id=m.mission_id AND e.review_status='reviewed' AND e.status='confirmed')) AS d1_any_confirmed,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id=m.mission_id AND e.reviewer_kind='independent' AND e.review_status='reviewed' AND e.status='confirmed')) AS d2_indep,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id=m.mission_id AND e.reviewer_kind='independent' AND e.review_status='reviewed' AND e.status='confirmed' AND e.cross_model_verified)) AS d3_indep_xmodel
FROM (
    SELECT DISTINCT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
) m;

-- 阶段切分：最后一次 independent 结论时刻
SELECT max(created_at) AS last_indep_conclusion
FROM outcome_evaluations
WHERE reviewer_kind='independent' AND review_status='reviewed' AND status='confirmed';

-- 阶段切分计数（切点=上一步结果）
SELECT (l.created_at <= ts::timestamptz) AS before_cut, count(DISTINCT (l.mission_id, l.detail->>'repo', (l.detail->>'pr')::int)) AS merged_prs
FROM mission_lifecycle_log l, (SELECT max(created_at) AS ts FROM outcome_evaluations WHERE reviewer_kind='independent' AND review_status='reviewed' AND status='confirmed') c
WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
GROUP BY 1;

-- pending 积压
SELECT count(*) AS pending_rows, count(DISTINCT mission_id) AS pending_missions
FROM outcome_evaluations
WHERE review_status='pending';

-- 任务粒度
SELECT count(DISTINCT m.mission_id) AS merged_missions,
       count(DISTINCT m.mission_id) FILTER (WHERE EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id=m.mission_id AND e.reviewer_kind='independent' AND e.review_status='reviewed' AND e.status='confirmed')) AS indep_passed_missions
FROM (
    SELECT DISTINCT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
) m;
