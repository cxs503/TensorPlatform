"""Frozen probe set for the auto-grading pipeline.

A *probe* is a pure numerical routine with an independent reference, so its
measured value can be graded without a human reading a report:

===============  ===========================================================
routine          reference
===============  ===========================================================
``quadrature``   composite trapezoidal rule vs the closed form of the integral
``ivp``          explicit RK4 vs the analytic solution of a linear ODE
``series``       partial sum vs the closed form of a geometric series
===============  ===========================================================

Defect modes re-wrap a routine to imitate the ways a "capability" can be missing
behind an HTTP 200: a constant, a constant times a factor, a biased formula, a
deterministic jitter, a wrong unit scale, a clamped/plateau branch, a coarse
lattice and a call-order-dependent value.  They exist so the auto-grading
mechanism can be *backtested* against labelled negatives — a mechanism that only
ever sees honest endpoints has an unmeasured false-positive rate.

Everything here is generic mathematics; the frozen endpoint table itself is
injected from configuration (see ``config/capability_governance/probe_set.json``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# reference numerical routines
# ---------------------------------------------------------------------------


def quadrature_measure(n: int) -> Dict[str, float]:
    """Trapezoidal quadrature of sin over [0, pi]; closed form = 2."""
    n = int(n)
    if n <= 0:
        raise ValueError("n must be positive")
    h = math.pi / n
    total = 0.5 * (math.sin(0.0) + math.sin(math.pi))
    for i in range(1, n):
        total += math.sin(i * h)
    return {"measured": total * h, "reference": 2.0}


def ivp_measure(steps: int) -> Dict[str, float]:
    """RK4 for y' = -2 y, y(0)=1, integrated to t=1; closed form = exp(-2)."""
    steps = int(steps)
    if steps <= 0:
        raise ValueError("steps must be positive")
    dt = 1.0 / steps
    y = 1.0
    t = 0.0
    for _ in range(steps):
        k1 = -2.0 * y
        k2 = -2.0 * (y + 0.5 * dt * k1)
        k3 = -2.0 * (y + 0.5 * dt * k2)
        k4 = -2.0 * (y + dt * k3)
        y += dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        t += dt  # noqa: F841  (kept explicit: the loop integrates to t=1)
    return {"measured": y, "reference": math.exp(-2.0)}


def series_measure(n: int) -> Dict[str, float]:
    """Partial sum of sum_{k>=1} 2^-k; closed form = 1."""
    n = int(n)
    if n <= 0:
        raise ValueError("n must be positive")
    total = 0.0
    term = 1.0
    for _ in range(n):
        term *= 0.5
        total += term
    return {"measured": total, "reference": 1.0}


ROUTINES: Dict[str, Callable[[int], Dict[str, float]]] = {
    "quadrature": quadrature_measure,
    "ivp": ivp_measure,
    "series": series_measure,
}

# defect modes -> (callable factory, label) ---------------------------------
# A label is what the frozen table asserts the honest grade is: an endpoint that
# ignores its input is a MOCK, an endpoint that answers but misses the reference
# tolerance is WITHHELD (unproven, not necessarily faked).
DEFECT_LABELS: Dict[str, str] = {
    "ok": "REAL",
    "constant": "MOCK",
    "scaled_constant": "MOCK",
    "drifting": "MOCK",
    "biased": "WITHHELD",
    "jitter": "WITHHELD",
    "unit_error": "WITHHELD",
    "clamped": "WITHHELD",
    "lattice": "MOCK",
}


def _defect(routine: Callable[[int], Dict[str, float]], mode: str,
            params: Mapping[str, Any], design_point: int) -> Callable[[int], Dict[str, float]]:
    """Wrap ``routine`` with a defect.  Pure functions of the call index where needed."""
    state = {"calls": 0}
    design = design_point

    def _base(x: int) -> Dict[str, float]:
        out = routine(x)
        return {"measured": float(out["measured"]), "reference": float(out["reference"])}

    def _constant(x: int) -> Dict[str, float]:
        out = _base(design)
        return {"measured": out["measured"], "reference": out["reference"]}

    if mode == "ok":
        return _base
    if mode == "constant":
        return _constant
    if mode == "scaled_constant":
        k = float(params.get("factor", 1.5))
        return lambda x: {k2: (v * k if k2 == "measured" else v) for k2, v in _constant(x).items()}
    if mode == "biased":
        k = float(params.get("factor", 1.2))
        return lambda x: {k2: (v * k if k2 == "measured" else v) for k2, v in _base(x).items()}
    if mode == "jitter":
        amp = float(params.get("amplitude", 0.05))
        seen: Dict[int, float] = {}

        def _jitter(x: int) -> Dict[str, float]:
            # deterministic per input (so repeat calls agree) but alternating in
            # sign across the grid: a biased formula, not a random one
            if x not in seen:
                seen[x] = 1.0 if len(seen) % 2 == 0 else -1.0
            out = _base(x)
            return {"measured": out["measured"] * (1.0 + amp * seen[x]),
                    "reference": out["reference"]}
        return _jitter
    if mode == "unit_error":
        k = float(params.get("factor", 1000.0))
        return lambda x: {k2: (v * k if k2 == "measured" else v) for k2, v in _base(x).items()}
    if mode == "clamped":
        cap = int(params.get("cap", design))
        return lambda x: {k2: (v if k2 != "measured" else float(routine(min(int(x), cap))["measured"]))
                          for k2, v in _base(x).items()}
    if mode == "lattice":
        step = float(params.get("step", 0.05))
        def _lat(x: int) -> Dict[str, float]:
            out = _base(x)
            return {"measured": round(out["measured"] / step) * step, "reference": out["reference"]}
        return _lat
    if mode == "drifting":
        def _drift(x: int) -> Dict[str, float]:
            state["calls"] += 1
            out = _base(x)
            return {"measured": out["measured"] * (1.0 + 1e-5 * state["calls"]),
                    "reference": out["reference"]}
        return _drift
    raise ValueError(f"unknown defect mode '{mode}'")


@dataclass
class ProbeResult:
    """One (endpoint, grid point) measurement."""

    endpoint_id: str
    routine: str
    defect: str
    grid: Sequence[int]
    measured: List[float]
    reference: List[float]
    digests_repeat: List[str] = field(default_factory=list)
    repeat_equal: List[bool] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "routine": self.routine,
            "defect": self.defect,
            "grid": list(self.grid),
            "measured": list(self.measured),
            "reference": list(self.reference),
            "digests_repeat": list(self.digests_repeat),
            "repeat_equal": list(self.repeat_equal),
        }


def run_endpoint(spec: Mapping[str, Any]) -> ProbeResult:
    """Execute one frozen endpoint spec across its parameter grid."""
    routine_name = spec["routine"]
    if routine_name not in ROUTINES:
        raise KeyError(f"unknown routine '{routine_name}'")
    grid = [int(x) for x in spec["grid"]]
    if len(grid) < 2:
        raise ValueError("grid needs at least two points")
    defect = spec.get("defect", "ok")
    fn = _defect(ROUTINES[routine_name], defect, spec.get("defect_params") or {}, grid[0])
    measured: List[float] = []
    reference: List[float] = []
    digests: List[str] = []
    repeat_equal: List[bool] = []
    import hashlib
    import json

    for x in grid:
        out = fn(x)
        measured.append(float(out["measured"]))
        reference.append(float(out["reference"]))
        # second call on the same input: a stable capability must reproduce itself
        again = fn(x)
        payload = json.dumps({"first": out["measured"], "repeat": again["measured"]},
                             sort_keys=True)
        digests.append(hashlib.sha256(payload.encode("utf-8")).hexdigest())
        repeat_equal.append(bool(out["measured"] == again["measured"]))
    return ProbeResult(endpoint_id=spec["endpoint_id"], routine=routine_name, defect=defect,
                       grid=grid, measured=measured, reference=reference,
                       digests_repeat=digests, repeat_equal=repeat_equal)