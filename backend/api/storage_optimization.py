"""
储能优化调度 API
"""
import random
from datetime import datetime, timedelta

from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from services.storage_optimization_service import StrategyType, get_storage_service
from sqlalchemy import text

router = APIRouter(prefix="/storage", tags=["储能优化"])


# ========== 状态端点 (固定路径必须在动态路径之前) ==========


@router.get("/status")
def get_status(db=Depends(get_db)):
    """获取储能服务状态 - 从数据库读取"""
    # 统计运行中的系统
    result = db.execute(
        text("SELECT COUNT(*) FROM storage_systems WHERE status = 'running'")
    )
    system_count = result.fetchone()[0] or 0

    # 获取平均SOC
    result = db.execute(
        text(
            "SELECT COALESCE(AVG(soc), 50) FROM storage_systems WHERE status = 'running'"
        )
    )
    avg_soc = float(result.fetchone()[0]) or 50

    # 获取总容量
    result = db.execute(
        text(
            "SELECT COALESCE(SUM(capacity_kwh), 0) FROM storage_systems WHERE status = 'running'"
        )
    )
    total_capacity = float(result.fetchone()[0]) or 0

    return {
        "status": "ready",
        "system_count": system_count,
        "soc": round(avg_soc, 1),
        "today_profit": round(total_capacity * 0.5, 0),
        "capacity": round(total_capacity, 0),
        "efficiency": 89.5,
    }


@router.get("/summary")
def get_summary(db=Depends(get_db)):
    """获取储能汇总 - 从数据库读取"""
    # 获取汇总统计
    result = db.execute(
        text(
            """
        SELECT
            COUNT(*) as count,
            COALESCE(SUM(capacity_kwh), 0) as total_capacity,
            COALESCE(SUM(power_kw), 0) as total_power,
            COALESCE(AVG(soc), 50) as avg_soc
        FROM storage_systems
        WHERE status = 'running'
    """
        )
    )
    row = result.fetchone()

    system_count = row[0] or 0
    total_capacity = float(row[1]) or 0
    total_power = float(row[2]) or 0
    avg_soc = float(row[3]) or 50

    return {
        "system_count": system_count,
        "soc": round(avg_soc, 1),
        "today_profit": round(total_capacity * 0.3, 0),
        "capacity": round(total_capacity, 0),
        "power_kw": round(total_power, 0),
        "efficiency": 89.5,
    }


@router.get("/curve")
def get_curve(db=Depends(get_db)):
    """获取储能曲线 - 基于数据库数据"""
    # 获取总容量
    result = db.execute(
        text(
            "SELECT COALESCE(SUM(capacity_kwh), 0) FROM storage_systems WHERE status = 'running'"
        )
    )
    total_capacity = float(result.fetchone()[0]) or 500

    data = []
    soc = 50  # 初始SOC

    for i in range(24):
        # 峰谷套利策略曲线
        if 0 <= i < 7:  # 谷时充电
            charge = total_capacity * 0.15 * random.uniform(0.8, 1.2)
            discharge = 0
            soc = min(90, soc + 10)
        elif 9 <= i < 12 or 18 <= i < 21:  # 峰时放电
            charge = 0
            discharge = total_capacity * 0.12 * random.uniform(0.8, 1.2)
            soc = max(20, soc - 12)
        else:  # 平时
            charge = total_capacity * 0.05 * random.uniform(0, 1)
            discharge = total_capacity * 0.03 * random.uniform(0, 1)
            soc = max(20, min(90, soc + random.randint(-5, 5)))

        data.append(
            {
                "hour": f"{i}:00",
                "charge_kw": round(charge, 1),
                "discharge_kw": round(discharge, 1),
                "soc_percent": round(soc, 1),
            }
        )

    return {"data": data, "total_capacity_kwh": round(total_capacity, 0), "demo": True, "note": "演示数据:总容量取自数据库,充放电曲线为峰谷策略模型+随机扰动生成,非真实调度记录"}


@router.get("/schedule-history")
def get_schedule_history(limit: int = 10, db=Depends(get_db)):
    """获取调度历史"""
    result = db.execute(text("SELECT COUNT(*) FROM storage_systems"))
    system_count = result.fetchone()[0] or 0

    items = []
    actions = ["charge", "discharge"]
    for i in range(min(limit, 10)):
        items.append(
            {
                "id": i + 1,
                "time": (datetime.now() - timedelta(hours=i)).strftime("%H:%M"),
                "action": random.choice(actions),
                "power": random.randint(100, 400),
                "profit": random.randint(-50, 100),
            }
        )
    return {"items": items, "demo": True, "note": "演示数据:调度动作/功率/收益均为随机生成,非真实历史调度记录"}


@router.get("/stats")
def get_storage_stats(db=Depends(get_db)):
    """获取储能统计"""
    result = db.execute(
        text(
            """
        SELECT
            COUNT(*) as count,
            COALESCE(SUM(capacity_kwh), 0) as total_capacity,
            COALESCE(AVG(soc), 50) as avg_soc
        FROM storage_systems
    """
        )
    )
    row = result.fetchone()

    return {
        "total_systems": row[0] or 0,
        "total_capacity_kwh": float(row[1]) or 0,
        "average_soc": float(row[2]) or 50,
    }


# ========== 储能系统管理 (动态路径) ==========


@router.get("")
def list_storages(db=Depends(get_db)):
    """列出储能系统 - 从数据库读取"""
    result = db.execute(
        text(
            """
        SELECT id, name, capacity_kwh, power_kw, soc, status
        FROM storage_systems
        LIMIT 100
    """
        )
    )

    items = []
    for row in result.fetchall():
        items.append(
            {
                "id": row[0],
                "name": row[1],
                "capacity_kwh": float(row[2]) if row[2] else 0,
                "power_kw": float(row[3]) if row[3] else 0,
                "soc": float(row[4]) if row[4] else 0,
                "status": row[5],
            }
        )

    return {"total": len(items), "items": items}


@router.post("")
def create_storage(req: dict):
    """创建储能系统"""
    service = get_storage_service()
    storage = service.create_storage(
        name=req.get("name"),
        capacity_mwh=req.get("capacity_mwh"),
        power_kw=req.get("power_kw"),
        efficiency=req.get("efficiency", 0.85),
    )
    return {
        "id": storage.id,
        "name": storage.name,
        "capacity_mwh": storage.capacity_mwh,
    }


@router.get("/{storage_id}")
def get_storage(storage_id: str):
    """获取储能系统"""
    service = get_storage_service()
    storage = service.get_storage(storage_id)
    if not storage:
        raise HTTPException(status_code=404, detail="储能系统不存在")
    return {
        "id": storage.id,
        "name": storage.name,
        "capacity_mwh": storage.capacity_mwh,
        "power_kw": storage.power_kw,
        "efficiency": storage.efficiency,
    }


@router.get("/{storage_id}/status")
def get_storage_status(storage_id: str):
    """获取储能状态"""
    service = get_storage_service()
    return service.get_status(storage_id)


@router.put("/{storage_id}/soc")
def update_soc(storage_id: str, soc: float):
    """更新SOC"""
    service = get_storage_service()
    if not service.update_soc(storage_id, soc):
        raise HTTPException(status_code=404, detail="储能系统不存在")
    return {"message": "更新成功", "soc": soc}


# ========== 优化调度 ==========


@router.post("/{storage_id}/optimize")
def optimize_schedule(storage_id: str, strategy: str = StrategyType.PEAK_VALLEY):
    """优化调度"""
    service = get_storage_service()
    result = service.optimize_schedule(storage_id, strategy)
    return {
        "id": result.id,
        "strategy": result.strategy,
        "schedule": result.schedule,
        "expected_revenue": result.expected_revenue,
    }


# ========== 实时调度 ==========


@router.post("/{storage_id}/execute")
def execute_schedule(
    storage_id: str, action: str, power_kw: float, duration_minutes: int = 60
):
    """执行调度"""
    service = get_storage_service()
    return service.execute_schedule(storage_id, action, power_kw, duration_minutes)


@router.get("/{storage_id}/schedule")
def get_schedule_history_by_id(storage_id: str, limit: int = 100):
    """获取调度历史"""
    service = get_storage_service()
    return service.get_schedule_history(storage_id, limit)


# ========== 收益分析 ==========


@router.get("/{storage_id}/revenue")
def analyze_revenue(storage_id: str, days: int = 30):
    """收益分析"""
    service = get_storage_service()
    return service.analyze_revenue(storage_id, days)
