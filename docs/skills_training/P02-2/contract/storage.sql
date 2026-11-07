-- 认证导出 job 表（契约先行冻结件；SQLite 兼容，可直跑）
-- 纪律：只建新表，不 ALTER 任何既有表；实施时落为 backend/migrations/ 新编号文件。
CREATE TABLE IF NOT EXISTS certification_exports (
    id                 TEXT PRIMARY KEY,            -- uuid4，工件路径唯一来源（防路径穿越）
    certification_id   TEXT NOT NULL,              -- 认证记录 id（实施时加 FK）
    tenant_id          TEXT NOT NULL,
    format             TEXT NOT NULL CHECK (format IN ('json', 'zip')),
    include_evidence   INTEGER NOT NULL DEFAULT 1,
    include_provenance INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'expired')),
    artifact_path      TEXT,                        -- data/cert_exports/{tenant_id}/{cert_id}/{job_id}.{json|zip}
    sha256             TEXT,                        -- 与 .sha256 sidecar 一致
    size_bytes         INTEGER,
    error              TEXT,
    created_by         TEXT NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at        TEXT,
    expires_at         TEXT NOT NULL               -- created_at + 30d，清扫任务据此置 expired
);
CREATE INDEX IF NOT EXISTS idx_cert_exports_cert   ON certification_exports (certification_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cert_exports_expiry ON certification_exports (expires_at);
CREATE INDEX IF NOT EXISTS idx_cert_exports_tenant ON certification_exports (tenant_id, status);

-- 保留期清扫（伪 SQL，实施为周期任务）：
-- UPDATE certification_exports SET status='expired'
--  WHERE status='succeeded' AND expires_at < datetime('now');
-- 随后删除对应工件与 sidecar。
