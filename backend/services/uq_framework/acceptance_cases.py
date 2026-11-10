"""The nine self-verification cases of framework v1, shared by tests and runner.

Test parity matters: if the acceptance runner and the pytest suite built their
cases separately, a passing suite would say nothing about the published artifacts.
So the case list lives here once, is domain-free (inputs are ``x0..xn``), and is
consumed by both ``tests/test_uq_framework_e2e.py`` and
``tools/uq_framework/run_acceptance.py``.

Each case returns ``(solvers, config, expected_pass_keys, reference)`` where
``reference`` carries the independent truth used only for reporting (analytic
moments, formal order, or the truth model of the ensemble).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from .config import UQConfig
from .marginals import InputDistribution
from .pce import latin_hypercube
from .synthetic import (
    diffusion_bvp,
    linear_response,
    model_ensemble,
    polynomial_response,
    quadratic_response,
    relaxation,
)

__all__ = [
    "DISTS_NU",
    "DISTS_TN",
    "DISTS_3",
    "INPUT_CASES",
    "NUMERIC_CASES",
    "MODEL_FORM_CASES",
    "all_cases",
    "validation_set",
    "truncation_truth_reference",
    "convergence_study",
    "model_form_robustness",
]

DISTS_NU = [InputDistribution.normal("x0", 0.0, 1.0),
            InputDistribution.uniform("x1", -1.0, 1.0)]
DISTS_TN = [InputDistribution.triangular("x0", 0.0, 1.0, 4.0),
            InputDistribution.normal("x1", 2.0, 0.5)]
DISTS_3 = [InputDistribution.normal("x0", 0.0, 1.0),
           InputDistribution.uniform("x1", -1.0, 1.0),
           InputDistribution.triangular("x2", 0.0, 1.0, 4.0)]

INPUT_PASS = ["C1_mc_mean", "C2_pce_mean", "C2_pce_std", "C2_pce_speedup", "C2_pce_r2"]
NUMERIC_PASS = ["C3_gci_order"]
MODEL_FORM_PASS = ["C4_model_form"]


def validation_set(ens: Dict[str, Any], dists: Sequence[InputDistribution],
                   n_points: int = 32, seed: int = 5,
                   meas_error: float = 0.005) -> Dict[str, Any]:
    """Synthetic validation data: truth plus a small relative measurement error."""
    U = latin_hypercube(n_points, len(dists), seed)
    X = np.column_stack([d.from_unit(U[:, j]) for j, d in enumerate(dists)])
    kw = {d.name: X[:, j] for j, d in enumerate(dists)}
    y_true = np.asarray(ens["truth"](**kw), dtype=float)
    rng = np.random.default_rng(seed + 1)
    y_meas = y_true + rng.normal(0.0, meas_error * np.abs(y_true).mean(), y_true.shape)
    return {"truth": y_meas,
            "models": {k: np.asarray(m(**kw), dtype=float) for k, m in ens["models"].items()}}


def truncation_truth_reference() -> Tuple[Any, Dict[str, Any]]:
    """Exact moments of the truncation ensemble's truth ``x0^2 + 0.5 x0 x1 + x2``."""
    func, meta = polynomial_response({(2, 0, 0): 1.0, (1, 1, 0): 0.5, (0, 0, 1): 1.0},
                                     ["x0", "x1", "x2"])
    return func, meta["reference"](DISTS_3)


def _input_case(case_id: str, func, meta, dists, order: int):
    ref = meta["reference"](dists)
    cfg = UQConfig(case_id, dists, propagation_method="both", n_mc=1000, pce_order=order,
                   pce_train_factor=2.5, pce_holdout=48, sobol=True, sobol_n_base=512,
                   seed=3, reference={"mean": ref["mean"], "std": ref["std"]})
    return func, cfg, INPUT_PASS, ref


def _numeric_case(case_id: str, qoi_of_n, levels, p_expected):
    vals = [qoi_of_n(n) for n in levels]
    cfg = UQConfig(case_id, DISTS_NU, propagation_method="monte_carlo", n_mc=200,
                   sobol=False, numerical={"grid_solutions": vals, "r21": 2.0,
                                           "r32": 2.0, "p_expected": p_expected})
    solver = lambda x0, x1: 1.0 + 0.0 * np.asarray(x0) + 0.0 * np.asarray(x1)  # noqa: E731
    return solver, cfg, NUMERIC_PASS, {"p_expected": p_expected, "grid": vals,
                                       "levels": list(levels)}


def _model_form_case(case_id: str, kind: str, dists, n: int = 600,
                     weights: str = "bma"):
    """Model-form case.  ``weights`` is part of the case, not a default.

    ``bma`` is used where at least one member is a credible explanation of the
    validation data.  For the ``competing_biases`` ensemble *every* member is
    wrong and the validation set is nearly noiseless, so BMA degenerates into
    model *selection* (one weight -> 1) and can be *worse* than plain averaging;
    that case therefore declares ``equal`` weights, and the degeneracy itself is
    pinned by a separate test as a documented limitation.
    """
    ens = model_ensemble(kind, seed=0)
    cfg = UQConfig(case_id, dists, propagation_method="monte_carlo", n_mc=n, sobol=False,
                   model_form={"weights": weights, "validation": validation_set(ens, dists),
                               "truth": ens["truth"], "reference_model": "exact"
                               if "exact" in ens["models"] else next(iter(ens["models"])),
                               "n_samples": n})
    return ens["models"], cfg, MODEL_FORM_PASS, {"kind": kind}


INPUT_CASES = {
    "in_linear_normal_uniform": lambda: _input_case(
        "in_linear_normal_uniform", *linear_response([2.0, 3.0], 1.0), DISTS_NU, 2),
    "in_linear_triangular_normal": lambda: _input_case(
        "in_linear_triangular_normal", *linear_response([1.0, 2.0], 0.0), DISTS_TN, 2),
    "in_quadratic_interaction": lambda: _input_case(
        "in_quadratic_interaction", *quadratic_response(), DISTS_3, 2),
}

NUMERIC_CASES = {
    "num_euler_p1": lambda: _numeric_case(
        "num_euler_p1", lambda n: relaxation(n, "euler")["qoi"], (80, 40, 20), 1.0),
    "num_elliptic_p2": lambda: _numeric_case(
        "num_elliptic_p2", lambda n: diffusion_bvp(n)["qoi"], (80, 40, 20), 2.0),
    "num_rk4_p4": lambda: _numeric_case(
        "num_rk4_p4", lambda n: relaxation(n, "rk4")["qoi"], (80, 40, 20), 4.0),
}

DISTS_2 = DISTS_NU

MODEL_FORM_CASES = {
    "mf_affine": lambda: _model_form_case("mf_affine", "affine", DISTS_NU),
    "mf_truncation": lambda: _model_form_case("mf_truncation", "truncation", DISTS_3),
    "mf_common_bias": lambda: _model_form_case("mf_common_bias", "uniform_bias", DISTS_NU),
    "mf_competing_biases": lambda: _model_form_case("mf_competing_biases",
                                                    "competing_biases", DISTS_NU,
                                                    weights="equal"),
}


def all_cases() -> Dict[str, Dict[str, Any]]:
    """The full 3x3 acceptance matrix, keyed by case id."""
    return {"input_propagation": INPUT_CASES, "numerical_error": NUMERIC_CASES,
            "model_form": MODEL_FORM_CASES}


def full_matrix_case() -> Tuple[Dict[str, Any], UQConfig, Dict[str, Any]]:
    """One case that carries all three legs at once (report / integration check)."""
    from .synthetic import model_ensemble as _ensemble

    ens = _ensemble("truncation", seed=0)
    _, ref = truncation_truth_reference()
    vals = [diffusion_bvp(n)["qoi"] for n in (80, 40, 20)]
    sol = diffusion_bvp(80)
    cfg = UQConfig(
        "full_matrix_case", DISTS_3, propagation_method="both", n_mc=1000, pce_order=2,
        pce_train_factor=2.5, pce_holdout=48, sobol=True, sobol_n_base=512,
        reference={"mean": ref["mean"], "std": ref["std"]},
        numerical={"grid_solutions": vals, "r21": 2.0, "r32": 2.0, "p_expected": 2.0,
                   "linear_system": {"A": sol["A"], "x": sol["u"], "b": sol["b"]},
                   "roundoff_kernel": lambda dt: diffusion_bvp(80, dt)["u_mid"]},
        model_form={"weights": "bma", "validation": validation_set(ens, DISTS_3),
                    "truth": ens["truth"], "n_samples": 600,
                    "reference_model": "exact"},
    )
    return dict(ens["models"]), cfg, ens


def model_form_robustness(kind: str = "truncation",
                          levels: Sequence[float] = (0.005, 0.05, 0.2),
                          n: int = 600) -> List[Dict[str, Any]]:
    """BMA deviation vs the measurement-error level of the validation data.

    With a nearly noiseless synthetic validation set the posterior collapses onto
    whichever model is exactly right, which would make the C4 claim trivial.  This
    sweep raises the validation error to 20 % so the weights are genuinely
    spread, and reports whether the ensemble mean still lands within tolerance.
    """
    from .modelform import compare_models

    dists = DISTS_2 if kind != "truncation" else DISTS_3
    ens = model_ensemble(kind, seed=0)
    rows = []
    for level in levels:
        val = validation_set(ens, dists, meas_error=float(level))
        r = compare_models(ens["models"], dists, n=n, weights="bma",
                          validation=val, truth=ens["truth"])
        rows.append({
            "meas_error": float(level),
            "deviation_vs_truth": r.deviation_vs_truth,
            "max_weight": float(max(r.weights.values())),
            "weights": r.weights,
            "model_form_rel_spread": r.model_form_rel_spread,
            "within_tolerance": bool(r.deviation_vs_truth < 0.10),
        })
    return rows


def convergence_study(funcs_meta: Tuple[Any, Dict[str, Any]],
                      dists: Sequence[InputDistribution] = tuple(DISTS_NU),
                      budgets: Sequence[int] = (1000, 10000, 100000),
                      seeds: Sequence[int] = (0, 1, 2, 3, 4)) -> List[Dict[str, Any]]:
    """Monte Carlo moment error vs budget (the honest scale of the 2 % criterion)."""
    from .montecarlo import run_monte_carlo

    func, meta = funcs_meta
    ref = meta["reference"](list(dists))
    rows = []
    for n in budgets:
        dm, dv = [], []
        for s in seeds:
            out = run_monte_carlo(func, list(dists), n=n, seed=s)
            dm.append(abs(out["mean"] - ref["mean"]) / abs(ref["mean"]))
            dv.append(abs(out["variance"] - ref["variance"]) / ref["variance"])
        rows.append({
            "n_samples": int(n),
            "mean_rel_err_median": float(np.median(dm)),
            "mean_rel_err_max": float(np.max(dm)),
            "variance_rel_err_median": float(np.median(dv)),
            "variance_rel_err_max": float(np.max(dv)),
            "variance_rel_err_expected": float(np.sqrt(2.0 / (n - 1))),
            "n_seeds": len(seeds),
        })
    return rows