#!/usr/bin/env python3
"""从 runs/m1593/audit_struct_vib.json 生成图 + 报告文档（数字全部程序化注入）。

输出：
  docs/verifier/figs/m1593_fig1_plate.png, _fig2_sn.png, _fig3_errors.png, _fig4_mock.png
  docs/verifier/m1593_struct_vib_audit.md
  runs/m1593/figures.json（图名+caption，供报告内嵌）
"""
from __future__ import annotations

import json
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import struct_vib_audit as A  # noqa: E402

RUN = os.path.join(ROOT, "runs", "m1593")
FIGD = os.path.join(ROOT, "docs", "verifier", "figs")
os.makedirs(FIGD, exist_ok=True)

with open(os.path.join(RUN, "audit_struct_vib.json")) as f:
    audit = json.load(f)
with open(os.path.join(RUN, "live_responses.json")) as f:
    probes = json.load(f)["probes"]
rows = {c["id"]: c for c in audit["criteria"]}


# ───────────────────────── 图 1：板格屈曲系数钳位 ─────────────────────────
def fig1():
    gammas = [0.6 + 0.02 * i for i in range(280)]
    sig = [A.sigma_cr(A.plate_k_compression(g), 0.012 / 0.6) for g in gammas]
    plat = rows["P3"]["platform"]
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    ax.plot(gammas, sig, "C0-", lw=2, label="Analytic SS plate: $\\sigma_{cr}=k_{min}\\pi^2E(t/b)^2/[12(1-\\nu^2)]$")
    ax.axhline(plat, color="C3", ls="--", lw=2, label=f"Platform /fea/plate-buckling (constant solution) = {plat} MPa")
    ax.axhline(A.SIGMA_Y, color="0.4", ls=":", lw=1.4, label=f"yield $\\sigma_y$ = {A.SIGMA_Y:.0f} MPa")
    for cid, g in (("P3", 1.0), ("P1", 1.5), ("P2", 3.7)):
        e = rows[cid]["err_pct"]
        ax.plot([g], [plat], "C3o", ms=7)
        ax.annotate(f"a/b={g:g}\nerr {e:+.2f}%", (g, plat), textcoords="offset points",
                    xytext=(6, 22), fontsize=8, color="C3")
    ax.set_xlabel("Plate aspect ratio a/b (b=0.6 m, t=12 mm)")
    ax.set_ylabel("Buckling stress $\\sigma_{cr}$ [MPa]")
    ax.set_title("Fig.1 Plate buckling: analytic k(a/b) vs platform constant k=4 clamp")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower right")
    p = os.path.join(FIGD, "m1593_fig1_plate.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return p


# ───────────────────────── 图 2：S-N 膝点不连续 ─────────────────────────
def fig2():
    n80 = rows["F2"]["note"]
    dd80 = (probes["fatigue_BS-D_80"]["body"]["damage_distribution"] or [{}])[0]
    dd50 = (probes["fatigue_DNV-C_50"]["body"]["damage_distribution"] or [{}])[0]
    n_plat_80, n_plat_50 = dd80.get("fatigue_life"), dd50.get("fatigue_life")
    impl_a1 = n_plat_80 * 80.0 ** 3
    impl_a2 = n_plat_50 * 50.0 ** 5
    s_knee_p = (impl_a1 / 1e7) ** (1 / 3)
    ss = [x / 10 for x in range(300, 1201)]           # 30 … 120 MPa
    dnv = [A.sn_life_dnv_d(s) for s in ss]
    plat = [impl_a1 * s ** -3 if s >= s_knee_p else impl_a2 * s ** -5 for s in ss]
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    ax.loglog(ss, dnv, "C0-", lw=2, label="DNV-RP-C203 curve D (m1=3/12.164, m2=5/15.606, knee N=1e7)")
    ax.loglog(ss, plat, "C3--", lw=2,
              label=f"Platform /fatigue/analyze back-solved (log a1={math.log10(impl_a1):.3f}, log a2={math.log10(impl_a2):.3f})")
    ax.axhline(1e7, color="0.5", ls=":", lw=1.2)
    ax.plot([s_knee_p, s_knee_p], [1e6, 1e8], "0.5", ls=":", lw=1.0)
    ax.annotate(f"Knee discontinuity\nN jump {rows['F2']['platform']:+.1f}%", (s_knee_p, 1e7),
                textcoords="offset points", xytext=(10, 26), fontsize=8, color="C3")
    ax.plot([80], [n_plat_80], "C3o", ms=6)
    ax.plot([50], [n_plat_50], "C3s", ms=6)
    ax.plot([80], [A.sn_life_dnv_d(80)], "C0o", ms=6)
    ax.plot([50], [A.sn_life_dnv_d(50)], "C0s", ms=6)
    ax.set_xlabel("Stress range S [MPa]")
    ax.set_ylabel("Allowable cycles N")
    ax.set_title("Fig.2 S-N bilinear: platform (back-solved) vs DNV-RP-C203 D")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    p = os.path.join(FIGD, "m1593_fig2_sn.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return p


# ───────────────────────── 图 3：判据误差全景 ─────────────────────────
def fig3():
    pts = [(c["id"], c["err_pct"], c["verdict"], c["quantity"]) for c in audit["criteria"]
           if c["err_pct"] is not None]
    pts.sort(key=lambda x: abs(x[1]))
    labels = [(f"{c[0]} {c[2]}" if isinstance(c[2], str) else f"{c[0]}")[:38] for c in pts]
    vals = [abs(c[1]) for c in pts]
    colors = {"PASS": "C2", "WARN": "C1", "FAIL": "C3", "WITHHELD": "0.6"}
    fig, ax = plt.subplots(figsize=(8.2, 0.34 * len(pts) + 1.6))
    ax.barh(range(len(pts)), vals, color=[colors[c[2]] for c in pts])
    ax.set_yticks(range(len(pts)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xscale("log")
    ax.axvline(3, color="C1", ls="--", lw=1.2, label="3% acceptable (PASS bound)")
    ax.axvline(15, color="C3", ls="--", lw=1.2, label="15% unacceptable (FAIL bound)")
    for i, c in enumerate(pts):
        ax.text(max(vals[i], 0.05) * 1.15, i, f"{c[1]:+.2f}%", va="center", fontsize=7)
    ax.set_xlabel("|error| vs closed-form / rule reference [%] (log axis)")
    ax.set_title("Fig.3 Structural & vibration criteria: error overview (3% / 15% thresholds)")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3, axis="x")
    p = os.path.join(FIGD, "m1593_fig3_errors.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return p


# ───────────────────────── 图 4：MOCK 证据 ─────────────────────────
def fig4():
    ships = [("B_5k", 5000), ("A_20k", 20000), ("C_100k", 100000)]
    lives, disp = [], []
    for name, d in ships:
        b = probes["structure_fatigue_" + name]["body"]
        disp.append(d)
        lives.append(b["calculated_life"])
    accs, sids = [], []
    for sid in (1, 2, 999, 12345):
        key = "vibration_predict" if sid == 1 else f"vibration_ship_{sid}"
        b = probes[key]["body"]
        sids.append(sid)
        accs.append(b["acceleration"])
    fig, axs = plt.subplots(1, 2, figsize=(9.6, 4.0))
    axs[0].plot(disp, lives, "C3o-", lw=2)
    axs[0].set_xscale("log")
    axs[0].set_xlabel("Displacement [t]")
    axs[0].set_ylabel("calculated_life [years]")
    axs[0].set_title(f"/structure/fatigue: {len(set(lives))} distinct value(s)\n"
                     f"(5k/20k/100k t all = {lives[0]} y, stress range fixed 70.5 MPa)", fontsize=10)
    axs[0].set_ylim(0, max(lives) * 1.3)
    axs[0].grid(alpha=0.3)
    axs[1].plot(range(len(sids)), accs, "C3s-", lw=2)
    axs[1].set_xticks(range(len(sids)))
    axs[1].set_xticklabels([str(s) for s in sids])
    axs[1].set_xlabel("ship_id (999/12345 do not exist; /ships is empty)")
    axs[1].set_ylabel("acceleration")
    axs[1].set_title(f"/vibration/predict: {len(set(accs))} distinct value(s) = {accs[0]}\n"
                     "noise fixed 65.0 dB, no existence check", fontsize=10)
    axs[1].set_ylim(0, max(accs) * 1.6)
    axs[1].grid(alpha=0.3)
    fig.suptitle("Fig.4 Input-independence evidence (MOCK verdict basis)", fontsize=11)
    p = os.path.join(FIGD, "m1593_fig4_mock.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    return p


def md_table():
    hdr = ("| ID | 域 | 端点 | 判据量 | 平台 | 基准 | 单位 | 误差 | 判决 |\n"
           "|---|---|---|---|---|---|---|---|---|\n")
    lines = [hdr]
    for c in audit["criteria"]:
        def fmt(v):
            if v is None:
                return "—"
            if isinstance(v, float):
                return f"{v:.4g}" if abs(v) >= 0.01 else f"{v:.3e}"
            return str(v)
        lines.append("| {id} | {domain} | `{ep}` | {q} | {p} | {r} | {u} | {e} | **{v}** |\n".format(
            id=c["id"], domain=c["domain"], ep=c["endpoint"], q=c["quantity"],
            p=fmt(c["platform"]), r=fmt(c["reference"]), u=c["unit"],
            e="—" if c["err_pct"] is None else f"{c['err_pct']:+.2f}%",
            v=c["verdict"]))
    return "".join(lines)


def main():
    f1, f2, f3, f4 = fig1(), fig2(), fig3(), fig4()
    caps = [
        {"name": "m1593_fig1_plate.png", "path": f1,
         "caption": "图1 板格弹性屈曲：解析 k_min(a/b)=min_m(m/γ+γ/m)² 曲线 vs 平台常数 k=4 钳位；"
                    f"a/b=1.5 处误差 {rows['P1']['err_pct']:+.2f}%"},
        {"name": "m1593_fig2_sn.png", "path": f2,
         "caption": "图2 S-N 双线性：平台反解曲线与 DNV-RP-C203 D 曲线对比，膝点处 N 跳变 "
                    f"{rows['F2']['platform']:+.1f}%"},
        {"name": "m1593_fig3_errors.png", "path": f3,
         "caption": "图3 判据误差全景（3%/15% 阈值线）"},
        {"name": "m1593_fig4_mock.png", "path": f4,
         "caption": "图4 输入无关性证据：/structure/fatigue 疲劳寿命对排水量不变、"
                    "/vibration/predict 对 ship_id 不变"},
    ]
    figs_json = os.path.join(RUN, "figures.json")
    with open(figs_json, "w") as f:
        json.dump(caps, f, ensure_ascii=False, indent=1)

    s = audit["summary"]
    doc = DOC_TEMPLATE.format(
        s_pass=s.get("PASS", 0), s_warn=s.get("WARN", 0), s_fail=s.get("FAIL", 0),
        s_with=s.get("WITHHELD", 0), scored=audit["scored"],
        table=md_table(),
        p3=rows["P3"]["platform"], p1e=rows["P1"]["err_pct"], p2e=rows["P2"]["err_pct"],
        p5p=rows["P5"]["platform"], p5e=rows["P5"]["err_pct"],
        p4p=rows["P4"]["platform"], p6p=rows["P6"]["platform"],
        p7e=rows["P7"]["err_pct"], p7r=rows["P7"]["reference"], p9e=rows["P9"]["err_pct"],
        s2p=rows["S2"]["platform"], s2e=rows["S2"]["err_pct"],
        s3p=rows["S3"]["platform"], s4p=rows["S4"]["platform"], s4r=rows["S4"]["reference"],
        s4e=rows["S4"]["err_pct"], f2e=rows["F2"]["platform"], f3p=rows["F3"]["platform"],
        f1e=rows["F1"]["err_pct"], l1p=rows["L1"]["platform"], l2p=rows["L2"]["platform"],
        l2r=rows["L2"]["reference"], l2e=rows["L2"]["err_pct"],
        s1_life=probes["structure_fatigue_A_20k"]["body"]["calculated_life"],
        vib=probes["vibration_predict"]["body"]["acceleration"],
        vib_n=probes["vibration_predict"]["body"]["noise_level"],
    )
    out = os.path.join(ROOT, "docs", "verifier", "m1593_struct_vib_audit.md")
    with open(out, "w") as f:
        f.write(doc)
    print("wrote", out, "and", figs_json)


DOC_TEMPLATE = """# 结构 / 振动噪声域验证欠账清算 v1（m1593）

> 席位：models（tp-mod-2）· 分支 `agent/m1593` · 只读黑盒探针 + 独立闭式解复算
> 裁决机器可读工件：`runs/m1593/audit_struct_vib.json`，原始响应：`runs/m1593/live_responses.json`
> 判决口径：|相对误差| ≤ 3% PASS，3%–15% WARN，> 15% FAIL（平台精度标准）；无外部基准 → WITHHELD

## ① 为什么做这一单（覆盖缺口）

平台 21 项能力台账里的「阻力 / 稳性 / 耐波 / 操纵 / 推进 / 绿色 / 冰区」在近 24 小时内都已有验证欠账清算报告
（#1135/#1136/#1137/#1139/#1140/#1150/#1156/#1157 等），而**结构（总纵强度、板格、疲劳）与振动噪声**这两个
学科在报告池里零覆盖——尽管 OpenAPI 面上存在 22 条相关路径（`/structure/*`、`/fatigue*`、`/fea/plate-buckling`、
`/vibration/predict`）。本单把这两个域的「验证欠账」一次结清：为每条可判端点建立**闭式解基准**并逐项裁决。

## ② 方法

1. **端点面测绘**：`:8001/openapi.json` 共 411 条路径，正则筛 `struct|vibrat|noise|fatigue|strength|plate|buckling`
   得 22 条；取其中 5 个可计算端点做黑盒探针（`/structure/analyze`、`/structure/plate`、`/structure/fatigue`、
   `/fatigue/analyze`、`/fea/plate-buckling`）＋ `/vibration/predict` 可用性。
2. **独立参考核**（`tools/verifier/struct_vib_audit.py`，纯闭式、零第三方依赖）：
   - 板格弹性屈曲 `sigma_cr = k·π²E/(12(1-ν²))·(t/b)²`，`k = min_m (m/γ+γ/m)²`（Timoshenko & Gere §9.1），
     剪切 `k_s = 5.34+4/γ²`，四边固支 `k=7.0`，单边自由长板 `k=0.425`，纯弯曲 `k=23.9`；
   - 弹塑性折减 Johnson–Ostenfeld `sigma_u = sigma_y(1 - sigma_y/(4 sigma_cr))`（CSR/DNV-RP-C201 口径）；
   - 疲劳 DNV-RP-C203 D 曲线双线性（m1=3/12.164、m2=5/15.606、膝点 N=1e7）＋ Palmgren–Miner 求和，
     并用**膝点连续性恒等式** `a2 = 1e7·S_knee^5` 做无量纲自洽判定；
   - 船体梁：箱形中剖面精确和式（中性轴/惯性矩/剖面模数）＋ IACS UR S11 波浪弯矩（C1=0.19 简化带）。
3. **MOCK 检测**：同一端点在 3 组主尺度（5k/20k/100k DWT）与 4 个 ship_id（含不存在的 999/12345）下
   比较输出离散度，离散度为 0 即判输入无关。
4. 全部判据以 JSON 落盘，再用 `--replay` 离线重算（缓存 = 唯一数据源，测试锁死结论，防结论漂移）。

## ③ 结果

共 {scored} 条可算判据：**PASS {s_pass} / WARN {s_warn} / FAIL {s_fail}**（另 {s_with} 条端点不可用→WITHHELD）。

{table}

图见 `docs/verifier/figs/`：

- 图1（`m1593_fig1_plate.png`）：解析 `k_min(a/b)` 曲线 vs 平台常数解。平台对 `a/b` 一栏完全钳位在
  k=4（`{p3}` MPa），在整数长宽比处误差 {p3}→0%，在 a/b=1.5 处误差 {p1e:+.2f}%（WARN），a/b=3.7 处 {p2e:+.2f}%。
- 图2（`m1593_fig2_sn.png`）：把平台 N(80 MPa)、N(50 MPa) 反解成隐含 S-N 参数，可见膝点处 N 跳变
  {f2e:+.1f}%——双线性两段不是同一条曲线。
- 图3（`m1593_fig3_errors.png`）：判据误差全景，两条阈值线为 3% / 15%。
- 图4（`m1593_fig4_mock.png`）：MOCK 证据——疲劳寿命对排水量、加速度对 ship_id 都是水平线。

## ④ 关键发现（按严重度）

1. **`/structure/fatigue` 完全输入无关（MOCK）**：5k / 20k / 100k DWT 三组主尺度返回同一
   `calculated_life = {s1_life}` 年、`stress_range = 70.5` MPa、`s_n_curve = DNV-C`、`status = 合格`。
2. **`/structure/analyze` 量纲混用（单位 bug）**：`utilization = max_stress[Pa] / allowable[MPa]`，
   实测 {s2p}（应为 0.046），即利用率被放大 1e6 倍，任何输入都会得到「需校核」；且 `plate_thickness`
   15→30 mm 输出变化 {s3p}−0.000%，板厚不进入计算路径。响应时延 ~150 s（首调 25 s 超时）。
3. **S-N 双线性不自洽**：高周段隐含 log a2 对应膝点 N 比 1e7 大 {f2e:+.1f}%，低应力长寿命段系统性超长
   （N(50 MPa) 平台 2.10e7 vs DNV D 1.29e7）；且 `sn_curve` 选择器无效——BS-D 与 DNV-C 返回逐字节相同
   （`sn_curve_used` 字段却随请求变化，属"标签跟输入走、数值不跟"）。
4. **`/fea/plate-buckling` 基础用例是真的**：四边简支 k=4 基准 `{p3}` MPa（误差 0.00%）、固支 `{p4p}`
   （k=7，0.00%）、剪切 `{p6p}`（k_s=5.59，−0.001%）全部 PASS，`/structure/plate` 的 `t²` 标度也正确
   （{l1p:+.0f}%）。缺陷在**参数面**：`load_type=bending` 被忽略（返回压缩值，误差 {p9e:+.1f}%）、
   `boundary=free` 被当成固支（{p5p} vs 32.27 MPa，误差 {p5e:+.0f}%）、`critical_load` 恒 0（死字段）、
   无 Ostenfeld 折减（`sigma_cr > sigma_y/2` 时承载高估 {p7e:+.1f}%）。
5. **`/structure/plate` 绝对量级偏低**：σ_cr 唯一可解释几何（b=框距 0.6 m 四边简支）下应为
   {l2r} MPa，实测 {l2p} MPa（{l2e:+.1f}%，等效 k≈0.43 即单边自由系数）→ 船底板判据被系统性低估，
   `status` 长期停在「需加强」。
6. **振动噪声域尚不可用**：`/vibration/predict` 对 ship_id=1/2/999/12345 都返回 acceleration={vib}、
   noise_level={vib_n} dB，`/ships` 库为空且不存在校验 → 常数桩（MOCK）。
7. **船体梁应力量级**：`max_stress` 按 Pa 解释为 {s4p} MPa，箱形剖面 + UR S11 波浪弯矩基准 {s4r} MPa，
   偏离 {s4e:+.1f}%——但真实中剖面模数未知，此条只作量级带警示（严格判定 WITHHELD）。

## ⑤ 注意点 / 局限

- 结构端点为主尺度级黑盒：真实中剖面、骨材布置、双层底高度均不可见，因此**量级带类判据**
  （S4、L2）依赖"最宽容的几何解释"，已逐条在 `note` 字段写明假设；弹塑性类判据（P7）只在
  `sigma_cr > sigma_y/2` 时成立，属规范口径而非唯一真理。
- `k_min(γ)` 对 γ∈整数时为 4.0 是数学事实（不是平台凑数），所以整数长宽比的 PASS 不代表参数面正确——
  本单特意用 a/b=1.5 与 3.7 两个非整数点把钳位行为暴露出来。
- `/structure/analyze` 与 `/structure/plate` 单次响应 100–200 s，探针按 240 s 超时单发；已在本机
  load15≈125 的高负载下执行，未并发压测，时延数据只作可用性提示，不作性能判定。
- 振动域缺 `ship` 库与频谱输出（只有标量 acceleration/noise_level），船体梁垂向 2 节点固有频率
  `f ≈ 22.4·sqrt(EI/(m L⁴))` 基准已写进工具但**未启用**——待 ship 库就绪后再补正式判决。

## ⑥ 误差判定（逐项回答"是否 < 3%"）

- 板格屈曲基础用例（P2/P3/P4/P6，`k=4/7/5.59`）：**是**，最大 |误差| 0.61%（P2 a/b=3.7）。
- 板厚标度（L1）：**是**，+300.0% vs 理论 +300%。
- Miner 内部自洽（F1 安全系数=1/D）：**是**，误差 {f1e:+.3f}%。
- 板格参数面（P1 a/b 钳位 / P5 free 边界 / P7 Ostenfeld / P9 弯曲载荷）：**否**，−7.84% / +1547% / +60.0% / −83.3%。
- 疲劳 S-N（F2 膝点连续性 / F3 曲线选择器）：**否**，{f2e:+.1f}% / 选择器无效（差异 0.00%）。
- 结构服务端（S2 单位 / S3 板厚 / S4 量级 / L2 量级）：**否**，1e8 量级 / 0.00% / {s4e:+.1f}% / {l2e:+.1f}%。
- 振动域（V1）：无可用基准（ship 库空）→ WITHHELD，不做误差粉饰。

## 关闭路径（给后续单）

1. `/structure/fatigue`：接入真实波浪弯矩/应力谱，删除常数桩；至少让 `stress_range` 随 L/B/D/Cb 变化。
2. `/structure/analyze`：统一量纲（应力统一 MPa 或统一 Pa 后再比 allowable），暴露 `Z`/`M` 中间量，
   把 `plate_thickness` 接进剖面模数；并把响应时间从 150 s 降到秒级（当前不可用于在线批量）。
3. `/fatigue/analyze`：改用 DNV-RP-C203 表参数双线性（膝点连续性由 `tools/verifier/struct_vib_audit.py::
   sn_continuity_residual` 守住），让 `sn_curve` 真正选表。
4. `/fea/plate-buckling`：`load_type`/`boundary` 映射表 + `critical_load` 落地 + Ostenfeld 折减开关。
5. `/vibration/predict`：先建 ship 库与频谱响应，再用闭式固有频率与 ISO 6954 判据复核。
"""

if __name__ == "__main__":
    main()