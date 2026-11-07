"""
KPI Guide Tracker API — GET /tensor/kpi/guide_tracker

返回 12 项 KPI 实时值，从 tensor_dev_missions / tensor_reports / ship_samples /
metered_usage / integrity_events 表计算。每项 KPI 含 current / target / pct。

缓存策略：内存缓存 TTL 300 秒（5 分钟），避免全表扫描导致超时。
"""
from __future__ import annotations

import time
import threading
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from database.config import get_db

router = APIRouter(prefix="/tensor/kpi", tags=["KPI 指标追踪"])

# ── 内存缓存层（TTL 5 分钟） ──────────────────────────────────────────
_CACHE: dict[str, Any] = {}
_CACHE_TS: float = 0.0
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 300  # seconds


def _get_cached() -> dict[str, Any] | None:
    with _CACHE_LOCK:
        if _CACHE and (time.time() - _CACHE_TS) < CACHE_TTL:
            return dict(_CACHE)
    return None


def _set_cached(data: dict[str, Any]) -> None:
    global _CACHE, _CACHE_TS
    with _CACHE_LOCK:
        _CACHE = dict(data)
        _CACHE_TS = time.time()


# ── KPI 定义 ─────────────────────────────────────────────────────────
# 12 项 KPI，每项: {key, label, current, target, pct}
# target 为预设目标值，pct = current / target * 100（target=0 时 pct=0）

KPI_TARGETS = {
    "total_missions":        1000,
    "active_missions":       50,
    "completed_missions":    500,
    "failed_missions":       20,
    "total_reports":         800,
    "published_reports":     400,
    "total_samples":         2000,
    "passed_samples":        1500,
    "mission_success_rate":  90,    # percentage target
    "avg_mission_duration":  3600,  # seconds target
    "total_tool_calls":      50000,
    "integrity_events":      5,     # target = max acceptable
}

KPI_LABELS = {
    "total_missions":        "任务总数",
    "active_missions":       "进行中任务",
    "completed_missions":    "已完成任务",
    "failed_missions":       "失败任务",
    "total_reports":         "报告总数",
    "published_reports":     "已发布报告",
    "total_samples":         "样本总数",
    "passed_samples":        "通过验证样本",
    "mission_success_rate":  "任务成功率(%)",
    "avg_mission_duration":  "平均任务耗时(秒)",
    "total_tool_calls":      "总工具调用次数",
    "integrity_events":      "完整性事件(未解决)",
}


def _safe_pct(current: float, target: float) -> float:
    """计算百分比，target=0 时返回 0。"""
    if target == 0:
        return 0.0
    return round(current / target * 100, 2)


def _compute_kpis(db: Session) -> list[dict[str, Any]]:
    """从数据库计算 12 项 KPI 值。"""
    # 检测方言以选择兼容的 SQL
    dialect_name = db.bind.dialect.name if db.bind else "postgresql"
    is_sqlite = dialect_name == "sqlite"

    # 1. total_missions
    r = db.execute(text("SELECT COUNT(*) FROM tensor_dev_missions")).scalar()
    total_missions = int(r or 0)

    # 2. active_missions (claimed or running)
    r = db.execute(text(
        "SELECT COUNT(*) FROM tensor_dev_missions WHERE status IN ('claimed', 'running')"
    )).scalar()
    active_missions = int(r or 0)

    # 3. completed_missions
    r = db.execute(text(
        "SELECT COUNT(*) FROM tensor_dev_missions WHERE status = 'completed'"
    )).scalar()
    completed_missions = int(r or 0)

    # 4. failed_missions
    r = db.execute(text(
        "SELECT COUNT(*) FROM tensor_dev_missions WHERE status = 'failed'"
    )).scalar()
    failed_missions = int(r or 0)

    # 5. total_reports
    r = db.execute(text("SELECT COUNT(*) FROM tensor_reports")).scalar()
    total_reports = int(r or 0)

    # 6. published_reports
    r = db.execute(text(
        "SELECT COUNT(*) FROM tensor_reports WHERE status = 'published'"
    )).scalar()
    published_reports = int(r or 0)

    # 7. total_samples
    r = db.execute(text("SELECT COUNT(*) FROM ship_samples")).scalar()
    total_samples = int(r or 0)

    # 8. passed_samples
    if is_sqlite:
        r = db.execute(text(
            "SELECT COUNT(*) FROM ship_samples WHERE passed = 1"
        )).scalar()
    else:
        r = db.execute(text(
            "SELECT COUNT(*) FROM ship_samples WHERE passed = true"
        )).scalar()
    passed_samples = int(r or 0)

    # 9. mission_success_rate = completed / (completed + failed) * 100
    terminal = completed_missions + failed_missions
    if terminal > 0:
        mission_success_rate = round(completed_missions / terminal * 100, 2)
    else:
        mission_success_rate = 0.0

    # 10. avg_mission_duration (seconds) — 从 completed 任务的 heartbeat 推算
    # SQLite 不支持 EXTRACT(EPOCH FROM ...)，使用 strftime('%s', ...) 替代
    if is_sqlite:
        r = db.execute(text(
            "SELECT AVG(strftime('%s', completed_at) - strftime('%s', created_at)) "
            "FROM tensor_dev_missions WHERE status = 'completed' AND completed_at IS NOT NULL"
        )).scalar()
    else:
        r = db.execute(text(
            "SELECT AVG(EXTRACT(EPOCH FROM (completed_at - created_at))) "
            "FROM tensor_dev_missions WHERE status = 'completed' AND completed_at IS NOT NULL"
        )).scalar()
    avg_mission_duration = round(float(r or 0), 2)

    # 11. total_tool_calls
    r = db.execute(text("SELECT COALESCE(SUM(tool_calls), 0) FROM metered_usage")).scalar()
    total_tool_calls = int(r or 0)

    # 12. integrity_events (unresolved)
    if is_sqlite:
        r = db.execute(text(
            "SELECT COUNT(*) FROM integrity_events WHERE resolved = 0"
        )).scalar()
    else:
        r = db.execute(text(
            "SELECT COUNT(*) FROM integrity_events WHERE resolved = false"
        )).scalar()
    integrity_events = int(r or 0)

    # 组装 12 项 KPI
    currents = {
        "total_missions": total_missions,
        "active_missions": active_missions,
        "completed_missions": completed_missions,
        "failed_missions": failed_missions,
        "total_reports": total_reports,
        "published_reports": published_reports,
        "total_samples": total_samples,
        "passed_samples": passed_samples,
        "mission_success_rate": mission_success_rate,
        "avg_mission_duration": avg_mission_duration,
        "total_tool_calls": total_tool_calls,
        "integrity_events": integrity_events,
    }

    kpis = []
    for key in KPI_TARGETS:
        current = currents[key]
        target = KPI_TARGETS[key]
        pct = _safe_pct(current, target)
        kpis.append({
            "key": key,
            "label": KPI_LABELS[key],
            "current": current,
            "target": target,
            "pct": pct,
        })

    return kpis


@router.get("/guide_tracker")
def get_guide_tracker(db: Session = Depends(get_db), _user: dict = Depends(require_auth)):
    """
    返回 12 项 KPI 实时值（从 missions/samples/reports 表计算）。
    每项含 current / target / pct。
    使用内存缓存（TTL 5 分钟）避免全表扫描超时。
    """
    cached = _get_cached()
    if cached is not None:
        return cached

    kpis = _compute_kpis(db)
    result = {"kpis": kpis, "count": len(kpis), "cached": False}
    _set_cached(result)
    return result
