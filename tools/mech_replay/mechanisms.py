"""Mechanism registry — uniform pure-function wrappers for 5 platform mechanisms.

Each mechanism exposes:
  - name: str
  - invoke(input: dict) -> dict   (pure, no I/O, no DB, no network)
  - output_sha256(output: dict) -> str
  - required_fields: list[str]    (output keys that must be present)

Design: zero domain vocabulary.  All inputs are structural (numbers, strings,
lists of dicts with generic keys).  The mechanisms are real platform code
imported from their canonical locations.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Real imports from platform mechanisms ──────────────────────────────
from tools.maturity_gate.gate import campaign_verdict as _real_campaign_verdict
from tools.maturity_gate.evidence import compare as _real_compare
from tools.drift_classifier.classifier import (
    classify_drift_fingerprint as _real_classify_drift,
    fingerprint_of as _real_fingerprint_of,
)
from tools.gate_contract.contract import canonical_json as _real_canonical_json


# ── Helpers ─────────────────────────────────────────────────────────────

def _output_sha256(obj: Any) -> str:
    """Deterministic sha256 of mechanism output."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── Mechanism 1: maturity_gate_verdict ──────────────────────────────────

def _invoke_maturity_gate(inp: Dict[str, Any]) -> Dict[str, Any]:
    """campaign_verdict(summaries, threshold, claim_mismatch_total) → verdict dict.

    Input schema:
      summaries: list of {node_key, evaluable_ratio, pass_ratio, claim_mismatches}
      threshold: float (0, 1]
      claim_mismatch_total: int
    """
    summaries = inp["summaries"]
    threshold = inp["threshold"]
    mismatch = inp.get("claim_mismatch_total", 0)
    result = _real_campaign_verdict(summaries, threshold, mismatch)
    return result


MECHANISM_MATURITY_GATE = {
    "name": "maturity_gate_verdict",
    "invoke": _invoke_maturity_gate,
    "output_sha256": _output_sha256,
    "required_fields": ["verdict", "nodes_total", "nodes_green", "nodes_red",
                        "min_pass_ratio", "claim_mismatch_total", "blocking"],
}


# ── Mechanism 2: evidence_comparator ────────────────────────────────────

def _invoke_evidence_comparator(inp: Dict[str, Any]) -> Dict[str, Any]:
    """compare(value, comparator, threshold, tolerance) → bool.

    Input schema:
      checks: list of {value, comparator, threshold, tolerance?}
    Output: {results: list of {passed: bool, check_idx: int}}
    """
    results = []
    for i, chk in enumerate(inp["checks"]):
        val = chk["value"]
        comp = chk["comparator"]
        thr = chk.get("threshold")
        tol = chk.get("tolerance", 0.0)
        try:
            passed = _real_compare(val, comp, thr, tol)
            results.append({"passed": passed, "check_idx": i, "error": None})
        except Exception as exc:
            results.append({"passed": False, "check_idx": i, "error": str(exc)})
    return {"results": results, "all_pass": all(r["passed"] for r in results)}


MECHANISM_EVIDENCE_COMPARATOR = {
    "name": "evidence_comparator",
    "invoke": _invoke_evidence_comparator,
    "output_sha256": _output_sha256,
    "required_fields": ["results", "all_pass"],
}


# ── Mechanism 3: evidence_grader ────────────────────────────────────────

def _invoke_evidence_grader(inp: Dict[str, Any]) -> Dict[str, Any]:
    """Simplified evidence grading: rule-DAG evaluation on a contract-like dict.

    We implement a domain-neutral subset of the R00-R13 rule DAG here
    (the real grade_contract requires file I/O; this pure wrapper takes
    pre-resolved values).

    Input schema:
      has_schema: bool
      has_numeric: bool
      has_repro_cmd: bool
      has_anchor: bool
      anchor_recomputable: bool
      has_residual: bool
      has_independent_path: bool
      path_recomputable: bool
      residual_recomputable: bool
      tolerance_declared: bool
      tolerance_met: bool
      paths_independent: bool
      neg_control_fails: bool
      provenance_matches: bool

    Output: {grade: str, failing_rule: str|None, rule_trace: list}
    """
    rules = [
        ("R00_SCHEMA", inp.get("has_schema", True)),
        ("R01_NUMERIC_PRESENT", inp.get("has_numeric", True)),
        ("R02_REPRO_CMD", inp.get("has_repro_cmd", True)),
        ("R03_ANCHOR_PRESENT", inp.get("has_anchor", True)),
        ("R04_ANCHOR_RECOMPUTABLE", inp.get("anchor_recomputable", True)),
        ("R05_RESIDUAL_PRESENT", inp.get("has_residual", True)),
        ("R06_INDEPENDENT_PATH", inp.get("has_independent_path", True)),
        ("R07_PATH_RECOMPUTABLE", inp.get("path_recomputable", True)),
        ("R08_RESIDUAL_RECOMPUTABLE", inp.get("residual_recomputable", True)),
        ("R09_TOLERANCE", inp.get("tolerance_declared", True) and inp.get("tolerance_met", True)),
        ("R10_INDEPENDENCE", inp.get("paths_independent", True)),
        ("R11_NEGATIVE_CONTROL", inp.get("neg_control_fails", True)),
        ("R12_PROVENANCE_DIGEST", inp.get("provenance_matches", True)),
    ]
    grade_map = {
        "R00_SCHEMA": "UNKNOWN",
        "R01_NUMERIC_PRESENT": "STUB",
        "R02_REPRO_CMD": "MOCK",
        "R03_ANCHOR_PRESENT": "MOCK",
        "R04_ANCHOR_RECOMPUTABLE": "MOCK",
        "R05_RESIDUAL_PRESENT": "WITHHELD",
        "R06_INDEPENDENT_PATH": "WITHHELD",
        "R07_PATH_RECOMPUTABLE": "WITHHELD",
        "R08_RESIDUAL_RECOMPUTABLE": "WITHHELD",
        "R09_TOLERANCE": "WITHHELD",
        "R10_INDEPENDENCE": "WITHHELD",
        "R11_NEGATIVE_CONTROL": "WITHHELD",
        "R12_PROVENANCE_DIGEST": "WITHHELD",
    }
    trace = []
    failing = None
    for rule_id, gate_passes in rules:
        trace.append({"rule": rule_id, "passed": gate_passes})
        if not gate_passes and failing is None:
            failing = rule_id
    grade = "REAL" if failing is None else grade_map.get(failing, "WITHHELD")
    return {"grade": grade, "failing_rule": failing, "rule_trace": trace}


MECHANISM_EVIDENCE_GRADER = {
    "name": "evidence_grader",
    "invoke": _invoke_evidence_grader,
    "output_sha256": _output_sha256,
    "required_fields": ["grade", "failing_rule", "rule_trace"],
}


# ── Mechanism 4: drift_classifier ───────────────────────────────────────

def _invoke_drift_classifier(inp: Dict[str, Any]) -> Dict[str, Any]:
    """classify_drift_fingerprint(baseline, candidate, declared_tol) → drift class.

    Input schema:
      pairs: list of {baseline: any, candidate: any, declared_tol: float}
    Output: {classifications: list of {drift_class, pair_idx, details}}
    """
    classifications = []
    for i, pair in enumerate(inp["pairs"]):
        bl = pair["baseline"]
        cand = pair["candidate"]
        tol = pair.get("declared_tol", 0.01)
        result = _real_classify_drift(bl, cand, declared_tol=tol)
        classifications.append({
            "pair_idx": i,
            "drift_class": result.get("drift_class", result.get("class", "unknown")),
            "details": {k: v for k, v in result.items()
                        if k not in ("drift_class", "class")},
        })
    return {"classifications": classifications}


MECHANISM_DRIFT_CLASSIFIER = {
    "name": "drift_classifier",
    "invoke": _invoke_drift_classifier,
    "output_sha256": _output_sha256,
    "required_fields": ["classifications"],
}


# ── Mechanism 5: perf_gate_case ─────────────────────────────────────────

def _invoke_perf_gate(inp: Dict[str, Any]) -> Dict[str, Any]:
    """Performance gate case evaluation (domain-neutral structural version).

    Evaluates 3 checks: geometry validity, error threshold, mass conservation.
    Input schema (domain-neutral field names):
      cases: list of {
        geometry: {metric_a, metric_b, metric_c} | null,
        results: {error_pct_a, error_pct_b, conservation_err} | null,
        expected_verdict: "PASS" | "FAIL"
      }
    Output: {evaluations: list of {case_idx, verdict, reasons}}
    """
    evaluations = []
    for i, case in enumerate(inp["cases"]):
        reasons = []
        geo = case.get("geometry")
        res = case.get("results")
        # C1: geometry check
        if geo is None:
            reasons.append("geometry_not_verified: missing")
        else:
            required = {"metric_a", "metric_b", "metric_c"}
            missing = required - set(geo.keys())
            if missing:
                reasons.append(f"geometry_missing_fields: {missing}")
            else:
                ma = geo.get("metric_a", 0)
                if ma <= 0 or ma > 1:
                    reasons.append(f"geometry_invalid: metric_a={ma}")
        # C2: error threshold
        if res is None:
            reasons.append("results_missing")
        else:
            for key in ("error_pct_a", "error_pct_b"):
                val = res.get(key)
                if val is not None and val > 15.0:
                    reasons.append(f"error_exceeded: {key}={val}")
        # C3: mass conservation
        if res is not None:
            mc = res.get("conservation_err")
            if mc is not None and mc > 1.0:
                reasons.append(f"conservation_exceeded: {mc}")

        verdict = "FAIL" if reasons else "PASS"
        evaluations.append({"case_idx": i, "verdict": verdict, "reasons": reasons})
    return {"evaluations": evaluations}


MECHANISM_PERF_GATE = {
    "name": "perf_gate_case",
    "invoke": _invoke_perf_gate,
    "output_sha256": _output_sha256,
    "required_fields": ["evaluations"],
}


# ── Registry ────────────────────────────────────────────────────────────

ALL_MECHANISMS: Dict[str, Dict[str, Any]] = {
    "maturity_gate_verdict": MECHANISM_MATURITY_GATE,
    "evidence_comparator": MECHANISM_EVIDENCE_COMPARATOR,
    "evidence_grader": MECHANISM_EVIDENCE_GRADER,
    "drift_classifier": MECHANISM_DRIFT_CLASSIFIER,
    "perf_gate_case": MECHANISM_PERF_GATE,
}


def get_mechanism(name: str) -> Dict[str, Any]:
    """Look up a mechanism by name. Raises KeyError if not found."""
    if name not in ALL_MECHANISMS:
        raise KeyError(f"Unknown mechanism: {name!r}. "
                       f"Available: {sorted(ALL_MECHANISMS.keys())}")
    return ALL_MECHANISMS[name]


def list_mechanisms() -> List[str]:
    return sorted(ALL_MECHANISMS.keys())
