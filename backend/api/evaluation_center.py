"""
评测中心 API — 工业算法基准评测与排行榜
"""
from fastapi import APIRouter, Depends
from database.config import get_db
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/evaluation-center", tags=["评测中心"])

# 预定义评测基线(基于公开数据集的标准期望值)
BASELINES = [
    {"dataset": "ENB2012 能源效率", "task": "regression", "metric": "R²", "baseline": 0.75, "excellent": 0.95},
    {"dataset": "AI4I 预测性维护", "task": "classification", "metric": "accuracy", "baseline": 0.85, "excellent": 0.97},
    {"dataset": "钢铁行业能耗", "task": "regression", "metric": "R²", "baseline": 0.35, "excellent": 0.80},
    {"dataset": "联合循环电厂", "task": "regression", "metric": "R²", "baseline": 0.60, "excellent": 0.90},
    {"dataset": "SECOM 半导体故障", "task": "classification", "metric": "accuracy", "baseline": 0.70, "excellent": 0.93},
    {"dataset": "家电能耗预测", "task": "regression", "metric": "R²", "baseline": 0.40, "excellent": 0.85},
    {"dataset": "住宅建筑数据", "task": "regression", "metric": "R²", "baseline": 0.50, "excellent": 0.88},
    {"dataset": "燃机排放数据", "task": "regression", "metric": "R²", "baseline": 0.55, "excellent": 0.90},
    {"dataset": "城市电力消费", "task": "regression", "metric": "R²", "baseline": 0.45, "excellent": 0.85},
]

DEVICE_BASELINES = {
    "air_compressor": {
        "name": "空压机",
        "efficiency_pct": 78.0,
        "energy_per_unit": 0.12,
        "energy_per_unit_label": "kWh/m³",
        "baseline_power_kw": 75.0,
        "description": "典型工业螺杆空压机，额定排气压力0.7MPa",
    },
    "water_pump": {
        "name": "水泵",
        "efficiency_pct": 75.0,
        "energy_per_unit": 0.05,
        "energy_per_unit_label": "kWh/m³",
        "baseline_power_kw": 55.0,
        "description": "典型离心水泵，扬程50m",
    },
    "fan": {
        "name": "风机",
        "efficiency_pct": 72.0,
        "energy_per_unit": 0.03,
        "energy_per_unit_label": "kWh/m³",
        "baseline_power_kw": 45.0,
        "description": "典型工业离心风机，风压2000Pa",
    },
    "heating_furnace": {
        "name": "加热炉",
        "efficiency_pct": 82.0,
        "energy_per_unit": 0.80,
        "energy_per_unit_label": "kWh/kg",
        "baseline_power_kw": 120.0,
        "description": "典型工业电阻加热炉，工作温度800°C",
    },
    "boiler": {
        "name": "锅炉",
        "efficiency_pct": 85.0,
        "energy_per_unit": 0.70,
        "energy_per_unit_label": "kWh/kg-steam",
        "baseline_power_kw": 200.0,
        "description": "典型工业燃气蒸汽锅炉，蒸汽压力1.0MPa",
    },
    "chiller": {
        "name": "制冷机",
        "efficiency_pct": 70.0,
        "energy_per_unit": 0.60,
        "energy_per_unit_label": "kWh/ton",
        "baseline_power_kw": 150.0,
        "description": "典型工业离心式冷水机组，COP 5.0",
    },
    "transformer": {
        "name": "变压器",
        "efficiency_pct": 97.5,
        "energy_per_unit": 0.01,
        "energy_per_unit_label": "kWh/kVA",
        "baseline_power_kw": 500.0,
        "description": "典型油浸式电力变压器，额定容量1000kVA",
    },
    "motor": {
        "name": "电机",
        "efficiency_pct": 90.0,
        "energy_per_unit": 0.90,
        "energy_per_unit_label": "kWh/hp",
        "baseline_power_kw": 30.0,
        "description": "典型三相异步电动机，IE3能效等级",
    },
}


@router.get("/baselines/device/{device_type}")
def get_device_baseline(device_type: str):
    """获取特定设备的能耗基线"""
    device_type_lower = device_type.lower()
    if device_type_lower not in DEVICE_BASELINES:
        return {
            "error": f"未知设备类型: {device_type}。支持: {', '.join(DEVICE_BASELINES.keys())}"
        }
    return DEVICE_BASELINES[device_type_lower]


@router.get("/baselines/devices")
def get_all_device_baselines():
    """获取所有设备能耗基线"""
    return {"total": len(DEVICE_BASELINES), "devices": DEVICE_BASELINES}


@router.get("/baselines")
def get_baselines():
    """获取评测基线"""
    return {"total": len(BASELINES), "baselines": BASELINES}


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    """算法排行榜 — 基于真实训练结果"""
    jobs = db.execute(text(
        "SELECT name, metrics, dataset_id FROM training_jobs WHERE status='COMPLETED' AND metrics IS NOT NULL ORDER BY id DESC"
    )).fetchall()

    entries = []
    seen = set()
    for name, metrics_str, did in jobs:
        if name in seen: continue
        seen.add(name)
        import json
        m = json.loads(metrics_str) if isinstance(metrics_str, str) else (metrics_str or {})
        r2 = m.get("r2", m.get("accuracy"))
        if r2 is None: continue
        entries.append({
            "name": name,
            "dataset_id": did,
            "r2": round(float(r2), 4) if r2 else None,
            "metric": "R²" if "r2" in m else "accuracy",
        })

    entries.sort(key=lambda x: x["r2"] or 0, reverse=True)
    return {"total": len(entries), "leaderboard": entries[:20]}


@router.get("/datasets")
def evaluation_datasets(db: Session = Depends(get_db)):
    """可用评测数据集列表"""
    datasets = db.execute(text(
        "SELECT id, name, row_count, stage FROM datasets WHERE row_count > 100 ORDER BY row_count DESC"
    )).fetchall()

    result = []
    for did, name, rows, stage in datasets:
        # Check if trained
        trained = db.execute(text(
            "SELECT metrics FROM training_jobs WHERE dataset_id=:did AND status='COMPLETED' LIMIT 1"
        ), {"did": did}).fetchone()
        result.append({
            "id": did, "name": name, "rows": rows, "stage": stage,
            "trained": trained is not None,
        })

    return {"total": len(result), "datasets": result}
