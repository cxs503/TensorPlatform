-- Register locally mirrored, CC-BY public benchmarks as shared verification
-- assets. Files are mounted read-only at /app/public_datasets. Normal tenant
-- lists do not expose these backing records; curated-datasets does.

INSERT INTO datasets (
    name, description, file_path, size_bytes, row_count, stage, organization_id, created_at
)
SELECT
    'UCI Steel Industry Energy Consumption',
    '公开基准数据（CC BY 4.0）：钢铁行业能耗时序，用于能效基线、负荷分类、异常检测和数据质量验证。',
    '/app/public_datasets/uci-steel-industry-energy-consumption/raw/data.csv',
    0, 35040, 'raw', NULL, NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM datasets
    WHERE file_path = '/app/public_datasets/uci-steel-industry-energy-consumption/raw/data.csv'
);

INSERT INTO datasets (
    name, description, file_path, size_bytes, row_count, stage, organization_id, created_at
)
SELECT
    'UCI Gas Turbine CO and NOx',
    '公开基准数据（CC BY 4.0）：燃机传感器与排放时序，用于软测量、产能预测、排放预测和约束优化回放。',
    '/app/public_datasets/uci-gas-turbine-co-nox/raw/data.csv',
    0, 36733, 'raw', NULL, NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM datasets
    WHERE file_path = '/app/public_datasets/uci-gas-turbine-co-nox/raw/data.csv'
);

INSERT INTO data_assets (
    name, type, description, owner, tags, sensitivity_level, quality_score,
    schema_info, storage_info, source, status, version, created_at, updated_at
)
SELECT
    'UCI 钢铁行业能耗基准数据',
    'dataset',
    '35,040 条钢铁行业能耗记录。公开算法验证数据，不可作为客户节能收益或控制安全结论。',
    'UCI Machine Learning Repository',
    '["公开基准","钢铁","工业能效","时序","CC-BY-4.0"]'::jsonb,
    'public', 90,
    '{"domain":"工艺设备能效","scenario":"钢铁能耗基线与负荷分类","granularity":"15分钟级","recommended_tasks":["能效基线","负荷分类","异常检测","数据质量"],"license":"CC BY 4.0（须保留 UCI、作者和 DOI 归因）","source_url":"https://archive.ics.uci.edu/dataset/851/steel+industry+energy+consumption","doi":"10.24432/C52G8C","sha256":"9b1cee6f9cb9cd9df2b95814ca90a9a2ff15b7f5f1fba0fae3c643e82072eacc"}'::jsonb,
    jsonb_build_object('dataset_id', d.id, 'row_count', d.row_count, 'size_bytes', d.size_bytes, 'format', 'CSV'),
    'UCI Machine Learning Repository', 'active', '1.0.0', NOW(), NOW()
FROM datasets d
WHERE d.file_path = '/app/public_datasets/uci-steel-industry-energy-consumption/raw/data.csv'
  AND NOT EXISTS (
      SELECT 1 FROM data_assets a
      WHERE a.type = 'dataset' AND a.source = 'UCI Machine Learning Repository'
        AND a.storage_info->>'dataset_id' = d.id::text
  );

INSERT INTO data_assets (
    name, type, description, owner, tags, sensitivity_level, quality_score,
    schema_info, storage_info, source, status, version, created_at, updated_at
)
SELECT
    'UCI 燃机排放与产能基准数据',
    'dataset',
    '36,733 条燃机传感器与排放记录。公开算法验证数据，优化输出仅限仿真或人工审批建议。',
    'UCI Machine Learning Repository',
    '["公开基准","燃机","工业能效","排放","时序","CC-BY-4.0"]'::jsonb,
    'public', 90,
    '{"domain":"工艺设备能效","scenario":"燃机软测量、排放预测与约束优化回放","granularity":"小时级","recommended_tasks":["软测量","产能预测","排放预测","约束优化回放"],"license":"CC BY 4.0（须保留 UCI 和 DOI 归因）","source_url":"https://archive.ics.uci.edu/dataset/551/gas+turbine+co+and+nox+emission+data+set","doi":"10.24432/C5WC95","sha256":"33cbb8c68e24e3782547ccf0ed60d9ec0cc10732c3f84b08ed08fea08bfdaa6a"}'::jsonb,
    jsonb_build_object('dataset_id', d.id, 'row_count', d.row_count, 'size_bytes', d.size_bytes, 'format', 'CSV'),
    'UCI Machine Learning Repository', 'active', '1.0.0', NOW(), NOW()
FROM datasets d
WHERE d.file_path = '/app/public_datasets/uci-gas-turbine-co-nox/raw/data.csv'
  AND NOT EXISTS (
      SELECT 1 FROM data_assets a
      WHERE a.type = 'dataset' AND a.source = 'UCI Machine Learning Repository'
        AND a.storage_info->>'dataset_id' = d.id::text
  );
