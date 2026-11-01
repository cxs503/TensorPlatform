#!/usr/bin/env python3
"""生成 m1490 报告图（docs/m1490/*.png）。

图 1  resistance：SUBOFF AFF-8 Ct 三方对照（服务 / 本仓正确物理 / 审计自引基准）
图 2  stability：KB(Cb) 三模型对照（服务 evaluate、服务 calculations 的 2T/3、解析梯形）
图 3  stability：GZ 曲线（服务 gz-curve、服务 calculations wall-sided、本仓大倾角积分）
图 4  propulsion：敞水特性 KT/KQ/η0(J)（BEMT vs 服务直线近似 vs 致动盘上界）

用法：python3 docs/m1490/make_figures.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.request

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
OUT = os.path.join(ROOT, "docs", "m1490")
os.makedirs(OUT, exist_ok=True)

from ship_perf import conformance as cf  # noqa: E402
from ship_perf import hydrostatics as hs  # noqa: E402
from ship_perf import propeller as pr  # noqa: E402
from ship_perf import resistance as rs  # noqa: E402

API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001")


def post(path: str, body: dict):
    req = urllib.request.Request(
        f"{API}/api/v1/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # pragma: no cover
        return {"error": str(e)}


plt.rcParams.update({"figure.dpi": 130, "font.size": 9, "axes.grid": True, "grid.alpha": 0.3})


# ---------------------------------------------------------------- 图 1 阻力

def fig_resistance():
    sub = cf.CASE_SUBOFF.payload
    ref = rs.submerged_body_resistance(
        cf.suboff_hull(), sub["speed_ms"], appendages=rs.SUBOFF_APPENDAGES
    )
    ref_bare = rs.submerged_body_resistance(cf.suboff_hull(), sub["speed_ms"])
    live_rp = post(
        "resistance/predict",
        {
            "Lwl": sub["lwl"], "Lpp": sub["lpp"], "B": sub["b"], "T": sub["t"],
            "T_f": sub["t"], "T_a": sub["t"], "displacement": sub["displacement"],
            "Cb": sub["cb"], "Cm": sub["cm"], "Cwp": sub["cwp"], "speeds": [round(sub["speed_kn"], 3)],
        },
    )
    live_cr = post(
        "calculations/resistance",
        {
            "length": sub["lwl"], "beam": sub["b"], "draft": sub["t"],
            "displacement": sub["displacement"], "speed": round(sub["speed_kn"], 3),
            "cb": sub["cb"], "water_density": 998.2, "water_viscosity": 1.004e-6,
        },
    )
    ct_live_rp = None
    if isinstance(live_rp, list) and live_rp:
        p0 = live_rp[0]
        cf_api = rs.ittc57_cf(p0["reynolds_number"])
        ct_live_rp = p0["total_resistance"] / p0["friction_resistance"] * cf_api
    ct_live_cr = live_cr.get("ct") if isinstance(live_cr, dict) else None

    labels, vals, colors = ["Audit benchmark\n(report 1059)", "This work\n(1+k)Cf+Ca+app"], [0.0044, ref.ct], ["#888888", "#2b7bba"]
    labels.append("service /resistance/predict")
    vals.append(ct_live_rp)
    colors.append("#c0392b")
    labels.append("service /calculations/resistance")
    vals.append(ct_live_cr)
    colors.append("#e67e22")

    fig, ax = plt.subplots(1, 2, figsize=(10.5, 3.8))
    bars = ax[0].bar(range(len(vals)), vals, color=colors)
    ax[0].set_xticks(range(len(vals)))
    ax[0].set_xticklabels(labels, fontsize=7.5)
    ax[0].set_ylabel("Ct (reference area = wetted surface)")
    ax[0].set_title(f"SUBOFF AFF-8, L={sub['lwl']} m, V={sub['speed_ms']} m/s (Re={ref.re/1e6:.1f}e6)")
    for b, v in zip(bars, vals):
        if v:
            ax[0].text(b.get_x() + b.get_width() / 2, v * 1.02, f"{v:.5f}", ha="center", fontsize=7.5)
    ax[0].axhline(0.0044, ls="--", lw=1, color="#888888")

    parts = {
        "Cf (ITTC-57)": ref.cf,
        "kCf (form)": (ref.form_factor_1pk - 1.0) * ref.cf,
        "Ca (roughness)": rs.roughness_allowance(sub["lwl"]),
        "appendages CdA/S": rs.appendage_drag_area(rs.SUBOFF_APPENDAGES) / ref.surface,
    }
    bottom = 0.0
    for k, v in parts.items():
        ax[1].bar([0], [v], bottom=[bottom], label=f"{k} = {v:.5f}")
        bottom += v
    if ct_live_rp:
        ax[1].bar([1], [ct_live_rp], color="#c0392b", label=f"service Ct = {ct_live_rp:.5f}")
    if ct_live_cr:
        ax[1].bar([2], [ct_live_cr], color="#e67e22", label=f"service /calculations Ct = {ct_live_cr:.5f}")
    ax[1].set_xticks([0, 1, 2])
    ax[1].set_xticklabels(["this work", "service predict", "service calc"], fontsize=8)
    ax[1].set_ylabel("Ct")
    ax[1].set_title("Ct breakdown vs service (wave Cr applied to a body without free surface)")
    ax[1].legend(fontsize=6.5, loc="upper left")
    fig.tight_layout()
    path = os.path.join(OUT, "fig1_resistance_ct.png")
    fig.savefig(path)
    plt.close(fig)
    return path, {"ct_ref": ref.ct, "ct_ref_bare": ref_bare.ct, "ct_live_predict": ct_live_rp, "ct_live_calc": ct_live_cr}


# ---------------------------------------------------------------- 图 2 KB

def fig_kb():
    cbs = [0.5 + i * 0.02 for i in range(26)]
    analytic = [hs.kb_from_cb(10.0, cb) for cb in cbs]
    svc_eval = [10.0 * (1 - 0.5 * (1 - cb)) for cb in cbs]  # api/stability.evaluate
    svc_calc = [2.0 * 10.0 / 3.0] * len(cbs)  # api/calculations/stability (2T/3)
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.plot(cbs, analytic, "-", lw=2, label="analytic trapezoid (this work): Cb=1->T/2, Cb=0.5->2T/3")
    ax.plot(cbs, svc_eval, "--", lw=1.5, label="service /stability/evaluate: KB=T(1-0.5(1-Cb)) -> KB=T at Cb=1")
    ax.plot(cbs, svc_calc, ":", lw=1.8, label="service /calculations/stability: KB=2T/3 (wedge value on a box)")
    ax.axhline(5.0, color="k", lw=0.8, alpha=0.5)
    ax.text(0.505, 5.08, "analytic limit T/2 = 5 m (box)", fontsize=7)
    ax.set_xlabel("block coefficient Cb (T = 10 m)")
    ax.set_ylabel("KB (m)")
    ax.set_title("KB: three models compared")
    ax.legend(fontsize=7)
    fig.tight_layout()
    path = os.path.join(OUT, "fig2_kb_cb.png")
    fig.savefig(path)
    plt.close(fig)
    digits = {"kb_analytic_cb1": hs.kb_from_cb(10.0, 1.0), "kb_svc_eval_cb1": 10.0, "kb_svc_calc": 2 * 10 / 3}
    return path, digits


# ---------------------------------------------------------------- 图 3 GZ

def fig_gz():
    p = cf.CASE_BOX_A.payload
    ang = [float(a) for a in range(0, 91, 5)]
    ref = hs.evaluate_stability(p["beam"], p["draft"], p["depth"], p["cb"], p["kg"], p["cm"], angles=ang)
    bm = p["beam"] ** 2 / (12 * p["draft"] * p["cb"])
    ws = hs.wall_sided_gz(ang, ref.gm, bm)
    live = post(
        "calculations/stability",
        {k: p[k] for k in ("length", "beam", "draft", "displacement", "cb", "kg")},
    )
    gzc = post(
        "stability/gz-curve",
        {
            "L": p["length"], "B": p["beam"], "T": p["draft"], "D": p["depth"],
            "displacement": p["displacement"], "Cb": p["cb"], "Cm": p["cm"], "KG": p["kg"],
        },
    )
    fig, ax = plt.subplots(1, 2, figsize=(10.5, 3.8))
    ax[0].plot(ang, ref.gz, "-o", ms=3, lw=2, label="this work: large-angle integration")
    ax[0].plot(ang, ws, "--", lw=1.4, label="wall-sided analytic (valid theta<=45 deg)")
    if isinstance(gzc, dict) and gzc.get("gz_values"):
        ax[0].plot(gzc["angles"], gzc["gz_values"], "-s", ms=3, lw=1.3, label="service /stability/gz-curve")
    ax[0].axvline(45, color="k", lw=0.8, ls=":", alpha=0.6)
    ax[0].set_xlabel("heel angle theta (deg)")
    ax[0].set_ylabel("GZ (m)")
    ax[0].set_title(f"CASE_BOX_A: L=100 B=16 T=16 D=26 KG={p['kg']}")
    ax[0].legend(fontsize=7)

    # 30–40° 面积与 GZ_max 柱状对比
    labels = ["area 30-40 deg\n(m*rad)", "GZ_max 0-40 deg\n(m)"]
    ref_v = [hs.integrate_area(ang, ref.gz, 30, 40), hs.max_in_band(ang, ref.gz, 40)[0]]
    ws_v = [hs.integrate_area(ang, ws, 30, 40), hs.max_in_band(ang, ws, 40)[0]]
    svc_v = [
        float(live.get("area_30_40", float("nan"))),
        float(live.get("gz_max", float("nan"))),
    ]
    x = [0, 1]
    ax[1].bar([i - 0.26 for i in x], ref_v, width=0.26, label="this work")
    ax[1].bar(x, ws_v, width=0.26, label="wall-sided")
    ax[1].bar([i + 0.26 for i in x], svc_v, width=0.26, label="service /calculations")
    for i, (a, b, c) in enumerate(zip(ref_v, ws_v, svc_v)):
        ax[1].text(i + 0.26, c * 1.02, f"{c:.3f}", ha="center", fontsize=7)
    ax[1].set_xticks(x)
    ax[1].set_xticklabels(labels, fontsize=8)
    ax[1].set_title("Large-angle metrics: service KB=2T/3 -> GM 4.00 vs 1.33")
    ax[1].legend(fontsize=7)
    fig.tight_layout()
    path = os.path.join(OUT, "fig3_gz_curve.png")
    fig.savefig(path)
    plt.close(fig)
    digits = {
        "gzmax_ref": ref_v[1],
        "gzmax_ws": ws_v[1],
        "gzmax_svc": svc_v[1],
        "area3040_ref": ref_v[0],
        "area3040_ws": ws_v[0],
        "area3040_svc": svc_v[0],
        "gm_ref": ref.gm,
        "gm_svc": float(live.get("gm", float("nan"))) if isinstance(live, dict) else None,
    }
    return path, digits


# ---------------------------------------------------------------- 图 4 推进

def fig_prop():
    js = [i * 0.05 for i in range(0, 19)]
    pts = [pr.open_water(j, 4.0, 4, 1.0, 0.55) for j in js]
    kt = [q.kt for q in pts]
    kq = [q.kq for q in pts]
    eta = [q.eta0 for q in pts]
    eta_i = []
    for j, q in zip(js, pts):
        eta_i.append(pr.ideal_efficiency(8 * q.kt / (math.pi * j * j)) if j > 1e-6 else 1.0)
    stub_kt = [max(0.0, 0.3 - 0.2 * j) for j in js]
    stub_kq = [max(1e-6, 0.04 - 0.02 * j) for j in js]
    stub_eta = [j * k / (2 * math.pi * kq_) for j, k, kq_ in zip(js, stub_kt, stub_kq)]
    live = post(
        "calculations/propulsion",
        {
            "length": 100.0, "beam": 16.0, "draft": 6.0, "displacement": 6720 * 1.025,
            "speed": 14.0, "cb": 0.70, "prop_diameter": 4.0, "prop_pitch": 4.0,
            "prop_rpm": 127.0, "num_blades": 4, "blade_area_ratio": 0.55,
        },
    )
    fig, ax = plt.subplots(1, 2, figsize=(10.5, 3.8))
    ax[0].plot(js, kt, "-", lw=2, label="KT: BEMT (this work)")
    ax[0].plot(js, [10 * v for v in kq], "-", lw=2, label="10KQ: BEMT (this work)")
    ax[0].plot(js, stub_kt, "--", lw=1.2, label="KT: service line 0.3-0.2J")
    ax[0].plot(js, [10 * v for v in stub_kq], "--", lw=1.2, label="10KQ: service 0.04-0.02J")
    if isinstance(live, dict) and live.get("thrust"):
        kt_svc = live["thrust"] * 1000 / (1025.9 * (127 / 60) ** 2 * 4.0 ** 4)
        ax[0].plot([0.594], [kt_svc], "r*", ms=13, label=f"service point KT={kt_svc:.3f}")
        ax[0].plot([0.594], [kt_svc], "r*", ms=13)
    ax[0].axhspan(0.10, 0.35, color="tab:green", alpha=0.08)
    ax[0].text(0.05, 0.36, "B-series KT range 0.10-0.35", fontsize=7, color="tab:green")
    ax[0].set_xlabel("advance ratio J")
    ax[0].set_ylabel("KT / 10KQ")
    ax[0].set_title("open-water curves (Z=4, AE/A0=0.55, P/D=1.0)")
    ax[0].legend(fontsize=6.5)

    ax[1].plot(js, eta, "-", lw=2, label="eta0: BEMT (this work)")
    ax[1].plot(js, eta_i, ":", lw=1.4, label="eta_ideal = 2/(1+sqrt(1+C_T))")
    ax[1].plot(js, [min(v, 2.0) for v in stub_eta], "--", lw=1.2, label="eta0: service linear approximation")
    ax[1].axhline(0.998, color="r", lw=1.2, ls="--", label="hard bound 0.998")
    if isinstance(live, dict) and live.get("open_water_eff"):
        ax[1].plot([0.594], [live["open_water_eff"]], "r*", ms=13, label=f"service eta0={live['open_water_eff']:.3f} (out of bound)")
    ax[1].set_ylim(0, 1.9)
    ax[1].set_xlabel("advance ratio J")
    ax[1].set_ylabel("η0")
    ax[1].set_title("Open-water efficiency: service 1.545 > actuator-disk bound (audit D5)")
    ax[1].legend(fontsize=6.5)
    fig.tight_layout()
    path = os.path.join(OUT, "fig4_open_water.png")
    fig.savefig(path)
    plt.close(fig)
    digits = {
        "kt_bemt_j06": pr.open_water(0.6, 4.0, 4, 1.0, 0.55).kt,
        "eta_bemt_j06": pr.open_water(0.6, 4.0, 4, 1.0, 0.55).eta0,
        "svc_eta0": live.get("open_water_eff") if isinstance(live, dict) else None,
        "svc_thrust_kN": live.get("thrust") if isinstance(live, dict) else None,
    }
    return path, digits


if __name__ == "__main__":
    out = {}
    for fn in (fig_resistance, fig_kb, fig_gz, fig_prop):
        path, digits = fn()
        out[os.path.basename(path)] = digits
        print("wrote", path)
    with open(os.path.join(OUT, "figures_digits.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))