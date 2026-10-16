"""
种子数据：从现有算法商店条目创建标准化组件
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.config import SessionLocal
from models.core import Component, ComponentStatus


def build_input_schema(features: list) -> dict:
    return {
        "type": "object",
        "properties": {f: {"type": "number"} for f in features},
        "required": features,
    }


def build_output_schema(output_type: str) -> dict:
    type_map = {
        "continuous": {"type": "number", "description": "连续值输出"},
        "regression": {"type": "number", "description": "回归预测值"},
        "anomaly_detection": {"type": "object", "properties": {"anomaly_score": {"type": "number"}, "is_anomaly": {"type": "boolean"}}},
        "classification": {"type": "object", "properties": {"class": {"type": "integer"}, "probabilities": {"type": "array", "items": {"type": "number"}}}},
    }
    return type_map.get(output_type, {"type": "string"})


# Starter components derived from ALGORITHM_STORE
COMPONENTS = [
    {
        "name": "除尘系统能效优化",
        "type": "energy_optimization",
        "description": "基于深度强化学习的除尘系统能效优化，根据生产负荷动态调节风机转速",
        "input_features": ["production_load", "dust_concentration", "filter_pressure_diff", "temperature"],
        "output_type": "continuous",
        "parameters": {"algorithm": "DQN", "framework": "PyTorch", "energy_save": "25%"},
    },
    {
        "name": "脱硫脱硝优化",
        "type": "energy_optimization",
        "description": "优化脱硫脱硝工艺参数，降低药剂消耗",
        "input_features": ["so2_concentration", "nox_concentration", "limestone_flow", "slurry_ph"],
        "output_type": "continuous",
        "parameters": {"algorithm": "PPO", "framework": "PyTorch", "cost_reduction": "20%"},
    },
    {
        "name": "空压系统优化",
        "type": "energy_optimization",
        "description": "优化空压机运行策略，降低能耗",
        "input_features": ["air_demand", "pressure", "temperature", "power_consumption"],
        "output_type": "continuous",
        "parameters": {"algorithm": "MPC", "framework": "Scikit-learn", "energy_save": "15%"},
    },
    {
        "name": "储能电站峰谷套利",
        "type": "energy_optimization",
        "description": "预测电价走势，优化充放电策略",
        "input_features": ["electricity_price", "load_forecast", "soc", "weather"],
        "output_type": "continuous",
        "parameters": {"algorithm": "LSTM + MPC", "framework": "PyTorch", "profit_increase": "20%"},
    },
    {
        "name": "滤袋劣化预测",
        "type": "fault_prediction",
        "description": "预测滤袋寿命，优化更换周期",
        "input_features": ["differential_pressure", "temperature", "humidity", "operating_hours"],
        "output_type": "regression",
        "parameters": {"algorithm": "LSTM", "framework": "PyTorch", "accuracy": "92%", "advance_warning": "7 days"},
    },
    {
        "name": "轴承磨损预测",
        "type": "fault_prediction",
        "description": "基于振动信号分析预测轴承磨损",
        "input_features": ["vibration_x", "vibration_y", "temperature", "rpm"],
        "output_type": "anomaly_detection",
        "parameters": {"algorithm": "LSTM Autoencoder", "framework": "PyTorch", "accuracy": "96%"},
    },
    {
        "name": "电机故障诊断",
        "type": "fault_prediction",
        "description": "诊断电机各种故障类型",
        "input_features": ["current", "voltage", "temperature", "vibration"],
        "output_type": "classification",
        "parameters": {"algorithm": "Transformer", "framework": "PyTorch", "accuracy": "94%"},
    },
    {
        "name": "电力负荷预测",
        "type": "time_series_forecast",
        "description": "预测电力负荷，优化能源调度",
        "input_features": ["historical_load", "temperature", "day_of_week", "hour"],
        "output_type": "regression",
        "parameters": {"algorithm": "XGBoost", "framework": "Scikit-learn", "mape": "3.5%"},
    },
    {
        "name": "光伏功率预测",
        "type": "time_series_forecast",
        "description": "预测光伏发电功率",
        "input_features": ["irradiance", "temperature", "humidity", "cloud_cover"],
        "output_type": "regression",
        "parameters": {"algorithm": "LightGBM", "framework": "Scikit-learn", "rmse": "0.05"},
    },
    {
        "name": "安全帽佩戴检测",
        "type": "computer_vision",
        "description": "检测工厂内人员是否佩戴安全帽",
        "input_features": ["image_data"],
        "output_type": "classification",
        "parameters": {"algorithm": "YOLOv8", "framework": "PyTorch", "map": "0.92", "classes": ["helmet", "no_helmet"]},
    },
    {
        "name": "设备仪表读数识别",
        "type": "computer_vision",
        "description": "自动识别工业仪表读数",
        "input_features": ["image_data"],
        "output_type": "regression",
        "parameters": {"algorithm": "CNN + OCR", "framework": "PyTorch", "accuracy": "98%"},
    },
    {
        "name": "工单文本分类",
        "type": "nlp",
        "description": "自动分类工单类型，提高工单处理效率",
        "input_features": ["text"],
        "output_type": "classification",
        "parameters": {"algorithm": "BERT", "framework": "Transformers", "accuracy": "91%", "classes": ["维修", "巡检", "报修", "咨询"]},
    },
    {
        "name": "情感分析",
        "type": "nlp",
        "description": "分析文本情感倾向",
        "input_features": ["text"],
        "output_type": "classification",
        "parameters": {"algorithm": "BERT", "framework": "Transformers", "accuracy": "92%"},
    },
]


def seed():
    db = SessionLocal()
    try:
        existing = db.query(Component).count()
        if existing > 0:
            print(f"已有 {existing} 个组件，跳过种子数据")
            return

        for item in COMPONENTS:
            comp = Component(
                name=item["name"],
                type=item["type"],
                description=item["description"],
                input_schema=build_input_schema(item["input_features"]),
                output_schema=build_output_schema(item["output_type"]),
                parameters=item["parameters"],
                status=ComponentStatus.PUBLISHED,
            )
            db.add(comp)

        db.commit()
        print(f"成功创建 {len(COMPONENTS)} 个标准化组件")
    except Exception as e:
        db.rollback()
        print(f"种子数据创建失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
