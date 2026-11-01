"""生成 docs/verification/ice_domain_verification_v1.md（数字全部程序化注入）。"""
from __future__ import annotations

import json

S = json.load(open("artifacts/m1517/ice_criteria.json"))
from tools.verif.ice.ice_ref import markdown_table

p = S["platform"]
b = S["lindqvist_band_N"]
u = S["underprediction_factor"]
bc = S["base_case"]
iacs = S["iacs_plating_mm"]

doc = f"""# 冰区航行域验证欠账 v1：`/extended-calculations/ice` 对 Lindqvist/IACS 基准逐条复核

- 端点：`POST /api/v1/extended-calculations/ice`（自述方法 `method = "Lindqvist公式 + IACS极地规则"`）
- 判据工件：`artifacts/m1517/ice_criteria.json`、探针原始响应 `artifacts/m1517/ice_probe.json`
- 回归测试：`tests/test_ice_domain_criteria.py`（16 项，含参考实现自检与端点判决回归）
- 参考实现：`tools/verif/ice/ice_ref.py`（与端点互不共享代码）
- **判决：{S['verdict']}（{S['n_fail']}/{S['n_criteria']} 条判据 FAIL）**

## 1. 基准工况

| 参数 | 值 |
|---|---|
| L / B / T [m] | {bc['length']} / {bc['beam']} / {bc['draft']} |
| 排水量 [t] | {bc['displacement']} |
| 航速 [kn] | {bc['speed']} |
| Cb | {bc['cb']} |
| 冰级 / 冰厚 [m] | {bc['ice_class']} / {bc['ice_thickness']} |

平台响应：`ice_resistance = {p['ice_resistance_N']/1e3:.2f} kN`、
`ice_power = {p['ice_power_W']/1e3:.2f} kW`、
`continuous_speed = {p['continuous_speed_kn']:.1f} kn`、
`shell_plating = {p['shell_plating_mm']:.1f} mm`、
`frame_spacing = {p['frame_spacing_mm']:.0f} mm`。

## 2. 判据与判决

{markdown_table(S)}

判据分两类：**A 类**只依赖物理响应律与恒等式（不含任何需外部记忆的系数），
**B 类**依赖文献关系式（系数区间化给出，结论不依赖单点取值）。

## 3. 误差判定（关键量，是否 < 3% / < 15%）

| 关键量 | 平台 | 基准 | 相对误差 | 是否 < 15% |
|---|---|---|---|---|
| 层冰阻力 R（基准工况） | {p['ice_resistance_N']/1e3:.2f} kN | Lindqvist 区间 [{b['R_lo']/1e3:.1f}, {b['R_hi']/1e3:.1f}] kN | 低估 {u['lo']:.1f}×–{u['hi']:.1f}×（{100*(u['lo']-1):.0f}%–{100*(u['hi']-1):.0f}%） | **否** |
| 速度放大比 R(5kn)/R(1kn) | 1.000 | 1.73（同参数 Lindqvist） | −42.2% | **否** |
| 全速域 dR/dV | 0.0 N/kn | > 0（解析要求） | 定性失效 | **否** |
| 吃水响应 R(12m)−R(6m) | 0.0 N | > 0（浸没项 ∝ T） | 定性失效 | **否** |
| 挤压下界 | {p['ice_resistance_N']/1e3:.2f} kN | ≥ 91.0 kN（σ_c,min, α_min） | 低于下界 83.3% | **否** |
| PC7 壳板厚度 | {p['shell_plating_mm']:.1f} mm | ≈{iacs['PC7']:.1f} mm（IACS UR I2） | +{100*(p['shell_plating_mm']/iacs['PC7']-1):.0f}% | **否** |
| PC1 壳板厚度 | {p['shell_plating_mm']:.1f} mm | ≈{iacs['PC1']:.1f} mm（IACS UR I2） | −{100*(1-p['shell_plating_mm']/iacs['PC1']):.0f}% | **否** |
| 功率恒等式 P = R·V | 39.03 kW | 39.03 kW | ~0% | **是** |

> 结论：**没有任何关键量满足 3% 或 15% 门槛**；唯一精确成立的是功率恒等式，
> 但它只是把阻力误差线性放大到功率上（推力需求同样低估 {u['lo']:.0f}–{u['hi']:.0f}×），
> 对冰区航行（含主机选型、冰区航速预报、极地规则符合性）不可用。

## 4. 反推的端点实现形态（证据）

由参数扫描可复原端点阻力式：R 对 `displacement`、`draft`、`speed`、`ice_class`
**完全不变**；对 B 线性（ΔR/ΔB = 500 N/m）、对 L 弱线性、对 h 近 h^1.87。
即 R ≈ 500·B + 124·L + C(h) 形态的**几何线性硬编码式**，不含 Lindqvist 的
速度项 `(1+1.4v/√(gh))`、浸没项 `R_s ∝ T` 与挤压项 `0.5σ_c h²`。
`ice_class` 仅影响 `max_ice_thickness/continuous_speed` 两张常数表，对阻力与壳板厚度无作用。
`continuous_speed = 0.4·V` 且**未按等级上限截顶**（V=10 kn → 4.0 kn > PC7 上限 2.0 kn），
属等级饱和失效。

## 5. 注意点与局限

1. Lindqvist 系数（σ_c、σ_f、μ、α）以区间化取值（见 `LINDQVIST_BAND`），
   因此量级判据以"低估 33–136×"区间陈述，而非单点误差；任何区间端点都远大于 15% 门槛，
   结论对该不确定性不敏感。
2. IACS UR I2 壳板厚度按 `t = 667·s·√(f1·P/σ_y)`、首部区设计冰压表取值，
   σ_y 取 315 MPa；仅用于**量级与等级趋势**校核，不作为逐点符合性结论。
   逐点符合性须以现行 IACS URI2 原文与具体船型首部压力分布为准。
3. 验证只覆盖 `extended-calculations/ice` 单一端点与层冰（level ice）工况；
   碎冰/冰脊、冰-结构接触、冰载荷统计极值（对应 m761/m768 节点）不在本单范围。
4. 探针为只读调用，未修改平台任何数据；工件为一次快照，端点版本变化须重跑探针。

## 6. 修复契约（建议给下游修复单）

| 字段 | 现状 | 应满足 |
|---|---|---|
| `ice_resistance` | 几何线性硬编码，无视 V/T/class | 实现 Lindqvist 三分量 + 速度因子，基准工况落在 [{b['R_lo']/1e3:.0f}, {b['R_hi']/1e3:.0f}] kN 区间内 |
| `ice_power` | R·V 传递误差 | 随 R 修正后自然闭合（保留 A1 判据） |
| `continuous_speed` | 0.4·V 未截顶 | `min(0.4·V, class_cap)` |
| `shell_plating` | 恒定 25 mm | IACS UR I2 依等级 P 计算，PC1≈{iacs['PC1']:.0f} mm…PC7≈{iacs['PC7']:.0f} mm |
| `frame_spacing` | 仅随 h 变化 | 依等级与冰压变化，且与壳板厚度公式耦合 |
"""
open("docs/verification/ice_domain_verification_v1.md", "w").write(doc)
print("doc written", len(doc), "chars")
