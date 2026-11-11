"""级联失效传播：能力 X 从 REAL 降级到 MOCK 时，依赖 X 的能力置信度如何衰减。

传播模型（可手算、可复核、与实现解耦）
--------------------------------------
记号：``d`` 是 ``n`` 的直接上游（n 依赖 d），``w(d→n)`` 是依赖强度。

1. 自身相对退化度（事件造成的直接退化）::

       own_drop(n) = 0                                  if own_before(n) == 0
                   = clip((own_before-own_after)/own_before, 0, 1)   otherwise

2. 承接退化度（沿依赖边向下游传播，取最坏依赖）::

       deg(n) = max_{d ∈ deps(n)} w(d→n) * D(d)
       D(n)   = max(own_drop(n), deg(n))

   无上游的节点 ``deg = 0``。``D`` 是"该节点向外传递的退化强度"：既包含它自身
   掉级的部分，也包含它从上游承接的部分 —— 这就是级联链的递推核心。

3. 事件后有效置信度与相对衰减::

       eff_after(n) = own_after(n) * (1 - deg(n))
       eff_before(n) = own_before(n)
       confidence_decay(n) = deg(n)          （因 eff_before>0 时代数上恒等）

4. 等级回映射：置信带映射（``confidence_bands``），并受 ``cap_level_by_own_level``
   约束 —— 级联只能降级，不能把自身证据弱的能力抬到高等级。

为什么用 ``max`` 而不是乘积/求和：能力对上游的**正确性依赖**是取最坏短板（任一
依赖不可信则输出不可信），乘积会把两条各 0.5 的依赖夸大成 0.25 的"双重惩罚"，
求和不满足 [0,1] 有界。``max`` + 边权是唯一同时满足有界、单调、可手算的选择。

不动点迭代：``deg`` 从 0 起单调不减地松弛到不动点（DAG 下等价于拓扑序一趟，
有环时也能收敛）。迭代轮数与是否收敛显式返回，不静默截断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .model import (
    Snapshot,
    confidence_of,
    level_from_confidence,
    normalise_level,
    rank_of,
)

__all__ = [
    "CascadeEvent",
    "CascadeResult",
    "propagate_degradation",
    "simulate",
    "shortest_dependency_path",
]


@dataclass(frozen=True)
class CascadeEvent:
    """一次能力等级变化事件。``to_level`` 用台账等级词表（配置注入）。"""

    capability_id: str
    to_level: str

    def to_dict(self) -> Dict[str, str]:
        return {"capability_id": self.capability_id, "to_level": self.to_level}


@dataclass
class CascadeResult:
    event: Dict[str, str]
    from_level: str
    to_level: str
    own_drop: float
    degradation: Dict[str, float]
    eff_before: Dict[str, float]
    eff_after: Dict[str, float]
    levels_before: Dict[str, str]
    levels_after: Dict[str, str]
    affected: List[Dict[str, Any]]
    severity: str
    iterations: int
    converged: bool
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def affected_ids(self) -> List[str]:
        return [a["id"] for a in self.affected]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "own_drop": round(self.own_drop, 6),
            "severity": self.severity,
            "affected_count": len(self.affected),
            "affected": self.affected,
            "iterations": self.iterations,
            "converged": self.converged,
            "degradation": {k: round(v, 6) for k, v in sorted(self.degradation.items())},
            "eff_before": {k: round(v, 6) for k, v in sorted(self.eff_before.items())},
            "eff_after": {k: round(v, 6) for k, v in sorted(self.eff_after.items())},
            "levels_before": dict(sorted(self.levels_before.items())),
            "levels_after": dict(sorted(self.levels_after.items())),
            "meta": self.meta,
        }


def propagate_degradation(
    snapshot: Snapshot,
    own_drop: Mapping[str, float],
    *,
    default_weight: float,
    max_iterations: int = 64,
    tolerance: float = 1e-09,
) -> Tuple[Dict[str, float], int, bool]:
    """把 ``own_drop`` 沿依赖边传播到不动点，返回 (deg, 迭代轮数, 是否收敛)。"""
    deg: Dict[str, float] = {n: 0.0 for n in snapshot.nodes}
    deps: Dict[str, List[str]] = {n: snapshot.dependencies(n) for n in snapshot.nodes}
    iterations = 0
    converged = False
    for iterations in range(1, max_iterations + 1):
        delta = 0.0
        new_deg = dict(deg)
        for node_id in sorted(snapshot.nodes):
            upstream = deps.get(node_id) or []
            if not upstream:
                new_deg[node_id] = 0.0
                continue
            worst = 0.0
            for src in upstream:
                d_val = max(float(own_drop.get(src, 0.0)), deg.get(src, 0.0))
                w = snapshot.weight(src, node_id, default_weight)
                worst = max(worst, w * d_val)
            new_deg[node_id] = min(1.0, max(0.0, worst))
        for k in new_deg:
            delta = max(delta, abs(new_deg[k] - deg[k]))
        deg = new_deg
        if delta <= tolerance:
            converged = True
            break
    return deg, iterations, converged


def shortest_dependency_path(snapshot: Snapshot, src: str, dst: str) -> List[str]:
    """从事件源到受影响节点的最短依赖路径（BFS，确定序）。空列表 = 不可达。"""
    if src == dst:
        return [src]
    adj = snapshot.adjacency()
    prev: Dict[str, Optional[str]] = {src: None}
    queue: List[str] = [src]
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt in prev:
                continue
            prev[nxt] = cur
            if nxt == dst:
                path = [dst]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                return list(reversed(path))
            queue.append(nxt)
    return []


def _severity(affected: Sequence[Mapping[str, Any]], alert_cfg: Mapping[str, Any]) -> str:
    critical_drop = int(alert_cfg.get("critical_on_rank_drop", 2))
    high_count = int(alert_cfg.get("high_affected_count", 3))
    if not affected:
        return "info"
    max_drop = max(int(a.get("rank_drop", 0)) for a in affected)
    if max_drop >= critical_drop:
        return "critical"
    if len(affected) >= high_count or max_drop >= 1:
        return "high"
    return "medium"


def simulate(
    snapshot: Snapshot,
    event: CascadeEvent,
    cfg: Any,
    *,
    cap_by_own_level: Optional[bool] = None,
) -> CascadeResult:
    """模拟一次等级变化事件的级联影响（不修改入参快照）。"""
    node_id = event.capability_id
    if node_id not in snapshot.nodes:
        raise KeyError(f"unknown capability: {node_id}")
    level_rank = cfg.level_rank
    level_conf = cfg.level_confidence
    to_level = normalise_level(event.to_level, level_rank)
    from_level = snapshot.nodes[node_id].own_level
    cascade_cfg = dict((cfg.raw.get("cascade") or {}))
    cap_by_own = (
        bool(cascade_cfg.get("cap_level_by_own_level", True))
        if cap_by_own_level is None
        else bool(cap_by_own_level)
    )

    after_snapshot = snapshot.with_levels({node_id: to_level}, level_conf)
    own_before = snapshot.confidences()
    own_after = after_snapshot.confidences()
    own_drop: Dict[str, float] = {}
    for n in snapshot.nodes:
        b, a = own_before[n], own_after[n]
        if b <= 0.0:
            own_drop[n] = 0.0
        else:
            own_drop[n] = min(1.0, max(0.0, (b - a) / b))

    deg, iterations, converged = propagate_degradation(
        snapshot,
        own_drop,
        default_weight=cfg.default_weight,
        max_iterations=int(cascade_cfg.get("max_iterations", 64)),
        tolerance=float(cascade_cfg.get("tolerance", 1e-09)),
    )
    tol = float(cascade_cfg.get("affected_tolerance", 1e-09))
    eff_after = {n: own_after[n] * (1.0 - deg[n]) for n in snapshot.nodes}
    levels_after: Dict[str, str] = {}
    for n in snapshot.nodes:
        levels_after[n] = level_from_confidence(
            eff_after[n],
            cfg.bands,
            own_level=after_snapshot.nodes[n].own_level,
            level_rank=level_rank,
            cap_by_own_level=cap_by_own,
        )
    affected: List[Dict[str, Any]] = []
    for n in sorted(snapshot.nodes):
        d_total = max(own_drop[n], deg[n])
        # 受影响 = 有效置信度下降的节点；纯升级/无变化事件不产生受影响清单
        # （升级只改变自身等级，不使任何下游退化）。
        if d_total <= tol:
            continue
        rank_drop = rank_of(snapshot.nodes[n].own_level, level_rank) - rank_of(
            levels_after[n], level_rank
        )
        affected.append(
            {
                "id": n,
                "path": shortest_dependency_path(snapshot, node_id, n),
                "hop": max(0, len(shortest_dependency_path(snapshot, node_id, n)) - 1),
                "own_drop": round(own_drop[n], 6),
                "degradation": round(deg[n], 6),
                "confidence_decay": (round(deg[n], 6) if own_before[n] > 0 else None),
                "confidence_before": round(own_before[n], 6),
                "confidence_after": round(eff_after[n], 6),
                "level_before": snapshot.nodes[n].own_level,
                "level_after": levels_after[n],
                "rank_drop": rank_drop,
                "confidence_zero_basis": own_before[n] <= 0,
            }
        )
    if own_drop[node_id] > tol:
        direction = "degrade"
    elif own_after[node_id] > own_before[node_id]:
        direction = "upgrade"
    else:
        direction = "no_change"
    severity = _severity(affected, cfg.raw.get("alerts") or {})
    return CascadeResult(
        event=event.to_dict(),
        from_level=from_level,
        to_level=to_level,
        own_drop=own_drop[node_id],
        degradation=deg,
        eff_before=own_before,
        eff_after=eff_after,
        levels_before=snapshot.levels(),
        levels_after=levels_after,
        affected=affected,
        severity=severity,
        iterations=iterations,
        converged=converged,
        meta={
            "direction": direction,
            "changed_ids": [node_id],
            "default_weight": cfg.default_weight,
            "cap_level_by_own_level": cap_by_own,
            "affected_tolerance": tol,
        },
    )