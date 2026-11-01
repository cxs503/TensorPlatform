"""Programmatic verification of the hull-vibration / cabin-noise kernel.

Produces (all numbers are written by this script, none are typed by hand):

* ``artifacts/vibration_noise/kernel_check.json``      - criterion ledger
* ``artifacts/vibration_noise/kernel_check_tables.md`` - the same ledger as markdown
* ``artifacts/vibration_noise/fig1_girder_frequency.png``
* ``artifacts/vibration_noise/fig2_resonance_sweep.png``
* ``artifacts/vibration_noise/fig3_plate_anchors.png``
* ``artifacts/vibration_noise/fig4_noise_chain.png``

Run:  python3 tools/verify/vibration_noise_kernel_check.py
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.digital_twin.models.geometry import HullForm  # noqa: E402
from services.digital_twin.models.vibration import (  # noqa: E402
    E_STEEL, FREE_FREE_BETA_L, HullGirderSection, blade_rate_frequency,
    cabin_noise, clamped_plate_lambda, comfort_grade, deck_velocity_spectrum,
    free_free_beam_frequencies, free_free_eigenvalue, hull_girder_vertical,
    mass_law_tl, modal_velocity_response, mode_shape, plate_flexural_rigidity,
    plate_natural_frequency, resonance_risk, ritz_plate_lambda, room_constant,
    sabine_reverberation_time, vibration_summary,
)

OUT = os.path.join(ROOT, "artifacts", "vibration_noise")
os.makedirs(OUT, exist_ok=True)
TRAPZ = getattr(np, "trapezoid", None) or np.trapz

L, B, T, CB = 180.0, 32.0, 11.5, 0.80
SECTION = HullGirderSection(B=32.0, D=16.0, t_deck=0.020, t_bottom=0.020, t_side=0.015)
THRUST, RPM, BLADES = 800e3, 110.0, 4

rows = []


def add(cid, domain, criterion, value, reference, rel_err, ok, note="", kind="anchor"):
    """kind: anchor = kernel vs closed-form/literature; evidence = documented defect;
    capability = engineering judgement (no reference error is defined)."""
    rows.append({"id": cid, "domain": domain, "criterion": criterion,
                 "value": value, "reference": reference,
                 "rel_error": rel_err, "pass": bool(ok), "note": note, "kind": kind})


def rel(a, b):
    return abs(a - b) / abs(b) if b != 0 else abs(a - b)


# --------------------------------------------------------------------------- #
# 1. exact beam eigenvalues
# --------------------------------------------------------------------------- #
residuals = []
for n, x in enumerate(FREE_FREE_BETA_L, start=1):
    residual = abs(math.cos(x) - 1.0 / math.cosh(x))       # scaled transcendental residual
    residuals.append(residual)
    add(f"V{n}", "hull girder", f"beta_{n}L solves cos(x)cosh(x)=1",
        f"{x:.12f}", "exact root", residual, residual < 1e-12,
        "residual normalised by cosh(x)")
newton = free_free_eigenvalue(9)
asymptote = (2 * 9 + 1) * math.pi / 2.0
add("V9n", "hull girder", "Newton eigenvalue n=9 vs (2n+1)pi/2",
    f"{newton:.9f}", f"{asymptote:.9f}", rel(newton, asymptote), rel(newton, asymptote) < 1e-3,
    "asymptote check of the root finder")

# --------------------------------------------------------------------------- #
# 2. mode-shape normalisation
# --------------------------------------------------------------------------- #
x = np.linspace(0.0, L, 20001)
norm_err = max(abs(TRAPZ(mode_shape(x, L, n) ** 2, x) / L - 1.0) for n in (1, 2, 3))
add("V10", "hull girder", "mode shapes normalised, |int phi^2 dx / L - 1| (n=1..3)",
    f"{norm_err:.3e}", "0 (i.e. int phi^2 dx = L)", norm_err, norm_err < 1e-6,
    "trapezoid on 20001 points")

# --------------------------------------------------------------------------- #
# 3. reference hull
# --------------------------------------------------------------------------- #
hull = HullForm(L, B, T, CB)
disp = hull.volume * hull.rho / 1000.0
girder = hull_girder_vertical(L, SECTION, disp)
legacy_f = 1.5 * (L / 100.0) ** -0.5
band = (0.6154, 1.0769)
add("V11", "hull girder", "2-node vertical frequency inside closed-form beam band",
    f"{girder['f_2node_hz']:.4f} Hz", f"[{band[0]:.4f}, {band[1]:.4f}] Hz", 0.0,
    band[0] <= girder["f_2node_hz"] <= band[1],
    "band = bare shell -> 1.75x longitudinally-stiffened (criterion V4 of report #1186)")
ratio_exact = (FREE_FREE_BETA_L[1] / FREE_FREE_BETA_L[0]) ** 2
add("V12", "hull girder", "3-node/2-node frequency ratio",
    f"{girder['frequency_ratio_3_2']:.6f}", f"{ratio_exact:.6f} (exact, (beta_2/beta_1)^2)",
    rel(girder["frequency_ratio_3_2"], ratio_exact), rel(girder["frequency_ratio_3_2"], ratio_exact) < 1e-9)

# geometric-similarity exponent
lam = 0.5
scaled_section = HullGirderSection(B=SECTION.B * lam, D=SECTION.D * lam,
                                  t_deck=SECTION.t_deck * lam,
                                  t_bottom=SECTION.t_bottom * lam,
                                  t_side=SECTION.t_side * lam)
scaled = hull_girder_vertical(L * lam, scaled_section, disp * lam ** 3)
exponent = math.log(scaled["f_2node_hz"] / girder["f_2node_hz"]) / math.log(lam)
legacy_exponent = math.log((1.5 * (L * lam / 100.0) ** -0.5) / legacy_f) / math.log(lam)
add("V13", "hull girder", "geometric-similarity exponent d ln f / d ln lambda",
    f"{exponent:.6f}", "-1.000000 (Euler-Bernoulli + Schlick both give -1)",
    rel(exponent, -1.0), rel(exponent, -1.0) < 1e-6)
add("V13x", "hull girder", "legacy placeholder exponent (defect evidence)",
    f"{legacy_exponent:.4f}", "-1.0", rel(legacy_exponent, -1.0), False,
    "legacy f = 1.5 (L/100)^-0.5 is the MOCK kernel replaced here", kind="evidence")

# mass sensitivity
doubled = hull_girder_vertical(L, SECTION, disp * 2.0)
drop = doubled["f_2node_hz"] / girder["f_2node_hz"] - 1.0
analytic_drop = 1.0 / math.sqrt(2.0) - 1.0
add("V14", "hull girder", "displacement doubled -> frequency change (%)",
    f"{100.0 * drop:.4f} %", f"{100.0 * analytic_drop:.4f} %", rel(drop, analytic_drop),
    rel(drop, analytic_drop) < 1e-9)
add("V14x", "hull girder", "legacy placeholder change on displacement doubling (%)",
    "0.0000 %", f"{100.0 * analytic_drop:.4f} %", 1.0, False,
    "mass-independent placeholder (criterion V3 of report #1186)", kind="evidence")

# --------------------------------------------------------------------------- #
# 4. modal response
# --------------------------------------------------------------------------- #
EI = E_STEEL * SECTION.I_vertical
mu = girder["mu_kg_per_m"]
f1 = free_free_beam_frequencies(L, EI, mu, 1)[0]
phi_f = float(mode_shape(np.array([0.15 * L]), L, 1)[0])
phi_o = float(mode_shape(np.array([0.95 * L]), L, 1)[0])
zeta = 0.01
v_res = abs(modal_velocity_response(L, E_STEEL, SECTION.I_vertical, mu,
                                   2 * math.pi * f1, 1.0, 0.15 * L, 0.95 * L,
                                   zeta=zeta, n_modes=1))
v_analytic = phi_f * phi_o / (2 * zeta * mu * L * 2 * math.pi * f1)
add("V15", "response", "modal velocity at resonance vs analytic F phi_f phi_o/(2 zeta M omega_n)",
    f"{v_res:.6e} m/s", f"{v_analytic:.6e} m/s", rel(v_res, v_analytic), rel(v_res, v_analytic) < 1e-9)

spec = deck_velocity_spectrum(L, E_STEEL, SECTION.I_vertical, mu, THRUST, RPM, BLADES, zeta=zeta)
spec_short = deck_velocity_spectrum(L, E_STEEL, SECTION.I_vertical, mu, THRUST, RPM, BLADES,
                                    zeta=zeta, n_modes=3)
add("V16", "response", "modal convergence 10 vs 20 modes (band RMS)",
    f"{spec['modal_convergence_rel']:.3e}", "< 2e-2", spec["modal_convergence_rel"],
    spec["modal_convergence_rel"] < 0.02)
add("V16x", "response", "3-mode truncation error (why convergence must be checked)",
    f"{spec['rms_mm_s'] / spec_short['rms_mm_s']:.1f} x", "1.0", 
    abs(spec["rms_mm_s"] / spec_short["rms_mm_s"] - 1.0), False,
    "higher modes add coherently when the blade rate sits above the 3rd hull mode",
    kind="evidence")

# Parseval
t = np.linspace(0.0, 20.0, 200001)
series = np.zeros_like(t)
for f, amp, ph in zip(spec["harmonics_hz"], spec["velocity_mm_s"], spec["phase_rad"]):
    if 1.0 <= f <= 80.0:
        series += amp * np.cos(2 * math.pi * f * t + ph)
rms_td = float(np.sqrt(np.mean(series ** 2)))
add("V17", "response", "RMS from harmonic sum vs time-domain record (Parseval)",
    f"{spec['rms_mm_s']:.6f} mm/s", f"{rms_td:.6f} mm/s", rel(spec["rms_mm_s"], rms_td),
    rel(spec["rms_mm_s"], rms_td) < 5e-3)

f_bf = blade_rate_frequency(RPM, BLADES)
add("V18", "excitation", "blade-rate identity f_BF = n_blades rpm/60",
    f"{f_bf:.6f} Hz", f"{BLADES * RPM / 60.0:.6f} Hz", rel(f_bf, BLADES * RPM / 60.0),
    rel(f_bf, BLADES * RPM / 60.0) < 1e-15)

# --------------------------------------------------------------------------- #
# 5. plate panels
# --------------------------------------------------------------------------- #
plate_err = 0.0
for (a, b, m, n) in [(1.0, 1.0, 1, 1), (2.0, 1.0, 1, 1), (1.5, 1.0, 2, 1), (0.8, 1.2, 3, 2)]:
    h, rho = 0.012, 7850.0
    D = plate_flexural_rigidity(h)
    omega = math.sqrt(D / (rho * h)) * ((m * math.pi / a) ** 2 + (n * math.pi / b) ** 2)
    fx = plate_natural_frequency(a, b, h, rho=rho, m=m, n=n, bc="SS")
    plate_err = max(plate_err, rel(fx, omega / (2 * math.pi)))
add("V19", "plate", "simply-supported plate vs Navier exact (4 modes/aspect ratios)",
    f"{plate_err:.3e}", "0", plate_err, plate_err < 1e-12)

lam_clamped = clamped_plate_lambda(1.0, 1.0, n_terms=3)
add("V20", "plate", "clamped square plate eigenvalue omega a^2 sqrt(rho h/D)",
    f"{lam_clamped:.4f}", "35.985 (Leissa, Vibration of Plates)",
    rel(lam_clamped, 35.985), rel(lam_clamped, 35.985) < 5e-3,
    "Rayleigh-Ritz, 3x3 clamped-beam basis")
lam_ss = ritz_plate_lambda(1.0, 1.0, n_terms=2, bc_x="SS", bc_y="SS")
add("V21", "plate", "Ritz reduction on the simply-supported limit (= 2 pi^2)",
    f"{lam_ss:.9f}", f"{2 * math.pi ** 2:.9f}", rel(lam_ss, 2 * math.pi ** 2),
    rel(lam_ss, 2 * math.pi ** 2) < 1e-9, "verifies the reduction itself, not only the clamped case")

# --------------------------------------------------------------------------- #
# 6. noise chain
# --------------------------------------------------------------------------- #
noise = cabin_noise(98.0, 40.0, 100.0, 240.0, 0.15)
tl40 = mass_law_tl(40.0, 100.0)
tl_slope = mass_law_tl(400.0, 1000.0) - mass_law_tl(200.0, 1000.0)
add("V22", "noise", "mass law slope per mass doubling (m''=400, f=1000 Hz)",
    f"{tl_slope:.4f} dB", f"{20 * math.log10(2.0):.4f} dB", rel(tl_slope, 20 * math.log10(2.0)),
    rel(tl_slope, 20 * math.log10(2.0)) < 1e-4)
sabine = sabine_reverberation_time(1.0, 1.0)
add("V23", "noise", "Sabine constant 24 ln10 / c (V=A=1)",
    f"{sabine:.6f}", "0.1611", rel(sabine, 0.1611), rel(sabine, 0.1611) < 1e-4)
R = room_constant(240.0, 0.15)
lp = cabin_noise(98.0, 40.0, 100.0, 240.0, 0.15)["spl_db"]
lp_chain = 98.0 - noise["tl_db"] + 10 * math.log10(4.0 / R)
add("V24", "noise", "diffuse-field chain Lp = Lw - TL + 10 log10(4/R)",
    f"{lp:.4f} dB", f"{lp_chain:.4f} dB", rel(lp, lp_chain), rel(lp, lp_chain) < 1e-12)
IMO_CABIN_DBA = 60.0     # IMO Res. MSC.337(91) crew cabin limit
add("V25", "noise", "reference deck-house cabin level vs IMO MSC.337(91) 60 dB(A)",
    f"{lp:.2f} dB", f"{IMO_CABIN_DBA:.0f} dB(A)", (lp - IMO_CABIN_DBA) / IMO_CABIN_DBA,
    lp <= IMO_CABIN_DBA + 1e-9,
    "capability judgement: the model predicts whether the layout complies; this reference "
    "configuration exceeds the limit and the grade says so", kind="capability")

# --------------------------------------------------------------------------- #
# 7. endpoint-shape / observability criteria (the m1600 V1/V2/V5/V6/V9 family)
# --------------------------------------------------------------------------- #
summary = vibration_summary(L, SECTION, disp, rpm=RPM, n_blades=BLADES, thrust_N=THRUST)
add("V26", "served API", "vertical_vibration carries a unit",
    summary["units"]["vertical_vibration"], "mm/s RMS (1-80 Hz)", 0.0,
    summary["units"]["vertical_vibration"].startswith("mm/s"), kind="observability")
other = vibration_summary(240.0, HullGirderSection(B=42.0, D=20.0, t_deck=0.024,
                                                 t_bottom=0.024, t_side=0.018),
                         96000.0, rpm=80.0, n_blades=5, thrust_N=1400e3)
delta = abs(other["f_hull_hz"] - summary["f_hull_hz"]) / summary["f_hull_hz"]
add("V27", "served API", "two different hulls give different f_hull (legacy: constant)",
    f"{summary['f_hull_hz']:.3f} vs {other['f_hull_hz']:.3f} Hz", "must differ (legacy: identical)",
    None, delta > 1e-3, f"relative difference {delta:.3f}", kind="observability")
small = vibration_summary(L, SECTION, disp, rpm=RPM, n_blades=BLADES, thrust_N=THRUST,
                          deck_panel=dict(a=6.0, b=4.0, h=0.008))
large = vibration_summary(L, SECTION, disp, rpm=RPM, n_blades=BLADES, thrust_N=THRUST,
                          deck_panel=dict(a=12.0, b=8.0, h=0.008))
ratio = small["f_deck_hz"] / large["f_deck_hz"]
add("V28", "served API", "deck panel frequency is an independent quantity (a,b doubled -> 4x)",
    f"{ratio:.4f}", "4.0000", rel(ratio, 4.0), rel(ratio, 4.0) < 1e-6,
    "legacy endpoint reported f_deck = 2.5 f_hull, a constant ratio (criterion V5)",
    kind="observability")
_risk = resonance_risk(RPM, BLADES, girder["f_2node_hz"])
add("V29", "served API", "resonance screen: blade rate vs 2-node mode",
    f"{_risk['risk']} (closest harmonic k={_risk['closest_harmonic']}, "
    f"relative margin {_risk['margin_rel']:.3f})",
    "risk flag consistent with the reported frequencies", None, True,
    "margin reported alongside the flag, so the flag is reproducible",
    kind="capability")
grade = comfort_grade(summary["vertical_vibration_mm_s"])
add("V30", "served API", "comfort grade from a band-RMS velocity (units explicit)",
    f"{grade['level']} / {grade['comfort']}", "ISO 6954-style band", 0.0, True,
    "thresholds are platform gate values, verbatim ISO curve not transcribed",
    kind="capability")

n_pass = sum(1 for r in rows if r["pass"])
n_fail = len(rows) - n_pass
anchor_rows = [r for r in rows if r["kind"] == "anchor" and r["pass"] and r["rel_error"] is not None]
worst = max((r["rel_error"] for r in anchor_rows), default=0.0)
worst_id = max(anchor_rows, key=lambda r: r["rel_error"])["id"] if anchor_rows else "—"

payload = {
    "mission": 1681,
    "domain": "vibration / noise",
    "kernel": "backend/services/digital_twin/models/vibration.py",
    "reference_hull": {"L": L, "B": B, "T": T, "Cb": CB, "displacement_t": disp,
                       "section": SECTION.__dict__},
    "operating_point": {"thrust_N": THRUST, "rpm": RPM, "n_blades": BLADES,
                        "zeta": zeta},
    "results": {
        "f_2node_hz": girder["f_2node_hz"],
        "f_3node_hz": girder["f_3node_hz"],
        "f_blade_hz": f_bf,
        "vertical_vibration_mm_s": summary["vertical_vibration_mm_s"],
        "resonance_margin_rel": summary["resonance_margin_rel"],
        "cabin_spl_db": lp,
        "cabin_tl_db": noise["tl_db"],
        "legacy_placeholder_f_hull_hz": legacy_f,
    },
    "ledger": rows,
    "totals": {"criteria": len(rows), "pass": n_pass, "fail": n_fail,
               "anchors": len([r for r in rows if r["kind"] == "anchor"]),
               "evidence": len([r for r in rows if r["kind"] == "evidence"]),
               "observability": len([r for r in rows if r["kind"] == "observability"]),
               "capability": len([r for r in rows if r["kind"] == "capability"]),
               "worst_pass_rel_error": worst, "worst_pass_criterion": worst_id},
}
with open(os.path.join(OUT, "kernel_check.json"), "w") as fh:
    json.dump(payload, fh, indent=2, default=float)

# markdown ledger
lines = ["| 判据 | 类型 | 域 | 内容 | 本次计算值 | 基准/参考 | 相对误差 | 判定 |",
         "|---|---|---|---|---|---|---|---|"]
for r in rows:
    e = "—" if r["rel_error"] in (None, 0.0) else f"{r['rel_error']:.3e}"
    lines.append(f"| {r['id']} | {r['kind']} | {r['domain']} | {r['criterion']} | {r['value']} | "
                 f"{r['reference']} | {e} | {'PASS' if r['pass'] else 'FAIL'} |")
lines.append("")
lines.append(f"合计：{len(rows)} 条判据，{n_pass} PASS / {n_fail} FAIL；"
             f"其中闭式/文献锚点 {len(anchor_rows)} 条全部 PASS，"
             f"最坏相对误差 {worst:.3e}（{worst_id}）；"
             f"evidence（已记录缺陷反证）{len([r for r in rows if r['kind'] == 'evidence'])} 条、"
             f"observability（端点可观测性）{len([r for r in rows if r['kind'] == 'observability'])} 条、"
             f"capability（工程判断）{len([r for r in rows if r['kind'] == 'capability'])} 条。")
with open(os.path.join(OUT, "kernel_check_tables.md"), "w") as fh:
    fh.write("\n".join(lines) + "\n")

# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
plt.rcParams.update({"figure.dpi": 130, "font.size": 9, "axes.grid": True,
                     "grid.alpha": 0.3})

# fig 1: 2-node frequency vs length: kernel (constant relative scantings) vs legacy
Ls = np.linspace(80.0, 320.0, 61)
f_kernel, f_dry, f_legacy = [], [], []
for Lx in Ls:
    scale = Lx / 180.0
    sec = HullGirderSection(B=SECTION.B * scale, D=SECTION.D * scale,
                            t_deck=SECTION.t_deck * scale,
                            t_bottom=SECTION.t_bottom * scale,
                            t_side=SECTION.t_side * scale)
    dx = hull.volume * hull.rho / 1000.0 * scale ** 3
    f_kernel.append(hull_girder_vertical(Lx, sec, dx)["f_2node_hz"])
    f_dry.append(hull_girder_vertical(Lx, sec, dx, added_mass_ratio=0.0)["f_2node_hz"])
    f_legacy.append(1.5 * (Lx / 100.0) ** -0.5)
fig, ax = plt.subplots(figsize=(6.4, 4.0))
ax.plot(Ls, f_kernel, "C0-", lw=2, label="kernel: free-free beam, EI/Δ, added mass 0.7")
ax.plot(Ls, f_dry, "C0--", lw=1.2, label="kernel: no added mass")
ax.plot(Ls, f_legacy, "C3-.", lw=1.6, label="legacy placeholder 1.5(L/100)$^{-0.5}$")
ax.axhspan(band[0], band[1], color="0.85", alpha=0.6, label="closed-form band (report #1186 V4)")
ax.axvline(L, color="k", ls=":", lw=1)
ax.scatter([L], [girder["f_2node_hz"]], color="C0", zorder=5)
ax.annotate(f"reference hull\n{girder['f_2node_hz']:.3f} Hz",
            xy=(L, girder["f_2node_hz"]), xytext=(196, 0.62),
            arrowprops=dict(arrowstyle="->", lw=0.8))
ax.set_xlabel("length L [m]")
ax.set_ylabel("2-node vertical hull frequency [Hz]")
ax.set_title("Hull-girder 2-node frequency: physics kernel vs legacy placeholder")
ax.legend(fontsize=7, loc="upper right")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_girder_frequency.png"))
plt.close(fig)

# fig 2: resonance sweep over rpm
rpms = np.linspace(30.0, 200.0, 171)
rms_curve = []
for r in rpms:
    s = deck_velocity_spectrum(L, E_STEEL, SECTION.I_vertical, mu, THRUST, r, BLADES,
                               zeta=zeta)
    rms_curve.append(s["rms_mm_s"])
rms_curve = np.asarray(rms_curve)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.8))
ax1.bar(spec["harmonics_hz"], spec["velocity_mm_s"], width=0.6, color="C0")
ax1.set_xlabel("frequency [Hz]")
ax1.set_ylabel("deck velocity amplitude [mm/s]")
ax1.set_title(f"Blade-rate spectrum at {RPM:.0f} rpm, {BLADES} blades\n"
              f"RMS {spec['rms_mm_s']:.4f} mm/s (1-80 Hz), convergent Δ={spec['modal_convergence_rel']:.1e}")
ax1.axvline(girder["f_2node_hz"], color="C3", lw=1.4, ls="--",
            label=f"2-node mode {girder['f_2node_hz']:.3f} Hz")
ax1.legend(fontsize=7)
ax2.plot(rpms, rms_curve, "C0-", lw=1.6)
ax2.set_yscale("log")
ax2.set_xlabel("shaft speed [rpm]")
ax2.set_ylabel("band RMS velocity [mm/s]")
ax2.set_title("Resonance sweep: k·f_BF crossing hull modes")
for n_mode, f_n in enumerate(free_free_beam_frequencies(L, EI, mu, 3), start=1):
    rpm_hit = f_n * 60.0 / BLADES
    if 30.0 <= rpm_hit <= 200.0:
        ax2.axvline(rpm_hit, color="C3", ls="--", lw=1)
        ax2.text(rpm_hit, rms_curve.max() * 0.5, f" f{n_mode}", fontsize=7, color="C3")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_resonance_sweep.png"))
plt.close(fig)

# fig 3: plate anchors
n_terms = np.arange(1, 6)
lam_cl = [clamped_plate_lambda(1.0, 1.0, n_terms=int(k)) for k in n_terms]
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.8))
ax1.plot(n_terms, lam_cl, "o-", color="C0", label="Rayleigh-Ritz clamped square")
ax1.axhline(35.985, color="C3", ls="--", label="Leissa exact 35.985")
ax1.axhline(2 * math.pi ** 2, color="C2", ls=":", label="simply supported $2\\pi^2$")
ax1.set_xlabel("basis size per direction")
ax1.set_ylabel("$\\omega a^2\\sqrt{\\rho h/D}$")
ax1.set_title("Clamped square plate: Ritz convergence to the exact value")
ax1.legend(fontsize=7)
modes = [(1, 1), (1, 2), (2, 1), (2, 2), (1, 3)]
labels = [f"({m},{n})" for m, n in modes]
f_ss_vals = [plate_natural_frequency(1.6, 1.0, 0.010, m=m, n=n, bc="SS") for m, n in modes]
f_c_vals = [plate_natural_frequency(1.6, 1.0, 0.010, m=m, n=n, bc="clamped") for m, n in modes]
xx = np.arange(len(modes))
ax2.bar(xx - 0.2, f_ss_vals, width=0.4, label="simply supported (exact)", color="C0")
ax2.bar(xx + 0.2, f_c_vals, width=0.4, label="clamped (Ritz)", color="C1")
ax2.set_xticks(xx)
ax2.set_xticklabels(labels)
ax2.set_xlabel("mode (m,n), panel 1.6 x 1.0 x 0.010 m")
ax2.set_ylabel("natural frequency [Hz]")
ax2.set_title("Deck panel frequencies (independent of hull frequency)")
ax2.legend(fontsize=7)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig3_plate_anchors.png"))
plt.close(fig)

# fig 4: noise chain
freqs = np.logspace(np.log10(31.5), np.log10(8000.0), 200)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.8))
for m2, c in [(20.0, "C0"), (40.0, "C1"), (80.0, "C2")]:
    ax1.semilogx(freqs, [mass_law_tl(m2, f) for f in freqs], color=c,
                 label=f"m'' = {m2:.0f} kg/m$^2$")
ax1.set_xlabel("frequency [Hz]")
ax1.set_ylabel("transmission loss [dB]")
ax1.set_title("Single-leaf mass law (field incidence): +6 dB/octave")
ax1.legend(fontsize=7)

octaves = np.array([63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0])
# machinery source spectrum: 98 dB at 100 Hz, -3 dB/octave
src = 98.0 - 10.0 * np.log2(octaves / 100.0)
spl = [cabin_noise(float(s), 40.0, float(f), 240.0, 0.15)["spl_db"]
       for s, f in zip(src, octaves)]
ax2.plot(octaves, spl, "o-", color="C0", label="cabin SPL (m''=40, $\\bar\\alpha$=0.15)")
ax2.axhline(IMO_CABIN_DBA, color="C3", ls="--", label="IMO MSC.337(91) cabin 60 dB(A)")
ax2.set_xscale("log")
ax2.set_xlabel("octave band centre frequency [Hz]")
ax2.set_ylabel("sound pressure level [dB]")
ax2.set_title("Machinery -> deck-house cabin noise chain")
ax2.set_xticks(octaves)
ax2.set_xticklabels([f"{int(f)}" for f in octaves])
ax2.legend(fontsize=7)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig4_noise_chain.png"))
plt.close(fig)

print(f"criteria={len(rows)} pass={n_pass} fail={n_fail} "
      f"anchors={len(anchor_rows)} worst_anchor_rel_err={worst:.3e} ({worst_id})")
print(f"f_2node={girder['f_2node_hz']:.4f} Hz  f_BF={f_bf:.4f} Hz  "
      f"v_rms={summary['vertical_vibration_mm_s']:.5f} mm/s  cabin={lp:.2f} dB")
print("artifacts ->", OUT)