"""
GPU资源调度 API
"""
from fastapi import APIRouter, HTTPException
from services.gpu_scheduler_service import GPUAllocationType, get_gpu_scheduler_service

router = APIRouter(prefix="/gpu", tags=["GPU资源调度"])


# ========== GPU管理 ==========


@router.get("/gpus")
def list_gpus(status: str = None):
    """列出GPU"""
    service = get_gpu_scheduler_service()
    return service.list_gpus(status)


@router.get("/gpus/{gpu_id}")
def get_gpu(gpu_id: str):
    """获取GPU详情"""
    service = get_gpu_scheduler_service()
    gpu = service.get_gpu(gpu_id)
    if not gpu:
        raise HTTPException(status_code=404, detail="GPU不存在")
    return {
        "id": gpu.id,
        "name": gpu.name,
        "total_memory_mb": gpu.total_memory_mb,
        "used_memory_mb": gpu.used_memory_mb,
        "free_memory_mb": gpu.total_memory_mb - gpu.used_memory_mb,
        "utilization_percent": gpu.utilization_percent,
        "temperature_celsius": gpu.temperature_celsius,
        "power_watts": gpu.power_watts,
        "status": gpu.status,
        "device_index": gpu.device_index,
    }


@router.post("/gpus")
def register_gpu(req: dict):
    """注册GPU"""
    service = get_gpu_scheduler_service()
    gpu = service.register_gpu(
        gpu_id=req.get("gpu_id"),
        name=req.get("name"),
        total_memory_mb=req.get("total_memory_mb"),
        device_index=req.get("device_index"),
    )
    return {"id": gpu.id, "name": gpu.name, "status": gpu.status}


@router.delete("/gpus/{gpu_id}")
def unregister_gpu(gpu_id: str):
    """注销GPU"""
    service = get_gpu_scheduler_service()
    if not service.unregister_gpu(gpu_id):
        raise HTTPException(status_code=404, detail="GPU不存在")
    return {"message": "注销成功"}


# ========== GPU分配 ==========


@router.post("/allocate")
def allocate_gpu(req: dict):
    """分配GPU"""
    service = get_gpu_scheduler_service()
    result = service.allocate_gpu(
        job_id=req.get("job_id"),
        memory_mb=req.get("memory_mb", 4096),
        allocation_type=req.get("allocation_type", GPUAllocationType.FULL),
        user_id=req.get("user_id"),
    )
    if not result:
        raise HTTPException(status_code=400, detail="无可用GPU")
    return result


@router.post("/release/{job_id}")
def release_gpu(job_id: str):
    """释放GPU"""
    service = get_gpu_scheduler_service()
    if not service.release_gpu(job_id):
        raise HTTPException(status_code=404, detail="分配记录不存在")
    return {"message": "释放成功"}


@router.get("/jobs/{job_id}")
def get_job_allocation(job_id: str):
    """获取任务分配信息"""
    service = get_gpu_scheduler_service()
    job = service.get_job_allocation(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


# ========== vGPU管理 ==========


@router.post("/vgpus")
def create_vgpu(req: dict):
    """创建vGPU"""
    service = get_gpu_scheduler_service()
    vgpu = service.create_vgpu(
        name=req.get("name"),
        gpu_id=req.get("gpu_id"),
        memory_mb=req.get("memory_mb", 2048),
        user_id=req.get("user_id"),
    )
    if not vgpu:
        raise HTTPException(status_code=400, detail="创建失败，GPU资源不足")
    return {
        "id": vgpu.id,
        "name": vgpu.name,
        "gpu_id": vgpu.gpu_id,
        "memory_mb": vgpu.memory_mb,
        "status": vgpu.status,
    }


@router.get("/vgpus")
def list_vgpus(user_id: str = None):
    """列出vGPU"""
    service = get_gpu_scheduler_service()
    return service.list_vgpus(user_id)


@router.delete("/vgpus/{vgpu_id}")
def delete_vgpu(vgpu_id: str):
    """删除vGPU"""
    service = get_gpu_scheduler_service()
    if not service.delete_vgpu(vgpu_id):
        raise HTTPException(status_code=404, detail="vGPU不存在")
    return {"message": "删除成功"}


# ========== 统计 ==========


@router.get("/stats")
def get_gpu_stats():
    """获取GPU统计"""
    service = get_gpu_scheduler_service()
    return service.get_gpu_stats()
