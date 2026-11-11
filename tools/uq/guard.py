"""``tools.uq.guard`` -- mechanical guards and acceptance criteria plumbing.

Two guards protect every number the UQ engine publishes:

``surrogate_guard``
    The leave-one-out error of the chaos surrogate, normalised by the response
    spread, must stay below ``loo_tol`` (default 5%).  Above that the surrogate
    is not a trustworthy stand-in for the black box.

``degree_guard``
    Between successive chaos degrees both the mean and the variance must move by
    less than ``drift_tol`` (default 1%).  If they still move, the expansion has
    not converged at the highest degree tried and the honest answer is
    *"degree insufficient"*, not the last number computed.

``criterion`` / ``all_pass``
    Small helpers that turn a number plus a threshold into the auditable
    ``{status, value, threshold, detail}`` record the reports quote.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from .mc import compare_moments
from .pce import DEFAULT_DRIFT_TOL, DEFAULT_LOO_TOL

__all__ = [
    "criterion",
    "all_pass",
    "surrogate_guard",
    "degree_guard",
    "moment_guard",
    "sensitivity_guard",
    "DEFAULT_LOO_TOL",
    "DEFAULT_DRIFT_TOL",
]


def criterion(name: str, value: Optional[float], threshold: float, unit: str = "-",
              detail: str = "", passed: Optional[bool] = None) -> Dict[str, Any]:
    """One auditable acceptance record."""
    if passed is None:
        passed = bool(value is not None and value <= threshold)
    return {
        "name": name,
        "value": None if value is None else float(value),
        "threshold": float(threshold),
        "unit": unit,
        "status": "PASS" if passed else "FAIL",
        "detail": detail or f"{'' if value is None else f'{value:.6g}'} vs {threshold:.6g}",
    }


def all_pass(criteria: Sequence[Dict[str, Any]]) -> bool:
    return all(c["status"] == "PASS" for c in criteria)


def surrogate_guard(fit, tol_loo: float = DEFAULT_LOO_TOL) -> Dict[str, Any]:
    """LOO-error guard on a fitted surrogate."""
    from .pce import leave_one_out

    loo = leave_one_out(fit)
    rec = criterion("surrogate_loo_error", loo["loo_rel"], tol_loo, "fraction",
                    detail=(f"degree {fit.degree}, {fit.multi_index.shape[0]} basis terms, "
                            f"{fit.n_samples} samples, LOO RMSE {loo['loo_rmse']:.6g}"))
    rec["loo"] = loo
    return rec


def degree_guard(study: Dict[str, Any], tol_drift: float = DEFAULT_DRIFT_TOL) -> Dict[str, Any]:
    """Convergence guard on a degree-refinement study."""
    rec = criterion("mean_drift_between_degrees", study["mean_drift"], tol_drift, "fraction",
                    detail=(f"degrees {study['degrees']}; variance drift "
                            f"{study['variance_drift']:.6g}; verdict "
                            f"{study['status']}"))
    rec["status_engine"] = study["status"]
    rec["reasons"] = study["reasons"]
    return rec


def moment_guard(pce_moments: Dict[str, float], mc: Dict[str, Any],
                 mean_tol: float = 0.01, variance_tol: float = 0.03) -> List[Dict[str, Any]]:
    """PCE moments vs the Monte Carlo reference, with relative-error criteria."""
    cmp = compare_moments(pce_moments, mc)
    return [
        criterion("pce_mean_vs_mc_rel_error", cmp["mean_rel_error"], mean_tol, "fraction",
                  detail=(f"PCE {pce_moments['mean']:.6g} vs MC {mc['mean']:.6g} "
                          f"(z={cmp['mean_z']:.2f} MC standard errors)")),
        criterion("pce_variance_vs_mc_rel_error", cmp["variance_rel_error"], variance_tol,
                  "fraction",
                  detail=(f"PCE {pce_moments['variance']:.6g} vs MC {mc['variance']:.6g} "
                          f"(z={cmp['variance_z']:.2f} MC standard errors)")),
    ]


def sensitivity_guard(pce_sobol: Dict[str, Dict[str, float]],
                      reference: Dict[str, Dict[str, float]],
                      tol: float = 0.05) -> Dict[str, Any]:
    """Element-wise absolute error of the PCE Sobol' indices against a reference."""
    worst_first = max(abs(pce_sobol["first_order"][k] - reference["first_order"][k])
                      for k in reference["first_order"])
    worst_total = max(abs(pce_sobol["total_order"][k] - reference["total_order"][k])
                      for k in reference["total_order"])
    worst = max(worst_first, worst_total)
    return {
        "criterion": criterion("sobol_max_abs_error", worst, tol, "index",
                               detail=(f"max |dS1|={worst_first:.4f}, "
                                       f"max |dST|={worst_total:.4f}")),
        "max_first_error": float(worst_first),
        "max_total_error": float(worst_total),
    }