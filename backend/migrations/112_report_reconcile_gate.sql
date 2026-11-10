-- 112: 报告对账门（终态 ↔ 报告双向对账）落库面。
--
-- 背景：战役 DAG 的终态（done / blocked）此前没有任何「必须有报告」的机制门，
-- 补报靠人（守望线）事后喊话；反过来报告侧也没有归属校验，凭空/串号报告无人拦。
-- 本迁移只补两块**结构性**落库面，不改既有表语义：
--   tensor_report_supplement_queue  补报派单外箱（幂等键 = mission_id）
--   campaign_nodes(mission_id)      反向扫描索引（报告 → 节点）
--
-- 幂等：UNIQUE (organization_id, dispatch_key) 保证同一 mission 永远只有一张补报单，
-- 对账器二次运行零新增（J3）；派单完成（报告出现）只做 status pending→resolved
-- 的状态迁移，不删行（账本只增不改）。
--
-- 可移植性：本文件被单测当作建库脚本直接执行（SQLite），因此不用 SERIAL/AUTOINCREMENT/
-- JSONB，主键用确定性 sha256 文本（dispatch_id），Postgres 与 SQLite 均接受。

CREATE TABLE IF NOT EXISTS tensor_report_supplement_queue (
    dispatch_id     TEXT PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    dispatch_key    TEXT NOT NULL,
    dispatch_type   TEXT NOT NULL DEFAULT 'supplement_report',
    mission_id      BIGINT,
    node_id         BIGINT,
    plan_id         BIGINT,
    priority        TEXT NOT NULL DEFAULT 'normal',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    note            TEXT NOT NULL DEFAULT '',
    UNIQUE (organization_id, dispatch_key)
);

CREATE INDEX IF NOT EXISTS ix_report_supplement_status
    ON tensor_report_supplement_queue (organization_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_report_supplement_mission
    ON tensor_report_supplement_queue (mission_id);

-- 反向扫描（报告 mission_id → 战役节点）与正向扫描都走这条索引；
-- 无索引时全量 1382 终态节点 × 6979 报告会退化成 N×M 扫描。
CREATE INDEX IF NOT EXISTS ix_campaign_nodes_mission
    ON campaign_nodes (mission_id);

-- KPI 指标键：report_coverage_rate（tensor_guide_tracker 已在 095 建表，
-- 此处只补一个按指标键的趋势索引，供逐波趋势线读取）。
CREATE INDEX IF NOT EXISTS ix_guide_tracker_metric
    ON tensor_guide_tracker (organization_id, metric_key, snapshot_at DESC);