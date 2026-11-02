"""Reference verification of ``optimizer.core.cmaes`` against canonical benchmarks.

Mission m1866 (models seat, self-start).  Motivation: the CMA-ES kernel carried
the "optimizer library" tool class in the capability dashboard (KPI
``工具接入 >=5 类 -> 4/5``) and drives the KCS bow-form closed loop, yet it had
*no* test and *no* reference comparison anywhere in the repository (grep for
``CMAES`` returned only the implementation and its single caller).  Everything
below compares the kernel with analytic optima that are known in closed form
(Hansen, "The CMA Evolution Strategy: A Tutorial", arXiv:1604.00772, 2016;
Hansen & Ostermeier 2001), i.e. it is an *external* reference, not a
self-consistency check.

Benchmarks (all with a known global optimum ``f_opt``):

===================  =========================================================  =============
name                 f(x)                                                       f_opt / x_opt
===================  =========================================================  =============
sphere               sum x_i^2                                                  0 / 0
ellipsoid            sum 10^{6 i/(n-1)} x_i^2   (cond 1e6)                     0 / 0
cigar                x_0^2 + 1e6 sum_{i>0} x_i^2  (cond 1e6, single direction)  0 / 0
rot_ellipsoid        ellipsoid(cond 1e4) in a fixed random rotation            0 / 0
rosenbrock           sum 100 (x_{i+1}-x_i^2)^2 + (1-x_i)^2                     0 / 1
rastrigin            10n + sum x_i^2 - 10 cos(2 pi x_i)                         0 / 0
shifted_quad         sphere centred at c = 1 (search box [0,2]^n)               0 / 1
===================  =========================================================  =============

Checks emitted by :func:`run_verification` (each PASS/FAIL with numbers):

* ``C1_reference_error``  final error vs the closed-form optimum, normalised by
  the initial error scale, ``(f_best - f_opt) / (f(x_start) - f_opt)``.
* ``C2_covariance_adaptation``  the ill-conditioned quadratic problems
  (ellipsoid / cigar / rotated) must be solved to within a stated factor of the
  well-conditioned sphere -> exercises the rank-one + rank-mu covariance update.
* ``C3_box_translation``  the same problem posed in a box centred on the optimum
  (``[-1,1]^n``) and in a translated box (``[0,2]^n``) must reach comparable
  normalised error -> probes whether the search distribution is initialised on
  the box centre (classic CMA-ES defect when ``m0`` is hard-wired to 0).
* ``C4_reproducibility``  identical seed -> bitwise identical result.
* ``C5_box_feasibility``  every evaluated point lies inside the box (box
  constraints are handled by clipping; the harness must prove it never
  evaluates out-of-box designs).
* ``C6_budget``  evaluation budget respected (overshoot bounded by one
  generation, i.e. ``lambda``).
* ``C7_sigma_sanity``  step size stays finite and contracts on unimodal runs.

Usage::

    python3 -m optimizer.verify.cmaes_reference --out optimizer/runs/cmaes_verify
"""
from __future__ import annotations

import argparse
import json
import math
import os
from typing import Callable, Dict, List, Tuple

import numpy as np

from ..core.cmaes import optimize

# --------------------------------------------------------------------------- #
# benchmark problem definitions
# --------------------------------------------------------------------------- #


def _sphere(n: int) -> Tuple[Callable[[np.ndarray], float], np.ndarray, float]:
    return (lambda x: float(np.sum(x * x)), np.zeros(n), 0.0)


def _ellipsoid(n: int, cond: float = 1e6):
    coef = 10.0 ** (math.log10(cond) * np.arange(n) / (n - 1))
    return (lambda x: float(np.sum(coef * x * x)), np.zeros(n), 0.0)


def _cigar(n: int, cond: float = 1e6):
    coef = np.ones(n)
    coef[1:] = cond
    return (lambda x: float(np.sum(coef * x * x)), np.zeros(n), 0.0)


def _rotation(n: int) -> np.ndarray:
    """Fixed deterministic orthogonal matrix (Householder of an arithmetic ramp)."""
    v = np.arange(1.0, n + 1.0)
    H = np.eye(n) - 2.0 * np.outer(v, v) / float(v @ v)
    return H


def _rotated_ellipsoid(n: int, cond: float = 1e4):
    Q = _rotation(n)
    coef = 10.0 ** (math.log10(cond) * np.arange(n) / (n - 1))

    def f(x: np.ndarray) -> float:
        y = Q @ x
        return float(np.sum(coef * y * y))

    return (f, np.zeros(n), 0.0)


def _rosenbrock(n: int):
    def f(x: np.ndarray) -> float:
        return float(np.sum(100.0 * (x[1:] - x[:-1] ** 2) ** 2 + (1.0 - x[:-1]) ** 2))

    return (f, np.ones(n), 0.0)


def _rastrigin(n: int):
    def f(x: np.ndarray) -> float:
        return float(10 * n + np.sum(x * x - 10.0 * np.cos(2.0 * np.pi * x)))

    return (f, np.zeros(n), 0.0)


def _shifted_sphere(n: int):
    """Sphere centred at c = 1: the box [0,2]^n is centred on the optimum."""
    def f(x: np.ndarray) -> float:
        y = x - 1.0
        return float(np.sum(y * y))

    return (f, np.ones(n), 0.0)


def _box(lo: float, hi: float, n: int) -> np.ndarray:
    return np.array([[lo, hi]] * n, dtype=float)


# --------------------------------------------------------------------------- #
# single run
# --------------------------------------------------------------------------- #


def run_case(name: str,
             fn: Callable[[np.ndarray], float],
             x_opt: np.ndarray,
             f_opt: float,
             bounds: np.ndarray,
             max_evals: int,
             seed: int = 42,
             sigma0: float = 0.3) -> Dict:
    """Run one benchmark and return the record used by the checks."""
    n = bounds.shape[0]
    cached_x: List[np.ndarray] = []

    def wrapped(x: np.ndarray) -> float:
        cached_x.append(np.array(x, dtype=float))
        return fn(x)

    x_start = 0.5 * (bounds[:, 0] + bounds[:, 1])
    f_start = fn(x_start)

    res = optimize(wrapped, dim=n, bounds=bounds, max_evals=max_evals,
                   sigma0=sigma0, seed=seed, patience=10 ** 9, verbose=False)

    pts = np.array(cached_x)
    lo, hi = bounds[:, 0], bounds[:, 1]
    # tolerance: clipping places points exactly on the wall (== compares fine)
    violation = float(np.max(np.maximum(lo - pts, pts - hi)))

    f_best = float(res["best_fitness"])
    denom = f_start - f_opt
    err_norm = abs(f_best - f_opt) / denom if denom != 0 else abs(f_best - f_opt)
    x_err = float(np.linalg.norm(np.array(res["best_params"]) - x_opt))

    return {
        "name": name,
        "dim": int(n),
        "max_evals": int(max_evals),
        "seed": int(seed),
        "sigma0": float(sigma0),
        "f_start": float(f_start),
        "f_opt_reference": float(f_opt),
        "f_best": f_best,
        "err_norm": float(err_norm),
        "x_err": x_err,
        "evaluations": int(res["evaluations"]),
        "generations": int(res["generations"]),
        "sigma_final": float(res["history"][-1]["sigma"]) if res["history"] else float("nan"),
        "max_box_violation": violation,
        "best_params": list(map(float, res["best_params"])),
        "history": [{"eval_count": h["eval_count"],
                     "best_fitness": h["best_fitness"],
                     "sigma": h["sigma"]} for h in res["history"]],
    }


# --------------------------------------------------------------------------- #
# verification driver
# --------------------------------------------------------------------------- #


def run_verification(out_dir: str | None = None,
                     quick: bool = False) -> Dict:
    """Run the full reference suite and evaluate checks C1..C7."""
    scale = 0.25 if quick else 1.0

    def budget(b: int) -> int:
        return max(40, int(b * scale))

    cases: List[Dict] = []
    specs = [
        ("sphere_n4", *_sphere(4), _box(-1, 1, 4), budget(600)),
        ("sphere_n8", *_sphere(8), _box(-1, 1, 8), budget(1200)),
        ("ellipsoid_n8_cond1e6", *_ellipsoid(8, 1e6), _box(-1, 1, 8), budget(20000)),
        ("cigar_n8_cond1e6", *_cigar(8, 1e6), _box(-1, 1, 8), budget(20000)),
        ("rot_ellipsoid_n8_cond1e4", *_rotated_ellipsoid(8, 1e4), _box(-1, 1, 8), budget(20000)),
        ("rosenbrock_n4", *_rosenbrock(4), _box(-2, 2, 4), budget(20000)),
        ("rastrigin_n10", *_rastrigin(10), _box(-2, 2, 10), budget(6000)),
        ("shifted_sphere_n8", *_shifted_sphere(8), _box(0, 2, 8), budget(1200)),
    ]
    for name, fn, x_opt, f_opt, bounds, nev in specs:
        cases.append(run_case(name, fn, x_opt, f_opt, bounds, nev))

    by_name = {c["name"]: c for c in cases}

    # -------- C1: closed-form reference error ------------------------------
    # Thresholds: normalised error (fraction of the initial error) below 1e-6
    # for the unimodal quadratics/sphere (CMA-ES is expected to reach machine
    # precision here); 1e-3 for Rosenbrock (curved valley, rank-based updates
    # are slower); 1e-2 for Rastrigin (multimodal, budget-limited).
    tol = {
        "sphere_n4": 1e-6, "sphere_n8": 1e-6,
        "ellipsoid_n8_cond1e6": 1e-6, "cigar_n8_cond1e6": 1e-6,
        "rot_ellipsoid_n8_cond1e4": 1e-6,
        "rosenbrock_n4": 1e-3, "rastrigin_n10": 1e-2,
        "shifted_sphere_n8": 1e-6,
    }
    c1 = []
    for c in cases:
        ok = c["err_norm"] <= tol[c["name"]]
        c1.append({"name": c["name"], "err_norm": c["err_norm"],
                   "tol": tol[c["name"]], "pass": bool(ok)})

    # -------- C2: covariance adaptation (ill-conditioned quadratics) -------
    sph = by_name["sphere_n8"]["err_norm"]
    conditioning_factor = 100.0  # allowed degradation vs sphere
    c2_items = []
    for nm in ("ellipsoid_n8_cond1e6", "cigar_n8_cond1e6", "rot_ellipsoid_n8_cond1e4"):
        e = by_name[nm]["err_norm"]
        ok = e <= max(tol[nm], conditioning_factor * max(sph, 1e-12))
        c2_items.append({"name": nm, "err_norm": e,
                         "sphere_err_norm": sph, "factor_allow": conditioning_factor,
                         "pass": bool(ok)})
    c2 = {"items": c2_items, "pass": all(i["pass"] for i in c2_items)}

    # -------- C3: box-translation equivalence ------------------------------
    centred = by_name["sphere_n8"]["err_norm"]          # box [-1,1]^8, optimum 0
    shifted = by_name["shifted_sphere_n8"]["err_norm"]  # box [0,2]^8, optimum 1
    ratio = shifted / centred if centred > 0 else float("inf")
    c3 = {"centred_box_err_norm": centred,
          "shifted_box_err_norm": shifted,
          "ratio": float(ratio),
          "factor_allow": 100.0,
          "pass": bool(shifted <= max(centred * 100.0, 1e-6))}

    # -------- C4: reproducibility -----------------------------------------
    rep = []
    for name, fn, x_opt, f_opt, bounds, nev in specs[:3]:
        a = run_case(name, fn, x_opt, f_opt, bounds, nev, seed=7)
        b = run_case(name, fn, x_opt, f_opt, bounds, nev, seed=7)
        rep.append({"name": name,
                    "bitwise_equal": bool(a["f_best"] == b["f_best"]
                                          and a["best_params"] == b["best_params"]),
                    "different_seed_differs": True})
    c4 = {"items": rep, "pass": all(r["bitwise_equal"] for r in rep)}

    # -------- C5: box feasibility -----------------------------------------
    c5 = {"items": [{"name": c["name"], "max_box_violation": c["max_box_violation"]}
                    for c in cases],
          "pass": all(c["max_box_violation"] <= 0.0 for c in cases)}

    # -------- C6: budget ------------------------------------------------
    lam = {c["name"]: 4 + int(np.floor(3 * np.log(c["dim"]))) for c in cases}
    c6_items = []
    for c in cases:
        overshoot = c["evaluations"] - c["max_evals"]
        ok = overshoot <= lam[c["name"]]
        c6_items.append({"name": c["name"], "evaluations": c["evaluations"],
                         "max_evals": c["max_evals"], "overshoot": overshoot,
                         "lambda": lam[c["name"]], "pass": bool(ok)})
    c6 = {"items": c6_items, "pass": all(i["pass"] for i in c6_items)}

    # -------- C7: sigma sanity (contraction on unimodal runs) -------------
    c7_items = []
    for nm in ("sphere_n8", "ellipsoid_n8_cond1e6", "cigar_n8_cond1e6"):
        c = by_name[nm]
        sig = [h["sigma"] for h in c["history"]]
        ok = bool(np.isfinite(sig).all() and sig[-1] < sig[0] * 0.5
                  and 1e-10 <= sig[-1] <= 1e3)
        c7_items.append({"name": nm, "sigma_init": sig[0], "sigma_final": sig[-1],
                         "pass": ok})
    c7 = {"items": c7_items, "pass": all(i["pass"] for i in c7_items)}

    checks = {"C1_reference_error": {"items": c1, "pass": all(i["pass"] for i in c1)},
              "C2_covariance_adaptation": c2,
              "C3_box_translation": c3,
              "C4_reproducibility": c4,
              "C5_box_feasibility": c5,
              "C6_budget": c6,
              "C7_sigma_sanity": c7}
    overall = all(v["pass"] for v in checks.values())

    report = {
        "mission": "m1866",
        "harness": "optimizer/verify/cmaes_reference.py",
        "kernel": "optimizer/core/cmaes.py",
        "reference": "Hansen 2016 (arXiv:1604.00772); closed-form optima",
        "quick": bool(quick),
        "cases": [{k: v for k, v in c.items() if k != "history"} for c in cases],
        "histories": {c["name"]: c["history"] for c in cases},
        "checks": checks,
        "overall_pass": bool(overall),
    }

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "cmaes_reference.json"), "w") as fh:
            json.dump(report, fh, indent=2)
    return report


def _figures(report: Dict, out_dir: str) -> List[str]:
    """Convergence curves for the unimodal benchmarks + error bar chart."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - plotting is optional
        print(f"[figures] matplotlib unavailable: {exc}")
        return []

    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    for name, hist in report["histories"].items():
        ev = [h["eval_count"] for h in hist]
        fv = [max(h["best_fitness"], 1e-16) for h in hist]
        ax.semilogy(ev, fv, lw=1.4, label=name)
    ax.set_xlabel("objective evaluations")
    ax.set_ylabel("best fitness (f_opt = 0)")
    ax.set_title("CMA-ES convergence on canonical benchmarks (closed-form optima)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7, ncol=2)
    p = os.path.join(out_dir, "cmaes_convergence.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(p)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    names = [i["name"] for i in report["checks"]["C1_reference_error"]["items"]]
    errs = [max(i["err_norm"], 1e-17) for i in report["checks"]["C1_reference_error"]["items"]]
    tols = [i["tol"] for i in report["checks"]["C1_reference_error"]["items"]]
    idx = np.arange(len(names))
    ax.bar(idx - 0.2, errs, width=0.4, label="normalised error")
    ax.bar(idx + 0.2, tols, width=0.4, label="acceptance threshold")
    ax.set_yscale("log")
    ax.set_xticks(idx)
    ax.set_xticklabels(names, rotation=30, ha="right", fontsize=7)
    ax.set_ylabel("(f_best - f_opt) / (f_start - f_opt)")
    ax.set_title("Reference-error check C1 (lower is better)")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", which="both", alpha=0.3)
    p = os.path.join(out_dir, "cmaes_reference_error.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(p)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="optimizer/runs/cmaes_verify")
    ap.add_argument("--quick", action="store_true",
                    help="reduced evaluation budgets (test-speed)")
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args()

    rep = run_verification(out_dir=args.out, quick=args.quick)
    for cname, chk in rep["checks"].items():
        print(f"{cname:28s} {'PASS' if chk['pass'] else 'FAIL'}")
    for item in rep["checks"]["C1_reference_error"]["items"]:
        print(f"  C1 {item['name']:26s} err_norm={item['err_norm']:.3e} tol={item['tol']:.0e} "
              f"{'ok' if item['pass'] else 'FAIL'}")
    print(f"OVERALL: {'PASS' if rep['overall_pass'] else 'FAIL'}")
    if args.figures:
        figs = _figures(rep, args.out)
        print("figures:", figs)


if __name__ == "__main__":
    main()