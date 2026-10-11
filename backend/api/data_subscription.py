"""
数据订阅/共享 API
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from services.data_subscription_service import (
    SharePermission,
    SubscriptionStatus,
    get_subscription_service,
)

router = APIRouter(prefix="/data-subscription", tags=["数据订阅/共享"])


# ========== 订阅管理 ==========


@router.post("/subscriptions")
def create_subscription(req: dict):
    """创建数据订阅"""
    service = get_subscription_service()
    subscription = service.create_subscription(
        name=req.get("name"),
        dataset_id=req.get("dataset_id"),
        subscriber_id=req.get("subscriber_id"),
        filter_config=req.get("filter_config"),
        notification_channels=req.get("notification_channels", ["webhook"]),
        schedule=req.get("schedule", "realtime"),
    )
    return {
        "id": subscription.id,
        "name": subscription.name,
        "dataset_id": subscription.dataset_id,
        "status": subscription.status,
    }


@router.get("/subscriptions")
def list_subscriptions(subscriber_id: str = None):
    """列出订阅"""
    service = get_subscription_service()
    return service.list_subscriptions(subscriber_id)


@router.get("/subscriptions/{subscription_id}")
def get_subscription(subscription_id: str):
    """获取订阅详情"""
    service = get_subscription_service()
    sub = service.get_subscription(subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {
        "id": sub.id,
        "name": sub.name,
        "dataset_id": sub.dataset_id,
        "subscriber_id": sub.subscriber_id,
        "filter_config": sub.filter_config,
        "notification_channels": sub.notification_channels,
        "schedule": sub.schedule,
        "status": sub.status,
        "last_triggered": sub.last_triggered,
        "trigger_count": sub.trigger_count,
        "created_at": sub.created_at,
    }


@router.put("/subscriptions/{subscription_id}")
def update_subscription(subscription_id: str, req: dict):
    """更新订阅"""
    service = get_subscription_service()
    sub = service.update_subscription(
        subscription_id,
        status=req.get("status"),
        filter_config=req.get("filter_config"),
        notification_channels=req.get("notification_channels"),
    )
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"message": "更新成功", "status": sub.status}


@router.delete("/subscriptions/{subscription_id}")
def delete_subscription(subscription_id: str):
    """删除订阅"""
    service = get_subscription_service()
    if not service.delete_subscription(subscription_id):
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"message": "删除成功"}


# ========== 数据共享 ==========


@router.post("/shares")
def share_dataset(req: dict):
    """共享数据集"""
    service = get_subscription_service()
    share = service.share_dataset(
        dataset_id=req.get("dataset_id"),
        owner_id=req.get("owner_id"),
        target_tenant_id=req.get("target_tenant_id"),
        permission=req.get("permission", SharePermission.READ),
        expires_at=req.get("expires_at"),
    )
    return {
        "id": share.id,
        "dataset_id": share.dataset_id,
        "target_tenant_id": share.target_tenant_id,
        "permission": share.permission,
        "status": share.status,
    }


@router.get("/shares/by-me")
def list_shared_by_me(owner_id: str):
    """列出我共享的数据集"""
    service = get_subscription_service()
    return service.list_shared_by_me(owner_id)


@router.get("/shares/with-me")
def list_shared_with_me(tenant_id: str):
    """列出共享给我的数据集"""
    service = get_subscription_service()
    return service.list_shared_with_me(tenant_id)


@router.delete("/shares/{share_id}")
def revoke_share(share_id: str, owner_id: str):
    """撤销共享"""
    service = get_subscription_service()
    if not service.revoke_share(share_id, owner_id):
        raise HTTPException(status_code=404, detail="共享不存在或无权限")
    return {"message": "撤销成功"}


@router.put("/shares/{share_id}")
def update_share_permission(share_id: str, owner_id: str, permission: str):
    """更新共享权限"""
    service = get_subscription_service()
    share = service.update_share_permission(share_id, owner_id, permission)
    if not share:
        raise HTTPException(status_code=404, detail="共享不存在或无权限")
    return {"message": "更新成功", "permission": share.permission}
