"""backtest —— 就绪门的回测面：混淆矩阵 / PR 曲线 / 打乱标签负控。

评估口径（判据数字落 ``artifacts/dispatch_contract/backtest_report.json``）
-----------------------------------------------------------------------
* 预测正类 = 门判定 ``BLOCK``（即 score > 操作阈值）。
* 实际正类 = 语料主标签 ``label_blocked``。
* precision / recall / f1 / auc（秩统计，等值取平均秩）+ PR 曲线点集。
* **两种分母**都报：
  - ``population``：全体终态节点（含降级放行的无契约节点，它们只能判负）；
  - ``decidable``：声明了机器可解析 input_contract 的节点子集（门真正能判的那部分）。
  二者的差就是「契约覆盖率」对判据的直接代价，报告里必须并列，不许只挑好的报。
* 负控（AC3）：把标签按固定种子随机置换 ``N`` 次，在同一操作点上算 precision，
  与随机基线比较。平衡评估设计下随机基线 ≈ 0.5（正负各半），这也是我们做平衡
  采样的理由：不平衡下 precision 的零假设就是基率本身，AC3 的 0.5 不可解释。
* 训练/测试切分：``mission_id`` 奇偶（确定性、无标签信息）→ 阈值只在训练侧调，
  测试侧报数，避免「阈值调在测试集」的自欺。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contract_schema import load_config
from .readiness import VERDICT_BLOCK, assess

REPORT_SCHEMA = "dispatch-contract/backtest/v1"


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def confusion(y_true: Sequence[int], y_pred: Sequence[int]) -> dict:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 6), "recall": round(rec, 6),
            "f1": round(f1, 6), "specificity": round(spec, 6),
            "accuracy": round((tp + tn) / max(1, len(y_true)), 6),
            "n": len(y_true), "positives": sum(y_true)}


def roc_auc(scores: Sequence[float], y_true: Sequence[int]) -> float:
    """秩和法 AUC（等值取平均秩）；单一类别时返回 ``float('nan')``。"""
    n = len(scores)
    n_pos = sum(y_true)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = sorted(range(n), key=lambda i: scores[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    s = sum(ranks[i] for i in range(n) if y_true[i] == 1)
    return round((s - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg), 6)


def pr_curve(scores: Sequence[float], y_true: Sequence[int]) -> list[dict]:
    """按分数降序扫描得到 PR 曲线点（每个不同分数一个点）。"""
    pairs = sorted(zip(scores, y_true), key=lambda p: -p[0])
    total_pos = sum(y_true)
    pts = []
    tp = fp = 0
    for i, (s, y) in enumerate(pairs):
        if y == 1:
            tp += 1
        else:
            fp += 1
        if i + 1 == len(pairs) or pairs[i + 1][0] != s:
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec = tp / total_pos if total_pos else 0.0
            pts.append({"threshold": round(float(s), 6), "precision": round(prec, 6),
                        "recall": round(rec, 6), "tp": tp, "fp": fp})
    return pts


def best_threshold(scores: Sequence[float], y_true: Sequence[int], rule: str = "f1") -> float:
    """在给定集合上按 ``rule`` (f1|precision@recall) 选操作阈值（规则 ``score >= t``）。"""
    best_t, best_v = float("inf"), -1.0
    for pt in pr_curve(scores, y_true):
        prec, rec = pt["precision"], pt["recall"]
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        v = f1 if rule == "f1" else (prec if rec >= 0.7 else -1.0)
        if v > best_v + 1e-12:
            best_v, best_t = v, pt["threshold"]
    return 0.0 if best_t == float("inf") else best_t


# --------------------------------------------------------------------------- #
# 评估
# --------------------------------------------------------------------------- #
def score_records(records: Iterable[Mapping[str, Any]], cfg: Mapping[str, Any] | None = None) -> list[dict]:
    """对每条语料跑门，产出 (score, verdict, degraded, label) 记录。"""
    cfg = cfg or load_config()
    out = []
    for r in records:
        dispatch = r.get("dispatch") or {"node_id": r.get("node_id"), "contract": None}
        res = assess(dispatch, cfg)
        out.append({
            "mission_id": str(r.get("mission_id")),
            "label": int(r.get("label_blocked", 0)),
            "label_non_delivery": int(r.get("label_non_delivery", 0)),
            "score": float(res["score"]),
            "verdict": res["verdict"],
            "degraded": bool(res["degraded"]),
            "degrade_reason": res.get("degrade_reason"),
            "n_required": int(res.get("n_required") or 0),
            "unmet_codes": sorted({u["code"] for u in res.get("unmet", [])}),
            "label_input_unavailable": r.get("label_input_unavailable"),
            "oracle_unavailable": r.get("oracle_unavailable") or [],
            "fail_stage": r.get("fail_stage"),
        })
    return out


def _pred(scores: Sequence[float], threshold: float) -> list[int]:
    """预测规则：``score >= threshold``（与 :func:`pr_curve` 的阈值语义一致）。"""
    return [1 if s >= threshold - 1e-12 else 0 for s in scores]


def evaluate(records: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any] | None = None,
             shuffles: int | None = None, seed: int | None = None) -> dict:
    """完整回测：全体/可判子集/训练测试切分/PR/负控/平衡评估。"""
    cfg = cfg or load_config()
    bt = cfg.get("backtest") or {}
    nc = bt.get("negative_control") or {}
    n_shuf = int(shuffles if shuffles is not None else nc.get("shuffles", 200))
    rng_seed = int(seed if seed is not None else nc.get("seed", 20261110))

    scored = score_records(records, cfg)
    by_id = {s["mission_id"]: s for s in scored}

    def subset(rows):
        return {
            "scores": [r["score"] for r in rows],
            "labels": [r["label"] for r in rows],
            "verdicts": [r["verdict"] for r in rows],
        }

    population = subset(scored)
    decidable_rows = [r for r in scored if not r["degraded"]]
    decidable = subset(decidable_rows)

    # ---- 口径 A：契约可满足性（门判定 vs 独立事实查询 oracle）---- #
    # y_true = 派单时刻声明的输入在注册表中不可得（由 corpus.oracle_input_unavailable
    # 独立直扫交付事实表算出，与 readiness 零代码共享）；y_pred = 门判定 BLOCK。
    # 这一口径衡量「判定逻辑 + 契约推断」是否正确，不含「不可得是否导致节点失败」。
    tier_a_rows = [r for r in decidable_rows if r.get("label_input_unavailable") is not None]
    y_a = [int(r["label_input_unavailable"]) for r in tier_a_rows]
    p_a = [1 if r["verdict"] == VERDICT_BLOCK else 0 for r in tier_a_rows]
    disagreements = [
        {"mission_id": r["mission_id"], "oracle_unavailable": r["label_input_unavailable"],
         "gate_block": 1 if r["verdict"] == VERDICT_BLOCK else 0,
         "unmet_codes": r["unmet_codes"], "oracle_detail": r["oracle_unavailable"]}
        for r, y, p in zip(tier_a_rows, y_a, p_a) if y != p
    ]
    tier_a_section = {
        "definition": "y_true=派单时刻声明输入不可得(oracle 独立直扫交付事实表); y_pred=门判定 BLOCK",
        "confusion": confusion(y_a, p_a),
        "n_disagreements": len(disagreements),
        "disagreements": disagreements[:20],
    }

    # 训练/测试切分（mission_id 奇偶）
    train_rows = [r for r in scored if int(r["mission_id"]) % 2 == 0]
    test_rows = [r for r in scored if int(r["mission_id"]) % 2 == 1]
    thr = best_threshold([r["score"] for r in train_rows], [r["label"] for r in train_rows], "f1")
    if thr <= 0.0:
        # 训练侧无任何正分（门在该切分上一律放行）→ 退回门的判定规则 score>0
        thr = 1e-9

    def verdict_metrics(rows):
        y = [r["label"] for r in rows]
        p = [1 if r["verdict"] == VERDICT_BLOCK else 0 for r in rows]
        return confusion(y, p)

    def block_metrics(rows, threshold):
        y = [r["label"] for r in rows]
        p = _pred([r["score"] for r in rows], threshold)
        return confusion(y, p)

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "coverage": {
            "n_records": len(scored),
            "n_decidable": len(decidable_rows),
            "coverage": round(len(decidable_rows) / max(1, len(scored)), 6),
            "n_blocked": sum(population["labels"]),
            "n_blocked_decidable": sum(decidable["labels"]),
            "degrade_reason_counts": _count(r.get("degrade_reason") for r in scored),
            "unmet_code_counts": _count(c for r in scored for c in r["unmet_codes"]),
        },
        "operating_point": {"threshold_from": "train_f1", "threshold": thr},
        "population": {
            "auc": roc_auc(population["scores"], population["labels"]),
            "verdict_block": verdict_metrics(scored),
            "at_threshold": block_metrics(scored, thr),
        },
        "decidable": {
            "auc": roc_auc(decidable["scores"], decidable["labels"]),
            "verdict_block": verdict_metrics(decidable_rows),
            "at_threshold": block_metrics(decidable_rows, thr),
        },
        "split": {
            "n_train": len(train_rows), "n_test": len(test_rows),
            "train_at_threshold": block_metrics(train_rows, thr),
            "test_at_threshold": block_metrics(test_rows, thr),
            "test_auc": roc_auc([r["score"] for r in test_rows], [r["label"] for r in test_rows]),
        },
        "pr_curve": {
            "population": pr_curve(population["scores"], population["labels"]),
            "decidable": pr_curve(decidable["scores"], decidable["labels"]),
        },
    }
    report["tier_a_satisfiability"] = tier_a_section

    # ---- 负控：打乱标签，同一操作点，比较 precision 与随机基线 ---- #
    rng = random.Random(rng_seed)
    base_rows = decidable_rows if len(decidable["labels"]) >= 20 else scored
    base_labels = [r["label"] for r in base_rows]
    base_scores = [r["score"] for r in base_rows]
    precisions, recalls = [], []
    for _ in range(n_shuf):
        shuf = base_labels[:]
        rng.shuffle(shuf)
        p = _pred(base_scores, thr)
        cm = confusion(shuf, p)
        precisions.append(cm["precision"])
        recalls.append(cm["recall"])
    prevalence = sum(base_labels) / max(1, len(base_labels))
    report["negative_control"] = {
        "shuffles": n_shuf, "seed": rng_seed, "basis": "decidable" if base_rows is decidable_rows else "population",
        "label_prevalence": round(prevalence, 6),
        "precision_mean": round(sum(precisions) / len(precisions), 6),
        "precision_sd": round(_sd(precisions), 6),
        "precision_p05": round(sorted(precisions)[int(0.05 * len(precisions))], 6),
        "precision_p95": round(sorted(precisions)[int(0.95 * len(precisions))], 6),
        "recall_mean": round(sum(recalls) / len(recalls), 6),
        "random_baseline_precision": round(prevalence, 6),
        "observed_precision_at_threshold": report["decidable"]["at_threshold"]["precision"],
    }

    # ---- 口径 A 的负控与平衡设计（判据 AC3 的 0.5 随机基线）---- #
    rng2 = random.Random(rng_seed + 7)
    if tier_a_rows:
        pos_a = [r for r in tier_a_rows if r["label_input_unavailable"] == 1]
        neg_a = [r for r in tier_a_rows if r["label_input_unavailable"] == 0]
        k_a = min(len(pos_a), len(neg_a))
        bal_a = list(pos_a) + rng2.sample(neg_a, k_a) if k_a else list(tier_a_rows)
        yb = [int(r["label_input_unavailable"]) for r in bal_a]
        pb = [1 if r["verdict"] == VERDICT_BLOCK else 0 for r in bal_a]
        shuf_a = []
        for _ in range(n_shuf):
            s2 = yb[:]
            rng2.shuffle(s2)
            shuf_a.append(confusion(s2, pb)["precision"])
        report["tier_a_satisfiability"]["balanced_design"] = {
            "n": len(bal_a), "prevalence": round(sum(yb) / max(1, len(yb)), 6),
            "observed_precision": confusion(yb, pb)["precision"],
            "observed_recall": confusion(yb, pb)["recall"],
            "shuffled_precision_mean": round(sum(shuf_a) / len(shuf_a), 6),
            "shuffled_precision_sd": round(_sd(shuf_a), 6),
            "shuffled_precision_p05": round(sorted(shuf_a)[int(0.05 * len(shuf_a))], 6),
            "shuffled_precision_p95": round(sorted(shuf_a)[int(0.95 * len(shuf_a))], 6),
        }

    # ---- 平衡评估设计（AC3 的 0.5 随机基线在此设计下才成立） ---- #
    balanced = balanced_sample(scored, seed=rng_seed + 1)
    b_scores = [r["score"] for r in balanced]
    b_labels = [r["label"] for r in balanced]
    b_prec_obs = confusion(b_labels, _pred(b_scores, thr))["precision"]
    b_shuf = []
    for _ in range(n_shuf):
        shuf = b_labels[:]
        rng.shuffle(shuf)
        b_shuf.append(confusion(shuf, _pred(b_scores, thr))["precision"])
    report["balanced_design"] = {
        "n": len(balanced), "positives": sum(b_labels),
        "prevalence": round(sum(b_labels) / max(1, len(b_labels)), 6),
        "observed_precision": round(b_prec_obs, 6),
        "shuffled_precision_mean": round(sum(b_shuf) / len(b_shuf), 6),
        "shuffled_precision_sd": round(_sd(b_shuf), 6),
        "auc": roc_auc(b_scores, b_labels),
    }

    # ---- 按失败阶段归因（回答「blocked 到底为什么」） ---- #
    report["fail_stage_profile"] = _count(r.get("fail_stage") for r in scored if r["label"] == 1)
    return report


def balanced_sample(rows: Sequence[Mapping[str, Any]], seed: int = 0) -> list[Mapping[str, Any]]:
    """平衡评估集：全部正类 + 等量随机负类（确定性种子）。"""
    pos = [r for r in rows if r["label"] == 1]
    neg = [r for r in rows if r["label"] == 0]
    rng = random.Random(seed)
    k = min(len(pos), len(neg))
    return list(pos) + rng.sample(neg, k)


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        key = str(v)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _sd(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def write_report(report: Mapping[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")