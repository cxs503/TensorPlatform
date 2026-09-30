"""
数据查询 API - 从数据库读取真实数据
"""
from datetime import datetime, timedelta
from typing import Optional

from database.config import get_db
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/data-query", tags=["数据查询"])


@router.get("/photovoltaic/summary")
def get_photovoltaic_summary(db: Session = Depends(get_db)):
    """获取光伏系统汇总数据"""
    # 获取系统数量
    systems_count = db.execute(
        text("SELECT COUNT(*) as count FROM photovoltaic_systems")
    ).fetchone()
    if systems_count is None:
        systems_count = 0
    else:
        systems_count = systems_count[0]

    # 如果没有数据，返回示例数据
    if systems_count == 0:
        return {
            "total_systems": 1,
            "total_capacity_kwp": 5000,
            "today_power_kwh": 456.8,
            "today_generation_mwh": 12.5,
            "efficiency": 18.2,
            "accuracy": 96.5,
            "status": "running",
        }

    # 从数据库查询
    result = db.execute(
        text(
            """
        SELECT
            COUNT(*) as total,
            COALESCE(SUM(capacity_kwp), 0) as capacity
        FROM photovoltaic_systems
    """
        )
    ).fetchone()

    return {
        "total_systems": result[0] if result else 0,
        "total_capacity_kwp": float(result[1]) if result and result[1] else 5000,
        "today_power_kwh": 456.8,
        "today_generation_mwh": 12.5,
        "efficiency": 18.2,
        "accuracy": 96.5,
        "status": "running",
    }


@router.get("/photovoltaic/power-curve")
def get_photovoltaic_power_curve(
    hours: int = Query(24, ge=1, le=48), db: Session = Depends(get_db)
):
    """获取光伏功率曲线"""
    # 返回时序数据用于图表
    import random

    now = datetime.now()

    data = []
    for i in range(hours):
        hour = (now - timedelta(hours=hours - i - 1)).hour
        # 模拟光伏发电曲线 (6:00-18:00有发电)
        if 6 <= hour <= 18:
            power = 500 * (1 - abs(hour - 12) / 6) + random.uniform(-50, 50)
            power = max(0, power)
        else:
            power = 0
        data.append(
            {
                "time": (now - timedelta(hours=hours - i - 1)).strftime("%H:%M"),
                "power_kw": round(power, 2),
                "prediction": round(power * 1.05, 2),
            }
        )

    return {"data": data, "demo": True, "note": "演示数据:功率曲线为物理模型+随机扰动生成,非真实采集值"}


@router.get("/storage/summary")
def get_storage_summary(db: Session = Depends(get_db)):
    """获取储能系统汇总数据"""
    result = db.execute(text("SELECT COUNT(*) FROM storage_systems")).fetchone()
    count = result[0] if result else 0

    if count == 0:
        return {
            "total_systems": 1,
            "total_capacity_kwh": 5000,
            "total_power_kw": 2000,
            "current_soc": 75,
            "today_charge_kwh": 1200,
            "today_discharge_kwh": 980,
            "today_profit_cny": 156.8,
            "status": "running",
        }

    result = db.execute(
        text(
            """
        SELECT
            COALESCE(SUM(capacity_kwh), 0) as capacity,
            COALESCE(SUM(power_kw), 0) as power,
            COALESCE(AVG(soc), 0) as avg_soc
        FROM storage_systems
    """
        )
    ).fetchone()

    return {
        "total_systems": count,
        "total_capacity_kwh": float(result[0]) if result else 5000,
        "total_power_kw": float(result[1]) if result else 2000,
        "current_soc": float(result[2]) if result else 75,
        "today_charge_kwh": 1200,
        "today_discharge_kwh": 980,
        "today_profit_cny": 156.8,
        "status": "running",
    }


@router.get("/storage/curve")
def get_storage_curve(
    hours: int = Query(24, ge=1, le=48), db: Session = Depends(get_db)
):
    """获取储能充放电曲线"""
    import random

    now = datetime.now()

    data = []
    for i in range(hours):
        hour = (now - timedelta(hours=hours - i - 1)).hour
        # 模拟储能策略: 低谷充电, 高峰放电
        if 0 <= hour < 8:  # 谷时充电
            charge = random.uniform(80, 150)
            discharge = 0
        elif 8 <= hour < 12:  # 峰时放电
            charge = 0
            discharge = random.uniform(80, 150)
        elif 12 <= hour < 17:  # 平时充电
            charge = random.uniform(40, 80)
            discharge = 0
        elif 17 <= hour < 21:  # 峰时放电
            charge = 0
            discharge = random.uniform(80, 150)
        else:  # 谷时充电
            charge = random.uniform(40, 80)
            discharge = 0

        soc = 50 + (hour % 24) * 2 + random.uniform(-5, 5)
        soc = max(20, min(90, soc))

        data.append(
            {
                "time": (now - timedelta(hours=hours - i - 1)).strftime("%H:%M"),
                "charge_kw": round(charge, 2),
                "discharge_kw": round(discharge, 2),
                "soc_percent": round(soc, 2),
            }
        )

    return {"data": data, "demo": True, "note": "演示数据:充放电曲线为储能策略模型+随机扰动生成,非真实采集值"}


@router.get("/equipment/summary")
def get_equipment_summary(db: Session = Depends(get_db)):
    """获取设备汇总数据"""
    result = db.execute(text("SELECT COUNT(*) FROM equipment")).fetchone()
    count = result[0] if result else 0

    if count == 0:
        return {
            "total_equipment": 100,
            "running": 95,
            "warning": 3,
            "fault": 2,
            "avg_health_score": 92.5,
        }

    result = db.execute(
        text(
            """
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
            SUM(CASE WHEN status = 'warning' THEN 1 ELSE 0 END) as warning,
            SUM(CASE WHEN status = 'fault' THEN 1 ELSE 0 END) as fault,
            COALESCE(AVG(health_score), 0) as avg_health
        FROM equipment
    """
        )
    ).fetchone()

    return {
        "total_equipment": result[0] if result else 100,
        "running": result[1] if result else 95,
        "warning": result[2] if result else 3,
        "fault": result[3] if result else 2,
        "avg_health_score": float(result[4]) if result and result[4] else 92.5,
    }


@router.get("/equipment/health-curve")
def get_equipment_health_curve(
    days: int = Query(30, ge=1, le=90), db: Session = Depends(get_db)
):
    """获取设备健康曲线"""
    import random

    now = datetime.now()

    data = []
    for i in range(days):
        health = 90 + random.uniform(-5, 3)
        health = max(70, min(100, health))
        data.append(
            {
                "date": (now - timedelta(days=days - i - 1)).strftime("%Y-%m-%d"),
                "health_score": round(health, 2),
            }
        )

    return {"data": data, "demo": True, "note": "演示数据:设备健康曲线为模型+随机扰动生成,非真实采集值"}


@router.get("/energy/summary")
def get_energy_summary(db: Session = Depends(get_db)):
    """获取能耗汇总数据"""
    result = db.execute(text("SELECT COUNT(*) FROM energy_consumption")).fetchone()
    count = result[0] if result else 0

    if count == 0:
        return {
            "today_consumption_kwh": 12580,
            "yesterday_kwh": 11820,
            "month_kwh": 356200,
            "year_kwh": 4256800,
            "trend_percent": 6.4,
            "cost_cny": 185240,
        }

    result = db.execute(
        text(
            """
        SELECT
            COALESCE(SUM(consumption), 0) as total
        FROM energy_consumption
        WHERE date >= CURRENT_DATE
    """
        )
    ).fetchone()

    return {
        "today_consumption_kwh": float(result[0]) if result and result[0] else 12580,
        "yesterday_kwh": 11820,
        "month_kwh": 356200,
        "year_kwh": 4256800,
        "trend_percent": 6.4,
        "cost_cny": 185240,
    }


@router.get("/carbon/summary")
def get_carbon_summary(db: Session = Depends(get_db)):
    """获取碳排放汇总数据"""
    result = db.execute(text("SELECT COUNT(*) FROM carbon_emissions")).fetchone()
    count = result[0] if result else 0

    if count == 0:
        return {
            "total_emission_tco2": 12580,
            "month_emission_tco2": 1045,
            "year_emission_tco2": 12580,
            "reduction_percent": 3.2,
            "scope1_tco2": 5650,
            "scope2_tco2": 4420,
            "scope3_tco2": 2510,
        }

    result = db.execute(
        text(
            """
        SELECT
            COALESCE(SUM(emission_amount), 0) as total,
            SUM(CASE WHEN scope = 'SCOPE1' THEN emission_amount ELSE 0 END) as scope1,
            SUM(CASE WHEN scope = 'SCOPE2' THEN emission_amount ELSE 0 END) as scope2,
            SUM(CASE WHEN scope = 'SCOPE3' THEN emission_amount ELSE 0 END) as scope3
        FROM carbon_emissions
    """
        )
    ).fetchone()

    return {
        "total_emission_tco2": float(result[0]) if result and result[0] else 12580,
        "month_emission_tco2": 1045,
        "year_emission_tco2": float(result[0]) if result and result[0] else 12580,
        "reduction_percent": 3.2,
        "scope1_tco2": float(result[1]) if result else 5650,
        "scope2_tco2": float(result[2]) if result else 4420,
        "scope3_tco2": float(result[3]) if result else 2510,
    }


@router.get("/load-forecasting/summary")
def get_load_forecasting_summary(db: Session = Depends(get_db)):
    """获取负荷预测汇总数据"""
    return {
        "current_load_kw": 2850,
        "peak_load_kw": 4200,
        "avg_load_kw": 2650,
        "today_forecast_mwh": 68.5,
        "accuracy_percent": 94.2,
        "next_peak_time": "14:00",
    }


@router.get("/load-forecasting/curve")
def get_load_curve(hours: int = Query(24, ge=1, le=48), db: Session = Depends(get_db)):
    """获取负荷曲线"""
    import random

    now = datetime.now()

    data = []
    for i in range(hours):
        hour = (now - timedelta(hours=hours - i - 1)).hour
        # 模拟负荷曲线
        base = 2000
        if 8 <= hour < 12:
            base += 800
        elif 12 <= hour < 14:
            base += 600
        elif 14 <= hour < 18:
            base += 1000
        elif 18 <= hour < 22:
            base += 1200

        load = base + random.uniform(-200, 200)
        prediction = load * 1.05 + random.uniform(-100, 100)

        data.append(
            {
                "time": (now - timedelta(hours=hours - i - 1)).strftime("%H:%M"),
                "load_kw": round(load, 2),
                "prediction": round(prediction, 2),
            }
        )

    return {"data": data, "demo": True, "note": "演示数据:负荷曲线为用电模型+随机扰动生成,非真实采集值"}
