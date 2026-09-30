"""
告警管理服务
提供多渠道告警、告警规则引擎、告警升级
"""
import random
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class AlertLevel(str, Enum):
    """告警级别"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    """告警状态"""

    PENDING = "pending"
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class AlertChannel(str, Enum):
    """通知渠道"""

    SMS = "sms"
    EMAIL = "email"
    DINGTALK = "dingtalk"
    WECHAT = "wechat"
    WEBHOOK = "webhook"


class AlertRule:
    """告警规则"""

    def __init__(self, name: str, condition: Dict, level: str, channels: List[str]):
        self.id = f"rule_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.condition = condition  # 条件配置
        self.level = level
        self.channels = channels
        self.enabled = True
        self.created_at = datetime.now().isoformat()


class Alert:
    """告警"""

    def __init__(self, rule_id: str, title: str, message: str, level: str, source: str):
        self.id = f"alert_{uuid.uuid4().hex[:12]}"
        self.rule_id = rule_id
        self.title = title
        self.message = message
        self.level = level
        self.source = source
        self.status = AlertStatus.TRIGGERED
        self.channels_notified = []
        self.created_at = datetime.now().isoformat()
        self.acknowledged_at = None
        self.acknowledged_by = None
        self.resolved_at = None
        self.resolved_by = None


class AlertEscalation:
    """告警升级"""

    def __init__(self, alert_id: str, from_level: str, to_level: str, reason: str):
        self.id = f"escalation_{uuid.uuid4().hex[:12]}"
        self.alert_id = alert_id
        self.from_level = from_level
        self.to_level = to_level
        self.reason = reason
        self.created_at = datetime.now().isoformat()


class AlertService:
    """告警管理服务"""

    def __init__(self):
        self.rules: Dict[str, AlertRule] = {}
        self.alerts: Dict[str, Alert] = {}
        self.escalations: Dict[str, AlertEscalation] = {}
        self._init_default_rules()

    def _init_default_rules(self):
        """初始化默认规则"""
        rules = [
            (
                "设备温度过高",
                {"metric": "temperature", "operator": ">", "threshold": 70},
                AlertLevel.WARNING,
                ["dingtalk", "email"],
            ),
            (
                "设备故障",
                {"metric": "status", "operator": "==", "threshold": "fault"},
                AlertLevel.ERROR,
                ["dingtalk", "sms", "email"],
            ),
            (
                "能耗异常",
                {"metric": "power", "operator": ">", "threshold": 500},
                AlertLevel.WARNING,
                ["dingtalk"],
            ),
            (
                "系统宕机",
                {"metric": "status", "operator": "==", "threshold": "offline"},
                AlertLevel.CRITICAL,
                ["dingtalk", "sms", "email"],
            ),
        ]
        for name, condition, level, channels in rules:
            rule = AlertRule(name, condition, level, channels)
            self.rules[rule.id] = rule

    # ========== 告警规则管理 ==========

    def create_rule(
        self, name: str, condition: Dict, level: str, channels: List[str]
    ) -> AlertRule:
        """创建告警规则"""
        rule = AlertRule(name, condition, level, channels)
        self.rules[rule.id] = rule
        return rule

    def get_rule(self, rule_id: str) -> Optional[AlertRule]:
        """获取规则"""
        return self.rules.get(rule_id)

    def list_rules(self, enabled: bool = None) -> List[Dict]:
        """列出规则"""
        rules = self.rules.values()
        if enabled is not None:
            rules = [r for r in rules if r.enabled == enabled]

        return [
            {
                "id": r.id,
                "name": r.name,
                "condition": r.condition,
                "level": r.level,
                "channels": r.channels,
                "enabled": r.enabled,
                "created_at": r.created_at,
            }
            for r in rules
        ]

    def update_rule(self, rule_id: str, **kwargs) -> Optional[AlertRule]:
        """更新规则"""
        rule = self.rules.get(rule_id)
        if not rule:
            return None

        for key, value in kwargs.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        """删除规则"""
        if rule_id in self.rules:
            del self.rules[rule_id]
            return True
        return False

    # ========== 告警触发 ==========

    def trigger_alert(self, rule_id: str, source: str, data: Dict) -> Optional[Alert]:
        """触发告警"""
        rule = self.rules.get(rule_id)
        if not rule or not rule.enabled:
            return None

        # 检查条件
        if not self._check_condition(rule.condition, data):
            return None

        # 创建告警
        alert = Alert(
            rule_id=rule_id,
            title=rule.name,
            message=f"{rule.name} - 数据: {data}",
            level=rule.level,
            source=source,
        )
        self.alerts[alert.id] = alert

        # 发送通知
        self._send_notifications(alert, rule.channels)

        return alert

    def _check_condition(self, condition: Dict, data: Dict) -> bool:
        """检查条件"""
        metric = condition.get("metric")
        operator = condition.get("operator")
        threshold = condition.get("threshold")

        if metric not in data:
            return False

        value = data[metric]

        if operator == ">":
            return value > threshold
        elif operator == "<":
            return value < threshold
        elif operator == ">=":
            return value >= threshold
        elif operator == "<=":
            return value <= threshold
        elif operator == "==":
            return value == threshold
        elif operator == "!=":
            return value != threshold

        return False

    def _send_notifications(self, alert: Alert, channels: List[str]):
        """发送通知"""
        for channel in channels:
            # 模拟发送
            alert.channels_notified.append(channel)

    # ========== 告警处理 ==========

    def acknowledge_alert(self, alert_id: str, user_id: str) -> bool:
        """确认告警"""
        alert = self.alerts.get(alert_id)
        if not alert:
            return False

        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = datetime.now().isoformat()
        alert.acknowledged_by = user_id
        return True

    def resolve_alert(self, alert_id: str, user_id: str, comment: str = None) -> bool:
        """解决告警"""
        alert = self.alerts.get(alert_id)
        if not alert:
            return False

        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = datetime.now().isoformat()
        alert.resolved_by = user_id
        return True

    def list_alerts(
        self,
        status: str = None,
        level: str = None,
        source: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """列出告警"""
        alerts = self.alerts.values()
        if status:
            alerts = [a for a in alerts if a.status == status]
        if level:
            alerts = [a for a in alerts if a.level == level]
        if source:
            alerts = [a for a in alerts if a.source == source]

        alerts = sorted(alerts, key=lambda a: a.created_at, reverse=True)
        return [
            {
                "id": a.id,
                "rule_id": a.rule_id,
                "title": a.title,
                "message": a.message,
                "level": a.level,
                "source": a.source,
                "status": a.status,
                "channels_notified": a.channels_notified,
                "created_at": a.created_at,
                "acknowledged_at": a.acknowledged_at,
                "resolved_at": a.resolved_at,
            }
            for a in alerts[:limit]
        ]

    # ========== 告警升级 ==========

    def escalate_alert(
        self, alert_id: str, to_level: str, reason: str
    ) -> Optional[AlertEscalation]:
        """升级告警"""
        alert = self.alerts.get(alert_id)
        if not alert:
            return None

        from_level = alert.level
        alert.level = to_level
        alert.status = AlertStatus.ESCALATED

        escalation = AlertEscalation(alert_id, from_level, to_level, reason)
        self.escalations[escalation.id] = escalation

        return escalation

    def get_escalations(self, alert_id: str = None) -> List[Dict]:
        """获取升级记录"""
        escalations = self.escalations.values()
        if alert_id:
            escalations = [e for e in escalations if e.alert_id == alert_id]

        return [
            {
                "id": e.id,
                "alert_id": e.alert_id,
                "from_level": e.from_level,
                "to_level": e.to_level,
                "reason": e.reason,
                "created_at": e.created_at,
            }
            for e in escalations
        ]

    # ========== 统计 ==========

    def get_alert_stats(self) -> Dict:
        """获取告警统计"""
        total = len(self.alerts)
        triggered = sum(
            1 for a in self.alerts.values() if a.status == AlertStatus.TRIGGERED
        )
        acknowledged = sum(
            1 for a in self.alerts.values() if a.status == AlertStatus.ACKNOWLEDGED
        )
        resolved = sum(
            1 for a in self.alerts.values() if a.status == AlertStatus.RESOLVED
        )

        by_level = {}
        for a in self.alerts.values():
            by_level[a.level] = by_level.get(a.level, 0) + 1

        return {
            "total": total,
            "triggered": triggered,
            "acknowledged": acknowledged,
            "resolved": resolved,
            "by_level": by_level,
            "rules": len(self.rules),
        }


# 全局实例
_alert_service: Optional[AlertService] = None


def get_alert_service() -> AlertService:
    """获取告警服务"""
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService()
    return _alert_service
