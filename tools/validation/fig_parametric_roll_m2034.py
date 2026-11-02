"""m2034 作图与数据表：参数横摇内核（零手抄，全部数字由本脚本重算后落盘）。"""
from __future__ import annotations
import json, math, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
from ship_perf import parametric_roll as PR  # noqa: E402

OUT = os.path.join(ROOT, "artifacts", "parametric_roll")
os.makedirs(OUT, exist_ok=True)

BASE = dict(L=200.0, B0=32.0, T=9.0, kg=5.0, section_height=2.0)


def hull(kappa, taper=0.55):
    return PR.FlaredBarge(flare=kappa, taper=taper, **BASE)


# ---------------- 表 1：ε_cr(ζ) vs 解析 4ζ ----------------
zeta_list = [0.005, 0.01, 0.02, 0.05, 0.08]
thr = []
for z in zeta_list:
    ecr = PR.critical_eps(0.25, z, steps=800)
    thr.append({"zeta": z, "eps_cr_floquet": ecr, "eps_cr_analytic_4zeta": 4.0 * z,
                "rel_dev": abs(ecr - 4.0 * z) / (4.0 * z)})

# ---------------- 表 2：ΔGM/GM vs λ/L 与 κ ----------------
lam_ratio = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5]
lam_tab, sweep_store = [], {}
for k in (0.0, 0.45, 1.0):
    r = [PR.gm_phase_sweep(hull(k), 2.0, f * BASE["L"], n_phase=16, n=401)["dGM_ratio"]
         for f in lam_ratio]
    lam_tab.append({"flare": k, "lambda_over_L": lam_ratio, "dGM_ratio": r})
    sweep_store[k] = PR.gm_phase_sweep(hull(k), 2.0, BASE["L"], n_phase=36, n=801)

# ---------------- 表 3：端到端工况（T_roll 调到 δ≈1） ----------------
def t_roll_for_resonance(wavelength, speed, heading_deg):
    k = 2.0 * math.pi / wavelength
    w = math.sqrt(PR.G * k)
    we = PR.encounter_frequency(w, k, speed, heading_deg)
    return 2.0 * math.pi / (0.5 * we)  # ω_n = ω_e/2

cases = []
for name, kappa, H_wave, zeta in [
    ("A wall-sided zeta=0.02", 0.0, 5.0, 0.02),
    ("B large-flare zeta=0.02", 1.0, 5.0, 0.02),
    ("C large-flare zeta=0.05", 1.0, 5.0, 0.05),
]:
    tr = t_roll_for_resonance(BASE["L"], 8.0, 0.0)
    res = PR.assess_parametric_roll(hull(kappa), wave_height=H_wave, wavelength=BASE["L"],
                                    t_roll=tr, zeta=zeta, speed=8.0, heading_deg=0.0,
                                    n_phase=36, n=801, steps=800)
    cases.append({
        "name": name, "flare": kappa, "wave_height": H_wave, "zeta": zeta,
        "t_roll": tr, "dGM_ratio": res["geometry_layer"]["dGM_ratio"],
        "GM_mean": res["geometry_layer"]["GM_mean"],
        "eps": res["mathieu"]["eps"], "a_mathieu": res["mathieu"]["a_mathieu"],
        "eps_cr": res["eps_critical"], "rho_max": res["floquet"]["rho_max"],
        "growth_rate": res["floquet"]["growth_rate"],
        "verdict": res["verdict"],
    })

# ---------------- 图 1：GM(φ) 与 λ/L 峰化 ----------------
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
for k in (0.0, 0.45, 1.0):
    sw = sweep_store[k]
    ax[0].plot(np.degrees(sw["phases_rad"]), sw["GM"], marker="o", ms=3,
               label=f"κ(flare)={k:.2f}, ΔGM/GM={sw['dGM_ratio']*100:.2f}%")
ax[0].set_xlabel("wave phase φ (deg)"); ax[0].set_ylabel("GM in wave (m)")
ax[0].set_title("A) GM variation over one encounter cycle (λ=L, H=4 m)")
ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
for row in lam_tab:
    ax[1].plot(row["lambda_over_L"], [v * 100 for v in row["dGM_ratio"]], marker="s",
               label=f"κ={row['flare']:.2f}")
ax[1].axvline(1.0, ls="--", c="k", lw=.8, label="λ=L")
ax[1].set_xlabel("λ / L"); ax[1].set_ylabel("ΔGM/GM (%)")
ax[1].set_title("B) ΔGM peaks near λ≈L"); ax[1].legend(fontsize=8); ax[1].grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig1_gm_variation.png"), dpi=130)
plt.close(fig)

# ---------------- 图 2：Mathieu (a,ε) 稳定图 + 解析边界 ----------------
a_vals = np.linspace(0.05, 1.20, 60)
e_vals = np.linspace(0.0, 0.60, 61)
Z = np.empty((len(e_vals), len(a_vals)))
for i, e in enumerate(e_vals):
    for j, a in enumerate(a_vals):
        Z[i, j] = PR.monodromy(float(e), float(a), 0.02, steps=200)["growth_rate"]
fig, ax = plt.subplots(figsize=(7.2, 4.6))
im = ax.pcolormesh(a_vals, e_vals, Z, shading="auto", cmap="RdBu_r", vmin=-0.15, vmax=0.15)
ax.contour(a_vals, e_vals, Z, levels=[0.0], colors="k", linewidths=1.2)
ax.plot(a_vals, 4.0 * 0.02 * np.ones_like(a_vals), "k--", lw=1.0,
        label="analytic onset ε=4ζ (ζ=0.02)")
ax.scatter([0.25], [4.0 * 0.02], marker="*", s=160, c="lime", edgecolor="k",
           label="principal resonance a=1/4")
for c in cases:
    ax.scatter([c["a_mathieu"]], [c["eps"]], marker="X", s=90, label=c["name"])
ax.set_xlabel("Mathieu a = (ω_n/ω_e)²"); ax.set_ylabel("ε = ΔGM/(2·GM_mean)/GM")
ax.set_title("B) Floquet growth rate map (ζ=0.02); black = stability boundary")
ax.legend(fontsize=7, loc="upper right"); fig.colorbar(im, ax=ax, label="growth rate 1/s")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig2_mathieu_stability.png"), dpi=130)
plt.close(fig)

# ---------------- 图 3：时域包络（起振 vs 衰减） ----------------
fig, ax = plt.subplots(figsize=(7.2, 4.2))
for zeta, tag in [(0.005, "ζ=0.005 → driven"), (0.10, "ζ=0.10 → decay")]:
    eps = cases[1]["eps"]
    sim = PR.simulate_roll(eps, 0.25, zeta, theta0_deg=1.0, n_periods=50,
                           steps_per_period=480)
    ax.plot(np.arange(len(sim["peak_envelope"])) + 0.5, np.degrees(sim["peak_envelope"]),
            marker=".", ms=3, label=f"{tag} (fit ln-slope={sim['env_growth_rate']:+.4f})")
ax.set_yscale("log"); ax.set_xlabel("encounter periods"); ax.set_ylabel("roll peak envelope (deg)")
ax.set_title(f"C) 1-DOF roll at principal resonance (ε={cases[1]['eps']:.3f})")
ax.grid(alpha=.3, which="both"); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig3_time_domain.png"), dpi=130)
plt.close(fig)

# 独立 scipy 复核（写进表，供报告引用）
try:
    from scipy.integrate import solve_ivp
    def rho_scipy(eps, a, zeta):
        wn, damp = math.sqrt(a), 2 * zeta * math.sqrt(a)
        f = lambda t, y: [y[1], -damp * y[1] - a * (1 + eps * math.cos(t)) * y[0]]
        o = dict(rtol=1e-12, atol=1e-14)
        c1 = solve_ivp(f, [0, 2 * math.pi], [1, 0], **o).y[:, -1]
        c2 = solve_ivp(f, [0, 2 * math.pi], [0, 1], **o).y[:, -1]
        return float(np.abs(np.linalg.eigvals(np.array([[c1[0], c2[0]], [c1[1], c2[1]]]))).max())
    cross = []
    for eps, a, z in [(0.05, 0.25, 0.02), (0.30, 0.25, 0.02), (0.10, 1.0, 0.05)]:
        k_ = PR.monodromy(eps, a, z, steps=800)["rho_max"]
        r_ = rho_scipy(eps, a, z)
        cross.append({"eps": eps, "a": a, "zeta": z, "rho_kernel": k_, "rho_scipy": r_,
                      "rel_dev": abs(k_ - r_) / r_})
except Exception as exc:  # pragma: no cover
    cross = [{"error": str(exc)}]

payload = {"mission": 2034, "threshold_table": thr, "lambda_table": lam_tab,
           "cases": cases, "scipy_cross_check": cross,
           "figures": ["fig1_gm_variation.png", "fig2_mathieu_stability.png",
                       "fig3_time_domain.png"]}
with open(os.path.join(OUT, "scan.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=1)
print(json.dumps({"threshold": thr, "cases": cases, "scipy": cross,
                  "lambda_kappa1": lam_tab[-1]["dGM_ratio"]}, ensure_ascii=False, indent=1))
