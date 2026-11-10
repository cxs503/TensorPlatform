"""G-OPT benchmark validation: CMA-ES and SLSQP against closed-form optima.

Run:  python -m optimizer.gopt.validation.run_benchmarks
Out:  artifacts/gopt/benchmarks.json + artifacts/gopt/fig5_benchmark_errors.png

Protocol
--------
* CMA-ES: 5 seeds per unconstrained benchmark; success = ``f <= f* + f_tol``.
* SLSQP: 5 starting points per benchmark (optimum + a perturbation); success uses
  the same criterion, and every run must satisfy the constraints.
* scipy's SLSQP is used as an *independent reference implementation* for the
  constrained cases (same starting points); the agreement is reported, it is not
  used as the ground truth -- the ground truth is the analytic optimum.
* No assertion is of the form "the optimizer improved the objective": every
  success criterion compares against ``optimum_f`` from
  :mod:`optimizer.gopt.benchmarks`.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

import numpy as np

from .. import benchmarks as B
from ..cmaes import CMAESOptimizer
from ..interfaces import InjectedAdjointObjective
from ..adjoint_reference import FunctionAdjointSolver
from ..slsqp import SLSQPOptimizer

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUTDIR = os.path.join(_REPO_ROOT, "artifacts", "gopt")

CMA_BUDGET = {
    "sphere2": 2000, "sphere10": 3000, "ellipsoid5": 4000, "cigar5": 20000,
    "tablet5": 20000, "rosenbrock2": 4000, "rosenbrock5": 12000, "rastrigin5": 24000,
}
CMA_RESTARTS = {"cigar5": 3, "tablet5": 3, "rosenbrock5": 3, "rastrigin5": 4}


def run_cmaes(seeds: int = 5) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for key in ["sphere2", "sphere10", "ellipsoid5", "cigar5", "tablet5",
                "rosenbrock2", "rosenbrock5", "rastrigin5"]:
        p = B.BENCHMARKS[key]
        runs: List[Dict] = []
        for seed in range(seeds):
            res = CMAESOptimizer(
                dim=p.dim,
                bounds=p.bounds,
                seed=seed,
                max_evals=CMA_BUDGET[key],
                restarts=CMA_RESTARTS.get(key, 0),
            ).optimize(p.fun)
            runs.append(
                {
                    "seed": seed,
                    "f": float(res.fun),
                    "abs_err_f": float(res.fun - p.optimum_f),
                    "x_err_inf": float(np.max(np.abs(res.x - p.optimum_x))),
                    "n_eval": int(res.n_eval),
                    "termination": res.termination,
                    "success": bool(res.fun <= p.optimum_f + p.f_tol_abs),
                }
            )
        out[key] = {
            "dim": p.dim,
            "optimum_f": p.optimum_f,
            "f_tol_abs": p.f_tol_abs,
            "success_rate": float(np.mean([r["success"] for r in runs])),
            "median_abs_err_f": float(np.median([r["abs_err_f"] for r in runs])),
            "median_x_err_inf": float(np.median([r["x_err_inf"] for r in runs])),
            "median_n_eval": float(np.median([r["n_eval"] for r in runs])),
            "runs": runs,
        }
    return out


def run_slsqp(starts: int = 5, scipy_cross_check: bool = True) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    try:
        from scipy.optimize import minimize as scipy_minimize
    except Exception:  # pragma: no cover
        scipy_minimize = None

    for key in ["sphere2", "rosenbrock2", "eq_quadratic", "nocedal_17_3",
                "box_quadratic", "eq_ineq_quadratic"]:
        p = B.BENCHMARKS[key]
        obj = InjectedAdjointObjective(FunctionAdjointSolver(p.fun, p.jac, dim=p.dim, name=key))
        runs: List[Dict] = []
        rng = np.random.default_rng(7)
        for k in range(starts):
            x0 = p.optimum_x + (0.4 + 0.1 * k) * (1.0 if k % 2 == 0 else -1.0) * rng.uniform(
                0.5, 1.0, size=p.dim
            )
            res = SLSQPOptimizer(bounds=p.bounds, max_iter=200).optimize(obj, x0=x0,
                                                                       constraints=p.constraints)
            rec = {
                "start": x0.tolist(),
                "f": float(res.fun),
                "abs_err_f": float(res.fun - p.optimum_f),
                "x_err_inf": float(np.max(np.abs(res.x - p.optimum_x))),
                "nit": int(res.nit),
                "success": bool(res.success and res.constraint_violation <= 1e-7),
                "violation": float(res.constraint_violation),
            }
            if scipy_minimize is not None:
                cons = []
                if p.constraints is not None and p.constraints.ineq is not None:
                    cons.append({"type": "ineq", "fun": p.constraints.ineq,
                                 "jac": p.constraints.ineq_jac})
                if p.constraints is not None and p.constraints.eq is not None:
                    cons.append({"type": "eq", "fun": p.constraints.eq,
                                 "jac": p.constraints.eq_jac})
                ref = scipy_minimize(p.fun, x0, jac=p.jac, constraints=cons, method="SLSQP",
                                     bounds=p.bounds, options={"ftol": 1e-12, "maxiter": 400})
                rec["scipy_f"] = float(ref.fun)
                rec["scipy_agreement_abs"] = abs(float(ref.fun) - float(res.fun))
                # relative agreement with a floor: several benchmarks have f* = 0,
                # where a pure relative metric is dominated by round-off.
                rec["scipy_agreement_rel"] = rec["scipy_agreement_abs"] / max(
                    1e-8, abs(float(ref.fun))
                )
            runs.append(rec)
        out[key] = {
            "dim": p.dim,
            "optimum_f": p.optimum_f,
            "constrained": p.constraints is not None,
            "bounded": p.bounds is not None,
            "success_rate": float(np.mean([r["success"] for r in runs])),
            "median_abs_err_f": float(np.median([r["abs_err_f"] for r in runs])),
            "median_x_err_inf": float(np.median([r["x_err_inf"] for r in runs])),
            "median_scipy_agreement_rel": (
                float(np.median([r["scipy_agreement_rel"] for r in runs]))
                if scipy_minimize is not None else None
            ),
            "runs": runs,
        }
    return out


def run_noise_robustness(seeds: int = 5) -> Dict[str, Dict]:
    """Noisy sphere: does re-sampling keep the design-parameter error bounded?"""
    out: Dict[str, Dict] = {}
    dim = 3
    target = np.zeros(dim)
    for resample in (1, 5):
        errs = []
        for seed in range(seeds):
            rng = np.random.default_rng(1000 + seed)

            def noisy(x, rng=rng):
                return float(np.sum((x - target) ** 2) + 0.05 * rng.standard_normal())

            res = CMAESOptimizer(dim=dim, seed=seed, max_evals=2000, n_resample=resample).optimize(noisy)
            errs.append(float(np.linalg.norm(res.x - target)))
        out[f"n_resample={resample}"] = {
            "median_x_err_L2": float(np.median(errs)),
            "max_x_err_L2": float(np.max(errs)),
            "errors": errs,
        }
    return out


def make_figure(cma: Dict, sls: Dict, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    keys_c = list(cma)
    keys_s = list(sls)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    ax[0].barh(keys_c, [max(cma[k]["median_abs_err_f"], 1e-18) for k in keys_c], color="C2")
    ax[0].set_xscale("log")
    ax[0].set_xlabel(r"median $|f - f^\ast|$ over 5 seeds")
    ax[0].set_title("CMA-ES (derivative-free)")
    ax[0].grid(alpha=0.3, axis="x")
    ax[1].barh(keys_s, [max(sls[k]["median_abs_err_f"], 1e-18) for k in keys_s], color="C4")
    ax[1].set_xscale("log")
    ax[1].set_xlabel(r"median $|f - f^\ast|$ over 5 starts")
    ax[1].set_title("SLSQP (gradient callback)")
    ax[1].grid(alpha=0.3, axis="x")
    fig.suptitle("G-OPT kernels vs closed-form optima (log scale, lower is better)")
    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> Dict:
    os.makedirs(OUTDIR, exist_ok=True)
    cma = run_cmaes()
    sls = run_slsqp()
    noise = run_noise_robustness()
    summary = {
        "cmaes": {k: {kk: vv for kk, vv in v.items() if kk != "runs"} for k, v in cma.items()},
        "slsqp": {k: {kk: vv for kk, vv in v.items() if kk != "runs"} for k, v in sls.items()},
        "noise_robustness": noise,
        "raw": {"cmaes": cma, "slsqp": sls},
        "overall": {
            "cmaes_mean_success_rate": float(np.mean([v["success_rate"] for v in cma.values()])),
            "slsqp_mean_success_rate": float(np.mean([v["success_rate"] for v in sls.values()])),
            "slsqp_worst_scipy_agreement_rel": max(
                (v["median_scipy_agreement_rel"] or 0.0) for v in sls.values()
            ),
        },
    }
    fig_path = os.path.join(OUTDIR, "fig5_benchmark_errors.png")
    make_figure(cma, sls, fig_path)
    summary["figure"] = os.path.relpath(fig_path, _REPO_ROOT)
    with open(os.path.join(OUTDIR, "benchmarks.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    return summary


if __name__ == "__main__":
    s = main()
    print(json.dumps(s["overall"], indent=2))
    print(json.dumps(s["noise_robustness"], indent=2))