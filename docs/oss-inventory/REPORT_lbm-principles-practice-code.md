# 【盘点】lbm-principles-practice/code —— Krüger《LBM: Principles and Practice》官方配套代码

任务：FEAT-OSS-INVENTORY-LBM（mission 8286）· 单项目深盘 · tier 1
盘点人：建设公民智能体 · 日期：2026-11-09
证据方式：**代码本体逐文件读码**（codeload 源码快照，非文档辅助），并**实际编译运行**两个参考程序取得数字。

## 快照与可达性（诚实声明）

| 项 | 值 |
|---|---|
| 仓库 | `lbm-principles-practice/code`（默认分支 `master`；`main` 不存在 → 404） |
| 取码途径 | ① `git clone` 超时（SNI 间歇窗，120 s 未完成被收割）；② `api.github.com` **403 rate limit exceeded**（出口 IP `112.53.120.226`，本机无 token）；③ **`codeload.github.com` 成功**：`HTTP 200, 41627 B` |
| 快照 sha256 | `d8bbf0baf85622e20da7c9bee522cfe5350bd0a342ee32447feb20c37a4169df`（`/tmp/kpp_master.tgz`，2026-11-09 06:06 UTC） |
| 文件数 / 体积 | 58 文件 / 412 KB 解包，8969 行（cpp+h+cu+m+py+sh） |
| **未取证项** | API 403 ⇒ **commit SHA / pushed_at / star 数未取到**；本报告所有"活跃度"判断只基于目录内容，不臆造日期 |

---

## 一、概况

- **语言**：C++（`chapter5/6/8/9/11/13`，C++98 风格，无公共库化）+ CUDA（`chapter13/gpu/*.cu`）+ MATLAB（`chapter5/6`、`chapter13/matlab`）+ Python3（`chapter13/python3/vortexdecay.py`）。
- **许可证**：**MIT**。`LICENSE.txt:1` "Copyright (c) 2016 Timm Krüger, Halim Kusumaatmaja, Alexandr Kuzmin, Orest Shardt, Goncalo Silva, Erlend Magnus Viggen"；`LICENSE.txt:3` 授予 "use, copy, modify, merge, publish, distribute, sublicense"；`LICENSE.txt:5` 要求保留版权与许可声明。
- **许可证对我们的可用性判断**：**MIT ⇒ 可直接取用/改写/闭源再分发**，唯一义务是保留版权行 + 许可声明。对私有仓（ship-performance-platform）与开源仓（TensorLBM）均无障碍，优于 GPL 系 LBM 代码。**纪律**：吸收时新文件头保留原作者版权 + MIT 声明，并在 commit/文档记 provenance。
- **活跃度**：`README.md` 仅 7 行（只说明是本书配套代码，无版本/无 CHANGELOG/无 issue 模板）；**无 CI、无构建系统、无测试**（全树 `find` 无 `*.yml/*.yaml/Makefile/test*`）。每目录自带 `compile.sh`：`chapter13/cpu/compile.sh:5-9`（`g++ -std=c++98 -pedantic -O3 -Wall`）、`chapter13/gpu/compile.sh:6`（写死 `-arch sm_20`，需按卡改）、`chapter13/mpi/nonblocking/compile.sh:5-9`（`mpic++`）。⇒ **判为教学静态仓，非工程化持续维护项目**：代码质量与物理约定高，工程化程度≈0。
- **定位一句话**：**教科书级"最小可运行 + 物理约定明确"的参考实现集**——一算例一 `main`，专为"讲清一种方法/一种边界处理"而写；**最适合抄物理约定与验证模板，不是可复用求解器**。

---

## 二、方法谱系

**格子**：全仓**只有 D2Q9**（无 D3Q15/19/27）。
- 常量串：`chapter9/shanchen.cpp:44-46`（`cx[]/cy[]/weight[]`，**零方向在前**）；MATLAB 侧同一套但**零方向在后**（`chapter6/force_poiseuille_BB.m:37-39`，注释 "note zero direction is last"）。
- 方向编号有两种并存约定：零方向在前（`chapter9/shanchen.cpp:44-46`）与"静息单开 + 8 方向"（`chapter13/cpu/LBM.cpp:112-116` 有 ASCII 图 `6 2 5 / 3 0 1 / 7 4 8`）。**跨章复用须先对齐编号**。

**碰撞**：

| 谱系 | 位置 | 组织方式 |
|---|---|---|
| **BGK** | `chapter9/shanchen.cpp:252-254`（`omega=1./tau`）、`:273`（`f2=f1*(1-ω)+feq*ω+forcing`）；`chapter13/cpu/LBM.cpp:97-160`（融合 push 内 `omtauinv*ft+tauinv*feq`） | 单弛豫；`nu=(2τ-1)/6` 反解 τ（`chapter6/force_poiseuille_BB.m:31`） |
| **TRT（双弛豫 + magic Λ）** | `chapter8/gaussian_2d_trt.cpp:47-49`（`lambda=1/12; omegaminus=1.95; omegaplus=1/(0.5+lambda*omegaminus/(1-0.5*omegaminus))`）、`:116-133`（`gplus/gminus` 对称/反对称分解后分别松弛）；`chapter8/gaussian_1d_magic6.cpp:46-47,120-126`（Λ=1/6）；`chapter8/gaussian_1d_magic12.cpp:46-47,121-126`（Λ=1/12，且 plus/minus 角色互换 ⇒ 演示两侧等价）；`chapter8/cylinder.cpp:51-63,159-208`（`weights_trt[]`+`complement[]` 表 + `feq_plus/feq_minus` 解析式） | **把 Λ 显式参数化 + 平衡表驱动**，是我们 TRT 接口最值得照抄的组织方式（见 §八 Top2） |
| **MRT / KBC / cumulant / regularized** | **全仓无**（全树 grep `MRT|KBC|cumulant|regulariz` 零命中） | — |

**力项**：本仓实质是**力项方案的对照实验仓**（最大干货）：
- **Guo（二阶速度空间 × 二阶时间/空间）**：`chapter9/shanchen.cpp:261-263`（`forcing=w_i(1−0.5ω)[(3(c−u)+9c(c·u))·F]`，配 `:273`）；`chapter11/IBLBM_2D_Poiseuille.cpp:354-362`（**逐方向手展开 9 行**，可当查表）；通用式 `chapter6/force_poiseuille_BB.m:177-183`。
- **力项方案四象限**：`chapter6/force_poiseuille_BB.m:52-57` 给出开关语义（1 阶空间 = 不含 ω 修正；2 阶空间 = 乘 `(1−ω/2)`；1 阶速度空间 = 仅 `3w c·F`（Buick–Greated）；2 阶速度空间 = Guo），实现在 `:157-184`。
- **shifted-velocity / 半力动量修正**：`chapter6/force_poiseuille_BB.m:129-137`（`u=Σf c + F/2`，注释 "considers half-force correction"）；`chapter11/IBLBM_2D_Poiseuille.cpp:349-351`（`vel=u+0.5(F+gravity)/ρ`，注释 "Compute the force-shifted velocity"），而 `:410,412-417` 的 `momenta()` 明确注释**不含**力修正 ⇒ **"原始动量"与"力平移速度"两套量分开存**，多相耦合必须沿用此约定。
- **反例/对照**：`chapter6/force_poiseuille_BB.m:154` 用**线性化不可压平衡** `feq=w(ρ+3c·u)`（非标准二阶式），与标准式并存，属教学刻意对照。

---

## 三、边界处理（逐一定位）

| 型式 | 位置 | 关键细节 |
|---|---|---|
| **半程 bounce-back（静止壁）** | `chapter6/force_poiseuille_BB.m:214-223`；`chapter5/poiseuille_BB.m:123-137`；`chapter8/cylinder.cpp:214-227` | MATLAB 版在 **streaming 之后**回填（标准 half-way 时序） |
| **半程 BB + Ladd 动量修正（运动壁）** | `chapter11/IBLBM_2D_Poiseuille.cpp:381-396`（`:383` 注释 "Ladd's momentum correction term is included for moving walls"；`:390-391,395-396` 的 `±6 w_i ρ u_wall`） | x 向周期用 `%`（`:389`） |
| **Anti-bounce-back（Dirichlet 标量）** | `chapter8/film_antibb.cpp:156-179`（`f2[i]=-f2[opp]+2w_i C_wall`）、`chapter8/cylinder.cpp:222` | **标量（浓度/温度）Dirichlet 最简实现**，可直接搬到我们 ADE/温度 BC |
| **Inamuro 方法** | `chapter8/film_inamuro.cpp:157-215`（`:165` 先由已知分布反解 `dense_temp=(C_in−Σf_known)/Σw_unknown` 再按权重分配） | 入流/出流/墙/自由壁四段；`:221-...` 是"streaming 后施加"变体 `update_bounce_back_after_stream` |
| **湿节点 3 方案（equilibrium / 非平衡外推 / 非平衡 BB≈Zou-He 族）** | `chapter5/couette_wetnode.m:42-46`（开关）+`:111-146`（实现）；需先反解边界密度 `:116-123`（`rho=1/(1∓v)(f9+f1+f3+2(f4+f7+f8))`）；`:128-131` 平衡格式、`:133-137` 非平衡外推、`:138-145` 非平衡 BB。同构见 `chapter5/poiseuille_wetnode.m:47-51,151-`、`chapter6/force_poiseuille_wetnode.m:50-` | **全仓无 "Zou-He" 命名**，但 non-equilibrium BB 即 Zou-He 家族（诚实标注：关键字零命中，属内容判定） |
| **周期压力 BC（PBBC）** | `chapter6/force_poiseuille_BB.m:190-198`（`f(in)=w(ρ_in+3c·u)+(f_{NX−1}−feq_{NX−1})`）；`:89-90` 由 gradP 反推 `rho_inlet=3(NX−1)gradP+rho_outlet`；驱动组合 `:60-86`（纯力 / 50-50 / 线性分布） | Poiseuille 的第二种驱动方式（力驱动 vs 压差驱动） |
| **零梯度 / 自由滑移 free wall** | `chapter8/film_antibb.cpp:183-190`（出口整列拷贝）、`:192-205`（free wall 镜像方向拷贝 = specular）、`:207-215`（注释掉的"最简版"） | 标量无通量/流出 |
| **IBM（Peskin 2/4 点核）** | `chapter11/IBLBM_2D_Poiseuille.cpp:463-524`（`spread`：拉格朗日力→欧拉格点，`:512` 双线性权重乘积）、`:529-...`（`interpolate`）、`:587-601`（`stencil()`：`==2` 三角核 `1−|d|`，`==4` 四点核分段式）、`:430-458`（`:448` 内力 `F=−k(x−x_ref)·area`） | 主循环顺序固定 `:161-201`：interpolate → 更新位置 → 算节点力 → spread → LBM |
| **曲率/插值 BB（Bouzidi–Firdaous / Filippova–Hänel）** | **无**（`curvature|Bouzidi|filippova` 零命中）。`chapter8/cylinder.cpp:229-300` 用**阶梯逼近 + 三态节点标记**（圆外 −1、内部 1，两遍扫描把贴固体的流体点标 0 作边界节点） | 冰区/船体曲面必须自研曲率 BB；本仓只能参考"边界节点标记"预处理 |
| **3D Zou-He / 棱角处理** | 仅 `chapter8/film_inamuro.cpp:209` 起有 "Corners (averaging procedure)" 角点平均 | — |

---

## 四、多相/多组元（重点）

### 4.1 有什么：单一 Shan–Chen 实现（SCMP + 二组元），只有一种互作用势

`chapter9/shanchen.cpp` 是全仓唯一多相文件（429 行，单文件自洽）：

- **互作用势（只用一种）**：`ψ(ρ)=ρ0[1−exp(−ρ/ρ0)]`，`chapter9/shanchen.cpp:74-76`；`rho0=1`（`:37`）。
- **力项加法（SC 最近邻 + Guo 力项）**：
  - `computeSCForces()` `:79-109`：`F_s=−ψ_s(x)Σ_i w_i c_i ψ_ss(x+c_i)`；`i` 从 1 起**排除静息方向**（`:90`）；**只取 8 个最近邻**（无次近邻/E·c 高阶形式）；周期取模（`:91-92`）；自作用与交叉作用统一走 `g[s][ss]`（`:99-100`）。
  - 力矩阵装配 `:191-197`：`g[0][0]=gA; g[1][1]=gB; g[0][1]=g[1][0]=gAB`。
  - 力→分布：**Guo 二阶力项** `:261-263`（含 `(1−0.5ω)`）。
  - 速度：**重心速度 + 半力修正** `computeVelocity()` `:142-161`（`:151-152` `u+=0.5F`；`:154-156` 除总密度；总密度 `computeTotalDensity()` `:112-120`）。
  - 压力：`p=Σ_s ρ_s/3 + Σ_sΣ_ss g[s][ss]ψ_sψ_ss/6` —— `:126-139`（`:134` 为互作用项）。
- **两组分（MCMP）初始化**：`:215-229` 平面界面（上半 `ρ0=0.1/ρ1=0.9`，下半互换，总密度=1），配 `nfluids=2, gA=gB=0, gAB=6`（`:29,34-36,194-197`）。**这是"只有交叉作用、无自作用"的分离机制**，与"两组分分层 Poiseuille"所需的"互不相溶 + 各自黏度"**部分吻合**（`tauA/tauB` 已分开 `:30-31,185-189`）。
- **单组分液滴（汽液共存）**：`:201-211`（半径内 `ρ=rhol=2.1`、外 `ρ=rhog=0.15`）；初值直接置平衡分布 `:232-246`，`u=0`。
- **验证面（最值钱的多相资产）**：`calculateDeltaPressure()` `:326-377`——4×4 盒平均取汽/液压力（`:333-353`）；沿 `y=ny/2` 找 ρ 跨过 `ρ_av` 处线性插值得半径（`:357-367`）；输出 `γ=Δp·r`（`:369`）并打印 `ρ_g,ρ_l,Δp,r,γ`（`:371-374`）。**是可脚本化的 Young–Laplace 标定器**。

### 4.2 本仓**没有**的多相能力（诚实列出）

| 能力 | 状态 | 依据 |
|---|---|---|
| MCMP 分层 Poiseuille（两组分各占半通道、各自动黏度、界面共流） | **无算例** | `chapter9` 只有静止液滴/平面界面，且**完全周期边界**（`:271-272` 两向取模），**无壁面** ⇒ 产生不出 Poiseuille 剖面 |
| 高密度比（≫10） | **无手法** | 只有一组参数与单一 ψ；无 MCMP-EOS（Li/Luo、分段 ψ）、无润湿/接触角边界；**实测密度比 ≈9:1**（§4.3） |
| 自由面 free-surface（fill fraction / 质量重分布 / 界面重构） | **无** | 全树无 fill/free-surface 逻辑；`chapter8/film_*` 是**标量浓度**算例（`conc_wall/conc_inlet`，`chapter8/film_antibb.cpp:43-45`），**不是**自由面 LBM |
| 相场 Allen–Cahn | **无** | `allen|cahn|phase.field` 零命中 |
| 自由能 / 颜色梯度 / 相场 LBM | **无** | `free energy|colour|color gradient` 零命中 |
| Spurious current 量化/抑制 | **无** | `:326-377` 只输出 γ/ρ/p，无伪速度统计；无任何抑制措施 |
| 曲率驱动 Laplace 应力项、润湿边界 | **无** | — |

### 4.3 实测（我编译跑的数，非引用）

默认参数（`gA=−4.7, gAB=6, rhol=2.1, rhog=0.15, rho0=1, τ=1`，`nx=ny=64`，4000 步）：

| 量 | 实测 |
|---|---|
| `ρ_liq/ρ_gas`（平衡，4×4 盒平均） | `1.6707 / 0.18473` = **9.05 : 1** |
| 液滴半径（初值 15） | **16.32 lu**（初值 2.1 > 平衡液密度 1.67 ⇒ 多质量，液滴长大 ⇒ 该 demo **无质量守恒校验**） |
| `Δp=p_liq−p_gas` | `1.2699e−3` |
| `γ=Δp·r` | **0.020728** |
| 单核速度 | **5.46 MLUPS**（64², g++ -O3, x86） |

半径扫描（同参数、同样步数）见 §九表：`R=8` 时 `γ=0.0109`（−46%）；`R=24` 时半径估计器失效（`r=5207, γ=−4.86`）——**根因是 `chapter9/shanchen.cpp:358` 的 `double rad;` 未初始化**，若 `:360-367` 扫描未 break，`rad` 保持未定义值 ⇒ **上游缺陷（UB）**，`γ` 无意义却照常打印。吸收该标定器**必须补护栏**。---

## 五、GPU / 并行

**数据布局（AA-pattern + 方向主序 SoA）**：
- GPU：静息方向单开数组 `f0[NX*NY]`（`chapter13/gpu/LBM.cu:23-25` `gpu_field0_index`），8 个非静息方向是方向主序的 3D 展开 `[d][y][x]`（`:33-35` `return NX*(NY*(d-1)+y)+x`）。⇒ 同方向内 x 连续（**warp 合并访存友好**），相邻方向相距 `NX*NY`（无跨方向冲突）。即文献 AA-pattern，比朴素 AoS 更适合 GPU，又避免 9 个独立数组的寄存器压力。
- CPU 同布局（`chapter13/cpu/LBM.cpp:117-120`），且**仅当 `save==true` 才写回宏观量**（`:121-126` 注释 "only write to memory when needed"）⇒ 减少无效访存，这是 `cpu` 版相对 `cpu_intro` 版的核心优化。

**融合 kernel（最大工程干货）**：`chapter13/gpu/LBM.cu:172-252` 单 kernel `gpu_stream_collide_save` 同时完成"取邻点(pull)+算矩+松弛+写回"，**streaming 与 collision 融合、无中间 f_new**：邻点拉取 `:181-201`（`xm1/ym1/xp1/yp1` 用 `%` 周期），矩 `:203-215`，松弛写回 `:234-252`；`f0/f1/f2` 三数组轮转，host 侧每步**一次 launch**（`:161-170`）。教学版对照：`chapter13/cpu_intro/main.cpp:227`（独立 `stream()`）与 `:271`（独立 `collide()`）⇒ **同一物理两种组织的 A/B 组**，正好喂"融合 kernel"功能点。

**OpenMP**：`chapter13/openmp/LBM.cpp:39,53,105` 三条 `#pragma omp parallel for default(none) shared(...) schedule(static)`，按 `y` 行并行（`default(none)` 显式列 shared，教学级严谨）。

**MPI（1D y-slab，两版对照）**：
- 分区：`chapter13/mpi/nonblocking/main.cpp:53-66`（`rank_ny=NY/nprocs(+1 余数)`、`rank_ystart`）；数组按本 rank 行数分配 `:71-72`，**halo 各 1 行**（`rank_ny+2`）。
- 通信：`MPI_Isend/MPI_Irecv` 各 2 个请求（上下邻居 `rankp1/rankm1` 取模成环）`:106-110,124-125`；**每消息 `(ndir−1)*NX` 个 double 是连续内存**（`:108`）——因布局"方向主序 + 本 rank 连续行"，**halo 天然连续、免 pack/unpack**（AoS 则必须打包）。这是最值得抄的分布式优化点之一。
- **通信隐藏**：`chapter13/mpi/nonblocking/LBM.cpp:255,273-276` 在**内层 y 循环里**每算完一行 `MPI_Testall` 一次，把传输切碎与计算重叠；阻塞版 `chapter13/mpi/blocking/LBM.cpp` 用同步 `Send/Recv` 无重叠 ⇒ 又一对照实验。
- 归约：`MPI_Allreduce(...,7,...,MPI_SUM)`（非阻塞版 `chapter13/mpi/nonblocking/LBM.cpp:324`；阻塞版 `:230`），一次归约 7 个标量范数。
- 输出：按 rank 各写二进制分片 `save_scalar`（`chapter13/mpi/nonblocking/LBM.cpp:341-353`，`name%0Nd_rank%d.bin`），事后拼装——简单但**无并行 I/O**。

**没有的**：无多 GPU、无 3D 并行、无稀疏/多孔存储（`chapter8/cylinder.cpp:234,244-248` 是**稠密 int 掩码**）、无 AMR、无异步/pinned memcpy（`cudaMemcpy` 同步，`chapter13/gpu/LBM.cu:274`）。

---

## 六、可微 / 代码生成

- **autograd 友好度：零**。全仓纯 C/C++/MATLAB 过程式循环，无可微框架接口、无伴随/梯度代码（`torch|jax|autograd|grad|adjoint|tapenade` 零命中）。对我们的可微 LBM 方向，价值仅在**物理约定正确性参考**（力项/动量修正公式），不贡献 AD 实现。
- **符号推导 / 代码生成：无**（无 SymPy / 模板元编程 / kernel generator）。唯一"生成"是手写循环或手展开：`chapter9/shanchen.cpp:261-263`（`for i in NPOP`）vs `chapter11/IBLBM_2D_Poiseuille.cpp:354-362`（手展开 9 行）——后者利于编译器向量化但维护性差，可作我们 codegen 的对照基线。
- **精度**：全 `double`（GPU 版也是，`chapter13/gpu/LBM.cu:63` 签名），无 FP32/FP16 混合、无 fast-math 讨论。

---

## 七、与我们功能点的映射（对照表）

| feature_ledger slug | 本仓有什么（能否复用） | 判定 | 指针 |
|---|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE**（两组分分层 Poiseuille） | ❌ 无算例；但有 4 块可拼装积木：①两组分 SC 交叉力 + 独立 `tauA/tauB`；②平面界面初始化；③力/PBBC 双驱动 + 半力动量修正 + 4 种力项方案；④对解析 Poiseuille 的 L2 验证管线 | **adapt（拼装参考）** | ①`:29-36,79-109,185-197` ②`:215-229` ③`chapter6/force_poiseuille_BB.m:129-137,157-198` ④`:229-235` |
| **MCMP 高密度比** | ❌ 无手法；单一 ψ；实测密度比 ≈9:1；无 spurious-current 量化 | **reference only（反面基线）** | `chapter9/shanchen.cpp:34-40,74-76`；实测 §4.3 |
| **自由面（free surface）** | ❌ **无**（`chapter8/film_*` 是标量浓度，非自由面） | **不支持** | `chapter8/film_antibb.cpp:43-45` |
| **相场 Allen–Cahn** | ❌ 无 | **不支持** | — |
| **GPU 融合 kernel** | ✅ 单 kernel stream+collide（pull）+ AA-pattern + 每步一次 launch + save 才写回 | **fits** | `chapter13/gpu/LBM.cu:23-35,161-170,172-252`；`chapter13/cpu/LBM.cpp:121-126` |
| **分布式多卡** | ✅ 1D y-slab、halo 连续免打包、行级 `MPI_Testall` 重叠、blocking vs nonblocking 双实现 | **fits（CPU 集群侧）** | `chapter13/mpi/nonblocking/main.cpp:53-66,108,124-125`；`.../LBM.cpp:255,274,324` |
| **稀疏 / 多孔** | ❌ 无（只有稠密 int 几何掩码 + 阶梯边界节点重标定） | **reference（预处理思路）** | `chapter8/cylinder.cpp:229-300` |
| **案例组织** | ⚠️ 有但极简：一章一目录、一算例一 `main`、各自 `compile.sh`；**无公共库/无构建系统/无参数文件**（常量写死源码顶部） | **reference（反例：未参数化）** | `chapter13/cpu/compile.sh:1-9`；`chapter9/shanchen.cpp:24-40` |
| **测试与验证面** | ✅ 本仓最强项：解析解 + 相对 L2（`‖u−u_ana‖/‖u_ana‖`）+ 稳态判据 `|mean(u)/mean(u_old)−1|<1e−12`；三类基准（Poiseuille/Couette 解析、Taylor–Green 衰减、Young–Laplace/SCMP） | **fits（照抄成 pytest 夹具）** | `chapter5/poiseuille_BB.m:50,139-145`；`chapter6/force_poiseuille_BB.m:95,114-116,142-149,229-235`；`chapter13/python3/vortexdecay.py:52-59,113-118`；`chapter13/matlab/vortexdecay.m:102-127`；`chapter9/shanchen.cpp:326-377` |
| **CI** | ❌ 无（无 `.github/`、无 workflow、无 ctest） | **不支持** | 全树 `find` 无 `*.yml/Makefile` |
| **NEW: TRT-magic-参数化接口** | ✅ Λ 显式参数、Λ=1/6 与 1/12 双实现、`weights_trt/complement` 表驱动平衡 | **fits** | `chapter8/gaussian_2d_trt.cpp:47-49,116-133`；`chapter8/gaussian_1d_magic6.cpp:46-47,120-126`；`chapter8/cylinder.cpp:60-63` |
| **NEW: 标量 Dirichlet 边界库（anti-BB / Inamuro / 湿节点三方案）** | ✅ 同一 BC 的多实现并排对照 | **fits** | `chapter8/film_antibb.cpp:152-205`；`chapter8/film_inamuro.cpp:157-215`；`chapter5/couette_wetnode.m:111-146` |
| **NEW: IBM（Peskin 2/4 点核）用于船体/冰体浸没边界** | ✅ 完整可跑 2D 泊肃叶-弹性壁算例 | **adapt（2D→3D）** | `chapter11/IBLBM_2D_Poiseuille.cpp:430-458,463-524,529-,587-601` |

---

## 八、可吸收 Top3（对 TensorLBM / TensorPlatform 的具体吸收点）+ 风险

**Top1｜验证面模板：把"相对 L2 + 解析解 + 稳态判据"三件套固化为 pytest 夹具（→ TensorLBM 测试面，挂 `测试与验证面`）**
吸收点：①`L2=√Σ(u−u_ana)²/√Σu_ana²`，且**必须排除虚拟层**——`chapter6/force_poiseuille_BB.m:229-235` 注释明写 "i=1 and i=NX are virtual, not fluid, layers; thus not considered in error"，这条纪律能直接修掉"把 bounce-back 虚拟层算进误差"的历史偏差；②稳态判据 `conv=|mean(u)/mean(u_old)−1|<1e−12`（`:114-116,142-149`）；③Taylor–Green 用**标量指标**：理论衰减率 `2/t_d=2ν(kx²+ky²)` 对比拟合 `d lnE/dt`（`chapter13/matlab/vortexdecay.m:102-127`；我复算误差 0.077%，见 §九）。
**风险**：本仓 L2 是"全量范数"，无分区/加权 ⇒ 薄边界层算例（船体阻力）会被自由来流稀释；我们的阻力验证须改成分区/阻力系数相对误差，不能直接套。

**Top2｜TRT 的 Λ(magic) 参数化 + 表驱动平衡（→ TensorLBM 碰撞器接口，挂 `NEW: TRT-magic-参数化接口`）**
吸收点：`omega_plus=1/(0.5+Λ·ω⁻/(1−0.5ω⁻))` 的双向一致接口（`chapter8/gaussian_2d_trt.cpp:47-49`）；`weights_trt[]`+`complement[]` 两张常量表让 TRT 对称/反对称平衡由表生成、免手推（`chapter8/cylinder.cpp:60-63,159-208`）；Λ=1/6 与 1/12 双实现可当回归测试（`chapter8/gaussian_1d_magic6.cpp:120-126` vs `chapter8/gaussian_1d_magic12.cpp:121-126`）。
**风险**：本仓 TRT 只做**标量（扩散）**与纯扩散圆算例，**无 TRT 版流动+力项耦合**；Guo 力项在 TRT 下应对 `ω⁺/ω⁻` 分别修正，需我们自推，**不可照抄**。

**Top3｜GPU 融合 push + AA-pattern 布局（→ `GPU 融合 kernel`；顺带 `分布式多卡` 的免打包 halo）**
吸收点：`f0` 单开 + `f1[d][y][x]` 方向主序（`chapter13/gpu/LBM.cu:23-35`）；单 kernel pull 后**就地算矩/松弛**（`:172-252`），仅 `save` 时写宏观量（CPU 版同款 `chapter13/cpu/LBM.cpp:121-126`）；MPI 侧同布局使 halo 行连续，`(ndir−1)*NX` doubles **免打包直发**（`chapter13/mpi/nonblocking/main.cpp:108,124-125`），行级 `MPI_Testall` 重叠（`.../LBM.cpp:255,273-276`）。
**风险**：ⓐ 融合 pull 与多相"先全场上力场"冲突——SC 力是**全局先算力再碰撞**（`chapter9/shanchen.cpp:79-109` + `:398-...` 主循环），融合后要么两 kernel、要么邻域重算 ψ（额外 8 次 ψ 求值）。**本仓未回答这个问题**（GPU 版只做单相 Taylor–Green）；ⓑ 免打包依赖"方向主序 + y 连续"，改 x-y 分块或 3D 分区即失效；ⓒ 上游 GPU 代码写死 `-arch sm_20`（`chapter13/gpu/compile.sh:6`），且本机无 GPU ⇒ **GPU 段我未运行验证**（仅读码；CPU 同构版可跑通对照）。

---

## 九、复现与实测结果（程序化产出，零手抄）

环境：本机 x86_64，`g++ -O3`，python3.11 / numpy 2.4 / matplotlib 3.10；**未改物理参数**，只把 `nsteps 20000→4000`、`noutput 100→500`、`radius` 扫参、`vortexdecay.py` 的 `NMSG 100→10`（加密采样）。
产物：`artifacts/oss-inventory/code/{analyze.py, metrics.json, scmp_radius_sweep.csv, tg_decay.csv, fig1_scmp_young_laplace.png, fig2_tg_decay.png}`；复现脚本 `artifacts/oss-inventory/code/reproduce.sh`。

**表 1｜SCMP 半径扫描（Young–Laplace 自洽性）**

| R_init | ρ_g | ρ_l | 密度比 | Δp | r_fit | γ=Δp·r | 判定 |
|---|---|---|---|---|---|---|---|
| 8 | 0.19504 | 1.68702 | 8.65 | 1.7074e−3 | 6.382 | 0.010897 | 小液滴未分辨（−46.2%） |
| 12 | 0.18687 | 1.67736 | 8.98 | 1.6591e−3 | 12.291 | 0.020392 | 有效 |
| 16 | 0.18432 | 1.66937 | 9.06 | 1.1918e−3 | 17.432 | 0.020776 | 有效 |
| 20 | 0.18308 | 1.66419 | 9.09 | 8.5034e−4 | 22.365 | 0.019018 | 有效 |
| 24 | 0.17411 | 1.63248 | 9.38 | −9.3354e−4 | 5207.67 | −4.8616 | **估计器失效（UB）** |

过原点最小二乘（R=12/16/20）：**γ = 0.020268，r² = 0.988**；三点自身离散 8.7%。

**表 2｜Taylor–Green 衰减（`chapter13/python3/vortexdecay.py`，64², τ=1, u_max=0.02, 800 步, 81 采样点）**

| 指标 | 实测 | 理论/判据 | 误差 | <3%? |
|---|---|---|---|---|
| 衰减率 `−d lnE/dt` | 0.0064305 /step | `2/t_d=2νk²`=0.0064255 /step（`t_d`=311.26） | **0.077%** | ✅ 是 |
| `L2(u)` 相对误差（t≥200） | 0.047%–0.192%（末值 0.192%） | 解析解（0） | ≤0.19% | ✅ 是 |
| `L2(ρ)` 相对误差（t≥200） | 0.349%–**6.073%**（末值 2.885%） | 解析解（ρ≡1） | 峰值 6.07% | ❌ **否** |
| 单核速度 | 2.07 MLUPS（numpy 版） | — | — | — |

**误差判定声明**：TG 衰减率与速度场两项 **<3% 达标**；**密度场峰值 6.07% 不达标**——这不是我的实现引入，而是上游参考代码在该分辨率/马赫数下的固有噪声（弱可压 LBM 的密度误差在 u→0 时被相对范数放大），且上游**未提供任何验收阈值**。SCMP 侧 **上游未随代码给任何 γ 参考值**（无 README 数值、无文献对照注释）⇒ 按纪律**不能声称 <3%**，我只给自洽性（r²=0.988、三点离散 8.7%）与失效区定位（R=8 / R=24），**不伪装成精度达标**。

---

## 附：负面结论（避免后续重复投入）

1. 该仓**不能**提供：自由面、相场/Allen–Cahn、自由能/颜色梯度、MRT/KBC/cumulant、曲率（插值）BB、3D 格子、稀疏/多孔、CI、可微/代码生成 —— 全树 grep 零命中，属**确认不存在**而非未检查。
2. 该仓**能**提供（MIT 可商用、质量高）：力项方案矩阵（4 种 + 半力修正约定）、TRT-magic 参数化、标量边界 5 种实现对照、IBM 2/4 点核完整算例、GPU 融合 push + AA 布局、MPI halo 免打包 + 计算重叠、以及**整套"解析解 + 相对 L2 + 稳态判据"验证模板**。
3. 唯一已定位的**上游缺陷**：`chapter9/shanchen.cpp:358` `double rad;` 未初始化 + 半径扫描循环可能不 break ⇒ `γ` 打印未定义值（实测 R=24 得 `γ=−4.86`）。吸收该标定器必须加护栏（break 失败即报错、不打印 γ）。

[OSS-INV]
repo: lbm-principles-practice/code
lang: C++ (C++98, 主) + CUDA + MATLAB + Python3
license: MIT
methods: D2Q9 only; BGK; TRT with explicit magic parameter Lambda=1/6 and 1/12; forcing variants: Guo 2nd-order velocity-space with (1-omega/2) factor, Buick-Greated 1st-order, plus half-force momentum correction u+F/2; no MRT/KBC/cumulant/regularized
multiphase: Shan-Chen single pseudopotential psi=rho0(1-exp(-rho/rho0)) - SCMP droplet plus two-component planar interface, nearest-neighbour force with g[s][ss] matrix, Guo forcing, barycentric velocity with 0.5F shift, Young-Laplace gamma calibration; chapter9/shanchen.cpp:74-76, :79-109, :126-139, :142-161, :215-229, :252-280, :326-377. NO free-surface / free-energy / colour-gradient / Allen-Cahn.
boundary: half-way bounce-back (rest + moving wall with Ladd momentum correction); anti-bounce-back Dirichlet for scalars; Inamuro; wet-node 3 variants (equilibrium / non-equilibrium extrapolation / non-equilibrium BB ~ Zou-He family); periodic pressure BC (PBBC); zero-gradient outlet + specular free wall; staircase solid-node relabelling; immersed boundary (Peskin 2-/4-point kernels). No curvature/interpolated bounce-back, no 3D Zou-He.
gpu: single fused push kernel (stream+collide pull scheme; f0 separate + f1[d][y][x] AA-pattern), one launch per step, synchronous memcpy, double precision; MPI 1D y-slab with contiguous halo rows (no packing) + MPI_Testall overlap; OpenMP row-parallel
summary: MIT-licensed teaching-grade reference implementations (D2Q9 BGK/TRT, four forcing schemes, five boundary families, IBM, GPU and MPI variants) with explicit physics conventions but zero engineering (no library, no tests, no CI). Highest-value assets are the L2-vs-analytic validation templates plus the TRT/magic, fused-kernel and unpacked-halo MPI patterns; it has NO free-surface, phase-field, high-density-ratio or curvature-boundary capability.
[/OSS-INV]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: GAP-LBM-MCMP-POISEUILLE
tag: scmp-two-component-planar-interface
method: Two components share one D2Q9 lattice with per-component populations and separate tauA/tauB; segregation comes from the cross interaction g[0][1]=gAB inside the Shan-Chen force loop, forces enter through Guo 2nd-order forcing, velocity is the barycentric velocity with the 0.5F shift. The channel can then be driven either by an equivalent body force (Guo with half-force momentum correction) or by a PBBC density difference, and validated against the analytic Poiseuille parabola with a relative L2 norm that excludes the virtual wall layers.
files: chapter9/shanchen.cpp:29-36, chapter9/shanchen.cpp:79-109, chapter9/shanchen.cpp:142-161, chapter9/shanchen.cpp:191-197, chapter9/shanchen.cpp:215-229, chapter9/shanchen.cpp:261-263, chapter6/force_poiseuille_BB.m:129-137, chapter6/force_poiseuille_BB.m:157-198, chapter6/force_poiseuille_BB.m:229-235
conventions: rho0=1 pseudopotential reference; tau=1 gives nu=(2tau-1)/6=1/6; force term carries the (1-omega/2) factor for 2nd-order spatial discretisation; reported velocity is u+0.5F/rho while momenta() stores raw momentum; the ch9 file is fully periodic so a walled channel must be added; interface initialised with rho_A/rho_B=0.1/0.9 and total density 1
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: NEW:scmp-young-laplace-calibrator-with-guardrails
tag: shan-chen-laplace-pressure-calibration
method: Form the Shan-Chen pressure p=sum_s rho_s/3 + sum_{s,ss} g[s][ss] psi_s psi_ss/6, average liquid pressure in a 4x4 node box at the droplet centre and gas pressure in a corner box, locate the interface along a diameter by linear interpolation of the rho crossing rho_av=0.5(rho_gas+rho_liq), and report gamma=dp*r. Our run gave gamma=0.02027 (r^2 0.988 over R=12..20), density ratio 9.05:1, 5.46 MLUPS single core.
files: chapter9/shanchen.cpp:126-139, chapter9/shanchen.cpp:326-377, chapter9/shanchen.cpp:355-369, chapter9/shanchen.cpp:358
conventions: 4x4 node averaging boxes; radius from the rho_av crossing with linear interpolation; interaction term needs the 1/6 factor; the shipped estimator declares rad uninitialised at :358 and does not check whether the scan found a crossing, so gamma is printed from undefined memory when the droplet merges with its periodic image (measured gamma=-4.86 at R_init=24) - reuse must add a validity guard
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: GPU 融合 kernel
tag: fused-push-pull-aa-pattern
method: Keep the rest direction in its own array f0 and the eight moving directions in direction-major layout [d][y][x]; in one kernel pull the eight shifted neighbours, compute moments and relax to equilibrium in registers, and write only the new populations (plus macroscopic fields only when a save flag is set) - streaming and collision fused, one kernel launch per step, no intermediate post-stream array.
files: chapter13/gpu/LBM.cu:23-35, chapter13/gpu/LBM.cu:161-170, chapter13/gpu/LBM.cu:172-252, chapter13/gpu/LBM.cu:181-201, chapter13/gpu/LBM.cu:234-252, chapter13/cpu/LBM.cpp:112-126
conventions: periodicity via %NX and %NY; offset of a direction is NX*(NY*(d-1)+y)+x so x is contiguous for coalescing and directions are NX*NY apart; double precision throughout; macroscopic fields written only when needed; the CUDA code is single-phase without forcing (no multiphase variant exists)
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: 分布式多卡
tag: mpi-1d-slab-unpacked-halo-testall-overlap
method: Split NY into contiguous rank slabs with one halo row per side; because the layout is direction-major with contiguous y-rows, each halo block is (ndir-1)*NX contiguous doubles so MPI_Isend/MPI_Irecv needs no packing; call MPI_Testall after every interior y-row so communication overlaps the remaining collision work; combine global norms in one MPI_Allreduce over seven scalars; a blocking Send/Recv variant is shipped for A/B comparison.
files: chapter13/mpi/nonblocking/main.cpp:53-66, chapter13/mpi/nonblocking/main.cpp:108, chapter13/mpi/nonblocking/main.cpp:124-125, chapter13/mpi/nonblocking/LBM.cpp:255-276, chapter13/mpi/nonblocking/LBM.cpp:324, chapter13/mpi/blocking/LBM.cpp:230
conventions: 1D decomposition along y only; ranks wrap around (rankp1/rankm1 modulo nprocs); halo rows allocated as rank_ny+2; per-rank binary shards name%0Nd_rank%d.bin written without parallel I/O
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: NEW:trt-magic-parameterised-collision
tag: trt-lambda-magic-table-driven
method: Split populations into symmetric and antisymmetric parts f_pm=0.5*(f_i +- f_complement) and the corresponding equilibrium parts, relax each with its own rate omega_plus/omega_minus, and derive one rate from the other through the magic parameter Lambda: omega_plus=1/(0.5+Lambda*omega_minus/(1-0.5*omega_minus)). The discrete TRT equilibria are generated from two constant tables weights_trt[] and complement[], and Lambda=1/6 and 1/12 are both implemented with plus/minus roles swapped as a consistency check.
files: chapter8/gaussian_2d_trt.cpp:47-49, chapter8/gaussian_2d_trt.cpp:116-133, chapter8/gaussian_1d_magic6.cpp:46-47, chapter8/gaussian_1d_magic6.cpp:120-126, chapter8/gaussian_1d_magic12.cpp:46-47, chapter8/gaussian_1d_magic12.cpp:121-126, chapter8/cylinder.cpp:51-63, chapter8/cylinder.cpp:159-208
conventions: Lambda=1/6 or 1/12; omega_minus is set explicitly (e.g. 1.95) and omega_plus derived from it; weights_trt={0,1/3,1/3,1/3,1/3,1/12,1/12,1/12,1/12}; complement={0,3,4,1,2,7,8,5,6}; only scalar advection-diffusion TRT is demonstrated, no TRT flow plus forcing
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: 测试与验证面
tag: analytic-solution-relative-L2-harness
method: Every benchmark compares the simulated field with a closed-form solution and reports a relative L2 norm computed only over fluid nodes (wall/virtual layers excluded); steady problems iterate until |mean(u)/mean(u_old)-1| < 1e-12; Taylor-Green runs report the sum-square error, the L2 norms of rho and u, and the analytic decay time td=1/(nu*(kx^2+ky^2)) at fixed sampling intervals.
files: chapter6/force_poiseuille_BB.m:95, chapter6/force_poiseuille_BB.m:114-116, chapter6/force_poiseuille_BB.m:142-149, chapter6/force_poiseuille_BB.m:229-235, chapter5/poiseuille_BB.m:139-145, chapter13/python3/vortexdecay.py:52-59, chapter13/python3/vortexdecay.py:113-118, chapter13/matlab/vortexdecay.m:102-127
conventions: L2 = sqrt(sum((u-u_ana)^2))/sqrt(sum(u_ana^2)); node 1 and node NX are virtual layers and must be excluded from the error; convergence tolerance 1e-12 on the mean velocity ratio; our rerun of the TG harness gives decay rate within 0.077% of theory while the density L2 peaks at 6.07%
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lbm-principles-practice/code
slug: NEW:scalar-dirichlet-boundary-library
tag: anti-bounce-back-inamuro-wetnode
method: Three interchangeable ways to impose a Dirichlet value on a wall/inlet for a scalar (concentration/temperature) field: anti-bounce-back f2[i]=-f2[opp]+2*w_i*C_wall applied after streaming; Inamuro reconstruction that solves for the unknown-population density (C_in - sum f_known)/sum w_unknown and re-distributes it by weights; wet-node variants (equilibrium, non-equilibrium extrapolation using the neighbour's f-feq, and non-equilibrium bounce-back) with the boundary density recovered from the bounce-back relation.
files: chapter8/film_antibb.cpp:152-205, chapter8/film_inamuro.cpp:157-215, chapter5/couette_wetnode.m:111-146, chapter5/couette_wetnode.m:116-123, chapter8/cylinder.cpp:214-227
conventions: anti-bounce-back uses the opposite direction from the post-streaming array (f2), Inamuro uses the pre-streaming array (f); wet-node scheme selector wetnode=1 equilibrium / 2 non-equilibrium extrapolation / else non-equilibrium bounce-back; free-wall/specular is a mirrored copy of the neighbouring populations plus rho_corner=rho_neighbour
transfer: fits
[/OSS-SOL]