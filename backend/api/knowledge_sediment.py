"""KE 检索接口：把沉淀入库的四契约知识暴露成可检索的 API。

端点（挂在 ``/api/v1`` 下 → ``/api/v1/knowledge/...``）
-----------------------------------------------------
* ``GET  /knowledge/search?q=&domain=&limit=`` —— 检索（q 可空 = 按域列最近条目）
* ``GET  /knowledge/items/{entry_id}``         —— 单条知识（含溯源文件 sha256）
* ``GET  /knowledge/stats``                    —— 库概况 + schema 完整率 + 拒绝原因分布
* ``GET  /knowledge/rejected``                 —— 拒绝入库账（为什么没收）
* ``POST /knowledge/scan``                     —— 触发一次沉淀扫描（默认 dry_run）

设计纪律
--------
* **零领域语义**：本模块没有域词表；``domain`` 只是透传的字符串过滤条件。
* **诚实降级**：库文件缺失/损坏 → ``count=0`` + ``reason``，不抛 500、不编数字。
* 写路径（``/scan``）需要鉴权；读路径同样走 ``require_auth``（与平台其余台面一致）。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import require_auth
from services.knowledge_sediment import KnowledgeStore, scan_root, store_dir

router = APIRouter(prefix="/knowledge", tags=["知识沉淀·四契约检索"])


class ScanRequest(BaseModel):
    root: str | None = Field(default=None, description="待扫根目录（默认取 KM_SEDIMENT_WORK_ROOT）")
    limit: int | None = Field(default=None, ge=1, le=5000)
    dry_run: bool = True
    domain_map_path: str | None = None


def _store() -> KnowledgeStore:
    return KnowledgeStore(store_dir())


@router.get("/search")
def search_knowledge(
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=500),
    _u: dict = Depends(require_auth),
) -> dict[str, Any]:
    """四契约知识检索。返回命中条目 + 自报耗时（``took_ms``）。"""
    try:
        store = _store()
    except Exception as exc:  # 库不可读 → 降级，不抛 500
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "items": [], "total": 0}
    payload = store.search(q=q, domain=domain, limit=limit)
    payload["available"] = True
    payload["store_dir"] = str(store.dir)
    return payload


@router.get("/items/{entry_id}")
def get_knowledge(entry_id: str, _u: dict = Depends(require_auth)) -> dict[str, Any]:
    store = _store()
    entry = store.get(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"knowledge entry not found: {entry_id}")
    return entry


@router.get("/stats")
def knowledge_store_stats(_u: dict = Depends(require_auth)) -> dict[str, Any]:
    store = _store()
    stats = store.stats()
    stats["available"] = True
    stats["store_dir"] = str(store.dir)
    return stats


@router.get("/rejected")
def rejected_knowledge(
    limit: int = Query(default=50, ge=1, le=500),
    _u: dict = Depends(require_auth),
) -> dict[str, Any]:
    """拒绝入库账：无证据/契约不全的构造件在这里留痕（审计「为什么没收」）。"""
    store = _store()
    rows = store.rejected(limit=limit)
    return {"available": True, "count": len(rows), "items": rows}


@router.post("/scan")
def trigger_scan(body: ScanRequest, _u: dict = Depends(require_auth)) -> dict[str, Any]:
    """触发一次沉淀扫描（默认 dry_run=True，避免误写生产库）。"""
    root = body.root or os.environ.get("KM_SEDIMENT_WORK_ROOT")
    if not root:
        raise HTTPException(status_code=400, detail="root 未指定且 KM_SEDIMENT_WORK_ROOT 未配置")
    from services.knowledge_sediment.extractor import load_domain_map

    domain_map = load_domain_map(body.domain_map_path)
    summary = scan_root(
        root=root,
        store=KnowledgeStore(store_dir()),
        limit=body.limit,
        domain_map=domain_map,
        dry_run=body.dry_run,
    )
    summary.pop("rows", None) if body.dry_run else None
    return summary