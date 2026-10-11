"""
负荷预测服务
提供电力负荷预测、负荷曲线分析、峰谷时段识别
"""
import random
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class ForecastModel(str, Enum):
    """预测模型"""

    LSTM = "lstm"
    PROPHET = "prophet"
    ARIMA = "arima"
    XGBOOST = "xgboost"
    ENSEMBLE = "ensemble"


class TimePeriod(str, Enum):
    """时段类型"""

    PEAK = "peak"  # 峰
    VALLEY = "valley"  # 谷
    SHOULDER = "shoulder"  # 平


class LoadForecast:
    """负荷预测"""

    def __init__(
        self, entity_id: str, model: str, forecast_horizon: int, unit: str = "kW"
    ):
        self.id = f"forecast_{uuid.uuid4().hex[:12]}"
        self.entity_id = entity_id
        self.model = model
        self.forecast_horizon = forecast_horizon
        self.unit = unit
        self.predictions: List[Dict] = []
        self.metrics: Dict = {}
        self.created_at = datetime.now().isoformat()


class LoadPattern:
    """负荷模式"""

    def __init__(self, name: str, pattern_type: str, typical_curve: List[float]):
        self.id = f"pattern_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.pattern_type = pattern_type  # weekday, weekend, holiday
        self.typical_curve = typical_curve


class LoadForecastingService:
    """负荷预测服务"""

    def __init__(self):
        self.forecasts: Dict[str, LoadForecast] = {}
        self.patterns: Dict[str, LoadPattern] = []
        self._init_default_patterns()

    def _init_default_patterns(self):
        """初始化典型负荷曲线"""
        # 工作日典型曲线 (24小时)
        weekday = LoadPattern(
            name="工作日",
            pattern_type="weekday",
            typical_curve=[
                0.4,
                0.35,
                0.3,
                0.3,
                0.35,
                0.45,  # 0-5点
                0.6,
                0.75,
                0.9,
                0.95,
                1.0,
                1.0,  # 6-11点
                0.95,
                0.9,
                0.85,
                0.9,
                1.0,
                1.0,  # 12-17点
                0.95,
                0.85,
                0.7,
                0.55,
                0.45,
                0.4,  # 18-23点
            ],
        )
        self.patterns.append(weekday)

        # 周末典型曲线
        weekend = LoadPattern(
            name="周末",
            pattern_type="weekend",
            typical_curve=[
                0.45,
                0.4,
                0.35,
                0.35,
                0.4,
                0.5,
                0.6,
                0.7,
                0.75,
                0.8,
                0.8,
                0.8,
                0.75,
                0.75,
                0.8,
                0.85,
                0.9,
                0.9,
                0.85,
                0.75,
                0.6,
                0.5,
                0.5,
                0.45,
            ],
        )
        self.patterns.append(weekend)

    # ========== 负荷预测 ==========

    def create_forecast(
        self,
        entity_id: str,
        model: str = ForecastModel.ENSEMBLE,
        forecast_horizon: int = 24,
        historical_data: List[Dict] = None,
    ) -> LoadForecast:
        """创建负荷预测"""
        forecast = LoadForecast(entity_id, model, forecast_horizon)

        # 生成预测结果
        forecast.predictions = self._generate_predictions(entity_id, forecast_horizon)

        # 计算预测指标
        forecast.metrics = self._calculate_metrics(forecast.predictions)

        self.forecasts[forecast.id] = forecast
        return forecast

    def _generate_predictions(self, entity_id: str, horizon: int) -> List[Dict]:
        """生成预测（模拟）"""
        predictions = []
        base_load = 5000  # 基础负荷 kW

        # 判断是工作日还是周末
        now = datetime.now()
        is_weekday = now.weekday() < 5

        for i in range(horizon):
            hour = (now.hour + i) % 24

            # 选择对应曲线
            if is_weekday:
                pattern = self.patterns[0].typical_curve[hour]
            else:
                pattern = self.patterns[1].typical_curve[hour]

            # 添加基于温度的波动（非纯随机）
            import numpy as np
            temp_factor = 1 + 0.005 * (np.random.normal(0, 3))  # 温度波动影响
            value = base_load * pattern * max(0.85, min(1.15, temp_factor))

            predictions.append(
                {
                    "timestamp": (now + timedelta(hours=i)).isoformat(),
                    "hour": hour,
                    "predicted_load": round(value, 2),
                    "unit": "kW",
                }
            )

        return predictions

    def _calculate_metrics(self, predictions: List[Dict]) -> Dict:
        """计算预测指标"""
        if not predictions:
            return {}

        loads = [p["predicted_load"] for p in predictions]
        avg_load = sum(loads) / len(loads)
        max_load = max(loads)
        min_load = min(loads)

        return {
            "avg_load": round(avg_load, 2),
            "max_load": round(max_load, 2),
            "min_load": round(min_load, 2),
            "load_factor": round(avg_load / max_load, 3) if max_load > 0 else 0,
        }

    def get_forecast(self, forecast_id: str) -> Optional[LoadForecast]:
        """获取预测"""
        return self.forecasts.get(forecast_id)

    def list_forecasts(self, entity_id: str = None) -> List[Dict]:
        """列出预测"""
        forecasts = self.forecasts.values()
        if entity_id:
            forecasts = [f for f in forecasts if f.entity_id == entity_id]

        return [
            {
                "id": f.id,
                "entity_id": f.entity_id,
                "model": f.model,
                "forecast_horizon": f.forecast_horizon,
                "metrics": f.metrics,
                "created_at": f.created_at,
            }
            for f in forecasts
        ]

    # ========== 峰谷时段识别 ==========

    def identify_peak_valley(self, entity_id: str, date: str = None) -> Dict:
        """识别峰谷时段"""
        if not date:
            date = datetime.now().date()
        else:
            date = datetime.fromisoformat(date).date()

        # 模拟一天各时段
        periods = [
            {"hour": 0, "type": TimePeriod.VALLEY, "price": 0.3},
            {"hour": 6, "type": TimePeriod.SHOULDER, "price": 0.5},
            {"hour": 8, "type": TimePeriod.PEAK, "price": 0.8},
            {"hour": 10, "type": TimePeriod.PEAK, "price": 0.9},
            {"hour": 12, "type": TimePeriod.SHOULDER, "price": 0.6},
            {"hour": 14, "type": TimePeriod.PEAK, "price": 0.85},
            {"hour": 18, "type": TimePeriod.PEAK, "price": 1.0},
            {"hour": 20, "type": TimePeriod.SHOULDER, "price": 0.7},
            {"hour": 22, "type": TimePeriod.VALLEY, "price": 0.35},
        ]

        return {"entity_id": entity_id, "date": date.isoformat(), "periods": periods}

    # ========== 负荷曲线分析 ==========

    def analyze_load_curve(
        self, entity_id: str, start_time: datetime = None, end_time: datetime = None
    ) -> Dict:
        """分析负荷曲线"""
        if not start_time:
            start_time = datetime.now() - timedelta(days=7)
        if not end_time:
            end_time = datetime.now()

        # 模拟负荷曲线数据
        base = 5000
        data_points = []

        current = start_time
        while current <= end_time:
            hour = current.hour
            day_type = "weekday" if current.weekday() < 5 else "weekend"

            # 基于典型日负荷模式计算（非纯随机）
            import numpy as np
            if day_type == "weekday":
                if 8 <= hour < 12 or 14 <= hour < 18:
                    load = base * (1.0 + np.random.normal(0, 0.03))
                elif 18 <= hour < 22:
                    load = base * (0.9 + np.random.normal(0, 0.03))
                else:
                    load = base * (0.4 + np.random.normal(0, 0.05))
            else:
                load = base * (0.65 + np.random.normal(0, 0.05))

            data_points.append(
                {"timestamp": current.isoformat(), "load": round(load, 2)}
            )
            current += timedelta(hours=1)

        # 计算统计
        loads = [p["load"] for p in data_points]
        avg_load = sum(loads) / len(loads)

        return {
            "entity_id": entity_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "data_points": len(data_points),
            "statistics": {
                "avg_load": round(avg_load, 2),
                "max_load": round(max(loads), 2),
                "min_load": round(min(loads), 2),
                "load_variance": round(
                    sum((l - avg_load) ** 2 for l in loads) / len(loads), 2
                ),
            },
        }

    # ========== 需求响应建议 ==========

    def get_demand_response_suggestions(self, entity_id: str) -> Dict:
        """获取需求响应建议"""
        # 基于负荷分析给出建议
        suggestions = []

        # 峰时段建议
        suggestions.append(
            {
                "period": "峰时段 (8-12时, 14-18时, 18-22时)",
                "action": "将非必要生产调整至谷时段",
                "potential_savings": "15-20%",
            }
        )

        # 可再生能源建议
        suggestions.append(
            {
                "period": "日间 (10-16时)",
                "action": "增加光伏自发自用比例",
                "potential_savings": "10-15%",
            }
        )

        # 储能建议
        suggestions.append(
            {
                "period": "谷时段 (0-6时)",
                "action": "储能充电，峰时段放电",
                "potential_savings": "20-30%",
            }
        )

        return {"entity_id": entity_id, "suggestions": suggestions}

    # ========== 统计 ==========

    def get_forecast_stats(self) -> Dict:
        """获取预测统计"""
        return {
            "total_forecasts": len(self.forecasts),
            "models_used": list(set(f.model for f in self.forecasts.values())),
            "patterns": len(self.patterns),
        }


# 全局实例
_load_forecasting_service: Optional[LoadForecastingService] = None


def get_load_forecasting_service() -> LoadForecastingService:
    """获取负荷预测服务"""
    global _load_forecasting_service
    if _load_forecasting_service is None:
        _load_forecasting_service = LoadForecastingService()
    return _load_forecasting_service
