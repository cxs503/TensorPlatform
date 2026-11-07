"""审查预算周聚合任务（SPEC §6.5）：Celery 周期任务外壳。

调度：`review_budget.weekly_aggregate`，周期 604800 s（7 天），对**上一个完整 ISO 周**
聚合账本并把周报落 docs/governance/、摘要落 artifacts/。本期只测量不限额：
该任务不改动任何审查策略、不拦截任何派发。

真正的聚合逻辑在 services/review_budget_weekly.py（不依赖 Celery，可直接复算）；
本模块只在 Celery 可用时把同一函数注册成周期任务（CI/无 Redis 环境降级为纯函数）。
"""
from __future__ import annotations

from typing import Any, Optional

from services.review_budget_weekly import (
    WEEKLY_INTERVAL_S,
    WEEKLY_TASK_NAME,
    write_weekly_report,
)

__all__ = ["WEEKLY_TASK_NAME", "WEEKLY_INTERVAL_S", "weekly_aggregate", "celery_task"]


def weekly_aggregate(
    week_start: Optional[str] = None, ledger_path: Optional[str] = None
) -> Any:
    return write_weekly_report(week_start=week_start, ledger_path=ledger_path)


def _register_celery() -> Optional[Any]:
    try:
        from tasks.celery_app import celery_app
    except Exception:  # pragma: no cover - 无 Celery/Redis 的环境（CI）如实降级
        return None
    task = celery_app.task(name=WEEKLY_TASK_NAME)(weekly_aggregate)
    schedule = dict(celery_app.conf.beat_schedule or {})
    schedule.setdefault(
        "review-budget-weekly-aggregate",
        {"task": WEEKLY_TASK_NAME, "schedule": WEEKLY_INTERVAL_S},
    )
    celery_app.conf.beat_schedule = schedule
    return task


celery_task = _register_celery()