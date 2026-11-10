"""cycle_baseline.fairness —— 调度公平性：worker Jain / 等待 Gini / 饥饿 / 优先级反转。

两条纪律
--------
1. **分母永远显式**：任何比率都返回 ``None`` 而不是 0，除非样本存在且真为 0。
2. **检测器必须可判分**：饥饿与优先级反转都是"检测器"，其 F1 只在**带标签的合成夹具**上
   能算（真实数据没有人工标签）。因此本层把"检测"与"判分"分开：``detect_*`` 只输出
   判定，``score_detector`` 负责 tp/fp/fn → precision/recall/F1，报告里必须写明
   "F1 来自合成夹具，真实语料 F1 未知"。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from . import spec
from .segments import parse_ts
from .stats import gini, jain_index, mean, pcts


# ── worker 维度公平 ─────────────────────────────────────────────────────
def worker_fairness(records: Sequence[dict]) -> dict[str, Any]:
    """按 worker 聚合负载（领取计数 + 执行忙时），输出 Jain/Gini 与分布。"""
    counts: dict[str, float] = {}
    busy: dict[str, float] = {}
    waits: list[float] = []
    for r in records:
        w = r.get("worker")
        if w:
            counts[w] = counts.get(w, 0.0) + 1.0
            if r.get("t_exec") is not None:
                busy[w] = busy.get(w, 0.0) + float(r["t_exec"])
        if r.get("t_queue") is not None:
            waits.append(float(r["t_queue"]))
    workers = sorted(counts)
    cvec = [counts[w] for w in workers]
    bvec = [busy.get(w, 0.0) for w in workers]
    _mean = (sum(cvec) / len(cvec)) if cvec else None
    _var = (sum((x - _mean) ** 2 for x in cvec) / len(cvec)) if cvec and _mean else None
    return {
        "n_workers": len(workers),
        "max_over_mean_count": (max(cvec) / _mean) if (_mean and cvec) else None,
        "cv_count": ((_var ** 0.5) / _mean) if (_mean and _var) else None,
        "n_missions_with_worker": int(sum(cvec)),
        "jain_count": jain_index(cvec),
        "jain_busy_time": jain_index(bvec) if any(bvec) else None,
        "gini_count": gini(cvec),
        "gini_busy_time": gini(bvec) if any(bvec) else None,
        "wait_gini": gini(waits) if waits else None,
        "wait_p50_s": pcts(waits, qs=(0.5,))["p50"],
        "wait_p95_s": pcts(waits, qs=(0.95,))["p95"],
        "wait_n": len(waits),
        "top_workers": [
            {"worker": w, "missions": int(counts[w]), "busy_s": busy.get(w, 0.0)}
            for w in sorted(workers, key=lambda x: -counts[x])[:10]
        ],
        "definitions": {
            "jain": spec.DEFINITIONS["jain"],
            "gini": spec.DEFINITIONS["gini"],
        },
    }


# ── 饥饿检测 ────────────────────────────────────────────────────────────
def starvation_threshold(records: Sequence[dict], pctl: float = spec.STARVATION_PCTL) -> Optional[float]:
    waits = [r["t_queue"] for r in records if r.get("t_queue") is not None]
    if not waits:
        return None
    return pcts(waits, qs=(pctl,))[f"p{int(round(pctl * 100))}"]


def detect_starvation(records: Sequence[dict], threshold_s: Optional[float] = None,
                      pctl: float = spec.STARVATION_PCTL) -> dict[str, Any]:
    """饥饿 = 排队 > p95 且终态非 succeeded（等待超阈却未正常完成）。

    返回 flagged mission 列表 + 中间量（阈值、分母），便于审计与对拍。
    """
    thr = threshold_s if threshold_s is not None else starvation_threshold(records, pctl)
    flagged: list[dict[str, Any]] = []
    eligible = 0
    for r in records:
        if r.get("t_queue") is None:
            continue
        eligible += 1
        if thr is not None and r["t_queue"] > thr and r.get("status") != "succeeded":
            flagged.append({"mission_id": r.get("mission_id"), "worker": r.get("worker"),
                            "repo": r.get("repo"), "status": r.get("status"),
                            "wait_s": r["t_queue"], "threshold_s": thr})
    return {
        "threshold_s": thr,
        "percentile": pctl,
        "n_eligible": eligible,
        "n_flagged": len(flagged),
        "flag_rate": (len(flagged) / eligible) if eligible else None,
        "flagged": flagged,
        "definition": spec.DEFINITIONS["starvation"],
    }


# ── 优先级反转检测 ──────────────────────────────────────────────────────
def detect_priority_inversions(records: Sequence[dict],
                               gap_s: float = spec.INVERSION_GAP_S) -> dict[str, Any]:
    """同战役内：A 的 planned_start 早于 B（差 ≥ gap_s），但 A 的领取晚于 B → A 被反转。

    只对有 planned_start 且已领取的 mission 判定；未领取的（仍在队）不进配对，但单独计数，
    因为它们可能是"更长期地被跳过"（另给 starved_unclaimed 计数）。
    """
    by_plan: dict[Any, list[dict]] = {}
    for r in records:
        if r.get("plan_id") is None:
            continue
        by_plan.setdefault(r["plan_id"], []).append(r)
    flagged: list[dict[str, Any]] = []
    n_pairs = 0
    n_eligible = 0
    unclaimed = 0
    for plan_id, recs in by_plan.items():
        claimed = [r for r in recs if r.get("claimed_at") and parse_ts(r.get("planned_start"))]
        unclaimed += sum(1 for r in recs if not r.get("claimed_at"))
        n_eligible += len(claimed)
        for a in claimed:
            pa = parse_ts(a["planned_start"])
            ca = parse_ts(a["claimed_at"])
            jumped = []
            for b in claimed:
                if b is a:
                    continue
                pb = parse_ts(b["planned_start"])
                cb = parse_ts(b["claimed_at"])
                if (pb - pa).total_seconds() < gap_s:
                    continue
                n_pairs += 1
                if ca > cb:
                    jumped.append({"by_mission_id": b.get("mission_id"),
                                   "by_planned_start": b["planned_start"],
                                   "by_claimed_at": b["claimed_at"]})
            if jumped:
                flagged.append({"mission_id": a.get("mission_id"), "plan_id": plan_id,
                                "planned_start": a["planned_start"],
                                "claimed_at": a["claimed_at"],
                                "jumped_by": jumped[:5], "n_jumped_by": len(jumped)})
    return {
        "gap_s": gap_s,
        "n_eligible": n_eligible,
        "n_pairs_compared": n_pairs,
        "n_unclaimed_in_plans": unclaimed,
        "n_flagged": len(flagged),
        "flag_rate": (len(flagged) / n_eligible) if n_eligible else None,
        "flagged": flagged,
        "definition": spec.DEFINITIONS["priority_inversion"],
    }


# ── 检测器判分（合成带标签夹具专用） ─────────────────────────────────────
def score_detector(flagged_ids: Iterable[Any],
                   labels: dict[Any, Optional[bool]]) -> dict[str, Any]:
    """tp/fp/fn → precision/recall/F1。

    ``labels`` 是 ground truth：``True`` 正例、``False`` 负例、``None`` 歧义（边界样本，
    判分时**整条剔除**，既不记 tp/fp 也不记 fn —— 把边界样本算进分母是常见的"F1 注水"）。
    分母为 0 时对应指标为 None。
    """
    ambiguous = {k for k, v in labels.items() if v is None}
    unambiguous = {k: bool(v) for k, v in labels.items() if v is not None}
    flagged = {f for f in flagged_ids if f not in ambiguous}
    pos = {k for k, v in unambiguous.items() if v}
    tp = len(flagged & pos)
    fp = len(flagged - pos)
    fn = len(pos - flagged)
    precision = (tp / (tp + fp)) if (tp + fp) else None
    recall = (tp / (tp + fn)) if (tp + fn) else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = None
    return {"tp": tp, "fp": fp, "fn": fn, "n_flagged": len(flagged),
            "n_positives": len(pos), "n_unambiguous": len(unambiguous),
            "n_ambiguous_excluded": len(ambiguous),
            "precision": precision, "recall": recall, "f1": f1}


def mean_or_none(vals: Sequence[Optional[float]]) -> Optional[float]:
    return mean(vals)