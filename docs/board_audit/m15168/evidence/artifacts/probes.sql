-- 报告
SELECT coalesce(qa_status,'<null>') AS st, count(*) AS n FROM tensor_reports GROUP BY 1 ORDER BY n DESC;

-- 知识
SELECT coalesce(status,'<null>') AS st, count(*) AS n FROM knowledge_base GROUP BY 1;

-- PR
SELECT coalesce(detail->>'state','<none>') AS st, coalesce(detail->>'note','<none>') AS note, count(*) AS n FROM mission_lifecycle_log WHERE action='pr_gate' GROUP BY 1,2 ORDER BY n DESC;
