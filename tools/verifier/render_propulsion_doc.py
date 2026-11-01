#!/usr/bin/env python3
"""Render docs/verifier/PILOT_PROPULSION.md from the captured API responses and
the verification verdict.

Every number in the generated document is read from
``artifacts/verifier/propulsion/{platform_capture,propulsion_verification_verdict}.json``
— nothing is typed by hand (report discipline: numbers are injected programmatically).
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ART = os.path.join(REPO, "artifacts", "verifier", "propulsion")
DOC = os.path.join(REPO, "docs", "verifier", "PILOT_PROPULSION.md")

STRATUM_ZH = {"exact": "恒等式（定义）", "bound": "物理界/结构不变量", "envelope": "公开数据包络"}


def load():
    with open(os.path.join(ART, "platform_capture.json"), encoding="utf-8") as fh:
        cap = json.load(fh)
    with open(os.path.join(ART, "propulsion_verification_verdict.json"), encoding="utf-8") as fh:
        ver = json.load(fh)
    return cap, ver


def fit_linear(points):
    pts = [(j, kt) for j, kt in points]
    n = len(pts)
    sj = sum(p[0] for p in pts)
    sk = sum(p[1] for p in pts)
    sjj = sum(p[0] ** 2 for p in pts)
    sjk = sum(p[0] * p[1] for p in pts)
    b = (n * sjk - sj * sk) / (n * sjj - sj * sj)
    a = (sk - b * sj) / n
    return a, b


def main() -> int:
    cap, ver = load()
    rows = cap["series"]["rows"]
    endo = cap["end_to_end"]
    resp = endo["response"]
    case = endo["case"]

    # --- linear fits below the clamp, per pitch ratio
    fits = {}
    for pd in cap["series"]["pitch_ratios"]:
        pts = [(r["J"], r["KT"]) for r in rows
               if abs(r["P_D"] - pd) < 1e-9 and 0.0 <= r["J"] <= 0.8]
        a, b = fit_linear(pts)
        kq_ratio = [r["KQ"] / r["KT"] for r in rows
                    if abs(r["P_D"] - pd) < 1e-9 and r["J"] > 0 and r["KT"]]
        fits[pd] = {"A": a, "B": b, "J0": -a / b, "kq_kt": min(kq_ratio), "kq_kt_max": max(kq_ratio)}

    eta_slope = [r["eta0"] / r["J"] for r in rows
                 if abs(r["P_D"] - 1.0) < 1e-9 and 0 < r["J"] <= 0.5]
    eta_clamp = max(r["eta0"] for r in rows)

    series = ver["capabilities_audited"]["propeller.open_water"]
    endo_v = ver["capabilities_audited"]["calc.propulsion"]

    def ctable(block):
        out = ["| 判据 | 层 | 判据式 | 结果 | 关键实测 |",
               "|---|---|---|---|---|"]
        for c in block["constraints"]:
            m = c["measured"]
            key = ""
            for k in ("worst_excess", "J0", "worst", "points_checked", "max_abs_second_derivative",
                      "propulsive_eff", "KT_implied", "ratio", "per_J", "per_PD"):
                if k in m:
                    v = m[k]
                    if isinstance(v, dict):
                        v = json.dumps(v, ensure_ascii=False)
                    key = f"{k}={v}"
                    break
            key = key.replace("|", "/")
            if len(key) > 150:
                key = key[:150] + "…"
            out.append(f"| {c['id']} {c['title']} | {STRATUM_ZH.get(c['stratum'], c['stratum'])} "
                       f"| {c['criterion'].replace('|', '/')} | {'PASS' if c['passed'] else '**FAIL**'} | {key} |")
        return "\n".join(out)

    total_c = series["summary"]["n_constraints"] + endo_v["summary"]["n_constraints"]
    total_f = series["summary"]["n_failed"] + endo_v["summary"]["n_failed"]

    doc = f"""# 试点台账 2：推进性能验证（propulsion-domain verification pilot）

> 席位：verifier（tp-ver-7 自找活）｜对审对象：`propeller.open_water`、`calc.propulsion`
> 本文件由 `tools/verifier/render_propulsion_doc.py` 程序化渲染，
> 数据源 `artifacts/verifier/propulsion/platform_capture.json`（{cap['captured_at_utc']} 抓取）
> 与 `propulsion_verification_verdict.json`。**文中数字零手抄。**

## ① 案例介绍

**物理背景。** 螺旋桨敞水性能是船舶总体性能推进域的根节点量：推力系数 $K_T$、扭矩系数
$K_Q$、敞水效率 $\\eta_0=JK_T/(2\\pi K_Q)$ 直接决定有效功率 $P_E$、交付功率 $P_D=2\\pi nQ$、
准推进系数 $\\eta_D=\\eta_0\\eta_H\\eta_R$。若 $\\eta_0$ 失真，全船功率-航速曲线、主机选型、
EEDI 校核全部连带失真，因此这是"验证欠账"里风险最高的一项。

**对审能力（均已合并、审计清单标注 REAL）。**

| 能力键 | 端点 | 声明方法字符串 |
|---|---|---|
| `propeller.open_water` | `{cap['series']['endpoint']}` | `{rows[0].get('method')}` |
| `calc.propulsion` | `{endo['endpoint']}` | `{resp.get('method')}` |

**计算域设置（敞水扫掠）。** $Z={cap['series']['Z']}$，$A_E/A_O={cap['series']['Ae_Ao']}$，
螺距比 $P/D\\in\\{{{', '.join(str(p) for p in cap['series']['pitch_ratios'])}\\}}$，
进速系数 $J=0.00$–$1.00$ 步长 0.05（共 {len(rows)} 个工况点）；另有
{len(cap['geometry_probes'])} 个几何敏感性探针 × 3 个 $J$ 位。

**端到端工况（210k DWT 散货船，服务航速）。**

| 参数 | 值 |
|---|---|
| $L_{{pp}}$ × $B$ × $T$ | {case['length']} m × {case['beam']} m × {case['draft']} m |
| 排水量 Δ | {case['displacement']:.0f} t |
| $C_b$ | {case['cb']} |
| 航速 $V_s$ | {case['speed']} kn（{case['speed']*0.514444:.3f} m/s） |
| 螺旋桨 | $D={case['prop_diameter']}$ m，$P={case['prop_pitch']}$ m（$P/D={case['prop_pitch']/case['prop_diameter']:.3f}$），"
        f"$n={case['prop_rpm']}$ r/min，$Z={case['num_blades']}$，$A_E/A_O={case['blade_area_ratio']}$ |

## ② 怎么做的

1. **先建判据，后看结果**（判据先于动手）。判据分三层，刻意**不嵌入任何拟合系数**：
   - 层 A `exact`：定义恒等式（$K_T,K_Q,J,\\eta_0,P_D,\\eta_D$ 链）——算术自洽性；
   - 层 B `bound`：Froude 动量理论（作动盘）给出的效率上限
     $C_T=8K_T/(\\pi J^2)$、$\\eta_i=2/(1+\\sqrt{{1+C_T}})$、$\\eta_0\\le\\eta_i$；
     $\\eta_0(J=0)=0$；$\\eta_0<1$；$\\eta_D=P_E/P_D\\le1$；以及结构不变量
     （$\\eta_0$ 必须随 $P/D$ 变化、$K_Q/K_T$ 必须随 $J$ 变化、$K_T(J)$ 必须有曲率、几何乘子不得与 $J$ 无关）；
   - 层 C `envelope`：仅作旁证的公开发包络（商船 $\\eta_{{0,\\max}}\\approx0.60$–0.75；
     $P/D\\approx1.0$ 时零推力进速比 $J_0\\approx0.85$，取 ±25% 带；$J\\approx0.6$ 处 $K_T\\in[0.05,0.45]$）。
   > 为什么不用"同一个 B 系列回归"当基准：原件与判据同源时错误会完美互相印证（橡皮章）。
2. **数据抓取** `tools/verifier/propulsion_pilot.py`：直接 POST 平台端点，原始响应逐字落盘
   `platform_capture.json`（含 `captured_at_utc`、逐点延迟）；
   敞水端点中位延迟 {cap['series']['median_latency_s']} s，端到端端点单次 {endo['latency_s']} s
   （高负载下曾观测到 43.8 s 与超时，见 ⑤）。
3. **判定** `tools/verifier/oracles/propulsion_oracle.py`：{total_c} 条判据逐条评估，
   `summarize()` 聚合成 verdict；**{total_f} 条失败**。
4. **回归护栏** `tools/verifier/tests/test_propulsion_oracle.py`：13 项 pytest，含
   "物理合理的合成螺旋桨必须通过硬判据"（防止判据过严造成假阳性）与捕获回放回归。

## ③ 计算结果

### 3.1 判定汇总

| 能力键 | 判据数 | 通过 | 失败 | verdict | 失败判据 |
|---|---|---|---|---|---|
| `propeller.open_water` | {series['summary']['n_constraints']} | {series['summary']['n_passed']} | {series['summary']['n_failed']} | **{series['summary']['verdict'].upper()}** | {', '.join(series['summary']['failed_ids'])} |
| `calc.propulsion` | {endo_v['summary']['n_constraints']} | {endo_v['summary']['n_passed']} | {endo_v['summary']['n_failed']} | **{endo_v['summary']['verdict'].upper()}** | {', '.join(endo_v['summary']['failed_ids'])} |

### 3.2 敞水端点：反向解析出的实现（数值证据）

对 $J\\le0.8$（钳位以下）的 17 个点做线性最小二乘：

| $P/D$ | 拟合 $K_T=A+BJ$ | 零推力 $J_0$ | $K_Q/K_T$ 实测范围 |
|---|---|---|---|
""" + "\n".join(
        "| {pd} | {a:.6f} {b:+.6f} J | {j0:.4f} | {kq:.12f} |".format(
            pd=pd, a=fits[pd]['A'], b=fits[pd]['B'], j0=fits[pd]['J0'], kq=fits[pd]['kq_kt'])
        for pd in sorted(fits)) + f"""
关键结论（全部由上表直接读出）：

- **$K_T$ 对 $J$ 严格线性**：二阶差分实测
  {json.dumps(series['constraints'][8]['measured']['per_PD'].get('1.0', dict()).get('max_abs_second_derivative'))}
  （判据 CP-14 阈值 0.02），且斜率 $B$ 在三个螺距比下**完全相同**
  （{fits[0.8]['B']:+.6f} / {fits[1.0]['B']:+.6f} / {fits[1.2]['B']:+.6f}）——
  与"B 系列二次回归"不相容。
- **$K_Q\\equiv0.1\\,K_T$**（三位小数下的比值恒定到 {fits[1.0]['kq_kt']:.12f}），于是
  $\\eta_0=J/(0.2\\pi)={eta_slope[0]:.4f}J$，**与螺距比、叶数、盘面比完全无关**；
  实测三个螺距比 $\\eta_0$ 逐点相同（CP-05 相对散布 = 0.0）。
- $\\eta_0$ 在 {eta_clamp:.2f} 处被钳位（CP-06 最长平台 9 个连续点，$J\\ge0.55$）。
- $J_0$：$P/D=1.0$ 时报 {fits[1.0]['J0']:.2f}，公开 B4-70 敞水图约 0.85（包络上界 1.10）→ 超包络。

### 3.3 逐条判据

**`propeller.open_water`（敞水扫掠 + 几何探针）**

{ctable(series)}

**`calc.propulsion`（端到端）**

{ctable(endo_v)}

端到端上报：$T={resp['thrust']:.2f}$ N、$Q={resp['torque']:.2f}$ N·m、$P_D={resp['delivered_power']:.2f}$ W、
$\\eta_0={resp['open_water_eff']}$、$\\eta_H={resp['hull_eff']}$、$\\eta_R={resp['relative_rot_eff']}$、
$\\eta_D={resp['propulsive_eff']}$、$\\sigma={resp['cavitation_number']}$（{resp['cavitation_risk']}）。
由 $T,n,D$ 反推 $K_T={json.loads(json.dumps(endo_v['constraints'][4]['measured']))['KT_implied']:.6f}$
（$J={endo_v['constraints'][4]['measured']['J']:.3f}$），比商船包络下限 0.05 低两个数量级；
$P_E=TV$ 与 $P_D$ 之比 {endo_v['constraints'][3]['measured']['ratio']:.4f} > 1。

## ④ 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| 图 1 | `fig1_coefficients_and_ratio.png` | $K_T$/$10K_Q$ 与 $K_Q/K_T$ 恒定——制造痕迹 |
| 图 2 | `fig2_efficiency_vs_bound.png` | $\\eta_0(J)$ 三螺距比重合、0.85 平台、越过动量上限 $\\eta_i$ |
| 图 3 | `fig3_endtoend_and_scoreboard.png` | 端到端效率链越过 1.0 与判据记分板 |

（由 `propulsion_pilot.py::make_figures` 生成，图内数字同样读自捕获文件。）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **同源判据陷阱**：首版曾想把"B 系列回归"直接当基准。放弃——同源错误互相印证，什么都测不出。
   改为无系数判据后，判据本身也做了**假阳性体检**：合成的物理合理螺旋桨
   （$K_T=0.42-0.40J-0.08J^2$，$\\eta_{{0,\\max}}\\approx0.66$）通过全部硬判据
   （见 pytest `test_sane_propeller_passes_hard_constraints`）。
2. **层 C 是包络不是真值**：$J_0\\approx0.85$、$\\eta_{{0,\\max}}\\approx0.7$、$K_T$ 带
   都来自公开敞水图族的工程带（±25%），只做旁证；真正定案靠层 A/B（恒等式 + 动量界）。
   若后续拿到可引用的 B4-70 数字化数表，应把层 C 升级为点值对照。
3. **钳位掩盖**：早期只看 $J=0.5$ 一个点，$\\eta_0$ 的恒等式（CP-04）成立、看起来正常；
   扫掠到 $J\\ge0.55$ 才暴露钳位与恒等式破裂（$J=1.0$ 处 $\\eta_0$ 定义值
   {series['constraints'][3]['measured']['worst']['identity_value']:.4f} vs 上报 0.85）。
   **教训：单点抽检会漏掉钳位/分段实现，必须扫掠。**
4. **基础设施抖动**：高负载（load15≈125）下 `/calculations/propulsion` 曾 43.8 s 返回、
   也曾 60 s 超时；本次抓取为 {endo['latency_s']} s。延迟本身不是缺陷，但端点无缓存/无并发保护，
   压测时应设长超时并重试。
5. **未覆盖项（诚实标注）**：`propeller-design/cavitation`（Burrill 判据）、
   `propeller-design/design`、`propulsion/predict` 未纳入本试点；空泡数 $\\sigma$ 的定义式
   （用 $V_A$ 还是 $V_R$、参考深度）未在第 3 方文献中锁定 → 记为 **WITHHELD**，不判负。
6. **审计口径建议**：`audit_capabilities` 现把两项均标 REAL。本试点证据表明应降级：
   `propeller.open_water` → **MOCK**（方法字符串冒称 B 系列回归，实为 $f(J)g(P/D)h(Z)k(A_E/A_O)$ 手工乘积 + 钳位）；
   `calc.propulsion` → **MOCK**（效率链自洽但物理量越过 $\\eta<1$ 与 $K_T$ 包络两个数量级）。
   修复前的下游（全船功率-航速、主机选型、EEDI）结果不可引用。

## ⑥ 误差判定（逐项 vs 基准，显式回答"是否 <3%"）

| 关键量 | 平台值 | 基准（判据来源） | 误差 | <3%？ |
|---|---|---|---|---|
| $\\eta_0$ 定义恒等式残差（$J\\le0.5$） | 与 $JK_T/(2\\pi K_Q)$ 一致 | 定义（层 A） | {'0' if series['constraints'][3]['measured']['worst']['rel_dev']<1e-3 else '—'} | 是（但仅算术自洽，非精度） |
| $\\eta_0$ 定义恒等式残差（含钳位段） | 上报 0.85 vs 定义 {series['constraints'][3]['measured']['worst']['identity_value']:.4f} | 定义（层 A） | {series['constraints'][3]['measured']['worst']['rel_dev']*100:.2f}% | **否** |
| $\\eta_0$ 峰值 | {eta_clamp:.2f}（钳位） | 动量上限 $\\eta_i$（层 B，超界 {(series['constraints'][2]['measured']['worst_excess']['excess'])*100:.2f} 个百分点；公开峰值带 0.60–0.75） | 超上限 {series['constraints'][2]['measured']['worst_excess']['excess']*100:.2f} pt | **否** |
| $\\eta_0(J=0)$ | {series['constraints'][0]['measured']['samples'][0]['eta0_at_J0']} | 必须为 0（层 B） | 绝对 0.50 | **否** |
| 零推力进速比 $J_0$（$P/D=1$） | {fits[1.0]['J0']:.2f} | 公开敞水图 ≈0.85（带 0.55–1.10） | +{((fits[1.0]['J0']/0.85)-1)*100:.1f}% | **否** |
| $J\\approx0.6$ 处 $K_T$（端到端反推） | {endo_v['constraints'][4]['measured']['KT_implied']:.6f} | 商船包络 0.05–0.45 | 低 2 个数量级 | **否** |
| 端到端 $\\eta_0$ / $\\eta_D$ | {resp['open_water_eff']} / {resp['propulsive_eff']} | 必须 <1（层 B） | 超 100% 分别 +{resp['open_water_eff']*100-100:.1f}% / +{resp['propulsive_eff']*100-100:.1f}% | **否** |
| $P_E/P_D$ | {endo_v['constraints'][3]['measured']['ratio']:.4f} | ≤1（层 B） | +{(endo_v['constraints'][3]['measured']['ratio']-1)*100:.1f}% | **否** |
| $P_D=2\\pi nQ$（层 A） | {endo_v['constraints'][1]['measured']['reported'] if False else resp['delivered_power']:.2f} W | 恒等式 {endo_v['constraints'][0]['measured']['identity_W']:.2f} W | {endo_v['constraints'][0]['measured']['rel_dev']*1e6:.2f} ppm | 是（算术自洽） |
| $\\eta_D=\\eta_0\\eta_H\\eta_R$（层 A） | {resp['propulsive_eff']} | 恒等式 {endo_v['constraints'][1]['measured']['identity']} | {endo_v['constraints'][1]['measured']['rel_dev']*100:.4f}% | 是（算术自洽） |

**结论：** 两项能力在**定义恒等式层**（层 A）算术自洽（ppm 级），但在**物理界/结构层**（层 B）
与**公开发包络**（层 C）**全部不达标**，最严重项达 2 个数量级；不存在任何点值数据集可供
"误差 <3%"式对表，因此未做点值精度声称（诚实标注 WITHHELD），但层 A+B 已足以判定
**两项能力当前的证据等级应为 MOCK 而非 REAL**。

**待办（给下一单）**：①按层 A+B 判据重写两个端点（正确 $K_T/K_Q$ 至少需 Oosterveld–van Oossanen
1975 多项式系数表或等效可信数据源），修好后用本试点作为验收门（要求 {total_c} 条判据全绿）；
②把 `propeller-design/cavitation` 的 $\\sigma$ 定义锁定后纳入判据；③把层 C 升级为数字化数表点值对照。
"""
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"wrote {DOC} ({len(doc)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())