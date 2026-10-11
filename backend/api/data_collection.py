"""
实时数据采集 API
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from services.data_collection_service import (
    CollectionStatus,
    DataSourceType,
    get_collection_service,
)

router = APIRouter(prefix="/collection", tags=["实时数据采集"])


# ========== 采集任务管理 ==========


@router.get("/tasks")
def list_tasks(status: str = None):
    """列出采集任务"""
    service = get_collection_service()
    return service.list_tasks(status)


@router.post("/tasks")
def create_task(req: dict):
    """创建采集任务"""
    service = get_collection_service()
    task = service.create_task(
        name=req.get("name"),
        source_type=req.get("source_type", DataSourceType.INDUSTRIAL_DEVICE),
        source_config=req.get("source_config", {}),
        collection_interval=req.get("collection_interval", 1),
        batch_size=req.get("batch_size", 100),
    )
    return {
        "id": task.id,
        "name": task.name,
        "source_type": task.source_type,
        "status": task.status,
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    """获取采集任务"""
    service = get_collection_service()
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "id": task.id,
        "name": task.name,
        "source_type": task.source_type,
        "source_config": task.source_config,
        "collection_interval": task.collection_interval,
        "status": task.status,
        "total_collected": task.total_collected,
        "error_count": task.error_count,
        "last_collection_time": task.last_collection_time,
    }


@router.post("/tasks/{task_id}/start")
def start_task(task_id: str):
    """启动采集任务"""
    service = get_collection_service()
    if not service.start_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "任务已启动"}


@router.post("/tasks/{task_id}/pause")
def pause_task(task_id: str):
    """暂停采集任务"""
    service = get_collection_service()
    if not service.pause_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "任务已暂停"}


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str):
    """停止采集任务"""
    service = get_collection_service()
    if not service.stop_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "任务已停止"}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    """删除采集任务"""
    service = get_collection_service()
    if not service.delete_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "删除成功"}


# ========== 流水线管理 ==========


@router.get("/pipelines")
def list_pipelines():
    """列出数据流水线"""
    service = get_collection_service()
    return service.list_pipelines()


@router.post("/pipelines")
def create_pipeline(req: dict):
    """创建数据流水线"""
    service = get_collection_service()
    pipeline = service.create_pipeline(name=req.get("name"), steps=req.get("steps", []))
    return {"id": pipeline.id, "name": pipeline.name, "steps": pipeline.steps}


# ========== 数据获取 ==========


@router.get("/data")
def get_latest_data(source: str = None, limit: int = 100):
    """获取最新数据"""
    service = get_collection_service()
    return service.get_latest_data(source, limit)


@router.get("/data/query")
def query_historical_data(
    start_time: str, end_time: str, source: str = None, limit: int = 1000
):
    """查询历史数据"""
    service = get_collection_service()
    start = datetime.fromisoformat(start_time)
    end = datetime.fromisoformat(end_time)
    return service.query_historical_data(start, end, source, None, limit)


@router.post("/data/process")
def process_data(req: dict):
    """处理数据"""
    service = get_collection_service()
    pipeline_id = req.get("pipeline_id")
    data = req.get("data", [])
    result = service.process_data(pipeline_id, data)
    return {"processed_count": len(result), "data": result}


# ========== 统计 ==========


@router.get("/stats")
def get_collection_stats():
    """获取采集统计"""
    service = get_collection_service()
    return service.get_collection_stats()
