## 一、概况

- 主语言为 **C**（C99；`src/*.c` 的文件数与行数由脚本统计后注入第五节图表），**不是 Fortran**——与派发单上的先验描述不同，按代码本体更正。`src` 侧全部用 `__host__` / `__device__` / `__global__` 宏书写，由 `target/target.h` 在 `__NVCC__` / `__HIPCC__` / 其它之间编译期分派，因此 x86 序列、CUDA、HIP 三个后端共用同一份源码；文档为 LaTeX（`docs/*.tex`）。 〔`README.md:4`；`target/target.h:27`；`target/target.h:29`；`target/target.h:31`；`src/kernel_3d_v.h:71`〕
- 许可证 = **BSD-3-Clause**（Copyright 1998-2018 The University of Edinburgh；三条款：保留版权声明、保留条件与免责声明、不得用 Edinburgh 名义背书）。GitHub API 的 license 字段给出 `NOASSERTION`，只因 LICENSE 正文是未加 SPDX 头的 BSD-3 文本，实质仍是标准三条款 BSD。**对我们的可用性判断：可直接用于闭源/商业衍生**，唯一义务是随分发包保留三条款文本与免责声明、且不得暗示 Edinburgh 背书；无 copyleft 传染、无专利授权条款（亦即没有专利保护伞——照抄算法思路时需自行评估第三方专利）。 〔`LICENSE:4`；`LICENSE:7`；`LICENSE:17`〕
- 活跃度与快照指纹：`version.h` 记 **0.24.0**；仓库元数据（api.github.com，2026-11-09 取）pushed_at=2026-11-05、stars=74、size=195480 kB；`CHANGES.md` 有版本流水。本次深盘以 master 源码 tarball sha256=`6e216ef8f4c2f5654dc11b9c6b4f3570f97cc5e277193922b48cfaf0cd72e198` 作为可复现指纹（commit SHA 因 api.github.com 403 限流未取到，如实记录，用树指纹替代）。 〔`version.h:16`；`CHANGES.md:4`〕
- 定位一句话：**面向复杂流体（胶体颗粒、液晶、电解质、表面活性剂）的格子 Boltzmann 求解器**；核心是「自由能泛函 + 序参量场 + 守恒 Cahn-Hilliard 输运 + LBM 流体」四件套，粒子用 link bounce-back 双向耦合，附带 MPI 域分解与 CUDA/HIP 加速。 〔`src/free_energy.h:5`；`docs/intro.tex:20`〕

- 代码体量（脚本统计）：`src/*.c` **190 个文件 / 91126 行**；LaTeX 文档 14 篇；构建配置 14 套（gcc / mpicc / nvcc / hipcc 等）。
- 验证面（脚本统计）：单元测试 108 个 `test_*.c`，回归 case 163 个（= `tests/regression/<格子>/*.inp` 输入文件数，逐格 d2q9:5、d3q15:8、d3q19:5、d3q19-elec:9、d3q19-io:15、d3q19-short:108、d3q27:13）。

## 二、方法谱系（格子 / 碰撞 / 力项 各自在哪、怎么组织）

- **格子谱系**：D2Q9 / D3Q15 / D3Q19 / D3Q27 四套，各一个 `lb_d*q*.c`；速度集 `cv`、权重 `wv`、归一化因子 `na` 与**模态变换矩阵** `ma`（f→mode）、`mi`（mode→f）全部数据化。D3Q19 模态行依次为 ρ、c_x、c_y、c_z、s_xx、s_xy、s_xz、s_yy、s_yz、s_zz，其后是 ghost 模态；矩阵在构造期正交化并归一化（`wip += w*a*a` → `na = 1/wip`）。碰撞核只认模态，与格子维度解耦。 〔`src/lb_d3q19.c:132`；`src/lb_d3q19.c:135`；`src/lb_d3q19.c:77`；`src/lb_d2q9.c:37`；`src/lb_d3q15.c:38`；`src/lb_d3q27.c:38`〕
- **碰撞算子：只有一套通用 MRT，按松弛率配置切成三种谱系**。枚举 `lb_relaxation_enum` 含 `LB_RELAXATION_M10`（默认：各水动力模态独立松弛率，经典 MRT）、`LB_RELAXATION_BGK`（全部同一松弛率）、`LB_RELAXATION_TRT`（两组松弛率：shear 与 bulk 分开，等价于按对称性分组的 TRT）。站点核 `lb_collision_mrt1_site()` 流程：f→mode → 取 ρ,u,应力 → 松弛 → 回写模态 → mode→f。**没有 KBC / entropic / cumulant 碰撞核，也没有 RR 谱系**。 〔`src/lb_data_options.h:34`；`src/lb_data_options.h:35`；`src/lb_data_options.h:36`；`src/collision.c:1212`；`src/collision.c:61`；`src/collision.c:1285`〕
- **力项加法 = Guo 型速度移位（本仓唯一路径）**：先把体力折进速度 `u = (j + F/2)/ρ`，松弛非平衡应力时再加 `(2 - 1/τ)(u_a F_b + F_a u_b)`（Guo 力项的 MRT 形式），最后把动量模态回写为 `mode[1+ia] += F_a`。**没有** RK（reduced-kinetic）力项，也**没有** shifted-velocity 精确速度变体。 〔`src/collision.c:374`；`src/collision.c:464`；`src/collision.c:465`；`src/collision.c:518`〕
- **平衡态吃进自由能压强张量**：当自由能提供对称应力时，平衡应力取 `seq = ρ u u + symm`（`fe->use_stress_relaxation` 开关），即「两相界面应力直接进平衡态」而非事后加力——这是自由能 LBM 的关键做法。非水动力（ghost）模态各自趋向零松弛，可叠加受热噪声 `ghat` 与应力噪声 `shat`（涨落 LBM，方差由 1/τ 与 kT 给出），且噪声只在流体站点生成以保证回归可复现。 〔`src/collision.c:416`；`src/collision.c:407`；`src/collision.c:536`；`src/collision.c:487`〕
- **双分布（binary）分支仍在**：`lb_collision_binary()` → `lb_collision_mrt2_site()` 保留「每站点两条分布」的旧路径，注释明确说明只为对称自由能保留（与单分布路径并存，属兼容层而非独立模型）。 〔`src/collision.c:50`；`src/collision.c:64`〕
- **向量化组织**：站点函数内部用 `for_simd_v(iv, NSIMDVL)` 展开显式短向量循环，外层 `kernel_3d_v_t` 提供三维循环域与掩码；同一份代码在 CPU 被编译器向量化、在 GPU 上退化为线程内串行。 〔`src/kernel_3d_v.h:71`；`src/collision.c:291`；`src/kernel_3d_v.h:22`〕

## 三、边界处理（BBL / 进出口 / 曲率-IBM）

- **壁面：link bounce-back（BBL）为主力**。`wall_bbl()` 扫描 map 中标记为 wall 的连接做反弹；`wall_bbl_slip_kernel()` 追加 Navier 滑移（滑移长度由 `wall->param->slip` 控制）；初始化时 `wall_set_wall_distributions()` 把壁面侧分布铺成平衡态。 〔`src/wall.c:971`；`src/wall.c:1008`；`src/wall.c:1129`；`src/wall.c:911`；`src/wall.c:982`〕
- **胶体/曲面边界：Ladd 式 link bounce-back + 动量累积 + 润滑修正（没有 IBM）**。`bounce_back_on_links()` 三趟：pass0 清站点分布，pass1 对每条 crossing link 做 `f_i += dm_a`（动量交换，dm 由粒子表面速度算得，椭球用 Legendre 投影/切向基），pass2 把累积动量与力矩送回刚体（`bbl_6x6_gaussian_elimination` 解惯性张量方程）。`lubrication_*` 追加球-球近场润滑力，`bbl_wall_lubrication_account()` 处理粒子-壁面润滑亏空。**曲率处理：没有插值型 IBM**（无 delta 核、无 Eulerian–Lagrangian 力扩散），几何精度靠 link 细分 + 解析润滑修正获得——这是与我们 IBM 路线最大的方法论差异。 〔`src/bbl.c:221`；`src/bbl.c:440`；`src/bbl.c:671`；`src/bbl.c:1435`；`src/lubrication.h:39`；`src/bbl.c:62`〕
- **进出口：Dirichlet 型「给定 ρ 或 u」开边界，不是 Zou-He**。入口 `lb_bc_inflow_rhou` 把入口平面速度设为 u0，出口 `lb_bc_outflow_rhou` 把出口平面密度设为 ρ0，再由 `impose()` 以目标 ρ、u 重建分布；工厂 `lb_bc_open_rt()` 从输入文件读类型与数值。对 `src` 全量检索 `zou` 无命中 → 确认**没有 Zou-He，也没有非平衡反弹（NEE）格式**。 〔`src/lb_bc_inflow_rhou.c:273`；`src/lb_bc_inflow_rhou.c:478`；`src/lb_bc_outflow_rhou.c:272`；`src/lb_bc_open_rt.c:43`〕
- **湿边界 / 固-液接触角**：由「solid gradient」族在固体面用 wetting 参数修正序参量法向梯度实现——对称自由能用 `C`，三元体系用 `h1..h3` 并可直接反算接触角（`gradient_2d_ternary_solid.c` 注释给出 Eq.24-26 的映射，`fe_ternary_wetting_angles()` 输出角度）。序参量另有 inflow-fixed / outflow-free 边界。（`angle_cosine.c` 是胶体间余弦弯曲势，**不是**壁面接触角模型。） 〔`src/gradient_2d_ternary_solid.c:79`；`src/gradient_3d_27pt_solid.c:3`；`src/symmetric.h:44`；`src/fe_ternary.c:229`；`src/phi_bc_inflow_fixed.c:3`；`src/phi_bc_outflow_free.c:3`〕

- **曲率/IBM 逐项定位结论**：壁面曲率由 link 细分 + Navier 滑移表达，胶体曲率由 link 命中 + 解析润滑修正表达；`grep -ri 'immersed\|delta function' src` 无 IBM 实现，故本仓**没有**插值型 IBM / 多重直接力（MDF）可抄。

## 四、多相 / 多组元（重点：本仓路线与能力边界）

- **总判定：本仓没有 Shan-Chen（SCMP/MCMP）伪势模型，也没有 free-surface（VOF 类）模型**；多相走**自由能（van der Waals 型）路线 + 守恒序参量 Cahn-Hilliard**，并有三组分/表面活性剂扩展。`fe_id_enum` 列举全部自由能后端：NULL / SYMMETRIC / BRAZOVSKII / SURFACTANT / POLAR / LC / ELECTRO / ELECTRO_SYMMETRIC / LC_DROPLET / TERNARY，**其中没有任何互作用势（伪势）后端**。 〔`src/free_energy.h:27`；`src/free_energy.h:28`；`src/free_energy.h:36`；`src/free_energy.h:30`〕
- **互作用势形式（对称二元，主力）**：泛函 `F[φ] = (1/2) A φ² + (1/4) B φ⁴ + (1/2) κ (grad φ)²`（A<0 双井），化学势 `μ = Aφ + Bφ³ − κ ∇²φ`，热力学压强张量 `P_ab = [1/2 Aφ² + 3/4 Bφ⁴ − κφ∇²φ − 1/2 κ(∇φ)²] δ_ab + κ (∂_a φ)(∂_b φ)`（另有向量化版本供 SIMD/GPU）。参数集 = `a, b, kappa, c`（c 为壁面湿润参数）。**解析标度内置**：σ = sqrt(−8κA³/9B²)、界面厚度 ξ = sqrt(−2κ/A)——可直接由「给定 σ 与 ξ 反解 (A,B,κ)」，对我们参数标定极有用。 〔`src/symmetric.c:7`；`src/symmetric.c:316`；`src/symmetric.c:327`；`src/symmetric.c:410`；`src/symmetric.c:255`；`src/symmetric.c:271`；`src/symmetric.h:41`；`src/symmetric.h:43`〕
- **力项加法：两套并存可切换**。①**化学势梯度法** `f_a = −φ ∂_a μ`（`phi_grad_mu.c`：流体核 + 固体核 + 可选「外部化学势梯度」源项，并带回读修正 `phi_grad_mu_correction_kernel` 以保离散动量守恒）；②**热力学应力散度法** `∂_b P_ab`（`phi_force_stress.c::pth_stress_compute`，先全场存 3×3 应力再取散度，便于分离对称/反对称部分——液晶分支必需）。两者最终都写进 `hydro->force`，再经第二节的 Guo 型力项进入碰撞。 〔`src/phi_grad_mu.c:5`；`src/phi_grad_mu.c:35`；`src/phi_grad_mu.c:52`；`src/phi_force_stress.c:171`；`src/phi_force_stress.c:198`〕
- **序参量输运 = 面通量式守恒 Cahn-Hilliard**：逐面算扩散通量 `f_face −= M(μ_next − μ_here)`（六面各一次），叠加 `advection_x/...` 的对流通量，用 forward step 更新 φ。「面通量唯一」保证离散守恒；另给两种守恒漂移修正 `PHI_CONSERVE_COMPENSATED_SUM` 与 `PHI_CONSERVE_GLOBAL_SUBTRACT`，以及 `phi_ch_random_flux()` 的 Landau 型随机通量（涨落 CH）。旧差分实现 `ch_solver()` 仍在（`cahn_hilliard.c`），作为兼容路径。 〔`src/phi_cahn_hilliard.c:83`；`src/phi_cahn_hilliard.c:386`；`src/phi_cahn_hilliard.c:231`；`src/phi_cahn_hilliard.c:190`；`src/phi_cahn_hilliard.h:31`；`src/phi_cahn_hilliard.h:32`；`src/phi_cahn_hilliard.c:59`；`src/cahn_hilliard.c:174`〕
- **三元 / 表面活性剂（Semprebon et al., PRE 93, 033305 (2016)）**：三场 φ（组分）、ψ（表面活性剂浓度）、ρ；参数 `alpha, kappa1..3, h1..h3`；**ρ 在代码注释里被明确标为 "spectacting"（旁观者）**，即三元模型仍是等密度近似下的组分演化，不做真实密度对比。润湿由 `h1..h3` 给出并可反算接触角。另有 `surfactant.c`（Frumkin/Langmuir 等温线吸附型自由能）、`brazovskii.c`（微乳液）、液晶分支 `blue_phase.c`(Landau–de Gennes) + `leslie_ericksen.c`、电动力学 `fe_electro.c` + `nernst_planck.c` + `psi*.c`（Poisson：SOR 或 PETSc）。 〔`src/fe_ternary.c:6`；`src/fe_ternary.c:12`；`src/fe_ternary.h:29`；`src/fe_ternary.c:328`；`src/surfactant.h:33`；`src/fe_ternary.h:32`；`src/psi.c:426`〕
- **密度比能力：无（等密度 / 近 Boussinesq 近似）**。ρ 是 LBM 的零阶矩、φ 是独立平流场；**没有任何把 φ 映射成两相本相密度 ρ_liq/ρ_gas 的机制**，也没有 MCMP 的 G_αβ 互作用强度参数。三元模型的 `FE_RHO` 更是显式「旁观」。→ 对我们的「MCMP 高密度比」功能点，本仓只能作方法论参考，**不能照搬**。 〔`src/fe_ternary.c:12`；`src/physics.c:43`〕
- **伪流（spurious current）处理：没有专门的抑制或后处理机制**。文档只在 `docs/binary.tex` 指出「单一松弛率的自由能 LBM 会引入伪各向异性项」，其对策是算法层的多松弛率 MRT + 正确平衡应力（见第二节），而非事后修正。这是我们自己要补的一课：需自建「伪流幅度」验收量（本仓不提供）。 〔`docs/binary.tex:343`〕

### 4.1 与我们预期的差异（诚实记录）

- 派发单先验写「SCMP / gauge」，但代码本体里**既无 Shan-Chen 伪势，也无 gauge（规范）变换实现**；本仓多相=free-energy + Cahn-Hilliard。gauge 相关检索（`grep -rn gauge src`）零命中，故不编造。
- 派发单先验写「Fortran」，实测为 C（含 CUDA/HIP 宏），已在第一节更正。

## 五、GPU / 并行（布局 / 合并 / 分区 / 通信隐藏）

- **并行 = MPI 三维域分解 + SIMD/GPU 目标层；没有 OpenMP 或线程池层**。分区为 Px×Py×Pz 笛卡尔，坐标系统一用 `(n + 2*nhalo)` 的 stride 做索引，halo 宽度默认 1。 〔`src/coords.c:213`；`src/coords.c:66`〕
- **数据布局：三种寻址模型编译期可选**（`ADDR_AOS` 默认 / `ADDR_SOA` / `ADDR_AOSOA`），由 `addr_rank*` 一族宏在 `forward_addr_*`（AOS）与 `reverse_addr_*`（SOA）之间切换；AOSOA 用 `pseudo_iv(index) = index mod NVBLOCK` 把站点索引伪装成块内向量索引，使 SIMD 内层循环同时得到**内存合并**（同一 p 的相邻站点连续）与按 p 展开的寄存器友好性。**AOS 默认是历史的保守选择**（兼容存量核），GPU 用 SOA/AOSOA 提高合并度。 〔`src/memory.h:249`；`src/memory.h:232`；`src/memory.h:261`；`src/memory.h:271`；`src/memory.h:202`〕
- **分布数组**：`lb->f` 单块平铺，地址宏 `LB_ADDR(nsite, ndist, NVEL, index, n, p)`；传播用 f/fprime 双缓冲 pull 方式（`fprime[i] = f[i − c_p]`），随后交换指针，因此 streaming 不需要额外临时数组。 〔`src/lb_data.h:144`；`src/propagation.c:191`；`src/propagation.c:211`〕
- **通信与隐藏**：`lb_halo()` = `lb_halo_post()` + `lb_halo_wait()` —— 非阻塞 isend/irecv 成对提交、通信图初始化时预建（`lb_graph_halo_send_create` / `lb_graph_halo_recv_create`），**post 与 wait 分离，调用方可在其间插入本地计算以隐藏通信延迟**；场量（φ、μ、u、应力）走 `field_halo_swap()`；胶体跨进程用字节级 `MPI_Irecv/Isend`（`colloids_halo.c`）+ 分段规约（`colloid_sums.c`）；全局量用 `MPI_Allreduce`（如活动粒子计数）。 〔`src/lb_data.c:754`；`src/lb_data.c:1318`；`src/lb_data.c:1431`；`src/lb_data.c:48`；`src/field.c:384`；`src/colloids_halo.c:446`；`src/colloid_sums.c:321`；`src/bbl.c:191`〕
- **GPU**：单一 `target/` 抽象层把 `tdpMalloc/tdpMemcpy/tdpLaunchKernel/tdpDeviceSynchronize` 映射到 CUDA 或 HIP；`__host__`/`__device__` 宏让同一站点函数既作 CPU 循环体又作 GPU 线程体；`config/` 备有 nvcc / hipcc / nvcc+OpenMPI（CUDA-aware MPI）/ Archer2 等构建矩阵，含 `tdpDeviceGetP2PAttribute`（GPUDirect P2P）能力探测。**kernel 粒度 = 「一步一核」**：碰撞、传播、每个力项各是一个 kernel，由 `tdpDeviceSynchronize` 串联——**融合机会明确地留给我们**。 〔`target/target.h:38`；`target/target_cuda.c:40`；`target/Makefile:39`；`target/Makefile:43`；`src/collision.c:199`〕

### 5.1 代码体量分布（脚本统计，用于判断可复用代码的「重心」）

| 子系统 | .c 文件数 | 行数 |
|---|---:|---:|
| 并行/IO/基础设施（coords/io/util/stats） | 67 | 30237 |
| 自由能与相场（fe_*/phi_*/symmetric/CH） | 55 | 28923 |
| 胶体/粒子-流体耦合（BBL/润滑/对势） | 29 | 14055 |
| LBM 核心（碰撞/传播/格子/内存/内核） | 24 | 9396 |
| 边界与几何（wall/map/lb_bc/hydro） | 9 | 4812 |
| 其它 | 6 | 3703 |
| **合计** | **190** | **91126** |

见图 `figures/ludwig_inventory.png`（左：子系统行数；右：按格子的回归 case 数）。

## 六、可微 / 代码生成

- **可微 / 代码生成：两者都没有。** 无 autograd、无伴随、无 JAX/Torch 接口（仓内唯一 Python 文件是 `docs/figures/symmetric.py`，只画示意图）；无符号推导、无 kernel 生成器：模态矩阵、梯度模板、碰撞核全部手写 C，「生成」只在构建期发生（用宏裁剪格子/后端，`config/*.mk` 选编译器）。对我们的含义：**无法从 ludwig 借「自动微分」能力**，但它的「泛函 → μ → 压强张量 → 力」手写链路正是我们做 AD 时的**解析式对照源**，`fe_vt_t` 的 vtable 也是「换物理不改求解器」的现成接口范式。 〔`docs/figures/symmetric.py:1`；`src/free_energy.h:61`；`src/free_energy.h:68`〕

## 七、与我们功能点的映射（feature_ledger）

| feature_ledger slug | transfer | 本仓实现方式/证据 |
|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE` | `reference` | **不直接命中**：本仓无 Shan-Chen 互作用势，两组分分层 Poiseuille 用不上现成实现；可参考的是替代路径「压强张量进平衡态 + −φ∇μ 体力」，以及 `hydro_correct_momentum()` 的动量修正（保证体力不破坏整体动量守恒）。分层是否稳定还需要界面分辨率与伪流控制，这一条本仓不提供验收方法。 〔`src/symmetric.c:327`；`src/phi_grad_mu.c:40`；`src/hydro.c:667`〕 |
| `MCMP 高密度比` | `adapt` | **本仓无高密度比能力**（ρ 为旁观量）。可用的是「自由能→μ→力」链路与 σ/ξ 解析标度；高密度比所需的 ρ_liq/ρ_gas 与平衡态重构须我们在 TensorLBM 自建（变密度平衡态），并以两相密度比与 spurious current 双指标验收。 〔`src/fe_ternary.c:12`；`src/symmetric.c:271`〕 |
| `自由面（free surface）` | `reference` | **本仓无自由面模型**。可借用的组织是「map 固体/空单元状态 + 面通量无穿透条件」（`advflux_cs_no_normal_flux`），把它改造为「气相=空单元」的自由面近似。 〔`src/cahn_hilliard.c:193`；`src/map.h:26`〕 |
| `相场 Allen-Cahn` | `adapt` | **本仓只有守恒 Cahn-Hilliard（二阶、双井 φ⁴）与三元 Semprebon 泛函，无 Allen-Cahn（非守恒）**。但 `fe_vt_t` 的接口形态正好允许新增一个 AC 后端而不动求解器——这是我们该抄的扩展点；AC 方程本身（含润湿/接触角项）需我们自研。 〔`src/free_energy.h:45`；`src/fe_ternary.c:6`〕 |
| `GPU 融合 kernel` | `reference` | 本仓 kernel 粒度为「一步一核」，每步 `tdpDeviceSynchronize`，融合空间明确（collision + grad-mu + propagation 可合为一个核）。可参考其 `kernel_3d_t` 循环域描述、`__restrict__` 标注与 launch 参数计算方式。 〔`target/target_cuda.c:40`；`src/propagation.c:157`；`src/kernel_3d.h:39`〕 |
| `分布式多卡` | `adapt` | MPI 域分解 + halo post/wait 分离（通信隐藏）+ AOSOA 合并 + CUDA-aware MPI 构建矩阵；胶体跨进程用字节级 isend/irecv + 分段规约——可直接对照我们的粒子-流体耦合通信设计。 〔`src/lb_data.c:1431`；`src/memory.h:271`；`config/epcc-cirrus-nvcc-openmpi.mk:5`〕 |
| `稀疏/多孔` | `reference` | 任意固体几何由 per-site `map` 状态表达（fluid/wall/colloid links），壁面用 BBL 并支持 Navier 滑移 → 「多孔介质 = map 打洞 + 滑移壁」是现成范式，适合拿来搭稀疏/多孔验证面。 〔`src/wall.c:1129`；`src/wall.c:971`〕 |
| `案例组织` | `fits` | 输入文件（JSON，`util_cJSON`）+ 目录即案例（`tests/regression/<lattice>/<case>`），每 case 自包含参数与期望输出；构建配置外置到 `config/*.mk`。可直接映射到我们「一个案例目录 = 一份可复现证据」的做法。 〔`tests/regression/d3q19-short/serial-actv-s01.inp:3`；`src/util_cJSON.h:2`〕 |
| `测试与验证面` | `fits` | 单元测试按 feature 一个 `test_*.c`，回归比对用浮点按域容差脚本 `tests/awk-fp-diff.sh`（非字节比较）。可复用的是「物理量级容差比较器」与测试粒度；**不可复用的是其基准强度**（回归容差 ≠ 物理精度，仍需外接解析解/实验）。 〔`tests/awk-fp-diff.sh:5`；`tests/test-diff.sh:5`〕 |
| `CI` | `reference` | `regression.yml` 按格子（d2q9/d3q15/d3q19/d3q27）分 job 并行构建+跑回归，`codecov.yml` 出覆盖率，`codeql-analysis.yml` 做静态扫描；构建配置用 `cp config/github-gcc.mk config.mk` 注入。这种「矩阵分格 + 覆盖率 + 静态分析」三件套可照搬进我们仓的 CI 骨架。 〔`.github/workflows/regression.yml:31`；`.github/workflows/codecov.yml:14`〕 |
| `NEW: 涨落 LBM（ghost-mode 噪声源）` | `reference` | 本仓实现了「水动力模态确定性演化 + ghost 模态受热噪声」的涨落格子 Boltzmann（噪声方差由 1/τ 与 kT 决定），并可向 Cahn-Hilliard 注入随机通量。我们缺口账本里没有这一项，建议立 NEW：热涨落驱动的界面毛细波/Brownian 验证面。 〔`src/collision.c:487`；`src/collision.c:502`〕 |
| `NEW: 粒子-流体近场润滑修正（lubrication）` | `adapt` | BBL 之外再叠解析润滑力（球-球径向/切向、粒子-壁面亏空核），是低分辨率下把阻力算对的关键；其「截断距离 rch + 解析近场修正 + 汇总到刚体力/力矩」的结构可复用到我们的颗粒/冰区接触耦合。 〔`src/lubrication.h:25`；`src/bbl.c:62`〕 |

计入 `fits`/`adapt`/`reference` 的统计：fits=2、adapt=4、reference=6；其中 NEW 建议 2 条。

## 八、可吸收 Top3 + 风险

- **Top1｜自由能 vtable + 「泛函→μ/σ→力」可插拔链路（→ TensorLBM 多相模块）**：`free_energy.h` 用一张纯函数指针表（fed/mu/stress/str_symm/str_anti/hvector/htensor + 三个向量化版本）统一 10 种自由能，碰撞核只依赖 `str_symm_v`，即**换物理不改求解器**。可在 TensorLBM 复刻「单接口 + 多后端」（Python 侧用 Protocol/ABC），并把 `symmetric.c` 的 μ、P_ab、σ、ξ 解析式当参考实现写进单测。**风险**：BSD-3 需保留声明；其「平衡态直接吃 symm」的推导绑定 D3Q19 MRT 谱系，移植到我们的 D3Q27/多松弛时必须重推离散平衡与力项。 〔`src/free_energy.h:54`；`src/free_energy.h:62`；`src/collision.c:410`〕
- **Top2｜面通量式守恒 Cahn-Hilliard + 守恒漂移补偿（→ TensorLBM 相场/两组分输运）**：「每面只算一次通量、相邻单元共用」+ 两级守恒修正开关 + 可选随机通量，是把 CH 跑长时间不漂质量的实用配方，单文件规模小、可直接对照实现，并以「万步质量漂移 < 1e-10」作为我们的验收线。**风险**：其面通量实现假设 1 层 halo 与 Lees–Edwards 剪切坐标，移植时需去掉 LE 耦合（`lees_edw_index` / `lees_edw_ic_to_buff`）。 〔`src/phi_cahn_hilliard.c:190`；`src/phi_cahn_hilliard.h:32`；`src/phi_cahn_hilliard.c:378`〕
- **Top3｜数值回归面 + 案例组织（→ TensorPlatform 证据/CI 面）**：`tests/regression/<lattice>/` 按格子分目录、每 case 自包含输入与期望输出，用浮点按域容差比对（不是字节比较）；单元测试按 feature 一个 `test_*.c`；CI 按格子分 job 并行。**可吸收的是「物理量级容差比较器」与「目录即证据单元」的组织**。**风险**：其容差是「相对本仓上次输出」的回归容差，**不是物理基准**；我们仍必须外接解析解/实验基准，不能把「回归绿」当精度达标（>15% 判据不受其保护）。 〔`tests/test.sh:7`；`tests/awk-fp-diff.sh:5`；`tests/regression/d3q19-short/serial-actv-s01.inp:3`；`.github/workflows/regression.yml:31`〕

### 8.1 明确不可吸收 / 需自建的清单

- 无 Shan-Chen 互作用势与 G_αβ 参数 → MCMP 互作用模型须自建（本仓只给自由能参照）。
- 无密度比机制 → 高密度比能力须自建（变密度平衡态 + ρ 非旁观）。
- 无 free-surface → 自由面须自建（可借 map 空单元 + 面通量无穿透思路）。
- 无 Allen-Cahn、无 KBC/cumulant 碰撞、无 IBM、无 AD/代码生成 → 均需自建或另找来源。

## 九、方法与可复现性（机器块前的自证）

- 源码获取：`codeload.github.com` tarball（`git clone` 在 120 s 内未完成、`api.github.com` 递归树遇到 403 限流；tarball 路线成功），sha256=`6e216ef8f4c2f5654dc11b9c6b4f3570f97cc5e277193922b48cfaf0cd72e198`，解包于 /tmp（**未** clone 进 TensorPlatform 仓）。
- 每条结论的指针由脚本按 `(file, needle)` 现场解析成 `file:line`，needle 找不到即构建失败——报告里不存在手抄行号。
- 指针总数：156 条，全部解析成功。
- 体量/计数类数字（文件数、行数、case 数）由同一脚本统计后注入，零手抄。

```text
[OSS-INV]
repo: ludwig-cf/ludwig
lang: C (C99; CUDA/HIP device code via target layer; no Fortran)
license: BSD-3-Clause (University of Edinburgh, 1998-2018)
methods: lattice=D2Q9;D3Q15;D3Q19;D3Q27 | collision=MRT(default, per-mode M10);BGK;TRT(shear/bulk two-rate);two-distribution MRT2(binary, legacy) | forcing=Guo velocity-shift + (2-1/tau)(uF+Fu) stress correction (only path); no RK, no shifted-velocity
multiphase: free-energy van der Waals phi^4+kappa (src/symmetric.c:297,316,327-358), analytic sigma/xi (src/symmetric.c:255,271), ternary Semprebon PRE93,033305 (src/fe_ternary.c:5,23), surfactant Frumkin (src/surfactant.h:33), conserved Cahn-Hilliard face-flux (src/phi_cahn_hilliard.c:388); NO Shan-Chen/SCMP/MCMP, NO free-surface, NO density ratio (rho spectator)
boundary: link bounce-back Ladd for walls/colloids (src/wall.c:971, src/bbl.c:221); Navier slip (src/wall.c:1129); Dirichlet inflow-rhou/outflow-rhou, no Zou-He (src/lb_bc_inflow_rhou.c:478); wetting solid gradients (src/gradient_2d_ternary_solid.c:79); lubrication correction (src/lubrication.h:39)
gpu: CUDA/HIP via target/ layer (target/target.h, target/target_cuda.c); one-kernel-per-step with tdpDeviceSynchronize; AoS/SoA/AoSoA address models (src/memory.h); MPI Px*Py*Pz halo post/wait split (src/lb_data.c:754,1318,1431)
summary: Free-energy LBM for complex fluids (colloids/LC/electro/surfactant) in C with CUDA/HIP+MPI; no Shan-Chen, no density ratio, no free surface, no AD/codegen. Worth absorbing: free-energy vtable chain, face-flux conserved Cahn-Hilliard, and the tolerance-based numerical regression surface.
[/OSS-INV]
```

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: GAP-LBM-MCMP-POISEUILLE
tag: free-energy-pressure-tensor-body-force
method: 以 F=1/2A phi^2+1/4B phi^4+1/2 kappa(grad phi)^2 的自由能路线替代 Shan-Chen：压强张量 P_ab 直接进碰撞平衡应力，界面力以 -phi grad mu 加到 hydro->force，再用 Guo 速度移位入碰撞；两组分分层 Poiseuille 可照此搭（非伪势路线）。
files: `src/symmetric.c:327`, `src/phi_grad_mu.c:40`, `src/hydro.c:667`
conventions: sigma=sqrt(-8 kappa A^3/(9 B^2)), xi=sqrt(-2 kappa/A); 重力/体力经 hydro->force; 单位: lattice units, cs2=1/3
transfer: reference
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: MCMP 高密度比
tag: no-density-ratio-available
method: 本仓无密度比机制（rho 为旁观量，三元模型注释明示），只可借用其自由能->mu->力链路与sigma/xi 标度关系来反解界面参数；高密度比需自建变密度平衡态。
files: `src/fe_ternary.c:12`, `src/symmetric.c:271`
conventions: rho0 为物理参数 (src/physics.c)，phi 不映射到 rho_liq/rho_gas
transfer: adapt
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: 自由面（free surface）
tag: map-based-empty-site
method: 无自由面模型；可借鉴 map 状态 + 面通量无穿透（advflux_cs_no_normal_flux）把气相实现为空单元并在界面处施加质量/动量条件。
files: `src/cahn_hilliard.c:193`, `src/map.h:26`
conventions: map status = MAP_FLUID/MAP_SOLID; 面通量在固体面强制零法向
transfer: reference
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: 相场 Allen-Cahn
tag: cahn-hilliard-only
method: 只有守恒 Cahn-Hilliard（面通量 M grad mu，含守恒漂移补偿 PHI_CONSERVE_*）+ 三元 Semprebon 泛函；Allen-Cahn 非守恒方程需自研，但 fe_vt_t 提供了加后端的扩展点。
files: `src/free_energy.h:45`, `src/fe_ternary.c:6`
conventions: mobility M 为常数参数; phi in [-1,1]; 守恒修正开关 PHI_CONSERVE_DEFAULT/COMPENSATED/GLOBAL_SUBTRACT
transfer: adapt
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: GPU 融合 kernel
tag: one-kernel-per-step
method: 每步一个 __global__ 核并按 tdpDeviceSynchronize 串行：collision、propagation、每个力项各一核，融合空间明确；kernel_3d_t 提供统一循环域，__restrict__ 标注用于合并。
files: `target/target_cuda.c:40`, `src/propagation.c:157`, `src/kernel_3d.h:39`
conventions: nblk/ntpb 由 kernel_3d_launch_param 按 kiterations 计算; 默认 threads/block 见 tdp_get_max_threads
transfer: reference
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: 分布式多卡
tag: mpi-halo-post-wait-split
method: MPI 三维域分解，halo 交换拆成 post(非阻塞 isend/irecv + 预建通信图) 与 wait 两段，中间可插本地计算隐藏延迟；胶体跨进程字节级 MPI_Irecv/Isend + 分段规约。
files: `src/lb_data.c:1431`, `src/memory.h:271`, `config/epcc-cirrus-nvcc-openmpi.mk:5`
conventions: nhalo=1 (src/coords.c:66); 通信图 lb_graph_halo_send/recv_create
transfer: adapt
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: 稀疏/多孔
tag: map-solid-with-navier-slip
method: 任意固体几何 = per-site map 状态（fluid/wall/colloid links）+ link bounce-back，可选 Navier 滑移壁；多孔介质即 map 打洞 + 滑移壁。
files: `src/wall.c:1129`, `src/wall.c:971`
conventions: 滑移长度 wall->param->slip.*; map 由 map_init/map_options 从输入文件读
transfer: reference
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: 案例组织
tag: json-input-case-directory
method: 输入参数用 JSON（util_cJSON）读入，案例目录自包含输入+期望输出，构建配置外置 config/*.mk；目录即证据单元。
files: `tests/regression/d3q19-short/serial-actv-s01.inp:3`, `src/util_cJSON.h:2`
conventions: cs/config: {Px,Py,Pz} 域分解; 每 case 一个 Makefile target
transfer: fits
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: 测试与验证面
tag: fp-domain-tolerance-diff
method: 浮点按域容差比对（tests/awk-fp-diff.sh 由 test-diff.sh 驱动），单元测试按 feature 一个 test_*.c；容差为回归容差而非物理基准。
files: `tests/awk-fp-diff.sh:5`, `tests/test-diff.sh:5`
conventions: 容差以相对/绝对阈值参数化; 回归输出含 per-quantity 参考值文件
transfer: fits
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: CI
tag: per-lattice-matrix-ci
method: regression.yml 按格子分 job 并行构建+跑回归，codecov 出覆盖率，codeql 静态扫描；构建配置用 config/github-gcc.mk 覆盖。
files: `.github/workflows/regression.yml:31`, `.github/workflows/codecov.yml:14`
conventions: cp config/github-gcc.mk config.mk; sed 切换 D3Q19->D2Q9 等目标格子
transfer: reference
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: NEW: 涨落 LBM（ghost-mode 噪声源）
tag: fluctuating-lbm-ghost-modes
method: 水动力模态确定性演化、ghost 模态按 1/tau 与 kT 给方差受热噪声；可向 CH 注入随机通量。提供「热涨落驱动的界面毛细波/Brownian」验证面。
files: `src/collision.c:487`, `src/collision.c:502`
conventions: kt 为涨落强度参数; 噪声只在 MAP_FLUID 站点生成以保证可复现
transfer: reference
[/OSS-SOL]

```text
[OSS-SOL]
repo: ludwig-cf/ludwig
slug: NEW: 粒子-流体近场润滑修正（lubrication）
tag: analytic-lubrication-correction
method: BBL 之外叠解析近场润滑力（球-球径向/切向、粒子-壁面亏空），截断距离 rch 控制启用范围，力/力矩并入刚体总量。低分辨率下把阻力算对的关键。
files: `src/lubrication.h:25`, `src/bbl.c:62`
conventions: rch 截断（lubrication_rch_set）; eta 取自流体动力粘度; 力单位 lattice
transfer: adapt
[/OSS-SOL]

