"""
数据同步 API - 将虚拟数据同步到数据管理模块
"""
from database.config import get_db
from fastapi import APIRouter, Depends
from services.data_sync_service import DataSyncService
from sqlalchemy.orm import Session

router = APIRouter(prefix="/sync", tags=["数据同步"])


@router.post("/all")
def sync_all_data(db: Session = Depends(get_db)):
    """同步所有虚拟数据到数据管理模块"""
    results = DataSyncService.sync_all(db)

    # 统计成功数量
    success_count = sum(
        1
        for r in results.values()
        if r["datasets"].get("success")
        or r["lineage"].get("success")
        or r["catalog"].get("success")
    )

    return {"message": f"数据同步完成，成功同步 {success_count} 种数据类型", "results": results}


@router.post("/{data_type}")
def sync_data_type(data_type: str, db: Session = Depends(get_db)):
    """同步指定类型的虚拟数据"""
    results = {
        "data_type": data_type,
        "datasets": DataSyncService.sync_to_datasets(db, data_type),
        "lineage": DataSyncService.sync_to_lineage(db, data_type),
        "catalog": DataSyncService.sync_to_catalog(db, data_type),
    }

    success = any(
        r.get("success")
        for r in [results["datasets"], results["lineage"], results["catalog"]]
    )

    return {"message": "同步成功" if success else "同步失败", "results": results}


@router.get("/status")
def get_sync_status(db: Session = Depends(get_db)):
    """获取数据同步状态"""
    from sqlalchemy import text

    status = {}

    # 检查各表数据量
    tables = {
        "photovoltaic_systems": "光伏系统",
        "equipment": "设备数据",
        "storage_systems": "储能系统",
        "energy_consumption": "能耗数据",
        "carbon_emissions": "碳排放",
    }

    for table, name in tables.items():
        try:
            result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar() or 0

            # 检查是否已同步到datasets
            ds_result = db.execute(
                text("SELECT id, status FROM datasets WHERE name = :name"),
                {"name": name},
            )
            ds_row = ds_result.fetchone()

            status[table] = {
                "display_name": name,
                "count": count,
                "synced_to_datasets": ds_row is not None,
                "sync_status": ds_row[1] if ds_row else None,
            }
        except Exception as e:
            status[table] = {"display_name": name, "count": 0, "error": str(e)}

    return status
