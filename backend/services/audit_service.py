"""
审计日志服务
完整记录所有敏感操作，支持追溯和合规审计
"""
import hashlib
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditAction(str, Enum):
    """审计操作类型"""

    # 用户操作
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    USER_REGISTER = "user.register"
    USER_PASSWORD_CHANGE = "user.password_change"
    USER_PASSWORD_RESET = "user.password_reset"
    USER_PERMISSION_CHANGE = "user.permission_change"

    # 项目操作
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    PROJECT_DELETE = "project.delete"
    PROJECT_EXPORT = "project.export"

    # 数据操作
    DATASET_UPLOAD = "dataset.upload"
    DATASET_DOWNLOAD = "dataset.download"
    DATASET_DELETE = "dataset.delete"
    DATASET_ACCESS = "dataset.access"

    # 模型操作
    MODEL_CREATE = "model.create"
    MODEL_TRAIN = "model.train"
    MODEL_DEPLOY = "model.deploy"
    MODEL_DELETE = "model.delete"
    MODEL_EXPORT = "model.export"

    # 部署操作
    DEPLOYMENT_CREATE = "deployment.create"
    DEPLOYMENT_UPDATE = "deployment.update"
    DEPLOYMENT_DELETE = "deployment.delete"
    DEPLOYMENT_SCALE = "deployment.scale"

    # 系统操作
    SYSTEM_CONFIG_CHANGE = "system.config_change"
    SYSTEM_BACKUP = "system.backup"
    SYSTEM_RESTORE = "system.restore"

    # 敏感操作
    SENSITIVE_DATA_ACCESS = "sensitive_data.access"
    API_KEY_CREATE = "api_key.create"
    API_KEY_REVOKE = "api_key.revoke"


class AuditLevel(str, Enum):
    """审计级别"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditLogger:
    """审计日志记录器"""

    def __init__(self):
        self.log_dir = Path("/var/log/open-foundation/audit")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_file(self, date: str = None) -> Path:
        """获取日志文件路径"""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"audit_{date}.jsonl"

    def log(
        self,
        action: str,
        user_id: str = None,
        username: str = None,
        resource_type: str = None,
        resource_id: str = None,
        level: str = AuditLevel.INFO,
        details: Dict = None,
        ip: str = None,
        user_agent: str = None,
        result: str = "success",
    ):
        """记录审计日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "user_id": user_id,
            "username": username,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "level": level,
            "details": details or {},
            "ip": ip,
            "user_agent": user_agent,
            "result": result,
            # 生成唯一ID
            "event_id": hashlib.md5(
                f"{datetime.now().isoformat()}{action}{user_id}".encode()
            ).hexdigest()[:16],
        }

        # 写入文件
        log_file = self._get_log_file()
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        return log_entry


class AuditService:
    """审计日志服务"""

    def __init__(self):
        self.logger = AuditLogger()
        self.log_dir = Path("/var/log/open-foundation/audit")

    def log_event(
        self,
        action: str,
        user_id: str = None,
        username: str = None,
        resource_type: str = None,
        resource_id: str = None,
        level: str = AuditLevel.INFO,
        details: Dict = None,
        ip: str = None,
        user_agent: str = None,
        result: str = "success",
    ):
        """记录审计事件"""
        return self.logger.log(
            action,
            user_id,
            username,
            resource_type,
            resource_id,
            level,
            details,
            ip,
            user_agent,
            result,
        )

    def search(
        self,
        user_id: str = None,
        action: str = None,
        resource_type: str = None,
        resource_id: str = None,
        start_date: str = None,
        end_date: str = None,
        result: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """搜索审计日志"""
        results = []

        # 读取最近的文件
        date_files = sorted(self.log_dir.glob("audit_*.jsonl"), reverse=True)[:7]

        for log_file in date_files:
            with open(log_file, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                    except:
                        continue

                    # 过滤条件
                    if user_id and entry.get("user_id") != user_id:
                        continue
                    if action and entry.get("action") != action:
                        continue
                    if resource_type and entry.get("resource_type") != resource_type:
                        continue
                    if resource_id and entry.get("resource_id") != resource_id:
                        continue
                    if result and entry.get("result") != result:
                        continue

                    results.append(entry)

                    if len(results) >= limit:
                        break

            if len(results) >= limit:
                break

        # 按时间倒序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return results[:limit]

    def get_user_activity(self, user_id: str, days: int = 30) -> Dict:
        """获取用户活动统计"""
        recent_logs = self.search(user_id=user_id, limit=1000)

        # 统计操作类型
        action_counts = {}
        for log in recent_logs:
            action = log.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1

        # 统计失败操作
        failed_count = len([l for l in recent_logs if l.get("result") == "failed"])

        return {
            "user_id": user_id,
            "total_actions": len(recent_logs),
            "action_counts": action_counts,
            "failed_actions": failed_count,
            "first_activity": recent_logs[-1].get("timestamp") if recent_logs else None,
            "last_activity": recent_logs[0].get("timestamp") if recent_logs else None,
        }

    def get_security_events(self, days: int = 7) -> List[Dict]:
        """获取安全相关事件"""
        critical_actions = [
            AuditAction.USER_LOGIN,
            AuditAction.USER_PASSWORD_CHANGE,
            AuditAction.USER_PERMISSION_CHANGE,
            AuditAction.API_KEY_CREATE,
            AuditAction.API_KEY_REVOKE,
            AuditAction.SYSTEM_CONFIG_CHANGE,
        ]

        events = []
        for action in critical_actions:
            events.extend(self.search(action=action, limit=100))

        # 添加失败的操作
        events.extend(self.search(result="failed", limit=100))

        # 去重和排序
        seen = set()
        unique_events = []
        for e in events:
            event_id = e.get("event_id")
            if event_id not in seen:
                seen.add(event_id)
                unique_events.append(e)

        unique_events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return unique_events[:100]

    def export_logs(self, start_date: str, end_date: str, format: str = "json") -> str:
        """导出审计日志"""
        logs = self.search(start_date=start_date, end_date=end_date, limit=10000)

        if format == "json":
            return json.dumps(logs, indent=2, ensure_ascii=False)
        elif format == "csv":
            # 转换为CSV
            if not logs:
                return ""

            headers = [
                "timestamp",
                "action",
                "user_id",
                "username",
                "resource_type",
                "resource_id",
                "result",
                "ip",
            ]
            lines = [",".join(headers)]

            for log in logs:
                row = [str(log.get(h, "")) for h in headers]
                lines.append(",".join(row))

            return "\n".join(lines)

        return json.dumps(logs)


# 全局实例
_audit_service: Optional[AuditService] = None


def get_audit_service() -> AuditService:
    """获取审计服务"""
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService()
    return _audit_service
