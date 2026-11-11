"""
KPI Snapshot Validator — Dual Estimator Reconciliation (p488w1-n02).

Implements C1: dual-estimator reconciliation for repro_rate and other KPIs.
Two independent estimators compute the same KPI from the same snapshot;
if they disagree beyond tolerance, the snapshot is flagged.

Estimator A (pass-only):       pass_count / total_count
Estimator B (pass+fail+drift): pass_count / (pass_count + fail_count + drift_count)

When denominators differ, the estimators produce different values — this is
the designed inconsistency detector. Any KPI key where |est_a - est_b| > tol
is reported as an inconsistency.

Evidence discipline: all numbers derived from snapshot data, zero hand-copy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class EstimatorResult:
    """Result from one estimator pass over a snapshot."""
    estimator_name: str
    value: float | None
    numerator: int
    denominator: int
    components: dict[str, int] = field(default_factory=dict)


@dataclass
class ReconciliationFinding:
    """A single key-level reconciliation finding."""
    kpi_key: str
    estimator_a_value: float | None
    estimator_b_value: float | None
    abs_delta: float | None
    relative_delta_pct: float | None
    inconsistent: bool
    reason: str = ""


@dataclass
class SnapshotValidationReport:
    """Full validation report for one snapshot."""
    snapshot_id: str
    snapshot_hash: str
    validated_at: str
    findings: list[ReconciliationFinding] = field(default_factory=list)
    total_keys: int = 0
    inconsistent_keys: int = 0
    consistent_keys: int = 0
    has_provenance: bool = False
    provenance_fields: list[str] = field(default_factory=list)
    value_flips: list[dict[str, Any]] = field(default_factory=list)
    status: str = "UNKNOWN"  # PASS, FAIL, FAIL_CLOSED

    def to_dict(self) -> dict:
        d = {
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "validated_at": self.validated_at,
            "total_keys": self.total_keys,
            "inconsistent_keys": self.inconsistent_keys,
            "consistent_keys": self.consistent_keys,
            "has_provenance": self.has_provenance,
            "provenance_fields": self.provenance_fields,
            "status": self.status,
            "findings": [
                {
                    "kpi_key": f.kpi_key,
                    "estimator_a_value": f.estimator_a_value,
                    "estimator_b_value": f.estimator_b_value,
                    "abs_delta": f.abs_delta,
                    "relative_delta_pct": f.relative_delta_pct,
                    "inconsistent": f.inconsistent,
                    "reason": f.reason,
                }
                for f in self.findings
            ],
            "value_flips": self.value_flips,
        }
        return d


# ── Provenance fields that must be present for a snapshot to be trusted ──
REQUIRED_PROVENANCE_FIELDS = [
    "computed_at",
    "code_version",
    "input_version",
    "reference_version",
]

OPTIONAL_PROVENANCE_FIELDS = [
    "compute_host",
    "compute_user",
    "git_commit",
    "pipeline_run_id",
]


def compute_snapshot_hash(snapshot: dict) -> str:
    """Deterministic SHA-256 of snapshot content (sorted keys)."""
    canonical = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def check_provenance(snapshot: dict) -> tuple[bool, list[str]]:
    """
    Check if snapshot has required provenance fields.
    Returns (has_all_required, list_of_present_fields).
    """
    present = []
    for f in REQUIRED_PROVENANCE_FIELDS:
        if f in snapshot and snapshot[f] is not None and snapshot[f] != "":
            present.append(f)
    has_all = len(present) == len(REQUIRED_PROVENANCE_FIELDS)
    return has_all, present


def _extract_ratio_components(snapshot: dict, key: str) -> dict[str, int]:
    """
    Extract numerator/denominator components for a ratio KPI from snapshot.
    Handles multiple snapshot formats.
    """
    components = {}
    val = snapshot.get(key, {})
    if isinstance(val, dict):
        # Format: {"value": 0.85, "numerator": 17, "denominator": 20}
        components["numerator"] = val.get("numerator", 0)
        components["denominator"] = val.get("denominator", 0)
        # Also look for sub-fields
        for sub in ["pass_count", "fail_count", "drift_count", "total"]:
            if sub in val:
                components[sub] = val[sub]
    elif isinstance(val, (int, float)):
        # Scalar — no component breakdown available
        components["scalar_value"] = int(val) if isinstance(val, int) else 0
    return components


def estimator_a_pass_only(snapshot: dict, key: str) -> EstimatorResult:
    """
    Estimator A: pass / total (traditional repro_rate).
    Uses 'pass_count' and 'total' or 'denominator' fields.
    """
    comps = _extract_ratio_components(snapshot, key)
    pass_count = comps.get("pass_count", comps.get("numerator", 0))
    total = comps.get("total", comps.get("denominator", 0))

    if total <= 0:
        return EstimatorResult(
            estimator_name="A_pass_only",
            value=None, numerator=pass_count, denominator=total,
            components=comps,
        )
    return EstimatorResult(
        estimator_name="A_pass_only",
        value=pass_count / total,
        numerator=pass_count, denominator=total,
        components=comps,
    )


def estimator_b_full_denominator(snapshot: dict, key: str) -> EstimatorResult:
    """
    Estimator B: pass / (pass + fail + drift) — strict denominator.
    Includes drift as part of the denominator, making it stricter than A.
    """
    comps = _extract_ratio_components(snapshot, key)
    pass_count = comps.get("pass_count", comps.get("numerator", 0))
    fail_count = comps.get("fail_count", 0)
    drift_count = comps.get("drift_count", 0)
    full_denom = pass_count + fail_count + drift_count

    if full_denom <= 0:
        # Fall back to denominator field if explicit components missing
        total = comps.get("denominator", comps.get("total", 0))
        if total <= 0:
            return EstimatorResult(
                estimator_name="B_full_denominator",
                value=None, numerator=pass_count, denominator=0,
                components=comps,
            )
        full_denom = total

    return EstimatorResult(
        estimator_name="B_full_denominator",
        value=pass_count / full_denom,
        numerator=pass_count, denominator=full_denom,
        components=comps,
    )


def reconcile_key(
    snapshot: dict,
    key: str,
    tolerance: float = 1e-6,
) -> ReconciliationFinding:
    """
    Run both estimators on a single KPI key and compare.
    Returns a ReconciliationFinding.
    """
    ea = estimator_a_pass_only(snapshot, key)
    eb = estimator_b_full_denominator(snapshot, key)

    if ea.value is None or eb.value is None:
        return ReconciliationFinding(
            kpi_key=key,
            estimator_a_value=ea.value,
            estimator_b_value=eb.value,
            abs_delta=None,
            relative_delta_pct=None,
            inconsistent=False,
            reason="one_or_both_estimators_returned_null",
        )

    abs_delta = abs(ea.value - eb.value)
    ref = max(abs(ea.value), abs(eb.value), 1e-12)
    rel_delta_pct = (abs_delta / ref) * 100.0
    inconsistent = abs_delta > tolerance

    reason = ""
    if inconsistent:
        reason = (
            f"denominator_mismatch: A uses {ea.denominator}, "
            f"B uses {eb.denominator}; "
            f"drift_count={_extract_ratio_components(snapshot, key).get('drift_count', 0)}"
        )

    return ReconciliationFinding(
        kpi_key=key,
        estimator_a_value=round(ea.value, 8),
        estimator_b_value=round(eb.value, 8),
        abs_delta=round(abs_delta, 10),
        relative_delta_pct=round(rel_delta_pct, 6),
        inconsistent=inconsistent,
        reason=reason,
    )


def detect_value_flips(
    current_snapshot: dict,
    previous_snapshot: dict | None,
    keys: list[str],
    flip_threshold_pct: float = 5.0,
) -> list[dict[str, Any]]:
    """
    C3: Detect value flips between consecutive snapshots.
    A flip is when a KPI value changes by more than flip_threshold_pct
    without a documented component change (provenance justification).

    Returns list of flip records with justification status.
    """
    flips = []
    if previous_snapshot is None:
        return flips

    for key in keys:
        cur_val = _get_kpi_value(current_snapshot, key)
        prev_val = _get_kpi_value(previous_snapshot, key)

        if cur_val is None or prev_val is None:
            continue

        if prev_val == 0:
            if cur_val != 0:
                flips.append({
                    "kpi_key": key,
                    "previous_value": prev_val,
                    "current_value": cur_val,
                    "abs_change": cur_val - prev_val,
                    "relative_change_pct": None,
                    "has_component_justification": _check_component_justification(
                        current_snapshot, previous_snapshot, key
                    ),
                    "justified": False,
                    "reason": "division_by_zero_in_previous",
                })
            continue

        rel_change_pct = abs((cur_val - prev_val) / prev_val) * 100.0

        if rel_change_pct >= flip_threshold_pct:
            has_justification = _check_component_justification(
                current_snapshot, previous_snapshot, key
            )
            flips.append({
                "kpi_key": key,
                "previous_value": prev_val,
                "current_value": cur_val,
                "abs_change": round(cur_val - prev_val, 8),
                "relative_change_pct": round(rel_change_pct, 4),
                "has_component_justification": has_justification,
                "justified": has_justification,
                "reason": (
                    "component_change_documented"
                    if has_justification
                    else "UNJUSTIFIED_FLIP"
                ),
            })

    return flips


def _get_kpi_value(snapshot: dict, key: str) -> float | None:
    """Extract numeric KPI value from snapshot."""
    val = snapshot.get(key)
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        v = val.get("value")
        if v is not None:
            return float(v)
    return None


def _check_component_justification(
    current: dict, previous: dict, key: str
) -> bool:
    """
    Check if a value flip has component-level justification.
    A flip is justified if the numerator/denominator components changed
    AND the change is documented in provenance.
    """
    cur_comps = _extract_ratio_components(current, key)
    prev_comps = _extract_ratio_components(previous, key)

    if not cur_comps or not prev_comps:
        # No component breakdown — check if provenance explains the change
        cur_prov = current.get("provenance", {})
        if isinstance(cur_prov, dict) and cur_prov.get("change_justification"):
            return True
        return False

    # Components exist — check if any changed
    changed = False
    for comp_key in set(list(cur_comps.keys()) + list(prev_comps.keys())):
        if cur_comps.get(comp_key) != prev_comps.get(comp_key):
            changed = True
            break

    if not changed:
        return False  # No component change but value flipped = suspicious

    # Component changed — check provenance documents it
    cur_prov = current.get("provenance", {})
    if isinstance(cur_prov, dict):
        return bool(cur_prov.get("change_justification") or
                     cur_prov.get("input_version") != previous.get("provenance", {}).get("input_version"))
    return changed  # Component change is itself justification


def validate_snapshot(
    snapshot: dict,
    snapshot_id: str = "unknown",
    previous_snapshot: dict | None = None,
    tolerance: float = 1e-6,
    kpi_keys: list[str] | None = None,
) -> SnapshotValidationReport:
    """
    Full validation of a KPI snapshot:
    1. Provenance check (C2: fail-closed if no provenance)
    2. Dual estimator reconciliation (C1)
    3. Value flip detection (C3)

    Returns SnapshotValidationReport.
    """
    snap_hash = compute_snapshot_hash(snapshot)
    has_prov, prov_fields = check_provenance(snapshot)

    # Determine which keys to reconcile
    if kpi_keys is None:
        kpi_keys = [
            k for k, v in snapshot.items()
            if isinstance(v, (int, float, dict))
            and k not in REQUIRED_PROVENANCE_FIELDS
            and k not in OPTIONAL_PROVENANCE_FIELDS
            and k != "provenance"
        ]

    findings = []
    for key in kpi_keys:
        finding = reconcile_key(snapshot, key, tolerance)
        findings.append(finding)

    inconsistent_keys = sum(1 for f in findings if f.inconsistent)
    consistent_keys = len(findings) - inconsistent_keys

    # Value flip detection
    value_flips = detect_value_flips(snapshot, previous_snapshot, kpi_keys)

    # Determine status
    if not has_prov:
        status = "FAIL_CLOSED"  # C2: zero provenance = reject
    elif any(not vf["justified"] for vf in value_flips):
        status = "FAIL"  # C3: unjustified flip
    elif inconsistent_keys > 0:
        status = "WARN"  # C1: inconsistency detected but not fatal
    else:
        status = "PASS"

    report = SnapshotValidationReport(
        snapshot_id=snapshot_id,
        snapshot_hash=snap_hash,
        validated_at=datetime.now(timezone.utc).isoformat(),
        findings=findings,
        total_keys=len(findings),
        inconsistent_keys=inconsistent_keys,
        consistent_keys=consistent_keys,
        has_provenance=has_prov,
        provenance_fields=prov_fields,
        value_flips=value_flips,
        status=status,
    )
    return report


def validate_batch(
    snapshots: list[dict],
    snapshot_ids: list[str] | None = None,
    tolerance: float = 1e-6,
) -> list[SnapshotValidationReport]:
    """
    Validate a batch of snapshots sequentially (each compared to previous
    for value flip detection).
    """
    if snapshot_ids is None:
        snapshot_ids = [f"snap_{i:03d}" for i in range(len(snapshots))]

    reports = []
    prev = None
    for i, snap in enumerate(snapshots):
        report = validate_snapshot(
            snap,
            snapshot_id=snapshot_ids[i],
            previous_snapshot=prev,
            tolerance=tolerance,
        )
        reports.append(report)
        prev = snap
    return reports
