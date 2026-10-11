"""
IoT设备接入API
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# MQTT服务
from services.mqtt_service import get_mqtt_service

router = APIRouter(prefix="/iot", tags=["IoT设备"])


class DeviceRegisterRequest(BaseModel):
    device_id: str
    name: str
    device_type: str
    location: Optional[str] = None
    metadata: Optional[Dict] = None


class CommandRequest(BaseModel):
    command: str
    params: Optional[Dict] = None


@router.get("/devices")
def list_devices(status: Optional[str] = None):
    """列出所有设备"""
    mqtt = get_mqtt_service()
    devices = mqtt.list_devices(status)
    return {"total": len(devices), "items": devices}


@router.post("/devices")
def register_device(req: DeviceRegisterRequest):
    """注册新设备"""
    mqtt = get_mqtt_service()
    device = mqtt.register_device(
        req.device_id,
        {
            "name": req.name,
            "device_type": req.device_type,
            "location": req.location,
            "metadata": req.metadata or {},
        },
    )
    return device


@router.get("/devices/{device_id}")
def get_device(device_id: str):
    """获取设备信息"""
    mqtt = get_mqtt_service()
    device = mqtt.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.delete("/devices/{device_id}")
def delete_device(device_id: str):
    """删除设备"""
    mqtt = get_mqtt_service()
    success = mqtt.delete_device(device_id)
    if not success:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"message": "Device deleted"}


@router.post("/devices/{device_id}/command")
def send_command(device_id: str, req: CommandRequest):
    """向设备发送命令"""
    mqtt = get_mqtt_service()
    success = mqtt.send_command(device_id, req.command, req.params)
    if success:
        return {"message": "Command sent"}
    raise HTTPException(status_code=500, detail="Failed to send command")


@router.get("/devices/{device_id}/messages")
def get_device_messages(device_id: str, limit: int = 100):
    """获取设备消息历史"""
    mqtt = get_mqtt_service()
    messages = mqtt.get_messages(device_id, limit)
    return {"total": len(messages), "items": messages}


@router.get("/devices/{device_id}/metrics")
def get_device_metrics(device_id: str):
    """获取设备指标"""
    mqtt = get_mqtt_service()
    metrics = mqtt.get_device_metrics(device_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Device not found")
    return metrics


@router.get("/messages")
def get_all_messages(limit: int = 100):
    """获取所有消息"""
    mqtt = get_mqtt_service()
    messages = mqtt.get_messages(limit=limit)
    return {"total": len(messages), "items": messages}


@router.get("/status")
def get_mqtt_status():
    """获取MQTT连接状态"""
    mqtt = get_mqtt_service()
    return {
        "connected": mqtt.connected,
        "broker": mqtt.broker,
        "port": mqtt.port,
        "devices_count": len(mqtt.devices),
        "messages_count": len(mqtt.message_history),
    }
