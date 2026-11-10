"""``run_uq(solver, config) -> UQResult``: the one entry point of the framework.

All three uncertainty legs are assembled here and nowhere else, so the
composition order is auditable:

1. **numerical** (discretization / iteration / round-off).  Run first because a
   non-converged or pre-asymptotic number invalidates the statistics computed
   from it -- the residual gate is checked *before* the moments are trusted.
2. **input propagation**: Monte Carlo (reference) and/or polynomial chaos
   (cheap surrogate), plus the Sobol' shares, plus the explicit
   PCE-vs-MC agreement audit (``propagation["agreement"]``) that C2 scores.
3. **model form**: multi-model ensemble mean and variance decomposition
   (only if more than one solver is supplied).

The headline ``mean/variance/ci/sobol_indices`` are lifted from the cheapest
*trustworthy* leg (chaos when its hold-out ``R^2`` clears the gate, Monte Carlo
otherwise) and ``headline_method`` records the choice.

``solver`` may be a single callable (input propagation + numerical only) or a
mapping ``{name: callable}``; in the mapping case the first entry named by
``config.model_form["reference_model"]`` (default: first key) is used for the
propagation leg and *all* entries feed the model-form leg.
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional

import numpy as np

from .config import UQConfig
from .marginals import InputDistribution
from .modelform import run_model_form
from .montecarlo import run_monte_carlo
from .numeric import run_numerical_study
from .pce import fit_pce, total_degree_indices
from .result import UQResult, evaluate_criteria

__all__ = ["run_uq", "summarise_agreement"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarise_agreement(mc: Mapping[str, Any], pce: Mapping[str, Any]) -> Dict[str, Any]:
    """Relative PCE-vs-MC differences and the evaluation-cost ratio."""
    out: Dict[str, Any] = {}
    for key in ("mean", "std"):
        a = mc.get(key)
        b = pce.get(key)
        denom = abs(float(a)) if a not in (None, 0) else None
        out[f"{key}_rel_diff"] = (None if (b is None or denom in (None, 0.0))
                                  else float(abs(float(b) - float(a)) / denom))
    n_mc = float(mc.get("n_evals", 0) or 0)
    n_pce = float(pce.get("n_evals", 0) or 0)
    out["eval_speedup"] = float(n_mc / n_pce) if n_pce > 0 else None
    q1 = mc.get("quantiles") or {}
    q2 = pce.get("quantiles") or {}
    shared = sorted(set(q1) & set(q2))
    if shared:
        out["quantile_rel_diff"] = {
            k: (float(abs(q2[k] - q1[k]) / abs(q1[k])) if q1[k] else None) for k in shared
        }
    return out


def run_uq(
    solvers: Any,
    config: UQConfig,
    numerical_evidence: Optional[Mapping[str, Any]] = None,
) -> UQResult:
    """Execute one uncertainty-quantification analysis.

    Parameters
    ----------
    solvers : callable or ``{name: callable}``
        A callable ``f(**inputs) -> float`` (or ``f(X) -> float`` when
        ``config.call_style == "array"``), or a mapping of several such
        callables answering the same question with different model forms.
    config : UQConfig
        Declarative definition (marginals, budgets, criteria, evidence specs).
    numerical_evidence : mapping, optional
        ``{"grid_solutions": [...], "r21":..., "r32":..., "p_expected":...,
        "linear_system": {...}, "roundoff_kernel": f}`` -- overrides/extends
        ``config.numerical`` (config wins only when the argument is absent).
    """
    if not isinstance(config, UQConfig):
        raise TypeError("config must be a UQConfig instance")
    dists: list[InputDistribution] = list(config.input_distributions)
    models: Dict[str, Callable[..., Any]]
    if callable(solvers):
        models = {"reference": solvers}
    elif isinstance(solvers, Mapping) and solvers:
        models = dict(solvers)
    else:
        raise TypeError("solvers must be a callable or a non-empty mapping of callables")
    for name, fn in models.items():
        if not callable(fn):
            raise TypeError(f"solver {name!r} is not callable")

    mf_spec = dict(config.model_form or {})
    ref_name = str(mf_spec.get("reference_model", next(iter(models))))
    if ref_name not in models:
        raise ValueError(f"reference_model {ref_name!r} not among solvers {list(models)}")
    func = models[ref_name]

    # ---- 1. numerical leg ------------------------------------------------
    num_spec = dict(numerical_evidence or config.numerical or {})
    numerical = None
    if num_spec:
        numerical = run_numerical_study(
            grid_solutions=num_spec.get("grid_solutions"),
            r21=float(num_spec.get("r21", 2.0)),
            r32=num_spec.get("r32"),
            p_expected=num_spec.get("p_expected"),
            order_tol=float(num_spec.get("order_tol", config.criteria["gci_order_rel_err_max"])),
            linear_system=num_spec.get("linear_system"),
            residual_tol=float(num_spec.get("residual_tol", 1e-8)),
            roundoff_kernel=num_spec.get("roundoff_kernel"),
        ).as_dict()

    # ---- 2. input propagation -------------------------------------------
    propagation: Dict[str, Any] = {}
    method = config.propagation_method
    if method in ("monte_carlo", "both"):
        propagation["monte_carlo"] = run_monte_carlo(
            func, dists, n=config.n_mc, seed=config.seed, design=config.design,
            quantiles=config.quantiles, ci_level=config.ci_level,
            call_style=config.call_style, sobol=config.sobol,
            sobol_n_base=config.sobol_n_base, sobol_max_order=config.sobol_max_order,
        )
    if method in ("pce", "both"):
        n_terms = total_degree_indices(config.dimension, config.pce_order).shape[0]
        n_train = config.pce_train_size(n_terms)
        pce = fit_pce(func, dists, order=config.pce_order, n_train=n_train,
                      n_holdout=config.pce_holdout, seed=config.seed + 101,
                      call_style=config.call_style)
        payload = pce.as_dict()
        payload["quantiles"] = pce.quantiles(config.quantiles)
        # CI for the *surrogate's* mean estimate: draw the surrogate (free, no
        # model calls) and use the normal-theory standard error of that sample.
        surrogate_draws = pce.sample_output(20000, seed=config.seed + 3)
        se = float(np.std(surrogate_draws, ddof=1) / np.sqrt(surrogate_draws.size))
        centre = float(np.mean(surrogate_draws))
        payload["ci_mean"] = [centre - 1.96 * se, centre + 1.96 * se]
        payload["ci_method"] = "normal_theory_on_surrogate_draws(n=20000)" 
        payload["n_train"] = int(n_train)
        payload["pce_train_factor"] = float(config.pce_train_factor)
        propagation["pce"] = payload
    if "monte_carlo" in propagation and "pce" in propagation:
        propagation["agreement"] = summarise_agreement(propagation["monte_carlo"],
                                                       propagation["pce"])

    # ---- 3. model form ---------------------------------------------------
    model_form = None
    if len(models) > 1:
        model_form = run_model_form(models, dists, mf_spec).as_dict()

    # ---- 4. headline statistics -----------------------------------------
    pce_ok = ("pce" in propagation
              and propagation["pce"].get("r2_holdout", 0.0) >= config.criteria["pce_r2_min"])
    if pce_ok:
        src = propagation["pce"]
        headline = "pce"
    elif "monte_carlo" in propagation:
        src = propagation["monte_carlo"]
        headline = "monte_carlo"
    else:
        src = propagation["pce"]
        headline = "pce"
    sobol = src.get("sobol") or {}
    criteria = evaluate_criteria(config, propagation, numerical, model_form)

    provenance = {
        "generated_at": _now(),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "platform": platform.platform(),
        "config_seed": int(config.seed),
        "framework": "uq_framework/v1",
        "solver_names": list(models),
        "reference_model": ref_name,
        "n_model_evaluations": {
            "monte_carlo": (propagation.get("monte_carlo") or {}).get("n_evals"),
            "pce": (propagation.get("pce") or {}).get("n_evals"),
            "model_form": (model_form or {}).get("n_evals"),
        },
    }

    return UQResult(
        case_id=config.case_id,
        config=config.to_dict(),
        headline_method=headline,
        mean=float(src["mean"]),
        variance=float(src["variance"]),
        std=float(src["std"]),
        ci=list(src.get("ci_mean") or []),
        sobol_indices=sobol or None,
        propagation=propagation,
        numerical=numerical,
        model_form=model_form,
        criteria=criteria,
        provenance=provenance,
        notes=config.notes,
    )