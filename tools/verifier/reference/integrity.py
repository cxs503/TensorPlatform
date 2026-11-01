"""
Reference-integrity auditing for the verifier question bank.

The platform's headline KPI ("已合并功能复核通过率") only means something if a
question's ``expected_result`` comes from an *independent* source (analytical
solution, published regression, literature table) rather than from the same
code the candidate is built from.  The platform rule is explicit: self-consistent
tests do not count.

This module supplies the machine-checkable judgement that makes that rule
enforceable:

  C1  expression fingerprint   the question's provenance must name the canonical
                               published expression (no private/simplified form)
  C2  physical monotonicity    form factor must increase with fullness
  C3  magnitude window         reference must sit in the published family window
  C4  trend anchor             reference must agree with an independent one-param
                               correlation to within an order-of-magnitude band
  C5  independent re-derivation the bank value must be reproducible from the
                               published formula to 1e-9 relative

Each criterion returns a structured verdict so the audit output is auditable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional

from . import holtrop_mennen_1982 as hm

#: Magnitude window for 1+k across the declared case matrix (Cb 0.6-0.8 hulls,
#: L/B 6.2-6.7).  Published regressions give k = 0.16-0.32 for this family.
ONE_PK_WINDOW = (1.10, 1.50)

#: Relative half-width of the trend-anchor band, applied to the anchor's own
#: ``k`` (not to 1+k).  Calibrated for discrimination, not for accuracy: on the
#: declared case matrix the published values sit at 31-34% of the anchor while
#: the retired private expression sat at 78-94%, so 50% separates them with a
#: wide margin (see tests/test_reference_integrity.py::TestTrendBandSeparation).
TREND_RTOL = 0.50

#: Relative agreement required for an exact re-derivation of a closed form.
REDERIVE_RTOL = 1e-9


@dataclass
class CriterionResult:
    criterion: str
    passed: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# C5 — independent re-derivation of every published closed form in the bank
# --------------------------------------------------------------------------
def redrive_ittc1957(spec: Dict[str, Any]) -> Optional[float]:
    """Cf = 0.075 / (log10(Re) - 2)^2  (7th ITTC, 1957)."""
    Re = spec.get("Re")
    if not isinstance(Re, (int, float)) or Re <= 100:
        return None
    return 0.075 / (math.log10(Re) - 2.0) ** 2


def redrive_blasius(spec: Dict[str, Any]) -> Optional[float]:
    """Cf = 1.328 / sqrt(Re_L)  (Blasius 1908, exact laminar similarity)."""
    Re_L = spec.get("Re_L")
    if not isinstance(Re_L, (int, float)) or Re_L <= 0:
        return None
    return 1.328 / math.sqrt(Re_L)


def redrive_prandtl_schlichting(spec: Dict[str, Any]) -> Optional[float]:
    """Cf = 0.455 / (log10 Re_L)^2.58  (Schlichting, Boundary-Layer Theory 20.18)."""
    Re_L = spec.get("Re_L")
    if not isinstance(Re_L, (int, float)) or Re_L <= 0:
        return None
    return 0.455 / (math.log10(Re_L) ** 2.58)


def redrive_holtrop(spec: Dict[str, Any]) -> Optional[float]:
    """Published Holtrop-Mennen (1982) 1+k, from the reference module."""
    try:
        Cm = spec["Cm"]
        return hm.form_factor_1pk(
            Cb=spec["Cb"], L=spec["L"], B=spec["B"], T=spec["T"],
            Cm=Cm, lcb=spec.get("lcb", 0.0), C_stern=spec.get("C_stern", 0.0),
        )
    except (KeyError, ValueError, TypeError):
        return None


#: formula key -> (reference evaluator, expected metric key)
REDRIVERS: Dict[str, tuple] = {
    "ittc_1957": (redrive_ittc1957, "Cf"),
    "blasius_laminar": (redrive_blasius, "Cf"),
    "prandtl_schlichting": (redrive_prandtl_schlichting, "Cf"),
    "holtrop_mennen_form_factor": (redrive_holtrop, "1+k"),
}


def _formula_key(row: Dict[str, Any]) -> str:
    spec = row.get("specification") or {}
    return spec.get("formula") or spec.get("method") or ""


def check_rederivation(row: Dict[str, Any]) -> CriterionResult:
    """C5: recompute the reference value from the published formula."""
    key = _formula_key(row)
    if key not in REDRIVERS:
        return CriterionResult(
            "C5_rederivation", False,
            f"no published reference evaluator for formula '{key or '<none>'}'",
        )
    evaluator, metric = REDRIVERS[key]
    recomputed = evaluator(row.get("specification") or {})
    if recomputed is None:
        return CriterionResult(
            "C5_rederivation", False,
            f"insufficient specification to re-derive '{metric}' for '{key}'",
        )
    expected = (row.get("expected_result") or {}).get(metric)
    if not isinstance(expected, (int, float)):
        return CriterionResult(
            "C5_rederivation", False, f"bank value for '{metric}' missing or non-numeric",
        )
    if expected == 0:
        rel = abs(recomputed)
    else:
        rel = abs(recomputed - expected) / abs(expected)
    passed = rel <= REDERIVE_RTOL
    return CriterionResult(
        "C5_rederivation", passed,
        f"{metric}: bank={expected!r} recomputed={recomputed!r} rel_dev={rel:.3e} "
        f"(gate {REDERIVE_RTOL:g})",
    )


# --------------------------------------------------------------------------
# C1-C4 — form-factor specific conformance of a case matrix
# --------------------------------------------------------------------------
def check_expression_fingerprint(rows: List[Dict[str, Any]]) -> CriterionResult:
    """C1: every form-factor question must name the canonical expression."""
    bad = []
    for row in rows:
        prov = row.get("provenance") or {}
        expr = (prov.get("expression") or "").replace(" ", "")
        canon = hm.CANONICAL_1PK_EXPRESSION.replace(" ", "")
        if expr != canon:
            bad.append(row.get("title", "<untitled>"))
    if not rows:
        return CriterionResult("C1_expression_fingerprint", True, "no form-factor questions")
    if bad:
        return CriterionResult(
            "C1_expression_fingerprint", False,
            f"{len(bad)}/{len(rows)} form-factor questions lack the canonical published "
            f"expression, e.g. {bad[:3]}",
        )
    return CriterionResult(
        "C1_expression_fingerprint", True,
        f"{len(rows)}/{len(rows)} questions carry the canonical Holtrop-Mennen expression",
    )


def _one_pk_of(row: Dict[str, Any]) -> Optional[float]:
    v = (row.get("expected_result") or {}).get("1+k")
    return v if isinstance(v, (int, float)) else None


def check_monotonicity(rows: List[Dict[str, Any]]) -> CriterionResult:
    """C2: 1+k must increase with Cb (fixed L/B, B/T family)."""
    pairs = []
    for row in rows:
        spec = row.get("specification") or {}
        v = _one_pk_of(row)
        if v is not None and isinstance(spec.get("Cb"), (int, float)):
            pairs.append((spec["Cb"], v, row.get("title", "")))
    if len(pairs) < 2:
        return CriterionResult("C2_monotonicity", True, "fewer than two comparable cases")
    pairs.sort()
    violations = [
        f"{pairs[i][2]} ({pairs[i][0]}) -> {pairs[i+1][2]} ({pairs[i+1][0]})"
        for i in range(len(pairs) - 1)
        if pairs[i + 1][1] <= pairs[i][1]
    ]
    if violations:
        return CriterionResult(
            "C2_monotonicity", False,
            "1+k does not increase with Cb in " + "; ".join(violations),
        )
    return CriterionResult(
        "C2_monotonicity", True,
        "1+k increases with Cb over " + ", ".join(f"Cb={c}:{v:.4f}" for c, v, _ in pairs),
    )


def check_window(rows: List[Dict[str, Any]]) -> CriterionResult:
    """C3: 1+k must sit in the published family window."""
    lo, hi = ONE_PK_WINDOW
    outside = []
    checked = 0
    for row in rows:
        v = _one_pk_of(row)
        if v is None:
            continue
        checked += 1
        if not (lo <= v <= hi):
            outside.append(f"{row.get('title', '')}={v:.4f}")
    if outside:
        return CriterionResult(
            "C3_magnitude_window", False,
            f"{len(outside)}/{checked} values outside [{lo}, {hi}]: {outside[:4]}",
        )
    return CriterionResult(
        "C3_magnitude_window", True,
        f"{checked}/{checked} values inside [{lo}, {hi}]",
    )


def check_trend_anchor(rows: List[Dict[str, Any]]) -> CriterionResult:
    """C4: 1+k must agree with an independent one-parameter trend anchor."""
    worst = 0.0
    worst_title = ""
    checked = 0
    for row in rows:
        spec = row.get("specification") or {}
        v = _one_pk_of(row)
        Cb, L, B = spec.get("Cb"), spec.get("L"), spec.get("B")
        if v is None or not all(isinstance(x, (int, float)) for x in (Cb, L, B)) or B <= 0:
            continue
        checked += 1
        k_anchor = hm.watanabe_trend_anchor(Cb, L / B)
        if abs(k_anchor) < 1e-12:
            continue
        ratio = abs((v - 1.0) - k_anchor) / abs(k_anchor)
        if ratio > worst:
            worst, worst_title = ratio, row.get("title", "")
    if checked == 0:
        return CriterionResult("C4_trend_anchor", True, "no comparable cases")
    passed = worst <= TREND_RTOL
    return CriterionResult(
        "C4_trend_anchor", passed,
        f"max |k - k_anchor|/|k_anchor| = {worst:.3f} ({worst_title}); "
        f"gate {TREND_RTOL}",
    )


def audit_form_factor(rows: List[Dict[str, Any]]) -> List[CriterionResult]:
    """Run C1-C4 over the form-factor subset."""
    return [
        check_expression_fingerprint(rows),
        check_monotonicity(rows),
        check_window(rows),
        check_trend_anchor(rows),
    ]


def audit_bank(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Audit every question for independent re-derivation plus form-factor conformance."""
    c5 = [check_rederivation(row) for row in rows]
    ff_rows = [r for r in rows if _formula_key(r) == "holtrop_mennen_form_factor"]
    results = c5 + audit_form_factor(ff_rows)
    by_criterion: Dict[str, Dict[str, int]] = {}
    for r in results:
        slot = by_criterion.setdefault(r.criterion, {"pass": 0, "fail": 0})
        slot["pass" if r.passed else "fail"] += 1
    return {
        "n_questions": len(rows),
        "n_form_factor_questions": len(ff_rows),
        "criteria": [r.to_dict() for r in results],
        "summary": by_criterion,
        "all_passed": all(r.passed for r in results),
    }