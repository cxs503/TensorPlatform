UPDATE lineage_change_logs log
SET organization_id = node.organization_id
FROM lineage_nodes node
WHERE node.id = log.node_id AND log.organization_id IS NULL;

UPDATE lineage_change_logs log
SET organization_id = edge.organization_id
FROM lineage_edges edge
WHERE edge.id = log.edge_id AND log.organization_id IS NULL;
