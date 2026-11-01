"""构建并发布 m1517 论文级报告（数字从 artifacts 工件读入，图 base64 内嵌）。"""
from __future__ import annotations

import base64
import json
import pathlib

import requests

ROOT = pathlib.Path(__file__).resolve().parents[3]
S = json.load(open(ROOT / "artifacts/m1517/ice_criteria.json"))
p, b, u = S["platform"], S["lindqvist_band_N"], S["underprediction_factor"]
rows = {c["id"]: c for c in S["criteria"]}
iacs = S["iacs_plating_mm"]
FIG = ROOT / "artifacts/m1517/figs"


def fig(name, caption):
    return {"name": name, "caption": caption,
            "png_base64": base64.b64encode((FIG / name).read_bytes()).decode()}


table = "\n".join(
    f"| {c['id']} | {c['criterion']} | {c['value']} | {c['reference']} | **{c['verdict']}** |"
    for c in S["criteria"])

md = f"""## ① 案例介绍

冰区航行是平台九大学科之一，`POST /api/v1/extended-calculations/ice` 是其唯一入口，
响应自述方法为 `Lindqvist公式 + IACS极地规则`。本单对其做**文献/解析基准复核**（验证欠账类）。

基准工况（一艘 25 000 t 冰级油船量级，PC7）：

| 参数 | L [m] | B [m] | T [m] | Δ [t] | V [kn] | Cb | 冰级 | 冰厚 [m] |
|---|---|---|---|---|---|---|---|---|
| 值 | 150 | 23 | 8.5 | 25000 | 5 | 0.75 | PC7 | 1.0 |

平台响应：`ice_resistance = {p['ice_resistance_N']/1e3:.2f} kN`、`ice_power = {p['ice_power_W']/1e3:.2f} kW`、
`continuous_speed = {p['continuous_speed_kn']:.1f} kn`、`shell_plating = {p['shell_plating_mm']:.1f} mm`、
`frame_spacing = {p['frame_spacing_mm']:.0f} mm`。

## ② 怎么做的

1. **只读探针**（`tools/verif/ice/probe_ice.py`）：固定基准工况，做 31 个单变量变体
   （h∈[0.3,2.0] m、V∈[1,10] kn、PC1–PC7、Δ∈[10k,50k] t、B、L、T），原始响应落盘
   `artifacts/m1517/ice_probe.json`。
2. **独立参考实现**（`tools/verif/ice/ice_ref.py`，与端点不共享代码）：
   Lindqvist (1989) 三分量
   R_c=0.5σ_c h²(tanα+μ)、R_b=0.003σ_f B h^1.5 (L/T)^0.6、
   R_s=μ(ρ_w−ρ_i) g h B (T+B tanα/2)，
   R=(R_c+R_b)(1+1.4v/√(gh))+R_s(1+9.4v/√(gL))；
   IACS UR I2 壳板 t=667·s·√(f1·P/σ_y)。系数**区间化**（σ_c 0.5–1.5 MPa、
   σ_f 0.3–0.7 MPa、μ 0.05–0.15、α 20°–35°），故量级结论以区间给出。
3. **11 条判据**分 A 类（系数无关：功率恒等式、dR/dV、速度放大比、厚度标度指数、
   吃水响应、挤压下界、等级饱和、等级单调、位移一致性）与 B 类（Lindqvist 量级、IACS 壳板）。
4. 工具链：Python3 + numpy/matplotlib（图）+ pytest（回归 16 项）。

## ③ 计算结果（判据判决表）

| 判据 | 内容 | 平台实测 | 基准/参考 | 判决 |
|---|---|---|---|---|
{table}

参考实现量级：Lindqvist 区间 **[{b['R_lo']/1e3:.1f}, {b['R_hi']/1e3:.1f}] kN**，
平台 {p['ice_resistance_N']/1e3:.2f} kN，低估 **{u['lo']:.1f}×–{u['hi']:.1f}×**。

**判决：{S['verdict']}（{S['n_fail']}/{S['n_criteria']} FAIL）**

由图可复原端点实现形态：R 对 Δ、T、V、冰级**完全不变**，对 B 线性（500 N/m）、对 h 近 h^1.87，
即 `R ≈ 500·B + 124·L + C(h)` 的几何线性硬编码式；`continuous_speed = 0.4·V` 且未按等级截顶。

## ④ 结果图

图 1：层冰阻力–冰厚，平台（红）与独立 Lindqvist 参数区间（蓝带，对数纵轴）——平台低 1.5–2 个量级。
图 2：左=速度响应（平台恒为 15.17 kN 水平线，Lindqvist 随 V 上升）；右=等级饱和失效（V=10 kn 返回 4.0 kn，越 PC7 上限 2.0 kn）。
图 3：左=判据判决矩阵（A 类标 A、B 类标 B）；右=壳板厚度 vs 等级（平台恒 25 mm，IACS 依等级 PC1≈{iacs['PC1']:.0f} mm→PC7≈{iacs['PC7']:.0f} mm）。

## ⑤ 注意点与局限

1. Lindqvist 系数区间化，量级判据以"低估 {u['lo']:.0f}–{u['hi']:.0f}×"陈述；区间任一端点都远超 15% 门槛，结论对该不确定性不敏感。
2. IACS 壳板厚度用 t=667·s·√(f1·P/σ_y)、首部区设计冰压表、σ_y=315 MPa，仅作量级与等级趋势校核，不作逐点符合性结论。
3. 覆盖面：单端点 + 层冰工况；碎冰/冰脊、冰-结构接触与冰载荷极值（m761/m768）不在本单范围。
4. 探针只读，未改平台数据；工件为快照，端点版本变化须重跑。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 误差 | <3%? | <15%? |
|---|---|---|---|
| 层冰阻力 R（基准工况） | 低估 {u['lo']:.1f}–{u['hi']:.1f}×（{100*(u['lo']-1):.0f}%–{100*(u['hi']-1):.0f}%） | 否 | 否 |
| 速度放大比 R(5kn)/R(1kn) | 1.000 vs 1.73 → −42.2% | 否 | 否 |
| dR/dV | 0.0 N/kn（定性失效） | 否 | 否 |
| 吃水响应 R(12m)−R(6m) | 0.0 N（定性失效） | 否 | 否 |
| 挤压下界 | 低于下界 83.3% | 否 | 否 |
| PC7 壳板厚度 | {p['shell_plating_mm']:.1f} mm vs ≈{iacs['PC7']:.1f} mm → +{100*(p['shell_plating_mm']/iacs['PC7']-1):.0f}% | 否 | 否 |
| PC1 壳板厚度 | {p['shell_plating_mm']:.1f} mm vs ≈{iacs['PC1']:.1f} mm → −{100*(1-p['shell_plating_mm']/iacs['PC1']):.0f}% | 否 | 否 |
| 功率恒等式 P=R·V | ~0% | 是 | 是 |

**显式回答：没有任何物理关键量满足 3% 或 15% 门槛**；唯一精确成立的是恒等式 P=R·V（A1 PASS），
但它只把阻力误差线性传到功率：冰区推进功率/推力需求同样低估 1.5–2 个量级。
因此该端点**不足以支撑**冰区航速预报、主机选型或极地规则符合性判断；
交付修复契约（§docs/verification/ice_domain_verification_v1.md 第 6 节）供下游修复单直接使用。
"""

payload = {
    "title": "冰区航行域验证欠账清偿 v1（D5 判决 MOCK）：/extended-calculations/ice 对 Lindqvist/IACS 复核——阻力低估 33–136×、dR/dV≡0、吃水不参与、壳板厚度对等级恒定",
    "summary": (f"只读探针 31 变体 + 独立 Lindqvist/IACS 参考实现 + 11 条闭式判据："
                f"冰区端点判决 MOCK（{S['n_fail']}/{S['n_criteria']} FAIL）。层冰阻力 "
                f"{p['ice_resistance_N']/1e3:.2f} kN vs Lindqvist 区间 "
                f"[{b['R_lo']/1e3:.0f},{b['R_hi']/1e3:.0f}] kN，低估 {u['lo']:.0f}–{u['hi']:.0f}×；"
                f"dR/dV≡0、吃水/位移不参与、连续速度越过 PC7 上限、壳板厚度对等级恒定 25 mm。"
                f"交付参考实现/判据工件/16 项 pytest/3 图/修复契约。"),
    "scenario_key": "ice_navigation",
    "mission_id": 1517,
    "tags": ["ice", "verification", "lindqvist", "iacs-polar-class", "mock-adjudication", "m1517"],
    "markdown": md,
    "figures": [
        fig("fig1_R_vs_h.png", "层冰阻力–冰厚：平台端点 vs 独立 Lindqvist 参数区间（对数纵轴）"),
        fig("fig2_velocity_and_cap.png", "左：速度响应平台 dR/dV≡0；右：continuous_speed 未按等级上限截顶"),
        fig("fig3_verdicts_and_plating.png", "左：11 条判据判决矩阵；右：壳板厚度 vs IACS UR I2 估值"),
    ],
}

s = requests.Session()
tok = s.post("http://127.0.0.1:8010/api/v1/auth/login",
             json={"username": "eco_admin", "password": "EcoTest!2026pass"}, timeout=30)
tok.raise_for_status()
token = tok.json()["access_token"]
r = s.post("http://127.0.0.1:8010/api/v1/tensor/reports", json=payload, timeout=120,
           headers={"Authorization": f"Bearer {token}"})
print(r.status_code, r.text[:400])
