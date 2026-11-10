"""Drift Fingerprint Classifier — core classification logic.

Five drift classes (mutually exclusive, ordered by priority):
  1. ``bit_exact``      — SHA-256 of serialized outputs match exactly
  2. ``ulp_level``      — relative difference < 1e-15 (machine epsilon neighborhood)
  3. ``threshold_level`` — relative difference < declared tolerance but > ULP
  4. ``structural``     — missing keys, type mismatch, shape mismatch
  5. ``timeout_blocked`` — one or both runs timed out / incomplete

All functions are pure (no I/O, no global state, no domain semantics).
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

# ── Drift class constants ──────────────────────────────────────────────

DRIFT_BIT_EXACT = "bit_exact"
DRIFT_ULP_LEVEL = "ulp_level"
DRIFT_THRESHOLD_LEVEL = "threshold_level"
DRIFT_STRUCTURAL = "structural"
DRIFT_TIMEOUT_BLOCKED = "timeout_blocked"

DRIFT_CLASSES = (
    DRIFT_BIT_EXACT,
    DRIFT_ULP_LEVEL,
    DRIFT_THRESHOLD_LEVEL,
    DRIFT_STRUCTURAL,
    DRIFT_TIMEOUT_BLOCKED,
)

ULP_THRESHOLD = 1e-15  # machine-epsilon neighborhood for float64


# ── Fingerprint ────────────────────────────────────────────────────────

def fingerprint_of(obj: Any) -> str:
    """Deterministic SHA-256 fingerprint of a JSON-serializable object.

    Uses sort_keys + separators=(',', ':') for byte-stable serialization.
    Non-serializable objects get a type-tagged repr hash.
    """
    try:
        blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        blob = f"<unserializable:{type(obj).__name__}:{repr(obj)[:200]}>"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── ULP distance ───────────────────────────────────────────────────────

def compute_ulp_distance(a: float, b: float) -> float:
    """Relative ULP-style distance between two finite floats.

    Returns |a - b| / max(|a|, |b|, 1e-300) to avoid division by zero.
    For values near zero, uses absolute difference.
    """
    if not (math.isfinite(a) and math.isfinite(b)):
        return float("inf")
    denom = max(abs(a), abs(b), 1e-300)
    return abs(a - b) / denom


# ── Structure comparison ───────────────────────────────────────────────

def compare_structures(baseline: Any, candidate: Any, path: str = "") -> list[dict[str, Any]]:
    """Recursively compare the structure of two objects.

    Returns a list of structural differences (missing keys, type mismatches,
    shape mismatches). Empty list means structures are compatible.
    """
    diffs: list[dict[str, Any]] = []

    if type(baseline) is not type(candidate):
        # Allow int/float cross-comparison
        if isinstance(baseline, (int, float)) and isinstance(candidate, (int, float)):
            pass  # numeric cross-type is not structural
        else:
            diffs.append({
                "path": path or ".",
                "issue": "type_mismatch",
                "baseline_type": type(baseline).__name__,
                "candidate_type": type(candidate).__name__,
            })
            return diffs

    if isinstance(baseline, dict):
        b_keys = set(baseline.keys())
        c_keys = set(candidate.keys())
        missing = b_keys - c_keys
        extra = c_keys - b_keys
        for k in sorted(missing):
            diffs.append({"path": f"{path}.{k}" if path else k, "issue": "missing_key", "key": k})
        for k in sorted(extra):
            diffs.append({"path": f"{path}.{k}" if path else k, "issue": "extra_key", "key": k})
        for k in sorted(b_keys & c_keys):
            child_path = f"{path}.{k}" if path else k
            diffs.extend(compare_structures(baseline[k], candidate[k], child_path))

    elif isinstance(baseline, (list, tuple)):
        if len(baseline) != len(candidate):
            diffs.append({
                "path": path or ".",
                "issue": "length_mismatch",
                "baseline_len": len(baseline),
                "candidate_len": len(candidate),
            })
        else:
            for i, (bv, cv) in enumerate(zip(baseline, candidate)):
                diffs.extend(compare_structures(bv, cv, f"{path}[{i}]" if path else f"[{i}]"))

    return diffs


# ── Value-level drift classification ───────────────────────────────────

def classify_value_drift(baseline_val: Any, candidate_val: Any,
                         declared_tol: float = 0.01) -> dict[str, Any]:
    """Classify drift between two scalar values.

    Returns dict with: drift_class, ulp_distance, relative_diff, evidence.
    """
    # Check if both are numeric
    b_numeric = isinstance(baseline_val, (int, float)) and not isinstance(baseline_val, bool)
    c_numeric = isinstance(candidate_val, (int, float)) and not isinstance(candidate_val, bool)

    if not b_numeric or not c_numeric:
        # Non-numeric: exact match or structural
        if baseline_val == candidate_val:
            return {"drift_class": DRIFT_BIT_EXACT, "evidence": {"match": True}}
        else:
            return {"drift_class": DRIFT_STRUCTURAL,
                    "evidence": {"mismatch": True, "baseline": baseline_val, "candidate": candidate_val}}

    b = float(baseline_val)
    c = float(candidate_val)

    # Bit-exact check
    if b == c:
        return {"drift_class": DRIFT_BIT_EXACT, "evidence": {"bit_match": True}}

    ulp_dist = compute_ulp_distance(b, c)
    rel_diff = abs(b - c) / max(abs(b), abs(c), 1e-300)

    if ulp_dist < ULP_THRESHOLD:
        return {
            "drift_class": DRIFT_ULP_LEVEL,
            "ulp_distance": ulp_dist,
            "relative_diff": rel_diff,
            "evidence": {"ulp_below_threshold": True, "ulp_distance": ulp_dist},
        }

    if rel_diff < declared_tol:
        return {
            "drift_class": DRIFT_THRESHOLD_LEVEL,
            "ulp_distance": ulp_dist,
            "relative_diff": rel_diff,
            "declared_tol": declared_tol,
            "evidence": {"within_tolerance": True, "relative_diff": rel_diff, "tol": declared_tol},
        }

    # Beyond tolerance — still classify as threshold_level with exceeded flag
    return {
        "drift_class": DRIFT_THRESHOLD_LEVEL,
        "ulp_distance": ulp_dist,
        "relative_diff": rel_diff,
        "declared_tol": declared_tol,
        "evidence": {"exceeded_tolerance": True, "relative_diff": rel_diff, "tol": declared_tol},
    }


# ── Full drift fingerprint classification ──────────────────────────────

def classify_drift_fingerprint(baseline: Any, candidate: Any,
                                declared_tol: float = 0.01,
                                baseline_timed_out: bool = False,
                                candidate_timed_out: bool = False) -> dict[str, Any]:
    """Classify the overall drift between baseline and candidate outputs.

    Priority order:
      1. timeout_blocked — if either run timed out
      2. bit_exact — SHA-256 fingerprints match
      3. structural — key/type/shape mismatches
      4. ulp_level — all numeric diffs < 1e-15
      5. threshold_level — numeric diffs within declared tolerance

    Returns:
        {
            "drift_class": str,
            "evidence": dict,
            "details": list[dict],  # per-field breakdown
            "fingerprint_baseline": str,
            "fingerprint_candidate": str,
        }
    """
    fp_baseline = fingerprint_of(baseline)
    fp_candidate = fingerprint_of(candidate)

    # 1. Timeout check
    if baseline_timed_out or candidate_timed_out:
        return {
            "drift_class": DRIFT_TIMEOUT_BLOCKED,
            "evidence": {
                "baseline_timed_out": baseline_timed_out,
                "candidate_timed_out": candidate_timed_out,
            },
            "details": [],
            "fingerprint_baseline": fp_baseline,
            "fingerprint_candidate": fp_candidate,
        }

    # 2. Bit-exact check
    if fp_baseline == fp_candidate:
        return {
            "drift_class": DRIFT_BIT_EXACT,
            "evidence": {"sha256_match": True, "sha256": fp_baseline},
            "details": [],
            "fingerprint_baseline": fp_baseline,
            "fingerprint_candidate": fp_candidate,
        }

    # 3. Structural check
    struct_diffs = compare_structures(baseline, candidate)
    if struct_diffs:
        return {
            "drift_class": DRIFT_STRUCTURAL,
            "evidence": {"structural_diffs": struct_diffs[:20]},  # cap at 20
            "details": struct_diffs,
            "fingerprint_baseline": fp_baseline,
            "fingerprint_candidate": fp_candidate,
        }

    # 4-5. Numeric drift analysis (flat dicts or scalars)
    details = _numeric_drift_details(baseline, candidate, declared_tol)
    max_ulp = max((d.get("ulp_distance", 0) for d in details), default=0)
    max_rel = max((d.get("relative_diff", 0) for d in details), default=0)

    if max_ulp < ULP_THRESHOLD:
        drift_class = DRIFT_ULP_LEVEL
    else:
        drift_class = DRIFT_THRESHOLD_LEVEL

    return {
        "drift_class": drift_class,
        "evidence": {
            "max_ulp_distance": max_ulp,
            "max_relative_diff": max_rel,
            "declared_tol": declared_tol,
            "fields_compared": len(details),
        },
        "details": details,
        "fingerprint_baseline": fp_baseline,
        "fingerprint_candidate": fp_candidate,
    }


def _numeric_drift_details(baseline: Any, candidate: Any,
                           declared_tol: float) -> list[dict[str, Any]]:
    """Extract per-field numeric drift details from nested structures."""
    details = []

    if isinstance(baseline, dict) and isinstance(candidate, dict):
        shared_keys = sorted(set(baseline.keys()) & set(candidate.keys()))
        for key in shared_keys:
            bv = baseline[key]
            cv = candidate[key]
            if isinstance(bv, (int, float)) and isinstance(cv, (int, float)):
                if not isinstance(bv, bool) and not isinstance(cv, bool):
                    vd = classify_value_drift(bv, cv, declared_tol)
                    vd["field"] = key
                    details.append(vd)
            elif isinstance(bv, dict) and isinstance(cv, dict):
                details.extend(_numeric_drift_details(bv, cv, declared_tol))
    elif isinstance(baseline, (int, float)) and isinstance(candidate, (int, float)):
        if not isinstance(baseline, bool) and not isinstance(candidate, bool):
            vd = classify_value_drift(baseline, candidate, declared_tol)
            vd["field"] = "."
            details.append(vd)

    return details
