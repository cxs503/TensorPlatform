# 【方式集】LBM 开源实现方式集 v1 —— 24 仓盘点收敛为 16 个功能点

> 锚点 `FEAT-OSS-INVENTORY-LBM`（mission 8623）· scenario_key `oss-solutions`。输入：`oss_inventory` / `oss_solutions` / `feature_ledger` / `tensor_reports(scenario_key=oss-inventory)`，全部数字由 SQL 导出直取，导出件与生成脚本见 `artifacts/oss-solutions-v1/`（`oss_solutions.tsv` sha256 `6720316bb212280a`）。

## 摘要

- **归口链**：`oss_inventory` 在册 32 仓（其中 status=inventoried **29** 仓） → `oss_solutions` **196 条谱系行** → 归并为 **16 个功能点**；有谱系产出的仓 **24/29 = 82.8%**（另有 pending 未盘点 3 仓：`PorousMediaSimulation/openLBMPM`, `Zitzeronion/Swalbe.jl`, `apes-suite/musubi`）。
- **迁移评估分布**（归一口径）：`adapt` 77 条，`reference` 63 条，`fits` 46 条，`none` 8 条，`reject` 2 条。
- **许可证是硬约束**：copyleft(GPL) 8 仓，permissive 8 仓，no-license 5 仓，copyleft-strong(AGPL) 1 仓，restricted 1 仓，copyleft-weak(LGPL) 1 仓；GPL/AGPL/无许可证仓只能走「算法+公式级干净室重写」，本报告每条推荐都标注了对应许可处理方式。
- **『可搬码』比想象少**：46 条 `fits` 中只有 **25 条**持 permissive 许可，即全表真正可搬码的谱系行 = **25/196 = 12.8%**；其余 21 条虽技术可迁移但许可证要求干净室重写（缺口 E-10）。
- **推荐路线**：16 个功能点各给 1 主 + 2 备，共 **49 条推荐**，全部可溯源到具体 `(repo, tag)` 谱系行（机检 C2 = 49/49）。
- **缺口**：10 类（E-1~E-8），其中 6 仓已盘点但零归集、占位报告 2 条、占位标题污染报告 3 条、slug 语义重复 8 组、6 仓代码不可搬。

## 〇、物理背景、问题与参数（案例介绍）

**物理背景**：格子 Boltzmann 方法（LBM）是船舶总体性能预报的数值手段之一——自由面/多相（气-液-固界面、碎冰-水混合、含气空化）、低马赫外流（阻力/推进器尾流）、多孔与复杂几何（附体、管路、压载舱）都是它的目标域。本生态要自己长出可验证的 LBM 能力，缺的不是论文而是「工程实现方式」：同一功能点在 24 个开源仓里有不同配方，且各配方的适用域、精度与许可证完全不同。

**问题描述**：盘点阶段产出的是**按仓组织**的档案（29 仓 × 五维摘要）与 196 条手法级谱系行，彼此孤立；开发侧要回答的是**按功能点**的问题（『我要实现两组分分层 Poiseuille 对标，谁家的实现最值得提取？许可证允许吗？风险在哪？』）。本报告即把仓视角收敛为功能点视角。

**参数表（本单为知识资产类任务，参数即盘点面与判据阈值；不含船体几何）**

| 参数 | 取值 | 单位/性质 | 说明 |
|---|---|---|---|
| 在册仓 | 32 | 个（无量纲计数） | `oss_inventory` 全表 |
| 已盘点仓 | 29 | 个 | status=inventoried，台账锚点要求 ≥24 个 |
| 有谱系产出的仓 | 24 | 个 | 覆盖 82.8% |
| 谱系行 | 196 | 条（每条带 files:行 指针 + transfer） | 台账锚点要求 ≥40 条 |
| 归并后功能点 | 16 | 个 | 见第三章 F01~F16 |
| tier 分层 | 1/2/3 = 9/7/8 | 仓数 | T1 多相 6 / T2 可微GPU 8 / T3 HPC 10+ 的盘点设计 |
| 迁移评估阈值 | fits/adapt/reference/none/reject | 定性分级 | fits 额外要求 permissive 许可（本报告口径） |
| 指针覆盖率阈值 | ≥95 | % | 实测 96.9% |

**几何验证声明（Cb/L/B）**：本单**无船体几何、无计算域、无自由液面几何**，属知识资产类任务，故 Cb/L/B 几何验证项 **N/A**（不适用），而不是未验证；报告中出现的通道高 H、界面宽度 W 等符号均为被引用仓的格子单位约定，非本单计算输入。任何后续把 F01/F03 落成求解器代码的工单，必须先在TensorLBM 内做 Cb/L/B 几何校验再谈精度。

## 一、数据面（案例介绍）

三层数据集：**(a) 仓级档案** `oss_inventory`（29 仓，tier/许可证/语言/五维摘要）；**(b) 手法级谱系** `oss_solutions`（196 行，每行 = 「某仓用某手法实现某功能点」，带 `files` 文件:行指针、`conventions` 约定、`transfer` 迁移评估）；**(c) 锚点账本** `feature_ledger`（49 条）。

**transfer 口径**：原表出现 6 种取值（`adapt`, `fits`, `none`, `reference`, `reject`, `reject-as-is`），本报告归一到五值：`fits`=可搬码 / `adapt`=结构可借需改写 / `reference`=仅参考（许可证或架构不匹配） / `none`=该仓不支持 / `reject`=反例。


**表 1 仓清单（tier / 许可证分级 / 谱系行数）**

| 仓 | tier | 许可证分级 | 许可证原文（截断） | 谱系行数 |
|---|---|---|---|---|
| `AminZar69/CudaPhaseField` | T1 | copyleft(GPL) | GPL-3.0 (copyleft) -> reuse = algorithms only, zero code | 12 |
| `ebinan92/Fingering_dynamics` | T1 | no-license | NONE (license=None -> all rights reserved -> reference-o | 8 |
| `je-santos/MPLBM-UT` | T1 | copyleft(GPL) | GPL-3.0 (copyleft; 我们仅可 reference-only, 禁止代码搬运) | 7 |
| `lanl/MF-LBM` | T1 | permissive | BSD-3-Clause (SPDX BSD-3-Clause; LICENSE in tree — reusa | 7 |
| `lbm-principles-practice/code` | T1 | permissive | MIT | 7 |
| `ludwig-cf/ludwig` | T1 | permissive | BSD-3-Clause (University of Edinburgh, 1998-2018) | 12 |
| `Maarten-vd-Sande/lbm` | T1 | no-license | NONE (no LICENSE file in tree — default all-rights-reser | 5 |
| `PorousMediaSimulation/openLBMPM` | T1 | no-license | none declared (GitHub license=null, no LICENSE file in t | 5 |
| `sorush-khajepor/listLBM` | T1 | copyleft(GPL) | GPL-3.0 | 6 |
| `haimingz/vivsim` | T2 | permissive | MIT | 10 |
| `hietwll/LBM_Taichi` | T2 | no-license | 无（all-rights-reserved，不可直接复用） | 3 |
| `lettucecfd/lettuce` | T2 | permissive | MIT | 7 |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | T2 | no-license | NONE (no LICENSE file; default all-rights-reserved) | 7 |
| `lssfau/lbmpy` | T2 | copyleft(GPL) | GPL-3.0-or-later | 9 |
| `pylbm/pylbm` | T2 | permissive | BSD-3-Clause | 6 |
| `sailfish-team/sailfish` | T2 | copyleft-weak(LGPL) | LGPL-3.0 (declared README:3-4 / setup.py:51; no LICENSE  | 9 |
| `2b-t/LB-t` | T3 | permissive | MIT | 8 |
| `CFD-GO/TCLB` | T3 | copyleft(GPL) | GPL-3.0 | 10 |
| `cfdemons/LUMA` | T3 | permissive | Apache-2.0 (with NOTICE, University of Manchester) | 8 |
| `espressomd/espresso` | T3 | copyleft(GPL) | GPL-3.0 | 4 |
| `joconnor22/LIFE` | T3 | copyleft(GPL) | GPL-3.0 | 14 |
| `lssfau/walberla` | T3 | copyleft(GPL) | GPL-3.0 | 13 |
| `omalaspinas/palabos` | T3 | copyleft-strong(AGPL) | AGPL-3.0 | 10 |
| `ProjectPhysX/FluidX3D` | T3 | restricted | FluidX3D custom license（MIT 基座 + 7 条限制：仅 research/educat | 9 |

**图 1** 覆盖矩阵：行=仓（按 tier 排序），列=功能点，色=该仓在该功能点上的最佳迁移等级。右下角空白区（灰）即该仓对该功能点无实现，是「缺口定位」的可视化依据。

![fig1](01_coverage_heatmap.png)

**图 2** 各功能点谱系行数与迁移等级构成；**图 3** 谱系来源 tier 分布与许可证分级。

![fig2](02_transfer_mix.png)

![fig3](03_tier_license.png)

## 二、怎么做的（方法与流程）

1. **SQL 直取**：三表分别导出 TSV（`psql --no-align -F'\t'`）落盘，报告内所有计数/表格/图由脚本重算，零手抄。
2. **slug 归一 → 功能点归集**：`families.py` 显式白名单把全部原始 slug 映射到 16 个功能点，**未登记即 KeyError 硬失败**（防漏归集）；机检 C1 = 196/196。
3. **谱系表**：每功能点输出「仓 × tag × 手法原文 × 许可证分级」全量横表（method 不截断到无法复核的长度）。
4. **推荐路线**：每功能点 1 主 2 备，`(repo, tag)` 必须命中该功能点已有谱系行（机检 C2），推荐等级与许可证强绑定：只有 permissive 才允许写 `fits`（可搬码）。
5. **锚点对齐**：`feature_ledger` 未完成 LBM 锚点逐条给「可吃到的功能点 + 第一步 + 可消费谱系行数」。
6. **机器块**：`[OSS-SOL]` × 16 条综合推荐，供守护线并入 `oss_solutions`（transfer 已按综合判断修订）。
7. **机检**：C1~C6（归集完备、推荐可溯源、词表归一、指针粒度、仓覆盖、功能点规模）→ `checks.json`。

## 三、结果：16 个功能点的手法谱系与推荐路线

表头说明：`迁移`=综合后的迁移评估（原表值在推荐依据里给出）；`手法`为 `oss_solutions.method` 原文摘要，完整原文与 `files` 行级指针在 DB 中（按 `(repo, feature_slug, tag)` 唯一键可直取）。

### F01 两组分分层 Poiseuille 对标

*判据*：两组分/多组分在通道内分层共流，界面位置+分相流量+spurious current 三重解析对标

*规模*：21 条谱系行 / 18 仓；迁移分布 adapt 10，fits 1，none 1，reference 9

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `lssfau/lbmpy` | fits | `shan-chen-mcmp-barycentric-guo` | 每组分的力 F_sigma = -psi_sigma(x) * sum_{sigma'} g_{sigma sigma'} * sum_d w_d psi_sigma'(x+c_d) c_d，psi 用指数势 rho0*(1-exp(-rho/rho0))；两套 LBMConfig 各自 density_input 但共享 velocity_input = u_bary = sum(rho_sigma u_sigma)/sum(rho_sigma)，各配 Guo 力项、collide_only 内核 + 独立 pull streaming，每步前同步 rho 的 halo。 | copyleft(GPL) |
| `PorousMediaSimulation/openLBMPM` | adapt | `porter-explicit-forcing-4th-order` | Two-component layered/anti-parallel-wall Poiseuille: force split as f<-f-0.5F before collision and +F after, u=[sum(f c)+0.5(F0+F1)]/rho_tot, viscosity-weighted u_eq=sum[(sum(f c)+0.5F)/tau]/sum(rho/tau); isotropy 4/8/10 stencils (Sigma w c_x^2 = 1) suppress spurious currents and allow high viscosit | no-license |
| `ebinan92/Fingering_dynamics` | adapt | `phasefield-mcmp-poiseuille` | 用双分布自由能相场（f->rho,p; g->psi, mu=a*psi(1-psi^2)-kappa*lap(psi)）跑两相分层 Poiseuille：把障碍阵列几何换成解析通道，入口保留 Zou-He 速度边界。相对我们现有伪势/颜色梯度 MCMP，这是一条独立内核路线，可交叉验证。 | no-license |
| `ebinan92/Fingering_dynamics` | adapt | `two-conservation-zouhe-with-chemical-potential-flux` | 多相 Zou-He：入口同时补质量式 rho_in=1/(1-u_x)(sum f_k + ... - psi_x*mu/2) 与序参量式 psi_in=1-sum(g), 未知方向按 w1+w5+w8 归一分配；出口同式但 u_x=+u0。移植时必须补全出口角点并把出口改成压力/回压型。 | no-license |
| `haimingz/vivsim` | adapt | `force-corrected-nebb+body-force-driver-scaffold` | 通道 Poiseuille 驱动骨架——常体力 g 加在 Guo/EDM 力项上，壁面用「力修正半格」NEBB（把壁面格点速度先减去 g/(2ρ) 再代入 NEBB），四组 collision/forcing recipe 并列可切换，末段与解析剖面 u=Gx/(2ν)y(H−y) 对比。不含任何两组分/界面力，分层 MCMP 需自加伪势互作用与界面条件。 | permissive |
| `je-santos/MPLBM-UT` | adapt | `mcmp-two-lattice-shan-chen` | 两套 MultiBlockLattice3D 各持一组分密度/黏度，用 Palabos ShanChenMultiComponentProcessor3D 以单一 G 耦合产生相分离力；润湿性由壁面 bounce-back 黏附系数按几何 tag 分 5 类给出。分层 Poiseuille 只需把图像几何换成解析通道几何即可复用此骨架。 | copyleft(GPL) |
| `lbm-principles-practice/code` | adapt | `scmp-two-component-planar-interface` | Two components share one D2Q9 lattice with per-component populations and separate tauA/tauB; segregation comes from the cross interaction g[0][1]=gAB inside the Shan-Chen force loop, forces enter through Guo 2nd-order forcing, velocity is the barycentric velocity with the 0.5F shift. The channel can | permissive |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | adapt | `zou-he-channel-inlet-outlet-reconstruction` | 2D D2Q9 通道进出口的分布重建：进口先用已知分量反算密度 rho=(f0+f2+f4+2(f3+f6+f7))/(1-u)，再解 f1,f5,f8；出口用 vx=-1+(...) 反算 f3,f6,f7；每步在 setup_step 里覆写第一/最后一行格点。 | no-license |
| `omalaspinas/palabos` | adapt | `shan-chen-mcmp-psi-field` | 多组分 Shan-Chen 显式力 F=-G*psi(x)*sum_i t_i*psi(x+c_i)*c_i；先把 psi 缓存成标量场再访问邻居，力写回格子 external force 字段，由 ShanChenExternalForceBGKdynamics/Regularized 在 collide 内并入；D3Q19 走完全展开特化。 | copyleft-strong(AGPL) |
| `sailfish-team/sailfish` | adapt | `free-energy-binary-equilibrium-pressure-tensor` | 自由能二元模型的平衡态由符号推导：流体分布含体自由能压强 pb=ρ/3+A(−φ²/2+3φ⁴/4) 与 κ 各向异性修正（wxx/wyy/wzz/wxy/wyz/wxz），序参量分布含化学势 μ=A(−φ+φ³)−κ∇²φ；二阶矩闭包为 P_ab=[pb−κφ∇²φ]δ+ρu_a u_b+κ(∂_aφ∂_bφ−½\|∇φ\|²δ)，我方独立复核残差 1e-16。 | copyleft-weak(LGPL) |
| `sailfish-team/sailfish` | adapt | `two-component-sc-layered-poiseuille-edm` | 两组分 SC 分层 Poiseuille：各自分布求 ρ_σ 与 ρ_σu_σ，用松弛时间加权合成速度场 u=Σ(ρ_σ/τ_σ)u_σ/Σ(ρ_σ/τ_σ)；SC 力 F=−G12ψ_a(x)Σw_i e_i ψ_b(x+e_i) 转为加速度后经 EDM(shifted velocity) 进平衡态；半程反弹壁 + 壁后一层 unused 节点；两套黏度比 5 的解析剖面即验证基准。 | copyleft-weak(LGPL) |
| `2b-t/LB-t` | reference | `no-multiphase-layout-pattern-only` | 该仓无多相实现；唯一可借的是 NPOP 并排存多套分布的布局机制与"组分索引 p 进算子签名"的接口范式（从未实例化 NPOP>1，故只是范式不是实现）。 | permissive |
| `CFD-GO/TCLB` | reference | `layered-poiseuille-analytic-inlet` | 分层 Poiseuille 解析剖面直接作为初始/入口条件（U=6*Uavg*Y*(H-Y)/H^2，圆管 2*Uavg*(1-(r/R)^2)），并由 updateMyGlobals 把两相流量、动能、界面位置积成 globals 做误差度量；可复用于两组分分层 Poiseuille 解析对标。 | copyleft(GPL) |
| `ProjectPhysX/FluidX3D` | reference | `single-component-poiseuille-force-anchor-only` | 本仓无两组分（无 SC 互作用势、无分层界面），但与「分层 Poiseuille」共用同一套体积力机器：全局体积力作 Guo 源项进碰撞、解析反推 f=4u_maxρν/R²、并有运行中 L2 收敛判据；可作单组分对照臂的验收锚，两组分部分必须另行实现。 | restricted |
| `joconnor22/LIFE` | reference | `single-phase-periodic-x-plus-constant-dpdx-driving` | 本仓无多相，但提供了该 slug 所需驱动子机制的单相实现：左右壁设 eFluid 得到 x 周期，压差以常体积力 dpdx 注入（force_xy=(ρ·Drho·gravityX+dpdx)·ΔxΔt²/Δm，每格一次、不随 ρ 更新），配合正则化壁面对上下边界闭合，形成压力驱动的通道层流；重启/输出正常。 | copyleft(GPL) |
| `lettucecfd/lettuce` | reference | `single-phase-poiseuille-baseline-skeleton` | Flow 对象自带 analytic_solution(t) 与壁面 bounce-back 掩码，配 ErrorReporter 按间隔记录相对范数误差；MCMP 两组分分层 Poiseuille 可复用该骨架（单场 f 扩两组分 + 界面力项）。 | permissive |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | reference | `shan-chen-mcmp-tau-mixed-velocity` | 多组分单格子 MCMP 草稿：每组分布独立演化，碰撞前用 tau 加权把各组塌缩为一个共同速度 vel_mix=Σ(Vel*Rho/tau)/Σ(Rho/tau)，各组再以此速度算各自平衡态。 | no-license |
| `lssfau/walberla` | reference | `no-mcmp-two-phase-only` | 本仓无 MCMP（无组分概念、无组分输运方程）；两组分分层 Poiseuille 无法直接映射。可借用的只是相场的分层初始化几何写法与变密度平衡态装配，把"两组分"降级为"两相分层"来近似。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | reference | `free-energy-pressure-tensor-body-force` | 以 F=1/2A phi^2+1/4B phi^4+1/2 kappa(grad phi)^2 的自由能路线替代 Shan-Chen：压强张量 P_ab 直接进碰撞平衡应力，界面力以 -phi grad mu 加到 hydro->force，再用 Guo 速度移位入碰撞；两组分分层 Poiseuille 可照此搭（非伪势路线）。 | permissive |
| `sorush-khajepor/listLBM` | reference | `shan-chen-mcmp-velocity-mixing` | 多组分 SC：初始化时预计算 othercomp/othercomppotid 索引表，每步对 ncomps-1 个其它组分求相互作用力并叠加体积力 acc*rho；碰撞用「速度混合、无源项」方案 u=(sum_i sum_q c f + F)/(rho_1+rho_2)+F/rho_i 后各组分用 f_eq(rho_i,u)，rho 和过小则置 u=0。 | copyleft(GPL) |
| `AminZar69/CudaPhaseField` | none | `not-applicable-phase-field-not-pseudopotential` | No contribution. The code has no Shan-Chen pseudo-potential, no interaction strength G, no colour-gradient, no component-resolved pressure boundary, no Poiseuille/layered-flow setup; its multiphase physics is a ternary Allen-Cahn phase field with algebraic third phase. Mapping it onto the MCMP Poise | copyleft(GPL) |

**推荐路线**：以 lbm-principles-practice/code（MIT）的双组分 SC + Zou-He 反算为母版起最小可证单元：单通道 D2Q9、两组分各自演化、界面位置与分相流量由积分量输出；解析锚用 TCLB 的分层剖面 U=6·Uavg·Y(H−Y)/H²（reference，仅取公式）。力项注入取 lbmpy 的重心 Guo 形式，速度混合取 listLBM 的策略化写法并显式记录「谁混合、按 ρσ 还是按 τ」这一自由度。

**推荐依据（逐条可溯源）**

- `lbm-principles-practice/code` / `scmp-two-component-planar-interface` → 修订 transfer **adapt**（原表 `adapt`）。唯一 MIT 的可搬两组分 SC 骨架：单格子多组分分布 + 平面界面初值，最小可验证（原表即 adapt：通道几何/边界需改写，故不升 fits）。许可 permissive → 可搬码（保留版权声明）。
- `lssfau/lbmpy` / `shan-chen-mcmp-barycentric-guo` → 修订 transfer **reference**（原表 `fits`）。重心坐标 Guo 力项是正确的多组分力注入范式（力进入 u_eq 的方式决定界面伪速度）。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `sorush-khajepor/listLBM` / `shan-chen-mcmp-velocity-mixing` → 修订 transfer **reference**（原表 `reference`）。速度混合的另一种取法：预计算 othercomp 索引表，工程上最容易在大网格上跑。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。

**风险与许可证约束**：各家最大分歧不是方程而是『混合速度』与『壁面润湿系数』两处自由选择，直接决定判据②两相流量与界面 spurious current 是否达标；迁移时必须把这两处做成配置项并给出默认值与出处。lbmpy/listLBM 为 GPL-3.0 → 只读公式，不搬码。

### F02 伪势 SCMP/MCMP 力项与势函数

*判据*：Shan-Chen 互作用力求和、psi 形式、EOS 注入、Guo/EDM 力项注入点

*规模*：3 条谱系行 / 3 仓；迁移分布 adapt 2，reference 1

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `lbm-principles-practice/code` | adapt | `shan-chen-laplace-pressure-calibration` | Form the Shan-Chen pressure p=sum_s rho_s/3 + sum_{s,ss} g[s][ss] psi_s psi_ss/6, average liquid pressure in a 4x4 node box at the droplet centre and gas pressure in a corner box, locate the interface along a diameter by linear interpolation of the rho crossing rho_av=0.5(rho_gas+rho_liq), and repor | permissive |
| `sorush-khajepor/listLBM` | adapt | `pluggable-pseudopotential-guo-forcing` | 把 psi(rho) 作为过程指针策略对象注入碰撞；SC 相互作用力只覆写 getforce，统一由父类 Guo 力项（速度平移 u+F/2rho + 源项 (1-0.5omega)w[...]）注入，换 EOS/换多相模型不动碰撞主体。 | copyleft(GPL) |
| `sailfish-team/sailfish` | reference | `shan-chen-explicit-stencil-forcing` | 伪势 SC 力以显式 stencil 计算 F(x)=−Gψ(x)Σ_i w_i e_i ψ(x+e_i)（ψ 只取 linear 或 classic 指数形式），再除以 ρ 转成加速度进入碰撞；实测均匀场上该力恒为 0（无虚假体力），相对连续极限 −Gc_s²ψ∇ψ 的 L2 误差随界面厚以 a^-1.9 收敛（W=8a 时 0.39%，W=2a 时 5.99%）。 | copyleft-weak(LGPL) |

**推荐路线**：伪势层拆成三个可插拔点：ψ(ρ) 策略、互作用力 stencil（含 EDM 展开）、力项注入（Guo/EDM）。标定护栏直接吃 lbm-principles 的 Young-Laplace 标定器（fits），先钉 σ 再谈密度比。

**推荐依据（逐条可溯源）**

- `lbm-principles-practice/code` / `shan-chen-laplace-pressure-calibration` → 修订 transfer **adapt**（原表 `adapt`）。MIT 且自带『标定器 + 护栏』：把 ψ/EOS/G 与 Laplace 压差一起校准，正是 GAP-LBM-MCMP 验收要的工装形态。许可 permissive → 可搬码（保留版权声明）。
- `sailfish-team/sailfish` / `shan-chen-explicit-stencil-forcing` → 修订 transfer **reference**（原表 `reference`）。互作用力显式 stencil 求和的规范写法（LGPL → 只取公式），适合做力项基线对照。许可 copyleft-weak(LGPL) → 禁止搬码（动态链接例外不适用源码级抽取）。
- `sorush-khajepor/listLBM` / `pluggable-pseudopotential-guo-forcing` → 修订 transfer **adapt**（原表 `adapt`）。ψ 作为过程指针策略注入 + 统一 Guo 力项注入点，是我们要的分层结构。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。

**风险与许可证约束**：TCLB/listLBM 均 GPL-3.0；ψ 的 sqrt 定义域（ρ 下界）与 EDM 高阶项的数值稳定性需自测，不可照抄阈值。

### F03 高密度比与界面通量路线

*判据*：密度比≥10 的两相界面（伪势 EOS 改进 / color-gradient / 相场密度映射 / Lee 分裂）

*规模*：10 条谱系行 / 8 仓；迁移分布 adapt 4，none 1，reference 5

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `CFD-GO/TCLB` | adapt | `pressure-evolution-allencahn-phasefield` | 水动力分布零阶矩存压力、密度由相场 phi 代数映射，界面用保守 Allen-Cahn（含反扩散系数 1/W 的 Fphi 项），表面力取 mu*grad(phi) 并加 (0.5-tau)(rho_h-rho_l)(tau_ij*grad(phi)) 黏性力，配合 force_fixed_iterator 定点迭代自洽；是绕开伪势高密度比瓶颈的正解配方。 | copyleft(GPL) |
| `lssfau/lbmpy` | adapt | `pf-eos-maxwell-high-density-ratio` | 高密度比不走 psi(rho) 势，而是由 EOS 积分得游离能 psi_eos=int(p/rho^2 drho)，再用 Maxwell 等压条件 psi_eos(rho_g)=psi_eos(rho_l) 解积分常数，得 f_bulk=lambda/2*(psi_eos-psi_0)^2 + kappa 界面项；力由压力张量 pi_ab=(f-sum phi mu)delta_ab - kappa d_a phi d_b phi 取散度施加。 | copyleft(GPL) |
| `lssfau/walberla` | adapt | `variable-density-equilibrium-linear-in-C` | 密度作为相场 C 的线性函数直接进平衡态与体力（rho = rho_L + C*(rho_H-rho_L)，body_force = g*rho），粘度按 omega(C) 随相变；相场 MRT 松弛率表置零号矩为 0 保证质量守恒、只松弛一阶矩。实测算例密度比 1000(RTI)/744(Taylor bubble)。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | adapt | `no-density-ratio-available` | 本仓无密度比机制（rho 为旁观量，三元模型注释明示），只可借用其自由能->mu->力链路与sigma/xi 标度关系来反解界面参数；高密度比需自建变密度平衡态。 | permissive |
| `CFD-GO/TCLB` | reference | `shan-chen-edm-lycett-luo` | 伪势路线配方：psi=sqrt(-p_cs(rho)+rho/3) 由 C-S 型 EOS 给出，互作用力按 stencil 权重求和并用 EDM 精确差分展开，再叠 Lycett-Luo 高阶力项（含非平衡应力 Phi 与 gamma 修正）与粘度-密度平滑关系；壁面用虚拟密度 VirtualRhoWBC 控接触角。 | copyleft(GPL) |
| `CFD-GO/TCLB` | reference | `mcmp-two-component-cross-force` | 双组元 MCMP：湿/干两套 D2Q9 分布各自 MRT 碰撞，交叉作用力 F_k=-Gc*psi_k(x)*sum_i w_i*psi_k'(x+e_i)*e_i（sympy 生成 C 代码），壁面用 psi=Gad/Gc 设接触角，公共速度按 rho_k/omega_k 加权，压力 P=rho/3+Gc*psi_f*psi_g/3。 | copyleft(GPL) |
| `PorousMediaSimulation/openLBMPM` | reference | `peng-robinson-pseudopotential` | Peng-Robinson EOS pseudo-potential psi=sqrt(2(p-rho/3)/(c0 G)) with p from R,T,a,b,alpha; written but never invoked (calEffectiveMassPR is an empty stub) - reference only for wiring an EOS-based high-density-ratio SC branch. | no-license |
| `lanl/MF-LBM` | reference | `two-pdf-partial-density-colorgradient` | 用两套完整 PDF（f 与 g）分别承载两个"组元"，宏观量取和：den=ρ1+ρ2、φ=(ρ1-ρ2)/den、u 由总 PDF 的一阶矩给出；再着色把总 PDF 拆回两套（主方向权重 w_equ_1、对角 1/(36√2)），β 控界面锐度；粘度按 φ 线性插值倒数后转松弛率。 | permissive |
| `omalaspinas/palabos` | reference | `lee-two-phase-density-ratio` | Lee/He-Lee 两相扩散界面以显式 densityRatio 参数化，界面用相场 C 与化学势 mu 的三步分裂（C 与梯度 -> gradC/rho/mu -> gradMu/laplaceMu），表面张力与接触角由参数注入；SC 路线无任何高密度比机制。 | copyleft-strong(AGPL) |
| `AminZar69/CudaPhaseField` | none | `not-supported` | Not supported. No pseudo-potential/EOS (no Peng-Robinson, no Csanad), no free-energy functional, no van der Waals loop handling; the (1000,500,100) triple is a per-component mass density used only in rho=sum phi_k rho_k and in the pressure-gradient force, not a high-density-ratio mechanism. | copyleft(GPL) |

**推荐路线**：主路线走『相场/变密度平衡态』而不是死磕伪势 EOS：walberla 的 ρ=ρ_L+C(ρ_H−ρ_L) 线性映射 + TCLB 的压力演化格式组合，可同时保住密度比与 Mach 限制。伪势侧仅保留 MPLBM-UT 的低密度比基线作对照。

**推荐依据（逐条可溯源）**

- `lssfau/walberla` / `variable-density-equilibrium-linear-in-C` → 修订 transfer **adapt**（原表 `adapt`）。唯一把密度直接写进平衡态与体力、并把黏度随相变参数化的配方（ρ 比可至 1000）。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `CFD-GO/TCLB` / `pressure-evolution-allencahn-phasefield` → 修订 transfer **adapt**（原表 `adapt`）。『零阶矩存压力 + 密度由 φ 代数映射』绕开伪势密度比瓶颈，是高密度比锚点的正解形状。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `lanl/MF-LBM` / `two-pdf-partial-density-colorgradient` → 修订 transfer **reference**（原表 `reference`）。双套 PDF 的 color-gradient 写法（密度比 1）作为『界面通量路线』的对照基线；BSD 可读。许可 permissive → 可搬码（保留版权声明）。
- `CFD-GO/TCLB` / `shan-chen-edm-lycett-luo` → 修订 transfer **reference**（原表 `reference`）。伪势线上的高阶力项补丁（EDM 精确差分 + Lycett-Luo 修正），是提高密度比前的必要一步。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。

**风险与许可证约束**：密度比>100 后 spurious current 与界面增厚耦合，必须用 Laplace 压差+界面厚度双判据；walberla(GPL)/TCLB(GPL) 只可重写；MPLBM-UT 本体捆绑 GPL Palabos。

### F04 自由面（fill-level / VOF / mass tracking）

*判据*：气液界面用充填率+质量会计演化，不含相场序参量

*规模*：9 条谱系行 / 6 仓；迁移分布 adapt 6，fits 1，none 1，reference 1

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `omalaspinas/palabos` | fits | `thuerey-mass-tracking-free-surface` | 8 态 flag（empty/interface/fluid/wall/protect/temporarilyProtect/protectEmpty/slipWall）+ 界面格 mass 做体积记账，质量变化用入射分布累加（Thuerey eq.6），超额质量强制再分配、丢失质量单独统计成可观测量，表面张力由曲率法向注入。 | copyleft-strong(AGPL) |
| `Maarten-vd-Sande/lbm` | adapt | `thuerey-vof-mass-exchange` | 每格独立 mass 场做体积质量账本，F→I 直接取上游分布并记账，I→I 交换按两侧填充率 ε 平均加权；转相时把超额质量按 max(0,e·n) 加权投给 interface 邻格，下一步收集回 mass。 | no-license |
| `Maarten-vd-Sande/lbm` | adapt | `fill-gradient-normal` | 界面法向由填充率 ε 的相邻差分 n=0.5*(ε_{i-1}-ε_{i+1}, ε_{j-1}-ε_{j+1}) 给出，再用 n·e_opp>0 判据把"指向液体外"的方向用 feq+feq_opp-f_opp 重构覆盖。 | no-license |
| `Maarten-vd-Sande/lbm` | adapt | `average-surround-init` | 空腔格升级为界面格时，用周围 FLUID/INTERFACE 邻格的 rho 与 u 的算术平均初始化该格宏观量并重建 feq，避免新格从零注入伪压力。 | no-license |
| `ProjectPhysX/FluidX3D` | adapt | `free-surface-flag-state-machine-plic-curvature` | 用 TYPE_F/I/G 加转移标记 IF/IG/GI 的状态机逐格判定界面演化；界面格存质量 mass 与充填率 phi=clamp(mass/rho,0,1)；气体侧 DDF 由 feq(1−ρ_lap, u_shifted) 与质量通量重建；ρ_lap=6σK，K 由 D3Q27 邻域的 PLIC 立方体-平面反解 + 5 参数 Monge 面最小二乘求得；多余质量均分给 F/I 邻居，分不掉则留本地以保守恒。 | restricted |
| `lssfau/walberla` | adapt | `excess-mass-9-family` | 胞元转换后剩余质量按 均匀/加权 × 全界面/新界面/旧界面 × 是否回退液相 组合成 9 种分配模型，由 ExcessMassDistributionModel 按名字选择，分配 sweep 紧随胞元转换之后执行。 | copyleft(GPL) |
| `lssfau/walberla` | adapt | `fill-level-model-family-configurable` | flag 三态(liquid/interface/gas)+连续 fill level；时间步为固定 sweep 序列：边界→力加权→StreamReconstructAdvect 融合→碰撞/流式→胞元转换→回填→多余质量分配→标志复位→块状态更新；每个环节是可配置的模型族（重建 5 × 回填 5 × 多余质量 9 × 曲率 3）。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | reference | `map-based-empty-site` | 无自由面模型；可借鉴 map 状态 + 面通量无穿透（advflux_cs_no_normal_flux）把气相实现为空单元并在界面处施加质量/动量条件。 | permissive |
| `AminZar69/CudaPhaseField` | none | `not-supported` | Not supported. No VOF/fill fraction, no interface reconstruction, no empty-cell handling, no front tracking; the whole domain is filled with a three-phase mixture (sum phi = 1 everywhere). | copyleft(GPL) |

**推荐路线**：自由面按三件套拆解实现：状态机（flag 转移）、质量账本（F→I→G 转移与超额质量分配）、界面分布重建（法向由 fill level 差分 + 非平衡反弹）。质量分配必须做成可选模型族并带守恒断言（Σmass 漂移）。

**推荐依据（逐条可溯源）**

- `lssfau/walberla` / `excess-mass-9-family` → 修订 transfer **adapt**（原表 `adapt`）。把『多余质量怎么分配』显式做成 9 种可切换模型族——这正是自由面实现最容易悄悄错的地方。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `omalaspinas/palabos` / `thuerey-mass-tracking-free-surface` → 修订 transfer **reference**（原表 `fits`）。8 态 flag + 逐格质量账本的经典实现，状态机语义最完整。许可 copyleft-strong(AGPL) → 禁止搬码：算法/公式级 + 网络服务条款风险。
- `Maarten-vd-Sande/lbm` / `fill-gradient-normal` → 修订 transfer **reference**（原表 `adapt`）。极简：法向取填充率差分 + n·e_opp 判据重建界面分布，适合做教学级参考实现。许可 no-license → all-rights-reserved：禁止搬码，仅可引用已发表算法。

**风险与许可证约束**：FluidX3D 自定义许可明令『不得用于训练 AI 模型』，其自由面/PLIC 代码不得进入任何知识蒸馏语料；palabos AGPL → 只取状态机语义。

### F05 相场 Allen-Cahn / Cahn-Hilliard

*判据*：序参量+化学势的扩散界面演化（含三元/顺序参数/conservative AC）

*规模*：8 条谱系行 / 7 仓；迁移分布 adapt 5，fits 1，reference 2

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `ebinan92/Fingering_dynamics` | fits | `free-energy-cahn-hilliard-core` | 守恒型 Cahn-Hilliard 相场内核：序参量 psi 由第二个分布 g 承载（psi=sum g），mu=a*psi(1-psi^2)-kappa*lap(psi)，kink 解 psi=tanh(x/xi) 与 sigma=4*kappa/(3*xi)（等价 kappa=0.75*xi*sigma）经本报告解析+数值复核一致，可作为内核验收门。 | no-license |
| `AminZar69/CudaPhaseField` | adapt | `ternary-double-well-with-harmonic-lambda` | Ternary chemical potential mu_k = (12/w)[lambda_k phi_k(1-phi_k)(1-2phi_k) - 2 lambda_t phi1 phi2 (1-phi1-phi2)] - (3/4) w lambda_k lap(phi_k), with lambda_k = sigma_jk + sigma_ki - sigma_ij recovered from the 3x3 surface-tension matrix and lambda_t = 3/(1/lambda_1+1/lambda_2+1/lambda_3) (harmonic m | copyleft(GPL) |
| `AminZar69/CudaPhaseField` | adapt | `ternary-conservative-ac-two-order-parameters` | Conservative Allen-Cahn for phi1/phi2 with an interface-normal compression source term, phi3 closed algebraically as 1-phi1-phi2, relaxation factor fixed by mobility as w_c=1/(0.5+3m) (over-relaxed but \|1-w_c\|<1); equilibrium follows h_eq = phi_k*(w_i+w_i*(3+4.5 e.u-1.5u^2)) - 0.5*w_i*(4/w)*\|phi_k(1 | copyleft(GPL) |
| `CFD-GO/TCLB` | adapt | `conservative-allen-cahn-h-population` | 相场用第三套分布 h 承载：heq=C*feq(u)，界面力 Fphi=w_i*((1-4(C-0.5)^2)/W)*(e_i.n)，更新 h - omega_phi*(h-heq+0.5*Fphi) + Fphi；化学势 mu=4*(12*sigma/W)*(C-phi_l)*(C-phi_h)*(C-phi_avg)-1.5*sigma*W*laplace(C)，laplace 用各向同性 27 点模板。 | copyleft(GPL) |
| `lssfau/walberla` | adapt | `conservative-AC-double-force-split` | 相场走独立 MRT LB(松弛率 1/(0.5+3*mobility))，两个力项物理分离：interface_tracking_force(界面反扩散)与 hydrodynamic_force(CSF 表面张力+重力)，各自作为独立 AssignmentCollection 加到相场/水动力 update rule；接触角由 codegen 生成 boundary kernel；热毛细场景另加 D3Q7 温度场与 RK1-RK4 时间推进。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | adapt | `cahn-hilliard-only` | 只有守恒 Cahn-Hilliard（面通量 M grad mu，含守恒漂移补偿 PHI_CONSERVE_*）+ 三元 Semprebon 泛函；Allen-Cahn 非守恒方程需自研，但 fe_vt_t 提供了加后端的扩展点。 | permissive |
| `lssfau/lbmpy` | reference | `allen-cahn-csf-force-chain` | 化学势 mu = d f/d phi - kappa*laplacian(phi) 符号生成，界面演化用 interface_tracking_force 并入碰撞规则；流体侧力分解为 pressure_force(由 rho(phi) 差) + viscous_force(按 tau 与密度比) + surface_tension_force(CSF) + 可选 thermocapillary_surface_tension_force(sigma(T) 线性)；相场方程用 RK 推进。 | copyleft(GPL) |
| `omalaspinas/palabos` | reference | `lee-chemical-potential-splitting` | 无 Allen-Cahn 实现；最接近的是 Lee 保守相场分裂：先算相场 C 与梯度，再由 C/rho 得化学势 mu，再对 mu 求梯度/拉普拉斯得到界面力与表面张力，分 4 个 data processor 串联。 | copyleft-strong(AGPL) |

**推荐路线**：方程骨架取 ludwig（BSD，可搬），力项分层取 walberla 的双力项分离（重写），三元/顺序参数取 CudaPhaseField 的保守 AC + 调和 λ 构造（公式）；解析锚用自由能 kink 解。

**推荐依据（逐条可溯源）**

- `ludwig-cf/ludwig` / `cahn-hilliard-only` → 修订 transfer **adapt**（原表 `adapt`）。BSD-3 可搬：守恒 CH 的面通量形式 + 守恒漂移补偿 PHI_CONSERVE_* 是可直接落地的方程骨架。许可 permissive → 可搬码（保留版权声明）。
- `lssfau/walberla` / `conservative-AC-double-force-split` → 修订 transfer **adapt**（原表 `adapt`）。把 interface_tracking 力与 hydrodynamic 力物理分离（walberla 的做法）是相场-流体耦合的正确分层。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `ebinan92/Fingering_dynamics` / `free-energy-cahn-hilliard-core` → 修订 transfer **reference**（原表 `fits`）。双分布（f→ρ,u；g→ψ）自由能 CH 内核最短，kink 解可作解析锚。许可 no-license → all-rights-reserved：禁止搬码，仅可引用已发表算法。

**风险与许可证约束**：AC（非守恒）与 CH（守恒）不可混用；ludwig 自述无 Allen-Cahn，需自研 AC 项。CudaPhaseField GPL-3.0、Fingering 无许可证 → 公式级。

### F06 润湿 / 接触角

*判据*：壁面接触角边界（几何法、wall phase gradient、θ↔G_ads 反演、iso8 法向平滑）

*规模*：5 条谱系行 / 5 仓；迁移分布 adapt 4，fits 1

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `ebinan92/Fingering_dynamics` | fits | `diffuse-interface-wetting-via-wall-order-parameter` | 润湿不用 Shan-Chen 吸附系数 G_ads, 而是把壁面格点 psi 固定为 psi_wall 并作为差分虚拟节点参与 nabla/lap。优点：无 G_ads 标定；缺点：接触角与 psi_wall 的映射需自建标定表（本仓没做）。 | no-license |
| `je-santos/MPLBM-UT` | adapt | `huang2007-contact-angle-inversion` | 由壁面黏附系数反算接触角 cosθ=4G_ads/(Gc(ρ_w-ρ_nw))，并同时给非湿相侧 -G_ads；用液滴三点标定（G_ads=-0.4/0/+0.4 ↔ 156°/90°/24°）校验。移植时须去掉原实现的 abs() 以保留湿相/非湿相方向。 | copyleft(GPL) |
| `lanl/MF-LBM` | adapt | `akai2018-geometrical-wetting` | 在壁面邻层把已归一化的色梯度 n 绕"壁面法向 n_w 与当前 n 张成的平面"旋转到与 n_w 成 θ 的方向：cos ψ=sinθ(n_w·n)/√(1-(n_w·n)²)、候选 n± = (cosθ∓cos ψ)n_w ± …，两个候选解里取与原始 n 欧氏距离更近者，从而在壁面处强制目标静态接触角而无需引入任何人工体积力。 | permissive |
| `lssfau/walberla` | adapt | `two-independent-contact-angle-impls` | 自由面侧只缓存 sin/cos 的 ContactAngle + DetectWettingSweep/ObstacleFillLevelSweep 做几何修正；相场侧由 codegen 生成 ContactAngle boundary kernel（公式在 lbmpy，按界面厚度与角度反解壁面 C 的 Neumann 条件）。两套不共享代码。 | copyleft(GPL) |
| `sailfish-team/sailfish` | adapt | `wall-phase-gradient-wetting-bc` | 润湿边界通过壁面 φ 梯度参数 bc_wall_grad_phase 实现：半程反弹时用一阶 stencil、全程反弹时用一/二阶 stencil 把壁外虚拟 φ 设为 φ_wall−order*grad_phase，从而控制接触角；注释指 Kusumaatmaja 学位论文 p.18 option 2。 | copyleft-weak(LGPL) |

**推荐路线**：两条腿：几何法（MF-LBM，法向旋转，适配任意多相路线）作默认；吸附系数反演（MPLBM-UT 公式）作标定与交叉验证。壁面法向用 iso8 平滑预处理，否则阶梯壁面会把接触角误差放大。

**推荐依据（逐条可溯源）**

- `lanl/MF-LBM` / `akai2018-geometrical-wetting` → 修订 transfer **adapt**（原表 `adapt`）。BSD-3 可搬：几何法把色梯度绕 n_w 旋转到 θ，无需 G_ads 吸附系数标定。许可 permissive → 可搬码（保留版权声明）。
- `je-santos/MPLBM-UT` / `huang2007-contact-angle-inversion` → 修订 transfer **adapt**（原表 `adapt`）。提供 θ ↔ G_ads 的解析反演与三点标定流程（156°/90°/24°）。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `sailfish-team/sailfish` / `wall-phase-gradient-wetting-bc` → 修订 transfer **adapt**（原表 `adapt`）。相场侧壁面 φ 梯度参数化，stencil 阶数与反弹类型配对，最易嵌进相场路线。许可 copyleft-weak(LGPL) → 禁止搬码（动态链接例外不适用源码级抽取）。

**风险与许可证约束**：接触角在阶梯壁面上是网格相关量，必须报告『网格×θ』矩阵而非单点值；MF-LBM 属 color-gradient（密度比 1），迁到变密度路线需重推梯度归一化。

### F07 GPU 融合 kernel 与存储压缩

*判据*：stream/collide 融合、codegen、A-A pattern、SoA/pitched、FP16 压缩、寄存器压力

*规模*：22 条谱系行 / 18 仓；迁移分布 adapt 9，fits 3，reference 9，reject 1

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `haimingz/vivsim` | fits | `jax-pure-function-aa-pattern-static-slice` | 用「纯函数 + 静态 slice 拼接」代替 jnp.roll 实现 streaming（每方向一条 concatenate），配 jit(donate_argnums=0) 与 lax.scan 把时间循环整体融合；分布函数按 AA-pattern (q,NX,NY) 布局使空间维连续、便于 XLA 合并访存；benchmark 用 warmup+repeat+block_until_ready 逐算子计时。 | permissive |
| `lbm-principles-practice/code` | fits | `fused-push-pull-aa-pattern` | Keep the rest direction in its own array f0 and the eight moving directions in direction-major layout [d][y][x]; in one kernel pull the eight shifted neighbours, compute moments and relax to equilibrium in registers, and write only the new populations (plus macroscopic fields only when a save flag i | permissive |
| `lettucecfd/lettuce` | fits | `uint8-index-mask-torch-where` | no_collision_mask 存 uint8 算子索引（pre_boundary i / collision_index / post_boundary i）而非布尔；collide 阶段逐算子 torch.where(mask==i, op(f), f, out=f) 就地施加；no_streaming_mask 同法抑制指定方向对流。 | permissive |
| `2b-t/LB-t` | adapt | `aa-pattern-odd-even-indexing` | 单一扁平数组存全部分布，偶时间步正常写/对偶槽读、奇时间步镜像；靠 PAD 使 ND 为偶数、正负速度半区对称，MASK 清零静止分量第二槽避免重复写。 | permissive |
| `AminZar69/CudaPhaseField` | adapt | `shared-memory-halo-tile-plus-merged-propagation` | (a) REAL pattern - gradient_cal and chemical_potential stage phi1/phi2/phi3 into shared tiles s_phi(0:TILE+1,0:TILE+1) with a one-cell halo ring loaded by the tx==1/TILE and ty==1/TILE threads plus four dedicated corner threads, then syncthreads and run the 8-neighbour stencil entirely from shared m | copyleft(GPL) |
| `CFD-GO/TCLB` | adapt | `per-margin-arrays-pointer-shift-streaming` | 每个 stencil margin 一个独立数组（FTabs 按 NonEmptyMargin 生成），流式不搬数据而是把该 margin 的指针按方向平移（move.pocket），相邻线程读同向数组相邻地址以保证合并访问；迭代切成 border/interior 两个 kernel，border kernel 后立即发起异步 D2H 让 interior kernel 覆盖通信。 | copyleft(GPL) |
| `PorousMediaSimulation/openLBMPM` | adapt | `shared-memory-streaming-warp-shift` | Legacy branch performs streaming inside shared memory: 32-thread warp per lattice row, nine cuda.shared.array((32,)) buffers, boundary threads pull from adjacent rows => 8 global neighbour fetches removed; still one kernel per stage (not fused). | no-license |
| `ProjectPhysX/FluidX3D` | adapt | `fused-stream-collide-in-place-esoteric-pull` | 单个 OpenCL kernel 内一次完成 拉取(stream)→求 ρ,u→Guo 力项→碰撞→写回邻居，用 A-A 双半格 + Esoteric-Pull（按时间步奇偶翻转方向索引导址）实现单 cell 原地更新、只维护一块 DDF 缓冲；固壁格直接 return，由邻居写入的镜像值形成隐式 bounce-back。 | restricted |
| `ProjectPhysX/FluidX3D` | adapt | `fp16-storage-with-ddf-shifting` | 平衡态函数直接返回 f^eq−w（「扰动法/DDF-shifting」），宏观量用 Σf+1 复原，于是 DDF 数值集中在 ±1 附近、动态范围变小，可安全压到 IEEE half（硬件 vload_half*2^-15）或自定义 1-4-11 格式；算术全程仍 FP32。D3Q19 每格显存 93→55 B。 | restricted |
| `lanl/MF-LBM` | adapt | `aa-pattern-fused-stream-collide` | 奇数步在**单个 kernel** 内完成「从 18 个邻居 pull 分布 → 求 ρ1/ρ2/φ 与宏观量 → CSF 力 → MRT 碰撞 → R-K 再着色 → push 到邻居的反方向槽位」；偶数步不做流动，只在原地把反方向槽位互换再碰撞。每步每方向只发生 1 读 1 写，两套 PDF 交错寻址，避免全场 copy 与两套完整分布数组并存。 | permissive |
| `lettucecfd/lettuce` | adapt | `component-protocol-template-codegen` | 每个组件实现 native_available()/native_generator() 返回元数据，Generator 用字符串模板拼出 lettuce_cuda.cu（单 __global__ 内顺序 boundary→collision→streaming），subprocess setup.py install 编译为 torch CUDAExtension，Python 侧交换 f/f_next 实现零拷贝步进。 | permissive |
| `lssfau/lbmpy` | adapt | `fused-kernel-type-selector` | 用单一 kernel_type 选择器在「碰撞+宏观量输出+流式」之间融合或分离（default_stream_collide / collide_only / esotwist），pdf 数组按 fzyx（逐方向连续 SoA）布局，AA-pattern 单数组原地省一半内存免 swap，esotwist 只访问必要方向。 | copyleft(GPL) |
| `2b-t/LB-t` | reference | `fused-kernel-register-pipeline` | 无 GPU 实现；AVX2 路径演示了把矩求和、平衡态构造、碰撞回写全部保持在寄存器流水内、不落中间数组的单遍结构（可作为 GPU 融合 kernel 的算法骨架参考）。 | permissive |
| `AminZar69/CudaPhaseField` | reference | `maxregcount-pinned-before-tuning` | Register pressure, not bandwidth, is what breaks the biggest kernel: collision_h needs 18 populations + 18 equilibria + 18 source arrays live at once and overflowed the register budget, silently failing the launch. The fix is a compile-time cap applied to every build mode (maxregcount:64) documented | copyleft(GPL) |
| `espressomd/espresso` | reference | `stream-collide-fusion` | 碰撞+传播融合 kernel，通过 pystencils 代码生成，支持 AVX2/CUDA 自动向量化 | copyleft(GPL) |
| `hietwll/LBM_Taichi` | reference | `taichi-jit-single-phase-lgk` | Taichi @ti.kernel 自动 JIT 编译 D2Q9-BGK collide-stream 到 GPU；双缓冲 f_old/f_new；AoS Vector.field(9) 布局 | no-license |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | reference | `conv-as-streaming-fixed-kernel` | 把离散分布按 [1,*Ndim,q] 存放（q 当通道），stream = 一次 3x3(2D)/3x3x3(3D) 常量卷积核卷积、bounce = 一次 1x1 卷积（核为 BOUNCE 反相置换矩阵），迁移与方向重排/壁面反射都交给框架的卷积实现融合。 | no-license |
| `lssfau/walberla` | reference | `generated-gpu-sweep-pitched-plus-inplace-pack` | kernel 由 Python 生成，目标 Target.GPU，靠 kernel_type 选 stream_pull_collide / collide_stream_push 融合方向；GPUField 同时支持 AoS(zyxf)/SoA(fzyx) 且可开 pitched memory；交替 pull/push 用编译期奇偶分支实现 in-place 双场流式；GPU 通信用就地打包(CombinedInPlaceGpuPackInfo)与 GPU 直通缓冲。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | reference | `one-kernel-per-step` | 每步一个 __global__ 核并按 tdpDeviceSynchronize 串行：collision、propagation、每个力项各一核，融合空间明确；kernel_3d_t 提供统一循环域，__restrict__ 标注用于合并。 | permissive |
| `omalaspinas/palabos` | reference | `stdpar-aa-pattern-dispatch` | 加速引擎把格子改成 SoA（populations 一维 N*numPop 数组）+ 每格整数 collisionMatrix 模型 id + 半程 bounce-back 链接表，用 C++17 std::execution::par_unseq for_each 并行；GPU 仅通过 nvc++ 把 stdpar 编译到设备，无手写融合 kernel，collide 与 stream 仍为两次独立遍历。 | copyleft-strong(AGPL) |
| `sailfish-team/sailfish` | reference | `soa-per-direction-fused-collide-propagate` | 每个 lattice 一个平坦 buffer 长 Q*N_active，按方向分片连续（idx=gi+q*N+offset），使传播成为整片平移；碰撞与传播融合在同一内核（*CollideAndPropagate*），跨方向部分用块内共享内存传播或 warp shuffle，另提供 AB 双缓冲与 AA 原地读时传播两种访问模式。 | copyleft-weak(LGPL) |
| `ebinan92/Fingering_dynamics` | reject | `cupy-dropin-no-kernel-fusion` | GPU 版只是 numpy->cupy 直替换（78 处 cp.）, 无 RawKernel/ElementwiseKernel、无融合、无多卡；每步仍有 ~20 个算子级 kernel 与全网格临时量。只能参考算子求值顺序，性能路径不可继承（负面结论，用于关闭该能力的捷径假设）。 | no-license |

**推荐路线**：近期（可运行）：lettuce/vivsim 的框架内融合（MIT，fits）＋ A-A pattern 省一半内存；中期（性能）：照 walberla/lbmpy 的生成式 sweep 设计自研 codegen（禁止搬码）；存储压缩按 FluidX3D FP16 的公开算法自行实现。

**推荐依据（逐条可溯源）**

- `lettucecfd/lettuce` / `component-protocol-template-codegen` → 修订 transfer **adapt**（原表 `adapt`）。MIT：算子按组件模板生成、JIT 可选，Python 侧落地成本最低。许可 permissive → 可搬码（保留版权声明）。
- `haimingz/vivsim` / `jax-pure-function-aa-pattern-static-slice` → 修订 transfer **fits**（原表 `fits`）。MIT：纯函数 + static_slice 编译期定位，适合先验证融合正确性再追性能。许可 permissive → 可搬码（保留版权声明）。
- `lssfau/walberla` / `generated-gpu-sweep-pitched-plus-inplace-pack` → 修订 transfer **reference**（原表 `reference`）。生成型 sweep + GPUField AoS/SoA 双布局 + 原地打包，是设计范式而非可搬码。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。

**风险与许可证约束**：CudaPhaseField 的实测教训：寄存器压力（27.7 KB/block）先于带宽成为瓶颈，融合过多的单核会静默启动失败——融合粒度必须配 maxregcount 固定 + 启动错误检查。

### F08 分布式多卡 / MPI

*判据*：Cart 分区、halo 非阻塞交换、ghost 交换融合进 kernel、负载均衡

*规模*：14 条谱系行 / 14 仓；迁移分布 adapt 4，fits 2，none 1，reference 7

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `lbm-principles-practice/code` | fits | `mpi-1d-slab-unpacked-halo-testall-overlap` | Split NY into contiguous rank slabs with one halo row per side; because the layout is direction-major with contiguous y-rows, each halo block is (ndir-1)*NX contiguous doubles so MPI_Isend/MPI_Irecv needs no packing; call MPI_Testall after every interior y-row so communication overlaps the remaining | permissive |
| `pylbm/pylbm` | fits | `cartesian-mpi` | 基于 mpi4py 的笛卡尔拓扑管理，自动分区（Compute_dims）或用户指定，非阻塞 Irecv/Isend 通信 | permissive |
| `cfdemons/LUMA` | adapt | `mpi-cart-smart-decompose-halo` | MPI_Cart_create 建三维周期拓扑；载荷按活跃单元数评估（LoadImbalanceData）在均匀分解与 smart decomposition 间择优；每方向独立 send/recv 缓冲，先协商消息尺寸再 pack/unpack，步末用 MPI_Isend/Irecv 发起、Waitall 收齐；halo（recv layer）站点在力累加与 IBM 支撑点铺展时被跳过以防重复计数；跨 rank 标记点用 MarkerComm/SupportComm 结构交换。 | permissive |
| `lanl/MF-LBM` | adapt | `openacc-async-halo-overlap` | 把每步 kernel 拆成「边界条带 kernel（z/y 各自一条 async 队列）」+「内部大块 kernel」，中间插入 halo 打包与 MPI 异步收发；GPU 版（#ifdef _openacc）把内部 kernel 排在 mpi_send_req **之前** 提交，CPU 版相反，以此在两种平台上都让通信藏在大块计算后面；最后统一 sync 把 halo 灌回。 | permissive |
| `ludwig-cf/ludwig` | adapt | `mpi-halo-post-wait-split` | MPI 三维域分解，halo 交换拆成 post(非阻塞 isend/irecv + 预建通信图) 与 wait 两段，中间可插本地计算隐藏延迟；胶体跨进程字节级 MPI_Irecv/Isend + 分段规约。 | permissive |
| `omalaspinas/palabos` | adapt | `mpi-envelope-async-halo` | MPI 规则块分解 + envelope ghost 层；通信器可切：ParallelBlockCommunicator3D 内部用 sendRecvPool 做非阻塞收发（通信与计算重叠），BlockingCommunicator3D 作阻塞对照；多块管理支持稀疏块结构。 | copyleft-strong(AGPL) |
| `CFD-GO/TCLB` | reference | `async-copy-waitany-overlap` | 27 方向独立 MPI 缓冲；每步先用 outStream 异步 D2H，再 Irecv/Isend 全量发起并 MPI_Waitany 收一个拷一个（H2D 走 inStream），中间插入 interior kernel 覆盖通信；单 rank 单 GPU，多卡靠 MPI 域分解。 | copyleft(GPL) |
| `ProjectPhysX/FluidX3D` | reference | `single-node-multi-gpu-host-staged-halo` | 按 (Dx,Dy,Dz,Ox,Oy,Oz) 把域切成每 GPU 一块 LBM_Domain（各自 cl::Context/queue 与独立编译单元）；halo 用 transfer_extract_*/transfer__insert_* kernel 做 VRAM 内选择性拷贝，再经 host staging buffer 以 CPU 指针 swap 交换 +/− 方向缓冲；逐方向 x→y→z 串行推进，每方向前后各一次 finish_queue 全局同步。 | restricted |
| `haimingz/vivsim` | reference | `lax-ppermute-periodic-halo` | 域沿 x/y 均分到设备 mesh，每步用 jax.lax.ppermute 在设备轴上交换相邻子域边界分布函数（周期环形拓扑）实现跨设备迁移。最小可用但阻塞、无计算通信重叠。 | permissive |
| `je-santos/MPLBM-UT` | reference | `palabos-multiblock-mpi-plus-checkpoint` | 由 Palabos MultiBlockLattice3D 自动做 MPI 多块分区，应用层只负责生成 mpirun -np N 命令与检查点二进制落盘/恢复（saveBinaryBlock/loadBinaryBlock + 计数广播 bCast），使长压力阶梯可在多核上中断续算。 | copyleft(GPL) |
| `lssfau/lbmpy` | reference | `mpi-ghost-exchange-kernel-fused` | 本仓不做域分解，只把周期边界 ghost 交换编译进生成 kernel（拿 src/dst slice 直接生成 CUDA copy kernel），注释称比 cupy 数组拷贝快近 2 倍；域分解交给 pystencils DataHandling/waLBerla。无异步通信隐藏。 | copyleft(GPL) |
| `lssfau/walberla` | reference | `cross-physics-comm-overlap-no-cuda-stream` | 两套 PDF 场用相反流式方向，时间循环里用 BeforeFunction 启动 A 场的通信、用 B 场的计算遮盖它，再用 AfterFunction 等待；GPU 侧通信由生成型 pack info + UniformGPUScheme/NonUniformGPUScheme 完成。 | copyleft(GPL) |
| `sailfish-team/sailfish` | reference | `subdomain-zmq-connector-exchange` | 子域切分 + 连接器抽象（multiprocessing 共享数组 / ZMQ PAIR / 跨主机 ZMQ / blosc 压缩版）交换边界分布层；主从进程用 socket 交换端口与统计；集群通过 execnet(SSH) 或 PBS 启动每节点每 GPU 一个 runner。 | copyleft-weak(LGPL) |
| `AminZar69/CudaPhaseField` | none | `not-supported` | Not supported. No MPI, no domain decomposition, no halo exchange, no multi-GPU; execution model is one host + one GPU with module-level device arrays sized by compile-time nxd/nyd. | copyleft(GPL) |

**推荐路线**：分区层用 LUMA 的 Cart + 活跃单元负载评估（Apache，可搬）；通信层先把 halo 做成显式 non-blocking post/wait 两段（ludwig 的 post-wait-split）以便插计算遮盖，再图 ghost 交换融合。

**推荐依据（逐条可溯源）**

- `cfdemons/LUMA` / `mpi-cart-smart-decompose-halo` → 修订 transfer **adapt**（原表 `adapt`）。Apache-2.0 可搬：MPI_Cart 拓扑 + 按活跃单元数的 smart decomposition 择优，正是体素多孔域要的负载均衡。许可 permissive → 可搬码（保留版权声明）。
- `pylbm/pylbm` / `cartesian-mpi` → 修订 transfer **fits**（原表 `fits`）。BSD：mpi4py 笛卡尔分区骨架，最小可跑。许可 permissive → 可搬码（保留版权声明）。
- `lssfau/lbmpy` / `mpi-ghost-exchange-kernel-fused` → 修订 transfer **reference**（原表 `reference`）。把 ghost 交换编译进生成 kernel（自述比数组拷贝快近 2 倍）——目标形态。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。

**风险与许可证约束**：体素多孔域的空单元负载失衡是主要风险，均匀分区会退化；LUMA 的 load 评估函数需自测。

### F09 稀疏 / 多孔介质几何

*判据*：list 型稀疏存储、子链表分类、间接寻址、体素摄入、部分饱和固体、PSB

*规模*：19 条谱系行 / 16 仓；迁移分布 adapt 8，fits 5，none 1，reference 5

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `2b-t/LB-t` | fits | `halfway-bounce-back-parity-swap` | 壁面格点在碰撞后把 n 方向的 incoming 分布写到对偶方向 !n 与翻转时间步标签的槽位，等价标准 half-way BB；无插值/无曲率修正。 | permissive |
| `haimingz/vivsim` | fits | `mask-indexed-bounceback` | 用布尔 mask 标记固体格点，一行 f.at[:,mask].set(f[:,mask][OPP_DIRS]) 完成局部 bounce-back，几何任意（其算例把文字光栅化成 mask）。JAX 下无动态索引开销，天然适配多孔介质/复杂几何。 | permissive |
| `je-santos/MPLBM-UT` | fits | `quasi-static-pc-ladder-with-checkpoint` | 以 num_pc_steps 级递增出口密度实现准静态排驱，级差由 Δρ=6σcosθ/r_min 定（等价 Laplace 2σ\|cosθ\|/r）；每级跑到平均速度相对变化率<conv 判为定常；级间以 run_num.dat + saveBinaryBlock(两个 lattice) 断点续算。 | copyleft(GPL) |
| `je-santos/MPLBM-UT` | fits | `edt-two-segment-geometry-tag-compression` | 对二值图像做欧氏距离变换，距孔隙 1 层的固体设为 bounce-back 壁（携带润湿 tag），更深的固体设为 NoDynamics（零计算），其余为流体；几何按 tag 值（2608-2611）压缩为 int16 落盘，模拟端按 tag 分派 dynamics。 | copyleft(GPL) |
| `omalaspinas/palabos` | fits | `partial-bounce-back-porous` | 部分 bounce-back 动力学对象以「部分反射」表示多孔骨架/半渗透壁，配合稀疏块结构与孔隙几何示例；动量交换 bounce-back 另用于 IBM 受力统计。 | copyleft-strong(AGPL) |
| `ProjectPhysX/FluidX3D` | adapt | `gpu-raycast-triangle-voxelization` | 在 GPU 上对每个格点沿 3 个方向做三角网格射线求交（按 mesh bounding box 裁剪，无需 BVH），命中则置 TYPE_S 并按给定线速度/角速度写该格 u，实现 STL 一次性上格子且自动带移动壁速度；voxelize/unvoxelize 可在运行中反复调用做运动物体。 | restricted |
| `ebinan92/Fingering_dynamics` | adapt | `raster-obstacle-corner-class-bounce-back` | 任意栅格障碍 -> 布尔掩码 + 角点表（setblock）, 再用 8 类（矩形）/12 类（圆、椭圆凹凸角）半程反弹表处理边与角；几何管线可与其几何生成脚本对齐（交错 4/5 块阵列，本报告复算 40 块、孔隙率 88.9%）。 | no-license |
| `lanl/MF-LBM` | adapt | `indirect-addressing-wall-tables` | 用 int1 三维壁面数组表示几何；预处理阶段把固体格与"流-固界面格"各自展开成**一维列表**：固体格存 18 邻居方向号 + 各方向的流体邻居数 + 局部权重（la_weight），界面格存壁面单位法向 (nwx,nwy,nwz) 与局部接触角 θ；主循环只遍历列表即可完成 φ 在壁面的外推、色梯度法向的壁面修正与润湿旋转，代价 O(边界节点数) 而非 O(N) 全场扫描。 | permissive |
| `lssfau/lbmpy` | adapt | `sparse-list-based-aos-lbm` | 由 flag 场映射出「流体单元列表 + 每方向邻居索引」index array，pdf 存成 pdf_arr[n_fluid_cells, Q]（AoS），碰撞流式在 1D 索引空间一次完成（streaming 变成索引查表），边界另用 SparseLbBoundaryMapper 出 UBB 赋值 kernel。 | copyleft(GPL) |
| `lssfau/walberla` | adapt | `list-based-sparse-lbm-plus-permeability` | 稀疏 LBM 用活跃 cell 列表 + src/dst/idx 索引数组 + SoA 布局，由 create_lb_update_rule_sparse 生成 gather 式 kernel；多孔介质用 Darcy 反算渗透率评估器（给粘性与流轴）； | copyleft(GPL) |
| `sailfish-team/sailfish` | adapt | `indirect-node-addressing` | 间接寻址：维护 nodes[] 稠密→稀疏映射，内核以 nodes[dense_gi+offset] 访问活跃流体节点，未激活节点不占存储也不参与计算；配套 subdomain 端只需给出 encoded_map 的稀疏节点表。 | copyleft-weak(LGPL) |
| `sorush-khajepor/listLBM` | adapt | `voxel-imgproc-porosity-report` | 由体素图(.dat)或解析盒子标记 cell kind（wall/blackghost/blackwall），自动为周期方向预置 ghost 节点、自动识别角/边边界情形，最后输出 total/computational/black/Porosity 与各子链表单元数报表。 | copyleft(GPL) |
| `sorush-khajepor/listLBM` | adapt | `sparse-sublist-neighbor-list` | 把节点按「同一碰撞类型」分入子链表，每子链表挂一个碰撞对象（多态覆写 cellcollide/getforce 等）；邻居关系用 neilist(1:nq,icell) 显式存 cell 索引，纯固体节点（不与流体相邻）完全不进主链表，故 kernel 内无 if-solid 分支。 | copyleft(GPL) |
| `Maarten-vd-Sande/lbm` | reference | `bitflag-neighbour-mask` | 用一个整数位域同时打包格型（FLUID/INTERFACE/GAS/OBSTACLE/INLET/OUTLET）、邻域存在性（NO_FLUID_NEIGH/NO_EMPTY_NEIGH/NO_IFACE_NEIGH）与本步转换意图（TO_FLUID/TO_GAS），内层循环用位与 O(1) 跳过空单元。 | no-license |
| `PorousMediaSimulation/openLBMPM` | reference | `compressed-fluid-node-neighbour-table` | Compress only fluid (void) nodes into 1-D arrays [numFluids, N_fluid, 9] and precompute a neighbour table (8/24/36 int64 per node, negative encoding -solidIdx-2 for solid neighbours), regenerated only when the solid mask changes; turns porosity directly into array length. | no-license |
| `lanl/MF-LBM` | reference | `iso8-wall-normal-table` | 预处理阶段先把 int1 壁面数组做多级平滑，再用 ISO8（8 层、权重 4/45…1/5040）各向同性差分求固体表面法向，写入界面格的 n_w 表供主循环润湿模型使用；同一张表还用于 φ 在壁面的加权外推（权重 w_equ 累加/归一）。 | permissive |
| `lettucecfd/lettuce` | reference | `noble-torczynski-psb` | 固体节点按饱和度 ε 在 BGK 碰撞与全反射 BB 之间加权混合，B=ε(τ−0.5)/((1−ε)+(τ−0.5))，可表示部分填充/多孔介质体，无需解析曲面。 | permissive |
| `ludwig-cf/ludwig` | reference | `map-solid-with-navier-slip` | 任意固体几何 = per-site map 状态（fluid/wall/colloid links）+ link bounce-back，可选 Navier 滑移壁；多孔介质即 map 打洞 + 滑移壁。 | permissive |
| `AminZar69/CudaPhaseField` | none | `not-supported-mask-without-geometry` | Not supported. A solid mask exists (is_solid_node) and five kernels guard fluid updates with it, but set_host sets it to zero everywhere and the streaming kernels never consult it, so there is no bounce-back, no wall phi condition and no geometry generation; nothing in the tree can represent a porou | copyleft(GPL) |

**推荐路线**：几何摄入走『体素/图像 → EDT 分层 → tag 压缩』（MPLBM-UT fits）；存储走 list 型子链表 + 显式邻接表（listLBM 算法级重写）；亚网格层预留 PSB 钩子（lettuce 公式）。

**推荐依据（逐条可溯源）**

- `sorush-khajepor/listLBM` / `sparse-sublist-neighbor-list` → 修订 transfer **adapt**（原表 `adapt`）。子链表分类 + 显式邻接表 + 固体节点剔除，是唯一带『体素图像→孔隙率报表』全链路的实现。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `je-santos/MPLBM-UT` / `edt-two-segment-geometry-tag-compression` → 修订 transfer **adapt**（原表 `fits`）。EDT 距离变换把固体分『一层 BB 壁 + 更深 NoDynamics』，几何按 tag 压成 int16 —— 直接省算力。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `lettucecfd/lettuce` / `noble-torczynski-psb` → 修订 transfer **reference**（原表 `reference`）。部分饱和固体（PSB）把多孔阻力折进碰撞，是亚网格多孔层的正解。许可 permissive → 可搬码（保留版权声明）。

**风险与许可证约束**：list 型实现在 GPU 上会因间接寻址退化，需保留『稠密 mask 版 + 稀疏 list 版』双实现并给切换判据；listLBM GPL → 重写。

### F10 案例组织与运行管理

*判据*：案例=配置对象/JSON schema/单位换算/回调钩子/断点续算（含反例）

*规模*：16 条谱系行 / 16 仓；迁移分布 adapt 5，fits 8，none 1，reference 2

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `2b-t/LB-t` | fits | `case-schema-json-units-converter` | Simulation 把 discretisation/physics/initial_conditions/geometry/target/output/times 七段序列化为 JSON 并从同一文件读回；Converter 以物理长度/速度/密度与对应 LBM 比值构造全部换算因子。 | permissive |
| `cfdemons/LUMA` | fits | `compile-time-case-defs-plus-geometry-dsl` | 每个算例一个定义头 definitions_NNN.h（宏给维度/分辨率/雷诺数/壁面 BC 类型/加密层数/输出频率/体列表），运行时配套 input/ 目录下的 geometry.config（体定义表：FROM_FILE/FILAMENT_ARRAY/CIRCLE_SPHERE/SQUARE_CUBE/PLATE，含 LEV/REG/FLEX_RIGID/BC/DENSITY/YOUNG_MOD 等后缀参数，无关项填 NULL）与 inlet_profile.in / point-cloud .in；runTestSuite.sh 按算例号自动分发对应输入、选 1 或 8 rank，并把 | permissive |
| `haimingz/vivsim` | fits | `one-file-per-case-pure-step` | 案例组织范式——每个算例文件顶部集中参数块（几何/物理/LBM 三节），中部一个纯函数 step(f)（碰撞→力项→迁移→边界，顺序与所用 forcing 严格配套），底部 update_chunk+scan 跑；可视化/后处理只读 f。可作为我们案例目录模板。 | permissive |
| `je-santos/MPLBM-UT` | fits | `yml-driven-xml-template-pipeline` | 每个算例目录一个 input.yml + 一个 runner 脚本；create_palabos_input_file 用 f-string 模板把 yml 展开成求解器 XML，再写 mpirun bash 脚本并 subprocess 提交；后处理脚本从固定文本标记（'Pressure difference ='）解析输出。 | copyleft(GPL) |
| `lssfau/lbmpy` | fits | `lbmconfig-single-point-config` | 用一个 dataclass LBMConfig 集中全部方法/数值/输出配置（stencil, method, relaxation_rates, compressible, zero_centered, equilibrium_order, c_s_sq, force_model, force, kernel_type, streaming_pattern, output, velocity_input, density_input ...），场景工厂只负责几何与初边值，step 对象负责时间推进组合。 | copyleft(GPL) |
| `lssfau/walberla` | fits | `apps-4layer-plus-prm-plus-scenario-class` | 算例按 examples/showcases/benchmarks/tutorials 四层组织，每算例一个 .prm 参数文件 + 一个 Python Scenario 类（网格、物理参数、输出频率、每步回调一次配齐），C++ 侧只读 prm 装配；多相场景把模型族（重建/回填/多余质量/曲率）全部暴露到 prm。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | fits | `json-input-case-directory` | 输入参数用 JSON（util_cJSON）读入，案例目录自包含输入+期望输出，构建配置外置 config/*.mk；目录即证据单元。 | permissive |
| `omalaspinas/palabos` | fits | `examples-taxonomy` | examples 按用途分五条主线（showCases 演示 / codesByTopic 单主题最小复现 / benchmarks 性能 / tutorial 教学 / gpuExamples 加速版），每例独立 CMakeLists 并在顶层 CMake 全量 add_subdirectory 注册。 | copyleft-strong(AGPL) |
| `CFD-GO/TCLB` | adapt | `model-dir-as-build-and-ci-unit` | 每个模型一个目录 models/<物理类>/<格子>_<方法>/，三件套 Dynamics.R（声明密度/设置/节点类型/输出量）+ Dynamics.c.Rt（模板内核）+ conf.mk（编译选项），算例独立放 example/<类>/<name>.xml；make <model> 即编该模型，模型名同时是 CI 矩阵键。 | copyleft(GPL) |
| `joconnor22/LIFE` | adapt | `compiletime-params-plus-runtime-geometry-config` | 一个算例=一个目录，两份输入：params.h（网格/物性/边界类型/ω/步数，编译期常量，改则须重编译）+ input/geometry.config（运行期文本：CIRCLE/FILAMENT 关键字行，含编号、起始坐标、阵列间距、半径或 长度/厚度/角度/FLEX\|RIGID/单元数/BC/密度/E）；geometryReadIn 先扫一遍算 body/node 数预留内存，再逐行构造；重启时可就地增删体。 | copyleft(GPL) |
| `lanl/MF-LBM` | adapt | `job-status-checkpoint-machine` | 用 job_status.txt 承载算例状态机（new/continue/done/failed/reached_max_step），checkpoint 按**墙钟小时**定时（两级备份：主 + 第二备份），保存完整 PDF+φ（必要时含对流出流历史场），重启时读状态文件走 continue 路径避免重算；同时用 d_vol_* 支持按"注入孔隙体积"触发输出/监控，使长算例可无人值守分段。 | permissive |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | adapt | `per-step-setup-hook-and-mask-blend` | Solve(sess,Tf,initialize_step,setup_step,save_step,save_interval) 把每步的前处理、输出节奏以回调注入；边界条件用单个张量表达式 f=f*(1-boundary)+f_bc*boundary 合成任意几何。 | no-license |
| `sailfish-team/sailfish` | adapt | `example-per-physics-case-with-regtest-hook` | 每个物理案例一个可执行脚本（binary_fluid/ 下 SC 与 free-energy 各成系列），脚本只声明几何/初值/边界/参数，物理模型由 sim 类装配；对应的 regtest 用同名参数覆盖（minimal PBT），使"例—验证"一一对应。 | copyleft-weak(LGPL) |
| `ProjectPhysX/FluidX3D` | reference | `single-file-setup-switch-simultaneous-si-units` | 所有算例写在 setup.cpp 一个文件里、用注释开关切换；每个算例统一四段「①解析反推体积力/粘度 ②units.set_m_kg_s 建立 SI↔lattice 三标度 ③几何按坐标谓词写 flag ④run + 出图/写 VTK」，无量纲数（Re/We/Ca/Fr）由 units.si_* 直接打印。 | restricted |
| `sorush-khajepor/listLBM` | reference | `example-per-main-antipattern` | 每个算例是一个独立 main.f90，换算例必须手改顶层 CMakeLists.txt 的 example_path/project_name 再重新 cmake 构建；无任何自动化测试与 CI，验证结果只以 PNG/PDF 存在。 | copyleft(GPL) |
| `AminZar69/CudaPhaseField` | none | `hardcoded-parameter-modules` | STUB-grade case organisation: every case parameter is a Fortran parameter duplicated between device_var (device) and host_var (host) and the README instructs the user to keep the two copies identical manually; there is no input file, no case directory, no restart. Output is a fixed-name ASCII .vtr e | copyleft(GPL) |

**推荐路线**：案例格式取 LB-t 的 JSON schema（含单位换算与三基准量），配置对象取 lbmpy 的单一事实源写法，运行管理取 MF-LBM 的 checkpoint 状态机；把 listLBM 的单文件算例明确列为反例（不迁移）。

**推荐依据（逐条可溯源）**

- `2b-t/LB-t` / `case-schema-json-units-converter` → 修订 transfer **fits**（原表 `fits`）。MIT：七段式 JSON schema + LBM↔SI 三基准量换算，是唯一把单位换算内建进案例格式的。许可 permissive → 可搬码（保留版权声明）。
- `lssfau/lbmpy` / `lbmconfig-single-point-config` → 修订 transfer **adapt**（原表 `fits`）。单点配置对象 + 场景工厂，配置到算例的映射唯一且可校验。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `lanl/MF-LBM` / `job-status-checkpoint-machine` → 修订 transfer **adapt**（原表 `adapt`）。作业状态机 + 断点续算协议，长算例（排驱阶梯）必备。许可 permissive → 可搬码（保留版权声明）。

**风险与许可证约束**：单位换算是最大静默错误源（SI/LU 混用会给出物理上无意义却自洽的解）——schema 必须强制 re/ul/tau 三基准量并做量纲断言。

### F11 测试与验证面

*判据*：解析解回归、逐点参考场、符号 Chapman-Enskog/von Neumann、误差门、基准数据集

*规模*：24 条谱系行 / 21 仓；迁移分布 adapt 6，fits 14，none 1，reference 3

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `ProjectPhysX/FluidX3D` | fits | `analytic-anchored-in-situ-l2-convergence` | 每个算例自带「运行中算误差、误差不再下降即判定收敛」的 L2 判据：Poiseuille 圆柱管每 1000 步算 ‖u_num−u_max(1−r²/R²)‖₂/‖u_ref‖₂ 并要求下降；体积力由解析式反推 f=4u_maxρν/R² 使入口条件与解析解一致；另有 Stokes 阻力、Taylor-Green、lid-driven cavity、Karman、注水质量守恒等 40+ 算例。 | restricted |
| `cfdemons/LUMA` | fits | `baseline-diff-regression-harness` | 壳脚本逐算例编译+运行，把输出与 base/caseNNN 快照逐文件 diff——但实际只对单个 ASCII 转储 io_lite.Lev0.Reg0.Rnk0.100.dat 做 diff -q 判通过，失败时用 diff -y 生成并排差异日志；同时用 hdf5/restart/LiftDrag CSV 作为人工核查工件；同一物理用例分别以 1 rank 与 8 rank 运行以覆盖 MPI 路径。 | permissive |
| `hietwll/LBM_Taichi` | fits | `ghia-1982-lid-cavity-reference` | 纯文本 18×12 矩阵：y/u(Re=[100..10000])/x/v(Re=[100..10000])；顶盖腔中心线速度基准 | no-license |
| `joconnor22/LIFE` | fits | `bitwise-refdata-regression-plus-benchmark-cases` | 基线生成脚本把 nSteps/tinfo/tVTK/tRestart 统一改写为 500 后跑出 Results/ 并连同 params.h+geometry.config 存为 RefData；回归脚本逐案例 make clean && make、跑 2 次（TurekHron 二次测 restart）、再用 diff -r Results RefData 做逐字节比对，全绿才算过；配合 #ifdef ORDERED 让 OpenMP 归约可复现，使位精确比对可行。物理验证面=7 个算例（含 Turek-Hron FSI2 公开基准）+ TotalForces.out 的 Cd/Cl（0.5 | copyleft(GPL) |
| `joconnor22/LIFE` | fits | `regression-test-framework` | Shell-script regression testing: run all example cases, diff Results/ against stored RefData/. TurekHron case also tests restart capability. | copyleft(GPL) |
| `lbm-principles-practice/code` | fits | `analytic-solution-relative-L2-harness` | Every benchmark compares the simulated field with a closed-form solution and reports a relative L2 norm computed only over fluid nodes (wall/virtual layers excluded); steady problems iterate until \|mean(u)/mean(u_old)-1\| < 1e-12; Taylor-Green runs report the sum-square error, the L2 norms of rho and | permissive |
| `lettucecfd/lettuce` | fits | `analytic-error-reporter-1pct-gate` | 算例对象自带 analytic_solution(t) 返回 (p,u)；ErrorReporter 每 interval 步算 ‖u−u_ref‖/N^(d/2) 与压力同量并追加列表；pytest 用 rel=1% 峰值 + abs=1%·u_max 双门断言。 | permissive |
| `lssfau/lbmpy` | fits | `pointwise-reference-regression` | 每个物理能力配「与外部参考实现逐点比对」的回归测试（SC 单/双组分各与 C++ 参考实现沿中心线 64 点 rho 剖面 np.allclose），另配解析解库（Poiseuille/ShearWave/Taylor-Green）与自动符号 Chapman-Enskog 推导（按 epsilon 阶展开验证粘性与守恒律恢复）。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | fits | `fp-domain-tolerance-diff` | 浮点按域容差比对（tests/awk-fp-diff.sh 由 test-diff.sh 驱动），单元测试按 feature 一个 test_*.c；容差为回归容差而非物理基准。 | permissive |
| `omalaspinas/palabos` | fits | `plb-regression-flag-asan` | CMake enable_testing() + 编译期宏 PLB_REGRESSION 把同一份代码切成「短回归」构建（并默认开 -fsanitize=address、-fno-omit-frame-pointer），CI 跑短回归、本地跑长算例，不维护两套代码。 | copyleft-strong(AGPL) |
| `pylbm/pylbm` | fits | `chapman-enskog-auto` | 自动推导 LBM 格式的等价方程（修正方程），计算一阶/二阶修正项，用于精度分析 | permissive |
| `pylbm/pylbm` | fits | `von-neumann-auto` | 自动计算 LBM 格式的放大矩阵，对给定波矢计算特征值，判断 L2 稳定性 | permissive |
| `pylbm/pylbm` | fits | `pytest-baseline-reference` | tests/ 目录含 pytest 套件，baseline/ 和 reference/ 存放验证数据，支持参数化测试和收敛性测试 | permissive |
| `sailfish-team/sailfish` | fits | `analytic-profile-error-curve-plus-golden-fields` | 验证面三件套：(a) Poiseuille 解析剖面对比，对每档黏度算 max(v_sim)/max(v_th)−1 并画随黏度曲线；(b) Ghia 等 Re=100/400/1000 的 LDC golden 场按分量存 npz，逐例比对；(c) 扫描 G∈[3,5.5] 用密度场极值/序参量定位 spinodal 分解阈值（G≈4.0）。 | copyleft-weak(LGPL) |
| `2b-t/LB-t` | adapt | `templated-gtest-trait-matrix` | 为 population/indexing/lattice 各写"参数化+模板"测试基类，同一套断言对多个格子与访问模式实例化，覆盖 >1000 用例。 | permissive |
| `PorousMediaSimulation/openLBMPM` | adapt | `geometric-wetting-contact-angle` | Contact angle is imposed geometrically: extrapolate solid colour value from neighbouring fluid nodes, then project the colour gradient at near-wall fluid nodes onto the nearer of two unit vectors built from cos(theta) and the wall normal; also ships a built-in contact-angle measurement routine usabl | no-license |
| `ebinan92/Fingering_dynamics` | adapt | `spectral-saffman-taylor-integrating-factor` | 谱方法指进：涡量-流函数 phi_hat=-i*R*k_y*c_hat/(k^2+i*R(k_x*c_x+k_y*c_y)), 扩散用积分因子 exp(-k^2*dt) 精确、非线性项用 AB2（启动 RK4）。可作指进线性稳定性/分辨率基准，LBM 只负责非线性阶段与润湿壁面。 | no-license |
| `espressomd/espresso` | adapt | `lb-validation-suite` | 30+ 测试用例覆盖 Poiseuille/Couette/Stokes/剪切/质量守恒/动量守恒/边界条件/热涨落 | copyleft(GPL) |
| `haimingz/vivsim` | adapt | `continuous-error-print-vs-doi-refs` | 每个算例末尾把误差算成数字打印：圆柱绕流与 3 篇 DOI 文献的 Cd=1.389/Cl=0.718 比百分比误差，Couette/Taylor-Green/ABC 打印 rel-L2，Poiseuille 叠解析剖面。优点是参考值连出处写进源码，缺点是全仓无 assert、无 pytest、无 CI 门。 | permissive |
| `je-santos/MPLBM-UT` | adapt | `young-laplace-analytic-plus-droplet-calibration` | 用 Pc=2σcosθ/r 造解析 Pc(Sw) 与 LBM 结果同图比较（Young-Laplace 验证算例），另以平面基底液滴算例扫 G_ads 三点标定接触角；但仅绘图、无断言，需我们补成参数化 pytest 与门禁。 | copyleft(GPL) |
| `CFD-GO/TCLB` | reference | `instruction-dsl-tests-sh` | 测试用指令式 DSL 描述：run/fail/exists/sha1/csvconcatenate 等行内命令，测试脚本放 tests/external/<model>/*.test（git submodule），逐条打印 OK/FAIL 并计时，配合 tools/csvdiff 做阈值比较。 | copyleft(GPL) |
| `Maarten-vd-Sande/lbm` | reference | `oracle-minimal-d2q9-fs` | 500 行、3 内核文件的极简 2D 自由面骨架（宏观量→平衡态→BGK 碰撞→带相态分支的流动→相态更新），可作为形貌级参考实现与教学材料。 | no-license |
| `lssfau/walberla` | reference | `sweep-equivalence-regression` | 同一算例上对 CellwiseSweep / SplitSweep / SplitPureSweep / 生成 sweep 做逐 cell 数值比对，保证"换流式实现不改结果"；另有 codegen 测试目录与自由面子模块(动力学/表面几何/气泡)独立测试；多相唯一解析解对标是微通道热毛细 analytic 解（但只落 CSV，无误差统计脚本）。 | copyleft(GPL) |
| `AminZar69/CudaPhaseField` | none | `not-supported` | Not supported. Zero test files, zero reference datasets, no analytic-solution harness, no contact-angle or surface-tension measurement routine; the only quantitative artefact is a FP32-vs-FP64 throughput chart across GPUs, which says nothing about physical accuracy. Physical accuracy of this solver  | copyleft(GPL) |

**推荐路线**：验证面按三层建：符号层（等价方程/稳定性，pylbm 公式）、解析剖面层（lettuce 式 1% 门）、逐点参考场层（lbmpy 式中枢线/剖面 np.allclose）。基准数据集一律取论文原表（如 Ghia 1982），不取无许可证仓库的数据文件。

**推荐依据（逐条可溯源）**

- `lssfau/lbmpy` / `pointwise-reference-regression` → 修订 transfer **reference**（原表 `fits`）。逐点参考回归（沿中心线 64 点 np.allclose 对比外部参考实现）——比单一积分量误差强得多。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `pylbm/pylbm` / `chapman-enskog-auto` → 修订 transfer **fits**（原表 `fits`）。BSD：符号推导等价方程与 von Neumann 放大矩阵，是『格式对不对』的前置检查。许可 permissive → 可搬码（保留版权声明）。
- `lettucecfd/lettuce` / `analytic-error-reporter-1pct-gate` → 修订 transfer **fits**（原表 `fits`）。MIT：算例自带 analytic_solution + ErrorReporter + 1% 门，最易移植。许可 permissive → 可搬码（保留版权声明）。

**风险与许可证约束**：『只比积分量』会把误差抵消掉（LIFE 的 diff 只比单个 ASCII 转储即为例证）；参考数据必须有出处 DOI。

### F12 CI 分级门禁

*判据*：lint/unit/regression/full-matrix 分层、per-lattice 矩阵、缓存

*规模*：7 条谱系行 / 7 仓；迁移分布 fits 1，none 1，reference 4，reject 1

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `omalaspinas/palabos` | fits | `format-gate-ccache-matrix-ci` | GitLab CI 分阶段 prepare_docker -> check_format -> regression_tests -> parallel_regression_tests -> build_cmake，用 12 套 ccache key（gcc/clang × ser/par × rel/deb）矩阵缓存编译产物；AppVeyor 另覆盖 Windows/macOS。 | copyleft-strong(AGPL) |
| `CFD-GO/TCLB` | reference | `model-matrix-ci-plus-coverage` | CI 以模型为矩阵项：CPU 流水线对 16 个模型做 configure->compile->tests.sh->gcov 覆盖；GPU/HIP 流水线只做编译守卫；另一条流水线跑 tests/solid 独立单测。 | copyleft(GPL) |
| `lssfau/lbmpy` | reference | `gitlab-ci-staged-gates` | GitLab CI 分 8 个 stage（Code Quality / Unit Tests / Reporting / Prerelease-Tests / integration / nightly / docs / deploy），lint 用 nox -s lint，单测带 coverage 产物，nightly 由变量开关控制，docs 单独构建。 | copyleft(GPL) |
| `lssfau/walberla` | reference | `gitlab-single-file-pipeline` | 单文件 GitLab CI 流水线（.gitlab-ci.yml）驱动构建/测试；本盘只确认存在与位置，未读内容，判据未知。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | reference | `per-lattice-matrix-ci` | regression.yml 按格子分 job 并行构建+跑回归，codecov 出覆盖率，codeql 静态扫描；构建配置用 config/github-gcc.mk 覆盖。 | permissive |
| `AminZar69/CudaPhaseField` | none | `not-supported` | Not supported. .github contains only ISSUE_TEMPLATE (bug_report, feature_request, config) and PULL_REQUEST_TEMPLATE; there is no workflow, no build job, no GPU runner. The 2026 revival added templates but no automation, so nothing checks that the tree even compiles. | copyleft(GPL) |
| `ebinan92/Fingering_dynamics` | reject | `no-tests-no-ci-no-asserts` | 无 tests/、无 CI、无断言、无 requirements.txt；唯一含 Theta 的行从未被使用。验证面反面教材：任何从本仓搬来的物理都必须由我们自带验收门。 | no-license |

**推荐路线**：四层门禁：lint → unit → regression（短回归构建，palabos 的 PLB_REGRESSION 宏做法）→ full-matrix（按格子/按物理能力）。反例入册：Fingering_dynamics 无 tests/ 无 CI 无断言。

**推荐依据（逐条可溯源）**

- `ludwig-cf/ludwig` / `per-lattice-matrix-ci` → 修订 transfer **fits**（原表 `reference`）。按格子分 job 并行构建+回归，矩阵化最彻底。许可 permissive → 可搬码（保留版权声明）。
- `omalaspinas/palabos` / `format-gate-ccache-matrix-ci` → 修订 transfer **reference**（原表 `fits`）。分阶段 prepare→check_format→regression→package，门禁分层清晰。许可 copyleft-strong(AGPL) → 禁止搬码：算法/公式级 + 网络服务条款风险。
- `lssfau/lbmpy` / `gitlab-ci-staged-gates` → 修订 transfer **reference**（原表 `reference`）。8 stage 分级（Code Quality / Unit / Reporting / Prerelease…）。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。

**风险与许可证约束**：GPL 仓的 CI 配置文本可参考结构，但 yaml 内脚本逻辑属搬码范畴，只取『分层结构』不取脚本。

### F13 IBM / 流固耦合与受力

*判据*：浸入边界（Peskin 模板/MDF/ε 守恒校正/Aitken 耦合/梁 FEM/润滑修正/动量交换受力）

*规模*：11 条谱系行 / 5 仓；迁移分布 adapt 5，fits 2，reference 4

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `ProjectPhysX/FluidX3D` | fits | `momentum-exchange-solid-force-and-torque` | 由 DDF 直接算流体对固壁的力：对每个与固体相邻的流体格把 (f_i+f_ī) 的动量通量累加成 F（代码注释指出因 bounce-back 反射需 ×2），再用原子加按 TYPE_S 标记累出物体合力/力矩/形心，可直接对标船体阻力/扭矩验收。 | restricted |
| `joconnor22/LIFE` | fits | `ibm-epsilon-gram-implicit-correction` | 3 点 Roma/Peskin δ 核（支撑 1.5Δx，2D 最多 3×3=9 点）插值 ρ 与动量到标记点，取恢复力 F=2(ρ_i u_body − m_i)（Δt=1），铺展时乘 ε_i·ds_i；ε 由稠密系统 A_ij=Σ_s δ_i(x_s)δ_j(x_s)ds_j、Aε=1 一次解出（LAPACK dgetrf/dgetrs），使「插值→铺展→再插值」在标记点上还原总力；可选把所有体合成一个大系统。 | copyleft(GPL) |
| `cfdemons/LUMA` | adapt | `ibm-direct-forcing-rkpm-epsilon` | Peskin 3 点 δ 核（支撑 1.5Δx，逐标记点 dilation）插值 Euler 速度到 Lagrange 标记点；恢复力 F=2(ρ·u_interp − ρ_m·u_marker)（Δt=1）；再以同一 δ 核把 F·ε·ds^(D−1) 反向铺展到支撑点写入 force_xyz；ε 由解 A·ε=1（A_IJ=Σ δ(x_I−X_J)ΔV，Pinelli 2010 RKPM）求得以保证插值/铺展守恒；每步内置强耦合子迭代（重置 u=u_n、重算 IBM、残差<1e-4 或 10 次上限），FEM 侧 Newmark-β(α=0.25, δ=0.5)+0.5 松弛。 | permissive |
| `haimingz/vivsim` | adapt | `ib3d-diffuse-interface-mdf` | 3D 扩散界面 IBM 曲面力法——三角面片几何求面积/体积/ds，标记点用张量积 4×4×4 Peskin-4pt 模板插值/散布，多重直接力（MDF）迭代得到壁面力与反作用力，只在包围盒内 dynamic_slice/update。给出了曲面力法的一种实现方式，但仓内球绕流只出图、无 Cd 定量对比数据（该仓无基准）。 | permissive |
| `haimingz/vivsim` | adapt | `peskin-tensor-product-stencil-mdf` | 把 IBM stencil 一次性预计算成 (weights, flat_indices) 常数，运行期只做一次 einsum 插值与一次散播；插值/散布严格互为伴随（本次验证残差 2e-16），MDF 用 lax.fori_loop 迭代且可整体 jit。可直接搬到任何张量后端。 | permissive |
| `joconnor22/LIFE` | adapt | `ibm-immersed-objects` | IBM bodies (circle/filament) represented by Lagrange markers; force spreading via Dirac delta creates effective porous resistance. Rigid+flexible support. | copyleft(GPL) |
| `ludwig-cf/ludwig` | adapt | `analytic-lubrication-correction` | BBL 之外叠解析近场润滑力（球-球径向/切向、粒子-壁面亏空），截断距离 rch 控制启用范围，力/力矩并入刚体总量。低分辨率下把阻力算对的关键。 | permissive |
| `joconnor22/LIFE` | reference | `corotational-2node-beam-newmark-newton` | 2 节点 3 自由度（u,v,θ）平面梁：局部内力只用轴向伸长 u=(L²−L0²)/(L+L0) 与端弯矩 M1=2EI/L0(2θ1+θ2)、M2=2EI/L0(θ1+2θ2)，几何非线性由 K_NL 的 V0/L0、F0/L0 项承担；M 用一致质量（ρAL/420 的 140/156/22L0/54/13L0…），K_L 为标准梁刚度；Newmark-β(α=0.25,δ=0.5) 做时间积分，牛顿-拉夫逊线性化解 LAPACK。IBM 标记点按 ζ 映射到单元并用形函数插值位移/速度。 | copyleft(GPL) |
| `joconnor22/LIFE` | reference | `displacement-aitken-underrelaxation-fixed-point` | 每时间步内 do{预测(2.5U_n−2U_nm1+0.5U_nm2)→插值→FEM 全量重解(Newton-Raphson, TOL=1e-10, MAXIT=20)→残差}while(res>1e-8 且 it<20)；子迭代 k≥2 时用 Aitken：relax=−relax·(r_{k−1}·(r_k−r_{k−1}))/\|r_k−r_{k−1}\|²，位移 U=U_km1+relax·(U−U_km1)，残差范数按 ref_L·√(DOF) 归一。收敛后一次性铺展 IBM 力。 | copyleft(GPL) |
| `joconnor22/LIFE` | reference | `static-immersed-marker-obstacles` | 「稀疏/多孔/复杂几何」在本仓用静态刚性 IBM 标记环表达：geometry.config 的 CIRCLE 行按周长生成等距标记点（num=floor(2πr/Δx)），可一行写多体、按间距阵列（space_x/space_y），标记点速度恒 0 即为无滑移障碍；配合 3 点 δ 核 + ε 校正把障碍力铺到格子，等价于任意曲线/栅板/植被阵列的多孔障碍，但**不是** mask bounce-back、也无稀疏索引压缩。 | copyleft(GPL) |
| `joconnor22/LIFE` | reference | `implicit-IBM-Aitken-relaxation` | Direct-forcing IBM (Peskin 3pt Dirac, support radius 1.5dx). Strong FSI coupling: block Gauss-Seidel subiteration with Aitken dynamic under-relaxation. FEM: corotational beam + Newmark (alpha=0.25, delta=0.5) + Newton-Raphson. | copyleft(GPL) |

**推荐路线**：实现层先用 vivsim 的预计算 stencil（MIT 可直接搬），物理层照 LUMA 的三件套（力→ε 校正→子迭代）补上『力守恒』这一常被省略的环节；强耦合收敛判据取 LIFE 的 Aitken 形式。

**推荐依据（逐条可溯源）**

- `cfdemons/LUMA` / `ibm-direct-forcing-rkpm-epsilon` → 修订 transfer **adapt**（原表 `adapt`）。Apache-2.0：Peskin 3 点核 + RKPM ε 守恒校正 + 强耦合子迭代，谱系最完整且可搬。许可 permissive → 可搬码（保留版权声明）。
- `haimingz/vivsim` / `peskin-tensor-product-stencil-mdf` → 修订 transfer **fits**（原表 `adapt`）。MIT：把 IBM stencil 预计算成 (weights, flat_indices) 常数，运行期一次 einsum —— 落地最快。许可 permissive → 可搬码（保留版权声明）。
- `joconnor22/LIFE` / `displacement-aitken-underrelaxation-fixed-point` → 修订 transfer **reference**（原表 `reference`）。Aitken 亚松弛定点的收敛写法（2.5U_n−2U_{n−1}+0.5U_{n−2} 预测）。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。

**风险与许可证约束**：直接力法（MDF）在 ε 守恒校正缺位时会丢动量 → 阻力系统性偏高；GAP-LBM-SPHERE 验收必须同时给 Cd 收敛与力守恒残差。

### F14 碰撞算子族

*判据*：TRT magic、MRT、central-moment、KBC、正则化、AA-pattern 奇偶融合

*规模*：7 条谱系行 / 6 仓；迁移分布 adapt 1，fits 3，reference 3

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `2b-t/LB-t` | fits | `trt-two-relaxation-magic-lambda` | 以 nu=U*L/Re 定 tau_p=nu/CS^2+1/2、omega_p=1/tau_p，奇偶模态松弛率 omega_m=(tau_p-1/2)/(lambda+(tau_p-1/2)/2)，默认 lambda=0.25；每格只算 方向索引 d 取 1..HSPEED-1 的 fp/fm，静止分量单独 omega_p。 | permissive |
| `lbm-principles-practice/code` | fits | `trt-lambda-magic-table-driven` | Split populations into symmetric and antisymmetric parts f_pm=0.5*(f_i +- f_complement) and the corresponding equilibrium parts, relax each with its own rate omega_plus/omega_minus, and derive one rate from the other through the magic parameter Lambda: omega_plus=1/(0.5+Lambda*omega_minus/(1-0.5*ome | permissive |
| `lettucecfd/lettuce` | fits | `trt-magic-lambda-3-16` | TRT 用对称/反对称两支分布差分别以 tau_plus/tau_minus 松弛；魔法参数 Λ=(τ₊−½)(τ₋−½)=3/16 提高稳定性（probe 中 τ=0.5088 → τ₋=21.76）。 | permissive |
| `joconnor22/LIFE` | adapt | `central-moment-partial-relaxation` | 以局部速度 u_n 为原点把 9 个分布变换到中心矩 k0..k8：k0=ρ、k1/k2=F/2、k3=2ρc_s²、k4=(1−ω)·Σf(cx²−cy²)、k5=(1−ω)·Σf cx cy、k6/k7=½F c_s²、k8=ρc_s⁴，再逆变换回 fStar（显式展开 9 个方向）；平衡态用四阶 Hermite 乘积形式 ¼ρw(9cx²ux²+6cx ux−3ux²+2)(…)。等价两松弛率（剪切矩松弛、其余瞬时）。 | copyleft(GPL) |
| `cfdemons/LUMA` | reference | `kbc-n4-entropic-line` | 构造 13 个（3D）或 3 个（2D）矩及其平衡值，把 fneq 分解为熵部 ds 与镜像部 dh；用熵线闭合求 γ = 2/ω − (2−2/ω)(ds·dh)/(dh·dh)；碰撞写作 f* = f − (ω/2)(2ds + γdh) + F_i。D3Q27 需 27 全套速度表，2D 用 D2Q9 的 KBC-D。 | permissive |
| `espressomd/espresso` | reference | `mrt-thermalized` | MRT 松弛矩阵从粘度自动推导，支持热涨落 (kT 参数)，奇数模式松弛消除数值不稳定 | copyleft(GPL) |
| `joconnor22/LIFE` | reference | `central-moments-collision` | D2Q9 central moments (De Rosis 2019): f→k via shifted velocities, relax k4/k5 by omega, inverse transform to f*. Force via k1/k2 half-step and k6/k7 moment injection. | copyleft(GPL) |

**推荐路线**：碰撞算子按『SRT→TRT(magic)→MRT/central-moment』三级推进，先固定 magic 取值（λ=0.25 与 3/16 各跑一遍，记录壁面滑移差），再上 central-moment（LIFE 公式）与 KBC。

**推荐依据（逐条可溯源）**

- `2b-t/LB-t` / `trt-two-relaxation-magic-lambda` → 修订 transfer **fits**（原表 `fits`）。MIT：TRT 双松弛 + magic λ=0.25 的显式写法（壁面位置无关的经典取值）。许可 permissive → 可搬码（保留版权声明）。
- `lettucecfd/lettuce` / `trt-magic-lambda-3-16` → 修订 transfer **fits**（原表 `fits`）。MIT：λ=3/16 的另一种 magic 取值，便于对照壁面滑移误差。许可 permissive → 可搬码（保留版权声明）。
- `lbm-principles-practice/code` / `trt-lambda-magic-table-driven` → 修订 transfer **fits**（原表 `fits`）。MIT：magic 参数表格化，可扫参。许可 permissive → 可搬码（保留版权声明）。

**风险与许可证约束**：两家 magic λ（0.25 vs 3/16）给出不同壁面位置假设，混用会把 bounce-back 有效壁面偏移算错——必须与 F15 的 BB 约定成对配置。

### F15 边界算子族

*判据*：半程/插值 bounce-back、Zou-He、Guo 插值、正则化 BC、Lees-Edwards、湿节点固定密度壁

*规模*：11 条谱系行 / 9 仓；迁移分布 adapt 3，fits 3，reference 5

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `2b-t/LB-t` | fits | `guo-interpolation-inlet-outlet` | 在边界邻点算局部矩与非平衡态 fneq=f-feq，按边界类型/方向用目标宏观量替换（速度边界强制 u，压力边界强制 rho 与切向速度），回写 feq+fneq。 | permissive |
| `lbm-principles-practice/code` | fits | `anti-bounce-back-inamuro-wetnode` | Three interchangeable ways to impose a Dirichlet value on a wall/inlet for a scalar (concentration/temperature) field: anti-bounce-back f2[i]=-f2[opp]+2*w_i*C_wall applied after streaming; Inamuro reconstruction that solves for the unknown-population density (C_in - sum f_known)/sum w_unknown and re | permissive |
| `pylbm/pylbm` | fits | `linear-interpolated-bb` | 统一框架 LinearBounceBack 实现 α 参数化插值 bounce-back，含 CLI(4/(1+2δ))/YLI(2/(1+δ))/BFL 变体，支持后碰撞修正 | permissive |
| `cfdemons/LUMA` | adapt | `interpolated-bounceback-bouzidi-q` | 对与壁相交的链接按墙距比 q∈[0,1] 做二阶插值反弹：墙更近当前点用 f*=(1−2q)(f̄₊−f̄)+f̄（f̄ 为预迁移反向值，f̄₊ 为再远一格的预迁移值）；墙更近源点用 f*=(1−2q)(f−f̄)/(2−2q)+f̄（迁移后值）。q 由点云几何逐标记点逐方向预计算并取最小（最近墙优先），同一链接两条 BFL 同时命中时取最近；壁面力用该链接的动量交换累加到标记点。 | permissive |
| `joconnor22/LIFE` | adapt | `regularised-bc-nonequilibrium-stress-reconstruction` | 把边界格 f 拆成法向入射 f⁺ 与切向 f⁰：速度型用 ρ=(2f⁺+f⁰)/(1−n·u) 闭合，压力型用 u_n=n(1−(2f⁺+f⁰)/ρ) 反解；未知方向先按 feq+(f_opp−feq_opp) 建非平衡，求和得 Sxx/Syy/Sxy，再用 w/(2c_s⁴)[(cx²−c_s²)Sxx+(cy²−c_s²)Syy+2cxcySxy] 重建并叠加平衡态；角点只外推密度/切向速度。配套二阶单侧差分的对流出口。 | copyleft(GPL) |
| `lettucecfd/lettuce` | adapt | `bouzidi-ibb1-wall-distance` | 由墙体距离参数 d 预计算 (f_index_lt, d_lt)/(f_index_gt, d_gt)，按 d≤0.5 与 d>0.5 两分支做线性插值反弹；节点力矩交换可直接输出壁面力。 | permissive |
| `cfdemons/LUMA` | reference | `regularised-velocity-pressure-bc-latt2008` | 按法向把各速度方向分为流入侧 f⁺（含对角）与 f⁰，由已知 f 求非平衡应力张量分量 S_xx,S_yy,S_xy(,S_zz,S_xz,S_yz)，再用 f = feq + (w_i/2c_s⁴)[(c_x²−c_s²)S_xx+…+2c_xc_yS_xy] 重构未知方向；压力边界以 ρ=L_RHOIN+Δp 给定目标密度；角点/棱边方向数不足时沿法外推密度（一阶），压力边界在角点显式拒绝。 | permissive |
| `espressomd/espresso` | reference | `lees-edwards-shear` | 周期性剪切流边界，修改碰撞步施加剪切速度，PDF/速度/力在边界处插值 | copyleft(GPL) |
| `hietwll/LBM_Taichi` | reference | `equilibrium-difference-extrapolation` | 边界 f = f_eq(boundary) - f_eq(inner) + f_old(inner)；一阶外推，非标准 Zou-He | no-license |
| `joconnor22/LIFE` | reference | `regularised-BC-Latt2008` | Zou-He density/velocity from f+/f0, then reconstruct f = feq + (wi/2cs^4)[(cxi^2-cs^2)Sxx + (cyi^2-cs^2)Syy + 2*cx*cy*Sxy] from off-equilibrium stress tensor. Corner: extrapolate rho + buried-link=feq. | copyleft(GPL) |
| `sorush-khajepor/listLBM` | reference | `swap-streaming-wetnode-zouhe` | streaming 就地对 halfqs 一半方向与邻居交换 pdf（单趟无临时数组）；固体子链表跳过第二趟交换+空白碰撞=半步 bounce-back，且固体节点用固定 rho_wall/零速的「湿节点」参与伪势作用；进/出口用 Zou-He 型带切向与镜像对角修正的速度/密度边界，角点由自动生成的镜像对角表处理，出流用一阶外推，角上可复合。 | copyleft(GPL) |

**推荐路线**：边界库按『壁面（半程 BB / 插值 BB / 正则化）→ 出入口（Zou-He 反算 / 反反弹 Dirichlet / EE）→ 特殊（Lees-Edwards 剪切、湿节点固定密度、平衡态外插 EE）』三组收编；每组附壁面偏移与角点处理约定。EE 的实现只见于无许可证仓（hietwll/LBM_Taichi，all-rights-reserved），只可按公式自写。

**推荐依据（逐条可溯源）**

- `lbm-principles-practice/code` / `anti-bounce-back-inamuro-wetnode` → 修订 transfer **fits**（原表 `fits`）。MIT：反反弹 + Inamuro 湿节点，标量 Dirichlet（速度/压力）最干净的实现。许可 permissive → 可搬码（保留版权声明）。
- `cfdemons/LUMA` / `regularised-velocity-pressure-bc-latt2008` → 修订 transfer **adapt**（原表 `reference`）。Apache-2.0：Latt&Chopard 正则化 BC + 角点外推，曲面/运动壁的主流做法。许可 permissive → 可搬码（保留版权声明）。
- `cfdemons/LUMA` / `interpolated-bounceback-bouzidi-q` → 修订 transfer **adapt**（原表 `adapt`）。Apache-2.0：Bouzidi q 法插值反弹（移动/曲面壁），与半程 BB 构成壁面族。许可 permissive → 可搬码（保留版权声明）。

**风险与许可证约束**：角点双重赋值是每个边界库的隐藏 bug 源（多相 Zou-He 出口角点尤甚）；插值 BB 的 q 法与 BFL 法给出的有效壁面不同，不可混用。

### F16 网格加密 / 可微与生成式框架

*判据*：静态块加密 2:1、重叠网格 explode/coalesce、BPTT 可微、涨落噪声、符号化格子定义

*规模*：9 条谱系行 / 6 仓；迁移分布 adapt 5，fits 1，reference 3

| 仓 | 迁移 | tag | 手法（原文摘要） | 该仓许可证分级 |
|---|---|---|---|---|
| `pylbm/pylbm` | fits | `sympy-driven-codegen` | 用 SymPy 定义 LBM 格式（速度/多项式/平衡态/松弛参数），自动推导矩矩阵 M/M⁻¹，生成 Cython/NumPy/OpenCL kernel | permissive |
| `cfdemons/LUMA` | adapt | `overlapping-grid-refinement-explode-coalesce` | 每层细网格在一个粗时间步内跑 2 次子循环后再推进粗层；细→粗用 coalesce 把子网格单元平均成粗层 eTransitionToCoarser 值，粗→细用 explode 把父层 eTransitionToCoarser 的值填充到子网格 eRefined 邻域；界面通量不做碰撞（跳过 eTransitionToCoarser），界面两侧以 refinement_ratio 缩放体力与 τ（τ 由粗层按 ratio=2 换算）。 | permissive |
| `haimingz/vivsim` | adapt | `pure-function-fsi-fixed-point` | 全库纯函数 + 状态作 scan carry 传递，FSI 用固定点子迭代（FSI_ITER/IB_ITER）耦合，使整个时间循环结构上可被 jax.grad 穿透；但仓内无一处求导用例，属"结构就绪、能力未兑现"。 | permissive |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | adapt | `les-scalar-q-abs-cc-proxy` | 用 Q=sqrt(2*Σ_i (f_i-f_i^eq)^2 * EEk_i)（EEk=Σ_{n,m}\|c_n c_m\|）作为 Π_ab 模的标量代理，再 tau=0.5*(tau0+sqrt(tau0^2+6*Q*Sc/rho)) 做 Smagorinsky 型涡粘。 | no-license |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | adapt | `graph-unroll-bptt-les-parameter` | 提供 graph_unroll 分支让 collide/stream 返回图内张量而非写回 Variable，Unroll() 在 Python 侧串起 N 步并返回状态链；tau 由场量与标量 Sc 在图内算出，故梯度可从 N 步后的场回传到模型常数。 | no-license |
| `loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow` | adapt | `table-driven-lattice-selfcheck` | 每套格子由 WEIGHTS/LVELOC/BOUNCE/STREAM 四张常量表定义，Domain 用 reshape 统一装配；但上游未对 STREAM 与 LVELOC 做一致性自检，导致 D3Q19 的 q=3 槽位抄成 q=15 的槽位、+y 分布沿 (0,1,1) 迁移。 | no-license |
| `haimingz/vivsim` | reference | `buffer-layer-2to1-refinement` | 块结构 2:1 局部加密——细/粗块之间加一层 buffer，迁移后按方向做 fine→coarse 四点平均、coarse→fine 复制，并按 level 重算松弛参数以保持黏度一致。 | permissive |
| `lssfau/walberla` | reference | `block-amr-equal-level-stream-correction` | 块森林 2:1 加密 + 等值面边界流式修正（EqualLevelBorderStreamCorrection）+ 按旋度/涡量选加密区 + 加密层 ω 按 levelDependentRelaxationParameter 重标定 + 时间步 PDF 打包同步。 | copyleft(GPL) |
| `ludwig-cf/ludwig` | reference | `fluctuating-lbm-ghost-modes` | 水动力模态确定性演化、ghost 模态按 1/tau 与 kT 给方差受热噪声；可向 CH 注入随机通量。提供「热涨落驱动的界面毛细波/Brownian」验证面。 | permissive |

**推荐路线**：加密路线取 LUMA 的重叠网格（Apache 可搬）+ walberla 的等值面流式修正公式；可微/涨落路线只做设计预留（LOL 的 BPTT 与 ludwig 的 ghost-mode 涨落），两者许可证/框架均不可搬。

**推荐依据（逐条可溯源）**

- `cfdemons/LUMA` / `overlapping-grid-refinement-explode-coalesce` → 修订 transfer **adapt**（原表 `adapt`）。Apache-2.0：重叠网格 explode/coalesce + 2:1 子循环，块加密的工程化写法。许可 permissive → 可搬码（保留版权声明）。
- `lssfau/walberla` / `block-amr-equal-level-stream-correction` → 修订 transfer **reference**（原表 `reference`）。Schornbaum–Rüde 式等值面流式修正（跨层级非平衡校正），块加密正确性关键。许可 copyleft(GPL) → 禁止搬码：仅可取算法/公式，干净室重写。
- `pylbm/pylbm` / `sympy-driven-codegen` → 修订 transfer **fits**（原表 `fits`）。BSD：符号化格子定义与代码生成，避免手写 streaming 表（LOL 仓 q=3 表错误即反例）。许可 permissive → 可搬码（保留版权声明）。

**风险与许可证约束**：loliverhennigh 仓无 LICENSE（all-rights-reserved），BPTT 代码不得搬；FluidX3D 许可禁 AI 训练。块加密的跨层质量守恒若无等值面修正会产生系统性质量漂移。

## 四、与 feature_ledger 未完成锚点的对齐：下一步开发建议清单

`feature_ledger` 共 49 条，未完成 46 条；下表是本方式集**能直接供血**的锚点（含 LBM 域与消费方）。`可消费行数`=该锚点可立即取用的 `fits/adapt` 级谱系行计数（明细见 `ledger_alignment.json`）。

| 锚点 | 账本状态 | 可吃到的功能点 | 可消费行数 | 下一步第一步 |
|---|---|---|---|---|
| `FEAT-OSS-INVENTORY-LBM` | in_progress | ALL | 123 | 本报告即收口产物：24 仓 / 196 条谱系行 / 16 功能点；余下仓（见缺口 E-1）补盘后并入 F02/F07/F08/F09/F13。 |
| `GAP-LBM-MCMP-POISEUILLE` | in_progress | F01, F02, F06, F11 | 38 | 直接吃 F01 主路线（lbm-principles MIT 双组分 SC + TCLB 解析剖面公式）：先建 D2Q9 两组分通道 + 三判据（界面位置<5%、分相流量<10%、spurious current<0.005·Umax）机检，力项注入按 F02 的 Guo 重心形式。 |
| `FEAT-LBM-VERIFY-BENCH` | in_progress | F02, F11, F06 | 27 | 直接吃 F02 的 Young-Laplace 标定器（fits）+ F11 三层验证面：先钉 σ 与 spurious-current 基线数字表，再建 pytest 固化；消费链映射所需的 oss_solutions 条目可直接引 ledger_alignment.json。 |
| `GAP-LBM-SPHERE` | planned | F13, F09 | 20 | 吃 F13（vivsim 预计算 Peskin stencil 可搬 + LUMA ε 守恒校正）：球绕流 Cd 必须同时报告力守恒残差。 |
| `H2-FRAZIL-RHEOLOGY` | planned | F03, F07 | 16 | 幂律悬浮流变不在本轮盘点覆盖内（缺口 E-6）；可先借 F03 变密度平衡态与 F07 算子显式化把黏度做成场量函数。 |
| `FEAT-TK-OSS-LANDING` | in_progress | ALL | 123 | 落库优先级按 transfer=fits 先行（可搬码级），reference 按『公式级』落并标注许可证约束。 |
| `FEAT-OSS-PLAYBOOK` | in_progress | ALL | 123 | 本单的『SQL 直取→slug 归一→功能点归集→机检→推荐』五步可抽成跨域套路（DG/SPH 复用）。 |

**建议优先序（按「可搬码量 × 锚点验收紧迫度」）**

1. `FEAT-LBM-VERIFY-BENCH`：零代码搬入即可起跑——F02 的 Young-Laplace 标定器（fits, MIT）+ F11 的三层验证面（其中 pylbm/lettuce 均为 BSD/MIT）；先把 σ 与 spurious-current 基线数字表钉死，它同时是 `GAP-LBM-MCMP-POISEUILLE` 判据③的前置。
2. `GAP-LBM-MCMP-POISEUILLE`：F01 主路线可一次性凑齐三判据（界面位置 / 分相流量 / spurious current），解析剖面与误差度量取 TCLB 的公式（GPL → 重写）。
3. `GAP-LBM-SPHERE`：F13 的 vivsim 预计算 Peskin stencil 为 MIT 可直接搬，需补 LUMA 的 ε 守恒校正思路，否则阻力会系统性偏高。
4. `H2-FRAZIL-RHEOLOGY`：本轮盘点**未覆盖**幂律/非牛顿流变（见缺口 E-8），只能间接借 F03 变密度平衡态与 F07 算子显式化；建议先补 3~5 仓（frazil/悬浮/幂律）再谈实现。
5. `FEAT-TK-OSS-LANDING` / `FEAT-OSS-PLAYBOOK`：分别按 `transfer` 分级落库与按本单五步流程抽成跨域套路。

## 五、注意点（坑、局限、适用范围）

1. **许可证是最大隐性成本**：本轮 8 仓 GPL、1 仓 AGPL、5 仓无许可证、1 仓自定义受限。「推荐路线能提取」≠「代码能搬」：报告中 `fits` 一律要求 permissive 许可，其余按公式级重写。
2. **FluidX3D 自定义许可禁止『用于训练 AI 模型』**：其代码与注释不得进入任何知识蒸馏/训练语料，只能由人/智能体按已发表算法独立重写——这条对本生态（AI 生产活动）是硬红线。
3. **各家实现的分歧点常不在方程而在自由选择**：混合速度取法（ρσ 加权 vs τ 加权）、magic 参数 λ（0.25 vs 3/16）、插值 BB 的 q 法 vs BFL 法、多余质量分配模型——这些不显式记录就会重现「两份实现 18~32% 分歧」类事故。
4. **盘点数据本身有噪声**：slug 别名分裂（E-4）、transfer 非规范取值（E-5）、占位标题污染报告（E-3）、目录级指针（E-6），均已在缺口清单列出，未在本报告中静默清洗。
5. **适用范围**：本方式集只覆盖 LBM（单相/多相/自由面/相场/IBM/性能/工程化）。船体绕流的高 Re 工程化（壁模型 LES、VOF 破碎、六自由度运动）不在 24 仓盘点的能力面内，移植前需另立锚点。

## 六、误差判定（机检指标与判据对照）

本报告是**文献/代码盘点综合**，不含物理基准量，故不存在「计算值 vs 实验值」误差；可判定量是**归集完备性、可溯源性、覆盖度**三类，逐项如下（全部程序化重算）：

| 编号 | 指标 | 结果 | 阈值/判据 | 判定 |
|---|---|---|---|---|
| C1 | 谱系行归集完备率 | 196/196 | 100% | PASS |
| C2 | 推荐可溯源率 | 49/49 | 100% | PASS |
| C3 | transfer 词表归一 | 非规范取值 1 个已归一 | 归一后 5 值 | PASS(已归一,原件仍脏) |
| C4 | 文件行级指针覆盖率 | 96.9%（6 行为目录级） | ≥95% | PASS |
| C5 | 仓覆盖度 | 24/29 = 82.8% | 台账锚点要求 ≥24 仓 | PASS |
| C6 | 功能点最小规模 | 最小 3 仓 / 最大 21 仓 | ≥3 仓方可下推荐 | 见缺口 E-1/E-8 |
| C7 | `fits` ∩ permissive（真可搬码行） | 25/196 = 12.8% | 全部 fits 均须 permissive（本报告推荐的 12 条 fits 已 100% 满足） | 本报告推荐 PASS；台账原表 21 条不合规（缺口 E-10） |

**是否 <3%？** —— 本报告无物理基准量，此问 **N/A**（不是「已达标」也不是「未达标」）：A/B 类判定需要有量与参考值，本单输出的是知识资产与路线图。可替代的量化判定是上表 C1/C2 （应为 100%，实际 196/196、49/49）与 C5（≥24 仓，实际 24 仓）；C4 = 96.9%（未达 100%，因 CI/测试条目本身是目录级，已计入缺口 E-6 而非粉饰）。

## 七、缺口清单（盘点缺口，如实列出）

**E-1 仓已盘点但零谱系归集** —— oss_inventory.status=inventoried 而 oss_solutions 无行；本版方式集对这些仓无发言权
- Autodesk/XLB
- JuliusWeinmiller/ThermalMultiphaseOpenLB
- UvaCsl/HemoCell
- geoelements/mcmp-lbm
- hemelb-codes/hemelb
- openLB/openLB

**E-2 盘点报告占位/空壳** —— markdown 长度 <200 的 oss-inventory 报告
- 9561	【盘点】Autodesk/XLB — 开源 LBM 技术深盘	76
- 9844	【盘点】geoelements/mcmp-lbm: 3D多组元多相LBM（MRT）+ Taichi稀疏存储	58

**E-3 报告数据卫生** —— 标题为占位串（tttt…）的 oss-inventory 报告，应清理或改判
- 9921	tttttttttttttttttttt	42823
- 9923	tttttttttttttttttttt	42840
- 9924	tttttttttttttttttttt	42841

**E-4 slug 别名分裂（同一手法多处登记）** —— 8 组 slug 经归一后语义相同（去掉 NEW:/CAP-/GAP- 前缀与括注后同形），会让守护线按 (repo,feature_slug,tag) 的去重失效、统计口径漂移
- CI | GAP-LBM-CI | NEW:CAP-LBM-CI: F; 1; 2
- MCMP 高密度比 | NEW:MCMP 高密度比（两组分 Poiseuille 之外的高密度比分层流）: F; 0; 3
- 稀疏 / 多孔 | 稀疏/多孔: F; 0; 9
- NEW:测试与验证面（逐点参考回归 + 解析解 + 符号 Chapman-Enskog） | 测试与验证面: F; 1; 1
- NEW:案例组织（单点配置对象 + 场景工厂） | 案例组织: F; 1; 0
- NEW:分布式多卡（ghost 交换融合进生成 kernel） | 分布式多卡: F; 0; 8
- NEW:相场 Allen-Cahn（含 CSF 表面张力与热毛细） | 相场 Allen-Cahn: F; 0; 5
- 自由面 | 自由面（free surface）: F; 0; 4

**E-5 transfer 词表未强制** —— oss_solutions.transfer 出现非规范取值，统计与守护线判据会误读
- reject-as-is

**E-6 文件指针非行级** —— files 为目录级（多为 CI/测试目录），无法行级复核
- lssfau/walberla | CI | .gitlab-ci.yml
- espressomd/espresso | GPU 融合 kernel | src/walberla_bridge/src/lattice_boltzmann/generated_kernels/StreamColl
- hietwll/LBM_Taichi | NEW:ghia-1982-cavity-validation-data | data/ghia1982.dat
- pylbm/pylbm | NEW:test-organization | tests/test_*.py, tests/baseline/, tests/reference/
- joconnor22/LIFE | test-verification-surface | testing/run-tests.sh, testing/store-ref-data.sh
- espressomd/espresso | 测试与验证面 | testsuite/python/lb_poiseuille.py, testsuite/python/lb_couette_xy.py, 

**E-7 整仓代码不可搬（许可证）** —— 无许可证或自定义受限许可：只能人工读算法，禁止入库/蒸馏其代码文本
- Maarten-vd-Sande/lbm
- PorousMediaSimulation/openLBMPM
- ProjectPhysX/FluidX3D
- ebinan92/Fingering_dynamics
- hietwll/LBM_Taichi
- loliverhennigh/Lattice-Boltzmann-fluid-flow-in-Tensorflow

**E-8 LBM 域内功能点无覆盖** —— 本轮 16 功能点之外、与未完成锚点相关的空白
- 幂律/非牛顿悬浮流变（H2-FRAZIL-RHEOLOGY）——零盘点
- 热-流/共轭传热 LBM——仅 ThermalMultiphaseOpenLB 报告在但零归集
- 球面/曲面力法的定量 Cd 对标（GAP-LBM-SPHERE）——仅有实现，无定量验证数据
- 自由能三元以上/高密度比组合——ludwig 三元有实现但无密度比

**E-9 账本状态与产出不一致** —— oss_inventory.status 未随盘点产出更新：有谱系行的仓仍标 pending，反之亦然
- PorousMediaSimulation/openLBMPM (status=done, solutions=5)
- Autodesk/XLB (status=inventoried, solutions=0)
- JuliusWeinmiller/ThermalMultiphaseOpenLB (status=inventoried, solutions=0)
- UvaCsl/HemoCell (status=inventoried, solutions=0)
- geoelements/mcmp-lbm (status=inventoried, solutions=0)
- hemelb-codes/hemelb (status=inventoried, solutions=0)
- openLB/openLB (status=inventoried, solutions=0)

**E-10 transfer=fits 与许可证口径混用（最重要）** —— oss_solutions.transfer 的 fits 在盘点期表达的是『技术可迁移』而非『法律可搬码』：21/46 条 fits 落在 GPL/AGPL/无许可/受限仓上。本报告把两轴拆开：技术轴保留原值，法律轴另立『可搬码行』子集，并据此把推荐里的 fits 收紧为 adapt/reference。
- omalaspinas/palabos|CI|format-gate-ccache-matrix-ci|copyleft-strong(AGPL)
- lssfau/lbmpy|GAP-LBM-MCMP-POISEUILLE|shan-chen-mcmp-barycentric-guo|copyleft(GPL)
- je-santos/MPLBM-UT|NEW: 准静态排驱压力阶梯 + 断点续算协议|quasi-static-pc-ladder-with-checkpoint|copyleft(GPL)
- joconnor22/LIFE|NEW:CAP-LBM-VALIDATION-SURFACE|bitwise-refdata-regression-plus-benchmark-cases|copyleft(GPL)
- ebinan92/Fingering_dynamics|NEW:GAP-LBM-PHASEFIELD-FREEENERGY|free-energy-cahn-hilliard-core|no-license
- ebinan92/Fingering_dynamics|NEW:GAP-LBM-WETTING-DIFFUSE|diffuse-interface-wetting-via-wall-order-parameter|no-license
- joconnor22/LIFE|NEW:LBM-IBM-MDF-STENCIL|ibm-epsilon-gram-implicit-correction|copyleft(GPL)
- hietwll/LBM_Taichi|NEW:ghia-1982-cavity-validation-data|ghia-1982-lid-cavity-reference|no-license
- ProjectPhysX/FluidX3D|NEW:动量交换固体受力面(阻力/扭矩)|momentum-exchange-solid-force-and-torque|restricted
- lssfau/lbmpy|NEW:案例组织（单点配置对象 + 场景工厂）|lbmconfig-single-point-config|copyleft(GPL)
- lssfau/lbmpy|NEW:测试与验证面（逐点参考回归 + 解析解 + 符号 Chapman-Enskog）|pointwise-reference-regression|copyleft(GPL)
- joconnor22/LIFE|test-verification-surface|regression-test-framework|copyleft(GPL)
- je-santos/MPLBM-UT|案例组织|yml-driven-xml-template-pipeline|copyleft(GPL)
- lssfau/walberla|案例组织|apps-4layer-plus-prm-plus-scenario-class|copyleft(GPL)
- omalaspinas/palabos|案例组织|examples-taxonomy|copyleft-strong(AGPL)
- ProjectPhysX/FluidX3D|测试与验证面|analytic-anchored-in-situ-l2-convergence|restricted
- omalaspinas/palabos|测试与验证面|plb-regression-flag-asan|copyleft-strong(AGPL)
- sailfish-team/sailfish|测试与验证面|analytic-profile-error-curve-plus-golden-fields|copyleft-weak(LGPL)
- omalaspinas/palabos|稀疏 / 多孔|partial-bounce-back-porous|copyleft-strong(AGPL)
- je-santos/MPLBM-UT|稀疏/多孔|edt-two-segment-geometry-tag-compression|copyleft(GPL)
- omalaspinas/palabos|自由面|thuerey-mass-tracking-free-surface|copyleft-strong(AGPL)

> 缺口纪律：以上缺口**不阻塞**本报告结论（结论只基于已归集到的 196 条谱系行），但任何以「全仓已覆盖」为前提的下游决策都不成立。

## 八、机器块（[OSS-SOL] × 16，供守护线并入 oss_solutions）

```
[OSS-SOL]
repo: lbm-principles-practice/code
slug: GAP-LBM-MCMP-POISEUILLE
tag: scmp-two-component-planar-interface
method: [综合推荐 F01 两组分分层 Poiseuille 对标] 唯一 MIT 的可搬两组分 SC 骨架：单格子多组分分布 + 平面界面初值，最小可验证（原表即 adapt：通道几何/边界需改写，故不升 fits）；路线：以 lbm-principles-practice/code（MIT）的双组分 SC + Zou-He 反算为母版起最小可证单元：单通道 D2Q9、两组分各自演化、界面位置与分相流量由积分量输出；解析锚用 TCLB 的分层剖面 U=6·Uavg·Y(H−Y)/H²（reference，仅取公式）。力项注入取 lbmpy 的重心 Guo 形式，速度混合取 listLBM 的策略化写法并显式记录「谁混合、按 ρσ 还是按 τ」这一自由度。
files: chapter9/shanchen.cpp:29-36, chapter9/shanchen.cpp:79-109, chapter9/shanchen.cpp:142-161, chapter9/shanchen.cpp:191-197, chapter9/shanchen.cpp:215-229, chapter9/shanchen.cpp:261-263, chapter6/force_poiseuille_BB.m:129-137, chapter6/force_poiseuille_BB.m:157-198, chapter6/force_poiseuille_BB.m:229-235
conventions: rho0=1 pseudopotential reference; tau=1 gives nu=(2tau-1)/6=1/6; force term carries the (1-omega/2) factor for 2nd-order spatial discretisation; reported velocity is u+0.5F/rho while momenta() stores raw momentum; the ch9 file is fully periodic so a walled channel must be added; interface initialised with rho_A/rho_B=0.1/0.9 and total density 1
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: NEW:scmp-young-laplace-calibrator-with-guardrails
tag: shan-chen-laplace-pressure-calibration
method: [综合推荐 F02 伪势 SCMP/MCMP 力项与势函数] MIT 且自带『标定器 + 护栏』：把 ψ/EOS/G 与 Laplace 压差一起校准，正是 GAP-LBM-MCMP 验收要的工装形态；路线：伪势层拆成三个可插拔点：ψ(ρ) 策略、互作用力 stencil（含 EDM 展开）、力项注入（Guo/EDM）。标定护栏直接吃 lbm-principles 的 Young-Laplace 标定器（fits），先钉 σ 再谈密度比。
files: chapter9/shanchen.cpp:126-139, chapter9/shanchen.cpp:326-377, chapter9/shanchen.cpp:355-369, chapter9/shanchen.cpp:358
conventions: 4x4 node averaging boxes; radius from the rho_av crossing with linear interpolation; interaction term needs the 1/6 factor; the shipped estimator declares rad uninitialised at :358 and does not check whether the scan found a crossing, so gamma is printed from undefined memory when the droplet merges with its periodic image (measured gamma=-4.86 at R_init=24) - reuse must add a validity guard
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/walberla
slug: MCMP 高密度比
tag: variable-density-equilibrium-linear-in-C
method: [综合推荐 F03 高密度比与界面通量路线] 唯一把密度直接写进平衡态与体力、并把黏度随相变参数化的配方（ρ 比可至 1000）；路线：主路线走『相场/变密度平衡态』而不是死磕伪势 EOS：walberla 的 ρ=ρ_L+C(ρ_H−ρ_L) 线性映射 + TCLB 的压力演化格式组合，可同时保住密度比与 Mach 限制。伪势侧仅保留 MPLBM-UT 的低密度比基线作对照。
files: apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:59-66, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:72-74, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:45-46, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_taylor_bubble.py:20-21
conventions: rho_L/rho_H 为格点单位；粘度比 100(RTI)/4236(Taylor)；界面宽 3 格可跑；Morton 0.015；g 为格点加速度乘局部密度
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/walberla
slug: NEW:自由面多余质量分配模型族（9 种可切换）
tag: excess-mass-9-family
method: [综合推荐 F04 自由面（fill-level / VOF / mass tracking）] 把『多余质量怎么分配』显式做成 9 种可切换模型族——这正是自由面实现最容易悄悄错的地方；路线：自由面按三件套拆解实现：状态机（flag 转移）、质量账本（F→I→G 转移与超额质量分配）、界面分布重建（法向由 fill level 差分 + 非平衡反弹）。质量分配必须做成可选模型族并带守恒断言（Σmass 漂移）。
files: src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h:85-94, src/lbm/free_surface/dynamics/SurfaceDynamicsHandler.h:358
conventions: 模型名即枚举名（EvenlyAllInterface / WeightedAllInterface / ...FallbackLiquid）；分配发生在 cell conversion 之后、回填之前
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: ludwig-cf/ludwig
slug: 相场 Allen-Cahn
tag: cahn-hilliard-only
method: [综合推荐 F05 相场 Allen-Cahn / Cahn-Hilliard] BSD-3 可搬：守恒 CH 的面通量形式 + 守恒漂移补偿 PHI_CONSERVE_* 是可直接落地的方程骨架；路线：方程骨架取 ludwig（BSD，可搬），力项分层取 walberla 的双力项分离（重写），三元/顺序参数取 CudaPhaseField 的保守 AC + 调和 λ 构造（公式）；解析锚用自由能 kink 解。
files: `src/free_energy.h:45`, `src/fe_ternary.c:6`
conventions: mobility M 为常数参数; phi in [-1,1]; 守恒修正开关 PHI_CONSERVE_DEFAULT/COMPENSATED/GLOBAL_SUBTRACT
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:geometrical-wetting-normal-rotation
tag: akai2018-geometrical-wetting
method: [综合推荐 F06 润湿 / 接触角] BSD-3 可搬：几何法把色梯度绕 n_w 旋转到 θ，无需 G_ads 吸附系数标定；路线：两条腿：几何法（MF-LBM，法向旋转，适配任意多相路线）作默认；吸附系数反演（MPLBM-UT 公式）作标定与交叉验证。壁面法向用 iso8 平滑预处理，否则阶梯壁面会把接触角误差放大。
files: Phase_gradient.F90:209-265, Phase_gradient.F90:238-259
conventions: θ 为以润湿相量取的局部接触角；仅当该格 |C|>1e-6 才施加；候选择优用 distP<=distM 判据；仅适用 θ<90°（几何旋转无法表达疏水侧更强的反转）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lettucecfd/lettuce
slug: NEW:gpu-fused-kernel-codegen
tag: component-protocol-template-codegen
method: [综合推荐 F07 GPU 融合 kernel 与存储压缩] MIT：算子按组件模板生成、JIT 可选，Python 侧落地成本最低；路线：近期（可运行）：lettuce/vivsim 的框架内融合（MIT，fits）＋ A-A pattern 省一半内存；中期（性能）：照 walberla/lbmpy 的生成式 sweep 设计自研 codegen（禁止搬码）；存储压缩按 FluidX3D FP16 的公开算法自行实现。
files: lettuce/base.py:30-52, lettuce/cuda_native/_generator.py:46-127, lettuce/cuda_native/_template.py:39, lettuce/cuda_native/_template.py:55-81, lettuce/cuda_native/_default_code_gen.py:92-114
conventions: block 16x16(2D)/8x8x8(3D)；nvcc --use_fast_math --maxrregcount=128 -O3；扩展名=内容哈希前 8 hex，版本变更即重编译
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:分布式 MPI（Cart 分区 + 智能负载均衡 + halo 非阻塞交换）
tag: mpi-cart-smart-decompose-halo
method: [综合推荐 F08 分布式多卡 / MPI] Apache-2.0 可搬：MPI_Cart 拓扑 + 按活跃单元数的 smart decomposition 择优，正是体素多孔域要的负载均衡；路线：分区层用 LUMA 的 Cart + 活跃单元负载评估（Apache，可搬）；通信层先把 halo 做成显式 non-blocking post/wait 两段（ludwig 的 post-wait-split）以便插计算遮盖，再图 ghost 交换融合。
files: LUMA/src/MpiManager.cpp:107-136, LUMA/src/MpiManager.cpp:1220, LUMA/src/MpiManager.cpp:1457, LUMA/src/MpiManager.cpp:1400, LUMA/src/MpiManager.cpp:631, LUMA/src/MpiManager.cpp:718, LUMA/src/MpiManager.cpp:789, LUMA/src/MpiManager.cpp:822, LUMA/src/ObjectManager.cpp:105-110
conventions: 三维周期笛卡尔拓扑; halo 厚度 1 层; 消息尺寸先协商（Mpi_buffer_size_send/recv.cpp）; MPI_DOUBLE、f 缓冲按方向分段; 无通信/计算重叠（Waitall 同步）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sorush-khajepor/listLBM
slug: NEW:多孔介质稀疏索引集（子链表分类+显式邻接表+固体节点剔除）
tag: sparse-sublist-neighbor-list
method: [综合推荐 F09 稀疏 / 多孔介质几何] 子链表分类 + 显式邻接表 + 固体节点剔除，是唯一带『体素图像→孔隙率报表』全链路的实现；路线：几何摄入走『体素/图像 → EDT 分层 → tag 压缩』（MPLBM-UT fits）；存储走 list 型子链表 + 显式邻接表（listLBM 算法级重写）；亚网格层预留 PSB 钩子（lettuce 公式）。
files: lists/parts/part_mod.f90:14-42, lists/base/mainlist_mod.f90:104-119, lists/base/mainlist_mod.f90:111, collision/base/collisionBGK_mod.f90:77-83, lists/kinds/walllist_mod.f90:20-48, imgproc/imgproc2d_mod.f90:197-211
conventions: neilist 索引为 0 基方向 × cell 索引；halfqs 为每格子「半边方向」表(lattice_mod.f90:87,194,257)；子链表用 istinmlist/indinmlist 界定；实测 4 个 26x26 盒子使活跃流体 15876→13172(-17.0%)、600 步 wall clock 7.286→6.347s(-12.9%)
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: 2b-t/LB-t
slug: NEW:案例配置七段式 JSON schema + LBM↔SI 三基准量换算
tag: case-schema-json-units-converter
method: [综合推荐 F10 案例组织与运行管理] MIT：七段式 JSON schema + LBM↔SI 三基准量换算，是唯一把单位换算内建进案例格式的；路线：案例格式取 LB-t 的 JSON schema（含单位换算与三基准量），配置对象取 lbmpy 的单一事实源写法，运行管理取 MF-LBM 的 checkpoint 状态机；把 listLBM 的单文件算例明确列为反例（不迁移）。
files: include/lbt/simulation/simulation.hpp:682, include/lbt/converter.hpp:35, src/main.cpp:39, examples/test_1/settings.json
conventions: 三基准量 (Length, Velocity, Density) 定标; 压力因子=rho_f*u_f^2; 时间因子=len_f/vel_f
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:测试与验证面（逐点参考回归 + 解析解 + 符号 Chapman-Enskog）
tag: pointwise-reference-regression
method: [综合推荐 F11 测试与验证面] 逐点参考回归（沿中心线 64 点 np.allclose 对比外部参考实现）——比单一积分量误差强得多；路线：验证面按三层建：符号层（等价方程/稳定性，pylbm 公式）、解析剖面层（lettuce 式 1% 门）、逐点参考场层（lbmpy 式中枢线/剖面 np.allclose）。基准数据集一律取论文原表（如 Ghia 1982），不取无许可证仓库的数据文件。
files: tests/shan_chen/test_shan_chen_two_phase.py:85-92, tests/shan_chen/test_shan_chen_two_component.py:174-175, src/lbmpy/analytical_solutions.py:1, src/lbmpy/chapman_enskog/chapman_enskog.py:1
conventions: 参考值来自 lbm-principles-practice/chapter9/shanchen.cpp（1000 步）；allclose 默认 rtol/atol；CE 展开到 epsilon^2 以上易爆炸，建议仅做一次性方法族门禁
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: ludwig-cf/ludwig
slug: CI
tag: per-lattice-matrix-ci
method: [综合推荐 F12 CI 分级门禁] 按格子分 job 并行构建+回归，矩阵化最彻底；路线：四层门禁：lint → unit → regression（短回归构建，palabos 的 PLB_REGRESSION 宏做法）→ full-matrix（按格子/按物理能力）。反例入册：Fingering_dynamics 无 tests/ 无 CI 无断言。
files: `.github/workflows/regression.yml:31`, `.github/workflows/codecov.yml:14`
conventions: cp config/github-gcc.mk config.mk; sed 切换 D3Q19->D2Q9 等目标格子
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:IBM 直接力 + RKPM ε 守恒校正 + 强耦合子迭代
tag: ibm-direct-forcing-rkpm-epsilon
method: [综合推荐 F13 IBM / 流固耦合与受力] Apache-2.0：Peskin 3 点核 + RKPM ε 守恒校正 + 强耦合子迭代，谱系最完整且可搬；路线：实现层先用 vivsim 的预计算 stencil（MIT 可直接搬），物理层照 LUMA 的三件套（力→ε 校正→子迭代）补上『力守恒』这一常被省略的环节；强耦合收敛判据取 LIFE 的 Aitken 形式。
files: LUMA/src/ObjectManager_ops_ibm.cpp:255-272, LUMA/src/ObjectManager_ops_ibm.cpp:479-482, LUMA/src/ObjectManager_ops_ibm.cpp:498-596, LUMA/src/ObjectManager_ops_ibm.cpp:598-616, LUMA/src/ObjectManager_ops_ibm.cpp:621-694, LUMA/src/ObjectManager_ops_ibm.cpp:773-880, LUMA/src/ObjectManager_ops_ibm.cpp:111-140, LUMA/inc/definitions.h:228-231
conventions: δ 支撑 1.5Δx（3 点）; 体积尺度 volWidth=ε, volDepth=ds; 子迭代 TOL=1e-4, MAXIT=10; 可选 L_UNIVERSAL_EPSILON_CALC 全局统一 ε; 力系数 2 隐含 Δt=1
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: 2b-t/LB-t
slug: NEW:TRT 两松弛算子与 magic 参数 λ=0.25
tag: trt-two-relaxation-magic-lambda
method: [综合推荐 F14 碰撞算子族] MIT：TRT 双松弛 + magic λ=0.25 的显式写法（壁面位置无关的经典取值）；路线：碰撞算子按『SRT→TRT(magic)→MRT/central-moment』三级推进，先固定 magic 取值（λ=0.25 与 3/16 各跑一遍，记录壁面滑移差），再上 central-moment（LIFE 公式）与 KBC。
files: src/population/collision/collision_trt.hpp:61, src/population/collision/collision_trt.hpp:66, src/population/collision/collision_trt.hpp:195, src/population/collision/collision_trt.hpp:209, src/population/collision/collision_trt.hpp:217
conventions: CS=1/sqrt(3); tau=nu/CS^2+1/2; λ=0.25 为推荐 magic 值; 计算代价≈BGK+一次加减
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: NEW:scalar-dirichlet-boundary-library
tag: anti-bounce-back-inamuro-wetnode
method: [综合推荐 F15 边界算子族] MIT：反反弹 + Inamuro 湿节点，标量 Dirichlet（速度/压力）最干净的实现；路线：边界库按『壁面（半程 BB / 插值 BB / 正则化）→ 出入口（Zou-He 反算 / 反反弹 Dirichlet / EE）→ 特殊（Lees-Edwards 剪切、湿节点固定密度、平衡态外插 EE）』三组收编；每组附壁面偏移与角点处理约定。EE 的实现只见于无许可证仓（hietwll/LBM_Taichi，all-rights-reserved），只可按公式自写。
files: chapter8/film_antibb.cpp:152-205, chapter8/film_inamuro.cpp:157-215, chapter5/couette_wetnode.m:111-146, chapter5/couette_wetnode.m:116-123, chapter8/cylinder.cpp:214-227
conventions: anti-bounce-back uses the opposite direction from the post-streaming array (f2), Inamuro uses the pre-streaming array (f); wet-node scheme selector wetnode=1 equilibrium / 2 non-equilibrium extrapolation / else non-equilibrium bounce-back; free-wall/specular is a mirrored copy of the neighbouring populations plus rho_corner=rho_neighbour
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:重叠网格加密界面算子（explode/coalesce + 2:1 子循环）
tag: overlapping-grid-refinement-explode-coalesce
method: [综合推荐 F16 网格加密 / 可微与生成式框架] Apache-2.0：重叠网格 explode/coalesce + 2:1 子循环，块加密的工程化写法；路线：加密路线取 LUMA 的重叠网格（Apache 可搬）+ walberla 的等值面流式修正公式；可微/涨落路线只做设计预留（LOL 的 BPTT 与 ludwig 的 ghost-mode 涨落），两者许可证/框架均不可搬。
files: LUMA/src/GridObj_ops_lbm_optimised.cpp:36-47, LUMA/src/GridObj_ops_lbm_optimised.cpp:270-290, LUMA/src/GridObj_ops_lbm_optimised.cpp:598-620, LUMA/src/GridObj_ops_lbm_optimised.cpp:145-147, LUMA/src/GridObj_init_grids.cpp:399-441, LUMA/src/GridObj_init_grids.cpp:526, LUMA/inc/Enumerations.h:103-115
conventions: 加密比固定 2; 每粗步=2 细子循环; 标签 eRefined/eTransitionToCoarser/eTransitionToFiner/eFluid; 体力乘 refinement_ratio=1/2^level
transfer: adapt
[/OSS-SOL]
```

---

*生成脚本：`artifacts/oss-solutions-v1/{load_data,families,reco,build_report}.py`；数据导出：`oss_solutions.tsv`（196 行）、`oss_inventory.tsv`（29 行）、`feature_ledger.tsv`（49 行）；机检：`checks.json`；缺口：`gaps.json`。*
