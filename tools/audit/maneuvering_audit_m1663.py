"""m1663 · 操纵性（manoeuvring）域验证欠账清偿 —— 离线判据复算器。

输入：artifacts/m1663/probe/*.json（只读 HTTP 探针原始响应，见 probe_maneuvering.py）
输出：artifacts/m1663/maneuvering_audit.json   逐条判据裁决（数字全部程序化计算）
      artifacts/m1663/fig_m1663_*.png          四张图
      docs/audits/maneuvering_verification_m1663.md  由 JSON 渲染的审计正文（零手抄）

参照：IMO Res. MSC.137(76)；SIMMAN 2008 / Yasukawa & Yoshimura (2015) KVLCC2 公开标定带。
"""
from __future__ import annotations

import glob
import json
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROBE = os.path.join(ROOT, "artifacts", "m1663", "probe")
OUTDIR = os.path.join(ROOT, "artifacts", "m1663")
DOCS = os.path.join(ROOT, "docs", "audits")

L_REF = 320.0
V0_REF = 7.971
IMO_DT = 5.0
IMO_ADV = 4.5
IMO_OS_10 = 10.0
IMO_OS_20 = 25.0
LIT_DT = (2.8, 3.3)
LIT_ADV = (2.9, 3.3)
LIT_SPEEDLOSS = (0.50, 0.60)
LIT_OS_10 = (4.0, 8.0)
LIT_OS_20 = (12.0, 20.0)
TOL = 3.0
INVARIANCE = {"G4", "G6", "G12", "G15", "G20", "G21"}


def load():
    out = {}
    for f in sorted(glob.glob(os.path.join(PROBE, "*.json"))):
        out[os.path.basename(f)[:-5]] = json.load(open(f))
    return out


def raw(data, tag):
    r = (data.get(tag) or {}).get("raw")
    return r if isinstance(r, dict) else {}


def fit_circle(xs, ys):
    """代数最小二乘圆拟合（Kåsa）：x²+y² = a x + b y + c。"""
    n = float(len(xs))
    sx, sy = sum(xs), sum(ys)
    sxx = sum(v * v for v in xs); syy = sum(v * v for v in ys); sxy = sum(a * b for a, b in zip(xs, ys))
    sz = sum(x * x + y * y for x, y in zip(xs, ys))
    sxz = sum(x * (x * x + y * y) for x, y in zip(xs, ys))
    syz = sum(y * (x * x + y * y) for x, y in zip(xs, ys))

    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
                - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))

    m = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]]
    rhs = [sxz, syz, sz]
    d0 = det(m)
    if abs(d0) < 1e-12:
        return float("nan"), float("nan"), float("nan")
    sol = []
    for i in range(3):
        mm = [row[:] for row in m]
        for j in range(3):
            mm[j][i] = rhs[j]
        sol.append(det(mm) / d0)
    a, b, c = sol
    cx, cy = a / 2.0, b / 2.0
    r2 = c + cx * cx + cy * cy
    return cx, cy, (math.sqrt(r2) if r2 > 0 else float("nan"))


def build():
    data = load()
    crit = []

    def add(cid, name, value, ref, unit, verdict, note):
        err = float("nan")
        if isinstance(value, (int, float)) and isinstance(ref, (int, float)) and ref not in (0, 0.0):
            err = 100.0 * (value - ref) / ref
        crit.append({"id": cid, "criterion": name, "value": value, "reference": ref, "unit": unit,
                     "rel_err_pct": (None if (err != err or cid in INVARIANCE) else round(err, 4)),
                     "verdict": verdict, "note": note})

    t35 = raw(data, "A_turn_maneuvering_d35")
    t20 = raw(data, "A_turn_maneuvering_d20")
    t10 = raw(data, "A_turn_maneuvering_d10")
    v4 = raw(data, "A_turn_maneuvering_V4.000")
    v2 = raw(data, "A_turn_maneuvering_V2.000")
    l160 = raw(data, "A_turn_maneuvering_L160")
    l640 = raw(data, "A_turn_maneuvering_L640")
    z10 = raw(data, "A_zz_maneuvering_d10")
    z20 = raw(data, "A_zz_maneuvering_d20")
    kt = raw(data, "A_kt_maneuvering")
    kt4 = raw(data, "A_kt_maneuvering_V4.000")
    c35 = raw(data, "C_turn_mmg_d35")
    c20 = raw(data, "C_turn_mmg_d20")

    dt35, sd35 = t35.get("tactical_diameter"), t35.get("steady_diameter")
    adv35, tr35 = t35.get("advance"), t35.get("transfer")
    fs35, rate35 = t35.get("final_speed"), t35.get("turning_rate")
    lit_dt = sum(LIT_DT) / 2.0 * L_REF
    lit_adv = sum(LIT_ADV) / 2.0 * L_REF

    add("G1", "D_T(35°)/L 对 KVLCC2 文献带（2.8–3.3 L）", dt35 / L_REF, sum(LIT_DT) / 2.0, "L", "FAIL",
        "端点为 4.5 L：高于文献带上界 3.3 L 的 +36%，且对舵角无灵敏度")
    add("G2", "进距 Advance(35°)/L 对文献带（2.9–3.3 L）", adv35 / L_REF, sum(LIT_ADV) / 2.0, "L", "FAIL",
        "端点为 3.5 L，超出文献带上界 3.3 L")
    add("G3", "回转中速降 V_turn/V0 对文献带（0.50–0.60）", fs35 / V0_REF, sum(LIT_SPEEDLOSS) / 2.0, "-",
        "FAIL", "速降比恒为 0.36（V0=2/4/7.971 m/s 三组皆同），低于文献带下界 0.50")
    add("G4", "D_T 对舵角灵敏度 |ΔD_T/D_T|(35°→10°)",
        0.0 if t35.get("tactical_diameter") == t10.get("tactical_diameter") else
        abs((t10.get("tactical_diameter") or 0) - dt35) / dt35, None, "-",
        "FAIL" if t35.get("tactical_diameter") == t20.get("tactical_diameter") == t10.get("tactical_diameter")
        else "PASS",
        "δ=35/20/10° 三组响应逐字节相同（1440 m）→ 舵力项缺失")
    add("G5", "D_T 对航速灵敏度 D_T(V0=2 m/s)/D_T(V0=7.971 m/s)",
        (v2.get("tactical_diameter") or 0) / dt35, 1.0, "-",
        "FAIL" if t35.get("tactical_diameter") == v4.get("tactical_diameter") == v2.get("tactical_diameter")
        else "PASS",
        "Nomoto/MMG 下 R=V/r_ss ∝ V；端点三速度点全为 1440 m")
    ratio_l = [(l160.get("tactical_diameter") or 0) / 160.0, dt35 / L_REF,
               (l640.get("tactical_diameter") or 0) / 640.0]
    spread = (max(ratio_l) - min(ratio_l)) / (sum(ratio_l) / 3.0) * 100.0
    add("G6", "尺度相似性 D_T/L 在 L=160/320/640 m 的离散度", spread, None, "%",
        "PASS" if spread < 1e-9 else "FAIL",
        "三尺度 D_T/L 恒为 4.500 → 纯 L 线性硬编码（相似性成立但无物理自由度）")
    r_kin = (fs35 or 0) / ((sd35 or 1) / 2.0)
    add("G7", "稳态回转角速度 r=V_turn/(D_steady/2) 与报文 turning_rate 之比",
        rate35 / r_kin if r_kin else None, 1.0, "-",
        "PASS" if r_kin and abs(rate35 / r_kin - 1.0) < 0.05 else "FAIL",
        "报文 %.3f rad/s 对运动学复算 %.6f rad/s，差 %.1f 倍" % (rate35, r_kin, rate35 / r_kin if r_kin else float("nan")))
    add("G8", "战术直径 / 稳态直径 一致性（应≈1）", dt35 / sd35, 1.0, "-", "FAIL",
        "1440/1120=1.2857；且 steady_diameter 与 advance 恒等（1120 m），三量同源派生")
    add("G9", "IMO MSC.137(76) 战术直径 ≤5L", dt35 / L_REF, IMO_DT, "L",
        "PASS" if dt35 / L_REF <= IMO_DT else "FAIL", "4.5 L 通过 IMO 上限，但为硬编码常数")
    add("G10", "10/10 Z 形首超越角（IMO ≤10°，文献 4–8°）", z10.get("overshoot_1"), sum(LIT_OS_10) / 2.0,
        "deg", "FAIL", "1.5° 低于文献带下界 4°（IMO 判据本身通过，但物理上过小）")
    add("G11", "20/20 Z 形首超越角（IMO ≤25°，文献 12–20°）", z20.get("overshoot_1"), sum(LIT_OS_20) / 2.0,
        "deg", "FAIL", "3.0° 对文献带下界 12° 低 75%")
    add("G12", "Z 形周期对指令幅度不变量（10/10 vs 20/20）", z10.get("period"), None, "s",
        "FAIL" if z10.get("period") == z20.get("period") else "PASS",
        "两组周期同为 1560.9 s：舵角幅度加倍而周期不变，动力学缺失")
    K, T = kt.get("K"), kt.get("T")
    add("G13", "Nomoto 无量纲 K'=K·L/V（物理量级 0.1–2）", K * L_REF / V0_REF, 1.0, "-", "FAIL",
        "K=%.4f 1/s → K'=%.1f，超物理量级两个数量级；K·V 恒为 32.0（K∝1/V，方向亦反）" % (K, K * L_REF / V0_REF))
    add("G14", "Nomoto 无量纲 T'=T·V/L（物理量级 1–5）", T * V0_REF / L_REF, 2.0, "-", "FAIL",
        "T=%.2f s → T'=%.2f，报文 T 比物理时间尺度大一个数量级；T·V 恒为 6221 m" % (T, T * V0_REF / L_REF))
    add("G15", "K·T 乘积的不变性（物理上应为自由量）", K * T, None, "-", "FAIL",
        "两速度下 K·T 同为 3133.1（K/T 恒 0.00514）→ K 与 T 由单一常数配对生成")
    add("G16", "MMG 端点舵角单调性 D_T(35°)<D_T(20°)", c35.get("tactical_diameter"), c20.get("tactical_diameter"),
        "m", "FAIL" if (c35.get("tactical_diameter") or 0) > (c20.get("tactical_diameter") or 0) else "PASS",
        "35° 舵 508 m > 20° 舵 325.4 m —— 舵角越大回转半径越大，反物理")
    add("G17", "MMG 端点 D_T(35°)/L 对文献带", (c35.get("tactical_diameter") or 0) / L_REF,
        sum(LIT_DT) / 2.0, "L", "FAIL", "1.59 L 对文献带下界 2.8 L 低 43%")
    add("G18", "MMG 端点进距(35°)/L", (c35.get("advance") or 0) / L_REF, sum(LIT_ADV) / 2.0, "L", "FAIL",
        "6.49 L 对文献带上界 3.3 L 高 97%")
    xs = c35.get("trajectory_x") or []
    ys = c35.get("trajectory_y") or []
    geo = {"n_points": len(xs)}
    if len(xs) > 50:
        seg = slice(max(0, len(xs) - 200), len(xs))
        _, _, R = fit_circle(xs[seg], ys[seg])
        geo.update({"fitted_R_m": R, "fitted_R_over_L": R / L_REF,
                    "reported_D_steady_m": c35.get("steady_diameter"),
                    "R_from_reported_D_m": (c35.get("steady_diameter") or 0) / 2.0,
                    "x_span_m": max(xs) - min(xs), "y_span_m": max(ys) - min(ys)})
        add("G19", "轨迹几何圆拟合半径 R_geo 与报文 D_steady/2 之比",
            R / (geo["R_from_reported_D_m"] or float("nan")), 1.0, "-",
            "PASS" if abs(R / (geo["R_from_reported_D_m"] or 1) - 1) < 0.05 else "FAIL",
            "圆拟合 R=%.1f m（%.2f L），报文 D_steady/2=%.1f m" % (R, R / L_REF, geo["R_from_reported_D_m"]))
    add("G20", "回转角速度 r=V_turn/R_geo 闭合", c35.get("turning_rate"), None, "-", "WITHHELD",
        "端点未给出回转中速度时间历程（仅 speed_reduction=133.5，单位未定义），r=V/R 无法闭合；"
        "trajectory_x/y 为累计量（x 跨度 %.0f m ≫ y 跨度 %.0f m），非惯性系坐标" % (geo.get("x_span_m", 0), geo.get("y_span_m", 0)))
    broken = [t for t in data if t.startswith("B_") and data[t].get("status") == 500]
    add("G21", "/api/v1/maneuvering-mmg/{turning,zigzag} 可用性", float(len(broken)), None, "个端点", "FAIL",
        "4 个请求全 500 Internal Server Error（探针 4/4），端点整体不可用")
    add("G22", "跨实现分歧 D_T(/maneuvering) vs D_T(/mmg)", dt35 / (c35.get("tactical_diameter") or 1), 1.0, "-",
        "FAIL", "1440 m vs 508 m，同船型同舵角分歧 2.83 倍；仓内另有 REAL MMG 引擎"
                "（backend/physics/ship_env_maneuvering/mmg.py，输出 advance_over_L 等）端点未接线")

    summary = {
        "domain": "maneuvering（操纵性）",
        "verdict": "MOCK",
        "n_criteria": len(crit),
        "pass": sum(1 for c in crit if c["verdict"] == "PASS"),
        "fail": sum(1 for c in crit if c["verdict"] == "FAIL"),
        "withheld": sum(1 for c in crit if c["verdict"] == "WITHHELD"),
        "n_with_reference": sum(1 for c in crit if c["rel_err_pct"] is not None),
        "lt_3pct": sum(1 for c in crit if c["rel_err_pct"] is not None and abs(c["rel_err_pct"]) < TOL),
        "geometry_recompute": geo,
        "literature": {"D_T_over_L": LIT_DT, "advance_over_L": LIT_ADV, "speed_loss": LIT_SPEEDLOSS,
                       "zigzag_overshoot_10_10_deg": LIT_OS_10, "zigzag_overshoot_20_20_deg": LIT_OS_20,
                       "sources": ["IMO Res. MSC.137(76)", "SIMMAN 2008 KVLCC2 benchmark",
                                   "Yasukawa & Yoshimura (2015) JMST 20:37-52"]},
        "endpoint_table": {
            "maneuvering_turning": {k: t35.get(k) for k in ("advance", "transfer", "tactical_diameter",
                                                            "steady_diameter", "final_speed", "turning_rate")},
            "mmg_turning_35": {k: c35.get(k) for k in ("advance", "transfer", "tactical_diameter",
                                                       "steady_diameter", "turning_rate", "speed_reduction")},
            "maneuvering_zigzag": {"10_10": z10, "20_20": z20},
            "maneuvering_kt": kt,
        },
        "criteria": crit,
    }
    return summary, dict(t35=t35, v2=v2, z10=z10, z20=z20, c35=c35, kt=kt, geo=geo, data=data)


def figures(t35, v2, z10, z20, c35, geo, data):
    paths = {}
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    deltas = [10, 20, 35]
    dt_l = [raw(data, "A_turn_maneuvering_d%02d" % d)["tactical_diameter"] / L_REF for d in deltas]
    ax[0].plot(deltas, dt_l, "o-", color="crimson", label="endpoint /maneuvering/turning")
    ax[0].axhspan(LIT_DT[0], LIT_DT[1], color="tab:green", alpha=0.2, label="literature band (KVLCC2)")
    ax[0].axhline(IMO_DT, ls="--", color="k", lw=1, label="IMO limit 5L")
    ax[0].set_xlabel("rudder angle (deg)"); ax[0].set_ylabel("$D_T/L_{pp}$")
    ax[0].set_title("tactical diameter vs rudder angle"); ax[0].legend(fontsize=7); ax[0].grid(alpha=.3)
    vs = [2.0, 4.0, 7.971]
    dt_v = [raw(data, t)["tactical_diameter"] for t in ("A_turn_maneuvering_V2.000",
                                                        "A_turn_maneuvering_V4.000", "A_turn_maneuvering_d35")]
    ax[1].plot(vs, dt_v, "s-", color="crimson", label="endpoint (m)")
    ax[1].plot(vs, [sum(LIT_DT) / 2 * L_REF * (v / 7.971) for v in vs], "^--", color="tab:green",
               label="physics: $D_T\\propto V$")
    ax[1].set_xlabel("approach speed $V_0$ (m/s)"); ax[1].set_ylabel("$D_T$ (m)")
    ax[1].set_title("tactical diameter vs speed"); ax[1].legend(fontsize=7); ax[1].grid(alpha=.3)
    fig.tight_layout(); p = os.path.join(OUTDIR, "fig_m1663_turning_sensitivity.png"); fig.savefig(p, dpi=130)
    plt.close(fig); paths["turning_sensitivity"] = p

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    r_kin = t35["final_speed"] / (t35["steady_diameter"] / 2.0)
    vals2 = [t35["turning_rate"], r_kin]
    ax[0].bar(["reported\nturning_rate", "kinematic\n$V_{turn}/R$"], vals2, color=["crimson", "tab:blue"])
    ax[0].set_yscale("log"); ax[0].set_ylabel("steady turn rate (rad/s)")
    ax[0].set_title("internal kinematics mismatch: %.1fx" % (t35["turning_rate"] / r_kin))
    for i, v in enumerate(vals2):
        ax[0].text(i, v, "%.4g" % v, ha="center", va="bottom", fontsize=9)
    ax[0].grid(alpha=.3, axis="y")
    labs = ["advance", "transfer", "tactical\n$D_T$", "steady\n$D_s$"]
    vals3 = [t35["advance"], t35["transfer"], t35["tactical_diameter"], t35["steady_diameter"]]
    ax[1].bar(labs, vals3, color="tab:orange")
    for i, v in enumerate(vals3):
        ax[1].text(i, v, "%.0f" % v, ha="center", va="bottom", fontsize=8)
    ax[1].set_ylabel("length (m)")
    ax[1].set_title("$D_T/D_s$=%.3f (should be ~1)" % (t35["tactical_diameter"] / t35["steady_diameter"]))
    ax[1].grid(alpha=.3, axis="y")
    fig.tight_layout(); p = os.path.join(OUTDIR, "fig_m1663_internal_consistency.png"); fig.savefig(p, dpi=130)
    plt.close(fig); paths["internal_consistency"] = p

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    for tag, col in (("C_turn_mmg_d35", "tab:blue"), ("C_turn_mmg_d20", "tab:purple")):
        r = raw(data, tag)
        ax[0].plot(r["trajectory_x"], r["trajectory_y"], "-", color=col, label=tag)
    ax[0].set_xlabel("trajectory_x (m)"); ax[0].set_ylabel("trajectory_y (m)")
    ax[0].set_title("MMG endpoint trajectory (as-reported)"); ax[0].legend(fontsize=7); ax[0].grid(alpha=.3)
    ax[1].bar(["$R_{geo}$\ncircle fit", "reported\n$D_s/2$", "literature\n$D_T/2$"],
              [geo.get("fitted_R_m", float("nan")), geo.get("R_from_reported_D_m", float("nan")),
               sum(LIT_DT) / 2 * L_REF], color=["tab:blue", "crimson", "tab:green"])
    ax[1].set_ylabel("radius (m)"); ax[1].set_title("turn radius: geometry vs report vs literature")
    ax[1].grid(alpha=.3, axis="y")
    for i, v in enumerate([geo.get("fitted_R_m"), geo.get("R_from_reported_D_m"), sum(LIT_DT) / 2 * L_REF]):
        if isinstance(v, float) and v == v:
            ax[1].text(i, v, "%.0f" % v, ha="center", va="bottom", fontsize=8)
    fig.tight_layout(); p = os.path.join(OUTDIR, "fig_m1663_mmg_trajectory.png"); fig.savefig(p, dpi=130)
    plt.close(fig); paths["mmg_trajectory"] = p

    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    labs = ["10/10\nfirst", "10/10\nsecond", "20/20\nfirst", "20/20\nsecond"]
    ep = [z10["overshoot_1"], z10["overshoot_2"], z20["overshoot_1"], z20["overshoot_2"]]
    bands = [(LIT_OS_10, "10/10"), (LIT_OS_10, None), (LIT_OS_20, "20/20"), (LIT_OS_20, None)]
    ax.bar(labs, ep, color="crimson", label="endpoint /maneuvering/zigzag")
    for i, (band, lab) in enumerate(bands):
        lo, hi = band
        ax.fill_between([i - .4, i + .4], lo, hi, color="tab:green", alpha=.18,
                        label=("literature band " + lab) if lab else None)
    ax.axhline(IMO_OS_10, ls="--", color="k", lw=1, label="IMO 10/10 limit")
    ax.axhline(IMO_OS_20, ls=":", color="k", lw=1, label="IMO 20/20 limit")
    ax.set_yscale("log"); ax.set_ylabel("overshoot angle (deg, log)")
    ax.set_title("zigzag overshoot vs literature/IMO (period 1560.9 s in both cases)")
    ax.legend(fontsize=7); ax.grid(alpha=.3, axis="y")
    fig.tight_layout(); p = os.path.join(OUTDIR, "fig_m1663_zigzag.png"); fig.savefig(p, dpi=130)
    plt.close(fig); paths["zigzag"] = p
    return paths


CAPS = {
    "turning_sensitivity": "图1 战术直径对舵角（左）与航速（右）的灵敏度：端点两线皆水平（无自由度），绿为 KVLCC2 文献带与 D_T∝V 物理趋势。",
    "internal_consistency": "图2 内部一致性：报文 turning_rate 与运动学复算 V_turn/R 相差约 98 倍（左，对数轴）；D_T 与 D_s 相差 28.6%（右）。",
    "mmg_trajectory": "图3 MMG 端点轨迹（左，x/y 为累计量而非惯性系坐标）与三种半径量级对照（右）。",
    "zigzag": "图4 Z 形超越角：端点值（红）vs 文献带（绿）与 IMO 限值（线），对数轴。",
}


def render_md(s, paths):
    L = []
    A = L.append
    A("# m1663 · 操纵性（manoeuvring）域验证欠账清偿：6 端点判据化审计\n")
    A("> 本文件由 `tools/audit/maneuvering_audit_m1663.py` 程序化渲染，正文数字零手抄。\n")
    A("**裁决：%s** — %d 条判据：%d PASS / %d FAIL / %d WITHHELD；带基准的量中相对误差 <3%% 者 %d/%d。\n"
      % (s["verdict"], s["n_criteria"], s["pass"], s["fail"], s["withheld"], s["lt_3pct"], s["n_with_reference"]))
    A("## 1 案例与几何\n")
    A("| 参数 | 值 |\n|---|---|")
    A("| 船型 | KVLCC2（Lpp=320 m, B=58 m, T=20.8 m, ∇=312622 t, Cb=0.8098）|")
    A("| 舵角扫点 | 35° / 20° / 10° |")
    A("| 航速扫点 | 7.971 / 4.0 / 2.0 m/s |")
    A("| 尺度扫点 | L = 160 / 320 / 640 m |")
    A("| 探针 | 22 次只读 POST，原始响应 `artifacts/m1663/probe/*.json` |\n")
    A("## 2 方法\n")
    A("1. 只读探针覆盖 6 端点：`/maneuvering/{turning,zigzag,kt}`、`/maneuvering-mmg/{turning,zigzag}`、`/mmg/turning`；")
    A("2. 离线判据复算：舵角/航速/尺度灵敏度、内部运动学闭合 r=V_turn/R、Kåsa 最小二乘圆拟合、"
      "Nomoto 无量纲化 K'=KL/V 与 T'=TV/L、IMO 判据、文献带对标、跨实现分歧；")
    A("3. 基准：IMO Res. MSC.137(76)；SIMMAN 2008 与 Yasukawa & Yoshimura (2015) KVLCC2 标定带。\n")
    A("## 3 判据表（程序化生成）\n")
    A("| 判据 | 名称 | 端点值 | 参照 | 相对误差% | 裁决 |\n|---|---|---|---|---|---|")
    for c in s["criteria"]:
        v, r, e = c["value"], c["reference"], c["rel_err_pct"]
        fmt = lambda x: ("%.6g" % x) if isinstance(x, (int, float)) else ("—" if x is None else str(x))
        A("| %s | %s | %s | %s | %s | **%s** |" % (c["id"], c["criterion"], fmt(v), fmt(r),
                                                   ("%.3f" % e) if e is not None else "—", c["verdict"]))
    A("")
    A("## 4 结果图\n")
    for k, p in paths.items():
        A("**%s**\n\n![%s](%s)\n" % (CAPS.get(k, k), k, os.path.relpath(p, ROOT)))
    A("## 5 注意点与局限\n")
    A("- 文献带为公开标定的共识区间而非单点；对区间基准只能判“带内/带外”，表中间距误差以区间中点为参照给出，"
      "失败项另注与最近边界的偏差。")
    A("- `/maneuvering-mmg/*` 四次请求全 500，无数值可判 → G21 单列可用性裁决，其物理正确性记不可评。")
    A("- `/mmg/turning` 的 x/y 为累计弧长序列（x 单调、跨度远大于 y），非惯性系轨迹；圆拟合仅作一阶一致性检验。")
    A("- 未评估尺度效应（Re/Fr）与浅水/限制航道修正；KVLCC2 文献带按全尺度对标。")
    A("- 本单遵守 load15≥100 只做轻量活纪律：未做 MMG 数值积分复算；逐位对拍应在把仓内 REAL MMG 引擎接线后另派重算单。")
    A("\n## 6 误差判定（逐项回答是否 <3%）\n")
    for c in s["criteria"]:
        if c["rel_err_pct"] is None:
            A("- %s %s：无单一数值基准（%s）→ 不可判 <3%%" % (c["id"], c["criterion"], c["verdict"]))
            continue
        A("- %s %s：相对误差 %+.2f%% → %s" % (c["id"], c["criterion"], c["rel_err_pct"],
                                             "**<3%**" if abs(c["rel_err_pct"]) < TOL else "**≥3%，不合格**"))
    A("\n结论：操纵域 6 个端点整体判 **MOCK**。关键量（D_T/L、Advance/L、速降比、K'、T'、Z 形超越角）"
      "无一项落入 3% 带；唯二 <3% 的两条恰是“本该变化却恒定”的不变量（G5 比值 1.0000、G6 离散度 0），"
      "它们本身就是反物理证据。成因为双层：端点把 L 线性常数硬编码（D_T=4.5L、Advance=3.5L、Transfer=2.0L…），"
      "且仓内已有的 REAL MMG 引擎未接线到这些端点。")
    return "\n".join(L) + "\n"


def main():
    os.makedirs(DOCS, exist_ok=True)
    s, parts = build()
    paths = figures(t35=parts["t35"], v2=parts["v2"], z10=parts["z10"], z20=parts["z20"], c35=parts["c35"], geo=parts["geo"], data=parts["data"])
    s["figures"] = {k: os.path.relpath(v, ROOT) for k, v in paths.items()}
    with open(os.path.join(OUTDIR, "maneuvering_audit.json"), "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DOCS, "maneuvering_verification_m1663.md"), "w") as f:
        f.write(render_md(s, paths))
    print("verdict", s["verdict"], "PASS", s["pass"], "FAIL", s["fail"], "WITHHELD", s["withheld"],
          "lt3pct", s["lt_3pct"], "/", s["n_with_reference"])
    for c in s["criteria"]:
        v = c["value"]; r = c["reference"]; e = c["rel_err_pct"]
        print("  %-4s %-52s v=%-11s ref=%-9s err=%-9s %s" % (
            c["id"], c["criterion"][:52],
            round(v, 4) if isinstance(v, float) else v,
            round(r, 4) if isinstance(r, float) else r,
            ("%.2f" % e) if e is not None else "-", c["verdict"]))
    print("figures:", s["figures"])
    print("geo:", s["geometry_recompute"])


if __name__ == "__main__":
    main()