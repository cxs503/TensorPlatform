-- Shared verification dataset library. Actual files remain datasets so the
-- existing training/governance paths keep working; published metadata lives in
-- data_assets and is independent of tenant ownership.
-- This only publishes existing demo datasets; no source data is changed/deleted.
INSERT INTO data_assets (
    name, type, description, owner, tags, sensitivity_level, quality_score,
    schema_info, storage_info, source, status, version, created_at, updated_at
)
SELECT
    '生产过程机工艺能效验证数据',
    'dataset',
    '开放底座钢铁示范数据：生产过程机工艺时序，可用于能效诊断、软测量、异常检测与特征工程回放。',
    '示例组织',
    '["开放底座示范","钢铁","生产过程","工艺设备","时序"]'::jsonb,
    'public', 85,
    '{"domain":"工艺设备能效","scenario":"生产过程工艺能效优化","granularity":"时序","recommended_tasks":["能效基线","软测量","异常检测","特征工程"],"license":"仅限平台验证与演示，禁止对外再分发","source_url":""}'::jsonb,
    jsonb_build_object('dataset_id', d.id, 'row_count', d.row_count, 'size_bytes', d.size_bytes, 'format', 'CSV'),
    '示例组织脱敏/模拟示范数据', 'active', '1.0.0', NOW(), NOW()
FROM datasets d
WHERE d.name = '生产过程机工艺数据(钢企Demo)'
  AND d.file_path IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM data_assets a
      WHERE a.type = 'dataset'
        AND a.source = '示例组织脱敏/模拟示范数据'
        AND a.storage_info->>'dataset_id' = d.id::text
  );

INSERT INTO data_assets (
    name, type, description, owner, tags, sensitivity_level, quality_score,
    schema_info, storage_info, source, status, version, created_at, updated_at
)
SELECT
    '光伏发电功率验证数据',
    'dataset',
    '示例组织源示范数据：光伏功率时序，可用于出力预测、数据质量校验与源网荷储策略回放。',
    '示例组织',
    '["开放底座示范","新能源","光伏","功率预测","时序"]'::jsonb,
    'public', 80,
    '{"domain":"能源管控","scenario":"光伏出力预测与源网荷储调度","granularity":"小时级","recommended_tasks":["功率预测","数据质量","调度回放"],"license":"仅限平台验证与演示，禁止对外再分发","source_url":""}'::jsonb,
    jsonb_build_object('dataset_id', d.id, 'row_count', d.row_count, 'size_bytes', d.size_bytes, 'format', 'CSV'),
    '示例组织脱敏/模拟示范数据', 'active', '1.0.0', NOW(), NOW()
FROM datasets d
WHERE d.name = '光伏发电功率数据(新能源Demo)'
  AND d.file_path IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM data_assets a
      WHERE a.type = 'dataset'
        AND a.source = '示例组织脱敏/模拟示范数据'
        AND a.storage_info->>'dataset_id' = d.id::text
  );
