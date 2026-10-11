"""
多租户服务
支持租户隔离、资源配额、租户管理
"""
import json
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class TenantStatus(str, Enum):
    """租户状态"""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"


class TenantQuota:
    """租户配额"""

    def __init__(
        self,
        projects: int = 10,
        datasets: int = 100,
        models: int = 50,
        deployments: int = 10,
        storage_gb: int = 100,
        api_calls_per_day: int = 10000,
        users: int = 20,
    ):
        self.projects = projects
        self.datasets = datasets
        self.models = models
        self.deployments = deployments
        self.storage_gb = storage_gb
        self.api_calls_per_day = api_calls_per_day
        self.users = users

    def to_dict(self) -> dict:
        return {
            "projects": self.projects,
            "datasets": self.datasets,
            "models": self.models,
            "deployments": self.deployments,
            "storage_gb": self.storage_gb,
            "api_calls_per_day": self.api_calls_per_day,
            "users": self.users,
        }


class Tenant:
    """租户"""

    def __init__(
        self,
        name: str,
        slug: str,
        owner_id: int,
        quota: TenantQuota = None,
    ):
        self.id = f"tenant_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.slug = slug
        self.owner_id = owner_id
        self.quota = quota or TenantQuota()
        self.status = TenantStatus.ACTIVE
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.settings = {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "owner_id": self.owner_id,
            "quota": self.quota.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "settings": self.settings,
        }


class TenantUsage:
    """租户使用量"""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.projects_count = 0
        self.datasets_count = 0
        self.models_count = 0
        self.deployments_count = 0
        self.storage_used_mb = 0
        self.api_calls_today = 0
        self.users_count = 0
        self.last_reset = datetime.now().date().isoformat()

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "projects_count": self.projects_count,
            "datasets_count": self.datasets_count,
            "models_count": self.models_count,
            "deployments_count": self.deployments_count,
            "storage_used_mb": self.storage_used_mb,
            "api_calls_today": self.api_calls_today,
            "users_count": self.users_count,
            "last_reset": self.last_reset,
        }


class TenantService:
    """多租户服务"""

    def __init__(self):
        self.tenants: Dict[str, Tenant] = {}
        self.usage: Dict[str, TenantUsage] = {}
        self._load_tenants()

    def _load_tenants(self):
        """加载租户数据"""
        # 简化实现，使用内存存储
        pass

    def create_tenant(
        self,
        name: str,
        slug: str,
        owner_id: int,
        quota: Dict = None,
    ) -> Tenant:
        """创建租户"""
        quota_obj = TenantQuota(**quota) if quota else TenantQuota()
        tenant = Tenant(name, slug, owner_id, quota_obj)
        self.tenants[tenant.id] = tenant
        self.usage[tenant.id] = TenantUsage(tenant.id)
        return tenant

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """获取租户"""
        return self.tenants.get(tenant_id)

    def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """通过slug获取租户"""
        for tenant in self.tenants.values():
            if tenant.slug == slug:
                return tenant
        return None

    def list_tenants(self, status: str = None) -> List[Tenant]:
        """列出租户"""
        tenants = list(self.tenants.values())
        if status:
            tenants = [t for t in tenants if t.status == status]
        return tenants

    def update_tenant(self, tenant_id: str, updates: Dict) -> bool:
        """更新租户"""
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return False

        if "name" in updates:
            tenant.name = updates["name"]
        if "status" in updates:
            tenant.status = updates["status"]
        if "settings" in updates:
            tenant.settings.update(updates["settings"])

        tenant.updated_at = datetime.now().isoformat()
        return True

    def update_quota(self, tenant_id: str, quota: Dict) -> bool:
        """更新配额"""
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return False

        tenant.quota = TenantQuota(**quota)
        return True

    def delete_tenant(self, tenant_id: str) -> bool:
        """删除租户"""
        if tenant_id in self.tenants:
            del self.tenants[tenant_id]
            if tenant_id in self.usage:
                del self.usage[tenant_id]
            return True
        return False

    def get_usage(self, tenant_id: str) -> Optional[TenantUsage]:
        """获取使用量"""
        return self.usage.get(tenant_id)

    def check_quota(self, tenant_id: str, resource_type: str, count: int = 1) -> bool:
        """检查配额"""
        tenant = self.tenants.get(tenant_id)
        usage = self.usage.get(tenant_id)

        if not tenant or not usage:
            return False

        # 检查是否超配额
        if resource_type == "project":
            return usage.projects_count + count <= tenant.quota.projects
        elif resource_type == "dataset":
            return usage.datasets_count + count <= tenant.quota.datasets
        elif resource_type == "model":
            return usage.models_count + count <= tenant.quota.models
        elif resource_type == "deployment":
            return usage.deployments_count + count <= tenant.quota.deployments
        elif resource_type == "user":
            return usage.users_count + count <= tenant.quota.users
        elif resource_type == "api_call":
            return usage.api_calls_today + count <= tenant.quota.api_calls_per_day

        return True

    def increment_usage(self, tenant_id: str, resource_type: str, count: int = 1):
        """增加使用量"""
        usage = self.usage.get(tenant_id)
        if not usage:
            return

        if resource_type == "project":
            usage.projects_count += count
        elif resource_type == "dataset":
            usage.datasets_count += count
        elif resource_type == "model":
            usage.models_count += count
        elif resource_type == "deployment":
            usage.deployments_count += count
        elif resource_type == "user":
            usage.users_count += count
        elif resource_type == "api_call":
            usage.api_calls_today += count

    def decrement_usage(self, tenant_id: str, resource_type: str, count: int = 1):
        """减少使用量"""
        usage = self.usage.get(tenant_id)
        if not usage:
            return

        if resource_type == "project":
            usage.projects_count = max(0, usage.projects_count - count)
        elif resource_type == "dataset":
            usage.datasets_count = max(0, usage.datasets_count - count)
        elif resource_type == "model":
            usage.models_count = max(0, usage.models_count - count)
        elif resource_type == "deployment":
            usage.deployments_count = max(0, usage.deployments_count - count)

    def get_quota_usage_summary(self, tenant_id: str) -> Dict:
        """获取配额使用摘要"""
        tenant = self.tenants.get(tenant_id)
        usage = self.usage.get(tenant_id)

        if not tenant or not usage:
            return {}

        return {
            "tenant_id": tenant_id,
            "quota": tenant.quota.to_dict(),
            "usage": usage.to_dict(),
            "limits": {
                "projects": f"{usage.projects_count}/{tenant.quota.projects}",
                "datasets": f"{usage.datasets_count}/{tenant.quota.datasets}",
                "models": f"{usage.models_count}/{tenant.quota.models}",
                "deployments": f"{usage.deployments_count}/{tenant.quota.deployments}",
                "users": f"{usage.users_count}/{tenant.quota.users}",
                "api_calls": f"{usage.api_calls_today}/{tenant.quota.api_calls_per_day}",
            },
        }


# 全局实例
_tenant_service: Optional[TenantService] = None


def get_tenant_service() -> TenantService:
    """获取租户服务"""
    global _tenant_service
    if _tenant_service is None:
        _tenant_service = TenantService()
    return _tenant_service
