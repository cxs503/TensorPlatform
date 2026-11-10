# 多求解器耦合的保守场交换与时钟语义（节点 p154w2-N02）

**结论一句话**：把"交换必须守恒、时钟必须显式、出域必须 fail-closed"从口号变成九条机器可算判据，
C1–C9 全绿；新增的**闭式力矩守恒投影**把力与力矩残差从 1.00e-10 量级压到 1.70e-14，
新增的**二阶重构算子**让刚体运动 patch test 从 4.27%（一阶基线，>3%）降到 3.08e-16。
全部数字由 `artifacts/coupling-exchange-n02/acceptance.json` 程序化注入。

## ① 案例介绍

**物理/工程背景。** 分域耦合（流场 ⇄ 结构场、粗网格 ⇄ 细网格）每一时间步都要在界面上交换守恒量。
单次交换的守总量误差通常很小，但**一阶矩误差注入的是伪力矩**：耦合几千步后系统的角动量会系统性地漂移，
而每一步看起来都"守恒"。因此本节点把守恒律拆成三级（总量 / 一阶矩 / 力矩），
并额外要求"时钟语义显式"——子循环比、采样时刻、外推步数三类出域输入必须显式拒绝，而不是隐式舍入。

**几何与网格（全部合成，无实测几何）。**

| 用途 | 源网格 | 目标网格 | 说明 |
|---|---|---|---|
| 体量交换 | 体网格 6×6、4×4（[0,2]²） | 9×9、13×13、8³/7³/5³ | 目标网格与源网格**不对齐**（shifted），且非嵌套 |
| 嵌套对照 | 6×6 / 4³ | refined(2) | 严格嵌套，检验退化行为 |
| 面量（通量） | 面网格 6×3（法向分块 3） | 9×3 | 只在**同一张面**上做切向铺砌交换 |

**参数表。**

| 参数 | 值 | 来源 |
|---|---|---|
| 守恒门限（总量/一阶矩/力矩） | 1e-12 | 任务判据 C1 |
| patch test 门限 | 1e-12 | 任务判据 C3 |
| 端到端耦合门限 | 1e-10 | 任务判据 C4 |
| 正对照漂移门限 | 1e-2（1%） | 任务判据 C5 |
| 耦合循环数 / 扩散系数 / 步长 | 12 / 0.05 / 0.01 | `coupled_loop` 默认 |
| 投影条件数上限 | 1e12 | `moment_projection` |

## ② 怎么做的

**（1）四类量的差异化守恒律（表 1）。** 判据不再由调用点口头约定，而由 `QuantityLaw` 表声明：
每类量有自己的测度、是否守恒、是否必须保一阶矩、是否要求非负。标量（per-point intensive）
**没有**求和约束可言，它的合法律是"精度保持"——对它请求一阶矩约束会被显式拒绝。

| 量类 | 测度 | 守恒 | 必须保一阶矩 | 要求非负 | 可正可负 |
|---|---|---|---|---|---|
| density | volume | 是 | 是 | 是 | 否 |
| extensive | unity | 是 | 是 | 否 | 是 |
| flux | surface | 是 | 是 | 否 | 是 |
| scalar | volume | 否 | 否 | 否 | 是 |

**（2）统一接口 + 算子族。** `remap(src_field, src_mesh, dst_mesh, quantity_kind, spec)`：
一阶 L2 投影（基线）、**二阶分片线性重构**（本节点新增）、RBF＋守恒修正、最近邻（负控）。
向量走 `remap_vector`，逐分量投影一阶矩张量。

**（3）闭式力矩守恒投影（本节点的核心机制）。** 对目标测度 `m_j`、单元中心 `r_j`，加仿射修正

  `v'_j = v_j + a + b·(r_j − r_ref)`，未知量 `1+dim` 个，由两个约束一次解出：

  `a = (Δ0 − s_d·b)/M0`，`b = C_c^-1·(M − M_v − s_d·Δ0/M0)`，`C_c = Σ m_j d_j d_jᵀ − s_d s_dᵀ/M0`

其中 `d_j = r_j − r_ref`。**无迭代**，残差直接落在舍入量级；
协方差条件数大于 1e12（目标单元不张开各坐标轴）时**抛异常**而不是静默返回错值。
向量逐分量投影后，`Σ r × F` 是一阶矩张量的线性组合，**力矩自动精确守恒**。

**（4）面量（通量）的严格定义。** 通量定义在**面**上：一个跨越法向区间的单元描述的是"板"而非"面"，
若按法向求和会把同一块面积数多次、总量虚增。因此面量交换 = 切向铺砌重叠面积，
且**只有法向区间重合的补片才能交换**；两侧面不重合或目标补片无对应源补片 → 显式拒绝。

**（5）时钟状态机。** 四类时刻关系各带前置条件、误差阶声明与出域规则（表 6）；
`Δt_master/Δt_sub` 非整数比、采样时刻越界、外推步数超限三类必须显式抛 `ClockContractViolation`。
声明的误差阶不是口号：本报告用收敛研究**实测**阶数（线性插值实测 2.045，一步外推 1.998）。

**（6）工具链。** `python -m tools.coupling.exchange_gate`（判据引擎，纯 stdlib+numpy）、
31 条 pytest（含 5 条负控）、`tools/coupling_exchange_figures.py`（图表全部从判据 JSON 读数）。

## ③ 计算结果

**C1 量守恒（门限 1e-12）。** 最差总量残差 5.51e-16、最差一阶矩残差 7.10e-16、
最差力矩残差 1.70e-14。

| 量类 | 布局 | 算子 | 律 | 总量残差 | 一阶矩残差 | 力矩残差 |
|---|---|---|---|---|---|---|
| density | shifted | overlap_p1 | conserved | 0 | 3.19e-16 | — |
| density | shifted | rbf_conservative | conserved | 0 | 3.19e-16 | — |
| extensive | shifted | overlap_p1 | conserved | 3.02e-16 | 2.84e-16 | — |
| extensive | shifted | rbf_conservative | conserved | 0 | 4.26e-16 | — |
| vector | shifted | overlap_p1 | conserved | 5.51e-16 | 7.07e-16 | 5.68e-16 |
| vector | shifted | rbf_conservative | conserved | 3.67e-16 | 2.12e-16 | 1.14e-14 |
| density | nested | overlap_p1 | conserved | 1.70e-16 | 3.19e-16 | — |
| density | nested | rbf_conservative | conserved | 1.70e-16 | 3.19e-16 | — |
| extensive | nested | overlap_p1 | conserved | 1.51e-16 | 7.10e-16 | — |
| extensive | nested | rbf_conservative | conserved | 1.51e-16 | 4.26e-16 | — |
| vector | nested | overlap_p1 | conserved | 1.84e-16 | 2.83e-16 | 1.14e-15 |
| vector | nested | rbf_conservative | conserved | 1.84e-16 | 7.07e-16 | 1.70e-14 |
| flux | surface | overlap_l2 | conserved | 1.60e-16 | 2.36e-16 | — |
| flux | surface | rbf_conservative | conserved | 1.60e-16 | 1.58e-16 | — |
| scalar | shifted | overlap_p1 | precision | — | 2.41e-16 | — |

标量律的精度检查：仿射场重现误差 2.41e-16；
对标量请求一阶矩约束的结果：`refused: moment_exact requested for kind 'scalar', whose admissible law is precision preservation, not a sum constraint`。

**C2 非负与无过冲（门限：最小值 ≥ 0，最大值 ≤ 源最大值）。**

| 算子 | dst 最小值 | dst 最大值 | src 最大值 | 总量残差 | 非负 | 无过冲 | 限额投影破坏一阶矩 |
|---|---|---|---|---|---|---|---|
| overlap_l2 | 7.51e-06 | 4.82e+00 | 4.82e+00 | 1.16e-16 | 是 | 是 | 是 |
| overlap_p1 | 0 | 4.72e+00 | 4.82e+00 | 0 | 是 | 是 | 是 |
| rbf_conservative | 0 | 4.74e+00 | 4.82e+00 | 0 | 是 | 是 | 是 |

**C3 刚体运动 patch test（门限 1e-12）。** 二阶算子最差 3.08e-16；
一阶基线最差 4.27e-02（4.27%，被本门**检出**——这是负控，不是本方法误差）。

| 布局 | 运动 | 一阶 overlap_l2 | 二阶 overlap_p1 | RBF |
|---|---|---|---|---|
| nested | translation | 0 | 0 | 2.71e-02 |
| nested | rotation | 4.27e-02 | 1.52e-16 | 3.26e-02 |
| shifted-7 | translation | 2.22e-16 | 2.22e-16 | 2.20e-02 |
| shifted-7 | rotation | 3.68e-02 | 3.05e-16 | 2.66e-02 |
| shifted-5 | translation | 2.22e-16 | 2.22e-16 | 8.38e-03 |
| shifted-5 | rotation | 1.74e-02 | 3.08e-16 | 1.04e-02 |

**C4 端到端孪生耦合（门限 1e-10）。** 三种映射下总量/动量/能量残差：

| 算子 | 总量 | 动量 | 能量 | 最差 | 通过 |
|---|---|---|---|---|---|
| overlap_l2 | 7.08e-16 | 8.71e-16 | 0 | 8.71e-16 | 是 |
| overlap_p1 | 1.18e-15 | 5.22e-16 | 0 | 1.18e-15 | 是 |
| rbf_conservative | 8.26e-16 | 1.04e-15 | 5.24e-16 | 1.04e-15 | 是 |

**C5 正对照（消融：关掉守恒修正）。** 检出率 **100%**，
漂移区间 1.220e-01–3.464e-01（门限 1%）：

| 试验 | 无修正漂移 | 有修正残差 | 被检出 |
|---|---|---|---|
| 0 | 2.709e-01 | 0 | 是 |
| 1 | 1.536e-01 | 0 | 是 |
| 2 | 3.358e-01 | 2.17e-16 | 是 |
| 3 | 3.464e-01 | 0 | 是 |
| 4 | 1.555e-01 | 0 | 是 |
| 5 | 1.220e-01 | 1.14e-16 | 是 |
| 6 | 2.871e-01 | 2.22e-16 | 是 |
| 7 | 1.432e-01 | 2.27e-16 | 是 |
| 8 | 1.721e-01 | 0 | 是 |
| 9 | 2.062e-01 | 2.22e-16 | 是 |
| 10 | 1.889e-01 | 0 | 是 |
| 11 | 3.094e-01 | 0 | 是 |

**C6 时钟语义。**

| 模式 | 前置条件 | 声明阶 | 出域（网格间）规则 | 越界规则 |
|---|---|---|---|---|
| sync | a stored sample exists at t | 0 | refuse (fail-closed) | refuse |
| linear_interpolation | two stored samples bracket t | 2 | interpolate inside the stored span | refuse |
| one_step_extrapolation | >= 2 stored samples and t beyond the last one | 2 | extrapolate at most max_extrapolation_steps | refuse beyond the declared horizon |
| event_triggered | an event was published and the age <= max_staleness_steps | 0 | hold the last event within the staleness bound | refuse when stale |

| 模式 | 实测阶 | 最大误差 | 实测行为 |
|---|---|---|---|
| sync | 0.000 | 0 | exact at admissible times (no interpolation performe |
| linear_interpolation | 2.045 | 4.15e-03 | convergent |
| one_step_extrapolation | 1.998 | 5.80e-03 | convergent |
| event_triggered | 0.000 | 0 | exact at admissible times (no interpolation performe |

| 出域类别 | 拒绝 | 原因 |
|---|---|---|
| non_integer_ratio | 是 | dt_master/dt_sub = 3.3333333333333335 is not an integer: the subcyclin |
| sub_step_larger_than_master | 是 | sub step 0.25 exceeds master step 0.1: subcycling requires dt_sub <= d |
| sample_time_out_of_range | 是 | LINEAR_INTERPOLATION request at t=9.0 outside the sampled window [np.f |
| extrapolation_steps_exceeded | 是 | request t=1.25 is np.float64(0.75) beyond the last sample np.float64(0 |

**C7 幂等与可追溯。** 两次重映射字段 sha256 相同：`1aaf9d8a50f96f8a…`；
manifest `artifacts/coupling-exchange-n02/mapping_manifest.json` 摘要 `0949a784eba30ada…`。

**C8 零域词语料门。** 扫描 13 个文件，命中 **0** 条。

**C9 测试与架构。** pytest 31 条（负控/拒绝类 14 条），
架构 `loongarch64`；the control plane and the compute cluster are both loongarch64; the x86 leg is the repository CI job, not measurable from this worktree。

## ④ 结果图

- **fig1_conservation_c1.png** — C1 各类量的总量/一阶矩残差（对数轴），红线为 1e-12 门限
- **fig2_torque_c1.png** — C1 向量交换的一阶矩张量与合力矩，两者同在舍入量级
- **fig3_ablation_c5.png** — C5 消融：关掉守恒修正后漂移 12–35%（1% 门限上方），开修正回到 1e-16
- **fig4_patch_test_c3.png** — C3 patch test：二阶算子重现刚体运动，一阶基线偏离 2–4%
- **fig5_coupled_loop_c4.png** — C4 端到端耦合 12 循环的总量/动量/能量残差演化
- **fig6_clock_orders_c6.png** — C6 时钟模式实测误差阶（线性插值≈2，一步外推≈2 但误差常数更大）

## ⑤ 注意点与局限

1. **限额投影与精确一阶矩是张力关系（已量化）**：`bounded_mass_fix` 做局部再分配以保非负/无过冲，
   会把一阶矩推离精确值——实测最差 2.98e-02（即 2.98%），
   而无限额约束时同一算子的交付残差是 7.10e-16。本实现不隐藏这一点：
   C2 表最后一列显式报告 `moment_broken_by_bounds`，报告里的"一阶矩残差"一律是**交付值**（投影后再测），
   不是投影前的中间值。工程含义：力/力矩类量走"精确矩"路径（可正可负），
   质量/密度类量走"限额"路径，二者不可兼得——需要哪一条必须由调用方声明。
2. **门守的是离散矩**：目标场是分片常数表示，其连续一阶矩与离散一阶矩相差 O(h²)。
   本门保证**交换算子不改变离散矩**（否则耦合逐步累积漂移），这是算子级正确性；
   连续矩的 O(h²) 表示误差属于离散化精度，不在本门范围。
3. **通量要求"同一张面"**：跨法向分块不等（板状网格）时显式拒绝，而不是给出一个看起来合理的错数。
4. **二阶算子需要 ≥ dim+1 邻域**：梯度用最近邻最小二乘（伪逆兜底），
   一维/退化点集会被拒绝（`DegenerateMeasureError`）。
5. **RBF 无仿射精度**（patch test 2.2e-2 ~ 3.3e-2）：它只能配全局守恒修正使用，
   不能单独用于运动坐标系/移动体的界面交换。
6. **双架构只完成一半**：控制面与集群都是 `loongarch64`，x86 腿是仓 CI，本 worktree 内无法实测，
   已在 C9 中如实标注为待 CI 覆盖。
7. 本节点是**机制**节点：全部夹具为合成场，无实验/文献基准可比。

## ⑥ 误差判定（逐项对照，显式回答"是否 <3%"）

| 关键量 | 本方法 | 基准/门限 | 是否 <3% |
|---|---|---|---|
| 总量守恒残差（C1，最差） | 5.51e-16 | 1e-12 | 是（≪3%） |
| 一阶矩残差（C1，最差） | 7.10e-16 | 1e-12 | 是（≪3%） |
| 力矩残差（C1，最差） | 1.70e-14 | 1e-12 | 是（≪3%） |
| 刚体运动重现（C3，二阶） | 3.08e-16 | 1e-12 | 是（≪3%） |
| 一阶算子 patch test（C3 负控） | 4.27e-02 | 1e-12 | 否（4.27% ≥ 3%） ← **预期失败，用于证明门有牙齿** |
| 端到端耦合残差（C4，最差） | 1.18e-15 | 1e-10 | 是（≪3%） |
| 消融漂移（C5，最小） | 1.220e-01 | ≥1e-2 被判失败 | 是（12.2% ≥ 1%，100% 检出） |

**结论**：本方法自身的关键量误差全部在 1e-14 以下，远优于 3%；
唯一超过 3% 的数字是一阶基线（4.27%），它是**被本门否定的对照**，不是本方法的误差。
无实验/文献基准适用于本机制节点，故以解析不变量（守恒律、刚体运动）为基准，已在上表逐项列出。

**审计链**：`artifacts/coupling-exchange-n02/acceptance.json`（判据数字）→
`artifacts/coupling-exchange-n02/mapping_manifest.json`（交换记录摘要）→
`tools/coupling/exchange_gate.py`（判据实现）→ `tests/test_coupling_exchange_gate.py`（31 条用例）。
