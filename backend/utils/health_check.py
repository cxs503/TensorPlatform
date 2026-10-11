# -*- coding: utf-8 -*-
"""
健康检查配置
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

import psutil
from database.config import engine
from sqlalchemy import text


class HealthChecker:
    """健康检查器"""

    def __init__(self):
        self.health_status: Dict[str, Any] = {}
        self.last_check: Optional[datetime] = None

    async def check_database(self) -> Dict[str, Any]:
        """检查数据库连接"""
        result = {"status": "healthy", "database": "postgres"}

        try:
            with engine.connect() as conn:
                result_obj = conn.execute(text("SELECT 1"))
                result_obj.fetchone()

            result["connection"] = "ok"
            result["latency_ms"] = 0

        except Exception as e:
            result["status"] = "unhealthy"
            result["error"] = str(e)

        return result

    async def check_memory(self) -> Dict[str, Any]:
        """检查内存使用"""
        mem = psutil.virtual_memory()
        result = {
            "status": "healthy",
            "used_mb": round(mem.used / 1024 / 1024, 2),
            "total_mb": round(mem.total / 1024 / 1024, 2),
            "percent": mem.percent,
            "available_mb": round(mem.available / 1024 / 1024, 2),
        }

        if mem.percent > 90:
            result["status"] = "degraded"
            result["warning"] = "Memory usage > 90%"

        return result

    async def check_disk(self) -> Dict[str, Any]:
        """检查磁盘使用"""
        disk = psutil.disk_usage("/")
        result = {
            "status": "healthy",
            "used_gb": round(disk.used / 1024 / 1024 / 1024, 2),
            "total_gb": round(disk.total / 1024 / 1024 / 1024, 2),
            "percent": disk.percent,
        }

        if disk.percent > 85:
            result["status"] = "degraded"
            result["warning"] = "Disk usage > 85%"

        return result

    async def check_cpu(self) -> Dict[str, Any]:
        """检查 CPU 使用"""
        cpu_percent = psutil.cpu_percent(interval=0.5)
        result = {
            "status": "healthy",
            "percent": cpu_percent,
            "cores": psutil.cpu_count(),
        }

        if cpu_percent > 90:
            result["status"] = "degraded"
            result["warning"] = "CPU usage > 90%"

        return result

    async def check_services(self) -> Dict[str, Any]:
        """检查依赖服务"""
        result = {"redis": {"status": "unknown"}, "celery": {"status": "unknown"}}

        try:
            import redis

            r = redis.from_url("redis://localhost:6379/0")
            r.ping()
            result["redis"]["status"] = "healthy"
        except Exception as e:
            result["redis"]["status"] = "unhealthy"
            result["redis"]["error"] = str(e)

        return result

    async def check(self) -> Dict[str, Any]:
        """执行完整的健康检查"""
        self.last_check = datetime.utcnow()

        checks = {
            "database": await self.check_database(),
            "memory": await self.check_memory(),
            "disk": await self.check_disk(),
            "cpu": await self.check_cpu(),
            "services": await self.check_services(),
        }

        # 确定总体状态
        overall_status = "healthy"
        for check_name, check_result in checks.items():
            if check_result.get("status") == "unhealthy":
                overall_status = "unhealthy"
                break
            elif (
                check_result.get("status") == "degraded" and overall_status == "healthy"
            ):
                overall_status = "degraded"

        self.health_status = {
            "status": overall_status,
            "timestamp": self.last_check.isoformat(),
            "version": "1.0.0",
            "checks": checks,
        }

        return self.health_status

    def get_status(self) -> Dict[str, Any]:
        """获取健康状态"""
        return self.health_status or {
            "status": "unknown",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0",
        }


# 全局实例
health_checker = HealthChecker()


async def get_health_status() -> Dict[str, Any]:
    """获取健康状态（FastAPI 端点）"""
    return await health_checker.check()


if __name__ == "__main__":
    import asyncio

    async def test():
        status = await health_checker.check()
        print("\n健康检查结果:")
        print(f"总体状态: {status['status']}")
        print(f"时间: {status['timestamp']}")
        print(f"版本: {status['version']}")
        print("\n详细检查:")
        for check_name, check_result in status["checks"].items():
            print(f"  {check_name}: {check_result['status']}")

    asyncio.run(test())
