"""
告警管理 API
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from database.config import get_db
from models.core import Alert
from services.alert_service import AlertLevel, get_alert_service

router = APIRouter(prefix="/alerts", tags=["告警管理"])


# ========== 告警规则 ==========


@router.get("/rules")
def list_rules(enabled: bool = None):
    """列出告警规则"""
    service = get_alert_service()
    return service.list_rules(enabled)


@router.post("/rules")
def create_rule(req: dict):
    """创建告警规则"""
    service = get_alert_service()
    rule = service.create_rule(
        name=req.get("name"),
        condition=req.get("condition"),
        level=req.get("level", AlertLevel.WARNING),
        channels=req.get("channels", ["dingtalk"]),
    )
    return {"id": rule.id, "name": rule.name, "enabled": rule.enabled}


@router.get("/rules/{rule_id}")
def get_rule(rule_id: str):
    """获取规则"""
    service = get_alert_service()
    rule = service.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {
        "id": rule.id,
        "name": rule.name,
        "condition": rule.condition,
        "level": rule.level,
        "channels": rule.channels,
        "enabled": rule.enabled,
    }


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, req: dict):
    """更新规则"""
    service = get_alert_service()
    rule = service.update_rule(rule_id, **req)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"message": "更新成功"}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str):
    """删除规则"""
    service = get_alert_service()
    if not service.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"message": "删除成功"}


# ========== 告警触发 ==========


@router.post("/trigger")
def trigger_alert(req: dict):
    """触发告警"""
    service = get_alert_service()
    alert = service.trigger_alert(
        rule_id=req.get("rule_id"), source=req.get("source"), data=req.get("data", {})
    )
    if not alert:
        raise HTTPException(status_code=400, detail="触发失败")
    return {
        "id": alert.id,
        "title": alert.title,
        "level": alert.level,
        "status": alert.status,
    }


# ========== 告警处理 ==========


@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str, user_id: str):
    """确认告警"""
    service = get_alert_service()
    if not service.acknowledge_alert(alert_id, user_id):
        raise HTTPException(status_code=404, detail="告警不存在")
    return {"message": "已确认"}


@router.post("/{alert_id}/resolve")
def resolve_alert(
    alert_id: int,
    user_id: str = "system",
    comment: str = None,
    db: Session = Depends(get_db),
):
    """Resolve the same persistent alert records returned by the list API."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="告警不存在")
    alert.is_resolved = True
    alert.resolved_at = datetime.utcnow()
    db.commit()
    return {"message": "已解决"}


@router.post("/batch-resolve")
def batch_resolve_alerts(req: dict, db: Session = Depends(get_db)):
    ids = [int(value) for value in (req.get("ids") or []) if str(value).isdigit()]
    if not ids:
        raise HTTPException(status_code=400, detail="请选择需要解决的告警")
    now = datetime.utcnow()
    count = db.query(Alert).filter(Alert.id.in_(ids), Alert.is_resolved.is_(False)).update(
        {Alert.is_resolved: True, Alert.resolved_at: now},
        synchronize_session=False,
    )
    db.commit()
    return {"message": "批量解决成功", "resolved": count}


@router.get("")
def list_alerts(
    status: str = None, level: str = None, source: str = None, limit: int = 100,
    db: Session = Depends(get_db),
):
    """列出告警(从持久化 alerts 表读取)"""
    try:
        q = "SELECT id, title, severity, source, entity_id, message, is_resolved, created_at FROM alerts"
        cond, params = [], {"lim": limit}
        if source:
            cond.append("source = :src"); params["src"] = source
        if level:
            cond.append("severity = :lvl"); params["lvl"] = level
        if status == "unresolved":
            cond.append("is_resolved = false")
        elif status == "resolved":
            cond.append("is_resolved = true")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY id DESC LIMIT :lim"
        rows = db.execute(text(q), params).fetchall()
        return {"total": len(rows), "items": [{
            "id": r[0], "title": r[1], "severity": r[2], "source": r[3],
            "entity_id": r[4], "message": r[5], "is_resolved": r[6],
            "created_at": r[7].isoformat() if r[7] else None,
        } for r in rows]}
    except Exception as e:
        return {"total": 0, "items": [], "error": str(e)}


# ========== 告警升级 ==========


@router.post("/{alert_id}/escalate")
def escalate_alert(alert_id: str, to_level: str, reason: str):
    """升级告警"""
    service = get_alert_service()
    escalation = service.escalate_alert(alert_id, to_level, reason)
    if not escalation:
        raise HTTPException(status_code=404, detail="告警不存在")
    return {"message": "已升级"}


@router.get("/escalations")
def get_escalations(alert_id: str = None):
    """获取升级记录"""
    service = get_alert_service()
    return service.get_escalations(alert_id)


# ========== 统计 ==========


@router.get("/stats")
def get_alert_stats(db: Session = Depends(get_db)):
    """获取告警统计(从 alerts 表)"""
    try:
        total = db.execute(text("SELECT count(*) FROM alerts")).scalar() or 0
        unresolved = db.execute(text("SELECT count(*) FROM alerts WHERE is_resolved=false")).scalar() or 0
        resolved = db.execute(text("SELECT count(*) FROM alerts WHERE is_resolved=true")).scalar() or 0
        by_level = {r[0] or "info": r[1] for r in db.execute(
            text("SELECT severity, count(*) FROM alerts GROUP BY severity")).fetchall()}
        by_source = {r[0] or "unknown": r[1] for r in db.execute(
            text("SELECT source, count(*) FROM alerts GROUP BY source")).fetchall()}
        return {"total": total, "triggered": unresolved, "acknowledged": 0,
                "resolved": resolved, "unresolved": unresolved,
                "by_level": by_level, "by_source": by_source}
    except Exception as e:
        return {"total": 0, "triggered": 0, "resolved": 0, "by_level": {}, "error": str(e)}
