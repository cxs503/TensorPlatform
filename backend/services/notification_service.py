"""
告警通知服务
支持多种通知方式：钉钉、短信、邮件、Webhook
"""
import json
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import requests

try:
    import aiohttp

    ASYNC_AVAILABLE = True
except ImportError:
    ASYNC_AVAILABLE = False
    print("aiohttp not available")


class AlertLevel(str, Enum):
    """告警级别"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationChannel(str, Enum):
    """通知渠道"""

    DINGTALK = "dingtalk"
    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"
    WECHAT = "wechat"


class AlertRule:
    """告警规则"""

    def __init__(
        self,
        name: str,
        condition: str,
        threshold: float,
        level: str,
        channels: List[str],
        enabled: bool = True,
    ):
        self.id = f"rule_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self.name = name
        self.condition = condition  # gt, lt, eq, gte, lte
        self.threshold = threshold
        self.level = level
        self.channels = channels
        self.enabled = enabled
        self.created_at = datetime.now().isoformat()


class Alert:
    """告警"""

    def __init__(
        self,
        title: str,
        message: str,
        level: str,
        source: str = None,
        metadata: Dict = None,
    ):
        self.id = f"alert_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self.title = title
        self.message = message
        self.level = level
        self.source = source
        self.metadata = metadata or {}
        self.created_at = datetime.now().isoformat()
        self.status = "active"
        self.notified_channels = []


class NotificationService:
    """通知服务"""

    def __init__(self):
        # 钉钉配置
        self.dingtalk_webhook = os.getenv("DINGTALK_WEBHOOK", "")
        self.dingtalk_secret = os.getenv("DINGTALK_SECRET", "")

        # 邮件配置
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.from_email = os.getenv("FROM_EMAIL", "")

        # 短信配置
        self.sms_provider = os.getenv("SMS_PROVIDER", "")
        self.sms_api_key = os.getenv("SMS_API_KEY", "")

        # Webhook配置
        self.webhooks = os.getenv("WEBHOOK_URLS", "").split(",")

        # 告警规则
        self.rules: List[AlertRule] = []

        # 告警历史
        self.alerts: List[Alert] = []
        self.max_alerts = 1000

        # 通知历史
        self.notifications: List[Dict] = []

    def add_rule(self, rule: AlertRule):
        """添加告警规则"""
        self.rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        """删除告警规则"""
        for i, rule in enumerate(self.rules):
            if rule.id == rule_id:
                self.rules.pop(i)
                return True
        return False

    def get_rules(self, enabled: bool = None) -> List[Dict]:
        """获取告警规则"""
        rules = self.rules
        if enabled is not None:
            rules = [r for r in rules if r.enabled == enabled]
        return [
            {
                "id": r.id,
                "name": r.name,
                "condition": r.condition,
                "threshold": r.threshold,
                "level": r.level,
                "channels": r.channels,
                "enabled": r.enabled,
                "created_at": r.created_at,
            }
            for r in rules
        ]

    def check_condition(self, condition: str, value: float, threshold: float) -> bool:
        """检查条件"""
        if condition == "gt":
            return value > threshold
        elif condition == "lt":
            return value < threshold
        elif condition == "eq":
            return value == threshold
        elif condition == "gte":
            return value >= threshold
        elif condition == "lte":
            return value <= threshold
        return False

    def create_alert(
        self,
        title: str,
        message: str,
        level: str,
        source: str = None,
        metadata: Dict = None,
    ) -> Alert:
        """创建告警"""
        alert = Alert(title, message, level, source, metadata)
        self.alerts.append(alert)

        # 限制告警数量
        if len(self.alerts) > self.max_alerts:
            self.alerts = self.alerts[-self.max_alerts :]

        return alert

    def get_alerts(
        self, status: str = None, level: str = None, limit: int = 100
    ) -> List[Dict]:
        """获取告警列表"""
        alerts = self.alerts

        if status:
            alerts = [a for a in alerts if a.status == status]
        if level:
            alerts = [a for a in alerts if a.level == level]

        alerts.sort(key=lambda x: x.created_at, reverse=True)

        return [
            {
                "id": a.id,
                "title": a.title,
                "message": a.message,
                "level": a.level,
                "source": a.source,
                "status": a.status,
                "created_at": a.created_at,
            }
            for a in alerts[:limit]
        ]

    def resolve_alert(self, alert_id: str) -> bool:
        """解决告警"""
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.status = "resolved"
                return True
        return False

    async def send_notification(
        self,
        alert: Alert,
        channels: List[str],
    ):
        """发送通知"""
        for channel in channels:
            success = False
            try:
                if channel == NotificationChannel.DINGTALK:
                    success = await self._send_dingtalk(alert)
                elif channel == NotificationChannel.EMAIL:
                    success = await self._send_email(alert)
                elif channel == NotificationChannel.SMS:
                    success = await self._send_sms(alert)
                elif channel == NotificationChannel.WEBHOOK:
                    success = await self._send_webhook(alert)
            except Exception as e:
                print(f"Failed to send {channel} notification: {e}")
                success = False

            # 记录通知结果
            self.notifications.append(
                {
                    "alert_id": alert.id,
                    "channel": channel,
                    "success": success,
                    "timestamp": datetime.now().isoformat(),
                }
            )

    async def _send_dingtalk(self, alert: Alert) -> bool:
        """发送钉钉通知"""
        if not self.dingtalk_webhook:
            print("Dingtalk webhook not configured")
            return False

        # 构建消息
        msg = {
            "msgtype": "markdown",
            "markdown": {
                "title": alert.title,
                "text": f"## {alert.title}\n\n**级别**: {alert.level}\n\n**消息**: {alert.message}\n\n**时间**: {alert.created_at}",
            },
        }

        if ASYNC_AVAILABLE:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.dingtalk_webhook, json=msg) as resp:
                    return resp.status == 200
        else:
            resp = requests.post(self.dingtalk_webhook, json=msg)
            return resp.status == 200

    async def _send_email(self, alert: Alert) -> bool:
        """发送邮件通知"""
        if not self.smtp_host:
            print("SMTP not configured")
            return False

        # TODO: 实现邮件发送
        return True

    async def _send_sms(self, alert: Alert) -> bool:
        """发送短信通知"""
        if not self.sms_api_key:
            print("SMS provider not configured")
            return False

        # TODO: 实现短信发送
        return True

    async def _send_webhook(self, alert: Alert) -> bool:
        """发送WebHook通知"""
        if not self.webhooks:
            print("Webhooks not configured")
            return False

        payload = {
            "alert_id": alert.id,
            "title": alert.title,
            "message": alert.message,
            "level": alert.level,
            "source": alert.source,
            "timestamp": alert.created_at,
        }

        success = True
        for webhook_url in self.webhooks:
            if not webhook_url:
                continue

            try:
                if ASYNC_AVAILABLE:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(webhook_url, json=payload) as resp:
                            if resp.status != 200:
                                success = False
                else:
                    resp = requests.post(webhook_url, json=payload)
                    if resp.status != 200:
                        success = False
            except Exception as e:
                print(f"Webhook error: {e}")
                success = False

        return success

    def send_alert(
        self,
        title: str,
        message: str,
        level: str,
        channels: List[str] = None,
        source: str = None,
    ):
        """发送告警（同步版本）"""
        alert = self.create_alert(title, message, level, source)

        if channels is None:
            channels = [NotificationChannel.DINGTALK]  # 默认钉钉

        # 同步发送
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(self.send_notification(alert, channels))

        return alert


# 全局实例
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """获取通知服务"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
