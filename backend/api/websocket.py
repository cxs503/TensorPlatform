"""
WebSocket API
"""
import json
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from services.websocket_service import get_ws_manager

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    channel: Optional[str] = Query(
        "general",
        description="频道: general, alerts, training_{job_id}, inference_{deployment_id}",
    ),
):
    """
    WebSocket连接端点

    channel选项:
    - general: 通用消息
    - alerts: 告警通知
    - training_{job_id}: 特定训练任务进度
    - inference_{deployment_id}: 特定部署推理结果
    """
    manager = get_ws_manager()
    await manager.connect(websocket, channel)

    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_text()
            try:
                message = json.loads(data)

                # 处理客户端请求
                msg_type = message.get("type")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "subscribe":
                    # 重新订阅到指定频道
                    new_channel = message.get("channel", "general")
                    manager.disconnect(websocket, channel)
                    await manager.connect(websocket, new_channel)

            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        manager.disconnect(websocket, channel)


# ============ 消息推送接口 (供内部服务调用) ============


@router.post("/ws/push/training-progress")
async def push_training_progress(job_id: int, epoch: int, loss: float, metrics: dict):
    """推送训练进度"""
    from services.websocket_service import notify_training_progress

    await notify_training_progress(job_id, epoch, loss, metrics)
    return {"message": "Progress sent"}


@router.post("/ws/push/training-complete")
async def push_training_complete(job_id: int, model_id: str, metrics: dict):
    """推送训练完成"""
    from services.websocket_service import notify_training_complete

    await notify_training_complete(job_id, model_id, metrics)
    return {"message": "Complete notification sent"}


@router.post("/ws/push/alert")
async def push_alert(level: str, message: str, details: dict = None):
    """推送告警"""
    from services.websocket_service import notify_alert

    await notify_alert(level, message, details)
    return {"message": "Alert sent"}


@router.get("/ws/connections")
async def get_connection_stats():
    """获取连接统计"""
    manager = get_ws_manager()
    return {
        "total_connections": len(manager.clients),
        "alert_connections": len(manager.alert_clients),
        "training_jobs": len(manager.training_clients),
        "inference_deployments": len(manager.inference_clients),
    }
