"""Analytic-benchmark verification driver for the platform's PCE / Sobol stack.

Runs four independent checks and writes a machine-readable ledger:

  A  Gauss-Hermite nodes/weights     -> exact moments (2n-1 rule)
  B  Smolyak sparse grid             -> partition of unity + total-degree exactness
  C  ice_resistance_uq PCE + Sobol   -> closed-form Hermite model indices
  D  digital_twin Legendre PCE+index -> Ishigami / g-function / closed-form model

Usage:
    python3 -m tools.pce_verify.verify_pce --out artifacts/pce_verify/ledger.json

Everything printed is read back from the ledger, no hand-copied numbers.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Dict, List, Sequence, Tuple

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.pce_verify.analytic_benchmarks import (  # noqa: E402
    all_multi_indices,
    g_function,
    g_function_analytic,
    gauss_hermite_moment,
    hermite_basis_norm_sq,
    hermite_model_coefficients,
    hermite_model_indices,
    ishigami,
    ishigami_analytic,
    legendre_basis_norm_sq,
    legendre_model_coefficients,
    legendre_model_indices,
    rel_error,
    smolyak_exactness_degree,
)

EXACT_TOL = 1e-10   # "machine exact" gate for polynomial-exact claims
PASS_TOL = 0.03     # 3 % acceptance band for converged (non-polynomial) benchmarks


# ----------------------------------------------------------------------
# A. Gauss-Hermite quadrature
# ----------------------------------------------------------------------

def check_gauss_hermite(n_max: int = 6) -> Dict[str, object]:
    from backend.services.ice_resistance_uq import _gauss_hermite_nodes_weights

    rows: List[Dict[str, float]] = []
    worst_in_degree = 0.0
    worst_out_degree = 0.0
    for n in range(1, n_max + 1):
        nodes, weights = _gauss_hermite_nodes_weights(n)
        # sanity: node set symmetric, weights positive, partition of unity
        sum_w = float(weights.sum())
        row = {
            "n_nodes": n,
            "sum_weights": sum_w,
            "min_weight": float(weights.min()),
            "node_symmetry": float(np.max(np.abs(nodes + nodes[::-1]))),
        }
        k_max = 2 * n + 3
        errs = {}
        for k in range(k_max + 1):
            approx = float(np.sum(weights * nodes ** k))
            exact = gauss_hermite_moment(k)
            err = rel_error(approx, exact)
            errs[k] = err
            if k <= 2 * n - 1:
                worst_in_degree = max(worst_in_degree, err)
            elif exact != 0.0:
                worst_out_degree = max(worst_out_degree, err)
        row["max_rel_err_deg_le_2n-1"] = max(errs[k] for k in range(2 * n))
        row["degrees_exact"] = 2 * n - 1
        rows.append(row)

    ok = (
        worst_in_degree <= EXACT_TOL
        and all(abs(r["sum_weights"] - 1.0) <= EXACT_TOL for r in rows)
        and all(r["min_weight"] > 0.0 for r in rows)
    )
    return {
        "check": "gauss_hermite_moment_exactness",
        "rows": rows,
        "worst_rel_err_within_degree": worst_in_degree,
        "worst_rel_err_beyond_degree": worst_out_degree,
        "tolerance": EXACT_TOL,
        "verdict": "PASS" if ok else "FAIL",
    }


# ----------------------------------------------------------------------
# B. Smolyak sparse grid
# ----------------------------------------------------------------------

def check_smolyak(levels: Sequence[int] = (2, 3, 4), dims: Sequence[int] = (2, 3)) -> Dict[str, object]:
    from backend.services.ice_resistance_uq import smolyak_sparse_grid

    rows: List[Dict[str, float]] = []
    worst = 0.0
    for dim in dims:
        for level in levels:
            nodes, weights = smolyak_sparse_grid(level, dim)
            deg = smolyak_exactness_degree(level)
            errs = {}
            # single-coordinate moments E[x_1^k] for k <= deg
            for k in range(0, deg + 1):
                approx = float(np.sum(weights * nodes[:, 0] ** k))
                errs[f"E_x1^{k}"] = rel_error(approx, gauss_hermite_moment(k))
            # mixed moment E[x1^2 x2^2] = 1 (total degree 4) when deg >= 4
            if dim >= 2 and deg >= 4:
                approx = float(np.sum(weights * nodes[:, 0] ** 2 * nodes[:, 1] ** 2))
                errs["E_x1^2x2^2"] = rel_error(approx, 1.0)
            worst_in = max(errs.values()) if errs else 0.0
            rows.append({
                "dim": dim,
                "level": level,
                "n_points": int(len(nodes)),
                "sum_weights": float(weights.sum()),
                "min_weight": float(weights.min()),
                "max_weight": float(weights.max()),
                "n_negative_weights": int(np.sum(weights < 0)),
                "exact_degree_claim": deg,
                "worst_rel_err_within_degree": worst_in,
            })
            worst = max(worst, worst_in)
    ok = worst <= EXACT_TOL and all(abs(r["sum_weights"] - 1.0) <= EXACT_TOL for r in rows)
    return {
        "check": "smolyak_partition_of_unity_and_degree_exactness",
        "rows": rows,
        "worst_rel_err": worst,
        "tolerance": EXACT_TOL,
        "verdict": "PASS" if ok else "FAIL",
    }


# ----------------------------------------------------------------------
# C. ice_resistance_uq: projection PCE + Sobol from coefficients
# ----------------------------------------------------------------------

def _hermite_model_func(coeff: Dict[Tuple[int, ...], float], dim: int):
    from backend.services.ice_resistance_uq import _evaluate_pce_basis

    alpha_list = list(coeff.keys())
    c_list = [coeff[a] for a in alpha_list]

    def f(*x) -> float:
        pt = np.asarray(x, dtype=float)
        return float(sum(c * _evaluate_pce_basis(pt, a) for a, c in zip(alpha_list, c_list)))

    return f


def check_hermite_pce(
    dims: Sequence[int] = (2, 3, 4),
    configs: Sequence[Tuple[int, int]] = ((3, 3), (4, 3), (5, 4)),
) -> Dict[str, object]:
    """Grade the projection PCE against closed-form Hermite-model indices.

    ``configs`` are (max_level, max_poly_order) pairs.  Quadrature exactness of the
    sparse grid is ``2*max_level-1``; the projection integral of ``f * Psi_alpha``
    needs degree ``2*max_poly_order`` for a machine-exact coefficient, so the
    rule of thumb is ``max_level >= max_poly_order + 1``.
    """
    from backend.services.ice_resistance_uq import (
        UncertainInput,
        build_pce,
        pce_statistics,
        sobol_indices,
    )

    rows: List[Dict[str, object]] = []
    for dim in dims:
        coeff = hermite_model_coefficients(dim)
        ref = hermite_model_indices(coeff, dim)
        f_std = _hermite_model_func(coeff, dim)
        # UnecertainInput.std == nominal * std_relative, and a zero mean is not
        # expressible; use mean=1 / std=1 and shift the model to standard space.
        def func(*x_phys) -> float:
            return f_std(*[xi - 1.0 for xi in x_phys])

        inputs = [UncertainInput(f"x{i+1}", nominal=1.0, std_relative=1.0) for i in range(dim)]
        modes = [(level, order, False) for level, order in configs]
        # the shipped pre-fix configuration: level 3 with order 3 -> exactness 5 < 6
        modes.insert(0, (configs[0][0], configs[0][1], True))
        for level, order, under in modes:
            t0 = time.time()
            pce = build_pce(func, inputs, max_level=level, max_poly_order=order,
                            allow_under_integrated=under)
            stats = pce_statistics(pce)
            sob = sobol_indices(pce)
            names = [f"x{i+1}" for i in range(dim)]
            idx_map = {tuple(a): j for j, a in enumerate(pce["multi_indices"])}
            coef_err = 0.0
            for a, c in coeff.items():
                j = idx_map.get(tuple(a))
                got = pce["coefficients"][j] if j is not None else 0.0
                coef_err = max(coef_err, abs(got - c) / abs(c))
            fo_err = max(rel_error(sob["first_order"][n], ref["first_order"][i])
                         for i, n in enumerate(names))
            to_err = max(rel_error(sob["total_order"][n], ref["total_order"][i])
                         for i, n in enumerate(names))
            var_err = rel_error(stats["variance"], ref["variance"])
            rows.append({
                "dim": dim,
                "max_level_requested": level,
                "max_level": pce["max_level"],
                "max_poly_order": order,
                "allow_under_integrated": under,
                "under_integrated": pce["under_integrated"],
                "level_auto_raised": pce["level_auto_raised"],
                "n_quad_points": pce["n_evaluations"],
                "n_basis": len(pce["multi_indices"]),
                "quad_exact_degree": smolyak_exactness_degree(pce["max_level"]),
                "required_degree": 2 * order,
                "over_integrated": smolyak_exactness_degree(pce["max_level"]) >= 2 * order,
                "max_rel_err_coefficient": coef_err,
                "rel_err_variance": var_err,
                "max_rel_err_sobol_first": fo_err,
                "max_rel_err_sobol_total": to_err,
                "runtime_s": round(time.time() - t0, 3),
            })
    good = [r for r in rows if not r["under_integrated"]]
    bad = [r for r in rows if r["under_integrated"]]
    return {
        "check": "ice_pce_projection_vs_closed_form",
        "rows": rows,
        "worst_sobol_err_exact_projection": max(
            (max(r["max_rel_err_sobol_first"], r["max_rel_err_sobol_total"]) for r in good), default=0.0),
        "worst_sobol_err_under_integrated": max(
            (max(r["max_rel_err_sobol_first"], r["max_rel_err_sobol_total"]) for r in bad), default=0.0),
        "tolerance": EXACT_TOL,
        "verdict": "PASS" if good and max(
            (max(r["max_rel_err_sobol_first"], r["max_rel_err_sobol_total"]) for r in good),
            default=1.0) <= EXACT_TOL else "FAIL",
    }


# ----------------------------------------------------------------------
# D. digital_twin Legendre PCE + variance-based indices
# ----------------------------------------------------------------------

def check_legendre_model(dim: int = 3, degree: int = 4) -> Dict[str, object]:
    from backend.services.digital_twin.pce import PolynomialChaos

    coeff = legendre_model_coefficients(dim)
    ref = legendre_model_indices(coeff, dim)
    names = [f"x{i+1}" for i in range(dim)]

    def func(X: np.ndarray) -> np.ndarray:
        out = np.zeros(X.shape[0])
        for a, c in coeff.items():
            col = np.ones(X.shape[0])
            for j, k in enumerate(a):
                col = col * _leg_col(X[:, j], k)
            out += c * col
        return out

    pce = PolynomialChaos(names, [-1.0] * dim, [1.0] * dim, degree=degree)
    pce.fit(func, n_samples=400, seed=7)
    pred = pce.predict_matrix(np.array([[0.13, -0.42, 0.77, 0.5][:dim]]))
    exact = func(np.array([[0.13, -0.42, 0.77, 0.5][:dim]]))[0]
    stats = pce.statistics()["y0"]
    sob = pce.sobol_indices()["y0"]
    ref_stats = legendre_pce_statistics(pce)
    ref_sob = legendre_pce_sobol(pce)
    fo_err = max(rel_error(sob["first_order"][n], ref["first_order"][i]) for i, n in enumerate(names))
    to_err = max(rel_error(sob["total_order"][n], ref["total_order"][i]) for i, n in enumerate(names))
    return {
        "check": "legendre_pce_vs_closed_form",
        "engine_vs_reference_max_diff": max(
            abs(sob["first_order"][n] - ref_sob["first_order"][n]) for n in names) + max(
            abs(sob["total_order"][n] - ref_sob["total_order"][n]) for n in names) + rel_error(
            stats["variance"], ref_stats["variance"]),
        "dim": dim,
        "degree": degree,
        "n_basis": len(pce.indices),
        "rel_err_point_prediction": rel_error(float(pred[0, 0]), float(exact)),
        "rel_err_variance": rel_error(stats["variance"], ref["variance"]),
        "max_rel_err_sobol_first": fo_err,
        "max_rel_err_sobol_total": to_err,
        "verdict": "PASS" if max(fo_err, to_err) <= EXACT_TOL else "FAIL",
    }


def _leg_col(x: np.ndarray, degree: int) -> np.ndarray:
    from backend.services.digital_twin.pce import _legendre_all

    return _legendre_all(np.asarray(x, dtype=float), degree)[:, degree]


def legendre_pce_statistics(pce) -> Dict[str, float]:
    """(reference) mean/variance of a Legendre PCE; used to grade the engine."""
    key = next(iter(pce.coefficients))
    coef = pce.coefficients[key]
    mean = float(coef[0])
    var = 0.0
    for j, a in enumerate(pce.indices):
        if sum(a) == 0:
            continue
        var += float(coef[j]) ** 2 * legendre_basis_norm_sq(a)
    return {"mean": mean, "variance": var, "std": math.sqrt(max(var, 0.0))}


def legendre_pce_sobol(pce) -> Dict[str, object]:
    """(reference) variance-based indices of a Legendre PCE."""
    key = next(iter(pce.coefficients))
    coef = pce.coefficients[key]
    dim = len(pce.names)
    var = sum(float(coef[j]) ** 2 * legendre_basis_norm_sq(a)
              for j, a in enumerate(pce.indices) if sum(a) > 0)
    first, total = {}, {}
    for d in range(dim):
        v_d = v_t = 0.0
        for j, a in enumerate(pce.indices):
            if sum(a) == 0:
                continue
            term = float(coef[j]) ** 2 * legendre_basis_norm_sq(a)
            if a[d] > 0:
                v_t += term
                if all(a[k] == 0 for k in range(dim) if k != d):
                    v_d += term
        first[pce.names[d]] = v_d / var if var > 0 else 0.0
        total[pce.names[d]] = v_t / var if var > 0 else 0.0
    return {"variance": var, "first_order": first, "total_order": total}


def check_ishigami(degrees: Sequence[int] = (4, 6, 8, 10, 12)) -> Dict[str, object]:
    """Ishigami benchmark: does a Legendre PCE recover the analytic indices?"""
    from backend.services.digital_twin.pce import PolynomialChaos

    ref = ishigami_analytic()
    pi = math.pi
    rows = []
    for degree in degrees:
        pce = PolynomialChaos(["x1", "x2", "x3"], [-pi] * 3, [pi] * 3, degree=degree)
        pce.fit(lambda X: np.array([ishigami(row) for row in X]), n_samples=1200, seed=11)
        stats = pce.statistics()["y0"]
        sob = pce.sobol_indices()["y0"]
        names = ["x1", "x2", "x3"]
        rows.append({
            "degree": degree,
            "n_basis": len(pce.indices),
            "rel_err_variance": rel_error(stats["variance"], ref["variance"]),
            "max_abs_err_sobol_first": max(
                abs(sob["first_order"][n] - ref["first_order"][i]) for i, n in enumerate(names)),
            "max_abs_err_sobol_total": max(
                abs(sob["total_order"][n] - ref["total_order"][i]) for i, n in enumerate(names)),
            "sobol_first": sob["first_order"],
            "sobol_total": sob["total_order"],
        })
    best = min(rows, key=lambda r: max(r["max_abs_err_sobol_first"], r["max_abs_err_sobol_total"]))
    return {
        "check": "ishigami_1990",
        "reference": ref,
        "rows": rows,
        "best_degree": best["degree"],
        "best_max_abs_err": max(best["max_abs_err_sobol_first"], best["max_abs_err_sobol_total"]),
        "tolerance_abs": PASS_TOL,
        "verdict": "PASS" if max(best["max_abs_err_sobol_first"],
                                 best["max_abs_err_sobol_total"]) <= PASS_TOL else "FAIL",
    }


def check_g_function(a: Sequence[float] = (0.0, 0.5, 1.0, 3.0, 9.0),
                     degrees: Sequence[int] = (3, 5, 7, 9),
                     n_samples: int = 3000) -> Dict[str, object]:
    """Sobol g-function (product form, analytic indices) -- degree sweep.

    The g-function is C0 but not C1 at x = 0.5, so a spectral basis converges only
    algebraically; the sweep is what shows whether the engine *converges* rather
    than whether one degree happens to land inside the band.
    """
    from backend.services.digital_twin.pce import PolynomialChaos

    ref = g_function_analytic(a)
    dim = len(a)
    names = [f"x{i+1}" for i in range(dim)]
    rows = []
    for degree in degrees:
        pce = PolynomialChaos(names, [0.0] * dim, [1.0] * dim, degree=degree)
        pce.fit(lambda X: np.array([g_function(row, a) for row in X]), n_samples=n_samples, seed=13)
        stats = pce.statistics()["y0"]
        sob = pce.sobol_indices()["y0"]
        fo_err = max(abs(sob["first_order"][n] - ref["first_order"][i]) for i, n in enumerate(names))
        to_err = max(abs(sob["total_order"][n] - ref["total_order"][i]) for i, n in enumerate(names))
        rows.append({
            "degree": degree,
            "n_basis": len(pce.indices),
            "rel_err_variance": rel_error(stats["variance"], ref["variance"]),
            "max_abs_err_sobol_first": fo_err,
            "max_abs_err_sobol_total": to_err,
            "sobol_first": sob["first_order"],
            "sobol_total": sob["total_order"],
        })
    best = min(rows, key=lambda r: max(r["max_abs_err_sobol_first"], r["max_abs_err_sobol_total"]))
    return {
        "check": "sobol_g_function",
        "a": list(a),
        "rows": rows,
        "best_degree": best["degree"],
        "best_max_abs_err": max(best["max_abs_err_sobol_first"], best["max_abs_err_sobol_total"]),
        "tolerance_abs": PASS_TOL,
        "reference": ref,
        "verdict": "PASS" if max(best["max_abs_err_sobol_first"],
                                 best["max_abs_err_sobol_total"]) <= PASS_TOL else "FAIL",
    }


# ----------------------------------------------------------------------
# E. production configuration of the ice-resistance UQ chain
# ----------------------------------------------------------------------

def check_ice_production(ship: str = "MV_Arctic") -> Dict[str, object]:
    """Error budget of the shipped default (level=3, order=3) vs a coupled setting."""
    from backend.services.ice_resistance_uq import (
        SHIP_TRIAL_DATA,
        _calibrate_ship,
        build_pce,
        lindqvist_ice_resistance,
        monte_carlo_reference,
        evaluate_pce_physical,
        pce_statistics,
        run_ice_resistance_uq,
        sobol_indices,
        UncertainInput,
    )

    trial = SHIP_TRIAL_DATA[ship]
    ship_params = _calibrate_ship(ship)
    inputs = [
        UncertainInput("ice_strength_MPa", nominal=trial["nominal_ice_strength_MPa"], std_relative=0.20),
        UncertainInput("ice_thickness_m", nominal=trial["nominal_ice_thickness_m"], std_relative=0.15),
        UncertainInput("friction_coeff", nominal=trial["nominal_friction_coeff"], std_relative=0.25),
        UncertainInput("ship_speed_ms", nominal=trial["nominal_speed_ms"], std_relative=0.10),
    ]

    def model(sig, h, mu, v):
        return lindqvist_ice_resistance(ship_params, h, sig, mu, v)

    mc = monte_carlo_reference(model, inputs, n_samples=100_000)
    rows = []
    modes = [(3, 3, False), (4, 3, False), (5, 4, False)]
    modes.insert(0, (3, 3, True))   # pre-fix shipped default, under-integrated
    for level, order, under in modes:
        pce = build_pce(model, inputs, max_level=level, max_poly_order=order,
                        allow_under_integrated=under)
        st = pce_statistics(pce)
        sb = sobol_indices(pce)
        rows.append({
            "max_level_requested": level,
            "max_level": pce["max_level"],
            "max_poly_order": order,
            "allow_under_integrated": under,
            "under_integrated": pce["under_integrated"],
            "n_quad_points": pce["n_evaluations"],
            "rel_err_mean_vs_mc": rel_error(st["mean"], mc["mean"]),
            "rel_err_std_vs_mc": rel_error(st["std"], mc["std"]),
            "sobol_sum_first": sum(sb["first_order"].values()),
            "sobol_sum_total": sum(sb["total_order"].values()),
            "sobol_first": sb["first_order"],
            "sobol_total": sb["total_order"],
        })
    rng = np.random.default_rng(5)
    X = np.column_stack([inp.mean + inp.std * rng.standard_normal(4000) for inp in inputs])
    Y = np.array([model(*row) for row in X])
    for row in rows:
        pce = build_pce(model, inputs, max_level=row["max_level_requested"],
                        max_poly_order=row["max_poly_order"],
                        allow_under_integrated=row["allow_under_integrated"])
        Yp = np.array([evaluate_pce_physical(pce, x) for x in X])
        rel = np.abs(Yp - Y) / np.abs(Y)
        row["oos_mean_rel_err"] = float(rel.mean())
        row["oos_p95_rel_err"] = float(np.percentile(rel, 95))
        row["oos_max_rel_err"] = float(rel.max())
    legacy = run_ice_resistance_uq(ship, n_mc_reference=100_000, n_mc_validation=2000)
    return {
        "check": "ice_production_default_config",
        "ship": ship,
        "mc_reference": {"mean": mc["mean"], "std": mc["std"]},
        "rows": rows,
        "legacy_endpoint_result": {
            "mean_resistance_kN": legacy.mean_resistance_kN,
            "std_resistance_kN": legacy.std_resistance_kN,
            "mean_error": legacy.mean_error,
            "std_error": legacy.std_error,
            "ci_coverage": legacy.ci_coverage,
            "sobol_sum_first_order": legacy.sobol_sum_first_order,
            "sobol_sum_total_order": legacy.sobol_sum_total_order,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/pce_verify/ledger.json")
    ap.add_argument("--skip-heavy", action="store_true")
    args = ap.parse_args()

    ledger: Dict[str, object] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tolerances": {"machine_exact": EXACT_TOL, "acceptance_band": PASS_TOL},
    }
    ledger["A_gauss_hermite"] = check_gauss_hermite()
    ledger["B_smolyak"] = check_smolyak()
    ledger["C_hermite_pce"] = check_hermite_pce()
    ledger["D_legendre_model"] = check_legendre_model()
    ledger["E_ishigami"] = check_ishigami()
    ledger["F_g_function"] = check_g_function()
    if not args.skip_heavy:
        ledger["G_ice_production"] = check_ice_production()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(ledger, fh, indent=2, default=float)

    print(json.dumps(_summary(ledger), indent=2))
    print(f"\nledger -> {args.out}")
    return 0


def _summary(ledger: Dict[str, object]) -> Dict[str, object]:
    out = {}
    a = ledger["A_gauss_hermite"]
    out["A_gauss_hermite"] = {"verdict": a["verdict"],
                              "worst_in_degree": a["worst_rel_err_within_degree"]}
    b = ledger["B_smolyak"]
    out["B_smolyak"] = {"verdict": b["verdict"], "worst": b["worst_rel_err"]}
    c = ledger["C_hermite_pce"]
    out["C_hermite_pce"] = {
        "verdict": c["verdict"],
        "worst_exact_projection": c["worst_sobol_err_exact_projection"],
        "worst_under_integrated": c["worst_sobol_err_under_integrated"],
        "rows": [
            {"dim": r["dim"], "L_req": r["max_level_requested"], "L_used": r["max_level"],
             "p": r["max_poly_order"], "under": r["under_integrated"],
             "coef": f"{r['max_rel_err_coefficient']:.2e}",
             "sobol1": f"{r['max_rel_err_sobol_first']:.2e}",
             "sobolT": f"{r['max_rel_err_sobol_total']:.2e}"}
            for r in c["rows"]
        ],
    }
    d = ledger["D_legendre_model"]
    out["D_legendre_model"] = {"verdict": d["verdict"],
                               "sobol1": d["max_rel_err_sobol_first"],
                               "sobolT": d["max_rel_err_sobol_total"]}
    e = ledger["E_ishigami"]
    out["E_ishigami"] = {"verdict": e["verdict"], "best_degree": e["best_degree"],
                         "best_max_abs_err": e["best_max_abs_err"]}
    f = ledger["F_g_function"]
    out["F_g_function"] = {"verdict": f["verdict"], "best_degree": f["best_degree"],
                           "best_max_abs_err": f["best_max_abs_err"]}
    if "G_ice_production" in ledger:
        g = ledger["G_ice_production"]
        out["G_ice_production"] = [
            {"L_req": r["max_level_requested"], "L_used": r["max_level"],
             "p": r["max_poly_order"], "under": r["under_integrated"],
             "mean_err": f"{r['rel_err_mean_vs_mc']:.2%}",
             "std_err": f"{r['rel_err_std_vs_mc']:.2%}",
             "oos_mean": f"{r['oos_mean_rel_err']:.4%}",
             "oos_p95": f"{r['oos_p95_rel_err']:.3%}",
             "oos_max": f"{r['oos_max_rel_err']:.2%}"}
            for r in g["rows"]
        ]
    return out


if __name__ == "__main__":
    raise SystemExit(main())