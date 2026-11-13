#!/usr/bin/env python3
"""锚点查重门（M-R17-01）——调度前**只读**预检的纯函数内核。

背景（可复算基线 artifacts/r17/intel_summary.json）：200 单窗口内 169 单带
``【锚点=X】``，聚成 118 个锚点组，其中 48 组重复、剔除返修/交差审计/重开
派生后仍有 28 个独立重复组，白烧 28 个席位。平台此前无调度前查重门
（``tools/`` 里 anchor_dedup|同锚点|anchor_registry 只有语义无关命中）。

本模块只回答一个问题：**给定一个锚点，此刻再派一单是否与既有单撞车**。
它不派单、不写库、不改状态，也不阻断任何派单路径（advisory only，见
``docs/ANCHOR_DEDUP_GATE.md`` §5）。判定结果由调用方决定如何使用。

规则（与任务书 §2 对齐，逐条有测试锚定）：

* 同锚点**在飞**（``in_flight``）→ ``reject``；
* 同锚点 **24h 内已交付**（``recent_terminal`` 里 ``succeeded`` 且落在窗口内）
  → ``reject``；
* 同锚点 **24h 内 failed** → ``allow``（重试是合法动作，不得当成重复）;
* **派生单**（``parent_mission_id`` 非空，或 goal 带 返修/交差审计/重开/管到底
  标记）→ ``exempt``，放行且不参与冲突计数——返修单天然与父单同锚点。

零领域语义铁律：本模块不认识任何具体领域名词（锚点、名称、标签全来自
mission 数据：``anchor`` 字段或 goal 里的 ``【锚点=...】`` 标签）。派生
标记是平台治理词表而非领域词表（返修/交差审计/重开/管到底 是任务机自己的
派单派生语汇）。

锚点比较先做归一化（NFKC 全半角、大小写、空白、连字符变体），否则
``Wave-Slope`` / ``wave slope`` / ``WAVE—SLOPE`` 会被当成三个不同锚点。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

GATE_ID = "anchor-dedup-gate-v1"
DEFAULT_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 720  # 30 天，与 /task-key-conflicts 的 hours 上限同口径

#: ``【锚点=X】`` 标签；容忍 全角等号/冒号、锚点前后的空白与大小写。
ANCHOR_TAG_RE = re.compile(r"【\s*锚\s*点\s*[=＝:：]\s*([^】]+?)\s*】")

#: 派生单标记（平台派单语汇，不是领域词表）：
#: 返修=对已交付单的修补；交差审计=复核单；重开=被 reopen 的再派；管到底=续派。
DERIVED_MARKERS: tuple[str, ...] = ("返修", "交差审计", "重开", "管到底")

#: 各种 Unicode 连字符/破折号 + 半角连字符，归一成一个 ``-``。
_DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u30fc\ufe63"

#: 「已交付」= 不得用同锚点再派一次的状态。
DELIVERED_STATUSES = frozenset({"succeeded"})
#: 「可重试」= 上一单失败/被撤，重试合法。
RETRYABLE_STATUSES = frozenset({"failed", "stopped", "revoked", "orphan"})

DECISIONS = ("allow", "exempt", "reject")

__all__ = [
    "GATE_ID",
    "DEFAULT_WINDOW_HOURS",
    "MAX_WINDOW_HOURS",
    "ANCHOR_TAG_RE",
    "DERIVED_MARKERS",
    "DELIVERED_STATUSES",
    "RETRYABLE_STATUSES",
    "DECISIONS",
    "normalize_anchor",
    "extract_anchor",
    "is_derived",
    "gate",
    "describe",
]


# ── 归一化 ────────────────────────────────────────────────────────────────
def normalize_anchor(value: Any) -> str:
    """把锚点归一到可比较形态：NFKC → 连字符统一 → 去空白 → 小写。

    不做「去连字符」这种激进折叠：``A-B`` 与 ``AB`` 是两个不同锚点，
    误合并会把不同工作判成重复（那是比重复更贵的错）。
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.strip().strip("【】").strip()
    for ch in _DASH_CHARS:
        text = text.replace(ch, "-")
    text = re.sub(r"\s+", "", text)
    return text.lower()


def extract_anchor(goal: Any) -> str | None:
    """从 mission goal 文本里取 ``【锚点=X】``；没有标签返回 ``None``。"""
    if not goal:
        return None
    match = ANCHOR_TAG_RE.search(str(goal))
    if not match:
        return None
    anchor = match.group(1).strip()
    return anchor or None


def is_derived(mission: Mapping[str, Any] | None,
               markers: Sequence[str] = DERIVED_MARKERS) -> bool:
    """判断是否派生单（返修/交差审计/重开/管到底，或有父单）。"""
    if not mission:
        return False
    parent = mission.get("parent_mission_id")
    if parent not in (None, "", 0, "0"):
        return True
    goal = str(mission.get("goal") or "")
    return any(marker in goal for marker in markers)


def _anchor_of(mission: Mapping[str, Any] | Any) -> str:
    """取 mission 的锚点：``anchor`` 字段优先，否则从 goal 标签解析。"""
    if isinstance(mission, (int, str)):
        # 调用方只给了 mission id（已知归属该锚点）——按「同锚点」处理。
        return "\x00<id-only>"
    anchor = mission.get("anchor")
    if anchor:
        return normalize_anchor(anchor)
    return normalize_anchor(extract_anchor(mission.get("goal")))


def _coerce_dt(value: Any) -> datetime | None:
    """宽松解析时间：datetime / epoch 数字 / ISO 字符串 / psql 文本。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _when(mission: Mapping[str, Any]) -> datetime | None:
    """终态单的完成时刻；缺 ``finished_at`` 时退回 ``created_at``。"""
    return _coerce_dt(mission.get("finished_at")) or _coerce_dt(mission.get("created_at"))


def _mission_id(mission: Mapping[str, Any] | Any) -> Any:
    """mission id 归一成 int（psql/文本通道可能给出 '18051' 这样的字符串）。"""
    mid = mission.get("id") if isinstance(mission, Mapping) else mission
    if isinstance(mid, str) and mid.strip().isdigit():
        return int(mid.strip())
    return mid


def _status(mission: Mapping[str, Any]) -> str:
    return str(mission.get("status") or "").strip().lower()


# ── 判定内核 ──────────────────────────────────────────────────────────────
def gate(anchor: Any,
         in_flight: Iterable[Mapping[str, Any] | int] = (),
         recent_terminal: Iterable[Mapping[str, Any]] = (),
         now: datetime | None = None,
         window_hours: int | float = DEFAULT_WINDOW_HOURS,
         *,
         candidate: Mapping[str, Any] | None = None,
         markers: Sequence[str] = DERIVED_MARKERS) -> dict[str, Any]:
    """只读判定：这个锚点此刻再派一单是否撞车。

    参数
    ----
    anchor : 待派单锚点（任意大小写/空白/连字符/全半角变体）。
    in_flight : 同锚点候选池里的**在飞**单（dict 或 mission id 列表）。
    recent_terminal : 同锚点候选池里的**已终态**单（需要在 24h 窗口内裁决）。
    now : 判定基准时刻（默认 UTC now；注入是为了可测）。
    window_hours : 回看窗口小时数（1..720）。
    candidate : 待派单本身（用于派生单豁免；缺省=非派生）。
    markers : 派生标记词表（可注入口径）。

    返回 ``{decision, conflicts, reason, ...}``；``decision`` ∈
    ``("allow", "exempt", "reject")``。本函数无副作用、不写库。
    """
    as_of = _coerce_dt(now) if now is not None else datetime.now(timezone.utc)
    assert as_of is not None
    hours = float(window_hours if window_hours is not None else DEFAULT_WINDOW_HOURS)
    if hours <= 0:
        hours = DEFAULT_WINDOW_HOURS
    hours = min(hours, float(MAX_WINDOW_HOURS))
    cutoff = as_of - timedelta(hours=hours)

    target = normalize_anchor(anchor)
    result: dict[str, Any] = {
        "gate": GATE_ID,
        "anchor": anchor,
        "normalized_anchor": target,
        "decision": "allow",
        "conflicts": [],
        "conflicts_detail": [],
        "reason": "",
        "window_hours": hours,
        "as_of": as_of.isoformat(),
        "readonly": True,
        "counts": {"in_flight": 0, "recent_terminal": 0},
    }

    if not target:
        result["decision"] = "reject"
        result["reason"] = "锚点为空，无法判重（需要【锚点=X】或 anchor 字段）"
        return result

    # 1) 派生单豁免：返修/交差审计/重开/管到底与被修单天然同锚点，不算重复。
    if is_derived(candidate, markers):
        result["decision"] = "exempt"
        parent = (candidate or {}).get("parent_mission_id")
        if parent not in (None, "", 0, "0"):
            result["reason"] = f"派生单（parent_mission_id={parent}），豁免查重"
        else:
            hit = [m for m in markers if m in str((candidate or {}).get("goal") or "")]
            result["reason"] = f"派生单（goal 标记 {'/'.join(hit) or '派生'}），豁免查重"
        return result

    # 2) 同锚点在飞 → reject（不看年龄：卡住的在飞单应被回收而不是被再派一份）
    in_flight_conflicts: list[dict[str, Any]] = []
    terminal_by_status: dict[str, int] = {}
    for mission in in_flight or ():
        result["counts"]["in_flight"] += 1
        entry = mission if isinstance(mission, Mapping) else {"id": mission}
        if _anchor_of(mission) not in (target, "\x00<id-only>"):
            continue
        if is_derived(entry, markers):
            continue  # 派生单在飞不构成冲突（否则返修单永远卡住新单）
        in_flight_conflicts.append({
            "mission_id": _mission_id(entry),
            "status": _status(entry),
            "anchor": normalize_anchor(entry.get("anchor")) or target,
        })

    for mission in recent_terminal or ():
        result["counts"]["recent_terminal"] += 1
        entry = mission if isinstance(mission, Mapping) else {"id": mission}
        status = _status(entry)
        terminal_by_status[status] = terminal_by_status.get(status, 0) + 1
        if _anchor_of(mission) not in (target, "\x00<id-only>"):
            continue
        if is_derived(entry, markers):
            continue
        when = _when(entry)
        in_window = True if when is None else when >= cutoff
        if status in DELIVERED_STATUSES and in_window:
            in_flight_conflicts.append({
                "mission_id": _mission_id(entry),
                "status": status,
                "anchor": normalize_anchor(entry.get("anchor")) or target,
                "finished_at": (when.isoformat() if when else None),
                "conflict_kind": "delivered_in_window",
            })
        elif status in DELIVERED_STATUSES:
            # 已交付但出了窗口：记录事实，不构成冲突（窗口滚动会自然回收）。
            result.setdefault("out_of_window", []).append({
                "mission_id": _mission_id(entry),
                "status": status,
                "finished_at": (when.isoformat() if when else None),
            })

    result["counts"]["recent_terminal_by_status"] = terminal_by_status
    if in_flight_conflicts:
        result["decision"] = "reject"
        result["conflicts"] = [c.get("mission_id") for c in in_flight_conflicts]
        result["conflicts_detail"] = in_flight_conflicts
        kinds = {c.get("conflict_kind", "in_flight") for c in in_flight_conflicts}
        if kinds == {"delivered_in_window"}:
            result["reason"] = (
                f"同锚点 {hours:g}h 内已交付（mission {result['conflicts']}），无需再派"
            )
        elif kinds == {"in_flight"}:
            result["reason"] = f"同锚点已在飞（mission {result['conflicts']}），派生任务会白烧席位"
        else:
            result["reason"] = (
                f"同锚点已在飞/已交付（mission {result['conflicts']}），无需再派"
            )
        return result

    # 3) 窗口内只有失败/撤销类终态 → 重试合法
    retryable = sorted(k for k in terminal_by_status if k in RETRYABLE_STATUSES)
    if retryable:
        result["reason"] = (
            f"同锚点 {hours:g}h 内只有 {'/'.join(retryable)}（重试合法）"
        )
    elif result.get("out_of_window"):
        result["reason"] = f"同锚点仅 {hours:g}h 窗口外已交付（窗口已滚出），允许派单"
    else:
        result["reason"] = "无同锚点冲突"
    result["decision"] = "allow"
    return result


def describe(decision: dict[str, Any]) -> str:
    """把判定压成一行（派单日志/告警用）。"""
    conflicts = decision.get("conflicts") or []
    tail = f" conflicts={conflicts}" if conflicts else ""
    return f"[{decision.get('decision', '?')}] {decision.get('reason', '')}{tail}"