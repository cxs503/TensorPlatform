"""Reproduce the multi-solver coupling verification and emit artifacts.

Usage (from ``backend/``)::

    python -m services.multi_solver_coupling.run_verification \
        --out ../artifacts/m8045 --run-pytest

Writes ``multi_solver_coupling_verification.json`` plus three figures:
the coupled field vs the closed-form solution, the coupling-iteration residual
histories, and the mesh-refinement convergence of the interface quantities.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from .coupling import DirichletNeumannCoupler
from .solvers.base import dirichlet
from .verification import (
    CASE_PIECEWISE_K,
    CASE_VARIABLE_K,
    FLUX_TOL,
    RESIDUAL_TOL,
    build_pair,
    run_all,
    run_pair,
)

CASE_HIGH_CONTRAST = {
    "name": "high_contrast_k",
    "description": "k=1 on [0,0.5], k=10 on [0.5,1]: exact T_I=10/11, q=-20/11",
    "domain": (0.0, 1.0),
    "interface": 0.5,
    "k_left": 1.0,
    "k_right": 10.0,
    "source_left": None,
    "source_right": None,
    "left_bc": 0.0,
    "right_bc": 1.0,
    "n_left": 100,
    "n_right": 50,
}


def convergence_study() -> dict:
    """Residual histories: Aitken on all pairs + a fixed-omega baseline."""
    histories = []
    specs = [
        (CASE_VARIABLE_K, ("FVM", "FEM"), "aitken"),
        (CASE_PIECEWISE_K, ("FVM", "BEM"), "aitken"),
        (CASE_PIECEWISE_K, ("FEM", "BEM"), "aitken"),
        (CASE_HIGH_CONTRAST, ("FVM", "BEM"), 0.5),
        (CASE_HIGH_CONTRAST, ("FVM", "BEM"), "aitken"),
    ]
    for case, pair, relax in specs:
        sol_a, sol_b = build_pair(case, pair)
        coupler = DirichletNeumannCoupler(
            sol_a, sol_b, float(case["interface"]),
            dirichlet(float(case["left_bc"])), dirichlet(float(case["right_bc"])),
            tol=RESIDUAL_TOL, max_iter=400, relaxation=relax,
        )
        res = coupler.run()
        histories.append(
            {
                "label": f"{pair[0]}|{pair[1]} {case['name']} "
                         f"({relax if isinstance(relax, str) else f'omega={relax}'})",
                "case": case["name"],
                "pair": list(pair),
                "relaxation": relax if isinstance(relax, str) else float(relax),
                "iterations": res.iterations,
                "final_residual": res.final_residual,
                "t_interface": res.t_interface,
                "flux_rel_error": res.flux_rel_error,
                "residual_history": res.residual_history,
            }
        )
    return {"histories": histories}


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def _fig_field(out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    res = run_pair(CASE_VARIABLE_K, ("FVM", "FEM"))
    xa, Ta = res.left.x, res.left.T
    xb, Tb = res.right.x, res.right.T
    xs = np.linspace(0.0, 1.0, 400)
    exact = np.asarray(CASE_VARIABLE_K["exact_T"](xs))
    xi = float(CASE_VARIABLE_K["interface"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.plot(xs, exact, "k-", lw=2, label="closed form $e^{x}$")
    ax1.plot(xa, Ta, "o", ms=4, label="sub-domain A (FVM, $k=1+x$, $f=-(2+x)e^x$)")
    ax1.plot(xb, Tb, "s", ms=4, label="sub-domain B (FEM)")
    ax1.axvline(xi, color="0.4", ls="--", lw=1)
    ax1.set_xlabel("x [m]")
    ax1.set_ylabel("T [K]")
    ax1.set_title("coupled field vs closed form")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    ax2.semilogy(xa, np.abs(Ta - np.asarray(CASE_VARIABLE_K["exact_T"](xa))) + 1e-18, "o-", ms=3, label="FVM")
    ax2.semilogy(xb, np.abs(Tb - np.asarray(CASE_VARIABLE_K["exact_T"](xb))) + 1e-18, "s-", ms=3, label="FEM")
    ax2.set_xlabel("x [m]")
    ax2.set_ylabel("$|T - T_{exact}|$ [K]")
    ax2.set_title(
        f"pointwise error (interface flux closure {res.flux_rel_error:.1e})"
    )
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = out / "fig1_coupled_field.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _fig_convergence(out: Path, study: dict) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.6, 4.8))
    for item in study["histories"]:
        hist = item["residual_history"]
        ax.semilogy(
            np.arange(1, len(hist) + 1),
            np.maximum(hist, 1e-17),
            marker="o",
            ms=3.2,
            lw=1.4,
            label=f"{item['label']}  ({item['iterations']} it, "
                  f"r={item['final_residual']:.1e})",
        )
    ax.axhline(RESIDUAL_TOL, color="crimson", ls="--", lw=1.2,
               label=f"acceptance bound {RESIDUAL_TOL:.0e}")
    ax.set_xlabel("iteration k")
    ax.set_ylabel("interface residual $r^k$ [-]")
    ax.set_title("Dirichlet--Neumann coupling convergence")
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = out / "fig2_coupling_convergence.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _fig_refinement(out: Path, refined: dict, bem: dict) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = refined["rows"]
    h = np.array([r["h"] for r in rows])
    et = np.array([r["t_rel_error"] for r in rows])
    eq = np.array([r["q_rel_error"] for r in rows])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.loglog(h, et, "o-", label=f"interface T (order {refined['observed_order_T']:.2f})")
    ax1.loglog(h, eq, "s-", label=f"interface q (order {refined['observed_order_q']:.2f})")
    ref = et[0] * (h / h[0]) ** 2
    ax1.loglog(h, ref, "k--", lw=1, label="2nd-order reference")
    ax1.set_xlabel("h = 0.5/N [m]")
    ax1.set_ylabel("relative error vs closed form [-]")
    ax1.set_title(f"refinement, pair {refined['pair'][0]}|{refined['pair'][1]}")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3, which="both")

    panels = np.array([r["panels"] for r in bem["rows"]])
    lin = np.array([r["linear_max_rel_error"] for r in bem["rows"]])
    quad = np.array([r["quadratic_max_rel_error"] for r in bem["rows"]])
    ax2.loglog(panels, lin, "o-", label="BEM 2-D, T=x")
    ax2.loglog(panels, quad, "s-", label="BEM 2-D, T=x$^2-y^2$")
    ax2.set_xlabel("boundary panels [-]")
    ax2.set_ylabel("max rel. error of $q_n$ [-]")
    ax2.set_title("boundary-element kernel validation (unit disk)")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = out / "fig3_refinement_and_bem_kernel.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="artifacts/m8045", help="artifact directory")
    parser.add_argument("--run-pytest", action="store_true",
                        help="run the regression suite and fold its result in")
    parser.add_argument("--no-refine", action="store_true")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    xml = out / "pytest_multi_solver_coupling.xml"
    if args.run_pytest:
        cmd = [
            sys.executable, "-m", "pytest",
            "tests/test_multi_solver_coupling.py",
            f"--junitxml={xml}", "-q",
        ]
        backend = Path(__file__).resolve().parents[2]
        proc = subprocess.run(cmd, cwd=backend, capture_output=True, text=True)
        (out / "pytest_multi_solver_coupling.log").write_text(
            proc.stdout + "\n" + proc.stderr, encoding="utf-8"
        )
        if proc.returncode != 0:
            print(proc.stdout[-4000:])
            print(proc.stderr[-2000:])
            print("pytest FAILED -- verification numbers are still written")

    payload = run_all(pytest_xml=xml if xml.is_file() else None,
                      refine=not args.no_refine)
    study = convergence_study()
    payload["convergence_study"] = study

    c = payload["criteria"]
    payload["judgement"] = {
        "c1_flux_conservation": c["c1_energy_flux_conservation"]["pass"],
        "c2_contract_standardization": c["c2_contract_standardization"]["pass"],
        "c3_coupling_convergence": c["c3_coupling_convergence"]["pass"],
        "c4_regression": c["c4_regression_tests"].get("pass", False),
        "headline": {
            "max_interface_flux_rel_error": c["c1_energy_flux_conservation"]["max_rel_error"],
            "flux_bound": FLUX_TOL,
            "n_contract_pairs": c["c2_contract_standardization"]["n_pairs"],
            "contract_coverage": c["c2_contract_standardization"]["coverage_live"],
            "max_coupling_residual": c["c3_coupling_convergence"]["max_final_residual"],
            "residual_bound": RESIDUAL_TOL,
            "regression_pass_rate": c["c4_regression_tests"].get("pass_rate"),
        },
    }
    payload["all_pass"] = bool(all(payload["judgement"][k] for k in
                                   ("c1_flux_conservation", "c2_contract_standardization",
                                    "c3_coupling_convergence", "c4_regression")))

    figures = [
        _fig_field(out),
        _fig_convergence(out, study),
        _fig_refinement(out, payload["refinement_study"], c["c2_contract_standardization"]["bem_kernel_validation"]),
    ]
    payload["figures"] = [str(p) for p in figures]

    target = out / "multi_solver_coupling_verification.json"
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print(json.dumps(payload["judgement"], indent=2, default=str))
    print(f"artifacts -> {target}")
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())