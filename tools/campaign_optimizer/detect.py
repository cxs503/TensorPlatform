"""瓶颈检测：状态证据 + 图结构证据 → 严重度排序的瓶颈清单。

判定语义（零领域语义）
---------------------
一个节点是**瓶颈**，当且仅当：

1. 它的状态属于 ``status_vocab.blocking``（平台机器字段），**并且**
2. 严重度分数 ≥ ``detect.bottleneck_severity_floor``。

严重度由五个**结构化**分量加权（权重全部在 config）：

* ``gates_plan_completion``   —— 未满足即 plan 无法完成（plan 完成 = 全节点 satisfied）
* ``downstream_gated``        —— 被它卡住的传递后继占未完成节点的比例
* ``on_critical_path``        —— 总浮动 = 0（在最长剩余链上）
* ``downstream_weight_share`` —— 其下游剩余工期占 plan 剩余工期的比例
* ``stall_age``               —— 距最近一次状态更新的静默时长（有快照时钟时）

另有两类**非瓶颈**告警（不入瓶颈清单，避免稀释 precision）：

* ``risk``：活动态节点，其上游链含 blocking 节点（预测将被拖停）
* ``queue_bound``：未完成节点数 > 并发容量，且其中位于关键路径者（容量成为约束）

检测器**不读**节点名、不读 block_reason 文本、不读任何项目词汇：本模块对 ``Node.name`` /
``Node.reason_text`` 零引用（``tests/test_campaign_optimizer_zero_domain.py`` 用 AST 扫描断言）。
审计需要的文本证据由产物层（``cli.build_payload`` 的 ``evidence_text``）补，判定链看不到它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .model import Graph


class DetectError(ValueError):
    pass


@dataclass
class Bottleneck:
    node_id: int
    plan_id: int
    status: str
    node_type: str
    severity: float
    components: Dict[str, float]
    downstream_gated: int
    downstream_weight_s: float
    on_critical_path: bool
    gates_plan_completion: bool
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "node_type": self.node_type,
            "severity": round(self.severity, 6),
            "components": {k: round(v, 6) for k, v in sorted(self.components.items())},
            "downstream_gated": self.downstream_gated,
            "downstream_weight_s": round(self.downstream_weight_s, 3),
            "on_critical_path": self.on_critical_path,
            "gates_plan_completion": self.gates_plan_completion,
            "evidence": self.evidence,
        }


@dataclass
class Report:
    plan_id: int
    bottlenecks: List[Bottleneck]
    risks: List[Dict[str, Any]]
    queue_bound: List[Dict[str, Any]]
    critical_path: List[int]
    critical_path_s: float
    summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
            "risks": self.risks,
            "queue_bound": self.queue_bound,
            "critical_path": self.critical_path,
            "critical_path_s": round(self.critical_path_s, 3),
            "summary": self.summary,
        }

    @property
    def bottleneck_ids(self) -> List[int]:
        return [b.node_id for b in self.bottlenecks]


def _weights(graph: Graph, duration: Mapping[int, float]) -> Dict[int, float]:
    return {nid: float(duration.get(nid, 0.0)) for nid in graph.nodes}


def node_features(
    graph: Graph, weights: Mapping[int, float], now_offset_s: Optional[Mapping[int, float]] = None
) -> Dict[int, Dict[str, Any]]:
    """逐节点结构特征（纯函数）。"""
    remaining = graph.remaining()
    n_remaining = max(1, len(remaining))
    total_remaining_weight = sum(weights.get(nid, 0.0) for nid in remaining) or 1.0
    flt = graph.total_float(weights)
    feats: Dict[int, Dict[str, Any]] = {}
    for nid in graph.sorted_ids():
        node = graph.nodes[nid]
        down = graph.downstream_closure(nid)
        down_unsat = [d for d in down if not graph.is_satisfied(d)]
        down_weight = float(sum(weights.get(d, 0.0) for d in down_unsat))
        feat = {
            "node_type": node.node_type,
            "status": node.status,
            "satisfied": graph.is_satisfied(nid),
            "blocking": graph.is_blocking(nid),
            "gates_plan_completion": graph.gates_plan_completion(nid),
            "downstream_gated": len(down_unsat),
            "downstream_gated_share": len(down_unsat) / n_remaining,
            "downstream_weight_s": down_weight,
            "downstream_weight_share": down_weight / total_remaining_weight,
            "total_float_s": flt[nid],
            "on_critical_path": flt[nid] <= 1e-9,
            "stall_age_s": float((now_offset_s or {}).get(nid, 0.0)),
        }
        feats[nid] = feat
    return feats


def detect(
    graph: Graph,
    duration: Mapping[int, float],
    cfg: Mapping[str, Any],
    capacity: Optional[int] = None,
    now_offset_s: Optional[Mapping[int, float]] = None,
) -> Report:
    dcfg = cfg["detect"]
    weights = _weights(graph, duration)
    feats = node_features(graph, weights, now_offset_s)
    sev_w = dcfg["severity_weights"]
    floor = float(dcfg.get("bottleneck_severity_floor", 0.5))
    stall_full = float(dcfg.get("stall_age_s", 21600)) or 1.0
    satisfied = graph.satisfied_statuses

    bottlenecks: List[Bottleneck] = []
    for nid in graph.sorted_ids():
        f = feats[nid]
        if not f["blocking"]:
            continue
        comp = {
            "gates_plan_completion": float(f["gates_plan_completion"]),
            "downstream_gated": min(1.0, f["downstream_gated_share"]),
            "on_critical_path": float(f["on_critical_path"]),
            "downstream_weight_share": min(1.0, f["downstream_weight_share"]),
            "stall_age": min(1.0, f["stall_age_s"] / stall_full),
        }
        sev = sum(sev_w.get(k, 0.0) * v for k, v in comp.items())
        if sev + 1e-12 < floor:
            continue
        node = graph.nodes[nid]
        bottlenecks.append(
            Bottleneck(
                node_id=nid,
                plan_id=graph.plan_id,
                status=node.status,
                node_type=node.node_type,
                severity=sev,
                components=comp,
                downstream_gated=f["downstream_gated"],
                downstream_weight_s=f["downstream_weight_s"],
                on_critical_path=f["on_critical_path"],
                gates_plan_completion=f["gates_plan_completion"],
                evidence={
                    "mission_id": node.mission_id,
                    "consecutive_fails": node.consecutive_fails,
                    "duration_s": node.duration_s,
                },
            )
        )
    bottlenecks.sort(key=lambda b: (-b.severity, -b.downstream_gated, b.node_id))

    # 风险预测：活动态节点且上游含 blocking 节点
    risk_statuses = set(dcfg.get("risk_statuses", []))
    risks: List[Dict[str, Any]] = []
    for nid in graph.sorted_ids():
        node = graph.nodes[nid]
        if node.status not in risk_statuses or node.status in satisfied:
            continue
        up = graph.upstream_closure(nid)
        bad = [u for u in up if graph.is_blocking(u)]
        if not bad:
            continue
        risks.append(
            {
                "node_id": nid,
                "status": node.status,
                "blocked_upstream": bad,
                "downstream_gated": feats[nid]["downstream_gated"],
                "total_float_s": round(feats[nid]["total_float_s"], 3),
            }
        )
    risks.sort(key=lambda r: (r["node_id"]))

    # 容量约束：未完成可执行节点数超过并发容量
    queue_bound: List[Dict[str, Any]] = []
    if capacity:
        pending_active = [
            nid for nid in graph.remaining() if graph.nodes[nid].status not in satisfied
        ]
        if len(pending_active) > int(capacity):
            over = sorted(
                pending_active, key=lambda nid: (-feats[nid]["downstream_weight_s"], nid)
            )[int(capacity):]
            for nid in over[:50]:
                queue_bound.append(
                    {
                        "node_id": nid,
                        "status": graph.nodes[nid].status,
                        "on_critical_path": feats[nid]["on_critical_path"],
                        "excess_over_capacity": len(pending_active) - int(capacity),
                    }
                )

    cp_len, _ = graph.longest_paths(weights)
    report = Report(
        plan_id=graph.plan_id,
        bottlenecks=bottlenecks,
        risks=risks,
        queue_bound=queue_bound,
        critical_path=graph.critical_path(weights),
        critical_path_s=cp_len,
        summary={
            **graph.summary(),
            "n_bottlenecks": len(bottlenecks),
            "n_risks": len(risks),
            "n_queue_bound": len(queue_bound),
            "capacity": capacity,
            "severity_floor": floor,
        },
    )
    return report


def structural_heuristic_detect(graph: Graph, duration: Mapping[int, float]) -> List[int]:
    """**对照基线**（不是本工具默认行为）：只按结构标记关键路径上的节点，不看状态。

    仅用于负控有效性实验：证明 AC5 的假阳性判据有鉴别力（若检测器退化为纯结构启发，
    随机 DAG 上的假阳性率会显著超过阈值）。
    """
    weights = _weights(graph, duration)
    flt = graph.total_float(weights)
    return sorted(nid for nid, v in flt.items() if v <= 1e-9)