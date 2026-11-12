-- 123_agent_health_meter.sql
-- 席位健康度仪表（FEAT-AGENT-HARNESS，只计量不裁决）
--
-- 用途：按「席位」（agent 工作身份）落 7d 滚动健康快照，供
--       ① 病席位发现（RECUR_FAIL / LOW_SUCCESS / MEMORY_STARVED）
--       ② 记忆层活力计量（lessons_written / lessons_injected / memory_hits）
--       ③ 周报面（v_agent_health_weekly）
--       消费端：tools/agent/agent_health_meter.py（守护：日轮询 + flock 幂等）。
--
-- 纪律（与 owner 证据口径一致）：
-- 1. 只计量不裁决：本表只落数字 + 标记，不写判决、不写处置，处置归协调线。
-- 2. 零编造：归因不到的列如实置 0，并把「为什么是 0」写进 memory_attribution
--    （源表空 / 源不可达 / 无埋点位点），禁止用估算值填充。
-- 3. 可复算：source_sql 落当次快照实际执行的聚合 SQL 原文 + 参数 + 窗口边界，
--    任何人拿这一列能在同一库上重算同一行。
-- 4. 幂等：同一 (org, seat, snapshot_date, window_days) 唯一；重跑只覆盖，
--    不产生第二行（见 agent_health_meter.py 的 UPSERT）。
--
-- 席位标识口径（以真列为准，见 tools/agent/agent_health_meter.py 文档）：
--   tensor_dev_missions.claimed_by 是唯一在产数据里有值的席位标识列
--   （7d 窗口内 agent_session_id 全为 NULL）；claimed_by 与
--   factory_resident_workers.worker_id 同名可 join。

BEGIN;

CREATE TABLE IF NOT EXISTS tensor_agent_health (
    id                   BIGSERIAL PRIMARY KEY,
    organization_id      BIGINT       NOT NULL,
    seat_key             TEXT         NOT NULL,
    -- worker = 真实席位（claimed_by/worker_id 可归因）；unattributed = 窗口内
    -- 无席位标识的欠账桶（只计量，不参与病席位判定，避免假阳性）
    seat_kind            TEXT         NOT NULL DEFAULT 'worker'
                         CHECK (seat_kind IN ('worker', 'unattributed')),
    snapshot_date        DATE         NOT NULL,
    window_days          INTEGER      NOT NULL DEFAULT 7,
    -- 7d 滚动任务面
    missions_total       INTEGER      NOT NULL DEFAULT 0,
    missions_succeeded   INTEGER      NOT NULL DEFAULT 0,
    success_rate         NUMERIC(6,4),
    fail_stage_dist      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    recur_stage          TEXT,
    recur_stage_count    INTEGER      NOT NULL DEFAULT 0,
    forced_finish_count  INTEGER      NOT NULL DEFAULT 0,
    forced_finish_rate   NUMERIC(6,4),
    stage_attributed     INTEGER      NOT NULL DEFAULT 0,  -- 失败中带 fail_stage 的条数
    failures_total       INTEGER      NOT NULL DEFAULT 0,  -- 失败条数（含无 stage）
    -- 记忆面（归因不到如实置 0，原因见 memory_attribution）
    lessons_written      INTEGER      NOT NULL DEFAULT 0,
    lessons_injected     INTEGER      NOT NULL DEFAULT 0,
    memory_hits          INTEGER      NOT NULL DEFAULT 0,
    memory_lessons_stock INTEGER      NOT NULL DEFAULT 0,  -- DB 内 worker.memory 存量（无时间戳，非滚动）
    memory_key_missions  INTEGER      NOT NULL DEFAULT 0,  -- 7d 内声明 memory_key 的任务位点数
    memory_attribution   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    -- 健康分（数学定义见 docs/AGENT_HEALTH_METER.md，components 可复算）
    health_score         NUMERIC(6,2),
    score_components     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    -- 病席位标记（只标不计，[...] 为空表示未触发）
    markers              TEXT[]       NOT NULL DEFAULT '{}',
    markers_meta         JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- withheld/未验证标记及原因
    -- 可复算证据
    source_sql           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    computed_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_agent_health_seat_snapshot
        UNIQUE (organization_id, seat_key, snapshot_date, window_days)
);

CREATE INDEX IF NOT EXISTS ix_agent_health_snapshot
    ON tensor_agent_health (snapshot_date DESC, organization_id);
CREATE INDEX IF NOT EXISTS ix_agent_health_markers
    ON tensor_agent_health USING GIN (markers);
CREATE INDEX IF NOT EXISTS ix_agent_health_seat
    ON tensor_agent_health (seat_key, snapshot_date DESC);

COMMENT ON TABLE tensor_agent_health IS
    '席位健康度 7d 滚动快照（只计量不裁决）；健康分数学定义见 docs/AGENT_HEALTH_METER.md';
COMMENT ON COLUMN tensor_agent_health.markers IS
    '病席位标记：RECUR_FAIL(同 fail_stage>=3/7d) / LOW_SUCCESS(>=5单且成功率<地板) / MEMORY_STARVED(>=10单且记忆命中0)';

-- 席位最新面：每席位取最近一个快照日的行（看板 / 巡检用）
CREATE OR REPLACE VIEW v_agent_health_seat_latest AS
SELECT DISTINCT ON (h.organization_id, h.seat_key)
       h.organization_id,
       h.seat_key,
       h.seat_kind,
       h.snapshot_date,
       h.window_days,
       h.missions_total,
       h.missions_succeeded,
       h.success_rate,
       h.fail_stage_dist,
       h.recur_stage,
       h.recur_stage_count,
       h.forced_finish_count,
       h.forced_finish_rate,
       h.lessons_written,
       h.lessons_injected,
       h.memory_hits,
       h.memory_lessons_stock,
       h.memory_attribution,
       h.health_score,
       h.score_components,
       h.markers,
       h.markers_meta,
       cardinality(h.markers) AS marker_count,
       h.computed_at
FROM tensor_agent_health h
ORDER BY h.organization_id, h.seat_key, h.snapshot_date DESC, h.computed_at DESC;

COMMENT ON VIEW v_agent_health_seat_latest IS
    '每席位最近一次健康快照（DISTINCT ON 取最新 snapshot_date）；marker_count>0 即病席位候选';

-- 周报面：按周聚合（tensor_reports 周报 / 周会口径）
CREATE OR REPLACE VIEW v_agent_health_weekly AS
SELECT date_trunc('week', h.snapshot_date)::date AS week_start,
       h.organization_id,
       h.window_days,
       count(*)                                        AS seats,
       count(*) FILTER (WHERE 'RECUR_FAIL' = ANY (h.markers))     AS recur_fail_seats,
       count(*) FILTER (WHERE 'LOW_SUCCESS' = ANY (h.markers))    AS low_success_seats,
       count(*) FILTER (WHERE 'MEMORY_STARVED' = ANY (h.markers))  AS memory_starved_seats,
       count(*) FILTER (WHERE cardinality(h.markers) > 0)         AS any_marker_seats,
       round(avg(h.health_score), 2)                   AS avg_score,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY h.health_score)::numeric, 2) AS median_score,
       min(h.health_score)                             AS min_score,
       sum(h.missions_total)                           AS missions_total,
       sum(h.missions_succeeded)                       AS missions_succeeded,
       round(sum(h.missions_succeeded)::numeric / NULLIF(sum(h.missions_total), 0), 4) AS success_rate,
       sum(h.forced_finish_count)                      AS forced_finish_count,
       round(sum(h.forced_finish_count)::numeric / NULLIF(sum(h.missions_total), 0), 4) AS forced_finish_rate,
       sum(h.lessons_written)                          AS lessons_written,
       sum(h.lessons_injected)                         AS lessons_injected,
       sum(h.memory_hits)                              AS memory_hits
FROM tensor_agent_health h
WHERE h.seat_kind = 'worker'
GROUP BY 1, 2, 3;

COMMENT ON VIEW v_agent_health_weekly IS
    '席位健康周报面：按周聚合的标记人数/分数分布/任务面/记忆面（只含 seat_kind=worker）';

-- 后端只读账号可读
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'foundation') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON tensor_agent_health TO foundation';
        EXECUTE 'GRANT USAGE ON SEQUENCE tensor_agent_health_id_seq TO foundation';
        EXECUTE 'GRANT SELECT ON v_agent_health_seat_latest TO foundation';
        EXECUTE 'GRANT SELECT ON v_agent_health_weekly TO foundation';
    END IF;
END $$;

COMMIT;