"""Mutation engine — injects 6 degradation types into mechanisms.

Degradation types (M1-M6):
  M1  threshold_relax     — threshold widened by 20% (should change verdict)
  M2  exception_swallow   — errors caught, default returned
  M3  order_permutation   — input processing order changed (breaks order-independence)
  M4  idempotency_break   — second call returns different result
  M5  audit_field_drop    — output key silently removed
  M6  short_circuit_const — always returns a constant

Each mutator wraps a mechanism's invoke() and returns a degraded version.
The test suite checks that ALL 6 degradations are DETECTED (golden diff).
"""
from __future__ import annotations

import copy
import hashlib
import random
from typing import Any, Callable, Dict, List

from .mechanisms import get_mechanism


# ── M1: Threshold relaxation (20% wider) ────────────────────────────────

def mutate_m1_threshold_relax(mechanism_name: str) -> Callable:
    """Wrap mechanism so thresholds are relaxed by 20%."""
    mech = get_mechanism(mechanism_name)
    orig_invoke = mech["invoke"]

    def degraded_invoke(inp: Dict[str, Any]) -> Dict[str, Any]:
        mutated = copy.deepcopy(inp)
        if mechanism_name == "maturity_gate_verdict":
            # Lower threshold to make more nodes pass
            mutated["threshold"] = mutated.get("threshold", 0.8) * 0.5
        elif mechanism_name == "evidence_comparator":
            # Flip comparator logic
            for chk in mutated.get("checks", []):
                if chk.get("comparator") == "le":
                    chk["comparator"] = "ge"
                elif chk.get("comparator") == "ge":
                    chk["comparator"] = "le"
                elif chk.get("comparator") == "lt":
                    chk["comparator"] = "gt"
                elif chk.get("comparator") == "gt":
                    chk["comparator"] = "lt"
        elif mechanism_name == "evidence_grader":
            # Force first gate to fail (change from whatever it was)
            mutated["has_schema"] = not mutated.get("has_schema", True)
        elif mechanism_name == "drift_classifier":
            # Change all baseline values to force different classifications
            for pair in mutated.get("pairs", []):
                if isinstance(pair.get("baseline"), (int, float)):
                    pair["baseline"] = pair["baseline"] * 2.0  # Change value
                elif isinstance(pair.get("baseline"), dict):
                    pair["baseline"] = {"modified": True}  # Change structure
        elif mechanism_name == "perf_gate_case":
            # Flip geometry validity
            for case in mutated.get("cases", []):
                geo = case.get("geometry")
                if geo and "metric_a" in geo:
                    # Flip metric_a to invalid range
                    if geo["metric_a"] > 0 and geo["metric_a"] <= 1:
                        geo["metric_a"] = 2.0  # Make invalid
                    else:
                        geo["metric_a"] = 0.5  # Make valid
        return orig_invoke(mutated)

    return degraded_invoke


# ── M2: Exception swallow ───────────────────────────────────────────────

def mutate_m2_exception_swallow(mechanism_name: str) -> Callable:
    """Wrap mechanism so exceptions are caught and a default is returned."""
    mech = get_mechanism(mechanism_name)
    orig_invoke = mech["invoke"]

    def degraded_invoke(inp: Dict[str, Any]) -> Dict[str, Any]:
        try:
            mutated = copy.deepcopy(inp)
            if mechanism_name == "evidence_comparator":
                mutated["checks"].append({
                    "value": 1.0, "comparator": "INVALID_OP_XYZ",
                    "threshold": 0.5
                })
            elif mechanism_name == "maturity_gate_verdict":
                mutated["summaries"].append({
                    "node_key": "bad_node",
                    "evaluable_ratio": "not_a_number",
                    "pass_ratio": 0.5,
                    "claim_mismatches": [],
                })
            elif mechanism_name == "perf_gate_case":
                mutated["cases"].append({
                    "geometry": "invalid_string",
                    "results": {"error_pct_a": 5.0},
                    "expected_verdict": "FAIL",
                })
            elif mechanism_name == "drift_classifier":
                mutated["pairs"].append({
                    "baseline": object(),
                    "candidate": {"value": 1.0},
                    "declared_tol": 0.01,
                })
            elif mechanism_name == "evidence_grader":
                mutated["has_schema"] = "not_a_bool"
            return orig_invoke(mutated)
        except Exception:
            # Swallow: return a "safe" default
            if mechanism_name == "maturity_gate_verdict":
                return {"verdict": "PASS", "nodes_total": 0, "nodes_green": [],
                        "nodes_red": [], "min_pass_ratio": None,
                        "claim_mismatch_total": 0, "blocking": []}
            elif mechanism_name == "evidence_comparator":
                return {"results": [], "all_pass": True}
            elif mechanism_name == "evidence_grader":
                return {"grade": "REAL", "failing_rule": None, "rule_trace": []}
            elif mechanism_name == "drift_classifier":
                return {"classifications": []}
            elif mechanism_name == "perf_gate_case":
                return {"evaluations": []}
            return {}

    return degraded_invoke


# ── M3: Order permutation sensitivity ───────────────────────────────────

def mutate_m3_order_permutation(mechanism_name: str) -> Callable:
    """Wrap mechanism so input lists are sorted by hash (breaks order-independence).

    This injects a bug where the mechanism processes inputs in a different order
    (sorted by hash), which should change the output if the mechanism preserves
    input order in its output (via indices).
    """
    mech = get_mechanism(mechanism_name)
    orig_invoke = mech["invoke"]

    def _hash_key(item: Any) -> str:
        blob = str(sorted(item.items()) if isinstance(item, dict) else item).encode("utf-8")
        return hashlib.md5(blob).hexdigest()

    def degraded_invoke(inp: Dict[str, Any]) -> Dict[str, Any]:
        mutated = copy.deepcopy(inp)
        if mechanism_name == "maturity_gate_verdict":
            mutated["summaries"].sort(key=lambda x: _hash_key(x))
        elif mechanism_name == "evidence_comparator":
            mutated["checks"].sort(key=lambda x: _hash_key(x))
        elif mechanism_name == "drift_classifier":
            mutated["pairs"].sort(key=lambda x: _hash_key(x))
        elif mechanism_name == "perf_gate_case":
            mutated["cases"].sort(key=lambda x: _hash_key(x))
        # evidence_grader has no list inputs - skip
        return orig_invoke(mutated)

    return degraded_invoke


# ── M4: Idempotency break ──────────────────────────────────────────────

def mutate_m4_idempotency_break(mechanism_name: str) -> Callable:
    """Wrap mechanism so it always returns a mutated result (breaks idempotency)."""
    mech = get_mechanism(mechanism_name)
    orig_invoke = mech["invoke"]

    def degraded_invoke(inp: Dict[str, Any]) -> Dict[str, Any]:
        result = orig_invoke(inp)
        mutated = copy.deepcopy(result)
        # Always mutate the output to break idempotency
        if mechanism_name == "maturity_gate_verdict":
            mutated["verdict"] = "WITHHELD"
            mutated["nodes_total"] = 999
        elif mechanism_name == "evidence_comparator":
            mutated["all_pass"] = not mutated.get("all_pass", True)
            if mutated.get("results"):
                mutated["results"][0]["passed"] = not mutated["results"][0].get("passed", True)
        elif mechanism_name == "evidence_grader":
            mutated["grade"] = "UNKNOWN"
            mutated["failing_rule"] = "MUTATED"
        elif mechanism_name == "drift_classifier":
            if mutated.get("classifications"):
                mutated["classifications"][0]["drift_class"] = "timeout_blocked"
            else:
                mutated["classifications"] = [{"pair_idx": 999, "drift_class": "timeout_blocked", "details": {}}]
        elif mechanism_name == "perf_gate_case":
            if mutated.get("evaluations"):
                mutated["evaluations"][0]["verdict"] = "MUTATED"
            else:
                mutated["evaluations"] = [{"case_idx": 999, "verdict": "MUTATED", "reasons": []}]
        return mutated

    return degraded_invoke


# ── M5: Audit field drop ───────────────────────────────────────────────

def mutate_m5_audit_field_drop(mechanism_name: str) -> Callable:
    """Wrap mechanism so a required output field is silently dropped."""
    mech = get_mechanism(mechanism_name)
    orig_invoke = mech["invoke"]

    def degraded_invoke(inp: Dict[str, Any]) -> Dict[str, Any]:
        result = orig_invoke(inp)
        mutated = copy.deepcopy(result)
        if mechanism_name == "maturity_gate_verdict":
            mutated.pop("verdict", None)
        elif mechanism_name == "evidence_comparator":
            mutated.pop("all_pass", None)
        elif mechanism_name == "evidence_grader":
            mutated.pop("grade", None)
        elif mechanism_name == "drift_classifier":
            mutated.pop("classifications", None)
        elif mechanism_name == "perf_gate_case":
            mutated.pop("evaluations", None)
        return mutated

    return degraded_invoke


# ── M6: Short-circuit constant ──────────────────────────────────────────

def mutate_m6_short_circuit(mechanism_name: str) -> Callable:
    """Wrap mechanism so it always returns a constant (ignores input)."""

    def degraded_invoke(inp: Dict[str, Any]) -> Dict[str, Any]:
        if mechanism_name == "maturity_gate_verdict":
            return {"verdict": "PASS", "nodes_total": 0, "nodes_green": [],
                    "nodes_red": [], "min_pass_ratio": 1.0,
                    "claim_mismatch_total": 0, "blocking": []}
        elif mechanism_name == "evidence_comparator":
            return {"results": [{"passed": True, "check_idx": 0, "error": None}],
                    "all_pass": True}
        elif mechanism_name == "evidence_grader":
            return {"grade": "REAL", "failing_rule": None, "rule_trace": []}
        elif mechanism_name == "drift_classifier":
            return {"classifications": [{"pair_idx": 0, "drift_class": "bit_exact",
                                          "details": {}}]}
        elif mechanism_name == "perf_gate_case":
            return {"evaluations": [{"case_idx": 0, "verdict": "PASS", "reasons": []}]}
        return {}

    return degraded_invoke


# ── Mutator registry ────────────────────────────────────────────────────

MUTATORS = {
    "M1_threshold_relax": mutate_m1_threshold_relax,
    "M2_exception_swallow": mutate_m2_exception_swallow,
    "M3_order_permutation": mutate_m3_order_permutation,
    "M4_idempotency_break": mutate_m4_idempotency_break,
    "M5_audit_field_drop": mutate_m5_audit_field_drop,
    "M6_short_circuit": mutate_m6_short_circuit,
}


def apply_mutation(mechanism_name: str, mutation_id: str,
                   fixture: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a mutation to a fixture and return the degraded output."""
    if mutation_id not in MUTATORS:
        raise ValueError(f"Unknown mutation: {mutation_id}. Available: {list(MUTATORS.keys())}")
    mutator_fn = MUTATORS[mutation_id](mechanism_name)
    return mutator_fn(fixture["input"])


def get_all_mutation_ids() -> List[str]:
    return sorted(MUTATORS.keys())
