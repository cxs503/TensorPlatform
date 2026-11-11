-- KPI-SUCCESS-90 frozen snapshot export (read-only).
--
-- Usage (audited, no writes):
--   psql -U foundation -d tensorplatform -At -f tools/failure_attribution/export.sql \
--        > /tmp/kpi_success_90_missions.csv
--   python3 -m tools.failure_attribution measure \
--        --missions-csv /tmp/kpi_success_90_missions.csv \
--        --outdir artifacts/kpi_success_90
--
-- The jsonb `result` is trimmed to the fields the attribution engine reads, so
-- the snapshot stays small enough to hash and re-measure but still carries the
-- primary evidence (declared stage, gate reason, prose tail).
COPY (
  SELECT
    id,
    status,
    created_at,
    finished_at,
    fail_stage,
    jsonb_strip_nulls(jsonb_build_object(
      'headline',         result->>'headline',
      'tool_calls',       result->>'tool_calls',
      'compactions',      result->>'compactions',
      'model',            result->>'model',
      'stop_condition',   result->>'stop_condition',
      'declared_summary', result->>'declared_summary',
      'traceback_tail',   left(result->>'traceback_tail', 1500),
      'git_status',       left(result->>'git_status', 600),
      'evidence', jsonb_build_object(
        'checks', jsonb_build_object(
          'fail_stage', result->'evidence'->'checks'->>'fail_stage',
          'reason',     left(result->'evidence'->'checks'->>'reason', 400)
        )
      )
    ))::text AS result
  FROM tensor_dev_missions
  WHERE status IN ('succeeded', 'failed', 'stopped', 'revoked')
) TO STDOUT WITH CSV HEADER;