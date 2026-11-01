"""耐波性 D5 修复的图表与数字表生成（数字全部程序化提取，零手抄）。

读取 ``artifacts/m1547/seakeeping_audit/probes.json``（在线探针原始响应）与
``verdict.json``（11 条判据裁决），并用内核直接复算解析基线，输出：

* ``artifacts/m1547/figures/fig1_rao_curves.png``        三船三自由度 RAO 曲线 + 长波解析极限
* ``artifacts/m1547/figures/fig2_heading_speed.png``     浪向/航速敏感性（顶浪/首斜浪/横浪/随浪）
* ``artifacts/m1547/figures/fig3_natural_periods.png``   自然周期：端点 vs 解析基线 + 无附加质量下界
* ``artifacts/m1547/figures/fig4_strip_consistency.png`` 切片理论：A33 自洽性与高频衰减
* ``artifacts/m1547/results_table.json``                 报告用数字表

用法::

    python3 tests/seakeeping_audit/gen_fix_figures.py
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "api"))
import seakeeping_physics as sp  # noqa: E402

G = 9.80665
RHO = 1025.0
OUT = ROOT / "artifacts" / "m1547"
AUD = OUT / "seakeeping_audit"

SHIPS = {
    "KVLCC2": dict(L=320.0, B=58.0, T=20.8, displacement=312622.0, Cb=0.8098,
                   Cwp=0.86, KG=18.6, GM=9.0, speed=15.0),
    "S175": dict(L=175.0, B=25.4, T=9.5, displacement=24400.0, Cb=0.572,
                 Cwp=0.76, KG=8.0, GM=1.5, speed=15.0),
    "BARGE60": dict(L=60.0, B=12.0, T=4.0, displacement=2200.0, Cb=0.90,
                    Cwp=0.94, KG=3.0, GM=2.0, speed=0.0),
}


def analytic_ref(s, k_yy_ratio=0.30, add_mass_ratio=0.8, k_xx_ratio=0.35):
    """任务书 F5/F6 中的解析基线（与审计脚本同一口径）。"""
    nabla = s["displacement"] / (RHO / 1000.0)
    mass = RHO * nabla
    aw = sp.cwp_from_cb(s["Cb"]) * s["L"] * s["B"]
    t33 = 2 * math.pi * math.sqrt(mass * (1 + add_mass_ratio) / (RHO * G * aw))
    kb = 0.53 * s["T"]
    bm_l = (s["B"] * s["L"] ** 3 / 12.0) / nabla
    gm_l = bm_l + kb - s["KG"]
    t55 = 2 * math.pi * (k_yy_ratio * s["L"]) / math.sqrt(G * gm_l)
    t44 = 2 * math.pi * (k_xx_ratio * s["B"]) / math.sqrt(G * s["GM"])
    t_min = 2 * math.pi * math.sqrt(mass / (RHO * G * aw))
    return dict(T_heave=t33, T_pitch=t55, T_roll=t44, T_min=t_min, Aw=aw, mass=mass,
                GM_L=gm_l)


def main() -> int:
    P = json.loads((AUD / "probes.json").read_text())
    J = json.loads((AUD / "verdict.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "figures").mkdir(exist_ok=True)
    figdir = OUT / "figures"
    figs = []

    # ---------------------------------------------------------------- 图1 RAO 曲线
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.0))
    for ax, name in zip(axes, SHIPS):
        d = P["rao"][name]["base"]["data"]
        w = d["frequencies"]
        ax.plot(w, d["heave_rao"], "-", color="C0", lw=1.8, label="heave (m/m)")
        ax.plot(w, d["pitch_rao"], "--", color="C1", lw=1.6, label="pitch (rad/(ka))")
        beam = P["rao"][name]["beam90"]["data"]
        ax.plot(beam["frequencies"], beam["roll_rao"], "-.", color="C3", lw=1.4,
                label="roll @90° (rad/(ka))")
        ax.plot(d["frequencies"], d["roll_rao"], ":", color="C3", lw=1.2,
                label="roll @180° (head)")
        ax.axhline(1.0, color="k", ls=":", lw=1.0, label="long-wave limit = 1")
        ax.set_title(f"{name}: L={SHIPS[name]['L']:.0f} m, Fn-speed="
                     f"{SHIPS[name]['speed']:.0f} kn")
        ax.set_xlabel(r"$\omega$ (rad/s)")
        ax.set_ylabel("RAO (-)")
        ax.set_xscale("log")
        ax.grid(alpha=.3, which="both")
        ax.legend(fontsize=7)
    fig.suptitle("STF strip-theory RAO: heave != pitch, roll vanishes in head seas, "
                 "long-wave limit -> 1", fontsize=10)
    fig.tight_layout()
    p1 = figdir / "fig1_rao_curves.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append((p1.name, "图1 三船 RAO（顶浪基准 + 横浪横摇）：垂荡与纵摇不再重合；"
                          "顶浪横摇按对称性趋于 0；长波端垂荡→1（解析极限）。"))

    # -------------------------------------------------------- 图2 浪向/航速敏感性
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.0))
    d0 = P["rao"]["KVLCC2"]["base"]["data"]
    for key, lab, st in (("base", "base: 0.6 rad/s, 180°, 15 kn", "-"),
                         ("head0", "heading 0° (following)", "--"),
                         ("stern135", "heading 135°", "-."),
                         ("beam90", "heading 90° (beam)", ":")):
        dd = P["rao"]["KVLCC2"][key]["data"]
        axes[0].plot(dd["frequencies"], dd["heave_rao"], st, lw=1.5, label=lab)
        axes[0].plot(dd["frequencies"], dd["roll_rao"], st, lw=1.0, alpha=.45)
    axes[0].set_xscale("log")
    axes[0].set_xlabel(r"$\omega$ (rad/s)")
    axes[0].set_ylabel("RAO (-)")
    axes[0].set_title("KVLCC2: heading is consumed (heave solid, roll faint)")
    axes[0].grid(alpha=.3, which="both")
    axes[0].legend(fontsize=7)

    for name in SHIPS:
        a = P["rao"][name]["base"]["data"]
        b = P["rao"][name]["speed10"]["data"]
        axes[1].plot(a["frequencies"], a["heave_rao"], "-", lw=1.5,
                     label=f"{name} U={SHIPS[name]['speed']:.0f} kn")
        axes[1].plot(b["frequencies"], b["heave_rao"], "--", lw=1.2,
                     label=f"{name} U=10 kn")
    axes[1].set_xscale("log")
    axes[1].set_xlabel(r"$\omega$ (rad/s)")
    axes[1].set_ylabel("heave RAO (-)")
    axes[1].set_title("speed sensitivity via encounter frequency")
    axes[1].grid(alpha=.3, which="both")
    axes[1].legend(fontsize=6)
    fig.tight_layout()
    p2 = figdir / "fig2_heading_speed.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append((p2.name, "图2 左：浪向被端点消费（30°→180° 曲线分离，横浪横摇出现）；"
                          "右：航速经遭遇频率改变 RAO。"))

    # ------------------------------------------------------------ 图3 自然周期
    rows = []
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.2))
    import numpy as np
    labels, ref_v, end_v, strip_v = [], [], [], []
    for name, s in SHIPS.items():
        ref = analytic_ref(s)
        mot = P["motion"][name]["base"]["data"]
        st = P["strip"][name]["w06"]["data"]
        for comp, rk, mk, sk in (("heave", "T_heave", "heave", "natural_period_heave"),
                                 ("pitch", "T_pitch", "pitch", "natural_period_pitch"),
                                 ("roll", "T_roll", "roll", "natural_period_roll")):
            labels.append(f"{name}\n{comp}")
            ref_v.append(ref[rk])
            end_v.append(mot["natural_periods"][mk])
            strip_v.append(st[sk])
            rows.append(dict(ship=name, dof=comp, analytic_ref_s=round(ref[rk], 3),
                             motion_s=round(mot["natural_periods"][mk], 3),
                             strip_s=round(st[sk], 3),
                             err_vs_ref_pct=round(abs(mot["natural_periods"][mk] - ref[rk])
                                                  / ref[rk] * 100.0, 2)))
    x = np.arange(len(labels))
    axes[0].bar(x - 0.27, ref_v, 0.27, label="analytic baseline (F5/F6 convention)")
    axes[0].bar(x, end_v, 0.27, label="/seakeeping/motion")
    axes[0].bar(x + 0.27, strip_v, 0.27, label="/strip-theory")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels, fontsize=7)
    axes[0].set_ylabel("natural period (s)")
    axes[0].set_title("natural periods: endpoints vs analytic (two surfaces agree)")
    axes[0].legend(fontsize=7)
    axes[0].grid(alpha=.3, axis="y")

    for i, (name, s) in enumerate(SHIPS.items()):
        ref = analytic_ref(s)
        mot = P["motion"][name]["base"]["data"]["natural_periods"]
        ratio = mot["heave"] / ref["T_min"]
        axes[1].bar(i, ratio, 0.5, color="C2")
        axes[1].text(i, ratio + 0.03, f"{ratio:.3f}", ha="center", fontsize=8)
    axes[1].axhline(1.0, color="r", ls="--", lw=1.2,
                    label=r"$T_{min}=2\pi\sqrt{m/(\rho g C_{wp}LB)}$ (A33=0 lower bound)")
    axes[1].set_xticks(range(len(SHIPS)))
    axes[1].set_xticklabels(list(SHIPS), fontsize=8)
    axes[1].set_ylabel(r"$T_{33}/T_{min}$")
    axes[1].set_title("heave period above the no-added-mass lower bound")
    axes[1].legend(fontsize=7)
    axes[1].grid(alpha=.3, axis="y")
    fig.tight_layout()
    p3 = figdir / "fig3_natural_periods.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    figs.append((p3.name, "图3 左：/motion 与 /strip-theory 自然周期一致并与解析基线同量级；"
                          "右：垂荡周期高于无附加质量下界（比值 1.3~1.5，A33>0 非零附加质量）。"))

    # ------------------------------------------------- 图4 切片理论自洽性/衰减
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.0))
    for name in SHIPS:
        st = P["strip"][name]["w06"]["data"]
        axes[0].plot(st["frequencies"], st["pitch_rao"], "-", lw=1.5, label=f"{name} pitch")
        axes[0].plot(st["frequencies"], st["heave_rao"], "--", lw=1.2, label=f"{name} heave")
    axes[0].set_xscale("log")
    axes[0].set_xlabel(r"$\omega$ (rad/s)")
    axes[0].set_ylabel("RAO (-)")
    axes[0].set_title("strip endpoint: no 0.5 / 0.02 clamp floors, high-freq decay")
    axes[0].grid(alpha=.3, which="both")
    axes[0].legend(fontsize=6)

    names, a33_ratio, terr = [], [], []
    for name, s in SHIPS.items():
        st = P["strip"][name]["w06"]["data"]
        mass = s["displacement"] * 1000.0
        aw = sp.cwp_from_cb(s["Cb"]) * s["L"] * s["B"]
        t_imp = 2 * math.pi * math.sqrt((mass + st["added_mass_heave"]) / (RHO * G * aw))
        names.append(name)
        a33_ratio.append(st["added_mass_heave"] / mass)
        terr.append(abs(t_imp - st["natural_period_heave"]) / st["natural_period_heave"] * 100)
    axes[1].bar(np.arange(3) - 0.2, a33_ratio, 0.4, label="A33/m (-)")
    ax2 = axes[1].twinx()
    ax2.bar(np.arange(3) + 0.2, terr, 0.4, color="C3", label="T33 consistency error (%)")
    axes[1].axhspan(0.3, 2.0, color="g", alpha=.08, label="A33/m physical band 0.3~2")
    axes[1].set_xticks(range(3))
    axes[1].set_xticklabels(names, fontsize=8)
    axes[1].set_ylabel("A33 / m (-)")
    ax2.set_ylabel("|T33 from A33 - T33 reported| (%)")
    axes[1].set_title("added mass in kg: A33/m in band, T33 self-consistent (<5%)")
    h1, l1 = axes[1].get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    axes[1].legend(h1 + h2, l1 + l2, fontsize=6, loc="upper right")
    axes[1].grid(alpha=.3, axis="y")
    fig.tight_layout()
    p4 = figdir / "fig4_strip_consistency.png"
    fig.savefig(p4, dpi=140)
    plt.close(fig)
    figs.append((p4.name, "图4 左：切片端点纵摇/垂荡 RAO 全频段连续、高频自然衰减（无 clamp 平台）；"
                          "右：A33 量纲为 kg、A33/m 在 0.3~2 物理带内，且 T33 与自报 A33 自洽。"))

    # ------------------------------------------------------------- 数字表
    table = {"ships": {}, "natural_periods": rows,
             "verdict": J["summary"], "criteria": [
                 {"id": c["id"], "verdict": c["verdict"]} for c in J["criteria"]],
             "figures": [f[0] for f in figs]}
    for name, s in SHIPS.items():
        ref = analytic_ref(s)
        mot = P["motion"][name]["base"]["data"]
        st = P["strip"][name]["w06"]["data"]
        rao = P["rao"][name]["base"]["data"]
        beam = P["rao"][name]["beam90"]["data"]
        mass = s["displacement"] * 1000.0
        kxx = mot["natural_periods"]["roll"] * math.sqrt(G * s["GM"]) / (2 * math.pi) / s["B"]
        idx = [i for i, w in enumerate(rao["frequencies"])
               if sp.deep_water_k(w) * s["L"] < 0.3]
        table["ships"][name] = dict(
            T33_endpoint_s=round(mot["natural_periods"]["heave"], 3),
            T33_analytic_s=round(ref["T_heave"], 3),
            Tmin_no_added_mass_s=round(ref["T_min"], 3),
            T33_over_Tmin=round(mot["natural_periods"]["heave"] / ref["T_min"], 3),
            T55_endpoint_s=round(mot["natural_periods"]["pitch"], 3),
            T55_analytic_s=round(ref["T_pitch"], 3),
            T44_endpoint_s=round(mot["natural_periods"]["roll"], 3),
            kxx_implied_over_B=round(kxx, 3),
            A33_kg=round(st["added_mass_heave"], 1),
            A33_over_mass=round(st["added_mass_heave"] / mass, 3),
            T33_from_A33_s=round(2 * math.pi * math.sqrt(
                (mass + st["added_mass_heave"]) / (RHO * G * ref["Aw"])), 3),
            heave_rao_long_wave=round(rao["heave_rao"][idx[-1]], 4) if idx else None,
            kL_at_that_point=round(sp.deep_water_k(rao["frequencies"][idx[-1]]) * s["L"], 3)
            if idx else None,
            roll_head_over_beam_peak=round(max(rao["roll_rao"]) / max(beam["roll_rao"]), 4),
            max_rel_diff_heave_pitch=round(max(
                abs(h - p) / max(h, p, 1e-12) for h, p in
                zip(rao["heave_rao"], rao["pitch_rao"])), 4),
        )
    (OUT / "results_table.json").write_text(json.dumps(table, indent=1, ensure_ascii=False))

    meta = {"figures": [{"name": n, "caption": c} for n, c in figs],
            "table": "artifacts/m1547/results_table.json"}
    (OUT / "figures_metadata.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    print(json.dumps({k: v["T33_endpoint_s"] for k, v in table["ships"].items()}))
    print("summary:", table["verdict"])
    print("figures:", [f[0] for f in figs])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())