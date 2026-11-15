"""仿真成果资产楼 API（FEAT-SIM-DATAHOUSE 子件1）。

背景：每次成功计算的场数据（.npz/.npy/.vtk/...）过去随 worktree 回收一起消失。
守护 ``scripts/sim_asset_harvest.py`` 负责逐轮抢收并登记到 ``simulation_assets``；
本模块只提供**读**面：分页 + 按 repo/case/state/mission 过滤，外加一行总账。

端点：
  GET /api/v1/assets/sim                 # 列表（分页 + 过滤）
  GET /api/v1/assets/sim/summary         # 按 repo/state 聚合的总账
  GET /api/v1/assets/sim/{event_id}      # 单个事件的完整清单（含 sha256）

为什么只读：登记表是「捕获事件」的不可变快照。开放写接口就等于允许任何人
在没有真实文件的情况下伪造证据行；写路径唯一入口是守护进程（它必须先算 sha256）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

router = APIRouter(prefix="/assets/sim", tags=["仿真成果资产"])
log = logging.getLogger(__name__)


def _engine():
    """延迟取 engine：import 失败/数据库不可达时端点返回 503 而不是让 app 起不来。"""
    from database.config import engine
    return engine


def _fetch(sql: str, params: dict | None = None) -> list[Any]:
    with _engine().connect() as conn:
        return list(conn.execute(text(sql), params or {}).fetchall())


def _fetch_scalar(sql: str, params: dict | None = None) -> Any:
    rows = _fetch(sql, params)
    return rows[0][0] if rows else None


@router.get("")
def list_simulation_assets(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数（上限 200）"),
    repo: Optional[str] = Query(None, description="按仓库过滤，如 TensorLBM"),
    case: Optional[str] = Query(None, description="按算例键过滤（case_key）"),
    state: Optional[str] = Query(None, description="captured | partial | missed"),
    mission_id: Optional[int] = Query(None, description="按 mission 过滤"),
    sort: str = Query("captured_at", description="captured_at|bytes|files|id"),
    order: str = Query("desc", description="asc|desc"),
) -> dict:
    from services import sim_asset_registry as reg

    filters = {"repo": repo, "case_key": case, "state": state, "mission_id": mission_id}
    try:
        sql, params = reg.build_list_sql(filters=filters, page=page, page_size=page_size,
                                         sort=sort, order=order)
        csql, cparams = reg.build_count_sql(filters)
    except reg.QueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        rows = _fetch(sql, params)
        total = _fetch_scalar(csql, cparams) or 0
    except Exception as exc:                      # 数据库不可达/表未迁移
        log.warning("assets/sim 查询失败: %s", exc)
        raise HTTPException(status_code=503, detail=f"资产库不可用: {exc}") from exc
    items = [reg.row_to_item(r) for r in rows]
    return reg.paginate(items, total=total, page=page, page_size=page_size)


@router.get("/summary")
def simulation_asset_summary() -> dict:
    from services import sim_asset_registry as reg

    try:
        rows = _fetch(reg.build_summary_sql())
    except Exception as exc:
        log.warning("assets/sim/summary 查询失败: %s", exc)
        raise HTTPException(status_code=503, detail=f"资产库不可用: {exc}") from exc
    repos: dict[str, dict] = {}
    totals = {"events": 0, "bytes": 0, "files": 0,
              "captured": 0, "partial": 0, "missed": 0}
    for repo, state, events, nbytes, files in rows:
        slot = repos.setdefault(repo, {"repo": repo, "events": 0, "bytes": 0, "files": 0,
                                       "captured": 0, "partial": 0, "missed": 0})
        for bucket in (slot, totals):
            bucket["events"] += int(events or 0)
            bucket["bytes"] += int(nbytes or 0)
            bucket["files"] += int(files or 0)
            if state in ("captured", "partial", "missed"):
                bucket[state] += int(events or 0)
    return {"totals": totals, "by_repo": sorted(repos.values(), key=lambda r: -r["bytes"])}


@router.get("/{event_id}")
def get_simulation_asset(event_id: str) -> dict:
    from services import sim_asset_registry as reg

    sql = ("SELECT id, event_id, mission_id, repo, case_key, params, field_manifest, "
           "curve_manifest, geometry_manifest, config_manifest, report_url, disk_path, "
           "bytes_total, file_count, state, capture_reason, source_worktree, captured_at, "
           "updated_at FROM simulation_assets WHERE event_id = :event_id")
    try:
        rows = _fetch(sql, {"event_id": event_id})
    except Exception as exc:
        log.warning("assets/sim/%s 查询失败: %s", event_id, exc)
        raise HTTPException(status_code=503, detail=f"资产库不可用: {exc}") from exc
    if not rows:
        raise HTTPException(status_code=404, detail=f"未登记的捕获事件: {event_id}")
    keys = ["id", "event_id", "mission_id", "repo", "case_key", "params",
            "field_manifest", "curve_manifest", "geometry_manifest", "config_manifest",
            "report_url", "disk_path", "bytes_total", "file_count", "state",
            "capture_reason", "source_worktree", "captured_at", "updated_at"]
    out = {}
    for key, value in zip(keys, rows[0]):
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out