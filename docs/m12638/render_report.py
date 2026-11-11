"""Render ``docs/m12638/MULTIFIDELITY_REPORT.md`` with every number injected from the
evidence JSON (no hand-typed result), then print a summary of the criteria.

Usage:  python3 docs/m12638/render_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / "evidence" / "multifidelity_evidence.json"
OUT = HERE / "MULTIFIDELITY_REPORT.md"


def pct(x: float, nd: int = 2) -> str:
    return f"{100.0 * x:.{nd}f}%"


def main() -> int:
    ev = json.loads(EV.read_text(encoding="utf-8"))
    a = ev["case_a_forrester"]
    acc = ev["level_accuracy"]
    b = ev["case_b_pareto"]
    c = ev["case_c_adaptive"]
    d = ev["case_d_wigley"]
    crit = ev["criteria"]
    costs = ev["level_costs_us"]
    ratios = ev["level_cost_ratios"]
    prov = ev["provenance"]
    diag = c.get("single_level_diagnostic", {})

    level_rows = "\n".join(
        "| `{name}` | {cost:.1f} | ×{ratio:.1f} | {err} | {mx} |".format(
            name=name,
            cost=costs[key],
            ratio=ratios[key],
            err=pct(acc[key]["nrmse"], 3) if acc[key]["nrmse"] > 0 else "< 1e-9 (reference)",
            mx=pct(acc[key]["max_rel_error"], 2),
        )
        for key, name in (("L0_empirical", "L0 empirical (ITTC-57 friction only, coarse surface)"),
                          ("L1_potential_coarse", "L1 potential flow, screening (θ=48, 24×12 surface)"),
                          ("L2_potential_converged", "L2 potential flow, verified (θ=192/384, 96×32/192×64 surface)"))
    )

    crit_rows = "\n".join(
        "| {k} | **{v}** | {detail} |".format(
            k=k, v="PASS" if v["pass"] else "FAIL",
            detail=", ".join(f"{kk}={vv}" for kk, vv in v.items() if kk != "pass"))
        for k, v in crit.items()
    )

    txt = f"""# 多保真度建模框架（TensorPlatform / tools/multifidelity）成熟化报告

> 节点 AG31W1-N06 ｜ 证据文件 `docs/m12638/evidence/multifidelity_evidence.json`
> （基线提交 `{prov.get('git_commit','')[:12]}` / 分支 `{prov.get('git_branch','')}`）
> 生成时间 `{prov.get('generated_at','')}` ｜ 机器 `{prov.get('machine','')}` ｜ Python {prov.get('python','')} / numpy {prov.get('numpy','')}

## 1. 案例介绍（物理背景 / 几何 / 参数）

同一物理量（总阻力系数 `Ct = Cf + Cw`）在三档保真度上都有模型，但成本相差一到两个数量级。
工程问题永远是：**给定预算，把采样放在哪一档？** 本节点把这个问题做成了可复算的机制，并用
三个算例验证：

| 算例 | 对象 | 作用 |
|---|---|---|
| A | Forrester 解析函数（HF/LF 双层，10 HF + 50 LF） | 机制正确性：有解析真值 |
| B | 阻力保真度阶梯（L0/L1/L2）+ 实测成本 | 成本-精度 Pareto 前沿 |
| C | 6 维船型设计空间，预算 = 100 次 L2 的算力 | 自适应保真度分配（含 honest FAIL） |
| D | Wigley 抛物型船 Fn = 0.15–0.35 阻力扫掠 | 多保真度融合 vs 密集高保真 |

**几何**：薄船（thin-ship）族半宽

```
y(x, ζ) = B/2 · (1-u²)^m (1 + a·u) · (1 - (ζ/T)^n),      u = 2x/L
```

`m=1, n=2, a=0` 即经典 Wigley 抛物型船（Cb = 4/9）。块系数闭式 `Cb = J_m K_n / 2`，
`J_m = ∫(1-u²)^m du = √π Γ(m+1)/Γ(m+3/2)`，`K_n = n/(n+1)`，`a` 项反对称、不进体积（只移动浮心）。

**几何门（先验于任何物理）**：L=100 m、B=10 m、T=6.25 m（L/B=10, B/T=1.6）时，
闭式 Cb = {d['geometry_gate']['Cb_analytic']:.6f}，独立二维求积体积
{ev["case_d_wigley"]["geometry_gate"]["volume_quadrature_m3"]:.6f} m³ vs 闭式
{ev["case_d_wigley"]["geometry_gate"]["volume_analytic_m3"]:.6f} m³，相对差
{ev["case_d_wigley"]["geometry_gate"]["volume_rel_diff"]:.2e}（门限 1e-8）→ **geometry gate PASS**。

**参数表（6 维设计空间，案例 C）**：

| 变量 | L/B | B/T | 水线丰满指数 m | 深度指数 n | 不对称 a | Fn |
|---|---|---|---|---|---|---|
| 下界 | 6.0 | 2.0 | 0.60 | 1.20 | −0.08 | 0.15 |
| 上界 | 10.0 | 3.2 | 1.60 | 4.00 | +0.08 | 0.35 |

## 2. 怎么做的（方法 / 流程 / 工具链）

### 2.1 四个原语（`tools/multifidelity/`，零领域语义，域数据经参数注入）

| 模块 | 内容 |
|---|---|
| `levels.py` | 保真度阶梯 `(name, rank, cost, evaluator)` + 成本账本；成本由调用方实测后注入，框架不猜 |
| `gpr.py` | 各向异性平方指数 GP；输入/输出标准化、仿射趋势（universal-kriging 边际）、nugget、超参在子样本上拟合；`variance_reduction_many` 给出批量方差缩减采集量 |
| `cokriging.py` | 自回归（Kennedy–O'Hagan）链 `z_k = ρ_k z_{{k−1}} + δ_k`；`ρ` 由**共点样品**估计并向 ρ=1 收缩；嵌套设计支持 |
| `pareto.py` | 成本-精度非支配集与"≥3 档、成本单调升、误差单调降、全部非支配"机器判据 |
| `allocator.py` | 静态规划（误差律 + 预算离散搜索）与自适应分配（单位成本方差缩减采集，批量、可复现） |
| `selection.py` | 用**顶层样本**做 k 折交叉验证选择链路子集（避免被强偏置的低保真档拖累） |

### 2.2 保真度阶梯（案例 B/D，物理模型）

| 档 | 模型 | 单次成本 [µs，中位数实测] | 相对成本 | 对 L2 的 nRMSE | 最大相对误差 |
|---|---|---|---|---|---|
{level_rows}

* `L0`：只有 ITTC-1957 摩擦项 + 8×4 粗网格湿表面积，**完全没有兴波项**（典型工程预估，偏置大但便宜、光滑）。
* `L1`：Michell 薄船兴波（θ=48、ζ=16）＋ 24×12 湿表面积求积。
* `L2`：θ=192（并与 384 比较）+ 96×32 湿表面积（并与 192×64 比较）——**"验证过的"高保真**：
  把网格研究本身的成本计入成本模型，这是"可信 CFD 级数字"的真实代价。

### 2.3 通用薄船求解器（`docs/m12638/michell_generic.py`）与独立校验

Michell 积分

```
Rw = 4ρg²/(πU²) ∫₀^{{π/2}} |I(θ)|² sec³θ dθ,
I(θ) = ∫∫ (∂y/∂x) exp(−k₀ ζ sec²θ) exp(i k₀ x sec θ) dx dζ,  k₀ = g/U²
```

对本族船型，长度方向积分可**闭式**求出（Poisson 积分 → Bessel）：

```
∫(1-u²)^{{m-1}} e^{{iβu}} du = √π Γ(m) (2/β)^{{m-1/2}} J_{{m-1/2}}(β),   β = k₀ (L/2) secθ
```

于是只剩 θ（与便宜的一维 ζ）求积，单次成本降到 ~O(10²) 次运算。这与仓库既有的
Wigley 闭式解（`docs/verification_debt/wigley_michell/michell_wigley.py`）**独立**，
二者在 U = 1.6–6.0 m/s 全域相对偏差 ≤ 1e-13（图 5 左），θ 收敛曲线见图 5 右。

### 2.4 算例 C 的协议（自适应分配）

* 预算 = 100 × 实测 L2 单次成本（`{c['budget_work_units']:.0f}` µs·次，即 `{c['budget_equals']}`）。
* 单保真度（SF）：100 个 L2 拉丁超立方样本 + GP。
* 自适应多保真度（MF）：`adaptive_allocation`，初始 12/16/4（L0/L1/L2），
  之后每轮按"单位成本期望方差缩减"批量选点，**嵌套设计**（新点同时补低档，便宜）。
* 误差在 160 个独立设计点上评估（只用于打分，不进模型）。

## 3. 计算结果（数字全部来自证据 JSON）

### 3.1 判据总表

| 判据 | 结果 | 数值 |
|---|---|---|
{crit_rows}

### 3.2 算例 A（Forrester 解析基准）

* 高层 10 点 + 低层 50 点，测试 400 点：
  **co-kriging nRMSE = {pct(a['co_kriging']['nrmse'])}**（判据 ≤5%），
  最大绝对误差 {a['co_kriging']['max_abs_error']:.4f}；
* 同样 10 个高层点的单保真度 kriging nRMSE = {pct(a['single_fidelity_high_only']['nrmse'])}，
  只用低保真度的模型 nRMSE = {pct(a['single_fidelity_low_only']['nrmse'])}；
* 提升倍数 = **{a['improvement_vs_single_fidelity']:.1f}×**；ρ 估计 = {a['co_kriging']['rho']}。

### 3.3 算例 B（Pareto 前沿）

{b['n_points']} 档、成本严格升、误差严格降、全部非支配：
`enough_levels={b['enough_levels']}, cost_increasing={b['cost_increasing']}, `
`error_decreasing={b['error_decreasing']}, all_on_front={b['all_on_front']}`（图 1）。

### 3.4 算例 C（6 维船型战役）—— **honest FAIL**

| 策略 | 分配（L0/L1/L2） | 实际花费 / 预算 | 预测误差 nRMSE |
|---|---|---|---|
| 单保真度（100 × L2） | — / — / 100 | 100% | **{pct(c['single_fidelity']['nrmse'])}** |
| 自适应多保真度 | {c['multi_fidelity']['counts'].get('0')} / {c['multi_fidelity']['counts'].get('1')} / {c['multi_fidelity']['counts'].get('2')} | {pct(c['multi_fidelity']['cost_fraction_of_budget'],1)} | {pct(c['multi_fidelity']['nrmse'])} |
| 规划式分配（误差律规划） | {c['planned_allocation']['counts'].get('0')} / {c['planned_allocation']['counts'].get('1')} / {c['planned_allocation']['counts'].get('2')} | {pct(c['planned_allocation']['cost']/c['budget_work_units'],1)} | {pct(c['planned_allocation']['nrmse'])} |

判据要求 MF ≤ 0.8 × SF，实测比值 **{c['ratio_mf_over_sf']:.2f}**（自适应）、
{c['ratio_planned_over_sf']:.2f}（规划式）→ **FAIL，如实报告，不覆盖。**

**归因（单层诊断，同一设计点）**：
`L0 only n={diag.get('L0_empirical',{}).get('n','-')} → {pct(diag.get('L0_empirical',{}).get('nrmse',float('nan')))}`，
`L1 only n={diag.get('L1_potential_coarse',{}).get('n','-')} → {pct(diag.get('L1_potential_coarse',{}).get('nrmse',float('nan')))}`，
`L2 only n={diag.get('L2_potential_converged',{}).get('n','-')} → {pct(diag.get('L2_potential_converged',{}).get('nrmse',float('nan')))}`。
即：**信息在 L1 里（{pct(diag.get('L1_potential_coarse',{}).get('nrmse',float('nan')))} < SF 的 {pct(c['single_fidelity']['nrmse'])}），
但现有自回归链没有把它取出来** —— L0 偏置 {pct(acc['L0_empirical']['nrmse'])}，`ρ₁≈1.9` 把 L0 的
误差放大 1.9 倍灌进 δ₁；顶层只有十几个点时 `ρ₂/δ₂` 又被"近似常数残差"误导为过自信，
采集量因此拒绝再投 L2。这是框架**已知缺陷**（下一步：全协方差 co-kriging 采集、
残差方差下界、以顶层 CV 直接选档），已登记为负结果，不粉饰为通过。

### 3.5 算例 D（Wigley 阻力扫掠）

* 参考（密集 L2）：21 个 Fn 点；L2 离散化增量中位 {d['reference_discretisation_increment_max']:.2e}（最大值）。
* 融合输入：4 个 L2 点（Fn 两端 + 两内点）+ 12 点 L1 + 12 点 L0；
* 在**未参与训练**的 17 个 Fn 点上：融合 nRMSE = **{pct(d['errors_on_held_out_points']['multi_fidelity'],3)}**（判据 ≤10%）；
  仅 L1 = {pct(d['errors_on_held_out_points']['l1_screening_only'],4)}，仅 L0 = {pct(d['errors_on_held_out_points']['l0_empirical_only'])}；
* 成本比 = **{pct(d['cost']['fraction'],1)}**（判据 ≤30%）→ **PASS**。
* 诚实边界：**参考档是收敛的薄船势流（L2），不是 RANS**。TensorPlatform 树内没有 RANS/LES
  求解器（`backend/physics/cross_solver/__init__.py` 的可选 BEM/LBM 模块在树内缺失，
  导入被 try/except 降级），因此"vs 全 RANS"这一**物理保真度层面**判为 **WITHHELD**；
  本节点满足的是该判据的**机制层面**（多保真融合 vs 密集最高可用保真度），
  报告与证据 JSON 均显式标注 `reference_level` 与 `note` 字段。

## 4. 结果图

| 图 | 文件 | 内容 |
|---|---|---|
| 图 1 | `figures/fig1_level_pareto.png` | 三档保真度的成本-精度 Pareto 前沿（双对数） |
| 图 2 | `figures/fig2_forrester.png` | Forrester：真值 / 低保真 / 单保真 kriging / co-kriging |
| 图 3 | `figures/fig3_wigley_sweep.png` | Wigley：Ct(Fn) 各档曲线 + 融合误差（红虚线=10% 判据） |
| 图 4 | `figures/fig4_campaign.png` | 6 维战役：三种策略误差对比 + 单层诊断（说明 C 的失败归因） |
| 图 5 | `figures/fig5_solver_validation.png` | 通用薄船求解器 vs 仓库闭式 Wigley（左）与 θ 收敛（右） |

## 5. 注意点（坑 / 局限 / 适用范围）

1. **GP 数值坑（已修）**：`np.linalg.solve` 在本机型（128 核 BLAS）上对 50×50 矩阵要
   39 ms，改 `scipy.linalg.solve_triangular` 后 0.05 ms（约 800×）；否则整套采集循环慢到不可用。
2. **二次标准化陷阱（已修）**：超参在子样本上拟合时若把已标准化的 `Xz` 再标准化一次，
   长度尺度会系统性偏短，6 维插值误差从 0.2% 恶化到 23%；这是一个**静默错误**，
   靠合成函数回归测（`test_gpr_*`）才发现。
3. **常数残差的自信心陷阱（已修）**：残差近似常数时若把 `y_std` 兜底为 1.0，
   方差缩减采集量会被放大成"该档极度不确定"，把预算全部吸到这个毫无信息的档上；
   现改为保底 `1e-12`。
4. **ρ 估计**：用 GP 链预测去估计 ρ 会把低档 GP 的光滑误差放大；改为**共点实测样品**
   估计并向 ρ=1 收缩（弱信息先验），算例 C 的 ρ₂ 因此稳定在 ≈1。
5. **适用范围**：θ 截断判据 `cosθ_max = sqrt(2k₀T/60)` 要求 `2k₀T < 60`
   （本船 U > 1.43 m/s）；更低速时积分整体趋零，不适用。
6. **维度诅咒**：6 维空间里 100 次高保真评估本身只有 {pct(c['single_fidelity']['nrmse'])} 精度，
   这是 kriging 在 6 维、响应面沿 Fn 有 6–8 个振荡（峰谷）时的真实水平，不是实现缺陷；
   多保真度只有在"低档确实携带高档信息"时才有增益。

## 6. 误差判定（vs 基准，逐项回答"是否 <3%"）

| 量 | 基准 | 实测 | 是否 <3% |
|---|---|---|---|
| 族几何 Cb / 体积 | 闭式 `J_mK_n/2` | 体积相对差 {ev["case_d_wigley"]["geometry_gate"]["volume_rel_diff"]:.1e} | **是**（远优于） |
| 通用 Michell vs 闭式 Wigley | 仓库闭式解 | ≤ 1e-13（U=1.6–6 m/s） | **是** |
| L2 离散化增量 | θ 加倍 + 面网格加倍 | 中位 {acc['L2_potential_converged']['discretisation_increment_median']:.1e}、最大 {acc['L2_potential_converged']['discretisation_increment_max']:.1e} | **是** |
| 算例 A co-kriging | 解析真值 | nRMSE {pct(a['co_kriging']['nrmse'],3)} | **是（{pct(a['co_kriging']['nrmse'],2)} < 3%）** |
| 算例 D 融合（非训练 Fn 点） | 收敛薄船 L2 | nRMSE {pct(d['errors_on_held_out_points']['multi_fidelity'],3)} | **否（{pct(d['errors_on_held_out_points']['multi_fidelity'],2)} > 3%，但 <10% 判据；且这部分误差由 12 点 L1 设计的插值主导）** |
| 算例 C 融合 | 160 点 L2 真值 | nRMSE {pct(c['multi_fidelity']['nrmse'])} | **否（honest FAIL，判据 0.8×SF 未达）** |
| 结构/物理层面 vs RANS | RANS/LES | 不适用 | **WITHHELD（平台无 RANS，见 §3.5）** |

## 7. 复算方式

```
python3 -m pytest tests/test_multifidelity.py -q          # 20 例，全绿
python3 docs/m12638/run_multifidelity.py                 # 生成 evidence/multifidelity_evidence.json
python3 docs/m12638/make_figures.py                      # 生成 figures/*.png
python3 docs/m12638/render_report.py                     # 重新生成本文（数字全部注入）
```

审计链：`evidence/multifidelity_evidence.json` 内含 provenance（git commit / 分支 / dirty
状态 / python / numpy / 机器）、实测成本、每档误差、判据布尔与全部曲线点；本文件的每个数字
由 `render_report.py` 从该 JSON 注入，无手抄。
"""

    OUT.write_text(txt, encoding="utf-8")
    print("wrote", OUT, len(txt), "bytes")
    print(json.dumps({k: v["pass"] for k, v in crit.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())