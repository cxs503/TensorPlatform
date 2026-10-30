-- 095_ship_samples.sql
-- Ship Sample 表：存储船舶计算样本（批量扫描/基准验证/手动提交）

CREATE TABLE IF NOT EXISTS ship_samples (
    id SERIAL PRIMARY KEY,
    calc_type VARCHAR(32) NOT NULL,
    params JSON,
    result JSON,
    error TEXT,
    error_pct FLOAT,
    passed BOOLEAN,
    source VARCHAR(64),
    tags JSON,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_ship_samples_calc_type ON ship_samples(calc_type);
CREATE INDEX IF NOT EXISTS ix_ship_samples_calc_type_created ON ship_samples(calc_type, created_at);
