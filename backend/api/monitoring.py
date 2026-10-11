"""
开放数字化底座 - 监控告警API（数据库持久化版本）
"""
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from services.monitoring_service import get_health_status, get_system_metrics
from database.config import get_db
from models.core import Alert, AlertRule

router = APIRouter()


def _check_db_health() -> dict:
    """检测数据库连接状态"""
    try:
        from database.config import SessionLocal
        db = SessionLocal()
        start = time.time()
        db.execute(db.bind.dialect.do_ping)
        elapsed_ms = int((time.time() - start) * 1000)
        db.close()
        return {"status": "healthy", "response_time_ms": elapsed_ms}
    except Exception:
        return {"status": "unhealthy", "response_time_ms": None}


def _check_redis_health() -> dict:
    """检测 Redis 连接状态"""
    try:
        import redis
        r = redis.Redis(
            host="redis", port=6379,
            socket_connect_timeout=1, socket_timeout=1,
        )
        start = time.time()
        r.ping()
        elapsed_ms = int((time.time() - start) * 1000)
        r.close()
        return {"status": "healthy", "response_time_ms": elapsed_ms}
    except Exception:
        return {"status": "unhealthy", "response_time_ms": None}


# ============ 监控指标 API ============

@router.get("/monitoring/metrics")
def get_metrics():
    """获取系统监控指标 — 通过 psutil 获取真实 CPU/内存/磁盘/网络"""
    return get_system_metrics()


# ============ 系统健康检查 API ============

@router.get("/monitoring/health")
def get_health():
    """获取系统健康状态 — 真实检测各组件连接"""
    health = get_health_status()
    health["components"] = {
        "database": _check_db_health(),
        "cache": _check_redis_health(),
    }
    health["is_synthetic"] = False
    return health


# ============ 告警规则 API（DB 持久化） ============

class AlertRuleCreate(BaseModel):
    name: str
    metric: str
    threshold: float
    operator: str
    severity: str
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    metric: Optional[str] = None
    threshold: Optional[float] = None
    operator: Optional[str] = None
    severity: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/monitoring/alert-rules")
def get_alert_rules(db: Session = Depends(get_db)):
    """获取告警规则列表 — 从数据库查询"""
    rules = db.query(AlertRule).order_by(AlertRule.id).all()
    return {
        "total": len(rules),
        "items": [r.to_dict() for r in rules],
        "is_synthetic": False,
    }


@router.post("/monitoring/alerts")
def create_alert_rule(rule: AlertRuleCreate, db: Session = Depends(get_db)):
    """创建告警规则 — 持久化到数据库"""
    db_rule = AlertRule(
        name=rule.name, metric=rule.metric,
        threshold=rule.threshold, operator=rule.operator,
        severity=rule.severity, enabled=rule.enabled,
    )
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)
    return {"message": "Alert rule created", "rule": db_rule.to_dict()}


@router.put("/monitoring/alerts/{alert_id}")
def update_alert_rule(alert_id: int, rule: AlertRuleUpdate, db: Session = Depends(get_db)):
    """更新告警规则"""
    db_rule = db.query(AlertRule).filter(AlertRule.id == alert_id).first()
    if not db_rule:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    update_data = rule.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_rule, key, value)
    db.commit()
    db.refresh(db_rule)
    return {"message": "Alert rule updated", "rule": db_rule.to_dict()}


@router.delete("/monitoring/alerts/{alert_id}")
def delete_alert_rule(alert_id: int, db: Session = Depends(get_db)):
    """删除告警规则"""
    db_rule = db.query(AlertRule).filter(AlertRule.id == alert_id).first()
    if not db_rule:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    db.delete(db_rule)
    db.commit()
    return {"message": "Alert rule deleted"}


# ============ 告警事件 API（DB 持久化） ============

class AlertCreate(BaseModel):
    title: str
    severity: str = "warning"
    source: Optional[str] = None
    entity_id: Optional[str] = None
    message: Optional[str] = None


@router.get("/monitoring/alerts")
def get_alerts(
    status: Optional[str] = Query(None, description="resolved / active"),
    db: Session = Depends(get_db),
):
    """获取告警事件列表 — 从数据库查询"""
    q = db.query(Alert)
    if status == "resolved":
        q = q.filter(Alert.is_resolved == True)
    elif status == "active":
        q = q.filter(Alert.is_resolved == False)
    items = q.order_by(Alert.created_at.desc()).limit(100).all()
    return {
        "total": len(items),
        "items": [a.to_dict() for a in items],
        "is_synthetic": False,
    }


@router.get("/monitoring/alerts/{alert_id}")
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    """获取单个告警详情"""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert.to_dict()


@router.post("/monitoring/alert-events")
def create_alert_event(alert: AlertCreate, db: Session = Depends(get_db)):
    """创建告警事件"""
    db_alert = Alert(
        title=alert.title, severity=alert.severity,
        source=alert.source, entity_id=alert.entity_id,
        message=alert.message,
    )
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return {"message": "Alert created", "alert": db_alert.to_dict()}


@router.post("/monitoring/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    """解决告警"""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_resolved = True
    alert.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(alert)
    return {"message": "Alert resolved", "alert": alert.to_dict()}


# ============ Prometheus 指标导出 ============

@router.get("/monitoring/prometheus")
def prometheus_metrics():
    """Prometheus格式的指标导出"""
    metrics = get_system_metrics()
    m = metrics["metrics"]
    lines = [
        f'foundation_cpu_usage_percent {m["cpu"]["usage_percent"]}',
        f'foundation_cpu_cores {m["cpu"]["cores"]}',
        f'foundation_memory_usage_percent {m["memory"]["usage_percent"]}',
        f'foundation_memory_total_gb {m["memory"]["total_gb"]}',
        f'foundation_memory_used_gb {m["memory"]["used_gb"]}',
        f'foundation_disk_usage_percent {m["disk"]["usage_percent"]}',
        f'foundation_disk_total_gb {m["disk"]["total_gb"]}',
        f'foundation_disk_used_gb {m["disk"]["used_gb"]}',
        f'foundation_network_rx_mb {m["network"]["rx_mb"]}',
        f'foundation_network_tx_mb {m["network"]["tx_mb"]}',
    ]
    return "\n".join(lines)
