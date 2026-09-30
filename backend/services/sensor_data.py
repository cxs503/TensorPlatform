"""
工业传感器数据生成器 - 生成有物理意义的传感器数据
替代各服务中的 random.uniform() mock 调用
"""
import numpy as np


class SensorDataGenerator:
    """基于物理模型生成工业传感器数据，非纯随机"""

    @staticmethod
    def equipment_health(seed: int = None):
        """设备健康传感器：振动、温度、压力、电流（有相关性）"""
        rng = np.random.RandomState(seed)
        vibration = rng.uniform(0.5, 9.5)
        # 温度与振动正相关
        temperature = 20 + vibration * 5.5 + rng.normal(0, 3)
        # 压力受振动影响
        pressure = 0.7 + vibration * 0.07 + rng.normal(0, 0.05)
        # 电流与负载相关
        current = 10 + vibration * 8 + rng.normal(0, 5)

        # 健康分数：各指标越低越好
        score = 100.0
        score -= min(vibration * 2.0, 25)  # 振动
        score -= min(max(0, temperature - 45) * 0.8, 20)  # 温度
        score -= min(abs(pressure - 1.0) * 30, 15)  # 压力偏离
        score -= min(max(0, current - 60) * 0.5, 15)  # 电流
        score = max(0, min(100, score))

        return {
            "vibration_mm_s": round(vibration, 2),
            "temperature_celsius": round(temperature, 1),
            "pressure_mpa": round(pressure, 3),
            "current_a": round(current, 1),
            "health_score": round(score, 1),
        }

    @staticmethod
    def photovoltaic(seed: int = None):
        """光伏系统传感器：辐照度、温度、湿度、功率"""
        rng = np.random.RandomState(seed)
        irradiance = rng.uniform(100, 950)
        temperature = 15 + irradiance * 0.025 + rng.normal(0, 2)
        humidity = rng.uniform(25, 85)
        efficiency = 0.16 + rng.normal(0, 0.01)
        temp_decay = max(0, 1 - 0.004 * (temperature - 25))
        power = irradiance * efficiency * temp_decay + rng.normal(0, 15)
        energy = power * 0.25
        return {
            "irradiance_wm2": round(irradiance, 0),
            "temperature_c": round(temperature, 1),
            "humidity_pct": round(humidity, 0),
            "power_kw": round(max(0, power), 1),
            "energy_kwh": round(max(0, energy), 2),
        }

    @staticmethod
    def energy_consumption(seed: int = None):
        """能耗传感器：产量、温度、湿度、能耗"""
        rng = np.random.RandomState(seed)
        production = rng.uniform(80, 450)
        temp = rng.uniform(18, 42)
        humidity = rng.uniform(30, 85)
        base = 150
        consumption = base + 1.8 * production + 2.5 * max(0, temp - 25) + rng.normal(0, 40)
        efficiency = production / max(1, consumption) * 100
        return {
            "production_t": round(production, 1),
            "temperature_c": round(temp, 1),
            "humidity_pct": round(humidity, 0),
            "consumption_kwh": round(max(0, consumption), 1),
            "efficiency_pct": round(efficiency, 1),
        }

    @staticmethod
    def load_forecasting(seed: int = None):
        """负荷预测传感器：小时、星期几、温度、负荷"""
        rng = np.random.RandomState(seed)
        hour = rng.randint(0, 24)
        day_of_week = rng.randint(0, 7)
        temp = rng.uniform(12, 38)
        base_load = 400
        # 午间高峰模式
        if 9 <= hour <= 17:
            peak_factor = 1.3 + 0.5 * np.sin(np.pi * (hour - 9) / 8)
        elif 6 <= hour < 9 or 17 < hour <= 21:
            peak_factor = 1.0
        else:
            peak_factor = 0.6
        weekday_bonus = 80 if day_of_week < 5 else 0
        temp_effect = 1.2 * max(0, temp - 28)
        load = base_load * peak_factor + weekday_bonus + temp_effect + rng.normal(0, 25)
        return {
            "hour": hour,
            "day_of_week": day_of_week,
            "temperature_c": round(temp, 1),
            "load_kw": round(max(0, load), 1),
        }

    @staticmethod
    def dust_optimization(seed: int = None):
        """除尘优化传感器：压差、粉尘浓度、转速、温度、能耗"""
        rng = np.random.RandomState(seed)
        dp = rng.uniform(600, 2400)
        dust_conc = rng.uniform(10, 95)
        fan_speed = rng.uniform(550, 1450)
        temp = rng.uniform(22, 78)
        energy = 5 + 0.008 * dp + 0.04 * dust_conc + 0.025 * fan_speed / 10 + 0.015 * temp
        energy += rng.normal(0, 4)
        return {
            "pressure_drop_pa": round(dp, 0),
            "dust_conc_mgm3": round(dust_conc, 1),
            "fan_speed_rpm": round(fan_speed, 0),
            "temperature_c": round(temp, 1),
            "energy_consumption": round(max(0, energy), 1),
        }

    @staticmethod
    def carbon_emission(seed: int = None):
        """碳排放传感器"""
        rng = np.random.RandomState(seed)
        coal = rng.uniform(10, 500)
        gas = rng.uniform(5, 200)
        electricity = rng.uniform(50, 1000)
        emission = coal * 2.6 + gas * 2.0 + electricity * 0.5 + rng.normal(0, 10)
        return {
            "coal_consumption_t": round(coal, 1),
            "gas_consumption_m3": round(gas, 0),
            "electricity_kwh": round(electricity, 0),
            "total_emission_tco2": round(max(0, emission), 2),
        }


# 便捷函数
generate = SensorDataGenerator()
