"""
GPU资源调度服务
提供GPU池化管理、vGPU分配、GPU监控、显存追踪
"""
import uuid
import subprocess
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class GPUStatus(str, Enum):
    """GPU状态"""

    AVAILABLE = "available"
    OCCUPIED = "occupied"
    FAULTY = "faulty"
    MAINTENANCE = "maintenance"


class GPUAllocationType(str, Enum):
    """GPU分配类型"""

    FULL = "full"  # 整卡分配
    VGPU = "vgpu"  # vGPU虚拟分配
    MIG = "mig"  # MIG分割


class PhysicalGPU:
    """物理GPU"""

    def __init__(self, gpu_id: str, name: str, total_memory_mb: int, device_index: int):
        self.id = gpu_id
        self.name = name
        self.total_memory_mb = total_memory_mb
        self.device_index = device_index
        self.status = GPUStatus.AVAILABLE
        self.used_memory_mb = 0
        self.utilization_percent = 0
        self.temperature_celsius = 0
        self.power_watts = 0
        self.last_updated = datetime.now().isoformat()


class VGPUInstance:
    """vGPU实例"""

    def __init__(self, name: str, gpu_id: str, memory_mb: int, user_id: str = None):
        self.id = f"vgpu_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.gpu_id = gpu_id
        self.memory_mb = memory_mb
        self.user_id = user_id
        self.created_at = datetime.now().isoformat()
        self.status = "running"


class GPUAllocation:
    """GPU分配记录"""

    def __init__(
        self,
        job_id: str,
        gpu_id: str,
        allocation_type: str,
        memory_mb: int,
        user_id: str = None,
    ):
        self.id = f"alloc_{uuid.uuid4().hex[:12]}"
        self.job_id = job_id
        self.gpu_id = gpu_id
        self.allocation_type = allocation_type
        self.memory_mb = memory_mb
        self.user_id = user_id
        self.created_at = datetime.now().isoformat()
        self.released_at = None


class GPUSchedulerService:
    """GPU调度服务"""

    def __init__(self):
        self.physical_gpus: Dict[str, PhysicalGPU] = {}
        self.vgpu_instances: Dict[str, VGPUInstance] = {}
        self.allocations: Dict[str, GPUAllocation] = {}
        self.jobs: Dict[str, Dict] = {}  # job_id -> job_info
        self.discovery_error: Optional[str] = None
        self._last_discovery = 0.0
        self._refresh_discovered_gpus(force=True)

    @staticmethod
    def _number(value: str, default: float = 0.0) -> float:
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return default

    def _refresh_discovered_gpus(self, force: bool = False) -> None:
        """Discover real NVIDIA devices; never manufacture scheduler capacity."""
        now = time.monotonic()
        if not force and now - self._last_discovery < 5:
            return
        self._last_discovery = now
        command = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=3, check=True,
            )
        except FileNotFoundError:
            self.physical_gpus = {}
            self.discovery_error = "当前运行环境未安装或未挂载 NVIDIA 驱动"
            return
        except (subprocess.SubprocessError, OSError) as exc:
            self.physical_gpus = {}
            self.discovery_error = f"GPU探测失败：{str(exc)[:160]}"
            return

        discovered: Dict[str, PhysicalGPU] = {}
        for line in completed.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 7:
                continue
            index = int(self._number(parts[0], -1))
            if index < 0:
                continue
            gpu = PhysicalGPU(
                gpu_id=f"gpu-{index}",
                name=parts[1],
                total_memory_mb=int(self._number(parts[2])),
                device_index=index,
            )
            gpu.used_memory_mb = int(self._number(parts[3]))
            gpu.utilization_percent = self._number(parts[4])
            gpu.temperature_celsius = self._number(parts[5])
            gpu.power_watts = self._number(parts[6])
            gpu.status = GPUStatus.OCCUPIED if gpu.used_memory_mb > 0 else GPUStatus.AVAILABLE
            discovered[gpu.id] = gpu
        self.physical_gpus = discovered
        self.discovery_error = None if discovered else "未检测到可用的 NVIDIA GPU"

    def register_gpu(
        self, gpu_id: str, name: str, total_memory_mb: int, device_index: int
    ) -> PhysicalGPU:
        """注册物理GPU"""
        gpu = PhysicalGPU(gpu_id, name, total_memory_mb, device_index)
        self.physical_gpus[gpu_id] = gpu
        return gpu

    def unregister_gpu(self, gpu_id: str) -> bool:
        """注销GPU"""
        if gpu_id in self.physical_gpus:
            del self.physical_gpus[gpu_id]
            return True
        return False

    def get_gpu(self, gpu_id: str) -> Optional[PhysicalGPU]:
        """获取GPU信息"""
        self._refresh_discovered_gpus()
        return self.physical_gpus.get(gpu_id)

    def list_gpus(self, status: str = None) -> List[Dict]:
        """列出GPU"""
        self._refresh_discovered_gpus()
        gpus = self.physical_gpus.values()
        if status:
            gpus = [g for g in gpus if g.status == status]

        return [
            {
                "id": g.id,
                "name": g.name,
                "total_memory_mb": g.total_memory_mb,
                "used_memory_mb": g.used_memory_mb,
                "free_memory_mb": g.total_memory_mb - g.used_memory_mb,
                "utilization_percent": g.utilization_percent,
                "temperature_celsius": g.temperature_celsius,
                "power_watts": g.power_watts,
                "status": g.status,
                "device_index": g.device_index,
                "last_updated": g.last_updated,
            }
            for g in gpus
        ]

    def allocate_gpu(
        self,
        job_id: str,
        memory_mb: int,
        allocation_type: str = GPUAllocationType.FULL,
        user_id: str = None,
    ) -> Optional[Dict]:
        """分配GPU"""
        # 查找可用GPU
        available_gpu = None
        for gpu in self.physical_gpus.values():
            if gpu.status == GPUStatus.AVAILABLE:
                if allocation_type == GPUAllocationType.FULL:
                    if gpu.total_memory_mb - gpu.used_memory_mb >= memory_mb:
                        available_gpu = gpu
                        break
                elif allocation_type == GPUAllocationType.VGPU:
                    # vGPU可以多个共享
                    available_gpu = gpu
                    break

        if not available_gpu:
            return None

        # 创建分配记录
        allocation = GPUAllocation(
            job_id=job_id,
            gpu_id=available_gpu.id,
            allocation_type=allocation_type,
            memory_mb=memory_mb,
            user_id=user_id,
        )
        self.allocations[allocation.id] = allocation

        # 更新GPU状态
        available_gpu.used_memory_mb += memory_mb
        if allocation_type == GPUAllocationType.FULL:
            available_gpu.status = GPUStatus.OCCUPIED

        # 记录任务
        self.jobs[job_id] = {
            "id": job_id,
            "gpu_id": available_gpu.id,
            "memory_mb": memory_mb,
            "allocation_type": allocation_type,
            "status": "running",
            "created_at": allocation.created_at,
        }

        return {
            "allocation_id": allocation.id,
            "gpu_id": available_gpu.id,
            "job_id": job_id,
            "memory_mb": memory_mb,
            "device_index": available_gpu.device_index,
        }

    def release_gpu(self, job_id: str) -> bool:
        """释放GPU"""
        # 查找分配记录
        allocation = None
        for alloc in self.allocations.values():
            if alloc.job_id == job_id and alloc.released_at is None:
                allocation = alloc
                break

        if not allocation:
            return False

        # 释放GPU
        gpu = self.physical_gpus.get(allocation.gpu_id)
        if gpu:
            gpu.used_memory_mb -= allocation.memory_mb
            if gpu.used_memory_mb <= 0:
                gpu.used_memory_mb = 0
                gpu.status = GPUStatus.AVAILABLE

        # 更新分配记录
        allocation.released_at = datetime.now().isoformat()

        # 更新任务状态
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = "completed"

        return True

    def get_job_allocation(self, job_id: str) -> Optional[Dict]:
        """获取任务分配信息"""
        return self.jobs.get(job_id)

    def list_allocations(self, user_id: str = None) -> List[Dict]:
        """列出分配记录"""
        allocations = self.allocations.values()
        if user_id:
            allocations = [a for a in allocations if a.user_id == user_id]

        return [
            {
                "id": a.id,
                "job_id": a.job_id,
                "gpu_id": a.gpu_id,
                "allocation_type": a.allocation_type,
                "memory_mb": a.memory_mb,
                "user_id": a.user_id,
                "created_at": a.created_at,
                "released_at": a.released_at,
            }
            for a in allocations
        ]

    # vGPU管理
    def create_vgpu(
        self, name: str, gpu_id: str, memory_mb: int, user_id: str = None
    ) -> Optional[VGPUInstance]:
        """创建vGPU"""
        gpu = self.physical_gpus.get(gpu_id)
        if not gpu:
            return None

        # 检查显存是否足够
        available_memory = gpu.total_memory_mb - gpu.used_memory_mb
        if available_memory < memory_mb:
            return None

        vgpu = VGPUInstance(name, gpu_id, memory_mb, user_id)
        self.vgpu_instances[vgpu.id] = vgpu

        # 占用显存
        gpu.used_memory_mb += memory_mb

        return vgpu

    def delete_vgpu(self, vgpu_id: str) -> bool:
        """删除vGPU"""
        vgpu = self.vgpu_instances.get(vgpu_id)
        if not vgpu:
            return False

        # 释放显存
        gpu = self.physical_gpus.get(vgpu.gpu_id)
        if gpu:
            gpu.used_memory_mb -= vgpu.memory_mb
            if gpu.used_memory_mb <= 0:
                gpu.used_memory_mb = 0

        del self.vgpu_instances[vgpu_id]
        return True

    def list_vgpus(self, user_id: str = None) -> List[Dict]:
        """列出vGPU"""
        vgpus = self.vgpu_instances.values()
        if user_id:
            vgpus = [v for v in vgpus if v.user_id == user_id]

        return [
            {
                "id": v.id,
                "name": v.name,
                "gpu_id": v.gpu_id,
                "memory_mb": v.memory_mb,
                "user_id": v.user_id,
                "status": v.status,
                "created_at": v.created_at,
            }
            for v in vgpus
        ]

    # 监控统计
    def get_gpu_stats(self) -> Dict:
        """获取GPU统计信息"""
        self._refresh_discovered_gpus()
        total_gpus = len(self.physical_gpus)
        available_gpus = sum(
            1 for g in self.physical_gpus.values() if g.status == GPUStatus.AVAILABLE
        )
        occupied_gpus = sum(
            1 for g in self.physical_gpus.values() if g.status == GPUStatus.OCCUPIED
        )

        total_memory = sum(g.total_memory_mb for g in self.physical_gpus.values())
        used_memory = sum(g.used_memory_mb for g in self.physical_gpus.values())

        return {
            "total_gpus": total_gpus,
            "available_gpus": available_gpus,
            "occupied_gpus": occupied_gpus,
            "total_memory_mb": total_memory,
            "used_memory_mb": used_memory,
            "free_memory_mb": total_memory - used_memory,
            "utilization_percent": round(
                (used_memory / total_memory * 100) if total_memory > 0 else 0, 2
            ),
            "active_jobs": len(
                [j for j in self.jobs.values() if j.get("status") == "running"]
            ),
            "total_vgpus": len(self.vgpu_instances),
            "hardware_detected": total_gpus > 0,
            "data_source": "nvidia-smi" if total_gpus > 0 else None,
            "unavailable_reason": self.discovery_error,
        }

    # 更新GPU状态（模拟监控数据）
    def update_gpu_metrics(self, gpu_id: str, metrics: Dict):
        """更新GPU指标"""
        gpu = self.physical_gpus.get(gpu_id)
        if gpu:
            gpu.utilization_percent = metrics.get("utilization_percent", 0)
            gpu.temperature_celsius = metrics.get("temperature_celsius", 0)
            gpu.power_watts = metrics.get("power_watts", 0)
            gpu.last_updated = datetime.now().isoformat()


# 全局实例
_gpu_scheduler: Optional[GPUSchedulerService] = None


def get_gpu_scheduler_service() -> GPUSchedulerService:
    """获取GPU调度服务"""
    global _gpu_scheduler
    if _gpu_scheduler is None:
        _gpu_scheduler = GPUSchedulerService()
    return _gpu_scheduler
