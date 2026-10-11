-- Keep public benchmark metadata aligned with the locally verified files.
UPDATE datasets
SET size_bytes = 2731389
WHERE file_path = '/app/public_datasets/uci-steel-industry-energy-consumption/raw/data.csv';

UPDATE datasets
SET size_bytes = 2999043
WHERE file_path = '/app/public_datasets/uci-gas-turbine-co-nox/raw/data.csv';

UPDATE data_assets a
SET storage_info = jsonb_set(
    a.storage_info::jsonb,
    '{size_bytes}',
    to_jsonb(d.size_bytes)
)::json
FROM datasets d
WHERE a.storage_info->>'dataset_id' = d.id::text
  AND d.file_path IN (
    '/app/public_datasets/uci-steel-industry-energy-consumption/raw/data.csv',
    '/app/public_datasets/uci-gas-turbine-co-nox/raw/data.csv'
  );
