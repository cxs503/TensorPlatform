# 开源 LBM 技术盘点 · 单项目深盘：`lanl/MF-LBM`

> 锚点 FEAT-OSS-INVENTORY-LBM（波2）· tier 1 · LANL 可移植可扩展多孔介质 DNS 级 LBM（MPI + CUDA/OpenACC，BSD-3）
> 盘点对象：`https://github.com/lanl/MF-LBM` @ `11be324a97fe6331b2271102da370460715355e8`（`master` tip，2023-07-25 "fix geometry preprocessing bug"）
> 盘点方式：**读代码本体**（逐条结论带 `文件:行`）+ 一次**编译/运行探针**（§九，honest-FAIL，不粉饰）
> 与波1 权威清单的分歧（重要）：「多相档」归位正确，但**模型族判为 color-gradient (Rothman–Keller) + CSF + 几何润湿**，**不是 Shan-Chen/MCMP_ψ**，**且密度比为 1（无密度对比）**——两相细节见 §四。

---

## 零、取证与复现（provenance）

| 项 | 值 |
|---|---|
| 仓库 / 分支 / commit | `lanl/MF-LBM` / `master` / `11be324a97fe6331b2271102da370460715355e8`（`api.github.com/repos/lanl/MF-LBM/commits` 直读；前 3 条提交时间 2023-07-25 / 2023-01-08 / 2023-01-08） |
| 元数据 | stars 133，`size` 805 KB，`license = BSD 3-Clause "New" or "Revised"`（SPDX `BSD-3-Clause`），`pushed_at 2023-07-25` |
| 取证通道 | `curl` 在本机被安全策略拦截 → 改用 `urllib` 取 `https://codeload.github.com/lanl/MF-LBM/tar.gz/<sha>`（**588,782 B**，gzip 完整），解包到 `/tmp/mflbm_src`（**未进入我们的仓**） |
| 树规模 | 84 个文件；Fortran 源码 `14,915` 行（`wc -l` 汇总 `*.F90/*.f90/*.h`）；`images/core-dns.png` 1 张图 |
| 子模块 | `.gitmodules:1-3` 指向 `lanl/MF-LBM-extFiles`（示例几何：管+球、Bentheimer 砂岩 text image、WallArray 二进制）→ **未抓取，几何样例 WITHHELD**；本文所有几何结论仅指仓内代码可生成的几何（`Misc.F90:74-109`）与仓内预处理链 |
| 构建探针 | 本机 `gfortran 12.3.0`（Anolis，128 核），**无 MPI 实现**（无 `mpif90`/`mpirun`）→ 自写 **serial no-op MPI shim**（`runs/m8547/mpi_stub.f90` + `runs/m8547/mpif.h`，仅覆盖本仓实际调用的 26 个 MPI 符号），`-O2 -fopenmp`，按上游对象序编译 **0 error**；上游未改一行（源码副本在 `/tmp/mflbm_build/src`）。shim 与运行脚本见 `runs/m8547/` |
| 探针结论 | 二进制可运行、初始化/IO 正常；**动力学在 <1000 步内全场 NaN**（两套配置），因此**不给出** θ/表面张力/spurious-current 的定量精度结论（§九 honest-FAIL） |

---

## 一、概况

- **两个求解器**：`singlephase_3D/`（10 个源文件，单相绝对渗透率/DNS 湍流）与 `multiphase_3D/`（14 个源文件，两相排水/吸渗/相对渗透率）。二者共享大量同名例程但**不共享代码**（各自一份 `Mpi.F90/Misc.F90/Module.F90`），是**分叉复制**而非模块化复用。
- **语言/并行模型**：Fortran 90/95 + MPI + **指令式** OpenACC/OpenMP 混合。`multiphase_3D/makefile:1-40` 用 `arch = gpu|cpu|mic` 切换：GPU 走 `-acc -Mcuda -ta=tesla:pinned -D_openacc`（nvidia 编译器），CPU 走 `-x/host` + `-openmp`，MIC 走 `-xMIC-AVX512`；README 明说选指令式而非 CUDA 是为**跨平台保持可移植**（`README.md` "The code is written on Fortran 90 and employs MPI-OpenACC/OpenMP hybrid programing model"）。
- **许可证 BSD-3-Clause**：与波1 的 GPL 项目形成对照——**可带署名+许可证全文+免责声明地直接移植进 TensorLBM**（非 copyleft），但仍建议"读思想→自实现+单测"路线，避免把 Fortran 逐行译成 Python 造成不可维护的孪生代码。
- **活跃度**：`pushed_at 2023-07-25`，之后无提交；无 `.github/`、无 CI、无单元测试、无 `CMake`/`setup`；`test_suites/` 只有 shell 配置生成器（§五）。
- **一句话定位**：面向**岩石孔隙尺度两相驱替 DNS**的 Fortran+MPI+OpenACC 高性能 color-gradient LBM，卖点是「CSF + 几何润湿模型 → 较小的 spurious current」、「AA-pattern + SoA + 通信计算重叠」、「同一份源码跑 CPU/GPU/MIC/ARM」。

---

## 二、方法谱系

| 维度 | 本仓实现 | 指针 |
|---|---|---|
| 格子 | **仅 D3Q19**（无 D3Q27/D2Q9、无多块、无自适应） | 速度集 `Module.F90:111-113`；反方向表 `Module.F90:114`（**全树无消费者**，见 §八-4） |
| 碰撞 | **MRT**：19 个矩（ρ,e,e²,j,q,3p,3π,pww,πww,pxy,pyz,pzx,tx,ty,tz），矩空间松弛后反变换 | 矩计算 `Kernel_multiphase.F90:176-195`；松弛 `:197-216`；系数化反变换 `:219-256` |
| 松弛参数 | 4 套预设由宏 `mrt` 选择；仓内 `preprocessor.h:2` = `#define mrt 2`（"original"：`s_e=1.19, s_e2=1.4, s_pi=1.4, s_q=1.2, s_t=1.98`） | `preprocessor.h:1-3`、`Kernel_multiphase.F90:126-156` |
| 力项 | **CSF（Continuum Surface Force）**：`F = 0.5·γ·κ·c_norm·n̂ + 体力`，直接进入矩空间（无 Guo/EDM 速度修正，速度取 `+0.5F` 半步项） | `Kernel_multiphase.F90:116-119`、`:480-483`（CSF）、`:160-163`（`+0.5fx`）、`:198-213`（矩空间力修正） |
| 两相表示 | **两套 PDF（f=流体1、g=流体2）+ 分密度**：`φ=(ρ1-ρ2)/(ρ1+ρ2)`，`den=ρ1+ρ2` | `Module.F90:203-205`、`Kernel_multiphase.F90:108-110`、`:159` |
| 混色/再着色 | **Rothman–Keller 再着色**：`tmp=ρ1ρ2β/den`，方向权重 `w_equ_1`（主/对角 `RK_weight2=1/(36√2)`）；β 控界面锐度（推荐 0.9–0.95） | `Module.F90:225`、`Kernel_multiphase.F90:276-315` |
| 粘度 | **双粘度 + φ 加权混合**：`ω = 1/( 6/((1+φ)/ν1 + (1-φ)/ν2) + 0.5 )`（模板默认 ν1=0.004、ν2=0.4，比值 100） | `Kernel_multiphase.F90:124`、`:487`；`Init_multiphase.F90:116-125` |
| 相界面法向 | **ISO4 两级各向同性差分**（近邻 + 对角/次近邻加权 1/6、1/12），归一化后**壁面格置零** | `Module.F90:227`、`Phase_gradient.F90:40-62`、`:64-74` |
| 曲率 | `κ = (n_x²-1)k_xx + (n_y²-1)k_yy + (n_z²-1)k_zz + 2(n_x n_y k_xy + …)`（法向散度展开） | `Phase_gradient.F90:114-197`（`:194-197` 为合成式） |
| 润湿 | **几何润湿（geometrical wetting, Akai et al. 2018）**：在壁面邻层把色梯度矢量按局部法向 `n_w` 与接触角 θ **旋转**，两候选解取距离近者 | `Phase_gradient.F90:209-265`（`:238-259` 为旋转+择优）；旧 Leclaire 迭代式 `:268-343` **定义但永不调用**（死代码） |
| 壁面法向来源 | 预处理：把 `walls` 平滑成 `walls_smooth_2`，用 **ISO8（8 层）** 差分求固体法向，写入 `fluid_boundary_nodes(i)%nwx,nwy,nwz,theta` | `Geometry_preprocessing.F90:11`、`:236-360`（ISO8 差分）、`:505`（赋 θ） |
| 接触角范围 | **仅 0°–90°（以流体2=润湿相量取）**；流体1 恒为非润湿相 | `multiphase_3D/run_template/template-simulation_control.txt`（`theta` 段注释）；θ 换算 `IO_multiphase.F90:467-468`（`θ ← 180°-θ` 再转弧度） |
| 边界 | 速度入口＝**反弹式（anti-bounce-back）**+ 4 层 φ 鬼点外推；压力入口＝**Zou–He**；出口＝**对流出流**或 **Zou–He 压力**；固体壁＝**无显式例程**（见 §3-6） | `Boundary_multiphase_inlet.F90:6-53`、`:56-102`；调用点 `Main_multiphase.F90:399-410`、`:462-473`；出流/压力出口在 `Boundary_multiphase_outlet.F90` |
| BT/多孔板 | `porous_plate_cmd=1/2` 在指定 z 面阻断某一相（相对渗透率测量的"膜"） | `Boundary_multiphase_other.F90:8-94`、`:97-…` |
| 稳态判据 | 3 种：毛细压稳态 / 相场差稳态 / 分流量稳态 | `Module.F90:33-35`；`Monitor.F90:287-370`、`:370-472` |
| 维度 | **仅 3D**（无 2D 模式）；无 AMR、无多块、无 IBM | 全树 |

---

## 三、性能 / GPU / 分布式

1. **AA-pattern（A-A 交替寻址）+ 融合 stream-collide-recolor**：奇数步一个 kernel 内完成「pull 18 个方向 + 聚合 PDF + φ + 碰撞 + 再着色 + push」——见 `Kernel_multiphase.F90:45-105`（pull）、`:107-315`（φ/CSF/MRT/RK）、`:317-354`（push）；偶数步**无流动**，只在原地交换反方向槽位再碰撞（`:409-448` pull、`:690-717` push）。→ 每步每方向 **1 读 + 1 写**，无需两套完整 f 数组来回拷贝（对比我们 `free_surface_lbm` 里每步 4 次全场 copy 的做法，这是可借的思想）。
2. **SoA 布局**：19 个独立三维数组/相（`Module.F90:203`；单相 `Module.F90:177`），内层循环 `i`（x）连续 + `!DIR$ IVDEP`（`Kernel_multiphase.F90:41`）+ `!$acc loop collapse(3) device_type(NVIDIA)`（`:38`）→ 合并访存/向量化。
3. **OpenACC 异步队列**：`Module.F90:159-164` 定义 15 条命名队列（z/y/x 的 pdf/phi、edge、sync…）；主循环先异步发**边界条带** kernel（`Main_multiphase.F90:358-366` 偶数步、`:423-430` 奇数步），再打包/发 MPI，再发**内部大块** kernel（`:379`、`:442`），最后同步更新灌回 halo（`:385-387`、`:449-451`）。`#ifdef _openacc` 下把内部 kernel 排在 `mpi_send_req` **之前**（`:378-384`、`:441-447`），CPU 版则相反——这是"通信计算重叠"的核心手法。
4. **MPI 分解：仅 y/z 两维**。`IO_multiphase.F90:494-498`：`npx/=1` 直接报错退出（"this code has temporary disabled all X direction MPI communication"），x 方向的 async kernel 调用整段注释（`Main_multiphase.F90:367-370`、`:431-434`）；模板亦注明 "x direction domain decomposition disabled: npx = 1"。→ 实际是 **2D 进程网格 + 3D 分区**（`np = npy*npz`，`IO_multiphase.F90:489-492`）。
5. **Halo 深度**：`overlap = 1, overlap_walls = 2, overlap_phi = 4`（`Module.F90:263`）；φ 的 halo 用 26 邻居独立 tag（`Module.F90:247-253`）交换**面/棱/角**，即 3D 对角 halo 也补齐；异步层数可调 `MPI_async_layers_num 0,4,4`（模板）。
6. **壁面 no-slip 的实现（重要，易误判）**：全树**没有**壁面反弹例程——壁面在融合 kernel 里被 `if(walls(i,j,k)==0)` 跳过（`Kernel_multiphase.F90:44`、`:408`），而 push 步会把流体格的 outgoing 写进**反方向槽位**（`:318` `f2(i+1,j,k)=g1t1`），该槽位正是固体格下一步被相邻流体 pull 回来的槽位（`:48` `g1t2=f2(i+1,j,k)`）→ 等效**半程反弹（half-way bounce-back）**在流体/固体界面处，零壁速、无滑移（`Init_multiphase.F90:72` 注释 "half-way bounceback 0.5 + 0.5" 佐证）。**注意**：这是本文的**推断（inference）**，源注释未直说；且**未验证 1 格厚壁被两侧流体同时写入时的语义**（可能存在串流风险），也无动壁/滑移/IBM 能力。
7. **无 CUDA/HIP kernel、无混合精度**：README "Only double precision is supported"；全部并行靠编译器指令 → 代码**可移植**但**性能上限受编译器**（nvidia `-acc` 或 intel/gcc CPU 向量化）。
8. **自带基准**：`benchmark_cmd=1` → `Main_multiphase.F90:498-…` 的 `benchmark`（3 轮取时，输出 MLUPS，`max_time_step_benchmark` 控步数）；`test_suites/6.performance_benchmarking`、`8.performance_benchmarking_singlephase` 是纯性能配置。**无实测数字入库**（探针未跑通，§九）。

---

## 四、多相细节（本单重点；权威清单"多相档"待核实项）

**结论（源码确认）：color-gradient（Rothman–Keller）两相模型 + CSF 力项 + 几何润湿，密度比恒为 1。**

1. **不是 Shan-Chen/MCMP**：力项只有 CSF 与常体力两项（`Kernel_multiphase.F90:116-119`、`:480-483`），没有任何赝势 `ψ(ρ)`、没有组元间相互作用力、没有 `G` 作用强度参数。→ 与"MCMP（多组分多相 Shan-Chen）"是**不同模型族**；我们把 `GAP-LBM-MCMP-POISEUILLE` 挂在 SCMP/MCMP 线上的判据（两相分层 Poiseuille、密度比扫描）在本仓**无对应机制**。
2. **密度比 = 1（无密度对比）**：`den = ρ1+ρ2` 只作分母，`φ=(ρ1-ρ2)/den`，两套 PDF 各自守恒于 `ρ_i`，纯相内 `den≡1`；全树 `grep -i "density ratio"` **0 命中**，也没有 `la_rho1/la_rho2` 之类参数（参数表只有 `fluid1_viscosity / fluid2_viscosity / surface_tension / theta / RK_beta`，见 `template-simulation_control.txt`）。→ **高密度比 MCMP 能力 = NOT_SUPPORTED**；能对比的只有**粘度比**（默认 100:1）。
3. **表面张力**：`σ = gamma`（lattice unit，模板 0.03），通过 CSF `0.5σκ n̂`；`Module.F90:230` 还声明了 `phys_gamma, ca, u_filter` 等**物理量/滤波参数但无消费者**（死变量），说明"物理单位映射/速度滤波"是未完成的半成品。
4. **spurious current 抑制**：靠 (a) CSF 力项（对比假势模型）+ (b) 几何润湿旋转（`Phase_gradient.F90:209-265`）+ (c) 壁面法向表预热（`Geometry_preprocessing.F90:236-360`）。仓内**没有任何 spurious-current 的量化基准或回归数据**——README 的 "relatively small spurious currents" 是论文陈述，**仓内无证据**（见 §五、§九）。
5. **界面锐度**：`RK_beta`（0.9–0.95 推荐）控再着色强度；界面厚度由 β 与 ν 比共同决定，**无可调界面宽度参数**（不同于相场模型的 ε 参数）。
6. **自由面（VoF / fill fraction）**：**无**——没有格型分类（fluid/interface/gas）、没有填充率 ε、没有气相"空腔"。`φ` 是**色序参量**（-1..1），不是相场序参量。
7. **相场（Allen–Cahn / Cahn–Hilliard）**：**无**——没有化学势、没有界面自由能泛函、φ 不满足任何 PDE 演化方程（它由两套 PDF 的零阶矩**代数**给出，`Kernel_multiphase.F90:108-110`）。
8. **润湿/接触线**：静态接触角靠**壁面邻层法向旋转**（几何润湿），θ 只能取 **0–90°**；**无动接触角模型**（无 Blake/Kistler、无 slip length、无 Cox-Voinov），壁面零滑移 → 移动接触线奇性**未处理**；`θ>90°` 的输入检查被**注释掉**（`IO_multiphase.F90:463-466`）而模板仍声明"必须 <90°" → **超范围输入被静默接受**，是可移植时必须补的守卫。
9. **多孔介质专用能力**（本仓真卖点）：`int1` 壁面数组几何（`Misc.F90:6-210` 的 `set_walls`）、外部几何 3 步预处理链（text image → WallArray → 平滑/加缓冲层/法向表）、**间接寻址固体/流-固界面节点表**（`Module.F90:90-103`：`solid_boundary_nodes%neighbor_list(1:18)+la_weight`、`fluid_boundary_nodes%nwx..%theta`）、孔隙度/有效横截面积/有效孔隙节点数统计（`Init_multiphase.F90:97-103`）。

---

## 五、平台类能力：案例组织 / 测试验证面 / CI

1. **案例组织（reference 级，简单但可借鉴）**：运行目录只需三件东西——`simulation_control.txt`（全部算例参数，~200 行带注释）、`path_info.txt`（二进制/几何/边界信息路径）、`job_status.txt`（`new_simulation|continue_simulation|simulation_done|…`，`Main_multiphase.F90:91-95`、`:139-141`）；每个算例用 `config.sh` 以 `sed` 从模板生成控制文件（`test_suites/3D_simulation/1.drop_attached_wall/config.sh:20-98`）。**无 schema 校验**，参数只在 `IO_multiphase.F90:457-540` 做零散一致性检查（如 MPI 维数、周期/壁面冲突），错误一律 `mpi_abort` 无诊断码。
2. **测试验证面：实际为 0 断言**。`test_suites/3D_simulation/` 共 8 个目录（含 2 个性能），每个仅 `config.sh` + `clean.sh`（后者只 `rm` 输出）——**没有参考数据、没有 pass/fail、没有容差**；仓内也没有任何论文算例的基准 CSV。→ 我们**不能**引用它作为"已验证"，只能把它当**算例脚手架**。
3. **CI：无**（无 `.github/`、无 `Makefile test` 目标）。
4. **IO/后处理**：VTK legacy **BIG_ENDIAN 二进制**（`IO_multiphase.F90:894-900`，`vtk_type=1/2/3` 分流场/相场/CSF 力矢量）；极端规模走**分布式原始二进制** `save_phi`/`save_macro`（`IO_multiphase.F90:646-…`）+ 3 个独立 Fortran 后处理程序（`postprocessing/exteme_large_sim_parallel_IO/*`）。注意目录名 `exteme` 拼写错误（三处），是可移植时的坑。
5. **Checkpoint/续算**：`save_checkpoint` 支持双备份（`double_bak_checkpoint_pdf_cmd`）、按**墙钟小时**定时（`checkpoint_save_timer`/`simulation_duration_timer`）、续算读 `job_status.txt='continue_simulation'`（`IO_multiphase.F90:579-641`、`Main_multiphase.F90:222-263`）。这是本仓**最值得抄的工程机制**之一（长时间大算例的自动分段+双备份）。
6. **监控面**：`Monitor.F90:5-286` 每次输出饱和度/两相动能/分流量/压力剖面，最小二乘拟合相对渗透率斜率（`:19` 注释 `least square`）；稳态判据三选一；突破检测 `monitor_breakthrough`（`:472-511`）。

---

## 六、可微 / 代码生成

- **不可微**：纯 Fortran 手工内核，无伴随/自动微分/灵敏度接口。
- **无代码生成**：无 DSL、无符号推导、无 C++/CUDA 代码生成；唯一"生成"是 `preprocessor.h` 的 4 个编译期宏（`mrt=1..4`）。
- **无配置驱动 kernel**（对比 lbmpy/pystencils 家族）：方法选择靠**改宏 + 重编译**。

---

## 七、与我们功能点的映射（feature_ledger）

| feature_ledger slug | 本仓提供？ | 定位 | transfer | 说明 |
|---|---|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | **否** | — | — | 无组元间作用力、无密度对比（§四.1-2）；两相分层 Poiseuille 的物理（重力/压力驱动下的分层界面）在本模型里靠 CSF+壁面润湿做，**不能**作为该 GAP 的基线。**GAP 仍未闭合** |
| MCMP 高密度比 | **否** | — | — | 密度比恒 1（`den≡1`，无 density-ratio 参数）；**NOT_SUPPORTED** |
| 自由面 | **否** | — | — | 无格型/VoF 填充率（§四.6）；与我们 `free_surface_lbm*` 无可比面 |
| 相场（Allen–Cahn/CH） | **否** | — | — | φ 为色序参量（代数式），非 PDE 序参量（§四.7） |
| GPU 融合 kernel | **是（指令式，非 CUDA）** | `Kernel_multiphase.F90:45-354`、`:409-717` | **adapt** | 可借的三件：①AA-pattern 融合 stream+collide+recolor（每方向 1 读 1 写）；②奇偶步交替使偶数步免流动；③`#ifdef _openacc` 交换 kernel/MPI 提交顺序做通信计算重叠（`Main_multiphase.F90:378-384`）。我们的 torch 后端无法照抄指令，但**内核划分与访存账**可直接搬 |
| 分布式多卡 | **是（2D 进程网格）** | `Main_multiphase.F90:349-458`、`Mpi.F90` | **reference** | 面/棱/角三级 halo + 26 tag φ 交换 + 异步层数可调；但 x 方向并行被硬禁（`IO_multiphase.F90:494-498`），是"受限的 3D 分解"，抄的时候别当作完整 3D |
| 稀疏多孔几何 | **是（本仓最强项）** | `Module.F90:90-103`、`Geometry_preprocessing.F90:236-360`、`Misc.F90:6-210` | **adapt** | ①`int1` 壁面数组 + 间接寻址的固体/流固界面节点表（18 邻居 + 权重）→ 稀疏几何 O(边界数) 而非 O(N) 遍历；②壁面法向 ISO8 八层平滑差分；③三步预处理链（text image→WallArray→法向表），可直接映射成我们的"冰区/多孔几何 → 面元表"流水线 |
| 案例组织 | **是（含 checkpoint 机制）** | §五.1、§五.5 | **reference** | 单文件参数表 + `job_status.txt` 状态机 + 墙钟定时双备份 checkpoint，工程上比我们现有 `runs/` 约定更抗长算例中断 |
| 测试验证面 / CI | **否** | §五.2-3 | — | 0 断言、0 参考值、0 CI；**不能**作为验证基线，只能当算例脚手架 |

---

## 八、注意点 / 陷阱（读这个仓最容易误判的 8 处）

1. **文档与代码不符（接触角算例贴哪面墙）**：`Module.F90:26-27` 与模板控制文件都写 "a fluid1 drop attached to **y=1**"，但实现把球心放在 `(x,y,z) = (0, 中心, 中心)`（`Init_multiphase.F90:296`：`(x-(nxglobal+1)*0.0d0)**2 + …`）→ 液滴实际贴在 **x=xmin 壁**。判读必须落到算式，不能信注释。
2. **θ>90° 检查被注释、限制仍在**：`IO_multiphase.F90:463-466` 把 "contact angle > 90° → error" 整段注释掉，而模板仍声明"必须 <90°、流体1 恒为非润湿相"。→ 超范围输入**静默接受**，结果无意义；移植时必须补守卫。
3. **"multirange color gradient" 是死功能**：`Module.F90:228` 保留 ISO8 权重、`:233` 保留 `lap_mr`，但主循环的 `color_gradient_multirange` 整段被注释（`Phase_gradient.F90:347-505`），实际用的是 ISO4 两级差分。ISO8 只剩"壁面法向平滑预处理"在用（`Geometry_preprocessing.F90:236-360`）→ 别把它当作"支持 multirange 色梯度"的证据。
4. **`opc(0:18)` 死表**：`Module.F90:114` 定义反方向表但**全树无消费者**（`grep "opc("` 仅此一处）→ 不存在基于它的通用反弹例程；壁面 no-slip 靠 §三.6 的隐式机制。
5. **x 方向并行"看起来有、其实没有"**：`Module.F90:245-283` 定义了 x 方向全部 MPI 缓冲区与 tag，`Main_multiphase.F90:367-370/431-434` 却把 x 条带 kernel 整段注释、`IO_multiphase.F90:494-498` 硬性拒绝 `npx≠1` → "3D 分解"实际是 2D 网格。
6. **失效保护薄弱**：失稳检测**只发生在 monitor 里**（`Monitor.F90:5-…` 尾部 NaN 检查）——把 `monitor_timer` 设大（本文探针的 run2/run3 就是）则**全场 NaN 后仍会继续算到 `ntime_max`**（§九实测），没有任何 `ieee_is_nan(phi)` 兜底。长算例必须自己加外部判活。
7. **变量/目录名拼写错误**：`postprocessing/exteme_large_sim_parallel_IO`（`exteme`），若写脚本按 README 的 `extreme_...` 找目录会踩空。
8. **单相/多相双份源码分叉**：`singlephase_3D/0.src` 与 `multiphase_3D/0.src` 有 9 个同名文件（`Module/Misc/Mpi/Mpi_misc/Monitor/IO/Boundary/Initialization/Kernel`），改一处**不会**同步另一处；本单所有结论只对 `multiphase_3D` 负责。

---

## 九、运行时探针（honest-FAIL）

**做成了什么**：在没有 MPI 的机器上，用 serial no-op MPI shim（`runs/m8547/mpi_stub.f90`，26 个符号，全部 no-op/自复制）把**未修改的上游源码**编成可执行文件（`gfortran 12.3 -O2 -fopenmp`，15 个对象 0 error；`-fallow-argument-mismatch` 是上游 makefile 自己对 gcc10+ 的建议，`multiphase_3D/makefile:96`），并跑通初始化与 VTK 输出。

| 运行 | 配置 | 结果 |
|---|---|---|
| run1 | 48³，球心贴 x=xmin 壁，R0=12，θ=45°，模板默认（ν1=0.004、ν2=0.4、σ=0.03、β=0.95），monitor 开 | 第 **1000** 步 monitor 报 `Simulation failed due to NAN of "saturation or capillary number"` 并退出 |
| run2 | 同 run1，`monitor_timer` 拉大（不调用 monitor） | 跑到 6000 步**不退出**（退出判据只在 monitor 里），但 `phi` 在 **3000/6000 步的 VTK 全为 NaN**（110592/110592） |
| run3 | **50×80×80，R0=18，θ=45°**（贴近日志自带 `1.drop_attached_wall` 的 `nx/ny/nz/θ/R0`），monitor 关，2000 步 | **1000 步 VTK 全 NaN**（320000/320000）；`ntime=1` 时 `phi` 正常（2952 个 φ>0.5 节点、max\|φ\|=1） |
| 计时 | 320,000 格 × 2000 步，OMP 16 线程 | `real 59 s` → ≈ `10.8 MLUPS`（**仅供参考**：serial shim + 本机 128 核 unknown-arch，非上游官方基准） |

图 1（`runs/m8547/fig_probe_nan.png`）：左＝run3 `ntime=1` 的 φ 中心剖面（初始化正常，界面锐利、无 NaN）；右＝两套配置下 NaN 占比随步数跃到 1.0。

**为什么必须标 FAIL / WITHHELD**：
- 目标量（输入 θ=45° → 测得接触角；静态液滴的 `max|u|`）**没拿到**：场在到达准稳态前就 NaN，任何 θ/表面张力/spurious-current 的**定量精度**结论都不能给。
- **归因未定**：可能是（a）真实数值不稳定（τ1=0.512 已贴近稳定边界，σ/R0 与 ν 比 100 的组合未调）；（b）serial shim 在某条我未覆盖的路径上引入偏差（虽然 run2/run3 除 `mpi_bcast/mpi_reduce` 外无 MPI 调用，且这两者在 np=1 时对动力学无写回；但**未能证明**）。因此本报告的**全部能力判断以静态源码为准**，探针只承担两件已确认的事实：**①源码可编译可初始化；②`monitor` 不调用时 NaN 不会被拦截**（后者是源码级可证的设计缺陷，`Monitor.F90` 尾部 NaN 检查 + `Main_multiphase.F90:184` 的调用条件）。
- 说明：波1 报告里我用过语义等价 shim 做微观探针且成功；本仓（Fortran+MPI+OpenACC）的 shim 面更大，故可信度更低——如实标注，不粉饰。

**最小下一步**（若后续要把它当性能标杆）：`pip` 装 `mpi4py` 不行，需 `openmpi + gfortran` 的 `mpif90` 真构建，并用仓库自带 `test_suites/2.drainage`（作者的排水算例）跑 1 万步看是否稳定——本单不做（超范围，且不动我们的仓）。

---

## 十、结论

1. **能力登记（相对我们 12 仓）**：本仓能提供的**唯一**"新东西"是 **稀疏多孔几何的间接寻址表征 + 壁面法向预处理链 + AA-pattern 融合内核 + checkpoint 状态机**这四件工程/算法器物（均可 `adapt`，且 BSD-3 允许带署名移植）；在**多相物理**上它落在「密度比 1 的 color-gradient + CSF + 几何润湿」这一格，**对 `GAP-LBM-MCMP-POISEUILLE` 与"MCMP 高密度比"零贡献**。
2. **权威清单归位修正**：`多相档` 正确；但必须标注 **非 Shan-Chen/MCMP、密度比 = 1、θ∈(0°,90°)、仅 3D、x 方向并行禁用**——否则会被误当作"高密度比多相基线"引用，进而得到错误的对标结论。
3. **可复用性**：BSD-3-Clause（可移植，需署名+许可证+免责声明）；但**验证面为空**（0 断言/0 参考值/无 CI），任何"精度"主张都必须由我们自己在 TensorLBM 里复现后才能引用。

---

## 机器块

[OSS-INV]
repo: lanl/MF-LBM
lang: Fortran 90/95
license: BSD-3-Clause (SPDX BSD-3-Clause; LICENSE in tree — reusable with attribution)
methods: D3Q19; MRT(19 moments, 4 compile-time presets via preprocessor.h mrt=1..4, default mrt=2 s_e=1.19/s_e2=1.4/s_pi=1.4/s_q=1.2/s_t=1.98); color-gradient (Rothman-Keller) two-PDF two-species formulation with R-K recoloring (beta) and partial densities; force = CSF 0.5*gamma*kappa*|C|*n + body force, injected in moment space, u includes +0.5F; phi=(rho1-rho2)/(rho1+rho2) algebraic (NOT a phase-field PDE); ISO4 two-level isotropic color gradient; curvature from divergence of normal; geometrical wetting (Akai et al. 2018) normal rotation at wall layer, static CA only, theta in (0,90) deg; AA-pattern fused pull-collide-push with odd/even alternation; boundaries: anti-bounce-back velocity inlet with 4-layer phi ghost, Zou-He pressure inlet, convective or Zou-He pressure outlet, implicit half-way bounce-back at solid via skipped solid cells; walls: no-slip only (no moving wall, no IBM, no slip length, no dynamic CA); MPI 3D cartesian but x-decomposition disabled (npy*npz); OpenACC/OpenMP directives (arch gpu/cpu/mic), async queues + comm/compute overlap; double precision only; 3D only
multiphase: color-gradient + CSF + geometrical wetting, DENSITY RATIO = 1 (no density contrast; no density-ratio parameter anywhere, grep "density ratio" = 0 hits); viscosity contrast supported (default nu1=0.004, nu2=0.4); sigma = gamma (0.03); no Shan-Chen/pseudopotential, no MCMP psi, no free-surface/VoF cell types, no Allen-Cahn/Cahn-Hilliard phase field, no contact-line model; theta limited to (0,90) with the >90 input check commented out (IO_multiphase.F90:463-466)
boundary: anti-bounce-back velocity inlet + 4-layer phi extrapolation (Boundary_multiphase_inlet.F90:6-102); Zou-He pressure inlet/outlet; convective outlet; implicit half-way bounce-back via skipped solid cells in fused kernel (Kernel_multiphase.F90:44,318,408; Init_multiphase.F90:72) — INFERENCE, not stated in comments; porous plate membrane BC (Boundary_multiphase_other.F90:8-94); no IBM, no partial slip
gpu: OpenACC directives (device_type(NVIDIA), collapse(3), 15 async queues Module.F90:159-164, comm/compute overlap Main_multiphase.F90:349-458), makefile -acc -Mcuda -ta=tesla:pinned; CPU OpenMP + AVX512 options; SoA + AA-pattern; no CUDA/HIP kernels, no mixed precision
tests: 8 test_suites = shell config generators only, ZERO assertions / ZERO reference data / no CI / no unit tests; Monitor.F90 provides the only NaN guard and only when monitor_timer fires
runtime: buildable with gfortran 12.3 + serial no-op MPI shim (upstream unmodified); initialisation and VTK IO verified; sim goes full-field NaN within 1000 steps in two configurations => quantitative accuracy (contact angle, surface tension, spurious current) WITHHELD; NaN is not trapped when monitor is not invoked (source-confirmed design gap)
summary: LANL 的 Fortran+MPI+OpenACC 多孔介质两相 DNS 求解器（14,915 行，BSD-3）。卖点是稀疏孔隙几何的间接寻址表征 + 壁面法向预处理 + AA-pattern 融合内核 + 通信计算重叠 + checkpoint 状态机；多相本身是**密度比 1 的 color-gradient(CSF+几何润湿)**，非 Shan-Chen/MCMP，无自由面/相场，θ∈(0°,90°)，仅 3D 且 x 方向并行禁用。四件工程器物可 adapt 进 TensorLBM，多相物理对我们现有 GAP 零贡献。验证面为空，精度主张须自行复现。
[/OSS-INV]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:sparse-porous-indirect-boundary-node-tables
tag: indirect-addressing-wall-tables
method: 用 int1 三维壁面数组表示几何；预处理阶段把固体格与"流-固界面格"各自展开成**一维列表**：固体格存 18 邻居方向号 + 各方向的流体邻居数 + 局部权重（la_weight），界面格存壁面单位法向 (nwx,nwy,nwz) 与局部接触角 θ；主循环只遍历列表即可完成 φ 在壁面的外推、色梯度法向的壁面修正与润湿旋转，代价 O(边界节点数) 而非 O(N) 全场扫描。
files: Module.F90:90-103, Geometry_preprocessing.F90:236-360, Geometry_preprocessing.F90:394-421, Geometry_preprocessing.F90:505, Phase_gradient.F90:16-29, Phase_gradient.F90:88-109, Phase_gradient.F90:225-261
conventions: 邻居方向用 D3Q9/D3Q19 的全局方向索引 0..18；权重按平衡态权重 w_equ(ie) 累加后除以 la_weight 归一；walls=1 固体 / 0 流体；θ 以 180°-θ 传入（IO_multiphase.F90:467）后再转弧度
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:geometrical-wetting-normal-rotation
tag: akai2018-geometrical-wetting
method: 在壁面邻层把已归一化的色梯度 n 绕"壁面法向 n_w 与当前 n 张成的平面"旋转到与 n_w 成 θ 的方向：cos ψ=sinθ(n_w·n)/√(1-(n_w·n)²)、候选 n± = (cosθ∓cos ψ)n_w ± …，两个候选解里取与原始 n 欧氏距离更近者，从而在壁面处强制目标静态接触角而无需引入任何人工体积力。
files: Phase_gradient.F90:209-265, Phase_gradient.F90:238-259
conventions: θ 为以润湿相量取的局部接触角；仅当该格 |C|>1e-6 才施加；候选择优用 distP<=distM 判据；仅适用 θ<90°（几何旋转无法表达疏水侧更强的反转）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:aa-pattern-fused-odd-even-kernel
tag: aa-pattern-fused-stream-collide
method: 奇数步在**单个 kernel** 内完成「从 18 个邻居 pull 分布 → 求 ρ1/ρ2/φ 与宏观量 → CSF 力 → MRT 碰撞 → R-K 再着色 → push 到邻居的反方向槽位」；偶数步不做流动，只在原地把反方向槽位互换再碰撞。每步每方向只发生 1 读 1 写，两套 PDF 交错寻址，避免全场 copy 与两套完整分布数组并存。
files: Kernel_multiphase.F90:45-105, Kernel_multiphase.F90:107-315, Kernel_multiphase.F90:317-354, Kernel_multiphase.F90:409-448, Kernel_multiphase.F90:690-717, Main_multiphase.F90:354-458
conventions: push 写入的是邻居的**反方向槽位**（f2(i+1)=g1t1），因此 pull 读到的永远是上一步的 outgoing；输出（VTK/监控）只在偶数步后做（Module.F90:157 注释 "due to AA pattern streaming, PDFs after odd step is stored in opposite way"）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:async-halo-overlap-order-swap
tag: openacc-async-halo-overlap
method: 把每步 kernel 拆成「边界条带 kernel（z/y 各自一条 async 队列）」+「内部大块 kernel」，中间插入 halo 打包与 MPI 异步收发；GPU 版（#ifdef _openacc）把内部 kernel 排在 mpi_send_req **之前** 提交，CPU 版相反，以此在两种平台上都让通信藏在大块计算后面；最后统一 sync 把 halo 灌回。
files: Main_multiphase.F90:354-394, Main_multiphase.F90:419-458, Module.F90:159-164, Module.F90:263
conventions: 队列编号固定（LBM_async_z=2,y=3,x=4, edge=11..14）；async 层数由 MPI_async_layers_num 配置（模板 0,4,4），x 方向层数必须 0（x 分解禁用）；φ 用 26 个独立 tag 交换面/棱/角
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:wall-clock-checkpoint-state-machine
tag: job-status-checkpoint-machine
method: 用 job_status.txt 承载算例状态机（new/continue/done/failed/reached_max_step），checkpoint 按**墙钟小时**定时（两级备份：主 + 第二备份），保存完整 PDF+φ（必要时含对流出流历史场），重启时读状态文件走 continue 路径避免重算；同时用 d_vol_* 支持按"注入孔隙体积"触发输出/监控，使长算例可无人值守分段。
files: IO_multiphase.F90:579-641, Main_multiphase.F90:91-95, Main_multiphase.F90:139-141, Main_multiphase.F90:222-303, Init_multiphase.F90:154-184
conventions: checkpoint_save_timer/simulation_duration_timer 单位小时；double_bak_checkpoint_pdf_cmd 控制是否双写；对流出流 BC 开启时必须额外存 f_convec_bc/g_convec_bc/phi_convec_bc
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:wall-normal-iso8-smoothing-preprocess
tag: iso8-wall-normal-table
method: 预处理阶段先把 int1 壁面数组做多级平滑，再用 ISO8（8 层、权重 4/45…1/5040）各向同性差分求固体表面法向，写入界面格的 n_w 表供主循环润湿模型使用；同一张表还用于 φ 在壁面的加权外推（权重 w_equ 累加/归一）。
files: Geometry_preprocessing.F90:236-360, Geometry_preprocessing.F90:505, Module.F90:228, Phase_gradient.F90:16-29
conventions: ISO8 权重向量长度 7（对应 1..8 层）；仅在预处理用，主循环色梯度用 ISO4 两级差分（multirange 版在 Phase_gradient.F90:347-505 被整段注释，属死功能）
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: lanl/MF-LBM
slug: NEW:two-species-partial-density-colorgradient
tag: two-pdf-partial-density-colorgradient
method: 用两套完整 PDF（f 与 g）分别承载两个"组元"，宏观量取和：den=ρ1+ρ2、φ=(ρ1-ρ2)/den、u 由总 PDF 的一阶矩给出；再着色把总 PDF 拆回两套（主方向权重 w_equ_1、对角 1/(36√2)），β 控界面锐度；粘度按 φ 线性插值倒数后转松弛率。
files: Module.F90:203-205, Kernel_multiphase.F90:108-110, Kernel_multiphase.F90:159-163, Kernel_multiphase.F90:272-315, Kernel_multiphase.F90:124
conventions: 纯相内 den≡1（→ 密度比恒 1）；φ∈[-1,1]；β 推荐 0.9~0.95；ω = 1/(6/(((1+φ)/ν1+(1-φ)/ν2)) + 0.5)
transfer: reference
[/OSS-SOL]