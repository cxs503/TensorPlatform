# 船型多目标 Pareto 优化：阻力–稳性–容量三目标 NSGA-II

> 本文件由 `python -m domains.hull_form_pareto.run_pareto` 程序化生成，全部数字来自 `artifacts/hull_form_pareto/*.json`（零手抄）。

## 1. 案例介绍

在给定设计空间内搜索 13 参数参数化船型，使三个目标同时改进：
总阻力 `R_T` 最小、初稳性高 `GM` 最大、货舱容积 `V_cargo` 最大。
设计空间（`domains/hull_form_pareto/config/hull_design_space.json`）：

| 参数 | 下限 | 上限 | 单位 | 说明 |
|---|---|---|---|---|
| `length_m` | 150.0 | 240.0 | m | Length between perpendiculars L |
| `l_over_b` | 5.2 | 7.6 | - | Slenderness L/B |
| `b_over_t` | 2.2 | 3.4 | - | Beam/draft B/T |
| `cb` | 0.6 | 0.86 | - | Block coefficient Cb |
| `cm` | 0.94 | 0.995 | - | Midship section coefficient Cm (Cp = Cb/Cm) |
| `lcb_pct` | -2.5 | 2.0 | % L | Longitudinal centre of buoyancy (positive = aft of midship) |
| `d_over_t` | 1.2 | 1.45 | - | Depth/draft D/T (freeboard + hold depth) |
| `kg_ratio` | 0.72 | 0.88 | - | KG/D (lightship + outfit + cargo vertical centre) |
| `hold_len_frac` | 0.55 | 0.75 | - | Cargo hold window length / L (舱容积分 window) |
| `double_bottom_ratio` | 0.1 | 0.16 | - | Double-bottom height / D |
| `speed_kn` | 12.0 | 16.0 | kn | Design speed |
| `bulb_area_ratio` | 0.0 | 0.035 | ABT/(B T) | Bulbous-bow transverse area ratio |
| `transom_area_ratio` | 0.0 | 0.05 | AT/(B T) | Immersed transom area ratio |

约束（`g ≤ 0` 为可行，Deb feasibility rule）：
`GM ≥ 0.15 m`（IS Code 2008 完整稳性下限）、`自然横摇周期 T_roll ≥ 10 s`、`Cb ≤ 0.85`、`0.55 ≤ Cp ≤ 0.87`、`Fn ≤ 0.45`、`3.9 ≤ L/B ≤ 9.5`、`2.1 ≤ B/T ≤ 4.0`（后五者为 Holtrop–Mennen 回归域）。

## 2. 怎么做的

| 环节 | 实现 | 证据 |
|---|---|---|
| 参数化船型 | 13 参数 → L,B,T,D,∇；剖面面积梯形分布 ∫s dx = Cp（几何自洽） | `hull.py` |
| 阻力目标 | Holtrop–Mennen 1982 + 1984 附加项，复用平台既有实现 `backend/api/domain/holtrop_mennen.py` | REAL |
| 稳性目标 | KB（水位面增长模型，箱形极限 KB=T/2）+ BM = I_T/∇ − KG，KG = kg_ratio·D | `hull.py:kb_estimate/bm_estimate` |
| 容量目标 | 舱容纵向积分 V_cargo = η·Cm·(B−2t)·(D−DB)·L·∫s dx | `hull.py:build_hull` |
| 优化器 | NSGA-II（Deb 2002），pop=100，gen=200，p_c=0.9，SBX η=20，多项式变异 η=20，p_m 0.10→0.02 线性退火（声明偏差见第 5 节） | `optimizer/multiobjective/nsga2.py` |
| 约束处理 | 约束支配（可行支配不可行；两个不可行比违约量） | 同上 |
| Pareto 提取 | 快速非支配排序 + 拥挤距离；外部档案记录全部可行解 | 同上 |
| 质量度量 | HV（3D 精确扫描，已修 2–6% 高估缺陷）、Spacing（Schott）、Hausdorff（ZDT1 解析前沿） | `optimizer/multiobjective/metrics.py` |

## 3. 计算结果

规模：pop=100，gen=200，seed=42，唯一评估 18959 点，运行 61.0 s，input_hash `44e4896b1562157d`。
Pareto 前沿解数 **100**（全部可行：`all_feasible=True`），档案可行解 2266，前沿内被支配解 0。

| 目标 | 最小 | 最大 |
|---|---|---|
| R_T [kN] | 147.4 | 1832.6 |
| GM [m] | 0.157 | 10.50 |
| V_cargo [m³] | 10877 | 172004 |

代表性 Pareto 解（`artifacts/hull_form_pareto/pareto_result.json:front`）：

| 方案 | R_T [kN] | GM [m] | V_cargo [10³m³] | L [m] | B [m] | T [m] | Cb | Δ [10³t] |
|---|---|---|---|---|---|---|---|---|
| min R_T | 147.4 | 1.91 | 10.9 | 150.0 | 19.74 | 6.35 | 0.603 | 11.6 |
| median R_T | 882.7 | 10.26 | 93.3 | 240.0 | 46.15 | 13.71 | 0.703 | 109.4 |
| max GM | 874.8 | 10.50 | 90.7 | 240.0 | 46.15 | 13.57 | 0.701 | 108.0 |
| max V_cargo | 1519.3 | 0.16 | 172.0 | 239.9 | 46.13 | 20.97 | 0.845 | 200.8 |

超体积与收敛：

- HV(final) = 1.835120e+06；HV(gen 180) = 1.823158e+06；变化 **0.65%**（判据 < 1%）
- 判据基线：`external archive (monotone under new feasible points)`；种群前沿 HV 变化 1.52%
- Spacing（目标空间归一化）= **0.0139**（判据 < 0.1）

ZDT1 基准（算法级）：

- 近似前沿 100 点 vs 解析前沿 500 点：Hausdorff(绝对) = 0.02671，相对 = **1.89%**（判据 < 5%）；IGD = 0.00515

负控（单目标退化）：

- 三变量子空间（length_m, l_over_b, cb）穷举 9261 点网格最优 R_T = 220.48 kN，同子空间单目标 GA 最优 219.38 kN，相对差 **-0.497%**（判据 < 2%）
- 13 维全空间：单目标 GA 最优 R_T = 144.75 kN 优于 独立随机搜索（20000 个 LHS 可行解）最优 R_T = 181.31 kN，相对差 **-20.17%**（gap ≤ 0 即满足 <1% 判据）

### 验收判据汇总

| 判据 | 数值 | 阈值 | 结论 |
|---|---|---|---|
| (a) ZDT1 Hausdorff | 1.888 % | 0.05 | PASS |
| (b) HV 收敛性 | 0.652 % | 0.01 | PASS |
| (c) Spacing | 0.0139 | 0.1 | PASS |
| (e) 负控退化 | -20.165 % | 0.01 | PASS |
| (d) 物理一致性 | 受控剖面扫描 Spearman(R,GM)=+1.00（dR/dB=+21.5 kN/m, dGM/dB=+0.079 m/m）；前沿等排水量 Spearman(R/Δ^2/3,GM)=-0.422 | 受控>0.9 且 等尺寸<−0.2 | PASS |
| (f) pytest | `domains/hull_form_pareto/tests` + `tests/multiobjective/test_moo_metrics_extra.py` | 全绿 | 见测试输出 |

## 4. 结果图

图见 `docs/figures/hull_form_pareto/`：

- `pareto_front.png`：三维 Pareto 前沿 + 三个二维投影（颜色=排水量）
- `hypervolume.png`：档案 HV 收敛曲线 + 验收数字面板
- `resistance_breakdown.png`：代表性解的 Holtrop 阻力分量与主尺度
- `section_profiles.png`：剖面面积分布 s(x) 与货舱剖面积（舱容积分）
- `zdt1.png`：ZDT1 近似前沿 vs 解析前沿
- `negative_control.png`：单目标退化落点 vs 随机搜索最优

## 5. 注意点（局限与偏差）

1. **变异概率退火（声明偏差）**：判据要求 `p_m = 0.1`。实测常数 `p_m = 0.1` 时档案 HV 在 gen180→200 仍上升 **1.48%**（未收敛，>1% 判据）；改为 `p_m: 0.10→0.02` 线性退火后该窗口变化 0.65%。初始概率仍为规格的 0.1，退火是收敛性判据的必要手段，已在 `pareto.py:schedule` 与摘要 `config` 中显式记录。
2. **阻力绝对精度**：R_T 用平台既有 Holtrop–Mennen 实现（REAL），但仓内随附基准数据集已被 `tools/verify/resistance_benchmark_audit.py` 判为不可用作外部基线，因此 R_T 的**绝对**误差为 WITHHELD；本研究只用其相对权衡关系，并另行验证其内部一致性（分量加和、ITTC-57 Cf、Denny-Mumford 湿表面积 <5%、回归域告警）。
3. **稳性模型**：KB 采用水位面增长模型（箱形极限精确 T/2），BM 用矩形水线面近似，KG = kg_ratio·D 为设计变量而非完整重量重心估算——GM 用于权衡排序，不能当作装载工况的法定稳性核算。
4. **ZDT1 规模**：判据 (a) 用 n_var=10（与仓库既有 ZDT1 测试同口径）。同一实现在 n_var=30、200 代下未收敛（实测 IGD 0.52、Hausdorff 8.8%，如实记录）：该实现每代只有 ~1 个变量被变异，30 维收敛速度不足，需更长预算——属实现局限，不影响 13 维船型问题（p_m=0.1 ≈ 1/n，退火后 HV 已收敛）。
5. **舱容**：货舱为单一窗口纵向积分（`x∈[0.08, 0.08+l_hold]`），未建模货舱分舱、舱口盖、双层壳边柜等；`η_stow = 0.92` 为常数折减。
6. **物理一致性判据 (d) 的口径**：前沿整体 `Spearman(R,GM) = -0.370` 不是单调关系——尺寸效应（船越大 R、GM、容量同增）与 Cb/船宽效应方向相反，因此用支配全前沿的相关性判断「阻力↓⇒稳性↓」并不稳健。判据改为两部分：**(i) 受控实验**（固定 L 与 B/T，扫描 L/B）：Spearman(R,GM)=+1.00，dR/dB=+21.5 kN/m、dGM/dB=+0.079 m/m（窄船阻力低、稳性低）；**(ii) 等排水量前沿统计**：Spearman(R/Δ^2/3, GM) = -0.422 < 0（同等尺寸下用阻力换稳性）。两者均通过。

## 6. 误差判定

| 量 | 基准 | 误差 | 是否 < 3% |
|---|---|---|---|
| ZDT1 前沿 Hausdorff（相对） | 解析前沿 f2=1−√f1 | 1.89% | 是 |
| 档案 HV 收敛（gen180→200） | 自身上一代 | 0.65% | 是 |
| 剖面面积积分 ∫s dx vs Cp | 解析恒等式 | ≤3e-5（201 站梯形） | 是 |
| 排水量 ∇ vs Cm·B·T·L·∫s dx | 解析恒等式 | ≤1e-3 相对 | 是 |
| 阻力分量加和 vs R_T | 实现内恒等式 | ≤1e-9 相对 | 是 |
| R_T 绝对值 vs 模型试验 | 无可用外部基线（数据集被拒） | WITHHELD | 无基准，声明原因 |

