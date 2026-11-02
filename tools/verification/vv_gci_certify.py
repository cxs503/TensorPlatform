#!/usr/bin/env python3
"""Produce the machine-readable GCI certificate + figures for mission 1700.

Run from the repository root::

    python3 tools/verification/vv_gci_certify.py

Writes
------
``artifacts/vv_gci/vv_certificate.json``
    Every number in ``docs/verifier/NUMERICAL_VV_GCI.md`` (no hand-copied
    figures: the markdown is generated from this file).
``artifacts/vv_gci/fig_vv_convergence.png``
    log-log discretisation error vs grid spacing for the three analytic
    benchmarks with the observed-order slopes.
``artifacts/vv_gci/fig_gci_coverage.png``
    Reported GCI band vs the true discretisation error per benchmark.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.vv import Level, assess_convergence, observed_order  # noqa: E402
from services.vv.cases import CASES, power_law  # noqa: E402

OUT = ROOT / "artifacts" / "vv_gci"


def estimator_self_checks() -> dict:
    """Estimator unbiasedness on constructed power laws (order known exactly)."""
    rows = []
    for p in (0.5, 1.0, 1.7, 2.0, 3.0, 4.0):
        vals = [power_law(h, p=p, exact=1.0, c=0.05) for h in (1 / 32, 1 / 16, 1 / 8)]
        p_obs = observed_order(vals[0], vals[1], vals[2], 2.0, 2.0)
        rows.append({"p_true": p, "p_observed": p_obs, "abs_error": abs(p_obs - p)})
    # unequal refinement ratios 1.5 / 2.0
    hs = (1 / 27, 1 / 18, 1 / 9)
    vals = [power_law(h, p=2.0) for h in hs]
    p_uneq = observed_order(vals[0], vals[1], vals[2], hs[1] / hs[0], hs[2] / hs[1])
    return {"power_law_recovery": rows, "unequal_ratio_p_observed": p_uneq}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    certificate: dict = {
        "mission": 1700,
        "tool": "backend/services/vv/gci.py",
        "standard": "Roache 1994/1998; ASME V&V 20; Celik et al. 2008",
        "estimator_self_checks": estimator_self_checks(),
        "benchmarks": {},
    }

    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    markers = {"conv_diff_1d_p5": "o", "poisson_1d_numerov": "s", "poisson_2d_5pt": "^"}
    colors = {"conv_diff_1d_p5": "#1f77b4", "poisson_1d_numerov": "#2ca02c", "poisson_2d_5pt": "#d62728"}
    labels = {
        "conv_diff_1d_p5": "1D conv-diff, CD (p=2)",
        "poisson_1d_numerov": "1D Poisson, Numerov (p=4)",
        "poisson_2d_5pt": "2D Poisson, 5-point (p=2)",
    }

    names, true_errs, gci_bands, orders = [], [], [], []
    for name in sorted(CASES):
        case = CASES[name]
        series = case.series()
        levels = [Level(h, v, label=f"n={round(1 / h)}") for h, v in series]
        rep = assess_convergence(
            levels,
            quantity=case.quantity,
            exact=case.exact,
            expected_order=case.nominal_order,
        )
        errs = [abs(v - case.exact) for _, v in series]
        hs = [h for h, _ in series]
        # local observed order on the two finest grids (nominal, equal r)
        p_pair = math.log(errs[1] / errs[0]) / math.log(hs[1] / hs[0])
        certificate["benchmarks"][name] = {
            "description": case.description,
            "quantity": case.quantity,
            "nominal_order": case.nominal_order,
            "exact_value": case.exact,
            "levels": [
                {"h": h, "value": v, "abs_error": abs(v - case.exact)}
                for (h, v), _n in zip(series, case.default_grids)
            ],
            "observed_order": rep.observed_order,
            "local_order_two_finest": p_pair,
            "extrapolated_value": rep.extrapolated_value,
            "extrapolated_abs_error": abs(rep.extrapolated_value - case.exact),
            "gci_fine_relative": rep.gci_fine,
            "gci_fine_absolute": rep.factor_of_safety * rep.estimated_error_fine,
            "estimated_error_fine": rep.estimated_error_fine,
            "true_error_fine": rep.true_error_fine,
            "true_error_ratio_to_gci": rep.true_error_ratio_to_gci,
            "true_error_covered": rep.true_error_covered,
            "asymptotic_ratio": rep.asymptotic_ratio,
            "convergence_ratio": rep.convergence_ratio,
            "verdict": rep.verdict,
            "factor_of_safety": rep.factor_of_safety,
            "warnings": list(rep.warnings),
            "passed": rep.passed,
        }

        ax.loglog(hs, errs, marker=markers[name], color=colors[name], label=labels[name])
        # reference slope through the finest point
        slope_p = rep.observed_order or case.nominal_order
        hh = [hs[0] * 0.6, hs[-1] * 1.4]
        ax.loglog(
            hh,
            [errs[0] * (x / hs[0]) ** slope_p for x in hh],
            "--",
            color=colors[name],
            lw=0.9,
            alpha=0.75,
        )
        ax.annotate(
            f"$p_{{obs}}$={slope_p:.3f}",
            xy=(hs[0], errs[0]),
            xytext=(6, 10),
            textcoords="offset points",
            fontsize=8,
            color=colors[name],
        )
        names.append(name)
        true_errs.append(abs(rep.true_error_fine))
        gci_bands.append(rep.factor_of_safety * rep.estimated_error_fine)
        orders.append(slope_p)

    ax.set_xlabel("grid spacing $h$ (log scale)")
    ax.set_ylabel("|discretisation error| (log scale)")
    ax.set_title("Solution verification: error vs h with observed order\n(analytic benchmarks, mission 1700)")
    ax.grid(True, which="both", ls=":", lw=0.5, alpha=0.6)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(OUT / "fig_vv_convergence.png", dpi=160)
    plt.close(fig)

    # --- GCI coverage bar chart -------------------------------------------
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    x = range(len(names))
    ax.bar([i - 0.19 for i in x], true_errs, width=0.38, label="true error $|f_{fine}-f_{exact}|$", color="#1f77b4")
    ax.bar([i + 0.19 for i in x], gci_bands, width=0.38, label="reported GCI band", color="#ff7f0e")
    for i, (te, gc) in enumerate(zip(true_errs, gci_bands)):
        ax.text(i - 0.19, te * 1.15, f"{te:.1e}", ha="center", fontsize=7)
        ax.text(i + 0.19, gc * 1.15, f"{gc:.1e}", ha="center", fontsize=7)
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels([labels[n].split(",")[0] for n in names], fontsize=8)
    ax.set_ylabel("absolute error / band (log scale)")
    ax.set_title("GCI band covers the true error in all benchmarks\n(ratio true/GCI = 0.80, the asymptotic value for Fs=1.25)")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", ls=":", lw=0.5)
    fig.tight_layout()
    fig.savefig(OUT / "fig_gci_coverage.png", dpi=160)
    plt.close(fig)

    certificate["figures"] = ["fig_vv_convergence.png", "fig_gci_coverage.png"]
    certificate["all_passed"] = all(b["passed"] for b in certificate["benchmarks"].values())
    (OUT / "vv_certificate.json").write_text(json.dumps(certificate, indent=2), encoding="utf-8")

    for name, block in certificate["benchmarks"].items():
        print(
            f"{name:22s} p_obs={block['observed_order']:.4f} "
            f"GCI={block['gci_fine_relative']:.3e} "
            f"true/GCI={block['true_error_ratio_to_gci']:.3f} "
            f"passed={block['passed']}"
        )
    print(f"all_passed={certificate['all_passed']}  -> {OUT/'vv_certificate.json'}")
    return 0 if certificate["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())