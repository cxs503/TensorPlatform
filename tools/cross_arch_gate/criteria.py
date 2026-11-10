"""Assemble the six machine-checkable gate criteria into one verdict file.

Every number in the output is copied from an artifact produced earlier in the
same pipeline run (gate reports, mutation matrix, audit) — nothing is typed in by
hand, and each criterion carries the path of the artifact it came from.  That is
what makes the "criteria are satisfied" claim auditable instead of asserted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _criterion(value: Any, threshold: Any, passed: bool, comparator: str,
               source: str, note: str = "") -> Dict[str, Any]:
    return {"value": value, "threshold": threshold, "comparator": comparator,
            "pass": bool(passed), "source": source, "note": note}


def assemble(policy: Mapping[str, Any], pairs: Sequence[Mapping[str, Any]],
             mutation: Mapping[str, Any], latency_s: float,
             audit_report: Mapping[str, Any],
             extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build the criteria block from measured artifacts."""
    crit = policy["criteria"]
    metrics = mutation["metrics"]

    int_values = [p["int_bit_exact_rate"] for p in pairs]
    fp_values = [p["fp_within_band_fraction"] for p in pairs]
    int_rate = min(int_values) if int_values else 1.0
    fp_fraction = min(fp_values) if fp_values else 1.0

    criteria: Dict[str, Any] = {
        "c1_int_bit_exact_rate": _criterion(
            round(int_rate, 6), crit["int_bit_exact_rate_min"], int_rate >= crit["int_bit_exact_rate_min"],
            ">=", extra.get("gate_report", "") if extra else "",
            f"worst over {len(int_values)} arm pair(s); integer fields only"),
        "c2_fp_within_band_fraction": _criterion(
            round(fp_fraction, 6), crit["fp_within_band_fraction_min"],
            fp_fraction >= crit["fp_within_band_fraction_min"], ">=",
            extra.get("gate_report", "") if extra else "",
            "observable fp64 fields with relative error < 1e-10"),
        "c3_over_tolerance_detection_rate": _criterion(
            round(metrics["over_tolerance_detection_rate"], 6),
            crit["over_tolerance_detection_rate_min"],
            metrics["over_tolerance_detection_rate"] >= crit["over_tolerance_detection_rate_min"],
            ">=", extra.get("selftest_report", "") if extra else "",
            f"{metrics['detected_fields']}/{metrics['injected_fields']} injected fields flagged"),
        "c3_false_positive_rate": _criterion(
            round(metrics["false_positive_rate"], 6), crit["false_positive_rate_max"],
            metrics["false_positive_rate"] <= crit["false_positive_rate_max"], "<=",
            extra.get("selftest_report", "") if extra else "",
            f"{metrics['false_positive_fields']}/{metrics['benign_fields']} benign fields misflagged"),
        "c4_nan_detection_rate": _criterion(
            round(metrics["nan_detection_rate"], 6), crit["nan_detection_rate_min"],
            metrics["nan_detection_rate"] >= crit["nan_detection_rate_min"], ">=",
            extra.get("selftest_report", "") if extra else "",
            f"{metrics['nan_detected_fields']}/{metrics['nan_injected_fields']} injected NaN fields flagged"),
        "c5_e2e_latency_s": _criterion(
            round(latency_s, 3), crit["e2e_latency_s_max"],
            latency_s <= crit["e2e_latency_s_max"], "<=",
            extra.get("pipeline", "") if extra else "", "gate compute end to end (cluster queue excluded)"),
        "c6_domain_semantics_hits": _criterion(
            audit_report["hit_count"], crit["domain_semantics_hits_max"],
            audit_report["hit_count"] <= crit["domain_semantics_hits_max"], "<=",
            extra.get("audit_report", "") if extra else "",
            f"{audit_report['files_scanned']} file(s) scanned against the platform lexicon"),
    }

    arm_rows: List[Dict[str, Any]] = [
        {"reference": p["reference"]["arm"], "candidate": p["candidate"]["arm"],
         "verdict": p["verdict"], "same_digest": p["same_digest"],
         "class_counts": p["class_counts"], "max_ulp": p["max_ulp_observable"],
         "max_rel": p["max_rel_observable"]} for p in pairs]

    failed_pairs = [f"{p['reference']['arm']} vs {p['candidate']['arm']}"
                    for p in pairs if p["verdict"] != "PASS"]
    return {
        "schema_version": "1.0",
        "criteria": criteria,
        # Two independent conditions: the six measurable floors above, and the
        # strict per-pair verdicts.  The verdict fails on *any* out-of-band field
        # (that is what makes it a gate), while c2 only asks that at least 95 % of
        # the double-precision fields sit inside the band (that is what makes the
        # tolerance a tolerance).  Both are required; neither implies the other.
        "all_pairs_pass": not failed_pairs,
        "failed_pairs": failed_pairs,
        "all_pass": all(c["pass"] for c in criteria.values()) and not failed_pairs,
        "arms": arm_rows,
        "mutation_metrics": metrics,
        "latency_s": round(latency_s, 3),
        "policy": {k: v for k, v in policy.items() if k != "criteria"},
        "extra": dict(extra or {}),
    }


def save(report: Mapping[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path