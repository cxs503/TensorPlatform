# p106w2 跨求解器互裁集成门（n11）：滑移×气泡减阻机理互证、BEM-Cummins×FVM 水池 RAO 互裁、疲劳×扭振接口契约、桨出水×扭振耦合传递

- 战役 `p106w2` · 节点 `p106w2-n11-integration-gate` · 仓 `TensorPlatform`
- schema `tp.xsolver.interop_verdict/v1` · 生成时间 2026-11-10T12:52:46.742341+00:00
- **门总体判定 `FAIL`**（honest_fail=True，upgrade_forbidden=True）— 本门为 honest FAIL，不粉饰
- manifest sha256 `8ab590e622fd5405f0d9b45a26fd23c997b7095b13248419dbca6834b30e1f84` · verdict 内容摘要 `90ba8277e163f2d57171cea31667ab291a5db8c3e85db7a94da8301fa45caf45`（跨运行稳定：同一 manifest+工件复跑同摘要，见测试 `test_verdict_is_reproducible`）
- 断裂点 12 条，按类型：`{"artifact_missing": 2, "contract_violation": 1, "excitation_order_missing": 1, "outside_reference_band": 1, "shared_parameter_mismatch": 4, "unresolvable": 2, "upstream_not_delivered": 1}`

## 1 案例介绍

战役 p106w2（plan 499）把四条互相独立的验证面放进同一个窗口：LBM 侧的气泡减阻微观机理（n01）与壁面部分滑移边界（n02）、BEM 侧的时域记忆核 Cummins 表示（n05）、FVM 侧的时域数值波浪水池（n06）、船舶侧的扭振（n07）与螺旋桨出水（n09）、以及谱疲劳链（n10）。n11 不生产任何新的物理量，它要回答的是一个纯工程判定问题：

> 这些节点各自的数字，能否被另一个求解器/另一条链路独立复算与接受？若不能，断裂点具体在哪一条判据上？

四类互裁之所以必须成对设计，是因为每一对都涉及**同一个物理量在两种实现里被两种口径表达**：①减阻（阻力系数的相对下降）——滑移边界给出的是平均流位移带来的层流类比量，气泡减阻给出的是湍流调制+黏性+滑移三项之和，两者在相同摩擦雷诺数下是否可比，取决于是否在同一 ΔC_f 定义下、是否含 Reynolds 应力修正；②垂荡/纵摇 RAO——频域 BEM 与水槽时域自由运动的差别可以来自几何、非线性和数值耗散三类完全不同的原因，互裁前必须先判几何是否同一；③扭振应力幅→疲劳损伤——接口契约要比对量纲（应力是 MPa 还是 Pa）、幅值约定（峰值/零峰值/RMS）与 S-N 曲线标识，缺一项则 Miner 累积在数值上不可复现；④桨出水推力波动→轴系扭振——推力波动的阶次（叶频）必须真的进入受迫响应，否则两节点各自正确也只算平行作业。

因此本门的机器判据是四类结构化的：跨求解器 RAO 互裁容差 ≤10%；接口契约要求数据格式一致且量纲可解析可比；机理互证要求与文献趋势一致；负控要求任一上游节点自评判据 FAIL 或任一互裁判据 FAIL 时，门必须 FAIL 并输出具体断裂点——而不是给出一个可以被平均掉的总分。

### 1.1 战役节点（平台 plan 499 机器抽取）

| 节点 | 仓 | 平台任务 | 状态 | 自评判据数 |
|---|---|---|---|---|
| `p106w2-n01-lbm-bubble-turbulence` | TensorLBM | 10135 | in-flight | 5 |
| `p106w2-n02-lbm-slip-boundary` | TensorLBM | 9159 | done | 5 |
| `p106w2-n03-fem-detail-fatigue` | TensorFEM | 9160 | blocked | 6 |
| `p106w2-n04-bem-multihull` | TensorBEM | 9161 | done | 6 |
| `p106w2-n05-bem-cummins-kernel` | TensorBEM | 9162 | done | 6 |
| `p106w2-n06-fvm-wave-tank` | TensorFVM | 9163 | done | 6 |
| `p106w2-n08-spp-loading-stability` | ship-performance-platform | 9164 | done | 6 |
| `p106w2-n09-spp-propeller-emergence` | ship-performance-platform | 9165 | done | 5 |
| `p106w2-n10-spp-spectral-fatigue` | ship-performance-platform | 10256 | in-flight | 6 |
| `p106w2-n11-integration-gate` | TensorPlatform | 9562 | in-flight | 4 |
| `p106w2-n07-spp-torsional-vibration` | ship-performance-platform | 9167 | done | 5 |

### 1.2 被互裁工件的出处（来源 worktree + commit + sha256）

| 工件 | 来源节点 | 来源仓 | git commit | sha256(前12) | 可读 | 说明 |
|---|---|---|---|---|---|---|
| `n01_bubble` | — | — | `—` | `` | 否 | 节点 p106w2-n01 (mission 10135) in-flight，未交付工件 |
| `n02_slip_report` | p106w2-n02-lbm-slip-boundary | TensorLBM | `807645571c1a` | `2739c3139136` | 是 |  |
| `n02_slip_derived` | — | — | `—` | `19212a2a2d09` | 是 |  |
| `n05_campaign` | p106w2-n05-bem-cummins-kernel | TensorBEM | `46526aa75ca3` | `018331e7cffd` | 是 |  |
| `n05_rao` | — | — | `—` | `` | 否 | n05 交付频域系数 A(ω)/B(ω)/K(t)，未交付 RAO 曲线（需消费方解 Cummins 方程） |
| `n06_summary` | p106w2-n06-fvm-wave-tank | TensorFVM | `8c0011e80bdd` | `b47a62c95bc3` | 是 |  |
| `n06_geometry` | p106w2-n06-fvm-wave-tank | TensorFVM | `8c0011e80bdd` | `6ac17b25d52f` | 是 |  |
| `n06_rao` | — | — | `—` | `` | 否 | n06 自评判据 rao_vs_beM_le_10pct_out_of_band 未交付（cases_run=0） |
| `n07_results` | p106w2-n07-spp-torsional-vibration | ship-performance-platform | `b4288a9db097` | `d59b6cf99193` | 是 |  |
| `n09_summary` | p106w2-n09-spp-propeller-emergence | ship-performance-platform | `f27f8b8b22cd` | `334492b9f3b5` | 是 |  |
| `n10_fatigue` | — | — | `—` | `` | 否 | 节点 p106w2-n10 (mission 10256) in-flight，未交付工件 |

## 2 怎么做的（方法 / 流程 / 工具链）

**门骨架（平台机制，零领域语义）**：`backend/services/xsolver/interop/` 三个模块——`units`（单位拼写/换算因子/量纲向量的注入表，支持 `MPa`/`Hz`/`rpm` 等精确拼写与 `A_per_B`、`A_Bn` 组合形式，支持从字段名后缀反推单位）、`checks`（十类声明式检查：工件存在性、上游判据继承、曲线逐点互裁、参考带、趋势一致性、字段契约、跨节点共享参数、激励阶次覆盖、区间重叠、叠加残差）、`gate`（清单校验、工件按 sha256 钉住加载、逐项汇总与断裂点输出、确定性摘要）。

**判定代数（fail-closed + honest FAIL 保护）**：单项 = 任一检查 FAIL 则 FAIL，否则任一 WITHHELD 则 WITHHELD，否则 PASS；全门 = 任一 FAIL 则 FAIL。工件缺失或 sha256 不匹配 → 该检查 WITHHELD 且**永不**升级为 PASS；检查实现抛异常 → 捕获后判 WITHHELD（不许崩成绿）。门里没有“平均分”通道：任何单点失败都会以断裂点形式出现在 `fractures` 列表里。

**域内容注入**：战役清单 `data/campaigns/p106w2/interop_manifest.json` 承载四个验收项、阈值、接口契约与工件路径；兄弟节点工件由 `tools/xsolver_interop/ingest.py` 机读快照进 `data/campaigns/p106w2/raw/`（记录来源 worktree、git commit、源文件 sha256 与快照 sha256），再由 `extract_spec.json` 从中抽取数字（表格用正则、结论带用正则），**任何模式匹配失败=摄取失败**，不保留陈旧数字。

**零域语义的机器保证**：注入词表 `data/campaigns/p106w2/domain_vocab.json` 列出域词条，`tests/test_xsolver_interop_no_domain_vocab.py` 反查机制代码路径，出现即红；战役图（域叙事）放在数据包的 `runners/figures.py` 里，由 `--figures <runner>` 挂载，平台 CLI 只认约定函数签名。

### 2.1 检查类型使用统计（机器统计）

| 检查类型 | 使用次数 | PASS | FAIL | WITHHELD |
|---|---|---|---|---|
| `additivity` | 1 | 0 | 0 | 1 |
| `artifact_present` | 2 | 0 | 0 | 2 |
| `band_overlap` | 1 | 1 | 0 | 0 |
| `criterion_inheritance` | 1 | 0 | 1 | 0 |
| `field_contract` | 2 | 1 | 1 | 0 |
| `order_coverage` | 1 | 0 | 1 | 0 |
| `reference_band` | 1 | 0 | 1 | 0 |
| `series_compare` | 1 | 0 | 0 | 1 |
| `shared_parameter` | 5 | 1 | 4 | 0 |
| `trend_agreement` | 1 | 1 | 0 | 0 |

Python 3.11 标准库 + matplotlib（仅出图）；无新依赖。
复现命令：
```bash
python3 -m tools.xsolver_interop.ingest --campaign p106w2 --data data/campaigns/p106w2
python3 -m tools.xsolver_interop.run_gate --manifest data/campaigns/p106w2/interop_manifest.json \
  --data-dir data/campaigns/p106w2 --out artifacts/p106w2_interop \
  --figures data/campaigns/p106w2/runners/figures.py
python3 -m pytest tests/test_xsolver_interop_gate.py tests/test_xsolver_interop_campaign.py \
  tests/test_xsolver_interop_no_domain_vocab.py -q
```

## 3 计算结果（全部由 verdict.json 程序化注入）

### 3.1 四项验收总体判定

| 验收项 | 判定 | PASS | FAIL | WITHHELD |
|---|---|---|---|---|
| `a-mechanism-crosscheck` (a) LBM 滑移边界(n02) 与 气泡减阻(n01) 减阻机理互证 | **FAIL** | 1 | 1 | 1 |
| `b-rao-intercompare` (b) BEM-Cummins 记忆核(n05) 与 FVM 波浪水池(n06) RAO 互裁（≤10%） | **FAIL** | 0 | 3 | 1 |
| `c-fatigue-torsion-contract` (c) 谱疲劳链(n10) 与 扭振(n07) 接口契约与损伤可叠加性 | **FAIL** | 1 | 1 | 2 |
| `d-emergence-torsion-coupling` (d) 螺旋桨出水(n09) 推力波动 → 轴系扭振(n07) 激励传递 | **FAIL** | 2 | 3 | 0 |

### 3.2 逐检查数字

#### a-mechanism-crosscheck — (a) LBM 滑移边界(n02) 与 气泡减阻(n01) 减阻机理互证

| 检查 | 类型 | 判定 | 关键数字 |
|---|---|---|---|
| `a.bubble_artifact_present` | `artifact_present` | WITHHELD | 节点 p106w2-n01 (mission 10135) in-flight，未交付工件 |
| `a.slip_drag_reduction_vs_dns_band` | `reference_band` | FAIL | b+=10=0.236（参考下限的 1/21.2）; b+=20=0.472（参考下限的 1/10.6） |
| `a.dcf_monotone_in_bplus` | `trend_agreement` | PASS | Spearman=1.000 |

#### b-rao-intercompare — (b) BEM-Cummins 记忆核(n05) 与 FVM 波浪水池(n06) RAO 互裁（≤10%）

| 检查 | 类型 | 判定 | 关键数字 |
|---|---|---|---|
| `b.n06_rao_criterion` | `criterion_inheritance` | FAIL | 上游 pass=False, cases_run=0 |
| `b.geometry_block_coefficient` | `shared_parameter` | FAIL | 最大相对差 37.18%（容差 2.00%）; n05=0.707477, n06=0.444416 |
| `b.geometry_length` | `shared_parameter` | FAIL | 最大相对差 16.67%（容差 2.00%）; n05=12, n06=10 |
| `b.rao_series_intercompare` | `series_compare` | WITHHELD | artifact 'n05_rao' absent (n05 交付频域系数 A(ω)/B(ω)/K(t)，未交付 RAO 曲线（需消费方解 Cummins 方程）) |

#### c-fatigue-torsion-contract — (c) 谱疲劳链(n10) 与 扭振(n07) 接口契约与损伤可叠加性

| 检查 | 类型 | 判定 | 关键数字 |
|---|---|---|---|
| `c.fatigue_artifact_present` | `artifact_present` | WITHHELD | 节点 p106w2-n10 (mission 10256) in-flight，未交付工件 |
| `c.torsion_field_units` | `field_contract` | PASS | 违约 0 处（无） |
| `c.torsion_declarations` | `field_contract` | FAIL | 违约 4 处（missing_declaration） |
| `c.damage_additivity` | `additivity` | WITHHELD | artifact 'n10_fatigue' absent (节点 p106w2-n10 (mission 10256) in-flight，未交付工件) |

#### d-emergence-torsion-coupling — (d) 螺旋桨出水(n09) 推力波动 → 轴系扭振(n07) 激励传递

| 检查 | 类型 | 判定 | 关键数字 |
|---|---|---|---|
| `d.blade_count` | `shared_parameter` | PASS | 最大相对差 0.00%（容差 0.00%）; n09=5, n07=5 |
| `d.rated_speed` | `shared_parameter` | FAIL | 最大相对差 8.33%（容差 2.00%）; n09=110, n07=120 |
| `d.shaft_inertia` | `shared_parameter` | FAIL | 最大相对差 61.13%（容差 2.00%）; n09=5000, n07=12865 |
| `d.rpm_excursion_vs_barred_band` | `band_overlap` | PASS | 工作带 [48.51, 171.5] vs 禁带 [122.6, 141]，重叠 100% |
| `d.excitation_order_coverage` | `order_coverage` | FAIL | 缺阶次 [5]（已含 [3]） |

### 3.3 断裂点清单（门输出的具体断裂点）

| 验收项 | 检查 | 断裂类型 | 说明 |
|---|---|---|---|
| `a-mechanism-crosscheck` | `a.bubble_artifact_present` | `artifact_missing` | 下游判据所需工件 n01_bubble 缺失：节点 p106w2-n01 (mission 10135) in-flight，未交付工件 |
| `a-mechanism-crosscheck` | `a.slip_drag_reduction_vs_dns_band` | `outside_reference_band` | 2/2 点落在参考带 [5, 15] 外；最远点 b+=10=0.236（参考下限的 1/21.2） |
| `b-rao-intercompare` | `b.n06_rao_criterion` | `upstream_not_delivered` | 上游节点判据未满足（未交付）：not delivered: the 2-DOF free-motion run is unstable in the cut-cell tank and the forced-oscillation radiation coefficients are grid dependent; see README secti |
| `b-rao-intercompare` | `b.geometry_block_coefficient` | `shared_parameter_mismatch` | 共享参数 block_coefficient 跨节点不一致：n05=0.707477 vs n06=0.444416（相对差 37.18% > 2.00%） |
| `b-rao-intercompare` | `b.geometry_length` | `shared_parameter_mismatch` | 共享参数 length_m 跨节点不一致：n05=12 vs n06=10（相对差 16.67% > 2.00%） |
| `b-rao-intercompare` | `b.rao_series_intercompare` | `unresolvable` | artifact 'n05_rao' absent (n05 交付频域系数 A(ω)/B(ω)/K(t)，未交付 RAO 曲线（需消费方解 Cummins 方程）) |
| `c-fatigue-torsion-contract` | `c.fatigue_artifact_present` | `artifact_missing` | 下游判据所需工件 n10_fatigue 缺失：节点 p106w2-n10 (mission 10256) in-flight，未交付工件 |
| `c-fatigue-torsion-contract` | `c.torsion_declarations` | `contract_violation` | 4 处接口契约违约（missing_declaration） |
| `c-fatigue-torsion-contract` | `c.damage_additivity` | `unresolvable` | artifact 'n10_fatigue' absent (节点 p106w2-n10 (mission 10256) in-flight，未交付工件) |
| `d-emergence-torsion-coupling` | `d.rated_speed` | `shared_parameter_mismatch` | 共享参数 rated_rpm 跨节点不一致：n09=110 vs n07=120（相对差 8.33% > 2.00%） |
| `d-emergence-torsion-coupling` | `d.shaft_inertia` | `shared_parameter_mismatch` | 共享参数 shaft_inertia_kg_m2 跨节点不一致：n09=5000 vs n07=12865（相对差 61.13% > 2.00%） |
| `d-emergence-torsion-coupling` | `d.excitation_order_coverage` | `excitation_order_missing` | 下游受迫响应缺激励阶次 [5]（已含 [3]） |

## 4 结果图

图由数据包内的 `runners/figures.py` 从 verdict.json 与快照工件生成，随报告入库。

![fig1_slip_vs_dns_band.png](../artifacts/p106w2_interop/figures/fig1_slip_vs_dns_band.png)

*Fig 1  (a) mechanism cross-check: extracted n02 slip drag reduction vs the DNS band it cites*

![fig2_geometry_precondition.png](../artifacts/p106w2_interop/figures/fig2_geometry_precondition.png)

*Fig 2  (b) shared-parameter check: the two RAO arms were solved on different geometries*

![fig3_rpm_excursion_vs_barred.png](../artifacts/p106w2_interop/figures/fig3_rpm_excursion_vs_barred.png)

*Fig 3  (d) propeller-emergence rpm excursion vs the 5P barred band of the shafting*

![fig4_verdict_matrix.png](../artifacts/p106w2_interop/figures/fig4_verdict_matrix.png)

*Fig 4  check-level verdict matrix of the integration gate*

![fig5_shared_parameter_deviation.png](../artifacts/p106w2_interop/figures/fig5_shared_parameter_deviation.png)

*Fig 5  shared-parameter checks (Cb, L, rated rpm, shaft inertia) against their tolerances*

## 5 注意点（踩坑 / 局限性 / 适用范围）

**本门判 FAIL 是结论，不是缺陷**：一方面 n01（气泡减阻）与 n10（谱疲劳链）在验收时刻仍 in-flight、无交付工件，互裁的两侧不齐；另一方面 n06 自己的 RAO 判据就是 `pass=false / cases_run=0`（切单元水槽里 2-DOF 自由运动不稳定、受迫振荡附加质量随网格变号），上游自认未交付时下游任何“互裁通过”都是伪造。

**几何前置条件是最容易被忽略的断裂点**：n05（BEM-Cummins）与 n06（水池）各自算得都对，但两条臂用的不是同一条船——不做共享参数互裁就直接比 RAO，属于把两个不同的物理对象放在一张图上，误差数字没有意义。这正是本门把 `shared_parameter` 排在 `series_compare` 之前的原因。

**接口契约的“量纲正确”与“可用”是两件事**：n07 的应力量纲是自洽的（MPa → L^-1·M·T^-2，与 `stress` 期望量纲一致），但缺幅值约定、应力类型、工况标识与 S-N 曲线标识四项声明。量纲对了，Miner 累积仍不可复现——这是量纲检查单独存在时会漏掉的失败模式。

**惯量口径不可比是真实的跨节点语义冲突**：n09 的 `I_kgm2` 是单一轴系惯量，n07 的 `masses` 是逐部件集总（含主机+飞轮、齿轮箱、艉轴承、螺旋桨）。两者相加不是“谁算错了”，而是同一个名字下两种范围（scope）；契约里缺 scope 字段时，机器无法判断该不该比。本门如实判 FAIL 并在断裂点里写明两类数值。

**负控的方向**：本门没有把“找茬”做成通过条件。共享参数里桨叶数是 PASS、出水转速波动带与扭振禁带重叠 100% 也是 PASS——这两条恰恰是“耦合真实存在”的证据，说明检查器不是一味报红。

局限：本门不重算任何节点的物理量，也不对节点内部的数值方法做独立验证（那是各仓自己的判据面）；RAO 互裁因两侧均无 RAO 曲线序列而 WITHHELD，若 n05 侧补交由 A(ω)/B(ω) 解出的 RAO、n06 侧补交稳定 2-DOF 自由运动结果，同一清单无需改代码即可出数值互裁结论；本门不判定“哪个节点的物理更可信”，只判定“能否互裁 + 断裂点在哪”。

### 5.1 解锁条件（下一波由谁补什么）

- n01 交付：平面湍流通道 Re_τ=180 + 单分散微气泡稳态统计（体积分数、有效滑移长度 b⁺、ΔC_f 分项），并给出去泡极限 ΔC_f→0 的退化记录 → 与 n02 的滑移 ΔC_f(b⁺) 在同一 Re_τ 下给出贡献比。
- n06 交付：稳定 2-DOF 自由运动 RAO（或明确降级为受迫振荡+辐射系数路线并声明 GCI）；n05 交付：由 K(t)/A(ω)/B(ω) 解出的同几何 RAO——两者几何参数先过共享参数互裁（Cb/L/T/∇）。
- n10 交付：由应力 RAO→谱矩→窄带 Rayleigh→Miner 的损伤分量与合计；n07 补齐四项声明（幅值约定/应力类型/工况标识/S-N 曲线标识）并把桨叶阶受迫响应纳入结果文件。
- n09/n07 统一额定转速与惯量 scope 字段；n07 增加叶频激励通道（或在结果里显式声明“仅主机阶、未含叶频”）。

## 6 误差判定：关键量 vs 基准，显式回答“是否 <3%”

| 关键量 | 判定对象 | 数值/偏差 | 参考/容差 | 误差 | 是否 <3% |
|---|---|---|---|---|---|
| `a.slip_drag_reduction_vs_dns_band` b+=10 | 对文献带 | 0.236% | [5, 15]% | 带外 | 否（差 21.2 倍） |
| `a.slip_drag_reduction_vs_dns_band` b+=20 | 对文献带 | 0.472% | [5, 15]% | 带外 | 否（差 10.6 倍） |
| `b.n06_rao_criterion` | 上游自评判据 | pass=False | cases_run=0 | 未交付 | 无基准可判（上游未交付） |
| `b.geometry_block_coefficient` | 跨节点共享参数 | 37.18% | 容差 2.00% | 相对差 | 否 |
| `b.geometry_length` | 跨节点共享参数 | 16.67% | 容差 2.00% | 相对差 | 否 |
| `c.torsion_field_units` | 接口契约 | 0 处违约 | 0 处 | 结构性 | 是 |
| `c.torsion_declarations` | 接口契约 | 4 处违约 | 0 处 | 结构性 | 否（契约违约，非数值误差） |
| `d.blade_count` | 跨节点共享参数 | 0.00% | 容差 0.00% | 相对差 | 是 |
| `d.rated_speed` | 跨节点共享参数 | 8.33% | 容差 2.00% | 相对差 | 否 |
| `d.shaft_inertia` | 跨节点共享参数 | 61.13% | 容差 2.00% | 相对差 | 否 |
| `d.excitation_order_coverage` | 激励阶次覆盖 | 已含 [3] | 需含 [5] | 结构性 | 否（激励阶次缺失，非数值误差） |

> 判定口径：`是否 <3%` 列对数值型互裁量按 3% 硬线回答；结构性断裂（契约违约、激励阶次缺失、上游未交付）如实标注“无基准可判”或“非数值误差”，不折算成百分比来凑线。

### 6.1 结论

- 本门总体判定为 **FAIL**：4 项验收项判 FAIL（a-mechanism-crosscheck, b-rao-intercompare, c-fatigue-torsion-contract, d-emergence-torsion-coupling），断裂点 12 条，均有机器证据（工件 sha256 + 上游判据记录 + 数值偏差）。
- 达到 PASS 的检查共 4 条：`a.dcf_monotone_in_bplus`, `c.torsion_field_units`, `d.blade_count`, `d.rpm_excursion_vs_barred_band`。它们只证成各自那条关系（趋势单调 / 量纲自洽 / 桨叶数一致 / 工作带确实穿越禁带），**不构成任何验收项的放行**——单项判定取最差检查。
- 因此**不存在**“关键量误差 <3%”的合格声明可用于整体放行。
- honest FAIL 不可覆盖：即使后续把某一条断裂点补齐，本报告的判定也不会被回写为 PASS；新的裁决必须由新一次运行产生新的 verdict.json 与新的摘要。

## 7 测试证据

```
..........................                                               [100%]
=============================== warnings summary ===============================
../../../../usr/lib/python3.11/site-packages/anyio/pytest_plugin.py:14
  /usr/lib/python3.11/site-packages/anyio/pytest_plugin.py:14: PytestRemovedIn10Warning: _pytest.python.CallSpec2 has been renamed to CallSpec.
  The CallSpec2 alias will be removed in pytest 10.
  Update imports to use CallSpec instead.
  See https://docs.pytest.org/en/stable/deprecations.html#callspec2-renamed
    from _pytest.python import CallSpec2

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
26 passed, 1 warning in 1.75s
```

