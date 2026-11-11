"""资源账本：节点成本归集、簇级冗余成本、独立复算校验。

成本证据从哪来（诚实版）
------------------------
任务书写的是「簇内 ``campaign_nodes.budget_actual`` 逐簇求和」，但真库实测：

* **F1**：``campaign_nodes`` 表**没有** ``budget_actual`` 列（列表端点返回的字段为
  id/plan_id/milestone_id/mission_id/name/node_type/dependencies/planned_*/actual_*/
  status/reproduction_verdict/consecutive_fails/block_reason/metadata）。
* **F2**：plan 级 ``budget_actual`` 行行是 ``{"max_tokens":0,"max_tool_calls":0,
  "max_wall_seconds":0}``——从不回写（与本仓既有判例 m5313 一致）。

因此本账本改用**唯一真实存在的资源证据**：节点挂靠的 mission。

``wall_seconds_actual`` = (node.actual_end | mission.finished_at) − (node.actual_start |
mission.created_at)，两端齐全才算数（未闭合的区间记 0 并标 ``open_interval=true``，
避免用 wall clock 引入不可复现的数字）。
``wall_seconds_budget`` / ``tokens_budget`` / ``tool_calls_budget`` 来自 ``mission.budget``。

复算校验
--------
:func:`recompute_cluster_sums` 是**独立实现**（不复用 :func:`cluster_cost` 的任何中间量，
只吃同一份 ``costs`` 字典 + 簇成员 id），逐字段对账，返回相对误差；判据要求逐簇为 0。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .config import RedundancyConfig

COST_FIELDS = ("wall_seconds_actual", "wall_seconds_budget", "tokens_budget", "tool_calls_budget")


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def seconds_between(start: Any, end: Any) -> float | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds()
    return float(delta) if delta >= 0 else None


def node_cost(node: Mapping[str, Any], mission: Mapping[str, Any] | None,
              cfg: RedundancyConfig) -> dict[str, Any]:
    """一个节点的成本证据（缺失一律记 0 + 显式来源标记，不做静默补数）。"""
    fields = list(cfg.get("ledger.cost_fields"))
    budget = dict((mission or {}).get("budget") or {})
    start = node.get("actual_start") or (mission or {}).get("created_at")
    end = node.get("actual_end") or (mission or {}).get("finished_at")
    wall = seconds_between(start, end)
    out: dict[str, Any] = {
        "wall_seconds_actual": round(wall, 3) if wall is not None else 0.0,
        "open_interval": wall is None,
        "wall_seconds_budget": float(budget.get("max_wall_seconds") or 0.0),
        "tokens_budget": float(budget.get("max_tokens") or 0.0),
        "tool_calls_budget": float(budget.get("max_tool_calls") or 0.0),
        "interval_source": {
            "start": "node.actual_start" if node.get("actual_start") else "mission.created_at",
            "end": "node.actual_end" if node.get("actual_end") else "mission.finished_at",
        },
        "has_mission": bool(mission),
    }
    for f in fields:  # 配置新增字段时兜底为 0，避免 KeyError 掩盖缺证据
        out.setdefault(f, 0.0)
    return out


def build_costs(nodes: Sequence[Mapping[str, Any]], missions: Mapping[str, Any],
                cfg: RedundancyConfig) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for n in nodes:
        mid = n.get("mission_id")
        mission = missions.get(str(mid)) if mid is not None else None
        out[str(n["node_id"])] = node_cost(n, mission, cfg)
    return out


def _sum_field(rows: Iterable[Mapping[str, Any]], field: str) -> float:
    return round(sum(float((r.get("cost") or {}).get(field) or 0.0) for r in rows), 3)


def cluster_cost(members: Sequence[Mapping[str, Any]], cfg: RedundancyConfig) -> dict[str, Any]:
    """簇级成本：全簇求和 + keeper 保留成本 + 冗余（非 keeper）成本。"""
    fields = list(cfg.get("ledger.cost_fields"))
    keepers = [m for m in members if m.get("action") == "keep"]
    redundant = [m for m in members if m.get("action") != "keep"]
    total = {f: _sum_field(members, f) for f in fields}
    kept = {f: _sum_field(keepers, f) for f in fields}
    waste = {f: _sum_field(redundant, f) for f in fields}
    return {
        "total": total,
        "keeper": kept,
        "redundant": waste,
        "by_action": {
            a: {f: _sum_field([m for m in members if m.get("action") == a], f) for f in fields}
            for a in ("keep", "merge", "archive")
        },
    }


def recompute_cluster_sums(
    clusters: Sequence[Mapping[str, Any]], costs: Mapping[str, Mapping[str, Any]],
    cfg: RedundancyConfig,
) -> dict[str, Any]:
    """独立复算（不复用 cluster_cost）：返回逐簇/逐字段的账本 vs 复算相对误差。"""
    fields = list(cfg.get("ledger.cost_fields"))
    per_cluster: list[dict[str, Any]] = []
    max_err = 0.0
    for c in clusters:
        ledger = c.get("cost") or {}
        recomputed_total = {f: 0.0 for f in fields}
        recomputed_redundant = {f: 0.0 for f in fields}
        for m in c["members"]:
            node_id = str(m["node_id"])
            source = costs.get(node_id, {})
            action = m.get("action")
            for f in fields:
                value = float(source.get(f) or 0.0)
                recomputed_total[f] += value
                if action != "keep":
                    recomputed_redundant[f] += value
        errs = {}
        for f in fields:
            for label, mine, theirs in (
                ("total", (ledger.get("total") or {}).get(f, 0.0), recomputed_total[f]),
                ("redundant", (ledger.get("redundant") or {}).get(f, 0.0), recomputed_redundant[f]),
            ):
                errs[f"{label}.{f}"] = relative_error(float(mine or 0.0), float(theirs))
        max_err = max([max_err, *[v for v in errs.values() if v is not None]])
        per_cluster.append({"cluster_id": c["cluster_id"], "errors": errs})
    return {"per_cluster": per_cluster, "max_relative_error": max_err}


ACCOUNT_DECIMALS = 3  # 记账单位：1 毫秒；先按单位量化再比较，浮点求和顺序差异不应记为偏差


def relative_error(ledger_value: float, recomputed_value: float) -> float:
    """相对误差：先量化到记账单位（3 位小数），相等即 0；否则 |a-b| / max(|a|,|b|)。

    量化是必要的：账本按簇求和、复算按成员累加，浮点加法非结合，末位差异（~1e-16）不代表
    账目不符。量化后仍不等的才是真差异（会被如实报出）。
    """
    a = round(float(ledger_value), ACCOUNT_DECIMALS)
    b = round(float(recomputed_value), ACCOUNT_DECIMALS)
    if a == b:
        return 0.0
    denom = max(abs(a), abs(b))
    if denom == 0.0:
        return 0.0
    return abs(a - b) / denom


def portfolio(costs: Mapping[str, Mapping[str, Any]], clusters: Sequence[Mapping[str, Any]],
              cfg: RedundancyConfig) -> dict[str, Any]:
    """全局分母：全库成本 vs 冗余成本 vs 可立即节省（archive 部分）。"""
    fields = list(cfg.get("ledger.cost_fields"))
    total = {f: round(sum(float(c.get(f) or 0.0) for c in costs.values()), 3) for f in fields}
    redundant = {f: round(sum(float(((cl.get("cost") or {}).get("redundant") or {}).get(f) or 0.0)
                              for cl in clusters), 3) for f in fields}
    immediate = {f: round(sum(float(((cl.get("cost") or {}).get("by_action") or {})
                                    .get("archive", {}).get(f) or 0.0) for cl in clusters), 3)
                 for f in fields}
    share = {f: (redundant[f] / total[f] if total[f] else 0.0) for f in fields}
    return {
        "total": total,
        "redundant": redundant,
        "immediate_saving": immediate,
        "redundant_share": {k: round(v, 6) for k, v in share.items()},
        "open_intervals": sum(1 for c in costs.values() if c.get("open_interval")),
        "nodes": len(costs),
        "cost_source": {
            "primary": "node→mission wall-clock（actual_start/actual_end | created_at/finished_at）",
            "budget": "mission.budget.max_wall_seconds / max_tokens / max_tool_calls",
            "audit_findings": [
                "F1 campaign_nodes 无 budget_actual 列（真库字段实测）",
                "F2 campaign_plans.budget_actual 恒零（从不回写）",
            ],
        },
    }