#!/usr/bin/env python3
"""Acceptance run for the uncertainty-quantification framework v1.

Produces, under ``artifacts/uq_framework_v1/``:

* ``<case>_uq_report.json`` + two PNGs per case (confidence interval,
  sensitivity), with SHA-256 digests inside the JSON;
* ``acceptance_summary.json`` -- every criterion with the number that decided it,
  the 3x3 case matrix, the Monte Carlo convergence table, the GCI order table,
  the model-form comparison table, and the environment/provenance block;
* ``fig_uq_overview.png`` -- the two figures the report narrative needs
  (moment error vs sample budget, and observed vs formal order of accuracy).

Usage::

    python3 tools/uq_framework/run_acceptance.py [--outdir DIR] [--fast]
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.uq_framework import run_uq, write_report  # noqa: E402
from backend.services.uq_framework.acceptance_cases import (  # noqa: E402
    INPUT_CASES,
    MODEL_FORM_CASES,
    NUMERIC_CASES,
    convergence_study,
    full_matrix_case,
)
from backend.services.uq_framework.synthetic import (  # noqa: E402
    diffusion_bvp,
    linear_response,
    model_ensemble,
    relaxation,
)
from backend.services.uq_framework.marginals import InputDistribution  # noqa: E402

DEFAULT_OUT = ROOT / "artifacts" / "uq_framework_v1"


def _crit(res, keys):
    return {k: {"status": res.criteria[k]["status"], "value": res.criteria[k]["value"],
                "threshold": res.criteria[k]["threshold"], "unit": res.criteria[k]["unit"],
                "detail": res.criteria[k]["detail"]} for k in keys}


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def figure_overview(conv, order_rows, path: Path) -> Path:
    """Two-panel diagnostic: MC convergence and GCI order recovery."""
    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.0), dpi=130)

    n = [r["n_samples"] for r in conv]
    axes[0].loglog(n, [max(r["mean_rel_err_median"], 1e-7) for r in conv], "o-",
                   label="mean, median over seeds")
    axes[0].loglog(n, [r["variance_rel_err_median"] for r in conv], "s-",
                   label="variance, median over seeds")
    axes[0].loglog(n, [r["variance_rel_err_expected"] for r in conv], "k--", lw=1.0,
                   label=r"$\sqrt{2/n}$ (iid variance se)")
    axes[0].axhline(0.02, color="C3", lw=1.2, ls=":",
                    label="2 % acceptance threshold")
    axes[0].set_xlabel("Monte Carlo samples")
    axes[0].set_ylabel("relative deviation from the analytic value")
    axes[0].set_title("input propagation: error vs budget")
    axes[0].legend(fontsize=7)

    labels = [r["case"] for r in order_rows]
    obs = [r["p_obs"] for r in order_rows]
    exp = [r["p_expected"] for r in order_rows]
    x = np.arange(len(labels))
    axes[1].bar(x - 0.2, exp, width=0.4, color="0.65", label="formal order")
    axes[1].bar(x + 0.2, obs, width=0.4, color="C2", label="observed (GCI)")
    for xi, (o, e) in enumerate(zip(obs, exp)):
        axes[1].annotate(f"{o:.2f}\n({abs(o - e) / e * 100:.1f}%)", (xi + 0.2, o),
                         textcoords="offset points", xytext=(0, 4), ha="center", fontsize=7)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(labels, fontsize=8)
    axes[1].set_ylabel("order of accuracy p")
    axes[1].set_title("numerical error: GCI reproduces the formal order")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=str(DEFAULT_OUT))
    ap.add_argument("--fast", action="store_true", help="skip the 1e5 convergence point")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    summary = {
        "framework": "uq_framework/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {"python": sys.version.split()[0], "numpy": np.__version__,
                        "platform": platform.platform()},
        "cases": {}, "matrix": {},
    }

    # ---- 3 x 3 case matrix ----------------------------------------------
    for category, cases in (("input_propagation", INPUT_CASES),
                            ("numerical_error", NUMERIC_CASES),
                            ("model_form", MODEL_FORM_CASES)):
        rows = {}
        for case_id, build in cases.items():
            solvers, cfg, expected, reference = build()
            res = run_uq(solvers, cfg)
            bundle = write_report(res, out, make_figures=not args.no_figures)
            rows[case_id] = {
                "criteria": _crit(res, expected),
                "all_expected_pass": all(res.criteria[k]["status"] == "pass" for k in expected),
                "headline_method": res.headline_method,
                "mean": res.mean, "variance": res.variance, "std": res.std,
                "reference": {k: v for k, v in reference.items() if k != "sobol"},
                "report_json": str(bundle.json_path),
                "figures": {k: {"path": v["path"], "sha256": v["sha256"]}
                            for k, v in bundle.figures.items()},
            }
            if category == "numerical_error":
                rows[case_id]["p_obs"] = res.numerical["discretization"]["p_obs"]
                rows[case_id]["gci_fine_pct"] = res.numerical["discretization"]["gci_fine_pct"]
            if category == "model_form":
                rows[case_id]["model_form"] = {
                    "weights": res.model_form["weights"],
                    "deviation_vs_truth": res.model_form["deviation_vs_truth"],
                    "deviation_vs_truth_bias_corrected":
                        res.model_form["deviation_vs_truth_bias_corrected"],
                    "model_form_share": res.model_form["model_form_share"],
                    "model_form_rel_spread": res.model_form["model_form_rel_spread"],
                }
            print(f"[{category}] {case_id}: "
                  + ", ".join(f"{k}={res.criteria[k]['status']}" for k in expected))
        summary["matrix"][category] = rows
        assert len(rows) >= 3

    # ---- full matrix case (all three legs at once) -----------------------
    solvers, cfg, ens = full_matrix_case()
    res = run_uq(solvers, cfg)
    bundle = write_report(res, out, make_figures=not args.no_figures)
    summary["cases"]["full_matrix_case"] = {
        "criteria": {k: {"status": v["status"], "value": v["value"],
                         "threshold": v["threshold"], "stage": "all"}
                     for k, v in res.criteria.items()},
        "all_evaluated_pass": res.all_criteria_pass,
        "mean": res.mean, "std": res.std, "headline_method": res.headline_method,
        "numerical": res.numerical, "model_form": res.model_form,
        "report_json": str(bundle.json_path),
        "figures": {k: {"path": v["path"], "sha256": v["sha256"]}
                    for k, v in bundle.figures.items()},
    }

    # ---- Monte Carlo convergence (the honest scale of the 2 % rule) ------
    func, meta = linear_response([2.0, 3.0], 1.0)
    budgets = (1000, 10000) if args.fast else (1000, 10000, 100000)
    conv = convergence_study((func, meta), budgets=budgets)
    summary["mc_convergence"] = conv
    print("mc_convergence:", [(r["n_samples"], round(r["mean_rel_err_median"], 6),
                               round(r["variance_rel_err_median"], 5)) for r in conv])

    # ---- GCI order table -------------------------------------------------
    order_rows = []
    for case_id, (qoi, p_expected) in {
        "euler_p1": (lambda n: relaxation(n, "euler")["qoi"], 1.0),
        "elliptic_p2": (lambda n: diffusion_bvp(n)["qoi"], 2.0),
        "rk4_p4": (lambda n: relaxation(n, "rk4")["qoi"], 4.0),
    }.items():
        res_num = summary["matrix"]["numerical_error"][f"num_{case_id}"]
        order_rows.append({"case": case_id, "p_expected": p_expected,
                           "p_obs": res_num["p_obs"],
                           "deviation": abs(res_num["p_obs"] - p_expected) / p_expected,
                           "gci_fine_pct": res_num["gci_fine_pct"]})
    summary["gci_order_table"] = order_rows

    # band vs true error for the analytic QoI
    sols = [diffusion_bvp(n) for n in (80, 40, 20)]
    true_err = abs(sols[0]["qoi"] - sols[0]["exact_mid"])
    band = summary["matrix"]["numerical_error"]["num_elliptic_p2"]["gci_fine_pct"] / 100.0
    summary["gci_band_vs_true_error"] = {
        "true_error_rel": true_err / sols[0]["exact_mid"],
        "gci_band_rel": band,
        "band_over_true_error": band / (true_err / sols[0]["exact_mid"]),
        "brackets": true_err / sols[0]["exact_mid"] <= 3.0 * band,
    }

    # ---- model-form comparison table ------------------------------------
    mf_rows = []
    for kind, dists in (("affine", [InputDistribution.normal("x0", 0.0, 1.0),
                                    InputDistribution.uniform("x1", -1.0, 1.0)]),
                        ("competing_biases", [InputDistribution.normal("x0", 0.0, 1.0),
                                              InputDistribution.uniform("x1", -1.0, 1.0)]),
                        ("truncation", [InputDistribution.normal("x0", 0.0, 1.0),
                                        InputDistribution.uniform("x1", -1.0, 1.0),
                                        InputDistribution.triangular("x2", 0.0, 1.0, 4.0)])):
        ens2 = model_ensemble(kind, seed=0)
        from backend.services.uq_framework import compare_models
        from backend.services.uq_framework.acceptance_cases import validation_set

        val = validation_set(ens2, dists)
        for weight in ("equal", "bma"):
            r = compare_models(ens2["models"], dists, n=600, weights=weight,
                               validation=val, truth=ens2["truth"])
            mf_rows.append({"ensemble": kind, "weights": weight,
                            "ensemble_mean": r.ensemble_mean,
                            "deviation_vs_truth": r.deviation_vs_truth,
                            "deviation_vs_truth_bias_corrected": r.deviation_vs_truth_bc,
                            "model_form_rel_spread": r.model_form_rel_spread,
                            "model_form_share": r.model_form_share,
                            "weights_value": r.weights, "best_single": r.best_single})
    summary["model_form_table"] = mf_rows
    from backend.services.uq_framework.acceptance_cases import model_form_robustness

    summary["model_form_robustness"] = {
        "truncation": model_form_robustness("truncation"),
        "competing_biases": model_form_robustness("competing_biases", n=600),
    }

    # ---- overview figure -------------------------------------------------
    if not args.no_figures:
        fig = figure_overview(conv, order_rows, out / "fig_uq_overview.png")
        summary["overview_figure"] = str(fig)

    # ---- verdict ---------------------------------------------------------
    matrix_ok = all(row["all_expected_pass"]
                    for rows in summary["matrix"].values() for row in rows.values())
    summary["verdict"] = {
        "C1_input_propagation": all(
            row["criteria"]["C1_mc_mean"]["status"] == "pass"
            for row in summary["matrix"]["input_propagation"].values()),
        "C2_pce_efficiency": all(
            row["criteria"][k]["status"] == "pass"
            for row in summary["matrix"]["input_propagation"].values()
            for k in ("C2_pce_mean", "C2_pce_std", "C2_pce_speedup", "C2_pce_r2")),
        "C3_gci_order": all(abs(r["deviation"]) < 0.10 for r in order_rows),
        "C4_model_form": all(
            row["model_form"]["deviation_vs_truth"] < 0.10
            for row in summary["matrix"]["model_form"].values()),
        "C5_matrix_3x3": matrix_ok and all(len(v) >= 3 for v in summary["matrix"].values()),
        "C7_report": all(Path(row["report_json"]).exists()
                         for rows in summary["matrix"].values() for row in rows.values()),
    }
    (out / "acceptance_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print("verdict:", json.dumps(summary["verdict"]))
    print("written:", out / "acceptance_summary.json")
    return 0 if all(summary["verdict"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())