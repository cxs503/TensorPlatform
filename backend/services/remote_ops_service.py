"""
远程运维服务
提供设备远程重启、配置、诊断、升级等功能
"""
import os
import time
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class OperationType(str, Enum):
    """操作类型"""

    REBOOT = "reboot"  # 重启
    STOP = "stop"  # 停止
    START = "start"  # 启动
    UPDATE_CONFIG = "update_config"  # 更新配置
    FACTORY_RESET = "factory_reset"  # 恢复出厂
    FIRMWARE_UPGRADE = "firmware_upgrade"  # 固件升级
    DIAGNOSE = "diagnose"  # 诊断
    REMOTE_CONTROL = "remote_control"  # 远程控制


class OperationStatus(str, Enum):
    """操作状态"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RemoteOperation:
    """远程操作"""

    def __init__(
        self,
        device_id: str,
        operation_type: str,
        params: Dict = None,
        operator: str = "system",
    ):
        self.id = f"op_{uuid.uuid4().hex[:12]}"
        self.device_id = device_id
        self.operation_type = operation_type
        self.params = params or {}
        self.operator = operator
        self.status = OperationStatus.PENDING
        self.progress = 0
        self.result = None
        self.error = None
        self.created_at = datetime.now().isoformat()
        self.started_at = None
        self.completed_at = None


class RemoteSession:
    """远程会话"""

    def __init__(self, device_id: str, session_type: str = "ssh"):
        self.id = f"session_{uuid.uuid4().hex[:12]}"
        self.device_id = device_id
        self.session_type = session_type
        self.status = "active"
        self.created_at = datetime.now().isoformat()
        self.connected_at = None
        self.disconnected_at = None
        self.commands = []


class DeviceSnapshot:
    """设备快照"""

    def __init__(self, device_id: str):
        self.id = f"snap_{uuid.uuid4().hex[:12]}"
        self.device_id = device_id
        self.timestamp = datetime.now().isoformat()
        self.config = {}
        self.status = {}
        self.metrics = {}


class RemoteOpsService:
    """远程运维服务"""

    def __init__(self):
        self.operations: Dict[str, RemoteOperation] = {}
        self.sessions: Dict[str, RemoteSession] = {}
        self.snapshots: Dict[str, DeviceSnapshot] = {}
        self._init_sample_devices()

    def _init_sample_devices(self):
        """初始化示例设备"""
        # 设备在后续操作中使用

    # ========== 远程操作 ==========

    def create_operation(
        self,
        device_id: str,
        operation_type: str,
        params: Dict = None,
        operator: str = "system",
    ) -> RemoteOperation:
        """创建远程操作"""
        op = RemoteOperation(
            device_id=device_id,
            operation_type=operation_type,
            params=params,
            operator=operator,
        )
        self.operations[op.id] = op
        return op

    def execute_operation(self, operation_id: str) -> Optional[RemoteOperation]:
        """执行远程操作"""
        op = self.operations.get(operation_id)
        if not op:
            return None

        op.status = OperationStatus.RUNNING
        op.started_at = datetime.now().isoformat()

        # 模拟执行
        if op.operation_type == OperationType.REBOOT:
            op.progress = 50
            op.result = {"message": "设备正在重启", "next_status": "online"}
            op.status = OperationStatus.SUCCESS

        elif op.operation_type == OperationType.STOP:
            op.progress = 100
            op.result = {"message": "设备已停止"}
            op.status = OperationStatus.SUCCESS

        elif op.operation_type == OperationType.START:
            op.progress = 100
            op.result = {"message": "设备已启动"}
            op.status = OperationStatus.SUCCESS

        elif op.operation_type == OperationType.UPDATE_CONFIG:
            op.progress = 100
            op.result = {"message": "配置已更新", "updated_params": op.params}
            op.status = OperationStatus.SUCCESS

        elif op.operation_type == OperationType.FACTORY_RESET:
            op.progress = 100
            op.result = {"message": "已恢复出厂设置"}
            op.status = OperationStatus.SUCCESS

        elif op.operation_type == OperationType.FIRMWARE_UPGRADE:
            op.progress = 100
            op.result = {
                "message": "固件升级完成",
                "new_version": op.params.get("version", "v2.0.1"),
            }
            op.status = OperationStatus.SUCCESS

        elif op.operation_type == OperationType.DIAGNOSE:
            op.progress = 100
            op.result = self._diagnose_device(op.device_id)
            op.status = OperationStatus.SUCCESS

        elif op.operation_type == OperationType.REMOTE_CONTROL:
            op.progress = 100
            op.result = {"message": "控制指令已发送", "command": op.params.get("command")}
            op.status = OperationStatus.SUCCESS

        op.completed_at = datetime.now().isoformat()
        return op

    def _diagnose_device(self, device_id: str) -> Dict:
        """诊断设备"""
        # 获取真实系统指标
        cpu_usage = 0
        memory_usage = 0
        disk_usage = 0

        if PSUTIL_AVAILABLE:
            try:
                cpu_usage = int(psutil.cpu_percent(interval=0.1))
                memory_usage = int(psutil.virtual_memory().percent)
                disk_usage = int(psutil.disk_usage("/").percent)
            except Exception:
                cpu_usage = 50
                memory_usage = 50
                disk_usage = 50
        else:
            cpu_usage = 50
            memory_usage = 50
            disk_usage = 50

        return {
            "device_id": device_id,
            "connection": "正常",
            "response_time_ms": 45,
            "cpu_usage": cpu_usage,
            "memory_usage": memory_usage,
            "disk_usage": disk_usage,
            "network_status": "正常",
            "errors": [],
            "warnings": ["温度偏高 (45°C)", "固件版本可更新"],
            "recommendations": ["建议清理设备散热口", "建议升级固件到最新版本"],
        }

    def get_operation(self, operation_id: str) -> Optional[RemoteOperation]:
        """获取操作"""
        return self.operations.get(operation_id)

    def list_operations(
        self,
        device_id: str = None,
        operation_type: str = None,
        status: str = None,
        limit: int = 50,
    ) -> List[Dict]:
        """列出操作"""
        ops = self.operations.values()

        if device_id:
            ops = [o for o in ops if o.device_id == device_id]
        if operation_type:
            ops = [o for o in ops if o.operation_type == operation_type]
        if status:
            ops = [o for o in ops if o.status == status]

        ops = sorted(ops, key=lambda o: o.created_at, reverse=True)

        return [
            {
                "id": o.id,
                "device_id": o.device_id,
                "operation_type": o.operation_type,
                "params": o.params,
                "operator": o.operator,
                "status": o.status,
                "progress": o.progress,
                "result": o.result,
                "error": o.error,
                "created_at": o.created_at,
                "started_at": o.started_at,
                "completed_at": o.completed_at,
            }
            for o in ops[:limit]
        ]

    # ========== 远程会话 ==========

    def create_session(
        self, device_id: str, session_type: str = "ssh"
    ) -> RemoteSession:
        """创建远程会话"""
        session = RemoteSession(device_id, session_type)
        session.connected_at = datetime.now().isoformat()
        self.sessions[session.id] = session
        return session

    def close_session(self, session_id: str) -> bool:
        """关闭远程会话"""
        session = self.sessions.get(session_id)
        if not session:
            return False

        session.status = "closed"
        session.disconnected_at = datetime.now().isoformat()
        return True

    def execute_command(self, session_id: str, command: str) -> Dict:
        """执行命令"""
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "会话不存在"}

        # 模拟命令执行
        result = {
            "command": command,
            "output": f"模拟执行: {command}",
            "exit_code": 0,
            "timestamp": datetime.now().isoformat(),
        }

        session.commands.append(result)
        return result

    def list_sessions(self, device_id: str = None) -> List[Dict]:
        """列出远程会话"""
        sessions = self.sessions.values()

        if device_id:
            sessions = [s for s in sessions if s.device_id == device_id]

        return [
            {
                "id": s.id,
                "device_id": s.device_id,
                "session_type": s.session_type,
                "status": s.status,
                "created_at": s.created_at,
                "connected_at": s.connected_at,
                "commands_count": len(s.commands),
            }
            for s in sessions
        ]

    # ========== 设备快照 ==========

    def create_snapshot(self, device_id: str) -> DeviceSnapshot:
        """创建设备快照"""
        snap = DeviceSnapshot(device_id)

        # 快照配置数据
        snap.config = {
            "ip": "192.168.1.100",
            "subnet": "255.255.255.0",
            "gateway": "192.168.1.1",
            "dns": "8.8.8.8",
            "ntp_server": "pool.ntp.org",
            "timezone": "Asia/Shanghai",
        }

        # 获取真实系统运行时间和指标
        uptime_seconds = 86400
        cpu_usage = 50
        memory_usage = 50
        disk_usage = 50
        cpu_temp = 45

        if PSUTIL_AVAILABLE:
            try:
                uptime_seconds = int(time.time() - psutil.boot_time())
                cpu_usage = int(psutil.cpu_percent(interval=0.1))
                memory_usage = int(psutil.virtual_memory().percent)
                disk_usage = int(psutil.disk_usage("/").percent)

                # 尝试读取CPU温度 (Linux)
                try:
                    thermal_zones = [
                        "/sys/class/thermal/thermal_zone0/temp",
                        "/sys/class/thermal/thermal_zone1/temp",
                    ]
                    for tz in thermal_zones:
                        if os.path.exists(tz):
                            with open(tz, "r") as f:
                                temp_raw = int(f.read().strip()) / 1000.0
                                cpu_temp = int(temp_raw)
                                break
                except Exception:
                    pass
            except Exception:
                pass

        snap.status = {
            "online": True,
            "uptime_seconds": uptime_seconds,
            "last_error": None,
        }

        snap.metrics = {
            "cpu_temp": cpu_temp,
            "cpu_usage": cpu_usage,
            "memory_usage": memory_usage,
            "disk_usage": disk_usage,
        }

        self.snapshots[snap.id] = snap
        return snap

    def get_snapshot(self, snapshot_id: str) -> Optional[DeviceSnapshot]:
        """获取快照"""
        return self.snapshots.get(snapshot_id)

    def list_snapshots(self, device_id: str = None) -> List[Dict]:
        """列出快照"""
        snaps = self.snapshots.values()

        if device_id:
            snaps = [s for s in snaps if s.device_id == device_id]

        snaps = sorted(snaps, key=lambda s: s.timestamp, reverse=True)

        return [
            {
                "id": s.id,
                "device_id": s.device_id,
                "timestamp": s.timestamp,
                "config": s.config,
                "status": s.status,
                "metrics": s.metrics,
            }
            for s in snaps
        ]

    # ========== 批量操作 ==========

    def batch_operation(
        self, device_ids: List[str], operation_type: str, params: Dict = None
    ) -> List[Dict]:
        """批量操作"""
        results = []
        for device_id in device_ids:
            op = self.create_operation(device_id, operation_type, params)
            self.execute_operation(op.id)
            results.append(
                {"device_id": device_id, "operation_id": op.id, "status": op.status}
            )
        return results


# 全局实例
_remote_ops_service: Optional[RemoteOpsService] = None


def get_remote_ops_service() -> RemoteOpsService:
    """获取远程运维服务"""
    global _remote_ops_service
    if _remote_ops_service is None:
        _remote_ops_service = RemoteOpsService()
    return _remote_ops_service
