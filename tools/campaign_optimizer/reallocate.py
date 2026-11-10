"""资源重分配建议：每条建议都带**反事实可算**的节省量（不是拍脑袋）。

建议生成三步
------------
1. **定位**：``detect.detect`` 给出瓶颈清单（严重度排序）与容量约束告警。
2. **定性**：``rootcause.classify_graph`` 给出每节点根因 → 映射到动作（映射在 config）。
3. **定量**：对每个瓶颈单独做反事实仿真（只改该节点的重试策略，其余保持原始调度），
   得到 ``expected_saving_s = 原始 makespan − 反事实 makespan``；只有超过
   ``advice.min_expected_saving_s`` 的建议才进建议清单（避免噪声建议）。

容量建议：若 ``queue_bound`` 非空，则用 ``capacity + advice.capacity_increment`` 重跑，
节省量同样以 makespan 差计。

零领域语义：动作只是调度动词（改派/暂缓派单/返工/修复后重试/提优先级/扩容），
不含任何学科词汇；动作与根因的映射写在 config。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

from . import detect as detect_mod
from . import rootcause as rc_mod
from . import schedule as sched_mod
from .model import Graph


@dataclass
class Advice:
    plan_id: int
    node_id: int
    rootcause: str
    action: str
    severity: float
    on_critical_path: bool
    baseline_attempts: int
    improved_attempts: int
    expected_saving_s: float
    rationale: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "rootcause": self.rootcause,
            "action": self.action,
            "severity": round(self.severity, 6),
            "on_critical_path": self.on_critical_path,
            "baseline_attempts": self.baseline_attempts,
            "improved_attempts": self.improved_attempts,
            "expected_saving_s": round(self.expected_saving_s, 3),
            "expected_saving_h": round(self.expected_saving_s / 3600.0, 4),
            "rationale": self.rationale,
        }


def _counterfactual_policy(cfg: Mapping[str, Any], rc: str, node_id: int) -> Dict[str, Any]:
    """baseline 策略，但**只**把 (该根因, 该节点) 的重试预算换成 improved 值。"""
    base = dict(cfg["schedule"]["baseline"])
    imp_retry = cfg["schedule"]["improved"]["retry_by_rootcause"]
    retry_max = int(base.get("retry_max", 0))
    classes = list(imp_retry) + ["unknown"]
    mapping = {c: retry_max for c in classes}
    mapping[f"{rc}#{node_id}"] = int(imp_retry.get(rc, retry_max))
    base["retry_by_rootcause"] = mapping
    return base


def advice_for_plan(graph: Graph, duration: Mapping[int, float], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    s_cfg = cfg["schedule"]
    capacity = int(s_cfg["capacity"])
    rep = detect_mod.detect(graph, duration, cfg, capacity=capacity)
    classes = {c.node_id: c.label for c in rc_mod.classify_graph(graph, duration, cfg)}
    base_policy = dict(s_cfg["baseline"])
    imp_policy = dict(s_cfg["improved"])
    baseline = sched_mod.simulate(graph, duration, classes, base_policy, capacity, "baseline")
    improved = sched_mod.improved_schedule(graph, duration, classes, cfg, capacity)

    min_saving = float(cfg["advice"].get("min_expected_saving_s", 0.0))
    action_of = cfg["advice"]["action_by_rootcause"]
    baseline_retry = int(base_policy.get("retry_max", 0))
    imp_retry = imp_policy["retry_by_rootcause"]

    advices: List[Advice] = []
    for b in rep.bottlenecks:
        rc = classes.get(b.node_id, "unknown")
        cf_classes = dict(classes)
        cf_classes[b.node_id] = f"{rc}#{b.node_id}"
        cf_policy = _counterfactual_policy(cfg, rc, b.node_id)
        cf = sched_mod.simulate(graph, duration, cf_classes, cf_policy, capacity, "counterfactual")
        saving = max(0.0, baseline.makespan_s - cf.makespan_s)
        if saving + 1e-9 < min_saving:
            continue
        advices.append(
            Advice(
                plan_id=graph.plan_id,
                node_id=b.node_id,
                rootcause=rc,
                action=str(action_of.get(rc, "raise_priority")),
                severity=b.severity,
                on_critical_path=b.on_critical_path,
                baseline_attempts=baseline_retry + 1,
                improved_attempts=int(imp_retry.get(rc, baseline_retry)) + 1,
                expected_saving_s=saving,
                rationale={
                    "counterfactual_makespan_h": round(cf.makespan_s / 3600.0, 3),
                    "severity_components": {k: round(v, 4) for k, v in sorted(b.components.items())},
                    "downstream_gated": b.downstream_gated,
                    "baseline_attempts_policy": baseline_retry,
                    "improved_attempts_policy": int(imp_retry.get(rc, baseline_retry)),
                },
            )
        )
    advices.sort(key=lambda a: (-a.expected_saving_s, a.node_id))

    capacity_advice: List[Dict[str, Any]] = []
    if rep.queue_bound:
        inc = int(cfg["advice"].get("capacity_increment", 1))
        scaled = sched_mod.improved_schedule(graph, duration, classes, cfg, capacity + inc)
        saving = max(0.0, improved.makespan_s - scaled.makespan_s)
        if saving + 1e-9 >= min_saving:
            capacity_advice.append(
                {
                    "action": "scale_capacity",
                    "from_capacity": capacity,
                    "to_capacity": capacity + inc,
                    "expected_saving_s": round(saving, 3),
                    "queue_bound_nodes": [q["node_id"] for q in rep.queue_bound],
                    "rationale": "ready work exceeds capacity; critical-path nodes wait in queue",
                }
            )

    return {
        "plan_id": graph.plan_id,
        "graph": graph.summary(),
        "bottlenecks": [b.to_dict() for b in rep.bottlenecks],
        "rootcauses": [c.to_dict() for c in rc_mod.classify_graph(graph, duration, cfg)],
        "baseline": baseline.to_dict(),
        "improved": improved.to_dict(),
        "makespan_gain": (
            None
            if baseline.makespan_s <= 0
            else 1.0 - improved.makespan_s / baseline.makespan_s
        ),
        "advice": [a.to_dict() for a in advices],
        "capacity_advice": capacity_advice,
        "aggregate": {
            "n_bottlenecks": len(rep.bottlenecks),
            "n_advice": len(advices),
            "total_expected_saving_s": round(sum(a.expected_saving_s for a in advices), 3),
            "baseline_makespan_h": round(baseline.makespan_s / 3600.0, 4),
            "improved_makespan_h": round(improved.makespan_s / 3600.0, 4),
            "unique_saving_h": round((baseline.makespan_s - improved.makespan_s) / 3600.0, 4),
        },
    }