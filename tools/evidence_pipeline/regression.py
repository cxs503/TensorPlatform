"""Baseline comparison and drift classification (domain-neutral).

Drift is judged per metric on a *relative* scale with a per-metric floor, so a
metric that legitimately sits near zero cannot manufacture an infinite residual:

    rel_drift(m) = |cur - base| / max(|base|, floor(m))

The decision table is deliberately unforgiving — drift is never "warned
through":

======================  =====================================================
classification          meaning
======================  =====================================================
``stable``              every metric within ``tol`` of the baseline.
``no_baseline``         first run for this node; artifact recorded as the
                        baseline candidate, nothing to compare against.
``drift_reproducible``  drift detected *and* the re-runs reproduce the current
                        numbers exactly → the code/config changed; a human (or
                        the campaign) must decide whether the baseline is
                        re-blessed. The run is FAILED, never passed.
``drift_unverified``    drift detected but no re-run was allowed
                        (``regression.reruns = 0``) → unproven either way.
``drift_nondeterministic``
                        re-runs disagree with each other → the generator is
                        non-deterministic, which is itself a failure: an
                        unrepeatable number cannot carry evidence.
======================  =====================================================
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = ["compare", "decide", "DEFAULT_FLOOR"]

DEFAULT_FLOOR = 1e-12


def compare(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    tol: float,
    floors: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Per-metric relative drift between a current run and a baseline."""
    floors = dict(floors or {})
    per_metric: Dict[str, Any] = {}
    drifted: List[str] = []
    for metric in sorted(set(baseline) | set(current)):
        base = baseline.get(metric)
        cur = current.get(metric)
        base_f = float(base) if isinstance(base, (int, float)) and not isinstance(base, bool) else None
        cur_f = float(cur) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None
        if base_f is None or cur_f is None:
            entry = {
                "baseline": base,
                "current": cur,
                "rel_drift": math.inf,
                "tolerance": tol,
                "exceeded": True,
                "reason": "metric missing on one side (fail-closed)",
            }
        else:
            floor = max(float(floors.get(metric, DEFAULT_FLOOR)), 0.0)
            denom = max(abs(base_f), floor)
            drift = abs(cur_f - base_f) / denom if denom > 0.0 else abs(cur_f - base_f)
            entry = {
                "baseline": base_f,
                "current": cur_f,
                "rel_drift": drift,
                "tolerance": tol,
                "exceeded": bool(drift > tol),
            }
        if entry["exceeded"]:
            drifted.append(metric)
        per_metric[metric] = entry

    finite = [e["rel_drift"] for e in per_metric.values() if math.isfinite(e["rel_drift"])]
    return {
        "tol": tol,
        "floors": floors,
        "per_metric": per_metric,
        "drifted": drifted,
        "max_rel_drift": max(finite) if finite else (math.inf if per_metric else 0.0),
        "within_tolerance": not drifted,
    }


def decide(
    comparison: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    has_baseline: bool,
) -> Dict[str, Any]:
    """Classify the drift using every replay attempt the runner performed.

    ``attempts`` is the ordered list of observed payload digests (the first
    element being the judged run, the rest the triggered re-runs).
    """
    if not has_baseline:
        return {
            "classification": "no_baseline",
            "passed": True,
            "n_attempts": len(attempts),
            "drifted": [],
            "reason": "first run for this node: no committed baseline to compare against",
        }
    if comparison.get("within_tolerance"):
        return {
            "classification": "stable",
            "passed": True,
            "n_attempts": len(attempts),
            "drifted": [],
            "reason": "all metrics within tolerance of the baseline",
            "max_rel_drift": comparison.get("max_rel_drift"),
        }

    digests = [a.get("payload_digest") for a in attempts]
    drifted = list(comparison.get("drifted", []))
    if len(attempts) < 2:
        return {
            "classification": "drift_unverified",
            "passed": False,
            "n_attempts": len(attempts),
            "drifted": drifted,
            "reason": "drift detected but no re-run was allowed (regression.reruns = 0)",
            "max_rel_drift": comparison.get("max_rel_drift"),
        }
    if len(set(digests)) == 1:
        return {
            "classification": "drift_reproducible",
            "passed": False,
            "n_attempts": len(attempts),
            "drifted": drifted,
            "reason": (
                "drift detected and reproduced identically by every re-run: the producing "
                "code/config changed; the baseline must be re-blessed explicitly"
            ),
            "max_rel_drift": comparison.get("max_rel_drift"),
            "attempt_digests": digests,
        }
    return {
        "classification": "drift_nondeterministic",
        "passed": False,
        "n_attempts": len(attempts),
        "drifted": drifted,
        "reason": "re-runs disagree with each other: the number is not reproducible",
        "max_rel_drift": comparison.get("max_rel_drift"),
        "attempt_digests": digests,
    }