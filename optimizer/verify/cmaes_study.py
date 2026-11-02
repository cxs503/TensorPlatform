"""Multi-seed / budget-scaling study of ``optimizer.core.cmaes``.

Single-seed agreement with a closed-form optimum is weak evidence for a
stochastic optimiser, so this module follows the COCO/BBOB methodology
(Hansen, Auger, Ros, Mersmann, Tusar & Brockhoff, "COCO: A Platform for
Comparing Continuous Optimizers in a Black-Box Setting", 2021; Hansen 2016
tutorial for the algorithm) and reports, per benchmark:

* **success rate** over independent seeds at a target precision ``eps``
  (normalised error, ``eps = 1e-8`` on the convex quadratics, ``1e-6`` on
  Rosenbrock, ``1e-4`` on Rastrigin);
* **ERT** (expected running time, the BBOB headline metric):
  ``ERT = sum(evals_to_target over all runs) / #successful_runs``;
* median / quartiles of the final normalised error.

The second half of the module is an *engineering* budget study answering the
question that actually matters for the deployed KCS loop (``max_evals = 300``,
``dim = 8``): how much objective noise does the optimiser itself introduce at
that budget?  That number is the floor against which a reported resistance
improvement (e.g. the 1.377 % of ``optimizer/REPORT.md``) must be judged.

Usage::

    python3 -m optimizer.verify.cmaes_study --out optimizer/runs/cmaes_verify
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List

import numpy as np

from .cmaes_reference import (
    _box,
    _cigar,
    _ellipsoid,
    _rastrigin,
    _rosenbrock,
    _rotated_ellipsoid,
    _sphere,
    run_case,
)

BENCH = {
    "sphere_n8": (lambda: _sphere(8), _box(-1, 1, 8), 1e-8),
    "ellipsoid_n8_cond1e6": (lambda: _ellipsoid(8, 1e6), _box(-1, 1, 8), 1e-8),
    "cigar_n8_cond1e6": (lambda: _cigar(8, 1e6), _box(-1, 1, 8), 1e-8),
    "rot_ellipsoid_n8_cond1e4": (lambda: _rotated_ellipsoid(8, 1e4), _box(-1, 1, 8), 1e-8),
    "rosenbrock_n4": (lambda: _rosenbrock(4), _box(-2, 2, 4), 1e-6),
    "rastrigin_n10": (lambda: _rastrigin(10), _box(-2, 2, 10), 1e-4),
}

SEEDS = (1, 2, 3, 4, 5)


def _evals_to_target(hist: List[Dict], f_start: float, f_opt: float,
                     eps: float) -> int | None:
    """First evaluation count at which normalised error <= eps (BBOB T_target)."""
    denom = f_start - f_opt
    for h in hist:
        err = abs(h["best_fitness"] - f_opt) / denom if denom else abs(h["best_fitness"])
        if err <= eps:
            return int(h["eval_count"])
    return None


def run_study(out_dir: str | None = None,
              max_evals: int = 6000,
              seeds=SEEDS,
              budget_grid=None) -> Dict:
    """Multi-seed statistics + deployed-budget noise floor."""
    rows = []
    for name, (factory, bounds, eps) in BENCH.items():
        errs, times, succ, sigmas = [], [], 0, []
        for sd in seeds:
            fn, x_opt, f_opt = factory()
            rec = run_case(name, fn, x_opt, f_opt, bounds, max_evals,
                           seed=int(sd))
            errs.append(rec["err_norm"])
            sigmas.append(rec["sigma_final"])
            t = _evals_to_target(rec["history"], rec["f_start"], f_opt, eps)
            if t is not None:
                succ += 1
                times.append(t)
            else:
                times.append(max_evals)  # censored run: counted in the numerator
        errs_arr = np.array(errs)
        ert = float(np.sum(times) / succ) if succ else float("inf")
        rows.append({
            "benchmark": name,
            "dim": int(bounds.shape[0]),
            "seeds": len(seeds),
            "max_evals": int(max_evals),
            "target_eps": eps,
            "success_rate": succ / len(seeds),
            "ert_evals": ert,
            "median_err_norm": float(np.median(errs_arr)),
            "q25_err_norm": float(np.percentile(errs_arr, 25)),
            "q75_err_norm": float(np.percentile(errs_arr, 75)),
            "min_err_norm": float(errs_arr.min()),
            "max_err_norm": float(errs_arr.max()),
            "sigma_final_median": float(np.median(sigmas)),
        })

    # ---- deployed-budget noise floor (KCS loop uses max_evals = 300, dim = 8)
    if budget_grid is None:
        budget_grid = [100, 200, 300, 600, 1200, 2400, 4800]
    fn, x_opt, f_opt = _sphere(8)
    floor = []
    for nev in budget_grid:
        vals = [run_case("sphere_n8", fn, x_opt, f_opt, _box(-1, 1, 8), nev,
                         seed=int(sd))["err_norm"] for sd in seeds]
        e = np.array(vals)
        floor.append({"max_evals": nev,
                      "median_err_norm": float(np.median(e)),
                      "q25": float(np.percentile(e, 25)),
                      "q75": float(np.percentile(e, 75)),
                      "success_eps_1e-6": float(np.mean(e <= 1e-6))})

    # ---- linear-convergence rate on the sphere (CMA-ES theory: geometric)
    fn, x_opt, f_opt = _sphere(8)
    rec = run_case("sphere_n8", fn, x_opt, f_opt, _box(-1, 1, 8), 1200, seed=42)
    hist = rec["history"]
    gens = np.array([h["eval_count"] for h in hist], dtype=float)
    fit = np.array([max(h["best_fitness"], 1e-30) for h in hist], dtype=float)
    lo, hi = 2, max(4, len(fit) // 2)          # linear regime window
    if hi - lo >= 3:
        p = np.polyfit(gens[lo:hi], np.log10(fit[lo:hi]), 1)
        pred = np.polyval(p, gens[lo:hi])
        ss_res = float(np.sum((np.log10(fit[lo:hi]) - pred) ** 2))
        ss_tot = float(np.sum((np.log10(fit[lo:hi]) - np.log10(fit[lo:hi]).mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rate = {"slope_dex_per_eval": float(p[0]),
                "r2_linear_fit": float(r2),
                "window": [int(gens[lo]), int(gens[hi - 1])],
                "decades_reduced": float(np.log10(fit[lo]) - np.log10(fit[hi - 1]))}
    else:
        rate = {"slope_dex_per_eval": None, "r2_linear_fit": None,
                "window": None, "decades_reduced": None}

    report = {
        "mission": "m1866",
        "method": "COCO/BBOB-style multi-seed statistics (Hansen et al. 2021)",
        "kernel": "optimizer/core/cmaes.py",
        "multiseed": rows,
        "budget_noise_floor_sphere_n8": floor,
        "convergence_rate_sphere_n8": rate,
    }
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "cmaes_study.json"), "w") as fh:
            json.dump(report, fh, indent=2)
    return report


def figures(report: Dict, out_dir: str) -> List[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        print(f"[figures] matplotlib unavailable: {exc}")
        return []
    os.makedirs(out_dir, exist_ok=True)
    paths = []

    # ERT / success-rate table as a bar chart
    rows = report["multiseed"]
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    names = [r["benchmark"] for r in rows]
    ert = [r["ert_evals"] for r in rows]
    ax.bar(range(len(names)), ert, color="#3b7dd8")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=25, ha="right", fontsize=7)
    ax.set_ylabel("ERT [objective evaluations]")
    ax.set_title("Expected running time to target precision (5 seeds, BBOB-style)")
    for i, r in enumerate(rows):
        ax.text(i, ert[i], f"{r['success_rate']*100:.0f}%", ha="center",
                va="bottom", fontsize=7)
    ax.grid(True, axis="y", alpha=0.3)
    p = os.path.join(out_dir, "cmaes_ert.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)

    # deployed-budget noise floor
    fl = report["budget_noise_floor_sphere_n8"]
    fig, ax = plt.subplots(figsize=(7.0, 4.3))
    nev = [f["max_evals"] for f in fl]
    med = [max(f["median_err_norm"], 1e-18) for f in fl]
    q25 = [max(f["q25"], 1e-18) for f in fl]
    q75 = [max(f["q75"], 1e-18) for f in fl]
    ax.fill_between(nev, q25, q75, alpha=0.25, label="IQR over 5 seeds")
    ax.loglog(nev, med, "o-", label="median normalised error")
    ax.axvline(300, ls="--", lw=1.2, color="crimson", label="deployed KCS budget (300)")
    ax.set_xlabel("evaluation budget")
    ax.set_ylabel("(f_best - f_opt)/(f_start - f_opt)")
    ax.set_title("Optimiser noise floor vs evaluation budget (sphere, n=8)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(out_dir, "cmaes_budget_noise_floor.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="optimizer/runs/cmaes_verify")
    ap.add_argument("--max-evals", type=int, default=6000)
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args()
    rep = run_study(out_dir=args.out, max_evals=args.max_evals)
    print(f"{'benchmark':26s} {'succ':>5s} {'ERT':>9s} {'med_err':>10s}")
    for r in rep["multiseed"]:
        print(f"{r['benchmark']:26s} {r['success_rate']*100:4.0f}% "
              f"{r['ert_evals']:9.0f} {r['median_err_norm']:10.2e}")
    print("budget noise floor:", [(f['max_evals'], f"{f['median_err_norm']:.2e}")
                                  for f in rep["budget_noise_floor_sphere_n8"]])
    print("rate:", rep["convergence_rate_sphere_n8"])
    if args.figures:
        print("figures:", figures(rep, args.out))


if __name__ == "__main__":
    main()