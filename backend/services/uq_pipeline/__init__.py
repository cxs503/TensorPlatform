"""Dual-engine UQ pipeline: PCE + Monte Carlo with cross-validation.

Orchestrates uncertainty quantification using two independent engines:
1. Polynomial Chaos Expansion (PCE) - spectral projection or regression
2. Monte Carlo (MC) - Latin Hypercube Sampling (LHS) or quasi-Monte Carlo

The pipeline cross-validates the two engines and reports discrepancies.
Domain-free: the model under study is injected via an adapter callable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

# Import from existing PCE kernel
from backend.services.pce_uq import (
    PCE,
    CaseSpec,
    MCRun,
    Marginal,
    compare_moments,
    fit,
    monte_carlo,
    marginals_from_spec,
)


@dataclass
class UQResult:
    """Aggregated result from the dual-engine UQ pipeline."""
    # PCE results
    pce_mean: float
    pce_std: float
    pce_quantiles: Dict[str, float]
    pce_n_evaluations: int
    pce_wall_seconds: float
    # MC results
    mc_mean: float
    mc_std: float
    mc_quantiles: Dict[str, float]
    mc_n_evaluations: int
    mc_wall_seconds: float
    # Cross-validation
    mean_relative_error: float
    std_relative_error: float
    speedup_model_evaluations: float
    speedup_wall_clock: float
    # Optional fields
    pce_sobol: Optional[Dict[str, Any]] = None
    case_id: str = ""
    output_name: str = ""
    converged: bool = True

    def as_dict(self) -> Dict[str, Any]:
        out = {
            "case_id": self.case_id,
            "output_name": self.output_name,
            "pce": {
                "mean": self.pce_mean,
                "std": self.pce_std,
                "quantiles": self.pce_quantiles,
                "n_evaluations": self.pce_n_evaluations,
                "wall_seconds": self.pce_wall_seconds,
                "sobol": self.pce_sobol,
            },
            "mc": {
                "mean": self.mc_mean,
                "std": self.mc_std,
                "quantiles": self.mc_quantiles,
                "n_evaluations": self.mc_n_evaluations,
                "wall_seconds": self.mc_wall_seconds,
            },
            "cross_validation": {
                "mean_relative_error": self.mean_relative_error,
                "std_relative_error": self.std_relative_error,
                "speedup_model_evaluations": self.speedup_model_evaluations,
                "speedup_wall_clock": self.speedup_wall_clock,
            },
            "converged": self.converged,
        }
        return out


class DualEnginePipeline:
    """Dual-engine UQ pipeline: PCE + MC with cross-validation.

    Parameters
    ----------
    case_spec : CaseSpec or dict
        Case specification (JSON-serializable dict or CaseSpec object).
    model_func : callable or None
        The model under study: f(**kwargs) -> float. If None, resolved from case_spec.
    """

    def __init__(
        self,
        case_spec: Any,
        model_func: Optional[Callable] = None,
    ) -> None:
        if isinstance(case_spec, dict):
            self._spec = CaseSpec.from_dict(case_spec)
        else:
            self._spec = case_spec
        self._func = model_func or self._spec.resolve_model()
        self._marginals = marginals_from_spec(self._spec.inputs)

    def run(
        self,
        pce_method: str = "projection",
        pce_order: int = 5,
        pce_q_norm: float = 1.0,
        mc_n: int = 10000,
        mc_sampler: str = "lhs",
        mc_seed: int = 12345,
        surrogate_draws: int = 20000,
        compute_sobol: bool = True,
    ) -> UQResult:
        """Execute the dual-engine pipeline.

        Parameters
        ----------
        pce_method : str
            PCE fitting method: "projection" or "regression".
        pce_order : int
            Polynomial order for PCE.
        pce_q_norm : float
            Hyperbolic truncation norm (1.0 = full tensor).
        mc_n : int
            Number of Monte Carlo samples.
        mc_sampler : str
            MC sampling method: "lhs", "qmc", or "mc".
        mc_seed : int
            Random seed for MC.
        surrogate_draws : int
            Number of samples from PCE surrogate for comparison.
        compute_sobol : bool
            Whether to compute Sobol indices from PCE.

        Returns
        -------
        UQResult
            Aggregated results from both engines.
        """
        # --- PCE engine ---
        t0 = time.perf_counter()
        pce = fit(
            func=self._func,
            marginals=self._marginals,
            method=pce_method,
            order=int(pce_order),
            q_norm=float(pce_q_norm),
        )
        pce_dt = time.perf_counter() - t0
        pce_mean = float(pce.mean)
        pce_std = float(pce.std)
        pce_quantiles = pce.quantiles(probs=[0.05, 0.5, 0.95], n=surrogate_draws, seed=mc_seed)
        pce_sobol = pce.sobol() if compute_sobol else None

        # --- MC engine ---
        mc = monte_carlo(
            func=self._func,
            marginals=self._marginals,
            n=int(mc_n),
            sampler=mc_sampler,
            seed=int(mc_seed),
        )
        mc_mean = float(mc.mean)
        mc_std = float(mc.std)
        mc_quantiles = {k: float(v) for k, v in mc.quantiles.items()}

        # --- Cross-validation ---
        def rel_err(a: float, b: float) -> float:
            return abs(a - b) / abs(b) if b != 0 else abs(a - b)

        mean_rel = rel_err(pce_mean, mc_mean)
        std_rel = rel_err(pce_std, mc.std)
        speedup_eval = float(mc.n_model_evaluations) / max(pce.n_model_evaluations, 1)
        speedup_wall = float(mc.wall_seconds) / max(pce.fit_seconds, 1e-12)

        # Convergence check: mean error <= 1%, std error <= 2%
        converged = (mean_rel <= 0.01) and (std_rel <= 0.02)

        return UQResult(
            pce_mean=pce_mean,
            pce_std=pce_std,
            pce_quantiles=pce_quantiles,
            pce_n_evaluations=pce.n_model_evaluations,
            pce_wall_seconds=pce.fit_seconds,
            pce_sobol=pce_sobol,
            mc_mean=mc_mean,
            mc_std=mc_std,
            mc_quantiles=mc_quantiles,
            mc_n_evaluations=mc.n_model_evaluations,
            mc_wall_seconds=mc.wall_seconds,
            mean_relative_error=mean_rel,
            std_relative_error=std_rel,
            speedup_model_evaluations=speedup_eval,
            speedup_wall_clock=speedup_wall,
            case_id=self._spec.case_id,
            output_name=self._spec.model.output if hasattr(self._spec.model, "output") else "",
            converged=converged,
        )


def run_pipeline_from_spec(
    spec_dict: Dict[str, Any],
    model_func: Optional[Callable] = None,
    **kwargs,
) -> UQResult:
    """Convenience wrapper: build pipeline from dict and run."""
    pipeline = DualEnginePipeline(case_spec=spec_dict, model_func=model_func)
    return pipeline.run(**kwargs)


__all__ = [
    "DualEnginePipeline",
    "UQResult",
    "run_pipeline_from_spec",
]
