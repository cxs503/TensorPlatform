-- Preserve checksums of published migrations and retain dataset/lineage history.
-- Withdraw only the shared assets registered by migrations 017 and 018.
UPDATE data_assets a
SET status = 'archived', updated_at = NOW()
FROM datasets d
WHERE a.type = 'dataset'
  AND a.user_id IS NULL
  AND a.storage_info->>'dataset_id' = d.id::text
  AND (
      (a.name = 'UCI 钢铁行业能耗基准数据'
       AND a.source = 'UCI Machine Learning Repository'
       AND d.organization_id IS NULL
       AND d.file_path = '/app/public_datasets/uci-steel-industry-energy-consumption/raw/data.csv')
      OR
      (a.name = '生产过程机工艺能效验证数据'
       AND a.source = '示例组织脱敏/模拟示范数据'
       AND d.name = '生产过程机工艺数据(钢企Demo)')
  );

-- Remove unchanged seeds only; never delete billing or user-owned records.
DELETE FROM components c
WHERE c.workspace_id IS NULL
  AND (
      (c.name = '炼钢温度预测'
       AND c.type = 'time_series_forecast'
       AND c.description = '预测炼钢过程中温度变化趋势'
       AND c.parameters::jsonb = '{"algorithm":"GRU","framework":"PyTorch","accuracy":"93%%"}'::jsonb)
      OR
      (c.name = '钢表面缺陷检测'
       AND c.type = 'computer_vision'
       AND c.description = '检测钢材表面缺陷（裂纹、划痕等）'
       AND c.parameters::jsonb = '{"algorithm":"ResNet50","framework":"PyTorch","accuracy":"97%%","classes":["normal","crack","scratch","pit"]}'::jsonb)
  )
  AND NOT EXISTS (SELECT 1 FROM pricing_configs p WHERE p.component_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM billing_records b WHERE b.component_id = c.id);
