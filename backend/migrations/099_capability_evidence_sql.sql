-- 099 capability_evidence_sql: 能力台账证据链 SQL 溯源表
-- 目标：每个能力等级的判定可 SQL 溯源到具体测试/基准/误差值
-- 设计：零领域语义（只认能力 ID/指标/数值/阈值，不含任何域词条）

BEGIN;

-- ── 能力注册表 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS capability_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id   TEXT NOT NULL UNIQUE,       -- 能力唯一标识（如 calc.resistance）
    description     TEXT,                        -- 能力描述
    domain          TEXT,                        -- 所属领域（通用标签，非硬编码域词）
    claimed_level   TEXT DEFAULT 'WITHHELD',     -- 自述等级
    expected_level  TEXT DEFAULT 'WITHHELD',     -- 预期等级
    registered_at   TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    metadata        TEXT DEFAULT '{}'            -- JSON 附加信息
);

-- ── 证据条目表 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evidence_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_uuid   TEXT NOT NULL UNIQUE,        -- 证据唯一 UUID
    capability_id   TEXT NOT NULL,               -- 关联能力
    source          TEXT NOT NULL,               -- 来源类型: pytest/benchmark/report
    source_path     TEXT NOT NULL,               -- 源文件路径
    metric_name     TEXT NOT NULL,               -- 指标名
    measured_value  REAL NOT NULL,               -- 测量值
    reference_value REAL NOT NULL,               -- 参考值
    error_pct       REAL NOT NULL,               -- 误差百分比
    passed          INTEGER NOT NULL,            -- 是否通过 (0/1)
    threshold_pct   REAL NOT NULL,               -- 使用的阈值
    run_id          TEXT,                        -- 运行批次 ID
    collected_at    TEXT DEFAULT (datetime('now')),
    extra           TEXT DEFAULT '{}',           -- JSON 附加信息
    FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_capability ON evidence_items(capability_id);
CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence_items(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence_items(source);

-- ── 证据包表（聚合多条证据） ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evidence_bundles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_uuid     TEXT NOT NULL UNIQUE,
    capability_id   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    total_items     INTEGER NOT NULL DEFAULT 0,
    pass_count      INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    max_error_pct   REAL NOT NULL DEFAULT 0.0,
    avg_error_pct   REAL NOT NULL DEFAULT 0.0,
    source_summary  TEXT DEFAULT '{}',           -- JSON: {"pytest": 2, "benchmark": 1}
    collected_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
);

CREATE INDEX IF NOT EXISTS idx_bundle_capability ON evidence_bundles(capability_id);
CREATE INDEX IF NOT EXISTS idx_bundle_run ON evidence_bundles(run_id);

-- ── 裁定结果表 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adjudication_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    adjudication_uuid TEXT NOT NULL UNIQUE,
    capability_id   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    evidence_grade  TEXT NOT NULL,               -- REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED
    confidence      REAL NOT NULL,               -- 置信度 [0, 1]
    reason          TEXT NOT NULL,               -- 裁定理由
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    pass_count      INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    thresholds_used TEXT NOT NULL DEFAULT '{}',  -- JSON: {"default_error_pct": 15.0}
    adjudicated_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
);

CREATE INDEX IF NOT EXISTS idx_adjudication_capability ON adjudication_results(capability_id);
CREATE INDEX IF NOT EXISTS idx_adjudication_run ON adjudication_results(run_id);
CREATE INDEX IF NOT EXISTS idx_adjudication_grade ON adjudication_results(evidence_grade);

-- ── 台账变更历史表 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ledger_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    change_uuid     TEXT NOT NULL UNIQUE,
    capability_id   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    old_level       TEXT NOT NULL,
    new_level       TEXT NOT NULL,
    evidence_grade  TEXT NOT NULL,
    action          TEXT NOT NULL,               -- upgrade/downgrade/no_change/overclaim_alert
    reason          TEXT NOT NULL,
    rollback_point  TEXT,                        -- 回滚点标识（ledger hash）
    prev_hash       TEXT,                        -- 前一条哈希（链式校验）
    entry_hash      TEXT NOT NULL,               -- 本条哈希
    changed_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
);

CREATE INDEX IF NOT EXISTS idx_ledger_capability ON ledger_changes(capability_id);
CREATE INDEX IF NOT EXISTS idx_ledger_run ON ledger_changes(run_id);

-- ── 溯源查询视图 ────────────────────────────────────────────────────
-- 视图：每个能力最新裁定 + 证据明细（一条 SQL 即可溯源）
CREATE VIEW IF NOT EXISTS v_capability_trace AS
SELECT
    cr.capability_id,
    cr.claimed_level,
    ar.evidence_grade,
    ar.confidence,
    ar.reason AS adjudication_reason,
    ar.run_id,
    ar.adjudicated_at,
    eb.total_items,
    eb.pass_count,
    eb.fail_count,
    eb.max_error_pct,
    eb.avg_error_pct,
    lc.old_level,
    lc.new_level,
    lc.action,
    lc.changed_at
FROM capability_registry cr
LEFT JOIN adjudication_results ar ON cr.capability_id = ar.capability_id
    AND ar.id = (SELECT MAX(id) FROM adjudication_results WHERE capability_id = cr.capability_id)
LEFT JOIN evidence_bundles eb ON cr.capability_id = eb.capability_id
    AND eb.run_id = ar.run_id
LEFT JOIN ledger_changes lc ON cr.capability_id = lc.capability_id
    AND lc.id = (SELECT MAX(id) FROM ledger_changes WHERE capability_id = cr.capability_id);

-- 视图：证据明细溯源（按能力 ID 查询所有证据条目）
CREATE VIEW IF NOT EXISTS v_evidence_detail AS
SELECT
    ei.capability_id,
    ei.evidence_uuid,
    ei.source,
    ei.source_path,
    ei.metric_name,
    ei.measured_value,
    ei.reference_value,
    ei.error_pct,
    ei.passed,
    ei.threshold_pct,
    ei.run_id,
    ei.collected_at,
    ar.evidence_grade,
    ar.adjudicated_at
FROM evidence_items ei
LEFT JOIN evidence_bundles eb ON ei.capability_id = eb.capability_id AND ei.run_id = eb.run_id
LEFT JOIN adjudication_results ar ON ei.capability_id = ar.capability_id AND ei.run_id = ar.run_id;

COMMIT;
