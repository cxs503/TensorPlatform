"""The gate itself: compare two arms, classify every field, decide PASS/FAIL.

``compare`` is intentionally a pure function of (reference record, candidate
record, policy).  That property is what makes the *judging logic* testable
independently of any hardware: the mission's detection-rate / false-positive-rate
criteria are statements about this function, and they are measured by feeding it
records with known injected differences (see :mod:`.selftest`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import classify, fixtures, record as record_mod

DEFAULT_POLICY_PATH = Path(__file__).resolve().parent / "default_policy.json"


def load_policy(path: Optional[Path] = None) -> Dict[str, Any]:
    return json.loads(Path(path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8"))


def compare(ref: Mapping[str, Any], cand: Mapping[str, Any],
            policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Classify every field of two arms and return a machine-readable report."""
    ref_fields = ref["fields"]
    cand_fields = cand["fields"]
    names: List[str] = sorted(set(ref_fields) | set(cand_fields))
    rows = [classify.classify_field(name, ref_fields.get(name), cand_fields.get(name), policy)
            for name in names]

    counts = classify.summarize(rows)
    int_rows = [r for r in rows if r.get("kind") == fixtures.INT]
    fp_rows = [r for r in rows if r.get("kind") == fixtures.FP]
    diag_rows = [r for r in rows if r.get("kind") == fixtures.DIAG]

    int_ok = [r for r in int_rows if r["class"] == classify.BIT_IDENTICAL]
    rel_tol = float(policy["fp64_rel_tol"])
    fp_within = [r for r in fp_rows
                 if r["class"] == classify.BIT_IDENTICAL
                 or (r["class"] in (classify.WITHIN_TOLERANCE,) and (r["rel"] or 0.0) < rel_tol)]
    fp_mismatch = [r for r in rows if r["class"] == classify.NAN and not r["ok"]]
    over = [r for r in rows if r["class"] == classify.OVER_TOLERANCE]

    criteria = policy["criteria"]
    int_bit_exact_rate = len(int_ok) / len(int_rows) if int_rows else 1.0
    fp_within_band_fraction = len(fp_within) / len(fp_rows) if fp_rows else 1.0
    structural = [r for r in rows if r["detail"] in ("missing_field", "kind_mismatch")]

    failed_reasons: List[str] = []
    if over:
        failed_reasons.append(f"{len(over)} field(s) over tolerance")
    if fp_mismatch:
        failed_reasons.append(f"{len(fp_mismatch)} single-sided NaN field(s)")
    if structural:
        failed_reasons.append(f"{len(structural)} structural difference(s)")
    if int_bit_exact_rate < float(criteria["int_bit_exact_rate_min"]):
        failed_reasons.append(f"integer bit-exact rate {int_bit_exact_rate:.4f} below "
                              f"{criteria['int_bit_exact_rate_min']}")
    if fp_within_band_fraction < float(criteria["fp_within_band_fraction_min"]):
        failed_reasons.append(f"fp within-band fraction {fp_within_band_fraction:.4f} below "
                              f"{criteria['fp_within_band_fraction_min']}")

    return {
        "schema_version": "1.0",
        "reference": {"arm": ref["provenance"]["arm"], "machine": ref["provenance"]["machine"],
                      "digest": ref["digest"], "variants": ref["provenance"].get("variants", {})},
        "candidate": {"arm": cand["provenance"]["arm"], "machine": cand["provenance"]["machine"],
                      "digest": cand["digest"], "variants": cand["provenance"].get("variants", {})},
        "same_digest": ref["digest"] == cand["digest"],
        "field_count": len(rows),
        "class_counts": counts,
        "int_bit_exact_rate": int_bit_exact_rate,
        "fp_within_band_fraction": fp_within_band_fraction,
        "over_tolerance_fields": [r["name"] for r in over],
        "nan_mismatch_fields": [r["name"] for r in fp_mismatch],
        "diagnostic_fields": [r["name"] for r in diag_rows],
        "max_ulp_observable": max([r["ulp"] for r in fp_rows if r.get("ulp") is not None] or [0]),
        "max_rel_observable": max([r["rel"] for r in fp_rows if r.get("rel") is not None] or [0.0]),
        "diagnostics_passed": all(r["ok"] for r in diag_rows),
        "verdict": "PASS" if not failed_reasons else "FAIL",
        "verdict_reasons": failed_reasons,
        "policy": {"fp64_rel_tol": policy["fp64_rel_tol"], "fp64_ulp_cap": policy["fp64_ulp_cap"],
                   "diag_abs_tol": policy["diag_abs_tol"]},
        "rows": rows,
    }


def compare_files(ref_path: Path, cand_path: Path, policy: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    policy = policy or load_policy()
    return compare(record_mod.load_record(ref_path), record_mod.load_record(cand_path), policy)


def save_report(report: Mapping[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def compact(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Report without the per-field rows (for summary tables)."""
    return {k: v for k, v in report.items() if k != "rows"}