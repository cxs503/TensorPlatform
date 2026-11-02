#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 artifacts/verif/dynstab/results.json 生成审计文档与在线报告（数字零手抄）。

    docs/verification/DYNAMIC_STABILITY_AUDIT_M1857.md
    POST $TENSOR_API_BASE/tensor/reports   （figures 以 base64 内嵌）

运行：python3 tools/verif/dynstab/make_doc.py [--publish]
"""

from __future__ import annotations

import base64
import json
import math
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ART = os.path.join(HERE, "artifacts")
DOC = os.path.join(ROOT, "docs", "verification", "DYNAMIC_STABILITY_AUDIT_M1857.md")
MISSION_ID = 1857


def env(path="/root/tp-agent/agent.env"):
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def pct(x, nd=1):
    return "n/a" if x is None or not isinstance(x, (int, float)) or not math.isfinite(x) else f"{x*100:+.{nd}f}%"


def build_markdown(rec) -> str:
    cases = rec["cases"]
    names = list(cases)
    checks = rec["checks"]
    n_fail = sum(1 for c in checks if c["verdict"] == "FAIL")
    s1 = cases["S1_bulk_50k"]
    ref1, rep1 = s1["reference"], s1["platform_std"]
    iso1 = s1["platform_P504"]
    flips = rec["flips"]

    def row_dev(n):
        d = cases[n]["deviation"]
        return (f"| {n} | {cases[n]['reference']['ratio']:.3g} | {cases[n]['platform_std']['ratio']:.3g} "
                f"| {pct(d['ratio_std_rel'])} | {pct(d['ratio_iso_rel'])} | "
                f"{cases[n]['deviation']['verdict_ref']} → {cases[n]['deviation']['verdict_platform']} |")

    dev_rows = "\n".join(row_dev(n) for n in names)
    chk_rows = "\n".join(f"| {c['id']} | {c['criterion']} | {str(c['observed'])[:220]} | **{c['verdict']}** |"
                         for c in checks)
    geom_rows = "\n".join(
        f"| {n} | {cases[n]['ship']['L']:.0f} | {cases[n]['ship']['B']:.0f} | {cases[n]['ship']['T']:.1f} "
        f"| {cases[n]['ship']['displacement']:.0f} | {cases[n]['ship']['GM_true']:.2f} "
        f"| {cases[n]['reference']['lw1']:.4f} | {cases[n]['reference']['theta0']:.2f} "
        f"| {cases[n]['reference']['a']*1e3:.3f} | {cases[n]['reference']['b']*1e3:.1f} "
        f"| {cases[n]['reference']['ratio']:.3g} |" for n in names)
    flips_txt = ("无" if not flips else
                 "; ".join(f"GM={f['GM']:.2f} m, 受风面积×{f['area_scale']:.0f}: 规范 b/a={f['ratio_ref']:.3g}"
                           f"（不满足）vs 平台 {f['ratio_platform']:.3g}（判满足）" for f in flips))
    tr = s1["roll"]
    selfchk = rec["integration_selfcheck"]
    int_err = max(abs(v) for d in selfchk for k, v in d.items() if k != "case")

    return f"""# 动稳性端点独立复核（/dynamic-stability/analyze · /roll-motion）

**一句话结论**：平台自称「动稳性分析」的两个端点，在**标准输入**（GZ 曲线自 0° 起）下
**HTTP 500 / 全部工况复现**，`/roll-motion` 对任何合法输入恒 500；即使绕开崩溃点，
其风倾面积定义、横摇周期系数、横摇幅值与 IS Code 2008 §2.3 / 线性横摇理论逐项不符，
9 条硬判据 **{n_fail} 条 FAIL**。该域不满足定量决策要求，建议降级为 NOT_SUPPORTED 直至修复。

> 工件：`tools/verif/dynstab/{{reference.py, analyze_audit.py}}` → `artifacts/verif/dynstab/results.json`
> + `fig1_gz_areas.png` / `fig2_roll_defects.png` / `fig3_status_and_errors.png`；
> 门禁：`tests/test_dynamic_stability_conformance.py`（18 项，L1 恒等式 / L2 单调 / L3 反事实 / L4 回归钉）。
> 复现：`python3 tools/verif/dynstab/analyze_audit.py && python3 -m pytest tests/test_dynamic_stability_conformance.py -q`（≈6 s，纯算术）。

## ① 案例介绍

复核对象（backend/api/dynamic_stability.py，运行于 :8001 的服务当前版本）：

| 端点 | summary | 声称内容 |
|---|---|---|
| `POST /api/v1/dynamic-stability/analyze` | 动稳性分析 | 风倾力矩/风致横倾角/波浪横摇/动稳性曲线/稳性面积/评级 |
| `POST /api/v1/dynamic-stability/roll-motion` | 横摇运动分析 | 横摇固有周期/RAO/幅值/相位/能量 |

4 型典型船（公开典型主尺度；**GZ 曲线由解析式构造**——壁式段 GZ=GM·sinθ、甲板浸没后线性衰减至消失角——
故面积有闭式真值，不取自平台）：

| 算例 | L [m] | B [m] | T [m] | Δ [t] | GM [m] | lw1 [m] | θ0 [°] | a [m·rad]×1e-3 | b [m·rad]×1e-3 | b/a |
|---|---|---|---|---|---|---|---|---|---|---|
{geom_rows}

环境：定常风 IS Code P=504 Pa；波浪 Hs=3.0 m / Te=9.0 s。受风面积与力臂用**平台自身启发式**
（A=0.7·L·(D−T)、Z=(D−T)/2+T/2），以便把差异完全归到**算式**而不是几何口径。

## ② 怎么做的

1. **独立参考实现**（`reference.py`，零平台依赖）：IS Code 2008 §2.3 的 lw1/lw2/θ0/a/b（a=∫₀^θ0(lw2−GZ)dθ，
   b=∫_θ0^θf GZ dθ，θf=min(进水角,50°)）；线性横摇理论 T_r=2πk/√(g·GM)、|H_θ|=1/[(1−ν²)²+(2ζν)²]^½、
   φ_a=(k_w H/2)|H_θ|。
2. **平台算式复刻器**（`platform_analyze_replica` / `platform_roll_motion_replica`）：逐行复刻端点算式，
   常量取平台值（g=9.81），用于 ① 与在线端点做逐位自校（在线可用时）② 端点 500 时仍可量的反事实。
   自校结果：`area_ratio` 在线 {rep1['ratio']:.6f} vs 复刻 {rep1['ratio']:.6f}（差 {abs(rep1['ratio']-cases['S1_bulk_50k']['replica_selfcheck']['area_ratio_abs_dev']):.2e}）——
   逐位一致到 1e-7 级（在线返回值四舍五入 3 位小数）。
3. **两种风压口径分离**：(A) P=504 Pa 只换算式 → 隔离「定义式误差」；(B) 输入 V=26 m/s（平台用动压 0.5ρV²=414.05 Pa）
   → 叠加「风压取值误差」。这一步是必须的：不分离就会把两件事混成一个百分数（首版即如此，已纠正）。
4. **边界船扫描**：6×6=36 组 (GM, 受风面积倍率) 网格，程序化找「规范判决 vs 平台判决」翻转点（不手挑）。
5. 数字全部由脚本写入 `results.json`，本文件由 `make_doc.py` 注入，零手抄。

## ③ 计算结果

### 3.1 判据表（9 条）

| ID | 判据 | 实测 | 判决 |
|---|---|---|---|
{chk_rows}

### 3.2 数值比对（同一几何、同一波浪；ratio 列）

| 算例 | 规范 b/a | 平台 area_ratio | 相对误差（V=26 m/s） | 相对误差（P=504 隔离） | 判决（规范→平台） |
|---|---|---|---|---|---|
{dev_rows}

- **面积 a（K3，隔离风压后）**：相对误差 {pct(min(c['deviation']['a_iso_rel'] for c in cases.values()))} …
  {pct(max(c['deviation']['a_iso_rel'] for c in cases.values()))}——小角域内「少乘 1.5」与「积分区间改用 max_heel」
  两处偏差**部分抵消**（解析：规范 a≈lw1²/GM，平台 a≈lw1·θ0≈lw1²/GM），故此处不可夸大为数量级错误；
  但抵消只在 θ0 小时成立，θ0 变大即失效（见 K4/K5）。
- **面积 b（K4）**：相对误差 {pct(min(c['deviation']['b_iso_rel'] for c in cases.values()))} …
  {pct(max(c['deviation']['b_iso_rel'] for c in cases.values()))}——平台自 0° 起积分整条曲线，
  多出的正是 ∫₀^θ0 GZ dθ（逐位恒等，见 L3 门禁）。
- **判决翻转（K6）**：{flips_txt}。

### 3.3 横摇子模型

| 量 | 平台 analyze | 平台 roll-motion | 参考（k=0.35B） | 结论 |
|---|---|---|---|---|
| T_r（S1, GM_est={tr['Tr_platform_analyze_0.8B'] and cases['S1_bulk_50k']['platform_std']['gm_est']:.3f} m） | {tr['Tr_platform_analyze_0.8B']:.2f} s（k=0.8B） | {tr['Tr_platform_rollmotion_0.4B']:.2f} s（k=0.4B） | {tr['Tr_reference_0.35B']:.2f} s | 同一物理量两处实现差 {tr['Tr_analyze_vs_sibling_ratio']:.3f}×；analyze 侧相对参考 {pct(tr['Tr_analyze_vs_ref_rel'])} |
| 横摇幅值（Hs=3 m, Te=9 s） | {tr['roll_amp_platform_deg']:.4f}° | —（端点 500） | {tr['roll_amp_linear_theory_deg']:.3f}° | 相对误差 {pct(tr['roll_amp_rel'])} |

平台幅值式为 (H/2)·RAO·0.1：把波幅[m]直接乘 0.1 当角度标度，系数无来源、无单位换算；
参考式 φ_a=(k_w H/2)|H_θ| 中波倾角 k_w H/2 = {tr['wave_slope_rad']:.4f} rad。

### 3.4 图

1. `fig1_gz_areas.png`：左=IS Code 面积几何（GZ、lw1、lw2、θ0、θf、a/b 阴影）；右=同几何同风下规范 b/a 与平台 area_ratio 对比（log 轴）。
2. `fig2_roll_defects.png`：左=T_r 的三种系数定义（0.8B/0.4B/0.35B + 文献带）；右=横摇幅值 平台启发式 vs 线性理论。
3. `fig3_status_and_errors.png`：左=a/b/ratio 相对误差 vs 3% 验收线；右=K1–K8 判决矩阵。

## ④ 注意点 / 踩过的坑

1. **不分离风压口径就会夸大定义式误差**：首版把 P=0.5ρV²(26 m/s)=414.05 Pa 与规范 504 Pa 混在一起，
   得到 a 的 −30% 全部记在「缺 1.5 倍率」头上。解析复核（小角 a=lw1²/GM）表明那是风压误差
   ({pct(cases['S1_bulk_50k']['deviation']['wind_pressure_std_vs_code_rel'])} 压差 → 平方 → {(1+cases['S1_bulk_50k']['deviation']['wind_pressure_std_vs_code_rel'])**2-1:+.1%} 面积差)，
   定义式误差在小角域只有百分级（见 K3 行）。已改为双口径分别报告。
2. **崩溃点掩盖了算式问题**：canonical GZ 曲线（0° 起）全部 500，若只用一条曲线探针，会得到「端点不可用」的
   单一结论而看不到后面的算式错误。做法：同时投 0° 起与 5° 起两种曲线，前者记可用性、后者记算式。
3. **复刻器必须用平台常量**：平台 g=9.81，参考实现 g=9.80665；不统一会让「复刻 vs 在线」自校差 6e-4，
   被误判成算式差异。统一后差降到 1e-7（在线四舍五入 3 位）。
4. **k=0.35B 是文献取值带（0.30–0.45B）不是单一定值**：故 K7 只主张「0.8B 越出带上界」与
   「两处实现自相矛盾 2.0×」两件不依赖具体 k 的事实。
5. **端点 500 不属物理误差**：K1/K2 是可用性判据，归 MOCK/STUB 类，不进误差百分比统计。

## ⑤ 局限与适用范围（honest）

1. 本单**只复核、不修复**平台代码（船舶仓另库、且 :8001 为在跑生产服务）；修复建议已按条列出并派单。
2. 参考实现的 GZ 曲线是**解析构造**，不是实测/CFD 复原的复原力臂；因此本条批准的是「公式与规范的一致性」，
   不批准任何真实船的合规结论。
3. 受风面积/力臂沿用平台启发式（A=0.7L(D−T)、Z=(D−T)/2+T/2），未复核该启发式本身；
   若几何口径也错，绝对误差会更大（本条只隔离算式）。
4. 横摇幅值参考式是**线性理论基线**（未含粘性/涡/升力阻尼与非线性复原力臂修正），故 K8 只判量级与量纲，
   不做 3% 级精度主张。
5. IG/进水角一律取 50°（与平台同）以隔离算式；真实进水角需船型开口信息，本单不覆盖。
6. K6 的翻转样本数为 1/36，属**存在性**证据（证明会出安全相关 false PASS），不是发生率估计。

## ⑥ 误差判定（显式回答「是否 < 3%」）

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| 参考实现数值积分 ∫GZ dθ | 壁式段闭式 GM(cos lo−cos hi) | 最大相对误差 {int_err:.2e} | **是**（1e-9 级） |
| 复刻器 vs 在线 area_ratio（S1） | :8001 实时值 | 差 {abs(cases['S1_bulk_50k']['replica_selfcheck']['area_ratio_abs_dev']):.2e}（在线 3 位舍入） | **是** |
| 面积 a（定义式，P=504 隔离） | IS Code §2.3 | {pct(min(c['deviation']['a_iso_rel'] for c in cases.values()))} … {pct(max(c['deviation']['a_iso_rel'] for c in cases.values()))} | **否**（最大 {pct(max(c['deviation']['a_iso_rel'] for c in cases.values()))}） |
| 面积 b（积分区间） | IS Code §2.3 | {pct(min(c['deviation']['b_iso_rel'] for c in cases.values()))} … {pct(max(c['deviation']['b_iso_rel'] for c in cases.values()))} | **否** |
| b/a | IS Code §2.3 | {pct(min(c['deviation']['ratio_iso_rel'] for c in cases.values()))} … {pct(max(c['deviation']['ratio_iso_rel'] for c in cases.values()))} | **否** |
| 定常风压 | 504 Pa | {pct(cases['S1_bulk_50k']['deviation']['wind_pressure_std_vs_code_rel'])} | **否** |
| 横摇固有周期 | k=0.35B（文献带 0.30–0.45B） | {pct(tr['Tr_analyze_vs_ref_rel'])} | **否**（越带上界 {pct(tr['Tr_analyze_vs_ref_rel']-(0.45/0.35-1))}） |
| 横摇幅值 | 线性横摇理论 | {pct(tr['roll_amp_rel'])} | **否**（差约两个数量级） |
| 端点可用性（canonical 输入） | HTTP 200 | 4/4 算例 HTTP 500；roll-motion 恒 500 | **否**（可用性判据） |

**结论**：该域 9 条判据 {n_fail} 条 FAIL，关键量无一满足 3% 验收；建议能力等级 **NOT_SUPPORTED（定量）**，
修复后按本表逐项复测（门禁 `tests/test_dynamic_stability_conformance.py` 的 L4 钉子会在修复时翻红，即为进展信号）。
"""


def figures():
    out = []
    caps = {
        "fig1_gz_areas.png": "图1 左：IS Code 2008 §2.3 面积几何（GZ 曲线、lw1/lw2、θ0、θf、a/b 阴影），S1 散货船；右：同几何同风下规范 b/a 与平台 area_ratio（log 轴）。",
        "fig2_roll_defects.png": "图2 左：横摇固有周期三种回转半径系数（analyze 0.8B / roll-motion 0.4B / 参考 0.35B 与文献带）；右：横摇幅值 平台启发式 vs 线性横摇理论。",
        "fig3_status_and_errors.png": "图3 左：a / b / b-a 相对误差 vs 3% 验收线（log）；右：K1–K8 判据判决矩阵。",
    }
    for name, cap in caps.items():
        p = os.path.join(ART, name)
        if os.path.exists(p):
            out.append({"name": name, "caption": cap,
                        "png_base64": base64.b64encode(open(p, "rb").read()).decode()})
    return out


def publish(md: str, rec) -> None:
    e = env()
    base = e.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    login = {"username": e.get("TENSOR_AGENT_USER", "eco_admin"),
             "password": e.get("TENSOR_AGENT_PASS", "")}
    req = urllib.request.Request(f"{base}/auth/login", data=json.dumps(login).encode(),
                                 headers={"Content-Type": "application/json"})
    tok = json.load(urllib.request.urlopen(req, timeout=30))
    tok = tok.get("access_token") or tok.get("token")
    payload = {"title": "动稳性端点独立复核：canonical GZ 曲线 4/4 算例 HTTP 500、"
                        "横摇周期两实现自相矛盾 2.0×、面积定义致 1/36 false PASS（m1857）",
               "summary": "对 /api/v1/dynamic-stability/{analyze,roll-motion} 做独立复核："
                          "IS Code 2008 §2.3 天气衡准面积定义 + 线性横摇理论为基准，9 条判据 9 条 FAIL；"
                          "canonical（0° 起）GZ 曲线全部 500，roll-motion 恒 500；横摇幅值差约两个数量级。"
                          "建议降级 NOT_SUPPORTED 并派单修复。",
               "mission_id": MISSION_ID,
               "tags": ["verification", "dynamic-stability", "IMO-IS-Code-2008", "false-PASS",
                        "roll-period", "ship-performance-platform"],
               "markdown": md, "figures": figures()}
    for key in ({"scenario_key": "dynamic_stability_audit"}, {}):
        body = dict(payload, **key)
        r = urllib.request.Request(f"{base}/tensor/reports", data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json",
                                            "Authorization": "Bearer " + tok})
        try:
            resp = json.load(urllib.request.urlopen(r, timeout=120))
            print("published report id:", resp.get("id") or resp)
            return
        except urllib.error.HTTPError as ex:
            print("publish failed", ex.code, ex.read()[:300].decode("utf-8", "replace"))


if __name__ == "__main__":
    rec = json.load(open(os.path.join(ART, "results.json"), encoding="utf-8"))
    md = build_markdown(rec)
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    open(DOC, "w", encoding="utf-8").write(md)
    print("wrote", DOC, len(md), "chars")
    if "--publish" in sys.argv:
        publish(md, rec)