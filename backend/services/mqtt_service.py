"""
MQTT设备接入服务
支持IoT设备连接和数据采集
"""
import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:
    import paho.mqtt.client as mqtt

    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    print("MQTT not available")


class MQTTSevice:
    """MQTT服务"""

    def __init__(self):
        self.broker = os.getenv("MQTT_BROKER", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.username = os.getenv("MQTT_USERNAME", "")
        self.password = os.getenv("MQTT_PASSWORD", "")
        self.client_id = os.getenv("MQTT_CLIENT_ID", "open-digital-foundation")

        # 设备注册表
        self.devices: Dict[str, Dict[str, Any]] = {}

        # 消息回调
        self.message_handlers: Dict[str, Callable] = {}

        # 消息历史
        self.message_history: List[Dict] = []
        self.max_history = 1000

        # 连接状态
        self.connected = False
        self._client = None

        if MQTT_AVAILABLE:
            self._setup_client()

    def _setup_client(self):
        """设置MQTT客户端"""
        self._client = mqtt.Client(client_id=self.client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        if self.username and self.password:
            self._client.username_pw_set(self.username, self.password)

    def _on_connect(self, client, userdata, flags, rc):
        """连接回调"""
        if rc == 0:
            self.connected = True
            print(f"Connected to MQTT broker {self.broker}:{self.port}")
            # 订阅设备主题
            client.subscribe("foundation/devices/+/data")
        else:
            print(f"MQTT connection failed with code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """断开连接回调"""
        self.connected = False
        print("Disconnected from MQTT broker")

    def _on_message(self, client, userdata, msg):
        """消息回调"""
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode("utf-8"))

            # 解析设备ID
            parts = topic.split("/")
            if len(parts) >= 4:
                device_id = parts[2]
                self._handle_device_message(device_id, payload)
        except Exception as e:
            print(f"Error processing MQTT message: {e}")

    def _handle_device_message(self, device_id: str, payload: Dict):
        """处理设备消息"""
        # 更新设备最后活跃时间
        if device_id in self.devices:
            self.devices[device_id]["last_active"] = datetime.now().isoformat()
            self.devices[device_id]["status"] = "online"

        # 存储消息
        message = {
            "device_id": device_id,
            "payload": payload,
            "timestamp": datetime.now().isoformat(),
        }
        self.message_history.append(message)

        # 限制历史记录数量
        if len(self.message_history) > self.max_history:
            self.message_history = self.message_history[-self.max_history :]

        # 调用消息处理函数
        handler_key = f"device/{device_id}"
        if handler_key in self.message_handlers:
            self.message_handlers[handler_key](payload)

    def connect(self) -> bool:
        """连接到MQTT broker"""
        if not MQTT_AVAILABLE:
            return False

        try:
            self._client.connect(self.broker, self.port, 60)
            self._client.loop_start()
            return True
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}")
            return False

    def disconnect(self):
        """断开MQTT连接"""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    def register_device(
        self, device_id: str, device_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        注册设备

        Args:
            device_id: 设备ID
            device_info: 设备信息
        """
        self.devices[device_id] = {
            **device_info,
            "device_id": device_id,
            "registered_at": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
            "status": "online",
        }

        return self.devices[device_id]

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """获取设备信息"""
        return self.devices.get(device_id)

    def list_devices(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出设备"""
        if status:
            return [d for d in self.devices.values() if d.get("status") == status]
        return list(self.devices.values())

    def update_device_status(self, device_id: str, status: str) -> bool:
        """更新设备状态"""
        if device_id in self.devices:
            self.devices[device_id]["status"] = status
            self.devices[device_id]["last_active"] = datetime.now().isoformat()
            return True
        return False

    def delete_device(self, device_id: str) -> bool:
        """删除设备"""
        if device_id in self.devices:
            del self.devices[device_id]
            return True
        return False

    def publish(self, topic: str, payload: Dict[str, Any], qos: int = 0) -> bool:
        """
        发布消息

        Args:
            topic: 主题
            payload: 消息内容
            qos: 服务质量等级
        """
        if not self._client or not self.connected:
            print("Not connected to MQTT broker")
            return False

        try:
            result = self._client.publish(topic, json.dumps(payload), qos=qos)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            print(f"Publish failed: {e}")
            return False

    def send_command(self, device_id: str, command: str, params: Dict = None) -> bool:
        """向设备发送命令"""
        topic = f"foundation/devices/{device_id}/command"
        payload = {
            "command": command,
            "params": params or {},
            "timestamp": datetime.now().isoformat(),
        }
        return self.publish(topic, payload, qos=1)

    def subscribe(self, topic: str, handler: Callable[[Dict], None] = None):
        """订阅主题"""
        if not self._client:
            return

        self._client.subscribe(topic)

        if handler:
            self.message_handlers[topic] = handler

    def get_messages(
        self, device_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        """获取消息历史"""
        messages = self.message_history

        if device_id:
            messages = [m for m in messages if m.get("device_id") == device_id]

        return messages[-limit:]

    def get_device_metrics(self, device_id: str) -> Dict[str, Any]:
        """获取设备指标"""
        device = self.get_device(device_id)
        if not device:
            return {}

        device_messages = self.get_messages(device_id, limit=100)

        # 计算基本指标
        return {
            "device_id": device_id,
            "status": device.get("status"),
            "last_active": device.get("last_active"),
            "message_count": len(device_messages),
            "registered_at": device.get("registered_at"),
        }


# 全局MQTT服务
_mqtt_service: Optional[MQTTSevice] = None


def get_mqtt_service() -> MQTTSevice:
    """获取MQTT服务实例"""
    global _mqtt_service
    if _mqtt_service is None:
        _mqtt_service = MQTTSevice()
    return _mqtt_service
