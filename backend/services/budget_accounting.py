"""Budget accounting for dev missions — FP15-BUDGET-ACCOUNTING.

Problem (FP15 实测，24h 全量 SQL 复算)
--------------------------------------
终态单里 ``budget_used`` 100% 为空 → 终局裁决（``budget_exhausted`` /
``no_progress``）无法被外部复核：没有"花了多少"，就没有"为什么判定耗尽"。

本模块把一次终态转移里"花了多少"落成可复算的四元组

    {"tool_calls": int, "tokens": int, "wall_seconds": int, "source": str}

设计纪律
--------
* **纯函数优先**：``derive_budget_used`` 无 IO、永不抛异常。空/缺字段/脏类型
  （负数、NaN、字符串、bool）一律降级为 0 + 来源 ``unavailable``——记账不能
  变成新的终态单点故障（fail-open，与 eval/记忆钩子同款纪律）。
* **source 逐字段写清出处**：审计必须能区分"agent 自报"与"服务端从 result /
  heartbeat 事实回算"，只有后者可被交叉验证；把两者混成一句 ``declared``
  等于把证据等级抹平。
* **零领域语义**：只认 mission/计数字段，不含任何领域词条（夹具用合成 task_key）。

出处优先级（高 → 低）
--------------------
* ``tool_calls``  : usage 声明 → result.tool_calls → result.progress.tool_calls
                   → result.detail 里的 ``tool_calls=N``（老 runner 的字符串）
* ``tokens``      : usage 声明 → result.tokens → result.progress.tokens
* ``wall_seconds``: usage 声明 → result.wall_seconds → result.progress.wall_seconds
                   → 服务端时钟（now - created_at，含排队时间的上界）

兼容：同时写入 ``tool_calls_used`` / ``tokens_used`` / ``wall_seconds_used``
三个历史键——suspend/resume 的预算续算与 write-off 的 credit_ledger 都读老名字。
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import text

#: 契约键（顺序即 source 字符串的字段顺序；验收按此四键判空）
BUDGET_USED_KEYS = ("tool_calls", "tokens", "wall_seconds", "source")

#: 逐字段来源标签
SOURCE_AGENT_DECLARED = "agent_declared"
SOURCE_RESULT_FACT = "result_fact"
SOURCE_HEARTBEAT_PROGRESS = "heartbeat_progress"
SOURCE_RUNNER_DETAIL = "runner_detail"
SOURCE_SERVER_CLOCK = "server_clock_upper_bound"
SOURCE_UNAVAILABLE = "unavailable"

_DETAIL_TOOL_CALLS = re.compile(r"tool_calls\s*[=:]\s*(\d+)")


def _count(value: Any) -> int | None:
    """Coerce a candidate count to a non-negative int, else None (never raises)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value) or value < 0:
            return None
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            number = float(raw)
        except ValueError:
            return None
        if math.isnan(number) or math.isinf(number) or number < 0:
            return None
        return int(number)
    return None


def _detail_tool_calls(detail: Any) -> int | None:
    """``"tool_calls=80"``（老 runner 的 detail 字符串）→ 80；其它 → None。"""
    if isinstance(detail, dict):
        return _count(detail.get("tool_calls"))
    if not isinstance(detail, str):
        return None
    match = _DETAIL_TOOL_CALLS.search(detail)
    return int(match.group(1)) if match else None


def as_mapping(value: Any) -> Mapping[str, Any]:
    """jsonb 列可能以 str 回读（sqlite / 老驱动）——统一成 mapping。"""
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (str, bytes)):
        try:
            loaded = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return loaded if isinstance(loaded, Mapping) else {}
    return {}


def derive_budget_used(*, result: Any = None, declared: Any = None,
                       measured_wall_seconds: Any = None,
                       recorded_at: str | None = None) -> dict[str, Any]:
    """Derive the persisted ``budget_used`` record from result facts.

    ``result``   : mission.result (jsonb) —— 可含 tool_calls / tokens /
                   wall_seconds / progress{tool_calls,...} / detail 字符串
    ``declared`` : agent 在 /complete 里自报的 usage 块（最高优先级）
    ``measured_wall_seconds`` : 服务端时钟测得的上界（含排队），最后兜底
    """
    result_map = as_mapping(result)
    declared_map = as_mapping(declared)
    progress = result_map.get("progress")
    progress_map = progress if isinstance(progress, Mapping) else {}

    candidates: dict[str, list[tuple[Any, str]]] = {
        "tool_calls": [
            (declared_map.get("tool_calls"), SOURCE_AGENT_DECLARED),
            (result_map.get("tool_calls"), SOURCE_RESULT_FACT),
            (progress_map.get("tool_calls"), SOURCE_HEARTBEAT_PROGRESS),
            (_detail_tool_calls(result_map.get("detail")), SOURCE_RUNNER_DETAIL),
        ],
        "tokens": [
            (declared_map.get("tokens"), SOURCE_AGENT_DECLARED),
            (result_map.get("tokens"), SOURCE_RESULT_FACT),
            (progress_map.get("tokens"), SOURCE_HEARTBEAT_PROGRESS),
        ],
        "wall_seconds": [
            (declared_map.get("wall_seconds"), SOURCE_AGENT_DECLARED),
            (result_map.get("wall_seconds"), SOURCE_RESULT_FACT),
            (progress_map.get("wall_seconds"), SOURCE_HEARTBEAT_PROGRESS),
            (measured_wall_seconds, SOURCE_SERVER_CLOCK),
        ],
    }

    record: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for field in ("tool_calls", "tokens", "wall_seconds"):
        value, source = 0, SOURCE_UNAVAILABLE
        for raw, label in candidates[field]:
            count = _count(raw)
            if count is None:
                continue
            # 0 秒墙钟没有信息量：真跑过的单必有耗时，故墙钟只接受 >=1s。
            if field == "wall_seconds" and count <= 0:
                continue
            value, source = count, label
            break
        record[field] = value
        sources[field] = source

    record["source"] = ";".join(f"{f}={sources[f]}" for f in ("tool_calls", "tokens", "wall_seconds"))
    record["sources"] = dict(sources)
    # 历史键（suspend/resume 续算 + write-off credit_ledger 读这三个名字）
    record["tool_calls_used"] = record["tool_calls"]
    record["tokens_used"] = record["tokens"]
    record["wall_seconds_used"] = record["wall_seconds"]
    if recorded_at:
        record["recorded_at"] = recorded_at
    return record


def _seconds_since(value: Any, now: datetime) -> int:
    """now - created_at 的秒数（naive 时间按 UTC 解释）；不可解析 → 0。"""
    if value is None:
        return 0
    stamp = value
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if not isinstance(stamp, datetime):
        return 0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0, int((now - stamp).total_seconds()))


def budget_used_for_row(row: Any, *, declared: Any = None,
                        now: datetime | None = None) -> dict[str, Any]:
    """Derive ``budget_used`` for a mission row mapping (no DB write).

    ``row`` 可以是 SQLAlchemy ``RowMapping`` 或普通 dict；缺列按空处理。
    """
    getter = row.get if hasattr(row, "get") else (lambda key, default=None: default)
    result = getter("result", None)
    created_at = getter("created_at", None)
    moment = now or datetime.now(timezone.utc)
    measured = _seconds_since(created_at, moment)
    return derive_budget_used(
        result=result,
        declared=declared,
        measured_wall_seconds=measured,
        recorded_at=moment.replace(microsecond=0).isoformat(),
    )


def dumps(record: Mapping[str, Any]) -> str:
    """JSON text for a ``CAST(:bu AS jsonb)`` parameter."""
    return json.dumps(dict(record), ensure_ascii=False, default=str)


def record_budget_used(db: Any, mission_id: int, *, row: Any = None,
                       declared: Any = None) -> dict[str, Any]:
    """Persist ``budget_used`` for one mission without touching other columns.

    Used by terminal paths that only flip status (stop / lazy-reap / write-off
    backfill of a legacy row). Returns the record it wrote.
    """
    if row is None:
        row = db.execute(
            text("SELECT id, result, budget, created_at FROM tensor_dev_missions WHERE id = :mid"),
            {"mid": mission_id},
        ).mappings().first()
    if row is None:
        return derive_budget_used(declared=declared)
    record = budget_used_for_row(row, declared=declared)
    db.execute(
        text("UPDATE tensor_dev_missions SET budget_used = CAST(:bu AS jsonb) WHERE id = :mid"),
        {"mid": mission_id, "bu": dumps(record)},
    )
    return record


def usage_is_recorded(record: Any) -> bool:
    """True iff a ``budget_used`` blob carries the four contract keys."""
    record_map = as_mapping(record)
    return all(key in record_map for key in BUDGET_USED_KEYS)


def default_window(hours: float = 24.0, *, now: datetime | None = None) -> tuple[str, str]:
    """ISO window ``(since, until)`` used by the cap-curve / intel scripts."""
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(hours=hours)
    return since.replace(microsecond=0).isoformat(), moment.replace(microsecond=0).isoformat()