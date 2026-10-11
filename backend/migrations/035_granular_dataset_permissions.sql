-- 035: 数据集查看改为所有身份的基础能力，将笼统的 datasets:write 拆为具体操作。

INSERT INTO rbac_permissions (code, name, resource_type, action, description, is_builtin) VALUES
    ('datasets:upload', '上传数据集', 'datasets', 'upload', '新建数据集并上传本地文件', TRUE),
    ('datasets:connect', '接入数据源', 'datasets', 'connect', '新建、连接、测试和导入外部数据源', TRUE),
    ('datasets:download', '下载数据集', 'datasets', 'download', '下载数据集或数据分割结果', TRUE),
    ('datasets:edit', '编辑数据集', 'datasets', 'edit', '在线编辑并将结果保存为新的数据集', TRUE),
    ('datasets:process', '清洗与加工', 'datasets', 'process', '执行清洗、治理、分割、对齐、特征工程和标注', TRUE),
    ('datasets:analyze', '运行数据分析', 'datasets', 'analyze', '运行算法分析、智能分析并管理分析记录', TRUE),
    ('datasets:delete', '删除数据集', 'datasets', 'delete', '删除数据集及其文件', TRUE)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    resource_type = EXCLUDED.resource_type,
    action = EXCLUDED.action,
    description = EXCLUDED.description,
    updated_at = NOW();

-- 原“管理数据集”代表全部操作：迁移时为原持有者授予全部细分权限。
INSERT INTO rbac_role_permissions (role_code, permission_code)
SELECT old.role_code, granular.code
FROM rbac_role_permissions old
CROSS JOIN rbac_permissions granular
WHERE old.permission_code = 'datasets:write'
  AND granular.code IN (
      'datasets:upload', 'datasets:connect', 'datasets:download', 'datasets:edit',
      'datasets:process', 'datasets:analyze', 'datasets:delete'
  )
ON CONFLICT DO NOTHING;

-- 查看数据集为所有登录身份的基础能力，不再出现在可配置权限中。
INSERT INTO rbac_role_permissions (role_code, permission_code)
SELECT code, 'datasets:read' FROM rbac_roles
ON CONFLICT DO NOTHING;

DELETE FROM rbac_role_permissions WHERE permission_code = 'datasets:write';
DELETE FROM rbac_permissions WHERE code = 'datasets:write';
