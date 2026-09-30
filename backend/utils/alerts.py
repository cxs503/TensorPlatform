# -*- coding: utf-8 -*-
"""
告警通知配置
支持健康检查异常时发送告警
"""
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class AlertManager:
    """告警管理器"""

    def __init__(self):
        self.webhook_url = os.getenv("ALERT_WEBHOOK_URL")
        self.slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
        self.email_recipients = os.getenv("ALERT_EMAIL_RECIPIENTS", "").split(",")
        self.alert_cooldown = int(os.getenv("ALERT_COOLDOWN_MINUTES", "5"))
        self.last_alert: Dict[str, datetime] = {}

    def should_alert(self, alert_type: str) -> bool:
        """检查是否应该发送告警（冷却期检查）"""
        if alert_type in self.last_alert:
            last_time = self.last_alert[alert_type]
            cooldown_seconds = self.alert_cooldown * 60
            if (datetime.utcnow() - last_time).total_seconds() < cooldown_seconds:
                return False
        return True

    def update_last_alert(self, alert_type: str):
        """更新最后告警时间"""
        self.last_alert[alert_type] = datetime.utcnow()

    def send_slack_alert(self, title: str, message: str, severity: str = "warning"):
        """发送 Slack 告警"""
        if not self.slack_webhook:
            return False

        payload = {
            "attachments": [
                {
                    "title": title,
                    "text": message,
                    "color": "danger" if severity == "critical" else "warning",
                }
            ]
        }

        try:
            response = requests.post(self.slack_webhook, json=payload, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"发送 Slack 告警失败: {e}")
            return False

    def send_email_alert(self, subject: str, body: str):
        """发送邮件告警"""
        # 需要配置 SMTP 服务器
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASSWORD")

        if not all([smtp_host, smtp_user, smtp_pass]):
            logger.warning("SMTP 未配置，跳过邮件告警")
            return False

        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            msg = MIMEMultipart()
            msg["From"] = smtp_user
            msg["To"] = ", ".join(self.email_recipients)
            msg["Subject"] = f"[开放底座AI] {subject}"
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, self.email_recipients, msg.as_string())
            server.quit()

            return True
        except Exception as e:
            logger.error(f"发送邮件告警失败: {e}")
            return False

    def send_dingtalk_alert(self, title: str, message: str):
        """发送钉钉告警"""
        dingtalk_webhook = os.getenv("DINGTALK_WEBHOOK_URL")
        if not dingtalk_webhook:
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{message}\n\n> {datetime.utcnow().isoformat()} UTC",
            },
        }

        try:
            response = requests.post(dingtalk_webhook, json=payload, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"发送钉钉告警失败: {e}")
            return False

    def send_telegram_alert(self, message: str, chat_id: Optional[str] = None):
        """发送 Telegram 告警"""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        if not all([token, chat_id]):
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}

        try:
            response = requests.post(url, json=payload, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"发送 Telegram 告警失败: {e}")
            return False

    def notify_health_issue(self, check_name: str, issue: Dict[str, Any]):
        """通知健康检查问题"""
        if not self.should_alert(f"health_{check_name}"):
            return

        title = f"[开放底座AI] 健康检查告警: {check_name}"
        message = f"""
**问题**: {issue.get('status', 'unknown')}
**详细信息**: {issue.get('error', '无详细信息')}
**时间**: {datetime.utcnow().isoformat()} UTC

**服务**: {check_name}
**Severity**: {issue.get('severity', 'warning')}
        """

        # 发送多种通知
        self.send_slack_alert(title, message)
        self.send_dingtalk_alert(title, message)
        self.send_telegram_alert(f"⚠️ {title}\n\n{message}")

        self.update_last_alert(f"health_{check_name}")
        logger.warning(f"发送健康检查告警: {check_name}")


# 全局实例
alert_manager = AlertManager()


def notify_async(background_tasks: BackgroundTasks, *args, **kwargs):
    """异步通知（用于 FastAPI 路由）"""
    background_tasks.add_task(alert_manager.notify_health_issue, *args, **kwargs)


if __name__ == "__main__":
    # 测试代码
    print("告警管理器已初始化")
    print(f"Webhook URL: {'配置' if alert_manager.webhook_url else '未配置'}")
    print(f"Slack Webhook: {'配置' if alert_manager.slack_webhook else '未配置'}")
    print(f"邮件接收人: {alert_manager.email_recipients}")
