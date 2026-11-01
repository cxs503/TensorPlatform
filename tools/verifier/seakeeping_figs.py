"""由 runs/m1534_sk/seakeeping_audit.json 程序化生成图与数字表（零手抄）。

产物：
  runs/m1534_sk/figs/fig1_rao_vs_period.png
  runs/m1534_sk/figs/fig2_heading_polar.png
  runs/m1534_sk/figs/fig3_heave_limits.png
  docs/verifier/SEAKEEPING_TABLES.md   （全部数字由本脚本注入）

用法： python tools/verifier/seakeeping_figs.py
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seakeeping_reference as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUDIT = os.path.join(ROOT, "runs", "m1534_sk", "seakeeping_audit.json")
FIGDIR = os.path.join(ROOT, "runs", "m1534_sk", "figs")
TABLES = os.path.join(ROOT, "docs", "verifier", "SEAKEEPING_TABLES.md")


def load():
    with open(AUDIT) as fh:
        return json.load(fh)


def fig1(audit):
    c2 = next(c for c in audit["criteria"] if c["criterion"].startswith("K2"))
    c3 = next(c for c in audit["criteria"] if c["criterion"].startswith("K3"))
    c4 = next(c for c in audit["criteria"] if c["criterion"].startswith("K4"))
    ms = audit["mother_ship"]
    hull = R.Hull(ms["length"], ms["beam"], ms["draft"], ms["displacement"], ms["cb"])
    t4 = c3["platform_reported_T4"]
    wn4 = R.TWO_PI / t4

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    panels = [
        ("heave_rao_max", "heave RAO [-]", lambda T: hull.heave_rao(T), c2["rows_heave"] if "rows_heave" in c2 else None),
        ("roll_rao_max", "roll RAO [-] (beam seas)", lambda T: R.roll_rao(R.circular_frequency(T), wn4, 0.10), None),
        ("pitch_rao_max", "pitch RAO [deg/m] (head seas)", lambda T: hull.pitch_rao(T) * 57.29578, None),
    ]
    for ax, (key, label, ref_fn, _unused) in zip(axes, panels):
        T = list(c2["sweep_T"])
        vals = c2["sweep_" + key.split("_")[0]]  # sweep_heave / sweep_roll / sweep_pitch
        ax.plot(T, vals, "o-", lw=2, label="platform /calculations/seakeeping")
        Tf = [0.5 + i * 0.1 for i in range(1, 300)]
        ax.plot(Tf, [ref_fn(t) for t in Tf], "-", lw=2, label="independent reference kernel")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("wave period T [s] (log)")
        ax.set_ylabel(label)
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=8)
        sec = ax.secondary_xaxis("top", functions=(lambda t: 9.80665 * t * t / (2 * 3.141592653589793 * ms["length"]),
                                                  lambda l: (2 * 3.141592653589793 * ms["length"] * l / 9.80665) ** 0.5))
        sec.set_xlabel("lambda / L", fontsize=8)
    axes[0].set_title("heave: platform constant vs two exact limits", fontsize=10)
    axes[1].set_title("roll (beam seas): no resonance structure", fontsize=10)
    axes[2].set_title("pitch (head seas): no peak at lambda/L~1", fontsize=10)
    fig.suptitle("Seakeeping D5: platform RAOs vs independent reference (mother ship L=180m)", fontsize=11)
    fig.tight_layout()
    out = os.path.join(FIGDIR, "fig1_rao_vs_period.png")
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def fig2(audit):
    k5 = next(c for c in audit["criteria"] if c["criterion"].startswith("K5"))
    rows = sorted(k5["platform_rows"], key=lambda r: r["heading"])
    head = [r["heading"] for r in rows]
    plat = [r["roll_rao_max"] for r in rows]
    shape = R.heading_roll_factor
    base = max(plat)
    fig = plt.figure(figsize=(9, 4.2))
    ax = fig.add_subplot(111, projection="polar")
    ax.plot([h * 3.141592653589793 / 180 for h in head], plat, "o-", lw=2, label="platform roll RAO (T=9s)")
    ax.plot([h * 3.141592653589793 / 180 for h in head],
            [base * shape(h) for h in head], "--", label="|sin(beta)| shape (quantity right, magnitude flat)")
    ax.set_title("roll heading dependence: shape matches |sin beta|, no frequency/encounter coupling", fontsize=9)
    ax.legend(loc="lower left", fontsize=7, bbox_to_anchor=(-0.15, -0.15))
    out = os.path.join(FIGDIR, "fig2_heading_polar.png")
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out


def fig3(audit):
    c2 = next(c for c in audit["criteria"] if c["criterion"].startswith("K2"))
    ms = audit["mother_ship"]
    hull = R.Hull(ms["length"], ms["beam"], ms["draft"], ms["displacement"], ms["cb"])
    T = [0.8 + i * 0.1 for i in range(1, 320)]
    lo, hi = R.heave_rao_asymptotes(1.0)
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    ax.plot(T, [hull.heave_rao(t) for t in T], lw=2, label="reference (asymptotes 0 -> 1)")
    ax.plot(list(c2["sweep_T"]), c2["sweep_heave"], "o-", lw=2,
            label="platform (constant %.3f)" % c2["short_wave"]["heave_rao"])
    ax.axhline(hi, color="k", ls=":", lw=1.2, label="long-wave exact limit RAO=1")
    ax.axhline(lo, color="k", ls=":", lw=1.2)
    ax.set_xscale("log")
    ax.set_xlabel("wave period T [s] (log)")
    ax.set_ylabel("heave RAO [-]")
    ax.set_title("K2: constant heave RAO violates both exact limits (short wave ->0, long wave ->1)", fontsize=10)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    out = os.path.join(FIGDIR, "fig3_heave_limits.png")
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out


def tables(audit):
    lines = ["# 耐波域 D5 审计数字表（由 tools/verifier/seakeeping_figs.py 程序化注入，零手抄）",
             "",
             f"- 生成时间：{audit['generated_at']}",
             f"- 母型船：L={audit['mother_ship']['length']} m, B={audit['mother_ship']['beam']} m, "
             f"T={audit['mother_ship']['draft']} m, Δ={audit['mother_ship']['displacement']} t, "
             f"Cb={audit['mother_ship']['cb']}, V={audit['mother_ship']['speed']} kn",
             f"- 端点基址：{audit['base']}",
             f"- 参考核：`{audit['reference_kernel']}`",
             "",
             "## 判决汇总", "",
             "| 判据 | 判决 | 正向对照 |", "|---|---|---|"]
    for c in audit["criteria"]:
        fc = c.get("forward_control", {}).get("passed")
        lines.append(f"| {c['criterion']} | **{c['verdict']}** | {('PASS' if fc else 'FAIL') if fc is not None else '-'} |")
    lines += ["", f"域判决：**{audit['domain_verdict']}**；全部正向对照通过：{audit['forward_controls_all_passed']}", ""]

    k2 = next(c for c in audit["criteria"] if c["criterion"].startswith("K2"))
    lines += ["## K2 垂荡 RAO 两端极限（heading=180）", "",
              "| T [s] | λ/L | 平台 RAO | 参考核 RAO | 相对偏差 |", "|---|---|---|---|---|"]
    hull = R.Hull(audit["mother_ship"]["length"], audit["mother_ship"]["beam"], audit["mother_ship"]["draft"],
                  audit["mother_ship"]["displacement"], audit["mother_ship"]["cb"])
    for T, v in zip(k2["sweep_T"], k2["sweep_heave"]):
        ref = hull.heave_rao(T)
        rel = "inf" if ref == 0 else f"{(v - ref) / ref * 100:+.1f}%"
        lines.append(f"| {T:.1f} | {R.deep_water_wavelength(T) / audit['mother_ship']['length']:.2f} | {v:.3f} | {ref:.3f} | {rel} |")

    k3 = next(c for c in audit["criteria"] if c["criterion"].startswith("K3"))
    lines += ["", f"## K3 横摇频响（横浪, 平台自报 T4={k3['platform_reported_T4']} s, ζ=0.10）", "",
              "| T [s] | λ/L | 平台 RAO | 参考核 RAO | 相对偏差 |", "|---|---|---|---|---|"]
    for e in k3["rows"]:
        lines.append(f"| {e['Tp']:.1f} | {e['lam_over_L']:.2f} | {e['platform']:.3f} | {e['ref_roll_rao']:.3f} | {e['rel_err'] * 100:+.1f}% |")

    k4 = next(c for c in audit["criteria"] if c["criterion"].startswith("K4"))
    lines += ["", f"## K4 纵摇顶浪（λ=L 对应 T={k4['lambda_equal_L_period']} s）", "",
              "| T [s] | λ/L | 平台 RAO [deg/m] | 参考核 RAO [deg/m] |", "|---|---|---|---|"]
    for e in k4["rows"]:
        lines.append(f"| {e['Tp']:.1f} | {e['lam_over_L']:.2f} | {e['platform']:.3f} | {e['ref_pitch_rao_deg']:.3f} |")

    k5 = next(c for c in audit["criteria"] if c["criterion"].startswith("K5"))
    lines += ["", "## K5 艏向耦合（Tp=9 s, U=10 kn）", "",
              "| heading [deg] | 横摇 RAO | 纵摇 RAO | 垂向加速度 | 2.0+3.0·\\|sin\\| | |sin| |",
              "|---|---|---|---|---|---|"]
    import math
    for r in k5["platform_rows"]:
        h = r["heading"]
        lines.append(f"| {h:.0f} | {r['roll_rao_max']:.2f} | {r['pitch_rao_max']:.2f} | {r['vertical_acc']:.3f} | "
                     f"{2.0 + 3.0 * abs(math.sin(math.radians(h))):.3f} | {R.heading_roll_factor(h):.3f} |")
    lines += ["", "| heading [deg] | ω_e [rad/s]（解析） |", "|---|---|"]
    for h, w in k5["omega_e"].items():
        lines.append(f"| {h} | {w:.3f} |")

    k7 = next(c for c in audit["criteria"] if c["criterion"].startswith("K7"))
    lines += ["", "## K7 横摇固有周期结构（同 L/B/T，变排水量）", "",
              "| Δ [t] | 隐含 Cb | 平台 T4 [s] | 参考 GM [m] | 参考 T4 [s] |", "|---|---|---|---|---|"]
    for r in k7["rows"]:
        lines.append(f"| {r['displacement']:.0f} | {r['cb_implied']:.3f} | {r['platform_T4']} | {r['ref_gm_estimate']} | {r['ref_T4']} |")
    lines += ["", f"平台 T4 反推 GM = {k7['platform_implied_GM']} m = {k7['implied_GM_over_B']}·B（无出处常量）；"
                  f"平台 T4 跨度 {k7['platform_T4_spread']} s，参考核跨度 {k7['ref_T4_spread']} s。", ""]

    k6 = next(c for c in audit["criteria"] if c["criterion"].startswith("K6"))
    lines += ["## K6 波高线性（正向对照）", "",
              "| 量 | 幅值比 (H=2m / H=4m) |", "|---|---|"]
    for k, v in k6["pairs"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## K8/K9 端点可用性", "",
              f"- `/dynamic-stability/roll-motion`：HTTP {next(c for c in audit['criteria'] if c['criterion'].startswith('K8'))['status']}",
              f"- `/ai-models/predict/seakeeping` 输入 schema：{next(c for c in audit['criteria'] if c['criterion'].startswith('K9'))['input_schema']}", ""]
    os.makedirs(os.path.dirname(TABLES), exist_ok=True)
    with open(TABLES, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return TABLES


def main():
    os.makedirs(FIGDIR, exist_ok=True)
    audit = load()
    # 补入 sweep 序列（图与表共用同一数据源）
    sweeps = [p for p in audit["probes"] if p["path"].endswith("/calculations/seakeeping")
              and p.get("status") == 200 and p["body"].get("heading") == 180.0]
    seen = {}
    for p in sweeps:
        seen[round(p["body"]["wave_period"], 3)] = p["response"]
    Ts = sorted(seen)
    for c in audit["criteria"]:
        if c["criterion"].startswith("K2"):
            c["sweep_T"] = Ts
            c["sweep_heave"] = [seen[t]["heave_rao_max"] for t in Ts]
            c["sweep_roll"] = [seen[t]["roll_rao_max"] for t in Ts]
            c["sweep_pitch"] = [seen[t]["pitch_rao_max"] for t in Ts]
    outs = [fig1(audit), fig2(audit), fig3(audit), tables(audit)]
    for o in outs:
        print("written", o)


if __name__ == "__main__":
    main()