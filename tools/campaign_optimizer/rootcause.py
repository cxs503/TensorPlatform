"""根因分类：声明式规则引擎（规则在 config，代码只做谓词求值）。

四类根因（与平台事件语义对齐，不含任何项目词汇）
-----------------------------------------------
* ``upstream_not_delivered`` —— 节点在等上游交付/授权放行（gate 类或上游未满足）
* ``artifact_mismatch``      —— 交付存在但口径不符（重复失败后仍被判不合格/空交付）
* ``resource_shortage``      —— 执行侧资源/执行器失效（runner 丢失、模型调用失败、无任务挂载）
* ``code_error``             —— 同一节点重复失败（任务本身有缺陷）
* ``unknown``                —— 证据不足（显式保留，不硬凑）

**特征白名单**（``ALLOWED_FEATURES``）：分类器只能看到这些结构化量。
节点名 / block_reason 等文本字段**不在白名单内**——这是 AC4「零领域语义」的机器可验形式：
``tests`` 里有「隐去全部文本后分类结果不变」与「随机改名后分类结果不变」两条行为证明。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .model import Graph, Node

ALLOWED_FEATURES = (
    "node_type",
    "has_mission",
    "mission_class",
    "consecutive_fails",
    "upstream_unsatisfied",
    "blocked_upstream",
    "downstream_gated",
    "duration_ratio",
    "total_float_s",
    "on_critical_path",
)

DEFAULT_MISSION_CLASS: Dict[str, Tuple[str, ...]] = {
    "succeeded": ("succeeded", "success", "done", "completed", "merged", "accepted", "verified"),
    "failed": ("failed", "stopped", "orphan", "revoked", "timeout", "error", "abandoned",
               "cancelled", "canceled", "rejected"),
    "active": ("running", "claimed", "in_progress", "pending", "queued", "dispatched",
               "awaiting_acceptance", "in-flight", "assigned"),
    "none": (),
}

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "gte": lambda a, b: _num(a) >= _num(b),
    "lte": lambda a, b: _num(a) <= _num(b),
    "gt": lambda a, b: _num(a) > _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "truthy": lambda a, _b: bool(a),
    "falsy": lambda a, _b: not bool(a),
}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


class RuleError(ValueError):
    pass


def mission_class(status: str, cfg: Mapping[str, Any]) -> str:
    table = cfg["rootcause"].get("mission_class") or DEFAULT_MISSION_CLASS
    s = (status or "").strip().lower()
    if not s:
        return "none"
    for cls, values in table.items():
        if s in {str(v).lower() for v in values}:
            return cls
    return "other"


def features(
    graph: Graph,
    node: Node,
    duration: Mapping[int, float],
    plan_median_duration_s: float = 0.0,
    cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """结构化特征（白名单唯一来源）。"""
    up = graph.upstream_closure(node.node_id)
    down = graph.downstream_closure(node.node_id)
    med = plan_median_duration_s or 0.0
    ratio = (float(duration.get(node.node_id, 0.0)) / med) if med > 0 else 0.0
    class_cfg = cfg or {"rootcause": {}}
    return {
        "node_type": node.node_type,
        "has_mission": node.mission_id is not None,
        "mission_class": mission_class(node.mission_status, class_cfg),
        "consecutive_fails": int(node.consecutive_fails),
        "upstream_unsatisfied": sum(1 for u in up if not graph.is_satisfied(u)),
        "blocked_upstream": sum(1 for u in up if graph.is_blocking(u)),
        "downstream_gated": sum(1 for d in down if not graph.is_satisfied(d)),
        "duration_ratio": ratio,
        "total_float_s": float(graph.total_float(duration).get(node.node_id, 0.0)),
        "on_critical_path": graph.total_float(duration).get(node.node_id, 1.0) <= 1e-9,
    }


def _eval_condition(cond: Mapping[str, Any], feat: Mapping[str, Any]) -> bool:
    name = cond.get("feature")
    op = cond.get("op", "eq")
    if name not in ALLOWED_FEATURES:
        raise RuleError(f"feature not in whitelist: {name!r}")
    if op not in _OPS:
        raise RuleError(f"unknown op: {op!r}")
    return bool(_OPS[op](feat.get(name), cond.get("value")))


def _eval_when(when: Mapping[str, Any], feat: Mapping[str, Any]) -> bool:
    if "all" in when:
        return all(_eval_condition(c, feat) for c in when["all"]) if when["all"] else True
    if "any" in when:
        return any(_eval_condition(c, feat) for c in when["any"])
    raise RuleError("rule 'when' must contain 'all' or 'any'")


def classify(feat: Mapping[str, Any], cfg: Mapping[str, Any]) -> Tuple[str, str]:
    """返回 (根因类, 命中规则 id)。规则顺序即优先级（config 声明，首次命中即返回）。"""
    extra = set(feat) - set(ALLOWED_FEATURES)
    if extra:
        raise RuleError(f"feature dict carries non-whitelisted keys: {sorted(extra)}")
    for rule in cfg["rootcause"]["rules"]:
        if _eval_when(rule["when"], feat):
            return str(rule["class"]), str(rule["id"])
    return "unknown", "no_rule_matched"


@dataclass
class Classification:
    node_id: int
    plan_id: int
    label: str
    rule_id: str
    features: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "label": self.label,
            "rule_id": self.rule_id,
            "features": {k: (round(v, 6) if isinstance(v, float) else v)
                         for k, v in sorted(self.features.items())},
        }


def classify_graph(
    graph: Graph, duration: Mapping[int, float], cfg: Mapping[str, Any]
) -> List[Classification]:
    durs = sorted(float(duration.get(nid, 0.0)) for nid in graph.nodes)
    med = durs[len(durs) // 2] if durs else 0.0
    out: List[Classification] = []
    for nid in graph.sorted_ids():
        node = graph.nodes[nid]
        if not graph.is_blocking(nid):
            continue
        feat = features(graph, node, duration, med, cfg)
        label, rule_id = classify(feat, cfg)
        out.append(Classification(node_id=nid, plan_id=graph.plan_id, label=label,
                                  rule_id=rule_id, features=feat))
    return out


# --------------------------------------------------------------------------- #
# 评估（混淆矩阵 / 准确率）
# --------------------------------------------------------------------------- #
def confusion_matrix(
    truth: Sequence[str], pred: Sequence[str], labels: Sequence[str]
) -> Dict[str, Dict[str, int]]:
    if len(truth) != len(pred):
        raise ValueError("truth/pred length mismatch")
    m = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(truth, pred):
        m.setdefault(t, {x: 0 for x in labels}).setdefault(p, 0)
        m[t][p] = m[t].get(p, 0) + 1
    return m


def accuracy(truth: Sequence[str], pred: Sequence[str]) -> Optional[float]:
    if not truth:
        return None
    return sum(1 for t, p in zip(truth, pred) if t == p) / len(truth)


def per_class_recall(
    truth: Sequence[str], pred: Sequence[str], labels: Sequence[str]
) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for lab in labels:
        idx = [i for i, t in enumerate(truth) if t == lab]
        out[lab] = (
            None if not idx else sum(1 for i in idx if pred[i] == lab) / len(idx)
        )
    return out


def stratified_split(
    items: Sequence[Any], key, test_ratio: float, seed: int
) -> Tuple[List[int], List[int]]:
    """按标签分层的时间无关随机拆分（返回索引），确定性：同 seed 同结果。"""
    import random

    rng = random.Random(seed)
    buckets: Dict[Any, List[int]] = {}
    for i, it in enumerate(items):
        buckets.setdefault(key(it), []).append(i)
    train: List[int] = []
    test: List[int] = []
    for lab in sorted(buckets, key=lambda x: str(x)):
        idx = sorted(buckets[lab])
        rng.shuffle(idx)
        n_test = max(1, int(round(len(idx) * test_ratio))) if len(idx) > 1 else 0
        test.extend(idx[:n_test])
        train.extend(idx[n_test:])
    return sorted(train), sorted(test)