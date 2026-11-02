#!/usr/bin/env python3
"""Generate docs/m1690/REPORT.md and publish the platform report (numbers injected).

All numbers are read from ``artifacts/m1690/ice_resistance_evidence.json``
(produced by ``tools/verify_ice_resistance_evidence.py``) -- nothing is typed by
hand, so the report cannot drift from the artifacts.

    python3 tools/m1690_report.py [--publish]
"""

from __future__ import annotations

import base64
import glob
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "m1690")
DATA = json.load(open(os.path.join(ART, "ice_resistance_evidence.json"), encoding="utf-8"))
DOC = os.path.join(ROOT, "docs", "m1690", "REPORT.md")

S = DATA["summary"]
SHIPS = DATA["ships"]
NAMES = list(SHIPS)

FIG_CAPTIONS = {
    "fig1_resistance_vs_thickness.png":
        "图 1 阻力-冰厚关系：实线为公式原值（未标定），虚线为按单船试验锚点缩放后的值，"
        "空心圆为平台记录的实船试验均值（标定点）。锚点只能整体平移曲线。",
    "fig2_component_shares.png":
        "图 2 标定工况下的分量占比。submersion 含 0.3L 工程长度、speed 含 0.1 系数，"
        "二者合计占比即标注的“不可溯源份额”。",
    "fig3_circularity.png":
        "图 3 循环性证据：原始公式残差 vs 锚定后残差（对数轴）。锚定后残差 ~1e-16%，"
        "由构造强制为零，不构成精度证据。",
    "fig4_cross_model_band.png":
        "图 4 同一 (h, σ, μ, V) 下三种实现的对比：本平台 Lindqvist、独立 Lindqvist-like、"
        "Riska 型；黑色短横为平台记录试验均值。",
    "fig5_beam_scaling.png":
        "图 5 船宽广延性：bending 分量乘了 B，crushing 分量未乘，总阻力随 B 的指数 < 1。",
}


def pct(x: float, nd: int = 2) -> str:
    return f"{100.0 * x:+.{nd}f}%"


def build_markdown() -> str:
    rows_nominal = "\n".join(
        "| {n} | {h:.2f} | {s:.2f} | {mu:.2f} | {v:.2f} | {rt:.1f} | {raw:.1f} | {p} | {f:.4f} |".format(
            n=n, h=r["nominal"]["ice_thickness_m"], s=r["nominal"]["ice_strength_MPa"],
            mu=r["nominal"]["friction_coeff"], v=r["nominal"]["ship_speed_ms"],
            rt=r["nominal"]["trial_mean_kN"], raw=r["raw_kN"],
            p=pct(r["raw_residual_vs_trial"]), f=r["calibration_factor"],
        )
        for n, r in SHIPS.items()
    )
    rows_audit = "\n".join(
        "| {n} | {cal:.1e} | {hx:.3f} | {bx:.3f} | {u:.1f}% | {cm:.1f}% | {cf:.2f} |".format(
            n=n,
            cal=abs(r["calibrated_residual_vs_trial"]),
            hx=r["thickness_exponent"], bx=r["beam_exponent_total"],
            u=100 * r["untraceable_share"], cm=100 * r["cross_model_band_rel"],
            cf=r["counterfactual_ratio_vs_raw"],
        )
        for n, r in SHIPS.items()
    )
    rows_err = "\n".join(
        "| {n} 总阻力（未标定） | 实船试验均值 {rt:.0f} kN | {raw:.1f} kN，{p} | ❌ 否 |".format(
            n=n, rt=r["nominal"]["trial_mean_kN"], raw=r["raw_kN"],
            p=pct(r["raw_residual_vs_trial"]),
        )
        for n, r in SHIPS.items()
    )
    rows_terms = "\n".join(
        "| {n} | {c:.1f} | {b:.1f} | {s:.1f} | {v:.1f} |".format(
            n=n, c=r["terms_kN"]["crushing"], b=r["terms_kN"]["bending"],
            s=r["terms_kN"]["submersion"], v=r["terms_kN"]["speed"],
        )
        for n, r in SHIPS.items()
    )

    md = f"""# 冰区阻力链（Lindqvist）证据分级复核：单船试验锚点造成“名义零误差”（m1690）

**结论（先给判词）**：`ice-uq` 的确定性端点返回的是 **按单船实船试验均值锚定后的**
Lindqvist 估算值，标定工况残差恒等于 0（实测 {S['max_abs_calibrated_residual']:.1e} 量级），
这是构造性质而非验证结果。未锚定的公式原值对试验均值的偏差为
{', '.join(f"{n} {pct(r['raw_residual_vs_trial'])}" for n, r in SHIPS.items())}，
其中 Terry_Fox 超出 15% 验收线。**绝对精度判定：WITHHELD（不可主张）**；
模型仅在“单点锚定 + 结构不变量”层面受证。

## ① 案例介绍

对象：平台冰区阻力链 `backend/services/ice_resistance_uq.py`（Lindqvist 1989 型分层阻力
+ PCE/Smolyak 不确定度量化），对外经 `POST /api/v1/ice-uq/resistance` 与 `/ice-uq/uq` 暴露。
物理背景：层冰中船舶阻力分解为挤压（crushing）+ 弯曲破冰（bending）+ 浸没（submersion）
+ 破冰碎块水动力（speed）四项，是冰区航行战役（p9 / Wave 9，M0 地基校验阶段）的基础砖块。

算例为平台内置的三条实船参数（几何 + 标定工况 + 平台记录的试验均值）：

| 船 | h [m] | σ_c [MPa] | μ | V [m/s] | 试验均值 [kN] | 公式原值 [kN] | 原始偏差 | 锚定因子 |
|---|---|---|---|---|---|---|---|---|
{rows_nominal}

## ② 怎么做的

1. **只读审计**：新增 `tools/verify_ice_resistance_evidence.py`，从生产模块取值而不改默认路径，
   逐船测量（a）原始残差、（b）锚定后残差、（c）锚定因子及其离散度、（d）分量分解、
   （e）冰厚指数 `d lnR/d ln h`、（f）船宽指数 `d lnR/d ln B`、（g）单调性/分解守恒等不变量、
   （h）跨实现对撞（`backend/physics/ship_env_maneuvering/loads.py` 的 Riska 型与
   lindqvist_like 两套独立分解，同 (h, σ, μ, V) 输入）。全部数字落
   `artifacts/m1690/ice_resistance_evidence.json`，图件 5 张。
2. **最小代码改动**（不改物理公式）：把 `_lindqvist_raw` 拆出可审计的
   `lindqvist_terms()`（四项分解，求和与原值严格一致），新增公开函数
   `lindqvist_ice_resistance_uncalibrated()`，并把标定语义写进 docstring；
   确定性 API 响应新增 `raw_resistance_kN` / `calibration_factor` / `terms_kN`
   三个可选字段，使调用方一眼可见“返回值被锚定过”。
3. **回归锁定**：新增 `tests/test_ice_resistance_evidence_grade.py`（28 条），
   把“锚定工况残差为零是循环”“锚定因子不可复现尺度误差”“crushing 不带接触宽度”
   等结论钉成机器可判的断言，防止日后被重新包装成 REAL。

## ③ 计算结果

| 船 | 锚定后残差 | h 指数 | B 指数 | 不可溯源分量占比 | 跨实现带宽 | 若 crushing 补 B 的倍数 |
|---|---|---|---|---|---|---|
{rows_audit}

分量分解（标定工况，未锚定，单位 kN）：

| 船 | crushing | bending | submersion | speed |
|---|---|---|---|---|
{rows_terms}

结构性结论：

* **分解守恒**：Σ分量 − 原值 = {S['invariants']['terms_sum_max_rel_err']:.1e}（机器判定，无漂移）。
* **单调性**：h / V / μ 三个方向全部单调（{S['invariants']['monotone_all']}）。
* **冰厚指数 {S['thickness_exponent_range'][0]:.2f}–{S['thickness_exponent_range'][1]:.2f}**：介于
  浸没主导（∝h）与弯曲主导（∝h²）之间；锚定因子是标量，只能整体平移曲线，
  **无法修正厚度标度**，故非标定工况下的相对误差与原始值同级。
* **船宽指数 {S['beam_exponent_range'][0]:.2f}–{S['beam_exponent_range'][1]:.2f}（<1）**：bending 乘了 B，
  crushing 没有接触宽度因子，同一实现内部标度不自洽；若简单给 crushing 补上 B，
  总阻力将放大 {SHIPS['MV_Arctic']['counterfactual_ratio_vs_raw']:.1f}–{SHIPS['Polarstern']['counterfactual_ratio_vs_raw']:.1f} 倍，
  远超试验均值——说明该项结构不能靠猜测修补，只能回到原始文献（离线不可得）。
* **跨实现带宽 {S['cross_model_band_range'][0]*100:.0f}%–{S['cross_model_band_range'][1]*100:.0f}%**：
  三套实现之间的模型形式不确定性远大于 15% 验收线，任何“<15% 精度”宣称都无依据。

## ④ 结果图

见 figures：图 1 阻力-冰厚曲线（公式 vs 锚定 vs 试验点）；图 2 分量占比（标注不可溯源份额）；
图 3 循环性直方（对数轴，展示锚定后残差 ~1e-16%）；图 4 跨实现带宽 vs 试验均值；
图 5 船宽广延性（对数-对数，实测斜率 <1）。

## ⑤ 注意点 / 局限

1. **参照值本身未再验证**：三条船试验均值是平台内置记录（模块注释指向 Riska et al. 1997 /
   Kühnlein 1997 / Spencer & Jones 2001），本次离线环境无法取原文核对，故本单只主张“实现 vs
   平台记录值”的偏差，不主张对真实试验的精度。
2. **物理公式未改**：`0.3L`（浸没）与 `0.1`（速度项）两个常数无从溯源，crushing 缺接触宽度因子；
   在原文不可得的前提下任何“修正”都是猜测，因此本单只做暴露与定级，不做物理修补。
3. **锚定的双刃性**：`calibration_factor` 让名义点精确命中试验均值，工程上可用于保守包络，
   但因此产生的零残差**不能计入验证**；本单把它从“隐式 REAL”降为“单点锚定”。
4. **跨实现带宽不代表真值**：Riska 型与 lindqvist_like 的系数也在文献区间内自选，
   带宽只度量模型形式差异。
5. **PCE/UQ 链未受影响**：UQ 的 mean/std 误差是对 MC 参考（同一阻力模型）的，属于代理精度，
   与本单的物理精度问题是两件事；原 45 条 UQ 测试全绿。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 结果 | 是否 <3% |
|---|---|---|---|
{rows_err}
| 锚定后总阻力（标定工况） | 试验均值 | {S['max_abs_calibrated_residual']:.1e} | ⚠ 数值上满足，但**构造为零，不计入验证** |
| Σ分量 − 公式原值 | 代数恒等 | {S['invariants']['terms_sum_max_rel_err']:.1e} | ✅ 是 |
| 单调性（h/V/μ） | 物理符号约束 | 三方向单调 | ✅ 是（定性） |
| 船宽广延性 | 应 ∝B¹ | {S['beam_exponent_range'][0]:.2f}–{S['beam_exponent_range'][1]:.2f} | ❌ 否 |

**综合**：绝对量级精度 **<3% 不成立**（最优船亦有 {abs(min(r['raw_residual_vs_trial'] for r in SHIPS.values()))*100:.1f}% 原始偏差，且标度不自洽）；
证据等级由“REAL（隐含）”下调为 **WITHHELD（绝对精度）/ 单点锚定**。

## 复现

```
python3 tools/verify_ice_resistance_evidence.py          # 生成 artifacts/m1690/*
PYTHONPATH=backend python3 -m pytest tests/test_ice_resistance_evidence_grade.py -q   # 28 passed
PYTHONPATH=backend python3 -m pytest tests/test_pce_ice_resistance_uq.py -q           # 45 passed（回归）
```
"""
    return md


def publish(md: str) -> None:
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010").rstrip("/")
    tok = open("/tmp/tok.txt", encoding="utf-8").read().strip()
    figs = []
    for path in sorted(glob.glob(os.path.join(ART, "fig*.png"))):
        name = os.path.basename(path)
        figs.append({
            "name": name,
            "caption": FIG_CAPTIONS.get(name, name),
            "png_base64": base64.b64encode(open(path, "rb").read()).decode(),
        })
    body = {
        "title": "冰区阻力链（Lindqvist）证据分级复核：单船试验锚点造成名义零误差、crushing 缺接触宽度（m1690）",
        "summary": (
            f"ice-uq 确定性端点返回的是按单船试验均值锚定后的 Lindqvist 值，标定工况残差"
            f"{S['max_abs_calibrated_residual']:.1e} 系构造为零；未锚定公式原值与试验均值偏差 "
            + "、".join(f"{n} {pct(r['raw_residual_vs_trial'])}" for n, r in SHIPS.items())
            + f"（Terry_Fox 超 15% 线）；冰厚指数 {S['thickness_exponent_range'][0]:.2f}–{S['thickness_exponent_range'][1]:.2f}、"
            f"船宽指数 {S['beam_exponent_range'][0]:.2f}–{S['beam_exponent_range'][1]:.2f}（bending 乘 B、crushing 未乘，内部不自洽）；"
            f"跨实现带宽 {S['cross_model_band_range'][0]*100:.0f}%–{S['cross_model_band_range'][1]*100:.0f}%。"
            "已暴露 raw/factor/terms 三个 API 字段并加 28 条回归测试，绝对精度定级 WITHHELD。"
        ),
        "scenario_key": "ice_resistance_evidence_audit",
        "mission_id": 1690,
        "tags": ["ice", "lindqvist", "evidence-grade", "verification-debt", "withheld"],
        "markdown": md,
        "figures": figs,
    }
    req = urllib.request.Request(
        base + "/tensor/reports",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    print("report published:", json.dumps(resp, ensure_ascii=False)[:300])


def main() -> int:
    md = build_markdown()
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    open(DOC, "w", encoding="utf-8").write(md)
    print("wrote", DOC, len(md), "chars")
    if "--publish" in sys.argv:
        publish(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())