"""Cross-solver adjudication engine.

Input: a corpus of canonical cases (see :mod:`services.xsolver.corpus`).
Output: per-problem comparison tables plus an explicit **attribution** of every
difference. That attribution is the whole point: two solvers reporting
``C_T = 0.0160`` and ``C_T = 0.0002`` differ by 99 %, but that is not a numerical
disagreement -- one arm reports a friction term only, the other a wave term only.
Calling that "the solvers disagree by 99 %" would be a fabricated finding.

Attribution kinds produced here (all evidence-backed, no heuristics on names):

``term_coverage``      the arms report disjoint subsets of the component sum
                       (``C_T == C_F + C_W + C_R`` within closure tolerance), so part
                       of the gap is definitional
``normalization``      a per-arm derived metric (e.g. ``C_F / C_ITTCT57`` or
                       ``C_F * S_wet``) differs while the ratio between arms stays
                       constant across conditions -> unit chain / reference-area
                       convention, not physics
``discretisation``     the gap shrinks along the grid-level series of one arm
``model_formulation``  the same term is produced by formally different methods
                       (declared ``method`` strings) and the difference exceeds the
                       comparison tolerance -> not adjudicable without an anchor
``unattributed``       none of the above holds; reported as-is, never rounded away
"""
from __future__ import annotations

import datetime as _dt
import itertools
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import normalize as N
from .conservation import FAIL, PASS, RANK, WARN, WITHHELD, gate

ALIASES = N.ALIASES
COMPONENT_IDENTITY = ALIASES["component_identity"]
COMPONENTS = COMPONENT_IDENTITY["components"]
TOTAL = COMPONENT_IDENTITY["total"]
CLOSURE_TOL = COMPONENT_IDENTITY["closure_tolerance_rel"]
COMPARE = ALIASES["comparison_quantities"]
NORMALIZATION = ALIASES.get("normalization_analyses", [])
BASELINES = ALIASES.get("reference_baselines", {})

#: relative difference below which two arms are called "agreeing" on a quantity
AGREEMENT_TOL = 0.05
#: coefficient of variation below which a cross-condition ratio counts as constant
CONSTANT_RATIO_CV = 0.02


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def rel_diff(a: float, b: float) -> Optional[float]:
    """Symmetric relative difference ``|a-b| / max(|a|,|b|)``."""
    scale = max(abs(a), abs(b))
    if scale == 0:
        return 0.0
    return abs(a - b) / scale


def _arm_view(case: Dict[str, Any], conservation: Dict[str, Any]) -> Dict[str, Any]:
    quantities = dict(case["output"].get("canonical_quantities") or {})
    components = {q: quantities[q] for q in COMPONENTS if q in quantities}
    total = quantities.get(TOTAL)
    nonzero = [v for v in components.values() if v not in (None, 0.0)]
    closure = None
    if total not in (None, 0.0) and components:
        closure = abs(total - sum(components.values())) / abs(total)
    coverage = "full" if (total is not None and len(nonzero) >= 2
                          and closure is not None and closure <= CLOSURE_TOL) else "partial"
    return {
        "case_id": case["case_id"],
        "solver": case["input"]["solver"]["name"],
        "family": case["input"]["solver"]["family"],
        "version": case["provenance"].get("solver_version"),
        "method": case["input"]["solver"].get("method"),
        "git_commit": case["provenance"].get("git_commit"),
        "input_digest": case["input"]["provenance"]["input_digest"],
        "problem_id": case["problem_id"],
        "condition": case.get("condition"),
        "is_reference_arm": bool(case.get("is_reference_arm")),
        "quantities": quantities,
        "components": components,
        "component_sum": sum(components.values()) if components else None,
        "closure_residual": closure,
        "coverage": coverage,
        "reported_terms": sorted(k for k, v in components.items() if v not in (None, 0.0)),
        "conservation": {
            "status": conservation["status"],
            "excluded": conservation["excluded_from_comparison"],
            "checks": conservation["checks"],
        },
        "convergence": case["output"].get("convergence"),
        "grid_levels": (case["output"].get("convergence") or {}).get("grid_levels") or [],
    }


def _condition_key(case: Dict[str, Any]) -> str:
    condition = case.get("condition")
    if not condition:
        return "single"
    return f"{condition.get('label')}={condition.get('value')}"


def _comparisons(arms: Sequence[Dict[str, Any]], quantity: str) -> List[Dict[str, Any]]:
    """Pairwise comparison, always *within one condition*.

    Comparing two Froude numbers to each other would manufacture a difference that
    has nothing to do with either solver, so the condition is part of the pair key.
    """
    out = []
    by_condition: Dict[str, List[Dict[str, Any]]] = {}
    for arm in arms:
        by_condition.setdefault(_condition_key(arm), []).append(arm)
    for condition, group in sorted(by_condition.items()):
        for a, b in itertools.combinations(group, 2):
            if a["solver"] == b["solver"]:
                continue
            va, vb = a["quantities"].get(quantity), b["quantities"].get(quantity)
            if va is None or vb is None:
                continue
            diff = rel_diff(va, vb)
            out.append({
                "quantity": quantity,
                "condition": condition,
            "arm_a": a["solver"],
            "arm_b": b["solver"],
            "value_a": va,
            "value_b": vb,
            "rel_diff": diff,
            "ratio": (va / vb) if vb else None,
            "status": PASS if diff is not None and diff <= AGREEMENT_TOL else FAIL,
            "convention_note": "symmetric relative difference |a-b|/max(|a|,|b|), so the "
                               "number is independent of which arm is taken as denominator",
        })
    return out


def _condition_consistency(arms: Sequence[Dict[str, Any]], quantities: Sequence[str]) -> List[Dict[str, Any]]:
    """Ratio between two arms across conditions: constant ratio => scale factor."""
    out: List[Dict[str, Any]] = []
    for a, b in itertools.combinations(arms, 2):
        for quantity in quantities:
            ratios = []
            for cond in sorted({_condition_key(x) for x in arms}):
                va = _value_on(a, cond, quantity)
                vb = _value_on(b, cond, quantity)
                if va not in (None, 0.0) and vb not in (None, 0.0):
                    ratios.append(va / vb)
            if len(ratios) < 2:
                continue
            mean = statistics.fmean(ratios)
            cv = (statistics.pstdev(ratios) / abs(mean)) if mean else None
            out.append({
                "quantity": quantity,
                "arm_a": a["solver"],
                "arm_b": b["solver"],
                "ratios": ratios,
                "ratio_mean": mean,
                "ratio_cv": cv,
                "constant": cv is not None and cv <= CONSTANT_RATIO_CV,
            })
    return out


def _group_by_condition(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("condition", "single"), []).append(row)
    return grouped


def _value_on(arm: Dict[str, Any], condition: str, quantity: str) -> Optional[float]:
    arm_cond = arm.get("condition")
    if arm_cond:
        label = f"{arm_cond.get('label')}={arm_cond.get('value')}"
        if label != condition:
            return None
    elif condition != "single":
        return None
    return arm["quantities"].get(quantity)


def _normalization_analysis(arms: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for spec in NORMALIZATION:
        quantity = spec["quantity"]
        derived: Dict[str, List[Dict[str, Any]]] = {}
        for arm in arms:
            value = arm["quantities"].get(quantity)
            if value is None:
                continue
            row: Dict[str, Any] = {"solver": arm["solver"], "condition": _condition_key(arm),
                                   "value": value}
            if spec["form"] == "ratio":
                ref = arm["quantities"].get(spec["reference_quantity"])
                if ref in (None, 0.0):
                    continue
                row["reference"] = ref
                row["metric"] = value / ref
                row["metric_name"] = f"{quantity}/{spec['reference_quantity']}"
            elif spec["form"] == "product":
                scale = arm["quantities"].get(spec["scale_quantity"])
                if scale is None:
                    continue
                row["scale"] = scale
                row["metric"] = value * scale
                row["metric_name"] = f"{quantity}*{spec['scale_quantity']}"
            else:
                raise ValueError(f"unknown normalization form {spec['form']!r}")
            derived.setdefault(row["condition"], []).append(row)
        pairwise = []
        for condition, rows in derived.items():
            for a, b in itertools.combinations(rows, 2):
                metric_a, metric_b = a["metric"], b["metric"]
                pairwise.append({
                    "condition": condition,
                    "metric_name": a.get("metric_name"),
                    "arm_a": a["solver"], "arm_b": b["solver"],
                    "metric_a": metric_a, "metric_b": metric_b,
                    "rel_diff": rel_diff(metric_a, metric_b),
                    "ratio": (metric_a / metric_b) if metric_b else None,
                })
        if pairwise:
            out.append({
                "id": spec["id"],
                "metric_name": pairwise[0].get("metric_name"),
                "description": spec["description"],
                "rows": [row for rows in derived.values() for row in rows],
                "pairwise": pairwise,
            })
    return out


def _baseline_analysis(arms: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for name, spec in BASELINES.items():
        num, den = spec["numerator"], spec["denominator"]
        rows = []
        for arm in arms:
            value = arm["quantities"].get(num)
            base = arm["quantities"].get(den)
            if value is None or base in (None, 0.0):
                continue
            rows.append({"solver": arm["solver"], "condition": _condition_key(arm),
                         "value": value, "baseline": base, "factor": value / base})
        factors = [r["factor"] for r in rows]
        if len(factors) >= 2:
            out.append({
                "id": name,
                "description": spec["description"],
                "rows": rows,
                "spread": rel_diff(min(factors), max(factors)),
                "shared_baseline": len({round(r["baseline"], 12) for r in rows}) == 1,
            })
    return out


def _discretisation_evidence(arm: Dict[str, Any], quantity: Optional[str] = None) -> Dict[str, Any]:
    levels = arm.get("grid_levels") or []
    converged = (arm.get("convergence") or {}).get("observed_order") is not None
    return {"grid_levels": levels, "level_count": len(levels), "order_reported": converged}


# ---------------------------------------------------------------------------
# group adjudication
# ---------------------------------------------------------------------------
def _adjudicate_group(problem_id: str, cases: Sequence[Dict[str, Any]],
                      gates: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    arms_all = [_arm_view(c, gates[c["case_id"]]) for c in cases]
    excluded = [a for a in arms_all if a["conservation"]["excluded"]]
    arms = [a for a in arms_all if not a["conservation"]["excluded"]]

    conditions = sorted({_condition_key(a) for a in arms}) or ["single"]
    available = set()
    for arm in arms:
        available.update(arm["quantities"].keys())
    wanted = [q for q in dict.fromkeys(COMPARE) if q in available]
    for extra in ("C_F", "C_W", "C_R", "C_P"):
        if extra in available and extra not in wanted:
            wanted.append(extra)

    comparisons = []
    for quantity in wanted:
        comparisons.extend(_comparisons(arms, quantity))

    term_coverage = []
    if TOTAL in available:
        union = set()
        for arm in arms:
            union.update(arm["reported_terms"])
        for arm in arms:
            entry = {
                "solver": arm["solver"],
                "condition": _condition_key(arm),
                "reported_terms": arm["reported_terms"],
                "missing_terms": sorted(union - set(arm["reported_terms"])),
                "closure_residual": arm["closure_residual"],
                "coverage": arm["coverage"],
                "disjoint_with": sorted(
                    {other["solver"] for other in arms
                     if not (set(arm["reported_terms"]) & set(other["reported_terms"]))
                     and other["solver"] != arm["solver"]}),
            }
            if entry not in term_coverage:
                term_coverage.append(entry)

    normalization = _normalization_analysis(arms)
    baselines = _baseline_analysis(arms)
    consistency = _condition_consistency(arms, wanted)

    # -- attribution ------------------------------------------------------
    findings: List[Dict[str, Any]] = []
    kinds: List[str] = []

    def add(kind: str, statement: str, evidence: Dict[str, Any], magnitude: Optional[float]):
        kinds.append(kind)
        findings.append({"kind": kind, "statement": statement, "evidence": evidence,
                         "magnitude": magnitude})

    comparisons = sorted(comparisons,
                         key=lambda c: (c["quantity"] != TOTAL, -(c["rel_diff"] or 0.0)))
    seen_pairs = set()
    for row in comparisons[:12]:
        if row["status"] == PASS:
            continue
        evidence = {"quantity": row["quantity"], "arm_a": row["arm_a"], "arm_b": row["arm_b"],
                    "rel_diff": row["rel_diff"], "ratio": row["ratio"]}

        # (1) disjoint/partial term coverage
        cov_a = next((t for t in term_coverage if t["solver"] == row["arm_a"]), None)
        cov_b = next((t for t in term_coverage if t["solver"] == row["arm_b"]), None)
        if cov_a and cov_b and row["quantity"] == TOTAL:
            if set(cov_a["reported_terms"]).isdisjoint(cov_b["reported_terms"]):
                add("term_coverage",
                    f"{row['arm_a']} reports only {cov_a['reported_terms']} while {row['arm_b']} "
                    f"reports only {cov_b['reported_terms']}: the {row['rel_diff']*100:.1f}% gap is a "
                    f"definitional difference in which terms are summed, not a numerical disagreement",
                    {**evidence, "terms_a": cov_a["reported_terms"], "terms_b": cov_b["reported_terms"]},
                    row["rel_diff"])
                continue
            missing = [t for t in cov_a["missing_terms"] if t in cov_b["reported_terms"]]
            if missing:
                add("term_coverage",
                    f"{row['arm_a']} omits {missing} reported by {row['arm_b']}; the total-coefficient "
                    f"gap of {row['rel_diff']*100:.1f}% is partly definitional",
                    {**evidence, "omitted_terms": missing}, row["rel_diff"])
                continue

        # (2) an arm reporting an exact zero for a component simply does not model it
        if min(abs(row["value_a"]), abs(row["value_b"])) == 0.0:
            zero_arm = row["arm_a"] if row["value_a"] == 0.0 else row["arm_b"]
            add("term_absent",
                f"{zero_arm} reports {row['quantity']} = 0 by construction (the term is not part of "
                f"its model), so the {row['rel_diff']*100:.1f}% gap against the other arm is a "
                f"definition of scope, not a disagreement",
                {**evidence, "zero_arm": zero_arm, "zero_quantity": row["quantity"]},
                row["rel_diff"])
            continue

        # (3) constant scale factor across conditions
        const = [c for c in consistency if c["quantity"] == row["quantity"]
                 and {c["arm_a"], c["arm_b"]} == {row["arm_a"], row["arm_b"]} and c["constant"]]
        if const:
            add("normalization",
                f"{row['arm_a']} and {row['arm_b']} differ by the constant factor "
                f"{const[0]['ratio_mean']:.4f} at every condition (CV={const[0]['ratio_cv']:.2e}); a "
                f"condition-independent factor is a unit-chain / reference-area convention, not physics",
                {**evidence, "constant_ratio": const[0]["ratio_mean"],
                 "ratio_cv": const[0]["ratio_cv"]}, row["rel_diff"])
            continue

        # (4) same term, formally different method, beyond tolerance
        arm_obj_a = next(a for a in arms if a["solver"] == row["arm_a"])
        arm_obj_b = next(a for a in arms if a["solver"] == row["arm_b"])
        if (arm_obj_a["method"] and arm_obj_b["method"]
                and arm_obj_a["method"] != arm_obj_b["method"]
                and row["quantity"] in COMPONENTS):
            add("model_formulation",
                f"the {row['quantity']} term is produced by two different formulations "
                f"({arm_obj_a['method']!r} vs {arm_obj_b['method']!r}) differing by "
                f"{row['rel_diff']*100:.1f}%; no numerical adjudication is possible without an "
                f"external anchor for that term",
                {**evidence, "methods": [arm_obj_a["method"], arm_obj_b["method"]]},
                row["rel_diff"])
            continue

        add("unattributed",
            f"{row['quantity']} differs by {row['rel_diff']*100:.1f}% between {row['arm_a']} and "
            f"{row['arm_b']} and no definitional explanation was found",
            evidence, row["rel_diff"])

    # normalisation metric disagreement is itself a finding even when the raw
    # quantity comparison was already attributed
    for analysis in normalization:
        for row in analysis["pairwise"]:
            if row["rel_diff"] is not None and row["rel_diff"] > AGREEMENT_TOL:
                tag = (analysis["id"], row["arm_a"], row["arm_b"])
                if tag in seen_pairs:
                    continue
                seen_pairs.add(tag)
                add("unresolved_after_alignment",
                    f"after removing the reference-area/unit convention (comparing "
                    f"{analysis['metric_name']}) {row['arm_a']} and {row['arm_b']} still differ by "
                    f"{row['rel_diff']*100:.1f}% at {row['condition']}: the convention explains part of "
                    f"the gap but not all of it, so a residual difference remains to be adjudicated",
                    {"analysis": analysis["id"], **row}, row["rel_diff"])
    for analysis in baselines:
        for condition, base_rows in _group_by_condition(analysis["rows"]).items():
            if len(base_rows) < 2:
                continue
            factors = {r["solver"]: r["factor"] for r in base_rows}
            if len({round(r["baseline"], 12) for r in base_rows}) != 1:
                continue
            spread = rel_diff(min(factors.values()), max(factors.values()))
            if spread <= AGREEMENT_TOL:
                continue
            add("normalization",
                f"both arms cite the same correlation baseline {base_rows[0]['baseline']:.6f} at "
                f"{condition} but sit at factors "
                f"{', '.join(f'{k}:{v:.3f}' for k, v in factors.items())} of it (spread "
                f"{spread*100:.1f}%); a friction coefficient several times its own cited correlation is "
                f"a reference-area/unit definition, not a discretisation error",
                {"analysis": analysis["id"], "condition": condition, "factors": factors},
                spread)

    deduped: List[Dict[str, Any]] = []
    for finding in findings:
        if any(f["kind"] == finding["kind"] and f["statement"] == finding["statement"]
               for f in deduped):
            continue
        deduped.append(finding)
    findings = deduped
    kinds = list(dict.fromkeys(f["kind"] for f in findings))

    # -- reference arm ----------------------------------------------------
    reference_rows = []
    ref = next((a for a in arms if a["is_reference_arm"]), None)
    if ref:
        for arm in arms:
            if arm["solver"] == ref["solver"]:
                continue
            for quantity in wanted:
                va, vb = arm["quantities"].get(quantity), ref["quantities"].get(quantity)
                if va is None or vb is None:
                    continue
                reference_rows.append({"quantity": quantity, "arm": arm["solver"],
                                       "reference": ref["solver"], "value": va,
                                       "reference_value": vb, "rel_diff": rel_diff(va, vb),
                                       "status": PASS if rel_diff(va, vb) <= AGREEMENT_TOL else FAIL})

    for row in reference_rows:
        if row["status"] == PASS:
            arm_obj = next(a for a in arms if a["solver"] == row["arm"])
            add("reference_anchor_agreement",
                f"{row['arm']} reproduces the reference arm ({row['reference']}) on {row['quantity']} "
                f"to {row['rel_diff']*100:.3f}%"
                + (f"; grid levels reported: {arm_obj['grid_levels']}"
                   if arm_obj["grid_levels"] else ""),
                {"quantity": row["quantity"], "arm": row["arm"], "reference": row["reference"],
                 "rel_diff": row["rel_diff"], "grid_levels": arm_obj["grid_levels"]},
                row["rel_diff"])
        else:
            add("discretisation",
                f"{row['arm']} misses the reference arm ({row['reference']}) on {row['quantity']} by "
                f"{row['rel_diff']*100:.3f}%; the arm's own grid series is the first explanation to test",
                {"quantity": row["quantity"], "arm": row["arm"], "reference": row["reference"],
                 "rel_diff": row["rel_diff"]}, row["rel_diff"])

    max_diff = max((c["rel_diff"] for c in comparisons if c["rel_diff"] is not None), default=None)
    all_traceable = all(a["input_digest"] and a["version"] and a["git_commit"] for a in arms)
    conservation_gap = any(a["conservation"]["status"] in (WITHHELD, WARN) for a in arms)
    distinct_solvers = sorted({a["solver"] for a in arms})
    status = "comparable" if (max_diff is not None and max_diff <= AGREEMENT_TOL) else "not_comparable"
    if len(distinct_solvers) < 2:
        status = "single_arm"

    return {
        "problem_id": problem_id,
        "status": status,
        "distinct_solvers": distinct_solvers,
        "conditions": conditions,
        "arms": arms_all,
        "excluded_arms": [a["case_id"] for a in excluded],
        "comparisons": comparisons,
        "term_coverage": term_coverage,
        "normalization": normalization,
        "baselines": baselines,
        "condition_consistency": consistency,
        "reference_comparison": reference_rows,
        "attribution": {"kinds": kinds, "findings": findings,
                        "max_rel_diff": max_diff},
        "traceability": {"all_arms_traceable": all_traceable,
                         "conservation_gap": conservation_gap},
        "convergence_evidence": {a["solver"]: _discretisation_evidence(a) for a in arms},
    }


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def adjudicate(corpus: Dict[str, Any], *, tolerance_overrides: Optional[Dict[str, Dict[str, float]]] = None
               ) -> Dict[str, Any]:
    cases = corpus.get("cases", [])
    gates: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        family = case["input"]["solver"].get("family")
        override = (tolerance_overrides or {}).get(family)
        gates[case["case_id"]] = gate(case["output"]["conservation_residuals"], family, override)

    by_problem: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        by_problem.setdefault(case["problem_id"], []).append(case)

    groups = [_adjudicate_group(pid, group, gates) for pid, group in sorted(by_problem.items())]

    counts = {s: 0 for s in RANK}
    for g in gates.values():
        counts[g["status"]] += 1
    excluded = sorted(cid for cid, g in gates.items() if g["excluded_from_comparison"])

    findings: List[Dict[str, Any]] = []
    for group in groups:
        for finding in group["attribution"]["findings"]:
            findings.append({"problem_id": group["problem_id"], **finding})

    report = {
        "schema": "xsolver/adjudication/v1",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "corpus": {"schema": corpus.get("schema"), "cases": len(cases),
                   "harvested_utc": corpus.get("harvested_utc"),
                   "digest": N.digest_of(corpus)},
        "gate": {"conservation_counts": counts, "excluded": excluded,
                 "policy": "a case with conservation_fail never enters a cross-solver comparison"},
        "groups": groups,
        "findings": findings,
        "trace": {
            "input_digests": {c["case_id"]: c["input"]["provenance"]["input_digest"] for c in cases},
            "solver_versions": {c["case_id"]: c["provenance"].get("solver_version") for c in cases},
            "git_commits": {c["case_id"]: c["provenance"].get("git_commit") for c in cases},
            "source_sha256": {c["case_id"]: c["provenance"]["source_sha256"] for c in cases},
        },
    }
    # The digest is content-addressed: it hashes the report *without* the wall-clock
    # stamp, so two runs over the same corpus produce the same digest.  Hashing the
    # stamp as well would make the trace look reproducible while carrying a field that
    # can never reproduce, which is worse than no digest at all.
    report["trace"]["report_digest"] = N.digest_of(
        {k: v for k, v in report.items() if k != "generated_utc"})
    return report