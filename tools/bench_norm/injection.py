"""Ground-truth injection layer (declared simulation, not measurement).

Purpose: the statistical machinery of `alpha.py` / `mixed_effects.py` must be
validated against a world whose true architecture factor is known, because a
real x86_64 host is not available in this execution environment (see the
report's honest-status section).  Injected records carry `source="injected"`
and are never mixed with measured ones in any reported number.

Generative model (log scale),

    log T = mu + log(alpha_arm) + delta_workload + x.beta + u_host + eps
    u_host ~ N(0, sigma_u^2),  eps ~ N(0, sigma_e^2)

with the crucial identifiability rule: `delta` (the genuine implementation
difference that normalisation is supposed to *expose*) is applied only to
workloads whose `role` is "evaluation".  Workloads of role "calibration"
carry delta = 0 by construction, because alpha_arch is defined on workloads
where both architectures run the *same* code.  Estimating alpha on workloads
that also carry delta would make alpha and delta statistically confounded
(alpha_hat would silently absorb the very effect the framework is meant to
measure) -- this is the trap documented in the task report.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

import numpy as np

from .schema import Observation


def _grid(workloads: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Expand {name, role, grid: {feature: [values...]}} into one entry per cell."""
    out: List[Dict[str, Any]] = []
    for w in workloads:
        role = w.get("role", "evaluation")
        names = list(w["grid"].keys())
        combos = [{}]
        for n in names:
            combos = [dict(c, **{n: float(v)}) for c in combos for v in w["grid"][n]]
        for c in combos:
            label = "_".join(f"{n}{c[n]:g}" for n in names)
            out.append({"workload": f"{w['name']}:{label}", "role": role, "features": c})
    return out


def generate(scenario: Dict[str, Any], seed: int = 0) -> List[Dict[str, Any]]:
    """Return injected observations for one Monte-Carlo replicate."""
    rng = np.random.default_rng(seed)
    arms = scenario["arms"]
    feature_names = list(scenario["feature_names"])
    beta = np.asarray(scenario["beta"], dtype=float)          # [intercept, *coefs]
    sigma_e = float(scenario.get("sigma_e", 0.05))
    sigma_u = float(scenario.get("sigma_u", 0.02))
    reps = int(scenario.get("reps", 9))
    deltas = scenario.get("delta_true", {})                   # arm -> log residual (evaluation only)
    grid = _grid(scenario["workloads"])
    if not grid:
        raise ValueError("empty workload grid")

    records: List[Observation] = []
    for arm in arms:
        name, isa = arm["arm"], arm["isa"]
        log_alpha = math.log(float(arm.get("alpha_true", 1.0)))
        hosts = list(arm.get("hosts", [arm.get("host", name)]))
        for host in hosts:
            u_h = float(rng.normal(scale=sigma_u))
            for g in grid:
                delta = float(deltas.get(name, 0.0)) if g["role"] == "evaluation" else 0.0
                x = [1.0] + [math.log10(g["features"][n]) for n in feature_names]
                mu = log_alpha + delta + float(np.dot(x, beta)) + u_h
                for r in range(reps):
                    t = math.exp(mu + float(rng.normal(scale=sigma_e)))
                    records.append(Observation(
                        arm=name, isa=isa, host=str(host), workload=g["workload"],
                        features=dict(g["features"]), repetition=r,
                        t_seconds=t, source="injected",
                        meta={"scenario": scenario.get("name", "unnamed"), "seed": seed,
                              "role": g["role"],
                              "alpha_true": float(arm.get("alpha_true", 1.0)),
                              "delta_true_log": delta,
                              "log_alpha": log_alpha, "u_host": u_h},
                    ))
    return [r.to_dict() for r in records]


def true_alpha(scenario: Dict[str, Any], arm: str) -> float:
    for a in scenario["arms"]:
        if a["arm"] == arm:
            return float(a.get("alpha_true", 1.0))
    raise KeyError(arm)


def true_delta_pct(scenario: Dict[str, Any], arm: str) -> float:
    d = float(scenario.get("delta_true", {}).get(arm, 0.0))
    return (math.exp(d) - 1.0) * 100.0


def true_difference_pct(scenario: Dict[str, Any], arm: str) -> float:
    """Un-normalised arm-vs-ref difference implied by the generator (eval workload)."""
    a = true_alpha(scenario, arm)
    d = float(scenario.get("delta_true", {}).get(arm, 0.0))
    return (a * math.exp(d) - 1.0) * 100.0