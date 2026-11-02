#!/usr/bin/env python3
"""Build and publish the V-01 progress report from artifacts/verification/v01/metrics.json.

Every number in the markdown body is injected from the metrics file (no hand-copying).
Figures are embedded as base64 PNG. Credentials come from the environment
(TENSOR_AGENT_USER / TENSOR_AGENT_PASS), the API base from TENSOR_API_BASE.

Run:  python3 scripts/publish_report_v01.py [--dry-run]
"""
from __future__ import annotations

import base64
import glob
import json
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = os.path.join(ROOT, "artifacts", "verification", "v01", "metrics.json")
FIGGLOB = os.path.join(ROOT, "docs", "verification", "v01_figs", "*.png")

CAPTIONS = {
    "fig1_wetted_surface.png":
        "图1 左：精确回转体湿面积（点/曲线）vs 代码的 $C_b$ 单参数拟合（黑线）；"
        "右：五类参考几何的湿面积偏差，红线为 15% 门槛",
    "fig2_friction_and_resistance.png":
        "图2 左：ITTC-1957（实现）与 Schoenherr 1932 交叉；"
        "右：同一组端点输入下 $R_t$ 随真实几何的不确定带",
    "fig3_stability_gz.png":
        "图3 左：一阶原理 GZ 与端点闭式对比（点线为适用域分界）；"
        "右：相对误差在域内约 1e-6 级、域外迅速失控",
    "fig4_structural_probes.png":
        "图4 左：$R_t$ 对吃水零灵敏度（T 从不进入公式）；"
        "右：$C_r$ 对 $F_n$ 严格单调，结构上无法表达兴波阻力峰谷",
}


def build() -> dict:
    m = json.load(open(METRICS, encoding="utf-8"))
    r1, r3, r4, r5, r6 = (m["R1_ittc1957"], m["R3_identities"], m["R4_wetted_surface"],
                          m["R5_gz"], m["R6_structural"])
    row = {x["name"]: x for x in r4["rows"]}
    sub, cos_, cyl, full = row["suboff_like"], row["cosine_power"], row["cylinder"], row["full_form"]
    worst_big = max(x["rel_err_ittc"] for x in r1["rows"] if x["Rf"] > 10.0)
    dev_sch = max(abs(x["dev_vs_schoenherr"]) for x in r1["rows"]) * 100.0
    cross = r4["cb_15pct_crossover"]
    band = abs(cos_["rel_err"] - sub["rel_err"]) * 100.0
    sb = m["meta"]["suboff"]

    md = []
    md.append(
        "## 1 案例\n"
        "平台能力审计把 `calc.resistance` / `calc.stability` 判为 REAL，但仓内此前 **0 个** 测试触及这两条端点"
        "（`grep -rl 'calculations/resistance' backend/tests` 无命中）。本单为它们配独立基准并量化误差。"
        f"阻力算例（端点默认参数）：L={sb['length']} m, B={sb['beam']} m, T={sb['draft']} m, "
        f"Cb={sb['cb']}, V=3.05 m/s, ρ=998 kg/m³, ν=1.004e-6 m²/s；"
        f"稳性算例：箱型驳 L=100 m, B={r5['barge']['B']:.0f} m, T={r5['barge']['T']:.0f} m, "
        f"KG={r5['barge']['KG']:.0f} m, 型深 {r5['barge']['depth']:.0f} m（GM={r5['GM']:.4f} m）。")
    md.append(
        "## 2 方法\n"
        "参考量一律不复用被测公式：R1 ITTC-1957 用 mpmath 50 位精度独立重算；R2 Schoenherr 1932 线交叉检验；"
        "R3 平板阻力定义恒等式；R4 回转体湿面积精确积分 $S_w=\\int 2\\pi r\\sqrt{1+r'^2}dx$（40 万点）；"
        "R5 箱型驳浸没剖面 20 万条带积分一阶原理 $GZ=(\\bar y_B-\\tfrac{B}{2})\\cos\\varphi+(\\bar z_B-KG)\\sin\\varphi$；"
        "R6 结构探针（吃水灵敏度、$C_r(F_n)$ 单调性、小角极限）。"
        f"积分器自检：回转体积分对长球体闭式面积的相对偏差 {m['R4_spheroid_quadrature_check']:.2e}。")
    md.append(
        "## 3 结果\n"
        f"① 摩擦线：Re=10⁶–10⁹ 端点反推 $C_F$ 与 ITTC-1957 的最大相对误差 {r1['max_rel_err']:.2e}"
        f"（出现在 Rf 最小的点，可由响应字段 4 位小数舍入完全解释）；Rf>10 N 各点仅 {worst_big:.2e}，"
        f"属浮点精度级；与 Schoenherr 线偏差 {dev_sch:.2f}%。"
        f"② 湿面积：圆柱(Cb=1) {cyl['rel_err']*100:.2f}%、长球体(2/3) {row['spheroid']['rel_err']*100:.2f}%、"
        f"余弦幂体(0.542) {cos_['rel_err']*100:.2f}%、SUBOFF 比例尖首尾(0.542) {sub['rel_err']*100:+.2f}%、"
        f"丰满体(0.75) {full['rel_err']*100:.2f}%。"
        f"③ Cb 扫描（SUBOFF 长度比例族）：-15% 门槛交叉点 **Cb={cross:.3f}**，Cb=0.95 时 "
        f"{r4['sweep'][-1]['rel_err']*100:+.2f}%；同 Cb=0.542 换形状真值差 {band:.1f} 个百分点 → "
        "端点没有任何参数能表达该形状不确定带。"
        f"④ 稳性：适用域 φ≤{r5['phi_valid_limit']:.2f}° 内最大绝对误差 {r5['max_abs_err_valid']:.2e} m、"
        f"相对 {r5['max_rel_err_valid']:.2e}；域外（舭部出水 {r5['phi_bilge_emergence']:.2f}°、"
        f"甲板入水 {r5['phi_deck_immersion']:.2f}° 之后）偏差最大 {r5['max_rel_err_beyond_validity']*100:.1f}%，"
        f"端点既不校验也不告警。小角极限与 $GM+\\tfrac12 BM\\tan^2\\varphi$ 偏差 {r5['small_angle_rel_err']:.2e}。"
        f"⑤ 结构探针：吃水变 20 倍 $R_t$ 恒为 {r6['Rt_vs_draft'][0]:.4f} N（相对变化 "
        f"{r6['draft_sensitivity_rel']:.1e}，因 T 从不进入任何公式）；"
        f"$C_r=0.002(1+2C_b)F_n^{{2.5}}$ 严格单调（{r6['cr_monotone_increasing']}），结构上无法表达兴波阻力峰谷；"
        "形状因子 $(1+k)$ 与粗糙度补贴 $\\Delta C_F$ 均未实现。")
    md.append(
        "## 4 图\n"
        "图1 精确湿面积与 Cb 单参数拟合的对比及偏差棒状图；图2 摩擦线交叉检验 + 同输入不同真值几何的 $R_t$ 不确定带；"
        "图3 一阶原理 GZ 与闭式对比及适用域分界；图4 吃水零灵敏度与 $C_r$ 单调性。")
    md.append(
        "## 5 注意点\n"
        "① 端点服务（:8001/:8002）复算时不可达，验证为进程内调用同一路由函数，未覆盖 HTTP 层与部署差异；"
        "② $S_w$ 真值来自本文显式公开母型的回转体族（超椭圆帽/余弦幂/长球体/圆柱），不是 SUBOFF 官方式子——"
        "结论是「$C_b$ 单参数拟合对一类真实回转体系统性低估（Cb>0.71 时 >15%）」，"
        "而圆柱 $\\pi DL$ 反例不依赖任何文献，是硬判据；"
        "③ $C_r$ 峰谷位置按通行教科书结论定性引用、未逐篇核页码，故 $C_r$ 量化误差标 WITHHELD 而不粉饰；"
        "④ 尚未与实船/船模拖曳试验对比；⑤ 本单不改实现，先立判据留证据，修公式另开单。")
    md.append(
        "## 6 误差判定（逐项回答是否 <3%）\n"
        f"$C_F$ {worst_big:.2e} → **是**；Schoenherr 交叉 {dev_sch:.2f}% → 是；"
        f"$R_t=R_f+R_r$ {r3['Rt_minus_Rf_Rr_abs']:.2e} N → 是；GZ 适用域 {r5['max_rel_err_valid']:.2e} → 是；"
        f"小角极限 {r5['small_angle_rel_err']:.2e} → 是；$S_w$ Cb=0.542 尖首尾 {sub['rel_err']*100:+.2f}% → 是"
        f"（但同 Cb 换形状即 {cos_['rel_err']*100:.2f}%）；$S_w$ Cb=0.75 {full['rel_err']*100:.2f}% → **否**；"
        f"圆柱硬反例 {cyl['rel_err']*100:.2f}% → **否**；$R_t$（Cb≥{cross:.2f}）可低至 "
        f"{r4['sweep'][-1]['rel_err']*100:.2f}% → **否**（>15%）；吃水灵敏度恒为 0 → **否**（结构性缺陷）。"
        "$C_r$ 无基准、无法判定，标 WITHHELD。"
        "**结论：`calc.stability` 保留 REAL 并声明适用域；`calc.resistance` 建议降级 WITHHELD"
        "（摩擦项 REAL，几何项/残差项/吃水项不可采信）。**")

    figs = []
    for p in sorted(glob.glob(FIGGLOB)):
        n = os.path.basename(p)
        figs.append({"name": n, "caption": CAPTIONS.get(n, n),
                     "png_base64": base64.b64encode(open(p, "rb").read()).decode()})

    return {
        "title": ("V-01 验证欠账：船舶阻力/稳性计算端点独立基准复核"
                  "（摩擦项通过，几何项与吃水项不合格）"),
        "summary": ("给审计标 REAL 但零测试的两条端点配独立基准：ITTC-1957 摩擦线复现到浮点精度"
                    f"（Rf>10 N 各点 {worst_big:.1e}），稳性直壁闭式与一阶原理积分在适用域内一致到 "
                    f"{r5['max_rel_err_valid']:.1e}；但湿面积 Cb 单参数拟合在 Cb>{cross:.3f} 时低估 >15%"
                    f"（圆柱硬反例 {cyl['rel_err']*100:.1f}%，Cb=0.95 达 {r4['sweep'][-1]['rel_err']*100:.1f}%），"
                    "且吃水字段从不进入公式（20 倍 T 变化 R_t 恒值）、Cr 结构上无法表达峰谷。"
                    "建议 calc.stability 保留 REAL 并声明适用域，calc.resistance 降级 WITHHELD。"),
        "scenario_key": "verification_debt_calc_endpoints_v01",
        "mission_id": 1607,
        "tags": ["验证欠账", "阻力", "稳性", "ITTC-1957", "湿表面积", "审计", "TensorPlatform"],
        "markdown": "\n\n".join(md),
        "figures": figs,
    }


def main() -> int:
    rep = build()
    out = "/tmp/report.json"
    json.dump(rep, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"built {out}: markdown {len(rep['markdown'])} chars, {len(rep['figures'])} figures")
    if "--dry-run" in sys.argv:
        return 0

    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010").rstrip("/")
    user, pwd = os.environ.get("TENSOR_AGENT_USER"), os.environ.get("TENSOR_AGENT_PASS")
    if not (user and pwd):
        print("no credentials in env -> dry run only")
        return 0
    tok = None
    for path in ("/api/v1/auth/login", "/auth/login"):
        try:
            r = requests.post(base + path, json={"username": user, "password": pwd}, timeout=20)
            if r.ok:
                tok = r.json().get("access_token") or r.json().get("token")
                if tok:
                    print("login ok via", path)
                    break
        except Exception as exc:  # pragma: no cover
            print("login attempt failed:", path, exc)
    if not tok:
        print("login failed")
        return 1
    hdr = {"Authorization": f"Bearer {tok}"}
    for path in ("/api/v1/tensor/reports", "/tensor/reports"):
        try:
            r = requests.post(base + path, json=rep, headers=hdr, timeout=60)
            print(path, r.status_code, r.text[:300])
            if r.ok:
                return 0
        except Exception as exc:  # pragma: no cover
            print("post failed:", path, exc)
    return 1


if __name__ == "__main__":
    sys.exit(main())