#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""天气衡准验证探针：独立参考实现 vs 平台 /api/v1/dynamic-stability/weather-criterion。

产出（数字全部程序化注入报告，零手抄）：
  artifacts/weather_probe.json   全部数字
  artifacts/fig_gz_areas.png     GZ 曲线 + lw1/lw2 + a/b 区域（参考）与平台积分域
  artifacts/fig_ratio.png        b/a 随 theta1 / 受风面积变化 vs 平台值
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imo_weather import (GUST_FACTOR, P_STD, GZCurve, criterion_sweep,  # noqa: E402
                         platform_expression, ratio_vs_wind_area, weather_criterion,
                         wind_heeling_lever)

API = "http://127.0.0.1:8001/api/v1/dynamic-stability/weather-criterion"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
os.makedirs(OUT, exist_ok=True)

# case1: 高受风面积、富余稳性（阵风力臂在回摆角处已被 GZ 超过 -> a=0）
C1 = dict(name="case1_stiff_high_windage", L=100.0, B=18.0, T=6.0, disp=6000.0,
          A=1200.0, Z=9.0,
          gz=[(0, 0.0), (10, 0.16), (20, 0.33), (30, 0.46), (40, 0.52),
              (50, 0.45), (60, 0.31), (70, 0.12), (75.0, 0.0)])
# case2: 临界（lw2 略低于 GZ 峰值 -> 参考 b/a<<1 判不满足；平台算法仍判满足）
C2 = dict(name="case2_borderline_false_pass", L=60.0, B=13.0, T=3.2, disp=1800.0,
          A=700.0, Z=10.0,
          gz=[(0, 0.0), (10, 0.13), (20, 0.26), (30, 0.34), (40, 0.30),
              (50, 0.18), (60, 0.0)])
# case3: 明显不满足（阵风力臂远超 GZ 峰值 -> 双方均判不满足）
C3 = dict(name="case3_gross_noncompliant", L=60.0, B=13.0, T=3.2, disp=1800.0,
          A=1500.0, Z=10.0,
          gz=[(0, 0.0), (10, 0.13), (20, 0.26), (30, 0.34), (40, 0.30),
              (50, 0.18), (60, 0.0)])


def curve_of(c):
    return GZCurve(np.array([p[0] for p in c["gz"]], float),
                   np.array([p[1] for p in c["gz"]], float))


def call_api(c, keys=("heel", "GZ")):
    body = {"L": c["L"], "B": c["B"], "T": c["T"], "displacement": c["disp"],
            "GZ_curve": [{keys[0]: a, keys[1]: g} for a, g in c["gz"]],
            "wind_pressure": P_STD, "wind_area": c["A"], "wind_lever": c["Z"]}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"status": r.status, "data": json.load(r)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "data": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "data": f"{type(e).__name__}: {e}"}


def analyse(c):
    curve = curve_of(c)
    lw1 = wind_heeling_lever(P_STD, c["A"], c["Z"], c["disp"])
    sweep = criterion_sweep(curve, lw1)
    sweep_p = criterion_sweep(curve, lw1, route="pchip")
    rep = weather_criterion(curve, lw1, 15.0)          # theta1 代表点 15 deg
    a_grid = [round(c["A"] * f, 1) for f in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)]
    wind_area_rows = ratio_vs_wind_area(curve, P_STD, a_grid, c["Z"], c["disp"], 15.0)
    plat = platform_expression(curve.angles_deg, curve.gz_m, P_STD, c["A"], c["Z"], c["disp"])
    api = call_api(c)
    api_alt = call_api(c, keys=("angle", "gz"))   # schema 允许的等价键名
    agree = max(abs(s["ratio"] - p["ratio"]) / max(p["ratio"], 1e-12)
                for s, p in zip(sweep, sweep_p) if np.isfinite(s["ratio"]) and np.isfinite(p["ratio"]))
    return {
        "case": {k: v for k, v in c.items() if k != "gz"},
        "gz_curve": c["gz"],
        "theta_f_deg": curve.theta_f_deg,
        "gz_max_m": float(np.max(curve.gz_m)),
        "area_total_mrad": curve.area_total,
        "lw1_m": lw1, "lw2_m": GUST_FACTOR * lw1,
        "reference_sweep": sweep,
        "reference_representative_theta1_15": rep,
        "reference_representative_pchip": weather_criterion(curve, lw1, 15.0, route="pchip"),
        "route_agreement_max_rel": agree,
        "wind_area_sweep_theta1_15": wind_area_rows,
        "platform_formula_replica": plat,
        "platform_api": api,
        "platform_api_alt_keys": api_alt,
        "compliance_platform": plat["compliant"],
        "compliance_reference": rep["compliant"],
        "verdict_diverges": plat["compliant"] != rep["compliant"],
    }


def figures(res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    r1 = res[0]
    curve = curve_of(C1)
    th = np.linspace(0, curve.angles_deg[-1], 1500)
    gz = np.interp(th, curve.angles_deg, curve.gz_m)
    lw1, lw2 = r1["lw1_m"], r1["lw2_m"]
    rep = r1["reference_representative_theta1_15"]
    t1, tc, t2 = 15.0, rep["thetaC"], rep["theta2"]

    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    ax.plot(th, gz, "k-", lw=2.2, label="GZ(theta) input")
    ax.axhline(lw1, color="tab:blue", ls="--", lw=1.4, label=f"lw1 steady wind = {lw1:.4f} m")
    ax.axhline(lw2, color="tab:red", ls="-", lw=1.8, label=f"lw2 = 1.5*lw1 = {lw2:.4f} m")
    ax.fill_between(th, 0, gz, color="grey", alpha=.15,
                    label=f"platform area_b domain (0..{curve.angles_deg[-1]:.0f} deg) = "
                          f"{r1['platform_formula_replica']['area_b']:.4f}")
    m2 = (th >= tc) & (th <= t2)
    ax.fill_between(th[m2], lw2, np.interp(th[m2], curve.angles_deg, curve.gz_m),
                    color="tab:green", alpha=.35, label=f"b reference = {rep['area_b']:.4f} m*rad")
    for x, txt in ((t1, "theta1"), (tc, "thetaC"), (t2, "theta2")):
        ax.axvline(x, color="dimgrey", ls=":", lw=1)
        ax.text(x, ax.get_ylim()[1] * .97, txt, ha="center", fontsize=8, color="dimgrey")
    ax.annotate("a = 0 here: GZ(theta1) > lw2\n(ship never loses static margin)",
                xy=(t1, lw2), xytext=(28, lw2 * 0.45),
                arrowprops=dict(arrowstyle="->", color="tab:orange"), fontsize=8, color="tab:orange")
    ax.set_xlabel("heel angle theta [deg]"); ax.set_ylabel("lever [m]")
    ax.set_title(f"case1: reference b/a={rep['ratio']:.3g} (a=0) vs platform ratio="
                 f"{r1['platform_formula_replica']['ratio']:.2f}", fontsize=10)
    ax.grid(alpha=.3); ax.legend(fontsize=7.5, loc="upper right")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig_gz_areas.png"), dpi=140)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4))
    for r in res:
        t1s = [s["theta1"] for s in r["reference_sweep"]]
        rs = [s["ratio"] if np.isfinite(s["ratio"]) else 1e3 for s in r["reference_sweep"]]
        axes[0].plot(t1s, [min(v, 1e3) for v in rs], "o-", ms=4,
                     label=f"{r['case']['name']}: reference b/a")
        axes[0].axhline(min(r["platform_formula_replica"]["ratio"], 1e3), ls="--", lw=1.3,
                        label=f"{r['case']['name']}: platform {r['platform_formula_replica']['ratio']:.2f}")
    axes[0].axhline(1.0, color="k", lw=1.2, label="criterion b/a = 1")
    axes[0].set_xlabel("roll-back angle theta1 [deg]"); axes[0].set_ylabel("b/a  (log)")
    axes[0].set_yscale("log"); axes[0].grid(alpha=.3, which="both"); axes[0].legend(fontsize=6.5)

    for r in res:
        rows = r["wind_area_sweep_theta1_15"]
        axes[1].plot([w["A"] for w in rows],
                     [max(w["ratio"], 1e-2) if np.isfinite(w["ratio"]) else 1e3 for w in rows],
                     "o-", ms=4, label=f"{r['case']['name']}: reference")
        prows = [platform_expression([q[0] for q in r["gz_curve"]],
                                     [q[1] for q in r["gz_curve"]],
                                     P_STD, w["A"], r["case"]["Z"],
                                     r["case"]["disp"])["ratio"] for w in rows]
        axes[1].plot([w["A"] for w in rows], prows, "s--", ms=4, alpha=.6,
                     label=f"{r['case']['name']}: platform")
    axes[1].axhline(1.0, color="k", lw=1.2, label="criterion = 1")
    axes[1].set_xlabel("windage area A [m2]"); axes[1].set_ylabel("b/a  (log)")
    axes[1].set_yscale("log"); axes[1].grid(alpha=.3, which="both"); axes[1].legend(fontsize=6.5)
    fig.suptitle("Sensitivity: reference criterion flips with theta1 and windage area; "
                 "platform ratio stays on the permissive side", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig_ratio.png"), dpi=140)
    plt.close(fig)
    return ["fig_gz_areas.png", "fig_ratio.png"]


def main() -> int:
    res = [analyse(c) for c in (C1, C2, C3)]
    res = json.loads(json.dumps(res, default=float))
    figs = figures(res)
    payload = {"api": API, "P_std_Pa": P_STD, "gust_factor": GUST_FACTOR,
               "theta1_representative_deg": 15.0, "results": res, "figures": figs}
    with open(os.path.join(OUT, "weather_probe.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    for r in res:
        rep = r["reference_representative_theta1_15"]
        ratio_ref = "inf(a=0)" if not np.isfinite(rep["ratio"]) else f"{rep['ratio']:.3f}"
        print(f"[{r['case']['name']}] lw1={r['lw1_m']:.5f} lw2={r['lw2_m']:.5f} "
              f"ref b/a(theta1=15)={ratio_ref} compliant={rep['compliant']} | "
              f"platform ratio={r['platform_formula_replica']['ratio']:.3f} "
              f"compliant={r['platform_formula_replica']['compliant']} | "
              f"api={r['platform_api']['status']} alt_keys={r['platform_api_alt_keys']['status']} | "
              f"diverge={r['verdict_diverges']}")
    print("route agreement (linear vs pchip, max rel):",
          [round(r["route_agreement_max_rel"], 6) for r in res])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())