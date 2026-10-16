-- 数据血缘模块数据库迁移
-- 创建血缘节点表、关系表、图谱表和变更日志表

-- 血缘节点表
CREATE TABLE IF NOT EXISTS lineage_nodes (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    display_name VARCHAR(255),
    node_type VARCHAR(50) NOT NULL,
    system VARCHAR(100),
    database VARCHAR(100),
    schema_name VARCHAR(100),
    full_path VARCHAR(500),
    description TEXT,
    extra_metadata JSONB DEFAULT '{}',
    tags JSONB DEFAULT '[]',
    owner VARCHAR(100),
    quality_score INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100)
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_lineage_nodes_type ON lineage_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_lineage_nodes_system ON lineage_nodes(system);
CREATE INDEX IF NOT EXISTS idx_lineage_nodes_name ON lineage_nodes(name);
CREATE INDEX IF NOT EXISTS idx_lineage_nodes_database ON lineage_nodes(database);
CREATE INDEX IF NOT EXISTS idx_lineage_nodes_active ON lineage_nodes(is_active);

-- 血缘关系表
CREATE TABLE IF NOT EXISTS lineage_edges (
    id VARCHAR(36) PRIMARY KEY,
    source_id VARCHAR(36) NOT NULL REFERENCES lineage_nodes(id) ON DELETE CASCADE,
    target_id VARCHAR(36) NOT NULL REFERENCES lineage_nodes(id) ON DELETE CASCADE,
    edge_type VARCHAR(50) DEFAULT 'derived',
    transformation TEXT,
    transformation_type VARCHAR(50) DEFAULT 'sql',
    column_mapping JSONB DEFAULT '[]',
    impact_weight INTEGER DEFAULT 1,
    description TEXT,
    extra_metadata JSONB DEFAULT '{}',
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100)
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_lineage_edges_source ON lineage_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_lineage_edges_target ON lineage_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_lineage_edges_type ON lineage_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_lineage_edges_active ON lineage_edges(is_active);

-- 血缘图谱表
CREATE TABLE IF NOT EXISTS lineage_graphs (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    graph_data JSONB DEFAULT '{}',
    version VARCHAR(50) DEFAULT '1.0.0',
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100)
);

-- 血缘变更日志表
CREATE TABLE IF NOT EXISTS lineage_change_logs (
    id VARCHAR(36) PRIMARY KEY,
    node_id VARCHAR(36),
    edge_id VARCHAR(36),
    change_type VARCHAR(50) NOT NULL,
    change_detail JSONB DEFAULT '{}',
    old_value JSONB,
    new_value JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100)
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_lineage_logs_node ON lineage_change_logs(node_id);
CREATE INDEX IF NOT EXISTS idx_lineage_logs_edge ON lineage_change_logs(edge_id);
CREATE INDEX IF NOT EXISTS idx_lineage_logs_type ON lineage_change_logs(change_type);
CREATE INDEX IF NOT EXISTS idx_lineage_logs_created ON lineage_change_logs(created_at);

-- 插入示例数据（2026-10-16 架构收敛：演示节点改为领域中立示例）
INSERT INTO lineage_nodes (id, name, display_name, node_type, system, database, schema_name, full_path, description, extra_metadata, tags, owner, created_by)
VALUES
    ('ln-source-001', 'raw_sensor_data', '原始传感器数据', 'source', 'IoT Gateway', 'sensors', 'public', 'IoT Gateway.sensors.public.raw_sensor_data', '来自物联网网关的原始传感器数据', '{"frequency": "1min", "format": "JSON"}', '["sensor", "iot", "realtime"]', 'DataTeam', 'system'),
    ('ln-table-001', 'sensor_readings', '传感器读数表', 'table', 'PostgreSQL', 'analytics_db', 'public', 'PostgreSQL.analytics_db.public.sensor_readings', '清洗后的传感器读数数据表', '{"row_count": 1000000, "columns": 15}', '["analytics", "cleaned"]', 'DataTeam', 'system'),
    ('ln-table-002', 'aggregated_readings', '聚合读数表', 'table', 'PostgreSQL', 'analytics_db', 'public', 'PostgreSQL.analytics_db.public.aggregated_readings', '按设备汇总的传感器读数宽表', '{"row_count": 500000, "columns": 12}', '["aggregated", "features"]', 'DataTeam', 'system'),
    ('ln-table-003', 'anomaly_flags', '异常标记结果表', 'table', 'PostgreSQL', 'ml_results', 'public', 'PostgreSQL.ml_results.public.anomaly_flags', '异常检测模型输出结果', '{"row_count": 100000, "columns": 8}', '["ml", "prediction"]', 'MLTeam', 'system'),
    ('ln-model-001', 'anomaly_detection_model', '异常检测模型', 'model', 'MLFlow', 'models', 'production', 'MLFlow.models.production.anomaly_detection_model', '基于时序特征的设备异常检测模型', '{"algorithm": "IsolationForest", "accuracy": 0.95}', '["timeseries", "production"]', 'MLTeam', 'system'),
    ('ln-report-001', 'daily_summary_report', '每日汇总报表', 'report', 'BI Platform', 'reports', 'summary', 'BI Platform.reports.summary.daily_summary_report', '每日数据汇总分析报表', '{"refresh": "daily", "format": "PDF"}', '["daily", "summary"]', 'BizTeam', 'system'),
    ('ln-pipeline-001', 'etl_sensor_to_analytics', '传感器数据ETL管道', 'pipeline', 'Airflow', 'pipelines', 'etl', 'Airflow.pipelines.etl.etl_sensor_to_analytics', '从IoT网关到分析库的ETL管道', '{"schedule": "*/5 * * * *", "dag_id": "sensor_etl"}', '["etl", "scheduled"]', 'DataEng', 'system')
ON CONFLICT (id) DO NOTHING;

-- 插入示例关系数据
INSERT INTO lineage_edges (id, source_id, target_id, edge_type, transformation, transformation_type, description, created_by)
VALUES
    ('le-001', 'ln-source-001', 'ln-table-001', 'transform', 'INSERT INTO sensor_readings SELECT * FROM raw_sensor_data WHERE quality_score > 0.8', 'sql', '数据清洗和过滤低质量数据', 'system'),
    ('le-002', 'ln-table-001', 'ln-table-002', 'aggregate', 'INSERT INTO aggregated_readings SELECT device_id, AVG(power), AVG(voltage) FROM sensor_readings GROUP BY device_id', 'sql', '按设备汇总读数', 'system'),
    ('le-003', 'ln-table-001', 'ln-model-001', 'derived', 'Training data for anomaly detection model', 'python', '模型训练数据源', 'system'),
    ('le-004', 'ln-model-001', 'ln-table-003', 'transform', 'Model inference results stored to flag table', 'python', '模型推理结果存储', 'system'),
    ('le-005', 'ln-table-002', 'ln-report-001', 'derived', 'Daily summary report generated from aggregated data', 'sql', '报表数据来源', 'system'),
    ('le-006', 'ln-source-001', 'ln-pipeline-001', 'source', 'Raw sensor data input to ETL pipeline', 'sql', 'ETL管道数据源', 'system'),
    ('le-007', 'ln-pipeline-001', 'ln-table-001', 'target', 'ETL pipeline output to analytics table', 'sql', 'ETL管道输出', 'system')
ON CONFLICT (id) DO NOTHING;

-- 注释
COMMENT ON TABLE lineage_nodes IS '数据血缘节点表';
COMMENT ON TABLE lineage_edges IS '数据血缘关系表';
COMMENT ON TABLE lineage_graphs IS '数据血缘图谱表';
COMMENT ON TABLE lineage_change_logs IS '数据血缘变更日志表';

COMMENT ON COLUMN lineage_nodes.node_type IS '节点类型: source/table/column/report/model/pipeline/api';
COMMENT ON COLUMN lineage_edges.edge_type IS '关系类型: derived/transform/aggregate/join/copy';
COMMENT ON COLUMN lineage_change_logs.change_type IS '变更类型: create/update/delete';
