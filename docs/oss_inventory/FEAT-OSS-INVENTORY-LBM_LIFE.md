# 【盘点】joconnor22/LIFE — 开源 LBM 技术深盘（LBM-IBM-FEM 三耦，tier 3）

> 任务 ID: m8552 ｜ 锚点: FEAT-OSS-INVENTORY-LBM ｜ 盘点对象: `joconnor22/LIFE`（54★ / 20 fork / 6 open issues）
> 取码方式: `api.github.com` REST（`git/trees/HEAD?recursive=1` + 41 次 `contents/<path>` base64），**全树 41/41 blob 取齐**；
> 取码 commit: **`2af24286`（2020-06-10，v1.0.3）**，`pushed_at=2022-05-06`，`archived=false`。
> **未 clone 进工作树**（GPL-3.0 仓）；源码只在 `/tmp` 瞬态副本读取，本仓内仅落分析脚本/JSON/图（`runs/m8552/`）。
> 盘面纪律：每条方法结论带 `path:line`；代码里没有的直接写「**无**」，并给反向 grep 证据。

**六要素对应**：①案例=LIFE 仓本体 + 7 个随仓算例（参数表 §七.1，程序化解析）；②怎么做的=§七 前半（REST 逐文件取码 + 干净室重写其核代数做微验证）；③计算结果=§七.2 表（脚本注入，零手抄）；④图=`life_fig1.png`（4 面板，数据来自 `life_probe.json`）；⑤注意点=§八 风险清单 + 各节 ⚠ 标注；⑥误差判定=§九。

---

## 一、概况

| 项 | 值 | 证据 / 说明 |
|---|---|---|
| 主语言 | **C++（C++11，单可执行 `LIFE`）**，6061 行（`src/*.cpp` 4629 + `inc/*.h` 1432） | `makefile:5` `CFLAGS=-O3 -std=c++0x -fopenmp -Wall -Wextra` |
| 依赖 | **LAPACK + Boost.Filesystem**（无 CMake、无包管理、无 Python 胶水） | `makefile:13` `LIB=-llapack -lboost_system -lboost_filesystem`；`README.md:39-43` |
| 许可证 | **GPL-3.0**（`LICENSE:1-2` 为 GPLv3 全文；GitHub API `license.spdx_id=GPL-3.0`） | `LICENSE`；头部注释 `inc/defs.h:3-18` |
| **对我们可用性判断** | **reference-only，禁止代码级复制。** GPL-3.0 强 copyleft：把其源码/派生内核链进 TensorLBM 并对外分发，会要求 TensorLBM 整体转 GPL-3.0（与平台 MIT/Apache 路线冲突）。可行路径只有「读论文级配方 → 干净室重写 → 自建单测对标」，或先取得作者书面授权（仓内无 CLA，`README.md:41-52` 只写 PR 流程）。本报告所有 `method` 字段都按「自己写」理解。 | 同左 |
| 活跃度 | **停滞的研究/教学仓**：master 最后提交 `2020-06-10`（v1.0.3），`pushed_at=2022-05-06`（非 master 分支），6 open issues；无 CI、基线数据不入库（`testing/**` 被 gitignore）、无 release tag | `git log` 前 6：`2af24286 / 4c4c1fe1 / 2e41ec8c / 17319334 / 1164eb94 / 60f67b63`；`.gitignore:6` `/testing/**/`；树内无 `.github/` |
| 定位一句话 | **2D 单相 D2Q9 LBM + 3 点 δ 浸没边界（含 ε 守恒校正，解 `Aε=1`）+ 共旋梁有限元**的流固耦合求解器；主打**细长柔性结构**（Turek–Hron FSI2、倒置旗、PELskin、Honami 植被冠层），OpenMP 共享内存、**无 MPI、无 GPU、无多相**。 | `README.md:3-16`；`inc/defs.h:27-39`（`dims=2, nVels=9`） |

`README.md:5-16` 自述的 10 条 Key Features 与代码一一对应（本仓这点罕见地诚实）：
BGK（`src/Grid.cpp:236-244`）、中心矩算子（`:106-233`，De Rosis 2019）、正则化速度/压力边界（`:387-465`，Latt 2008）、
对流出口（`:468-474`，Yang 2013）、**隐式 IBM**（`src/IBMNode.cpp:51-94` + `src/Objects.cpp:235-321`，Li 2016 / Pinelli 2010）、
共旋梁 FEM（`src/FEMElement.cpp:72-139`）、隐式 Newmark（`src/FEMBody.cpp:112-144`）、LBM↔FEM 映射（`src/FEMBody.cpp:292-340`）、
块 Gauss-Seidel 强耦合（`src/Objects.cpp:26-60`，Degroote 2013）、Aitken 动态松弛（`src/Objects.cpp:175-213`，Irons & Tuck 1969）。

---

## 二、方法谱系

### 2.1 格子
**只有 D2Q9，且硬编码在构造函数里**：`c_s=1/√3`、9 个权重、速度集与反向量表一次写死（`src/Grid.cpp:1246-1250`）；
维度与速度数是编译期常量（`inc/defs.h:27-28`）。**无 D3Q19/D3Q27、无多块、无自适应、无非结构/曲线格**。
速度集为 `{0,0},{1,0},{-1,0},{0,1},{0,-1},{1,1},{-1,-1},{1,-1},{-1,1}`（`src/Grid.cpp:1249`），与
`opposite={0,2,1,4,3,6,5,8,7}`（`:1250`）自洽。

### 2.2 碰撞算子（两条，编译期二选一）
| 算子 | 位置 | 做法 |
|---|---|---|
| **BGK/SRT** | `src/Grid.cpp:236-244` | 推送式单行：`f = f_n + ω(f^eq − f_n) + (1−0.5ω)F`；`ω` 由 `params.h` 的 `tStep` 反推（`inc/params.h:85-95`），`tau=1/ω`、`nu=(tau−0.5)c_s²`（`src/Grid.cpp:1255-1256`） |
| **中心矩（central moments，部分松弛）** | `src/Grid.cpp:106-233`（`#ifdef CENTRAL_MOMENTS`） | 先把 `f` 变换到**以局部速度为原点的中心矩** `k_i`（`:109-133`），只对**剪切矩 `k4,k5` 施加 (1−ω)**（`:129-130`），其余矩直接取平衡值（`k0=ρ,k1=Fx/2,k2=Fy/2,k3=2ρc_s²,k6,k7=½F c_s²,k8=ρc_s⁴`，`:125-133`），再逆变换回 `f`（`:140-233`）。等价于「两松弛率」（`k4,k5` 一套、其余瞬时），数学上接近 TRT/regularised；**不是**完整 MRT、**不是** KBC、**不是** cumulant |
| TRT / MRT / KBC / cumulant / RR | **无** | 全仓无第二松弛率开关、无矩空间变换矩阵、无熵/正则化重构（`grep -i "trt\|mrt\|kbc\|cumulant\|regulariz"` 只命中 "regularised **BC**"，见 §三） |

平衡态有**两套**：BGK 用二阶 Hermite（`:262`）；中心矩路径用**四阶 Hermite 的乘积形式**
`¼ρ w (9cx²ux²+6cx ux−3ux²+2)(9cy²uy²+6cy uy−3uy²+2)`（`:257-259`，同样以 `u_n` 为中心，故与中心矩自洽）。
`CENTRAL_MOMENTS` 默认注释掉（`inc/params.h:28`），只有 LidDrivenCavity 打开（`examples/LidDrivenCavity/params.h:31`）——
即**该算子只在 Re=5000 方腔上被作者自己用过**，属「有实现、验证面窄」。

### 2.3 力项（Guo 半分分裂；IBM 力与体积力两条通道）
- 统一格式：**碰撞里加 `(1−0.5ω)F`**（`src/Grid.cpp:243`），**宏观速度里加 `0.5F/ρ`**（`:297-298`）→ 标准 Guo(2002) 半分分裂。
- 离散化式（BGK 分支专用）：`3 w_v [ Fx(cx−ux+3cx(cx ux+cy uy)) + Fy(cy−uy+3cy(cx ux+cy uy)) ]`，`F = force_xy + force_ibm`（`:267-279`）。
- 两条力通道分开存：`force_xy`（重力/压力梯度，`:1042-1043` 按 `ρ·Drho·g`、`dpdx` 与 `Δx Δt²/Δm` 量纲化）与 `force_ibm`（IBM 每步铺展，`src/IBMNode.cpp:61-94`）。
- **无** shifted-velocity/EDM、**无**粘性应力张量力、**无**外力矩耦合（力矩只走 FEM 侧 `src/FEMElement.cpp:53` 的 `Tsub` 投影）。
- ⚠ **口径不统一（推断，非跑出来的）**：`macroscopic()` 的速度修正只加 `0.5*force_xy`（`:297-298`），**没加** `0.5*force_ibm`；
  而 `IBMNodeClass::updateMacroscopic()` 加的是 `0.5*(force_xy+force_ibm)`（`src/IBMNode.cpp:121-122`）。两者在同一时间步先后写同一批格点，
  故**插值用到的速度取值依赖调用顺序**（`lbmKernel`→`objectKernel` 次序由 `src/Grid.cpp:25-33` 固定）。移植时须先统一口径。

### 2.4 代码组织
`Grid.cpp`（宏观量/平衡态/碰撞+流动/边界/IO/重启）、`IBMNode.cpp`（插值/力/铺展/支撑点）、
`FEMElement|FEMBody.cpp`（梁单元/牛顿-拉夫逊/Newmark）、`Objects.cpp`（耦合循环/ε/几何读入/输出）。
**无算子抽象、无 kernel 生成、无模板元编程**（`#ifdef` 是唯一「多态」，`inc/params.h:26-40`）；逐格标量循环。
状态数组是**扁平化 SoA-字段 / AoS-速度**：各场一个 `vector<double>`（`inc/Grid.h:76-84`），
每个格点 9 个分布函数连续存放 `f[id*9+v]`（`src/Grid.cpp:243/291`）——即「字段级 SoA + 格点内速度级 AoS」。

---

## 三、边界处理（逐一定位）

**先说结论：本仓没有 bounce-back（任何型式），也没有 Zou-He。** 反向 grep `bounce` 在 `src/`+`inc/` **0 命中**；
固体壁/结构一律靠**正则化边界（Latt 2008 / Yu & Fan 家族）** 或 **IBM 力**承担。

| 边界 | 位置 | 做法与性质 |
|---|---|---|
| **速度/壁面/自由滑移/压力：正则化边界** | `src/Grid.cpp:387-465` | ① 拆 `f⁺`（法向入射）与 `f⁰`（切向）`:404-413`；② 速度型用 `ρ=(2f⁺+f⁰)/(1−n·u_n)` 闭合 `:416-418`，压力型用 `u_n=n(1−(2f⁺+f⁰)/ρ)` 反解 `:421-423`；③ 未知方向先按 `f^eq+(f_opp−f^eq_opp)` 建非平衡 `:430-451`；④ 求非平衡应力 `Sxx,Syy,Sxy` `:457-459`；⑤ **用 `w_v/(2c_s⁴)[(cx²−c_s²)Sxx+(cy²−c_s²)Syy+2cx cy Sxy]` 重构**并叠加平衡态 `:462-464`。角点只外推密度、其余外推切向速度 `:390-395`, `:498-545`。这是「正则化 + 非平衡应力重构」，比 Zou-He 稳，且把 速度/压力/自由滑移/角点 统一到一条代码路径 |
| 壁面（无滑移） | `src/Grid.cpp:315-323` | 把 `u_n≡0` 代入上面的正则化 BC（**不是**半步反弹：无 `O(½Δx)` 壁面偏移，但也**无**曲率/斜壁精度增强） |
| 均匀压力出口 | `src/Grid.cpp:355-369` | 固定 `ρ_n=rho_in[j]`（初值 `ρ0=1`，`src/Grid.cpp:1279`；**`dpdx` 不入口 → 压力 BC 不携带压差**，压差只能走体积力通道，§2.3） |
| 自由滑移 | `src/Grid.cpp:338-352` | 法向速度置零 + 切向零梯度（`:342-348`）再走正则化 BC |
| **对流出口（Yang 2013）** | `src/Grid.cpp:372-377` + `:468-474` + `:477-495` | 出口面均速 `uOut=⟨u_x⟩_{Nx-1}` `:483-487`；对流速度 `delU=−uOut/2·(3u_{Nx-1}−4u_{Nx-2}+u_{Nx-3})`（**二阶单侧差分**）`:492-493`；只补 `f2,f6,f8` `:471-473`。**仅支持右边界**（硬检查 `:918-921`） |
| 周期 | `src/Grid.cpp:924-951`（把壁面类型设 `eFluid`）+ `:240` 的 `((i+dx+Nx)%Nx)` | 取模实现，**无独立周期标志**；周期与边界共存靠「把边界类型改成 eFluid」 |
| 入口软启动 | `src/Grid.cpp:547-556` | `(1−cos(πt/T))/2`（`inc/params.h:29` `INLET_RAMP 2.0`） |
| 法向/角点判定 | `src/Grid.cpp:498-545` | 由 `i==0/Nx-1/j==0/Ny-1` 推法向；角点两侧皆流体则报错退出 `:527-528` |
| 曲率/接触角/移动壁/润湿 | **无** | 无曲面反弹（Bouzidi/Filippova-Hänel）、无接触角、无移动壁（**刚性 IBM 体静止**，§3.1-8） |
| IBM（结构壁） | `src/IBMNode.cpp:26-136` | 见 §3.1 |

### 3.1 浸没边界（IBM）——技术含量最高的一块
1. **δ 核**：**3 点二次核（Roma/Peskin 家族，支撑 1.5Δx）**：`|d|>0.5` 时 `(5−3|d|−√(−3(1−|d|)²+1))/6`，否则 `(1+√(1−3|d|²))/3`（`inc/Utils.h:220-233`）。**不是** 4 点核。分区一致性复算：`Σδ−1 ≤ 2.2e-16`（§七.2 表 1）。
2. **支撑点搜索**：以 `round(x/Dx)` 为中心、窗 ±2 格、取 `|d|<1.5`（`src/IBMNode.cpp:144-178`）→ **每维最多 3 点，2D 最多 9 点**；缓冲常量正好 `suppSize=9`（`inc/defs.h:39`），越界 `ERROR`（`src/IBMNode.cpp:171-172`）——**零余量：加宽核/3D 化第一刀就砍这里**。
3. **插值（Euler→Lagrange）**：`ρ_i=Σ_s δ ρ_s`、`m_i=Σ_s δ ρ_s u_s`（`src/IBMNode.cpp:26-48`）。
4. **直接力（Li 2016 型）**：`F = 2.0·(velScale·interpRho·vel_body − interpMom)`（`src/IBMNode.cpp:51-58`）；系数 2 隐含 `Δt=1` 与半分约定；`velScale=Δt/Δx`（`:54`）。
5. **铺展（Lagrange→Euler）**：`F_grid += F·ε_i·ds_i·δ`（`src/IBMNode.cpp:61-94`）；`ds_i` = 到同体最近标记点距离 / Δx（`:182-204`）。
6. **ε 守恒校正（Pinelli 2010 RKPM 思路，本仓 IBM 的关键件）**：解**稠密系统** `A_ij=Σ_{s∈supp(i)} δ_i(x_s)δ_j(x_s)ds_j`、`b≡1`（`src/Objects.cpp:235-321`），LAPACK `dgetrf_/dgetrs_`（`src/Utils.cpp:288-311`）。可选 `UNI_EPSILON` 把所有体合成一个大系统（`src/Objects.cpp:238-252`, `:315-320`）。实测：不校正总力守恒残差 **11%–37.5%**，校正后 **≤9e-16**（§七.2 表 2）。
7. **标记点去重叠**：间距 `<0.5Δx` 删点；柔性-柔性对则报错退出（`src/Objects.cpp:866-901`，阈值 `:882`）。
8. **刚体无刚体动力学**：`eRigid` 体只在 `src/IBMBody.cpp:45-76` 建静态标记环，`vel≡0`（`src/IBMNode.cpp:218-220`）；全仓无位置/姿态积分 → **只能算固定障碍物（圆柱/栅板），不能算涡激振动/操纵运动**。

---

## 四、多相 / 多组元（重点）——**本仓：无**

**明确结论：无 SCMP、无 MCMP、无 free-energy、无 phase-field/Allen-Cahn、无颜色梯度、无 free-surface、无相互作用势 ψ、无 recoloring、无表面张力。**

反向证据（可复现）：
- 只有**一套**分布函数 `f/f_n`（`inc/Grid.h:83-84`）、**一套**密度 `rho/rho_n`（`:78-79`）；多组元至少需要第二套分布或第二密度场；
- `grep -rni "shan|chen|free.?energy|phase.?field|allen.?cahn|color.?gradient|surface.?tension|recolor|chemical|density.?ratio|pseudopotential|van.?der"` 于 `src/ inc/` **0 命中**（唯一相似词是 `UNI_EPSILON` 编译开关，非物理）；
- 晶格类型枚举只有 `{eFluid, eWall, eVelocity, eFreeSlip, ePressure, eConvective}`（`inc/defs.h:45`），即**单相流体的边界分类**，无相态标志；
- 唯一「相」概念是结构/流体之分（IBM 标记 vs 格点），不是组分。

故任务要求的「互作用势形式 / 力项加法 / 密度比能力 / spurious current 处理」在本仓**全部 NOT_APPLICABLE**——不编造。
本仓唯一可借给多相路线的是**边界与 IBM 通道**（§三）；多相配方须从 listLBM/MPLBM-UT/Palabos 系取（见 `docs/oss_inventory/` 既有盘点）。

---

## 五、GPU / 并行

| 维度 | 本仓实现 | 指针 |
|---|---|---|
| 并行模型 | **仅 OpenMP 共享内存**（27 处 `#pragma omp`：`Grid.cpp` 6 / `IBMNode.cpp` 6 / `Objects.cpp` 14 / `Utils.cpp` 1） | `src/Grid.cpp:48`, `:55`, `:65`, `:78`, `:87` |
| 负载调度 | `schedule(guided)` 为主；主循环 **`collapse(2)`** 二维展平 | `src/Grid.cpp:65-75` |
| 数据布局 | 字段级 **SoA** + 格点内速度级 **AoS**（`f[id*9+v]`）；一维扁平 `id=i*Ny+j`（**y 连续**） | `inc/Grid.h:76-84`；`src/Grid.cpp:70` |
| 流动算法 | **push + 取模环绕**：`recv_id=((i+cx+Nx)%Nx)*Ny+((j+cy+Ny)%Ny)`，碰撞流动一行，`f_n.swap(f)` 换缓冲 | `src/Grid.cpp:42-45`, `:240`, `:232` |
| 内存合并/向量化 | 差：外层 `v` 循环 9 次写 **9 个不同 site**（间隔 `9×8=72 B`）；无 SIMD pragma、无对齐、无 streaming；`%` 每方向每格一次（无 magic offset） | `grep -i "omp simd\|__m256\|avx"` 0 命中 |
| 归约确定性 | 关键设计：`#ifdef ORDERED` 用 `ordered` 替换 `atomic`，**牺牲并行度换位精确可复现**（IBM 铺展 + FEM 残差） | `src/IBMNode.cpp:76-93`；`src/Objects.cpp:71-90`, `:130-139`；开关 `inc/params.h:38` |
| MPI/域分解 | **无**（`grep -i mpi` 0 命中）；单机单域，无 halo、无通信隐藏 | — |
| GPU | **无**（`grep -i "cuda\|opencl\|hip\|sycl\|kokkos"` 0 命中） | — |
| 性能度量 | 仅 `MLUPS = Nx·Ny/(1e6·loopTime)`（`loopTime` 每步均值） | `src/Grid.cpp:595-610` |
| 可扩展性隐患 | `UNI_EPSILON` 把所有标记点合成**稠密 `n×n`**，每子迭代一次 `dgetrf`：`Honami` 类（128×~31≈4k 标记）即 `O(n³)≈6e10` flops/子迭代 | `src/Objects.cpp:238-252`, `:264-311` |
| 输出缺陷（**源码可确认**） | `writeVTK` 写 `ImageData` 时循环 `for j { for i { rho[i*Ny+j] } }`，文件流顺序=内存 `i*Ny+j`（x 慢、y 快），而 VTK 规定 **x 最快** → **输出场在 x/y 上转置/错序（Nx≠Ny 时是打乱，不只是转置）**；速度场同理 | `src/Grid.cpp:857-889`（对比 `:813` `WholeExtent`） |

---

## 六、可微 / 代码生成

- **可微：无。** 无 autograd/伴随/符号求导痕迹（`grep -i "autograd\|adjoint\|tapenade\|symbolic\|codegen"` 0 命中）；全套手写标量内核 + LAPACK 直接解。
- **代码生成：无。** 唯一「编译期多态」是 `#ifdef`（`CENTRAL_MOMENTS / UNI_EPSILON / ORDERED / INLET_RAMP / WOMERSLEY / INITIAL_DEFLECT / VTK / FORCES / TIPS`，`inc/params.h:26-40`）；且「算例 = 一份 `params.h` 头文件」（改算例必须重编译，`README.md:20-24` 自承）。
- **对可微路线的结构性障碍（重要）**：`objectKernel` 是**固定点迭代 + 稠密线性解 + 分支式 ε 重算**（`src/Objects.cpp:26-60`, `:235-321`），且**流体场在一个时间步内不重解**（§八 R4）——既不隐式到流体、也没写成可微定点扫描；接 AD 需重写耦合层，不是「加个 `grad`」。

---

## 七、与我们功能点的映射

### 7.1 随仓算例与运行点（程序化解析 `examples/*/params.h`，脚本 `runs/m8552/life_probe.py`）

| 算例 | 网格 | Δx (m) | Δt (s) | ω | τ | Ma | 物理 Re | 步数 | 说明 |
|---|---|---|---|---|---|---|---|---|---|
| ChannelFlow | 501×51 | 0.02 | 5.0e-4 | 1.8605 | 0.5375 | 0.0433 | 100（U·H/ν） | 60 000 | 抛物线入口 + 压力出口，正则化 BC 的最小回归 |
| Cylinder | 441×83 | 0.005 | 2.5e-4 | 1.8868 | 0.5300 | 0.0866 | 100（U·D/ν，D=0.1） | 80 000 | **静止**圆柱绕流（无自由度） |
| TurekHron | 501×83 | 0.005 | 2.5e-4 | 1.8868 | 0.5300 | 0.0866 | 100（U·D/ν） | 80 000 | **FSI2 公开基准**：ρ_s=10⁴、ρ_f=10³、E=1.4e6、20 单元、CLAMPED |
| LidDrivenCavity | 101×101 | 0.01 | 1.0e-3 | 1.9763 | 0.5060 | 0.1732 | **5000**（U·L/ν） | 50 000 | **唯一启用中心矩算子**的算例 |
| InvertedFlag | 631×451 | 1.9e-3 | 6.0e-6 | 1.7551 | 0.5698 | 0.0405 | 452.7 | 720 000 | 倒置旗颤振；E=3e9、ρ_s=1780 |
| PELskin | 735×91 | 6.7e-4 | 3.7e-5 | 1.9048 | 0.5250 | 0（静止启动） | — | 270 000 | **周期 x + 常压差 `dpdx=4550 Pa/m` 驱动** + 10 片柔性板；`UNI_EPSILON` |
| Honami | 3106×91 | 0.0333 | 6.9e-4 | 1.9104 | 0.5234 | 0.0361 | 240 | 900 000 | 边界层入口 + **128 片植被**（20 单元/片），本仓最大算例 |

运行点全部落在 `Ma≤0.173`、`τ∈[0.506,0.570]`（`ω∈[1.755,1.976]`）——**保守不可压区间**；且 `Δt=Δx²ν_lat/ν_p`（`src/Grid.cpp:1257-1258`）是**扩散缩放**：网格加密时 `Ma∝Δx→0` 而 `τ` 基本固定。对我们案例库的直接含义：**「一张算例表 = 网格/物性/ω/步数」** 便于复现，但不可运行期切换（风险 R3）。

### 7.2 复算结果（干净室重实现其核代数，非运行其二进制）

**表 1 — 3 点 δ 核与支撑窗（`inc/Utils.h:220-233` + `src/IBMNode.cpp:139-179`）**

| 量 | 值 |
|---|---|
| 分区一致性 `max|Σ_s δ(x_s−X)−1|`（2001 个分数偏移） | **2.2e-16** |
| 每维支撑点数 | {2, 3}（`|d|==0.5` 落在核断点上时 2 点，其余 3 点） |
| 2D 最大支撑数 | **9**（= `suppSize`，零余量） |

**表 2 — ε 守恒校正（`src/Objects.cpp:235-321`）**

| 标记间距 ds/Δx | cond(A) | ε 范围 | 不校正总力残差 | 校正后残差 |
|---|---|---|---|---|
| 0.5 | **2.5e16（数值奇异）** | [−11.96, +13.80] | 37.5% | 4.4e-16 |
| 0.7 | 2.4e3 | [−23.47, +31.01] | 33.4% | 8.9e-16 |
| 1.0 | 7.6 | [0.747, 1.607] | 25.0% | 1.1e-16 |
| 1.3 | 3.0 | [0.916, 1.301] | 18.4% | 1.1e-16 |
| 1.5 | 1.9 | [0.971, 1.172] | 12.5% | 0 |
| 2.0 | 1.2 | [0.897, 0.950] | 11.1% | 0 |

> 结论：**ε 校正不是可选优化，是力守恒的必要条件**（25% 量级总力误差会直接进 Cd/Cl 与结构响应）；但 `cond(A)` 在 `ds→0.5Δx` 发散，
> 而仓内去重叠阈值正好 `0.5Δx`（`src/Objects.cpp:882`）——**守卫线恰好落在奇异点上**（R2）。工程建议：标记点间距 ≥ `1.0Δx`（cond≤7.6）。

### 7.3 功能点对照表（能挂尽挂；挂不上写原因）

| 功能点 slug | 本仓有无 | 指针 / 结论 | transfer |
|---|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | **无多相**；但**单相版「周期+常压差驱动」子机制齐备**：`PELskin` = 周期 x + `dpdx` 体积力 + 通道层流 | `examples/PELskin/params.h:47-56`（`dpdx=4550`、`WALL_LEFT/RIGHT=eFluid`）；力通道 `src/Grid.cpp:1042-1043`。**只借驱动/边界，不借多相** | reference（子机制） |
| MCMP 高密度比 | **无** | §四反向证据 | 不可映射 |
| 自由面 | **无** | 同上（无相态 `cell_type`、无质量/填充率记账） | 不可映射 |
| 相场 Allen-Cahn | **无** | 同上 | 不可映射 |
| GPU 融合 kernel | **无** | §五（0 CUDA/OpenCL/SIMD pragma） | 不可映射 |
| 分布式多卡 | **无** | §五（0 MPI） | 不可映射 |
| 稀疏/多孔/复杂几何 | **部分替代**：静态刚性 IBM 标记环可表达任意曲线/阵列障碍（圆柱、栅板、128 片植被）；**无 mask bounce-back、无稀疏压缩索引、无曲壁插值反弹** | `src/IBMBody.cpp:45-76`（圆）、`:79-135`（线）；刚性体静止 `src/IBMNode.cpp:218-220` | reference |
| 案例组织 | **有（可借鉴范式）**：`params.h`（编译期网格/物性/BC/步数）+ `geometry.config`（**运行期**几何：`CIRCLE`/`FILAMENT` 关键字 + 起始位置/间距/长度/单元数/E/ρ）+ `examples/run-example.sh`；**支持重启中途增删体** | `inc/params.h:43-100`；`input/geometry.config:8-19`；`src/Objects.cpp:324-489`；`README.md:26-37` | **adapt** |
| 测试与验证面 | **有（本仓最值得抄的工程习惯）**：`store-ref-data.sh` 固定 `nSteps=500` 生成基线；`run-tests.sh` 逐字节 `diff -r Results RefData` 全绿才算过；`ORDERED` 保证归约确定性 → **位精确回归可行**；plus 7 算例含 Turek–Hron 公开基准与 Cd/Cl 输出 | `testing/store-ref-data.sh:27`；`testing/run-tests.sh:100`；`inc/params.h:38`；`src/Objects.cpp:491-539`（Cd/Cl `:531-534`） | **fits**（配容差化改造） |
| CI | **无**（树内无 `.github/`）；测试靠人手跑，且 `RefData` 不入库（`.gitignore:6`） | — | 不可映射（**负面样例**：基线不入库=CI 无法自动跑） |
| `NEW:LBM-IBM-MDF-STENCIL`（IBM 多重直接力/ε 校正） | **有，且是「隐式变体」**：不是迭代 MDF，而是解一次 `Aε=1`（Pinelli 2010） | `src/Objects.cpp:235-321`；`src/IBMNode.cpp:26-94`；`inc/Utils.h:220-233` | **fits**（2D 直接用；3D 需扩 `suppSize`） |
| `NEW:LBM-REGULARISED-BC-YUFAN`（正则化速度/压力/自由滑移/角点组合边界） | **有** | `src/Grid.cpp:387-465` | **adapt** |
| `NEW:LBM-CENTRAL-MOMENT-COLLISION`（部分松弛中心矩） | **有** | `src/Grid.cpp:106-233`, `:257-259` | **adapt** |
| `NEW:LBM-FSI-BLOCK-GS-AITKEN`（位移 Aitken + 残差范数 + 二阶预测） | **有** | `src/Objects.cpp:26-60`, `:175-213`；`src/FEMBody.cpp:244-289` | **reference**（耦合口径见 §八 R4） |
| `NEW:LBM-COROTATIONAL-BEAM-FEM`（共旋 2 节点梁 + Newmark + Newton） | **有** | `src/FEMElement.cpp:72-139`, `:202-262`；`src/FEMBody.cpp:26-144` | **reference** |
| `NEW:CAP-LBM-VALIDATION-SURFACE` | **有（弱形式）** | 同「测试与验证面」行 | **adapt** |
| `NEW:CAP-LBM-CASE-ORGANIZATION` | **有** | 同「案例组织」行 | **adapt** |
| `NEW:CAP-LBM-CI` | **无** | 同 CI 行 | 不可映射 |

---

## 八、可吸收 Top3 + 风险

**Top1｜IBM 的 ε 守恒校正（对口 `NEW:LBM-IBM-MDF-STENCIL`，transfer=fits）**
`A_ij=Σ_s δ_iδ_j ds_j`、`Aε=1`，`A` 稠密但 `n` 只是**单体的标记点数**（几十~几百），LAPACK 直接解代价可接受；它一次解决「插值/铺展不互伴 → 总力丢 11–37%」的老问题（§七.2 表 2）。对我们的价值**可量化、可单测**：单测 = 给定标记分布，断言 `‖Aε−1‖<1e-12` 且 `cond(A)<1e3`（间距 ≥1Δx）。搬过来只需两件：3 点 δ 核 + 一次稠密解。
**注意 `dgetrf/dgetrs` 的 `trans='T'` 是「列主序当行主序」的技巧**（`src/Utils.cpp:288-311`），自己实现别把这个 `T` 抄进去。
风险：① 3D 支撑点从 9 涨到 27/64，`suppSize` 必须动态；② `UNI_EPSILON` 的全局大系统 `O(n³)` 不可取，应按体重建或用块对角/局部迭代。

**Top2｜正则化边界作「统一边界面」（`NEW:LBM-REGULARISED-BC-YUFAN`，transfer=adapt）**
一份代码覆盖 速度入口/壁面/自由滑移/压力出口 + 角点 + 非平衡应力重构（`src/Grid.cpp:387-465`），比「Zou-He + 各边界各写一遍」更省分支、更稳（正则化抑制非平衡噪声）。配套**对流出口**（`:468-495`，二阶单侧差分对流速度）是开边界（船后长尾迹/远场）常用件。
风险：① 与中心矩/高阶平衡态混用时「重构非平衡」口径要一致（作者只在 BGK 下全量用）；② 本实现的压力 BC **不携带压差**（`rho_in≡ρ0`），驱动必须走体积力——我们要显式带 Δp，否则「入口速度+出口压力」算例会退化成零流量。

**Top3｜位精确回归测试面（`NEW:CAP-LBM-VALIDATION-SURFACE` + `NEW:CAP-LBM-CASE-ORGANIZATION`，transfer=fits/adapt）**
`store-ref-data.sh` 固定步数生成基线 + `run-tests.sh` 逐字节 diff + `ORDERED` 保证 OpenMP 归约确定性（`inc/params.h:38`；`src/IBMNode.cpp:76-93`）→ **同一 ISA 上 0 误差回归**。对平台面的含义：CI 里做「金标准位精确 + 物理量容差」双层门（位精确抓重构意外、容差门抓数值退化），且**基线必须入库**（本仓 `.gitignore:6` 把 `testing/**` 排掉=自废回归能力，反面教材）。
风险：位精确 diff 对编译器/架构极脆（集群 loong64 vs GitHub x86 必挂）→ 按「固定容器位精确」与「跨架构 1e-10 相对容差」两档拆。

### 风险清单（移植决策用）
- **R1 GPL-3.0 传染**：不可复制代码，只能读配方重写（§一）。
- **R2 ε 系统守卫线选错**：去重叠阈值 `0.5Δx`（`src/Objects.cpp:882`）正好是 `cond(A)→1e16` 的点；我们应取 ≥`1.0Δx` 并在建体时算 `cond(A)` 报警。
- **R3 算例=头文件**：改网格/物性需重编译（`README.md:20-24`），与「域数据外置」铁律相反——只借「一张表描述一个算例」的形式，不借「改头文件重编译」的机制。
- **R4 耦合口径名不副实**：README 称 "strongly-coupled block Gauss-Seidel"（`README.md:14`），但**流体在一时间步内只推进一次**（`src/Grid.cpp:25-33`：`lbmKernel()` 先跑完，`objectKernel()` 的子迭代只对结构位移做定点迭代 + Aitken，`src/Objects.cpp:26-52`；插值用冻结的 `f`）→ **结构侧隐式、流体侧显式交错**，力被铺展进「下一步」的碰撞。不是错，但**不能当 IQN-ILS/全隐式对标**；要全隐式须每子迭代重解 LBM（代价 10–20×）。
- **R5 输出错序**：`writeVTK` 的 x/y 顺序与 VTK 约定不符（`src/Grid.cpp:857-889`）→ 后处理拿到转置/错序云图。
- **R6 刚体无自由度**：不能算涡激振动/操纵运动（§3.1-8），与「操纵性」战役需求不匹配。
- **R7 精度策略单一**：全 `double`、无精度开关；我们若走混合精度，这份实现无参考价值。

---

## 九、误差判定（诚实声明）

1. **本单未编译、未运行 LIFE**：`makefile:13` 需 `liblapack-dev` + `libboost-*-dev`，本控制面容器与 loong64 节点均无该两套头/库（探针：`ls /usr/include/lapacke.h /usr/include/boost/version.hpp` 均 No such file；`ldconfig -p | grep -E "liblapack|libboost"` 空），安装系统包超出「只改工作树」边界 → **不做 runtime 确认**。所有物理结论均为 **source-confirmed（源码直见）**。
2. **已做的定量复算（runtime-confirmed，但对象是「我重实现的代数」，不是其二进制）**：
   - 表 1 分区一致性 `2.2e-16`、支撑点上界 9 = `suppSize` → 「3 点核 + 9 点缓冲刚好够用」的判定误差 **<1e-15，远好于 3%**。
   - 表 2 ε 校正把总力守恒残差从 `11%–37.5%` 压到 `≤9e-16` → 该机制收益量级被定量确认（相对改善 >1e15 倍）；`cond(A)` 在 `0.5Δx` 处 `2.5e16` → R2 定量成立。
3. **无法给的数字**：Cd/Cl、频率、振幅、ε 对*实际*算例的影响幅度——必须跑二进制才有，**本单不给**（不编造）。故「关键量 vs 基准是否 <3%」对本单 **NOT_APPLICABLE**：零运行、无物理产出；能下的判定只有「代数级正确性 ≤1e-15」与「源码结论逐条有指针」。
4. **图**：`runs/m8552/life_fig1.png`（4 面板：(a) 有/无 ε 的力守恒残差 vs 标记间距；(b) `cond(A)` 与 `max|ε−1|`；(c) 7 算例运行点 `Ma–ω`；(d) 模块 LOC/并行度），所有数值由 `life_probe.py` 注入，零手抄。
5. **取码通道预算（对后续 24+ 仓盘点有用的操作性结论）**：`api.github.com` 未认证配额 = **60 请求/小时/IP**，本仓一次盘点就花 **42 次**（1 × `git/trees` + 41 × `contents`），即**每 IP 每小时只能盘 ~1.4 个仓**。本单第二次复跑 `fetch_life.py` 时在 `examples/TurekHron/params.h` 处遇到 `403 rate limit exceeded`（重试 4 次均失败），属**预期现象非故障**；后续大部队盘点应①带 token 提配额，或②优先 `git clone`（SNI 间歇窗），或③把已取快照在 `/tmp` 留存复用（本单即用此策略：首轮取齐后所有分析与复算都在本地快照上做，未再打 API）。脚本 `runs/m8552/fetch_life.py` 已内置该提示。


---

## 十、机器块

[OSS-INV]
repo: joconnor22/LIFE
lang: C++ (C++11 + OpenMP; 依赖 LAPACK + Boost.Filesystem)
license: GPL-3.0
methods: D2Q9（硬编码 src/Grid.cpp:1246-1250）；BGK/SRT（src/Grid.cpp:236-244）+ 中心矩部分松弛算子（#ifdef CENTRAL_MOMENTS, src/Grid.cpp:106-233，四阶 Hermite 平衡态 :257-259）；力项 Guo 半分分裂 (1-0.5ω)F（:243）+ u+0.5F/ρ（:297-298），离散式 :267-279
multiphase: 无（单组分单相：仅一套 f/rho，inc/Grid.h:78-84；无 ψ/序参量/表面张力/recoloring 符号，反向 grep 0 命中）
boundary: 正则化速度/压力/自由滑移/角点边界（Latt 2008 家族，src/Grid.cpp:387-465）+ 对流出口 Yang 2013（:468-495）+ 周期=壁面 eFluid 取模；无 bounce-back（grep 0 命中）、无 Zou-He、无曲壁插值反弹；固体壁靠 IBM（src/IBMNode.cpp:26-136）
gpu: 无（0 CUDA/OpenCL/MPI；OpenMP 27 处 pragma，push 算法 + 逐点 AoS f[id*9+v]，atomic/ORDERED 二选一保确定性）
summary: 2D 单相 D2Q9 LBM + 3 点 δ 浸没边界（ε 守恒校正解 Aε=1，Pinelli 2010）+ 共旋梁 FEM/Newmark 的细长结构 FSI 求解器，OpenMP 共享内存、无 MPI/GPU/多相。
         可吸收价值集中在 IBM ε 隐式校正（总力守恒 11-37% → 1e-16）、正则化统一边界、中心矩算子、位移 Aitken 耦合与位精确回归测试面。
[/OSS-INV]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:LBM-IBM-MDF-STENCIL
tag: ibm-epsilon-gram-implicit-correction
method: 3 点 Roma/Peskin δ 核（支撑 1.5Δx，2D 最多 3×3=9 点）插值 ρ 与动量到标记点，取恢复力 F=2(ρ_i u_body − m_i)（Δt=1），铺展时乘 ε_i·ds_i；ε 由稠密系统 A_ij=Σ_s δ_i(x_s)δ_j(x_s)ds_j、Aε=1 一次解出（LAPACK dgetrf/dgetrs），使「插值→铺展→再插值」在标记点上还原总力；可选把所有体合成一个大系统。
files: src/Objects.cpp:235, src/Objects.cpp:283, src/Objects.cpp:303, src/Objects.cpp:306, src/IBMNode.cpp:26, src/IBMNode.cpp:51, src/IBMNode.cpp:61, src/IBMNode.cpp:73, src/IBMNode.cpp:139, inc/Utils.h:220, src/Utils.cpp:288
conventions: 3 点核（非 4 点），|d|>1.5 截断；每维最多 3 点、2D 最多 9 点 = suppSize（inc/defs.h:39）；ds 单位 Δx；力系数 2 隐含 Δt=1 与半分分裂；ε 解用 trans='T' 抵消 LAPACK 列主序；实测总力守恒：无 ε 11-37%，有 ε ≤9e-16；cond(A) 在 ds=0.5Δx 处 2.5e16（奇异）
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:LBM-REGULARISED-BC-YUFAN
tag: regularised-bc-nonequilibrium-stress-reconstruction
method: 把边界格 f 拆成法向入射 f⁺ 与切向 f⁰：速度型用 ρ=(2f⁺+f⁰)/(1−n·u) 闭合，压力型用 u_n=n(1−(2f⁺+f⁰)/ρ) 反解；未知方向先按 feq+(f_opp−feq_opp) 建非平衡，求和得 Sxx/Syy/Sxy，再用 w/(2c_s⁴)[(cx²−c_s²)Sxx+(cy²−c_s²)Syy+2cxcySxy] 重建并叠加平衡态；角点只外推密度/切向速度。配套二阶单侧差分的对流出口。
files: src/Grid.cpp:315, src/Grid.cpp:338, src/Grid.cpp:355, src/Grid.cpp:387, src/Grid.cpp:404, src/Grid.cpp:416, src/Grid.cpp:421, src/Grid.cpp:457, src/Grid.cpp:462, src/Grid.cpp:468, src/Grid.cpp:477, src/Grid.cpp:498
conventions: cs²=1/3；法向由 i==0/Nx-1/j==0/Ny-1 判定；压力 BC 固定 rho_in（本仓 ≡1，不携带 Δp）；对流出口仅右边界；D2Q9 专用（重构张量硬编码 2D 分量）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:LBM-CENTRAL-MOMENT-COLLISION
tag: central-moment-partial-relaxation
method: 以局部速度 u_n 为原点把 9 个分布变换到中心矩 k0..k8：k0=ρ、k1/k2=F/2、k3=2ρc_s²、k4=(1−ω)·Σf(cx²−cy²)、k5=(1−ω)·Σf cx cy、k6/k7=½F c_s²、k8=ρc_s⁴，再逆变换回 fStar（显式展开 9 个方向）；平衡态用四阶 Hermite 乘积形式 ¼ρw(9cx²ux²+6cx ux−3ux²+2)(…)。等价两松弛率（剪切矩松弛、其余瞬时）。
files: src/Grid.cpp:106, src/Grid.cpp:109, src/Grid.cpp:125, src/Grid.cpp:129, src/Grid.cpp:140, src/Grid.cpp:225, src/Grid.cpp:257, inc/params.h:28
conventions: 需 #ifdef CENTRAL_MOMENTS 开启；仅 D2Q9；F 含 force_xy+force_ibm；作者只在该仓 Re=5000 方腔用过（examples/LidDrivenCavity/params.h:31），无独立验证面
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:LBM-FSI-BLOCK-GS-AITKEN
tag: displacement-aitken-underrelaxation-fixed-point
method: 每时间步内 do{预测(2.5U_n−2U_nm1+0.5U_nm2)→插值→FEM 全量重解(Newton-Raphson, TOL=1e-10, MAXIT=20)→残差}while(res>1e-8 且 it<20)；子迭代 k≥2 时用 Aitken：relax=−relax·(r_{k−1}·(r_k−r_{k−1}))/|r_k−r_{k−1}|²，位移 U=U_km1+relax·(U−U_km1)，残差范数按 ref_L·√(DOF) 归一。收敛后一次性铺展 IBM 力。
files: src/Objects.cpp:26, src/Objects.cpp:30, src/Objects.cpp:52, src/Objects.cpp:152, src/Objects.cpp:175, src/Objects.cpp:189, src/Objects.cpp:199, src/FEMBody.cpp:26, src/FEMBody.cpp:40, src/FEMBody.cpp:244, src/FEMBody.cpp:254, src/FEMBody.cpp:259
conventions: MAXIT=20（src/Objects.cpp:30）、subTol=1e-8（inc/params.h:81）；relaxMax=1.0（inc/params.h:80） 且首步裁剪 |relax|≤relaxMax；注意流体场在本步内冻结（非全隐式，见 R4）；ORDERED 保证归约可复现
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:LBM-COROTATIONAL-BEAM-FEM
tag: corotational-2node-beam-newmark-newton
method: 2 节点 3 自由度（u,v,θ）平面梁：局部内力只用轴向伸长 u=(L²−L0²)/(L+L0) 与端弯矩 M1=2EI/L0(2θ1+θ2)、M2=2EI/L0(θ1+2θ2)，几何非线性由 K_NL 的 V0/L0、F0/L0 项承担；M 用一致质量（ρAL/420 的 140/156/22L0/54/13L0…），K_L 为标准梁刚度；Newmark-β(α=0.25,δ=0.5) 做时间积分，牛顿-拉夫逊线性化解 LAPACK。IBM 标记点按 ζ 映射到单元并用形函数插值位移/速度。
files: src/FEMElement.cpp:72, src/FEMElement.cpp:75, src/FEMElement.cpp:80, src/FEMElement.cpp:109, src/FEMElement.cpp:120, src/FEMElement.cpp:202, src/FEMElement.cpp:216, src/FEMElement.cpp:231, src/FEMElement.cpp:255, src/FEMBody.cpp:112, src/FEMBody.cpp:129, src/FEMBody.cpp:147, src/FEMBody.cpp:292
conventions: 无剪切变形（Euler-Bernoulli 系，2 节点 3 DOF）；A=Δx·h、I=Δx·h³/12（厚度 h 取 geometry.config 第 8 列）；只输入 E 与 ρ（无泊松比，2D 梁不需要）；CLAMPED/SUPPORTED 两种 BC 由 bcDOFs 裁行
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:CAP-LBM-VALIDATION-SURFACE
tag: bitwise-refdata-regression-plus-benchmark-cases
method: 基线生成脚本把 nSteps/tinfo/tVTK/tRestart 统一改写为 500 后跑出 Results/ 并连同 params.h+geometry.config 存为 RefData；回归脚本逐案例 make clean && make、跑 2 次（TurekHron 二次测 restart）、再用 diff -r Results RefData 做逐字节比对，全绿才算过；配合 #ifdef ORDERED 让 OpenMP 归约可复现，使位精确比对可行。物理验证面=7 个算例（含 Turek-Hron FSI2 公开基准）+ TotalForces.out 的 Cd/Cl（0.5ρU²L 归一）。
files: testing/store-ref-data.sh:27, testing/store-ref-data.sh:33, testing/run-tests.sh:56, testing/run-tests.sh:100, inc/params.h:38, src/Objects.cpp:491, src/Objects.cpp:531, src/IBMNode.cpp:76
conventions: 基线 nSteps 固定 500；比对为字节级（无容差）；RefData 不入库（.gitignore:6）→ 无 CI；Cd/Cl 用 ND=0.5·ref_rho·ref_U²·ref_L；restart 用二进制快照 + 头部一致性校验（src/Grid.cpp:1084-1104）
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:CAP-LBM-CASE-ORGANIZATION
tag: compiletime-params-plus-runtime-geometry-config
method: 一个算例=一个目录，两份输入：params.h（网格/物性/边界类型/ω/步数，编译期常量，改则须重编译）+ input/geometry.config（运行期文本：CIRCLE/FILAMENT 关键字行，含编号、起始坐标、阵列间距、半径或 长度/厚度/角度/FLEX|RIGID/单元数/BC/密度/E）；geometryReadIn 先扫一遍算 body/node 数预留内存，再逐行构造；重启时可就地增删体。
files: inc/params.h:43, inc/params.h:85, input/geometry.config:9, input/geometry.config:13, input/geometry.config:18, input/geometry.config:19, src/Objects.cpp:324, src/Objects.cpp:358, src/Objects.cpp:410, src/Objects.cpp:430, examples/run-example.sh:38
conventions: 关键字 6 列通用 + 2~9 列专用；间距为 0 表示单体；角度单位=度（读入后 ×π/180）；FLEXIBLE 才会创建 FEMBody；改 params.h 必须 make clean；运行期只支持 input/geometry.config 这一个约定路径
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: GAP-LBM-MCMP-POISEUILLE
tag: single-phase-periodic-x-plus-constant-dpdx-driving
method: 本仓无多相，但提供了该 slug 所需驱动子机制的单相实现：左右壁设 eFluid 得到 x 周期，压差以常体积力 dpdx 注入（force_xy=(ρ·Drho·gravityX+dpdx)·ΔxΔt²/Δm，每格一次、不随 ρ 更新），配合正则化壁面对上下边界闭合，形成压力驱动的通道层流；重启/输出正常。
files: examples/PELskin/params.h:47, examples/PELskin/params.h:52, src/Grid.cpp:1042, src/Grid.cpp:924, src/Grid.cpp:387
conventions: dpdx 单位 Pa/m；周期靠把边界类型改成 eFluid（无独立周期标志）；本仓压力 BC 不携带 Δp，压差只能走体积力；ρ 变化时体积力不重算（若 g≠0 需注意）
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: joconnor22/LIFE
slug: NEW:LBM-IBM-STATIC-OBSTACLE-SET
tag: static-immersed-marker-obstacles
method: 「稀疏/多孔/复杂几何」在本仓用静态刚性 IBM 标记环表达：geometry.config 的 CIRCLE 行按周长生成等距标记点（num=floor(2πr/Δx)），可一行写多体、按间距阵列（space_x/space_y），标记点速度恒 0 即为无滑移障碍；配合 3 点 δ 核 + ε 校正把障碍力铺到格子，等价于任意曲线/栅板/植被阵列的多孔障碍，但**不是** mask bounce-back、也无稀疏索引压缩。
files: src/IBMBody.cpp:55, src/IBMBody.cpp:64, src/IBMNode.cpp:218, src/Objects.cpp:410, src/Objects.cpp:866, input/geometry.config:18
conventions: 标记间距 = 2πr/floor(2πr/Δx) ≈ Δx；<0.5Δx 的标记会被删（刚性-刚性谁删谁不确定）；刚性体无自由度（不能动）；体间重叠只做标记去重，不做几何求交
transfer: reference
[/OSS-SOL]
