#!/usr/bin/env python3
"""m1851：生成 2D 辐射阻尼的曲线/图/报告数据（数字全部程序化注入，零手抄）。"""
from __future__ import annotations

import json
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.ship_perf import radiation_2d as RAD  # noqa: E402
from src.ship_perf import seakeeping as SEA  # noqa: E402
from src.ship_perf.constants import G, RHO_SEA  # noqa: E402

OUT = os.path.join(ROOT, "artifacts", "radiation_2d")
os.makedirs(OUT, exist_ok=True)
A = 1.0
A_REF = RHO_SEA * math.pi * A * A / 2.0
KAS = [0.3, 0.4, 0.5, 0.7, 1.0, 1.4, 2.0, 2.8, 4.0, 5.0, 6.0]
GRID = dict(n_theta=200, n_per_wl=40)

rows = [RAD.solve_radiation_2d(ka, A, **GRID) for ka in KAS]

# ---- 网格收敛与判据数值（表用）
conv = []
for ka in (1.0, 4.0):
    c = RAD.solve_radiation_2d(ka, A, n_theta=200, n_per_wl=40)
    f = RAD.solve_radiation_2d(ka, A, n_theta=320, n_per_wl=60)
    conv.append({"ka": ka,
                 "a33_star_coarse": c["a33_dimensionless"], "a33_star_fine": f["a33_dimensionless"],
                 "b33_star_coarse": c["b33_dimensionless"], "b33_star_fine": f["b33_dimensionless"],
                 "rel_diff_a33": abs(f["a33"] / c["a33"] - 1.0),
                 "rel_diff_b33": abs(f["b33"] / c["b33"] - 1.0)})

# ---- 面板法交叉锚（全圆 ρπa²，取半 = 半浸圆 Dirichlet 极限）
n = 240
circle = [(A * math.sin(2 * math.pi * i / n), A * math.cos(2 * math.pi * i / n)) for i in range(n)]
panel_full = SEA.panel_added_mass(circle)["heave"]
panel_half = 0.5 * panel_full

# ---- RAO：物理 B33 vs 经验阻尼比
length = 20.0
hull = SEA.Hull(length=length, beam=2.0 * A, draft=A, cb=0.85)
a33_hull = RAD.solve_radiation_2d(1.0, A, **GRID)["a33"] * length
mass = hull.volume * RHO_SEA
periods = [1.0 + 0.5 * i for i in range(17)]          # 1..9 s


def b33_of_omega(w):
    return RAD.solve_radiation_2d(w * w / G * A, A, **GRID)["b33"] * length


rao_phys = RAD.heave_rao_with_radiation_damping(
    hull.waterplane_area, hull.draft, mass, a33_hull, periods, b33_of_omega)
rao_guess = SEA.heave_rao(hull, periods, mass, a33=a33_hull, damping_ratio=0.15)

peak_phys = max(r["rao_heave"] for r in rao_phys)
peak_guess = max(r["rao_heave"] for r in rao_guess)
t_peak_phys = max(rao_phys, key=lambda r: r["rao_heave"])["period"]
t_peak_guess = max(rao_guess, key=lambda r: r["rao_heave"])["period"]

# ω→0 极限验证（长周期端）
w_small = 0.25
ka_small = w_small * w_small / G * A
b_small = RAD.solve_radiation_2d(ka_small, A, **GRID)["b33"] * length
rao_small = RAD.heave_rao_with_radiation_damping(
    hull.waterplane_area, hull.draft, mass, a33_hull, [2.0 * math.pi / w_small],
    lambda w: b_small)[0]["rao_heave"]

data = {
    "grid": GRID, "ka": KAS,
    "a33_star": [r["a33_dimensionless"] for r in rows],
    "b33_star": [r["b33_dimensionless"] for r in rows],
    "flux_ratio": [r["flux_ratio"] for r in rows],
    "a33": [r["a33"] for r in rows],
    "b33": [r["b33"] for r in rows],
    "a_ref": A_REF,
    "convergence": conv,
    "panel_full_circle": panel_full,
    "panel_half_circle": panel_half,
    "panel_anchor_error": abs(panel_full / (RHO_SEA * math.pi) - 1.0),
    "cross_check_a33_ka4": {
        "a33": rows[KAS.index(4.0)]["a33"],
        "half_panel": panel_half,
        "rel_diff": abs(rows[KAS.index(4.0)]["a33"] / panel_half - 1.0),
    },
    "short_wave_error_ka4": abs(rows[KAS.index(4.0)]["a33_dimensionless"] - 1.0),
    "rao": {"periods": periods,
            "phys": [r["rao_heave"] for r in rao_phys],
            "guess015": [r["rao_heave"] for r in rao_guess],
            "b33_hull": [r["b33"] for r in rao_phys],
            "peak_phys": peak_phys, "t_peak_phys": t_peak_phys,
            "peak_guess": peak_guess, "t_peak_guess": t_peak_guess,
            "peak_rel_diff": abs(peak_phys / peak_guess - 1.0),
            "rao_long_wave": rao_small,
            "long_wave_error": abs(rao_small - 1.0)},
    "hull": {"length": length, "beam": hull.beam, "draft": hull.draft, "cb": hull.cb,
             "mass": mass, "a33_hull": a33_hull},
    "evidence": RAD.EVIDENCE,
}
with open(os.path.join(OUT, "curve.json"), "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=1, ensure_ascii=False)

# ---- 图 1：A33*/B33* 与极限
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
ax[0].plot(KAS, data["a33_star"], "o-", label="A33 / (ρπa²/2)")
ax[0].axhline(1.0, color="k", ls="--", lw=1, label="精确短波极限 1")
ax[0].set_xlabel("ka"); ax[0].set_ylabel("A33*"); ax[0].set_title("Added mass (heave, semicircle)")
ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)
ax[1].plot(KAS, data["b33_star"], "s-", color="C3", label="B33 / (ρωπa²/2)")
ax[1].set_xlabel("ka"); ax[1].set_ylabel("B33*"); ax[1].set_yscale("log")
ax[1].set_title("Radiation damping (heave, semicircle)")
ax[1].grid(alpha=.3, which="both"); ax[1].legend(fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig_a33_b33.png"), dpi=150)

# ---- 图 2：RAO 物理阻尼 vs 经验阻尼比；能流校验
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
ax[0].plot(periods, data["rao"]["phys"], "o-", label="B33 from 2D radiation solve (REAL)")
ax[0].plot(periods, data["rao"]["guess015"], "s--", label="guessed damping ratio ζ=0.15 (m1809)")
ax[0].axhline(1.0, color="k", lw=.8, ls=":", label="ω→0 exact limit RAO=1")
ax[0].set_xlabel("wave period T [s]"); ax[0].set_ylabel("heave RAO")
ax[0].set_title("Heave RAO: physical damping vs guessed ratio")
ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)
ax[1].plot(KAS, data["flux_ratio"], "o-")
ax[1].axhline(1.0, color="k", ls="--", lw=1)
ax[1].set_xlabel("ka"); ax[1].set_ylabel("P_control-surface / (½B33V²)")
ax[1].set_ylim(0.8, 1.2)
ax[1].set_title("Energy balance (exact identity, <3%)")
ax[1].grid(alpha=.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig_rao_energy.png"), dpi=150)

print(json.dumps({k: data[k] for k in ("short_wave_error_ka4", "cross_check_a33_ka4",
                                       "panel_anchor_error", "convergence")},
                 indent=1, ensure_ascii=False))
print("RAO:", json.dumps({k: data["rao"][k] for k in
                          ("peak_phys", "t_peak_phys", "peak_guess", "t_peak_guess",
                           "peak_rel_diff", "rao_long_wave", "long_wave_error")}, indent=1))
print("flux_ratio range:", min(data["flux_ratio"]), max(data["flux_ratio"]))
print("artifacts ->", OUT)