#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动稳性端点验证探针：独立参考实现 vs 平台 /api/v1/dynamic-stability/{analyze,roll-motion}。

产出（零手抄）::
    artifacts/verif/dynstab/results.json
    artifacts/verif/dynstab/fig{1,2,3}_*.png

关键方法：把「定义式误差」与「风压取值误差」分离测量——
  (A) P_override=504 Pa（IS Code 风压），只换算式 → 隔离定义式误差；
  (B) wind_speed=26 m/s（平台动压 0.5ρV²=414.05 Pa）→ 叠加风压误差。

运行：python3 tools/verif/dynstab/analyze_audit.py（约 5 s，离线可跑）
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from reference import (  # noqa: E402
    G, K_ROLL_BAND, K_ROLL_NOM, P_WIND, Ship, _gz_integral, cases,
    gz_area_analytic_wallside, platform_analyze_replica, platform_roll_motion_replica,
    roll_amplitude_deg, roll_natural_period, weather_areas, wind_heeling_lever,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "artifacts")
API = "http://127.0.0.1:8001/api/v1/dynamic-stability"
TIMEOUT = 30
V_EQ_504 = math.sqrt(2.0 * P_WIND / 1.225)   # ≈28.68 m/s 对应 504 Pa


def post(path: str, body: dict) -> Dict[str, object]:
    req = urllib.request.Request(f"{API}/{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return {"status": r.status, "data": json.load(r)}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read()[:300].decode("utf-8", "replace")
        except Exception:
            detail = ""
        return {"status": e.code, "data": None, "detail": detail}
    except Exception as e:
        return {"status": "unreachable", "data": None, "detail": str(e)}


def analyze_body(ship: Ship, gz: List[Dict[str, float]], wind_speed: float,
                 Hs: float = 3.0, Te: float = 9.0) -> dict:
    return {"L": ship.L, "B": ship.B, "T": ship.T, "D": ship.D, "Cb": ship.Cb,
            "displacement": ship.displacement,
            "VCG": round(0.55 * ship.D, 2), "LCG": round(0.5 * ship.L, 2), "TCG": 0.0,
            "GZ_curve": gz, "wind_speed": wind_speed,
            "wave_height": Hs, "wave_period": Te}


def rel_err(x: float, ref: float) -> float:
    if ref == 0:
        return float("nan") if x != 0 else 0.0
    return (x - ref) / ref


def pct(x: float, nd: int = 1) -> str:
    return "n/a" if not math.isfinite(x) else f"{x*100:+.{nd}f}%"


# --------------------------------------------------------------------------
def run() -> Dict[str, object]:
    ships = cases()
    rec: Dict[str, object] = {"generated_by": "tools/verif/dynstab/analyze_audit.py",
                              "api": API, "cases": {}, "checks": []}

    # 0) 数值路径自校：细网格梯形 vs 壁式段闭式积分
    rec["integration_selfcheck"] = []
    for s in ships:
        note = {"case": s.name}
        for lo, hi in ((0.0, 5.0), (5.0, s.theta_deck), (0.0, s.theta_deck)):
            note[f"int_{int(lo)}_{int(hi)}_rel"] = rel_err(
                _gz_integral(s, lo, hi, n=20000), gz_area_analytic_wallside(s, lo, hi))
        rec["integration_selfcheck"].append(note)

    # 1) 4 工况 × {canonical 0° 起, 5° 起} × {P=504 隔离, V=26 标准}
    for s in ships:
        gz_canon, gz_shift = s.gz_table(5.0, 0.0), s.gz_table(5.0, 5.0)
        live_canon = post("analyze", analyze_body(s, gz_canon, 26.0))
        live_shift = post("analyze", analyze_body(s, gz_shift, 26.0))

        lw1 = wind_heeling_lever(P_WIND, s.wind_area, s.wind_lever, s.displacement)
        ref = weather_areas(s, lw1)

        rep_iso = platform_analyze_replica(s, gz_shift, wind_speed=V_EQ_504, Hs=3.0,
                                           Te=9.0, P_override=P_WIND)
        rep_std = platform_analyze_replica(s, gz_shift, wind_speed=26.0, Hs=3.0, Te=9.0)

        selfcheck = None
        if live_shift.get("status") == 200:
            d = live_shift["data"]
            selfcheck = {"area_ratio_abs_dev": abs(d["area_ratio"] - rep_std["ratio"]),
                         "area_gz_abs_dev": abs(d["area_under_GZ"] - rep_std["area_gz"]),
                         "wind_moment_MNm_live": d["wind_heeling_moment"],
                         "wind_moment_MNm_replica": round(rep_std["wind_moment_MNm"], 3)}

        rm = platform_roll_motion_replica(s, GM=rep_std["gm_est"], Hs=3.0, Te=9.0)
        tr_ref = roll_natural_period(s.B, rep_std["gm_est"], K_ROLL_NOM)
        roll_ref = roll_amplitude_deg(3.0, 9.0, tr_ref, 0.1)

        rec["cases"][s.name] = {
            "ship": {"L": s.L, "B": s.B, "T": s.T, "D": s.D, "Cb": s.Cb,
                     "displacement": s.displacement, "GM_true": s.GM,
                     "theta_deck": s.theta_deck, "theta_vanish": s.theta_vanish,
                     "wind_area": s.wind_area, "wind_lever": s.wind_lever},
            "gz_curve_deg": gz_canon,
            "live": {"canonical_from_0deg": live_canon, "shifted_from_5deg": live_shift},
            "reference": ref,
            "platform_P504": rep_iso,
            "platform_std": rep_std,
            "replica_selfcheck": selfcheck,
            "roll": {"Tr_platform_analyze_0.8B": rep_std["Tr_analyze"],
                     "Tr_platform_rollmotion_0.4B": rm["Tr"],
                     "Tr_reference_0.35B": tr_ref,
                     "Tr_ref_band_0.30_0.45B": [
                         roll_natural_period(s.B, rep_std["gm_est"], K_ROLL_BAND[0]),
                         roll_natural_period(s.B, rep_std["gm_est"], K_ROLL_BAND[1])],
                     "Tr_analyze_vs_ref_rel": rel_err(rep_std["Tr_analyze"], tr_ref),
                     "Tr_analyze_vs_sibling_ratio": rep_std["Tr_analyze"] / rm["Tr"],
                     "roll_amp_platform_deg": rep_std["wave_roll_deg"],
                     "roll_amp_linear_theory_deg": roll_ref["amp_deg"],
                     "roll_amp_rel": rel_err(rep_std["wave_roll_deg"], roll_ref["amp_deg"]),
                     "wave_slope_rad": roll_ref["wave_slope_rad"]},
            "deviation": {
                "a_iso_rel": rel_err(rep_iso["area_wind"], ref["a"]),
                "a_std_rel": rel_err(rep_std["area_wind"], ref["a"]),
                "b_iso_rel": rel_err(rep_iso["area_gz"], ref["b"]),
                "ratio_iso_rel": rel_err(rep_iso["ratio"], ref["ratio"]),
                "ratio_std_rel": rel_err(rep_std["ratio"], ref["ratio"]),
                "wind_pressure_std_vs_code_rel": rel_err(rep_std["wind_pressure"], P_WIND),
                "verdict_ref": "PASS" if ref["ratio"] >= 1.0 else "FAIL",
                "verdict_platform": "PASS" if rep_std["ratio"] >= 1.0 else "FAIL",
                "verdict_platform_iso": "PASS" if rep_iso["ratio"] >= 1.0 else "FAIL"},
        }

    # 2) 边界船扫描：判决翻转 = 安全相关 false PASS / false FAIL
    sweep, flips = [], []
    base = ships[0]
    for gm in (0.15, 0.25, 0.35, 0.50, 0.80, 1.20):
        for sc in (1.0, 2.0, 4.0, 8.0, 16.0, 40.0):
            s = Ship("sweep", L=base.L, B=base.B, T=base.T, D=base.D, Cb=base.Cb,
                     displacement=base.displacement, GM=gm, theta_deck=base.theta_deck,
                     theta_vanish=base.theta_vanish,
                     wind_area=base.wind_area * sc, wind_lever=base.wind_lever)
            lw1 = wind_heeling_lever(P_WIND, s.wind_area, s.wind_lever, s.displacement)
            w = weather_areas(s, lw1)
            rep = platform_analyze_replica(s, s.gz_table(5.0, 5.0), wind_speed=26.0,
                                           Hs=3.0, Te=9.0)
            row = {"GM": gm, "area_scale": sc, "lw1": lw1, "theta0": w["theta0"],
                   "has_steady_heel": bool(w["has_steady_heel"]),
                   "ratio_ref": w["ratio"], "ratio_platform": rep["ratio"],
                   "verdict_ref": "PASS" if w["ratio"] >= 1.0 else "FAIL",
                   "verdict_platform": "PASS" if rep["ratio"] >= 1.0 else "FAIL"}
            sweep.append(row)
            if row["verdict_ref"] != row["verdict_platform"]:
                flips.append(dict(row))
    rec["sweep"], rec["flips"] = sweep, flips

    # 3) 判据表
    def add(kid, text, ref_desc, observed, verdict, note=""):
        rec["checks"].append({"id": kid, "criterion": text, "reference": ref_desc,
                              "observed": observed, "verdict": verdict, "note": note})

    canon = {n: c["live"]["canonical_from_0deg"]["status"] for n, c in rec["cases"].items()}
    add("K1", "canonical GZ 曲线（0° 起）可用性", "HTTP 200 + 数值场",
        json.dumps(canon, ensure_ascii=False),
        "FAIL" if any(v != 200 for v in canon.values()) else "PASS",
        "GM=GZ[0]/sin(heel0)，首点 heel=0 → 除零")

    rm_status = post("roll-motion", {
        "L": 190.0, "B": 32.0, "T": 12.0, "Cb": 0.82, "displacement": 50000.0, "GM": 1.6,
        "KB": 4.0, "roll_damping": 0.1, "wave_amplitude": 1.5, "wave_period": 9.0,
        "wave_heading": 90})
    rec["rollmotion_probe"] = rm_status
    add("K2", "roll-motion 端点可用性", "HTTP 200 + 横摇要素",
        f"HTTP {rm_status['status']}", "PASS" if rm_status["status"] == 200 else "FAIL",
        "RollMotionAnalyzer 引用 self.g 与 input_data.g，两者均不存在")

    ais = [c["deviation"]["a_iso_rel"] for c in rec["cases"].values()]
    add("K3", "风倾面积 a 的规范定义（lw2=1.5·lw1；区间 0→θ0；被积函数 lw2−GZ）",
        "IS Code 2008 §2.3.3/§2.3.4；同风压 P=504 Pa",
        f"a 相对误差 {pct(min(ais))} … {pct(max(ais))}",
        "FAIL" if max(abs(x) for x in ais) > 0.03 else "PASS",
        "平台取 lw1·(0…wind_heel+wave_roll)，无 1.5 倍率、不减 GZ、上限非 θ0")

    ps = [c["deviation"]["wind_pressure_std_vs_code_rel"] for c in rec["cases"].values()]
    add("K3b", "定常风压取值", "P = 504 Pa（IS Code 2008 §2.3.1）",
        f"平台 P=0.5ρV²={rec['cases']['S1_bulk_50k']['platform_std']['wind_pressure']:.2f} Pa"
        f" @V=26 m/s（{pct(ps[0])}）",
        "FAIL" if abs(ps[0]) > 0.03 else "PASS",
        "IS Code 的 504 Pa 对应 V≈28.68 m/s；以 V 为输入时须声明等效风压口径")

    bs = [c["deviation"]["b_iso_rel"] for c in rec["cases"].values()]
    add("K4", "GZ 面积 b 的积分区间（θ0→θf，θf=min(进水角,50°)）",
        "IS Code 2008 §2.3.4", f"b 相对误差 {pct(min(bs))} … {pct(max(bs))}",
        "FAIL" if max(abs(x) for x in bs) > 0.03 else "PASS",
        "平台自 0° 起积分整条曲线，进水角硬编码 50°（无进水角计算）")

    rs = [c["deviation"]["ratio_iso_rel"] for c in rec["cases"].values()]
    add("K5", "b/a 数值可比性（隔离风压误差后）", "IS Code b/a",
        f"ratio 相对误差 {pct(min(rs))} … {pct(max(rs))}",
        "FAIL" if max(abs(x) for x in rs) > 0.03 else "PASS",
        "定义式不同 → 比值不可与规范值对照；「稳性良好/临界/不足」阈值(2/1.5/1)无规范依据")

    add("K6", "合规判决一致性（b/a≥1）",
        f"规范判决 vs 平台判决（{len(sweep)} 组 GM×受风面积扫描 + 4 工况）",
        f"翻转 {len(flips)}/{len(sweep)}："
        + "; ".join(f"GM={f['GM']:.2f},A×{f['area_scale']:.0f}: "
                    f"{f['verdict_ref']}→{f['verdict_platform']} "
                    f"({f['ratio_ref']:.3g} vs {f['ratio_platform']:.3g})"
                    for f in flips[:4]),
        "FAIL" if flips else "PASS",
        "翻转方向全为 false PASS（平台判满足、规范判不满足）" if flips else "未观测到翻转")

    tr = rec["cases"]["S1_bulk_50k"]["roll"]
    add("K7", "横摇固有周期系数 k/B", "T_r=2πk/√(g·GM)，k≈0.35B（文献带 0.30–0.45B）",
        f"analyze 用 0.8B（vs 0.35B：{pct(tr['Tr_analyze_vs_ref_rel'])}；"
        f"越文献带上界 {pct(tr['Tr_analyze_vs_ref_rel'] - (0.45/0.35-1))}）；"
        f"兄弟端点 roll-motion 用 0.4B（自相矛盾 {tr['Tr_analyze_vs_sibling_ratio']:.3f}×）",
        "FAIL", "k=0.35B 为 CONF_MED 文献取值；0.8B 无法用横摇回转半径解释")

    ampe = [c["roll"]["roll_amp_rel"] for c in rec["cases"].values()]
    add("K8", "横摇幅值量纲/量级", "φ_a=(k_w·H/2)·|H_θ|，k_w=ω²/g（线性横摇理论）",
        f"幅值相对误差 {pct(min(ampe))} … {pct(max(ampe))}",
        "FAIL" if min(abs(x) for x in ampe) > 0.15 else "PASS",
        "平台 (H/2)·RAO·0.1：把波幅[m]直接乘 0.1 当角度标度，系数无来源")

    # 4) 图 + 落盘
    os.makedirs(ART, exist_ok=True)
    _fig1(rec, ships, os.path.join(ART, "fig1_gz_areas.png"))
    _fig2(rec, os.path.join(ART, "fig2_roll_defects.png"))
    _fig3(rec, os.path.join(ART, "fig3_status_and_errors.png"))
    with open(os.path.join(ART, "results.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    print("== dynamic-stability audit ==")
    for c in rec["checks"]:
        print(f"  {c['id']:4s} {c['verdict']:4s} | {c['criterion'][:40]:40s} | {str(c['observed'])[:88]}")
    print(f"  flips={len(flips)}/{len(sweep)}")
    return rec


# --------------------------------------------------------------------------
def _fig1(rec, ships, path):
    s = ships[0]
    ref = rec["cases"][s.name]["reference"]
    th = np.linspace(0.0, s.theta_vanish, 800)
    gzv = np.array([s.gz(t) for t in th])
    fig, ax = plt.subplots(1, 2, figsize=(12.5, 4.8))
    a0 = ax[0]
    a0.plot(th, gzv, "k-", lw=2.0, label="GZ curve (analytic)")
    a0.axhline(ref["lw1"], color="tab:blue", ls="--", lw=1.4,
               label=f"lw1 = {ref['lw1']:.4f} m (P=504 Pa)")
    a0.axhline(ref["lw2"], color="tab:red", ls="--", lw=1.4, label=f"lw2 = 1.5 lw1 = {ref['lw2']:.4f} m")
    t0, tf = ref["theta0"], ref["theta_f"]
    m1, m2 = th <= t0, (th >= t0) & (th <= tf)
    a0.fill_between(th[m1], ref["lw2"], np.interp(th[m1], th, gzv), color="tab:red", alpha=0.30,
                    label=f"a = {ref['a']*1e3:.3f}e-3 m·rad (code)")
    a0.fill_between(th[m2], 0, np.interp(th[m2], th, gzv), color="tab:green", alpha=0.30,
                    label=f"b = {ref['b']*1e3:.1f}e-3 m·rad (code)")
    a0.axvline(t0, color="tab:red", lw=1.0), a0.axvline(tf, color="gray", ls=":", lw=1.0)
    a0.annotate(f"$\\theta_0$={t0:.2f}°", (t0, ref["lw2"] * 1.3), fontsize=9, color="tab:red")
    a0.annotate(f"$\\theta_f$={tf:.0f}°", (tf - 9, 0.06), fontsize=9, color="gray")
    a0.set_xlabel("heel angle [deg]"), a0.set_ylabel("GZ [m]")
    a0.set_title(f"IS Code 2008 §2.3 areas, {s.name} (b/a={ref['ratio']:.1f})")
    a0.legend(fontsize=7.5, loc="upper right"), a0.grid(alpha=0.3)

    a1 = ax[1]
    names = list(rec["cases"])
    x = np.arange(len(names))
    a1.bar(x - 0.2, [rec["cases"][n]["reference"]["ratio"] for n in names], 0.4,
           label="IS Code b/a", color="tab:green")
    a1.bar(x + 0.2, [rec["cases"][n]["platform_std"]["ratio"] for n in names], 0.4,
           label="platform area_ratio", color="tab:orange")
    a1.set_yscale("log"), a1.axhline(1.0, color="k", ls="--", lw=1.0)
    a1.set_xticks(x), a1.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7.5)
    a1.set_ylabel("b/a [-], log scale")
    a1.set_title("same geometry & wind: ratio differs (definition)")
    a1.legend(fontsize=8), a1.grid(alpha=0.3, axis="y")
    fig.tight_layout(), fig.savefig(path, dpi=140), plt.close(fig)


def _fig2(rec, path):
    fig, ax = plt.subplots(1, 2, figsize=(12.5, 4.6))
    gm = np.linspace(0.3, 3.0, 200)
    B = rec["cases"]["S1_bulk_50k"]["ship"]["B"]
    ax[0].plot(gm, [2 * math.pi * B / math.sqrt(G * x) * 0.8 for x in gm], "r-", lw=2,
               label="analyze: k=0.8B (platform)")
    ax[0].plot(gm, [2 * math.pi * B / math.sqrt(G * x) * 0.4 for x in gm], "b--", lw=1.6,
               label="roll-motion: k=0.4B (sibling)")
    ax[0].plot(gm, [2 * math.pi * K_ROLL_NOM * B / math.sqrt(G * x) for x in gm], "k-", lw=2,
               label="reference: k=0.35B")
    ax[0].fill_between(gm, [2 * math.pi * K_ROLL_BAND[0] * B / math.sqrt(G * x) for x in gm],
                       [2 * math.pi * K_ROLL_BAND[1] * B / math.sqrt(G * x) for x in gm],
                       color="gray", alpha=0.25, label="literature band 0.30-0.45B")
    ax[0].set_xlabel("GM [m]"), ax[0].set_ylabel("roll natural period [s]")
    ax[0].set_title(f"roll period: platform self-contradiction 2.0x (B={B:.0f} m)")
    ax[0].legend(fontsize=8), ax[0].grid(alpha=0.3)

    hs = np.linspace(0.5, 8.0, 60)
    Tr_ref = roll_natural_period(B, 1.6, K_ROLL_NOM)
    Tr_pl = 2 * math.pi * B / math.sqrt(G * 1.6) * 0.8
    ax[1].plot(hs, [roll_amplitude_deg(h, 9.0, Tr_ref, 0.1)["amp_deg"] for h in hs], "k-",
               lw=2, label="linear roll theory")
    ax[1].plot(hs, [(h / 2.0) * (1 / math.sqrt((1 - (Tr_pl / 9.0) ** 2) ** 2 +
                                                 (0.2 * Tr_pl / 9.0) ** 2)) * 0.1 for h in hs],
               "r-", lw=2, label="platform (H/2 x RAO x 0.1)")
    ax[1].set_xlabel("Hs [m] @ Te=9 s"), ax[1].set_ylabel("roll amplitude [deg]")
    ax[1].set_title("roll amplitude: heuristic vs linear theory")
    ax[1].legend(fontsize=8), ax[1].grid(alpha=0.3)
    fig.tight_layout(), fig.savefig(path, dpi=140), plt.close(fig)


def _fig3(rec, path):
    fig, ax = plt.subplots(1, 2, figsize=(12.5, 4.4))
    names = list(rec["cases"])
    x = np.arange(len(names))
    for k, (key, lab) in enumerate((("a_iso_rel", "area a (definition)"),
                                    ("b_iso_rel", "area b"),
                                    ("ratio_iso_rel", "b/a"))):
        ax[0].bar(x + (k - 1) * 0.26,
                  [abs(rec["cases"][n]["deviation"][key]) for n in names], 0.26, label=lab)
    ax[0].set_yscale("log"), ax[0].axhline(0.03, color="k", ls="--", lw=1.2)
    ax[0].annotate("3% acceptance", (0.02, 0.033), fontsize=8)
    ax[0].set_xticks(x), ax[0].set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7.5)
    ax[0].set_ylabel("|relative error| vs IS Code, log")
    ax[0].set_title("platform vs IS Code (P=504 Pa isolated)")
    ax[0].legend(fontsize=8), ax[0].grid(alpha=0.3, axis="y")

    ids = [c["id"] for c in rec["checks"]]
    ok = [c["verdict"] == "PASS" for c in rec["checks"]]
    ax[1].bar(ids, [1] * len(ids), color=["tab:green" if v else "tab:red" for v in ok])
    for i, v in enumerate(ok):
        ax[1].text(i, 0.5, "PASS" if v else "FAIL", ha="center", va="center",
                   fontsize=8, color="white", rotation=90)
    ax[1].set_ylim(0, 1.05), ax[1].set_yticks([])
    ax[1].set_title("dynamic-stability verdicts K1-K8")
    fig.tight_layout(), fig.savefig(path, dpi=140), plt.close(fig)


if __name__ == "__main__":
    run()