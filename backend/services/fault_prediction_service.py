"""
设备故障预测服务
提供异常检测、故障预警、寿命预测、健康评估
"""
import random
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class FaultType(str, Enum):
    """故障类型"""

    ABNORMAL_VIBRATION = "abnormal_vibration"
    OVERHEATING = "overheating"
    BEARING_WEAR = "bearing_wear"
    FILTER_DEGRADATION = "filter_degradation"
    MOTOR_FAILURE = "motor_failure"
    ELECTRICAL_FAULT = "electrical_fault"


class HealthStatus(str, Enum):
    """健康状态"""

    HEALTHY = "healthy"
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    FAULT = "fault"


class Equipment:
    """设备"""

    def __init__(self, name: str, equipment_type: str, location: str = None):
        self.id = f"equip_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.equipment_type = equipment_type
        self.location = location
        self.health_status = HealthStatus.HEALTHY
        self.health_score = 100
        self.install_date = datetime.now().isoformat()
        self.last_maintenance = datetime.now().isoformat()


class FaultPrediction:
    """故障预测"""

    def __init__(self, equipment_id: str, prediction_type: str):
        self.id = f"pred_{uuid.uuid4().hex[:12]}"
        self.equipment_id = equipment_id
        self.prediction_type = prediction_type  # fault, remaining_life
        self.predictions: List[Dict] = []
        self.risk_level = "low"
        self.estimated_failure_date = None
        self.created_at = datetime.now().isoformat()


class AnomalyRecord:
    """异常记录"""

    def __init__(
        self, equipment_id: str, anomaly_type: str, severity: str, description: str
    ):
        self.id = f"anomaly_{uuid.uuid4().hex[:12]}"
        self.equipment_id = equipment_id
        self.anomaly_type = anomaly_type
        self.severity = severity
        self.description = description
        self.status = "open"
        self.detected_at = datetime.now().isoformat()
        self.resolved_at = None


class FaultPredictionService:
    """设备故障预测服务"""

    def __init__(self):
        self.equipment: Dict[str, Equipment] = {}
        self.predictions: Dict[str, FaultPrediction] = {}
        self.anomalies: Dict[str, AnomalyRecord] = {}
        self.sensor_data: Dict[str, List[Dict]] = {}
        self._init_default_equipment()

    def _init_default_equipment(self):
        """初始化示例设备"""
        equipment_list = [
            ("除尘风机", "dust_collector", "车间A"),
            ("空压机组", "air_compressor", "车间B"),
            ("水泵", "water_pump", "车间A"),
            ("电机", "motor", "车间C"),
        ]
        for name, etype, location in equipment_list:
            equip = Equipment(name, etype, location)
            self.equipment[equip.id] = equip

    # ========== 设备管理 ==========

    def register_equipment(
        self, name: str, equipment_type: str, location: str = None
    ) -> Equipment:
        """注册设备"""
        equip = Equipment(name, equipment_type, location)
        self.equipment[equip.id] = equip
        return equip

    def get_equipment(self, equipment_id: str) -> Optional[Equipment]:
        """获取设备"""
        return self.equipment.get(equipment_id)

    def list_equipment(self, equipment_type: str = None) -> List[Dict]:
        """列出设备"""
        equip_list = self.equipment.values()
        if equipment_type:
            equip_list = [e for e in equip_list if e.equipment_type == equipment_type]

        return [
            {
                "id": e.id,
                "name": e.name,
                "equipment_type": e.equipment_type,
                "location": e.location,
                "health_status": e.health_status,
                "health_score": e.health_score,
                "install_date": e.install_date,
                "last_maintenance": e.last_maintenance,
            }
            for e in equip_list
        ]

    # ========== 健康评估 ==========

    def assess_health(self, equipment_id: str) -> Dict:
        """健康评估"""
        equip = self.equipment.get(equipment_id)
        if not equip:
            return {"error": "设备不存在"}

        from services.sensor_data import generate as sensor

        # 使用物理模型生成传感器数据（非纯随机）
        sd = sensor.equipment_health()
        vibration = sd["vibration_mm_s"]
        temperature = sd["temperature_celsius"]
        pressure = sd["pressure_mpa"]
        current = sd["current_a"]
        score = sd["health_score"]

        # 确定健康状态
        if score >= 90:
            status = HealthStatus.HEALTHY
        elif score >= 70:
            status = HealthStatus.NORMAL
        elif score >= 50:
            status = HealthStatus.WARNING
        elif score >= 30:
            status = HealthStatus.CRITICAL
        else:
            status = HealthStatus.FAULT

        equip.health_score = score
        equip.health_status = status

        return {
            "equipment_id": equipment_id,
            "health_score": score,
            "health_status": status.value,
            "sensors": {
                "vibration_mm_s": round(vibration, 2),
                "temperature_celsius": round(temperature, 1),
                "pressure_mpa": round(pressure, 3),
                "current_a": round(current, 1),
            },
            "assessment_time": datetime.now().isoformat(),
        }

    def get_all_health_status(self) -> List[Dict]:
        """获取所有设备健康状态"""
        result = []
        for equip in self.equipment.values():
            health = self.assess_health(equip.id)
            result.append(
                {
                    "id": equip.id,
                    "name": equip.name,
                    "health_score": health["health_score"],
                    "health_status": health["health_status"],
                }
            )
        return result

    # ========== 故障预测 ==========

    def predict_fault(
        self, equipment_id: str, prediction_type: str = "fault"
    ) -> FaultPrediction:
        """故障预测"""
        equip = self.equipment.get(equipment_id)
        if not equip:
            raise ValueError("设备不存在")

        prediction = FaultPrediction(equipment_id, prediction_type)

        # 使用健康评估结果来生成预测
        health = self.assess_health(equipment_id)
        health_score = health["health_score"]

        # 生成预测结果
        if prediction_type == "fault":
            prediction.predictions = self._predict_faults(equipment_id, health_score)
            # 基于健康分数计算风险等级
            if health_score >= 85:
                prediction.risk_level = "low"
            elif health_score >= 65:
                prediction.risk_level = "medium"
            elif health_score >= 45:
                prediction.risk_level = "high"
            else:
                prediction.risk_level = "critical"
                days_until_failure = max(3, int(health_score * 0.3))
                prediction.estimated_failure_date = (
                    datetime.now() + timedelta(days=days_until_failure)
                ).isoformat()

        else:  # remaining_life
            prediction.predictions = self._predict_remaining_life(equipment_id, health_score)

        self.predictions[prediction.id] = prediction
        return prediction

    def _predict_faults(self, equipment_id: str, health_score: float) -> List[Dict]:
        """预测可能的故障（基于健康分数而非纯随机）"""
        faults = []
        fault_types = list(FaultType)

        # 根据健康分数决定故障严重程度
        base_prob = max(0.1, 1 - health_score / 100)
        num_faults = 1 if health_score >= 70 else (2 if health_score >= 45 else 3)
        selected = fault_types[:num_faults]

        for i, fault_type in enumerate(selected):
            prob = min(0.95, base_prob + (0.1 * (2 - i)))
            days = max(3, int(health_score * 0.5 - i * 10))
            faults.append({
                "fault_type": fault_type.value,
                "probability": round(prob, 2),
                "days_until": days,
                "severity": "high" if prob > 0.7 else ("medium" if prob > 0.5 else "low"),
                "recommended_action": _get_recommendation(fault_type.value),
            })

        return faults

    def _predict_remaining_life(self, equipment_id: str, health_score: float) -> List[Dict]:
        """预测剩余寿命（基于健康分数）"""
        return [
            {
                "component": "轴承",
                "remaining_life_days": max(10, int(health_score * 3.5)),
                "confidence": round(min(0.95, health_score / 100 + 0.1), 2),
            },
            {
                "component": "电机",
                "remaining_life_days": max(30, int(health_score * 7)),
                "confidence": round(min(0.90, health_score / 100 + 0.05), 2),
            },
            {
                "component": "过滤器",
                "remaining_life_days": max(7, int(health_score * 0.8)),
                "confidence": round(min(0.95, health_score / 100 + 0.15), 2),
            },
        ]

    def get_prediction(self, prediction_id: str) -> Optional[FaultPrediction]:
        """获取预测结果"""
        return self.predictions.get(prediction_id)

    # ========== 异常检测 ==========

    def detect_anomaly(self, equipment_id: str, sensor_data: Dict) -> AnomalyRecord:
        """检测异常"""
        equip = self.equipment.get(equipment_id)
        if not equip:
            raise ValueError("设备不存在")

        # 简单异常检测规则
        anomalies = []
        if sensor_data.get("vibration", 0) > 7:
            anomalies.append(
                {
                    "type": FaultType.ABNORMAL_VIBRATION,
                    "severity": "high",
                    "description": "振动异常超标",
                }
            )
        if sensor_data.get("temperature", 0) > 70:
            anomalies.append(
                {
                    "type": FaultType.OVERHEATING,
                    "severity": "medium",
                    "description": "温度过高",
                }
            )

        # 如果没有异常，返回None
        if not anomalies:
            return None

        # 记录异常
        anomaly = AnomalyRecord(
            equipment_id=equipment_id,
            anomaly_type=anomalies[0]["type"].value,
            severity=anomalies[0]["severity"],
            description=anomalies[0]["description"],
        )
        self.anomalies[anomaly.id] = anomaly

        return anomaly

    def list_anomalies(
        self, equipment_id: str = None, status: str = None
    ) -> List[Dict]:
        """列出异常"""
        anomalies = self.anomalies.values()
        if equipment_id:
            anomalies = [a for a in anomalies if a.equipment_id == equipment_id]
        if status:
            anomalies = [a for a in anomalies if a.status == status]

        return [
            {
                "id": a.id,
                "equipment_id": a.equipment_id,
                "anomaly_type": a.anomaly_type,
                "severity": a.severity,
                "description": a.description,
                "status": a.status,
                "detected_at": a.detected_at,
                "resolved_at": a.resolved_at,
            }
            for a in anomalies
        ]

    def resolve_anomaly(self, anomaly_id: str) -> bool:
        """解决异常"""
        anomaly = self.anomalies.get(anomaly_id)
        if not anomaly:
            return False
        anomaly.status = "resolved"
        anomaly.resolved_at = datetime.now().isoformat()
        return True

    # ========== 维护建议 ==========

    def get_maintenance_suggestions(self, equipment_id: str) -> Dict:
        """获取维护建议"""
        equip = self.equipment.get(equipment_id)
        if not equip:
            return {"error": "设备不存在"}

        health = self.assess_health(equipment_id)
        suggestions = []

        if health["health_score"] < 50:
            suggestions.append(
                {"priority": "high", "action": "立即安排检修", "reason": "设备健康状态严重"}
            )
        elif health["health_score"] < 70:
            suggestions.append(
                {"priority": "medium", "action": "尽快安排维护", "reason": "设备存在潜在故障风险"}
            )
        else:
            suggestions.append(
                {"priority": "low", "action": "常规维护", "reason": "设备运行正常"}
            )

        # 基于故障预测添加建议
        prediction = self.predict_fault(equipment_id)
        if prediction.risk_level in ["high", "critical"]:
            suggestions.append(
                {
                    "priority": "high",
                    "action": "准备更换备件",
                    "reason": f"预测{ prediction.risk_level }故障风险",
                }
            )

        return {
            "equipment_id": equipment_id,
            "equipment_name": equip.name,
            "current_health_score": health["health_score"],
            "suggestions": suggestions,
        }


def _get_recommendation(fault_type: str) -> str:
    """获取故障处理建议"""
    recommendations = {
        FaultType.ABNORMAL_VIBRATION.value: "检查轴承平衡，必要时更换",
        FaultType.OVERHEATING.value: "检查散热系统，清理灰尘",
        FaultType.BEARING_WEAR.value: "计划更换轴承",
        FaultType.FILTER_DEGRADATION.value: "更换滤袋",
        FaultType.MOTOR_FAILURE.value: "检查电机绕组",
        FaultType.ELECTRICAL_FAULT.value: "检查电气连接",
    }
    return recommendations.get(fault_type, "请专业人员检修")


# 全局实例
_fault_service: Optional[FaultPredictionService] = None


def get_fault_prediction_service() -> FaultPredictionService:
    """获取故障预测服务"""
    global _fault_service
    if _fault_service is None:
        _fault_service = FaultPredictionService()
    return _fault_service
