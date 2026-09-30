"""
工业协议转换服务
提供Modbus/OPC-UA/S7协议适配、工业设备接入
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ProtocolType(str, Enum):
    """协议类型"""

    MODBUS_TCP = "modbus_tcp"
    MODBUS_RTU = "modbus_rtu"
    OPC_UA = "opc_ua"
    S7 = "s7"
    MQTT = "mqtt"
    HTTP = "http"


class DeviceStatus(str, Enum):
    """设备状态"""

    ONLINE = "online"
    OFFLINE = "offline"
    FAULT = "fault"
    UNKNOWN = "unknown"


class DataPoint:
    """数据点"""

    def __init__(
        self,
        name: str,
        address: str,
        data_type: str,
        unit: str = None,
        scale: float = 1.0,
    ):
        self.id = f"dp_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.address = address
        self.data_type = data_type  # int, float, bool, string
        self.unit = unit
        self.scale = scale
        self.last_value = None
        self.last_updated = None


class IndustrialDevice:
    """工业设备"""

    def __init__(
        self,
        name: str,
        protocol: str,
        host: str = None,
        port: int = None,
        device_id: str = None,
    ):
        self.id = device_id or f"device_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.protocol = protocol
        self.host = host
        self.port = port
        self.status = DeviceStatus.UNKNOWN
        self.data_points: List[DataPoint] = []
        self.metadata: Dict = {}
        self.last_heartbeat = None
        self.created_at = datetime.now().isoformat()


class ProtocolMapping:
    """协议映射"""

    def __init__(
        self, source_protocol: str, target_protocol: str, mapping_config: Dict
    ):
        self.id = f"mapping_{uuid.uuid4().hex[:12]}"
        self.source_protocol = source_protocol
        self.target_protocol = target_protocol
        self.mapping_config = mapping_config
        self.enabled = True


class IndustrialProtocolService:
    """工业协议服务"""

    def __init__(self):
        self.devices: Dict[str, IndustrialDevice] = {}
        self.mappings: Dict[str, ProtocolMapping] = {}
        self.data_buffer: Dict[str, List[Dict]] = {}  # device_id -> data
        self._init_default_devices()

    def _init_default_devices(self):
        """初始化示例设备"""
        # 除尘系统设备
        dust_device = IndustrialDevice(
            name="除尘系统控制器",
            protocol=ProtocolType.S7,
            host="192.168.1.100",
            port=102,
            device_id="dust_controller_001",
        )
        dust_device.data_points = [
            DataPoint("风机转速", "DB1.DBD0", "int", "rpm", 1.0),
            DataPoint("电机功率", "DB1.DBD4", "float", "kW", 0.1),
            DataPoint("除尘效率", "DB1.DBD8", "float", "%", 0.1),
            DataPoint("压差", "DB1.DBD12", "float", "Pa", 0.1),
            DataPoint("运行状态", "DB1.DBX16.0", "bool", "", 1.0),
        ]
        self.devices[dust_device.id] = dust_device

        # 光伏逆变器
        pv_device = IndustrialDevice(
            name="光伏逆变器",
            protocol=ProtocolType.MODBUS_TCP,
            host="192.168.1.101",
            port=502,
            device_id="pv_inverter_001",
        )
        pv_device.data_points = [
            DataPoint("直流电压", "40001", "float", "V", 0.1),
            DataPoint("直流电流", "40003", "float", "A", 0.1),
            DataPoint("交流功率", "40005", "float", "kW", 0.1),
            DataPoint("日发电量", "40013", "float", "kWh", 0.1),
            DataPoint("效率", "40015", "float", "%", 0.1),
        ]
        self.devices[pv_device.id] = pv_device

    # ========== 设备管理 ==========

    def register_device(
        self,
        name: str,
        protocol: str,
        host: str = None,
        port: int = None,
        device_id: str = None,
        metadata: Dict = None,
    ) -> IndustrialDevice:
        """注册设备"""
        device = IndustrialDevice(name, protocol, host, port, device_id)
        device.metadata = metadata or {}
        self.devices[device.id] = device
        self.data_buffer[device.id] = []
        return device

    def unregister_device(self, device_id: str) -> bool:
        """注销设备"""
        if device_id in self.devices:
            del self.devices[device_id]
            if device_id in self.data_buffer:
                del self.data_buffer[device_id]
            return True
        return False

    def get_device(self, device_id: str) -> Optional[IndustrialDevice]:
        """获取设备"""
        return self.devices.get(device_id)

    def list_devices(self, protocol: str = None, status: str = None) -> List[Dict]:
        """列出设备"""
        devices = self.devices.values()
        if protocol:
            devices = [d for d in devices if d.protocol == protocol]
        if status:
            devices = [d for d in devices if d.status == status]

        return [
            {
                "id": d.id,
                "name": d.name,
                "protocol": d.protocol,
                "host": d.host,
                "port": d.port,
                "status": d.status,
                "data_points_count": len(d.data_points),
                "last_heartbeat": d.last_heartbeat,
                "created_at": d.created_at,
            }
            for d in devices
        ]

    def update_device_status(self, device_id: str, status: str) -> bool:
        """更新设备状态"""
        device = self.devices.get(device_id)
        if not device:
            return False
        device.status = status
        device.last_heartbeat = datetime.now().isoformat()
        return True

    # ========== 数据点管理 ==========

    def add_data_point(
        self,
        device_id: str,
        name: str,
        address: str,
        data_type: str,
        unit: str = None,
        scale: float = 1.0,
    ) -> Optional[DataPoint]:
        """添加数据点"""
        device = self.devices.get(device_id)
        if not device:
            return None

        dp = DataPoint(name, address, data_type, unit, scale)
        device.data_points.append(dp)
        return dp

    def remove_data_point(self, device_id: str, data_point_id: str) -> bool:
        """移除数据点"""
        device = self.devices.get(device_id)
        if not device:
            return False

        device.data_points = [dp for dp in device.data_points if dp.id != data_point_id]
        return True

    def get_data_points(self, device_id: str) -> List[Dict]:
        """获取设备数据点"""
        device = self.devices.get(device_id)
        if not device:
            return []

        return [
            {
                "id": dp.id,
                "name": dp.name,
                "address": dp.address,
                "data_type": dp.data_type,
                "unit": dp.unit,
                "scale": dp.scale,
                "last_value": dp.last_value,
                "last_updated": dp.last_updated,
            }
            for dp in device.data_points
        ]

    # ========== 数据采集 ==========

    def read_data(self, device_id: str, data_point_ids: List[str] = None) -> Dict:
        """读取数据（使用struct模拟Modbus协议二进制打包/解包）"""
        import struct
        import hashlib

        device = self.devices.get(device_id)
        if not device:
            return {"error": "设备不存在"}

        # 模拟读取数据 — 使用struct.pack/unpack模拟Modbus寄存器读取
        result = {}
        for dp in device.data_points:
            if data_point_ids and dp.id not in data_point_ids:
                continue

            # 基于设备ID、数据点地址和时间生成确定性"传感器种子"
            seed_bytes = hashlib.md5(
                f"{device_id}:{dp.address}:{datetime.now().strftime('%Y%m%d%H')}"
                .encode()
            ).digest()

            if dp.data_type == "int":
                # 模拟Modbus 16位保持寄存器（Holding Register）
                # 使用struct打包/解包模拟二进制传输
                raw_value = (seed_bytes[0] << 8 | seed_bytes[1]) % 10000
                if raw_value < 100:
                    raw_value += 100  # 保证有意义的范围内
                packed = struct.pack(">H", raw_value)
                value = struct.unpack(">H", packed)[0]

            elif dp.data_type == "float":
                # 模拟Modbus 32位浮点数（两个连续寄存器 IEEE 754）
                # 生成0-100范围的浮点数
                raw_float = (seed_bytes[2] << 8 | seed_bytes[3]) / 655.35
                packed = struct.pack(">f", raw_float)
                value = round(struct.unpack(">f", packed)[0], 2)

            elif dp.data_type == "bool":
                # 模拟Modbus线圈（Coil）读取
                raw_bool = bool(seed_bytes[4] & 0x01)
                packed = struct.pack(">?", raw_bool)
                value = struct.unpack(">?", packed)[0]

            else:
                value = "unknown"

            # 应用缩放
            if dp.scale != 1.0 and isinstance(value, (int, float)):
                value = value * dp.scale

            dp.last_value = value
            dp.last_updated = datetime.now().isoformat()
            result[dp.name] = value

        # 缓存数据
        if device_id in self.data_buffer:
            self.data_buffer[device_id].append(
                {"timestamp": datetime.now().isoformat(), "data": result}
            )
            # 保留最近1000条
            if len(self.data_buffer[device_id]) > 1000:
                self.data_buffer[device_id] = self.data_buffer[device_id][-1000:]

        return result

    def write_data(self, device_id: str, data_point_id: str, value: Any) -> bool:
        """写入数据（控制）"""
        device = self.devices.get(device_id)
        if not device:
            return False

        # 查找数据点
        for dp in device.data_points:
            if dp.id == data_point_id:
                # 模拟写入
                dp.last_value = value
                dp.last_updated = datetime.now().isoformat()
                return True

        return False

    def get_historical_data(
        self,
        device_id: str,
        start_time: str = None,
        end_time: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """获取历史数据"""
        buffer = self.data_buffer.get(device_id, [])
        if not buffer:
            return []

        return buffer[-limit:]

    # ========== 协议映射 ==========

    def create_mapping(
        self, source_protocol: str, target_protocol: str, mapping_config: Dict
    ) -> ProtocolMapping:
        """创建协议映射"""
        mapping = ProtocolMapping(source_protocol, target_protocol, mapping_config)
        self.mappings[mapping.id] = mapping
        return mapping

    def execute_mapping(self, mapping_id: str, source_data: Dict) -> Optional[Dict]:
        """执行协议转换"""
        mapping = self.mappings.get(mapping_id)
        if not mapping or not mapping.enabled:
            return None

        # 简单映射逻辑
        result = {}
        for target_key, source_key in mapping.mapping_config.items():
            if source_key in source_data:
                result[target_key] = source_data[source_key]

        return result

    def list_mappings(self) -> List[Dict]:
        """列出协议映射"""
        return [
            {
                "id": m.id,
                "source_protocol": m.source_protocol,
                "target_protocol": m.target_protocol,
                "enabled": m.enabled,
            }
            for m in self.mappings.values()
        ]

    # ========== 统计 ==========

    def get_protocol_stats(self) -> Dict:
        """获取协议统计"""
        total_devices = len(self.devices)
        online = sum(
            1 for d in self.devices.values() if d.status == DeviceStatus.ONLINE
        )
        offline = sum(
            1 for d in self.devices.values() if d.status == DeviceStatus.OFFLINE
        )

        protocols = {}
        for d in self.devices.values():
            protocols[d.protocol] = protocols.get(d.protocol, 0) + 1

        total_data_points = sum(len(d.data_points) for d in self.devices.values())

        return {
            "total_devices": total_devices,
            "online_devices": online,
            "offline_devices": offline,
            "protocols": protocols,
            "total_data_points": total_data_points,
            "total_mappings": len(self.mappings),
        }


# 全局实例
_protocol_service: Optional[IndustrialProtocolService] = None


def get_industrial_protocol_service() -> IndustrialProtocolService:
    """获取工业协议服务"""
    global _protocol_service
    if _protocol_service is None:
        _protocol_service = IndustrialProtocolService()
    return _protocol_service
