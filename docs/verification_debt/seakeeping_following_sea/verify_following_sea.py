#!/usr/bin/env python3
"""耐波域验证欠账：随浪（following sea）ω_e ≤ 0 无效频带的解析复核。

问题
----
`backend/services/seakeeping.py` 的遭遇频率 ω_e = ω − k U cosβ（深水 k = ω²/g）。
随浪（cosβ = 1）时 ω_e ≤ 0 ⟺ ω ≥ ω_c = g/U（U 为航速）。该频带内 SDOF 传递函数
在非正自变量上求值，物理上无意义，但旧版 `response_statistics` 把它按"对称"传递
静默计入 σ，不报警。

解析基准（可机器判定，无需文献表值）
------------------------------------
1. 剪断频率闭式：ω_c = g / (U cosβ)，cosβ > 0；cosβ ≤ 0（顶浪/横浪）时永不进入。
2. ITTC/ISSC 谱 S = α A ω^-5 exp(−B ω^-4)，B = 691/T1^4 的尾部积分有闭式：
       ∫_{ω_c}^{∞} S dω / m0 = 1 − exp(−B ω_c^{-4})      （α、A 约去）
   即无效频带携带的能量占比**解析可知**，可与数值掩码结果逐点比对。
3. 阶梯恒等式：mask_invalid=False 的结果必须与历史实现逐位一致（向后兼容）。

输出：numbers.json + 3 张 png。运行：
    python3 docs/verification_debt/seakeeping_following_sea/verify_following_sea.py
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MOD = os.path.join(ROOT, "backend", "services", "seakeeping.py")

spec = importlib.util.spec_from_file_location("seakeeping_svc", MOD)
sk = importlib.util.module_from_spec(spec)
sys.modules["seakeeping_svc"] = sk
spec.loader.exec_module(sk)

G = sk.G
KN = 1852.0 / 3600.0  # 1 kn in m/s

# 代表性船：KCS 级巴拿马型集装箱船量级（几何闭合：∇ = Cb·L·B·T）
HULL = dict(
    L=230.0, B=32.2, T=10.8, Cb=0.651, Cwp=0.80,
    displacement_t=51900.0, KG=9.0, GM_T=1.2,
)
MODEL = sk.SeakeepingModel(**HULL)
HS, T1, GAMMA = 3.0, 8.0, 3.3
OMEGA = np.linspace(0.10, 2.60, 5001)
B_ITTC = 691.0 / T1 ** 4


def ittc_tail_fraction(omega_c: float) -> float:
    """闭式：ITTC/ISSC 谱在 ω ≥ ω_c 上的能量占比 = 1 − exp(−B ω_c^-4)。

    推导：∫ A ω^-5 e^{-Bω^-4} dω，令 u=ω^-4 ⇒ m0 = A/(4B)，
    尾部 = (A/4B)(1 − e^{-B ω_c^-4})，故占比 = 1 − e^{-B ω_c^-4}。
    """
    if not (omega_c > 0.0) or math.isinf(omega_c):
        return 0.0
    return 1.0 - math.exp(-B_ITTC * omega_c ** -4.0)


def run_case(speed_kn: float, heading_deg: float) -> dict:
    U = speed_kn * KN
    spec_d = sk.spectrum("ittc", HS, T1, list(OMEGA), gamma=GAMMA)
    raw = sk.response_statistics(MODEL, spec_d, heading_deg=heading_deg, speed=U, mask_invalid=False)
    msk = sk.response_statistics(MODEL, spec_d, heading_deg=heading_deg, speed=U, mask_invalid=True)
    cosb = math.cos(math.radians(heading_deg))
    omega_c = G / (U * cosb) if (U > 0 and cosb > 1e-9) else math.inf
    analytic = ittc_tail_fraction(omega_c) if math.isfinite(omega_c) else 0.0
    numeric = float(raw["encounter_invalid_energy_fraction"])
    d_raw, d_msk = float(raw["sigma_heave"]), float(msk["sigma_heave"])
    return {
        "speed_kn": speed_kn,
        "heading_deg": heading_deg,
        "omega_c_analytic_rad_s": omega_c if math.isfinite(omega_c) else None,
        "omega_c_code_rad_s": raw["omega_e_first_invalid_rad_s"] if raw["encounter_invalid"] else None,
        "cutoff_rel_err_pct": (
            100.0 * abs(float(raw["omega_e_first_invalid_rad_s"]) - omega_c) / omega_c
            if (raw["encounter_invalid"] and math.isfinite(omega_c) and omega_c > 0) else 0.0
        ),
        "invalid_energy_fraction_analytic": analytic,
        "invalid_energy_fraction_numeric": numeric,
        "invalid_fraction_abs_err": abs(numeric - analytic),
        "sigma_heave_raw_m": d_raw,
        "sigma_heave_masked_m": d_msk,
        "sigma_heave_bias_pct": 100.0 * (d_raw - d_msk) / d_msk if d_msk > 0 else float("nan"),
        "sigma_pitch_raw_deg": float(raw["sigma_pitch"]),
        "sigma_pitch_masked_deg": float(msk["sigma_pitch"]),
        "sigma_acc_raw": float(raw["sigma_vertical_acc_m_s2"]),
        "sigma_acc_masked": float(msk["sigma_vertical_acc_m_s2"]),
    }


def main() -> int:
    speeds = [0.0, 2.0, 5.0, 8.0, 10.0, 12.0, 15.0, 18.0, 20.0]
    headings = [180.0, 135.0, 90.0, 45.0, 0.0]
    cases = [run_case(u, h) for u in speeds for h in headings]
    by = {(c["speed_kn"], c["heading_deg"]): c for c in cases}

    # --- 判据汇总（解析 vs 数值） ---
    checks = []
    for c in cases:
        if c["omega_c_analytic_rad_s"] is None:
            continue
        checks.append({
            "case": f'{c["speed_kn"]:g}kn/{c["heading_deg"]:g}deg',
            "cutoff_rel_err_pct": c["cutoff_rel_err_pct"],
            "invalid_fraction_abs_err": c["invalid_fraction_abs_err"],
            "pass": bool(c["cutoff_rel_err_pct"] < 1.0 and c["invalid_fraction_abs_err"] < 0.01),
        })

    # --- 图 1：随浪谱 + 无效频带（15 kn / 20 kn） ---
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for k, u in enumerate((15.0, 20.0)):
        sp = sk.spectrum("ittc", HS, T1, list(OMEGA), gamma=GAMMA)
        S = np.array(sp["S"])
        wc = G / (u * KN)
        a = ax[k]
        a.plot(OMEGA, S / S.max(), lw=1.6, color="#1f3b73", label="ITTC spectrum (normalised)")
        a.axvspan(wc, OMEGA[-1], color="#c0392b", alpha=0.20, label=r"$\omega_e\leq 0$ band ($\omega\geq g/U$)")
        a.axvline(wc, color="#c0392b", ls="--", lw=1.2)
        frac = ittc_tail_fraction(wc)
        a.set_xlim(0.1, 2.6)
        a.set_xlabel(r"$\omega$ [rad/s]")
        a.set_ylabel(r"$S_\zeta(\omega)/\max$")
        a.set_title(f"U = {u:g} kn, following sea: {100*frac:.1f}% of $m_0$ in invalid band")
        a.legend(fontsize=8, loc="upper right")
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig1_invalid_band.png"), dpi=150)

    # --- 图 2：无效能量占比 vs 航速（解析曲线 + 数值点，三个艏向） ---
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    uu = np.linspace(1.0, 22.0, 400)
    for h, col in ((0.0, "#c0392b"), (45.0, "#e67e22"), (90.0, "#7f8c8d")):
        cb = math.cos(math.radians(h))
        if cb <= 0:
            continue
        ana = [ittc_tail_fraction(G / (u * KN * cb)) for u in uu]
        ax.plot(uu, np.array(ana) * 100, lw=1.6, color=col, label=f"closed form, {h:g} deg")
        xs = [c["speed_kn"] for c in cases if c["heading_deg"] == h and c["speed_kn"] > 0]
        ys = [100 * by[(u, h)]["invalid_energy_fraction_numeric"] for u in xs]
        ax.plot(xs, ys, "o", ms=4, color=col, mfc="none", label=f"platform numeric, {h:g} deg")
    ax.axhline(0.0, color="k", lw=0.6)
    ax.set_xlabel("speed U [kn]")
    ax.set_ylabel(r"$m_0$ share with $\omega_e\leq0$ [%]")
    ax.set_title("Following-sea invalid-band energy: closed form vs platform")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig2_fraction_vs_speed.png"), dpi=150)

    # --- 图 3：σ_heave vs 航速：raw（含无效带）vs masked，随浪 vs 顶浪 ---
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    xs = [u for u in speeds if u > 0]
    for h, col in ((0.0, "#c0392b"), (180.0, "#1f3b73")):
        ax.plot(xs, [by[(u, h)]["sigma_heave_raw_m"] for u in xs], "-o", ms=4, color=col,
                label=f"raw (historical), hdg {h:g}")
        ax.plot(xs, [by[(u, h)]["sigma_heave_masked_m"] for u in xs], "--s", ms=4, mfc="none",
                color=col, label=f"masked, hdg {h:g}")
    ax.set_xlabel("speed U [kn]")
    ax.set_ylabel(r"$\sigma_{heave}$ [m]")
    ax.set_title(f"Hs={HS:g} m, T1={T1:g} s ITTC: heave response vs speed")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig3_sigma_vs_speed.png"), dpi=150)

    out = {
        "hull": HULL,
        "sea_state": {"kind": "ittc", "Hs_m": HS, "T1_s": T1, "gamma": GAMMA},
        "grid": {"omega_min": float(OMEGA[0]), "omega_max": float(OMEGA[-1]), "n": int(OMEGA.size)},
        "model_notes": {
            "kappa33": MODEL.kappa33, "kappa55": MODEL.kappa55, "kappa44": MODEL.kappa44,
            "zeta33": MODEL.zeta33, "zeta55": MODEL.zeta55, "zeta44": MODEL.zeta44,
            "natural_periods_s": MODEL.natural_periods(),
        },
        "cases": cases,
        "checks": checks,
        "checks_all_pass": all(c["pass"] for c in checks),
        "max_cutoff_rel_err_pct": max((c["cutoff_rel_err_pct"] for c in checks), default=0.0),
        "max_invalid_fraction_abs_err": max((c["invalid_fraction_abs_err"] for c in checks), default=0.0),
        "worst_sigma_bias_pct": max(
            (abs(c["sigma_heave_bias_pct"]) for c in cases
             if isinstance(c["sigma_heave_bias_pct"], float) and math.isfinite(c["sigma_heave_bias_pct"])),
            default=0.0,
        ),
    }
    with open(os.path.join(HERE, "numbers.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps({k: out[k] for k in ("checks_all_pass", "max_cutoff_rel_err_pct",
                                          "max_invalid_fraction_abs_err", "worst_sigma_bias_pct")},
                     indent=2))
    for c in cases:
        if c["heading_deg"] in (0.0, 180.0):
            print(f'{c["speed_kn"]:5.1f}kn hdg{c["heading_deg"]:5.0f} '
                  f'frac_ana={c["invalid_energy_fraction_analytic"]:.4f} '
                  f'frac_num={c["invalid_energy_fraction_numeric"]:.4f} '
                  f'sigma_raw={c["sigma_heave_raw_m"]:.4f} masked={c["sigma_heave_masked_m"]:.4f} '
                  f'bias={c["sigma_heave_bias_pct"]:+.2f}%')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())