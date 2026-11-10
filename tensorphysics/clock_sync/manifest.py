"""RunManifest extension: the ``coupling_clock`` block (>= 4 audited fields).

The platform contract ``contracts/tensorphysics-run-manifest-v1.schema.json`` is
extended here — never replaced — with an optional object that records *how the
clocks of several solvers were reconciled*.  Eight fields are defined, four of
which are mandatory once the block is present:

    coupling_interval_s   the exchange period Δ
    master_dt_s           step of the solver that owns the schedule
    sync_tolerance_s      admissible |skew| at a coupling point
    max_skew_s            observed |skew| over the run

and the optional ones: ``scheme``, ``subcycle_ratios``, ``coupling_points_s``,
``exchanged_fields``, ``n_coupling_points``.

Without this block a coupled run is not reproducible: the same solvers with a
different exchange period, a different alignment rule or a different tolerance
produce different numbers, and nothing in the manifest would say so.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np

__all__ = [
    "REQUIRED_COUPLING_CLOCK_FIELDS",
    "OPTIONAL_COUPLING_CLOCK_FIELDS",
    "coupling_clock_block",
    "validate_coupling_clock",
]

REQUIRED_COUPLING_CLOCK_FIELDS = (
    "coupling_interval_s",
    "master_dt_s",
    "sync_tolerance_s",
    "max_skew_s",
)

OPTIONAL_COUPLING_CLOCK_FIELDS = (
    "scheme",
    "subcycle_ratios",
    "coupling_points_s",
    "n_coupling_points",
    "exchanged_fields",
    "align_last_substep",
)


def coupling_clock_block(
    clock,
    trace=None,
    *,
    exchanged_fields: Sequence[str] = (),
    coupling_points: Optional[Iterable[float]] = None,
    scheme: str = "master-slave-subcycling",
) -> Dict[str, object]:
    """Build the manifest block from a :class:`CouplingClock` (and its trace)."""
    master_dt = float(clock.master.dt)
    if coupling_points is None and trace is not None:
        coupling_points = trace.coupling_times
    points = [] if coupling_points is None else [float(t) for t in coupling_points]
    ratios = {}
    if trace is not None:
        ratios = {k: float(v) for k, v in trace.subcycle_ratios.items()}
    else:
        ratios = {s.name: float(s.dt / master_dt) for s in clock.specs}
    return {
        "scheme": scheme,
        "coupling_interval_s": float(clock.coupling_interval),
        "master_dt_s": master_dt,
        "sync_tolerance_s": float(clock.sync_tolerance),
        "max_skew_s": float(trace.max_skew) if trace is not None else 0.0,
        "align_last_substep": bool(clock.align_last_substep),
        "subcycle_ratios": ratios,
        "n_coupling_points": len(points),
        "coupling_points_s": points,
        "exchanged_fields": [str(f) for f in exchanged_fields],
    }


def validate_coupling_clock(block: Mapping[str, object]) -> Dict[str, object]:
    """Structural + sanity validation of a manifest ``coupling_clock`` block."""
    problems: List[str] = []
    for field in REQUIRED_COUPLING_CLOCK_FIELDS:
        if field not in block:
            problems.append(f"missing required field {field!r}")
            continue
        value = block[field]
        if not isinstance(value, (int, float)) or not np.isfinite(float(value)):
            problems.append(f"{field!r} must be a finite number")
    if "coupling_interval_s" in block and float(block["coupling_interval_s"]) <= 0.0:
        problems.append("coupling_interval_s must be positive")
    if "master_dt_s" in block and float(block["master_dt_s"]) <= 0.0:
        problems.append("master_dt_s must be positive")
    if "sync_tolerance_s" in block and float(block["sync_tolerance_s"]) <= 0.0:
        problems.append("sync_tolerance_s must be positive")
    if {"max_skew_s", "sync_tolerance_s"} <= set(block):
        if float(block["max_skew_s"]) > float(block["sync_tolerance_s"]):
            problems.append("observed max_skew_s exceeds the declared sync_tolerance_s")
    if "subcycle_ratios" in block:
        ratios = block["subcycle_ratios"]
        if not isinstance(ratios, Mapping) or not ratios:
            problems.append("subcycle_ratios must be a non-empty object")
        elif any(float(v) <= 0.0 for v in ratios.values()):
            problems.append("subcycle_ratios must be positive")
    if "coupling_points_s" in block:
        pts = list(block["coupling_points_s"])
        if "n_coupling_points" in block and int(block["n_coupling_points"]) != len(pts):
            problems.append("n_coupling_points does not match coupling_points_s")
        if any(b <= a for a, b in zip(pts, pts[1:])):
            problems.append("coupling_points_s must be strictly increasing")
    present = [f for f in REQUIRED_COUPLING_CLOCK_FIELDS + OPTIONAL_COUPLING_CLOCK_FIELDS if f in block]
    return {
        "ok": not problems,
        "problems": problems,
        "n_required_present": sum(1 for f in REQUIRED_COUPLING_CLOCK_FIELDS if f in block),
        "n_fields_present": len(present),
        "fields_present": present,
    }