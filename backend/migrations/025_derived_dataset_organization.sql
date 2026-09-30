UPDATE datasets derived
SET organization_id = source.organization_id
FROM datasets source
WHERE derived.governed_from = source.id
  AND derived.stage IN ('engineered', 'labeled')
  AND derived.organization_id IS DISTINCT FROM source.organization_id;
