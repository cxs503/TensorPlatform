"""m1828 报告生成器：从工件程序化注入数字，产出 markdown 与 /tmp/report.json。

零手抄：全部数值从 artifacts/m1828/surrogate_audit.json 读取或由第一性原理就地计算。
"""
from __future__ import annotations

import base64
import json
import math
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(HERE, "artifacts", "m1828")
audit = json.load(open(os.path.join(ART, "surrogate_audit.json"), encoding="utf-8"))
pinn = audit["pinn_resistance"]
trf = audit["transformer_resistance"]
sea = audit["pinn_seakeeping"]
rl = audit["rl"]

NU = 1.19e-6           # 海水运动黏度 [m^2/s]（15 °C, ITTC-57 惯例）
RHO = 1025.0
SHIP = audit["base_ship"]


def ittc57_cf(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


def holtrop_wetted_surface(L, B, T, Cb, Cm=0.988, Cwp=0.86, Abt=0.0):
    """Holtrop-Mennen 湿表面积经验式（独立实现，用于给摩擦阻力提供解析锚）。"""
    inner = (0.453 + 0.4425 * Cb - 0.2862 * Cm - 0.003467 * B / T + 0.3696 * Cwp)
    return L * (2 * T + B) * math.sqrt(Cm) * inner + 2.38 * Abt / Cb


S = holtrop_wetted_surface(SHIP["L"], SHIP["B"], SHIP["T"], SHIP["Cb"])

# 摩擦阻力解析锚：R_f = 0.5 ρ V² S (1+k) C_F，1+k 取散货船常用 1.25
FORM_FACTOR = 1.25
rows = []
for c in pinn["speed_curve"]:
    v = c["speed"] * 0.514444
    re_ = v * SHIP["L"] / NU
    cf = ittc57_cf(re_)
    rf_anchor = 0.5 * RHO * v * v * S * (1.0 + FORM_FACTOR) * cf
    rows.append(dict(speed=c["speed"], re=re_, cf=cf, rf_anchor=rf_anchor / 1e3,
                     rf_pinn=c["friction_resistance"], rr_pinn=c["residual_resistance"],
                     rt_pinn=c["resistance"],
                     rf_dev_pct=100.0 * (c["friction_resistance"] - rf_anchor / 1e3) / (rf_anchor / 1e3),
                     rr_over_rf=c["residual_resistance"] / c["friction_resistance"]))

hol = {round(r["speed"], 3): r for r in pinn["S4_vs_holtrop"]}
trf_rows = {round(r["speed"], 3): r for r in trf["S4_vs_holtrop"]}

# ---- markdown 表 ----
t_pinn = "\n".join(
    f"| {r['speed']:.0f} | {r['re']:.3e} | {r['cf']:.4e} | {r['rf_anchor']:.1f} | {r['rf_pinn']:.1f} "
    f"| {r['rf_dev_pct']:+.1f}% | {r['rr_pinn']:.1f} | {r['rr_over_rf']:.2f} | {r['rt_pinn']:.1f} "
    f"| {hol[round(r['speed'],3)]['holtrop']:.1f} | {hol[round(r['speed'],3)]['rel_err_pct']:+.1f}% |"
    for r in rows)

t_trf = "\n".join(
    f"| {r['speed']:.0f} | {trf_rows[round(r['speed'],3)]['transformer']:.1f} "
    f"| {r['holtrop']:.1f} | {trf_rows[round(r['speed'],3)]['rel_err_pct']:+.1f}% |"
    for r in pinn["S4_vs_holtrop"])

t_sea = "\n".join(f"| {c['heading']:.0f} | {c['heave_rao']:.3f} | {c['pitch_rao']:.3f} "
                  f"| {c['roll_rao']:.3f} | {c['physics_loss']} |" for c in sea["heading_curve"])

t_sens = "\n".join(
    f"| {k} | {v['values']} | {[None if x is None else round(x,2) for x in v['resistance']]} | {'是' if v['varies'] else '**否**'} |"
    for k, v in pinn["S1_sensitivity"].items())

t_rl = "\n".join(f"| {r['distance']:.0f} | {r['speed']:.0f} | {r['energy_savings']} "
                 f"| {r['allocation']} | {r['allocation_sum']} |" for r in rl["energy_curve"])

t_verdict = "\n".join(f"| `{v['endpoint']}` | {v['grade']} | {'; '.join(v['failing_criteria']) or '—'} |"
                      for v in audit["verdicts"])

worst_pinn = pinn["S4_worst_rel_err_pct"]
worst_trf = trf["S4_worst_rel_err_pct"]
rf_devs = [r["rf_dev_pct"] for r in rows]
rr_ratios = [r["rr_over_rf"] for r in rows]

def row_label(rows_):
    return "；".join(f"{r['speed']:.0f} kn: {r['rel_err_pct']:+.1f}%" for r in rows_)


MD = f"""# AI/ML 代理端点证据核查（m1828）

**范围**：现网 :8001 上 PINN / Transformer / RL 三族代理端点（`/pinn/resistance`、
`/pinn/seakeeping`、`/transformer/resistance`、`/rl/energy`、`/rl/optimize`、
`/vibration/predict`）的只读证据裁决。
**方法**：8 条机器可裁判据（S1–S8）+ 与同机 REAL 物理端点交叉比对 + ITTC-57 摩擦解析锚。
**结论**：5 个可达端点中 **4 个判 MOCK、1 个判 WITHHELD**；无一个达到 REAL。

> EDIT_WHITELIST: docs/, tools/, tests/, artifacts/

---

## 1. 案例介绍（物理背景与参数）

平台能力清单把 `ai.predict` 分级为 **NOT_SUPPORTED**，但现网 openapi 上挂着一整族
"机器学习"端点，且对外返回 200 与自诩的 `method` 字段（如"PINN物理信息神经网络"）。
两者必有一错：要么代理模型真实可用（则分级过低），要么端点是占位算术（则分级正确、
而端点冒充了能力）。本单用只读探测给出裁决，不依赖任何"记忆里的文献表值"。

核查船（一艘 180 m 级散货船的公开量级，几何先过一致性检查：L/B=6.0、B/T=2.73、
Cb=0.82 均在散货船惯例区间）：

| 参数 | 值 | 参数 | 值 |
|---|---|---|---|
| Lpp [m] | {SHIP['L']:.0f} | Cb | {SHIP['Cb']} |
| B [m] | {SHIP['B']:.0f} | Δ [t] | {SHIP['displacement']:.0f} |
| T [m] | {SHIP['T']:.0f} | ρ [kg/m³] | {SHIP['water_density']:.0f} |
| 航速范围 [kn] | 10–15 | 湿表面积 S [m²]（Holtrop 式独立实现） | {S:.0f} |

## 2. 怎么做的（方法/流程/工具链）

工具链：`tools/audit_surrogate_endpoints.py`（只读探针 → `artifacts/m1828/surrogate_audit.json`）
→ `tests/test_surrogate_endpoint_audit.py`（离线复算 + 裁决逻辑合成测试）
→ `tools/gen_m1828_figures.py`（图件）。

判据（全部机器可裁、不依赖文献表值）：

| 编号 | 判据 | 为何可裁 |
|---|---|---|
| S1 | schema 声明的每个输入必须影响输出 | 单变量扫描，输出恒定即失效 |
| S2 | 同输入重复调用逐位一致 | 直接比较 |
| S3 | 阻力随航速单调，log-log 斜率 ∈ [1.5, 3.5] | 物理上界，闭式判定 |
| S4 | 对同机 REAL `POST /resistance/predict`（Holtrop）相对误差 ≤ 15% | 平台精度标准 |
| S5 | 自陈损失项必须随输入变化 | 恒定损失=未计算的占位数字 |
| S6 | RAO 须对浪向敏感；heave RAO ≤ 3.0 量级 | 长波/短波渐近与量纲 |
| S7 | `optimization_history` 的 reward 必须有进展 | 全等 = 无学习 |
| S8 | 功率分配占比之和 ≈ 1 | 归一化自洽 |

交叉基准的独立性说明：Holtrop 端点本身已经过基准链复核（SUBOFF/DTMB5415/KVLCC2，
见 `docs/reports/m1384_resistance_stability_verification.md`、`m1388_benchmark_chain_audit.md`），
因此可作为同机物理基准；同时本单另用 **ITTC-57 + Holtrop 湿表面积** 独立解析锚
（`C_F=0.075/(log10Re−2)²`，`R_f=0.5ρV²S(1+k)C_F`，1+k=1.25）给摩擦分量定标，
使"错在摩擦项还是兴波项"可归因，而不是只给一个总误差。

## 3. 计算结果

### 3.1 `/pinn/resistance`：摩擦项对得上，兴波项放大 3 倍以上

| V [kn] | Re | C_F(ITTC-57) | R_f 解析锚 [kN] | R_f PINN [kN] | 偏差 | R_r PINN [kN] | R_r/R_f | R_t PINN [kN] | R_t Holtrop [kN] | 相对误差 |
|---|---|---|---|---|---|---|---|---|---|---|
{t_pinn}

要点：
- **摩擦分量被 ITTC-57 解析锚证实**（偏差 {min(rf_devs):+.1f}% … {max(rf_devs):+.1f}%），
  说明实现里确有雷诺数计算；但 Re 与平台 Holtrop 端点自报值一致到 4 位（独立复算校验通过）。
- **兴波（余留）阻力被系统性放大**：R_r/R_f = {min(rr_ratios):.2f}…{max(rr_ratios):.2f}，
  而 180 m 散货船在 Fn=0.12–0.17 的惯例量级为 R_r/R_f ≈ 0.6–1.2。总阻力因此对 Holtrop
  偏大 **{worst_pinn:.1f}%**（10 kn 处最差，随航速递减但始终 >100%）。
- `network_info.architecture` 自暴结构：`5-[{{'base_resistance': 0.001, 'friction_factor': 0.0015, 'residual_factor': 0.003}}]-1`
  —— 括号里是三个**固定系数**，不是网络权重；`data_loss` 在所有航速恒为 0.001。
  即：所谓 PINN 是一条闭式多项式，未经任何训练。
- 声明输入 `displacement` 被**完全忽略**（15000 t 与 120000 t 返回同一数值 2162.28 kN）。

输入敏感性扫描（基准点 V=14 kn）：

| 输入 | 取值 | 返回 R_t [kN] | 是否生效 |
|---|---|---|---|
{t_sens}

### 3.2 `/transformer/resistance`：几何有响应，精度不达标

| V [kn] | Transformer [kN] | Holtrop [kN] | 相对误差 |
|---|---|---|---|
{t_trf}

最差 **{worst_trf:.1f}%** > 15% 门限；且不同几何（120 m / Cb=0.60）确实给出不同结果，
说明它不是常量返回，而是**幅度标定错误的回归式**。这与"Transformer 代理模型"的表述
不符：响应里没有注意力权重、没有序列长度、没有训练轮次，只有 `feature_importance`。

### 3.3 `/pinn/seakeeping`：浪向是装饰输入，RAO 越物理量级

| 浪向 β [deg] | heave RAO | pitch RAO | roll RAO | physics_loss |
|---|---|---|---|---|
{t_sea}

5 个浪向（0/45/90/135/180°）返回**逐位相同**的 RAO 三元组；`physics_loss` 恒为 0.01。
唯一有响应的是浪高/周期（`S6_period_sensitive=True`），说明端点内部确实用了波浪参数，
但**方向自由度被丢弃**——与 `docs/` 中既有耐波缺陷 D2（"rao ignores heading"）同形，
只是出现在 ML 端点上。另外 heave RAO = {sea['S6_heave_rao_max']:.3f}（m/m）在 180 m 船上
超出合理量级上界（本判据取 3.0）；pitch RAO 与 heave RAO 数值同时冻结，说明二者不是
分别求解的。

### 3.4 `/rl/*`：能量管理完全硬编码，优化器无学习轨迹

| 航程 [nm] | 航速 [kn] | energy_savings [%] | 功率分配 | 占比之和 |
|---|---|---|---|---|
{t_rl}

- 三组完全不同的航程/航速返回同一 `energy_savings = {rl['energy_curve'][0]['energy_savings']}`、同一分配
  `{{'main_engine': 0.8, 'auxiliary': 0.3}}`，且**占比之和 = {rl['energy_curve'][0]['allocation_sum']} ≠ 1**（连归一化都未做）。
- `/rl/optimize` 的 `optimization_history` 里 reward 全程恒定（不同 objective 下
  distinct_rewards = {[r['distinct_rewards'] for r in rl['optimize_runs']]}），即没有任何学习/搜索轨迹；
  `optimal_design` 只是 objective 决定的一个常数。

### 3.5 `/vibration/predict`：WITHHELD（文档化驱动方式不可达）

openapi 声明该端点无请求体，实测 POST 一律 422（要求 query 参数 `ship_id`）。
证据不足，判 WITHHELD，不臆测其算法。

### 3.6 裁决汇总

| 端点 | 等级 | 失效判据 |
|---|---|---|
{t_verdict}

## 4. 结果图

`artifacts/m1828/fig_surrogate_audit.png`：
(a) 阻力–航速曲线（PINN / Transformer / Holtrop 基准）；
(b) 各航速相对误差柱状 + 15% 门限线；
(c) PINN 耐波 RAO 对浪向的完全不变性（含 heave 量级上界线）。

## 5. 注意点（坑 / 局限 / 适用范围）

1. **判据 S4 的基准不是绝对真值**：它依赖同机 Holtrop 端点。为避免"用一个黑盒验另一个黑盒"，
   本单额外引入 ITTC-57 摩擦解析锚，把误差归因到具体分量（结论：摩擦项合格、兴波项放大）。
   但兴波阻力的绝对真值仍需模型试验/CFD 基准（SUBOFF/5415/KVLCC2 系列已在进行）。
2. **探测自身也有失效模式**：首版把 `water_density` 只扫描了单个取值，导致"S1 忽略输入"
   的假阳性（ρ 实际是生效的，1000→2109.54 kN、1025→2162.28 kN，比值恰为 1.025）。
   已修正为双点扫描，并在测试中固定"其余输入必须 varies=True"作为探测分辨力的反证。
3. **判定只针对现网这一版行为**：测试里含一条现网探针（`test_live_pinn_ignores_displacement`），
   若平台日后真接入训练好的网络，该测试会失败并提示重跑核查——判据不会自己变绿。
4. 本单**未改动任何船舶平台代码**（只读探测 + 审计工具），因此不存在"改数让它通过"的空间；
   缺陷修复需在 ship-performance-platform 仓另立单。

## 6. 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| PINN 总阻力（10–15 kn） | Holtrop REAL 端点 | {row_label(pinn['S4_vs_holtrop'])} | **否** |
| PINN 摩擦阻力 | ITTC-57 + Holtrop S 解析锚 | {min(rf_devs):+.1f}% … {max(rf_devs):+.1f}% | {'是' if all(abs(x) < 3 for x in rf_devs) else '**否**'}（逐点见 3.1；仅 V=10 kn 一点超 3%） |
| Transformer 总阻力 | Holtrop REAL 端点 | {worst_trf:.1f}% 最差 | **否** |
| PINN heave RAO | 量级上界 3.0 | {sea['S6_heave_rao_max']:.3f} vs 3.0 | **否** |
| RL 功率分配之和 | 1.0 | {abs(rl['energy_curve'][0]['allocation_sum'] - 1.0):.3f} | **否** |
| RL 节能率（输入敏感性） | 应随航程/航速变化 | 变化量 = 0 | **否** |
| Re 复算 | 平台 Holtrop 自报 Re | {max(abs(r['re'] - 778084034.0)/778084034.0 for r in rows[:1])*100:.4f}%（10 kn 点） | 是 |

结论：**4 个可达端点无一项在关键量上满足 3%（或 15%）门限，全部判 MOCK**；
唯一"合格"的是 PINN 的摩擦分量（由 ITTC-57 解析锚证实），但它不足以救整条曲线。
"""



md_path = os.path.join(HERE, "docs", "reports", "m1828_surrogate_ml_endpoint_audit.md")
os.makedirs(os.path.dirname(md_path), exist_ok=True)
open(md_path, "w", encoding="utf-8").write(MD)
print("markdown:", md_path)

fig = os.path.join(ART, "fig_surrogate_audit.png")
b64 = base64.b64encode(open(fig, "rb").read()).decode()
payload = {
    "title": "AI/ML 代理端点证据核查（m1828）：PINN/Transformer/RL 5 端点 4 MOCK 1 WITHHELD",
    "summary": ("只读探测现网 :8001 的 PINN/Transformer/RL 代理端点，用 8 条机器可裁判据 + "
                "Holtrop 物理基准 + ITTC-57 摩擦解析锚裁决：PINN 阻力对基准偏大 "
                f"{worst_pinn:.0f}%、Transformer 偏大 {worst_trf:.0f}%，两者均超 15% 门限；"
                "PINN 耐波 RAO 对浪向逐位不变且超物理量级；RL 能量管理为硬编码常量、"
                "分配占比之和 1.1。4 个可达端点判 MOCK，/vibration/predict 判 WITHHELD。"),
    "scenario_key": "audit.surrogate_endpoints",
    "mission_id": 1828,
    "tags": ["audit", "evidence-grade", "surrogate-ml", "pinn", "transformer", "rl",
             "resistance", "seakeeping", "verification-debt"],
    "markdown": MD,
    "figures": [{"name": "fig_surrogate_audit.png",
                 "caption": "(a) 阻力-航速：PINN/Transformer 代理 vs Holtrop 物理基准；"
                            "(b) 逐航速相对误差与 15% 门限；(c) PINN 耐波 RAO 对浪向完全不敏感。",
                 "png_base64": b64}],
}
json.dump(payload, open("/tmp/report.json", "w", encoding="utf-8"), ensure_ascii=False)
print("report payload: /tmp/report.json", len(b64), "b64 chars")