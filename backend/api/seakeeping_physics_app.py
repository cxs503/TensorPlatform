"""独立 FastAPI 应用：把耐波性三端点物理实现挂到与线上同构的 ``/api/v1`` 前缀。

用于在隔离端口上做机器裁决（``tools/seakeeping_audit/audit_seakeeping.py``）
与 pytest（TestClient），不触碰线上 :8001 进程：

    uvicorn api.seakeeping_physics_app:app --host 127.0.0.1 --port 8021
"""
from __future__ import annotations

from fastapi import FastAPI

try:                                     # 包内导入
    from api.seakeeping_physical_api import router as seakeeping_router
except Exception:                        # 独立模块路径（离线裁决面）
    from seakeeping_physical_api import router as seakeeping_router

app = FastAPI(title="Ship performance — seakeeping physics surface", version="1.0.0")
app.include_router(seakeeping_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "surface": "seakeeping-physics", "endpoints":
            ["/api/v1/seakeeping/rao", "/api/v1/seakeeping/motion",
             "/api/v1/seakeeping-advanced/strip-theory"]}