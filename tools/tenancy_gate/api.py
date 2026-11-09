#!/usr/bin/env python3
"""冲突台账的只读查询 API（框架无关核心 + 可选 FastAPI 路由）。

纪律
----
* **纯只读**：只读 JSONL 台账，不追加、不改写；写入一律走 :mod:`.ledger`。
* **框架无关核心**：:func:`build_query` / :func:`build_verify` / :func:`build_recompute`
  是普通函数（可直接单测、可被任何网关调用）；:func:`create_router` 只在需要 HTTP 时
  才导入 FastAPI，导入失败不影响核心可用。
* **零领域语义**：过滤维度只有 ``kind / repo / mission_id / 时间窗``，全部是平台通用概念。
* 鉴权与限流由宿主应用承担（本模块不实现，也不读任何密钥）。
"""
from __future__ import annotations

from pathlib import Path

from .config import load_config
from .ledger import query_events, read_events, recompute_matrix, verify_chain
from .model import digest

__all__ = ["build_query", "build_verify", "build_recompute", "create_router"]


def build_query(ledger_path: str | Path, kind=None, repo=None, mission_id=None,
                since=None, until=None, limit: int = 200) -> dict:
    events = query_events(ledger_path, kind=kind, repo=repo, mission_id=mission_id,
                          since=since, until=until)
    total = len(events)
    items = events[-limit:] if limit and total > limit else events
    return {
        "schema": "tenancy_gate/query/v1",
        "filter": {"kind": kind, "repo": repo, "mission_id": mission_id,
                   "since": since, "until": until, "limit": limit},
        "total": total,
        "returned": len(items),
        "items": items,
        "digest": digest(items),
    }


def build_verify(ledger_path: str | Path) -> dict:
    result = verify_chain(ledger_path)
    return {"schema": "tenancy_gate/verify/v1", **result}


def build_recompute(ledger_path: str | Path, event_id: str, config: dict | None = None) -> dict:
    cfg = config or load_config(None)
    for event in read_events(ledger_path):
        if event.get("event_id") == event_id:
            out = recompute_matrix(event, cfg)
            return {"schema": "tenancy_gate/recompute/v1", "event_id": event_id, **out}
    return {"schema": "tenancy_gate/recompute/v1", "event_id": event_id,
            "error": "event_not_found", "match": False}


def create_router(ledger_path: str | Path):
    """可选 FastAPI 路由（宿主应用挂载；默认只读、无鉴权，需由宿主加认证中间件）。"""
    from fastapi import APIRouter, HTTPException, Query  # 延迟导入，核心不依赖 FastAPI

    router = APIRouter(prefix="/tenancy/conflicts", tags=["tenancy"])

    @router.get("")
    def list_conflicts(kind: str | None = None, repo: str | None = None,
                       mission_id: str | None = None, since: str | None = None,
                       until: str | None = None, limit: int = Query(200, ge=1, le=5000)):
        return build_query(ledger_path, kind=kind, repo=repo, mission_id=mission_id,
                           since=since, until=until, limit=limit)

    @router.get("/verify")
    def verify():
        return build_verify(ledger_path)

    @router.post("/recompute")
    def recompute(event_id: str):
        result = build_recompute(ledger_path, event_id)
        if result.get("error"):
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    return router