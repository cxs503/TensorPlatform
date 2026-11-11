"""Drift computation: matched criteria pair -> quantified narrowing of the bar.

Semantics (each rule is deliberate and documented, no hidden weighting)
-----------------------------------------------------------------------
* **Threshold relaxation** (same direction): ``r = (t_report - t_proposal)/|t_proposal|``
  for upper bounds and ``(t_proposal - t_report)/|t_proposal|`` for lower
  bounds.  ``r > 0`` means the bar moved *towards* easier acceptance.
  Percent and bare ratios are already canonicalised, so ``3% -> 0.03`` is
  ``r = 0`` (benign re-format), while ``3% -> 5%`` is ``r = 0.667``.
  ``t_proposal == 0`` has no scale: the absolute delta is reported and the
  policy's ``relax_threshold`` is applied on the delta (flagged
  ``zero_scale``).
* **Range widening**: width change ``(w1-w0)/w0`` plus containment; a range that
  no longer sits inside the proposal range is a relaxation even if narrower in
  width (the accepted region moved).
* **Operator downgrade**: rank drops within the same direction (``<`` -> ``<=``,
  ``>`` -> ``>=``, ``==`` -> one-sided).  Strictly this is a *small* numerical
  effect (the boundary point) but the node contract calls it drift, because it
  changes what a report is allowed to claim.
* **Direction / kind swap**: upper -> lower, or a different measurement family
  (ratio vs count vs order vs p-value).  The original bar is replaced.
* **Tolerance removal/widening**: explicit ± band removed or widened > policy.
* **Undeclared drop**: a proposal criterion with no counterpart and no *declared*
  waiver.  Presence-only criteria (no number) are reported as
  ``dropped_unjudgeable`` and are not significant by default - there is nothing
  to measure, and pretending otherwise would be a false positive.

Every row carries an ``impact`` sentence and an ``action`` sentence built from
the measured numbers (never hand-written in the report).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import matching, model


def _pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{100.0 * x:.4g}%"


def fmt_criterion(c: Optional[model.Criterion]) -> str:
    if c is None:
        return "（缺失）"
    if c.kind == model.KIND_PRESENCE or c.value is None:
        return f"{c.subject or c.raw}（无数值判据）"
    if c.op == model.RANGE_OP:
        return f"{c.subject}∈[{c.lo:.4g}, {c.hi:.4g}]"
    if c.unit == model.UNIT_FRACTION:
        body = f"{c.op}{_pct(c.value)}"
    elif c.unit == model.UNIT_COUNT:
        body = f"{c.op}{c.value:g} 例"
    elif c.unit == model.UNIT_ORDER:
        body = f"{c.op}{c.value:g} 阶"
    elif c.unit == model.UNIT_PVALUE:
        body = f"{c.op}{c.value:g}"
    else:
        body = f"{c.op}{c.value:g}"
    if c.tolerance is not None:
        tol_txt = _pct(c.tolerance) if c.unit == model.UNIT_FRACTION else f"{c.tolerance:g}"
        body += f"（±{tol_txt}）"
    return f"{c.subject or c.raw} {body}"


def relaxation_ratio(a: model.Criterion, b: model.Criterion) -> Optional[float]:
    """>0 means loosened.  ``None`` when the two are not comparable on a scale."""
    # range vs range
    if a.op == model.RANGE_OP and b.op == model.RANGE_OP:
        w0 = (a.hi - a.lo) if (a.hi is not None and a.lo is not None) else 0.0
        w1 = (b.hi - b.lo) if (b.hi is not None and b.lo is not None) else 0.0
        if w0 <= 0:
            return None
        width_ratio = (w1 - w0) / w0
        inside = (b.lo >= a.lo - 1e-15) and (b.hi <= a.hi + 1e-15)
        if not inside:
            return max(width_ratio, 1.0)
        return width_ratio
    if a.value is None or b.value is None:
        return None
    if model.operator_direction(a.op) != model.operator_direction(b.op):
        return None                     # direction change handled separately
    if model.operator_direction(a.op) == "upper":
        delta = b.value - a.value
    elif model.operator_direction(a.op) == "lower":
        delta = a.value - b.value
    else:                               # equality: any value change is drift
        return None if abs(a.value - b.value) < 1e-15 else 1.0
    if abs(a.value) < 1e-15:
        # zero scale: use the canonical comparison tolerance as the unit
        unit = 1.0 if a.unit == model.UNIT_COUNT else (1e-3 if a.unit == model.UNIT_FRACTION else 1.0)
        return delta / unit
    return delta / abs(a.value)


def _downgrade(a: model.Criterion, b: model.Criterion) -> bool:
    if model.operator_direction(a.op) != model.operator_direction(b.op):
        return False
    ra, rb = model.OP_RANK.get(a.op, 0), model.OP_RANK.get(b.op, 0)
    if a.op == model.RANGE_OP or b.op == model.RANGE_OP:
        return ra > rb
    if a.op == "==" and b.op != "==":
        return True
    return rb < ra


def _tolerance_widening(a: model.Criterion, b: model.Criterion) -> Optional[float]:
    if a.tolerance is None:
        return None
    if b.tolerance is None:
        return None                     # removal is handled as its own reason
    if abs(a.tolerance) < 1e-15:
        return None
    return (b.tolerance - a.tolerance) / abs(a.tolerance)


def compare_pair(a: model.Criterion, b: model.Criterion, score: float,
                 policy: Dict[str, Any]) -> model.CriterionDrift:
    row = model.CriterionDrift(proposal=a, report=b, match_score=score)
    relax_th = float(policy.get("relax_threshold", 0.10))
    tol_th = float(policy.get("tolerance_widen_threshold", 0.10))

    if matching.kind_family(a) != matching.kind_family(b):
        row.kind_change = True
        row.reasons.append("kind_change")
    if model.operator_direction(a.op) != model.operator_direction(b.op):
        row.direction_change = True
        row.reasons.append("direction_change")
    if a.compound and not b.compound:
        row.reasons.append("compound_dropped")
    if _downgrade(a, b):
        row.operator_downgrade = True
        row.reasons.append("operator_downgrade")

    rr = relaxation_ratio(a, b)
    row.relaxation_ratio = rr
    if rr is not None and rr > relax_th:
        row.reasons.append("threshold_relaxed")
    elif rr is not None and a.value is not None and abs(a.value) < 1e-15 and rr > 0:
        row.reasons.append("threshold_relaxed_abs")

    tw = _tolerance_widening(a, b)
    row.tolerance_widening = tw
    if tw is not None and tw > tol_th:
        row.reasons.append("tolerance_widened")
    if a.tolerance is not None and b.tolerance is None:
        row.reasons.append("tolerance_removed")

    significant_reasons = {
        "threshold_relaxed", "threshold_relaxed_abs", "operator_downgrade",
        "direction_change", "kind_change", "tolerance_widened", "tolerance_removed",
        "compound_dropped",
    }
    if policy.get("operator_downgrade_significant", True) is False:
        significant_reasons.discard("operator_downgrade")
    if policy.get("direction_change_significant", True) is False:
        significant_reasons.discard("direction_change")
    if policy.get("kind_change_significant", True) is False:
        significant_reasons.discard("kind_change")

    row.status = "significant" if any(r in significant_reasons for r in row.reasons) else "ok"

    # ── impact / action, built from measured numbers ───────────────────
    impacts: List[str] = []
    actions: List[str] = []
    if row.relaxation_ratio is not None and "threshold_relaxed" in row.reasons:
        impacts.append(
            f"阈值由 {fmt_criterion(a)} 放宽到 {fmt_criterion(b)}（放宽 {_pct(row.relaxation_ratio)}），"
            f"超出允许放宽 {_pct(relax_th)}；按新阈值通过的报告不能证明提案要求的精度"
        )
        actions.append(f"将「{b.subject}」阈值回退到提案值（{fmt_criterion(a)}）后重跑验收")
    if row.operator_downgrade:
        impacts.append(
            f"比较算子由 {a.op} 弱化为 {b.op}（同阈值 {fmt_criterion(b)}），恰好等于阈值的样本由不合格变为合格"
        )
        actions.append(f"把「{b.subject}」算子恢复为严格形式 {a.op}")
    if "compound_dropped" in row.reasons:
        impacts.append("复合判据的两个方向约束只剩一侧，另一侧约束消失（等价于放宽）")
        actions.append(f"恢复「{b.subject}」的复合判据（两侧约束都要写）")
    if row.direction_change:
        impacts.append(f"判据方向由上界变为{model.operator_direction(b.op)}，原约束不再可裁")
        actions.append(f"恢复「{b.subject}」的{model.operator_direction(a.op)}约束")
    if row.kind_change:
        impacts.append(
            f"判据族由 {matching.kind_family(a)} 变为 {matching.kind_family(b)}（{a.kind} -> {b.kind}），测量对象被替换"
        )
        actions.append(f"恢复「{b.subject}」的原判据族（{a.kind}）或提交具名豁免")
    if row.tolerance_widening is not None and "tolerance_widened" in row.reasons:
        impacts.append(f"容差 ±{_pct(a.tolerance)} 放宽到 ±{_pct(b.tolerance)}（+{_pct(row.tolerance_widening)}）")
        actions.append(f"把「{b.subject}」容差回退到 ±{_pct(a.tolerance)}")
    if "tolerance_removed" in row.reasons:
        impacts.append(f"显式容差 ±{_pct(a.tolerance)} 被删除，阈值从区间判定退化为点判定")
        actions.append(f"补回「{b.subject}」的 ±{_pct(a.tolerance)} 容差")

    row.impact = "；".join(impacts)
    row.action = actions[0] if actions else ""
    return row


def audit(proposal_doc: Any, report_doc: Any, proposal_ref: str = "proposal",
          report_ref: str = "report", policy: Optional[Dict[str, Any]] = None,
          lexicon: Optional[Dict[str, Any]] = None,
          recognize_waivers: bool = True) -> model.DriftAudit:
    pol = policy or matching.load_policy()
    lex = lexicon or matching.load_lexicon()
    prop, _ = _parse_doc(proposal_doc, proposal_ref, lex)
    rep, waivers = _parse_doc(report_doc, report_ref, lex)

    pairs, unmatched_p, unmatched_r = matching.match(prop, rep, pol)
    rows: List[model.CriterionDrift] = []
    for i, j, score in pairs:
        row = compare_pair(prop[i], rep[j], score, pol)
        if row.status == "significant" and recognize_waivers:
            w = matching.match_waiver(prop[i], waivers)
            if w is not None and w.declared:
                row.status = "declared_waiver"
                row.waiver = w
                row.impact = (row.impact + "；" if row.impact else "") + \
                    f"报告以具名豁免声明了该放宽（{w.reason} / {w.approved_by}），不计为静默漂移"
                row.action = "无需回退；豁免已入审计链，复核时可追溯"
        rows.append(row)

    for i in unmatched_p:
        c = prop[i]
        row = model.CriterionDrift(proposal=c, report=None, match_score=0.0)
        if c.kind == model.KIND_PRESENCE or c.value is None:
            row.status = "ok"
            row.reasons.append("dropped_unjudgeable")
            row.impact = f"提案判据「{c.raw}」无数值要素，无法机裁，未计入漂移（人工复核项）"
            row.action = "人工确认该定性判据是否仍被验收报告覆盖"
        else:
            w = matching.match_waiver(c, waivers) if recognize_waivers else None
            row.reasons.append("dropped_undeclared")
            row.status = "significant"
            row.impact = (f"提案判据「{fmt_criterion(c)}」在验收报告中无对应项且无豁免声明，"
                          f"等于取消该检查；原精度保证失去落点")
            row.action = f"补交「{c.subject}」的验收证据，或提交具名豁免并更新提案"
            if w is not None and w.declared:
                row.status = "declared_waiver"
                row.waiver = w
                row.impact += f"（已由豁免 {w.approved_by} 声明）"
        rows.append(row)

    for j in unmatched_r:
        c = rep[j]
        rows.append(model.CriterionDrift(
            proposal=None, report=c, status="added",
            reasons=["added_criterion"],
            impact=f"验收报告新增判据「{fmt_criterion(c)}」（收紧方向），不构成漂移",
            action="无需动作；新增判据按提案修订流程登记",
        ))

    out = model.DriftAudit(proposal_ref=proposal_ref, report_ref=report_ref, rows=rows, policy=pol)
    return out


def _parse_doc(doc: Any, ref: str, lex: Dict[str, Any]):
    from . import parser as _parser
    return _parser.parse_document(doc, ref)