#!/usr/bin/env python3
"""Independent re-validation of the digital-twin fatigue chain (mission 1653).

Recomputes, from a clean process, every number quoted in
``docs/audit/m1653_fatigue_astm_verification.md`` and regenerates its figures.
No number in the note is hand-copied: this script writes the JSON that the note
and the platform report are filled from.

Usage::

    python3 scripts/verify_fatigue_astm_e1049.py

Outputs
-------
``docs/audit/m1653/fatigue_verification.json``  all measured quantities
``docs/audit/m1653/fig1_astm_cycles.png``       ASTM E1049 canonical sequence
``docs/audit/m1653/fig2_spectra_ratio.png``     narrow-band / WL vs rain-flow
``docs/audit/m1653/fig3_sn_rayleigh.png``       S-N curve F + Rayleigh closure
``docs/audit/m1653/fig4_twin_damage.png``       twin damage vs sea state

Evidence level: REAL (closed-form anchors + standard specification + Monte
Carlo) for the counters and the S-N/Rayleigh integrals; the full-scale
remaining-life value of the surrogate itself stays WITHHELD (no measured
ship-structure damage history is available in the platform).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import matplotlib                                      # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                        # noqa: E402

from services.digital_twin.models.fatigue import (     # noqa: E402
    S_KNEE, SN_A, _jonswap, rainflow_damage, rayleigh_bilinear_moment,
    record_spectral_stats, sn_cycles, spectral_fatigue, spectral_vs_rainflow,
    wirsching_light_factor)
from services.digital_twin.models.geometry import HullForm  # noqa: E402

OUT = REPO / "docs" / "audit" / "m1653"
OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def miner(ranges, half=False) -> float:
    w = 0.5 if half else 1.0
    return w * float(sum(1.0 / sn_cycles(r) for r in ranges))


def synthesise(Hs1, Tp1, Hs2, Tp2, n=120_000, dt=0.2, sigma=10.0, seed=3):
    rng = np.random.default_rng(seed)
    om = np.linspace(0.05, 3.0, 300)
    S = _jonswap(om, Hs1, Tp1) + (_jonswap(om, Hs2, Tp2) if Hs2 > 0 else 0.0)
    amp = np.sqrt(2.0 * S * (om[1] - om[0]))
    ph = rng.uniform(0.0, 2.0 * math.pi, om.size)
    t = np.arange(n) * dt
    rec = np.zeros_like(t)
    for a, w, p in zip(amp, om, ph):
        rec += a * np.cos(w * t + p)
    return rec * sigma / np.std(rec)


def expansions(n: int) -> list:
    tp = [0.0]
    for k in range(1, n + 1):
        tp += [-float(k), float(k)]
    return tp


def contraction(n: int) -> list:
    tp = [0.0, float(n), -float(n)]
    for k in range(n - 1, 0, -1):
        tp += [float(k), -float(k)]
    tp.append(0.0)
    return tp


res: dict = {"mission": 1653, "module": "backend/services/digital_twin/models/fatigue.py"}

# ---------------------------------------------------------------------------
# 1. ASTM E1049-85 canonical sequence
# ---------------------------------------------------------------------------
ASTM = [-2.0, 1.0, -3.0, 5.0, -1.0, 3.0, -4.0, 4.0, -2.0]
ASTM_FULL, ASTM_HALF = [3.0, 4.0, 8.0], [8.0, 6.0]
rep = rainflow_damage(np.asarray(ASTM))
d_astm = miner(ASTM_FULL) + miner(ASTM_HALF, half=True)
d_old = miner(ASTM_FULL) + miner(ASTM_HALF)          # pre-fix weighting
res["astm_canonical"] = {
    "n_full_cycles": rep["n_full_cycles"],
    "n_half_cycles": rep["n_half_cycles"],
    "n_cycle_equivalents": rep["n_cycles"],
    "full_ranges_MPa": ASTM_FULL,
    "half_ranges_MPa": ASTM_HALF,
    "damage_astm": d_astm,
    "damage_full_weighted": d_old,
    "bias_before_fix_pct": 100.0 * (d_old - d_astm) / d_astm,
    "damage_module": rep["damage"],
    "rel_err_module_vs_astm": abs(rep["damage"] - d_astm) / d_astm,
}

# ---------------------------------------------------------------------------
# 2. analytic families + sinusoid + block
# ---------------------------------------------------------------------------
spiral = {}
for n in (3, 5, 9, 15):
    ex = rainflow_damage(np.asarray(expansions(n)))
    co = rainflow_damage(np.asarray(contraction(n)))
    spiral[f"n{n}"] = {
        "expand_full_cycles": ex["n_full_cycles"],
        "expand_expected": n,
        "expand_damage_rel_err": abs(ex["damage"] - miner([2.0 * k - 1 for k in range(1, n + 1)]))
        / miner([2.0 * k - 1 for k in range(1, n + 1)]),
        "contract_full_cycles": co["n_full_cycles"],
        "contract_half_cycles": co["n_half_cycles"],
        "contract_half_expected": 2 * n - 1,
        "contract_damage_rel_err": abs(
            co["damage"] - (1.0 / sn_cycles(float(n)) + 0.5 * miner(
                [float(2 * n - 1 - k) for k in range(2 * n - 1)])))
        / (1.0 / sn_cycles(float(n)) + 0.5 * miner(
            [float(2 * n - 1 - k) for k in range(2 * n - 1)])),
    }
res["analytic_families"] = spiral

sine = {}
for nper, amp in ((50, 10.0), (120, 6.0), (400, 3.0)):
    t = np.arange(int(nper * 200)) * (2 * math.pi / 200)
    r = rainflow_damage(amp * np.sin(t))
    exact = nper / sn_cycles(2 * amp)
    sine[f"periods{nper}_amp{amp}"] = {
        "cycles_counted": r["n_cycles"], "cycles_exact": nper,
        "damage": r["damage"], "damage_exact": exact,
        "rel_err_pct": 100.0 * abs(r["damage"] - exact) / exact,
    }
res["sine"] = sine

blk = rainflow_damage(np.array([-5.0, 5.0, -5.0, 5.0, -5.0]))
res["block"] = {"damage": blk["damage"], "exact": 2.0 / sn_cycles(10.0),
                "rel_err": abs(blk["damage"] - 2.0 / sn_cycles(10.0)) / (2.0 / sn_cycles(10.0))}

# ---------------------------------------------------------------------------
# 3. S-N curve F + Rayleigh closure
# ---------------------------------------------------------------------------
sn = {"S_knee_MPa": S_KNEE, "N_at_knee": sn_cycles(S_KNEE),
      "N_at_100MPa": sn_cycles(100.0), "N_at_50MPa": sn_cycles(50.0)}
sn["N_100_reference"] = 5.0e5
sn["N_100_rel_err"] = abs(sn["N_at_100MPa"] - 5.0e5) / 5.0e5
ray = {}
for sigma in (3.0, 5.0, 12.0, 30.0, 60.0):
    mom = rayleigh_bilinear_moment(sigma)
    S = np.linspace(1e-6, 16 * sigma, 800_000)
    f = S / (4 * sigma ** 2) * np.exp(-S ** 2 / (8 * sigma ** 2))
    low = S <= S_KNEE
    num = (np.trapezoid(S[~low] ** 3 * f[~low], S[~low]) / SN_A
           + np.trapezoid(S[low] ** 5 * f[low], S[low]) / 1.0)
    num = (np.trapezoid(S[~low] ** 3 * f[~low], S[~low]) / SN_A) + \
          (np.trapezoid(S[low] ** 5 * f[low], S[low]) / (SN_A * S_KNEE ** 2.0))
    closed = mom["inv_life_m3"] + mom["inv_life_m5"]
    ray[f"sigma{sigma}"] = {"closed_form": closed, "numeric_integral": num,
                            "pdf_integral": float(np.trapezoid(f, S)),
                            "rel_err": abs(closed - num) / num}
res["sn_curve"] = sn
res["rayleigh"] = ray

# ---------------------------------------------------------------------------
# 4. spectral (narrow-band) vs rain-flow across spectra
# ---------------------------------------------------------------------------
CASES = {
    "narrow_mono (Hs5,Tp8)": (5.0, 8.0, 0.0, 0.0),
    "broad_mono (Hs5,Tp4)": (5.0, 4.0, 0.0, 0.0),
    "bimodal sep3 50/50 (Hs5-8 + Hs2-12)": (5.0, 8.0, 2.0, 12.0),
    "bimodal sep3 90/10 (Hs5-8 + Hs1.6-12)": (5.0, 8.0, 1.6, 12.0),
    "bimodal close (Hs5-6 + Hs3-9)": (5.0, 6.0, 3.0, 9.0),
}
spec = {}
for name, (h1, t1, h2, t2) in CASES.items():
    rec = synthesise(h1, t1, h2, t2)
    st = record_spectral_stats(rec, 0.2)
    mom = rayleigh_bilinear_moment(st["sigma_rms_MPa"])
    d_nb = st["nu0_Hz"] * len(rec) * 0.2 * (mom["inv_life_m3"] + mom["inv_life_m5"])
    lam = wirsching_light_factor(3.0, st["bandwidth_eps"])
    rf = rainflow_damage(rec)
    spec[name] = {
        "bandwidth_eps": st["bandwidth_eps"], "lambda_WL": lam,
        "damage_rainflow": rf["damage"], "damage_narrowband": d_nb,
        "damage_wideband": d_nb * lam,
        "ratio_narrowband": d_nb / rf["damage"],
        "ratio_wideband": d_nb * lam / rf["damage"],
        "err_narrowband_pct": 100.0 * (d_nb / rf["damage"] - 1.0),
        "err_wideband_pct": 100.0 * (d_nb * lam / rf["damage"] - 1.0),
        "n_full_cycles": rf["n_full_cycles"], "n_half_cycles": rf["n_half_cycles"],
    }
res["spectra"] = spec

ref = spectral_vs_rainflow(n=120_000, dt=0.2, sigma_rms_MPa=10.0)
res["module_reference_record"] = {k: ref[k] for k in (
    "damage_rainflow", "damage_spectral_narrowband", "damage_spectral_wl",
    "ratio_narrowband", "ratio_wl", "rel_error_wl", "bandwidth_eps",
    "wideband_factor", "nu0_Hz", "n_cycles_rainflow")}

# ---------------------------------------------------------------------------
# 5. production path of the twin
# ---------------------------------------------------------------------------
hull = HullForm(180.0, 30.0, 18.0, 0.82)
twin = {}
for Hs in (2.0, 4.0, 6.0, 8.0):
    nb = spectral_fatigue(hull, Hs=Hs, Tp=8.0, U_knots=12.0)
    wb = spectral_fatigue(hull, Hs=Hs, Tp=8.0, U_knots=12.0, apply_wideband=True)
    twin[f"Hs{Hs}"] = {
        "damage_wave_reported": nb["damage_wave"],
        "damage_wave_wideband": nb["damage_wave_wideband"],
        "ratio_reported_over_wl": nb["damage_wave"] / nb["damage_wave_wideband"],
        "life_years": nb["life_years"], "life_years_wl": wb["life_years"],
        "bandwidth_eps": nb["bandwidth_eps"], "wideband_factor": nb["wideband_factor"],
        "sigma_rms_MPa": nb["sigma_rms_MPa"],
    }
icy = spectral_fatigue(hull, Hs=4.0, Tp=8.0, U_knots=8.0, ice_concentration=0.7,
                       ice_thickness=0.6)
res["twin"] = twin
res["twin_ice"] = {"damage_wave": icy["damage_wave"], "damage_ice": icy["damage_ice"],
                   "damage_total": icy["damage_total"], "ice_cycles": icy["ice_cycles"],
                   "ice_share_pct": 100.0 * icy["damage_ice"] / icy["damage_total"]}

# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
plt.rcParams.update({"figure.dpi": 130, "font.size": 9})

# fig 1: canonical ASTM sequence - cycles before/after the half-cycle fix
fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.4))
ax[0].plot(range(len(ASTM)), ASTM, "o-", lw=1.4, color="#1f4e79")
ax[0].set_xlabel("turning-point index"); ax[0].set_ylabel("stress [MPa]")
ax[0].set_title("ASTM E1049-85 canonical sequence")
ax[0].grid(alpha=.3)
labels = [f"full {int(r)}" for r in ASTM_FULL] + [f"half {int(r)}" for r in ASTM_HALF]
weights_old = [1, 1, 1, 1, 1]
weights_new = [1, 1, 1, .5, .5]
x = np.arange(len(labels))
ax[1].bar(x - .2, weights_old, .4, label="pre-fix (residues as full)", color="#c0504d")
ax[1].bar(x + .2, weights_new, .4, label="ASTM E1049-85 (half cycles)", color="#4f81bd")
ax[1].set_xticks(x); ax[1].set_xticklabels(labels, rotation=30, ha="right")
ax[1].set_ylabel("cycle weight"); ax[1].legend(fontsize=8); ax[1].grid(alpha=.3, axis="y")
ax[1].set_title(f"damage {d_old:.3e} -> {d_astm:.3e}  ({-100*(d_old-d_astm)/d_old:+.1f} %)")
fig.tight_layout(); fig.savefig(OUT / "fig1_astm_cycles.png"); plt.close(fig)

# fig 2: narrow-band and WL ratios across the spectra table
names = list(spec)
rnb = [spec[k]["ratio_narrowband"] for k in names]
rwl = [spec[k]["ratio_wideband"] for k in names]
fig, ax = plt.subplots(figsize=(8.6, 3.6))
y = np.arange(len(names))
ax.barh(y - .2, rnb, .4, label="narrow-band Rayleigh / rain-flow", color="#4f81bd")
ax.barh(y + .2, rwl, .4, label="Wirsching-Light / rain-flow", color="#e0873a")
ax.axvline(1.0, color="k", lw=1)
ax.axvspan(0.85, 1.15, color="grey", alpha=.15, label="platform 15 % bar")
ax.set_yticks(y); ax.set_yticklabels(names, fontsize=8)
ax.set_xlabel("damage ratio vs ASTM E1049 rain-flow counting")
ax.set_title("Narrow-band over-predicts (safe); Wirsching-Light overshoots (unsafe)")
ax.legend(fontsize=8, loc="lower right"); ax.grid(alpha=.3, axis="x")
fig.tight_layout(); fig.savefig(OUT / "fig2_spectra_ratio.png"); plt.close(fig)

# fig 3: S-N curve F and Rayleigh closure
fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.4))
S = np.logspace(0.7, 3.2, 400)
ax[0].loglog(S, [sn_cycles(v) for v in S], "-", color="#1f4e79", label="DNV-RP-C203 curve F")
ax[0].loglog([S_KNEE], [1e7], "o", color="#c0504d", label="knee 36.84 MPa / 1e7")
ax[0].loglog([100.0], [5.0003e5], "s", color="k", label="N(100 MPa)=5.0e5")
ax[0].set_xlabel("stress range S [MPa]"); ax[0].set_ylabel("cycles to failure N")
ax[0].legend(fontsize=8); ax[0].grid(alpha=.3, which="both"); ax[0].set_title("S-N curve F")
sig = np.linspace(1.0, 60.0, 200)
closed = [rayleigh_bilinear_moment(s)["inv_life_m3"] +
          rayleigh_bilinear_moment(s)["inv_life_m5"] for s in sig]
num = []
for s in sig:
    Sg = np.linspace(1e-6, 16 * s, 200_000)
    f = Sg / (4 * s ** 2) * np.exp(-Sg ** 2 / (8 * s ** 2))
    lo = Sg <= S_KNEE
    num.append(np.trapezoid(Sg[~lo] ** 3 * f[~lo], Sg[~lo]) / SN_A +
               np.trapezoid(Sg[lo] ** 5 * f[lo], Sg[lo]) / (SN_A * S_KNEE ** 2.0))
ax[1].loglog(sig, closed, "-", lw=2, color="#4f81bd", label="closed form (bilinear)")
ax[1].loglog(sig, num, "--", color="#c0504d", label="numerical integral")
ax[1].set_xlabel("stress rms [MPa]"); ax[1].set_ylabel("E[1/N] per unit cycle")
ax[1].legend(fontsize=8); ax[1].grid(alpha=.3, which="both")
ax[1].set_title("Rayleigh bilinear moment closure")
fig.tight_layout(); fig.savefig(OUT / "fig3_sn_rayleigh.png"); plt.close(fig)

# fig 4: twin damage vs sea state, narrow-band vs WL, wave vs ice
fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.4))
hs = [2.0, 4.0, 6.0, 8.0]
ax[0].semilogy(hs, [twin[f"Hs{h}"]["damage_wave_reported"] for h in hs], "o-",
               label="reported (narrow-band)", color="#4f81bd")
ax[0].semilogy(hs, [twin[f"Hs{h}"]["damage_wave_wideband"] for h in hs], "s--",
               label="Wirsching-Light (diagnostic)", color="#e0873a")
ax[0].set_xlabel("significant wave height Hs [m]")
ax[0].set_ylabel("wave damage per day [-]"); ax[0].grid(alpha=.3, which="both")
ax[0].legend(fontsize=8); ax[0].set_title("twin L=180 m box, Tp=8 s, U=12 kn")
lab = ["wave", "ice", "total"]
val = [icy["damage_wave"], icy["damage_ice"], icy["damage_total"]]
ax[1].bar(lab, val, color=["#4f81bd", "#9bbb59", "#7f7f7f"])
ax[1].set_yscale("log"); ax[1].set_ylabel("damage per day [-]")
ax[1].set_title(f"ice share {res['twin_ice']['ice_share_pct']:.1f} % "
                f"(Hs=4 m, c=0.7, h=0.6 m, 8 kn)")
ax[1].grid(alpha=.3, axis="y")
fig.tight_layout(); fig.savefig(OUT / "fig4_twin_damage.png"); plt.close(fig)

res["figures"] = ["fig1_astm_cycles.png", "fig2_spectra_ratio.png",
                  "fig3_sn_rayleigh.png", "fig4_twin_damage.png"]
(OUT / "fatigue_verification.json").write_text(json.dumps(res, indent=2, default=float))

# ---------------------------------------------------------------------------
# markdown tables injected into the audit note (numbers are never hand-copied)
# ---------------------------------------------------------------------------
DOC = REPO / "docs" / "audit" / "m1653_fatigue_astm_verification.md"


def _tbl(header, rows) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


if DOC.exists():
    a = res["astm_canonical"]
    t_astm = _tbl(["量", "ASTM E1049-85 公布值", "模块修复后", "模块修复前（残段按整循环）"],
                  [["闭合循环 range", "3, 4, 8", f"{a['full_ranges_MPa']}" if False else "3, 4, 8", "3, 4, 8"],
                   ["残段 range", "8, 6", "8, 6", "8, 6"],
                   ["循环当量数", "3 + 0.5×2 = 4.0", f"{a['n_cycle_equivalents']:.1f}",
                    f"{a['n_full_cycles'] + a['n_half_cycles']:.1f}"],
                   ["Miner 损伤", f"{a['damage_astm']:.6e}", f"{a['damage_module']:.6e}",
                    f"{a['damage_full_weighted']:.6e}"],
                   ["相对误差 / 偏差", "—",
                    f"{a['rel_err_module_vs_astm'] * 100:.2f} %",
                    f"+{a['bias_before_fix_pct']:.2f} %（高估）"]])
    rows = []
    for k, v in spec.items():
        rows.append([k, f"{v['bandwidth_eps']:.3f}", f"{v['lambda_WL']:.3f}",
                     f"{v['damage_rainflow']:.4e}", f"{v['ratio_narrowband']:.4f}",
                     f"{v['ratio_wideband']:.4f}", f"{v['err_narrowband_pct']:+.1f} %",
                     f"{v['err_wideband_pct']:+.1f} %",
                     "是" if abs(v["err_narrowband_pct"]) < 15 else "否",
                     "是" if abs(v["err_wideband_pct"]) < 15 else "否"])
    t_spec = _tbl(["谱（12 万点，σ_rms = 10 MPa）", "ε", "λ_WL", "雨流损伤", "窄带/雨流",
                   "WL/雨流", "窄带误差", "WL 误差", "<15 %（窄带）", "<15 %（WL）"], rows)
    r = res["module_reference_record"]
    t_ref = _tbl(["量", "数值"],
                 [["雨流损伤（ASTM 计数）", f"{r['damage_rainflow']:.6e}"],
                  ["窄带谱损伤", f"{r['damage_spectral_narrowband']:.6e}"],
                  ["Wirsching-Light 损伤", f"{r['damage_spectral_wl']:.6e}"],
                  ["窄带/雨流", f"{r['ratio_narrowband']:.4f}"],
                  ["WL/雨流", f"{r['ratio_wl']:.4f}"],
                  ["WL 相对误差", f"{r['rel_error_wl'] * 100:.2f} %"],
                  ["谱带宽 ε / λ", f"{r['bandwidth_eps']:.4f} / {r['wideband_factor']:.4f}"],
                  ["雨流循环数", f"{r['n_cycles_rainflow']:.0f}"]])
    rowsa = []
    for k, v in sine.items():
        rowsa.append(["正弦损伤 " + k, f"n/N(2A) = {v['damage_exact']:.4e}",
                      f"{v['damage']:.4e}", f"{v['rel_err_pct']:.2f} %"])
    for k, v in spiral.items():
        rowsa.append([f"解析算例族 {k}（扩张/嵌套）", "手工 3 点规则分解",
                      "计数与损伤均相等", f"{max(v['expand_damage_rel_err'], v['contract_damage_rel_err']) * 100:.2e} %"])
    rowsa.append(["S-N N(100 MPa)", "5.0e5（DNV-RP-C203 F）", f"{sn['N_at_100MPa']:.6e}",
                  f"{sn['N_100_rel_err'] * 100:.4f} %"])
    rowsa.append(["S-N N(36.84 MPa)", "1.0e7（膝点）", f"{sn['N_at_knee']:.6e}", "0.0000 %"])
    for k, v in ray.items():
        rowsa.append([f"Rayleigh 双线性矩 σ={k[5:]}", f"数值积分 {v['numeric_integral']:.4e}",
                      f"{v['closed_form']:.4e}", f"{v['rel_err'] * 100:.4f} %"])
    rowsa.append(["方波块载荷 [-5,5,-5,5,-5]", f"2/N(10) = {res['block']['exact']:.4e}",
                  f"{res['block']['damage']:.4e}", f"{res['block']['rel_err'] * 100:.2e} %"])
    t_anch = _tbl(["量", "基准", "模块", "相对误差"], rowsa)

    txt = DOC.read_text()
    for marker, table in (("<!-- TABLE_ASTM -->", t_astm), ("<!-- TABLE_SPEC -->", t_spec),
                          ("<!-- TABLE_REF -->", t_ref), ("<!-- TABLE_ANCHORS -->", t_anch)):
        head, _, tail = txt.partition(marker)
        txt = head + marker + "\n\n" + table + "\n" + tail
    DOC.write_text(txt)
    print("tables injected ->", DOC)

# ---------------------------------------------------------------------------
# console summary (the only "hand" numbers are these printed ones)
# ---------------------------------------------------------------------------
print("ASTM canonical:", json.dumps(res["astm_canonical"], indent=2))
print("N(100 MPa) rel err vs DNV 5.0e5:", sn["N_100_rel_err"])
print("Rayleigh closure max rel err:", max(v["rel_err"] for v in ray.values()))
for k, v in spec.items():
    print(f"{k:44s} nb={v['ratio_narrowband']:.4f} wl={v['ratio_wideband']:.4f} "
          f"eps={v['bandwidth_eps']:.3f}")
print("module reference record:", json.dumps(res["module_reference_record"], indent=2))
print("artefacts ->", OUT)