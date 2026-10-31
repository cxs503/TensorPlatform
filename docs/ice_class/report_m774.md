# 冰级船型多目标优化框架成熟化 —— 返工重发报告（取代 report id=700）

> **本报告定位**：返工单 mission 774 针对原报告 **id=700**（原工作单 mission 767，战役节点 W2-N08）
> 的 7 条缺陷逐条返工。本文由 `docs/ice_class/build_report_m774.py` 生成，**正文每一个数字都从工件
> `runs/ice_class/pareto_front.csv`、`optimization_result.json`、`convergence_history.json`
> 程序化读取或计算得到**，零手抄。原报告 id=700 中与之冲突的数字一律作废，以本报告为准。

## 0. 缺陷清单逐条闭合

| # | 缺陷 | 原报告 (id=700) 声称 | 工件实际（本次程序化读取） | 处理 |
|---|---|---|---|---|
| 1 | R_ice range | [26.3, 200.4] kN | [36.94, 266.01] kN | 重算并改正；本报告全部数字由 build_report_m774.py 从 CSV 读出 |
| 2 | R_ow range | [54.8, 304.6] kN | [88.90, 671.08] kN | 同上；旧值来自早期一次未对齐的运行，已废弃 |
| 3 | GM range | [1.06, 8.75] m | [1.2679, 8.6316] m | 同上 |
| 4a | Ice-optimal 设计点 | α_bow=15°, R_ice=26.3, R_ow=54.8, GM=1.06 | α_bow=21.68°, R_ice=36.94, R_ow=92.11, GM=1.2839 (CSV L5) | 改为「取 CSV 中 R_ice 最小行」的机器选取，并标注 CSV 行号 |
| 4b | GM-optimal 设计点 | α_stem=39°, R_ow=323, GM=8.75 | α_stem=25.15°, R_ow=671.08, GM=8.6316 (CSV L3) | 改为「取 CSV 中 GM 最大行」；并列情形在 §3.3 列出行号 |
| 5 | 多种子声明 | Tested across 8 random seeds ... all pass | 工件仅 seed=42（metadata.seed），无其他种子工件 | 整段删除；改为显式单种子证据声明 + 未做的多随机种子研究（§7） |
| 6 | 场量云图豁免 | 无说明，仅 4 张图 | 已补写豁免理由（§5.1），并新增 1 张工件范围核对图 | 补写理由 + 追加图形证据 |
| 7 | 误差判定格式 | R_ice / R_ow 只写 N/A (empirical) | 逐项显式回答「是否<3%」：均为「否」+ 依据 | §8 重写为逐项「是否<3%」列；无基准项声明原因与替代核验 |

**根因说明**：缺陷 1–3 的根因是原报告手工誊写了早期一次未对齐运行的数字（与最终入库工件不是同一次
run 的输出），而不是算法/工件错误——工件本身自洽（§8 末行，重算偏差为机器精度量级）。缺陷 4 的根因
是代表点「按直觉填写」而非「按极值选取」。本版不再手工誊写任何数字，并加 pytest 护栏（§2 第 5 条）。

## 1. 案例介绍

### 1.1 物理背景

冰区航行船舶面临「冰阻力最小化 vs 敞水阻力最小化 vs 稳性（GM）最大化」的三目标冲突：尖艏减小冰
接触宽度 B_eff = B·sin(α_bow)·(0.5+0.5·Cb) 而降冰阻，但抬高半进流角 i_E = 90° − α_bow、增大形状
因子 k，从而抬高敞水阻力；宽 B、大 Cb 提升 GM，但同时抬高两类阻力。因此不存在单点最优，必须在
Pareto 意义上搜索折中面。

### 1.2 设计变量（6 维，取自 optimization_result.json → design_variables.bounds）

| Variable | Bound (optimizer input) |
|---|---|
| L [m] | [80, 200] |
| B [m] | [15, 35] |
| T [m] | [5, 12] |
| Cb [-] | [0.5, 0.85] |
| α_bow [deg] | [15, 35] |
| α_stem [deg] | [10, 40] |

### 1.3 目标与约束

- 目标：min R_ice（h=1 m 层冰、V=5 kn，Lindqvist 1989）、min R_ow（V=15 kn，Holtrop–Mennen 1982 +
  i_E 修正）、max GM（GM = KB + BM − KG，壁侧近似 + 冰级 KG 系数 1.05）。
- 约束：Cb ∈ [0.50, 0.85]、L/B ∈ [4, 7]、B/T ∈ [2.5, 4]、α_bow ∈ [15°, 35°]、GM ≥ 0.15 m
  （IMO Polar Code）、Δ ≥ 500 t（IACS UR I 结构重量可行性）。

### 1.4 运行规模（取自 metadata / convergence_history.json）

| 项 | 值 |
|---|---|
| 算法 | NSGA-II（约束支配 + LHS 初始化 + SBX / 多项式变异） |
| 种群 / 代数 | 40 / 50 |
| 随机种子 | **42（仅此一个，工件无第二种子记录）** |
| 目标评价总次数 | 2040 |
| 是否使用代理模型 | True |
| Wall time | 1.134 s |
| Pareto 解数（工件 n_solutions） | 40（CSV 40 行，互异目标向量 39 个，重复行 1 行） |
| HV 参考点 | [3000.0, 8000.0, 0.0] |

## 2. 方法与工具链

1. **搜索**：NSGA-II（Deb 2002），pop=40、gen=50、seed=42，约束用
   constrained-domination，HV 用固定参考点 [3000.0, 8000.0, 0.0] 保证跨代可比。
2. **目标评价**：三个经验/解析模型（Lindqvist 1989 / Holtrop–Mennen 1982 修正 / 壁侧 GM），实现并冻结在
   `optimizer/ice_class/objectives.py`；本报告 §8 用该冻结模块对工件做重算校核。
3. **落盘工件**：`optimization_result.json`（种群、目标、HV 历史、metadata）、`pareto_front.csv`
   （40 行前沿扁平表）、`convergence_history.json`（收敛判据摘要）。
4. **本报告生成链**：`python3 docs/ice_class/build_report_m774.py` → 读三件工件 → 计算统计量 →
   渲染 `docs/ice_class/report_m774.md` + `docs/ice_class/metrics_m774.json` → POST `/tensor/reports`
   （引用原工作单 mission 767，标题标注取代 id=700）。
5. **回归护栏**：`tests/test_ice_class_report_consistency.py` 从 CSV 独立重算区间与三个代表点，
   断言其与报告 Markdown 中的数字一致——原报告「人写数字与工件脱钩」的失效模式被测试钉死。

## 3. 计算结果（全部程序化注入）

### 3.1 验收判据（工件实测）

| Criterion | Metric | Threshold | Measured (from artifacts) | Pass? |
|---|---|---|---|---|
| (a) HV convergence | max HV change rate, last 5 gen | < 0.01 | **0.000534** | ✓ |
| (b) Pareto non-dominance | mutually dominating pairs (strict float compare) | 0 pairs | **0 pairs** | ✓ |
| (b') same vectors, GM tie merged at 1e-9 | pairs once analytically equal GM is merged | 0 pairs | **1 pair(s)** | ✗ (see 3.2b) |
| (b'') CSV as printed | pairs on the rounded CSV values | 0 pairs | **1 pair(s)** | ✗ (see 3.2b) |
| (c) Constraint feasibility | feasible ratio | 1.0 | **1.000** | ✓ |
| (d) Objective tradeoff | R_ice(OW-opt) vs R_ice(Ice-opt) | > 0.10 | **0.3197** | ✓ |
| (e) Wall time | single optimisation | < 600 s | **1.134 s** | ✓ |

> (a) 的 max_hv_change_rate_last_5 取自 convergence_history.json；本报告另用 hv_history 末 5 点独立复算得
> 0.000534，与工件值 0.000534 的相对差 0.0e+00。
> (b) 由本脚本对全部 40 个解做全对全支配检验；三种口径（全精度严格浮点 / 解析等价 GM 合并 /
> CSV 打印精度）在这份工件上给出**不同**结论，逐条列于 §3.2b 并给出物理解释。
> (c) 同时用「工件 feasible 列比例」与「对 Cb、L/B、B/T、α_bow、GM、Δ 独立重算」两条路径判定，
> 均为 1.000。

### 3.2 目标区间（本版修正，取代原报告 id=700 的区间）

| 目标 | 最小 | 最大 | 均值 | 总体标准差 | 单位 |
|---|---|---|---|---|---|
| R_ice | 36.94 | 266.01 | 128.19 | 69.97 | kN |
| R_ow | 88.90 | 671.08 | 353.07 | 186.18 | kN |
| GM | 1.2679 | 8.6316 | 5.5003 | 2.2869 | m |

### 3.2b 前沿非支配性审计（三种判定口径，结论不同，必须披露）

| 口径 | 数据源 | 支配对数 | 判据 (b) 是否通过 |
|---|---|---|---|
| 全精度严格浮点（= 验收测试口径） | optimization_result.json → pareto_objs | 0 | 是 |
| 全精度 + 解析等价 GM 合并（容差 1e-9） | 同上 | 1 | 否 |
| CSV 打印精度（R_ice/R_ow 2 位、GM 4 位） | pareto_front.csv | 1 | 否 |

**审计发现（本版新增，原报告 id=700 未提及）**：CSV 的 L41 行在「R_ice、R_ow 均更差、GM 相同」意义下被 L33 行支配（ΔR_ice = 0.62%、ΔR_ow = 2.68%、ΔGM = 0.0e+00；两解仅 L 不同：148.51 m vs 144.06 m），即前沿里有 1 个数学上被支配的退化解。它能通过机器判据 (b)，是因为 GM 解析式中 L 在 BM = I_wp/∇ 里分子分母相消（I_wp ∝ L·B³/12、∇ ∝ L·B·T·Cb），两解 GM 在数学上严格相等，而浮点实现在 1e-15 量级保留了符号为「有利」的舍入差，使严格浮点比较判为「互不支配」。

**结论（如实，不粉饰）**：判据 (b) 在验收测试的浮点口径下通过；但按物理/解析意义，前沿含 1 个应剔除的退化解，属前沿后处理（去重）缺陷。本轮返工仅授权改报告，故不修改算法与工件，只在此披露并把「前沿剪枝 + 去重键纳入 GM/L 解析等价」列入 §7 后续工作第 1 条。

### 3.3 代表设计点（机器选取：min R_ice / min R_ow / max GM）

| 设计点 | L | B | T | Cb | α_bow | α_stem | R_ice [kN] | R_ow [kN] | GM [m] | 来源 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Ice-optimal (min R_ice) | 80.00 | 15.00 | 5.00 | 0.5061 | 21.68 | 10.00 | 36.94 | 92.11 | 1.2839 | CSV L5 | R_ice 全局最小 |
| OW-optimal (min R_ow) | 80.00 | 15.00 | 5.00 | 0.5000 | 29.63 | 10.00 | 48.75 | 88.90 | 1.2898 | CSV L2 | R_ow 全局最小 |
| GM-optimal (max GM) | 156.50 | 35.00 | 8.90 | 0.8500 | 15.74 | 25.15 | 146.22 | 671.08 | 8.6316 | CSV L3 | GM 全局最大 |

（GM 最大值在 CSV 中并列出现 2 次，行号 L3, L7，目标向量完全相同，视作同一设计点。）

三个代表点均由一次遍历取极值得到，取极值规则与 CSV 行号写在表内，可被任何人独立复核，例如
`sort -t, -k7 -g runs/ice_class/pareto_front.csv | head -1` 即得 Ice-optimal 行。

### 3.4 折中关系（由上式代表点直接相除得到）

- R_ice 折中比（判据 d）：(R_ice@OW-opt − R_ice@Ice-opt)/R_ice@Ice-opt =
  (48.75 − 36.94)/36.94 = **0.3197**
  （31.97% > 10%，判据成立）。
- 反向代价：把 GM 从 1.2839 m 抬到 8.6316 m，R_ice 上升
  (146.22 − 36.94)/36.94 = 295.83%。
- 反向看敞水收益：OW-opt 的 R_ow 比 Ice-opt 只低 (92.11 − 88.90)/
  92.11 = 3.48%。两端点尺度几乎相同（L=80.00 m、
  B=15.00 m、T=5.00 m），差别集中在 Cb（0.5061 vs
  0.5000）与 α_bow（21.68° vs 29.63°）。
- **不对称性（本版量化，原报告未给）**：α_bow 由 21.68° 增到
  29.63°，换来 R_ow 仅降 3.48%，代价是 R_ice 升
  31.97%——即在该物理模型里「艏部变钝」对冰阻的惩罚远大于对敞水阻力的收益，
  这正是前沿两端点都停在 L≈80 m、B=15 m 的原因：**该模型下冰/敞水冲突由 Cb 与艏部角度主导，
  而非单纯船体尺度**。适用性上须注意 i_E 系数 0.004 为标定值（§6.2），此不对称幅度随该系数变化。

### 3.5 约束满足性（独立重算，不依赖工件的布尔列）

| Constraint | Required | Realised range on the front (recomputed) | Pass? |
|---|---|---|---|
| Cb | [0.50, 0.85] | [0.5000, 0.8500] | ✓ |
| L/B | [4, 7] | [4.009, 5.697] | ✓ |
| B/T | [2.5, 4] | [3.000, 4.000] | ✓ |
| α_bow | [15, 35] deg | [15.00, 34.79] | ✓ |
| GM (IMO Polar Code) | ≥ 0.15 m | min 1.2679 m | ✓ |
| Displacement (IACS UR I) | ≥ 500 t | min 3,075.0 t | ✓ |

### 3.6 工件一致性

- CSV ↔ JSON 对应性：`pareto_front.csv` 的 40 行全部能在 `optimization_result.json` 的
  `pareto_pop` 中按设计向量匹配（匹配阈值 5e-3，即 CSV 打印精度），匹配 40/40 行，
  设计变量最大分量偏差 0.0050（来自 CSV 的 2–4 位小数打印舍入，阈值 5e-3）。
  说明 CSV 与 JSON 是同一次运行的同一份前沿，不存在跨运行拼接。
- 目标值自洽性：见 §8 末行（工件 vs 冻结模块重算）。

### 3.7 收敛性

| 量 | 值 |
|---|---|
| 首代 HV | 152,746,986 |
| 末代 HV | 195,098,217（= convergence.final_hv） |
| HV 相对增长 | 27.73% |
| 末 5 代最大变化率（工件） | 0.000534 |
| 末 5 代最大变化率（本脚本复算） | 0.000534 |
| 是否收敛 | True |

## 4. 图表

1. **pareto_3d** —— 3D Pareto front（R_ice、R_ow、GM），颜色映射 GM；数据源 pareto_front.csv。
2. **pareto_2d_projections** —— Pareto 前沿二维投影：R_ice–R_ow、R_ice–GM、R_ow–GM，显示三目标冲突。
3. **design_space_parallel** —— Pareto 解的设计空间平行坐标（L, B, T, Cb, α_bow, α_stem），颜色为 GM。
4. **convergence_curve** —— NSGA-II 收敛：HV 随代数（50 代，final HV=195,098,217）。
5. **objective_ranges_check** —— 本版新增：由 CSV 直接绘制的三目标排序散点与 min/max（红虚线），并标出原报告 id=700 的错误区间，作为范围修正的图形证据。

![objective_ranges_check](artifacts/ice_class/objective_ranges_check.png)

## 5. 图件说明与豁免

### 5.1 为什么没有「场量云图」（缺陷 6 的正式豁免理由）

**豁免成立**，理由三条，均由任务性质决定，不是缺证据：

1. **本任务不是场仿真。** 交付物是一个零维/经验公式驱动的多目标优化框架：目标函数是
   40 个标量（R_ice、R_ow、GM），设计变量是 6 个标量。计算域内不存在需要离散的场
   （无 RANS/势流网格、无 FEM 单元、无冰-结构接触场），因此**不存在可在空间上着色的物理量**。
   强行画云图只能是无物理来源的示意，属于伪造证据。
2. **本类问题的标准可视化是「目标空间 + 设计空间」图。** 3D Pareto 面、二维投影、平行坐标、
   HV 收敛曲线即为此类；本版另加 `objective_ranges_check.png`，把 CSV 排序散点与 min/max 直接画出来，
   用于钉死本次修正的目标区间。
3. **上真场仿真时云图立刻可补。** 若把 R_ice 换成 DEM/FEM 冰载荷场、把 R_ow 换成 RANS 船体表面压力场，
   则应补「冰压力/舷侧接触力云图」与「船体表面压力系数 Cp 云图」。这是**尚未做的升级**，不是本单交付缺口。

### 5.2 与基线的比较曲线

工件集内无实验/模型试验基线（见 §8），故不画「模型 vs 实测」曲线，以免用无来源数字充数。
可做的比较是内部一致性：§8 的「工件 vs 冻结模块重算」给出全精度链路偏差 0.0e+00（机器精度）
与 CSV 打印精度链路偏差（1e-3 量级，纯舍入）。两者都是表格式核对而非物理对比，用表格即可，
画成曲线只会把舍入噪声放大成「有意义的差别」，反而误导，故不画。

## 6. 注意点（踩过的坑 / 局限 / 适用范围）

### 6.1 本次返工踩到的坑（原报告 id=700 的失效模式）

- **手工誊写数字 = 事故源。** 区间与代表点来自早期未对齐运行，与最终工件不同源，且彼此内部不一致，
  一次产生 5 条缺陷。对策：数字全部由脚本读工件生成，并加 pytest 护栏防回退。
- **代表点选取规则未写明。** 原报告 Ice-optimal 的 α_bow=15° 实为变量下界而非该行真实值（工件该行为
  21.68°），说明是「按直觉填」而非「按极值取」。对策：每个代表点标注 CSV 行号与
  取极值规则，并显式处理并列（2 个 GM 最大值并列）。
- **「测了 8 个种子」属无害化夸大。** 工件只有 seed=42，无任何其他种子的落盘记录。对策：整段删除
  （§6.2），不做事后补跑式的补救。

### 6.2 证据边界（明确「没做什么」）

- **单种子**：只有 seed=42 一次运行入库。**未做**多随机种子鲁棒性研究，因此**不能**声称「8 个种子
  全部通过」，也**不主张**该 Pareto 前端对种子不变——这一项证据等级为 WITHHELD，需后续单独立项（§7）。
- **无实验基准**：工件集不含模型试验或实船数据，§8 对 R_ice/R_ow 的「<3%」只能答「否」。
- **物理模型适用域**：Lindqvist 面向一年层冰、无冰脊/碎冰区；Holtrop–Mennen 适用 Fn 中低速民用船型，
  冰区附体（冰带、推进器保护）未建模；GM 用壁侧近似，大倾角需完整 GZ 积分。
- **i_E 系数 0.004 为标定值**（标准 Holtrop 用 0.0015），目的是让 α_bow 产生可辨识的敞水阻力梯度；
  这会令 R_ow 绝对值偏离标准 Holtrop，属方法学选择，对外给绝对阻力时须改回标准值。
- **平台集成未打通**：本单调用平台 `calculations/resistance` 返回「8001/8002 均不可达」，故目标函数是
  模块内自研实现，未经平台阻力/稳性链交叉验证。属环境限制，如实记录。

## 7. 后续工作（把 WITHHELD 变成 REAL 的最小路径）

1. **前沿剪枝**：剔除 §3.2b 的退化解，并在后处理中按「设计向量 + 解析等价目标」去重
   （GM 与 L 解析无关，应在去重键中体现），使严格浮点与容差两种支配判据一致。
2. **多随机种子**：跑 seed ∈ {42, 123, 7, 999, 2024}（pop=40、gen=50），每个种子落盘
   `runs/ice_class/seed_<n>/` 下的 pareto_front.csv + convergence_history.json，统计 HV 与目标区间的
   种子间离散度。**在这些文件入库之前，报告不得出现「多种子通过」字样。**
3. **独立基线**：平台 8001 恢复后，用 `calculations/resistance` 对三个代表点做独立阻力复算，把
   「模型 vs 平台」偏差补进 §8 的 <3% 判定。
4. **场级升级**：以 RANS 或 DEM 冰载荷替换经验式，届时按 §5.1 第 3 条补场量云图。

## 8. 误差判定（逐项显式回答「是否<3%」）

| 关键量 | 参照/基准 | 本次可给出的定量核验 | 相对偏差 | 是否 <3%？ |
|---|---|---|---|---|
| R_ice（Lindqvist 1989） | 无实验/模型试验基准落盘 | 仅内部自洽 + 文献散布 15–25% | 基准缺失 | **否** —— 无基准可对；且文献量级 15–25%，不可能 ≤3% |
| R_ow（Holtrop–Mennen + i_E 修正） | 无实验/模型试验基准落盘 | 仅内部自洽 + 文献散布 5–10%，且 i_E 系数为标定值 0.004 | 基准缺失 | **否** —— 无基准可对；文献量级 5–10%，且已偏离标准 Holtrop |
| GM（壁侧近似） | 无完整静水力/GZ 基准落盘 | 与冻结模块重算一致（下行） | 见下行 | **否（对「精度 vs 实船/静水力基准」）；是（对「工件-模型自洽」）** |
| 工件-模型自洽（全精度链路，40 解 × 三目标） | 冻结 objectives.py 重算 | 最大相对偏差 | 0.0e+00 / 0.0e+00 / 0.0e+00 | **是**（机器精度量级） |
| 工件-模型自洽（CSV 打印精度链路） | 冻结 objectives.py 以 CSV 舍入后的设计变量重算 | 最大相对偏差 | 9.2e-04 / 3.8e-04 / 1.0e-03 | **是**（仅舍入，≪3%） |

判定口径（避免含糊）：

- **本报告「是否<3%」回答的是「该量相对真实基准的误差是否小于 3%」。** R_ice、R_ow 的答案都是
  **否**：工件中不存在任何实验、模型试验或平台独立实现的参考值，且所用经验式在文献中的精度量级
  本身是 5–25%，既无基准可对，也不可能达到 3%。原报告写「N/A (empirical)」属回避，本版改为显式否定。
- **能够真正做到 <3% 的是另一件事：工件与模型的数值自洽**（表中末行）。这证明 `pareto_front.csv`
  的数字确实来自冻结目标模块而非人工誊写——正是原报告缺陷 1–4 的病灶。重算使用 `/srv/tensor/agent/work/m767/optimizer/ice_class/objectives.py`（原始 run 的冻结目标函数模块，只读导入）：全精度链路把 optimization_result.json 的 pareto_pop 逐解喂回模型、与 pareto_objs 比对（40 解）；CSV 链路把 CSV 里 2–4 位小数的设计变量喂回模型、与 CSV 目标列比对，其 1.0e-03 量级偏差完全由打印舍入造成（CSV 目标值本身只有 2–4 位有效小数）。
- **提升到「是」的唯一途径**：引入真实基准（模型试验 / 实船试航 / 平台独立阻力实现），按 §7 补跑后重判。

## 9. 工件与可复现性

| 工件 | MD5 |
|---|---|
| runs/ice_class/pareto_front.csv | `17cdca10f98973381d4df1fc83a06a48` |
| runs/ice_class/optimization_result.json | `45ce291dfe584552ba5e83c21d2b090d` |
| runs/ice_class/convergence_history.json | `f2f8f9c9deadc89a8bcf8330d9ec3f0a` |

复现：`ICECLASS_CODE_DIR=<optimizer/ice_class 路径> python3 docs/ice_class/build_report_m774.py`
（Python 3.11.6）。生成器对「工件缺失 / CSV 为空 / 判据不满足 / 前沿行数与 n_solutions 不符」
fail-closed，不写报告。

## 10. 参考文献

1. Lindqvist, G. (1989). A straightforward method for calculation of ice resistance of ships. POAC.
2. Holtrop, J., Mennen, G.G.J. (1982). An approximate power prediction method. ISP 29.
3. Deb, K. et al. (2002). NSGA-II. IEEE Trans. Evol. Comput. 6(2).
4. IACS UR I — Polar Class Ship Structural Requirements.
5. IMO Polar Code — International Code for Ships Operating in Polar Waters.
