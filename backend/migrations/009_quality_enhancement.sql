-- 数据质量模块数据库迁移脚本
-- 创建增强的数据质量表

-- 质量规则表（增强版）
CREATE TABLE IF NOT EXISTS quality_rules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    rule_type VARCHAR(50) NOT NULL,  -- completeness, uniqueness, range, pattern, custom
    field VARCHAR(100),              -- 目标字段
    condition TEXT,                  -- 条件表达式
    threshold JSONB,                 -- 阈值配置
    severity VARCHAR(20) DEFAULT 'warning',  -- critical, warning, info
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER
);

-- 质量检查结果表
CREATE TABLE IF NOT EXISTS quality_check_results (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES quality_rules(id),
    dataset_id INTEGER NOT NULL,
    passed_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    score INTEGER,                   -- 质量得分 0-100
    details JSONB,                   -- 详细结果
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    checked_by INTEGER
);

-- 质量异常记录表
CREATE TABLE IF NOT EXISTS quality_anomalies (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL,
    rule_id INTEGER REFERENCES quality_rules(id),
    field VARCHAR(100),
    anomaly_type VARCHAR(50),        -- null, duplicate, out_of_range, pattern_mismatch
    expected_value TEXT,
    actual_value TEXT,
    record_ids JSONB,                -- 异常记录ID列表
    record_count INTEGER DEFAULT 1,
    severity VARCHAR(20) DEFAULT 'warning',  -- critical, warning, info
    status VARCHAR(20) DEFAULT 'open',        -- open, resolved, ignored
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolved_by INTEGER,
    resolution_note TEXT
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_quality_rules_type ON quality_rules(rule_type);
CREATE INDEX IF NOT EXISTS idx_quality_rules_active ON quality_rules(is_active);
CREATE INDEX IF NOT EXISTS idx_quality_check_results_rule ON quality_check_results(rule_id);
CREATE INDEX IF NOT EXISTS idx_quality_check_results_dataset ON quality_check_results(dataset_id);
CREATE INDEX IF NOT EXISTS idx_quality_check_results_time ON quality_check_results(checked_at);
CREATE INDEX IF NOT EXISTS idx_quality_anomalies_dataset ON quality_anomalies(dataset_id);
CREATE INDEX IF NOT EXISTS idx_quality_anomalies_status ON quality_anomalies(status);
CREATE INDEX IF NOT EXISTS idx_quality_anomalies_severity ON quality_anomalies(severity);

-- 插入默认规则示例
INSERT INTO quality_rules (name, description, rule_type, field, threshold, severity) VALUES
('数据完整性检查', '检查关键字段是否有空值', 'completeness', '*', '{"min": 0.95}', 'critical'),
('主键唯一性', '检查主键字段是否唯一', 'uniqueness', 'id', '{"max_rate": 0}', 'critical'),
('数值范围检查', '检查数值是否在合理范围内', 'range', 'value', '{"min": 0, "max": 1000000}', 'warning'),
('邮箱格式检查', '检查邮箱字段格式是否正确', 'pattern', 'email', '{"pattern": "^[a-zA-Z0-9._%%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"}', 'warning')
ON CONFLICT DO NOTHING;
