"""Gate registry and dispatch (domain-neutral).

A gate turns a generator payload into a machine-checkable verdict.  The pipeline
ships two types:

``numeric``
    Per-metric bounds (``min`` and/or ``max``) read from ``payload.metrics``.
    A metric named by the configuration but absent from the payload is a
    *failure*, never a silent pass (a missing number is not a good number).

``conservation``
    Cross-library field-exchange conservation, implemented in
    :mod:`tools.evidence_pipeline.conservation_gate` on top of the coupling
    toolkit's metric definitions.

Dispatch happens here, which is what makes the gate type an extension point: a
new type only needs one entry in :data:`GATE_RUNNERS`.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence

from .config import BenchmarkConfig, GateSpec
from .conservation_gate import GateOutcome, run_conservation_gate

__all__ = ["GateOutcome", "run_numeric_gate", "run_gates", "summarise", "GATE_RUNNERS"]


def run_numeric_gate(spec: GateSpec, payload: Mapping[str, Any]) -> GateOutcome:
    metrics = payload.get("metrics") if isinstance(payload, Mapping) else None
    if not isinstance(metrics, Mapping):
        return GateOutcome(
            id=spec.id,
            type=spec.type,
            passed=False,
            failures=["metrics_unavailable: payload carries no 'metrics' object"],
            metrics={},
            thresholds=dict(spec.thresholds),
        )

    failures: List[str] = []
    observed: Dict[str, Any] = {}
    for metric, bound in spec.thresholds.items():
        if metric not in metrics:
            failures.append(f"{metric}: missing from payload (fail-closed)")
            continue
        value = metrics[metric]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(
            float(value)
        ):
            failures.append(f"{metric}: not a finite number ({value!r})")
            continue
        value = float(value)
        observed[metric] = value
        if "min" in bound and not (value >= bound["min"]):
            failures.append(f"{metric} {value:.12g} < min {bound['min']:.12g}")
        if "max" in bound and not (value <= bound["max"]):
            failures.append(f"{metric} {value:.12g} > max {bound['max']:.12g}")

    return GateOutcome(
        id=spec.id,
        type=spec.type,
        passed=not failures,
        failures=failures,
        metrics=observed,
        thresholds=dict(spec.thresholds),
    )


GATE_RUNNERS = {
    "numeric": run_numeric_gate,
    "conservation": run_conservation_gate,
}


def run_gates(cfg: BenchmarkConfig, payload: Mapping[str, Any]) -> List[GateOutcome]:
    """Run every gate in declaration order (order is auditable, not incidental)."""
    outcomes: List[GateOutcome] = []
    for spec in cfg.gates:
        runner = GATE_RUNNERS.get(spec.type)
        if runner is None:  # pragma: no cover - config validation already rejects this
            outcomes.append(
                GateOutcome(
                    id=spec.id,
                    type=spec.type,
                    passed=False,
                    failures=[f"unknown gate type {spec.type!r}"],
                    thresholds=dict(spec.thresholds),
                )
            )
            continue
        outcomes.append(runner(spec, payload))
    return outcomes


def summarise(outcomes: Sequence[GateOutcome]) -> Dict[str, Any]:
    """Compact verdict used by the artifact and the run record."""
    failures = {o.id: list(o.failures) for o in outcomes if not o.passed}
    return {
        "n_gates": len(outcomes),
        "n_passed": sum(1 for o in outcomes if o.passed),
        "passed": all(o.passed for o in outcomes),
        "failures": failures,
    }