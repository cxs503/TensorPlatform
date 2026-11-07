"""交叉模型验证：两独立推理族对同一物理案例独立判级（IN47-N08 判据 1）。

判据（机器可算）
----------------
1. 每个案例被两族**独立**判级，``cross_model_verified`` 仅在
   「证据词汇相同且 E 等级相同」时为 True —— 只有双方一致才确认 REAL 得到支撑。
2. 一致率 = 一致案例 / 可比案例；阈值 ``MIN_CONSISTENCY = 0.80``。
3. 等级距离 > 1 的不一致记为 ``severe``（如一方 REAL、一方 STUB），必须单列。
4. 与预注册参考标准比对给出**精确率**（``precision_vs_oracle``）：
   ``P(oracle=REAL | cross_model_verified)`` —— 交叉验证只能提「一致」，
   一致是否等于「真」，必须用独立参考标准度量。这是本模块的诚实性关口。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence

from .corpus import CaseRecord, REAL, WITHHELD, oracle_label
from .families import agree, family_a, family_b, label_distance

SCHEMA = "in47-n08-cross-model/1"
#: 一致率阈值（预注册）。
MIN_CONSISTENCY = 0.80


def cross_verify(
    cases: Sequence[CaseRecord],
    *,
    fam_a: Callable[[CaseRecord], Dict[str, Any]] = family_a,
    fam_b: Callable[[CaseRecord], Dict[str, Any]] = family_b,
    min_consistency: float = MIN_CONSISTENCY,
) -> Dict[str, Any]:
    """两族独立判级 + 一致性统计 + 与参考标准的一致率（精确率）。"""
    rows: List[Dict[str, Any]] = []
    verified: List[str] = []
    disputes: List[Dict[str, Any]] = []
    severe: List[Dict[str, Any]] = []

    tp = fp = fn = tn = 0  # 混淆：cross_model_verified(REAL 判定) vs oracle
    for c in cases:
        va = fam_a(c)
        vb = fam_b(c)
        ok = agree(va, vb)
        orc = oracle_label(c)
        rows.append({
            "case_id": c.case_id,
            "domain": c.domain,
            "hull": c.hull,
            "split": c.split,
            "family_a": {k: va[k] for k in ("label", "e_level", "grade", "score")},
            "family_b": {k: vb[k] for k in ("label", "e_level", "grade", "score")},
            "agreed": ok,
            "consistent_label": va["label"] if ok else None,
            # 联合判级：不一致时不确认 REAL，按保守侧 WITHHELD 上报（原始两族判级完整保留）
            "joint_label": va["label"] if ok else WITHHELD,
            "oracle_label": orc["label"],
            "oracle_reason": orc["reason"],
            "abs_error_pct": None if c.abs_error_pct is None else round(c.abs_error_pct, 3),
            "ref_class": c.ref_class,
        })
        if ok:
            verified.append(c.case_id)
        else:
            d = label_distance(va["label"], vb["label"])
            entry = {"case_id": c.case_id, "family_a": va["label"], "family_b": vb["label"],
                     "e_distance": d}
            disputes.append(entry)
            if d > 1:
                severe.append(entry)
        # 精确率/召回率：把「两族一致判 REAL」当作正预测
        pos = ok and va["label"] == REAL
        truth = orc["label"] == REAL
        tp += int(pos and truth)
        fp += int(pos and not truth)
        fn += int((not pos) and truth)
        tn += int((not pos) and (not truth))

    total = len(rows)
    consistency = len(verified) / total if total else None
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "schema": SCHEMA,
        "n_cases": total,
        "n_compared": total,
        "n_agreed": len(verified),
        "consistency": None if consistency is None else round(consistency, 4),
        "min_consistency": min_consistency,
        "cross_model_verified": verified,
        "disputes": disputes,
        "severe_mismatches": severe,
        "oracle_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision_vs_oracle": None if precision is None else round(precision, 4),
        "recall_vs_oracle": None if recall is None else round(recall, 4),
        "pass": consistency is not None and consistency >= min_consistency,
        "rows": rows,
        "note": "两族独立判级；一致 = 证据词汇且 E 等级相同；仅一致案例标 cross_model_verified=true。",
    }