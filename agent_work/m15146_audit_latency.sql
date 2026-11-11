-- m15146 独立复算（修正版 v4）：复核时延（口径=measure.py:77,256-263：min(decided_at) - merged_at）
\pset border 2
WITH m AS (
    SELECT l.mission_id, l.detail->>'repo' AS repo, (l.detail->>'pr')::int AS pr, min(l.created_at) AS merged_at
    FROM mission_lifecycle_log l
    WHERE l.action='pr_gate' AND l.detail->>'state'='merged' AND l.created_at >= now() - interval '30 days'
    GROUP BY 1,2,3
),
fr AS (
    SELECT mission_id, min(NULLIF(decided_at,'')::timestamptz) AS first_review_at
    FROM outcome_evaluations GROUP BY 1
)
SELECT count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (fr.first_review_at - m.merged_at)) AS median_iv,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY (fr.first_review_at - m.merged_at)) AS p90_iv
FROM m JOIN fr ON fr.mission_id = m.mission_id
WHERE fr.first_review_at IS NOT NULL AND fr.first_review_at >= m.merged_at;
