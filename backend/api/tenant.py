"""
多租户管理API
"""
from typing import Dict, Optional

from utils.dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.tenant_service import get_tenant_service

router = APIRouter(prefix="/tenants", tags=["多租户管理"])


class CreateTenantReq(BaseModel):
    name: str
    slug: str  # 租户标识，如 company-name
    quota: Optional[Dict] = None


class UpdateTenantReq(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    settings: Optional[Dict] = None


class UpdateQuotaReq(BaseModel):
    projects: Optional[int] = None
    datasets: Optional[int] = None
    models: Optional[int] = None
    deployments: Optional[int] = None
    storage_gb: Optional[int] = None
    api_calls_per_day: Optional[int] = None
    users: Optional[int] = None


@router.post("")
def create_tenant(req: CreateTenantReq, current_user: dict = Depends(get_current_user)):
    """创建租户"""
    service = get_tenant_service()
    user_id = current_user.get("user_id", 0)

    # 检查slug唯一性
    existing = service.get_tenant_by_slug(req.slug)
    if existing:
        raise HTTPException(status_code=400, detail="租户标识已存在")

    tenant = service.create_tenant(req.name, req.slug, user_id, req.quota)
    return tenant.to_dict()


@router.get("")
def list_tenants(current_user: dict = Depends(get_current_user)):
    """列出租户"""
    service = get_tenant_service()
    tenants = service.list_tenants()
    return {"total": len(tenants), "items": [t.to_dict() for t in tenants]}


@router.get("/{tenant_id}")
def get_tenant(tenant_id: str):
    """获取租户详情"""
    service = get_tenant_service()
    tenant = service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")
    return tenant.to_dict()


@router.put("/{tenant_id}")
def update_tenant(tenant_id: str, req: UpdateTenantReq):
    """更新租户"""
    service = get_tenant_service()
    updates = req.dict(exclude_unset=True)
    success = service.update_tenant(tenant_id, updates)
    if success:
        return {"message": "租户已更新"}
    raise HTTPException(status_code=404, detail="租户不存在")


@router.delete("/{tenant_id}")
def delete_tenant(tenant_id: str):
    """删除租户"""
    service = get_tenant_service()
    success = service.delete_tenant(tenant_id)
    if success:
        return {"message": "租户已删除"}
    raise HTTPException(status_code=404, detail="租户不存在")


@router.get("/{tenant_id}/quota")
def get_quota(tenant_id: str):
    """获取配额使用情况"""
    service = get_tenant_service()
    summary = service.get_quota_usage_summary(tenant_id)
    if not summary:
        raise HTTPException(status_code=404, detail="租户不存在")
    return summary


@router.put("/{tenant_id}/quota")
def update_quota(tenant_id: str, req: UpdateQuotaReq):
    """更新租户配额"""
    service = get_tenant_service()
    quota = req.dict(exclude_unset=True)
    success = service.update_quota(tenant_id, quota)
    if success:
        return {"message": "配额已更新"}
    raise HTTPException(status_code=404, detail="租户不存在")


@router.get("/{tenant_id}/usage")
def get_usage(tenant_id: str):
    """获取使用量"""
    service = get_tenant_service()
    usage = service.get_usage(tenant_id)
    if not usage:
        raise HTTPException(status_code=404, detail="租户不存在")
    return usage.to_dict()


@router.get("/my-tenant")
def get_my_tenant(current_user: dict = Depends(get_current_user)):
    """获取当前用户所属租户"""
    # 从用户信息获取租户ID
    tenant_id = current_user.get("tenant_id")
    if not tenant_id:
        return {"message": "未绑定租户"}

    service = get_tenant_service()
    tenant = service.get_tenant(tenant_id)
    if not tenant:
        return {"message": "租户不存在"}

    return tenant.to_dict()
