"""Direct Monte Carlo reference and surrogate-vs-Monte-Carlo cost accounting.

The comparison this module formalises is the one an engineer actually cares
about: "with the PCE surrogate I get the mean / standard deviation / band in
seconds -- how many black-box runs did that cost, and how large is the error
against just brute-forcing it?"

Two acceleration numbers are reported, never mixed:

``speedup_model_evaluations``
    ``n_mc / n_pce`` -- the number of black-box runs avoided.  This is the
    cost-model-free number that matters when the evaluation is an expensive
    solver, and it is the primary criterion of the mission.
``speedup_wall_clock``
    ``t_mc / t_pce`` measured on the *same* machine with the *same* scalar
    callable interface.  For a cheap closed-form model the surrogate's own
    setup cost (quadrature grid + projection solve) is not negligible, so this
    number is expected to be smaller; it is reported as measured, never
    inflated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

import numpy as np

from .kernel import PCE, evaluate_scalar_model, make_unit_sample
from .marginals import Marginal


@dataclass
class MCRun:
    """Direct Monte Carlo estimate of the output statistics."""

    mean: float
    std: float
    quantiles: Dict[str, float]
    n_model_evaluations: int
    wall_seconds: float
    sampler: str
    seed: int
    samples: np.ndarray

    @property
    def standard_error_mean(self) -> float:
        n = max(self.n_model_evaluations, 1)
        return float(self.std / np.sqrt(n))

    def as_dict(self, keep_samples: bool = False) -> Dict[str, Any]:
        out = {
            "mean": float(self.mean),
            "std": float(self.std),
            "std_error_of_mean": self.standard_error_mean,
            "quantiles": {k: float(v) for k, v in self.quantiles.items()},
            "n_model_evaluations": int(self.n_model_evaluations),
            "wall_seconds": float(self.wall_seconds),
            "sampler": self.sampler,
            "seed": int(self.seed),
        }
        if keep_samples:
            out["samples"] = [float(v) for v in self.samples]
        return out


def monte_carlo(
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    n: int = 10000,
    sampler: str = "lhs",
    seed: int = 12345,
    output: Optional[str] = None,
    probs: Sequence[float] = (0.05, 0.5, 0.95),
    call_style: str = "kwargs",
    fixed: Optional[Dict[str, float]] = None,
) -> MCRun:
    """Direct Monte Carlo / LHS estimate of the output statistics (reference)."""
    marginals = tuple(marginals)
    t0 = time.perf_counter()
    U = make_unit_sample(int(n), len(marginals), method=sampler, seed=seed)
    X = np.column_stack([m.from_unit(U[:, j]) for j, m in enumerate(marginals)])
    Y = evaluate_scalar_model(func, X, [m.name for m in marginals], output=output,
                              call_style=call_style, fixed=fixed)
    dt = time.perf_counter() - t0
    return MCRun(
        mean=float(np.mean(Y)),
        std=float(np.std(Y, ddof=1)),
        quantiles={f"p{100 * p:g}": float(np.quantile(Y, p)) for p in probs},
        n_model_evaluations=int(n),
        wall_seconds=float(dt),
        sampler=str(sampler),
        seed=int(seed),
        samples=np.asarray(Y, dtype=float),
    )


def compare_moments(
    pce: PCE,
    mc: MCRun,
    reference: Optional[MCRun] = None,
    surrogates: int = 20000,
    seed: int = 7,
) -> Dict[str, Any]:
    """Error of the surrogate statistics against Monte Carlo.

    ``mc`` is the brute-force baseline (the budget the surrogate has to beat);
    ``reference`` -- when supplied -- is a much larger run used to separate the
    surrogate's own discretisation error from the sampling error of the
    baseline itself.
    """
    y_pce = pce.sample_output(n=surrogates, seed=seed)
    pce_mean = float(np.mean(y_pce))
    pce_std = float(np.std(y_pce, ddof=1))
    spectral_mean, spectral_std = pce.mean, pce.std

    def rel(a: float, b: float) -> float:
        return float(abs(a - b) / abs(b)) if b else float("inf")

    out: Dict[str, Any] = {
        "pce": {
            "mean_sampled": pce_mean,
            "std_sampled": pce_std,
            "mean_spectral": spectral_mean,
            "std_spectral": spectral_std,
            "n_surrogate_draws": int(surrogates),
            "n_model_evaluations": int(pce.n_model_evaluations),
            "wall_seconds": float(pce.fit_seconds),
        },
        "mc_baseline": mc.as_dict(),
        "error_vs_mc_baseline": {
            "mean_rel": rel(pce_mean, mc.mean),
            "std_rel": rel(pce_std, mc.std),
            "spectral_mean_rel": rel(spectral_mean, mc.mean),
            "spectral_std_rel": rel(spectral_std, mc.std),
        },
        "speedup_model_evaluations": float(mc.n_model_evaluations / max(pce.n_model_evaluations, 1)),
        "speedup_wall_clock": float(mc.wall_seconds / max(pce.fit_seconds, 1e-12)),
        "mc_sampling_error": {
            "std_error_of_mean": mc.standard_error_mean,
            "std_error_of_std_rel": float(1.0 / np.sqrt(2.0 * max(mc.n_model_evaluations - 1, 1))),
        },
    }
    if reference is not None:
        out["error_vs_high_accuracy_reference"] = {
            "mean_rel": rel(pce_mean, reference.mean),
            "std_rel": rel(pce_std, reference.std),
            "mc_baseline_mean_rel": rel(mc.mean, reference.mean),
            "mc_baseline_std_rel": rel(mc.std, reference.std),
            "reference": reference.as_dict(),
        }
    return out