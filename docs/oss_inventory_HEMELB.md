# 【盘点】hemelb-codes/hemelb —— 大规模复杂几何并行 LBM（血流，tier 3）

> 任务锚点 FEAT-OSS-INVENTORY-LBM / m8301。读码途径：`git clone --depth 1 https://github.com/hemelb-codes/hemelb.git` 成功（本地 /tmp/hemelb_src，**未进我们的仓**）。
> 固定版本：`432d3386e32571487a1521f88a407db5dc9ca171`（2025-05-14，"Update path of ParMETIS source"）。
> 证据分级：本报告所有"有/无"结论均为 **source-confirmed**（直接读文件 + 全仓 grep），无一项来自文档推断；未运行仿真（只读盘点，未编译——见 §五/§九 局限）。

---

## 一、概况

| 项 | 内容 | 指针 |
|---|---|---|
| 主语言 | C++20（template + concepts + `if constexpr` 编译期多态）；辅以 Python 几何工具链（`geometry-tool/HlbGmyTool/`）与 CMake | `Code/lb/concepts.h:58`；`geometry-tool/` |
| 规模 | `Code/` 下 `*.cc/*.h` 合计 **67 894 行**（实测 `wc -l`），249 个 `.cc` + 309 个 `.h` + 153 个 `.py` | 实测（见 `artifacts/m8301/fig1_loc_by_subsystem.png`） |
| 许可证 | **LGPL-3.0** | `LICENSE:1-2`（"GNU LESSER GENERAL PUBLIC LICENSE / Version 3"） |
| 活跃度 | HEAD 2025-05-14；GitHub Actions 每周一 07:00 UTC 定时全量测试 + PR 触发 | `.github/workflows/main-app.yml:9-13` |
| 定位一句话 | 面向"十亿级格点、由医学影像体素化得到的复杂血管几何"的 **CPU-MPI 分布式 LBM**（D3Q15/19/27 + bounce-back 族 + 非牛顿血液流变 + 红细胞 IBM 悬浮），**不含 GPU 后端、不含多相** | `CMake/HemeLbOptions.cmake:54-68`；§四/§五 |

### 许可证对我们的可用性判断（重要）

- LGPL-3.0 允许**动态链接/作为库被调用**而不强制我们整体开源；但 (a) 对 LGPL 代码本身的修改须以 LGPL 发布，(b) 静态链接（LGPLv3 §4(d)）必须提供"可重新链接"的手段。
- 本仓的方法实现**高度模板化**（`Lattice<Q,CD,W,COMPRESSIBLE>` 与 kernel 类全在头文件里，例如 `Code/lb/lattices/D3Q15.h:15-56`、`Code/lb/kernels/LBGK.h:56-63`），**任何"抄头文件"的动作都会把我们自己的代码变成衍生作品** → 与我们闭源/自持许可策略冲突。
- **结论：只做 methods-level 参考（读公式、自己重写），不复制源码片段**。本报告因此只给"方法 + 文件:行 指针"，不给可直接粘贴的代码。凡标 `transfer: reference` 的条目，落地时必须在我方仓里重写并注明出处。

---

## 二、方法谱系

### 2.1 格子（速度集）

| 速度集 | 现实 | 指针 |
|---|---|---|
| D3Q15 | 15 向量 + 权重硬编码（`2/9, 1/9×6, 1/72×8`），`COMPRESSIBLE=true` | `Code/lb/lattices/D3Q15.h:15-56` |
| D3Q19 | 19 向量 + 权重（`1/3, 1/18×6, 1/36×12`） | `Code/lb/lattices/D3Q19.h:14-49` |
| D3Q27 | 27 向量 | `Code/lb/lattices/D3Q27.h:14-` |
| D3Q15i | **不可压变体**：复用 D3Q15 的 `VECTORS/EQMWEIGHTS`，只把 `COMPRESSIBLE` 置 false | `Code/lb/lattices/D3Q15i.h:10-19` |
| D2Q9 / 2D | **无**。CMake 选项枚举仅 `D3Q15 D3Q19 D3Q27 D3Q15i` | `CMake/HemeLbOptions.cmake:54-56` |

组织方式：单一类模板 `Lattice<NUMVECTORS, CD[], W[], COMPRESSIBLE>`（`Code/lb/lattices/Lattice.h`）提供全部公共算子——`CalculateFeq`、`CalculateDensityAndMomentum`、`CalculateDensityMomentumFEq`（`Lattice.h:471-483`，含力版本 `:489-507`）、`CalculateForceDistribution`、`CalculateVonMisesStress`（`Lattice.h:509-520`，壁面剪应力/Von Mises 直接从非平衡分布求）。速度集只提供数据表，算子全部继承 → **新加速度集只需填两张表**。

### 2.2 碰撞算子（kernel）

| 谱系 | 实现要点 | 指针 |
|---|---|---|
| BGK（单松弛） | `f_post = f + (f − f_eq)·ω`，`ω = GetOmega()`；HemeLB 约定 `omega = −1/tau` | `Code/lb/kernels/LBGK.h:56-63` |
| TRT（双松弛） | 按 `INVERSEDIRECTIONS` 配对拆对称/反对称部分；`Lambda = (τ₊−½)(τ₋−½)` **硬编码 3/16**（注释明写"为保证 HWBB 壁面位置正确"，TODO 未参数化） | `Code/lb/kernels/TRT.h:94-121`，`Lambda` 在 `:101` |
| MRT（D'Humières 基底） | `Ω = −M⁻¹·Ŝ·M(f−f_eq) = −Mᵀ(MMᵀ)⁻¹Ŝm_neq`；`(MMᵀ)⁻¹` 与 `Ŝ` 均为对角阵 | `Code/lb/kernels/MRT.h:26-39`（公式注释），碰撞 `:88-` |
| MRT 松弛谱 | D3Q19：`s1=1.19, s2=1.4, s4=1.2, s9=1/τ, s10=1.4, s13=1/τ, s16=1.98`（取 d'Humières 2002，黏性项仍用 1/τ） | `Code/lb/kernels/DHumieresD3Q19MRTBasis.cc:11-36` |
| MRT 基底范围 | **只有** D3Q15 / D3Q19 两套基底（`MomentBases.h` 只 include 这两个） | `Code/lb/kernels/MomentBases.h:9-10` |
| 熵格子 ELBM | 两个变体（Ansumali / Chik），共用 `EntropicBase`：每站点维护缓存的 `alpha`，用 H 函数在 `[2τ, 2τ/deviation]` 区间做二分/牛顿求根使 H 最小 | `Code/lb/kernels/Entropic.h:37, 94, 113-134`；`EntropicAnsumali.h:33`；`EntropicChik.h:29-32` |
| 非牛顿 BGK | `LBGKNN<Rheo,L>`：**逐站点 τ 数组**（`mTau(localFluidSiteCount)`），本步用上一步算出的 τ | `Code/lb/kernels/LBGKNN.h:26-51, 66-67` |
| 流变模型 | Carreau-Yasuda（人血拟合 / 鼠血拟合）、Casson、截断幂律 | `Code/lb/kernels/RheologyModels.h`；`CarreauYasudaRheologyModel.h`、`CassonRheologyModel.h`、`TruncatedPowerLawRheologyModel.h` |
| KBC / cascaded / regularized / RR / 中央矩 | **全仓 grep 零命中** | `grep -ril "KBC\|cascaded\|regularized\|recursive.regulariz" Code/` → 空 |

组织方式：`concepts.h` 用 C++20 concept 规定 kernel 必须具备的接口（`Code/lb/concepts.h:58` 定义 `collision_type`，`:76` 定义 streamer 对 collision 的约束）；具体选择在**编译期**由字符串常量分派（见 §六）。

### 2.3 力项（forcing）

- **只有 Guo 方案**：`GuoForcingLBGK` 包装 BGK，注释直引 `Phys. Rev. E 65, 046308 (2002)`（Guo, Zheng & Shi）— `Code/lb/kernels/GuoForcingLBGK.h:16-21`。
- 力分布 `F_i` 的公式：`F_i = (1 − 1/(2τ)) · w_i · [ (f·c_i − u·f)/cs² + (f·c_i)(u·c_i)/cs⁴ ]`，标量版在 `Code/lb/lattices/Lattice.h:446-465`（`prefactor = 1.0 - 1.0/(2.0*tau)` 在 `:453`），**SSE3 手写特化版**在 `Lattice.h:379-441`（`__m128d` 广播 + 双精度 2-lane，`:391-407`）。
- 半步力修正：速度取 `(Σf c + f/2)/ρ` 的形式由 `CalculateDensityMomentumFEq(f, force, …)` 重载完成（`Lattice.h:489-507`；`Lattice.h:496-499` 传 force 进 `CalculateDensityAndMomentum`）。这一点在 IBM 插值里也被显式处理：有力的 kernel 走 `HasForce` 分支（`Code/redblood/VelocityInterpolation.h:38-41, 70-92`）。
- **无** Shan-Chen 势、**无** RK/精确差分、**无** shifted-velocity 表述（`grep -ril "shan.chen|shifted.velocity|exact.difference"` 零命中）。

---

## 三、边界处理（逐一定位）

壁面族（由 `HEMELB_WALL_BOUNDARY` 编译期选择，`CMake/HemeLbOptions.cmake:60-62`）：

| 型式 | 实现 | 指针 |
|---|---|---|
| 简单 bounce-back（半程，壁在 link 中点） | 把 `f_post[dir]` 写到 `INVERSEDIRECTIONS[dir]` 槽 | `Code/lb/streamers/SimpleBounceBack.h:33-48`；索引算式 `:23-26` |
| **BFL 插值 bounce-back**（Bouzidi/Firdaous/Lallemand 2001） | 读每 link 的壁距分数 `q = site.GetWallDistance<L>(dir)`；`q ≥ 0.5` 当场用 Eq(5b)：`f = [f_post(i) + (2q−1)·f_post(ī)] / (2q)`（`:64-65`）；`q < 0.5` 先做简单反弹占位，等到邻居算完在 `PostStepLink` 里补 Eq(5a)：`f(ī) = 2q·f(ī) + (1−2q)·f(i)`（`:81-90`） | `Code/lb/streamers/BouzidiFirdaousLallemand.h:41-91` |
| GZS 外推壁面（Guo/Zheng/Shi PoF 14, 2007 (2002)） | 壁站点用**二阶外推**：先用 `u_wall = u_next·(q−1)/(q+1)` 一阶估计，再外推（注释给两步推导） | `Code/lb/streamers/GuoZhengShi.h:18-22`（出处）、`:139-201`（两步估计） |
| Junk-Yang 一点边界（PRE 72, 026705 (2005)） | 不合"逐 link"范式：每站点组装**入/出速度集**并解一个小线性方程组（最小二乘 + 正则参数 `THETA=0.7`） | `Code/lb/streamers/JunkYang.h:23-29`（出处）、`:48`（`THETA(0.7)`）、`:126-193`（组集/求解/回写） |

进/出口族（`HEMELB_INLET_BOUNDARY` / `HEMELB_OUTLET_BOUNDARY`，`CMake/HemeLbOptions.cmake:63-68`）：

| 型式 | 实现 | 指针 |
|---|---|---|
| Nash 零阶压力出口 | ghost 站点密度直接取 iolet 给定密度；法向速度 = 流体侧 `u·n`（除以密度补偿 momentum 存储），代回 `f_eq` 后写反方向 | `Code/lb/streamers/NashZerothOrderPressure.h:27-60`（关键：`:36` 取密度、`:43` 投影、`:54` 求 f_eq） |
| Ladd 移动壁/速度入口（Ladd JFM 1994 Eq 3.3） | `f'(r,t+1) = f(r,t*) − 2·a₁_i·ρ·u·c_i`，`a₁_i = w_i/cs²`；壁速取 link 中点（`halfWay = sitePos + 0.5·c_i`） | `Code/lb/streamers/LaddIolet.h:35-60`（公式 `:39-43`、中点 `:49-50`） |
| 虚拟站点重构（速度/压力入口通用） | 每虚拟站点存 `q[]`、`sumQiSq`、`velocityMatrixInv[3][3]` → 最小二乘线性重构 | `Code/lb/streamers/VirtualSite.h:61-90` |
| iolet 波形 | 常数/余弦/抛物线/Womersley/文件/多尺度 | `Code/lb/iolets/InOutLetCosine.h`、`InOutLetParabolicVelocity.h`、`InOutLetWomersleyVelocity.h`、`InOutLetFileVelocity.h`、`InOutLetMultiscale.h` |

**曲率**：不在运行期做曲率修正。几何预处理阶段给**每条 cut link** 存 `distanceToIntersection`（`Code/geometry/GeometrySiteLink.h:24-29`），由 `.gmy` 读出（`Code/geometry/GeometryReader.cc:592-611`），按站点+方向存成 `float` 数组（`Code/geometry/Domain.cc:85-86, 218-228`）。曲壁精度 = BFL 的线性（2 阶）插值 × 体素化给出的 q；**无**曲率张量/法向梯度修正格式。
附带验证：单元测试里明确按"壁面在壁体素中点偏移半格"修正解析半径（`Code/tests/pythontests/poiseuilleflowtest.py:76`）。

**Zou-He**：**无**（`grep -ril "zou"` 零命中）。**IBM** 存在但用于红细胞，不是壁面 BC → 见 §四。

---

## 四、多相 / 多组元 —— 【本仓：无】

**结论：HemeLB 没有任何多相/多组元能力。** 证据（全仓、非抽样）：

```
grep -ril "shan.chen|scmp|mcmp|cahn|hilliard|surface.tension|phase.field|freesurface|free.surface|multiphase|color.gradient" Code/
→ 仅 Code/tests/helpers/LabelledRequest.cc   （测试夹具里的字符串，误命中，与多相无关）
```

逐项对照任务要求（全部"无"，故不编造实现）：

| 要求项 | 本仓现实 | 指针 |
|---|---|---|
| SCMP（单组元多相，Shan-Chen 伪势） | 无 | 上述 grep 空 |
| MCMP（双组元，G_σσ' 互作用势） | 无 | 同上 |
| free-energy / 相场 Allen-Cahn / Cahn-Hilliard | 无 | 同上 |
| 颜色梯度（RK）模型 | 无 | `grep "recolor\|perturbation"` 零命中 |
| free-surface（VOF/质量追踪） | 无 | 同上 |
| 互作用势形式 / 力项加法 / 密度比 / spurious current 处理 | 无任何对应代码 | — |

**它唯一的"多组元替代物"是"单相 + 悬浮弹性体"**（IBM 红细胞 + 胶体颗粒），这是"流固耦合"，**不等价于两相流**：没有界面、没有密度比、没有表面张力项。写清楚以免下游误采：

- 红细胞膜本构：**Skalak 应变能** `w = κ_s/12·(I₁² + 2I₁ − 2I₂) + κ_α/12·I₂²`（shearModulus/dilationModulus 双模量）— `Code/redblood/CellEnergy.cc:180-185`；面元级力由解析导数给出（`:211-219` 的 `dw_dI1, dw_dI2`）。
- 面积/体积约束：惩罚项 `E = k/2·(ΔS)²/S₀`、`k/2·(ΔV)²/V₀` — `CellEnergy.cc:149-155`、`:106-117`。
- 弯曲能：相邻面元取向角 `√3·k_β·(θ−θ₀)²` — `CellEnergy.cc:16-20, 22-58`。
- 节点排斥：`−K_int·[(Δx/d)^n − (Δx/d_cut)^n]`（带 NaN 保护）— `Code/redblood/Node2Node.h:23-43`。
- 流固耦合算法：**IBM + Peskin 型 4 点核**（`stencil.h:38-54` 的四点核，另有三点/两点/cosine 近似 `:22-26, 57-60`）；速度插值 `interpolateVelocity`（`Code/redblood/VelocityInterpolation.h:116-136`，含力修正分支 `:70-92`）；力反向铺展 `spreadForce2Grid`（`Code/redblood/GridAndCell.impl.h:15-26`、`:32-35`，逐站点 `AddToForce(force·weight)` 在 `:45-54`）；主循环 `CellArmy::Fluid2CellInteractions`（`Code/redblood/CellArmy.h:210-215`）+ 跨 rank 力交换（`Code/redblood/parallel/SpreadForces.h:103-116`）+ 速度积分（`parallel/IntegrateVelocities.h`）。
- 胶体侧有润滑修正（`Code/colloids/LubricationBC.h:34-87`，对壁/对壁双向速度修正）与拖曳系数（`Code/colloids/Particle.h:53`）。

---

## 五、GPU / 并行

### 5.1 GPU：**无**（诚实结论，非"未找到证据"）

```
grep -ril "cuda|gpu|kokkos|__device__|opencl|hipMalloc" Code/ CMake/ CMakeLists.txt  →  零命中
```
唯一向量化是 **x86 SSE3**（`CMake/HemeLbOptions.cmake:32`；手写特化见 `Code/lb/lattices/Lattice.h:379-441`），可关。**无 OpenMP**（无 `find_package(OpenMP)`，无 `#pragma omp`）。所以"tier 3 大规模并行"= **CPU-MPI**，不是 GPU。
→ 对我们的"GPU 融合 kernel"功能点：**本仓提供 0 可直接复用资产**（只有一条可借鉴的"手工 SIMD 特化 + 标量 fallback 同源"的代码组织方式）。

### 5.2 数据布局：site-major AoS（每站点 Q 个分布连续）

- 双缓冲 `std::vector<distribn_t> m_currentDistributions / m_nextDistributions` + 每站点一个 `LatticeForceVector`：`Code/geometry/FieldData.h:35-37`。
- 访问式为 **`f[siteIndex * NUMVECTORS + direction]`**（连续站点号，非 x/y/z 三元索引）：`Code/geometry/Site.h:196, 200, 209-214`；`FieldData.h:117-140`（`GetFOld/GetFNew` 返回 `&arr[index]`）。
- 所以是 **AoS-per-site（Q 个 double 相邻）**，一个站点一次 collide+stream 的读写落在 1–4 条 cache line 内；**不是 SoA/padded 布局**，也没有 AA-pattern（无 GPU 需求故无）。
- 壁距 `q` 按站点×方向另存一份：`Code/geometry/Domain.cc:85-86, 218-228`（每个 cut link 一个 float），即几何信息与分布函数分离 → 运行期零几何查询（可吸收点，见 §八）。

### 5.3 MPI 分区：两级

1. **八叉树 block 级均分**（默认/快速）：按 block 内 fluid site 计数递归二分，`share_range` / `assign_range`（`Code/geometry/decomposition/BasicDecomposition.cc:21-54`），入口 `:56`。
2. **图划分**（`OptimisedDecomposition`）：构造 block/link 邻接图后调 **`ParMETIS_V3_PartKway`**（`Code/geometry/decomposition/OptimisedDecomposition.cc:138`，参数注释 `:104-135`，失败即抛异常并要求"必须能正确分解才能启动并行仿真" `:156-168`）。
   **关键设计**：顶点权重按**站点边界类型**查表——bulk/墙/进/出/墙∩进/墙∩出 六类，且权重随 wall BC 类型变化（BFL=8, GZS=28, JUNKYANG=40, SIMPLEBOUNCEBACK=5, 基准 4），并按 CPU 架构（AMD Bulldozer / Intel Sandy Bridge / NEUTRAL）分别标定 — `Code/geometry/decomposition/DecompositionWeights.h.in:14-40`。**这是"按边界类型预测每站点计算代价"的经验配方**，见 §八 Top1。

### 5.4 通信隐藏：midDomain / domainEdge 分离 + 异步飞行

站点按"邻居是否在同 rank"切两块：`Domain::GetMidDomainSiteCount` / `GetDomainEdgeSiteCount`（`Code/geometry/Domain.h:257-258`）。每步顺序（`Code/lb/README.md` 末段 + 代码）：

1. `RequestComms()` — 只**登记** Isend/Irecv，不阻塞：`Code/lb/lb.hpp:162-173`（`mLatDat->SendAndReceive(mNet)`）；
2. `PreSend()` — **先算 domainEdge 站点**（要发出去的数据），按 流体→墙→进→出 四类逐段：`Code/lb/lb.hpp:176-190`（`StreamAndCollide(*mMidFluidCollision, offset, …)` 但 offset 从 `GetMidDomainSiteCount()` 开始，即只算 edge）；
3. `PreReceive()` / `PostReceive()` — 收齐后**只修边站点**：`Code/lb/lb.hpp:215-254`；
4. `EndIteration()` — 交换缓冲：`Code/lb/lb.hpp:312`。

即"算 interior 期间让 halo 消息在网络上飞"的经典半步延迟隐藏。另有两档更激进的变体：

- **分相（phased）传播**：`net/phased/StepManager.h:34`（按 `steps::Step` 分阶段调度各 concern）、`Code/net/PhasedBroadcast.h:33`（注释说明 `splay`=相邻节点通信需跨越的迭代数、`ovrlp`=收发重叠迭代数）→ 把远距离依赖摊到多个时间步，降低全局同步频率；开关 `HEMELB_SEPARATE_CONCERNS`（`CMake/HemeLbOptions.cmake:35`）。
- **点对点实现可换**：`Coalesce / Separated / Immediate`（`CMake/HemeLbOptions.cmake:72-77`）。
- 多尺度耦合走 MPWide 跨超算通信：`Code/multiscale/mpwide/MPWideIntercommunicator.h`，配合 `intercommunicand/SharedValue`（`Code/multiscale/`）。

---

## 六、可微 / 代码生成

| 维度 | 结论 | 指针 |
|---|---|---|
| autograd / 反向自动微分 | **无**（纯 C++ 手工前向，`grep "autograd\|grad\|tensor\|backward"` 无框架痕迹） | — |
| 符号推导 | **无** | — |
| kernel 生成（Triton/CUDA codegen/sympy→C） | **无** | — |
| 实际采用的"生成"手段 | **编译期多态 = 用编译器当代码生成器** | 下列三处 |
| ① 变体以字符串常量注入 | `build_info` 里放 `KERNEL / LATTICE / WALL_BOUNDARY / INLET_BOUNDARY / OUTLET_BOUNDARY` 等 `ct_string` 常量 | `Code/build_info.h.in:25-31`；选项定义 `CMake/HemeLbOptions.cmake:54-68` |
| ② 工厂用 `if constexpr` 分派 | `get_default_kernel<L>()`：LBGK / EntropicAnsumali / EntropicChik / MRT(按 lattice 选基底) / TRT / NNCY / NNCYMOUSE / NNC / NNTPL / GuoForcingLBGK | `Code/lb/Kernels.h:23-55` |
| ③ 边界组合由类型工厂拼装 | `StreamerTypeFactory<WallLink, IoletLink>` + `NullLink` 表示"该类不存在"，wall 四种、iolet 两种任意组合 | `Code/lb/Streamers.h:22-53`；`Code/lb/streamers/StreamerTypeFactory.h:22-46` |

**对我们含义**：这套设计的代价是**运行期不可切换**——想换 lattice/kernel/BC 必须重新编译并重链接整个求解器；组合爆炸由 CI 的编译矩阵兜（§七）。若我们要"运行期可配置 + 可微"，这条路线**不能照搬**，只能借鉴其"concept 约束 + 类型工厂 + Null-object"的表达力。

---

## 七、与我们功能点的映射

> 判据：能挂则挂（给 `transfer` 评价），挂不上就写"无实现"，**绝不为凑数编造**。

| feature_ledger slug（类） | HemeLB 是否提供实现 | 能拿到的资产 | 指针 | transfer |
|---|---|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE**（多相） | ❌ 实现上**无** | 只有**分层/单相 Poiseuille 的验证方法学**：解析抛物线与仿真逐站点 `assertAlmostEqual(delta=1e-3)`，且显式把壁面按半格点偏移修正半径 | `Code/tests/pythontests/poiseuilleflowtest.py:41-45, 76, 92` | reference（验证面） |
| MCMP 高密度比 | ❌ 无 | — | §四 grep | — |
| 自由面 | ❌ 无 | — | 同上 | — |
| 相场 Allen-Cahn | ❌ 无 | — | 同上 | — |
| GPU 融合 kernel | ❌ 无 GPU | 仅"标量 + SSE3 同源双实现"的组织方式（同一公式两份：`Lattice.h:446-465` vs `:379-441`，靠 `#ifdef` 切） | 同左 | reference |
| 分布式多卡 | ❌ 无 GPU 分布式；**有 CPU-MPI 分布式** | ①midDomain/domainEdge 半步延迟隐藏骨架；②按边界类型加权的 ParMETIS 分区；③顶点权重表按 wall BC 标定 | `lb.hpp:162-312`；`OptimisedDecomposition.cc:138`；`DecompositionWeights.h.in:25-40` | **adapt**（骨架可搬到多卡 halo） |
| 稀疏 / 多孔 / 任意几何 | ✅ 强项 | 八叉树 block + gmy 体素 + 每 link 壁距分数 q，运行期零几何查询 | `GeometrySiteLink.h:24-29`；`GeometryReader.cc:592-611`；`Domain.cc:218-228` | **adapt** |
| 曲壁二阶边界（通用壁面能力） | ✅ | BFL 插值反弹（延迟式 PostStepLink） | `BouzidiFirdaousLallemand.h:41-91` | **fits**（我方若做 CPU LBM 可直接按公式重写） |
| 非牛顿流变（船舶相关：污泥/冰浆/血液） | ✅ | Carreau-Yasuda / Casson / 截断幂律 + 逐站点 τ | `LBGKNN.h:26-51`；`RheologyModels.h` | adapt |
| IBM 流固耦合（若将来做冰-结构、螺旋桨-水） | ✅ | Peskin 4 点核 + 力铺展 + 跨 rank 力交换 | `stencil.h:38-54`；`GridAndCell.impl.h:15-54`；`parallel/SpreadForces.h:103-116` | reference |
| **案例组织** | ⚠️ 弱：无通用 case suite，配置=ini + `.gmy` 几何 + CMake 变体 | 只有 `InOutLet*` 波形族 + `SimConfig` | `Code/lb/iolets/`；`Code/configuration/SimConfig.cc` | reference |
| **测试与验证面** | ✅ 强 | Catch2 构造性单测（每 BC 一个 SECTION、逐站点 golden 值、容差 1e-10）+ Python 解析解回归 | `Code/tests/lb/StreamerTests.cc:28`（SECTION: Bulk/BB/BFL/GZS/JunkYang/Nash `:39,88,223,335,499,626`）；`KernelTests.cc:114, 360`（"MRT 常数松弛 = LBGK" 交叉校验）；`CollisionTests.cc:25-159`；容差 `StreamerTests.cc:19` | **adapt**（"每 BC 一条构造性单测 + 与 BGK 交叉校验"直接可用） |
| **CI** | ✅ 强 | 编译器矩阵 gnu-11/12/13 × fluid-only / RBC 两种 build（即不同 lattice×kernel×boundary 组合门）；每周 cron 全量；copyright + include-guard 静态检查 | `.github/workflows/main-app.yml:29-40`（静态检查）、`:42-60`（编译器×build 矩阵）、`:9-13`（每周 cron）、`:181`（长测试门） | **adapt** |
| NEW: 编译期变体分派（lattice×kernel×BC 组合） | ✅ | concept + type factory + NullLink 的表达范式 | `concepts.h:58,76`；`Kernels.h:23-55`；`Streamers.h:22-53` | reference（**反面教材**：不可运行期切换） |
| NEW: 按边界类型标定的并行负载权重表 | ✅ | 六类站点权重 × wall BC × CPU 架构 的实测标定表 | `DecompositionWeights.h.in:14-40` | **fits** |

---

## 八、可吸收 Top3 + 风险

### Top1（fits）按"边界类型"加权的图划分负载均衡
- **做法**：域分解不是"每站点代价=1"，而是把站点按 bulk/墙/进/出/墙∩进/墙∩出 六类分别给整数权重，且权重随所选 wall BC 变化（BFL=8、GZS=28、JUNKYANG=40、SBB=5、基准=4），按目标 CPU 架构分别标定；把这些权重作为 ParMETIS 顶点权重 `vwgt` 传进去。
- **指针**：`Code/geometry/decomposition/DecompositionWeights.h.in:14-40`；调用点 `Code/geometry/decomposition/OptimisedDecomposition.cc:138`（参数注释 `:104-135`，`wgtflag`/`ncon` 说明 `:82-84`）。
- **对我们**：TensorLBM/TensorPlatform 若做多卡或多进程域分解，**负载不均的主因正是边界站点比 bulk 贵 2–10 倍**（该仓量化了：JUNKYANG 40 vs bulk 4）。可直接把"按 BC 类型查表得权重"照搬进我们的分区器，权重数值需在我们自己的核上重标（不可直接引用其绝对值——那是 AMD Bulldozer / Intel Sandy Bridge 上测的）。
- **风险**：权重是机型相关的魔数，跨架构需重标；表是**手写常量**（`.h.in`），无自动标定脚本（未见 profiling 驱动自动生成），所以值会随代码演进而漂移。

### Top2（adapt）midDomain/domainEdge 分离 + 异步通信飞行
- **做法**：站点按"邻居是否跨 rank"切两块；每步 `登记异步收发 → 只算 edge 站点 → 收发在算 interior 期间飞行 → 收齐后只修 edge`；`PostStepLink` 把"需要邻居数据才能完成"的边界更新挂到该阶段。
- **指针**：`Code/lb/lb.hpp:162-173`（RequestComms）、`:176-190`（PreSend，offset 从 `GetMidDomainSiteCount()` 起）、`:215-254`（Pre/PostReceive）、`:312`（EndIteration）；站点切分接口 `Code/geometry/Domain.h:257-258`；顺序说明 `Code/lb/README.md` 末段；延迟完成的具体载体 `BouzidiFirdaousLallemand.h:70-91`（`PostStepLink`）。
- **对我们**：这是与 GPU/CPU 无关的**纯骨架**，可直接搬到我们的多卡 LBM：把 halo 站点延后到通信完成之后再算，把 "halo 交换" 从同步阻塞变成"等消息时先算 interior"。对 TensorPlatform 也有意义——同样的"分阶段 + 延迟完成"模式适用于分布式任务的 overlap 调度（其 `net/phased/StepManager.h:34` 把"concern × step"矩阵化，就是一种通用的阶段调度器）。
- **风险**：需要站点重排（按 mid/edge 分区存储），与我们现有"按坐标索引"的布局冲突；edge 站点数量少时收益趋零（弱扩展才划算）。

### Top3（fits）每-link 壁距分数 q + 延迟式 BFL 插值反弹
- **做法**：几何预处理把"到壁面交点的距离分数"存成**每站点每方向一个 float**，运行期 bounce-back 只读 q 做线性插值，且 `q<0.5` 分支延迟到邻居算完再补（避免重复碰撞）。
- **指针**：`Code/lb/streamers/BouzidiFirdaousLallemand.h:41-91`（Eq 5b 在 `:64-65`，Eq 5a 在 `:81-90`）；几何数据 `Code/geometry/GeometrySiteLink.h:24-29`、`Code/geometry/GeometryReader.cc:592-611`、`Code/geometry/Domain.cc:218-228`；半格点修正的验证证据 `tests/pythontests/poiseuilleflowtest.py:76`。
- **对我们**：船体曲面/任意几何 LBM 的二阶壁面方案，**开销极低**（运行期零几何查询、只多一次乘加），比"每步重算壁距/法向"便宜一个量级。可直接按公式在我们仓里重写。
- **风险**：① 只有**线性**插值（2 阶上限），曲率修正能力有限，不满足高 Renolds 曲壁精度需求时应配曲率修正格式；② TRT 的 `Lambda=3/16` 是**为了配合 HWBB 壁面位置的硬编码魔数**（`TRT.h:96-101` 注释自认 TODO），意味着"BFL + 某 TRT 参数"这一组合的壁面位置是**按经验配平**而非导出的——我方若同时改壁面格式与 TRT 自由参数，**不能照抄 3/16**，须重新做壁面位置一致性推导。

### 其它风险（诚实登记）
- **LGPL-3**：见 §一，只做方法重写。
- **对本轮核心缺口帮助为零**：多相（MCMP/自由面/相场）与 GPU 两项**本仓 0 资产**；不要指望本仓解决 `GAP-LBM-MCMP-POISEUILLE`。
- **未编译/未跑仿真**：本单只读盘点，未安装 ParMETIS/Boost/MPI 依赖构建，因此"编译期变体能否全组合通过"未做运行期验证（我只读了 CI 的矩阵定义 `.github/workflows/main-app.yml:42-60`，未实跑）。**这是本报告最大的证据边界**：所有结论是 source-confirmed，但不是 runtime-confirmed。
- **`D3Q27` 未逐行读**（只确认存在 27 向量定义 `D3Q27.h:14-` 且 CMake 枚举含之）；MRT 对其无基底（`Kernels.h:37-38` 会抛 "No MRT basis for lattice"）——这点是从代码分支逻辑读出的，属 source-confirmed。

---

## 九、附：本次盘点的可复现命令

```bash
git clone --depth 1 https://github.com/hemelb-codes/hemelb.git /tmp/hemelb_src
cd /tmp/hemelb_src && git log -1 --format=%H   # 432d3386e32571487a1521f88a407db5dc9ca171
head -2 LICENSE                                 # LGPL v3
grep -ril "shan.chen|scmp|mcmp|cahn|hilliard|surface.tension|phase.field|free.surface|multiphase|color.gradient" Code/   # → 只 tests/helpers/LabelledRequest.cc
grep -ril "cuda|gpu|kokkos|__device__|opencl|hipMalloc" Code/ CMake/ CMakeLists.txt                                      # → 空
grep -ril "KBC|cascaded|regularized|zou|smagorinsky" Code/                                                               # → 空
find Code -name '*.cc' -o -name '*.h' | xargs cat | wc -l    # 67894
```
图：`artifacts/m8301/fig1_loc_by_subsystem.png`（各子系统 LOC，实测）、`artifacts/m8301/fig2_method_coverage.png`（方法谱系有/无矩阵，来自上列 grep）。

---

[OSS-INV]
repo: hemelb-codes/hemelb
lang: C++20 (template/concepts) + CMake, Python geometry toolchain
license: LGPL-3.0
methods: D3Q15/D3Q19/D3Q27/D3Q15i; BGK, TRT (Lambda=3/16 hardcoded), MRT (D'Humieres D3Q15/D3Q19 bases), entropic ELBM (Ansumali, Chik, H-function root find), non-Newtonian BGK (Carreau-Yasuda human/mouse, Casson, truncated power law); forcing = Guo only (PRE 65 046308); no KBC/cascaded/regularized/RR/no Zou-He
multiphase: 无 (repo-wide grep for shan-chen/scmp/mcmp/cahn-hilliard/phase-field/free-surface/color-gradient is empty except a test fixture string); only single-phase + immersed elastic bodies (RBC Skalak membrane `Code/redblood/CellEnergy.cc:180-185`, Peskin 4-point IBM `Code/redblood/stencil.h:38-54`)
boundary: simple bounce-back (`Code/lb/streamers/SimpleBounceBack.h:33-48`); Bouzidi-Firdaous-Lallemand interpolated BB with per-link wall-distance q and deferred PostStepLink (`Code/lb/streamers/BouzidiFirdaousLallemand.h:41-91`); Guo-Zheng-Shi extrapolation (`GuoZhengShi.h:139-201`); Junk-Yang one-point least-squares (THETA=0.7, `JunkYang.h:23-48`); Nash zero-order pressure iolet (`NashZerothOrderPressure.h:27-60`); Ladd moving wall (`LaddIolet.h:35-60`); virtual-site least-squares iolet (`VirtualSite.h:61-90`); curvature handled purely by precomputed cut-link distance (`Code/geometry/GeometrySiteLink.h:24-29`)
gpu: 无 (zero hits for cuda/gpu/kokkos/opencl; only x86 SSE3 intrinsics `Code/lb/lattices/Lattice.h:379-441`, no OpenMP); parallel = CPU MPI: site-major AoS `f[site*Q+dir]` (`Code/geometry/Site.h:196`), two-level decomposition (octree block split `BasicDecomposition.cc:21-54` + ParMETIS_V3_PartKway with per-boundary-type vertex weights `OptimisedDecomposition.cc:138`, `DecompositionWeights.h.in:25-40`), midDomain/domainEdge split with async comm flying (`Code/lb/lb.hpp:162-312`)
summary: HemeLB 是 CPU-MPI 的十亿级复杂血管几何 LBM，强项在 bounce-back 族边界（BFL 插值 + per-link 壁距分数）、非牛顿血液流变、红细胞 IBM，以及"按边界类型加权的图划分 + midDomain/domainEdge 半步延迟隐藏"的并行骨架；但完全没有多相/多组元（SCMP/MCMP/free-energy/相场/自由面全部零命中）也没有任何 GPU/加速器后端。
对我们的价值集中在三点：边界类型加权分区配方、通信/计算重叠骨架、低开销二阶曲壁（BFL+q）；对本轮多相缺口与 GPU 融合 kernel 缺口，本仓提供 0 可实现资产。许可 LGPL-3.0 → 只可做方法级重写，不可复制模板头文件。
[/OSS-INV]

[OSS-SOL]
repo: hemelb-codes/hemelb
slug: NEW:分布式 LBM 负载均衡-按边界类型加权的顶点权重图划分
tag: parmetis-boundary-type-vertex-weights
method: 域分解时先按站点边界类型（bulk/墙/进/出/墙∩进/墙∩出）给整数权重，权重值随所选壁面格式变化（BFL=8, GZS=28, JUNKYANG=40, SBB=5, 基准=4）并按目标 CPU 架构分别标定，再把该权重表作为 ParMETIS 的顶点权重 vwgt 送入 ParMETIS_V3_PartKway 做 K-way 划分。
files: Code/geometry/decomposition/DecompositionWeights.h.in:14, Code/geometry/decomposition/DecompositionWeights.h.in:25, Code/geometry/decomposition/OptimisedDecomposition.cc:104, Code/geometry/decomposition/OptimisedDecomposition.cc:138
conventions: 权重为无量纲相对代价整数（bulk 基准=4）；六类站点枚举顺序 bulk/wall/inlet/outlet/wall-inlet/wall-outlet；权重按 (wall BC 类型 × CPU 架构) 二维查表；ParMETIS 参数 wgtflag 控制边/点权重开关
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: hemelb-codes/hemelb
slug: NEW:通信/计算重叠-midDomain/domainEdge 分离与半步延迟隐藏
tag: midedge-split-async-halo-overlap
method: 每时间步先登记异步 Isend/Irecv（不阻塞），随后只对"邻居跨 rank"的 domainEdge 站点做 collide+stream 并发送，让 halo 消息在网络上飞行；收齐后只修 edge 站点，最后交换分布函数双缓冲；对需要邻居数据才能完成的边界更新（如 BFL 的 q<0.5 分支）提供延迟完成钩子 PostStepLink，在消息到达后补算。
files: Code/lb/lb.hpp:162, Code/lb/lb.hpp:176, Code/lb/lb.hpp:254, Code/lb/lb.hpp:312, Code/geometry/Domain.h:257, Code/lb/streamers/BouzidiFirdaousLallemand.h:70
conventions: 站点按 midDomain(0..N_mid) / domainEdge(N_mid..) 连续重排；edge 内再按 流体/墙/进/出 四段分别统计计数；每步固定六阶段 post-receive→edge→async send→mid→wait→fixup
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: hemelb-codes/hemelb
slug: NEW:任意几何二阶曲壁-每 link 壁距分数 + 延迟式 BFL 插值反弹
tag: bfl-per-link-q-deferred
method: 几何预处理阶段为每条 cut link 预计算并存储到壁面交点的距离分数 q（每站点每方向一个 float）；运行期 bounce-back 只读 q：q>=0.5 当场用 Eq(5b) f(i)=[f_post(i)+(2q-1)f_post(ib)]/(2q)，q<0.5 先写简单反弹占位、等反向邻居算完再在 PostStepLink 用 Eq(5a) f(ib)=2q f(ib)+(1-2q) f(i) 补全，避免重复碰撞。
files: Code/lb/streamers/BouzidiFirdaousLallemand.h:41, Code/lb/streamers/BouzidiFirdaousLallemand.h:64, Code/lb/streamers/BouzidiFirdaousLallemand.h:81, Code/geometry/GeometrySiteLink.h:24, Code/geometry/GeometryReader.cc:592
conventions: q∈(0,1] 为从流体站点到壁面交点的距离（以格距为单位），q<0.5 壁靠近流体站点；线性插值（2 阶上限）；壁面实际位于壁体素中点偏移半格处，解析解对比时须对半径做 +0.5 voxel 修正
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: hemelb-codes/hemelb
slug: NEW:非牛顿流变-逐站点 tau 的 BGK 与 Carreau-Yasuda/Casson/截断幂律模型
tag: per-site-tau-nonnewtonian-bgk
method: 保持标准 BGK 碰撞式 f+=ω(f_eq-f)，但把松弛时间改为逐站点数组；每步先用上一步存下的 τ 碰撞，碰撞后按当前剪切率调用流变模型 CalculateViscosityForShearRate(shearRate, density) 更新该站点 τ，形成一次滞后（lagged）耦合。
files: Code/lb/kernels/LBGKNN.h:26, Code/lb/kernels/LBGKNN.h:46, Code/lb/kernels/LBGKNN.h:66, Code/lb/kernels/CarreauYasudaRheologyModel.h, Code/lb/kernels/CassonRheologyModel.h
conventions: τ 为格单位无量纲；HemeLB 约定 omega = -1/tau；τ 数组长度=本进程局部流体站点数；流变模型按 CRTP 提供 CalculateViscosityForShearRate(const PhysicalRate&, const LatticeDensity&)，被 static_assert 强制
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: hemelb-codes/hemelb
slug: NEW:测试与验证面-每边界条件一条构造性单测 + 解析解逐站点回归
tag: bcond-golden-unit-test-plus-analytic-regression
method: 每个边界条件在 Catch2 里独占一个 SECTION，用手工初始化的各向异性分布函数跑一格 collide+stream，然后逐站点逐方向与手算期望值比对（容差 1e-10）；跨算子一致性用"MRT 在常数松弛下应严格退化为 BGK"交叉校验；外层再配一条 Python 端回归：跑圆柱管仿真，把中线速度与抛物线解析解逐点 assertAlmostEqual(delta=1e-3)。
files: Code/tests/lb/StreamerTests.cc:28, Code/tests/lb/StreamerTests.cc:19, Code/tests/lb/KernelTests.cc:360, Code/tests/pythontests/poiseuilleflowtest.py:41, Code/tests/pythontests/poiseuilleflowtest.py:92
conventions: 单测容差 allowedError=1e-10（绝对 margin）；解析解对比容差 delta=1e-3（物理单位 m/s）；解析半径须加 0.5 voxel 修正以对齐半程 bounce-back 壁面位置；MRT==BGK 交叉校验要求所有松弛参数取 1/tau
transfer: adapt
[/OSS-SOL]