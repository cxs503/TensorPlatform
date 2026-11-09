# 【盘点】je-santos/MPLBM-UT —— Shan-Chen 多相多孔介质 3D 图像直驱（tier 1）

- 盘点主体：TensorPlatform 建设公民（mission 8284，锚点 FEAT-OSS-INVENTORY-LBM）
- 读码途径：`git clone` 被 SNI 窗口拒绝（120 s 超时），改用 **api.github.com contents API（base64）**逐文件取回 21 个文件；全树 `git/trees/HEAD?recursive=1`（162 blob）用于确证"无 CI/无 GPU/无自由面"等缺席结论。
- 取回清单与 blob SHA 见 `runs/oss_mplbm_ut/source_manifest.json`（可复核）。
- 定量复算脚本：`runs/oss_mplbm_ut/analysis.py` → `analysis_results.json`；图件 `runs/oss_mplbm_ut/figures/*.png`；纯公式模块 `runs/oss_mplbm_ut/wetting_law.py`。
- 回归校验：`tests/test_oss_inventory_mplbm_ut.py`（**18 passed**，`python3 -m pytest tests/test_oss_inventory_mplbm_ut.py -q`）——把"接触角反演重现仓内三点标定 <3%"、"排驱压差 ≡ Laplace"、"中性润湿零毛管压"固定成断言。
- **未读到的东西（不参与任何结论）**：`src/palabos-v2.2.1.zip`（4.3 MB 捆绑 Palabos 本体，未展开）、`examples/nanoconfinement/nano_lbm/lev_lbm.c`（取文件时 API 60/h 限额耗尽）。

---

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 仓库 | je-santos/MPLBM-UT | api.github.com `/repos/...` |
| 主语言 | **Python**（工作流层）+ 3 个 C++ Palabos 应用驱动（1702 行） | GitHub `language=Python`；LOC 实测见下 |
| 许可证 | **GPL-3.0** | `LICENSE:1-3` "GNU GENERAL PUBLIC LICENSE Version 3" |
| 活跃度 | **已归档（archived=true）**；created 2019-03-07，最后 push **2023-12-01**；183★/64 fork，6 open issues | `/repos/...` |
| 论文 | Santos et al. 2022, SoftwareX 18, 101097 | `README.rst:312-314` |
| 定位一句话 | 把 3D micro-CT 图像转成 **Palabos 的 Shan-Chen 两相 / 单相渗流算例**的**工作流层**：几何制备 → XML 输入生成 → `mpirun` 提交 → 毛管压力/相渗曲线后处理；**求解器本体不在本仓** | `README.rst:10`、`Install.sh:3-11`、`src/2-phase_LBM/ShanChen.cpp:1-2` |

**许可证对我们的可用性判断（关键）**：GPL-3.0 是**强 copyleft**，且本仓的全部物理能力依赖同样 GPL 的 Palabos 2.2.1（`Install.sh:3` 解压捆绑 zip 编译）。

- **代码文本不可并入 TensorLBM/TensorPlatform**（除非整仓亦 GPL 且开源分发），也不可 link Palabos 静态库后闭源分发。
- **可用**的部分：算法思想、公式、参数约定、接口/工作流设计——这些属不受版权保护的思想层面，由我们**重写实现**后使用（本报告所有 OSS-SOL 条目的 `transfer` 已逐条标注，均不含"抄代码"）。
- 结论：**reference-only（只做方法参考）**，与 tier 1 的"可提取复用"定位存在法务级落差，须在后续 harness 落库时保留该标记。

LOC 实测（`runs/oss_mplbm_ut/analysis_results.json:loc`，图 2）：

| 文件 | 行数 | 层 |
|---|---|---|
| `src/2-phase_LBM/ShanChen.cpp` | 946 | Palabos 应用驱动（两相） |
| `src/1-phase_LBM/permeability.cpp` | 380 | Palabos 应用驱动（单相 BGK） |
| `src/1-phase_LBM/permeability_MRT.cpp` | 376 | Palabos 应用驱动（名为 MRT） |
| `src/python/mplbm_utils/*.py`（8 件） | 1320 | 工作流层 |
| `examples/*.py`（9 件） | 1013 | 算例模板 |

即：**3 个 C++ 文件全是 `main()` + 自由函数堆叠的应用驱动，没有任何 LBM 内核（碰撞/迁移/受力算子）自研代码**；这些算子全部来自 Palabos（`ShanChen.cpp:1-2` include `palabos3D.h/.hh`）。

---

## 二、方法谱系

### 2.1 格子（lattice）

| 用途 | 描述符 | 指针 |
|---|---|---|
| 两相 Shan-Chen | `descriptors::ForcedShanChenD3Q19Descriptor`（D3Q19 + **额外两个变量存外力**，见 `:16` 注释） | `src/2-phase_LBM/ShanChen.cpp:15-17` |
| 单相渗透率（BGK 版） | `descriptors::D3Q19Descriptor` | `src/1-phase_LBM/permeability.cpp:32` |
| 单相渗透率（MRT 版） | `descriptors::MRTD3Q19Descriptor`（D3Q19 那行被注释掉） | `src/1-phase_LBM/permeability_MRT.cpp:32-33` |

全仓只有 **D3Q19**；无 D2Q9/D3Q15/D3Q27。

组织方式：`typedef double T`（`ShanChen.cpp:15`，双精度）+ `#define DESCRIPTOR`（`:17`）——即"单描述符、编译期固定"，切格子要改宏重编译。对照我们 TensorLBM 的多描述符运行期注册，这是最原始的一档。

### 2.2 碰撞

| 模型 | 实现 | 指针 |
|---|---|---|
| **ExternalMomentRegularizedBGK**（正则化 BGK + 外力矩） | `new ExternalMomentRegularizedBGKdynamics<T,DESCRIPTOR>(omega_f2/omega_f1)` | `ShanChen.cpp:596-601`（注释明写"Use regularized BGK dynamics to improve numerical stability (but note that BGK dynamics works well too)"） |
| **BGK** | `new BGKdynamics<T,DESCRIPTOR>(omega)` | `permeability.cpp:283`、`permeability_MRT.cpp:283` |
| MRT | **声明了 MRT 描述符但实例化的是 BGKdynamics** → 实际碰撞仍是 BGK | `permeability_MRT.cpp:33` vs `:283`（全仓 `grep MRTdynamics` = 0 命中） |
| TRT / KBC / RR / entropic | **无** | `grep -E "KBC|entropic|Recursive"` 全仓 0 命中 |

运动黏度由松弛因子反算：`nu = (1/omega - 0.5)/invCs2`（`ShanChen.cpp:593-594`、`permeability.cpp:267`）。

> **可复用性判定**：本仓对"碰撞谱系"的贡献≈0（只有"选哪个 Palabos 类"），`permeability_MRT.cpp` 的命名与实现不符是一处**名实不符**，若我们照它做"MRT 基线对比"会得到假结论——列入风险。

### 2.3 力项

三处，全部是**调用 Palabos 接口**，不是自研离散：

1. **组分间 Shan-Chen 力**：`integrateProcessingFunctional(new ShanChenMultiComponentProcessor3D<T,DESCRIPTOR>(G, constOmegaValues), ...)`（`ShanChen.cpp:619-621`）；`constOmegaValues` 的推入顺序 `omega_f2, omega_f1`（`:613-616`）必须与 `blockLattices` 推入顺序 `fluid2, fluid1`（`:610-612`）一致——**顺序错配即静默算错**，这是 copy 这份代码最易踩的坑。
2. **常体力**：`setExternalVector(lattice, bbox, DESCRIPTOR<T>::ExternalField::forceBeginsAt, Array<T,3>(force_f1,0.,0.))`（`ShanChen.cpp:349-352`，`force_f1=1e-4` 见 input xml:30-31）。
3. **润湿壁面黏附力**：由 `BounceBack<T,DESCRIPTOR>(G_ads)` 的黏附系数承担（`ShanChen.cpp:296-313`），不是体积力。

Guo/RK/shifted-velocity 等力项**在本仓文件中不出现**；Palabos 内部用哪种格式在捆绑 zip 里，**未读（unknown）**。

### 2.4 代码组织

单文件 `main()` + 自由函数（`readGeometry` `:149-185`、`setboundaryvalue` `:187-198`、`InitializeFluidsFromImage` `:203-256`、`PorousMediaSetup` `:258-366`、`writeGif_f1*`/`writeVTK_*`），参数走 XML：`XMLreader document(...)`（`ShanChen.cpp:448-518`，约 24 个字段：`geometry/size`、`geometry/per/fluid{1,2}`、`init/fluid{1,2}`、`fluids/{Gc,omega_*,force_*,G_ads_f1_s1..s4,rho_*,pressure_bc,rho_*_i,rho_d,num_pc_steps,min_radius}`、`output/{out_folder,save_sim,save_it,convergence,it_max,it_conv,it_gif,it_vtk,rho_vtk,print_geom,print_stl}`）。无类层次、无单元测试、无 CI。

---

## 三、边界处理（逐一定位）

| 型式 | 有/无 | 指针与说明 |
|---|---|---|
| **Bounce-back（半程，带润湿黏附）** | 有 | `defineDynamics(lattice_fluid1, geometry, new BounceBack<T,DESCRIPTOR>(Gads_f1_s1), 1)`，同一几何标签在 fluid1/fluid2 上取 `+G_ads`/`-G_ads`（`ShanChen.cpp:296-297`）。共 **4 类润湿性 + 1 类中性**：标签 `1`(s1) `4`(s2) `5`(中性, G_ads=0) `6`(s3) `7`(s4)（`:296-313`） |
| **NoDynamics（固体内部）** | 有 | 标签 `2` 的体相固体用 `NoDynamics` 而非 BB：**只让最外层固体参与反弹，内部固体零计算**（`ShanChen.cpp:292-293`） |
| **压力出入口（Dirichlet ρ→p）** | 有 | `boundaryCondition->addPressureBoundary0N(inlet, ...)` / `addPressureBoundary0P(outlet, ...)` + `setBoundaryDensity`（`ShanChen.cpp:273-287`；单相版 `permeability.cpp:135-143`）。Palabos 该 BC 的内部离散格式（Zou/He 或正则化）**未读，unknown** |
| **周期** | 有 | `lattice.periodicity().toggle(0..2, px/py/pz)`（`ShanChen.cpp:603-608`），两相可各自独立设周期（XML `geometry/per/fluid1|fluid2`，`input xml:8-11`） |
| **Zou-He / Inamuro 显式实现** | 无 | `grep -E "ZouHe|zou.?he|Inamuro"` = 0 |
| **插值 bounce-back / 曲率修正** | 无 | `grep -E "InterpolatedBounceBack|interpolatedBounce"` = 0；壁面一律"体素阶梯" |
| **IBM（浸没边界）** | 无 | `grep -E "IBM|immersed|ImmersedWall"` = 0 |
| **出入口盒子** | 有 | `inlet = Box3D(1,2, 1,ny-2, 1,nz-2)`、`outlet = Box3D(nx-2,nx-1, ...)`（`ShanChen.cpp:639-640`）——即 2 层入口/出口片，配合 `.dat` 几何两端补 `num_slices` 空片（`pore_utils.py:41-42`） |

**值得带走的工程细节**：入口/出口压力以"密度差"实现——`setBoundaryDensity(lattice, outlet, 1 - deltaP*invCs2)`（`permeability.cpp:140`），即 `Δρ = 3Δp`（`invCs2=3`）；两相侧 `deltaP = (ρ_fluid1 - ρ_fluid2)/3`（`ShanChen.cpp:628`）。同一条 `p=ρ/3` 约定贯穿单相/两相两侧，这是把"图像渗流"接到"达西定律"的窄腰。

---

## 四、多相/多组元（重点）

### 4.1 类型判定：**MCMP（双组分 Shan-Chen，双 lattice 联邦）**，非 SCMP

- 两套独立 `MultiBlockLattice3D`：`lattice_fluid2`（非湿相，omega_f2）与 `lattice_fluid1`（湿相，omega_f1）（`ShanChen.cpp:598-601`）；
- 二者通过 `ShanChenMultiComponentProcessor3D<T,DESCRIPTOR>(G, constOmegaValues)` 在多块上耦合（`:619-621`），`G` ← XML `Gc`（默认 **0.9**，正=组分互斥 → 相分离，`input xml:27`）；
- 每个 lattice 自带完整密度/速度场；图像里"孔隙"标签 0/2608 归 fluid2、"流体1"标签 3 归 fluid1（`InitializeFluidsFromImage`：`:219-251`；标签语义 `pore_utils.py:52-55`）。

### 4.2 互作用势形式与力项加法（诚实边界）

- **本仓没有任何 `psi(ρ)` 或 SC 力的离散代码**——全部在 Palabos 的 `ShanChenMultiComponentProcessor3D` 内（捆绑于 `src/palabos-v2.2.1.zip`，**未展开读，unknown**）。本仓只负责：(a) 传 `G` 与双 `omega`；(b) 提供存放外力的描述符变量（`ForcedShanChenD3Q19Descriptor`，`:16-17`）；(c) 用 `ExternalField::forceBeginsAt` 追加常体力（`:349-352`）。
- **潜在覆盖风险（推断，未运行验证）**：常体力写 `forceBeginsAt`（`:349-352`），而 SC 处理器每步也向同一字段写入组分力；若处理器覆盖该字段，则 `force_f1/force_f2` 失效或叠加次序依赖实现——我们若复用此"同一字段双源"的设计，须显式分离"外力项"与"相互作用力项"。

### 4.3 润湿性/接触角：Huang et al. (2007) 近似，**已定量核实**

仓内用 `cos θ ≃ 4·G_ads /(G_c·Δρ)` 反推入口压力（`ShanChen.cpp:573`，注释"Taking absolute value so that the difference in density is always positive"）：

```cpp
573: T cos_theta = abs(4*Gads_f1_s1/(G*(rho_f1_inlet - rhoNoFluid)));
574: T sigma = 0.15;                       // tuning parameter from docs
575: T delta_rho = 6*sigma*cos_theta/min_radius;
```

用仓内默认参数（`Gc=0.9`、`ρ_f1=2`、`ρ_d=0.06` → `Δρ=1.94`、`σ=0.15`）复算，并对仓内**实测标定三点**（`contact_angle_droplet_test.py:61` 注释：`G_ads=-0.4 → 156°`，`0 → 90°`，`+0.4 → 24°`）：

| G_ads,f1 | 仓内标定 θ | 公式 cosθ=4G/(GcΔρ) | 相对误差 |
|---|---|---|---|
| −0.4 | 156° | 156.4° | **0.26%** |
| 0.0 | 90° | 90.0° | **0.00%** |
| +0.4 | 24° | 23.6° | **1.68%** |

→ 公式与实测标定自洽（均 <3%，见第六节）。**同时暴露一处语义缺陷**：`:573` 的 `abs()` 把 `cosθ` 的**符号丢掉**——−0.4（156°，强非湿相/疏水）经 abs 后变成 23.6°（亲水）。对"只算排驱入口压力大小 `2σ|cosθ|/r`"无害，但**不能**用它区分吸渗/排驱方向，也不能作为我们"目标接触角 → G_ads"的通用反演器（我们若移植，必须保留符号：`cosθ = 4G_ads/(G_c Δρ)`，并同时给 two-sided `G_ads,f2 = -G_ads,f1`，见 `:296-297`）。

### 4.4 密度比能力

- 输入默认 `rho_f1 = 2`（湿相组分密度）、`rho_f2 = 2`（非湿相组分在自身占位处的密度）、`rho_d = 0.06`（"溶解相"密度，即另一组分在同点位的残留密度；`input xml:36-37,43`、`ShanChen.cpp:499`）。
- 名义密度对比 = `2 / 0.06 ≈ 33`，但这是**人为设定的稀疏残留密度**，不是通过状态方程得到的稳定高密度比；仓内**无**任何密度比控制/标定机制、无 HDR 手段（`grep "density ratio"` = 0）。
- 结论：**MCMP 高密度比能力 = 无**。对我们 `GAP-LBM-MCMP-POISEUILLE` 的高密度比分支，本仓**不可作为实现参照**，只能作为"低对比 SC 的基线"。

### 4.5 spurious current（伪电流）

- 仓内**无任何针对性处理**（`grep -i spurious` 全仓 0 命中）。
- 仅有两处间接缓解：① 用正则化 BGK 提升稳定性（`ShanChen.cpp:596-597` 注释）；② 提供 `G_ads=0` 的中性润湿壁面标签 5（`:304-305`）。
- 若我们吸收其排驱协议，**必须自建 spurious-current 指标**（否则 `Pc` 曲线在低 Ca 段被伪速度污染，无法判断 3% 精度）。

### 4.6 缺席确认（多相谱系纵横）

| 手法 | 本仓 | 证据 |
|---|---|---|
| SCMP（单组分伪势） | 无 | 只有双 lattice MCMP（`:598-621`） |
| free-energy / Allen-Cahn 相场 | **无** | `grep -E "Allen.?Cahn\|phase.?field"` = 0 |
| 颜色梯度 / 着色流体 | **无** | `grep -E "color.?gradient\|redFraction"` = 0 |
| free-surface / VOF | **无** | `grep -E "free.?[Ss]urface\|Hecht\|Vof"` = 0 |
| 反应输运（repo topics 里有 `reactive`） | 仓内代码未见（topics 宣传，未实现于已读文件） | 已读 22 文件 0 命中 |

### 4.7 准静态排驱 + 相渗：**本仓最有价值的多相工程套路**

```cpp
526: if (pressure_bc == true) runnum = num_pc_steps + 1;    // 压力级数
570-583: 每级出口密度 rho_fluid2[n] = rho_f2_i - n*step_size，step 由 Δρ=6σcosθ/r_min 定
619-621: SC 耦合处理器
759-849: 主循环 + 收敛判据 relE = |old-new|*100/old/it_conv，两级都 < convergence 即进下一级
857-914: 收敛后写 gif/vtk/dat，存 run_num.dat 与 saveBinaryBlock 支持断点续算
```

- 收敛判据：平均速度相对变化率/每迭代（`ShanChen.cpp:820-821`，`it_conv=1000`、`convergence=1e-4` 见 `input xml:50-52`），达到即 `checkconv=1`（`:846-849`），或 `it_max` 兜底（`:852-855`，默认 5e5）。
- 断点续算：`run_num.dat` 存"下一个还没算的压力级"（`:875-878`）、`iter_num.dat` + `saveBinaryBlock(lattice1/lattice2)` 存相场（`:897-903`），重启用 `loadBinaryBlock`（`:668-669`）——**集群上跑 40 核 × 数十级压力阶梯不丢进度**，这是可直接借的工作流设计。
- 相对渗透率：两相跑完后按每级饱和度切几何 → 逐相单相 K（`create_geom_for_rel_perm.py:76-114` 逐文件生成 `f1_for_kr_{i}.dat`/`f2_for_kr_{i}.dat`，`:122-128` 算 Sw）；`parse_palabos_output.py:28-37` 从 `relPerm&vels.txt` 切出 `krw`/`krnw`（`divide_kr_data = (num_runs-1)/2 + 1`）。

---

## 五、GPU / 并行

| 项 | 结论 | 证据 |
|---|---|---|
| GPU | **无任何实现**（仓内唯一 "GPU" 字符串是 README 未勾选的 TODO 复选框） | `README.rst:307` `- [ ] Testing GPU capabilities`；`grep -E "CUDA\|OpenCL\|kokkos"` = 0 |
| 分布式 | **有，但完全由 Palabos 提供**：本仓只生成 `mpirun -np N` 命令并 `subprocess.run` 提交 | `examples/contact_angle_droplet/contact_angle_droplet_test.py:45,50-51`；`README.rst:75-77`（MPI=OpenMPI 2.1.1/MPICH2）；`ShanChen.cpp:653,751` 用 `global::mpi().bCast` 广播重启计数 |
| 分区方式 | Palabos `MultiBlockLattice3D` 自动把多块撒到 MPI rank（本仓无自研分区/域分解代码） | `ShanChen.cpp:598-601`（构造即多块）；`README.rst:90` 给出经验值"每 rank 约 20³–50³"（40 核跑 128×52×175 ≈ 每核 22³） |
| 数据布局 | Palabos `Cell<T,Descriptor>` 结构体（每格含 q 个 population + 2 个外力变量，见 `ForcedShanChenD3Q19Descriptor` `:16-17`）——**属 Palabos 内部布局，本仓无自研 SoA/AoS/AA-pattern 代码** | `ShanChen.cpp:16-17` |
| 内存合并 / 通信隐藏 / 融合 kernel | 无（本仓层面零实现，Palabos 内部未读，unknown） | 已读 22 文件 0 命中 |
| 通信量优化 | 只有一个应用层技巧：固体内部 `NoDynamics`（`:292-293`）→ 减少活跃格点；响应阈值"每 rank 20³ 起"（`README.rst:90`） | 同左 |

> 对我们的意义：**GPU/多卡两项"已有开源实现方式集"在本仓拿不到任何东西**（正因如此它在我们 GPU 融合 kernel / 分布式多卡两个 slug 上只能记 "无"）。它的并行遗产是"作业封装 + 检查点续算 + 每 rank 25³ 经验标度"，属平台类而非内核类。

---

## 六、可微 / 代码生成

| 项 | 结论 | 证据 |
|---|---|---|
| autograd/JAX/PyTorch 友好度 | **无**。全仓依赖 `numpy/scipy/skimage/porespy/pyvista/matplotlib/pandas`，无 torch/jax；C++ 侧双精度手写模板 | `src/python/setup.py:15`；`ShanChen.cpp:15` |
| 符号推导 | 无 | 0 命中 |
| kernel 代码生成 | 无（无 codegen、无模板元编程产出，除 Palabos 自身的 C++ 模板） | 0 命中 |
| 唯一的"代码生成" | **输入文件字符串模板生成**：用 f-string 直拼 Palabos XML（`create_palabos_input_file.py:18-91` 单相、`:93-250` 两相、`:253-320` 相渗），并写 `run_shanchen_sim.sh` | `create_palabos_input_file.py:209-241`；`contact_angle_droplet_test.py:45-48` |

对 TensorLBM 的可微路线：**零参考价值**。唯一可借鉴的是"配置层用模板生成求解器输入"的工程形态（平台类，见第七节 `案例组织`）。

---

## 七、与我们功能点的映射（feature_ledger slug 对照表）

| slug（feature_ledger） | 本仓是否有实现 | 判定 | 指针 |
|---|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE**（两组分分层 Poiseuille） | **无该算例**，但有完整 MCMP 骨架（双 lattice + SC 耦合 + 润湿 BB + 压力阶梯）可直接改造为分层通道构型 | **adapt**（骨架可改造，需把图像驱动几何换成解析通道几何：`create_geom_for_palabos.py:22-24` 现在是 `np.fromfile(raw)`） | `ShanChen.cpp:598-621`、`:296-313`、`:570-583` |
| **MCMP 高密度比** | **无** | **not_supported**（名义对比 2/0.06≈33 只是设定值，无 HDR 机制） | `input xml:36-43`、`ShanChen.cpp:499` |
| **自由面（free surface）** | **无** | **not_supported** | grep 0 命中 |
| **相场 Allen-Cahn** | **无** | **not_supported** | grep 0 命中 |
| **GPU 融合 kernel** | **无**（仅 TODO） | **not_supported** | `README.rst:307` |
| **分布式多卡 / MPI** | 有（Palabos 分块 + 本仓 mpirun 封装 + 检查点续算） | **reference** | `contact_angle_droplet_test.py:45`、`ShanChen.cpp:653,668-669,897-903`、`README.rst:75-90` |
| **稀疏 / 多孔（性能类）** | **有实质可吸收点**：EDT 两段式几何——孔隙相邻固体层设 `BounceBack`，其余固体 `NoDynamics`；几何以 tag 压缩（2608-2611）落盘 | **fits**（思想可直接移植到我们的多孔/稀疏活跃集；代码须重写，GPL） | `pore_utils.py:11-58`（`:19` edist、`:34-35` 分两段、`:52-55` tag 编码）、`ShanChen.cpp:292-313` |
| **案例组织** | 有：每算例一目录 `input.yml` + `2_phase_sim.py` 脚本 + 自动生成 XML/bash | **fits**（模板化案例 = 我们 case registry 的样式参考） | `examples/steady_state_relperm_w_argparse/{input/2_phase_sim_input.xml,2_phase_sim.py}`、`create_palabos_input_file.py:1-320` |
| **测试与验证面** | **半有**：有 Young-Laplace 解析对照与接触角三点标定**脚本**，但**无断言、无 pytest、无 CI**（绘图即"验证"） | **adapt**（判据思路可用，需我们自己补断言与门禁） | `examples/young_laplace_validation/2_phase_sim.py:121-146,159`、`contact_angle_droplet_test.py:61-67` |
| **CI** | **无** | **not_supported** | 全树 162 blob 中无 `.github/`；6 个 `.yml` 全是算例 `input.yml` |
| **NEW: 润湿性反演器（θ ↔ G_ads 双向标定 + 符号保留）** | 本仓有单向且丢符号的用法 | **adapt**（我们自建双向、带符号、可验证） | `ShanChen.cpp:573`、`contact_angle_droplet_test.py:61` |
| **NEW: 准静态压力阶梯 + 断点续算协议**（可作我们 MCMP 排驱验收协议） | 有 | **fits**（协议可照搬，代码重写） | `ShanChen.cpp:526-537,570-583,875-903` |

补充：`pore_utils.py:120-176` 还有一条**与 PoreSpy 的混合路线**——用 PoreSpy 的 quasi-static drainage 造初始饱和度场，再按 Washburn `pc = -2σcosθ/(dt·voxel)`（`:151`，`σ=0.15` 同为 `:149`）映射成 MPLBM 几何，实现"孔隙网络初值 + LBM 精算"的加速初始化。这在我们"稀疏/多孔 + 案例组织"两处都可参考（快速给初值，减少 LBM 排驱级数）。

---

## 八、可吸收 Top3 + 风险

**Top1｜EDT 两段式几何 + tag→润湿性多类（稀疏/多孔性能类）**
把"孔隙相邻固体层"与"固体内部"分开：前者 `BounceBack(G_ads)`（并携带润湿性标签 1/4/5/6/7），后者 `NoDynamics`（`pore_utils.py:34-35`、`ShanChen.cpp:292-313`）。收益：① 活跃格点/内存大降（多孔介质固体占比常 60%+）；② 润湿性以**整数 tag 分 5 类**而非逐点参数——我们的多孔/混合润湿岩心可以照这个接口设计（tag 表 → dynamics 表）。落地方式：在 TensorLBM 的几何注册层实现"solid-shell/solid-core"两段 + tag→boundary-spec 映射表，**重写实现，不搬代码（GPL）**。

**Top2｜准静态排驱协议 + 断点续算（GAP-LBM-MCMP-POISEUILLE 的验收骨架）**
`num_pc_steps` 级压力阶梯、`Δρ = 6σcosθ/r_min`、`Δp = Δρ/3`、每级收敛判据 `relE<conv`、`run_num.dat`/`saveBinaryBlock` 续算（`ShanChen.cpp:526-537,570-583,820-849,875-903`）。我们做两组分分层 Poiseuille / 相渗曲线时，可直接采用同一套"级进 + 每级定常 + 保存断点"的协议，配我们的验收门（Laplace 恒等已被我复算为 **0.00% 误差**，可当 sanity gate）。风险：其收敛判据是"平均速度变化率"，对局部通道（分层 Poiseuille）会过早判敛 → 我们必须换成"体积流量 + 界面位置"双判据。

**Top3｜验证面模板：Young-Laplace 解析对照 + 接触角三点标定 + θ 反演器**
`young_laplace_validation/2_phase_sim.py:121-146` 用 `Pc = 2σcosθ/r` 造解析曲线与 LBM 结果同图对比（`:159`），`contact_angle_droplet_test.py:61-67` 用 −0.4/0/+0.4 三点扫 θ。我们缺的正是这种"参数→物理量"的验收判据（现在平台侧有验收机制但缺物理判据）。可直接重写成 `tests/` 里的参数化 pytest：给定 `G_ads/Gc/Δρ`，断言 `θ` 与解析反演的偏差 <3%（我已验证本仓标定值满足：0.26%/0%/1.68%）。

**风险清单**
1. **法务**：GPL-3.0 + Palabos GPL ⇒ 代码不可搬，只能重写（每条 OSS-SOL 已标 `transfer`，无一条为"copy"）。若后续 agent 直接粘贴，属许可证污染，需在 harness 侧加 git-diff 级检测。
2. **名实不符**：`permeability_MRT.cpp:33` 声明 MRT 描述符但 `:283` 实例化 `BGKdynamics`（全仓无 `MRTdynamics`）——**"MRT 基线"是假的**，切勿当 MRT 参考。
3. **两处实现隐患**：`ShanChen.cpp:573` 的 `abs()` 丢接触角符号；`ShanChen.cpp:349-352` 与 `:619-621` 对同一 `forceBeginsAt` 字段双源写力（推断有覆盖/次序依赖）。照抄即带病。
4. **几何 I/O 未闭环（unknown）**：Python 侧以 **int16 二进制**写 `.dat`（`pore_utils.py:51,56` + `create_geom_for_palabos.py:49` 的 `tofile`），C++ 侧以 `plb_ifstream >> *slice` 读入 **int32** 场（`ShanChen.cpp:163`），且仓内**未找到** 2608-2611 → tag `1/2/4/5/6/7` 的解码逻辑。复用前必须在集群上实跑一次确认（本次未编译 Palabos，无法确证）。
5. **休眠 + 无门禁**：archived（2023-12 停更），无 CI/无断言，所有精度声明都是作者论文级而非仓内可复现门禁；Palabos 2.2.1 已旧。
6. **API 限流留下的缺口**：`src/palabos-v2.2.1.zip`（SC 势函数与力离散的真实实现）与 `lev_lbm.c` 未读 → 本报告对"互作用势形式/力项离散"只能给到调用点，不给公式（**WITHHELD**，不做臆造）。

---

## 误差判定（第 ⑥ 要素，逐项）

| 关键量 | 基准 | 本仓/复算值 | 相对误差 | <3%？ |
|---|---|---|---|---|
| θ(G_ads=−0.4) | 仓内实测标定 156°（`contact_angle_droplet_test.py:61`） | 156.4°（`cosθ=4G/(GcΔρ)` 复算） | 0.26% | ✅ 是 |
| θ(G_ads=0) | 90° | 90.0° | 0.00% | ✅ 是 |
| θ(G_ads=+0.4) | 24° | 23.6° | 1.68% | ✅ 是 |
| 排驱总压差 Δp | Laplace `2σ|cosθ|/r_min` | `Δρ/3 = 2σ|cosθ|/r_min`（`ShanChen.cpp:575,628`） | **0.00%**（代数恒等，3 组参数抽检，`analysis_results.json:pc_ladder_max_rel_err`） | ✅ 是（恒等） |
| 出入口密度↔压力换算 Δρ | `p=ρ/3` | `Δρ = 3Δp`（`permeability.cpp:140`） | 0.00%（恒等） | ✅ 是 |
| **求解器绝对精度**（K、Pc(Sw)、kr(Sw) vs 实验/解析） | —— | **无法从仓内获得**：无参考数据文件、无 CI、无断言；本次未编译 Palabos 2.2.1，未跑算例 | **未知** | ❌ **WITHHELD（不给数、不粉饰）** |

> 说明：前三行是"仓内公式 vs 仓内标定"的**自洽性**核对（不是实验验证）；第六行是**能力真伪**的核对——本仓没有任何可执行的精度门禁，故本报告**不宣称**任何求解精度结论。若要给 TensorLBM 提供"对标实验 <3%"的证据，须在集群上编译 Palabos 2.2.1 并复跑 Young-Laplace 算例。

**最小下一步（建议，不在本单范围）**：`run_cluster` 在 n315 上 clone + `Install.sh` 编译 Palabos 2.2.1（CPU），跑 `examples/young_laplace_validation`（50×52×175，40 核）复现其 Pc(Sw) 与解析曲线偏差，产出"该实现的真实精度"数字；同时实测风险 4（几何 I/O 是否闭环）。

---

[OSS-INV]
repo: je-santos/MPLBM-UT
lang: Python (+3 C++ Palabos 应用驱动; 求解器本体=捆绑 Palabos 2.2.1, GPL)
license: GPL-3.0 (copyleft; 我们仅可 reference-only, 禁止代码搬运)
methods: D3Q19(ForcedShanChenD3Q19/D3Q19/MRTD3Q19); 碰撞=ExternalMomentRegularizedBGK(两相)+BGK(单相), MRT仅声明未用; 力项=ShanChenMultiComponentProcessor3D(Palabos)+ExternalField::forceBeginsAt 常体力+BounceBack(G_ads) 润湿; 无TRT/KBC/RR/entropic
multiphase: MCMP 双 lattice Shan-Chen (ShanChen.cpp:598-601, 619-621) + 润湿接触角 Huang2007 cosθ=4G_ads/(GcΔρ) (:573) + 准静态压力阶梯 (:526-537,570-583)；无SCMP/free-energy/相场/颜色梯度/自由面；无高密度比机制；无 spurious current 处理
boundary: 半程 bounce-back 带黏附 5 类 tag(1/4/5/6/7) (:296-313) + 内部固体 NoDynamics (:292-293) + Palabos 压力出入口 addPressureBoundary0N/0P (:273-287) + 周期 toggle (:603-608)；无 Zou-He/插值BB/曲率/IBM
gpu: 无 (README.rst:307 为未勾选 TODO)；并行仅靠 Palabos MPI 分块 + 本仓 mpirun 封装 (contact_angle_droplet_test.py:45) + 二进制检查点续算 (:897-903)
summary: 本仓是 GPL 的 Palabos Shan-Chen 多相渗流"图像直驱工作流层"，自身零 LBM 内核、零 GPU、零 CI、无断言；物理实现全部在捆绑的 Palabos 2.2.1 里，故方法谱系只能给到调用点。可吸收的实质是工程协议：EDT 两段式稀疏几何+润湿 tag、准静态排驱+断点续算、Young-Laplace/接触角验证面；绝对精度无法从仓内证明(WITHHELD)。
[/OSS-INV]

[OSS-SOL]
repo: je-santos/MPLBM-UT
slug: GAP-LBM-MCMP-POISEUILLE
tag: mcmp-two-lattice-shan-chen
method: 两套 MultiBlockLattice3D 各持一组分密度/黏度，用 Palabos ShanChenMultiComponentProcessor3D 以单一 G 耦合产生相分离力；润湿性由壁面 bounce-back 黏附系数按几何 tag 分 5 类给出。分层 Poiseuille 只需把图像几何换成解析通道几何即可复用此骨架。
files: src/2-phase_LBM/ShanChen.cpp:598-601, src/2-phase_LBM/ShanChen.cpp:610-621, src/2-phase_LBM/ShanChen.cpp:296-313
conventions: D3Q19 双精度; omega→nu=(1/omega-0.5)/3; Gc>0 为组分互斥(默认0.9); rho_f1=2, rho_d=0.06; G_ads,f2=-G_ads,f1 对称; 压力以密度差实现 Δρ=3Δp
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: je-santos/MPLBM-UT
slug: 稀疏/多孔
tag: edt-two-segment-geometry-tag-compression
method: 对二值图像做欧氏距离变换，距孔隙 1 层的固体设为 bounce-back 壁（携带润湿 tag），更深的固体设为 NoDynamics（零计算），其余为流体；几何按 tag 值（2608-2611）压缩为 int16 落盘，模拟端按 tag 分派 dynamics。
files: src/python/mplbm_utils/pore_utils.py:19-35, src/python/mplbm_utils/pore_utils.py:41-58, src/2-phase_LBM/ShanChen.cpp:292-313
conventions: tag 2608=孔隙, 2609=边界层, 2610=固体, 2611=回填非湿相; 两端各补 num_slices 空片供电压力出入口; swapXZ 必须与输入一致
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: je-santos/MPLBM-UT
slug: NEW: 准静态排驱压力阶梯 + 断点续算协议
tag: quasi-static-pc-ladder-with-checkpoint
method: 以 num_pc_steps 级递增出口密度实现准静态排驱，级差由 Δρ=6σcosθ/r_min 定（等价 Laplace 2σ|cosθ|/r）；每级跑到平均速度相对变化率<conv 判为定常；级间以 run_num.dat + saveBinaryBlock(两个 lattice) 断点续算。
files: src/2-phase_LBM/ShanChen.cpp:526-537, src/2-phase_LBM/ShanChen.cpp:570-583, src/2-phase_LBM/ShanChen.cpp:820-849, src/2-phase_LBM/ShanChen.cpp:875-903
conventions: Δp=Δρ/3 (p=ρ/3); sigma=0.15 为模型内部标定值; convergence=1e-4 %/iter, it_conv=1000, it_max=5e5; min_radius 决定可分辨喉道
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: je-santos/MPLBM-UT
slug: NEW: 润湿性反演器（θ ↔ G_ads 双向标定，符号保留）
tag: huang2007-contact-angle-inversion
method: 由壁面黏附系数反算接触角 cosθ=4G_ads/(Gc(ρ_w-ρ_nw))，并同时给非湿相侧 -G_ads；用液滴三点标定（G_ads=-0.4/0/+0.4 ↔ 156°/90°/24°）校验。移植时须去掉原实现的 abs() 以保留湿相/非湿相方向。
files: src/2-phase_LBM/ShanChen.cpp:573, src/2-phase_LBM/ShanChen.cpp:296-297, examples/contact_angle_droplet/contact_angle_droplet_test.py:61
conventions: Gc=0.9, ρ_w=2, ρ_nw(溶解)=0.06 → Δρ=1.94; θ 与 G_ads 单调反比; 三点标定误差 0.26%/0%/1.68%
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: je-santos/MPLBM-UT
slug: 分布式多卡
tag: palabos-multiblock-mpi-plus-checkpoint
method: 由 Palabos MultiBlockLattice3D 自动做 MPI 多块分区，应用层只负责生成 mpirun -np N 命令与检查点二进制落盘/恢复（saveBinaryBlock/loadBinaryBlock + 计数广播 bCast），使长压力阶梯可在多核上中断续算。
files: examples/contact_angle_droplet/contact_angle_droplet_test.py:45, src/2-phase_LBM/ShanChen.cpp:653, src/2-phase_LBM/ShanChen.cpp:668-669, src/2-phase_LBM/ShanChen.cpp:897-903
conventions: 经验标度每 rank 20^3~50^3 格点 (README.rst:90); MPI=OpenMPI2.1.1/MPICH2; 无 GPU/无通信隐藏
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: je-santos/MPLBM-UT
slug: 测试与验证面
tag: young-laplace-analytic-plus-droplet-calibration
method: 用 Pc=2σcosθ/r 造解析 Pc(Sw) 与 LBM 结果同图比较（Young-Laplace 验证算例），另以平面基底液滴算例扫 G_ads 三点标定接触角；但仅绘图、无断言，需我们补成参数化 pytest 与门禁。
files: examples/young_laplace_validation/2_phase_sim.py:121-146, examples/young_laplace_validation/2_phase_sim.py:159, examples/contact_angle_droplet/contact_angle_droplet_test.py:61-67
conventions: sigma=0.15 取自模型标定; 圆柱管半径扫描 + 高度 175 格; 40 核/50x52x175 参考算例
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: je-santos/MPLBM-UT
slug: 案例组织
tag: yml-driven-xml-template-pipeline
method: 每个算例目录一个 input.yml + 一个 runner 脚本；create_palabos_input_file 用 f-string 模板把 yml 展开成求解器 XML，再写 mpirun bash 脚本并 subprocess 提交；后处理脚本从固定文本标记（'Pressure difference ='）解析输出。
files: src/python/mplbm_utils/create_palabos_input_file.py:93-250, src/python/mplbm_utils/parse_palabos_output.py:4-16, examples/steady_state_relperm_w_argparse/input/2_phase_sim_input.xml:1-57
conventions: 配置层级 geometry/init/fluids/output 四段; 输出固定文本 'Pressure difference = x' 作为解析锚点; 算例自带大小/分辨率/压力级数
transfer: fits
[/OSS-SOL]