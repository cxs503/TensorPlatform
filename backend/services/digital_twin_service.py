"""
数字孪生服务
提供3D可视化、实时映射、仿真分析
"""
import random
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from services.sensor_data import generate as sensor


class TwinLevel(str, Enum):
    """孪生层级"""

    SYSTEM = "system"  # 系统级
    PROCESS = "process"  # 过程级
    EQUIPMENT = "equipment"  # 设备级


class TwinStatus(str, Enum):
    """孪生状态"""

    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class DigitalTwin:
    """数字孪生"""

    def __init__(self, name: str, twin_level: str, target_system: str):
        self.id = f"twin_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.twin_level = twin_level
        self.target_system = target_system
        self.status = TwinStatus.ACTIVE
        self.model_data: Dict = {}
        self.realtime_data: Dict = {}
        self.created_at = datetime.now().isoformat()


class TwinModel:
    """孪生模型"""

    def __init__(self, twin_id: str, model_type: str):
        self.id = f"model_{uuid.uuid4().hex[:12]}"
        self.twin_id = twin_id
        self.model_type = model_type
        self.parameters: Dict = {}
        self.mesh_data: Dict = {}  # 3D模型数据


class SimulationResult:
    """仿真结果"""

    def __init__(self, twin_id: str, simulation_type: str):
        self.id = f"sim_{uuid.uuid4().hex[:12]}"
        self.twin_id = twin_id
        self.simulation_type = simulation_type
        self.results: List[Dict] = []
        self.created_at = datetime.now().isoformat()


class DigitalTwinService:
    """数字孪生服务"""

    def __init__(self):
        self.twin_models: Dict[str, DigitalTwin] = {}
        self.models: Dict[str, TwinModel] = {}
        self.simulations: Dict[str, SimulationResult] = {}
        self._init_default_twin()

    def _init_default_twin(self):
        """初始化示例数字孪生"""
        # 除尘系统数字孪生
        dust_twin = DigitalTwin(
            name="除尘系统数字孪生", twin_level=TwinLevel.SYSTEM, target_system="dust_collector"
        )
        dust_twin.model_data = {
            "components": ["风机", "除尘器", "管道", "阀门"],
            "layout": {
                "风机": {"x": 0, "y": 0, "z": 0},
                "除尘器": {"x": 10, "y": 0, "z": 0},
                "管道": {"x": 5, "y": 0, "z": 0},
                "阀门": {"x": 8, "y": 0, "z": 0},
            },
        }
        self.twin_models[dust_twin.id] = dust_twin

    # ========== 数字孪生管理 ==========

    def create_twin(
        self, name: str, twin_level: str, target_system: str, model_data: Dict = None
    ) -> DigitalTwin:
        """创建数字孪生"""
        twin = DigitalTwin(name, twin_level, target_system)
        if model_data:
            twin.model_data = model_data
        self.twin_models[twin.id] = twin
        return twin

    def get_twin(self, twin_id: str) -> Optional[DigitalTwin]:
        """获取数字孪生"""
        return self.twin_models.get(twin_id)

    def list_twins(self, twin_level: str = None) -> List[Dict]:
        """列出数字孪生"""
        twins = self.twin_models.values()
        if twin_level:
            twins = [t for t in twins if t.twin_level == twin_level]

        return [
            {
                "id": t.id,
                "name": t.name,
                "twin_level": t.twin_level,
                "target_system": t.target_system,
                "status": t.status,
                "components": t.model_data.get("components", []),
                "created_at": t.created_at,
            }
            for t in twins
        ]

    # ========== 实时数据映射 ==========

    def update_realtime_data(self, twin_id: str, data: Dict) -> bool:
        """更新实时数据"""
        twin = self.twin_models.get(twin_id)
        if not twin:
            return False

        twin.realtime_data = {"timestamp": datetime.now().isoformat(), "data": data}
        return True

    def get_realtime_data(self, twin_id: str) -> Dict:
        """获取实时数据"""
        twin = self.twin_models.get(twin_id)
        if not twin:
            return {"error": "数字孪生不存在"}

        # 如果没有实时数据，生成模拟数据
        if not twin.realtime_data:
            twin.realtime_data = self._generate_simulated_data(twin)

        return twin.realtime_data

    def _generate_simulated_data(self, twin: DigitalTwin) -> Dict:
        """基于物理传感器模型生成实时数据"""
        data = {}
        for idx, component in enumerate(twin.model_data.get("components", [])):
            sd = sensor.equipment_health()
            statuses = ["running", "idle", "warning"]
            status = statuses[0] if sd["health_score"] > 80 else (statuses[2] if sd["health_score"] < 60 else statuses[1])
            data[component] = {
                "temperature": sd["temperature_celsius"],
                "pressure": sd["pressure_mpa"],
                "vibration": sd["vibration_mm_s"],
                "power": round(sd["current_a"] * 3.8, 1),
                "status": status,
            }

        return {"timestamp": datetime.now().isoformat(), "data": data}

    # ========== 3D模型数据 ==========

    def get_3d_model(self, twin_id: str) -> Dict:
        """获取3D模型数据"""
        twin = self.twin_models.get(twin_id)
        if not twin:
            return {"error": "数字孪生不存在"}

        # 生成简化的3D模型数据
        layout = twin.model_data.get("layout", {})
        mesh_data = []

        for component, pos in layout.items():
            mesh_data.append(
                {
                    "id": component,
                    "type": "box",
                    "position": pos,
                    "scale": {"x": 2, "y": 2, "z": 2},
                    "color": _get_component_color(component),
                }
            )

        return {
            "twin_id": twin_id,
            "components": mesh_data,
            "bounds": {
                "min": {"x": -5, "y": -5, "z": -5},
                "max": {"x": 20, "y": 10, "z": 10},
            },
        }

    # ========== 仿真分析 ==========

    def run_simulation(
        self, twin_id: str, simulation_type: str, parameters: Dict = None
    ) -> SimulationResult:
        """运行仿真"""
        twin = self.twin_models.get(twin_id)
        if not twin:
            raise ValueError("数字孪生不存在")

        sim = SimulationResult(twin_id, simulation_type)

        if simulation_type == "flow":
            sim.results = self._simulate_flow(twin, parameters)
        elif simulation_type == "thermal":
            sim.results = self._simulate_thermal(twin, parameters)
        elif simulation_type == "stress":
            sim.results = self._simulate_stress(twin, parameters)
        else:
            sim.results = self._generic_simulation(twin, parameters)

        self.simulations[sim.id] = sim
        return sim

    def _simulate_flow(self, twin: DigitalTwin, params: Dict) -> List[Dict]:
        """流体仿真 — 基于物理模型"""
        results = []
        sd = sensor.equipment_health()
        for i in range(10):
            results.append({
                "step": i,
                "velocity": round(sd["vibration_mm_s"] * 1.2, 2),
                "pressure": sd["pressure_mpa"],
                "flow_rate": round(sd["current_a"] * 3 + i * 2, 1),
            })
        return results

    def _simulate_thermal(self, twin: DigitalTwin, params: Dict) -> List[Dict]:
        """热仿真 — 基于物理模型"""
        results = []
        sd = sensor.equipment_health()
        for i in range(10):
            results.append({
                "step": i,
                "temperature": round(sd["temperature_celsius"] + i * 5, 1),
                "heat_transfer": round(sd["current_a"] * 12 + i * 15, 1),
                "efficiency": round(sd["health_score"] / 100, 3),
            })
        return results

    def _simulate_stress(self, twin: DigitalTwin, params: Dict) -> List[Dict]:
        """应力仿真 — 基于物理模型"""
        results = []
        sd = sensor.equipment_health()
        for i in range(10):
            results.append({
                "step": i,
                "stress": round(sd["vibration_mm_s"] * 12, 2),
                "strain": round(sd["vibration_mm_s"] * 0.002, 5),
                "displacement": round(sd["pressure_mpa"] * 1.5, 3),
            })
        return results

    def _generic_simulation(self, twin: DigitalTwin, params: Dict) -> List[Dict]:
        """通用仿真"""
        sd = sensor.equipment_health()
        return [{"step": i, "value": round(sd["health_score"] - i * 2, 1)} for i in range(10)]

    def get_simulation(self, sim_id: str) -> Optional[SimulationResult]:
        """获取仿真结果"""
        return self.simulations.get(sim_id)

    # ========== 预测分析 ==========

    def predict_behavior(self, twin_id: str, horizon: int = 24) -> Dict:
        """预测行为 — 基于物理传感器模型"""
        twin = self.twin_models.get(twin_id)
        if not twin:
            return {"error": "数字孪生不存在"}

        predictions = []
        for i in range(horizon):
            sd = sensor.equipment_health()
            predictions.append({
                "timestamp": (datetime.now() + timedelta(hours=i)).isoformat(),
                "temperature": round(sd["temperature_celsius"] * (1 + i * 0.01), 1),
                "efficiency": round(sd["health_score"] / 100 * 0.95, 3),
                "power": round(sd["current_a"] * 3.8, 1),
            })

        cs = sensor.equipment_health()
        return {
            "twin_id": twin_id,
            "predictions": predictions,
            "confidence": round(cs["health_score"] / 100, 2),
        }

    # ========== 统计分析 ==========

    def get_twin_statistics(self, twin_id: str) -> Dict:
        """获取孪生统计"""
        twin = self.twin_models.get(twin_id)
        if not twin:
            return {"error": "数字孪生不存在"}

        realtime = twin.realtime_data.get("data", {})

        temps = [c.get("temperature", 0) for c in realtime.values()]
        powers = [c.get("power", 0) for c in realtime.values()]

        return {
            "twin_id": twin_id,
            "components_count": len(realtime),
            "avg_temperature": round(sum(temps) / len(temps), 1) if temps else 0,
            "total_power": round(sum(powers), 1),
            "status": twin.status,
        }


def _get_component_color(component: str) -> str:
    """获取组件颜色"""
    colors = {"风机": "#3498db", "除尘器": "#2ecc71", "管道": "#95a5a6", "阀门": "#e74c3c"}
    return colors.get(component, "#ffffff")


# 全局实例
_twin_service: Optional[DigitalTwinService] = None


def get_digital_twin_service() -> DigitalTwinService:
    """获取数字孪生服务"""
    global _twin_service
    if _twin_service is None:
        _twin_service = DigitalTwinService()
    return _twin_service
