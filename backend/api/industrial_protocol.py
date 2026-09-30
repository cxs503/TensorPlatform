"""
工业协议 API
"""
from fastapi import APIRouter, HTTPException
from services.industrial_protocol_service import (
    ProtocolType,
    get_industrial_protocol_service,
)

router = APIRouter(prefix="/industrial", tags=["工业协议"])


# ========== 设备管理 ==========


@router.get("/devices")
def list_devices(protocol: str = None, status: str = None):
    """列出设备"""
    service = get_industrial_protocol_service()
    return service.list_devices(protocol, status)


@router.post("/devices")
def register_device(req: dict):
    """注册设备"""
    service = get_industrial_protocol_service()
    device = service.register_device(
        name=req.get("name"),
        protocol=req.get("protocol"),
        host=req.get("host"),
        port=req.get("port"),
        device_id=req.get("device_id"),
        metadata=req.get("metadata"),
    )
    return {
        "id": device.id,
        "name": device.name,
        "protocol": device.protocol,
        "status": device.status,
    }


@router.get("/devices/{device_id}")
def get_device(device_id: str):
    """获取设备"""
    service = get_industrial_protocol_service()
    device = service.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {
        "id": device.id,
        "name": device.name,
        "protocol": device.protocol,
        "host": device.host,
        "port": device.port,
        "status": device.status,
        "metadata": device.metadata,
        "data_points": service.get_data_points(device_id),
    }


@router.delete("/devices/{device_id}")
def unregister_device(device_id: str):
    """注销设备"""
    service = get_industrial_protocol_service()
    if not service.unregister_device(device_id):
        raise HTTPException(status_code=404, detail="设备不存在")
    return {"message": "注销成功"}


@router.put("/devices/{device_id}/status")
def update_device_status(device_id: str, status: str):
    """更新设备状态"""
    service = get_industrial_protocol_service()
    if not service.update_device_status(device_id, status):
        raise HTTPException(status_code=404, detail="设备不存在")
    return {"message": "状态更新成功"}


# ========== 数据点管理 ==========


@router.post("/devices/{device_id}/data-points")
def add_data_point(device_id: str, req: dict):
    """添加数据点"""
    service = get_industrial_protocol_service()
    dp = service.add_data_point(
        device_id=device_id,
        name=req.get("name"),
        address=req.get("address"),
        data_type=req.get("data_type"),
        unit=req.get("unit"),
        scale=req.get("scale", 1.0),
    )
    if not dp:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {
        "id": dp.id,
        "name": dp.name,
        "address": dp.address,
        "data_type": dp.data_type,
    }


@router.get("/devices/{device_id}/data-points")
def get_data_points(device_id: str):
    """获取数据点"""
    service = get_industrial_protocol_service()
    return service.get_data_points(device_id)


@router.delete("/devices/{device_id}/data-points/{data_point_id}")
def remove_data_point(device_id: str, data_point_id: str):
    """移除数据点"""
    service = get_industrial_protocol_service()
    if not service.remove_data_point(device_id, data_point_id):
        raise HTTPException(status_code=404, detail="设备或数据点不存在")
    return {"message": "移除成功"}


# ========== 数据采集 ==========


@router.get("/devices/{device_id}/read")
def read_data(device_id: str, data_point_ids: str = None):
    """读取数据"""
    service = get_industrial_protocol_service()
    ids = data_point_ids.split(",") if data_point_ids else None
    return service.read_data(device_id, ids)


@router.post("/devices/{device_id}/write")
def write_data(device_id: str, req: dict):
    """写入数据（控制）"""
    service = get_industrial_protocol_service()
    success = service.write_data(
        device_id=device_id,
        data_point_id=req.get("data_point_id"),
        value=req.get("value"),
    )
    if not success:
        raise HTTPException(status_code=400, detail="写入失败")
    return {"message": "写入成功"}


@router.get("/devices/{device_id}/history")
def get_historical_data(
    device_id: str, start_time: str = None, end_time: str = None, limit: int = 100
):
    """获取历史数据"""
    service = get_industrial_protocol_service()
    return service.get_historical_data(device_id, start_time, end_time, limit)


# ========== 协议映射 ==========


@router.get("/mappings")
def list_mappings():
    """列出协议映射"""
    service = get_industrial_protocol_service()
    return service.list_mappings()


@router.post("/mappings")
def create_mapping(req: dict):
    """创建协议映射"""
    service = get_industrial_protocol_service()
    mapping = service.create_mapping(
        source_protocol=req.get("source_protocol"),
        target_protocol=req.get("target_protocol"),
        mapping_config=req.get("mapping_config"),
    )
    return {
        "id": mapping.id,
        "source_protocol": mapping.source_protocol,
        "target_protocol": mapping.target_protocol,
    }


@router.post("/mappings/{mapping_id}/execute")
def execute_mapping(mapping_id: str, req: dict):
    """执行协议转换"""
    service = get_industrial_protocol_service()
    result = service.execute_mapping(mapping_id, req.get("source_data", {}))
    if result is None:
        raise HTTPException(status_code=404, detail="映射不存在")
    return result


# ========== 统计 ==========


@router.get("/stats")
def get_protocol_stats():
    """获取协议统计"""
    service = get_industrial_protocol_service()
    return service.get_protocol_stats()
