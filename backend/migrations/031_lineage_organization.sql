ALTER TABLE lineage_nodes
    ADD COLUMN IF NOT EXISTS organization_id INTEGER NULL REFERENCES organizations(id) ON DELETE SET NULL;

ALTER TABLE lineage_edges
    ADD COLUMN IF NOT EXISTS organization_id INTEGER NULL REFERENCES organizations(id) ON DELETE SET NULL;

ALTER TABLE lineage_change_logs
    ADD COLUMN IF NOT EXISTS organization_id INTEGER NULL REFERENCES organizations(id) ON DELETE SET NULL;

UPDATE lineage_nodes node
SET organization_id = dataset.organization_id,
    resource_type = COALESCE(node.resource_type, 'dataset'),
    resource_id = COALESCE(node.resource_id, dataset.id)
FROM datasets dataset
WHERE node.id = 'ln-dataset-' || dataset.id::text;

UPDATE lineage_nodes node
SET organization_id = job.organization_id,
    resource_type = COALESCE(node.resource_type, 'training_job'),
    resource_id = COALESCE(node.resource_id, job.id)
FROM training_jobs job
WHERE node.id = 'ln-job-' || job.id::text;

UPDATE lineage_nodes node
SET organization_id = model.organization_id,
    resource_type = COALESCE(node.resource_type, 'model'),
    resource_id = COALESCE(node.resource_id, model.id)
FROM models model
WHERE node.id = 'ln-model-' || model.id::text;

UPDATE lineage_edges edge
SET organization_id = COALESCE(source.organization_id, target.organization_id)
FROM lineage_nodes source, lineage_nodes target
WHERE source.id = edge.source_id AND target.id = edge.target_id
  AND edge.organization_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_lineage_nodes_org ON lineage_nodes(organization_id);
CREATE INDEX IF NOT EXISTS idx_lineage_edges_org ON lineage_edges(organization_id);
CREATE INDEX IF NOT EXISTS idx_lineage_change_logs_org ON lineage_change_logs(organization_id);
