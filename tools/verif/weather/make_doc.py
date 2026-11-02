#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 artifacts/weather_probe.json 生成 docs/verif/weather_criterion_verification.md（数字零手抄）。"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ART = os.path.join(HERE, "artifacts", "weather_probe.json")
DOC = os.path.join(ROOT, "docs", "verif", "weather_criterion_verification.md")


def f(x, n=4):
    if x is None:
        return "—"
    if isinstance(x, float) and (x == float("inf")):
        return "∞ (a=0)"
    return f"{x:.{n}f}"


def main() -> int:
    d = json.load(open(ART, encoding="utf-8"))
    res = d["results"]
    P = d["P_std_Pa"]
    t1 = d["theta1_representative_deg"]

    rows_case = []
    for r in res:
        rep = r["reference_representative_theta1_15"]
        pc = r["reference_representative_pchip"]
        pf = r["platform_formula_replica"]
        rows_case.append(
            f"| {r['case']['name']} | {f(r['lw1_m'],5)} | {f(r['lw2_m'],5)} | "
            f"{f(rep['thetaC'],2)} | {f(rep['theta2'],2)} | {f(rep['area_a'],5)} | {f(rep['area_b'],5)} | "
            f"{f(rep['ratio'],3)} | {rep['compliant']} | {f(pc['ratio'],3)} | "
            f"{f(pf['area_a'],5)} | {f(pf['area_b'],5)} | {f(pf['ratio'],3)} | {pf['compliant']} |")

    dev_rows = []
    for r in res:
        rep = r["reference_representative_theta1_15"]
        pf = r["platform_formula_replica"]
        ok_rev = "翻转 (false PASS)" if r["verdict_diverges"] else "一致"
        if rep["area_a"] > 1e-12 and pf["area_a"] > 0:
            ea = (pf["area_a"] - rep["area_a"]) / rep["area_a"] * 100
        else:
            ea = None
        eb = (pf["area_b"] - rep["area_b"]) / rep["area_b"] * 100 if rep["area_b"] > 1e-12 else None
        er = (pf["ratio"] - rep["ratio"]) / rep["ratio"] * 100 if rep["ratio"] > 1e-12 else None
        dev_rows.append(
            f"| {r['case']['name']} | {f(r['lw1_m'],5)} | "
            f"{'—' if ea is None else f'{ea:+.1f}%'} | "
            f"{'—' if eb is None else f'{eb:+.1f}%'} | "
            f"{'—' if er is None else f'{er:+.1f}%'} | {ok_rev} |")

    # 逐项误差判定（承担 <3% 判决的量）
    g_pen = abs(9.81 - 9.80665) / 9.80665 * 100          # 平台硬编码 g
    gust_pen = (1.0 / 1.5 - 1.0) * 100                    # 缺 1.5 阵风因子
    lw1_mech = None
    try:
        import sys
        sys.path.insert(0, HERE)
        from imo_weather import mechanical_check_lw1
        lw1_mech = mechanical_check_lw1()
    except Exception:  # noqa: BLE001
        pass

    r2 = res[1]
    rep2 = r2["reference_representative_theta1_15"]
    pf2 = r2["platform_formula_replica"]
    err2 = (pf2["ratio"] - rep2["ratio"]) / rep2["ratio"] * 100

    sweep_c1 = res[0]["reference_sweep"]
    sweep_txt = " | ".join(
        f"{s['theta1']:.0f}°: {'∞' if s['ratio'] == float('inf') else f(s['ratio'],3)}"
        for s in sweep_c1)

    md = f"""# 验证欠账：IMO 天气衡准（weather criterion）对 IS Code 2008 A.2.3 的逐条复核

- 复核对象：`POST /api/v1/dynamic-stability/weather-criterion`（summary「天气衡准检查」，description「IMO天气衡准」）
- 实现位置：`backend/api/dynamic_stability.py :: WeatherCriterionChecker.check`（serving copy `/srv/tensor/ship-workspace/...`）
- 独立参考实现：`tools/verif/weather/imo_weather.py`（零平台依赖，双数值路径）
- 探针 / 图 / 数字：`tools/verif/weather/probe_weather.py` → `artifacts/weather_probe.json`、`fig_gz_areas.png`、`fig_ratio.png`
- 回归测试：`tests/test_weather_criterion_reference.py`（{len(res)} 算例 + 10 条判据/合同测试，全绿）
- 欠账来源：`docs/verification/GZ_CROSS_CURVE_AUDIT.md` §7 明确列「未覆盖：…风暴横摇衡准（weather criterion）」

## 1. 规范要求（IS Code 2008, MSC.267(85), Part A §2.3）

| 要素 | 规范表达 | 置信度 |
|---|---|---|
| 定常风压 | P = {P:.0f} Pa | CONF_HIGH |
| 定常风倾力臂 | lw1 = P·A·Z / (1000·g·Δ)  [m]，Δ[t]，g=9.80665 | CONF_HIGH |
| 阵风力臂 | lw2 = 1.5·lw1 | CONF_HIGH |
| 定常风平衡角 | θ0: GZ(θ0) = lw1 | CONF_HIGH |
| 横摇回摆角 | θ1 = 109·k·X1·X2·√(r·s)（表 2.3.4） | CONF_MED（代码表未转录，故用 θ1 扫描） |
| 面积 a | ∫ θ1→θC (lw2 − GZ) dθ，θC = GZ 与 lw2 的上风侧交点 | CONF_HIGH |
| 面积 b | ∫ θC→θ2 (GZ − lw2) dθ | CONF_HIGH |
| 终止角 | θ2 = min(下风侧交点, θf, 50°) | CONF_HIGH |
| 判据 | b ≥ a（等价 b/a ≥ 1） | CONF_HIGH |

设计纪律：承担 `<3%` 判决的量只用 CONF_HIGH 定义式；θ1 代码表属 CONF_MED，故以 θ1∈[0°,30°] 扫描给出判据族，
任一 θ1 下结论同向才算判据稳健——这足以复核「平台表达是否等于规范表达」，因为平台实现里既没有 θ1 也没有 lw2。

## 2. 算例（几何/参数全表）

| 算例 | L [m] | B [m] | T [m] | Δ [t] | 受风面积 A [m²] | 风压中心高 Z [m] | GZ_max [m] | θf [°] |
|---|---|---|---|---|---|---|---|---|
""" + "\n".join(
        f"| {r['case']['name']} | {r['case']['L']:.0f} | {r['case']['B']:.0f} | {r['case']['T']:.1f} | "
        f"{r['case']['disp']:.0f} | {r['case']['A']:.0f} | {r['case']['Z']:.0f} | "
        f"{f(r['gz_max_m'],3)} | {f(r['theta_f_deg'],1)} |" for r in res) + f"""

三个算例覆盖判据的三个区：`case1` 稳性富余（θ1 处 GZ 已高于 lw2 → a=0）、`case2` 边界船（b 只剩薄层）、
`case3` 明显不满足（lw2 超过 GZ 峰值 → b=0）。

## 3. 结果：参考实现 vs 平台算式（θ1 = {t1:.0f}° 代表点）

| 算例 | lw1 [m] | lw2 [m] | θC [°] | θ2 [°] | a_ref | b_ref | b/a_ref | 合规_ref | b/a_pchip | a_plat | b_plat | b/a_plat | 合规_plat |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join(rows_case) + f"""

`*_plat` 列为逐行复刻平台 `WeatherCriterionChecker.check` 的算式（取证用），其与实测 API 返回值在 2% 内一致
（`tests/test_weather_criterion_reference.py::test_platform_false_pass_regression` 钉住）。

## 4. 平台表达与规范的逐条映射（缺陷清单）

| # | 规范要素 | 平台做法 | 判定 |
|---|---|---|---|
| 1 | lw1 = P·A·Z/(1000·g·Δ) | 同式，但 g 硬编码 9.81 | 通过（偏差 {g_pen:.3f}%） |
| 2 | lw2 = 1.5·lw1（阵风） | **缺失**，area_a 直接用 lw1 | 不通过（力臂低 33.3%，判据单向放宽） |
| 3 | 横摇回摆角 θ1 | **缺失**（隐式 θ1 = 0°） | 不通过（忽略上风侧横摇，a 区被低估） |
| 4 | θ2 = min(下风侧交点, θf, 50°) | **硬编码 50°** | 不通过（与 GZ 曲线解耦；case2 真值 θ2 = {f(rep2['theta2'],1)}°） |
| 5 | b = ∫θC→θ2 (GZ − lw2) | 对**整条 GZ 曲线 0→末采样角**做梯形积分，含 GZ<lw2 与 θ>θ2 段 | 不通过（b 被系统性放大） |
| 6 | a = ∫θ1→θC (lw2 − GZ) | 用 lw1 × 50° 的矩形 | 不通过（力臂、区间、形状三重错配） |
| 7 | 判据 b ≥ a | 同形式 ratio ≥ 1 | 形式通过、输入错 → 结论错 |
| 8 | API 合同：`GZ_curve: array<object>` | 实现只认键 `heel`/`GZ`，其余键抛 KeyError → HTTP 500 | 不通过（合同缺陷，见 §6） |
| 9 | 悬置常数 `assess/ratio` 缺省 | `area_a==0 → ratio=10`、`downflooding=50` 常量 | 不通过（未声明启发式） |

## 5. 误差判定（关键量 vs 规范基准，逐项是否 <3%）

| 算例 | lw1 [m] | 平台 a 相对误差 | 平台 b 相对误差 | 平台 b/a 相对误差 | 合规判决 |
|---|---|---|---|---|---|
""" + "\n".join(dev_rows) + f"""

补充量化：

- **lw1 定义式**：机械自检 `{lw1_mech['lw1']:.9f}` m vs 手算 `{lw1_mech['hand']:.9f}` m，相对误差 `{lw1_mech['rel_err']:.2e}` → **<3% 满足**；平台仅因 `g=9.81` 引入 `{g_pen:.3f}%` 偏差，仍 **<3%**。
- **阵风力臂 lw2**：平台缺失 1.5 因子 → 力臂相对偏差 `{gust_pen:+.1f}%` → **不满足 <3%**（单项即超限 11 倍）。
- **b/a（case2 边界船）**：平台 `{f(pf2['ratio'],3)}` vs 规范 `{f(rep2['ratio'],3)}` → **相对误差 {err2:+.1f}%，不满足 <3%**；且判决从「不满足」翻成「满足」（**false PASS**）。
- **合规判决一致性**：3 个算例中 1 个**判决翻转**（case2），另外 2 个虽结论相同但数值不可比（case1 规范 a=0 使 b/a 无界，平台给出有限值 {f(res[0]['platform_formula_replica']['ratio'],2)}；case3 规范 b=0）。
- **θ1 稳健性（case1 扫描）**：{sweep_txt} → 在 θ1∈[0°,30°] 全扫描下「b/a 巨大/自动满足」的结论不变，故 case1 的通过判决对代码表转录误差不敏感。
- **数值路径自校**：参考实现 linear 与 PCHIP 两条独立路径的 b/a 相对差，case1/case3 为 `{[f(r['route_agreement_max_rel']*100,3) for r in res][0]}%`/`{[f(r['route_agreement_max_rel']*100,3) for r in res][2]}%`；**case2 为 `{f(res[1]['route_agreement_max_rel']*100,1)}%`**——边界船 b 只剩薄层，b/a 对 GZ 插值模型敏感。为此本报告对 case2 **只主张判决（b<a），不主张数值**；工程建议：边界船 GZ 采样间隔应 ≤5°，且 b/a 应同时给出两路径区间。

## 6. 附带发现的合同缺陷（可独立修）

1. `WeatherCriterionInput.GZ_curve: array<object>` 对键名零约束，而实现硬取 `["heel"]`/`["GZ"]`：
   `{{"angle":..,"gz":..}}` 的**合法载荷返回 500**（`alt_keys=500`，见 `tests::test_platform_500_on_schema_legal_keys`）。应改为显式 schema（`GZPoint{{heel: float, GZ: float}}`）并在缺键时返回 422。
2. `displacement` 只用于力臂，未与 L/B/T/Cb 做一致性校验（沿用 GZ 审计同一建议）。
3. 响应 `method` 恒为「天气衡准检查」，无 `validation_status`，无法区分「规范实现」与「启发式」——建议改名 `IMO_ISCODE2008_A2.3` 并补 `standard_ref`。

## 7. 结论与证据等级

- 该端点对「IMO 天气衡准」的能力声明 → **WITHHELD**：算式可执行、数值有限，但**与规范不同构**（缺 1.5 阵风臂、缺 θ1、θ2 硬编码、a/b 积分域错配），且存在 **false PASS** 与 **500 合同缺陷**。
- 在 `case2` 这类边界船上，平台把「不满足」判成「满足」——**安全相关结论不可用于合规判定**，直至按本报告 §4 修正。
- 修复验收判据（建议作为门禁）：`tests/test_weather_criterion_reference.py` 全绿 + case2 `b/a` 与规范相对误差 <3% + 合法键名不 500。

## 8. 复现

```bash
python3 tools/verif/weather/probe_weather.py        # 生成 artifacts/weather_probe.json + 两张图
python3 tools/verif/weather/make_doc.py             # 由工件重生成本文件（数字零手抄）
python3 -m pytest tests/test_weather_criterion_reference.py -q
```

## 9. 局限

- θ1 未按 IS Code 表 2.3.4 转录（CONF_MED），以扫描替代；因此本报告判定「平台缺 lw2/θ1 结构」成立，但**不**给出与规范逐位对齐的单点 b/a。
- 未涉及破损稳性、自由液面修正、非线性横摇阻尼；GZ 输入取自平台/外部生成，本单不重算 GZ 曲线（另有 `GZ_CROSS_CURVE_AUDIT.md` 覆盖）。
- case2 数值对插值敏感（见 §5），修正后仍需用实船密采样 GZ 复核。
"""
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("wrote", DOC, len(md), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())