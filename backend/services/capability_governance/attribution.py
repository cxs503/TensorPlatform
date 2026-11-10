"""Cross-solver deviation attribution chain — four mechanical dimensions.

Why this module exists
----------------------
``services.xsolver.adjudicate`` answers "do two solver arms disagree, and does the
disagreement survive a coarse label" (normalisation / baseline / discretisation
*suspicion*).  It does not answer the question a reviewer actually asks:

    of the X % disagreement, how much is *accounted for* and how much is
    unexplained?

This module turns that into arithmetic.  For each arm the raw relative deviation
against a reference arm is pushed through a **fixed correction sequence**

    format  ->  output definition  ->  input definition  ->  discretisation

and the incremental drop of the deviation after every step is that dimension's
contribution (a telescoping sum, so the shares plus the unexplained residual add
to exactly 1 by construction, not by bookkeeping).  What survives the last
correction is reported as ``residual`` — it is *not* silently absorbed.

Definitions
-----------
* ``format``            unit scale / normalisation base / sign convention —
                        presentation of the same physical number.
* ``output definition`` the quantity is normalised on a different reference
                        (e.g. coefficient referred to a different base area).
* ``input definition``  the two arms were not run at identical inputs; the gap is
                        mapped through the *declared* log-log sensitivity
                        exponents of the reference arm.
* ``discretisation``    the arm's own grid sequence is Richardson-extrapolated
                        with its declared observed order.

Every correction is a *declared, auditable* number taken from the record; nothing
is fitted here.  A missing declaration is an error, never a silent default — an
unattributable deviation must be visible as such.

Domain-neutral by construction: this file carries mechanics only.  Which arm is
which solver, what the quantity is called and where the records come from are
injected by the caller (configuration / artifacts).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "xsv-attribution-chain/1.0"
DIMENSIONS: Tuple[str, ...] = ("format", "output", "input_definition", "discretisation")


class AttributionError(ValueError):
    """A record lacks a declaration needed to attribute the deviation."""


def _rel_diff(a: float, b: float) -> float:
    """Symmetric-free relative deviation of ``a`` from the reference ``b``."""
    if b == 0.0:
        return 0.0 if a == 0.0 else math.inf
    return abs(a - b) / abs(b)


def _require(arm: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in arm:
        raise AttributionError(f"{where}: missing declaration '{key}'")
    return arm[key]


def _physical_value(arm: Mapping[str, Any]) -> float:
    """Reported quantity value in SI-ish presentation coordinates.

    ``reported * unit_scale * normalization_base * sign``.  All three are declared
    by the arm; defaults are *identity*, which is a declaration of "same units",
    and is fine because the format step then contributes exactly zero.
    """
    fmt = arm.get("format") or {}
    scale = float(fmt.get("unit_scale", 1.0)) * float(fmt.get("normalization_base", 1.0))
    sign = float(fmt.get("sign", 1.0))
    return float(arm["value"]) * scale * sign


def _definition_value(arm: Mapping[str, Any]) -> float:
    """Physical value mapped onto the canonical quantity definition."""
    definition = arm.get("output_definition") or {}
    factor = float(definition.get("definition_scale", 1.0))
    return _physical_value(arm) * factor


def _input_factor(arm: Mapping[str, Any], reference: Mapping[str, Any]) -> float:
    """``prod_i (x_arm_i / x_ref_i) ** s_i`` — the declared input-definition map."""
    arm_inputs = dict(arm.get("inputs") or {})
    ref_inputs = dict(reference.get("inputs") or {})
    if arm_inputs == ref_inputs:
        return 1.0
    sensitivities = dict(reference.get("sensitivities") or {})
    factor = 1.0
    for name, ref_value in ref_inputs.items():
        if name not in arm_inputs:
            raise AttributionError(f"input '{name}' present in reference but absent in arm")
        arm_value = arm_inputs[name]
        if name not in sensitivities:
            raise AttributionError(
                f"input '{name}' differs between arms but no sensitivity exponent is declared"
            )
        if float(ref_value) == 0.0 or float(arm_value) == 0.0:
            raise AttributionError(f"input '{name}' cannot be mapped through logs (zero value)")
        factor *= (float(arm_value) / float(ref_value)) ** float(sensitivities[name])
    if factor == 0.0:
        raise AttributionError("input correction factor collapsed to zero")
    return factor


def _extrapolated_value(arm: Mapping[str, Any], reference: Mapping[str, Any]) -> Optional[float]:
    """Richardson-extrapolated value of the arm using its own grid sequence.

    Returns ``None`` when the arm declares a single grid level (nothing to
    extrapolate) — the discretisation contribution is then 0 by construction and
    the un-modelled discretisation error stays in the residual, where it belongs.
    """
    levels = arm.get("grid_levels") or []
    if len(levels) < 2:
        return None
    order = float(_require(arm, "observed_order", "arm"))
    if not order > 0:
        raise AttributionError("observed_order must be positive")
    ordered = sorted(levels, key=lambda lv: float(lv["h"]), reverse=True)
    fine = ordered[-1]
    coarse = ordered[-2]
    h_fine, h_coarse = float(fine["h"]), float(coarse["h"])
    if not h_fine > 0 or not h_coarse > h_fine:
        raise AttributionError("grid_levels must be strictly refined (h_coarse > h_fine > 0)")
    ratio = h_coarse / h_fine
    # every level is mapped through *the same* format/definition/input corrections
    # as the reported value, otherwise the mesh channel would smuggle the input
    # offset back in (measured: it did, 100 % of the raw deviation)
    factor = _input_factor(arm, reference)
    coarse_corr = _level_to_canonical(coarse, arm) / factor
    fine_corr = _level_to_canonical(fine, arm) / factor
    # v_exact = v_fine + (v_fine - v_coarse) / (r^p - 1)   (standard Richardson)
    return fine_corr + (fine_corr - coarse_corr) / (ratio ** order - 1.0)


def _level_to_canonical(level: Mapping[str, Any], arm: Mapping[str, Any]) -> float:
    """Apply the same format+definition corrections to one grid-level value."""
    fmt = arm.get("format") or {}
    scale = float(fmt.get("unit_scale", 1.0)) * float(fmt.get("normalization_base", 1.0))
    sign = float(fmt.get("sign", 1.0))
    definition = arm.get("output_definition") or {}
    factor = float(definition.get("definition_scale", 1.0))
    return float(level["value"]) * scale * sign * factor


def _input_corrected_value(arm: Mapping[str, Any], reference: Mapping[str, Any]) -> float:
    """Map the arm value onto the reference inputs via declared sensitivities.

    First-order log-linear map: ``v_ref_inputs = v / prod_i (x_arm_i / x_ref_i)^s_i``.
    The exponents live on the *reference* arm (it is the one whose response is
    being borrowed); when the inputs are identical the factor is exactly 1.
    """
    return _definition_value(arm) / _input_factor(arm, reference)


def attribute_arm(arm: Mapping[str, Any], reference: Mapping[str, Any],
                  max_residual_share: float = 0.05) -> Dict[str, Any]:
    """Attribute one arm's deviation from ``reference`` to the four dimensions."""
    ref_value = _physical_value(reference)
    if ref_value == 0.0:
        raise AttributionError("reference value is zero: relative deviation undefined")

    raw = float(arm["value"])
    d0 = _rel_diff(raw, float(reference["value"]))

    steps: List[Dict[str, Any]] = []

    def _step(dim: str, before_value: float, after_value: float) -> float:
        dev_before = _rel_diff(before_value, ref_value)
        dev_after = _rel_diff(after_value, ref_value)
        steps.append({
            "dimension": dim,
            "value_before": before_value,
            "value_after": after_value,
            "deviation_before": dev_before,
            "deviation_after": dev_after,
            "delta_deviation": dev_before - dev_after,
        })
        return dev_after

    # 1) format ---------------------------------------------------------------
    v1 = _physical_value(arm)
    d1 = _step("format", raw, v1)

    # 2) output definition ----------------------------------------------------
    v2 = _definition_value(arm)
    d2 = _step("output", v1, v2)

    # 3) input definition -----------------------------------------------------
    v3 = _input_corrected_value(arm, reference)
    d3 = _step("input_definition", v2, v3)

    # 4) discretisation -------------------------------------------------------
    v4 = _extrapolated_value(arm, reference)
    if v4 is None:
        # single level: the correction channel does not exist -> reported as a
        # zero contribution, and any discretisation error remains in the residual
        d4 = d3
        steps.append({
            "dimension": "discretisation",
            "value_before": v3,
            "value_after": v3,
            "deviation_before": d3,
            "deviation_after": d3,
            "delta_deviation": 0.0,
            "note": "single grid level declared: not extrapolable",
        })
    else:
        d4 = _step("discretisation", v3, v4)

    contributions: Dict[str, float] = {}
    for step in steps:
        if d0 > 0:
            contributions[step["dimension"]] = step["delta_deviation"] / d0
        else:
            contributions[step["dimension"]] = 0.0

    residual_share = (d4 / d0) if d0 > 0 else 0.0
    dominant = max(contributions, key=lambda k: abs(contributions[k])) if d0 > 0 else None

    return {
        "arm_id": arm.get("arm_id"),
        "reference_id": reference.get("arm_id"),
        "deviation_raw": d0,
        "deviation_after_corrections": d4,
        "steps": steps,
        "contributions": contributions,
        "residual": d4,
        "residual_share": residual_share,
        "accounted_share": max(0.0, 1.0 - residual_share),
        "dominant_dimension": dominant,
        "attributed": residual_share <= max_residual_share,
        "max_residual_share": max_residual_share,
    }


def attribute_case(case: Mapping[str, Any], max_residual_share: float = 0.05) -> Dict[str, Any]:
    """Attribute every arm of one physical problem against the declared reference."""
    arms: Sequence[Mapping[str, Any]] = case["arms"]
    if len(arms) < 2:
        raise AttributionError("a case needs at least two arms")
    ref_id = case.get("reference_id") or arms[0].get("arm_id")
    reference = next((a for a in arms if a.get("arm_id") == ref_id), None)
    if reference is None:
        raise AttributionError(f"reference arm '{ref_id}' not found")

    chains = [attribute_arm(a, reference, max_residual_share)
              for a in arms if a.get("arm_id") != ref_id]

    completeness = (sum(c["accounted_share"] for c in chains) / len(chains)) if chains else 1.0
    return {
        "schema": SCHEMA,
        "case_id": case.get("case_id"),
        "quantity": case.get("quantity"),
        "reference_id": ref_id,
        "arms": chains,
        "attribution_completeness": completeness,
        "all_attributed": all(c["attributed"] for c in chains),
        "dimensions": list(DIMENSIONS),
        "max_residual_share": max_residual_share,
    }


def attribute_cases(cases: Iterable[Mapping[str, Any]],
                    max_residual_share: float = 0.05) -> Dict[str, Any]:
    """Attribute a batch of cases and summarise the four-dimensional budget."""
    results = [attribute_case(c, max_residual_share) for c in cases]
    per_dimension = {dim: 0.0 for dim in DIMENSIONS}
    n_arms = 0
    for res in results:
        for chain in res["arms"]:
            n_arms += 1
            for dim, share in chain["contributions"].items():
                per_dimension[dim] += share
    if n_arms:
        per_dimension = {k: v / n_arms for k, v in per_dimension.items()}
    completeness = (sum(r["attribution_completeness"] for r in results) / len(results)) if results else 1.0
    return {
        "schema": SCHEMA,
        "n_cases": len(results),
        "n_arms_attributed": n_arms,
        "cases": results,
        "mean_dimension_share": per_dimension,
        "attribution_completeness": completeness,
        "all_attributed": all(r["all_attributed"] for r in results),
        "dimension_coverage": sorted({s["dimension"] for r in results for c in r["arms"] for s in c["steps"]}),
    }