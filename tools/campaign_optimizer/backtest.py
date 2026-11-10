"""历史回测：判据 AC1–AC5 的机器可算实现（全部纯函数 + 确定性随机）。

四条回测线
----------
1. **瓶颈检测召回**（AC1）：对指定 plan 的节点状态分布，检测器给出的瓶颈集合 vs
   真实 blocking 集合 → recall / precision。
2. **根因分类**（AC2）：与人工标注对拍 → 混淆矩阵 + 准确率 + 分层留出集。
3. **吞吐回测**（AC3）：每个 plan 用同一 DAG、同一容量，比较原始调度 vs 重分配调度
   的**剩余工作完工时间**（remaining-work makespan）；再对有效样本聚合（中位数/均值）。
4. **负控**（AC5）：随机 DAG（依赖无真实语义）上的假阳性率；并给出**对照基线**
   （只看结构的启发式检测）让负控有鉴别力。

所有随机性都来自固定 seed；同输入逐字节复现。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import detect as detect_mod
from . import reallocate as realloc_mod
from . import rootcause as rc_mod
from . import schedule as sched_mod
from .model import Graph, Node


# --------------------------------------------------------------------------- #
# 随机 DAG 生成（负控用）
# --------------------------------------------------------------------------- #
def random_dag(n: int, edge_p: float, rng: random.Random, plan_id: int = -1,
               status_mix: Optional[Mapping[str, float]] = None,
               duration_range_h: Tuple[float, float] = (1.0, 10.0)) -> Graph:
    """按节点序号拓扑有序生成 DAG（i<j 才可能有边 ⇒ 必然无环）。"""
    nodes: List[Node] = []
    statuses = list(status_mix.keys()) if status_mix else ["done"]
    weights = [status_mix[s] for s in statuses] if status_mix else [1.0]
    for i in range(1, n + 1):
        deps = sorted(j for j in range(1, i) if rng.random() < edge_p)
        status = rng.choices(statuses, weights=weights, k=1)[0]
        dur = rng.uniform(*duration_range_h) * 3600.0
        nodes.append(
            Node(node_id=i, plan_id=plan_id, status=status, dependencies=tuple(deps),
                 duration_s=dur, mission_id=(1000 + i), mission_status=status,
                 node_type="task")
        )
    return Graph.build(nodes, ["done"], ["blocked", "failed", "orphan", "revoked"])


@dataclass
class NegativeControl:
    trials: int
    seed: int
    nodes_total: int
    healthy_nodes: int
    flagged_on_healthy: int
    fpr: Optional[float]
    heuristic_flagged: int
    heuristic_fpr: Optional[float]
    threshold: float
    passed: bool
    per_family: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trials": self.trials,
            "seed": self.seed,
            "nodes_total": self.nodes_total,
            "healthy_nodes": self.healthy_nodes,
            "flagged_on_healthy": self.flagged_on_healthy,
            "fpr": None if self.fpr is None else round(self.fpr, 6),
            "heuristic_flagged": self.heuristic_flagged,
            "heuristic_fpr": None if self.heuristic_fpr is None else round(self.heuristic_fpr, 6),
            "threshold": self.threshold,
            "passed": self.passed,
            "per_family": self.per_family,
        }


def negative_control(cfg: Mapping[str, Any]) -> NegativeControl:
    """随机 DAG 假阳性率：依赖边随机生成（无真实语义），状态按族给定。

    族 A ``healthy``：无 blocking 状态（全部完成/在飞/待办）→ 任何瓶颈都是假阳性。
    族 B ``historical_mix``：状态按历史边际分布抽样（含 blocking）→ 只统计
    **非 blocking 状态节点**上的假阳性（真 blocking 节点被标记是正确行为）。
    """
    bcfg = cfg["backtest"]
    trials = int(bcfg["random_dag_trials"])
    seed = int(bcfg["seed"])
    lo, hi = bcfg["random_dag_nodes"]
    p = float(bcfg["random_dag_edge_p"])
    thr = float(bcfg["fpr_threshold"])
    families = {
        "healthy": {"done": 0.6, "pending": 0.25, "in-flight": 0.15},
        "historical_mix": bcfg.get("random_status_mix")
        or {"done": 0.62, "pending": 0.12, "in-flight": 0.17, "blocked": 0.09},
    }
    capacity = int(cfg["schedule"]["capacity"])
    per_family: Dict[str, Any] = {}
    tot_nodes = tot_healthy = tot_fp = 0
    heur_nodes = heur_fp = 0
    rng = random.Random(seed)
    for fam, mix in families.items():
        f_nodes = f_healthy = f_fp = 0
        f_heur_fp = 0
        blocked_seen = 0
        blocked_flagged = 0
        n_bottlenecks = 0
        for t in range(trials):
            g = random_dag(rng.randint(lo, hi), p, rng, plan_id=-(t + 1), status_mix=mix)
            dur = {n.node_id: n.duration_s for n in g.nodes.values()}
            rep = detect_mod.detect(g, dur, cfg, capacity=capacity)
            flagged = set(rep.bottleneck_ids)
            n_bottlenecks += len(flagged)
            for nid in g.nodes:
                healthy = not g.is_blocking(nid)
                if healthy:
                    f_healthy += 1
                    if nid in flagged:
                        f_fp += 1
                else:
                    blocked_seen += 1
                    if nid in flagged:
                        blocked_flagged += 1
            heur = set(detect_mod.structural_heuristic_detect(g, dur))
            for nid in g.nodes:
                if not g.is_blocking(nid):
                    f_nodes += 1
                    if nid in heur:
                        f_heur_fp += 1
        per_family[fam] = {
            "trials": trials,
            "n_bottlenecks_flagged": n_bottlenecks,
            "healthy_nodes": f_healthy,
            "flagged_on_healthy": f_fp,
            "fpr": None if not f_healthy else round(f_fp / f_healthy, 6),
            "blocking_nodes": blocked_seen,
            "blocking_nodes_flagged": blocked_flagged,
            "blocking_recall": None if not blocked_seen else round(blocked_flagged / blocked_seen, 6),
            "heuristic_flagged_on_healthy": f_heur_fp,
            "heuristic_fpr": None if not f_nodes else round(f_heur_fp / f_nodes, 6),
        }
        tot_nodes += f_healthy
        tot_healthy += f_healthy
        tot_fp += f_fp
        heur_nodes += f_nodes
        heur_fp += f_heur_fp
    fpr = None if not tot_healthy else tot_fp / tot_healthy
    hfpr = None if not heur_nodes else heur_fp / heur_nodes
    return NegativeControl(
        trials=trials,
        seed=seed,
        nodes_total=tot_nodes,
        healthy_nodes=tot_healthy,
        flagged_on_healthy=tot_fp,
        fpr=fpr,
        heuristic_flagged=heur_fp,
        heuristic_fpr=hfpr,
        threshold=thr,
        passed=(fpr is not None and fpr <= thr),
        per_family=per_family,
    )


# --------------------------------------------------------------------------- #
# 检测召回（AC1）
# --------------------------------------------------------------------------- #
def detect_recall(graph: Graph, duration: Mapping[int, float], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    rep = detect_mod.detect(graph, duration, cfg, capacity=int(cfg["schedule"]["capacity"]))
    truth = {nid for nid in graph.nodes if graph.is_blocking(nid)}
    flagged = set(rep.bottleneck_ids)
    hit = truth & flagged
    return {
        "plan_id": graph.plan_id,
        "n_nodes": len(graph.nodes),
        "truth_blocking": sorted(truth),
        "flagged": sorted(flagged),
        "true_positives": sorted(hit),
        "false_negatives": sorted(truth - flagged),
        "false_positives": sorted(flagged - truth),
        "recall": None if not truth else len(hit) / len(truth),
        "precision": None if not flagged else len(hit) / len(flagged),
        "ranking": [b.node_id for b in rep.bottlenecks],
        "severities": {str(b.node_id): round(b.severity, 6) for b in rep.bottlenecks},
    }


# --------------------------------------------------------------------------- #
# 根因对拍（AC2）
# --------------------------------------------------------------------------- #
def load_labels(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def label_eval(labels: Mapping[str, Any], classifications: Sequence[rc_mod.Classification],
               cfg: Mapping[str, Any]) -> Dict[str, Any]:
    by_id = {str(c.node_id): c for c in classifications}
    classes = list(cfg["rootcause"]["classes"])
    rows: List[Dict[str, Any]] = []
    for rec in labels["labels"]:
        nid = str(rec["node_id"])
        c = by_id.get(nid)
        if c is None:
            continue
        rows.append(
            {
                "node_id": int(nid),
                "plan_id": rec.get("plan_id"),
                "manual_label": rec["manual_label"],
                "predicted": c.label,
                "rule_id": c.rule_id,
                "agree": rec["manual_label"] == c.label,
                "note": rec.get("note", ""),
            }
        )
    truth = [r["manual_label"] for r in rows]
    pred = [r["predicted"] for r in rows]
    cm = rc_mod.confusion_matrix(truth, pred, classes)
    acc = rc_mod.accuracy(truth, pred)
    rec = rc_mod.per_class_recall(truth, pred, classes)
    split_cfg = cfg["rootcause"].get("split", {"test_ratio": 0.4, "seed": 0})
    tr, te = rc_mod.stratified_split(rows, lambda r: r["manual_label"],
                                     float(split_cfg["test_ratio"]), int(split_cfg["seed"]))
    heldout_acc = rc_mod.accuracy([truth[i] for i in te], [pred[i] for i in te])
    threshold = float(cfg["backtest"].get("rootcause_accuracy_threshold", 0.70))
    return {
        "n_cases": len(rows),
        "labels_path_note": labels.get("label_source", ""),
        "classes": classes,
        "confusion_matrix": cm,
        "accuracy": None if acc is None else round(acc, 6),
        "per_class_recall": {k: (None if v is None else round(v, 6)) for k, v in rec.items()},
        "heldout": {
            "n_train": len(tr),
            "n_test": len(te),
            "accuracy": None if heldout_acc is None else round(heldout_acc, 6),
            "test_node_ids": sorted(rows[i]["node_id"] for i in te),
        },
        "threshold": threshold,
        "passed": (acc is not None and acc >= threshold),
        "cases": rows,
    }


# --------------------------------------------------------------------------- #
# 吞吐回测（AC3）
# --------------------------------------------------------------------------- #
@dataclass
class PlanBacktest:
    plan_id: int
    n_nodes: int
    n_remaining: int
    n_bottlenecks: int
    baseline_s: float
    improved_s: float
    gain: Optional[float]
    throughput_baseline: Optional[float]
    throughput_improved: Optional[float]
    rootcause_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "n_nodes": self.n_nodes,
            "n_remaining": self.n_remaining,
            "n_bottlenecks": self.n_bottlenecks,
            "baseline_makespan_h": round(self.baseline_s / 3600.0, 4),
            "improved_makespan_h": round(self.improved_s / 3600.0, 4),
            "gain": None if self.gain is None else round(self.gain, 6),
            "throughput_baseline_nodes_per_h": (
                None if self.throughput_baseline is None else round(self.throughput_baseline, 6)
            ),
            "throughput_improved_nodes_per_h": (
                None if self.throughput_improved is None else round(self.throughput_improved, 6)
            ),
            "rootcause_counts": dict(sorted(self.rootcause_counts.items())),
        }


def _plan_backtest(graph: Graph, duration: Mapping[int, float], cfg: Mapping[str, Any],
                   baseline_policy: Mapping[str, Any], attempt_divisor: float,
                   capacity: int) -> PlanBacktest:
    classes = {c.node_id: c.label for c in rc_mod.classify_graph(graph, duration, cfg)}
    rep = detect_mod.detect(graph, duration, cfg, capacity=capacity)
    base_p = dict(baseline_policy)
    base_p["attempt_divisor"] = attempt_divisor
    base = sched_mod.simulate(graph, duration, classes, base_p, capacity, "baseline")
    imp = sched_mod.improved_schedule(graph, duration, classes, cfg, capacity,
                                      divisor=attempt_divisor)
    gain = None if base.makespan_s <= 0 else 1.0 - imp.makespan_s / base.makespan_s
    n_remaining = len(graph.remaining())
    counts: Dict[str, int] = {}
    for c in classes.values():
        counts[c] = counts.get(c, 0) + 1
    return PlanBacktest(
        plan_id=graph.plan_id,
        n_nodes=len(graph.nodes),
        n_remaining=n_remaining,
        n_bottlenecks=len(rep.bottlenecks),
        baseline_s=base.makespan_s,
        improved_s=imp.makespan_s,
        gain=gain,
        throughput_baseline=(n_remaining / (base.makespan_s / 3600.0)) if base.makespan_s > 0 else None,
        throughput_improved=(n_remaining / (imp.makespan_s / 3600.0)) if imp.makespan_s > 0 else None,
        rootcause_counts=counts,
    )


def _median(xs: Sequence[float]) -> Optional[float]:
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def throughput_backtest(
    corpus, graphs: Mapping[int, Graph], cfg: Mapping[str, Any],
    plan_filter: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    bcfg = cfg["backtest"]
    max_plans = int(bcfg["max_plans"])
    thr = float(bcfg["throughput_gain_threshold"])
    sens = cfg["schedule"]["sensitivity"]
    considered: List[int] = []
    for pid, g in graphs.items():
        if plan_filter and pid not in plan_filter:
            continue
        if len(g.nodes) < 2:
            continue
        if not any(g.is_blocking(nid) for nid in g.nodes):
            continue
        considered.append(pid)
    considered.sort()
    selected = considered[:max_plans]
    # 旗舰样本必须入回测（AC3 的判据对象是 plan 233），否则判据数字会缺项
    flagship = int(bcfg.get("flagship_plan_id", 233))
    if flagship in considered and flagship not in selected:
        selected = sorted(selected + [flagship])

    def run(baseline_retry_max: int, divisor: float, capacity: int) -> List[PlanBacktest]:
        bp = dict(cfg["schedule"]["baseline"])
        bp["retry_max"] = baseline_retry_max
        out = []
        for pid in selected:
            g = graphs[pid]
            dur = {n.node_id: corpus.effective_duration(n) for n in g.nodes.values()}
            out.append(_plan_backtest(g, dur, cfg, bp, divisor, capacity))
        return out

    primary = run(int(cfg["schedule"]["baseline"]["retry_max"]),
                  float(cfg["schedule"]["baseline"]["attempt_divisor"]),
                  int(cfg["schedule"]["capacity"]))
    gains = [r.gain for r in primary if r.gain is not None]
    median_gain = _median(gains)
    mean_gain = (sum(gains) / len(gains)) if gains else None

    sensitivity: List[Dict[str, Any]] = []
    for retry in sens["baseline_retry_max"]:
        for div in sens["attempt_divisor"]:
            for cap in sens.get("capacity", [int(cfg["schedule"]["capacity"])]):
                rows = run(int(retry), float(div), int(cap))
                gg = [r.gain for r in rows if r.gain is not None]
                sensitivity.append(
                    {
                        "baseline_retry_max": retry,
                        "attempt_divisor": div,
                        "capacity": cap,
                        "n_plans": len(gg),
                        "median_gain": None if not gg else round(_median(gg), 6),
                        "mean_gain": None if not gg else round(sum(gg) / len(gg), 6),
                        "min_gain": None if not gg else round(min(gg), 6),
                    }
                )

    plan233 = next((r for r in primary if r.plan_id == 233), None)
    ac3_value = None if plan233 is None or plan233.gain is None else plan233.gain
    robust = all(
        (s["median_gain"] is not None and s["median_gain"] >= thr) for s in sensitivity
    )
    return {
        "n_plans_considered": len(considered),
        "n_plans_selected": len(selected),
        "plan_ids": selected,
        "primary": [r.to_dict() for r in primary],
        "median_gain": None if median_gain is None else round(median_gain, 6),
        "mean_gain": None if mean_gain is None else round(mean_gain, 6),
        "plan233_gain": None if ac3_value is None else round(ac3_value, 6),
        "threshold": thr,
        "passed_on_plan233": (ac3_value is not None and ac3_value >= thr),
        "passed_on_median": (median_gain is not None and median_gain >= thr),
        "sensitivity": sensitivity,
        "sensitivity_all_pass": bool(robust),
    }


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def build_plan233_view(corpus, graphs: Mapping[int, Graph], cfg: Mapping[str, Any],
                       plan_id: int = 233) -> Dict[str, Any]:
    g = graphs[plan_id]
    dur = {n.node_id: corpus.effective_duration(n) for n in g.nodes.values()}
    out = {
        "plan_id": plan_id,
        "detect": detect_recall(g, dur, cfg),
        "advice": realloc_mod.advice_for_plan(g, dur, cfg),
    }
    return out