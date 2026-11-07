"""外部效度：hold-out 船型集上验证「非过拟合」（IN47-N08 判据 2）。

两条独立的效度度量
------------------
**(a) 评分器外部效度（本节点结论用）**
    评分器（两族 + 参考标准）在 ``verified`` 船型集上判级的正确率 vs 在
    ``holdout`` 船型集（完全不参与任何标定）上的正确率，要求
    ``|Δacc| ≤ 5 pp``；两族一致率同样要求 ``|Δconsistency| ≤ 5 pp``。
    这是「阈值没被这组船型过拟合」的直接检验。

**(b) 物理模型外部效度（审计发现，逐物理域报告）**
    在适用域内（``domain_ok``）按域比较 hold-out 与 verified 船型的平均绝对
    预报误差 ``|Δerr| ≤ 5 pp``。域内误差是模型自身的泛化误差，不是评分器的。
    域外案例（如棱柱体公式用于抛物剖面）按适用域规则剔除，并单独列出。

两者都不允许按结果调整阈值：族 A/B 的阈值是预注册常量（15% 与 τ=0.72），
本模块只测量，不拟合。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from .corpus import (
    REF_SELF_CONSISTENCY_TOL_PCT,
    CaseRecord,
    REAL,
    WITHHELD,
    holdout_hulls,
    oracle_label,
    verified_hulls,
)
from .families import agree, family_a, family_b

SCHEMA = "in47-n08-external-validity/1"
#: 外部效度容差（pp = 百分点），预注册。
MAX_DELTA_PP = 5.0
#: hold-out 船型数量下限。
MIN_HOLDOUT_HULLS = 3


def _accuracy(rows: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    hit = sum(1 for r in rows if r[key] == r["oracle_label"])
    return round(hit / len(rows), 4)


def _mean_abs_err(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    vals = [r["abs_error_pct"] for r in rows if r["abs_error_pct"] is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def evaluate_external_validity(
    cases: Sequence[CaseRecord],
    *,
    fam_a: Callable[[CaseRecord], Dict[str, Any]] = family_a,
    fam_b: Callable[[CaseRecord], Dict[str, Any]] = family_b,
    max_delta_pp: float = MAX_DELTA_PP,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for c in cases:
        va = fam_a(c)
        vb = fam_b(c)
        ok = agree(va, vb)
        orc = oracle_label(c)
        rows.append({
            "case_id": c.case_id, "domain": c.domain, "hull": c.hull, "split": c.split,
            # 联合判级：一致时取该判级；争议时不确认 REAL，按保守侧 WITHHELD 上报
            "joint_label": va["label"] if ok else WITHHELD,
            "family_a_label": va["label"],
            "family_b_label": vb["label"],
            "oracle_label": orc["label"],
            "agreed": ok,
            "abs_error_pct": None if c.abs_error_pct is None else round(c.abs_error_pct, 3),
            "domain_ok": c.domain_ok,
            "ref_class": c.ref_class,
            "ref_self_consistent": (
                c.ref_self_consistency_pct is None
                or abs(c.ref_self_consistency_pct) <= REF_SELF_CONSISTENCY_TOL_PCT),
        })

    by_split = {}
    for split in ("verified", "holdout"):
        sub = [r for r in rows if r["split"] == split]
        by_split[split] = {
            "n_cases": len(sub),
            "hulls": sorted({r["hull"] for r in sub}),
            "joint_accuracy": _accuracy(sub, "joint_label"),
            "family_a_accuracy": _accuracy(sub, "family_a_label"),
            "family_b_accuracy": _accuracy(sub, "family_b_label"),
            "consistency": (round(sum(1 for r in sub if r["agreed"]) / len(sub), 4) if sub else None),
            "mean_abs_error_pct": _mean_abs_err(sub),
        }

    # (a) 评分器外部效度
    dv = by_split["verified"]
    dh = by_split["holdout"]
    delta_acc = (None if (dv["joint_accuracy"] is None or dh["joint_accuracy"] is None)
                 else round(100.0 * (dh["joint_accuracy"] - dv["joint_accuracy"]), 4))
    delta_cons = (None if (dv["consistency"] is None or dh["consistency"] is None)
                  else round(100.0 * (dh["consistency"] - dv["consistency"]), 4))
    delta_err = (None if (dv["mean_abs_error_pct"] is None or dh["mean_abs_error_pct"] is None)
                 else round(dh["mean_abs_error_pct"] - dv["mean_abs_error_pct"], 4))

    scorer_ok = (
        len(holdout_hulls(cases)) >= MIN_HOLDOUT_HULLS
        and delta_acc is not None and abs(delta_acc) <= max_delta_pp
        and delta_cons is not None and abs(delta_cons) <= max_delta_pp
    )

    # (b) 物理模型外部效度：逐域（只用适用域内案例）
    domains = sorted({r["domain"] for r in rows})
    per_domain: Dict[str, Any] = {}
    for d in domains:
        # 参考值自一致性失败的案例不代表模型误差（是参考侧问题），剔除并单列
        v = [r for r in rows if r["domain"] == d and r["split"] == "verified"
             and r["domain_ok"] and r["ref_self_consistent"]]
        h = [r for r in rows if r["domain"] == d and r["split"] == "holdout"
             and r["domain_ok"] and r["ref_self_consistent"]]
        mv, mh = _mean_abs_err(v), _mean_abs_err(h)
        per_domain[d] = {
            "n_verified": len(v), "n_holdout": len(h),
            "mean_abs_err_verified_pct": mv, "mean_abs_err_holdout_pct": mh,
            "delta_err_pp": (None if (mv is None or mh is None) else round(mh - mv, 4)),
            "measured": (mv is not None and mh is not None),
        }
        if per_domain[d]["measured"]:
            per_domain[d]["pass"] = abs(per_domain[d]["delta_err_pp"]) <= max_delta_pp
    measured_domains = {k: v for k, v in per_domain.items() if v.get("measured")}
    model_ok = bool(measured_domains) and all(v["pass"] for v in measured_domains.values())

    return {
        "schema": SCHEMA,
        "holdout_hulls": holdout_hulls(cases),
        "verified_hulls": verified_hulls(cases),
        "n_holdout_hulls": len(holdout_hulls(cases)),
        "min_holdout_hulls": MIN_HOLDOUT_HULLS,
        "by_split": by_split,
        "scorer_external_validity": {
            "delta_accuracy_pp": delta_acc,
            "delta_consistency_pp": delta_cons,
            "delta_mean_abs_err_pp": delta_err,
            "tolerance_pp": max_delta_pp,
            "pass": scorer_ok,
        },
        "model_generalization": {
            "per_domain": per_domain,
            "pass": model_ok,
            "failing_domains": sorted(k for k, v in measured_domains.items() if not v["pass"]),
        },
        "pass": scorer_ok,
        "rows": rows,
        "note": ("评分器外部效度=|Δacc|,|Δ一致性|≤5pp（本节点结论）；"
                 "物理模型泛化=域内 |Δ平均误差|≤5pp（逐域审计发现，失败域单列且不粉饰）。"),
    }