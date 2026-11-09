# 【盘点】CFD-GO/TCLB — 代码级技术盘点（tier 3：模板化 MPI+CUDA LBM）

- 锚点：FEAT-OSS-INVENTORY-LBM（LBM 开源项目深盘 · CFD-GO/TCLB）
- 读码途径：`git clone https://github.com/CFD-GO/TCLB.git`（成功；未走 GitHub REST 路线）
- 快照：branch `master`，HEAD `ded67cd768cf7e727bd078af139e3ec7895076e5`，提交日期 2025-11-27
- 规模：665 文件（不含 .git）；`.cpp/.h` 合计 25,609 行（注意：`.c/.cu` 多数为模板生成产物，真实"手写算法"在 `models/*/Dynamics.c.Rt` + `src/lib/*.R`）；`models/*/Dynamics.R` 61 个模型；`example/**.xml` 134 个算例
- 纪律：每条方法结论带 `path:line`；未逐行核验者标注（未核验）；代码里没有的直接写"无"

---

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 主语言 | **C++/CUDA 内核 + R 模板生成器（rtemplate）+ 模板内嵌 Python（sympy/lbmpy/pystencils）** | `README.md:5`；`src/main.R`；`models/lbmpy/d2q9_lbmpy/Dynamics.c.Rt:17-48` |
| 许可证 | **GNU GPL v3.0** | `LICENSE:1-2` |
| 活跃度 | HEAD 提交 2025-11-27；CI 五条流水线（CPU 测试 / CUDA 编译 / HIP 编译 / 其它测试 / rinside）；stars、PR 数 **WITHHELD**（api.github.com 出网被策略拦截，未取得） | `.github/workflows/cpu_test.yml:1-30`；`gpu_comp.yml`；`hip_comp.yml`；`other_tests.yml` |
| 定位一句话 | 用"模板+符号推导"把 LBM 模型（D2Q9…D3Q27、BGK/MRT/TRT/cumulant、多相多组元、相场、非牛顿、热流、反应、颗粒）**自动生成 CUDA/CPU 内核**的 HPC 框架；"写物理而不是写 kernel"，同一份模型描述同时出 MPI+CPU / MPI+CUDA / MPI+HIP 后端、文档与 CI 矩阵 | `README.md:5`；`src/conf.R:771-792`；`src/Lattice.cu.Rt:500-530` |

**许可证对我们的可用性判断**：GPL-3.0 强 copyleft——把其源码（含模板生成的 C 代码）链接/派生进我们仓并对外分发会要求整体 GPL-3.0。若 TensorLBM/TensorPlatform 维持 MIT/Apache-2.0，**不得复制其代码，也不得把 `src/lib/*.R` / `Dynamics.c.Rt` 改写后并入**。可用方式限定为**读码提方法论 + 干净室重写**（离散格式属公开文献：保守 Allen-Cahn 相场、Lycett-Luo 力项、Bouzidi 插值反弹、Guo 的 `u+0.5F/ρ`）。结论：**reference-only**，本文所有 `transfer` 建议应理解为"自己写"。

---

## 二、方法谱系

### 2.1 格子
格子不硬编码，由模型声明 `AddDensity(name="f[i]", dx=,dy=,dz=)` 定义，`src/conf.R` 据此推导 margin 并生成 `FTabs`（`src/Global.h.Rt:64-72`、`src/conf.R:771-792`）。通用格子库：**D2Q9** `src/lib/lattice.R:8-22`；**D3Q7** `:31-43`；**D3Q19** `:51-75`；**D3Q27** `:88-92`（注释说明与 `expand.grid` 排序差异）；**D3Q15** 作相场轻界面格子 `models/multiphase/d3q27_pf_velocity/Dynamics.c.Rt:49-53`（`OPTIONS_q27 → hPops 27 else 15`）。双 population 声明：`models/multiphase/experimental/d2q9_pp_MCMP/Dynamics.R:56-58`（group `f`/`g`）。

### 2.2 碰撞算子
组织原则：**碰撞算子不是继承体系，而是模板里由 R 符号代码生成**。核心是 `src/lib/feq.R` 的 `MRT_eq(U, rho, J, order=, mat=)`——用 `polyAlgebra` 从速度集自动推导任意阶平衡态矩与转换矩阵；BGK/MRT/cumulant 只是 `mat` 与松弛矩阵 `Omega` 的不同取值。

| 算子 | 位置 | 细节 |
|---|---|---|
| SRT/BGK | `models/flow/d2q9_SRT/Dynamics.c`；`models/flow/d2q9_les/Dynamics.c.Rt` | 单松弛 `omega`；LES 同处算涡粘 |
| MRT（矩空间） | `models/flow/d2q9/Dynamics.c.Rt:202-268` | `R=f·M` 取矩，按阶选松弛 `S2/S3/S4`（`:220-223`），`f=R·M⁻¹`（`:266`）；矩阵由 `feq.R` 生成，另有 Cholesky 正交化（`:17-23`） |
| WMRT | `models/multiphase/d2q9_scmp/Dynamics.c.Rt:114-125` | 对平衡态矩系数矩阵 QR，`M=MO·R⁻¹`（`Options$WMRT`，`:118-123`） |
| TRT | `models/particles/d3q27_PSM/Dynamics.R:76-77`（`Lambda` magic）；`models/nonnewtonian/d3q27_kl/Dynamics.R:82,87`；`models/heat/d3q27_tePSM_per/Dynamics.R:82-83` | 非全局算子而是 `Options$TRT`/节点类型开关，奇偶矩两套松弛率 |
| Cumulant | `models/flow/d3q27_cumulant/Dynamics.c.Rt`；`src/lib/cumulant.R:1-44`；多相版 `d2q9_scmp/Dynamics.c.Rt:877-880` | 先求中心矩再累积量变换 |
| KBC / Karlin | **无**（`grep -i "kbc\|Karlin"` 零命中） | NOT_SUPPORTED |
| Regularized (RR) | **无显式实现** | `order=12` 高阶平衡（`d2q9_scmp:99`）是"高阶平衡矩"非正则化；等价性（未核验） |

### 2.3 力项
| 格式 | 位置 | 做法 |
|---|---|---|
| Guo 型（半力速度 + 矩空间添加） | `models/multiphase/d3q27_pf_velocity/Dynamics.c.Rt:642-668` | `F_total=F_surf+F_pressure+F_body+F_mu`；`u = m0[2:4] + 0.5·F/ρ`（`:646`，即 Guo `u*=u+F/2ρ`）；力以矩空间 `mF` 加入（`:664-668`） |
| 黏性力隐式定点迭代 | 同上 `:624-647` | `do{…}while(j<force_fixed_iterator)`，反复用新 `u` 重算应力再算 `F_mu`；默认 2（`Dynamics.R:198`）——高密度比抑制黏性滞后的关键 |
| EDM（Kupershtokh 精确差分） | `models/multiphase/d2q9_scmp/Dynamics.c.Rt:497-510` | `C(R[-1],(R*R*A+R*R[1]*(A*(-2)+1))[-1])`（`:503`），`A=Kupershtokh_A`（`:500`，默认 -0.152，`Dynamics.R:71-75`）；归一 `F-=(2/3)(R·gs)U`（`:509`） |
| Lycett–Luo 高阶力项 | 同上 `:800-858` | `df_LBL` 含 `γτ(F²-3(F·e)²)`、`2ρτ(F·(u-e)-3(F·e)(u·e))`、非平衡应力 `Φ`（`:834`）；`γ=1-ω/4+0.5(5-κ)ρε₀φ⁻²ω/36`（`:858`）；`κ=LBL_kappa`、`ε₀=LBL_epsilon0`（`Dynamics.R:77-78`） |
| 体积力（矩空间注能） | `models/flow/d2q9/Dynamics.c.Rt:254-262` | `Jx+=(GravitationX+BC[0])·ρ`，等价"力=动量注入" |
| 压力梯度力 | `d3q27_pf_velocity/Dynamics.c.Rt:516-519` | `F_p=-(1/3)p(ρ_h-ρ_l)∇φ` |
| shifted-velocity 完整式 | **无** | 只有 Guo 半修正；未见 `feq(ρ,u+τF/ρ)` |

推导辅助：`models/multiphase/d2q9_scmp/Forces.R:1-75` 是开发期矩空间展开脚本（不在 `conf.mk`，不参与编译）。

---

## 三、边界处理

| 边界 | 位置 | 实现 |
|---|---|---|
| 全反弹 bounce-back | `src/lib/boundary.R:33-53`（`FullBounceBack`）；`models/flow/d2q9/Dynamics.c.Rt:145-160` | 速度集 `merge` 求反向配对后 `C(f[j],f[i])`；`Options$bc` 分支额外累计壁面动量 |
| Zou–He（旧式闭式） | `src/lib/boundary.R:67-108` | `fs[sel]=(feq+(fs-feq)[bounce])[sel]`（`:84`），按矩约束符号求解（`:96-104`），分 pressure/velocity |
| **Zou–He（通用符号求解器）** | `src/lib/boundary.R:143-198`（`ZouHeRewrite`） | 任意法向 `n`：重建以 `R` 为动量的平衡态（`:165`）、对称非平衡（`:170`）、单次出现变量消元解方程（`:188-195`）→ "边界条件算出来" |
| Zou–He 应用点 | `models/flow/d2q9/Dynamics.c.Rt:162-200`（E/W/N/S × Velocity/Pressure 8 函数）；多相特化 `d2q9_scmp/Dynamics.c.Rt:345-352`（`rhs=PV("Density")`） | 每方向一函数，`Run()` 里 `switch(NodeType & NODE_BOUNDARY)` 分派（`d2q9:108-143`） |
| 对称/镜像 | `src/lib/boundary.R:37-52`；`d2q9_scmp/Dynamics.c.Rt:404-422`；势的镜像 `:460-507` | 对偶方向赋值 |
| 移动壁 | `d2q9_scmp/Dynamics.c.Rt:390-402`（`MovingWallVelocity`）；`d3q27_pf_velocity/Boundary.c.Rt:187-224` | Zou-He 变体，壁面动量进反弹项 |
| **曲率/插值反弹（Bouzidi cut-link）** | `models/flow/d3q27_cumulant/Dynamics.c.Rt:179-232`（`#ifdef OPTIONS_IB`）；`cut_t Q` 在 `src/LatticeContainer.h.Rt:47`，访问 `src/LatticeAccess.inc.cpp.Rt:279` | 切点分数 `q∈(0,CUT_MAX)`→`p=q·2/CUT_MAX`；`p>1` 二次外插 `fa/p+f_old(p-1)/p`（`:203`），`p≤1` 线性插值 `fa·p+f_i(1-p)`（`:205`）；同时累计动量交换 `F+=(f_bb-fa)e_i`（`:211-215`），`NODE_Body` 上聚合 `Drag/Lateral/Lift`（`:219-231`） |
| 曲率壁润湿（接触角） | `d3q27_pf_velocity/Boundary.c.Rt:674-780`；开关/字段 `Dynamics.R:44-46,97-108,201` | `a=-h(4/IntWidth)cos(radAngle)`（`:716`、`:750`）；曲线边界三角面元插值系数 `coeff_v1..v3`/`triangle_index` 做方向导数（`:764-780`）；近壁法向修正 `:674-687`（作者自承 `:39-41` 为临时 fix） |
| 出流 Neumann/对流 | `Boundary.c.Rt:704-717`；φ 的 ∇² 出口特化 `Dynamics.c.Rt:175-214` | 零梯度/对流出口，抑制周期边界对单相出口干扰（`:206-209`） |
| **IBM（delta 铺力）** | **无** | `grep -i "immersed\|IBM"` 仅命中 `#ifdef OPTIONS_IB`（实为插值反弹命名）；颗粒耦合走"固体查找器+动量交换+原子加"：`src/SolidContainer.h:10-24`、`src/Particle.hpp:43-93`，外力交换 `src/RemoteForceInterface.h`、`src/lammps.cpp` → NOT_SUPPORTED |

---

## 四、多相 / 多组元（重点）

### 4.1 有无总表
| 手法 | 有无 | 指针 |
|---|---|---|
| SCMP 伪势 | **有**（力项格式×碰撞算子×粘度模型三维可配） | `models/multiphase/d2q9_scmp/`（`Dynamics.c.Rt` 999 行）；`example/multiphase/scmp/drop.xml` |
| MCMP 双组元 | **有（实验性）** | `models/multiphase/experimental/d2q9_pp_MCMP/`（559 行）；`example/multiphase/pp_mcmp/d2q9_pp_MCMP_test-00.xml`、`test-01-contact.xml` |
| 相场 Allen-Cahn（保守） | **有**，最完整（3D/热毛细/曲线壁润湿/高密度比） | `models/multiphase/d3q27_pf_velocity/`（`Dynamics.c.Rt` 950+ 行、`Boundary.c.Rt` 1053 行、`thermo.c.Rt`）；`example/multiphase/d3q27_pf_velocity/{bubbleRiseBenchmark.xml.RT, ContactAngle_45*.xml, RayleighTaylorInstability_3D.xml}` |
| 相场其它变体 | 有 | `d2q9_pf`、`d2q9_pf_velocity`、`d2q9_pf_pressureEvolution`、`experimental/d2q9_lee`（`Dynamics.c.Rt:241-244` 化学势 `mu(±e)`） |
| free-energy | **无** | 未见自由能泛函/化学势驱动单分布模型 |
| 颜色梯度/CSF | **有** | `models/multiphase/d2q9_csf/`（梯度 `Dynamics.c.Rt:98,149,503-521`）、`models/experimental/d3q27_csf/`；`example/multiphase/csf/`（Zalesak 盘、Taylor 气泡、Andersen 迭代润湿） |
| free-surface | **无**（`grep -rli "free.surface\|freeSurface"` 零命中） | NOT_SUPPORTED |
| Kupershtokh（CS EOS） | 有 | `d2q9_kuper/`、`d3q19_kuper/` |
| 凝固/相变 | 有（附带） | `models/multiphase/solidification/d2q9_solid/` |
| 热毛细/温度相关 σ | 有 | `d3q27_pf_velocity/thermo.c.Rt`；`Dynamics.c.Rt:528-560`（含 `gradT`）、`:314-316`（`sigma_T`、`sigma_TT`） |

### 4.2 SCMP：势/力/密度比/spurious current
- **ψ(ρ)**：`realCalcPhi(rho)=sqrt(-p_cs(ρ)+ρ/3)`，`p_cs` 是**常量硬编码**的 C-S 型 EOS 闭式（`d2q9_scmp/Dynamics.c.Rt:543-555`，常量 `a2/b2/c2` 在 `:546-548`），再乘 `Kupershtokh_K`（`:552`）。EOS 参数不可配（`Dynamics.R` 只暴露 `Temperature`、`Kupershtokh_K`）→ 反面教材：状态方程必须外置。
- **互作用力**：stencil 权重 `gs=(0,1,1,1,1,1/4,1/4,1/4,1/4)`（`:499`）在实际邻居求和，EDM 展开 `:503`；壁面额外 `R+=R₁²A` 且 `phnb` 掩膜（`:504-506`）；`F-=(2/3)(R·gs)U`（`:509`）；邻近势缓存于 `phi_neib[...]`（`initPhi` `:526-541`）。
- **力入流体**：`Jx+=F.x+GravitationX·ρ`（`:793-794`），`dF`（EDM/LBL 矩修正）加到矩 `m`（`:862`），再 `f=m·M⁻¹` 或 FMT（`:864-869`）。
- **密度比**：注释声明基于 Lycett-Luo 可达 "arbitrarily high density ratios"（`:6`）；实现是 `evalOmega(ρ)` 粘度-密度关系（阶跃 `rho>1?ω_l:ω_v` 或平滑 `μ=μ_l+(ρ-ρ_l)(μ_v-μ_l)/(ρ_v-ρ_l)`，`:428-440`）+ EDM/LBL。无代码级上限、无高密度比对标算例（`example/multiphase/scmp/drop.xml` 仅单液滴）→ 数值上限 **WITHHELD（需实测）**。
- **润湿**：`VirtualRhoWBC`（虚拟壁密度，DOI 10.1103/PhysRevE.100.053313）：壁面 `rho_n=-Density`（`:571-584`），壁面势 `rho_n>0?φ:φ(0,0)`（`:538`），`phnb` 掩膜（`:595-600`）；参数 `LVRho_phi_dr`、`LVRho_ulimit/llimit`（`Dynamics.R:63-66`）；另有粗糙 `Wetting`（`Dynamics.R:86`）。
- **spurious current**：**无专门处理**（无滤波/压力修正/well-balanced 项），仅靠 Lycett-Luo 高阶力项 + WMRT/cumulant + 平滑粘度间接抑制。

### 4.3 MCMP（双组元）
- 两套分布 `f`（湿）+`g`（干），各自 MRT（`d2q9_pp_MCMP/Dynamics.c.Rt:19-52`：`EQ`/`EQ_g`）。
- 交叉力（sympy 生成）：`F_k=-Gc·ψ_k(x)·Σ_{i≥1} w_i·ψ_{k'}(x+e_i)·e_i`（`:162-176`；ψ 取值 `:127-180`；`w_i`、`c` 在 `:66-71`）。
- 势：`psi_f=ρ_f`、`psi_g=ρ_g`（`:193`、`:204`）——最简线性伪势；C-S 势与 Stiles-Xue 势被注释掉（`:194`、`:205`）。`EoS_CS()`（`:116-126`）算了 p 但**从未被力调用**（`CalcPsi_f` 注释 `TO DO: link to getFf()`，`:117`）→ **EOS 接入 MCMP 力属未完成**。
- 壁面：`psi_f=Gad2/Gc`、`psi_g=Gad1/Gc`（`:198`、`:209`）；`Gc/Gad1/Gad2` 定义 `Dynamics.R:63-65`。
- 压力/速度：`P=ρ/3+Gc·ψ_f·ψ_g/3`（`:181-187`）；公共速度按 `ρ_k/ω_k` 加权（`:93-115`）。
- 密度比：`Density` 与 `Density_dry`（"ideal gas assumption"，`Dynamics.R:60-61`）→ 低密度比双组元；无压力演化/无 EOS 一致力 → 高密度比 NOT_SUPPORTED/WITHHELD。
- 诚实未竟项：MRT 路径注释"尚未正确含多组元力（TO-DO）"（`:310-316`）；另有 `experimental/d2q9_pp_LBL`（Lycett-Brown 双组元）。

### 4.4 相场保守 Allen-Cahn（本仓最强，最值得吸收）
`models/multiphase/d3q27_pf_velocity`（Fakhari & Mitchell；版本史 `Dynamics.c.Rt:8-44`）：
1. **变量分离**：`ρ=ρ_l+(φ-φ_l)(ρ_h-ρ_l)/(φ_h-φ_l)`（`:580`）；水动力分布零阶矩**是压力不是密度**（`:586-599`：`EQ$Req[1]=PV("p"); p=m0[0]`）→ "压力演化 / improved locality" 高密度比配方。
2. **化学势**：`mu=4(12σ/W)(φ-φ_l)(φ-φ_h)(φ-φ̄)-1.5σW·∇²φ`（`:224-225`）；温度版用 `SurfaceTension(0,0,0)`（`:221-222`）；∇² 用 `myLaplace`（各向同性 27 点，`:211-213`），出口特殊模板 `:175-214`。
3. **h 的保守 Allen-Cahn**：`heq=C·feq(u)`（`:652-653`），`Fphi=w_i[(1-4(C-0.5)²)/W](e_i·n)`（`:649,661`），`h←h-ω_φ(h-heq+0.5Fphi)+Fphi`（`:662`）——完整离散式（含反扩散系数 `1/W`）。
4. **力组装**：`F=F_surf+F_pressure+F_body+F_mu`（`:642-644`）；`F_surf=μ∇φ+热毛细`（`:528-560`）；`F_mu=(0.5-τ)(ρ_h-ρ_l)(τ_ij·∇φ)`（`:639-641`，BGK 版 `(0.5-τ)/τ`，`:908-910`）；定点迭代自洽（`:624-647`）。
5. **高密度比**：`Density_h/Density_l` + 压力分离 + `calcTau` 三种粘度混合（`:484-513`）；注释对标 water-air（`:21`）。
6. **润湿**：表面能式与几何式（`Options$geometric`，`Boundary.c.Rt:674-780`），支持曲线边界三角面元插值。

---

## 五、GPU / 并行

**数据布局 = 按 margin（stencil 偏移）分段存储 + "pocket" 指针滑移流式**
- `FTabs` 不是一个大数组，而是每个非空 margin 一指针（`src/Global.h.Rt:64-72` 按 `NonEmptyMargin` 生成）；按 `m$Size` 分配（`src/LatticeContainer.inc.cpp.Rt:85-103`），`PreAlloc` 一次大块切分（`:106-113`）。
- **流式不搬数据**：`move.pocket(i,offset,ctx)` 直接对 margin 指针加减偏移（`src/LatticeAccess.inc.cpp.Rt:70-80`）→ 按方向平移指针实现 pull 流式，相邻线程读同向数组相邻地址 ⇒ 天然合并访问。
- 双缓冲 `in/out`（`src/LatticeContainer.h.Rt:40-44`；`src/Lattice.cu.Rt:478`）；`STORAGE_BITS` 16/32/64 压缩（`LatticeAccess.inc.cpp.Rt:13-33`）；`DIRECT_MEM` 走 pinned 内存（`LatticeContainer.inc.cpp.Rt:90-96`）。
- 说明：代码里**没有** "AA-pattern" 字样（`grep` 零命中）→ 命名（未核验），机制已核验。

**核函数切分**：每次迭代切成"边界核 + 内部核"——`RunBorder<...>` → `MPIStream_A()` → `RunInterior<...>`（`src/Lattice.cu.Rt:500-530`）；边界核只处理需 halo 的条带，内部核处理大块，同 stream 顺序执行，让 D2H 拷贝与内部核并行。stage/globals 策略模板化 `NoGlobals/IntegrateGlobals/OnlyObjective`（`:502-512`）。固体/颗粒 `SolidAll/SolidTree/SolidGrid` 编译期三选一（`src/SolidContainer.h:12-22`），每步 `CopyToGPU`（`Lattice.cu.Rt:407-412`）。

**MPI 分区与通信隐藏**：27 方向独立缓冲 `mpiin[27]/mpiout[27]`（`src/Lattice.h.Rt:70-72`）。三步曲（`src/Lattice.cu.Rt:327-386`）：① `MPIStream_A()` 异步 D2H（`:327-333`）；② `MPIStream_B()` `MPI_Irecv`+`MPI_Isend` 全量，`MPI_Waitany` 收一个拷一个（`:366-380`），H2D 用 `inStream`（`:371,378`）；③ 中间插 `RunInterior` 覆盖通信（`:496-530`）。多级并行：MPMD（`src/MPMD.hpp`）、LAMMPS 力交换（`src/lammps.cpp`）、入口湍流（`src/SyntheticTurbulence.cpp`）。**单进程多 GPU：无**（一 rank 一 GPU，靠 MPI 扩卡）。

---

## 六、可微 / 代码生成

**可微 = 离散伴随（Tapenade 生成反向代码），不是 autograd 框架**
- `src/ADset.sh.Rt`：主函数 `MAIN_FUN=Run`，密度与 globals 为 `VARIABLES`、settings 为 `SETTINGS`（`:5-16`）；封装 `tools/makeAD`、`tools/ADmod.R`。
- **控制流可逆化自研**：`src/ADTools.cu:14-40` 用 shared-memory 位栈实现 `pushcontrol1b/2b/3b` 与 `pop`，分支决策压栈供反向回放；`src/ADpre.h.Rt:18-30` 把 settings 宏编码为 `0xFFFFxx` 常量，梯度走 `_DT` 变量（`:22-25`）。
- 伴随模型是每模型可选第二模板：`models/flow/d2q9_les/Dynamics_adj.c.Rt`（`DensityAD`、`getRhoB/getUB`）；标记 `ITER_ADJOINT`（`src/Lattice.h.Rt:18`），checkpoint ≤33 层（`Lattice.h.Rt:31`）。→ 与 JAX/PyTorch 生态不通，属 reference。

**代码生成三层**
1. R+rtemplate 主链：`Dynamics.R` 声明 → `src/conf.R`（Margin/Settings/Fields/NodeType 位掩码 + 文档）→ `Dynamics.c.Rt`（内嵌 R，`polyAlgebra` 推导后 `C(...)` 打印 C）。入口 `src/main.R`；makefile 亦生成（`src/makefile.Rt`、`src/makefile.main.Rt`）；连 XML schema 与文档都生成（`src/schema.xsd.Rt`、`src/Model.md.Rt`、`src/Models.md.Rt`、`src/catalog.xml.Rt`）。
2. 模板内嵌 Python：`experimental/d2q9_pp_MCMP/Dynamics.c.Rt:55-81`（sympy `print_ccode` 打印力表达式）；`models/lbmpy/d2q9_lbmpy/Dynamics.c.Rt:17-48`（`LBMConfig(method=Method.CUMULANT, force=[...])` + `create_lb_update_rule`，自述"first working example of lbmpy coupling" `:12`）。
3. 单位系统：settings 可带单位，`src/unit.cpp`/`unit.h` 换算；示例 `pf_velocity/Dynamics.R:201`（`radAngle` 可用 `90d`）。
4. 辅助工具：`tools/RT`、`tools/ADmod.R`、`tools/stlutil`、`tools/compareH5.py`、`tools/csvdiff`、`tools/stensil.plot.R`。

---

## 七、与我们功能点的映射（feature_ledger）

| slug | 本仓实现 | 判定 |
|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE**（两组分分层 Poiseuille 解析对标） | ① 双组元 MCMP `experimental/d2q9_pp_MCMP/Dynamics.c.Rt:93-211`；② 分层 Poiseuille 解析入口剖面（仅相场用）`d3q27_pf_velocity/Dynamics.c.Rt:266`（`U=6Uavg·Y(H-Y)/H²`）、圆管 `:270`，设置 `HEIGHT/Uavg/developedFlow/pipeRadius`（`Dynamics.R:219-225`）；③ 相场自述已对标 "Layered Poiseuille flow"（`Dynamics.c.Rt:20`） | **部分可挂（reference）**：没有"两组分分层 Poiseuille 解析对标"现成算例；有零件（MCMP 双组元 + 分层剖面 + 壁面润湿），需自拼。可吸收"解析剖面作入口 + globals 积分做误差度量"（`updateMyGlobals:721-750`） |
| **NEW:GAP-LBM-MCMP-HIGHDENSITY** | 伪势 `d2q9_scmp/Dynamics.c.Rt:6,543-555,800-858`；相场 `d3q27_pf_velocity/Dynamics.c.Rt:580,586-599,639-647` | **可挂（adapt）**：伪势靠 Lycett-Luo+WMRT 硬扛，压力演化相场才是正解 |
| **NEW:CAP-LBM-PF-ALLENCAHN** | `d3q27_pf_velocity`：`Dynamics.c.Rt:649-669`、`:171-228`、`:528-560`、`Boundary.c.Rt:674-780` | **可挂（adapt）**；风险：曲线壁润湿自述有临时 fix（`:39-41`），不可照抄 |
| **NEW:CAP-LBM-VALIDATION-SURFACE** | `tools/tests.sh:154-190`（`run/fail/exists/sha1/csvconcatenate` 指令式 DSL）；测试在 `tests/external/<model>/*.test`（`tests.sh:121-140`）；`tools/csvdiff`；`tests/solid/`（独立 C++ 单测+makefile） | **可挂（reference）** |
| **NEW:CAP-LBM-CI** | `.github/workflows/cpu_test.yml:7-30`（16 模型矩阵，编译+跑）；`gpu_comp.yml`/`hip_comp.yml`（仅编译）；`other_tests.yml`（`tests/solid`+gcov）；`.github/actions/{install,configure,compile,test,coverage}` | **可挂（reference）**：CI 按"模型"粒度而非"包"粒度 |
| **NEW:CAP-LBM-GPU-FUSED-KERNEL** | margin 数组+指针滑移（`LatticeAccess.inc.cpp.Rt:70-80`）、边界/内部核切分（`Lattice.cu.Rt:500-530`）、存储位宽切换（`LatticeAccess.inc.cpp.Rt:13-33`） | **可挂（adapt）**：非 kernel fusion，但"每方向独立数组+指针平移替代搬运"低成本高收益 |
| **NEW:CAP-LBM-MPI-MULTIGPU** | `MPIStream_A/B` 异步拷贝+`Waitany` 流水+内核插队（`Lattice.cu.Rt:327-386,496-530`） | **可挂（reference）**：单 rank 单 GPU |
| **NEW:CAP-LBM-POROUS-COMPRESSED-INDEX** | `SolidAll/SolidTree/SolidGrid` 三档查找器（`src/SolidContainer.h:12-22`）、区间树（`src/SolidTree.h:8-21`） | **可挂（reference）**：有固体索引，无压缩流体节点表；多孔需自加 |
| **NEW:CAP-LBM-CASE-ORGANIZATION** | `models/<物理类>/<格子>_<方法>/` 三件套（`Dynamics.R`+`Dynamics.c.Rt`+`conf.mk`）；算例独立 `example/<类>/<name>.xml`（134 个）；`make <model>`（`README.md:20-33`） | **可挂（adapt）**：模型=目录=编译单元=CI 矩阵项 |
| **NEW:CAP-LBM-FREESURFACE** | **无** | 不挂（NOT_SUPPORTED），登记为缺口 |

---

## 八、可吸收 Top3 + 风险

**Top1｜相场保守 Allen-Cahn + 压力演化式高密度比多相**（对口 `NEW:GAP-LBM-MCMP-HIGHDENSITY`）
- 分布零阶矩取压力、密度由相场代数给出（`Dynamics.c.Rt:580,586-599`）：绕开高密度比下动量不平衡的核心技巧。
- 界面分布一行保守更新 `h←h-ω_φ(h-heq+0.5Fφ)+Fφ`（`:649-662`）：反扩散系数与界面宽度解耦，比"两分布+源项"稳。
- 黏性力定点自洽迭代（`:624-647`）：力↔应力隐式关系用 2 次定点打断，高密度比必须要。
- 误差面挂点：`updateMyGlobals` 把两相流量/动能/界面位置做成 globals（`:721-750`），可承载 `GAP-LBM-MCMP-POISEUILLE` 的误差度量。

**Top2｜布局/通信工程三件套**（对口 `NEW:CAP-LBM-GPU-FUSED-KERNEL`、`NEW:CAP-LBM-MPI-MULTIGPU`）
- 每方向 margin 独立数组 + `move.pocket` 指针滑移代替数据搬运（`LatticeAccess.inc.cpp.Rt:70-80`）。
- 边界核/内部核两段式 + 异步 D2H 插队（`Lattice.cu.Rt:500-530` + `327-386`）：通信隐藏无需复杂调度器。
- 存储位宽 16/32/64 可切（`LatticeAccess.inc.cpp.Rt:13-33`）：snapshot/伴随省内存零成本。

**Top3｜模板化代码生成 + 声明式模型**（对口 `NEW:CAP-LBM-CASE-ORGANIZATION`、`NEW:CAP-LBM-VALIDATION-SURFACE`、`NEW:CAP-LBM-CI`）
- 声明→符号推导→打印内核，且**边界条件也算出来**（`src/lib/boundary.R:143-198`）：与 TensorPlatform"域外置、零硬编码"同构（域内容放模板目录，平台只给符号工具）。
- 模型=目录=编译单元=CI 矩阵项（`cpu_test.yml:7-30`）。
- `tools/tests.sh` 指令式测试 DSL（`tests.sh:154-190`）：免 Python 胶水表达"跑+比阈值+查文件"。

**风险**：① GPL-3.0 传染——不可复制代码，落地按文献干净室重写并独立验证；② 生成器依赖链重（R+rtemplate+gvector+polyAlgebra 私有 R 生态，`README.md:44-49`），不可移植，需 Python/sympy 重建；③ "能跑"≠"对"——多处自注 TO-DO/临时 fix（`pp_MCMP:117`、`:310-316`、`Boundary.c.Rt:39-41`），只取配方不取"已验证"结论；④ 硬编码 EOS 常数（`d2q9_scmp:546-548`）提醒域参数必须外置。

---

## 九、方法谱系速查
格子：D2Q9/D3Q7/D3Q15/D3Q19/D3Q27（`src/lib/lattice.R:8-92`；D3Q15 `d3q27_pf_velocity/Dynamics.c.Rt:49-53`）｜碰撞：SRT-BGK、MRT（`d2q9:202-268`）、WMRT（`d2q9_scmp:114-125`）、TRT（`d3q27_PSM/Dynamics.R:76-77`）、Cumulant（`d3q27_cumulant`/`src/lib/cumulant.R`）；无 KBC、无 regularized｜力项：Guo 半力速度+矩空间（`pf_velocity:642-668`）、EDM（`d2q9_scmp:497-510`）、Lycett-Luo（`:800-858`）、压力梯度（`pf_velocity:516-519`）｜多相：SCMP、MCMP（实验性）、保守 Allen-Cahn 相场（压力演化）、CSF 颜色梯度、Lee、凝固；无 free-energy/free-surface｜边界：全反弹、Zou-He（符号求解）、对称、移动壁、Bouzidi 插值反弹、曲线壁几何润湿、Neumann/对流出口；无 IBM｜GPU/并行：margin 分段数组+指针滑移、边界/内部核切分、异步 D2H/H2D+Waitany、27 方向 MPI 缓冲、存储位宽压缩；单 rank 单 GPU｜可微/生成：Tapenade 离散伴随+控制流位栈；R/rtemplate 主链+内嵌 sympy/lbmpy+单位系统。

---

## 十、误差判定（诚实声明）

**本单为静态读码盘点，未运行任何算例**：
- 关键量误差：**无（NOT_APPLICABLE）**——未编译、未运行 TCLB，无任何数值场量产出。
- 「是否 <3%」：**不适用**。理由：任务性质是代码级技术盘点（提取实现方式集），非数值验证；且 TCLB 编译链需 R+rtemplate+gvector+polyAlgebra 四个私有 R 包 + MPI + CUDA（`README.md:44-49`），本工作树无该环境，临时安装会污染受控环境。
- 证据类型：本报告全部结论为 **source-confirmed（源码直见）**，未做 runtime-confirmed；`WITHHELD` 只用于两处源码不可判定项（SCMP 密度比上限、仓库 stars/活跃度）。
- **图：无。** 理由：无运行即无场量云图；本主题为代码结构盘点，唯一候选图（架构/谱系框图）的信息已由第二~六节表格完整覆盖，为图而图降低信息密度。

---

## 十一、机器块

见 `docs/oss_inventory/2026-11-09_TCLB_machine_block.txt`（[OSS-INV] + 5×[OSS-SOL]）。