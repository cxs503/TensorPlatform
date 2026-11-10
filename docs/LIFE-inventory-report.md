# 【盘点】joconnor22/LIFE — LBM-IBM-FE 流固耦合求解器

> 代码级技术盘点 · 2026-11-09 · 基于 master HEAD commit `2af242866d`（2020-06-10，版本 v1.0.3，`inc/defs.h:31-32`）
> 本地镜像 `/tmp/life` 与 GitHub HEAD 逐 blob SHA 比对一致（38 个文件 0 mismatch），全部行号指针以该镜像为准。

---

## 一、概况

| 项 | 值 |
|---|---|
| 语言 | C++（`-std=c++0x`，`makefile:5`），src+inc 共 6061 行 |
| 许可证 | **GPL-3.0**（`LICENSE`，各源文件头注释同） |
| 许可证对我们的可用性判断 | ⚠️ **GPL 传染性**：若 TensorLBM 链接/复制其代码则须整体 GPL 化；**仅可参考算法与公式，不可复制代码**。定位为算法"教科书"级参考实现 |
| 活跃度 | 低：master 最后提交 `2af242866d` 2020-06-10（GitHub API：54 stars / 20 forks / 6 open issues；created 2019-10-18）。单人项目（Joseph O'Connor） |
| 定位 | **2D 格子 Boltzmann + 隐式浸没边界 + 有限元** 流固耦合求解器，专注细长柔性结构（梁/膜）与不可压流耦合（`README.md:2-3`） |

---

## 二、方法谱系

### 格子模型
- **D2Q9（唯一）**：维度硬编码 `inc/defs.h:35-36`（`dims=2; nVels=9`）
  - 权重 `w = {4/9, 1/9×4, 1/36×4}`：`src/Grid.cpp:1248`
  - 离散速度 `c`（rest,E,W,N,S,NE,SW,NW,SE 排列）：`src/Grid.cpp:1249`
  - 反向速度表 `opposite = {0,2,1,4,3,6,5,8,7}`：`src/Grid.cpp:1250`
  - 声速 `c_s = 1/√3`：`src/Grid.cpp:1247`
  - **无 D3Q19/D3Q27 支持**

### 碰撞算子（`streamCollide`，`src/Grid.cpp:103`）
1. **BGK（默认）**：`src/Grid.cpp:243`
   - `f_recv = f_n + ω(f_eq − f_n) + (1−0.5ω)·F_lattice`（push-for-collide-stream 组织：先算 recv_id 再写目标格，`src/Grid.cpp:240-243`）
   - 平衡态 2 阶 Hermite：`src/Grid.cpp:262`
2. **Central Moments（CM，De Rosis et al. 2019）**：`src/Grid.cpp:106-220`（`#ifdef CENTRAL_MOMENTS` 编译期切换）
   - 局部静止参考系（shifted by u）：`src/Grid.cpp:116-117`
   - 仅 k4（非对角应力差）/k5（剪切矩）做松弛：`k4=(1−ω)k4Pre; k5=(1−ω)k5Pre`：`src/Grid.cpp:129-130`
   - 力矩注入：k1/k2=½F（半时间步力）、k6/k7=½F·c_s²：`src/Grid.cpp:126-127,131-132`
   - 精确矩：k0=ρ、k3=2ρc_s²、k8=ρc_s⁴：`src/Grid.cpp:125,128,133`
   - 逆变换：9 个显式多项式直接还原 f*：`src/Grid.cpp:142-220`
   - **注意**：CM 模式平衡态为 4 阶 Hermite（`src/Grid.cpp:259`），与 BGK 的 2 阶（`src/Grid.cpp:262`）不同

### 力项
- **Guo 型显式力（BGK 模式）**：`latticeForce`，`src/Grid.cpp:267-279`
  - `F_lattice = 3w_i[F_x(c_x−u_x+c_x·3(c·u)) + F_y(c_y−u_y+c_y·3(c·u))]`：`src/Grid.cpp:278`
  - 合力 = `force_xy`（重力+压力梯度，`src/Grid.cpp:1042-1043`）+ `force_ibm`（IBM 体积力）：`src/Grid.cpp:274-275`
  - 宏观速度修正 `u = (Σc_i f_i + 0.5F_xy)/ρ`：`src/Grid.cpp:297-298`
- **CM 模式力处理**：无显式 Guo 项，力经中心矩 k1/k2/k6/k7 注入：`src/Grid.cpp:126-127,131-132`
- **Womersley 振荡压力梯度**（`#ifdef WOMERSLEY`）：余弦调制 dpdx：`src/Grid.cpp:59-60`

---

## 三、边界处理

### 正则化边界（regularised BC，Latt & Chopard 2008）
`regularisedBC`，`src/Grid.cpp:387-466`：
- **Zou-He 型闭式**：速度型 BC 密度 `ρ=(2f⁺+f⁰)/(1−n·u)`：`src/Grid.cpp:417`；压力型 BC 法向速度 `u_n = n(1−(2f⁺+f⁰)/ρ)`：`src/Grid.cpp:422`
- 未知分布函数先以反对称重构 `f = f_eq + (f_opp − f_eq,opp)`：`src/Grid.cpp:443,450`
- 非平衡应力 Sxx/Syy/Sxy 由 f−f_eq 二阶矩：`src/Grid.cpp:457-459`
- 最终重构 `f = f_eq + (w_i/2c_s⁴)[(c²_x−c_s²)Sxx + (c²_y−c_s²)Syy + 2c_xc_y Sxy]`：`src/Grid.cpp:464`
- 角点：外推密度（`src/Grid.cpp:394`）+ buried link 置 f_eq（`src/Grid.cpp:441`）

### 边界类型（`applyBCs`，`src/Grid.cpp:302-385`）
- `eWall` 无滑移：`src/Grid.cpp:315-323`
- `eVelocity` 指定速度（含 ramp 系数）：`src/Grid.cpp:327-335`
- `eFreeSlip` 法向零+切向零梯度：`src/Grid.cpp:339-352`
- `ePressure` 指定密度：`src/Grid.cpp:356-369`
- `eConvective` 对流出口（Yang 2013）（case 在 `src/Grid.cpp:373`，函数 `src/Grid.cpp:468-476`）；对流速度二阶后向差分 `delU=−uOut/2·(3u₁−4u₂+u₃)`：`src/Grid.cpp:492`

### IBM 边界（核心特色，隐式浸没边界）
- **Dirac delta 核**：Peskin 3 点正则化，支撑半径 1.5：`inc/Utils.h:220-232`
- 插值（Euler 网格→Lagrange 标记点）：`src/IBMNode.cpp:26-47`
- 力计算（直接强迫型）：`force = 2·(Dt/Dx)·(ρ·u_target − ρ·u_interp)`：`src/IBMNode.cpp:51-58`（公式在 `:57`）
- 力散布（点力→Euler 网格，`F·ε·ds·δ`）：`src/IBMNode.cpp:61-95`
- ε（光滑参数）：组装 IBM 插值矩阵 A（Σδ_iδ_j·ds）解 A·ε=1（LAPACK）：`src/Objects.cpp:234-320`
- 支撑点搜索 / ds 计算：`src/IBMNode.cpp:139,182`
- 几何类型：刚性圆（`src/IBMBody.cpp:45-51`）、柔性/刚性细丝 filament（`src/IBMBody.cpp:79-92`）

### 无 bounce-back
- 全仓 grep "bounce" 零命中；壁面一律走 regularised BC（非平衡重构），非半程反弹。

---

## 四、多相/多组元

## **无**

本仓为**单相不可压流 + 流固耦合**求解器：
- 密度 ρ 全局初始化为 1.0 且无演化方程：`src/Grid.cpp:1244,1269`
- 无 Shan-Chen 伪势、无自由能、无相场、无颜色梯度、无自由面（全仓 grep 零命中）
- 无多组元分布函数、无状态方程、无 spurious current 处理（无相界面）

**结论：LIFE 不涉及任何多相/多组元 LBM 方法。**

---

## 五、GPU/并行

### 共享内存并行（OpenMP）
- LBM 主循环：`#pragma omp parallel`（`src/Grid.cpp:48`）+ `omp for schedule(guided) collapse(2)`（`src/Grid.cpp:65-74`）
- 宏观量：`src/Grid.cpp:78-87`；IBM 插值/散布：`src/Objects.cpp:108`、`src/IBMNode.cpp:74-90`
- FEM 组装：`omp parallel for ordered schedule(dynamic,1)` 或 `schedule(guided) reduction`：`src/Objects.cpp:72-74`
- **确定性开关**：`#define ORDERED`（`inc/params.h:36`）——ordered 归约换跨线程数可重复性；散布用 `omp atomic` 或 ordered 二选一（`src/IBMNode.cpp:78-90`）

### 数据布局
- **AoS**：`f[id*nVels+v]`（每格 9 个分布函数连续，`src/Grid.cpp:243`）；`u[id*dims+d]` 交错
- 索引 `id = i*Ny + j`（`src/Grid.cpp:70`），j 为内层循环 → 沿 j 方向内存连续，访问合并

### 无 GPU / 无 MPI
- 全仓 grep CUDA/OpenCL/MPI 零命中；纯单节点共享内存
- 编译：`CFLAGS=-O3 -std=c++0x -fopenmp -Wall -Wextra`（`makefile:5`）；链接 `-llapack -lboost_system -lboost_filesystem`（`makefile:22`）

---

## 六、可微/代码生成

- **无可微支持**：纯手写 C++，无 autograd/JAX/Tape 集成
- **无代码生成**：碰撞多项式全部手工展开（`src/Grid.cpp:142-220`）
- **编译期多态**：`#ifdef CENTRAL_MOMENTS / WOMERSLEY / UNI_EPSILON / ORDERED` 预处理宏切换算法路径
- **参数编译**：`params.h` 为 C++ 头文件，改参数必须重编译（`README.md` "Case Definition" 节明示）

---

## 七、与我们功能点的映射

| 我们的功能点 (feature_ledger slug) | LIFE 覆盖情况 | 映射 |
|---|---|---|
| GAP-LBM-MCMP-POISEUILLE | ❌ 无多相（第四节） | 不映射 |
| MCMP 高密度比 | ❌ 无多相 | 不映射 |
| 自由面 | ❌ 无 | 不映射 |
| 相场 Allen-Cahn | ❌ 无 | 不映射 |
| GPU 融合 kernel | ❌ 无 GPU（第五节） | 不映射 |
| 分布式多卡 | ❌ 无 MPI | 不映射 |
| 稀疏/多孔 | ⚠️ 间接：IBM 浸没物体可当多孔阻力 | NEW:ibm-porous-media（adapt） |
| 案例组织 | ✅ `examples/` 7 个完整算例（ChannelFlow/Cylinder/Honami/InvertedFlag/LidDrivenCavity/PELskin/TurekHron），params.h+geometry.config 双文件制 | fits（参考） |
| 测试与验证面 | ✅ `testing/run-tests.sh` 回归测试（diff Results/ vs RefData/） | fits |
| CI | ❌ 无 `.github/`，仅 shell 脚本 | 不映射 |
| **FSI 流固耦合** | ✅ 核心能力（隐式 IBM + 强耦合 + Aitken） | NEW:ibm-fsi-strong-coupling |
| **Central Moments 碰撞** | ✅ 完整 D2Q9 CM 实现 | NEW:central-moments-D2Q9 |
| **正则化边界** | ✅ Latt 2008 完整实现（含角点） | NEW:regularised-boundary |

> 对照 TensorLBM 现状（`/root/TensorLBM/src/tensorlbm/solver.py:51,165,217,274,376` 已有 BGK/MRT/RLBM/TRT，**无 CM**）：CM 是真实缺口，LIFE 的 CM 实现有直接参考价值。

---

## 八、可吸收 Top3 + 风险

### Top 1：Central Moments 碰撞算子（reference）
- **价值**：CM 碰撞提高高 Re 稳定性；LIFE 给出 D2Q9 CM 正/逆变换全部显式公式 + 力矩注入方式，TensorLBM 现有碰撞族（BGK/MRT/RLBM/TRT）中无 CM，属真实缺口
- **文件**：`src/Grid.cpp:106-220`（正变换+松弛+逆变换）、`src/Grid.cpp:259`（4 阶 Hermite 平衡态）、`src/Grid.cpp:126-132`（力矩注入）
- **吸收方式**：**reference**——按公式在 TensorLBM 自实现（torch 化），不复制 GPL 代码
- **风险**：仅 D2Q9，推广 D3Q19/D3Q27 需自行推导矩空间；CM 力耦合与 Guo 不同，需守恒律/解析解验证

### Top 2：隐式 IBM + 强耦合 FSI 方案（reference）
- **价值**：直接强迫型隐式 IBM（3 点 Dirac，支撑 1.5）+ block Gauss-Seidel 子迭代（MAXIT=20，subTol=1e-8）+ Aitken 动态松弛 + 共旋梁 FEM（Newmark α=0.25/δ=0.5 + Newton-Raphson TOL=1e-10）
- **文件**：`src/Objects.cpp:25-62`（耦合循环）、`src/Objects.cpp:175-199`（Aitken）、`src/IBMNode.cpp:26-95`（插值-力-散布）、`src/Objects.cpp:234-320`（ε 求解）、`src/FEMBody.cpp:26-68`（NR 循环 TOL=1e-10/MAXIT=20 在 `:40-41`）、`src/FEMBody.cpp:112-127`（Newmark）、`inc/params.h:76-77`（Newmark 参数）
- **吸收方式**：**reference**——算法架构与 Aitken 策略可借鉴；梁单元内力公式（`src/FEMElement.cpp:72-99`）可对照 TensorFEM
- **风险**：GPL 不可复制；仅 2D 梁（2 节点×3 DOF），推广 3D 壳/实体工作量大

### Top 3：正则化边界 + 回归测试框架（reference + fits）
- **价值**：Latt 2008 regularised BC 清晰实现（Zou-He 型闭式 + 非平衡应力重构 + 角点处理）；`testing/run-tests.sh` 全算例 diff 回归（含 restart 测试）
- **文件**：`src/Grid.cpp:387-466`（BC）、`src/Grid.cpp:468-497`（对流出口）、`testing/run-tests.sh:44-100`（diff 判据在 `:100`）
- **吸收方式**：BC 公式 **reference**；测试框架模式（RefData 基线 + 逐算例 diff）**fits**，可直接套用于 TensorLBM 验证面
- **风险**：regularised BC 在曲边界精度逊于 bounce-back 变体；diff 判据为二进制精确匹配，无物理量容差

---

## 机器块

[OSS-INV]
repo: joconnor22/LIFE
lang: C++
license: GPL-3.0-only
methods: D2Q9;BGK+CentralMoments(DeRosis2019);Guo-force(BGK)+CM-moment-forcing
multiphase: 无
boundary: regularised(Latt2008)+convective(Yang2013)+IBM(Peskin3pt-dirac); 无bounce-back
gpu: 无(仅OpenMP共享内存)
summary: 2D LBM-IBM-FEM 流固耦合求解器，BGK/CM 双碰撞+正则化 BC+隐式 IBM 强耦合（Aitken 松弛）。GPL 许可仅可参考不可复制；无多相、无 GPU、无 MPI。
[/OSS-INV]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:central-moments-D2Q9
tag: central-moments-collision
method: D2Q9 central moments (De Rosis 2019): f→k in frame shifted by u, relax only k4/k5 by omega, inverse transform via 9 explicit polynomials to f*. Force injected as moments k1/k2=half-step F and k6/k7=F*cs^2.
files: src/Grid.cpp:106-220, src/Grid.cpp:259, src/Grid.cpp:126-132
conventions: c_s=1/sqrt(3); omega=1/tau; k3=2*rho*cs^2; k8=rho*cs^4; 4th-order Hermite equilibrium in CM mode
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:regularised-boundary
tag: regularised-BC-Latt2008
method: Zou-He-type closure rho=(2*fplus+fzero)/(1-n.u) for velocity BCs, u_n=n*(1-(2*fplus+fzero)/rho) for pressure BCs; unknowns via anti-symmetric reconstruction then f=feq+(wi/2cs^4)[(cxi^2-cs^2)Sxx+(cyi^2-cs^2)Syy+2cx*cy*Sxy] from off-equilibrium stress. Corner: extrapolate rho + buried link=feq.
files: src/Grid.cpp:387-466, src/Grid.cpp:468-497
conventions: normalVector from getNormalVector(); BCVec stores boundary site IDs; ramp coefficient on velocity BCs
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:ibm-fsi-strong-coupling
tag: implicit-IBM-Aitken-relaxation
method: Direct-forcing implicit IBM (Peskin 3-point Dirac, support 1.5dx). Strong FSI coupling: block Gauss-Seidel subiteration (MAXIT=20, subTol=1e-8) with Aitken dynamic under-relaxation. FEM: corotational 2-node beam (3 DOF/node) + Newmark (alpha=0.25, delta=0.5) + Newton-Raphson (TOL=1e-10).
files: src/Objects.cpp:25-62, src/Objects.cpp:175-199, src/IBMNode.cpp:26-95, src/FEMBody.cpp:26-68, inc/Utils.h:220-232
conventions: epsilon solved from IBM interpolation matrix diagonal (A*eps=1, LAPACK); ds=min marker spacing; force=2*(Dt/Dx)*(rho*u_target - interpMom)
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:ibm-porous-media
tag: ibm-immersed-objects
method: IBM bodies (rigid circle / rigid+flexible filament) represented by Lagrange markers; force spreading via Dirac delta creates effective porous resistance. Epsilon smoothing from interpolation matrix.
files: src/Objects.cpp:234-320, src/IBMBody.cpp:45-92
conventions: geometry.config defines body shapes; UNI_EPSILON for global smoothing parameter
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: test-verification-surface
tag: regression-test-framework
method: Shell-script regression testing: run all example cases, diff Results/ against stored RefData/ (binary-exact, no tolerance). TurekHron case also exercises restart capability.
files: testing/run-tests.sh, testing/store-ref-data.sh
conventions: params.h copied from RefData into inc/ before build; diff --exclude=Log.out
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: case-organization
tag: params-header-plus-geometry-config
method: Two-file case definition: params.h (compile-time domain/flow/BC setup, rebuild required) + input/geometry.config (runtime-read IBM body list). 7 reference cases shipped (ChannelFlow, Cylinder, Honami, InvertedFlag, LidDrivenCavity, PELskin, TurekHron).
files: examples/run-example.sh, examples/TurekHron/params.h, input/geometry.config
conventions: geometry.config may be empty for pure LBM cases; restart supported across geometry changes
transfer: fits
[/OSS-SOL]
