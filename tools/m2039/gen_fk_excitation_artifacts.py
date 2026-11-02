#!/usr/bin/env python3
"""m2039 工件生成：升沉 FK 激励有限频率精确核的验证表 + 图（数字零手抄）。"""

from __future__ import annotations

import json
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from src.ship_perf.constants import G as G_PLAT  # noqa: E402
from src.ship_perf.constants import RHO_SEA as RHO_PLAT  # noqa: E402
from src.ship_perf.seakeeping import Hull, fk_planform_factor, heave_rao  # noqa: E402
from src.ship_verification.fk_excitation_exact import (  # noqa: E402
    circle_fk_closed,
    fk_heave_force_hull,
    fk_heave_force_long_wave,
    fk_heave_force_rect_closed,
    fk_heave_force_section,
    long_wave_overestimate,
    rect_section,
    semicircle_section,
    sinc,
    wavenumber,
)

OUT = os.path.join(HERE, "outputs")
FIG = os.path.join(OUT, "figs")
os.makedirs(FIG, exist_ok=True)

RHO_V, G_V = 1025.0, 9.81  # 验证模块独立常数

CASES = {
    "KVLCC2": Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82),
    "patrol60": Hull(length=60.0, beam=9.0, draft=3.0, cb=0.62),
}
MASS = {"KVLCC2": 4.5e8, "patrol60": 9.5e5}
HEADINGS = (0.0, 45.0, 90.0)


def awp(hull: Hull) -> float:
    xs = np.linspace(-hull.length / 2, hull.length / 2, 40001)
    return float(np.trapezoid([hull.beam_at(x) for x in xs], xs))


_BX_CACHE = {}


def bx(hull: Hull, n: int = 4001):
    key = (id(hull), hull.length, hull.beam, hull.cb, n)
    if key not in _BX_CACHE:
        xs = np.linspace(-hull.length / 2, hull.length / 2, n)
        _BX_CACHE[key] = (xs, np.array([hull.beam_at(x) for x in xs]), hull.length / n)
    return _BX_CACHE[key]


def factor_vec(hull: Hull, periods, heading_deg: float, n: int = 4001) -> np.ndarray:
    """式 (3) 的水线面相位因子（对周期向量化）。"""
    xs, bs, dx = bx(hull, n)
    k = (2.0 * np.pi / np.asarray(periods, dtype=float)) ** 2 / G_PLAT
    psi = math.radians(heading_deg)
    out = np.empty(k.size)
    for i, ki in enumerate(k):
        u = ki * bs * math.sin(psi) / 2.0
        sb = np.where(np.abs(u) < 1e-9, 1.0 - u * u / 6.0,
                      np.sin(np.where(u == 0, 1.0, u)) / np.where(u == 0, 1.0, u))
        out[i] = abs(np.sum(bs * sb * np.exp(-1j * ki * xs * math.cos(psi))) * dx)
    return out / float(np.sum(bs) * dx)


def exact_fk_vec(hull: Hull, periods, heading_deg: float, wave_amp: float = 1.0) -> np.ndarray:
    k = (2.0 * np.pi / np.asarray(periods, dtype=float)) ** 2 / G_PLAT
    awp_m = awp(hull)
    return RHO_PLAT * G_PLAT * wave_amp * awp_m * np.exp(-k * hull.draft) * factor_vec(
        hull, periods, heading_deg)


def anchor_checks() -> dict:
    """四条独立锚点的残差（全部程序化计算）。"""
    checks = {}

    # A1 二维矩形：闭合 sinc vs 周长求积
    B, T = 58.0, 20.8
    a1 = []
    for period in (6.0, 8.0, 10.0, 14.0):
        k = wavenumber(period)
        closed = RHO_V * G_V * B * math.exp(-k * T) * abs(sinc(k * B / 2.0))
        quad = fk_heave_force_section(rect_section(B, T, 4000), 1.0, period, 90.0, waterline=T)
        a1.append({"period_s": period, "closed_N": closed, "quad_N": quad,
                   "rel": (quad - closed) / closed})
    checks["A1_rect_2d_sinc_vs_perimeter_quadrature"] = a1

    # A2 半圆：解析级数 vs 周长求积
    a2 = []
    a = 10.0
    for period in (4.0, 6.0, 8.0, 10.0, 14.0, 20.0):
        closed = abs(circle_fk_closed(a, 1.0, period))
        quad = fk_heave_force_section(semicircle_section(a, 20000), 1.0, period, 90.0, waterline=0.0)
        a2.append({"period_s": period, "ka": wavenumber(period) * a,
                   "series_N": closed, "quad_N": quad, "rel": (quad - closed) / closed})
    checks["A2_semicircle_series_vs_perimeter_quadrature"] = a2

    # A3 三维船体积分 vs 矩形闭合（箱型，Cb=1）
    hb = Hull(length=320.0, beam=58.0, draft=20.8, cb=1.0)
    a3 = []
    for period in (6.0, 8.0, 12.0, 20.0):
        quad = fk_heave_force_hull(hb, 1.0, period, 90.0, n_x=40001)["force_N"]
        closed = fk_heave_force_rect_closed(320.0, 58.0, 20.8, 1.0, period, 90.0)["force_N"]
        a3.append({"period_s": period, "quad_N": quad, "closed_N": closed,
                   "rel": (quad - closed) / closed})
    checks["A3_hull_quadrature_vs_rect_closed"] = a3

    # A4 长波极限恒等式
    a4 = []
    for key, hull in CASES.items():
        period = 400.0
        k = wavenumber(period, G_PLAT)
        exact = fk_heave_force_hull(hull, 1.0, period, 90.0, g=G_PLAT, n_x=40001)["force_N"]
        long_wave = fk_heave_force_long_wave(awp(hull), hull.draft, 1.0, period, g=G_PLAT, rho=RHO_PLAT)
        a4.append({"case": key, "lambda_over_L": (2 * math.pi / k) / hull.length,
                   "exact_N": exact, "long_wave_N": long_wave,
                   "rel": (exact - long_wave) / long_wave})
        checks.setdefault("A4_long_wave_limit", []).append(a4[-1])
    return checks


def debt_table() -> dict:
    """平台长波式 vs 精确 FK：逐周期高估倍数与 <3% 波段。"""
    out = {}
    periods = [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 24.0, 30.0]
    for key, hull in CASES.items():
        lam = 2 * math.pi / ((2 * np.pi / np.asarray(periods)) ** 2 / G_PLAT)
        awp_m = awp(hull)
        long_wave = RHO_PLAT * G_PLAT * awp_m * np.exp(
            -((2 * np.pi / np.asarray(periods)) ** 2 / G_PLAT) * hull.draft)
        exact = exact_fk_vec(hull, periods, 90.0)
        factor = factor_vec(hull, periods, 90.0)
        rows = []
        for i, t in enumerate(periods):
            rows.append({
                "period_s": t,
                "wavelength_m": float(lam[i]),
                "k": float((2 * np.pi / t) ** 2 / G_PLAT),
                "lambda_over_L": float(lam[i] / hull.length),
                "planform_factor": float(factor[i]),
                "fk_long_wave_N": float(long_wave[i]),
                "fk_exact_N": float(exact[i]),
                "ratio_long_wave_over_exact": float(long_wave[i] / exact[i]),
                "rel_err_pct": float(100.0 * (long_wave[i] - exact[i]) / exact[i]),
            })
        ok = [r["lambda_over_L"] for r in rows if r["rel_err_pct"] < 3.0]
        out[key] = {
            "geometry": {"L": hull.length, "B": hull.beam, "T": hull.draft, "Cb": hull.cb,
                         "A_wp_m2": awp_m},
            "rows": rows,
            "max_overestimate_ratio": max(r["ratio_long_wave_over_exact"] for r in rows),
            "lambda_over_L_for_below_3pct": min(ok) if ok else None,
            "period_for_below_3pct_s": max(r["period_s"] for r in rows if r["rel_err_pct"] < 3.0)
            if ok else None,
        }
    return out


def rao_impact() -> dict:
    """升沉 RAO：长波式 vs 精确 FK（阻尼比 0.15，EVIDENCE 标注 WITHHELD）。"""
    out = {}
    for key, hull in CASES.items():
        periods = [4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0]
        lw = {r["period"]: r for r in heave_rao(hull, periods, MASS[key], rho=RHO_PLAT)}
        ex = {r["period"]: r for r in heave_rao(hull, periods, MASS[key], rho=RHO_PLAT,
                                               fk_model="exact", heading_deg=90.0)}
        rows = []
        for p in periods:
            rows.append({
                "period_s": p,
                "wavelength_m": 2 * math.pi / ((2 * math.pi / p) ** 2 / G_PLAT),
                "lambda_over_L": (2 * math.pi / ((2 * math.pi / p) ** 2 / G_PLAT)) / hull.length,
                "rao_long_wave": lw[p]["rao_heave"],
                "rao_exact_fk": ex[p]["rao_heave"],
                "ratio": ex[p]["rao_heave"] / lw[p]["rao_heave"],
                "planform_factor": fk_planform_factor(hull, (2 * math.pi / p) ** 2 / G_PLAT, 90.0),
            })
        out[key] = rows
    return out


def figures(anchors, debt, rao):
    # 图 1：FK 激励 vs λ/L（三艏向）与平台长波式
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6))
    for ax, (key, hull) in zip(axes, CASES.items()):
        periods = np.logspace(math.log10(3.0), math.log10(60.0), 120)
        lam_over_L = (2 * math.pi / ((2 * np.pi / periods) ** 2 / G_PLAT)) / hull.length
        for heading in HEADINGS:
            ax.loglog(lam_over_L, factor_vec(hull, periods, heading), lw=1.6,
                      label=f"exact FK, psi={heading:.0f} deg")
        ax.loglog(lam_over_L, np.ones_like(periods), "k--", lw=2.0,
                  label="platform long-wave (k->0)")
        ax.axvline(1.0, color="0.6", ls=":", lw=1.0)
        ax.set_xlabel(r"$\lambda/L$")
        ax.set_ylabel(r"$|F_3|/(\rho g \zeta_a A_{wp})$")
        ax.set_title(f"{key}: L={hull.length:.0f} m, B={hull.beam:.0f} m, T={hull.draft:.1f} m")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=7.5)
    fig.suptitle("Heave Froude-Krylov excitation: exact finite-frequency kernel vs platform long-wave formula",
                 fontsize=10)
    fig.tight_layout()
    p1 = os.path.join(FIG, "fig1_fk_excitation_vs_wavelength.png")
    fig.savefig(p1, dpi=150)
    plt.close(fig)

    # 图 2：相对误差与 3% 带
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    for key in CASES:
        rows = debt[key]["rows"]
        ax.semilogx([r["lambda_over_L"] for r in rows], [r["rel_err_pct"] for r in rows],
                    "o-", label=key)
    ax.axhline(3.0, color="r", ls="--", lw=1.2, label="3 % criterion")
    ax.axhline(15.0, color="darkred", ls=":", lw=1.0, label="15 % acceptance limit")
    ax.set_xlabel(r"$\lambda/L$")
    ax.set_ylabel("long-wave formula error vs exact FK  [%]")
    ax.set_title("Verification-debt quantification: heave FK excitation is wavelength-blind")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    p2 = os.path.join(FIG, "fig2_debt_error_vs_wavelength.png")
    fig.savefig(p2, dpi=150)
    plt.close(fig)

    # 图 3：升沉 RAO 影响 + 锚点残差
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.4))
    ax = axes[0]
    for key in CASES:
        rows = rao[key]
        ax.loglog([r["lambda_over_L"] for r in rows], [r["rao_long_wave"] for r in rows],
                  "s--", label=f"{key} long-wave")
        ax.loglog([r["lambda_over_L"] for r in rows], [r["rao_exact_fk"] for r in rows],
                  "o-", label=f"{key} exact FK")
    ax.set_xlabel(r"$\lambda/L$")
    ax.set_ylabel(r"heave RAO  $|z_a|/\zeta_a$")
    ax.set_title("heave RAO (damping ratio 0.15, radiation damping = WITHHELD)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7.5)

    ax = axes[1]
    a1 = anchors["A1_rect_2d_sinc_vs_perimeter_quadrature"]
    a2 = anchors["A2_semicircle_series_vs_perimeter_quadrature"]
    a3 = anchors["A3_hull_quadrature_vs_rect_closed"]
    ax.semilogy([r["period_s"] for r in a1], [abs(r["rel"]) + 1e-17 for r in a1], "o-", label="A1 rect 2D: closed vs perimeter")
    ax.semilogy([r["period_s"] for r in a2], [abs(r["rel"]) + 1e-17 for r in a2], "s-", label="A2 semicircle: series vs perimeter")
    ax.semilogy([r["period_s"] for r in a3], [abs(r["rel"]) + 1e-17 for r in a3], "^-", label="A3 hull: quadrature vs closed")
    ax.axhline(1e-6, color="r", ls="--", lw=1.0, label="anchor tolerance 1e-6")
    ax.set_xlabel("wave period [s]")
    ax.set_ylabel("|relative residual|")
    ax.set_title("independent-anchor residuals (all labels REAL)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7.5)
    fig.tight_layout()
    p3 = os.path.join(FIG, "fig3_rao_impact_and_anchor_residuals.png")
    fig.savefig(p3, dpi=150)
    plt.close(fig)

    # 图 4：半圆截面 Smith 修正云图（e^{-kz} 压力 × 法向）
    fig, axes = plt.subplots(1, 3, figsize=(12.0, 3.8))
    a = 10.0
    th = np.linspace(math.pi, 2 * math.pi, 400)
    for ax, period in zip(axes, (6.0, 10.0, 20.0)):
        k = wavenumber(period, G_V)
        y, z = a * np.cos(th), a * np.sin(th)
        p = np.exp(k * z) * np.cos(k * y)
        ax.scatter(y, z, c=p, cmap="inferno", s=12)
        ax.plot(y, z, color="0.5", lw=0.8)
        ax.set_aspect("equal")
        ax.set_title(f"semicircle section, T={period:.0f} s (ka={k*a:.2f})\n"
                     f"$p/(\\rho g\\zeta_a)\\cos(ky)$, |F3|/m={abs(circle_fk_closed(a,1.0,period))/(RHO_V*G_V):.2f} m²")
        ax.set_xlabel("y [m]")
        ax.set_ylabel("z [m]")
    fig.suptitle("Incident-pressure field over a wetted section (field map behind the FK integral)", fontsize=10)
    fig.tight_layout()
    p4 = os.path.join(FIG, "fig4_section_pressure_field.png")
    fig.savefig(p4, dpi=150)
    plt.close(fig)
    return [p1, p2, p3, p4]


def main():
    anchors = anchor_checks()
    debt = debt_table()
    rao = rao_impact()
    figs = figures(anchors, debt, rao)
    payload = {
        "mission": 2039,
        "title": "heave FK excitation finite-frequency kernel verification",
        "constants": {"rho_verification": RHO_V, "g_verification": G_V,
                      "rho_platform": RHO_PLAT, "g_platform": G_PLAT},
        "anchors": anchors,
        "debt": debt,
        "rao_impact": rao,
        "figures": [os.path.relpath(f, ROOT) for f in figs],
    }
    path = os.path.join(OUT, "fk_excitation_verification.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(json.dumps({"out": path, "figs": payload["figures"]}, indent=2))

    for key, d in debt.items():
        print(f"\n== {key} ==")
        for r in d["rows"]:
            print(f"  T={r['period_s']:5.1f}s λ/L={r['lambda_over_L']:6.3f} "
                  f"ratio={r['ratio_long_wave_over_exact']:8.3f} err={r['rel_err_pct']:9.2f}%")
        print(f"  max overestimate = {d['max_overestimate_ratio']:.2f}x;"
              f" <3% from λ/L = {d['lambda_over_L_for_below_3pct']}")


if __name__ == "__main__":
    main()