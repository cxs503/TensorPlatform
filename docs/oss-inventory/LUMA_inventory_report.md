# 【盘点】LUMA — 曼彻斯特大学 LBM-IBM-FEM 教研求解器

**任务 ID**: m8303 ｜ **锚点**: FEAT-OSS-INVENTORY-LBM ｜ **tier 3**
**盘点对象**: `cfdemons/LUMA`（The University of Manchester）
**读码基线**: 本地 clone（`git clone --filter=blob:none https://github.com/cfdemons/LUMA.git`），HEAD `5a5aa19d7205`（末次提交 2023-08-03），`LUMA_VERSION "1.7.12"`（`LUMA/inc/definitions.h:24`；release notes 停在 1.7.12，`LUMA/docs/release_notes.luma:1`）
**盘点方式**: 代码本体逐文件核读（20,069 行 `src/*.cpp` + 4,690 行 `inc/*.h`，见 §七生成表）；文档仅用于版本/变更史交叉验证
**统计与作图**: `docs/oss-inventory/scripts/luma_extract.py`（数字、表、图全部由脚本从 clone 现场产出，零手抄）
**日期**: 2026-11-09 ｜ **证据分级**: 结论全部 **source-confirmed**（每条带 `文件:行`）；未运行求解器（见「六要素自检」第 6 条）

---

## 一、概况

| 维度 | 内容 | 证据 |
|---|---|---|
| **语言** | C++（C++0x，微软 `stdafx` 预编译头风格），约 2.47 万行 `src`+`inc` | `LUMA/makefile:6`（`-std=c++0x`）、`LUMA/inc/stdafx.h:83` |
| **许可证** | **Apache-2.0** + `NOTICE`（版权由曼彻斯特大学代管，署名 Harwood / O'Connor / Camps Santasmasas，2017-12-07） | `LICENSE:1-3`、`NOTICE:1-8` |
| **许可证对我们的可用性** | ✅ **可吸收代码**（非仅参考）：Apache-2.0 允许商用/修改/再分发，义务仅为保留版权与 NOTICE、标注修改；与我们 MIT/Apache 栈兼容，**无 copyleft 污染风险**（对比同门 GPL 的 LIFE，姊妹盘点 #9563）。建议 TensorLBM 引入时把 NOTICE 登进 `THIRD_PARTY` | `LICENSE:1-3`、`NOTICE:1-8` |
| **活跃度** | **停滞/教研收尾**：66 commits、4 位作者（Adrian Harwood×39、Ian Hinder×20）、末次提交 2023-08-03（一次小修 merge），功能发布止于 v1.7.12（2021-06）；**无 CI**（0 个 workflow） | 生成表 TABLE-FACTS |
| **定位** | **2D/3D LBM + 浸没边界（IBM/BFL/反弹）+ 有限元（FEM）流固耦合求解器**，特色是**多级重叠网格加密（2:1 + 时间子循环）** 与 **MPI 域分解/智能负载均衡**；面向湍流/传热与柔性体 FSI 教研 | `LUMA/src/main_lbm.cpp:441`；§二/§五 |

**一句话定位**：LUMA 是曼彻斯特大学 2017–2023 维护的「宏配置 + 单内核 + 重叠网格加密 + 直接力 IBM + Newmark FEM」C++ LBM-IB-FE 教研代码，**单相、无 GPU、无多相**；其**加密界面算子、IBM 守恒校正（RKPM ε）、正则化进出口边界**三块成熟度明显高于多数开源 LBM，值得按 Apache-2.0 吸收。

---

## 二、方法谱系

### 2.1 格子（lattice）

| 格子 | 实现位置 | 组织方式 |
|---|---|---|
| **D2Q9**（`L_DIMS==2` 唯一） | `LUMA/inc/definitions.h:311`（`L_NUM_VELS 9`）；速度表 `LUMA/src/stdafx.cpp:114-124`；权重 `stdafx.cpp:147-149` | 2D 只有 9 速，无其它 2D 格子 |
| **D3Q19**（3D 默认） | `definitions.h:305`；`stdafx.cpp:81-101`；权重 `stdafx.cpp:140-142` | 非 KBC 时启用 |
| **D3Q27**（仅 KBC 时） | `definitions.h:302-308`；速度表 `stdafx.cpp:31-77`（同时给 `c[3][27]` 与 `c_opt[27][3]`）；权重 `stdafx.cpp:132-135` | KBC-N4 需完整 27 速矩集，与碰撞算子**强绑定** |
| 声速 | `cs = 1/sqrt(3)` | `stdafx.cpp:153` |

**组织特点（关键）**
- 格子/维度/BC/碰撞全部是**编译期宏**（`L_DIMS`、`L_USE_KBC_COLLISION`、`L_WALL_*`、`L_NUM_LEVELS`…，`definitions.h:83-263`）。换算例＝改头文件重编译，**没有运行时格子注册/多态**（对比 XLB 的 `register_backend`）。
- 存在**两套速度表索引约定**：老内核 `c[dir][v]`（`stdafx.cpp:31`，列主），优化内核 `c_opt[v][dir]`（`stdafx.cpp:40`，行主）。语义相反，是移植最易静默出错的点（见 R3）。
- 单内核 + 编译期展开：`for (v=0; v<L_NUM_VELS; ++v)` 全仓统一，`v` 最内层且紧跟站点索引（`f[v + id*L_NUM_VELS]`）。

### 2.2 碰撞（collision）

| 模型 | 实现位置 | 说明 |
|---|---|---|
| **BGK（默认）** | `LUMA/src/GridObj_ops_lbm_optimised.cpp:765-789`（`_LBM_collide_opt`） | `fNew[v] += ω(feq[v] − fNew[v]) + force_i[v]`——**累加形式**，因为此刻 `fNew` 已装入「已迁移」值：碰撞与迁移在**同一趟循环**融合（拉取式 streaming + 就地碰撞），`f.swap(fNew)` 在 `:200` |
| **BGK + Smagorinsky LES** | `:717-757`（`_LBM_smag`）；开关 `definitions.h:126`；`Cs=0.3` `definitions.h:127` | 由非平衡应力内积 `Q=sqrt(2 S_ij S_ij)` 求 `τ_t`（`:754`），返回**逐点** `ω_s`（`:770`） |
| **KBC（KBC-N4 3D / KBC-D 2D）** | `GridObj_ops_lbm_optimised.cpp:1122-1300`（优化版）；`LUMA/src/GridObj_ops_lbm.cpp:44-260`（DEPRECATED） | ①构造矩/平衡矩（3D 13 矩 `:1138-1142`，2D 3 矩）→ ②`fneq` 分解为熵部 `ds` 与镜像部 `dh` → ③熵线求 `γ`（`:1285-1292`，`β⁻¹=2/ω`，`γ=β⁻¹−(2−β⁻¹)(ds·dh)/(dh·dh)`）→ ④`f* = f − β(2ds + γdh) + force_i`（`:1295-1300`） |
| **MRT / TRT / 累积量 / 正则化碰撞** | **无** | 全仓 `MRT|TRT|cumulant|two-relax|entropic` 无实现；release notes 明写 **KBC 替换 MRT 上位**（`LUMA/docs/release_notes.luma:245`）。正则化只用于**边界**（§三） |

**组织特点**：碰撞算子以 `#ifdef L_USE_KBC_COLLISION` 二选一编译（`:147-151`），KBC 与 BGK 共用「迁移即碰撞」结构；Smagorinsky **正交叠加**（改 ω 不改公式）——可移植的正确取向。

### 2.3 力项（forcing）

| 项 | 实现位置 | 说明 |
|---|---|---|
| **Guo 2002 二阶力格式** | `GridObj_ops_lbm_optimised.cpp:928-989`（`_LBM_forceGrid_opt`） | 式注 `:937-950`；`λ_i=(1−ω/2)·w_i/c_s²`（`:975`），`β_i=(c_i·u)/c_s²`（`:978-980`），`F_i=λ_iΣ_d F_d[(c_di(1+β_i)−u_d)]`（`:982-988`） |
| **力项两处注入** | ①碰撞后进 f：`:785`（BGK）、`:1300`（KBC）；②动量半步：`:830-834` | 与 Guo 理论一致 |
| **重力/体积力** | `:1330-1336`（`force_xyz=ρ·g·refinement_ratio`）；开关 `definitions.h:113-116` | 统一「逐点笛卡尔力场」，IBM 走同一通道 |
| **IBM 恢复力** | `LUMA/src/ObjectManager_ops_ibm.cpp:672-676`（铺展到支撑点，取负号进 `force_xyz`） | 单相 IBM 与体积力可叠加 |
| **shifted-velocity / EDAC / exact-difference / RK** | **无** | — |

**⚠ 源码级缺陷（本单实测发现）**：优化内核动量半步的 3D 分支把 z 分量加到 X 动量上：

```cpp
// LUMA/src/GridObj_ops_lbm_optimised.cpp:830-834
rhouX_temp += 0.5 * force_xyz[0 + id * L_DIMS];
rhouY_temp += 0.5 * force_xyz[1 + id * L_DIMS];
#if (L_DIMS == 3)
rhouX_temp += 0.5 * force_xyz[2 + id * L_DIMS];   // ← 应为 rhouZ_temp
#endif
```

非优化版正确（`LUMA/src/GridObj_ops_lbm.cpp:325-328` 用 `fuz_temp`）。**后果**：任何 3D + 力（`L_IBM_ON`/`L_GRAVITY_ON`）算例的 ux 被污染、uz 缺半步修正 → 动量不平衡；2D 不受影响（分支不编译）。这是「抄代码前必须先核」的直接证据。

---

## 三、边界处理

| 边界 | 型式 | 实现位置 | 关键细节 |
|---|---|---|---|
| **静止墙** | **半步反弹 HWBB** | `GridObj_ops_lbm_optimised.cpp:237-241` | 判据是**源点**类型 `src_type_local==eSolid`；固体由 `LatTyp` 标签给定（`LUMA/inc/Enumerations.h:103-115`）。壁面在连线中点，二阶精度 |
| **壁面力** | **动量交换 MEM** | `LUMA/src/ObjectManager.cpp:126-160`（HWBB 用 `2·c_d·f_toward`，注释 `:137-152`）；BFL 标记点动量交换 `:180-200`；每步清零 `:196` | 逐点逐方向累加，`isOnRecvLayer` 跳过 halo 防重复计数（`:105-110`） |
| **运动/曲面墙** | **插值反弹 BFL（Bouzidi–Firdaouss–Lallemand q 法）** | `:1012-1110`（`_LBM_applyBFL_opt`） | 每标记点每方向存墙距比 q（`LUMA/inc/BFLBody.h:69` 的 `Q`；`LUMA/src/BFLBody.cpp:391-396` 取最小 q）。两支：墙更近当前点 `f*=(1−2q)(f̄₊−f̄)+f̄`（`:1079-1085`）；墙更近源点 `f*=(1−2q)[(f−f̄)/(2−2q)]+f̄`（`:1097-1103`）；两条 BFL 同时命中取最近墙（`:1014-1016`） |
| **滑移墙** | **镜面反射** | `:527-590`；方向映射 `LUMA/src/GridUtils.cpp:26-60`（`getReflect`，`:488`） | 按法向分量符号改写；法向由 `isWithinDomainWall` 推断，非壁区调用 `L_ERROR` 退出（`:585-586`） |
| **速度入口/压力出口** | **正则化边界（Latt & Chopard），Zou-He 已移除** | `:313-500`；开关 `definitions.h:243`；移除证据 `docs/release_notes.luma:245` | 按法向拆 `f⁺`/`f⁰`（`:379-420`）→ 求非平衡应力 `S_xx,S_xy…`（`:420-500`）→ `f=feq+(w_i/2c_s⁴)[…S…]` 重构（`:495-500`）；压力 BC 用 `ρ=L_RHOIN+Δp`（`:340-343`） |
| **角点/棱边** | 沿法向外推一阶 | `:348-372`（`_LBM_updateAndExtrapolate`） | 速度 BC 角点外推密度；**压力 BC 角点显式不支持**（`:356-359` `L_ERROR`）；D3Q27 与正则化 BC **互斥**（`LUMA/src/GridObj_init_grids.cpp:267-270`） |
| **出流** | 二阶外推 | `:243-248`（取上游 2 格） | 需标 `eExtrapolateRight`（`Enumerations.h:115`） |
| **周期** | 索引取模（默认）+ MPI 周期拓扑 | `:216-221`；`LUMA/src/MpiManager.cpp:107-136`（`MPI_Cart_create(..., MPI_periodic, ...)`） | 开周期＝把该壁设 `eFluid`（`definitions.h:235-240`） |
| **曲率修正 / ghost-fluid / 部分滑移** | **无** | `curvature|curvilinear` 0 命中 | IBM 直接力不含曲面法向重构，精度靠 Peskin 核 + ε 校正 |
| **浸没边界 IBM** | **直接力 + Peskin 3 点核 + RKPM ε 守恒 + 强耦合子迭代** | 见下 | 本仓最值得吸收的一块 |

**IBM 全链路（`LUMA/src/ObjectManager_ops_ibm.cpp`）**
1. 核函数：Peskin 3 点（Roma–Peskin）`ibm_deltaKernel` `:255-272`，支撑域 1.5Δx，`dilation` 为逐点光滑尺度；
2. 支撑点搜索：`:279-460`（`ibm_findSupport`），并用 `estimated_position` 预判目标 rank（`:462-494`）；
3. 插值 E→L：`:498-596`，三维 δ 乘积 `:479-482`，得 `interpMom`/`interpRho`；
4. 恢复力（直接力）：`:598-616`，`F=2(ρu_interp − ρ_m u_marker)/Δt`（Δt=1，故系数 2）；
5. 铺展 L→E：`:621-694`，`force_xyz(support) −= δ·F·ε·ds^(D−1)`；
6. **ε 守恒权重（RKPM）**：`:773-880`，注释引 **Pinelli et al. 2010 JCP**（`:812-815`），解 `A·ε=1` 使插值/铺展严格守恒；多体可选 `L_UNIVERSAL_EPSILON_CALC` 全局统一 ε（`definitions.h:221`）；
7. 强耦合子迭代：`:111-140`，`res<1e-4` 或 `it=10`（`:116-117`）；FEM Newmark-β（`L_NB_ALPHA=0.25, L_NB_DELTA=0.5, L_RELAX=0.5`，`definitions.h:228-231`；`LUMA/src/FEMBody.cpp:160-215, 236-270`），线性系统用 LAPACK（`FEMBody.cpp:208-209`）。

---

## 四、多相 / 多组元（重点）

**结论：本仓完全没有多相/多组元能力。** 不是「弱」，是「零实现」——因此本节的正确写法是无（不编造）。

**证据（可机器复验）**：跨 `LUMA/src`、`LUMA/inc`、`LUMA/docs`、`LUMA/cases` 做同义词全覆盖扫描
`multiphase|multi-phase|shan.?chen|free.?energy|phase.?field|allen.?cahn|colour.?gradient|color.?gradient|scmp|mcmp|surface.?tension|spurious` → **命中 0 行**（生成表 TABLE-SWEEPS `multiphase_any=0`；复验见附录 A）。

结构性原因（源码确认，「架构上不存在」而非「没写」）：

1. **状态量只有一套**：`f / feq / fNew / rho / u` 各一份（`LUMA/inc/GridObj.h:80-103`），**无组分维、无序参量、无相场变量**。MCMP 需 `f_σ`、相场需 `φ`、自由面需液体积分数 `F`——一个都不存在。
2. **密度语义单组分**：`rho=Σ_v f_v`（`GridObj_ops_lbm_optimised.cpp:810-820`），`u=ρu/ρ`（`:836-841`）。伪势/自由能需要在**同一站点**区分两相密度与有效密度 `ψ(ρ)`，本结构无挂点。
3. **力项通道是通用入口但不是多相**：`force_xyz(i,j,k,d)`（`GridObj.h:86`）理论上可注入 Shan-Chen 力（`F=−Gψ(x)Σw_iψ(x+c_i)c_i`），但缺 `ψ(ρ)`、缺第二组分、缺界面张力与 spurious-current 抑制；且 `_LBM_forceGrid_opt` 的 `(1−ω/2)` Guo 权重与 SC 常用归一约定不同，需改系数（`:975`）。**「可注入力」≠「有多相」**。
4. **spurious current / 密度比 / 界面锐化**：无任何专门处理（无滤波、无高阶各向同性梯度、无 Lishchuk 颜色梯度、无 free-surface 质量重分布）。
5. **潜在复用点（诚实标注为「骨架」）**：`IBBody`/`FEMBody` 的「标记点-支撑点-铺展」基础设施与「相界面标记点」同构，但改成界面张力铺展需重写核函数与守恒权重，**属改造级，非复用**。

**对我们的直接含义**：`GAP-LBM-MCMP-POISEUILLE`、`MCMP 高密度比`、`自由面`、`相场 Allen-Cahn` 四项在 LUMA **挂不上**；替代候选应从 listLBM（SC-MCMP，#9558）、MPLBM-UT（#9553）或 Palabos/LBfoam 系补齐。LUMA 价值集中于**单相 FSI + 加密 + 边界**。

---## 五、GPU / 并行

| 维度 | 结论 | 证据 |
|---|---|---|
| **GPU** | **无**：`cuda|hip|__global__|kokkos|sycl|openacc` **0 命中**；无设备内存/kernel 启动 | 生成表 `gpu_cuda=0`；`LUMA/makefile:6-9` 仅 `mpicxx -O3 -fopenmp` |
| **共享内存** | **OpenMP 实验性**：`#pragma omp parallel for` 挂外层 `i` 循环，默认**关闭**（`definitions.h:86` 注释），开启时运行时告警 | `GridObj_ops_lbm_optimised.cpp:69-71`；`main_lbm.cpp:407-409` |
| **数据布局** | **站点内方向连续（per-site Q-vector）**：`f[v + id*L_NUM_VELS]`、`LatTyp[id]`、`id=k+j*K_lim+i*K_lim*M_lim`（z 最快）；双缓冲 `f`/`fNew` + 每步 `swap`，无别名 | `:79`、`:132`、`:200`；`GridObj.h:80-81`、`:103` |
| **内存合并** | 沿 z 连续→halo/streaming 局部性好；但每站点 Q 个 double 连续意味着**跨方向向量化**（非跨站点），且 `_LBM_stream_opt` 每方向查 `LatTyp[src_id]` 并走长分支链（BFL→slip→solid→外推→速度 BC→加密 explode/coalesce→常规），**分支密集、无 SIMD intrinsics** → x86 AVX 利用率受限（源码判断，未 profile） | `:206-296` |
| **命名陷阱** | `_LBM_coalesce_opt`/`_LBM_explode_opt` **不是** GPU 内存合并，而是**加密界面算子** | `:285-290`、`:598-700` |
| **MPI 域分解** | `MPI_Cart_create` 三维周期拓扑（`MpiManager.cpp:107-136`）；均匀分解 `mpi_uniformDecompose`（`:1220`）与 **smart decomposition**（`mpi_smartDecompose` `:1457`，按活跃单元数评估 `LoadImbalanceData` 选核心组合 `:1400`、`:1739`） | 同左 |
| **通信** | 非阻塞 `MPI_Isend/Irecv` + `MPI_Waitall`（`:718`、`:789`），每方向独立缓冲；先协商消息尺寸（`mpi_buffer_size` `:822`，配 `Mpi_buffer_size_send/recv.cpp`）再 pack/unpack（`Mpi_buffer_pack.cpp`）；每层网格时间步**末尾**发起（`:197-202`） | 同左 |
| **通信隐藏** | **无显式重叠**（`Waitall` 同步等待）；唯一「隐藏」是**网格层级流水**（细网格先 2 子循环再粗网格，`:36-47`）。未做异步进度/pipeline | `:36-47` |
| **多级加密** | 2:1 重叠块、每层 2 次子循环；界面标签 `eTransitionToCoarser/Finer` + `refinement_ratio` 缩放体力 | `:36-47`、`:270-290`、`:598-620`；`GridObj_init_grids.cpp:399-441` |
| **稀疏/多孔** | **无**（`porous|sparse|permeab` 0 命中）；仅 `type_local==eSolid → continue` 朴素跳过（`:88-93`），无链表/活化列表 | 生成表 `porous_sparse=0` |

---

## 六、可微 / 代码生成

| 维度 | 结论 | 证据 |
|---|---|---|
| **自动微分友好度** | **零**：纯命令式 C++ + 宏，无可微后端/伴随/tape；`autograd|tapenade|adjoint|symbolic|codegen` 0 命中 | 生成表 `autodiff_codegen=0` |
| **符号推导** | **无**（`LUMA/inc/Matrix.h:18` 仅为 2D 容器模板） | 同左 |
| **kernel 生成** | **无 JIT/代码生成**。所谓「生成」只有：①编译期宏配置（`definitions.h` 全篇）；②prefab 体构造器（圆/球/矩形/板/丝阵，`BFLBody.h:46-58`）与 `geometry.config` DSL（`LUMA/input/geometry.config:11-30`） | 同左 |
| **对我们的含义** | 若 TensorLBM 要可微，LUMA 只提供**离散公式参考**（Guo 源项、KBC 熵线、正则化 BC 重构式、RKPM ε），不提供可微实现 | — |

---

## 七、与我们功能点的映射

| 我们的功能点 | grep 证据(键=命中行) | 判定 |
|---|---|---|
| GAP-LBM-MCMP-POISEUILLE | `multiphase_any`=0 | 本仓无 |
| NEW:MCMP 高密度比 | `multiphase_any`=0 | 本仓无 |
| NEW:自由面 LBM | `multiphase_any`=0 | 本仓无 |
| NEW:相场 Allen-Cahn | `multiphase_any`=0 | 本仓无 |
| GPU 融合 kernel | `gpu_cuda`=0 | 本仓无 |
| NEW:分布式多卡 (MPI+halo+负载均衡) | `mpi_halo_nb`=25; `mpi_periodic_cart`=1 | 可实现/可复用 |
| 稀疏/多孔 | `porous_sparse`=0 | 本仓无 |
| 案例组织 | `case_defs_files`=10 | 可实现/可复用 |
| 测试与验证面 | `testsuite_cases`=9 | 可实现/可复用 |
| CI | `ci_files`=0 | 本仓无 |


**判读**
- **挂得上（可吸收/可适配）**：分布式 MPI（halo + 负载均衡）、案例组织、测试与验证面；另有三个高价值项不在给定 slug 清单，提 NEW：**重叠网格加密界面算子**、**IBM RKPM-ε 直接力强耦合**、**正则化进出口边界 + BFL 插值反弹**（见 §八）。
- **挂不上（本仓无）**：`GAP-LBM-MCMP-POISEUILLE`、`MCMP 高密度比`、`自由面`、`相场 Allen-Cahn`、`GPU 融合 kernel`、`稀疏/多孔`、`CI`。

**规模与证据密度（脚本现场统计）**

| 文件 | 行数 |
|---|---:|
| `LUMA/src/MpiManager.cpp` | 1865 |
| `LUMA/src/GridObj_ops_lbm_optimised.cpp` | 1436 |
| `LUMA/src/GridObj_init_grids.cpp` | 1379 |
| `LUMA/src/GridObj_ops_io.cpp` | 1375 |
| `LUMA/src/ObjectManager_ops_ibm.cpp` | 1142 |
| `LUMA/src/FEMBody.cpp` | 439 |
| `LUMA/src/ObjectManager.cpp` | 416 |
| `LUMA/inc/definitions.h` | 352 |
| `LUMA/src/GridObj_ops_lbm.cpp` | 341 |
| `LUMA/inc/Enumerations.h` | 183 |
| **合计 (src/*.cpp)** | **20069** |
| **合计 (inc/*.h)** | **4690** |


| 关键词扫描 | 命中行数 |
|---|---:|
| `multiphase_any` | 0 |
| `gpu_cuda` | 0 |
| `autodiff_codegen` | 0 |
| `kbc` | 17 |
| `bgk` | 9 |
| `smagorinsky` | 8 |
| `guo_forcing` | 5 |
| `regularised_bc` | 17 |
| `zou_he` | 1 |
| `halfway_bounceback` | 29 |
| `interpolated_bounceback_bfl` | 159 |
| `slip_specular` | 11 |
| `ibm_deltakernel` | 10 |
| `ibm_epsilon_rkpm` | 5 |
| `mpi_halo_nb` | 25 |
| `mpi_periodic_cart` | 1 |
| `openmp` | 1 |
| `grid_refinement` | 122 |
| `fem_newmark` | 38 |
| `openmp_or_mpi_any` | 1404 |
| `porous_sparse` | 0 |


| 项 | 值 |
|---|---|
| HEAD | `5a5aa19d7205` |
| 末次提交 | 2023-08-03 |
| 提交数 | 66 |
| LUMA_VERSION | 1.7.12 |
| 许可 | Apache License Version 2.0, January 2004 http://www.apache.org/licenses/ (NOTICE=True) |
| 主要作者 | Adrian Harwood×39, Ian Hinder×20, yangzhou-10×2, Marta Camps Santasmasas×2, martacamps×1 |
| CI workflows | 0 |
| 用例定义头 | 10 |
| 回归基线 case 目录 | 9 |


---

## 八、可吸收 Top3 + 风险

### Top1｜多级重叠网格加密：explode/coalesce 界面算子 + 2 次子循环（transfer: **adapt**）
TensorLBM 若做局部加密/多块，最稀缺的是**界面质量控制**。LUMA 用「重叠网格 + 标签化界面 + 拉取式填充」，收敛快、实现简单：
- 子循环：`GridObj_ops_lbm_optimised.cpp:36-47`（细网格每层跑 2 次再跑本层）；
- 细→粗（coalesce，平均）：`:598-700`；粗→细（explode，填充）：`:270-290` 调用点 + `_LBM_explode_opt`；
- 标签/几何：`eRefined/eTransitionToCoarser/eTransitionToFiner`（`Enumerations.h` 的 `eType`）、`GridObj_init_grids.cpp:399-441`、τ 由粗层按 ratio=2 换算（`:526`）；
- 界面处不做碰撞于 `eTransitionToCoarser`（`:145-147`）。

### Top2｜IBM 直接力 + RKPM ε 守恒校正 + 强耦合子迭代（transfer: **adapt**）
- 核：`ObjectManager_ops_ibm.cpp:255-272`（Peskin 3 点，支撑 1.5Δx，`dilation` 逐点可调）；
- 插值/铺展：`:498-596` / `:621-694`（体积尺度 `volWidth=ε`、`volDepth=ds`；跨 rank 在 `ObjectManager_ops_ibm_mpi.cpp`）；
- **ε 守恒校正**：`:773-880`（Pinelli 2010 RKPM，解 `A·ε=1`）——多数开源 IBM 缺这一步，直接决定插值/铺展力守恒；
- 强耦合：`:111-140`（`res<1e-4, maxit=10`）+ Newmark-β（`FEMBody.cpp:160-215`）+ `L_RELAX=0.5`。

### Top3｜正则化速度/压力边界（Latt & Chopard）+ 角点外推（transfer: **reference**）
- `GridObj_ops_lbm_optimised.cpp:313-500`：`f⁺/f⁰` 拆分 → 非平衡应力 → 重构；压力 BC 用 `ρ=L_RHOIN+Δp`；角点只外推密度、压力 BC 角点显式拒绝（`:356-359`）；与 D3Q27 互斥（`GridObj_init_grids.cpp:267-270`）。
- 价值：这是「去掉 Zou-He 后如何做稳定进出口」的成熟样板（release notes 记录了该路线转变），**角点/棱边退化处理**正是自研最易错处。

**附加可吸收（平台类，transfer: fits）**
- **案例组织**：`cases/testsuite/defs/definitions_000..009.h`（10 个编译期算例头）+ `input/geometry.config`（体定义 DSL，`:11-30`，后缀参数 + `NULL` 占位）+ `runTestSuite.sh`（按算例号分发输入、1 rank 与 8 rank 双跑）。
- **测试与验证面**：基线 `base/case000..009`（9 目录）+ **只 diff 一个 ASCII 转储** `io_lite.Lev0.Reg0.Rnk0.100.dat`（`runTestSuite.sh:29`、`:158-174`）→ 「弱断言回归台」原型：成本极低可移植，但只能抓崩溃/巨变，抓不到 3% 级精度退化；我们吸收时应升级为「逐量 + 相对误差阈值」。

### 风险（抄之前必须知道）

| # | 风险 | 影响 | 证据 |
|---|---|---|---|
| R1 | **3D 力项半步 bug**（`rhouX_temp += force_z/2`） | 直接搬 3D IBM/重力算例得错误 ux/uz；2D 无事 | `GridObj_ops_lbm_optimised.cpp:830-834` vs `GridObj_ops_lbm.cpp:325-328` |
| R2 | 配置全编译期宏，换算例须重编译 | 不能作运行时配置层，只能借思想不能借配置系统 | `definitions.h:83-263` |
| R3 | **两套速度表索引约定** `c[dir][v]` vs `c_opt[v][dir]` | 移植易静默错位（索引错不报错） | `stdafx.cpp:31` vs `:40`/`:81`/`:114` |
| R4 | 体数量限制：**BFL 与 BBB 各仅 1 个**；BFL 铺展「只考虑首个 BFL 体」 | 多体算例需先重构对象管理 | `LUMA/input/geometry.config:12-13`；`GridObj_ops_lbm_optimised.cpp` BFL 注释 |
| R5 | 停滞 + 无 CI + 断言面极窄（单文件 diff） | 不能当「可信基线」引其数值，只能当实现参考 | 生成表 `ci_files=0`；`runTestSuite.sh:29` |
| R6 | 重建成本：HDF5 + LAPACK + MPI + 旧式 C++0x，无 CI 脚本可复用 | 要跑其回归台需自建编译镜像 | `LUMA/makefile:16-28` |
| R7 | Apache-2.0 带 `NOTICE`（大学版权） | 吸收须保留 NOTICE/署名，衍生文件标注修改 | `NOTICE:1-8` |

---

## 六要素自检（论文级报告要求）

1. **案例介绍**：对象＝公开 LBM 求解器 `cfdemons/LUMA`（HEAD `5a5aa19d7205`，v1.7.12，Apache-2.0）；「几何/参数表」＝内核规模与配置宏表（§二/§七 生成表）。
2. **怎么做的**：`git clone --filter=blob:none` → 全树梳理（333 文件 / 60 个 C++ 源+头 / 20,069 行 src）→ 逐内核核读（碰撞/迁移/力/边界/IBM/MPI/加密）→ 22 条关键词脚本扫描（含否定验证）→ 生成映射表与图 → 与姊妹盘点（LIFE #9563，同门 GPL 版）交叉比对。
3. **计算结果（数字表，程序化注入）**：§七 三张表（LOC / 22 条关键词命中 / 映射判定）+ TABLE-FACTS，全部由 `luma_extract.py` 现场产出，可复验。
4. **结果图**：`artifacts/oss-inventory-LUMA/luma_capability_map.png`（左：十个功能点 REAL/PARTIAL/ABSENT；右：核心文件权重）。为方法覆盖率图而非场量云图——**本单为静态代码盘点，未运行求解器，无场量可画**（理由见第 6 条）。
5. **注意点**：①发现 3D 力项源码 bug（R1）——「能编译≠对」；②LUMA 多相是**零实现**而非弱实现，勿被「LBM-IBM-FEM 全能」印象误导；③`coalesce/explode` 与 GPU 无关；④配置全编译期，不可当运行时框架。
6. **误差判定（是否 <3%）**：**本单不适用数值误差判定**——未执行任何算例（原因：需 HDF5+MPI+LAPACK 环境，且仓内参考解仅为自身基线，无实验/解析基准可对标；本任务定位是「方法归集」而非「求解器验证」）。因此本报告**不声称**任何精度数字，也不声称 LUMA 满足 15% 精度线。可核验的是「结论↔源码」一致性：全部结论带 `文件:行`，关键「无」结论由 22 条关键词扫描（命中 0）机器复核；扫描已知局限＝同义词漏检（已用 12 个同义词覆盖多相语义，仍可能漏自造命名），故「无」结论限定为 **source-confirmed 但非 exhaustive**。

---

## 附录 A：复验命令

```bash
git clone --filter=blob:none https://github.com/cfdemons/LUMA.git /tmp/LUMA
grep -rn -iE 'multiphase|shan.?chen|free.?energy|phase.?field|allen.?cahn|colour.?gradient|scmp|mcmp|surface.?tension|spurious' \
     /tmp/LUMA/LUMA/src /tmp/LUMA/LUMA/inc /tmp/LUMA/LUMA/docs /tmp/LUMA/LUMA/cases | wc -l   # → 0（多相零实现）
grep -rn -iE 'cuda|hip|kokkos|sycl|openacc' /tmp/LUMA/LUMA/src /tmp/LUMA/LUMA/inc | wc -l      # → 0（无 GPU）
grep -n 'force_xyz\[2' /tmp/LUMA/LUMA/src/GridObj_ops_lbm_optimised.cpp                        # → :833（3D 力项 bug）
python3 docs/oss-inventory/scripts/luma_extract.py --repo /tmp/LUMA                             # 重生成表与图
```

---

## 守护线机器块

```
[OSS-INV]
repo: cfdemons/LUMA
lang: C++ (C++0x)
license: Apache-2.0 (with NOTICE, University of Manchester)
methods: D2Q9(stdafx.cpp:114-124; definitions.h:311); D3Q19(stdafx.cpp:81-101; definitions.h:305); D3Q27(仅KBC: stdafx.cpp:31-77; definitions.h:302-308); BGK+Smagorinsky LES(GridObj_ops_lbm_optimised.cpp:765-789, 717-757); KBC-N4/KBC-D(GridObj_ops_lbm_optimised.cpp:1122-1300); Guo2002力项(GridObj_ops_lbm_optimised.cpp:928-989; 注入:785/:1300; 动量半步:830-834); 无MRT/TRT/累积量/正则化碰撞
multiphase: 无（全仓同义词扫描0命中：shan-chen/free-energy/phase-field/colour-gradient/SCMP/MCMP/free-surface 均不存在；状态量仅单份 f/rho，GridObj.h:80-103）
boundary: 半步反弹HWBB(GridObj_ops_lbm_optimised.cpp:237-241); 插值反弹BFL/q法(:1012-1110; BFLBody.cpp:391-396); 镜面反射滑移(:527-590); 正则化速度/压力BC Latt&Chopard(:313-500; Zou-He已移除 release_notes.luma:245); 角点外推(:348-372); 二阶外推出流(:243-248); 周期索引取模(:216-221)+MPI周期拓扑(MpiManager.cpp:107-136); 动量交换力(ObjectManager.cpp:126-200); IBM直接力(Peskin3点核 ObjectManager_ops_ibm.cpp:255-272 + RKPM ε守恒 :773-880 + 子迭代 :111-140); 无曲率修正
gpu: 无(cuda/hip/kokkos/sycl/openacc 0命中); 仅MPI(Cart分区+均匀/智能负载均衡+Isend/Irecv/Waitall)+实验性OpenMP(默认关, definitions.h:86); 数据布局per-site Q-vector(f[v+id*Q], GridObj.h:80-81); 多级重叠加密2:1+时间子循环(GridObj_ops_lbm_optimised.cpp:36-47)
summary: LUMA 是曼彻斯特大学的单相 2D/3D LBM-IB-FE 教研求解器（Apache-2.0，66 commits，末次 2023-08，无 CI），成熟处集中在多级重叠网格加密界面算子、IBM 直接力+RKPM ε 守恒校正+强耦合子迭代、正则化进出口边界三块；多相/GPU/可微为零实现，且优化内核 3D 力项存在源码 bug（GridObj_ops_lbm_optimised.cpp:833 把 Fz/2 加进 ux）。
[/OSS-INV]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:重叠网格加密界面算子（explode/coalesce + 2:1 子循环）
tag: overlapping-grid-refinement-explode-coalesce
method: 每层细网格在一个粗时间步内跑 2 次子循环后再推进粗层；细→粗用 coalesce 把子网格单元平均成粗层 eTransitionToCoarser 值，粗→细用 explode 把父层 eTransitionToCoarser 的值填充到子网格 eRefined 邻域；界面通量不做碰撞（跳过 eTransitionToCoarser），界面两侧以 refinement_ratio 缩放体力与 τ（τ 由粗层按 ratio=2 换算）。
files: LUMA/src/GridObj_ops_lbm_optimised.cpp:36-47, LUMA/src/GridObj_ops_lbm_optimised.cpp:270-290, LUMA/src/GridObj_ops_lbm_optimised.cpp:598-620, LUMA/src/GridObj_ops_lbm_optimised.cpp:145-147, LUMA/src/GridObj_init_grids.cpp:399-441, LUMA/src/GridObj_init_grids.cpp:526, LUMA/inc/Enumerations.h:103-115
conventions: 加密比固定 2; 每粗步=2 细子循环; 标签 eRefined/eTransitionToCoarser/eTransitionToFiner/eFluid; 体力乘 refinement_ratio=1/2^level
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:IBM 直接力 + RKPM ε 守恒校正 + 强耦合子迭代
tag: ibm-direct-forcing-rkpm-epsilon
method: Peskin 3 点 δ 核（支撑 1.5Δx，逐标记点 dilation）插值 Euler 速度到 Lagrange 标记点；恢复力 F=2(ρ·u_interp − ρ_m·u_marker)（Δt=1）；再以同一 δ 核把 F·ε·ds^(D−1) 反向铺展到支撑点写入 force_xyz；ε 由解 A·ε=1（A_IJ=Σ δ(x_I−X_J)ΔV，Pinelli 2010 RKPM）求得以保证插值/铺展守恒；每步内置强耦合子迭代（重置 u=u_n、重算 IBM、残差<1e-4 或 10 次上限），FEM 侧 Newmark-β(α=0.25, δ=0.5)+0.5 松弛。
files: LUMA/src/ObjectManager_ops_ibm.cpp:255-272, LUMA/src/ObjectManager_ops_ibm.cpp:479-482, LUMA/src/ObjectManager_ops_ibm.cpp:498-596, LUMA/src/ObjectManager_ops_ibm.cpp:598-616, LUMA/src/ObjectManager_ops_ibm.cpp:621-694, LUMA/src/ObjectManager_ops_ibm.cpp:773-880, LUMA/src/ObjectManager_ops_ibm.cpp:111-140, LUMA/inc/definitions.h:228-231
conventions: δ 支撑 1.5Δx（3 点）; 体积尺度 volWidth=ε, volDepth=ds; 子迭代 TOL=1e-4, MAXIT=10; 可选 L_UNIVERSAL_EPSILON_CALC 全局统一 ε; 力系数 2 隐含 Δt=1
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:插值 bounce-back（BFL/q 法移动边界）
tag: interpolated-bounceback-bouzidi-q
method: 对与壁相交的链接按墙距比 q∈[0,1] 做二阶插值反弹：墙更近当前点用 f*=(1−2q)(f̄₊−f̄)+f̄（f̄ 为预迁移反向值，f̄₊ 为再远一格的预迁移值）；墙更近源点用 f*=(1−2q)(f−f̄)/(2−2q)+f̄（迁移后值）。q 由点云几何逐标记点逐方向预计算并取最小（最近墙优先），同一链接两条 BFL 同时命中时取最近；壁面力用该链接的动量交换累加到标记点。
files: LUMA/src/GridObj_ops_lbm_optimised.cpp:1012-1110, LUMA/src/GridObj_ops_lbm_optimised.cpp:1079-1085, LUMA/src/GridObj_ops_lbm_optimised.cpp:1097-1103, LUMA/src/BFLBody.cpp:391-396, LUMA/inc/BFLBody.h:69, LUMA/src/ObjectManager.cpp:180-200
conventions: q = 墙距/格距 ∈[0,1]; Q[v + L_NUM_VELS*marker] 每方向一份，-1 表示该链接无墙; 仅支持单个 BFL 体; 动量交换需跳过 recv layer 防重复
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:正则化速度/压力边界（Latt & Chopard）+ 角点外推
tag: regularised-velocity-pressure-bc-latt2008
method: 按法向把各速度方向分为流入侧 f⁺（含对角）与 f⁰，由已知 f 求非平衡应力张量分量 S_xx,S_yy,S_xy(,S_zz,S_xz,S_yz)，再用 f = feq + (w_i/2c_s⁴)[(c_x²−c_s²)S_xx+…+2c_xc_yS_xy] 重构未知方向；压力边界以 ρ=L_RHOIN+Δp 给定目标密度；角点/棱边方向数不足时沿法外推密度（一阶），压力边界在角点显式拒绝。
files: LUMA/src/GridObj_ops_lbm_optimised.cpp:313-500, LUMA/src/GridObj_ops_lbm_optimised.cpp:379-420, LUMA/src/GridObj_ops_lbm_optimised.cpp:495-500, LUMA/src/GridObj_ops_lbm_optimised.cpp:348-372, LUMA/src/GridObj_init_grids.cpp:267-270, LUMA/docs/release_notes.luma:245
conventions: cs=1/sqrt(3); 入流方向由 isWithinDomainWall 法向推断; L_VELOCITY_RAMP 无量纲爬升时间 2; D3Q27 与正则化 BC 互斥（编译期报错）; Zou-He 已被移除
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:KBC 碰撞（KBC-N4 3D / KBC-D 2D）
tag: kbc-n4-entropic-line
method: 构造 13 个（3D）或 3 个（2D）矩及其平衡值，把 fneq 分解为熵部 ds 与镜像部 dh；用熵线闭合求 γ = 2/ω − (2−2/ω)(ds·dh)/(dh·dh)；碰撞写作 f* = f − (ω/2)(2ds + γdh) + F_i。D3Q27 需 27 全套速度表，2D 用 D2Q9 的 KBC-D。
files: LUMA/src/GridObj_ops_lbm_optimised.cpp:1122-1300, LUMA/src/GridObj_ops_lbm_optimised.cpp:1285-1292, LUMA/src/GridObj_ops_lbm_optimised.cpp:1295-1300, LUMA/inc/definitions.h:302-308, LUMA/src/GridObj_ops_lbm.cpp:44-260
conventions: L_USE_KBC_COLLISION 编译期开关（默认关，用 BGK）; KBC 与 D3Q27 强绑定; bot_prod==0 时 γ=2（退化为正则化）; 力项仍走 Guo 的 force_i
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: 案例组织
tag: compile-time-case-defs-plus-geometry-dsl
method: 每个算例一个定义头 definitions_NNN.h（宏给维度/分辨率/雷诺数/壁面 BC 类型/加密层数/输出频率/体列表），运行时配套 input/ 目录下的 geometry.config（体定义表：FROM_FILE/FILAMENT_ARRAY/CIRCLE_SPHERE/SQUARE_CUBE/PLATE，含 LEV/REG/FLEX_RIGID/BC/DENSITY/YOUNG_MOD 等后缀参数，无关项填 NULL）与 inlet_profile.in / point-cloud .in；runTestSuite.sh 按算例号自动分发对应输入、选 1 或 8 rank，并把定义头拷入结果目录留痕。
files: LUMA/cases/testsuite/defs/definitions_000.h, LUMA/input/geometry.config:11-30, LUMA/cases/testsuite/scripts/runTestSuite.sh:64-120
conventions: 用例号 000-009（10 个定义头）; 后缀顺序固定、NULL 占位; LEV/REG 定位体所在网格层与区域; runTestSuite 中 case 0-5 用 1 rank、6-9 用 8 rank
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: 测试与验证面
tag: baseline-diff-regression-harness
method: 壳脚本逐算例编译+运行，把输出与 base/caseNNN 快照逐文件 diff——但实际只对单个 ASCII 转储 io_lite.Lev0.Reg0.Rnk0.100.dat 做 diff -q 判通过，失败时用 diff -y 生成并排差异日志；同时用 hdf5/restart/LiftDrag CSV 作为人工核查工件；同一物理用例分别以 1 rank 与 8 rank 运行以覆盖 MPI 路径。
files: LUMA/cases/testsuite/scripts/runTestSuite.sh:29, LUMA/cases/testsuite/scripts/runTestSuite.sh:158-174, LUMA/cases/testsuite/base/case001/io_lite.Lev0.Reg0.Rnk0.100.dat
conventions: 断言=单文件逐字节相等（非相对误差阈值）; 基线目录 9 个（case000..009，缺 case002）; 需 HDF5+MPI+LAPACK
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: cfdemons/LUMA
slug: NEW:分布式 MPI（Cart 分区 + 智能负载均衡 + halo 非阻塞交换）
tag: mpi-cart-smart-decompose-halo
method: MPI_Cart_create 建三维周期拓扑；载荷按活跃单元数评估（LoadImbalanceData）在均匀分解与 smart decomposition 间择优；每方向独立 send/recv 缓冲，先协商消息尺寸再 pack/unpack，步末用 MPI_Isend/Irecv 发起、Waitall 收齐；halo（recv layer）站点在力累加与 IBM 支撑点铺展时被跳过以防重复计数；跨 rank 标记点用 MarkerComm/SupportComm 结构交换。
files: LUMA/src/MpiManager.cpp:107-136, LUMA/src/MpiManager.cpp:1220, LUMA/src/MpiManager.cpp:1457, LUMA/src/MpiManager.cpp:1400, LUMA/src/MpiManager.cpp:631, LUMA/src/MpiManager.cpp:718, LUMA/src/MpiManager.cpp:789, LUMA/src/MpiManager.cpp:822, LUMA/src/ObjectManager.cpp:105-110
conventions: 三维周期笛卡尔拓扑; halo 厚度 1 层; 消息尺寸先协商（Mpi_buffer_size_send/recv.cpp）; MPI_DOUBLE、f 缓冲按方向分段; 无通信/计算重叠（Waitall 同步）
transfer: adapt
[/OSS-SOL]
```

---

**盘点结论**：LUMA 在**重叠网格加密界面算子、IBM 守恒校正、正则化边界**三块有可吸收的成熟实现（Apache-2.0，可搬代码，须保留 NOTICE）；在**多相、GPU、可微、稀疏/多孔**上为零实现，多相账本不可由本仓补齐；其 3D 力项源码 bug 提醒我们吸收任何第三方内核前必须先做「公式↔代码」逐行核对。