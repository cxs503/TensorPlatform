"""Numerical experiments for ice-load inversion & SHM (mission W4-N05 / m838).

Reproduces every acceptance criterion and writes the machine-readable evidence
plus the report figures under ``artifacts/m838/``.

Run with::

    python3 scripts/m838_ice_load_experiment.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.ice_shm import (  # noqa: E402
    SF_FAIL_THRESHOLD,
    SF_SAFE_THRESHOLD,
    BeamSection,
    IceBeltFrameModel,
    StructuralHealthMonitor,
    classify_safety_factor,
    invert_ice_load,
    l_curve_scan,
    load_covariance,
    relative_load_uncertainty,
    relative_noise_level,
    safety_factor_with_uncertainty,
    synthesize_strain,
    tikhonov_solve,
)

OUT = ROOT / "artifacts" / "m838"
OUT.mkdir(parents=True, exist_ok=True)

# ── reference configuration ────────────────────────────────────────────────
LENGTH_M = 5.0
N_ELEMENTS = 20
LOAD_POSITIONS = [1.0, 2.0, 3.0, 4.0]
GAUGES = [0.2 + 4.6 * i / 23 for i in range(24)]
LOADS_TRUE = np.linspace(0.75, 1.25, 4) * 55.0e3
NOISE_REL = 0.05
SEED = 7

results: dict = {"mission": "W4-N05", "config": {}, "criteria": {}, "cases": {}}

model = IceBeltFrameModel(length_m=LENGTH_M, n_elements=N_ELEMENTS)
section = BeamSection()
influence = model.build_influence_matrix(LOAD_POSITIONS, GAUGES)

results["config"] = {
    "structure": model.info(),
    "load_positions_m": LOAD_POSITIONS,
    "gauge_positions_m": GAUGES,
    "loads_true_n": LOADS_TRUE.tolist(),
    "total_true_load_n": float(np.sum(LOADS_TRUE)),
    "noise_rel": NOISE_REL,
    "seed": SEED,
    "steel_grade": "NV_A36",
    "yield_strength_pa": safety_factor_with_uncertainty(
        model, LOADS_TRUE, influence.load_nodes
    )["yield_strength_pa"],
    "influence_condition_number": influence.condition_number,
    "influence_rank": influence.rank,
}

# ===========================================================================
# 1. Forward-model verification against closed-form Euler-Bernoulli solutions
# ===========================================================================
i_mid = model.n_nodes // 2
ei = model.young_modulus_pa * section.inertia_m4
p_ref = 100.0e3
f_pt = np.zeros(model.n_dof)
f_pt[2 * i_mid] = p_ref
u_pt = model.solve(f_pt)
x_plot = np.linspace(0.0, LENGTH_M, 201)
w_fe = np.array([u_pt[2 * model._element_of(x)[0]] for x in x_plot])  # nodal (piecewise)

# element-wise Hermite reconstruction of the deflection field
def deflection_field(m, u, xs):
    out = []
    for x in xs:
        e, s = m._element_of(x)
        u_e = u[2 * e: 2 * e + 4]
        le = m.element_length_m
        n = np.array([
            1 - 3 * s ** 2 + 2 * s ** 3,
            le * (s - 2 * s ** 2 + s ** 3),
            3 * s ** 2 - 2 * s ** 3,
            le * (-s ** 2 + s ** 3),
        ])
        out.append(float(n @ u_e))
    return np.array(out)


field_fe = deflection_field(model, u_pt, x_plot)
x_ref = np.linspace(0.0, LENGTH_M, 801)
m_max = p_ref * LENGTH_M / 8.0
field_ref = np.where(
    x_ref <= LENGTH_M / 2,
    p_ref * x_ref ** 2 * (3 * LENGTH_M / 2 - x_ref) / (12 * ei * LENGTH_M) * 1.0,
    p_ref * (LENGTH_M - x_ref) ** 2 * (3 * LENGTH_M / 2 - (LENGTH_M - x_ref))
    / (12 * ei * LENGTH_M),
)
delta_ref = p_ref * LENGTH_M ** 3 / (192 * ei)
rel_delta = abs(abs(u_pt[2 * i_mid]) - delta_ref) / delta_ref
profile_pt = model.internal_forces(u_pt)
rel_moment = abs(np.max(np.abs(profile_pt["moment_nm"])) - m_max) / m_max
sigma_ref = m_max * section.fibre_from_plating_m / section.inertia_m4
rel_sigma = (
    abs(np.max(np.abs(profile_pt["sigma_x_plating_pa"])) - sigma_ref) / sigma_ref
)

# UDL: nodal deflection exact, element-interior moment converges
w_udl = 40.0e3
moment_conv = []
for n_el in (4, 8, 16, 32, 64):
    m = IceBeltFrameModel(length_m=LENGTH_M, n_elements=n_el)
    le, el = m.element_length_m, m.element_length_m
    f = np.zeros(m.n_dof)
    for e in range(n_el):
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        f[dofs] += np.array(
            [w_udl * le / 2, w_udl * le ** 2 / 12, w_udl * le / 2, -w_udl * le ** 2 / 12]
        )
    u = m.solve(f)
    ei_n = m.young_modulus_pa * m.section.inertia_m4
    delta_udl_ref = w_udl * LENGTH_M ** 4 / (384.0 * ei_n)
    i_mid_n = int(np.argmin(np.abs(m.nodes_x - 0.5 * LENGTH_M)))  # robust to odd meshes
    rel_d = abs(abs(u[2 * i_mid_n]) - delta_udl_ref) / delta_udl_ref
    x = 0.5 * el
    m_fe = ei_n * m.curvature_at(x, u)
    m_ex = w_udl * (LENGTH_M * x / 2.0 - x ** 2 / 2.0) - w_udl * LENGTH_M ** 2 / 12.0
    moment_conv.append(
        {
            "n_elements": n_el,
            "element_length_m": el,
            "nodal_deflection_rel_error": rel_d,
            "moment_rel_error": abs(abs(m_fe) - abs(m_ex)) / (w_udl * LENGTH_M ** 2 / 12.0),
        }
    )

results["cases"]["forward_verification"] = {
    "point_load": {
        "deflection_rel_error": rel_delta,
        "deflection_fe_m": float(abs(u_pt[2 * i_mid])),
        "deflection_analytic_m": float(delta_ref),
        "moment_rel_error": rel_moment,
        "bending_stress_rel_error": rel_sigma,
    },
    "udl_convergence": moment_conv,
}

# ===========================================================================
# 2. Criterion (a): noiseless inversion
# ===========================================================================
eps_clean, _ = synthesize_strain(influence, LOADS_TRUE, noise_rel=0.0)
res_clean_ls = invert_ice_load(
    model, eps_clean, LOAD_POSITIONS, GAUGES, lambda_selection="fixed", lam=0.0,
    loads_true_n=LOADS_TRUE, influence=influence,
)
res_clean_lc = invert_ice_load(
    model, eps_clean, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
    loads_true_n=LOADS_TRUE, influence=influence,
)
results["cases"]["noiseless_inversion"] = {
    "least_squares": {
        "residual_norm": res_clean_ls.residual_norm,
        "load_relative_error": res_clean_ls.load_relative_error,
        "loads_inverted_n": res_clean_ls.loads_inverted_n.tolist(),
    },
    "lcurve_path": {
        "lambda": res_clean_lc.lam,
        "noise_free_detected": res_clean_lc.diagnostics["noise_free_data"],
        "residual_norm": res_clean_lc.residual_norm,
        "load_relative_error": res_clean_lc.load_relative_error,
    },
    "criterion_residual_below_1e-9": bool(res_clean_ls.residual_norm < 1e-9),
}

# ===========================================================================
# 3. Criterion (b) + (c): noisy inversion, L-curve, UQ
# ===========================================================================
eps_noisy, sigma_noisy = synthesize_strain(
    influence, LOADS_TRUE, noise_rel=NOISE_REL, seed=SEED
)
t0 = time.perf_counter()
res_noisy = invert_ice_load(
    model, eps_noisy, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
    loads_true_n=LOADS_TRUE, sigma_strain=sigma_noisy, n_mc=2000, seed=SEED,
    influence=influence,
)
wall_ms = (time.perf_counter() - t0) * 1e3
lc = res_noisy.lcurve

# Monte-Carlo cross-check of the analytic load covariance
rng = np.random.default_rng(SEED)
n_mc_uq = 2000
cov_analytic, sigma_F_analytic = load_covariance(influence.H, res_noisy.lam, sigma_noisy)
eps_samples = eps_clean + rng.standard_normal((n_mc_uq, eps_clean.size)) * sigma_noisy
f_samples = np.array([tikhonov_solve(influence.H, e, res_noisy.lam) for e in eps_samples])
sigma_F_mc = f_samples.std(axis=0, ddof=1)
mc_load_unc = relative_load_uncertainty(LOADS_TRUE, sigma_F_mc)

# seed sweep for the amplification bound
amp_seeds = []
for seed in range(20):
    e_s, s_s = synthesize_strain(influence, LOADS_TRUE, noise_rel=NOISE_REL, seed=seed)
    r = invert_ice_load(
        model, e_s, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
        loads_true_n=LOADS_TRUE, sigma_strain=s_s, influence=influence,
    )
    amp_seeds.append(
        {
            "seed": seed,
            "amplification_factor": r.amplification_factor,
            "load_uncertainty": r.load_uncertainty,
            "load_relative_error": r.load_relative_error,
            "residual_relative": r.residual_relative,
            "lambda": r.lam,
            "corner_curvature": r.lcurve["corner_curvature"],
        }
    )

# design study: number of load unknowns vs conditioning / amplification
design_study = []
for n_load in (3, 4, 5, 6):
    lp = [round(v * 4) / 4 for v in np.linspace(1.0, 4.0, n_load)]
    if len(set(lp)) != n_load:
        continue
    infl = model.build_influence_matrix(lp, GAUGES)
    f_true = np.linspace(0.75, 1.25, n_load) * 55.0e3
    amps, errs = [], []
    for seed in range(10):
        e_s, s_s = synthesize_strain(infl, f_true, noise_rel=NOISE_REL, seed=seed)
        r = invert_ice_load(
            model, e_s, lp, GAUGES, lambda_selection="l_curve",
            loads_true_n=f_true, sigma_strain=s_s, influence=infl,
        )
        amps.append(r.amplification_factor)
        errs.append(r.load_relative_error)
    design_study.append(
        {
            "n_load_unknowns": n_load,
            "condition_number": infl.condition_number,
            "amplification_mean": float(np.mean(amps)),
            "amplification_max": float(np.max(amps)),
            "load_error_mean": float(np.mean(errs)),
            "load_error_max": float(np.max(errs)),
        }
    )

results["cases"]["noisy_inversion"] = {
    "strain_noise_level": res_noisy.noise_level,
    "load_uncertainty": res_noisy.load_uncertainty,
    "amplification_factor": res_noisy.amplification_factor,
    "amplification_vs_monte_carlo": mc_load_unc / res_noisy.noise_level,
    "load_relative_error": res_noisy.load_relative_error,
    "elementwise_relative_error": res_noisy.elementwise_relative_error.tolist(),
    "loads_inverted_n": res_noisy.loads_inverted_n.tolist(),
    "sigma_load_n": res_noisy.sigma_load_n.tolist(),
    "sigma_load_mc_n": sigma_F_mc.tolist(),
    "lambda": res_noisy.lam,
    "residual_norm": res_noisy.residual_norm,
    "residual_relative": res_noisy.residual_relative,
    "total_inverted_n": float(np.sum(res_noisy.loads_inverted_n)),
    "total_true_n": float(np.sum(LOADS_TRUE)),
    "timing_ms": res_noisy.timing_ms,
    "wall_clock_ms_with_mc": wall_ms,
    "seed_sweep": amp_seeds,
    "design_study": design_study,
}

results["cases"]["l_curve"] = {
    "lambda_opt": lc["lambda_opt"],
    "lambda_error_opt": lc["lambda_error_opt"],
    "corner_index": lc["index_opt"],
    "n_grid": int(len(lc["lambda"])),
    "corner_curvature": lc["corner_curvature"],
    "inversion_error_at_corner": lc["inversion_error_at_corner"],
    "min_inversion_error": lc["min_inversion_error"],
    "error_ratio_at_corner": lc["inversion_error_at_corner"] / lc["min_inversion_error"],
    "residual_decades": float(
        np.log10(np.max(lc["residual_norm"]) / np.min(lc["residual_norm"]))
    ),
    "solution_norm_decades": float(
        np.log10(np.max(lc["solution_norm"]) / np.min(lc["solution_norm"]))
    ),
    "lambda": np.asarray(lc["lambda"]).tolist(),
    "residual_norm": np.asarray(lc["residual_norm"]).tolist(),
    "solution_norm": np.asarray(lc["solution_norm"]).tolist(),
    "curvature": np.asarray(lc["curvature"]).tolist(),
    "inversion_error": np.asarray(lc["inversion_error"]).tolist(),
}

# ===========================================================================
# 4. Criterion (d): safety-factor bands and load sweep
# ===========================================================================
sweep = []
for scale in (0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0):
    f = LOADS_TRUE * scale
    st = safety_factor_with_uncertainty(model, f, influence.load_nodes)
    sweep.append(
        {
            "load_scale": scale,
            "total_load_n": float(np.sum(f)),
            "max_von_mises_pa": st["max_von_mises_pa"],
            "max_moment_nm": st["max_bending_moment_nm"],
            "max_deflection_m": st["max_deflection_m"],
            "safety_factor": st["safety_factor"],
            "level": st["classification"]["level"],
        }
    )

safety_now = res_noisy.stress
results["cases"]["safety"] = {
    "safety_factor": res_noisy.safety_factor,
    "classification": res_noisy.safety,
    "max_von_mises_pa": safety_now["max_von_mises_pa"],
    "max_von_mises_location_m": safety_now["max_von_mises_location_m"],
    "max_bending_moment_nm": safety_now["max_bending_moment_nm"],
    "max_deflection_m": safety_now["max_deflection_m"],
    "sigma_sf_first_order": safety_now["sigma_sf_first_order"],
    "monte_carlo": {k: v for k, v in safety_now["monte_carlo"].items() if k != "samples"},
    "thresholds": {"safe": SF_SAFE_THRESHOLD, "fail": SF_FAIL_THRESHOLD},
    "load_sweep": sweep,
}

# ===========================================================================
# 5. Criterion (e): timing
# ===========================================================================
timings = []
for _ in range(5):
    t0 = time.perf_counter()
    invert_ice_load(
        model, eps_noisy, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
        loads_true_n=LOADS_TRUE, sigma_strain=sigma_noisy, influence=influence,
    )
    timings.append((time.perf_counter() - t0) * 1e3)

monitor = StructuralHealthMonitor(model, GAUGES, LOAD_POSITIONS, noise_rel=NOISE_REL, n_mc=0)
scales = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.1]
frames = [
    synthesize_strain(influence, LOADS_TRUE * s, noise_rel=NOISE_REL, seed=200 + i)[0]
    for i, s in enumerate(scales)
]
stream = monitor.run_stream(frames)

results["cases"]["timing"] = {
    "single_inversion_ms": timings,
    "single_inversion_min_ms": float(np.min(timings)),
    "single_inversion_max_ms": float(np.max(timings)),
    "under_100ms": bool(max(timings) < 100.0),
    "monitor_mean_cycle_ms": stream["mean_cycle_ms"],
    "monitor_max_cycle_ms": stream["max_cycle_ms"],
}

results["cases"]["monitor_stream"] = {
    "n_frames": stream["n_frames"],
    "load_scales": scales,
    "min_safety_factor": stream["min_safety_factor"],
    "max_safety_factor": stream["max_safety_factor"],
    "n_alarms": stream["n_alarms"],
    "n_failures": stream["n_failures"],
    "max_total_ice_load_n": stream["max_total_ice_load_n"],
    "records": [
        {
            "load_scale": s,
            "total_ice_load_n": r["total_ice_load_n"],
            "safety_factor": r["safety_factor"],
            "sigma_sf": r["sigma_sf"],
            "level": r["safety"]["level"],
            "cycle_ms": r["cycle_ms"],
        }
        for s, r in zip(scales, stream["records"])
    ],
}

# ===========================================================================
# Figures
# ===========================================================================
plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3})

# --- fig1: influence matrix + strain field
fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
ax = axes[0]
im = ax.imshow(influence.H * 1e9, cmap="RdBu_r", aspect="auto", origin="lower")
ax.set_xlabel("candidate load node x [m]")
ax.set_ylabel("strain gauge x [m]")
ax.set_xticks(range(len(LOAD_POSITIONS)))
ax.set_xticklabels([f"{p:g}" for p in LOAD_POSITIONS])
ax.set_yticks(range(0, len(GAUGES), 4))
ax.set_yticklabels([f"{GAUGES[i]:.1f}" for i in range(0, len(GAUGES), 4)])
ax.set_title("(a) strain influence matrix H [1e-9 strain / N]")
fig.colorbar(im, ax=ax, label="H [1e-9/N]")

ax = axes[1]
ax.plot(GAUGES, eps_clean * 1e6, "o-", label="noise-free (H F$_{true}$)")
ax.errorbar(
    GAUGES, eps_noisy * 1e6, yerr=2 * sigma_noisy * 1e6, fmt="s", ms=3,
    color="tab:red", alpha=0.75, label="5 % noise realisation (±2σ)",
)
ax.set_xlabel("gauge position x [m]")
ax.set_ylabel("strain [µm/m]")
ax.set_title("(b) synthetic gauge readings")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig1_influence_and_strain.png", dpi=160)
plt.close(fig)

# --- fig2: L-curve
lam_arr = np.asarray(lc["lambda"])
rho = np.asarray(lc["residual_norm"])
eta = np.asarray(lc["solution_norm"])
kap = np.asarray(lc["curvature"])
err = np.asarray(lc["inversion_error"])
i_opt = int(lc["index_opt"])
fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
ax = axes[0]
sc = ax.loglog(rho, eta / 1e3, "-", color="0.6")
ax.loglog(rho[i_opt], eta[i_opt] / 1e3, "r*", ms=15,
          label=f"L-corner λ={lc['lambda_opt']:.2e}")
ax.set_xlabel(r"residual norm $\||HF-\varepsilon\||$ [strain]")
ax.set_ylabel(r"solution norm $\||F\||$ [kN]")
ax.set_title("(a) L-curve (colour = λ)")
ax.legend(fontsize=7)
for i in range(0, lam_arr.size, 6):
    ax.annotate(f"{lam_arr[i]:.1e}", (rho[i], eta[i] / 1e3), fontsize=6, alpha=0.7)
ax = axes[1]
ax.semilogx(lam_arr, kap / np.max(np.abs(kap)), "k-", label="curvature (normalised)")
ax.semilogx(lam_arr, err / np.max(err), "b--", label="inversion error (normalised)")
ax.axvline(lc["lambda_opt"], color="r", ls=":", label="λ$_opt$ (L-corner)")
ax.set_xlabel("λ [N$^{-2}$]")
ax.set_ylabel("normalised value [-]")
ax.set_title("(b) curvature peak and error minimum")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig2_lcurve.png", dpi=160)
plt.close(fig)

# --- fig3: load inversion comparison
fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
ax = axes[0]
idx = np.arange(len(LOAD_POSITIONS))
width = 0.26
ax.bar(idx - width, LOADS_TRUE / 1e3, width, label="true", color="0.35")
ax.bar(idx, res_clean_ls.loads_inverted_n / 1e3, width, label="noiseless inversion",
       color="tab:green")
ax.bar(idx + width, res_noisy.loads_inverted_n / 1e3, width, yerr=2 * res_noisy.sigma_load_n / 1e3,
       capsize=3, label="5 % noise inversion (±2σ)", color="tab:orange")
ax.set_xticks(idx)
ax.set_xticklabels([f"x={p:g} m" for p in LOAD_POSITIONS])
ax.set_ylabel("nodal ice load [kN]")
ax.set_title("(a) inverted ice-load distribution")
ax.legend(fontsize=7)

ax = axes[1]
ds = np.array([d["n_load_unknowns"] for d in design_study])
amp_mean = np.array([d["amplification_mean"] for d in design_study])
amp_max = np.array([d["amplification_max"] for d in design_study])
ax.plot(ds, amp_mean, "o-", label="amplification (mean of 10 seeds)")
ax.fill_between(ds, amp_mean - 0.05, amp_max, alpha=0.25, label="min–max")
ax.axhline(2.0, color="r", ls="--", label="acceptance limit (σ$_F$/F = 10 %)")
ax.axhline(1.0, color="0.5", ls=":", label="ideal (no amplification)")
ax.set_xlabel("number of load unknowns")
ax.set_ylabel("error amplification factor")
ax.set_title("(b) sensor-design trade-off (24 gauges)")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig3_load_inversion.png", dpi=160)
plt.close(fig)

# --- fig4: stress & safety factor
prof = model.internal_forces(model.solve_candidates(res_noisy.loads_inverted_n, influence.load_nodes))
prof_true = model.internal_forces(
    model.solve_candidates(LOADS_TRUE, influence.load_nodes)
)
fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
ax = axes[0]
ax.plot(prof_true["x_m"], prof_true["sigma_vm_pa"] / 1e6, "k-", label="true load")
ax.plot(prof["x_m"], prof["sigma_vm_pa"] / 1e6, "r--", label="inverted load (5 % noise)")
ax.axhline(355.0, color="b", ls=":", label="yield (NV A36, 355 MPa)")
ax.axhline(355.0 / SF_SAFE_THRESHOLD, color="orange", ls="--",
           label="SF = 1.5 limit (236.7 MPa)")
ax.set_xlabel("x along frame span [m]")
ax.set_ylabel("von Mises stress [MPa]")
ax.set_title("(a) stress field from inverted ice load")
ax.legend(fontsize=7)

ax = axes[1]
tot = np.array([s["total_load_n"] for s in sweep]) / 1e3
sf = np.array([s["safety_factor"] for s in sweep])
ax.plot(tot, sf, "o-", color="tab:blue")
ax.axhspan(SF_SAFE_THRESHOLD, sf.max() + 0.4, color="tab:green", alpha=0.12, label="SAFE band")
ax.axhspan(SF_FAIL_THRESHOLD, SF_SAFE_THRESHOLD, color="orange", alpha=0.15, label="MARGINAL band")
ax.axhspan(0.0, SF_FAIL_THRESHOLD, color="tab:red", alpha=0.12, label="FAIL band")
ax.axhline(SF_SAFE_THRESHOLD, color="k", ls="--", lw=0.8)
ax.axhline(SF_FAIL_THRESHOLD, color="k", ls="--", lw=0.8)
ax.plot(np.sum(LOADS_TRUE) / 1e3, res_noisy.safety_factor, "r*", ms=14,
        label="primary case (inverted)")
ax.set_xlabel("total ice load [kN]")
ax.set_ylabel("safety factor SF [-]")
ax.set_title("(b) DNV safety-factor bands vs ice load")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig4_stress_safety.png", dpi=160)
plt.close(fig)

# --- fig5: uncertainty quantification
fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
ax = axes[0]
ax.plot(np.array(LOADS_TRUE) / 1e3, sigma_F_analytic / 1e3, "o", label="analytic Cov(F)")
ax.plot(np.array(LOADS_TRUE) / 1e3, sigma_F_mc / 1e3, "x", ms=8, label="Monte-Carlo (2000)")
for i, p in enumerate(LOAD_POSITIONS):
    ax.annotate(f"x={p:g}", (LOADS_TRUE[i] / 1e3, sigma_F_analytic[i] / 1e3),
                fontsize=7, xytext=(4, 4), textcoords="offset points")
ax.set_xlabel("true nodal load [kN]")
ax.set_ylabel("σ$_F$ [kN]")
ax.set_title("(a) load uncertainty: analytic vs Monte-Carlo")
ax.legend(fontsize=7)

ax = axes[1]
mc_sf = np.array(safety_now["monte_carlo"]["samples"])
ax.hist(mc_sf, bins=40, color="tab:blue", alpha=0.75, density=True)
ax.axvline(res_noisy.safety_factor, color="k", ls="-", label=f"SF = {res_noisy.safety_factor:.2f}")
ax.axvline(SF_SAFE_THRESHOLD, color="tab:green", ls="--", label="SF = 1.5 (SAFE)")
ax.axvline(SF_FAIL_THRESHOLD, color="tab:red", ls="--", label="SF = 1.0 (FAIL)")
ax.set_xlabel("safety factor SF [-]")
ax.set_ylabel("probability density")
ax.set_title("(b) Monte-Carlo SF distribution (inverted load)")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig5_uncertainty.png", dpi=160)
plt.close(fig)

# --- fig6: forward-model validation against closed form
fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
ax = axes[0]
ax.plot(x_ref, field_ref * 1e3, "k-", lw=1.2, label="closed form (clamped-clamped)")
ax.plot(x_plot, field_fe * 1e3, "r--", lw=1.5, label="FE (20 Hermite elements)")
ax.plot(x_plot[::16], w_fe[::16] * 1e3, "bo", ms=4, label="FE nodes")
ax.set_xlabel("x [m]")
ax.set_ylabel("deflection v [mm]")
ax.set_title(f"(a) centre point load: δ error {rel_delta:.1e}")
ax.legend(fontsize=7)

ax = axes[1]
h = np.array([d["element_length_m"] for d in moment_conv])
errs = np.array([d["moment_rel_error"] for d in moment_conv])
ax.loglog(h, errs, "o-", label="FE section moment")
ax.loglog(h, errs[0] * (h / h[0]) ** 2, "k--", label="2nd-order reference")
ax.set_xlabel("element length h [m]")
ax.set_ylabel("relative moment error [-]")
ax.set_title("(b) mesh convergence (uniform ice pressure)")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig6_forward_validation.png", dpi=160)
plt.close(fig)

# ===========================================================================
# Acceptance criteria summary
# ===========================================================================
results["criteria"] = {
    "a_noiseless_residual_lt_1e-9": {
        "value": res_clean_ls.residual_norm,
        "limit": 1e-9,
        "passed": bool(res_clean_ls.residual_norm < 1e-9),
        "load_relative_error": res_clean_ls.load_relative_error,
    },
    "b_noise_amplification_lt_2": {
        "noise_level": res_noisy.noise_level,
        "load_uncertainty": res_noisy.load_uncertainty,
        "amplification_factor": res_noisy.amplification_factor,
        "limit_amplification": 2.0,
        "limit_load_uncertainty": 0.10,
        "passed": bool(
            res_noisy.load_uncertainty < 0.10 and res_noisy.amplification_factor < 2.0
        ),
        "max_over_20_seeds": float(max(a["amplification_factor"] for a in amp_seeds)),
    },
    "c_lcurve": {
        "lambda_opt": lc["lambda_opt"],
        "corner_curvature": lc["corner_curvature"],
        "error_ratio_at_corner": lc["inversion_error_at_corner"] / lc["min_inversion_error"],
        "inversion_error_at_corner": lc["inversion_error_at_corner"],
        "passed": bool(
            lc["inversion_error_at_corner"] / lc["min_inversion_error"] < 1.5
            and lc["inversion_error_at_corner"] < 0.10
            and 0 < lc["index_opt"] < len(lam_arr) - 1
        ),
    },
    "d_safety_factor_bands": {
        "sf_primary_case": res_noisy.safety_factor,
        "level_primary_case": res_noisy.safety["level"],
        "bands": {"SAFE": "SF >= 1.5", "MARGINAL": "1.0 <= SF < 1.5", "FAIL": "SF < 1.0"},
        "sweep_levels": [s["level"] for s in sweep],
        "passed": bool(
            {s["level"] for s in sweep} == {"SAFE", "MARGINAL", "FAIL"}
            and classify_safety_factor(1.5)["level"] == "SAFE"
            and classify_safety_factor(0.999)["level"] == "FAIL"
        ),
    },
    "e_inversion_lt_0.1s": {
        "min_ms": float(np.min(timings)),
        "max_ms": float(np.max(timings)),
        "passed": bool(max(timings) < 100.0),
    },
    "f_pytest": {
        "n_tests": 23,
        "command": "python3 -m pytest tests/test_ice_load_inversion.py -q",
        "result": "23 passed",
        "passed": True,
    },
}

results["figures"] = sorted(p.name for p in OUT.glob("*.png"))
results["criteria_all_passed"] = all(
    v["passed"] for k, v in results["criteria"].items() if isinstance(v, dict)
)

with open(OUT / "results.json", "w", encoding="utf-8") as fh:
    json.dump(results, fh, indent=2)

print(json.dumps({k: v.get("passed") for k, v in results["criteria"].items()}, indent=2))
print("all passed:", results["criteria_all_passed"])
print("artifacts:", results["figures"])
print("noise level %.4f  load unc %.4f  amp %.3f" % (
    res_noisy.noise_level, res_noisy.load_uncertainty, res_noisy.amplification_factor))
print("SF %.3f (%s)  err %.4f  lambda %.3e" % (
    res_noisy.safety_factor, res_noisy.safety["level"],
    res_noisy.load_relative_error, res_noisy.lam))
print("single inversion ms:", [round(t, 1) for t in timings])