import sys, math, json
sys.path.insert(0, 'backend')
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from services.seakeeping import (SeakeepingModel, spectrum, uniform_grid, response_statistics,
                                 _integrate, transfer, wave_number)

HULL = dict(L=150.0, B=22.0, T=8.0, Cb=0.68, Cwp=0.78, displacement_t=18400.0, KG=8.5, GM_T=1.5)
m = SeakeepingModel(**HULL)
out = {}

# ---- fig1: long-wave band-limited spectrum, RAOs, sigmas ----
ome = uniform_grid(0.05, 0.12, 400)
base = spectrum("pm", Hs=2.0, Tp=25.0, omega=ome)
S = [s if 0.05 <= w <= 0.12 else 0.0 for s, w in zip(base["S"], ome)]
spec = dict(base); spec["S"] = S
curve = m.rao_curve(ome, heading_deg=180.0, speed=0.0)
stats = response_statistics(m, spec, heading_deg=180.0, speed=0.0)
sigma_zeta = math.sqrt(_integrate(S, ome))
slope_var = _integrate([wave_number(w) ** 2 * s for s, w in zip(S, ome)], ome)
fig, ax = plt.subplots(1, 3, figsize=(15, 4))
ax[0].plot(ome, S, lw=1.5, label="band-limited PM, Hs=2 m, Tp=25 s")
ax[0].set_xlabel("omega [rad/s]"); ax[0].set_ylabel("S [m^2 s]"); ax[0].set_title("Long-wave band (lambda = 680-3900 m >> L=150 m)"); ax[0].legend()
ax[1].plot(ome, curve["heave_rao_m_per_m"], lw=1.5, label="heave RAO [m/m]")
ax[1].plot(ome, curve["pitch_rao_rad_per_rad"], lw=1.5, label="pitch RAO [rad/rad]")
ax[1].axhline(1.0, ls="--", c="k", lw=1, label="closed-form limit = 1")
ax[1].set_xlabel("omega [rad/s]"); ax[1].set_ylabel("RAO [-]"); ax[1].set_title("Long-wave RAO limits (coefficient independent)"); ax[1].legend(fontsize=8)
labels = ["sigma_heave [m]\nvs sigma_zeta", "significant_heave [m]\nvs 2 sigma_zeta",
          "sigma_pitch [deg]\nvs wave-slope sigma", "sigma_pitch (old bug)\n[deg]"]
vals = [stats["sigma_heave"], stats["significant_heave"], stats["sigma_pitch"], math.degrees(sigma_zeta)]
refs = [sigma_zeta, 2 * sigma_zeta, math.degrees(math.sqrt(slope_var)), None]
b = ax[2].bar(range(4), vals, color=["#1f77b4", "#1f77b4", "#2ca02c", "#d62728"])
for i, r in enumerate(refs):
    if r is not None:
        ax[2].plot([i - 0.4, i + 0.4], [r, r], "k--", lw=1.2)
ax[2].set_yscale("log"); ax[2].set_xticks(range(4)); ax[2].set_xticklabels(labels, fontsize=7)
ax[2].set_ylabel("value (log)"); ax[2].set_title("Spectral response vs closed-form (dashed)")
fig.tight_layout(); fig.savefig("docs/audits/figures/m1639_longwave_anchors.png", dpi=130); plt.close(fig)
out["fig1"] = {k: stats[k] for k in ("sigma_heave", "significant_heave", "sigma_pitch", "sigma_roll")}
out["sigma_zeta"] = sigma_zeta
out["slope_sigma_deg"] = math.degrees(math.sqrt(slope_var))

# ---- fig2: sigma_pitch vs Tp for head seas, fixed vs slope-normalised (defect) ----
tps = [4, 5, 6, 7, 8, 10, 12, 14]
fixed, buggy, heave_ref = [], [], []
ome2 = uniform_grid(0.02, 6.0, 1601)
curve2 = m.rao_curve(ome2, heading_deg=180.0, speed=0.0)
for tp in tps:
    sp = spectrum("jonswap", Hs=3.0, Tp=float(tp), omega=ome2)
    fixed.append(response_statistics(m, sp, heading_deg=180.0)["sigma_pitch"])
    buggy.append(math.degrees(math.sqrt(_integrate(
        [r * r * s for r, s in zip(curve2["pitch_rao_rad_per_rad"], sp["S"])], ome2))))
    heave_ref.append(response_statistics(m, sp, heading_deg=180.0)["sigma_heave"])
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
ax[0].plot(tps, fixed, "o-", label="fixed: per-unit-amplitude RAO [deg/m]")
ax[0].plot(tps, buggy, "s-", label="defect: slope-normalised RAO [rad/rad]")
ax[0].set_yscale("log"); ax[0].set_xlabel("Tp [s]"); ax[0].set_ylabel("sigma_pitch [deg]")
ax[0].set_title("Pitch spread, head seas, Hs=3 m"); ax[0].legend(fontsize=8)
ax[1].plot(tps, [b / f for b, f in zip(buggy, fixed)], "o-", c="#d62728")
ax[1].set_yscale("log"); ax[1].set_xlabel("Tp [s]"); ax[1].set_ylabel("defect / fixed [-]")
ax[1].set_title("Inflation factor (approaches 1/k = 2pi g / (w^2) )")
fig.tight_layout(); fig.savefig("docs/audits/figures/m1639_pitch_defect.png", dpi=130); plt.close(fig)
out["tp"] = tps; out["pitch_fixed"] = fixed; out["pitch_buggy"] = buggy; out["ratio"] = [b / f for b, f in zip(buggy, fixed)]

# ---- fig3: white-noise quadrature convergence + heave/pitch curves ----
nav = []
for n in (501, 1001, 2001, 4001, 8001, 16001):
    r = uniform_grid(1e-4, 400.0, n)
    nav.append((n, _integrate([transfer(x, 0.1) ** 2 for x in r], r)))
closed = math.pi / (4 * 0.1)
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
ax[0].semilogx([n for n, _ in nav], [abs(v / closed - 1) for _, v in nav], "o-")
ax[0].set_xlabel("trapezoid points over [1e-4, 400]"); ax[0].set_ylabel("|rel. error|")
ax[0].set_title("int_0^inf |H|^2 dr = pi/(4 zeta), zeta=0.1"); ax[0].set_yscale("log")
sp = spectrum("jonswap", Hs=3.0, Tp=8.0, omega=ome2)
ax[1].plot(ome2, sp["S"], lw=1.2, label="JONSWAP S(w), Hs=3 m Tp=8 s")
ax[1].set_xlim(0, 2.5); ax[1].set_xlabel("omega [rad/s]"); ax[1].set_ylabel("S [m^2 s]")
ax2 = ax[1].twinx(); ax2.plot(ome2, curve2["pitch_rao_deg_per_m"], c="#2ca02c", lw=1.2, label="pitch RAO [deg/m]")
ax2.set_ylabel("pitch RAO [deg/m]", color="#2ca02c")
ax[1].set_title("Default sea state: spectrum x pitch RAO"); ax[1].legend(fontsize=7, loc="upper right")
fig.tight_layout(); fig.savefig("docs/audits/figures/m1639_quadrature.png", dpi=130); plt.close(fig)
out["quad"] = [[n, v, abs(v / closed - 1)] for n, v in nav]
print(json.dumps(out, indent=1))
