"""
告警通知API
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.notification_service import (
    AlertLevel,
    NotificationChannel,
    get_notification_service,
)

router = APIRouter(prefix="/notifications", tags=["告警通知"])


class CreateAlertReq(BaseModel):
    title: str
    message: str
    level: str = "info"
    channels: Optional[List[str]] = None
    source: Optional[str] = None


class CreateRuleReq(BaseModel):
    name: str
    condition: str  # gt, lt, eq, gte, lte
    threshold: float
    level: str
    channels: List[str]
    enabled: bool = True


@router.post("/alerts")
def create_alert(req: CreateAlertReq):
    """创建并发送告警"""
    service = get_notification_service()
    alert = service.send_alert(
        title=req.title,
        message=req.message,
        level=req.level,
        channels=req.channels,
        source=req.source,
    )
    return {
        "alert_id": alert.id,
        "title": alert.title,
        "level": alert.level,
        "status": alert.status,
        "created_at": alert.created_at,
    }


@router.get("/alerts")
def get_alerts(
    status: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 100,
):
    """获取告警列表"""
    service = get_notification_service()
    alerts = service.get_alerts(status, level, limit)
    return {"total": len(alerts), "items": alerts}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str):
    """获取告警详情"""
    service = get_notification_service()
    for alert in service.alerts:
        if alert.id == alert_id:
            return {
                "id": alert.id,
                "title": alert.title,
                "message": alert.message,
                "level": alert.level,
                "source": alert.source,
                "status": alert.status,
                "metadata": alert.metadata,
                "created_at": alert.created_at,
            }
    raise HTTPException(status_code=404, detail="Alert not found")


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str):
    """解决告警"""
    service = get_notification_service()
    success = service.resolve_alert(alert_id)
    if success:
        return {"message": "Alert resolved"}
    raise HTTPException(status_code=404, detail="Alert not found")


@router.post("/rules")
def create_rule(req: CreateRuleReq):
    """创建告警规则"""
    from services.notification_service import AlertRule

    service = get_notification_service()
    rule = AlertRule(
        name=req.name,
        condition=req.condition,
        threshold=req.threshold,
        level=req.level,
        channels=req.channels,
        enabled=req.enabled,
    )
    service.add_rule(rule)
    return {"message": "Rule created", "rule_id": rule.id}


@router.get("/rules")
def get_rules(enabled: Optional[bool] = None):
    """获取告警规则"""
    service = get_notification_service()
    rules = service.get_rules(enabled)
    return {"total": len(rules), "items": rules}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str):
    """删除告警规则"""
    service = get_notification_service()
    success = service.remove_rule(rule_id)
    if success:
        return {"message": "Rule deleted"}
    raise HTTPException(status_code=404, detail="Rule not found")


@router.get("/channels")
def get_channels():
    """获取可用的通知渠道"""
    return {
        "channels": [
            {"id": "dingtalk", "name": "钉钉", "enabled": True},
            {"id": "email", "name": "邮件", "enabled": True},
            {"id": "sms", "name": "短信", "enabled": True},
            {"id": "webhook", "name": "Webhook", "enabled": True},
        ]
    }


@router.get("/status")
def get_notification_status():
    """获取通知服务状态"""
    service = get_notification_service()
    return {
        "dingtalk_configured": bool(service.dingtalk_webhook),
        "email_configured": bool(service.smtp_host),
        "sms_configured": bool(service.sms_api_key),
        "webhooks_configured": len(service.webhooks) > 0,
        "active_rules": len([r for r in service.rules if r.enabled]),
        "active_alerts": len([a for a in service.alerts if a.status == "active"]),
    }
