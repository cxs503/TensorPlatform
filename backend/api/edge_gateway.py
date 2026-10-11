"""
边缘网关API
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# 边缘网关服务
from services.edge_gateway_service import get_edge_gateway

router = APIRouter(prefix="/edge", tags=["边缘计算"])

# 已注册网关列表(进程内;演示态。生产应落库)
_gateways: List[Dict[str, Any]] = []


class ModelSyncRequest(BaseModel):
    models: List[Dict[str, Any]]


class InferenceRequest(BaseModel):
    model_id: str
    input_data: List[List[float]]


class PreprocessRequest(BaseModel):
    data: List[Dict]
    pipeline: str = "default"


class GatewayRegisterReq(BaseModel):
    name: str
    ip: str
    description: Optional[str] = None
    location: Optional[str] = None


@router.post("/register")
def register_gateway(req: GatewayRegisterReq):
    """注册边缘网关(ip 必填,落注册表)"""
    import uuid as _uuid
    from datetime import datetime as _dt
    if not req.ip or not req.ip.strip():
        raise HTTPException(status_code=422, detail="IP 地址必填")
    gw = {
        "gateway_id": "gw-" + _uuid.uuid4().hex[:8],
        "name": req.name,
        "ip": req.ip.strip(),
        "description": req.description,
        "location": req.location or "unknown",
        "status": "online",
        "registered_at": _dt.now().isoformat(),
    }
    _gateways.append(gw)
    return {"success": True, **gw}


@router.get("/status")
def get_gateway_status():
    """获取网关状态(含已注册网关列表)"""
    gateway = get_edge_gateway()
    data = gateway.get_status()
    data["gateways"] = _gateways
    return data


@router.post("/models/sync")
def sync_models(req: ModelSyncRequest):
    """同步模型到边缘节点"""
    gateway = get_edge_gateway()
    return gateway.sync_models(req.models)


@router.post("/models/{model_id}/load")
def load_model(model_id: str):
    """加载模型到边缘节点"""
    gateway = get_edge_gateway()
    return gateway.load_model(model_id)


@router.post("/models/{model_id}/unload")
def unload_model(model_id: str):
    """卸载模型"""
    gateway = get_edge_gateway()
    return gateway.unload_model(model_id)


@router.post("/inference")
def local_inference(req: InferenceRequest):
    """执行本地推理"""
    gateway = get_edge_gateway()
    try:
        return gateway.local_inference(req.model_id, req.input_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/preprocess")
def preprocess_data(req: PreprocessRequest):
    """数据预处理"""
    gateway = get_edge_gateway()
    return gateway.preprocess_data(req.data, req.pipeline)


@router.post("/aggregate")
def aggregate_results(results: List[Dict]):
    """聚合边缘节点结果"""
    gateway = get_edge_gateway()
    return gateway.aggregate_results(results)


@router.get("/metrics")
def get_metrics():
    """获取边缘网关指标"""
    gateway = get_edge_gateway()
    return gateway.get_metrics()
