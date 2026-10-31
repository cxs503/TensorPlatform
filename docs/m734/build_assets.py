#!/usr/bin/env python3
"""m734 rework of report 617 (SIR-PC2|I04 ice breaking force time series validation).

Read-only import of the agent/m716 implementation; nothing in the business code is touched.
Outputs (all numbers used by the report come from audit_results.json):

  1. recompute the three published acceptance criteria and check provenance vs
     artifacts/m716_ice_breaking/validation_results.json
  2. independent audit: compare the emitted time series against the case's own
     theoretical quantities (F_peak, F_mean, f_break) and diagnose why the
     published criteria are circular, plus a masked-assignment pathology check
  3. field contours: time-frequency energy field, ice-sheet deflection / bending
     field (beam on elastic foundation) and the along-waterline load distribution
"""
import importlib.util
import json
import math
import os
import shutil
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.abspath(__file__))            # docs/m734
REPO = os.path.dirname(os.path.dirname(ROOT))                # worktree root
M716 = os.environ.get("M716_ROOT", "/srv/tensor/agent/work/m716")
OUT = os.path.join(REPO, "artifacts", "m734_ice")
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, os.path.join(M716, "backend"))
from services.ice_navigation.ice_breaking_force import (   # noqa: E402
    IceBreakingForceModel, IceProperties, StructureProperties, ShipProperties,
)


def load_m716_script():
    path = os.path.join(M716, "scripts", "validate_ice_breaking_force.py")
    spec = importlib.util.spec_from_file_location("m716_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # main() is guarded by __main__
    return mod


def main():
    sc = load_m716_script()

    ice = IceProperties(thickness=1.0, flexural_strength=0.8e6, compressive_strength=5.0e6,
                        elastic_modulus=5.0e9, density=910.0, poisson_ratio=0.33)
    st = StructureProperties(width=20.0, slope_angle=0.52, shape_factor=1.2)
    ship = ShipProperties(velocity=2.0, heading=0.0)
    duration, dt = 600.0, 0.05

    phys = sc.compute_physics(ice, st, ship)
    exp_t, exp_f = sc.generate_experimental_data(phys, duration, dt, seed=42)
    calc_t, calc_f = sc.generate_model_data(exp_t, exp_f, phys, perturbation_level=0.08)

    ref = json.load(open(os.path.join(M716, "artifacts", "m716_ice_breaking",
                                      "validation_results.json")))

    exp_peak, exp_mean = float(np.max(exp_f)), float(np.mean(exp_f))
    calc_peak, calc_mean = float(np.max(calc_f)), float(np.mean(calc_f))
    freq_e, psd_e = sc.compute_spectrum(exp_t, exp_f)
    freq_c, psd_c = sc.compute_spectrum(calc_t, calc_f)
    fpk_e = sc.find_spectral_peak(freq_e, psd_e, phys["f_break"])
    fpk_c = sc.find_spectral_peak(freq_c, psd_c, phys["f_break"])

    peak_err = abs(calc_peak - exp_peak) / exp_peak
    mean_err = abs(calc_mean - exp_mean) / exp_mean
    spec_err = abs(fpk_c - fpk_e) / fpk_e
    repro = {
        "peak_error": peak_err, "mean_error": mean_err, "spectral_error": spec_err,
        "calc_peak_N": calc_peak, "exp_peak_N": exp_peak,
        "calc_mean_N": calc_mean, "exp_mean_N": exp_mean,
        "calc_spectral_peak_Hz": fpk_c, "exp_spectral_peak_Hz": fpk_e,
        "matches_published": {
            "peak": abs(peak_err - ref["peak_force_error"]) < 1e-9,
            "mean": abs(mean_err - ref["mean_force_error"]) < 1e-9,
            "spectral": abs(spec_err - ref["spectral_peak_frequency_error"]) < 1e-9,
        },
    }

    model = IceBreakingForceModel(ice, st, ship, failure_mode="mixed")
    F_peak_model = float(model.base_peak_force)
    F_mean_model = float(model.base_mean_force)
    F_peak_script = float(phys["F_peak"])
    F_mean_script = float(phys["F_mean"])
    f_break = float(phys["f_break"])
    L_c = float(phys["L_c"])
    L_break = float(phys["L_break"])

    # ideal envelope peak/mean ratio for a 65% parabolic build-up + exponential release
    tt = np.linspace(0, 1, 4001)
    tb = 0.65
    env = np.where(tt <= tb, (3 * (tt / tb) ** 2 - 2 * (tt / tb) ** 3),
                   np.exp(-4.0 * (tt - tb) / (1 - tb)))
    ideal_peak_mean = float(env.max() / env.mean())

    # masked chained assignment pathology, minimal reproduction of the m716 pattern
    a = np.zeros(5)
    m1 = np.array([True, True, True, False, False])
    m2 = np.array([True, False, False])
    a[m1][m2] = 9.0
    masked_noop = bool(np.all(a == 0.0))

    # ice sheet field: beam on elastic foundation, per unit width
    rho_w, g = 1025.0, 9.81
    k = rho_w * g
    EI_w = ice.elastic_modulus * ice.thickness ** 3 / (12 * (1 - ice.poisson_ratio ** 2))
    lam = (k / (4 * EI_w)) ** 0.25
    l_c_field = 1.0 / lam
    P_w = F_peak_model / st.width
    M_fail = ice.flexural_strength * ice.thickness ** 2 / 6.0

    x = np.linspace(0.0, 3.2 * l_c_field, 1601)
    Af = np.exp(-lam * x) * (np.cos(lam * x) + np.sin(lam * x))
    Cf = np.exp(-lam * x) * (np.cos(lam * x) - np.sin(lam * x))
    w_field = (P_w * lam / (2 * k)) * Af * 1000.0
    M_field = (P_w / (4 * lam)) * Cf
    M_max = float(np.max(np.abs(M_field)))
    P_flex_limit = 4 * lam * M_fail              # flexural line-load limit per unit width
    F_flex_limit = st.width * P_flex_limit       # over the 20 m contact width
    p_global = F_peak_model / (ice.thickness * st.width)

    y = np.linspace(-1.5 * st.width, 1.5 * st.width, 241)
    win = np.where(np.abs(y) <= st.width / 2,
                   0.5 + 0.5 * np.cos(np.pi * y / st.width) ** 2,
                   0.5 * np.exp(-(np.abs(y) - st.width / 2) / (0.25 * st.width)))
    W2d = np.outer(win, w_field)

    # along-waterline load distribution (declared visualisation mapping only)
    L_bow = 12.0
    s = np.linspace(0.0, L_bow, 241)
    yb = (st.width / 2) * np.sqrt(np.clip(s / L_bow, 0, 1))
    alpha = np.arctan(np.gradient(yb, s))
    wdir = np.sin(alpha) / np.trapezoid(np.sin(alpha), s)
    prof_pk = calc_peak * wdir / 1e6
    prof_mean = calc_mean * wdir / 1e6

    # ---------------- figures ----------------
    plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.3})
    nper = 2048

    fig, axs = plt.subplots(1, 3, figsize=(16, 4.2))
    for ax, sig, title in ((axs[0], exp_f, "(a) reference series |S(t,f)|"),
                           (axs[1], calc_f, "(b) model series |S(t,f)|")):
        ax.specgram(sig / 1e6, NFFT=nper, Fs=1.0 / dt, noverlap=int(0.75 * nper),
                    cmap="magma")
        ax.set_ylim(0, 0.12)
        ax.set_xlabel("t (s)")
        ax.set_ylabel("f (Hz)")
        ax.set_title(title)
        ax.axhline(f_break, color="cyan", ls="--", lw=1.2,
                   label=f"theory $f_{{br}}$={f_break:.4f} Hz")
        ax.axhline(0.3 * f_break, color="w", ls=":", lw=1.2, label="LPF cutoff $0.3f_{br}$")
        ax.legend(loc="upper right", fontsize=7, framealpha=0.5)
    ratio = 20 * np.log10(np.maximum(np.abs(calc_f), 1.0) / np.maximum(np.abs(exp_f), 1.0))
    im = axs[2].imshow(ratio[None, :], aspect="auto", cmap="coolwarm", vmin=-3, vmax=3,
                       extent=[calc_t[0], calc_t[-1], 0, 1])
    axs[2].set_yticks([])
    axs[2].set_xlabel("t (s)")
    axs[2].set_title("(c) $20\\log_{10}$(model/reference) [dB]")
    fig.colorbar(im, ax=axs[2], shrink=0.85, label="dB")
    plt.tight_layout()
    fig.savefig(os.path.join(OUT, "fig4_field_timefreq.png"), dpi=150)
    plt.close(fig)

    fig, axs = plt.subplots(1, 3, figsize=(16, 4.2))
    im = axs[0].pcolormesh(x, y, W2d, cmap="viridis", shading="auto")
    axs[0].axhline(st.width / 2, color="w", lw=0.8, ls=":")
    axs[0].axhline(-st.width / 2, color="w", lw=0.8, ls=":")
    axs[0].set_xlabel("x from contact line (m)")
    axs[0].set_ylabel("y along contact (m)")
    axs[0].set_title("(a) ice-sheet deflection field $w(x,y)$ [mm]")
    fig.colorbar(im, ax=axs[0], shrink=0.85, label="mm")
    axs[1].plot(x, M_field / 1e3, "k", lw=1.4, label="$M(x)$ beam on elastic foundation")
    axs[1].axhline(M_fail / 1e3, color="r", ls="--", lw=1.2,
                   label=f"$M_f=\\sigma_f h^2/6$={M_fail/1e3:.1f} kN m/m")
    axs[1].axvline(l_c_field, color="r", ls=":", lw=1.2, label=f"decay len $\\lambda^{{-1}}$={l_c_field:.1f} m")
    axs[1].axvline(L_break, color="b", ls="-.", lw=1.2, label=f"model $L_{{break}}$={L_break:.1f} m")
    axs[1].set_xlabel("x (m)")
    axs[1].set_ylabel("M (kN m/m)")
    axs[1].set_title("(b) bending moment -> breaking length")
    axs[1].legend(fontsize=7)
    axs[2].fill_between(s, 0, prof_pk, color="crimson", alpha=0.35, label="at series peak")
    axs[2].plot(s, prof_mean, "k", lw=1.2, label="cycle mean")
    axs[2].set_xlabel("s along waterline from FP (m)")
    axs[2].set_ylabel("q(s) (MN/m)")
    axs[2].set_title("(c) ice load distribution along waterline")
    axs[2].legend(fontsize=8)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT, "fig5_field_ice_sheet_hullload.png"), dpi=150)
    plt.close(fig)

    ind = {
        "peak": abs(calc_peak - F_peak_script) / F_peak_script,
        "mean": abs(calc_mean - F_mean_script) / F_mean_script,
        "spectral": abs(fpk_c - f_break) / f_break,
    }
    cyc = {"peak force": peak_err, "mean force": mean_err, "spectral peak": spec_err}
    ind_lab = {"peak force": ind["peak"], "mean force": ind["mean"], "spectral peak": ind["spectral"]}
    labs = list(cyc)
    xs = np.arange(len(labs))
    fig, ax = plt.subplots(figsize=(9.4, 4.4))
    ax.bar(xs - 0.19, [max(cyc[k], 1e-5) * 100 for k in labs], 0.36,
           label="vs reference series (circular)", color="#4C72B0")
    ax.bar(xs + 0.19, [max(ind_lab[k], 1e-5) * 100 for k in labs], 0.36,
           label="vs theory ($F_{peak}$, $F_{mean}$, $f_{br}$) - independent", color="#C44E52")
    ax.set_yscale("log")
    ax.axhline(3, color="k", ls="--", lw=1.2, label="3% bar")
    ax.axhline(15, color="orange", ls=":", lw=1.2, label="15% platform-unacceptable")
    ax.set_xticks(xs)
    ax.set_xticklabels(labs)
    ax.set_ylabel("relative error (%)")
    ax.set_ylim(1e-3, 3e3)
    for i, lb in enumerate(labs):
        ax.text(i - 0.19, max(cyc[lb], 1e-5) * 100 * 1.4, f"{cyc[lb]*100:.2f}%", ha="center", fontsize=8)
        ax.text(i + 0.19, max(ind_lab[lb], 1e-5) * 100 * 1.4, f"{ind_lab[lb]*100:.1f}%", ha="center", fontsize=8)
    ax.set_title("Reported criteria (circular reference) vs independent theoretical basis")
    ax.legend(fontsize=8)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT, "fig6_error_verdict.png"), dpi=150)
    plt.close(fig)

    def acf(sig, lag):
        a = sig - sig.mean()
        denom = np.dot(a, a)
        return float(np.dot(a[:-lag], a[lag:]) / denom) if lag else 1.0

    lag_break = int(round((1.0 / f_break) / dt))
    acf_break_exp = acf(exp_f, lag_break)
    acf_break_calc = acf(calc_f, lag_break)
    np.savez_compressed(os.path.join(OUT, "series.npz"), time=exp_t,
                        exp_force=exp_f, calc_force=calc_f)

    for f in ("fig1_time_series_comparison.png", "fig2_spectral_comparison.png",
              "fig3_validation_summary.png"):
        src = os.path.join(M716, "artifacts", "m716_ice_breaking", f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(OUT, f))

    audit = {
        "provenance": {
            "source_worktree": M716, "source_commit": "409b606",
            "source_files": ["backend/services/ice_navigation/ice_breaking_force.py",
                             "backend/services/ice_navigation/validation.py",
                             "scripts/validate_ice_breaking_force.py"],
            "published_report_id": 617, "mission_id": 716,
        },
        "case": {"thickness_m": ice.thickness, "sigma_f_MPa": ice.flexural_strength / 1e6,
                 "sigma_c_MPa": ice.compressive_strength / 1e6,
                 "E_GPa": ice.elastic_modulus / 1e9, "nu": ice.poisson_ratio,
                 "width_m": st.width, "slope_angle_deg": math.degrees(st.slope_angle),
                 "shape_factor": st.shape_factor, "velocity_ms": ship.velocity,
                 "duration_s": duration, "dt_s": dt, "n_samples": int(len(exp_t)),
                 "perturbation_level": 0.08},
        "derived": {"L_c_m": L_c, "L_break_m": L_break, "f_break_Hz": f_break,
                    "F_peak_script_N": F_peak_script, "F_mean_script_N": F_mean_script,
                    "F_peak_model_module_N": F_peak_model, "F_mean_model_module_N": F_mean_model,
                    "mean_ratio_script": F_mean_script / F_peak_script,
                    "mean_ratio_model": F_mean_model / F_peak_model,
                    "peak_discrepancy_two_correlations": abs(F_peak_script - F_peak_model) / F_peak_model},
        "recomputed_vs_published": repro,
        "series": {"exp_peak_N": exp_peak, "calc_peak_N": calc_peak,
                   "exp_mean_N": exp_mean, "calc_mean_N": calc_mean,
                   "peak_over_mean_exp": exp_peak / exp_mean,
                   "peak_over_mean_calc": calc_peak / calc_mean,
                   "ideal_peak_over_mean": ideal_peak_mean,
                   "theory_peak_over_mean": F_peak_script / F_mean_script,
                   "calc_peak_over_theory_peak": calc_peak / F_peak_script,
                   "exp_peak_over_theory_peak": exp_peak / F_peak_script},
        "diagnostics": {"masked_assign_noop": masked_noop,
                        "acf_at_breaking_period_exp": acf_break_exp,
                        "acf_at_breaking_period_calc": acf_break_calc,
                        "breaking_period_s": 1.0 / f_break, "lag_samples": lag_break,
                        "spectral_peak_Hz": fpk_c, "f_break_theory_Hz": f_break,
                        "lpf_cutoff_Hz": 0.3 * f_break,
                        "spectral_peak_at_lpf_edge": abs(fpk_c - 0.3 * f_break) < 0.015,
                        "resolution_Hz": 1.0 / duration},
        "independent_check": {"peak_vs_theory": ind["peak"], "mean_vs_theory": ind["mean"],
                              "spectral_vs_theory": ind["spectral"]},
        "ice_sheet_field": {"lambda_per_m": lam, "decay_length_m": l_c_field,
                            "L_c_code_m": L_c, "decay_over_Lc": l_c_field / L_c,
                            "EI_per_width_Nm2": EI_w, "k_Pa_per_m": k,
                            "P_per_width_N_per_m": P_w, "M_max_Nm_per_m": M_max,
                            "M_fail_Nm_per_m": M_fail, "w_max_mm": float(w_field.max()),
                            "P_flex_limit_N_per_m": P_flex_limit, "F_flex_limit_N": F_flex_limit,
                            "F_peak_over_flex_limit": F_peak_model / F_flex_limit,
                            "implied_global_pressure_MPa": p_global / 1e6,
                            "implied_pressure_over_sigma_c": p_global / ice.compressive_strength},
        "hull_load_mapping": {"L_bow_m": L_bow, "q_at_FP_MN_per_m": float(prof_pk[0]),
                              "q_mean_field_MN_per_m": float(np.mean(prof_pk)),
                              "note": "declared visualisation mapping; not used in any metric"},
    }
    json.dump(audit, open(os.path.join(OUT, "audit_results.json"), "w"), indent=2,
              ensure_ascii=False)
    print(json.dumps({k: audit[k] for k in ("recomputed_vs_published", "series",
                                            "diagnostics", "independent_check",
                                            "ice_sheet_field")}, indent=2, ensure_ascii=False))
    print("\nfigures:", sorted(f for f in os.listdir(OUT) if f.endswith(".png")))


if __name__ == "__main__":
    main()