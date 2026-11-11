"""回放重判器：把历史判据逐条重判，产出「判据退化清单」+ 可复算替代判据数值。

对每条历史判据，本模块给出五件事（缺一不可，否则不算"可复算"）：

1. **退化类**（T1/T2/T3/none）与触发依据（含分母、floor、符号的具体数）
2. **朴素判决**：历史口径下的相对误差与 PASS/FAIL（退化时该判决不可信）
3. **替代判据**：自适应族成员名 + 该成员口径下的误差数值 + 判决阈值
4. **结论翻转标记**：``flipped`` = 替代判决 ≠ 朴素判决；``declared_conflict`` =
   历史报告声明的判决 ≠ 替代判决（说明历史结论要么站得住，要么应当改正）
5. **替代判据的可复算性**：``recompute`` 段给出把数值代回公式所需的全部输入，
   复核者不需要原始报告即可重算（这是"零手抄"的机器保证）
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from .adaptive import AdaptiveResult, evaluate_family, family_table
from .degeneracy import DegeneracyClass, DegeneracyFinding, DegeneracyPolicy, classify_degeneracy
from .model import Criterion, Verdict, normalize_criterion, relative_error


def _recompute_payload(c: Criterion, a: AdaptiveResult) -> dict[str, Any]:
    """把替代判据的算式与代入值全部摊开，供第三方复算。"""
    formula = {
        "A1": "|y - y_ref| / max(|y_ref|, scale) <= tau",
        "A2": "|ln(y / y_ref)| <= |ln(1+tau)|",
        "A3": "|y - y_ref| <= |tau| * scale",
        "A4": "y in [lo*(1-tau), hi*(1+tau)]",
        "A5": "y >= target (comparator '>=') or y <= target ('<=')",
    }
    member = a.family_member.split("+")[0]
    return {
        "member": member,
        "formula": formula.get(member, "unknown"),
        "inputs": {
            "y": c.y,
            "y_ref": c.y_ref,
            "interval": c.reference_interval.as_dict() if c.reference_interval else None,
            "tau": c.tau,
            "scale": c.scale,
            "scale_source": c.scale_source,
            "comparator": c.comparator,
        },
        "value": a.value,
        "bound": a.bound,
        "verdict": a.verdict.value,
    }


def rejudge_one(
    c: Criterion,
    policy: DegeneracyPolicy | None = None,
    *,
    finding: DegeneracyFinding | None = None,
) -> dict[str, Any]:
    """单条判据回放重判。"""
    c = normalize_criterion(c)
    f = finding or classify_degeneracy(c, policy)
    a = evaluate_family(c, policy, finding=f)
    hist = c.declared_verdict
    declared_conflict = bool(hist is not None and hist is not Verdict.UNDECIDABLE and hist is not a.verdict)
    recorded_error = (c.source or {}).get("reported_error")
    reproduced = None
    reproduced_loose = None
    if recorded_error is not None and a.naive_relative is not None:
        # 回放自检两级：严格（1e-6，判"是不是同一条算出来的"）与宽松（2%，判"是不是舍入差"）
        denom = max(abs(recorded_error), 1e-300)
        dev = abs(a.naive_relative - recorded_error) / denom
        reproduced = dev <= 1e-6
        reproduced_loose = dev <= 2e-2
    return {
        "criterion_id": c.criterion_id,
        "quantity": c.quantity,
        "source": c.source,
        "declared_verdict": hist.value if hist else None,
        "recorded_error": recorded_error,
        "error_reproduced": reproduced,
        "error_reproduced_loose": reproduced_loose,
        "degeneracy": f.degeneracy.value,
        "degeneracy_reasons": list(f.reasons),
        "degeneracy_evidence": f.evidence,
        "naive_relative": a.naive_relative,
        "naive_verdict": a.naive_verdict.value,
        "adaptive_verdict": a.verdict.value,
        "adaptive_member": a.family_member,
        "adaptive_value": a.value,
        "adaptive_bound": a.bound,
        "binding": a.binding,
        "flipped": a.flipped,
        "declared_conflict": declared_conflict,
        "recompute": _recompute_payload(c, a),
        "notes": list(a.notes),
    }


def rejudge_corpus(
    records: Iterable[dict[str, Any] | Criterion],
    policy: DegeneracyPolicy | None = None,
) -> dict[str, Any]:
    """批量回放：接受快照记录（dict）或 Criterion。"""
    out: list[dict[str, Any]] = []
    results: list[AdaptiveResult] = []
    for rec in records:
        c = rec if isinstance(rec, Criterion) else Criterion.from_dict(rec)
        row = rejudge_one(c, policy)
        out.append(row)
        results.append(
            AdaptiveResult(
                criterion_id=row["criterion_id"],
                family_member=row["adaptive_member"],
                verdict=Verdict(row["adaptive_verdict"]),
                value=row["adaptive_value"],
                bound=row["adaptive_bound"],
                degeneracy=DegeneracyClass(row["degeneracy"]),
                degenerate_naive=row["degeneracy"] != DegeneracyClass.NONE.value,
                naive_relative=row["naive_relative"],
                naive_verdict=Verdict(row["naive_verdict"]),
                flipped=row["flipped"],
                binding=row["binding"],
                notes=tuple(row["notes"]),
            )
        )
    return {"rows": out, "family": family_table(results)}


def replay_summary(report: dict[str, Any]) -> dict[str, Any]:
    """回放汇总：退化率、翻转数、逐类计数、以及可发表的关键表。"""
    rows = report["rows"]
    n = len(rows)
    counts = {k.value: 0 for k in DegeneracyClass}
    for r in rows:
        counts[r["degeneracy"]] += 1
    degenerate = n - counts[DegeneracyClass.NONE.value]
    flipped = [r for r in rows if r["flipped"]]
    conflicts = [r for r in rows if r["declared_conflict"]]
    unbounded = [r for r in rows if r["naive_relative"] is None and r["degeneracy"] == DegeneracyClass.T1_ZERO_REFERENCE.value]
    reproduced = [r for r in rows if r["error_reproduced"] is True]
    reproduced_loose = [r for r in rows if r["error_reproduced_loose"] is True]
    reproduciable = [r for r in rows if r["error_reproduced"] is not None]
    return {
        "n_records": n,
        "n_degenerate": degenerate,
        "degenerate_rate": (degenerate / n) if n else 0.0,
        "counts": counts,
        "n_flipped": len(flipped),
        "flip_rate": (len(flipped) / n) if n else 0.0,
        "n_declared_conflict": len(conflicts),
        "n_naive_unbounded": len(unbounded),
        "n_error_checkable": len(reproduciable),
        "n_error_reproduced": len(reproduced),
        "error_reproduction_rate": (len(reproduced) / len(reproduciable)) if reproduciable else None,
        "error_reproduction_rate_within_2pct": (len(reproduced_loose) / len(reproduciable)) if reproduciable else None,
        "family": report["family"],
        "degenerate_rows": [
            {
                "criterion_id": r["criterion_id"],
                "degeneracy": r["degeneracy"],
                "quantity": r["quantity"],
                "adaptive_member": r["adaptive_member"],
                "adaptive_value": r["adaptive_value"],
                "adaptive_bound": r["adaptive_bound"],
                "adaptive_verdict": r["adaptive_verdict"],
                "naive_verdict": r["naive_verdict"],
                "declared_verdict": r["declared_verdict"],
                "source": {
                    k: r["source"].get(k) for k in ("kind", "path", "report_id", "mission_id", "line")
                },
                "reasons": r["degeneracy_reasons"],
            }
            for r in rows
            if r["degeneracy"] != DegeneracyClass.NONE.value
        ],
        "flipped_rows": [
            {
                "criterion_id": r["criterion_id"],
                "quantity": r["quantity"],
                "naive_verdict": r["naive_verdict"],
                "adaptive_verdict": r["adaptive_verdict"],
                "adaptive_member": r["adaptive_member"],
                "adaptive_value": r["adaptive_value"],
                "adaptive_bound": r["adaptive_bound"],
            }
            for r in flipped
        ],
    }


def agreement_stats(pred: list[str], truth: list[str]) -> dict[str, Any]:
    """分类一致率 + Cohen's κ（两评分者：分类器 vs 人工标注）。

    κ 的定义按二分/多分类通用式：
    ``κ = (p_o - p_e) / (1 - p_e)``，``p_e`` 按两侧边际分布乘积求和。
    """
    if len(pred) != len(truth):
        raise ValueError("pred and truth must have equal length")
    n = len(pred)
    if n == 0:
        return {"n": 0, "agreement": None, "kappa": None}
    labels = sorted(set(pred) | set(truth))
    confusion = {a: {b: 0 for b in labels} for a in labels}
    for p, t in zip(pred, truth):
        confusion[t][p] += 1
    agree = sum(1 for p, t in zip(pred, truth) if p == t)
    p_o = agree / n
    p_e = 0.0
    for lab in labels:
        row = sum(confusion[lab].values()) / n
        col = sum(confusion[a][lab] for a in labels) / n
        p_e += row * col
    kappa = None if p_e >= 1.0 else (p_o - p_e) / (1.0 - p_e)
    return {
        "n": n,
        "agreement": p_o,
        "kappa": kappa,
        "labels": labels,
        "confusion": confusion,
    }