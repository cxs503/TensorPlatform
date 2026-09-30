-- 034: 可持久化的角色权限配置
-- 角色编码与 users.role 的小写值保持一致；权限编码与 api.deps.require_permission 保持一致。

CREATE TABLE IF NOT EXISTS rbac_roles (
    code VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    is_builtin BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_rbac_roles_status CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS rbac_permissions (
    code VARCHAR(128) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    action VARCHAR(32) NOT NULL,
    description TEXT,
    is_builtin BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rbac_role_permissions (
    role_code VARCHAR(64) NOT NULL REFERENCES rbac_roles(code) ON DELETE CASCADE,
    permission_code VARCHAR(128) NOT NULL REFERENCES rbac_permissions(code) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (role_code, permission_code)
);

INSERT INTO rbac_roles (code, name, description, status, is_builtin) VALUES
    ('admin', '管理员', '系统管理员，可配置各身份的功能权限', 'active', TRUE),
    ('manager', '经理', '负责平台资源和业务流程管理', 'active', TRUE),
    ('engineer', '工程师', '负责数据处理、模型训练、评估和部署', 'active', TRUE),
    ('viewer', '访客', '以只读方式查看平台资源', 'active', TRUE)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = NOW();

INSERT INTO rbac_permissions (code, name, resource_type, action, description, is_builtin) VALUES
    ('datasets:read', '查看数据集', 'datasets', 'read', '查看、预览和下载数据集', TRUE),
    ('datasets:write', '管理数据集', 'datasets', 'write', '上传、接入、清洗、加工和删除数据集', TRUE),
    ('models:read', '查看模型', 'models', 'read', '查看模型、版本和模型文件信息', TRUE),
    ('models:write', '管理模型', 'models', 'write', '创建、修改和删除模型资产', TRUE),
    ('training:read', '查看训练', 'training', 'read', '查看训练任务、日志和实验结果', TRUE),
    ('training:write', '执行训练', 'training', 'write', '创建、运行、复现和停止训练任务', TRUE),
    ('deployment:read', '查看部署', 'deployment', 'read', '查看部署、端点和打包产物', TRUE),
    ('deployment:write', '管理部署', 'deployment', 'write', '创建、更新和删除部署', TRUE),
    ('experiments:read', '查看实验', 'experiments', 'read', '查看评估、对比和实验记录', TRUE),
    ('experiments:write', '执行实验', 'experiments', 'write', '创建和运行评估、对比实验', TRUE)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    resource_type = EXCLUDED.resource_type,
    action = EXCLUDED.action,
    description = EXCLUDED.description,
    updated_at = NOW();

-- 仅在角色还没有任何持久化配置时写入原有默认权限，避免重启覆盖管理员已保存的设置。
INSERT INTO rbac_role_permissions (role_code, permission_code)
SELECT role_code, permission_code
FROM (
    SELECT r.code AS role_code, p.code AS permission_code
    FROM rbac_roles r
    CROSS JOIN rbac_permissions p
    WHERE r.code IN ('admin', 'manager', 'engineer')
    UNION ALL
    SELECT 'viewer', p.code
    FROM rbac_permissions p
    WHERE p.code IN ('datasets:read', 'models:read')
) defaults
WHERE NOT EXISTS (
    SELECT 1 FROM rbac_role_permissions existing
    WHERE existing.role_code = defaults.role_code
)
ON CONFLICT DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_rbac_role_permissions_permission
    ON rbac_role_permissions(permission_code);

COMMENT ON TABLE rbac_role_permissions IS '角色与平台实际接口权限的持久化关联';
