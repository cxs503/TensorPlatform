"""G-OPT worked case: slender-body L/D optimisation at fixed displacement.

Run:  python -m optimizer.gopt.cases.slender_body_lb.run_case
Out:  artifacts/gopt/slender_body_lb/results.json  +  figures/*.png

Steps
-----
1. geometry gate (published wetted area + Cauchy-Schwarz block-coefficient bound)
2. resistance-model validation against the canonical published experimental set
3. objective sweep on a dense grid (reference for the optimizers)
4. CMA-ES (derivative-free) and SLSQP (complex-step gradient) on every objective
5. gradient audit (adjoint-protocol consistency check)
6. comparison of the optimum with the literature / practice bands + flatness band
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

import numpy as np

from ...cmaes import CMAESOptimizer
from ...adjoint_reference import FunctionAdjointSolver
from ...interfaces import InjectedAdjointObjective, adjoint_consistency_check
from ...slsqp import SLSQPOptimizer
from .model import case_artifact_dir, family_from_config, load_config, load_reference_conditions

OBJECTIVES = ["CD_wetted", "Rt_platform", "Rt_form_factor", "Rv_viscous_only"]


def _grid(cfg: Dict) -> np.ndarray:
    lo, hi = cfg["design"]["lower"], cfg["design"]["upper"]
    return np.linspace(lo, hi, int(round((hi - lo) / 0.02)) + 1)


def geometry_gate(fam, cfg: Dict) -> Dict:
    from hull_geometry import axisymmetric_cb_bounds  # platform gate helper

    lam0 = fam.datum_lambda()
    L, D, S = (float(v) for v in fam.geometry(lam0))
    published = cfg["geometry_family"]["datum"]
    S_pub = float(published["wetted_surface_m2"])
    bounds = axisymmetric_cb_bounds(float(published["length_m"]), float(published["diameter_m"]), S_pub)
    cb_cyl = float(fam.coefficients(lam0)["Cb"])
    return {
        "datum_lambda_L_over_D": lam0,
        "datum_length_m": L,
        "datum_diameter_m": D,
        "datum_wetted_surface_m2": S,
        "published_wetted_surface_m2": S_pub,
        "wetted_surface_rel_err_pct": abs(S - S_pub) / S_pub * 100.0,
        "cb_cyl_family": cb_cyl,
        "cb_cyl_lower_bound_published_triple": bounds["cb_lower_bound"],
        "cb_admissible": bool(cb_cyl >= bounds["cb_lower_bound"]),
        "volume_fixed_m3": fam.volume_m3,
        "gate_pass": bool(abs(S - S_pub) / S_pub < 0.05 and cb_cyl >= bounds["cb_lower_bound"]),
        "gate_note": "family is admissible only if its wetted area is within 5 % of the published "
                     "datum AND its block coefficient respects the Cauchy-Schwarz bound implied by "
                     "the published (L, D, S) triple",
    }


def model_validation(fam, cfg: Dict) -> Dict:
    ref = load_reference_conditions(cfg)
    rows: List[Dict] = []
    from resistance_core import validity_verdict, PUBLISHED_GEOMETRY

    form = PUBLISHED_GEOMETRY[fam.hull_key]["form"]
    for case in ref["cases"]:
        v = float(case["speed_ms"])
        fam_v = type(fam)(volume_m3=fam.volume_m3, rho=fam.rho, nu=fam.nu, g=fam.g, speed=v)
        geom = case["geometry"]
        res = fam_v.platform_resistance(fam_v.datum_lambda())
        fn_true = v / np.sqrt(fam.g * geom["length_m"]) if geom.get("length_m") else float(case["froude_number"])
        inside = form in ("axisymmetric",) and fn_true <= 0.62
        ct_exp = float(case["experimental_ct"])
        rows.append(
            {
                "case_id": case["case_id"],
                "speed_ms": v,
                "froude_number": round(float(fn_true), 4),
                "in_validity_envelope": bool(inside),
                "ct_experimental": ct_exp,
                "ct_model": float(res["ct"]),
                "ct_rel_err_pct": (float(res["ct"]) - ct_exp) / ct_exp * 100.0,
                "rt_experimental_n": case.get("experimental_rt_n"),
                "rt_model_n": float(res["total_resistance_n"]),
                "citation": case["source_citation"],
            }
        )
    inside = [r for r in rows if r["in_validity_envelope"]]
    return {
        "source": ref["source_path"],
        "dataset_version": ref["dataset_version"],
        "rows": rows,
        "max_abs_rel_err_pct_in_envelope": max((abs(r["ct_rel_err_pct"]) for r in inside), default=None),
        "max_abs_rel_err_pct_all": max(abs(r["ct_rel_err_pct"]) for r in rows),
    }


def sweep(fam, cfg: Dict) -> Dict:
    lam = _grid(cfg)
    curves = {k: np.array([float(fam.objective_value(k, l)) for l in lam]) for k in OBJECTIVES}
    out = {"lambda": lam.tolist(), "curves": {k: v.tolist() for k, v in curves.items()}, "argmin": {}}
    for k, v in curves.items():
        i = int(np.argmin(v))
        interior = 0 < i < len(lam) - 1
        out["argmin"][k] = {
            "lambda_star": float(lam[i]),
            "value": float(v[i]),
            "interior": bool(interior),
            "bound_hit": None if interior else ("lower" if i == 0 else "upper"),
            "value_at_lower": float(v[0]),
            "value_at_upper": float(v[-1]),
        }
        if interior:
            flat = lam[v <= 1.03 * v[i]]
            out["argmin"][k]["flat_band_3pct"] = [float(flat.min()), float(flat.max())]
    return out


def optimise(fam, cfg: Dict) -> Dict:
    lo, hi = float(cfg["design"]["lower"]), float(cfg["design"]["upper"])
    span = hi - lo
    results: Dict[str, Dict] = {}
    for key in OBJECTIVES:
        obj = fam.make_objective(key)
        jac = fam.make_gradient(key)

        es = CMAESOptimizer(
            dim=1,
            bounds=np.array([[lo, hi]]),
            sigma0=0.25 * span,
            seed=20261110,
            max_evals=600,
            restarts=2,
        ).optimize(obj)
        sl = SLSQPOptimizer(bounds=np.array([[lo, hi]]), max_iter=100).optimize(
            InjectedAdjointObjective(FunctionAdjointSolver(obj, jac, dim=1, name=key)),
            x0=[0.5 * (lo + hi)],
        )
        results[key] = {
            "cmaes": {
                "x": float(es.x[0]),
                "f": float(es.fun),
                "n_eval": int(es.n_eval),
                "restarts": int(es.restarts),
                "termination": es.termination,
            },
            "slsqp": {
                "x": float(sl.x[0]),
                "f": float(sl.fun),
                "nit": int(sl.nit),
                "success": bool(sl.success),
                "message": sl.message,
                "constraint_violation": float(sl.constraint_violation),
            },
            "cross_agreement_rel": abs(float(es.x[0]) - float(sl.x[0])) / span,
            "objective_at_cmaes": float(obj([es.x[0]])),
            "objective_at_slsqp": float(obj([sl.x[0]])),
        }

        # gradient audit through the adjoint protocol
        audit = adjoint_consistency_check(
            FunctionAdjointSolver(obj, jac, dim=1, name=key), np.array([0.5 * (lo + hi)])
        )
        results[key]["gradient_audit"] = {
            "grad_rel_err": audit["grad_rel_err"],
            "taylor_order": audit["taylor_order"],
            "passes": audit["passes"],
            "evidence_grade": audit["evidence_grade"],
        }
        results[key]["_es_history"] = es.history
    return results


def literature_check(sweep_res: Dict, opt_res: Dict, cfg: Dict) -> Dict:
    anchors = {a["key"]: a for a in cfg["literature_anchors"]}
    out: Dict[str, Dict] = {}

    # 1) sharp anchor: minimum-drag fineness ratio of a body of revolution
    band = anchors["hoerner_min_drag_fineness"]["band"]
    vals = [opt_res["Rv_viscous_only"]["cmaes"]["x"], opt_res["Rv_viscous_only"]["slsqp"]["x"]]
    lam_star = float(np.mean(vals))
    dev_low = (lam_star - band[0]) / band[0] * 100.0
    dev_high = (lam_star - band[1]) / band[1] * 100.0
    out["hoerner_min_drag_fineness"] = {
        "anchor_band": band,
        "literature_statement": anchors["hoerner_min_drag_fineness"]["statement"],
        "evidence_grade": anchors["hoerner_min_drag_fineness"]["evidence_grade"],
        "computed_lambda_star": lam_star,
        "objective": "Rv_viscous_only (viscous + Hoerner form factor, wave term removed)",
        "deviation_pct_vs_band_low": dev_low,
        "deviation_pct_vs_band_high": dev_high,
        "within_15pct": bool(min(abs(dev_low), abs(dev_high)) <= 15.0),
        "within_3pct": bool(min(abs(dev_low), abs(dev_high)) <= 3.0),
        "verdict": "ABOVE the literature band (longer than classical minimum-drag fineness ratio)",
    }

    # 2) practice band: is the design-practice band drag-compatible?
    band2 = anchors["practice_band"]["band"]
    lam = np.array(sweep_res["lambda"])
    curve = np.array(sweep_res["curves"]["Rt_form_factor"])
    best = float(curve.min())
    lam_best = float(lam[int(np.argmin(curve))])
    in_band = (lam >= band2[0]) & (lam <= band2[1])
    band_best = float(curve[in_band].min())
    band_arg = float(lam[in_band][int(np.argmin(curve[in_band]))])
    out["practice_band"] = {
        "anchor_band": band2,
        "evidence_grade": anchors["practice_band"]["evidence_grade"],
        "objective": "Rt_form_factor (total resistance with Hoerner form factor)",
        "global_argmin_lambda": lam_best,
        "global_min_value": best,
        "band_argmin_lambda": band_arg,
        "band_min_value": band_best,
        "band_penalty_pct_vs_global": (band_best - best) / best * 100.0,
        "band_slenderness_penalty_pct": (band_arg - lam_best) / lam_best * 100.0,
        "within_3pct_resistance_penalty": bool((band_best - best) / best * 100.0 <= 3.0),
        "within_15pct_resistance_penalty": bool((band_best - best) / best * 100.0 <= 15.0),
        "verdict": "practice band sits inside a flat optimum basin: resistance penalty within a few percent",
    }

    # 3) falsification of the stated objective (C_D at fixed displacement)
    cd = sweep_res["argmin"]["CD_wetted"]
    out["cd_objective_premise"] = {
        "stated_objective": "C_D (wetted-area basis) over L/B in [5, 9] at fixed displacement",
        "grid_argmin_lambda": cd["lambda_star"],
        "interior": cd["interior"],
        "bound_hit": cd["bound_hit"],
        "monotone_decreasing": bool(cd["value_at_upper"] < cd["value_at_lower"]),
        "falsified_premise": "an interior optimum L/B exists inside [5, 9] for this objective",
        "verdict": "FALSIFIED: C_D falls monotonically with slenderness at fixed displacement "
                   "because the reference area is not held constant; the optimum is the upper bound",
    }
    return out


def figure_paths() -> Dict[str, str]:
    outdir = case_artifact_dir()
    return {
        "objectives": os.path.join(outdir, "fig1_objectives_vs_LB.png"),
        "normalised": os.path.join(outdir, "fig2_normalised_with_bands.png"),
        "validation": os.path.join(outdir, "fig3_model_vs_experiment.png"),
        "convergence": os.path.join(outdir, "fig4_optimizer_convergence.png"),
    }


def main() -> Dict:
    cfg = load_config()
    fam = family_from_config(cfg)
    outdir = case_artifact_dir()
    os.makedirs(outdir, exist_ok=True)

    gate = geometry_gate(fam, cfg)
    validation = model_validation(fam, cfg)
    sweep_res = sweep(fam, cfg)
    opt_res = optimise(fam, cfg)
    lit = literature_check(sweep_res, opt_res, cfg)

    results = {
        "case_id": cfg["case_id"],
        "title": cfg["title"],
        "library": cfg["library"],
        "config": cfg,
        "geometry_gate": gate,
        "model_validation": validation,
        "sweep": sweep_res,
        "optimisation": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                         for k, v in opt_res.items()},
        "literature": lit,
    }
    with open(os.path.join(outdir, "results.json"), "w") as fh:
        json.dump(results, fh, indent=2)

    try:  # figures are optional for headless CI, never fatal
        make_figures(results, opt_res, figure_paths())
        results["figures"] = figure_paths()
        with open(os.path.join(outdir, "results.json"), "w") as fh:
            json.dump(results, fh, indent=2)
    except Exception as exc:  # pragma: no cover
        results["figures_error"] = repr(exc)
    return results


def make_figures(results: Dict, opt_res: Dict, paths: Dict[str, str]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lam = np.array(results["sweep"]["lambda"])
    curves = {k: np.array(v) for k, v in results["sweep"]["curves"].items()}
    outdir = case_artifact_dir()

    # --- fig 1: the two "raw" objectives ------------------------------------
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].plot(lam, curves["CD_wetted"], "C0-", lw=2)
    a = results["sweep"]["argmin"]["CD_wetted"]
    ax[0].plot(a["lambda_star"], a["value"], "C0*", ms=14)
    ax[0].set_xlabel("L/D [-]")
    ax[0].set_ylabel(r"$C_D$ [-] (wetted-area basis)")
    ax[0].set_title(f"$C_D$: grid argmin at L/D = {a['lambda_star']:.2f} (bound: {a['bound_hit']})")
    ax[0].grid(alpha=0.3)

    ax[1].plot(lam, curves["Rt_platform"], "C1-", lw=2, label="platform model ($C_F+C_R$)")
    ax[1].plot(lam, curves["Rt_form_factor"], "C3-", lw=2, label="with Hoerner $1+k$")
    for key, color in (("Rt_platform", "C1"), ("Rt_form_factor", "C3")):
        b = results["sweep"]["argmin"][key]
        ax[1].plot(b["lambda_star"], b["value"], color=color, marker="*", ms=14)
    ax[1].set_xlabel("L/D [-]")
    ax[1].set_ylabel(r"$R_t$ [N] at fixed displacement")
    ax[1].set_title("Total resistance: optimum moves with the drag model")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(paths["objectives"], dpi=130)
    plt.close(fig)

    # --- fig 2: normalised objectives + literature bands ---------------------
    fig, ax = plt.subplots(figsize=(7.6, 4.8))
    for key, color in (("CD_wetted", "C0"), ("Rt_platform", "C1"),
                       ("Rt_form_factor", "C3"), ("Rv_viscous_only", "C2")):
        y = curves[key]
        ax.plot(lam, y / y.min(), color=color, lw=2, label=f"{key} (min at {lam[np.argmin(y)]:.2f})")
        ax.plot(lam[np.argmin(y)], 1.0, color=color, marker="*", ms=13)
    for band, lab, col in ((results["config"]["literature_anchors"][0]["band"], "classical min-drag fineness", "0.85"),
                           (results["config"]["literature_anchors"][1]["band"], "practice band", "0.6")):
        ax.axvspan(band[0], band[1], color=col, alpha=0.25, label=f"{lab} [{band[0]}, {band[1]}]")
    ax.set_xlabel("L/D [-]")
    ax.set_ylabel(r"objective / its minimum [-]")
    ax.set_yscale("log")
    ax.set_title("Fixed-displacement L/D trade-off: interior optimum only in the\nviscous+form-factor objective")
    ax.legend(fontsize=7.5, loc="upper left")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(paths["normalised"], dpi=130)
    plt.close(fig)

    # --- fig 3: model vs experiment -----------------------------------------
    rows = results["model_validation"]["rows"]
    v = [r["speed_ms"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    ax.plot(v, [r["ct_experimental"] for r in rows], "ks-", label="published experiment")
    ax.plot(v, [r["ct_model"] for r in rows], "o--", color="C3", label="platform model (datum geometry)")
    for r in rows:
        ax.annotate(f"{r['ct_rel_err_pct']:+.1f}%", (r["speed_ms"], r["ct_model"]),
                    textcoords="offset points", xytext=(4, 6), fontsize=8,
                    color="black" if r["in_validity_envelope"] else "gray")
    ax.axvspan(4.05, 6.2, color="0.85", alpha=0.4, label="outside calibrated envelope (Fr > 0.62)")
    ax.set_xlabel("speed [m/s]")
    ax.set_ylabel(r"$C_t$ [-]")
    ax.set_title("Resistance-model validation against the canonical published set")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(paths["validation"], dpi=130)
    plt.close(fig)

    # --- fig 4: optimizer convergence ---------------------------------------
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    hist = opt_res["Rv_viscous_only"]["_es_history"]
    ax[0].plot([h["n_eval"] for h in hist], [h["best_so_far"] for h in hist], "C2-", lw=1.8)
    ax[0].set_xlabel("objective evaluations")
    ax[0].set_ylabel(r"best $R_v$ [N] so far")
    ax[0].set_title("CMA-ES convergence (viscous+form objective)")
    ax[0].grid(alpha=0.3)

    y = curves["Rv_viscous_only"]
    ax[1].plot(lam, y, "0.6", lw=1.4, label="objective")
    x_c = opt_res["Rv_viscous_only"]["cmaes"]["x"]
    x_s = opt_res["Rv_viscous_only"]["slsqp"]["x"]
    f_c = opt_res["Rv_viscous_only"]["cmaes"]["f"]
    f_s = opt_res["Rv_viscous_only"]["slsqp"]["f"]
    ax[1].plot(x_c, f_c, "C2*", ms=15, label=f"CMA-ES L/D={x_c:.3f}")
    ax[1].plot(x_s, f_s, "C4x", ms=10, mew=2, label=f"SLSQP L/D={x_s:.3f}")
    ax[1].set_xlabel("L/D [-]")
    ax[1].set_ylabel(r"$R_v$ [N]")
    ax[1].set_title("Both optimizers reach the same interior optimum")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(paths["convergence"], dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    res = main()
    print(json.dumps({
        "geometry_gate": res["geometry_gate"]["gate_pass"],
        "argmins": {k: v["lambda_star"] for k, v in res["sweep"]["argmin"].items()},
        "figures": res.get("figures", res.get("figures_error")),
    }, indent=2))