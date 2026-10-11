"""
WebSocket服务
支持实时训练进度推送、告警通知、在线推理结果推送
"""
import asyncio
import json
from datetime import datetime
from typing import Any, Callable, Dict, Set


# 存储连接的客户端
class WebSocketManager:
    """WebSocket连接管理器"""

    def __init__(self):
        # 通用客户端
        self.clients: Set[Any] = set()

        # 按类型分组
        self.training_clients: Dict[int, Set[Any]] = {}  # job_id -> clients
        self.alert_clients: Set[Any] = set()
        self.inference_clients: Dict[str, Set[Any]] = {}  # deployment_id -> clients

        # 消息队列
        self.message_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self, websocket, channel: str = "general"):
        """客户端连接"""
        await websocket.accept()
        self.clients.add(websocket)

        # 根据channel分类
        if channel == "alerts":
            self.alert_clients.add(websocket)
        elif channel.startswith("training_"):
            job_id = int(channel.split("_")[1])
            if job_id not in self.training_clients:
                self.training_clients[job_id] = set()
            self.training_clients[job_id].add(websocket)
        elif channel.startswith("inference_"):
            deployment_id = channel.split("_")[1]
            if deployment_id not in self.inference_clients:
                self.inference_clients[deployment_id] = set()
            self.inference_clients[deployment_id].add(websocket)

    def disconnect(self, websocket, channel: str = "general"):
        """客户端断开"""
        self.clients.discard(websocket)
        self.alert_clients.discard(websocket)

        # 从各分组中移除
        for job_id in self.training_clients:
            self.training_clients[job_id].discard(websocket)

        for deployment_id in self.inference_clients:
            self.inference_clients[deployment_id].discard(websocket)

    async def send_message(self, websocket, message: Dict):
        """发送消息到客户端"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            print(f"Send error: {e}")
            self.disconnect(websocket)

    async def broadcast(self, message: Dict, channel: str = "general"):
        """广播消息"""
        if channel == "general":
            clients = self.clients
        elif channel == "alerts":
            clients = self.alert_clients
        elif channel.startswith("training_"):
            job_id = int(channel.split("_")[1])
            clients = self.training_clients.get(job_id, set())
        elif channel.startswith("inference_"):
            deployment_id = channel.split("_")[1]
            clients = self.inference_clients.get(deployment_id, set())
        else:
            clients = set()

        # 发送给所有客户端
        disconnected = []
        for client in clients:
            try:
                await client.send_json(message)
            except:
                disconnected.append(client)

        # 清理断开的客户端
        for client in disconnected:
            self.disconnect(client)

    # ============ 训练进度推送 ============

    async def send_training_progress(self, job_id: int, progress: Dict):
        """发送训练进度"""
        message = {
            "type": "training_progress",
            "job_id": job_id,
            "data": progress,
            "timestamp": datetime.now().isoformat(),
        }
        channel = f"training_{job_id}"
        await self.broadcast(message, channel)

    async def send_training_complete(self, job_id: int, result: Dict):
        """发送训练完成消息"""
        message = {
            "type": "training_complete",
            "job_id": job_id,
            "data": result,
            "timestamp": datetime.now().isoformat(),
        }
        channel = f"training_{job_id}"
        await self.broadcast(message, channel)

    async def send_training_error(self, job_id: int, error: str):
        """发送训练错误消息"""
        message = {
            "type": "training_error",
            "job_id": job_id,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
        channel = f"training_{job_id}"
        await self.broadcast(message, channel)

    # ============ 告警推送 ============

    async def send_alert(self, alert: Dict):
        """发送告警"""
        message = {
            "type": "alert",
            "data": alert,
            "timestamp": datetime.now().isoformat(),
        }
        await self.broadcast(message, "alerts")

    # ============ 推理结果推送 ============

    async def send_inference_result(self, deployment_id: str, result: Dict):
        """发送推理结果"""
        message = {
            "type": "inference_result",
            "deployment_id": deployment_id,
            "data": result,
            "timestamp": datetime.now().isoformat(),
        }
        channel = f"inference_{deployment_id}"
        await self.broadcast(message, channel)

    # ============ 系统消息 ============

    async def send_system_message(self, message: str):
        """发送系统消息"""
        msg = {
            "type": "system",
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        await self.broadcast(msg, "general")


# 全局实例
_ws_manager: WebSocketManager = None


def get_ws_manager() -> WebSocketManager:
    """获取WebSocket管理器"""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager


# 便捷函数


async def notify_training_progress(job_id: int, epoch: int, loss: float, metrics: Dict):
    """通知训练进度"""
    await get_ws_manager().send_training_progress(
        job_id, {"epoch": epoch, "loss": loss, "metrics": metrics}
    )


async def notify_training_complete(job_id: int, model_id: str, metrics: Dict):
    """通知训练完成"""
    await get_ws_manager().send_training_complete(
        job_id, {"model_id": model_id, "metrics": metrics}
    )


async def notify_alert(level: str, message: str, details: Dict = None):
    """发送告警通知"""
    await get_ws_manager().send_alert(
        {"level": level, "message": message, "details": details or {}}
    )
