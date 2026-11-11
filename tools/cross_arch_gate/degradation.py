"""Fail-closed degradation logic for cross-architecture reproducibility.

Three verdict levels:
1. BIT_EXACT → PASS (逐位等价, ≤2 ULP)
2. STAT_EQUIV → ARCH_DIFF_ACCEPTED (统计等价, 可接受降级)
3. STAT_DIFF → ARCH_DIFF_REJECTED (统计不等价, 阻断合并)

The degradation is fail-closed: any uncertainty defaults to REJECTED.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from . import numerics
from .statistical import (
    full_statistical_equivalence,
    relative_error,
    l2_relative_error,
)


# --------------------------------------------------------------------------- #
# Verdict constants
# --------------------------------------------------------------------------- #
VERDICT_PASS = "PASS"
VERDICT_ARCH_DIFF_ACCEPTED = "ARCH_DIFF_ACCEPTED"
VERDICT_ARCH_DIFF_REJECTED = "ARCH_DIFF_REJECTED"


# --------------------------------------------------------------------------- #
# ULP-based bit-exact check
# --------------------------------------------------------------------------- #
def check_bit_exact(
    ref_record: Dict[str, Any],
    cand_record: Dict[str, Any],
    ulp_cap: int = 2,
) -> Dict[str, Any]:
    """Check bit-exact equivalence (≤ulp_cap ULP for all quantities).

    Returns per-quantity ULP distances and overall pass/fail.
    """
    results = {}
    max_ulp = 0
    all_pass = True

    # Check scalars
    ref_scalars = ref_record.get("scalars", {})
    cand_scalars = cand_record.get("scalars", {})
    for key in sorted(set(ref_scalars) | set(cand_scalars)):
        if key not in ref_scalars or key not in cand_scalars:
            results[f"scalar:{key}"] = {"ulp": float("inf"), "pass": False}
            all_pass = False
            max_ulp = float("inf")
            continue
        ulp = numerics.ulp_distance(ref_scalars[key], cand_scalars[key])
        passed = ulp <= ulp_cap
        results[f"scalar:{key}"] = {"ulp": ulp, "pass": passed}
        if not passed:
            all_pass = False
        if ulp != float("inf"):
            max_ulp = max(max_ulp, ulp)

    # Check fields
    ref_fields = ref_record.get("fields", {})
    cand_fields = cand_record.get("fields", {})
    for key in sorted(set(ref_fields) | set(cand_fields)):
        if key not in ref_fields or key not in cand_fields:
            results[f"field:{key}"] = {"ulp": float("inf"), "pass": False, "max_index": -1}
            all_pass = False
            max_ulp = float("inf")
            continue
        ref_vec = ref_fields[key]
        cand_vec = cand_fields[key]
        if len(ref_vec) != len(cand_vec):
            results[f"field:{key}"] = {"ulp": float("inf"), "pass": False}
            all_pass = False
            max_ulp = float("inf")
            continue

        field_max_ulp = 0
        field_max_idx = 0
        for idx, (rv, cv) in enumerate(zip(ref_vec, cand_vec)):
            u = numerics.ulp_distance(rv, cv)
            if u != float("inf") and u > field_max_ulp:
                field_max_ulp = u
                field_max_idx = idx
            elif u == float("inf"):
                field_max_ulp = float("inf")
                field_max_idx = idx

        passed = field_max_ulp <= ulp_cap
        results[f"field:{key}"] = {
            "ulp": field_max_ulp,
            "pass": passed,
            "max_index": field_max_idx,
        }
        if not passed:
            all_pass = False
        if field_max_ulp != float("inf"):
            max_ulp = max(max_ulp, field_max_ulp)

    return {
        "per_quantity": results,
        "all_pass": all_pass,
        "max_ulp": max_ulp,
        "ulp_cap": ulp_cap,
    }


# --------------------------------------------------------------------------- #
# Injected bias detection
# --------------------------------------------------------------------------- #
def check_injected_bias(
    ref_record: Dict[str, Any],
    cand_record: Dict[str, Any],
    bias_threshold: float = 0.005,  # 0.5%
) -> Dict[str, Any]:
    """Detect if any key quantity has been biased beyond threshold.

    This is the negative control: if the candidate has been deliberately
    perturbed by >0.5%, it must be REJECTED.
    """
    ref_scalars = ref_record.get("scalars", {})
    cand_scalars = cand_record.get("scalars", {})

    biased_quantities = []
    max_bias = 0.0

    for key in sorted(set(ref_scalars) & set(cand_scalars)):
        rel_err = relative_error(ref_scalars[key], cand_scalars[key])
        max_bias = max(max_bias, rel_err)
        if rel_err > bias_threshold:
            biased_quantities.append({
                "key": key,
                "ref": ref_scalars[key],
                "cand": cand_scalars[key],
                "relative_error": rel_err,
            })

    return {
        "biased_quantities": biased_quantities,
        "max_bias": max_bias,
        "threshold": bias_threshold,
        "any_biased": len(biased_quantities) > 0,
    }


# --------------------------------------------------------------------------- #
# Multi-run reproducibility (same-arch byte-identity)
# --------------------------------------------------------------------------- #
def check_same_arch_reproducibility(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Check that multiple runs on the same architecture produce byte-identical results.

    Uses deterministic JSON serialization + SHA-256 for byte-identity check.
    """
    from .solver_benchmarks import record_fingerprint

    if len(records) < 2:
        return {
            "n_runs": len(records),
            "all_identical": True,
            "fingerprints": [],
            "note": "Need at least 2 runs to check reproducibility",
        }

    fingerprints = [record_fingerprint(r) for r in records]
    all_identical = len(set(fingerprints)) == 1

    return {
        "n_runs": len(records),
        "all_identical": all_identical,
        "fingerprints": fingerprints,
        "unique_fingerprints": len(set(fingerprints)),
    }


def check_benchmark_suite_reproducibility(
    suite_records: List[Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Check reproducibility across all benchmarks in the suite."""
    from .solver_benchmarks import record_fingerprint

    if len(suite_records) < 2:
        return {
            "n_runs": len(suite_records),
            "all_identical": True,
            "per_benchmark": {},
        }

    # Check each benchmark independently
    bench_names = set()
    for suite in suite_records:
        bench_names.update(suite.keys())

    per_benchmark = {}
    all_identical = True

    for bench in sorted(bench_names):
        records = [s[bench] for s in suite_records if bench in s]
        fps = [record_fingerprint(r) for r in records]
        identical = len(set(fps)) == 1
        per_benchmark[bench] = {
            "n_runs": len(records),
            "all_identical": identical,
            "fingerprints": fps,
        }
        if not identical:
            all_identical = False

    return {
        "n_runs": len(suite_records),
        "all_identical": all_identical,
        "per_benchmark": per_benchmark,
    }


# --------------------------------------------------------------------------- #
# Combined degradation verdict
# --------------------------------------------------------------------------- #
def compute_degradation_verdict(
    ref_record: Dict[str, Any],
    cand_record: Dict[str, Any],
    ulp_cap: int = 2,
    scalar_rel_tol: float = 0.001,
    field_l2_tol: float = 0.005,
    ttest_p_threshold: float = 0.05,
    bias_threshold: float = 0.005,
) -> Dict[str, Any]:
    """Compute the fail-closed degradation verdict.

    Decision tree:
    1. If bit-exact (all ≤ ulp_cap ULP) → PASS
    2. Else if statistically equivalent → ARCH_DIFF_ACCEPTED
    3. Else → ARCH_DIFF_REJECTED

    The degradation is fail-closed: any check failure defaults to REJECTED.
    """
    # Step 1: Bit-exact check
    bit_exact = check_bit_exact(ref_record, cand_record, ulp_cap=ulp_cap)

    if bit_exact["all_pass"]:
        return {
            "verdict": VERDICT_PASS,
            "reason": "All quantities within ULP cap (bit-exact equivalent)",
            "bit_exact": bit_exact,
            "statistical": None,
            "bias_check": None,
        }

    # Step 2: Statistical equivalence
    stat_equiv = full_statistical_equivalence(
        ref_record, cand_record,
        scalar_rel_tol=scalar_rel_tol,
        field_l2_tol=field_l2_tol,
        ttest_p_threshold=ttest_p_threshold,
    )

    # Step 3: Bias detection
    bias = check_injected_bias(ref_record, cand_record, bias_threshold=bias_threshold)

    if bias["any_biased"]:
        # Injected bias detected → REJECTED
        return {
            "verdict": VERDICT_ARCH_DIFF_REJECTED,
            "reason": f"Injected bias detected: {len(bias['biased_quantities'])} quantity(ies) "
                      f"exceed {bias_threshold*100:.1f}% threshold",
            "bit_exact": bit_exact,
            "statistical": stat_equiv,
            "bias_check": bias,
        }

    if stat_equiv["all_pass"]:
        # Statistically equivalent but not bit-exact → ACCEPTED degradation
        return {
            "verdict": VERDICT_ARCH_DIFF_ACCEPTED,
            "reason": "Not bit-exact but statistically equivalent (acceptable arch difference)",
            "bit_exact": bit_exact,
            "statistical": stat_equiv,
            "bias_check": bias,
        }

    # Statistical equivalence failed → REJECTED
    return {
        "verdict": VERDICT_ARCH_DIFF_REJECTED,
        "reason": "Statistical equivalence failed (not bit-exact, not statistically equivalent)",
        "bit_exact": bit_exact,
        "statistical": stat_equiv,
        "bias_check": bias,
    }


# --------------------------------------------------------------------------- #
# Suite-level verdict
# --------------------------------------------------------------------------- #
def compute_suite_verdict(
    ref_suite: Dict[str, Dict[str, Any]],
    cand_suite: Dict[str, Dict[str, Any]],
    **kwargs,
) -> Dict[str, Any]:
    """Compute verdict across all benchmarks in the suite.

    Overall verdict is the worst of all per-benchmark verdicts.
    """
    per_benchmark = {}
    worst_verdict = VERDICT_PASS
    verdict_priority = {
        VERDICT_PASS: 0,
        VERDICT_ARCH_DIFF_ACCEPTED: 1,
        VERDICT_ARCH_DIFF_REJECTED: 2,
    }

    for bench_name in sorted(set(ref_suite) | set(cand_suite)):
        if bench_name not in ref_suite or bench_name not in cand_suite:
            per_benchmark[bench_name] = {
                "verdict": VERDICT_ARCH_DIFF_REJECTED,
                "reason": "Missing benchmark in one of the suites",
            }
            worst_verdict = VERDICT_ARCH_DIFF_REJECTED
            continue

        v = compute_degradation_verdict(
            ref_suite[bench_name], cand_suite[bench_name], **kwargs)
        per_benchmark[bench_name] = v

        if verdict_priority.get(v["verdict"], 2) > verdict_priority.get(worst_verdict, 0):
            worst_verdict = v["verdict"]

    return {
        "overall_verdict": worst_verdict,
        "per_benchmark": per_benchmark,
        "n_benchmarks": len(per_benchmark),
    }
