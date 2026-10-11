"""
RBAC权限管理服务
基于角色的访问控制
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# 预定义角色
ROLE_ADMIN = "admin"
ROLE_DATA_SCIENTIST = "data_scientist"
ROLE_ANALYST = "analyst"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"

# 预定义权限
PERMISSIONS = {
    # 项目权限
    "project:create": "创建项目",
    "project:read": "查看项目",
    "project:update": "更新项目",
    "project:delete": "删除项目",
    # 数据集权限
    "dataset:create": "创建数据集",
    "dataset:read": "查看数据集",
    "dataset:update": "更新数据集",
    "dataset:delete": "删除数据集",
    # 模型权限
    "model:create": "创建模型",
    "model:read": "查看模型",
    "model:train": "训练模型",
    "model:update": "更新模型",
    "model:delete": "删除模型",
    # 部署权限
    "deployment:create": "创建部署",
    "deployment:read": "查看部署",
    "deployment:update": "更新部署",
    "deployment:delete": "删除部署",
    "deployment:inference": "推理",
    # 用户权限
    "user:create": "创建用户",
    "user:read": "查看用户",
    "user:update": "更新用户",
    "user:delete": "删除用户",
    # 系统权限
    "system:config": "系统配置",
    "system:logs": "查看日志",
}

# 角色权限映射
ROLE_PERMISSIONS = {
    ROLE_ADMIN: list(PERMISSIONS.keys()),
    ROLE_DATA_SCIENTIST: [
        "project:create",
        "project:read",
        "project:update",
        "project:delete",
        "dataset:create",
        "dataset:read",
        "dataset:update",
        "dataset:delete",
        "model:create",
        "model:read",
        "model:train",
        "model:update",
        "model:delete",
        "deployment:create",
        "deployment:read",
        "deployment:update",
        "deployment:delete",
        "deployment:inference",
    ],
    ROLE_ANALYST: [
        "project:read",
        "project:update",
        "dataset:create",
        "dataset:read",
        "dataset:update",
        "model:read",
        "model:train",
        "deployment:read",
        "deployment:inference",
    ],
    ROLE_OPERATOR: [
        "project:read",
        "dataset:read",
        "model:read",
        "model:train",
        "deployment:read",
        "deployment:inference",
    ],
    ROLE_VIEWER: [
        "project:read",
        "dataset:read",
        "model:read",
        "deployment:read",
    ],
}


class RBACService:
    """RBAC权限管理服务"""

    def __init__(self):
        # 角色定义
        self.roles = {
            ROLE_ADMIN: {
                "name": "管理员",
                "description": "系统管理员，拥有所有权限",
                "permissions": ROLE_PERMISSIONS[ROLE_ADMIN],
            },
            ROLE_DATA_SCIENTIST: {
                "name": "数据科学家",
                "description": "可进行模型开发和训练",
                "permissions": ROLE_PERMISSIONS[ROLE_DATA_SCIENTIST],
            },
            ROLE_ANALYST: {
                "name": "数据分析师",
                "description": "可进行数据分析和模型使用",
                "permissions": ROLE_PERMISSIONS[ROLE_ANALYST],
            },
            ROLE_OPERATOR: {
                "name": "运维人员",
                "description": "可进行模型部署和推理",
                "permissions": ROLE_PERMISSIONS[ROLE_OPERATOR],
            },
            ROLE_VIEWER: {
                "name": "只读用户",
                "description": "只读访问",
                "permissions": ROLE_PERMISSIONS[ROLE_VIEWER],
            },
        }

    def get_all_roles(self) -> List[Dict[str, Any]]:
        """获取所有角色"""
        return [
            {"id": role_id, **role_info} for role_id, role_info in self.roles.items()
        ]

    def get_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        """获取角色详情"""
        if role_id in self.roles:
            return {"id": role_id, **self.roles[role_id]}
        return None

    def get_role_permissions(self, role_id: str) -> List[str]:
        """获取角色权限列表"""
        role = self.roles.get(role_id)
        if role:
            return role.get("permissions", [])
        return []

    def has_permission(self, role_id: str, permission: str) -> bool:
        """检查角色是否拥有权限"""
        permissions = self.get_role_permissions(role_id)
        return permission in permissions

    def check_permissions(
        self, role_id: str, required_permissions: List[str]
    ) -> Dict[str, bool]:
        """批量检查权限"""
        result = {}
        for perm in required_permissions:
            result[perm] = self.has_permission(role_id, perm)
        return result

    def get_all_permissions(self) -> Dict[str, str]:
        """获取所有权限定义"""
        return PERMISSIONS


# 全局实例
_rbac_service: Optional[RBACService] = None


def get_rbac_service() -> RBACService:
    """获取RBAC服务实例"""
    global _rbac_service
    if _rbac_service is None:
        _rbac_service = RBACService()
    return _rbac_service
