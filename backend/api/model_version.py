"""
模型版本管理与A/B Testing API
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# 服务
from services.model_version_service import get_ab_testing_manager, get_version_manager

router = APIRouter(prefix="/model-version", tags=["模型版本管理"])


# ============ 模型版本管理 ============


class CreateVersionReq(BaseModel):
    model_id: str
    description: str = ""
    metrics: Dict[str, float] = {}
    tags: List[str] = []


@router.post("/versions")
def create_version(req: CreateVersionReq):
    """创建新版本"""
    manager = get_version_manager()
    version = manager.create_version(
        req.model_id, req.description, req.metrics, req.tags
    )
    return {
        "model_id": version.model_id,
        "version": version.version,
        "description": version.description,
        "metrics": version.metrics,
        "tags": version.tags,
        "created_at": version.created_at,
        "is_current": version.is_current,
    }


@router.get("/models/{model_id}/versions")
def get_versions(model_id: str):
    """获取模型所有版本"""
    manager = get_version_manager()
    versions = manager.get_versions(model_id)
    return {
        "total": len(versions),
        "items": [
            {
                "model_id": v.model_id,
                "version": v.version,
                "description": v.description,
                "metrics": v.metrics,
                "tags": v.tags,
                "created_at": v.created_at,
                "is_current": v.is_current,
            }
            for v in versions
        ],
    }


@router.get("/models/{model_id}/versions/{version}")
def get_version(model_id: str, version: str):
    """获取指定版本"""
    manager = get_version_manager()
    v = manager.get_version(model_id, version)
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    return {
        "model_id": v.model_id,
        "version": v.version,
        "description": v.description,
        "metrics": v.metrics,
        "tags": v.tags,
        "created_at": v.created_at,
        "is_current": v.is_current,
    }


@router.get("/models/{model_id}/versions/current")
def get_current_version(model_id: str):
    """获取当前版本"""
    manager = get_version_manager()
    v = manager.get_current_version(model_id)
    if not v:
        raise HTTPException(status_code=404, detail="No versions found")
    return {
        "model_id": v.model_id,
        "version": v.version,
        "description": v.description,
        "metrics": v.metrics,
    }


@router.post("/models/{model_id}/versions/{version}/set-current")
def set_current_version(model_id: str, version: str):
    """设置当前版本"""
    manager = get_version_manager()
    success = manager.set_current_version(model_id, version)
    if success:
        return {"message": f"Version {version} set as current"}
    raise HTTPException(status_code=404, detail="Version not found")


@router.post("/models/{model_id}/versions/{version}/rollback")
def rollback_version(model_id: str, version: str):
    """回滚版本"""
    manager = get_version_manager()
    success = manager.rollback(model_id, version)
    if success:
        return {"message": f"Rolled back to version {version}"}
    raise HTTPException(status_code=404, detail="Version not found")


@router.get("/models/{model_id}/compare")
def compare_versions(model_id: str, version1: str, version2: str):
    """对比两个版本"""
    manager = get_version_manager()
    try:
        return manager.compare_versions(model_id, version1, version2)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ A/B Testing ============


class CreateABTestReq(BaseModel):
    name: str
    model_id: str
    versions: List[str]
    traffic_split: Dict[str, int]


@router.post("/ab-tests")
def create_ab_test(req: CreateABTestReq):
    """创建A/B测试"""
    manager = get_ab_testing_manager()
    try:
        test = manager.create_test(
            req.name, req.model_id, req.versions, req.traffic_split
        )
        return {
            "test_id": test.test_id,
            "name": test.name,
            "model_id": test.model_id,
            "versions": test.versions,
            "traffic_split": test.traffic_split,
            "status": test.status,
            "created_at": test.created_at,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ab-tests")
def list_ab_tests(status: Optional[str] = None):
    """列出A/B测试"""
    manager = get_ab_testing_manager()
    tests = manager.list_tests(status)
    return {
        "total": len(tests),
        "items": [
            {
                "test_id": t.test_id,
                "name": t.name,
                "model_id": t.model_id,
                "versions": t.versions,
                "traffic_split": t.traffic_split,
                "status": t.status,
                "created_at": t.created_at,
            }
            for t in tests
        ],
    }


@router.get("/ab-tests/{test_id}")
def get_ab_test(test_id: str):
    """获取A/B测试详情"""
    manager = get_ab_testing_manager()
    test = manager.get_test(test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")
    return {
        "test_id": test.test_id,
        "name": test.name,
        "model_id": test.model_id,
        "versions": test.versions,
        "traffic_split": test.traffic_split,
        "status": test.status,
        "created_at": test.created_at,
    }


@router.get("/ab-tests/{test_id}/results")
def get_ab_test_results(test_id: str):
    """获取A/B测试结果"""
    manager = get_ab_testing_manager()
    results = manager.get_test_results(test_id)
    return {"test_id": test_id, "results": results}


@router.post("/ab-tests/{test_id}/stop")
def stop_ab_test(test_id: str):
    """停止A/B测试"""
    manager = get_ab_testing_manager()
    success = manager.stop_test(test_id)
    if success:
        return {"message": "Test stopped"}
    raise HTTPException(status_code=404, detail="Test not found")


@router.post("/ab-tests/{test_id}/record")
def record_ab_result(test_id: str, version: str, success: bool, latency: float = 0):
    """记录A/B测试结果"""
    manager = get_ab_testing_manager()
    manager.record_result(test_id, version, {"success": success, "latency": latency})
    return {"message": "Result recorded"}
