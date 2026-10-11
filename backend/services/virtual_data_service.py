"""
虚拟数据生成服务
为示例组织各业务场景生成训练数据和演示数据
"""
import json
import math
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.sensor_data import generate as sensor


class VirtualDataGenerator:
    """虚拟数据生成器"""

    def __init__(self):
        self.generated_data = {}

    # ==================== 光伏数据生成 ====================

    def generate_pv_system_data(self, count: int = 5) -> List[Dict]:
        """生成光伏系统数据"""
        systems = []
        locations = ["北京", "上海", "深圳", "新疆", "甘肃", "青海", "江苏", "浙江"]

        for i in range(count):
            sd = sensor.photovoltaic()
            location_idx = int(sd["irradiance_wm2"]) % len(locations)  # 基于传感器数据确定性选择
            system = {
                "id": f"pv_{uuid.uuid4().hex[:8]}",
                "name": f"光伏电站{i+1}",
                "capacity_kwp": int(sd["power_kw"]),
                "location": locations[location_idx],
                "install_date": (
                    datetime.now() - timedelta(days=int(sd["irradiance_wm2"] * 2))
                ).strftime("%Y-%m-%d"),
                "panel_count": int(sd["irradiance_wm2"] * 2.5),
                "status": "running" if sd["power_kw"] > 200 else "maintenance",
                "created_at": datetime.now().isoformat(),
            }
            systems.append(system)
        return systems

    def generate_pv_power_data(self, system_id: str, days: int = 30) -> List[Dict]:
        """生成光伏功率数据（时序数据 — 基于物理模型）"""
        records = []
        base_date = datetime.now() - timedelta(days=days)
        sd = sensor.photovoltaic()

        for day in range(days):
            date = base_date + timedelta(days=day)
            for hour in range(24):
                timestamp = date + timedelta(hours=hour)
                if 6 <= hour <= 18:
                    solar_hour = hour - 6
                    base_power = math.sin(solar_hour / 12 * math.pi)
                    # 使用传感器辐照度作为天气因子
                    weather_factor = sd["irradiance_wm2"] / 950
                    power = base_power * weather_factor * sd["power_kw"]
                else:
                    power = 0

                record = {
                    "id": f"pv_power_{uuid.uuid4().hex[:8]}",
                    "system_id": system_id,
                    "timestamp": timestamp.isoformat(),
                    "power_kw": round(power, 2),
                    "irradiance": round(power / 10 * 1.0, 1)
                    if power > 0
                    else 0,
                    "panel_temp": round(25 + (power / 100) * (sd["temperature_c"] * 0.8 / 25), 1)
                    if power > 0
                    else 25,
                    "created_at": datetime.now().isoformat(),
                }
                records.append(record)

        return records

    # ==================== 储能数据生成 ====================

    def generate_storage_system_data(self, count: int = 3) -> List[Dict]:
        """生成储能系统数据"""
        systems = []
        for i in range(count):
            sd = sensor.energy_consumption()
            system = {
                "id": f"storage_{uuid.uuid4().hex[:8]}",
                "name": f"储能系统{i+1}",
                "capacity_kwh": int(sd["consumption_kwh"] * 3),
                "power_kw": int(sd["consumption_kwh"] * 0.8),
                "efficiency": round(sd["efficiency_pct"] / 100, 3),
                "soc": round(sd["efficiency_pct"] * 0.8, 1),
                "status": "running",
                "created_at": datetime.now().isoformat(),
            }
            systems.append(system)
        return systems

    def generate_storage_operation_data(
        self, system_id: str, days: int = 30
    ) -> List[Dict]:
        """生成储能运行数据"""
        records = []
        base_date = datetime.now() - timedelta(days=days)
        sd = sensor.energy_consumption()

        for day in range(days):
            date = base_date + timedelta(days=day)
            for hour in range(24):
                timestamp = date + timedelta(hours=hour)
                # 模拟充放电策略（夜间充电，白天放电）
                if 0 <= hour < 6:
                    charge_power = sd["consumption_kwh"] * 0.4
                    action = "charging"
                elif 6 <= hour < 22:
                    charge_power = -sd["consumption_kwh"] * 0.3
                    action = "discharging"
                else:
                    charge_power = 0
                    action = "idle"

                record = {
                    "id": f"storage_op_{uuid.uuid4().hex[:8]}",
                    "system_id": system_id,
                    "timestamp": timestamp.isoformat(),
                    "soc": max(20, min(90, 50 + day % 30 + hour % 10)),
                    "charge_power": round(charge_power, 2),
                    "action": action,
                    "electricity_price": 1.2 if action == "discharging" else 0.3,
                    "created_at": datetime.now().isoformat(),
                }
                records.append(record)

        return records

    # ==================== 设备传感器数据生成 ====================

    def generate_equipment_data(self, count: int = 10) -> List[Dict]:
        """生成设备数据"""
        equipment = []
        equipment_types = ["inverter", "motor", "pump", "compressor", "fan"]

        for i in range(count):
            sd = sensor.equipment_health()
            eq_type = equipment_types[int(sd["health_score"]) % len(equipment_types)]  # 基于传感器数据确定性选择
            eq = {
                "id": f"eq_{uuid.uuid4().hex[:8]}",
                "name": f"设备{i+1}-{eq_type}",
                "type": eq_type,
                "location": f"车间{int(sd['health_score'] / 20) + 1}",
                "status": "running" if sd["health_score"] > 80 else ("fault" if sd["health_score"] < 60 else "idle"),
                "health_score": round(sd["health_score"], 1),
                "install_date": (
                    datetime.now() - timedelta(days=int(sd["current_a"] * 7))
                ).strftime("%Y-%m-%d"),
                "created_at": datetime.now().isoformat(),
            }
            equipment.append(eq)
        return equipment

    def generate_sensor_data(self, equipment_id: str, hours: int = 168) -> List[Dict]:
        """生成传感器数据（7天，每小时一条 — 基于物理模型）"""
        records = []
        base_time = datetime.now() - timedelta(hours=hours)
        sd = sensor.equipment_health()

        base_temp = sd["temperature_celsius"] * 0.8
        base_vibration = sd["vibration_mm_s"] * 0.5
        base_current = sd["current_a"] * 0.8
        base_voltage = 380

        for i in range(hours):
            timestamp = base_time + timedelta(hours=i)
            sd_i = sensor.equipment_health()
            # 添加物理波动
            temp = base_temp + (sd_i["temperature_celsius"] - sd["temperature_celsius"]) * 0.2
            vibration = base_vibration + (sd_i["vibration_mm_s"] - sd["vibration_mm_s"]) * 0.2
            current = base_current + (sd_i["current_a"] - sd["current_a"]) * 0.2
            voltage = base_voltage + (sd_i["pressure_mpa"] - sd["pressure_mpa"]) * 10

            # 偶尔添加异常（故障前兆，基于健康分数）
            if sd_i["health_score"] < 60:
                temp += sd_i["temperature_celsius"] * 0.15
                vibration += sd_i["vibration_mm_s"] * 0.5

            record = {
                "id": f"sensor_{uuid.uuid4().hex[:8]}",
                "equipment_id": equipment_id,
                "timestamp": timestamp.isoformat(),
                "temperature": round(temp, 1),
                "vibration": round(vibration, 2),
                "current": round(current, 1),
                "voltage": round(voltage, 1),
                "pressure": round(sd_i["pressure_mpa"], 2),
                "rpm": int(1440 + sd_i["vibration_mm_s"] * 6),
                "created_at": datetime.now().isoformat(),
            }
            records.append(record)

        return records

    # ==================== 能耗数据生成 ====================

    def generate_energy_consumption_data(
        self, entity_count: int = 5, days: int = 90
    ) -> List[Dict]:
        """生成能耗数据"""
        records = []
        entity_types = ["workshop", "production_line", "equipment"]
        energy_types = ["electricity", "natural_gas", "steam", "coal"]
        base_date = datetime.now() - timedelta(days=days)

        for entity in range(entity_count):
            entity_id = f"entity_{entity+1}"
            entity_type = entity_types[entity % len(entity_types)]  # 基于索引确定性选择
            sd = sensor.energy_consumption()

            for day in range(days):
                date = base_date + timedelta(days=day)
                is_workday = date.weekday() < 5
                base_consumption = sd["consumption_kwh"] if is_workday else sd["consumption_kwh"] * 0.2

                for energy_type in energy_types:
                    type_factor = {
                        "electricity": 1.0,
                        "natural_gas": 0.8,
                        "steam": 0.6,
                        "coal": 0.5,
                    }.get(energy_type, 1.0)
                    consumption = base_consumption * type_factor

                    record = {
                        "id": f"energy_{uuid.uuid4().hex[:8]}",
                        "entity_id": entity_id,
                        "entity_type": entity_type,
                        "energy_type": energy_type,
                        "consumption": round(consumption, 2),
                        "unit": {
                            "electricity": "kWh", "natural_gas": "m³",
                            "steam": "t", "coal": "t",
                        }.get(energy_type, "kWh"),
                        "date": date.strftime("%Y-%m-%d"),
                        "cost": round(consumption * sd["efficiency_pct"] / 60, 2),
                        "created_at": datetime.now().isoformat(),
                    }
                    records.append(record)

        return records

    # ==================== 碳排放数据生成 ====================

    def generate_carbon_emission_data(
        self, entity_count: int = 5, days: int = 90
    ) -> List[Dict]:
        """生成碳排放数据"""
        records = []
        scopes = ["scope1", "scope2", "scope3"]
        sources = ["电力消耗", "天然气消耗", "车辆燃油", "生产工艺"]
        base_date = datetime.now() - timedelta(days=days)

        for entity in range(entity_count):
            entity_id = f"entity_{entity+1}"
            sc = sensor.carbon_emission()

            for day in range(days):
                date = base_date + timedelta(days=day)
                for scope, source in zip(scopes, sources):
                    emission = sc["total_emission_tco2"] / 10

                    record = {
                        "id": f"carbon_{uuid.uuid4().hex[:8]}",
                        "entity_id": entity_id,
                        "scope": scope,
                        "source": source,
                        "emission_tco2": round(emission, 3),
                        "date": date.strftime("%Y-%m-%d"),
                        "activity_data": round(emission * 1.5, 2),
                        "unit": "tCO2",
                        "created_at": datetime.now().isoformat(),
                    }
                    records.append(record)

        return records

    # ==================== 负荷预测数据生成 ====================

    def generate_load_forecast_data(
        self, entity_id: str = "main_grid", days: int = 90
    ) -> List[Dict]:
        """生成负荷预测数据"""
        records = []
        base_date = datetime.now() - timedelta(days=days)

        for day in range(days):
            date = base_date + timedelta(days=day)
            is_workday = date.weekday() < 5
            base_load = 800 if is_workday else 400
            lf = sensor.load_forecasting()

            for hour in range(24):
                timestamp = date + timedelta(hours=hour)
                if 0 <= hour < 6:
                    hour_factor = 0.6
                elif 6 <= hour < 9:
                    hour_factor = 1.2
                elif 9 <= hour < 12:
                    hour_factor = 1.0
                elif 12 <= hour < 14:
                    hour_factor = 0.9
                elif 14 <= hour < 18:
                    hour_factor = 1.1
                elif 18 <= hour < 22:
                    hour_factor = 1.3
                else:
                    hour_factor = 0.8

                load = base_load * hour_factor * (lf["load_kw"] / 400)

                record = {
                    "id": f"load_{uuid.uuid4().hex[:8]}",
                    "entity_id": entity_id,
                    "timestamp": timestamp.isoformat(),
                    "load_kw": round(load, 2),
                    "temperature": round(lf["temperature_c"], 1),
                    "humidity": round(lf["temperature_c"] * 2.5, 1),
                    "is_workday": is_workday,
                    "created_at": datetime.now().isoformat(),
                }
                records.append(record)

        return records

    # ==================== 除尘系统数据生成 ====================

    def generate_dust_system_data(self, count: int = 3) -> List[Dict]:
        """生成除尘系统数据"""
        systems = []
        for i in range(count):
            sd = sensor.dust_optimization()
            system = {
                "id": f"dust_{uuid.uuid4().hex[:8]}",
                "name": f"除尘系统{i+1}",
                "location": f"车间{i+1}",
                "capacity_m3h": int(sd["fan_speed_rpm"] * 100),
                "fan_power_kw": int(sd["energy_consumption"] * 2),
                "dust_concentration": round(sd["dust_conc_mgm3"], 1),
                "status": "running",
                "created_at": datetime.now().isoformat(),
            }
            systems.append(system)
        return systems

    def generate_dust_operation_data(
        self, system_id: str, days: int = 30
    ) -> List[Dict]:
        """生成除尘系统运行数据"""
        records = []
        base_date = datetime.now() - timedelta(days=days)
        sd = sensor.dust_optimization()

        for day in range(days):
            date = base_date + timedelta(days=day)
            for hour in range(24):
                timestamp = date + timedelta(hours=hour)
                fan_speed = 40 + math.sin(hour / 24 * 2 * math.pi) * 10 + (sd["fan_speed_rpm"] / 100)
                pressure_diff = 2.0 + (fan_speed / 50) * (sd["pressure_drop_pa"] / 1000)
                power = (fan_speed / 50) * sd["energy_consumption"]

                record = {
                    "id": f"dust_op_{uuid.uuid4().hex[:8]}",
                    "system_id": system_id,
                    "timestamp": timestamp.isoformat(),
                    "fan_speed": round(fan_speed, 1),
                    "inlet_pressure": round(-pressure_diff, 2),
                    "outlet_pressure": round(pressure_diff * 0.3, 2),
                    "gas_flow": round(sd["fan_speed_rpm"] * 60, 0),
                    "dust_concentration": round(sd["dust_conc_mgm3"], 1),
                    "power_kw": round(power, 1),
                    "efficiency": round(sd["energy_consumption"] / 50, 3),
                    "created_at": datetime.now().isoformat(),
                }
                records.append(record)

        return records

    # ==================== 生成完整训练数据集 ====================

    def generate_training_datasets(self) -> Dict[str, List[Dict]]:
        """生成用于模型训练的完整数据集"""
        datasets = {}

        # 1. 光伏功率预测数据集
        pv_systems = self.generate_pv_system_data(3)
        pv_power_records = []
        for system in pv_systems:
            pv_power_records.extend(self.generate_pv_power_data(system["id"], 30))
        datasets["pv_power_prediction"] = pv_power_records

        # 2. 故障预测数据集
        equipment = self.generate_equipment_data(10)
        sensor_records = []
        for eq in equipment:
            sensor_records.extend(self.generate_sensor_data(eq["id"], 168))
        datasets["fault_prediction"] = sensor_records

        # 3. 能耗预测数据集
        datasets["energy_consumption"] = self.generate_energy_consumption_data(5, 90)

        # 4. 负荷预测数据集
        datasets["load_forecasting"] = self.generate_load_forecast_data("main_grid", 90)

        # 5. 碳排放数据集
        datasets["carbon_emission"] = self.generate_carbon_emission_data(5, 90)

        # 6. 除尘系统优化数据集
        dust_systems = self.generate_dust_system_data(2)
        dust_records = []
        for system in dust_systems:
            dust_records.extend(self.generate_dust_operation_data(system["id"], 30))
        datasets["dust_optimization"] = dust_records

        # 7. 储能运行数据集
        storage_systems = self.generate_storage_system_data(2)
        storage_records = []
        for system in storage_systems:
            storage_records.extend(
                self.generate_storage_operation_data(system["id"], 30)
            )
        datasets["storage_operation"] = storage_records

        return datasets


# 全局实例
_data_generator: Optional[VirtualDataGenerator] = None


def get_data_generator() -> VirtualDataGenerator:
    """获取数据生成器"""
    global _data_generator
    if _data_generator is None:
        _data_generator = VirtualDataGenerator()
    return _data_generator
