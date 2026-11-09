"""Run the P66-W2-N07 ice-navigation UQ study and emit the artifact bundle.

Outputs (all inside this directory):

* ``results.json``   -- every criterion number, the sensitivity ranking, the
  confidence intervals, the propagation-chain diagnostics and the analytic
  anchors, all in one machine-readable file;
* ``tables.md``      -- the same numbers as markdown tables (programmatically
  generated, zero hand-typing);
* ``figs/*.png``     -- confidence-interval chart, sensitivity ranking, error
  propagation path, cumulative-field evolution with the sqrt(Y) growth check,
  and the MC-vs-QMC convergence ladder.

Run:  python3 artifacts/m7297/run_uq_case.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.uq_propagation import (  # noqa: E402
    accelerated_uncertainty,
    figure_confidence_intervals,
    figure_convergence,
    figure_propagation_path,
    figure_sensitivity,
    output_uncertainty,
    propagation_chain,
    qmc_variance_reduction,
    replicate_ci_width,
    sobol_hdmr,
)

import case_ice_navigation as case  # noqa: E402  (same directory)

FIG = os.path.join(HERE, "figs")
os.makedirs(FIG, exist_ok=True)

SEED = 20261108
N_SOBOL_BASE = 16384
N_CI = 100000
N_REP = 20
N_PER_REP = 5000
N_MC_ACCEL = 200000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> dict:
    t_start = time.time()
    res: dict = {"mission": 7297, "node": "P66-W2-N07", "seed": SEED}

    # ------------------------------------------------------------------
    # 0. Anchors: nominal closed form, trial comparison, input diagnostics
    # ------------------------------------------------------------------
    log("anchors: nominal closed form")
    anchor = case.nominal_depth_closed_form()
    X_nom = case.NOMINAL[None, :]
    model_nom = {
        "p95_depth_mm": float(case.qoi_p95_depth_mm(X_nom)[0]),
        "mean_depth_mm": float(case.qoi_mean_depth_mm(X_nom)[0]),
        "max_depth_mm": float(case.qoi_max_depth_mm(X_nom)[0]),
        "resistance_kN": float(case.qoi_resistance_kN(X_nom)[0]),
    }
    # closed form is the *mean-station* value; the model nominal mean station
    # value must reproduce it to round-off
    anchor["model_mean_station_depth_mm"] = model_nom["mean_depth_mm"]
    anchor["model_vs_closed_form_rel_err"] = abs(
        model_nom["mean_depth_mm"] - anchor["depth_mm"]) / anchor["depth_mm"]
    trial_mean = case.SHIP_TRIAL_DATA[case.SHIP_NAME]["mean_resistance_kN"]
    anchor["trial_mean_resistance_kN"] = float(trial_mean)
    anchor["model_vs_trial_rel_err"] = abs(model_nom["resistance_kN"] - trial_mean) / trial_mean
    res["anchors"] = {
        "nominal_closed_form": anchor,
        "nominal_model": model_nom,
        "input_diagnostics": [s.diagnostics() for s in case.INPUT_SPECS],
        "station_shape_factor": case.PHI.tolist(),
        "constants": {
            "P_REF_Pa": case.P_REF, "SIGMA_C_REF_Pa": case.SIGMA_C_REF,
            "H_REF_m": case.H_REF, "V_REF_ms": case.V_REF, "beta": case.BETA,
            "K_nominal": case.K_NOMINAL, "H_steel_Pa": case.H_NOMINAL,
            "service_years": case.SERVICE_YEARS, "in_ice_track_nm": case.IN_ICE_TRACK_NM,
            "yearly_log_sigma": case.YEARLY_LOG_SIGMA,
        },
    }
    log(f"   nominal depth {anchor['depth_mm']:.3f} mm, model {model_nom['mean_depth_mm']:.3f} mm, "
        f"rel err {anchor['model_vs_closed_form_rel_err']:.2e}; resistance {model_nom['resistance_kN']:.1f} kN "
        f"vs trial {trial_mean:.0f} kN")

    # ------------------------------------------------------------------
    # 1. Sobol' closure decomposition (criterion 1)
    # ------------------------------------------------------------------
    log("criterion 1: Sobol' closure (7 inputs, max_order=2)")
    sobol_depth = sobol_hdmr(
        case.qoi_p95_depth_mm, case.INPUT_SPECS, n_base=N_SOBOL_BASE, max_order=2,
        seed=SEED, n_boot=120,
    )
    log(f"   p95_depth closure {sobol_depth['closure']:.4f} "
        f"higher-order {sobol_depth['higher_order_share']:.4f}")
    sobol_res = sobol_hdmr(
        case.qoi_resistance_kN, case.INPUT_SPECS, n_base=N_SOBOL_BASE, max_order=2,
        seed=SEED + 1, n_boot=120,
    )
    log(f"   resistance closure {sobol_res['closure']:.4f} "
        f"higher-order {sobol_res['higher_order_share']:.4f}")

    def _closure_block(sb: dict, label: str) -> dict:
        return {
            "qoi": label,
            "closure": sb["closure"],
            "higher_order_share": sb["higher_order_share"],
            "closure_band": sb["closure_band"],
            "closure_ok": sb["closure_ok"],
            "closure_ci_bootstrap": sb["bootstrap"].get("closure_ci"),
            "closure_std_bootstrap": sb["bootstrap"].get("closure_std"),
            "sum_first_order": sb["sum_first_order"],
            "sum_total_order": sb["sum_total_order"],
            "first_order": sb["first_order"],
            "second_order": sb["second_order"],
            "total_order": sb["total_order"],
            "ranking": [nm for nm, _ in sb["ranking"]],
            "variance": sb["variance"],
            "n_base": sb["n_base"],
            "n_evals": sb["n_evals"],
        }

    c1 = {"criterion": "Sigma Sobol' indices (closure over orders<=2) in [0.95, 1.05]",
          "per_qoi": [_closure_block(sobol_depth, "qoi_p95_depth_mm"),
                      _closure_block(sobol_res, "qoi_resistance_kN")]}
    c1["pass"] = all(blk["closure_ok"] for blk in c1["per_qoi"])
    c1["worst_closure"] = min(blk["closure"] for blk in c1["per_qoi"])
    c1["value"] = c1["worst_closure"]
    res["criteria"] = {"C1_sobol_closure": c1}

    # ------------------------------------------------------------------
    # 2. CI width vs independent replicates (criterion 2)
    # ------------------------------------------------------------------
    log("criterion 2: 95% CI width vs independent-replicate statistics")
    qois = {
        "qoi_p95_depth_mm": case.qoi_p95_depth_mm,
        "qoi_resistance_kN": case.qoi_resistance_kN,
    }
    c2_rows = []
    for name, fn in qois.items():
        single = output_uncertainty(fn, case.INPUT_SPECS, n=N_CI, quantiles=(0.95,),
                                    seed=SEED + 2, method="qmc")
        reps = replicate_ci_width(fn, case.INPUT_SPECS, n_rep=N_REP, n_per_rep=N_PER_REP,
                                  quantiles=(0.95,), seed=SEED + 3, method="mc")
        col = single["columns"][single["output_names"][0]]
        w_single_mean = col["ci_mean_width"]
        w_rep_mean = reps["mean_ci_width"][0]
        w_single_q = col["quantiles"]["0.95"]["width_bootstrap"]
        w_rep_q = reps["quantile_ci_width"]["0.95"]
        c2_rows.append({
            "qoi": name,
            "mean": col["mean"], "std": col["std"],
            "p95": col["quantiles"]["0.95"]["estimate"],
            "p95_ci": [col["quantiles"]["0.95"]["bootstrap"]["ci_low"],
                       col["quantiles"]["0.95"]["bootstrap"]["ci_high"]],
            "p95_ci_order_statistic": [col["quantiles"]["0.95"]["order_statistic"]["ci_low"],
                                       col["quantiles"]["0.95"]["order_statistic"]["ci_high"]],
            "mean_ci_width_single": w_single_mean,
            "mean_ci_width_replicates": w_rep_mean,
            "mean_width_ratio": w_single_mean / w_rep_mean,
            "p95_ci_width_single": w_single_q,
            "p95_ci_width_replicates": w_rep_q,
            "p95_width_ratio": w_single_q / w_rep_q,
            "n_single": single["n_evals"], "n_rep": reps["n_rep"],
            "n_per_rep": reps["n_per_rep"],
        })
        log(f"   {name}: mean width ratio {c2_rows[-1]['mean_width_ratio']:.3f}, "
            f"p95 width ratio {c2_rows[-1]['p95_width_ratio']:.3f}")
    ratios = [r["mean_width_ratio"] for r in c2_rows] + [r["p95_width_ratio"] for r in c2_rows]
    res["criteria"]["C2_ci_consistency"] = {
        "criterion": "95% CI width ratio (single accelerated run / independent replicates) in [0.8, 1.2]",
        "rows": c2_rows,
        "ratios": ratios,
        "min_ratio": float(min(ratios)),
        "max_ratio": float(max(ratios)),
        "value": float(max(abs(x - 1.0) for x in ratios)),
        "pass": bool(all(0.8 <= x <= 1.2 for x in ratios)),
    }

    # ------------------------------------------------------------------
    # 3. Error-propagation chain: conditioning (criterion 3)
    # ------------------------------------------------------------------
    log("criterion 3: propagation-chain Jacobian conditioning")
    chain_abr = propagation_chain(
        case.ABRASION_CHAIN, case.NOMINAL, case.INPUT_SPECS, n_mc=20000, seed=SEED + 4,
    )
    log(f"   abrasion chain: cond {chain_abr['condition_number']:.3e}, "
        f"input-space cond {chain_abr['input_space_condition_number']:.3e}")
    x0_res = np.array([case.NOMINAL[j] for j in case.RESISTANCE_IDX])
    chain_res = propagation_chain(
        case.RESISTANCE_CHAIN, x0_res, case.RESISTANCE_SPECS, n_mc=20000, seed=SEED + 5,
    )
    log(f"   resistance chain: cond {chain_res['condition_number']:.3e}, "
        f"input-space cond {chain_res['input_space_condition_number']:.3e}")

    def _chain_block(ch: dict, label: str) -> dict:
        return {
            "chain": label,
            "condition_number": ch["condition_number"],
            "input_space_condition_number": ch["input_space_condition_number"],
            "rank_output": ch["rank_output"], "rank_input": ch["rank_input"],
            "definition": ch["condition_number_definition"],
            "stages": [
                {"stage": st["stage"],
                 "local_condition_number": st["local_condition_number"],
                 "amplification": st["amplification"],
                 "linearisation_ratio": st["linearisation_ratio"],
                 "sigma_mc": st["sigma_mc"]}
                for st in ch["stages"]
            ],
            "variance_contributions": ch["variance_contributions"],
            "variance_contribution_ranking": ch["variance_contribution_ranking"],
            "dominant_input": ch["dominant_input"],
            "final_nominal": ch["final_nominal"],
            "final_output_names": ch["final_output_names"],
        }

    c3 = {
        "criterion": "error-propagation-chain Jacobian condition number < 1e6",
        "chains": [_chain_block(chain_abr, "abrasion_field_chain"),
                   _chain_block(chain_res, "resistance_chain")],
        "graph_abrasion": chain_abr["graph"],
    }
    conds = [blk["condition_number"] for blk in c3["chains"]]
    c3["worst_condition_number"] = float(max(conds))
    c3["value"] = c3["worst_condition_number"]
    c3["pass"] = bool(all(c < 1e6 for c in conds))
    res["criteria"]["C3_condition_number"] = c3

    # ------------------------------------------------------------------
    # 4. Acceleration vs brute-force Monte Carlo (criterion 4)
    # ------------------------------------------------------------------
    log("criterion 4: UQ acceleration vs brute-force MC")
    acc = accelerated_uncertainty(
        case.qoi_p95_depth_mm, case.INPUT_SPECS, n_design=512, order=2, n_valid=256,
        n_mc=N_MC_ACCEL, quantiles=(0.95,), seed=SEED + 6, n_rep=10, n_per_rep=1024,
    )
    qmc_red = {k: v for k, v in acc["matched_budget"].items()}
    log(f"   speedup_evals {acc['speedup_evals']:.1f}, wall {acc['brute_force']['wall_s']:.2f}s -> "
        f"{acc['accelerated']['wall_s']:.2f}s, surrogate rel L2 {acc['accelerated']['validation_rel_l2']:.2e}")
    c4 = {
        "criterion": "UQ speed-up >= 10 vs brute-force Monte Carlo",
        "speedup_evals": acc["speedup_evals"],
        "speedup_wall": acc["speedup_wall"],
        "brute_force_n_evals": acc["brute_force"]["n_evals"],
        "accelerated_n_model_evals": acc["accelerated"]["n_model_evals"],
        "brute_force_wall_s": acc["brute_force"]["wall_s"],
        "accelerated_wall_s": acc["accelerated"]["wall_s"],
        "surrogate": acc["accelerated"]["surrogate"],
        "validation_rel_l2": acc["accelerated"]["validation_rel_l2"],
        "validation_rel_max": acc["accelerated"]["validation_rel_max"],
        "quantile_width_ratio": acc["quantile_width_ratio"],
        "qmc_variance_reduction": qmc_red,
        "brute_force_stats": acc["brute_force"]["columns"],
        "accelerated_stats": acc["accelerated"]["columns"],
    }
    c4["value"] = float(acc["speedup_evals"])
    c4["pass"] = bool(acc["speedup_evals"] >= 10.0
                      and acc["accelerated"]["validation_rel_l2"] < 0.05
                      and all(0.8 <= v <= 1.2 for v in acc["quantile_width_ratio"].values()))
    res["criteria"]["C4_speedup"] = c4

    # ------------------------------------------------------------------
    # 5. Spatio-temporal field error growth (analytic sqrt(Y) anchor)
    # ------------------------------------------------------------------
    log("field evolution: nested MC, sqrt(Y) growth check")
    growth = case.field_growth_check(n_outer=4096, seed=SEED + 7)
    log(f"   growth exponent {growth['growth_exponent_fit']:.3f} (expected 0.5, "
        f"rel err {growth['growth_exponent_rel_error']:.3f})")
    res["field_evolution"] = growth

    # ------------------------------------------------------------------
    # 6. Convergence ladder (MC vs QMC) for the figure
    # ------------------------------------------------------------------
    log("convergence ladder MC vs QMC")
    budgets = [256, 1024, 4096, 16384]
    ladder = {"mc": [], "qmc": []}
    for n in budgets:
        red = qmc_variance_reduction(case.qoi_p95_depth_mm, case.INPUT_SPECS,
                                     n_rep=8, n_per_rep=n, seed=SEED + 8 + n)
        ladder["mc"].append((n, float(red["std_mean_mc"][0])))
        ladder["qmc"].append((n, float(red["std_mean_qmc"][0])))
    res["convergence_ladder"] = ladder

    # ------------------------------------------------------------------
    # 7. Figures (criterion 5)
    # ------------------------------------------------------------------
    log("figures")
    depth_row = [r for r in c2_rows if r["qoi"] == "qoi_p95_depth_mm"][0]
    res_row = [r for r in c2_rows if r["qoi"] == "qoi_resistance_kN"][0]
    ci_rows = [
        {"label": "P95 abrasion depth [mm]", "value": depth_row["p95"],
         "lo": depth_row["p95_ci"][0], "hi": depth_row["p95_ci"][1]},
        {"label": "mean abrasion depth [mm]", "value": depth_row["mean"],
         "lo": depth_row["mean"] - 0.5 * depth_row["mean_ci_width_single"],
         "hi": depth_row["mean"] + 0.5 * depth_row["mean_ci_width_single"]},
        {"label": "total ice resistance [kN]", "value": res_row["p95"],
         "lo": res_row["p95_ci"][0], "hi": res_row["p95_ci"][1]},
        {"label": "mean ice resistance [kN]", "value": res_row["mean"],
         "lo": res_row["mean"] - 0.5 * res_row["mean_ci_width_single"],
         "hi": res_row["mean"] + 0.5 * res_row["mean_ci_width_single"]},
    ]
    fig_ci = figure_confidence_intervals(
        ci_rows, os.path.join(FIG, "fig1_ci_quantities.png"),
        title="N03/N04 quantities of interest with 95 % intervals (QMC, N=%d)" % N_CI,
        xlabel="value (each row has its own scale)",
    )

    sens_rows = []
    for nm in case.NAMES:
        sens_rows.append({
            "name": nm,
            "S1": sobol_depth["first_order"][nm],
            "ST": sobol_depth["total_order"][nm],
        })
    sens_rows.sort(key=lambda r: -r["ST"])
    fig_sens = figure_sensitivity(
        sens_rows, os.path.join(FIG, "fig2_sobol_p95_depth.png"),
        title="N03 P95 abrasion depth -- Sobol' ranking (closure %.3f)" % sobol_depth["closure"],
    )
    sens_rows_r = []
    for nm in case.NAMES:
        sens_rows_r.append({
            "name": nm,
            "S1": sobol_res["first_order"][nm],
            "ST": sobol_res["total_order"][nm],
        })
    sens_rows_r.sort(key=lambda r: -r["ST"])
    fig_sens_r = figure_sensitivity(
        sens_rows_r, os.path.join(FIG, "fig3_sobol_resistance.png"),
        title="N04 ice resistance -- Sobol' ranking (closure %.3f)" % sobol_res["closure"],
    )
    fig_path = figure_propagation_path(
        chain_abr["graph"], os.path.join(FIG, "fig4_propagation_path.png"),
        title="Error-propagation path, abrasion field chain (edge width ~ variance share)",
        edge_threshold=0.02,
    )

    # field evolution figure: mean +/- band over years + growth law
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    years = np.asarray(growth["years"], dtype=float)
    means = np.asarray(growth["mean_max_mm"])
    lo = np.asarray(growth["q05_max_mm"])
    hi = np.asarray(growth["q95_max_mm"])
    stds = np.asarray(growth["std_max_mm"])
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.2))
    ax = axes[0]
    ax.fill_between(years, lo, hi, color="#1f4e79", alpha=0.18,
                    label="5-95 % envelope (yearly severity)")
    ax.plot(years, means, color="#1f4e79", lw=1.8, label="mean of max-station depth")
    ax.plot(years, means + 1.96 * stds, color="#c00000", ls="--", lw=1.1,
            label="mean +- 1.96 sigma")
    ax.plot(years, np.maximum(means - 1.96 * stds, 0.0), color="#c00000", ls="--", lw=1.1)
    ax.set_xlabel("service year Y")
    ax.set_ylabel("max station abrasion depth [mm]")
    ax.set_title("N05 cumulative field evolution (nested MC, n=%d)" % growth["n_outer"])
    ax.grid(ls=":", alpha=0.5)
    ax.legend(fontsize=7.5)
    ax = axes[1]
    ax.loglog(years, stds, "o-", color="#1f4e79", lw=1.5, label="std across samples")
    ref = stds[0] * np.sqrt(years / years[0])
    ax.loglog(years, ref, "k--", lw=1.0, label=r"$\propto\sqrt{Y}$ (i.i.d. anchors)")
    ax.set_xlabel("service year Y")
    ax.set_ylabel("std of max station depth [mm]")
    ax.set_title("error growth law: fitted exponent %.3f (expected 0.500)"
                 % growth["growth_exponent_fit"])
    ax.grid(True, which="both", ls=":", alpha=0.5)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig_field = os.path.join(FIG, "fig5_field_evolution.png")
    fig.savefig(fig_field, dpi=170)
    plt.close(fig)

    fig_conv = figure_convergence(
        {"plain Monte Carlo": ladder["mc"], "scrambled QMC": ladder["qmc"]},
        os.path.join(FIG, "fig6_convergence_mc_vs_qmc.png"),
        title="replicate scatter of the mean estimator, MC vs QMC (QoI: P95 depth)",
        ylabel="std of replicate means [mm]",
    )

    figures = [fig_ci, fig_sens, fig_sens_r, fig_path, fig_field, fig_conv]
    res["criteria"]["C5_report_artifacts"] = {
        "criterion": "report carries sensitivity ranking + CI chart + propagation path",
        "figures": [os.path.relpath(f, ROOT) for f in figures],
        "figure_roles": {
            os.path.basename(fig_ci): "95 % confidence intervals of N03/N04 quantities",
            os.path.basename(fig_sens): "sensitivity ranking table/chart, P95 depth",
            os.path.basename(fig_sens_r): "sensitivity ranking table/chart, resistance",
            os.path.basename(fig_path): "error-propagation path diagram",
            os.path.basename(fig_field): "N05 field evolution + sqrt(Y) growth law",
            os.path.basename(fig_conv): "MC vs QMC estimator convergence",
        },
        "table_file": "artifacts/m7297/tables.md",
        "pass": bool(len(figures) >= 3),
    }

    # ------------------------------------------------------------------
    # 8. Verdict
    # ------------------------------------------------------------------
    res["elapsed_s"] = time.time() - t_start
    res["criteria"]["all_pass"] = bool(
        res["criteria"]["C1_sobol_closure"]["pass"]
        and res["criteria"]["C2_ci_consistency"]["pass"]
        and res["criteria"]["C3_condition_number"]["pass"]
        and res["criteria"]["C4_speedup"]["pass"]
        and res["criteria"]["C5_report_artifacts"]["pass"]
    )
    res["field_growth_check"] = {
        "fitted_exponent": growth["growth_exponent_fit"],
        "expected": growth["growth_exponent_expected"],
        "rel_error": growth["growth_exponent_rel_error"],
        "pass": bool(growth["growth_exponent_rel_error"] < 0.1),
    }

    with open(os.path.join(HERE, "results.json"), "w") as fh:
        json.dump(res, fh, indent=2)
    write_tables(res)
    log(f"done in {res['elapsed_s']:.1f}s, all_pass={res['criteria']['all_pass']}")
    return res


def write_tables(res: dict) -> None:
    """Markdown tables generated from the JSON (no hand-typed numbers)."""
    L = []
    L.append("# P66-W2-N07 ice-navigation UQ -- result tables\n")
    L.append("Generated by `artifacts/m7297/run_uq_case.py`; every number below is read "
             "from `results.json`.\n")
    L.append("## Criterion summary\n")
    L.append("| criterion | value | band | pass |")
    L.append("|---|---|---|---|")
    c = res["criteria"]
    L.append(f"| C1 Sobol' closure | {c['C1_sobol_closure']['value']:.4f} | [0.95, 1.05] | "
             f"{c['C1_sobol_closure']['pass']} |")
    L.append(f"| C2 CI-width ratio (worst deviation) | {c['C2_ci_consistency']['value']:.4f} | "
             f"[0.8, 1.2] | {c['C2_ci_consistency']['pass']} |")
    L.append(f"| C3 chain condition number | {c['C3_condition_number']['value']:.3e} | < 1e6 | "
             f"{c['C3_condition_number']['pass']} |")
    L.append(f"| C4 UQ speed-up (model evals) | {c['C4_speedup']['value']:.1f}x | >= 10 | "
             f"{c['C4_speedup']['pass']} |")
    L.append(f"| C5 report artifacts | {len(c['C5_report_artifacts']['figures'])} figures | >= 3 | "
             f"{c['C5_report_artifacts']['pass']} |")
    L.append(f"| all_pass | {c['all_pass']} |  |  |\n")

    L.append("## Sensitivity ranking (Sobol', closure over orders <= 2)\n")
    for blk in c["C1_sobol_closure"]["per_qoi"]:
        L.append(f"### {blk['qoi']} (closure {blk['closure']:.4f}, "
                 f"higher-order share {blk['higher_order_share']:.4f})\n")
        L.append("| input | S1 | ST | S1 bootstrap std |")
        L.append("|---|---|---|---|")
        for nm, s1 in sorted(blk["first_order"].items(), key=lambda kv: -kv[1]):
            st = blk["total_order"][nm]
            sd = blk.get("closure_std_bootstrap")
            L.append(f"| {nm} | {s1:.4f} | {st:.4f} |  |")
        if blk["second_order"]:
            L.append("\nSecond-order (interaction) shares:\n")
            L.append("| pair | S_ij |")
            L.append("|---|---|")
            for pair, v in sorted(blk["second_order"].items(), key=lambda kv: -abs(kv[1]))[:6]:
                L.append(f"| {pair} | {v:+.5f} |")
        L.append("")

    L.append("## Quantities of interest with 95 % intervals\n")
    L.append("| QoI | mean | std | P95 | P95 95% CI (bootstrap) | P95 95% CI (order stat.) |")
    L.append("|---|---|---|---|---|---|")
    for r in c["C2_ci_consistency"]["rows"]:
        L.append(f"| {r['qoi']} | {r['mean']:.4f} | {r['std']:.4f} | {r['p95']:.4f} | "
                 f"[{r['p95_ci'][0]:.4f}, {r['p95_ci'][1]:.4f}] | "
                 f"[{r['p95_ci_order_statistic'][0]:.4f}, {r['p95_ci_order_statistic'][1]:.4f}] |")
    L.append("")
    L.append("CI width consistency (single run vs independent replicates):\n")
    L.append("| QoI | mean width ratio | P95 width ratio |")
    L.append("|---|---|---|")
    for r in c["C2_ci_consistency"]["rows"]:
        L.append(f"| {r['qoi']} | {r['mean_width_ratio']:.3f} | {r['p95_width_ratio']:.3f} |")
    L.append("")

    L.append("## Error-propagation chain diagnostics\n")
    L.append("| chain | cond(J) | input-space cond | dominant input | stage | local cond | amplification |")
    L.append("|---|---|---|---|---|---|---|")
    for ch in c["C3_condition_number"]["chains"]:
        for st in ch["stages"]:
            L.append(f"| {ch['chain']} | {ch['condition_number']:.3e} | "
                     f"{ch['input_space_condition_number']:.3e} | {ch['dominant_input']} | "
                     f"{st['stage']} | {st['local_condition_number']:.3e} | {st['amplification']:.3f} |")
    L.append("")
    L.append("Per-input variance contribution at the end of each chain:\n")
    L.append("| chain | contributions |")
    L.append("|---|---|")
    for ch in c["C3_condition_number"]["chains"]:
        contrib = ", ".join(f"{k}={v:.3f}" for k, v in
                            sorted(ch["variance_contributions"].items(), key=lambda kv: -kv[1]))
        L.append(f"| {ch['chain']} | {contrib} |")
    L.append("")

    L.append("## Acceleration study\n")
    c4 = c["C4_speedup"]
    L.append(f"* brute force: {c4['brute_force_n_evals']} model evaluations, "
             f"{c4['brute_force_wall_s']:.2f} s")
    L.append(f"* accelerated: {c4['accelerated_n_model_evals']} model evaluations, "
             f"{c4['accelerated_wall_s']:.2f} s")
    L.append(f"* speed-up (model evaluations): {c4['speedup_evals']:.1f}x; "
             f"wall clock: {c4['speedup_wall']:.1f}x")
    L.append(f"* surrogate: {c4['surrogate']}")
    L.append(f"* validation rel L2 / max: {c4['validation_rel_l2']:.3e} / {c4['validation_rel_max']:.3e}")
    L.append(f"* QMC variance reduction (matched budget): "
             f"{c4['qmc_variance_reduction']['speedup']}")
    L.append("")

    L.append("## Analytic anchors\n")
    anc = res["anchors"]
    L.append(f"* nominal closed-form depth: {anc['nominal_closed_form']['depth_mm']:.4f} mm "
             f"({anc['nominal_closed_form']['rate_mm_per_1000nm']:.3f} mm/1000 nm, "
             f"inside service band {anc['nominal_closed_form']['inside_service_band']})")
    L.append(f"* model nominal mean-station depth: "
             f"{anc['nominal_closed_form']['model_mean_station_depth_mm']:.4f} mm "
             f"(rel err {anc['nominal_closed_form']['model_vs_closed_form_rel_err']:.2e})")
    L.append(f"* nominal resistance {anc['nominal_model']['resistance_kN']:.1f} kN vs trial mean "
             f"{anc['nominal_closed_form']['trial_mean_resistance_kN']:.0f} kN "
             f"(rel err {anc['nominal_closed_form']['model_vs_trial_rel_err']:.4f})")
    fe = res["field_evolution"]
    L.append(f"* cumulative-field growth exponent: {fe['growth_exponent_fit']:.4f} "
             f"(expected 0.5, rel err {fe['growth_exponent_rel_error']:.3f})")
    with open(os.path.join(HERE, "tables.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()