"""
数据加载器 - 从数据库、CSV文件或内存中加载训练数据
"""
import csv
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

UPLOADS_DIR = Path("/app/uploads")
DATASETS_DIR = UPLOADS_DIR / "datasets"


def load_dataset_data(
    db: Session, dataset_id: int, feature_cols: Optional[List[str]] = None,
    target_col: Optional[str] = None,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    从数据集加载特征和标签。
    返回: (X, y, feature_names)
    """
    # 1. 尝试从 CSV 文件加载
    # 用 SQL 直查避免 models.core 重复导入触发 SQLAlchemy 表重定义
    row = db.execute(
        text("SELECT id, name, file_path FROM datasets WHERE id = :did"),
        {"did": dataset_id}
    ).fetchone()
    if not row:
        raise ValueError(f"Dataset {dataset_id} not found")
    
    file_path = row[2]
    if file_path and os.path.exists(file_path):
        df = pd.read_csv(file_path)
    else:
        # 查找 uploads/datasets/ 下的 CSV 文件
        pattern = f"{dataset_id}_*.csv"
        csv_files = sorted(DATASETS_DIR.glob(pattern))
        if csv_files:
            df = pd.read_csv(csv_files[0])
        else:
            raise ValueError(
                f"No data file found for dataset {dataset_id}. "
                f"Upload a CSV file first."
            )

    if len(df) < 10:
        raise ValueError(
            f"Dataset {dataset_id} has only {len(df)} rows. "
            f"Need at least 10 rows for training."
        )

    # 智能选择列
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        raise ValueError(f"Dataset {dataset_id} has no numeric columns")

    # 确定目标列 y
    if target_col and target_col in df.columns:
        y = df[target_col].values
    elif feature_cols:
        raise ValueError(
            f"Dataset {dataset_id}: 指定了 feature_cols 但未提供有效的 target_col"
        )
    elif len(numeric_cols) >= 2:
        # 未指定 target:从后往前找第一个不全为 NaN 的数值列作 target
        target_idx = -1
        for i in range(len(numeric_cols) - 1, -1, -1):
            col = numeric_cols[i]
            if df[col].notna().sum() > 0:
                target_idx = i
                break
        if target_idx < 0:
            raise ValueError(f"Dataset {dataset_id}: all numeric columns are NaN")
        target_col = numeric_cols[target_idx]
        y = df[target_col].values
    else:
        target_col = numeric_cols[0]
        y = df[target_col].values

    # 确定特征列 X
    if feature_cols:
        X_cols = [c for c in feature_cols if c in df.columns and c != target_col]
        if not X_cols:
            raise ValueError(
                f"Dataset {dataset_id}: 指定的特征列均不可用: {feature_cols}"
            )
    else:
        X_cols = [c for c in numeric_cols if c != target_col]
        if not X_cols:
            X_cols = numeric_cols

    # 工业数据哨兵值清洗:将 -999/-9999/9999 等占位值替换为 NaN
    # (这些是工业传感器/SCADA 系统的"缺失数据"标记,直接喂入模型会导致负 R²)
    _SENTINELS = [-999, -9999, 9999, -99999, 99999, -999.0, -9999.0, 9999.0]
    for _col in numeric_cols:
        df[_col] = df[_col].replace(_SENTINELS, np.nan)

    # 去除 target 为 NaN 的行
    y_series = pd.Series(y)
    numeric_y = pd.to_numeric(y_series, errors="coerce")
    non_numeric_mask = y_series.notna() & numeric_y.isna()
    if non_numeric_mask.any():
        invalid_examples = y_series[non_numeric_mask].astype(str).head(3).tolist()
        raise ValueError(
            f"Dataset {dataset_id}: target column {target_col!r} contains non-numeric values "
            f"(examples: {invalid_examples}). Please select a numeric target column."
        )
    y = numeric_y.to_numpy(dtype=np.float64)
    valid_mask = numeric_y.notna() & np.isfinite(numeric_y)
    if not valid_mask.all():
        n_before = len(y)
        X = df[X_cols].loc[valid_mask[valid_mask].index].fillna(0).values.astype(np.float64)
        y = y[valid_mask.values]
        if len(y) < 10:
            raise ValueError(f"Dataset {dataset_id}: only {len(y)} valid rows after dropping NaN")
    else:
        X = df[X_cols].fillna(0).values.astype(np.float64)
    y = np.array(y, dtype=np.float64)

    return X, y, X_cols


def generate_synthetic_data(
    scenario: str, n_samples: int = 1000,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    为特定业务场景生成有意义的合成数据。
    用于 demo/测试，当没有真实数据时 fallback。
    """
    np.random.seed(42)

    if scenario == "pv_power" or scenario == "photovoltaic":
        # 光伏功率预测：辐照度、温度 → 功率
        irradiance = np.random.uniform(0, 1000, n_samples)
        temperature = np.random.uniform(10, 45, n_samples)
        humidity = np.random.uniform(20, 90, n_samples)
        # 功率 ≈ 辐照度 * 效率系数 - 温度衰减
        efficiency = 0.15 + np.random.normal(0, 0.01, n_samples)
        temp_decay = 1 - 0.004 * (temperature - 25)
        power = irradiance * efficiency * temp_decay + np.random.normal(0, 10, n_samples)
        X = np.column_stack([irradiance, temperature, humidity])
        y = power
        feature_names = ["irradiance_wm2", "temperature_c", "humidity_pct"]

    elif scenario == "fault_prediction":
        # 故障预测：振动、温度、压力 → 故障标签(0/1)
        vibration = np.random.uniform(0, 10, n_samples)
        temperature = np.random.uniform(20, 80, n_samples)
        pressure = np.random.uniform(0, 100, n_samples)
        # 故障概率随参数升高
        fault_score = 0.3 * (vibration / 10) + 0.3 * (temperature / 80) + 0.3 * (pressure / 100)
        fault = (fault_score + np.random.normal(0, 0.1, n_samples) > 0.5).astype(float)
        X = np.column_stack([vibration, temperature, pressure])
        y = fault
        feature_names = ["vibration", "temperature_c", "pressure_bar"]

    elif scenario == "energy_consumption":
        # 能耗预测：产量、温度、湿度 → 能耗
        production = np.random.uniform(100, 500, n_samples)
        temp = np.random.uniform(15, 40, n_samples)
        humidity = np.random.uniform(30, 90, n_samples)
        consumption = 200 + 2 * production + 3 * temp + np.random.normal(0, 50, n_samples)
        X = np.column_stack([production, temp, humidity])
        y = consumption
        feature_names = ["production_t", "temperature_c", "humidity_pct"]

    elif scenario == "load_forecasting":
        # 负荷预测：时间特征 → 负荷
        hour = np.tile(np.arange(24), n_samples // 24 + 1)[:n_samples]
        day_of_week = np.random.randint(0, 7, n_samples)
        temp = np.random.uniform(10, 38, n_samples)
        # 负荷模式：工作日高、周末低、午间高峰
        load = 500 + 200 * np.sin(np.pi * (hour - 6) / 12) * (hour >= 6) * (hour <= 18)
        load += 100 * (day_of_week < 5) + 0.5 * temp + np.random.normal(0, 30, n_samples)
        X = np.column_stack([hour, day_of_week, temp])
        y = load
        feature_names = ["hour", "day_of_week", "temperature_c"]

    elif scenario == "dust_optimization":
        # 除尘优化：压差、粉尘浓度、转速、温度 → 能耗
        dp = np.random.uniform(500, 2500, n_samples)       # 压差 Pa
        dust_conc = np.random.uniform(5, 100, n_samples)    # 粉尘浓度 mg/m³
        fan_speed = np.random.uniform(500, 1500, n_samples) # 转速 rpm
        temp = np.random.uniform(20, 80, n_samples)         # 温度
        # 能耗模型
        energy = 0.01 * dp + 0.05 * dust_conc + 0.03 * (fan_speed / 10) + 0.02 * temp
        energy += np.random.normal(0, 5, n_samples)
        X = np.column_stack([dp, dust_conc, fan_speed, temp])
        y = energy
        feature_names = ["pressure_drop_pa", "dust_conc_mgm3", "fan_speed_rpm", "temperature_c"]

    elif scenario == "steel_quality":
        # 钢铁质量预测
        carbon = np.random.uniform(0.05, 0.3, n_samples)
        manganese = np.random.uniform(0.5, 1.5, n_samples)
        temperature = np.random.uniform(800, 1200, n_samples)
        cooling_rate = np.random.uniform(10, 100, n_samples)
        quality = 50 - 100 * carbon + 20 * manganese - 0.05 * temperature - 0.1 * cooling_rate
        quality += np.random.normal(0, 5, n_samples)
        X = np.column_stack([carbon, manganese, temperature, cooling_rate])
        y = quality
        feature_names = ["carbon_pct", "manganese_pct", "temperature_c", "cooling_rate"]

    elif scenario == "sinter_temperature":
        # 生产过程终点温度预测：机速/料层/透气率/压力/烟道温度 → 终点温度
        speed = np.random.uniform(1.5, 2.0, n_samples)
        thickness = np.random.uniform(1100, 1400, n_samples)
        permeability = np.random.uniform(250, 320, n_samples)
        pressure1 = np.random.uniform(-17, -14, n_samples)
        pressure2 = np.random.uniform(-17, -14, n_samples)
        temp15 = np.random.uniform(70, 120, n_samples)
        temp16 = np.random.uniform(60, 100, n_samples)
        temp19 = np.random.uniform(170, 250, n_samples)
        # 终点温度受机速和烟道温度影响最大
        end_temp = 150 + 30 * speed - 0.02 * thickness + 0.15 * permeability
        end_temp += 0.5 * temp15 + 0.3 * temp16 + 0.8 * temp19
        end_temp += np.random.normal(0, 10, n_samples)
        X = np.column_stack([speed, thickness, permeability, pressure1, pressure2, temp15, temp16, temp19])
        y = end_temp
        feature_names = ["speed", "thickness", "permeability", "pressure1", "pressure2", "temp15", "temp16", "temp19"]

    elif scenario == "sinter_feo_real":
        # 按需求文档: 工艺参数优化比例 + 环冷斜率 → FeO含量 (目标 7.5~8.5%)
        n_samples = max(n_samples, 5000)
        # 工艺参数优化比例 = (煤粉 + 焦粉) / (铁料1-10 + 冷返矿)
        coal_coke = np.random.uniform(10, 30, n_samples)
        iron_ore = np.random.uniform(200, 500, n_samples)
        return_ore = np.random.uniform(50, 200, n_samples)
        carbon_ratio = coal_coke / (iron_ore + return_ore)  # ~0.04-0.12
        # 环冷斜率: 罩内温度随时间的变化率 (通常为负，冷却过程)
        cooling_slope = np.random.uniform(-0.5, -0.02, n_samples)
        # FeO = 基础值 + 工艺参数优化影响 + 冷却速度影响 + 噪声 (目标 7.5~8.5%)
        feo = 7.0 + 24 * carbon_ratio + 3.5 * cooling_slope
        feo += np.random.normal(0, 0.3, n_samples)  # 化验误差
        X = np.column_stack([carbon_ratio, cooling_slope])
        y = feo
        feature_names = ["carbon_ratio", "cooling_slope"]

    elif scenario == "sinter_feo":
        # 生产过程工艺参数优化FeO预测：工艺参数优化比例 + 环冷斜率 → FeO含量
        carbon_ratio = np.random.uniform(0.06, 0.12, n_samples)
        cooling_slope = np.random.uniform(-0.3, -0.05, n_samples)
        # FeO含量主要受工艺参数优化比例影响
        feo = 6.0 + 25 * carbon_ratio + 3 * cooling_slope
        feo += np.random.normal(0, 0.5, n_samples)
        X = np.column_stack([carbon_ratio, cooling_slope])
        y = feo
        feature_names = ["carbon_ratio", "cooling_slope"]

    elif scenario == "dust_collector":
        # 除尘器运行数据：产能负荷、粉尘浓度、压差、温度、转速 → 能耗
        production_load = np.random.uniform(50, 200, n_samples)        # 产能负荷 t/h
        dust_conc = np.random.uniform(5, 80, n_samples)                # 粉尘浓度 mg/m³
        pressure_diff = np.random.uniform(200, 1500, n_samples)        # 过滤压差 Pa
        temperature = np.random.uniform(30, 90, n_samples)             # 温度 °C
        fan_speed = np.random.uniform(400, 1400, n_samples)            # 风机转速 rpm
        # 能耗模型：受压差和粉尘浓度影响最大
        energy = 50 + 0.05 * pressure_diff + 0.3 * dust_conc + 0.02 * fan_speed + 0.1 * temperature
        energy += np.random.normal(0, 10, n_samples)
        X = np.column_stack([production_load, dust_conc, pressure_diff, temperature, fan_speed])
        y = energy
        feature_names = ["production_load", "dust_concentration", "filter_pressure_diff", "temperature", "fan_speed"]

    else:
        # 通用场景：生成随机回归数据
        X = np.random.randn(n_samples, 5)
        true_coef = np.array([1.5, -0.8, 2.0, 0.3, -1.2])
        y = X @ true_coef + np.random.normal(0, 0.5, n_samples)
        feature_names = [f"feature_{i}" for i in range(5)]

    return X, y, feature_names


def persist_synthetic_data(
    db: Session,
    scenario: str,
    X: np.ndarray,
    y: np.ndarray,
    feature_names: List[str],
    entity_id: str = None,
) -> int:
    """
    将合成的数据持久化到对应的数据库表中。
    返回 INSERT 记录数。
    """
    import uuid as _uuid
    from datetime import datetime as _datetime

    if entity_id is None:
        entity_id = f"{scenario}_{_uuid.uuid4().hex[:8]}"

    count = 0
    now = _datetime.utcnow()

    if scenario in ("sinter_temperature", "sinter_feo", "sinter_feo_real"):
        from models.core import SinteringData
        records = []
        for i in range(len(X)):
            row = X[i]
            rec = SinteringData(
                entity_id=entity_id,
                recorded_at=now,
            )
            # Map features to columns based on feature_names
            for j, name in enumerate(feature_names):
                if j < row.shape[0]:
                    val = float(row[j])
                    if name == "speed":
                        rec.machine_speed = val
                    elif name == "thickness":
                        rec.bed_thickness = val
                    elif name == "permeability":
                        rec.permeability = val
                    elif name == "pressure1":
                        rec.pressure1 = val
                    elif name == "pressure2":
                        rec.pressure2 = val
                    elif name == "temp15":
                        rec.temp15 = val
                    elif name == "temp16":
                        rec.temp16 = val
                    elif name == "temp19":
                        rec.temp19 = val
                    elif name == "carbon_ratio":
                        rec.carbon_ratio = val
                    elif name == "cooling_slope":
                        rec.cooling_slope = val
            # Set target value
            if scenario in ("sinter_temperature",):
                rec.end_temp = float(y[i])
            elif scenario in ("sinter_feo", "sinter_feo_real"):
                rec.feo_content = float(y[i])
            records.append(rec)

        db.bulk_save_objects(records)
        db.commit()
        count = len(records)

    elif scenario == "dust_collector":
        from models.core import DustCollectorData
        records = []
        for i in range(len(X)):
            row = X[i]
            rec = DustCollectorData(
                entity_id=entity_id,
                recorded_at=now,
            )
            for j, name in enumerate(feature_names):
                if j < row.shape[0]:
                    val = float(row[j])
                    if name == "production_load":
                        rec.production_load = val
                    elif name == "dust_concentration":
                        rec.dust_concentration = val
                    elif name == "filter_pressure_diff":
                        rec.filter_pressure_diff = val
                    elif name == "temperature":
                        rec.temperature = val
                    elif name == "fan_speed":
                        rec.fan_speed = val
            rec.energy_consumption_kwh = float(y[i])
            records.append(rec)

        db.bulk_save_objects(records)
        db.commit()
        count = len(records)

    return count


def get_training_data(
    db: Session, dataset_id: Optional[int] = None,
    scenario: Optional[str] = None,
    feature_cols: Optional[List[str]] = None,
    target_col: Optional[str] = None,
    n_samples: int = 1000,
    persist_to_db: bool = False,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    统一入口：获取训练数据。
    优先使用 dataset_id，其次 scenario 生成合成数据。
    persist_to_db=True 时将合成数据写入对应业务表。
    """
    if dataset_id:
        try:
            return load_dataset_data(db, dataset_id, feature_cols, target_col)
        except ValueError as e:
            # 数据集无法加载，回退到合成数据
            if scenario:
                print(f"Warning: Cannot load dataset {dataset_id}: {e}. Using synthetic data.")
                X, y, names = generate_synthetic_data(scenario, n_samples)
                if persist_to_db:
                    persist_synthetic_data(db, scenario, X, y, names)
                return X, y, names
            raise

    if scenario:
        X, y, names = generate_synthetic_data(scenario, n_samples)
        if persist_to_db:
            persist_synthetic_data(db, scenario, X, y, names)
        return X, y, names

    return generate_synthetic_data("generic", n_samples)
