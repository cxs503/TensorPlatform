"""离散事件调度模拟器：把「重试策略 + 优先级 + 并发容量」翻译成可比的完成时间。

语义（尽量少、尽量显式）
------------------------
* **已满足节点**（status ∈ satisfied）不再消耗未来算力：它在 t=0 之前已完成。
* **未满足节点**必须在某个 worker 上串行执行；它需要
  ``failed_attempts + 1`` 次尝试，每次耗时 ``attempt_duration_s``。
* **依赖**：所有前驱完成后节点才可开工（两种策略都遵守；差别不在这里）。
* **并发容量**：同时最多 ``capacity`` 个节点在跑；排队顺序由 ``priority`` 决定。
* **baseline（原始调度）**：按派单顺序（node_id 升序）排队，失败重试沿用历史
  ``retry_max`` 次，用尽后等一次外部干预（``intervention_latency_s``）再在新执行器上
  成功一次 —— 即 ``(retry_max + 1) × attempt + L``。
* **improved（重分配后）**：根因已知，重试次数按根因给（``retry_by_rootcause``），
  无干预时延；排队优先级 ``critical_first``（下游剩余工期大者优先）。
* 全程无随机数：同输入同输出，可逐字节复现。

``attempt_duration_s`` 的口径
-----------------------------
历史节点的 ``duration_s`` 是 **含重试的总墙钟**；单次尝试耗时 =
``duration_s / attempt_divisor``。divisor 缺省 5（历史观测中线上
"x5" 五连败占多数），可在 config.schedule 里改，回测会做 divisor ∈ {5,2} 的敏感性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .model import Graph


@dataclass
class NodePlan:
    node_id: int
    rootcause: str
    attempts_total: int
    work_s: float
    start_s: float = 0.0
    finish_s: float = 0.0
    priority_key: Tuple[Any, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "rootcause": self.rootcause,
            "attempts_total": self.attempts_total,
            "work_s": round(self.work_s, 3),
            "start_s": round(self.start_s, 3),
            "finish_s": round(self.finish_s, 3),
        }


@dataclass
class ScheduleResult:
    label: str
    makespan_s: float
    nodes: List[NodePlan] = field(default_factory=list)
    queued_nodes: int = 0
    capacity: int = 0
    policy: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "makespan_s": round(self.makespan_s, 3),
            "makespan_h": round(self.makespan_s / 3600.0, 4),
            "queued_nodes": self.queued_nodes,
            "capacity": self.capacity,
            "policy": self.policy,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @property
    def throughput_per_hour(self) -> float:
        return (len(self.nodes) / (self.makespan_s / 3600.0)) if self.makespan_s > 0 else 0.0


class ScheduleError(ValueError):
    pass


def attempt_duration(
    graph: Graph, duration: Mapping[int, float], divisor: float
) -> Dict[int, float]:
    if divisor <= 0:
        raise ScheduleError("attempt divisor must be > 0")
    return {
        nid: max(float(duration.get(nid, 0.0)) / divisor, 1e-6) for nid in graph.nodes
    }


def _work_items(
    graph: Graph,
    attempt_s: Mapping[int, float],
    rootcause: Mapping[int, str],
    failed_attempts: Mapping[str, int],
    intervention_latency_s: float = 0.0,
) -> Dict[int, NodePlan]:
    plans: Dict[int, NodePlan] = {}
    for nid in graph.sorted_ids():
        if graph.is_satisfied(nid):
            plans[nid] = NodePlan(node_id=nid, rootcause="satisfied", attempts_total=0, work_s=0.0)
            continue
        rc = rootcause.get(nid, "unknown")
        fails = int(failed_attempts.get(rc, failed_attempts.get("unknown", 0)))
        attempts = max(1, fails + 1)
        work = attempts * attempt_s[nid] + (intervention_latency_s if fails > 0 else 0.0)
        plans[nid] = NodePlan(node_id=nid, rootcause=rc, attempts_total=attempts, work_s=work)
    return plans


def _priority_keys(
    graph: Graph, weights: Mapping[int, float], rule: str
) -> Dict[int, Tuple[Any, ...]]:
    if rule == "dispatch_order":
        return {nid: (nid,) for nid in graph.nodes}
    if rule == "critical_first":
        flt = graph.total_float(weights)
        out: Dict[int, Tuple[Any, ...]] = {}
        for nid in graph.nodes:
            down_w = graph.downstream_weight(nid, weights)
            out[nid] = (-down_w, round(flt[nid], 6), nid)
        return out
    raise ScheduleError(f"unknown priority rule: {rule!r}")


def simulate(
    graph: Graph,
    weights: Mapping[int, float],
    rootcause: Mapping[int, str],
    policy: Mapping[str, Any],
    capacity: int,
    label: str = "schedule",
) -> ScheduleResult:
    """确定性列表调度：每次从就绪队列取优先级最高者上空闲 worker。"""
    if capacity <= 0:
        raise ScheduleError("capacity must be >= 1")
    divisor = float(policy.get("attempt_divisor", 5))
    attempt_s = attempt_duration(graph, weights, divisor)
    retry_by_rc = policy.get("retry_by_rootcause")
    if retry_by_rc is None:
        retry_max = int(policy.get("retry_max", 0))
        failed = {rc: retry_max for rc in set(rootcause.values()) | {"unknown"}}
    else:
        failed = {str(k): int(v) for k, v in retry_by_rc.items()}
    latency = float(policy.get("intervention_latency_s", 0.0))
    plans = _work_items(graph, attempt_s, rootcause, failed, latency)
    prio = _priority_keys(graph, weights, str(policy.get("priority", "dispatch_order")))
    for nid, key in prio.items():
        plans[nid].priority_key = key
    makespan, max_queue = _simulate_loop(graph, plans, capacity)
    policy_out: Dict[str, Any] = {
        k: v for k, v in sorted(policy.items()) if k != "retry_by_rootcause"
    }
    if retry_by_rc:
        policy_out["retry_by_rootcause"] = dict(sorted(retry_by_rc.items()))
    return ScheduleResult(
        label=label,
        makespan_s=makespan,
        nodes=[plans[nid] for nid in sorted(plans)],
        queued_nodes=max_queue,
        capacity=capacity,
        policy=policy_out,
    )


def _simulate_loop(
    graph: Graph,
    plans: Mapping[int, NodePlan],
    capacity: int,
) -> Tuple[float, int]:
    """列表调度的确定性事件循环。返回 (makespan, 最大排队长度)。"""
    indeg = {nid: len(graph.nodes[nid].dependencies) for nid in graph.nodes}
    children = graph.children_map()
    ready = sorted([nid for nid, d in indeg.items() if d == 0],
                   key=lambda n: plans[n].priority_key)
    running: List[Tuple[float, int]] = []
    now = 0.0
    idle = capacity
    max_queue = 0

    def finish_upto(t: float) -> None:
        nonlocal idle
        running.sort()
        while running and running[0][0] <= t + 1e-12:
            _, nid = running.pop(0)
            for c in children[nid]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
            idle += 1

    while True:
        ready.sort(key=lambda n: plans[n].priority_key)
        while ready and idle > 0:
            nid = ready.pop(0)
            if plans[nid].work_s <= 0:
                # 无工作量的已满足节点：立即完成，不占 worker
                running.append((now, nid))
                continue
            plan = plans[nid]
            plan.start_s = now
            plan.finish_s = now + plan.work_s
            running.append((plan.finish_s, nid))
            idle -= 1
        max_queue = max(max_queue, len(ready))
        if not running:
            break
        running.sort()
        now = running[0][0]
        finish_upto(now)
        if not ready and not running:
            break
    makespan = max((p.finish_s for p in plans.values()), default=0.0)
    unfinished = sum(1 for nid, d in indeg.items() if d > 0 and plans[nid].work_s > 0)
    if unfinished and makespan <= 0:
        raise ScheduleError("scheduler produced no progress")
    return makespan, max_queue

def improved_schedule(
    graph: Graph,
    weights: Mapping[int, float],
    rootcause: Mapping[int, str],
    cfg: Mapping[str, Any],
    capacity: int,
    divisor: Optional[float] = None,
) -> ScheduleResult:
    """重分配调度（**带守卫**）：在候选优先级中取 makespan 最小者。

    守卫的必要性来自回测实测：并发容量紧张时 ``critical_first`` 会输给派单顺序
    （见回测敏感性表 min_gain < 0 的那一档）。真实引擎必须先算再改，
    所以这里只接受**不劣于**原始优先级的结果（平手取第一个候选）。
    """
    base_cfg = cfg["schedule"]["improved"]
    candidates = list(base_cfg.get("priority_candidates") or [base_cfg.get("priority", "dispatch_order")])
    best: Optional[ScheduleResult] = None
    for rule in candidates:
        policy = dict(base_cfg)
        policy["priority"] = rule
        if divisor is not None:
            policy["attempt_divisor"] = divisor
        res = simulate(graph, weights, rootcause, policy, capacity, f"improved[{rule}]")
        if best is None or res.makespan_s < best.makespan_s - 1e-9:
            best = res
    assert best is not None
    return best
