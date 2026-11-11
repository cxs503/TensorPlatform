"""Statistical equivalence testing for cross-architecture reproducibility.

Provides three levels of comparison:
1. Scalar key quantities: relative error threshold (<0.1%)
2. Field quantities: L2 norm relative error (<0.5%)
3. Distribution-level: Welch's t-test (p > 0.05)

These correspond to the task's statistical equivalence criteria:
- Key quantity relative error < 0.1%
- Field L2 norm relative error < 0.5%
- Welch's t-test p > 0.05

All implementations use only Python stdlib + scipy.stats (for Welch's t-test).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple, Optional


# --------------------------------------------------------------------------- #
# Relative error
# --------------------------------------------------------------------------- #
def relative_error(ref: float, cand: float) -> float:
    """Compute relative error |ref - cand| / max(|ref|, epsilon).

    Returns 0.0 if both are exactly zero.
    Returns inf if ref is zero but cand is not.
    """
    denom = max(abs(ref), 1e-300)
    if ref == 0.0 and cand == 0.0:
        return 0.0
    return abs(ref - cand) / denom


def check_scalar_equivalence(
    ref_scalars: Dict[str, float],
    cand_scalars: Dict[str, float],
    rel_tol: float = 0.001,  # 0.1%
) -> Dict[str, Any]:
    """Check scalar key quantity equivalence.

    Returns dict with per-quantity results and overall pass/fail.
    """
    results = {}
    all_pass = True
    max_rel_err = 0.0

    for key in sorted(set(ref_scalars) | set(cand_scalars)):
        if key not in ref_scalars:
            results[key] = {"status": "MISSING_IN_REF", "rel_error": float("inf")}
            all_pass = False
            continue
        if key not in cand_scalars:
            results[key] = {"status": "MISSING_IN_CAND", "rel_error": float("inf")}
            all_pass = False
            continue

        ref_val = ref_scalars[key]
        cand_val = cand_scalars[key]
        rel_err = relative_error(ref_val, cand_val)
        passed = rel_err < rel_tol
        max_rel_err = max(max_rel_err, rel_err)

        results[key] = {
            "ref": ref_val,
            "cand": cand_val,
            "rel_error": rel_err,
            "threshold": rel_tol,
            "pass": passed,
        }
        if not passed:
            all_pass = False

    return {
        "per_quantity": results,
        "all_pass": all_pass,
        "max_relative_error": max_rel_err,
        "threshold": rel_tol,
    }


# --------------------------------------------------------------------------- #
# L2 norm comparison for fields
# --------------------------------------------------------------------------- #
def l2_norm(values: List[float]) -> float:
    """Compute L2 norm of a vector."""
    return math.sqrt(sum(v * v for v in values))


def l2_relative_error(ref: List[float], cand: List[float]) -> float:
    """Compute relative L2 error: ||ref - cand|| / ||ref||.

    Returns 0.0 if both are zero vectors.
    """
    if len(ref) != len(cand):
        return float("inf")
    diff = [r - c for r, c in zip(ref, cand)]
    diff_norm = l2_norm(diff)
    ref_norm = l2_norm(ref)
    if ref_norm == 0.0:
        return 0.0 if diff_norm == 0.0 else float("inf")
    return diff_norm / ref_norm


def check_field_equivalence(
    ref_fields: Dict[str, List[float]],
    cand_fields: Dict[str, List[float]],
    l2_tol: float = 0.005,  # 0.5%
) -> Dict[str, Any]:
    """Check field quantity equivalence via L2 norm relative error.

    Returns dict with per-field results and overall pass/fail.
    """
    results = {}
    all_pass = True
    max_l2_err = 0.0

    for key in sorted(set(ref_fields) | set(cand_fields)):
        if key not in ref_fields:
            results[key] = {"status": "MISSING_IN_REF", "l2_rel_error": float("inf")}
            all_pass = False
            continue
        if key not in cand_fields:
            results[key] = {"status": "MISSING_IN_CAND", "l2_rel_error": float("inf")}
            all_pass = False
            continue

        ref_vec = ref_fields[key]
        cand_vec = cand_fields[key]
        l2_err = l2_relative_error(ref_vec, cand_vec)
        passed = l2_err < l2_tol
        max_l2_err = max(max_l2_err, l2_err)

        results[key] = {
            "ref_l2": l2_norm(ref_vec),
            "cand_l2": l2_norm(cand_vec),
            "l2_rel_error": l2_err,
            "threshold": l2_tol,
            "length": len(ref_vec),
            "pass": passed,
        }
        if not passed:
            all_pass = False

    return {
        "per_field": results,
        "all_pass": all_pass,
        "max_l2_relative_error": max_l2_err,
        "threshold": l2_tol,
    }


# --------------------------------------------------------------------------- #
# Welch's t-test (using scipy if available, else manual implementation)
# --------------------------------------------------------------------------- #
def welch_ttest(
    sample_a: List[float],
    sample_b: List[float],
) -> Dict[str, float]:
    """Perform Welch's t-test for equal means.

    Returns t-statistic, degrees of freedom, and p-value.
    Uses scipy.stats.ttest_ind if available, otherwise manual implementation.
    """
    try:
        from scipy import stats
        t_stat, p_value = stats.ttest_ind(sample_a, sample_b, equal_var=False)
        # Welch-Satterthwaite degrees of freedom
        n1, n2 = len(sample_a), len(sample_b)
        v1 = _variance(sample_a)
        v2 = _variance(sample_b)
        if v1 == 0 and v2 == 0:
            df = float("inf")
        else:
            num = (v1/n1 + v2/n2)**2
            denom = (v1/n1)**2/(n1-1) + (v2/n2)**2/(n2-1)
            df = num / denom if denom > 0 else float("inf")
        return {"t_statistic": t_stat, "p_value": p_value, "df": df}
    except ImportError:
        return _welch_ttest_manual(sample_a, sample_b)


def _mean(values: List[float]) -> float:
    return sum(values) / len(values)


def _variance(values: List[float]) -> float:
    m = _mean(values)
    return sum((v - m)**2 for v in values) / (len(values) - 1)


def _welch_ttest_manual(sample_a: List[float], sample_b: List[float]) -> Dict[str, float]:
    """Manual Welch's t-test implementation (fallback when scipy unavailable)."""
    n1, n2 = len(sample_a), len(sample_b)
    m1, m2 = _mean(sample_a), _mean(sample_b)
    v1, v2 = _variance(sample_a), _variance(sample_b)

    se = math.sqrt(v1/n1 + v2/n2) if (v1/n1 + v2/n2) > 0 else 1e-300
    t_stat = (m1 - m2) / se

    # Welch-Satterthwaite df
    num = (v1/n1 + v2/n2)**2
    denom = (v1/n1)**2/(n1-1) + (v2/n2)**2/(n2-1)
    df = num / denom if denom > 0 else float("inf")

    # Approximate p-value using normal approximation for large df
    # For small df, this is less accurate but sufficient for gate purposes
    p_value = 2.0 * _normal_cdf(-abs(t_stat))

    return {"t_statistic": t_stat, "p_value": p_value, "df": df}


def _normal_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    if x > 8.0:
        return 1.0
    if x < -8.0:
        return 0.0
    # Use error function relation: Phi(x) = 0.5 * (1 + erf(x/sqrt(2)))
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def check_distribution_equivalence(
    ref_fields: Dict[str, List[float]],
    cand_fields: Dict[str, List[float]],
    p_threshold: float = 0.05,
) -> Dict[str, Any]:
    """Check distribution equivalence via Welch's t-test on field values.

    For each field, tests whether the two samples come from distributions
    with equal means. p > threshold means we cannot reject the null hypothesis
    of equal means → statistically equivalent.
    """
    results = {}
    all_pass = True
    min_p_value = 1.0

    for key in sorted(set(ref_fields) & set(cand_fields)):
        ref_vec = ref_fields[key]
        cand_vec = cand_fields[key]

        if len(ref_vec) < 2 or len(cand_vec) < 2:
            results[key] = {"status": "INSUFFICIENT_SAMPLES", "p_value": 0.0}
            all_pass = False
            continue

        test = welch_ttest(ref_vec, cand_vec)
        passed = test["p_value"] > p_threshold
        min_p_value = min(min_p_value, test["p_value"])

        results[key] = {
            "t_statistic": test["t_statistic"],
            "p_value": test["p_value"],
            "df": test["df"],
            "threshold": p_threshold,
            "pass": passed,
        }
        if not passed:
            all_pass = False

    return {
        "per_field": results,
        "all_pass": all_pass,
        "min_p_value": min_p_value,
        "threshold": p_threshold,
    }


# --------------------------------------------------------------------------- #
# Combined statistical equivalence report
# --------------------------------------------------------------------------- #
def full_statistical_equivalence(
    ref_record: Dict[str, Any],
    cand_record: Dict[str, Any],
    scalar_rel_tol: float = 0.001,
    field_l2_tol: float = 0.005,
    ttest_p_threshold: float = 0.05,
) -> Dict[str, Any]:
    """Run all three statistical equivalence checks.

    Returns combined report with overall verdict.
    """
    scalar_check = check_scalar_equivalence(
        ref_record.get("scalars", {}),
        cand_record.get("scalars", {}),
        rel_tol=scalar_rel_tol,
    )
    field_check = check_field_equivalence(
        ref_record.get("fields", {}),
        cand_record.get("fields", {}),
        l2_tol=field_l2_tol,
    )
    dist_check = check_distribution_equivalence(
        ref_record.get("fields", {}),
        cand_record.get("fields", {}),
        p_threshold=ttest_p_threshold,
    )

    all_pass = scalar_check["all_pass"] and field_check["all_pass"] and dist_check["all_pass"]

    return {
        "scalar_equivalence": scalar_check,
        "field_equivalence": field_check,
        "distribution_equivalence": dist_check,
        "all_pass": all_pass,
        "thresholds": {
            "scalar_rel_tol": scalar_rel_tol,
            "field_l2_tol": field_l2_tol,
            "ttest_p_threshold": ttest_p_threshold,
        },
    }
