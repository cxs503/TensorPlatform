"""Literature/analytic verification of the shipped CMA-ES kernel.

The shipped kernel ``optimizer.core.cmaes`` is re-checked against two
*independent* references that never call it:

* closed-form strategy parameters of Hansen (2016), arXiv:1604.00772,
  eqs. (5), (49)-(55);
* a from-paper re-implementation (``cmaes_reference.ReferenceCMAES``) in the
  tutorial's own B/D eigenvalue parametrisation.

Criterion for each benchmark function is the *analytic* global minimum
(``f* = 0`` for every function used here), so "error" below means "distance of
the best found value from the analytically known optimum", not a self-consistency
check between two runs of the same code.

Every number quoted in ``docs/cmaes_literature_verification.md`` is produced by
``python -m optimizer.verification.cmaes_literature_verification`` and written to
``artifacts/cmaes_verification/cmaes_verification.json`` (no hand copies).

Reference for the acceptance thresholds:

* parameter agreement: machine precision (closed form, no discretisation);
* evals-to-target vs the independent reference implementation: ratio within a
  factor 3 (Monte-Carlo noise of a single seed, plus the different sampling
  parametrisation, is of that order; pycma reports similar run-to-run spread);
* sampling fidelity: empirical covariance of 2e5 draws vs the exact ``sigma^2 C``,
  relative Frobenius error < 3% (statistical floor ~ sqrt(2/N) = 0.3% here).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from optimizer.core.cmaes import CMAES, optimize
from optimizer.verification.cmaes_reference import (
    BENCHMARKS,
    ReferenceCMAES,
    hansen2016_params,
    rotation_matrix,
    rotated,
)

BOUND = 10.0

# (function, dim, target precision on f - f*, eval cap)
CASES = [
    ("sphere", 2, 1e-12, 40000),
    ("sphere", 5, 1e-10, 40000),
    ("sphere", 10, 1e-8, 40000),
    ("ellipsoid1e6", 2, 1e-10, 40000),
    ("ellipsoid1e6", 5, 1e-6, 40000),
    ("cigar", 2, 1e-10, 40000),
    ("cigar", 5, 1e-6, 40000),
    ("rosenbrock", 2, 1e-10, 40000),
    ("rosenbrock", 5, 1e-6, 40000),
]

PARAM_DIMS = (2, 3, 5, 8, 10, 20)


# ---------------------------------------------------------------------------
# C1: closed-form strategy parameters vs Hansen (2016)
# ---------------------------------------------------------------------------
def check_strategy_parameters(dims=PARAM_DIMS) -> dict:
    rows = []
    for n in dims:
        ref = hansen2016_params(n)
        k = CMAES(n, np.tile([-BOUND, BOUND], (n, 1)))
        got = {"lambda": k.lambda_, "mu": k.mu, "mueff": k.mueff, "cc": k.cc,
               "cs": k.cs, "c1": k.c1, "cmu": k.cmu, "damps": k.damps,
               "chiN": k.chiN}
        rel = {}
        for key, val in got.items():
            denom = max(abs(ref[key]), 1e-300)
            rel[key] = abs(val - ref[key]) / denom
        rel["weights_maxabs"] = float(np.max(np.abs(k.weights - ref["weights"])))
        rows.append({"dim": n, "max_rel_err": max(rel.values()), "rel_err": rel})
    worst = max(r["max_rel_err"] for r in rows)
    return {"rows": rows, "worst_rel_err": worst,
            "pass": bool(worst < 1e-12), "gate": "max_rel_err < 1e-12"}


# ---------------------------------------------------------------------------
# C2: sampling distribution == N(mean, sigma^2 C)
# ---------------------------------------------------------------------------
def check_sampling_fidelity(dim: int = 6, n_pop: int = 20000, seed: int = 11) -> dict:
    rng = np.random.RandomState(3)
    A = rng.randn(dim, dim)
    C_target = A @ A.T + 0.1 * np.eye(dim)          # arbitrary well-conditioned SPD
    sigma = 0.7
    k = CMAES(dim, np.tile([-1e6, 1e6], (dim, 1)), sigma0=sigma, seed=seed)
    k.C = C_target.copy()
    k.mean = np.zeros(dim)
    draws = np.vstack([k.sample_population() for _ in range(n_pop)])
    emp = np.cov(draws, rowvar=False)
    exact = sigma ** 2 * C_target
    rel_fro = float(np.linalg.norm(emp - exact) / np.linalg.norm(exact))
    return {"dim": dim, "n_draws": int(draws.shape[0]), "rel_fro_err": rel_fro,
            "pass": bool(rel_fro < 0.03), "gate": "rel_fro_err < 3%"}


# ---------------------------------------------------------------------------
# C3: evals-to-target, shipped vs independent reference implementation
# ---------------------------------------------------------------------------
def _evals_to_target_kernel(f, n, target, cap, seed):
    b = np.tile([-BOUND, BOUND], (n, 1))
    res = optimize(f, n, b, max_evals=cap, sigma0=1.0, seed=seed, verbose=False,
                   patience=10 ** 6)  # stagnation off: measure pure search efficiency
    for rec in res["history"]:
        if rec["best_fitness"] < target:
            return rec["eval_count"], res["best_fitness"], res
    return None, res["best_fitness"], res


def _evals_to_target_ref(f, n, target, cap, seed):
    ref = ReferenceCMAES(n, 1.0, seed=seed)
    res = ref.run(f, cap, target=target)
    if res["best_fitness"] < target:
        return res["evaluations"], res["best_fitness"], res
    return None, res["best_fitness"], res


def check_convergence(cases=CASES, seed: int = 42) -> dict:
    rows = []
    for name, n, target, cap in cases:
        f = BENCHMARKS[name][0](n)
        nk, bk, rk = _evals_to_target_kernel(f, n, target, cap, seed)
        nr, br, rr = _evals_to_target_ref(f, n, target, cap, seed)
        ratio = (nk / nr) if (nk and nr) else None
        rows.append({
            "function": name, "dim": n, "target": target, "analytic_optimum": 0.0,
            "shipped_best": bk, "shipped_evals_to_target": nk,
            "reference_best": br, "reference_evals_to_target": nr,
            "ratio_shipped_over_reference": ratio,
            "shipped_reached": nk is not None, "reference_reached": nr is not None,
        })
    reached = [r for r in rows if r["shipped_reached"]]
    ratios = [r["ratio_shipped_over_reference"] for r in reached
              if r["ratio_shipped_over_reference"] is not None]
    worst_ratio = max(ratios) if ratios else float("inf")
    return {"rows": rows, "n_reached": len(reached), "n_cases": len(rows),
            "worst_ratio": worst_ratio,
            "pass": bool(len(reached) == len(rows) and worst_ratio <= 3.0),
            "gate": "all cases reach target AND evals ratio (shipped/reference) <= 3"}


# ---------------------------------------------------------------------------
# C4: rotation invariance (Hansen 2016, sec. 3.3)
# ---------------------------------------------------------------------------
def check_rotation_invariance(name: str = "ellipsoid1e6", n: int = 5,
                              target: float = 1e-6, seeds=(1, 2, 3)) -> dict:
    f = BENCHMARKS[name][0](n)
    f_rot = rotated(f, n, seed=7)
    rows = []
    for seed in seeds:
        a, _, _ = _evals_to_target_kernel(f, n, target, 40000, seed)
        b, _, _ = _evals_to_target_kernel(f_rot, n, target, 40000, seed)
        rows.append({"seed": seed, "evals_axis_aligned": a, "evals_rotated": b})
    ok = [r for r in rows if r["evals_axis_aligned"] and r["evals_rotated"]]
    ratios = [r["evals_rotated"] / r["evals_axis_aligned"] for r in ok]
    worst = max(ratios) if ratios else float("inf")
    return {"rows": rows, "worst_ratio": worst,
            "pass": bool(len(ok) == len(rows) and worst <= 3.0),
            "gate": "evals(rotated)/evals(axis-aligned) <= 3 on every seed"}


# ---------------------------------------------------------------------------
# C5: reproducibility and covariance positive definiteness
# ---------------------------------------------------------------------------
def check_reproducibility(seed: int = 42) -> dict:
    f = BENCHMARKS["ellipsoid1e6"][0](6)
    b = np.tile([-BOUND, BOUND], (6, 1))
    r1 = optimize(f, 6, b, max_evals=3000, sigma0=1.0, seed=seed, verbose=False, patience=500)
    r2 = optimize(f, 6, b, max_evals=3000, sigma0=1.0, seed=seed, verbose=False, patience=500)
    identical = (r1["best_fitness"] == r2["best_fitness"]
                 and [h["best_fitness"] for h in r1["history"]]
                 == [h["best_fitness"] for h in r2["history"]])
    return {"best_fitness": r1["best_fitness"], "bit_identical": bool(identical),
            "pass": bool(identical), "gate": "same seed -> bit-identical trajectory"}


def check_covariance_psd() -> dict:
    """min eigen-value of C along a run must stay >= 0 (C is a covariance)."""
    k = CMAES(8, np.tile([-BOUND, BOUND], (8, 1)), sigma0=1.0, seed=5)
    f = BENCHMARKS["ellipsoid1e6"][0](8)
    min_eig = np.inf
    for _ in range(400):
        pop = k.sample_population()
        fit = np.array([f(x) for x in pop])
        k.update(pop, fit)
        min_eig = min(min_eig, float(np.min(np.linalg.eigvalsh(k.C))))
    return {"min_eigenvalue": min_eig, "psd_repairs": k.psd_repairs,
            "pass": bool(min_eig >= -1e-12), "gate": "min eig(C) >= -1e-12"}


def check_psd_repair_guard() -> dict:
    """A deliberately indefinite C must be repaired, not silently isotropised."""
    k = CMAES(4, np.tile([-BOUND, BOUND], (4, 1)), seed=0)
    bad = np.eye(4)
    bad[0, 1] = bad[1, 0] = 5.0        # eigen-values: 6, -4, 1, 1 -> indefinite
    k.C = bad
    pop = k.sample_population()
    min_eig = float(np.min(np.linalg.eigvalsh(k.C)))
    return {"min_eigenvalue_after_repair": min_eig, "psd_repairs": k.psd_repairs,
            "sample_finite": bool(np.all(np.isfinite(pop))),
            "pass": bool(min_eig > 0 and k.psd_repairs == 1 and np.all(np.isfinite(pop))),
            "gate": "indefinite C -> eigen-clamped SPD, repair counter == 1"}


def check_bounds_respected() -> dict:
    """Sampled candidates and the mean must stay inside the declared box."""
    lo, hi = np.array([0.5, -2.0, 1.0]), np.array([1.5, 0.0, 3.0])
    b = np.column_stack([lo, hi])
    k = CMAES(3, b, sigma0=5.0, seed=4)   # sigma >> box: clipping is active
    f = lambda x: float(np.sum((x - 0.9) ** 2))
    worst_viol = 0.0
    mean_viol = 0.0
    for _ in range(120):
        pop = k.sample_population()
        worst_viol = max(worst_viol, float(np.max(np.maximum(lo - pop, pop - hi))))
        fit = np.array([f(x) for x in pop])
        k.update(pop, fit)
        mean_viol = max(mean_viol, float(np.max(np.maximum(lo - k.mean, k.mean - hi))))
    return {"max_sample_violation": worst_viol, "max_mean_violation": mean_viol,
            "pass": bool(worst_viol <= 0.0 and mean_viol <= 0.0),
            "gate": "no sample and no mean outside the box"}


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def run_all(quick: bool = False) -> dict:
    t0 = time.time()
    out = {
        "check_strategy_parameters": check_strategy_parameters(),
        "check_sampling_fidelity": check_sampling_fidelity(),
        "check_convergence": check_convergence(cases=CASES[:4] if quick else CASES),
        "check_rotation_invariance": check_rotation_invariance(),
        "check_reproducibility": check_reproducibility(),
        "check_covariance_psd": check_covariance_psd(),
        "check_psd_repair_guard": check_psd_repair_guard(),
        "check_bounds_respected": check_bounds_respected(),
    }
    checks = {k: v for k, v in out.items() if isinstance(v, dict) and "pass" in v}
    out["summary"] = {
        "checks": {k: v["pass"] for k, v in checks.items()},
        "all_pass": all(v["pass"] for v in checks.values()),
        "n_pass": sum(1 for v in checks.values() if v["pass"]),
        "n_total": len(checks),
        "wall_seconds": round(time.time() - t0, 1),
        "host_loadavg": [float(x) for x in Path("/proc/loadavg").read_text().split()[:3]]
        if Path("/proc/loadavg").exists() else None,
    }
    return out


def make_figures(res: dict, outdir: Path) -> list:
    """Convergence curves + evals-to-target comparison (paper figures)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    files = []

    # Fig 1: convergence curves, shipped vs independent reference
    show = [("sphere", 5, 1e-10), ("ellipsoid1e6", 5, 1e-6),
            ("cigar", 5, 1e-6), ("rosenbrock", 5, 1e-6)]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))
    for ax, (name, n, target) in zip(axes.ravel(), show):
        f = BENCHMARKS[name][0](n)
        _, _, rk = _evals_to_target_kernel(f, n, target, 40000, 42)
        _, _, rr = _evals_to_target_ref(f, n, target, 40000, 42)
        khist = np.array([h["best_fitness"] for h in rk["history"]], dtype=float)
        ax.semilogy(np.arange(1, len(khist) + 1),
                    np.maximum(khist, 1e-300), lw=1.6, label="shipped kernel")
        ax.semilogy(np.arange(1, len(rr["history"]) + 1),
                    np.maximum(rr["history"], 1e-300), lw=1.6, ls="--", label="reference (Hansen 2016)")
        ax.axhline(target, color="k", lw=0.8, ls=":", label="target")
        ax.set_title(f"{name}, N={n}", fontsize=10)
        ax.set_xlabel("generation")
        ax.set_ylabel(r"best $f - f^\ast$")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("CMA-ES: shipped kernel vs independent reference implementation", fontsize=12)
    fig.tight_layout()
    p1 = outdir / "cmaes_convergence_vs_reference.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    files.append({"name": p1.name, "path": str(p1),
                  "caption": "Best-so-far fitness per generation on 5-D analytic benchmarks "
                             "(sphere/ellipsoid/cigar/rosenbrock, f*=0), shipped kernel vs "
                             "from-paper reference implementation."})

    # Fig 2: evals-to-target bars
    rows = res["check_convergence"]["rows"]
    labels = [f"{r['function']}\nN={r['dim']}" for r in rows]
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(11, 4.2))
    w = 0.38
    sk = [r["shipped_evals_to_target"] or 0 for r in rows]
    sr = [r["reference_evals_to_target"] or 0 for r in rows]
    ax.bar(x - w / 2, sk, w, label="shipped")
    ax.bar(x + w / 2, sr, w, label="reference")
    for xi, r in zip(x, rows):
        if r["ratio_shipped_over_reference"]:
            ax.text(xi, max(sk[list(x).index(xi)], sr[list(x).index(xi)]) * 1.02,
                    f"{r['ratio_shipped_over_reference']:.2f}x", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("evaluations to target")
    ax.set_title("Search efficiency vs independent reference (ratio annotated)", fontsize=11)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p2 = outdir / "cmaes_evals_to_target.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    files.append({"name": p2.name, "path": str(p2),
                  "caption": "Evaluations needed to reach the analytic-optimum target; "
                             "annotation = shipped/reference ratio (acceptance gate <= 3)."})

    # Fig 3: error table as a heat map (log10 achieved distance to analytic optimum)
    mat = np.array([[np.log10(max(r["shipped_best"], 1e-300))] for r in rows])
    fig, ax = plt.subplots(figsize=(3.2, 0.42 * len(rows) + 1.2))
    im = ax.imshow(mat, cmap="viridis", aspect="auto")
    ax.set_xticks([0])
    ax.set_xticklabels([r"$\log_{10}(f_{best}-f^\ast)$"])
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([f"{r['function']} N={r['dim']}" for r in rows], fontsize=8)
    for i, r in enumerate(rows):
        ax.text(0, i, f"{r['shipped_best']:.1e}", ha="center", va="center",
                color="w", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.4)
    fig.tight_layout()
    p3 = outdir / "cmaes_achieved_precision.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    files.append({"name": p3.name, "path": str(p3),
                  "caption": "Achieved distance to the analytically known optimum per case."})
    return files


def main() -> dict:
    root = Path(__file__).resolve().parents[2]
    outdir = root / "artifacts" / "cmaes_verification"
    outdir.mkdir(parents=True, exist_ok=True)
    res = run_all()
    figs = make_figures(res, outdir)
    res["figures"] = [{"name": f["name"], "caption": f["caption"]} for f in figs]
    res["artifact_path"] = str(outdir / "cmaes_verification.json")
    (outdir / "cmaes_verification.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res["summary"], indent=2))
    for r in res["check_convergence"]["rows"]:
        print(f"{r['function']:12s} N={r['dim']:2d} shipped={r['shipped_best']:.3e} "
              f"ref={r['reference_best']:.3e} evals {r['shipped_evals_to_target']} vs "
              f"{r['reference_evals_to_target']} ratio={r['ratio_shipped_over_reference']}")
    return res


if __name__ == "__main__":
    main()