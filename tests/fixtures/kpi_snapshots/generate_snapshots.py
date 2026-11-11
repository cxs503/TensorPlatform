"""
Generate 18 synthetic KPI snapshots for traceability audit (p488w1-n02).

These snapshots simulate a realistic KPI evolution timeline with:
- Some snapshots with full provenance (trusted)
- Some snapshots missing provenance (should be fail-closed)
- Some snapshots with value flips (some justified, some not)
- Some snapshots with estimator inconsistencies (drift_count present)

The snapshots are deterministic (seeded) for reproducibility.
"""
import json
import os
from datetime import datetime, timezone, timedelta

SNAPSHOTS = []

# ── Helper ────────────────────────────────────────────────────────────
def _make_snapshot(
    idx: int,
    repro_pass: int,
    repro_fail: int,
    repro_drift: int,
    success: int,
    failed: int,
    benchmark: int,
    kb: int,
    has_provenance: bool = True,
    has_flip_justification: bool = True,
    code_version: str = "v1.0.0",
    input_version: str = "input-v1",
    reference_version: str = "ref-v1",
    notes: str = "",
):
    """Create a single KPI snapshot."""
    total = repro_pass + repro_fail + repro_drift
    repro_rate = repro_pass / total if total > 0 else 0.0
    success_rate = success / (success + failed) if (success + failed) > 0 else 0.0

    base_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
    computed_at = (base_time + timedelta(days=idx)).isoformat()

    snap = {
        "snapshot_id": f"kpi_snap_{idx:03d}",
        "repro_rate": {
            "value": round(repro_rate, 6),
            "numerator": repro_pass,
            "denominator": total,
            "pass_count": repro_pass,
            "fail_count": repro_fail,
            "drift_count": repro_drift,
            "total": total,
        },
        "success_rate": {
            "value": round(success_rate, 6),
            "numerator": success,
            "denominator": success + failed,
        },
        "benchmark_cases": benchmark,
        "kb_samples": kb,
        "lineage_coverage": {
            "value": round(min(1.0, 0.7 + idx * 0.02), 6),
            "numerator": int(50 + idx * 3),
            "denominator": int(70 + idx * 2),
        },
    }

    if has_provenance:
        snap["computed_at"] = computed_at
        snap["code_version"] = code_version
        snap["input_version"] = input_version
        snap["reference_version"] = reference_version
        snap["provenance"] = {
            "computed_at": computed_at,
            "code_version": code_version,
            "input_version": input_version,
            "reference_version": reference_version,
            "pipeline_run_id": f"run_{idx:04d}",
            "compute_host": "loong64-node-01",
        }
        if has_flip_justification and idx > 0:
            snap["provenance"]["change_justification"] = (
                f"Incremental data update at day {idx}"
            )

    if notes:
        snap["notes"] = notes

    return snap


# ── Generate 18 snapshots ────────────────────────────────────────────

# Snapshots 0-4: Normal evolution, full provenance, no drift
for i in range(5):
    SNAPSHOTS.append(_make_snapshot(
        idx=i,
        repro_pass=17 + i,
        repro_fail=2,
        repro_drift=1,
        success=40 + i * 2,
        failed=5,
        benchmark=5 + i,
        kb=100 + i * 10,
        has_provenance=True,
    ))

# Snapshot 5: Missing provenance (should be FAIL_CLOSED)
SNAPSHOTS.append(_make_snapshot(
    idx=5,
    repro_pass=22,
    repro_fail=2,
    repro_drift=1,
    success=50,
    failed=5,
    benchmark=10,
    kb=150,
    has_provenance=False,
))

# Snapshots 6-9: Normal with drift_count causing estimator inconsistency
for i in range(6, 10):
    SNAPSHOTS.append(_make_snapshot(
        idx=i,
        repro_pass=20 + i,
        repro_fail=3,
        repro_drift=5 + i,  # Significant drift → estimator mismatch
        success=45 + i,
        failed=4,
        benchmark=8 + i,
        kb=160 + i * 5,
        has_provenance=True,
        code_version=f"v1.{i}.0",
    ))

# Snapshot 10: Missing provenance (FAIL_CLOSED)
SNAPSHOTS.append(_make_snapshot(
    idx=10,
    repro_pass=30,
    repro_fail=1,
    repro_drift=2,
    success=55,
    failed=3,
    benchmark=14,
    kb=200,
    has_provenance=False,
))

# Snapshot 11: Large value flip WITH justification
SNAPSHOTS.append(_make_snapshot(
    idx=11,
    repro_pass=35,  # Big jump from ~30
    repro_fail=2,
    repro_drift=3,
    success=60,
    failed=3,
    benchmark=15,
    kb=220,
    has_provenance=True,
    has_flip_justification=True,
    code_version="v2.0.0",
    input_version="input-v2",
    notes="Major data refresh — input version changed",
))

# Snapshot 12: Large value flip WITHOUT justification (UNJUSTIFIED)
snap12 = _make_snapshot(
    idx=12,
    repro_pass=15,  # Big drop from 35 — no justification
    repro_fail=2,
    repro_drift=3,
    success=30,  # Big drop
    failed=3,
    benchmark=10,  # Big drop
    kb=150,  # Big drop
    has_provenance=True,
    has_flip_justification=False,
    code_version="v2.0.0",  # Same code version — suspicious
    input_version="input-v2",  # Same input — suspicious
    notes="No explanation for value changes",
)
# Remove change_justification to make it truly unjustified
if "provenance" in snap12 and "change_justification" in snap12["provenance"]:
    del snap12["provenance"]["change_justification"]
SNAPSHOTS.append(snap12)

# Snapshots 13-17: Recovery with full provenance
for i in range(13, 18):
    SNAPSHOTS.append(_make_snapshot(
        idx=i,
        repro_pass=20 + i,
        repro_fail=2,
        repro_drift=2,
        success=45 + i,
        failed=3,
        benchmark=12 + (i - 13),
        kb=180 + i * 5,
        has_provenance=True,
        code_version=f"v2.{i-12}.0",
        input_version=f"input-v{i-12}",
        reference_version=f"ref-v{i-12}",
    ))

assert len(SNAPSHOTS) == 18, f"Expected 18 snapshots, got {len(SNAPSHOTS)}"


def save_snapshots(output_dir: str) -> str:
    """Save all snapshots to a JSON file."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "kpi_snapshots_18.json")
    with open(path, "w") as f:
        json.dump({
            "description": "18 synthetic KPI snapshots for traceability audit",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_count": len(SNAPSHOTS),
            "snapshots": SNAPSHOTS,
        }, f, indent=2)
    return path


if __name__ == "__main__":
    path = save_snapshots("tests/fixtures/kpi_snapshots")
    print(f"Saved {len(SNAPSHOTS)} snapshots to {path}")
