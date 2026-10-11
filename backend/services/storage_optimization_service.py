"""
储能优化调度服务
提供SOC管理、充放电调度、峰谷套利优化策略
"""
import random
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class BatteryStatus(str, Enum):
    """电池状态"""

    CHARGING = "charging"
    DISCHARGING = "discharging"
    IDLE = "idle"
    FAULT = "fault"


class StrategyType(str, Enum):
    """优化策略"""

    PEAK_VALLEY = "peak_valley"  # 峰谷套利
    DEMAND_RESPONSE = "demand_response"  # 需求响应
    RENEWABLE_SMOOTHING = "renewable_smoothing"  # 新能源平滑
    BACKUP_POWER = "backup_power"  # 备用电源


class EnergyStorage:
    """储能系统"""

    def __init__(
        self, name: str, capacity_mwh: float, power_kw: float, efficiency: float = 0.85
    ):
        self.id = f"storage_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.capacity_mwh = capacity_mwh
        self.power_kw = power_kw
        self.efficiency = efficiency
        self.current_soc = 0.5  # 当前SOC 0-1
        self.status = BatteryStatus.IDLE
        self.created_at = datetime.now().isoformat()


class ScheduleRecord:
    """调度记录"""

    def __init__(
        self,
        storage_id: str,
        timestamp: datetime,
        action: str,
        power_kw: float,
        duration_minutes: int,
    ):
        self.id = f"schedule_{uuid.uuid4().hex[:12]}"
        self.storage_id = storage_id
        self.timestamp = timestamp
        self.action = action  # charge, discharge, idle
        self.power_kw = power_kw
        self.duration_minutes = duration_minutes
        self.energy_mwh = power_kw * duration_minutes / 60 / 1000


class OptimizationResult:
    """优化结果"""

    def __init__(self, storage_id: str, strategy: str, schedule: List[Dict]):
        self.id = f"optimize_{uuid.uuid4().hex[:12]}"
        self.storage_id = storage_id
        self.strategy = strategy
        self.schedule = schedule
        self.expected_revenue = 0
        self.created_at = datetime.now().isoformat()


class EnergyStorageService:
    """储能优化调度服务"""

    def __init__(self):
        self.storages: Dict[str, EnergyStorage] = {}
        self.schedules: Dict[str, List[ScheduleRecord]] = {}
        self._init_default_storage()

    def _init_default_storage(self):
        """初始化示例储能系统"""
        storage = EnergyStorage(
            name="园区储能电站", capacity_mwh=10.0, power_kw=5000, efficiency=0.88
        )
        self.storages[storage.id] = storage
        self.schedules[storage.id] = []

    # ========== 储能系统管理 ==========

    def create_storage(
        self, name: str, capacity_mwh: float, power_kw: float, efficiency: float = 0.85
    ) -> EnergyStorage:
        """创建储能系统"""
        storage = EnergyStorage(name, capacity_mwh, power_kw, efficiency)
        self.storages[storage.id] = storage
        self.schedules[storage.id] = []
        return storage

    def get_storage(self, storage_id: str) -> Optional[EnergyStorage]:
        """获取储能系统"""
        return self.storages.get(storage_id)

    def list_storages(self) -> List[Dict]:
        """列出储能系统"""
        return [
            {
                "id": s.id,
                "name": s.name,
                "capacity_mwh": s.capacity_mwh,
                "power_kw": s.power_kw,
                "efficiency": s.efficiency,
                "current_soc": s.current_soc,
                "current_soc_mwh": round(s.capacity_mwh * s.current_soc, 2),
                "status": s.status,
                "created_at": s.created_at,
            }
            for s in self.storages.values()
        ]

    def update_soc(self, storage_id: str, soc: float) -> bool:
        """更新SOC"""
        storage = self.storages.get(storage_id)
        if not storage:
            return False
        storage.current_soc = max(0, min(1, soc))
        return True

    def get_status(self, storage_id: str) -> Dict:
        """获取储能状态"""
        storage = self.storages.get(storage_id)
        if not storage:
            return {"error": "储能系统不存在"}

        available_charge = storage.capacity_mwh * (1 - storage.current_soc)
        available_discharge = (
            storage.capacity_mwh * storage.current_soc * storage.efficiency
        )

        return {
            "id": storage.id,
            "name": storage.name,
            "current_soc": round(storage.current_soc, 3),
            "current_soc_mwh": round(storage.capacity_mwh * storage.current_soc, 2),
            "capacity_mwh": storage.capacity_mwh,
            "available_charge_mwh": round(available_charge, 2),
            "available_discharge_mwh": round(available_discharge, 2),
            "power_kw": storage.power_kw,
            "efficiency": storage.efficiency,
            "status": storage.status,
        }

    # ========== 优化调度 ==========

    def optimize_schedule(
        self,
        storage_id: str,
        strategy: str = StrategyType.PEAK_VALLEY,
        electricity_prices: List[Dict] = None,
    ) -> OptimizationResult:
        """优化调度"""
        storage = self.storages.get(storage_id)
        if not storage:
            raise ValueError("储能系统不存在")

        # 生成优化调度计划
        schedule = self._generate_schedule(storage, strategy, electricity_prices)

        result = OptimizationResult(storage_id, strategy, schedule)
        result.expected_revenue = self._calculate_revenue(schedule)

        return result

    def _generate_schedule(
        self, storage: EnergyStorage, strategy: str, prices: List[Dict]
    ) -> List[Dict]:
        """生成调度计划"""
        schedule = []
        now = datetime.now()

        if strategy == StrategyType.PEAK_VALLEY:
            # 峰谷套利策略
            # 谷时段(0-6点)充电，峰时段(8-11, 18-21)放电
            for hour in range(24):
                timestamp = now + timedelta(hours=hour)

                if 0 <= hour < 6:  # 谷时段 - 充电
                    action = "charge"
                    power = storage.power_kw
                elif 8 <= hour < 11 or 18 <= hour < 21:  # 峰时段 - 放电
                    action = "discharge"
                    power = storage.power_kw * 0.8
                elif 6 <= hour < 8 or 11 <= hour < 18 or 21 <= hour < 24:  # 平时段
                    if storage.current_soc > 0.7:
                        action = "discharge"
                        power = storage.power_kw * 0.3
                    else:
                        action = "charge"
                        power = storage.power_kw * 0.3
                else:
                    action = "idle"
                    power = 0

                schedule.append(
                    {
                        "hour": hour,
                        "timestamp": timestamp.isoformat(),
                        "action": action,
                        "power_kw": power,
                        "estimated_revenue": round(
                            power * 0.1 if action == "discharge" else 0, 2
                        ),
                    }
                )

        elif strategy == StrategyType.DEMAND_RESPONSE:
            # 需求响应策略
            for hour in range(24):
                timestamp = now + timedelta(hours=hour)
                if 9 <= hour < 17:  # 高峰时段放电
                    action = "discharge"
                    power = storage.power_kw * 0.5
                else:
                    action = "charge"
                    power = storage.power_kw * 0.5

                schedule.append(
                    {
                        "hour": hour,
                        "timestamp": timestamp.isoformat(),
                        "action": action,
                        "power_kw": power,
                    }
                )

        return schedule

    def _calculate_revenue(self, schedule: List[Dict]) -> float:
        """计算预期收益"""
        total = 0
        for item in schedule:
            if item["action"] == "discharge":
                # 假设放电收益
                total += item["power_kw"] * 0.1
        return round(total, 2)

    # ========== 实时调度 ==========

    def execute_schedule(
        self, storage_id: str, action: str, power_kw: float, duration_minutes: int = 60
    ) -> Dict:
        """执行调度"""
        storage = self.storages.get(storage_id)
        if not storage:
            return {"error": "储能系统不存在"}

        # 更新状态
        if action == "charge":
            # 充电
            energy_mwh = power_kw * duration_minutes / 60 / 1000
            energy_after_loss = energy_mwh * storage.efficiency
            storage.current_soc = min(
                1, storage.current_soc + energy_after_loss / storage.capacity_mwh
            )
            storage.status = BatteryStatus.CHARGING

            # 记录
            record = ScheduleRecord(
                storage_id, datetime.now(), action, power_kw, duration_minutes
            )
            self.schedules[storage_id].append(record)

        elif action == "discharge":
            # 放电
            energy_mwh = power_kw * duration_minutes / 60 / 1000
            energy_after_loss = energy_mwh / storage.efficiency
            storage.current_soc = max(
                0, storage.current_soc - energy_after_loss / storage.capacity_mwh
            )
            storage.status = BatteryStatus.DISCHARGING

            # 记录
            record = ScheduleRecord(
                storage_id, datetime.now(), action, power_kw, duration_minutes
            )
            self.schedules[storage_id].append(record)

        else:
            storage.status = BatteryStatus.IDLE

        return {
            "action": action,
            "power_kw": power_kw,
            "duration_minutes": duration_minutes,
            "new_soc": round(storage.current_soc, 3),
            "status": storage.status,
        }

    def get_schedule_history(self, storage_id: str, limit: int = 100) -> List[Dict]:
        """获取调度历史"""
        schedules = self.schedules.get(storage_id, [])
        schedules = schedules[-limit:]

        return [
            {
                "id": s.id,
                "timestamp": s.timestamp.isoformat(),
                "action": s.action,
                "power_kw": s.power_kw,
                "duration_minutes": s.duration_minutes,
                "energy_mwh": round(s.energy_mwh, 3),
            }
            for s in schedules
        ]

    # ========== 收益分析 ==========

    def analyze_revenue(self, storage_id: str, days: int = 30) -> Dict:
        """收益分析"""
        storage = self.storages.get(storage_id)
        if not storage:
            return {"error": "储能系统不存在"}

        # 模拟收益数据
        daily_charge = storage.capacity_mwh * 0.8 * storage.efficiency
        daily_discharge = storage.capacity_mwh * 0.8
        cycle_count = 1  # 每天1次循环

        # 峰谷电价差
        peak_price = 1.2  # 元/kWh
        valley_price = 0.3  # 元/kWh

        daily_revenue = (
            daily_discharge * peak_price - daily_charge * valley_price
        ) * 1000

        return {
            "storage_id": storage_id,
            "analysis_period_days": days,
            "daily": {
                "charge_energy_mwh": round(daily_charge, 2),
                "discharge_energy_mwh": round(daily_discharge, 2),
                "cycle_count": cycle_count,
                "revenue": round(daily_revenue, 2),
            },
            "monthly": {
                "total_revenue": round(daily_revenue * days, 2),
                "total_cycles": cycle_count * days,
            },
            "investment": {
                "capacity_mwh": storage.capacity_mwh,
                "power_kw": storage.power_kw,
                "estimated_cost": round(
                    storage.capacity_mwh * 1500000 + storage.power_kw * 800, 0
                ),  # 估算
            },
        }

    # ========== 统计 ==========

    def get_storage_stats(self) -> Dict:
        """获取储能统计"""
        total_capacity = sum(s.capacity_mwh for s in self.storages.values())
        total_power = sum(s.power_kw for s in self.storages.values())

        return {
            "total_storages": len(self.storages),
            "total_capacity_mwh": total_capacity,
            "total_power_kw": total_power,
            "active_storages": sum(
                1
                for s in self.storages.values()
                if s.status in [BatteryStatus.CHARGING, BatteryStatus.DISCHARGING]
            ),
        }


# 全局实例
_storage_service: Optional[EnergyStorageService] = None


def get_storage_service() -> EnergyStorageService:
    """获取储能服务"""
    global _storage_service
    if _storage_service is None:
        _storage_service = EnergyStorageService()
    return _storage_service
