# 【盘点】ThermalMultiphaseOpenLB（JuliusWeinmiller/ThermalMultiphaseOpenLB）

> OSS-INVENTORY 波2 · scenario_key=`oss-inventory` · repo `JuliusWeinmiller/ThermalMultiphaseOpenLB`
> · commit `769efd471ac9`（2026-09-12，单 commit 单作者）。
> 派发单标签：OpenLB 的热-多相扩展（相变/热毛细方向），"2026 活跃"。
> **纪律**：本文每个 `文件:行` 由 `tools/oss_inventory/tmo_evidence.py` 的 needle 现场解析
> （`tools/oss_inventory/tmo_inventory.py` 渲染；指针缺失**或歧义**即构建失败）；每个数字由
> `tools/oss_inventory/tmo_eos_probe.py` **运行本仓自带的 Python EOS 模块**得到，零手抄。

---

## 一、概况

- **是什么**：OpenLB 的**外挂扩展**（不是独立求解器），加「单组分热-多相相变」：
  伪势 Shan-Chen 多相 + 温度 DDF + 相变源项，面向池沸腾/液滴蒸发。
  `README.md:2`；作者自述为硕士论文产物（TU Delft 论文库）`README.md:4` · `README.md:5`。
- **体量**（脚本统计 → `artifacts/oss_inventory/tmo_stats.json`）：被跟踪文件 120 个；
  扩展代码合计 **12561 行**（头文件 40 个 / 5678 行；
  算例 .cpp 12 个 / 5247 行；
  Python 9 个 / 1621 行）。

| EOS / 交互势 | 2 | 340 |
| Maxwell 等面积构造 | 2 | 375 |
| NS 动力学（修正 Guo 力格式） | 6 | 687 |
| 温度 DDF 动力学 | 2 | 311 |
| 热-流耦合 post-processor | 16 | 3070 |
| 物性 / 附加字段 / 边界 misc | 4 | 366 |
| 测量（归约 functor） | 6 | 468 |

- **依赖与构建**：须把 `phaseChangeExtension/` 放进 OpenLB 根目录（`README.md:30`）、
  `global.mk` 加 `-lgsl -lgslcblas`（`README.md:33`），并**手工改 OpenLB 本体**把
  `BounceBack` 的 `private` 改成 `protected`（`README.md:36`）；算例用
  `ROOT := ../../..` 指回 OpenLB（`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/definitions.mk:28`）。→ 它是**需要打补丁的源码级扩展**，
  不是可独立编译的库。
- **版本现实**：README 说目标 OpenLB v1.3-1（`README.md:25`）且「与 v1.4 不兼容」
  （`README.md:27`）；但源码用到 `olb::instances::getBulkMomenta`、`descriptors::tag::MRT`、
  `Cell::defineField` 等 1.4/1.5 期 API（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:356`、`phaseChangeExtension/phaseChangeSource/combinedShanChenThermalMRTCouplingPostProcessor2D.hh:48`）。
  **我们未构建、未运行 C++**（仓内无 OpenLB 依赖），故版本可用性记 **WITHHELD**，不臆造。
- **许可（决定能不能抄代码）**：全仓 **无 LICENSE 文件**（`license_files` = []）；
  新增源文件只有「OpenLB Extension ... for his thesis」注释、**无任何许可证文本**；
  Makefile/module.mk 系继承 OpenLB 模板的 **GPL-2.0-or-later** 声明
  （31 个 .h/.mk 带 GPL 段落，例 `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/Makefile:95`）。
  → 判定：**OpenLB 的 GPL 派生物**，扩展部分无独立授权。对我们（TensorLBM 非 GPL）：
  **不可复制代码**，只可读思想后自研。这与波1 的 ludwig（BSD-3）处置不同。
- **活跃度**：单 commit `769efd471ac9`（2026-09-12），无分支/PR/issue 交互；代码里
  留下 `// From python file: 0.3074`（`phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:80`）这类「论文落地稿」痕迹。
  按纪律修正派发单先验：**近期有提交，但无工程活动证据 → 学术参考实现，非活跃工程仓。**
- **论文谱系**（作者自列）：`README.md:57` · `README.md:61` ·
  `README.md:65` · `README.md:71` · `README.md:77`
  → Li/Luo/Li PRE 86,016709（力格式）；Gong & Cheng IJHMT 2012（相变 LBM）；
  Li/Zhou/Yan PRE 96,063303（改进热 LBM）；Li/Luo/Li PRE 87,053301（大密度比伪势）。

---

## 二、方法谱系

| 维度 | 本仓做法 | 指针 |
|---|---|---|
| 格子 | 流场 D2Q9/D3Q19（带 FORCE/EXTERNAL_FORCE/PSI_PSEUDO_RHO 字段）；温度 D2Q5/D3Q7 | `phaseChangeExtension/phaseChangeExamples/laplace2D/laplace2D.cpp:32` |
| 附加字段 | `PSI_PSEUDO_RHO`（ψ 缓存）、`PHI_THERMAL`（温度源项）、`PREV_T_V`、`PREV_PHI`（时间导数缓存） | `phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:19` · `phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:20` · `phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:21` · `phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:22` |
| 流场碰撞 | **BGK** + Guo 力 + **Li/Luo σ 修正**；σ 被作者称为「热力学一致性」调参 | `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.h:34` · `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:30` · `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:80` |
| 力格式 | `_prefactor=σ/(1/ω−0.5)`；先 `u+=F/2`，再 `u+=σ(F−F_ext)ρ/ψ²`，最后 `addExternalForce` | `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:35` · `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:77` · `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:83` · `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:86` |
| 温度碰撞 | 独立 DDF：一阶平衡态 + φ 源项 + 0.5·∂_tφ + c·∂_t(Tu)（PRE 96,063303） | `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.h:30` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:44` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:79` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:83` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:89` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:78` |
| 温度 MRT | 仅温度场有 MRT：`invM_S` 由 `descriptors::s` 生成，源项走 `invM(1−S/2)` | `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.h:58` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:202` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:208` |
| 状态方程 | 抽象基类四钩子：`ψ(ρ)`、`ψ(ρ,T)`、`(∂p/∂T)_ρ`、`p(V)`；实现 **PR** 与 **CS** | `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.h:36` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.h:38` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.h:40` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:71` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:104` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:191` |
| ψ 定义 | `ψ = sqrt(6(p−ρ/3)/G)` | `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:93` |
| 共存密度 | **Maxwell 等面积**：GSL Brent 求根 + QAGS 自适应积分，令 ∫(p−p_sat)dV=0；精度 1e-6、迭代 200 | `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:150` · `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:88` · `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:140` · `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:282` · `phaseChangeExtension/phaseChangeSource/maxwellConstruction.h:45` |
| 热物性 | `FluidProperties`：按 ρ 归一化后在汽/液值线性插值（c_v、k），温度比线性映射 | `phaseChangeExtension/phaseChangeSource/fluidProperties.hh:76` · `phaseChangeExtension/phaseChangeSource/fluidProperties.hh:56` · `phaseChangeExtension/phaseChangeSource/fluidProperties.hh:81` |
| 热-流耦合 | 单个组合 post-processor 一次算完：ψ 场缓存 → 伪势+浮力体力 → 温度源项 φ → 速度耦合 | `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:113` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:143` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:161` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:187` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:195` |
| 浮力 | 非 Boussinesq：`(1−ρ̄/ρ)·g` | `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:157` |
| **没有** | 流场 MRT/KBC/cumulant、IBM、可微/代码生成、GPU、MCMP、自由面、相场 | 见第四、五节与 stats.json |

`InteractionPotentialExtended` 的四虚函数是**真正的扩展点**（换 EOS 不动求解器）：
`phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.h:20` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.h:84`。这是做「EOS→共存密度→伪势」链路时
最值得照抄的**接口形态**（代码本身因 GPL 不能抄，见第一节）。

---

## 三、边界 / 壁面 / 润湿

- 壁面 = 标准 `BounceBack`；**润湿性靠壁面密度 ρ_wall**（Shan-Chen 流-固作用的等效实现）：
  `phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:11` · `phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:161` · `phaseChangeExtension/phaseChangeExamples/poolBoiling2D/poolBoiling2D.cpp:79`。
- OpenLB 的 `BounceBack::_rho` 是 private，作者写了子类 `BounceBackVariableRho` 在运行期改 ρ_wall，
  并需按 README 打补丁：`phaseChangeExtension/phaseChangeSource/miscFeatures.h:90` · `phaseChangeExtension/phaseChangeSource/miscFeatures.h:84` ·
  `phaseChangeExtension/phaseChangeSource/miscFeatures.h:94` · `phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:399`。
- 温度边界：用 OpenLB 的 AD 边界条件族（Dirichlet/Neumann）：
  `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:441` · `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:442`。
- 周期域：`cuboidGeometry.setPeriodicity(true,true)`（液滴/Laplace 消壁面影响）：
  `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:361`。
- **没有**：几何接触角输入（只能给 ρ_wall 标量，不能直接指定 θ）、自由滑移/部分滑移、移动壁、
  IBM。→「指定接触角」必须自建（壁面标量无法表达 θ）。

---

## 四、多相 / 相变（本仓的心脏）

### 4.1 类别先钉死

**单组分伪势（SCMP）+ 温度相关 EOS + 相变源项。**

- **不是 MCMP**：只有一套流场分布函数与一组 `PSI_PSEUDO_RHO`（`phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:19`）；
  无第二组分 ψ、无 G_αβ 互作用矩阵、无 recoloring。
- **不是自由面**：气相是被 EOS 描述的稀疏相（ρ_v ≫ 0），界面是密度梯度的弥散界面；
  无空单元/`ε=mass/ρ` 记账。
- **不是相场**：无 φ 序参量、无 Cahn-Hilliard/Allen-Cahn；`PHI_THERMAL` 是温度方程的**源项**
  而不是序参量（`phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:20` · `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.hh:79`）。

### 4.2 高密度比从哪来（最值得吸收的部分）

1. PR-EOS：`Tc=0.0778/0.45724·a/b/R`、`ρc=pc/0.307/R/Tc`，温度只进 `α(T)`
   —— `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:76` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:78` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:104`；
   `(∂p/∂T)_ρ` 有解析表达式（`phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:116`）。
2. 用 Maxwell 等面积构造求该 T 下的共存密度对（`phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:150` · `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:282`）。
3. 算例把这对密度交给 `FluidProperties` 与初始化
   （`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:386` · `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:388` · `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:410`）。
4. 潜热**不显式给 L_v**，而是经 `(∂p/∂T)_ρ` 进温度方程：
   `φ = T∇·u − (T/Tc)/(ρc_v)·(∂p/∂T)_ρ·∇·u` —— 压缩功与相变潜热合并成同一个 ∇·u 源项
   （`phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:176` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:182` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:184` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:185`）。

**我们实测**（`tmo_eos_probe.py` 调用本仓 `eos.py`；格点 PR：G=−1, a=2/49, b=2/21, ω=0.3443）：

| Tr=T/Tc | ρ_l（格点） | ρ_v（格点） | 密度比 | p_sat（格点） | Maxwell 等面积残差（相对） |
|---:|---:|---:|---:|---:|---:|
| 0.50 | 9.219562 | 0.000831244 | 1.109e+04 | 3.027e-05 | 2.6e-13 |
| 0.55 | 8.987790 | 0.00334003 | 2691 | 0.0001333 | 1.8e-13 |
| 0.60 | 8.725175 | 0.0102185 | 853.9 | 0.0004414 | 1.2e-12 |
| 0.65 | 8.425559 | 0.025628 | 328.8 | 0.001181 | 5.5e-13 |
| 0.70 | 8.080690 | 0.0555955 | 145.3 | 0.00269 | 1.2e-13 |
| 0.75 | 7.679136 | 0.108588 | 70.72 | 0.005412 | 2.6e-13 |
| 0.80 | 7.204233 | 0.19704 | 36.56 | 0.009892 | 4.6e-13 |
| 0.85 | 6.629706 | 0.341204 | 19.43 | 0.01676 | 2.0e-13 |
| 0.90 | 5.908229 | 0.579975 | 10.19 | 0.02675 | 1.3e-13 |
| 0.95 | 4.928407 | 1.01303 | 4.865 | 0.0407 | 4.0e-13 |

- 实测 10/10 个温度比（Tr=0.50→0.95）全部收敛 → 可达密度比区间 = **1.109e+04（Tr=0.50） ~ 4.865（Tr=0.95）**。README 所称「高密度比」
  （`README.md:8`）本质是**低 Tr 下 EOS 的共存行为**；σ 修正的作用是让这个密度比下
  **不发散**，而不是把密度比「调大」。
- Maxwell 构造自身自洽：等面积残差 ≤ 1.2e-12（相对）——C++ GSL 与 Python scipy
  两套实现都在机器精度闭合。
- 跨实现细节：C++ 用 Zc=0.307、Python 用 0.3074（`phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:78` · `phaseChangeExtension/EquationOfStatePython/eos.py:331`）；
  实测把 ρc 换回 0.307 重算，共存密度**逐位不变**（ρ_l 相对差 0.0e+00，
  ρ_v 0.0e+00）——ρc 只当求根初值/区间。**该「不一致」被数值否定，不作缺陷记。**

### 4.3 稳定化手段（可复用清单）

- **σ 修正力格式**：`u += σ(F−F_ext)ρ/ψ²`，σ≈0.33（`phaseChangeExtension/phaseChangeExamples/poolBoiling2D/poolBoiling2D.cpp:88`）。
  这是伪势在 10³ 密度比下不崩的直接原因。
- **两遍缓存**：先把 ρ/ψ/u/T 摊到 (nx+2)(ny+2) 缓存，再遍历内点算力与源项
  （`phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:79`），避免「边算边改」的雅可比污染。
- **半隐式热求解器（semi-hybrid）**：把 LBM 自带扩散项扣掉（`−constant_k∇²T`，
  `constant_k=(1/τ_T−0.5)/invCs2`），再显式加回 `∇·(k(ρ)∇T)` 与链式项 `dk/dρ·∇ρ·∇T`：
  `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:48` · `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:217` · `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:220` ·
  `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:221` · `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:224`。
  目的：热扩散率与 ν 解耦、允许 k(ρ) 变物性；代价：显式 FD 受扩散 CFL 约束（Δt 变小）。

### 4.4 相变算例（是验证面，不是库）

| 算例 | 干什么 | 判据 | 指针 |
|---|---|---|---|
| `laplace2D/3D` | 静止液滴扫半径 | Laplace 律 Δp=σ/R 线性度 + 反算 σ | `phaseChangeExtension/phaseChangeExamples/laplace2D/laplace2D.cpp:15` · `phaseChangeExtension/phaseChangeExamples/laplace2D/laplace2D.cpp:329` |
| `d2law2D/3D` | 加热液滴蒸发 | **D² 律**（直径平方线性衰减） | `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:17` |
| `thermodynamicConsistency2D/3D` | Tr×σ×a 扫描 | 仿真共存密度 vs Maxwell 解析值 | `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:13` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:285` |
| `poolBoiling2D` | 池沸腾起泡 | 视觉/经验（ρ_wall=5、g×10⁴、噪声 5%） | `phaseChangeExtension/phaseChangeExamples/poolBoiling2D/poolBoiling2D.cpp:17` · `phaseChangeExtension/phaseChangeExamples/poolBoiling2D/poolBoiling2D.cpp:84` · `phaseChangeExtension/phaseChangeExamples/poolBoiling2D/poolBoiling2D.cpp:76` |
| `wallWettability2D/3D` | ρ_wall → 接触角 | 视觉（图像量角） | `phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:11` |
| `phaseSeparation3D` / `evaporatingDroplet2D` / `maxwellConstruction` | 旋节分解 / 蒸发起步 / 纯 Maxwell 出表 | — | `phaseChangeExtension/phaseChangeExamples/phaseSeparation3D/phaseSeparation3D.cpp:14` · `phaseChangeExtension/phaseChangeExamples/evaporatingDroplet2D/evaporatingDroplet2D.cpp:17` · `phaseChangeExtension/phaseChangeExamples/maxwellConstruction/maxwellConstruction.cpp:12` · `phaseChangeExtension/phaseChangeExamples/maxwellConstruction/maxwellConstruction.cpp:85` |

---

## 五、GPU / 并行 / 测量

- **无 GPU**：扩展全仓 `CUDA / __device__ / OpenMP / #pragma omp / HIP / SYCL` 命中 =
  CUDA=0, __device__=0, OpenMP=0, #pragma omp=0, SYCL=0, HIP=0（脚本统计）。热-流耦合是逐格双循环的 CPU post-processor（`phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:79`）。
  对我们的「GPU 融合 kernel」：**只借结构边界**（ψ / ∇ρ / ∇T / 力 / 源项在同一次遍历里算完，
  见 `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:180` · `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:197`），不借代码。
- **并行 = OpenLB 的 MPI 域分解**：`noOfCuboids = mpi().getSize()`（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:356`）+
  `HeuristicLoadBalancer`（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:364`）；缓存场是单块内 `BlockData2D`，
  跨块一致性依赖 OpenLB SuperLattice 的同步次序。测得 `singleton::mpi()` 调用点
  12 处（全部在算例里，扩展自身无通信代码）。
- **测量/归约**：作者补了三个归约 functor（在 indicator 子集上求和/平均并回传体素数）：
  `phaseChangeExtension/phaseChangeSource/blockIntegralF2D.hh:58` · `phaseChangeExtension/phaseChangeSource/blockIntegralF2D.hh:67` · `phaseChangeExtension/phaseChangeSource/blockAverage2D.h:36` ·
  `phaseChangeExtension/phaseChangeSource/superAverage2D.h:37` → 这正是「液滴质量/直径/汽化率」这类标量验收量该落的位置
  （d2law 需要它却没接上，见 6.2）。
- 输出：VTM/VTK（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:240`）+ heatmap（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:289`）+ gnuplotData；
  没有内置的标量时间序列写盘（除参数表 `phaseChangeExtension/phaseChangeSource/fluidProperties.hh:125`）。

---

## 六、验证面与可复现性（本仓最弱一环，也是我们该反着做的地方）

### 6.1 现有验证面

- **0 个单元测试**（被跟踪文件里 `test/tests/unittest` 命中 0 个）、
  **0 个 CI**（`.github` / `*.yml` 命中 0 个）。
- 6 个 `resultsEval*.py`（resultsEvalDerivativeComparison.py, resultsEvalLaplacePressure.py, resultsEvalThermoConsist2D.py, resultsEvalThermoConsist3D.py, resultsEvalThermoConsistComparison.py）**把仿真结果数组写死**在脚本里再画图人眼看：
  `phaseChangeExtension/phaseChangeExamples/laplace2D/resultsEvalLaplacePressure.py:15` · `phaseChangeExtension/phaseChangeExamples/laplace2D/resultsEvalLaplacePressure.py:28`；还有硬编码时间门槛
  `if int(time) > 5000:`（`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:47`）。→ 不可回归、不可跨版本比对。

### 6.2 扫描面 vs 评估脚本（脚本解析实测）

C++ 扫描（`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:285` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:286` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:287`；
目录名用 `std::setprecision(2)`：`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:290`）对 Python 查询
（`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:21` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:23` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:25`）：

- **σ**：C++ 产出 ['0.33', '0.34', '0.35']，脚本查询 ['0.3', '0.31', '0.32', '0.33', '0.34', '0.35']
  → **查了但永不存在的档位 ['0.3', '0.31', '0.32']**（脚本静默跳过，图上「缺点」而不报错）。
- **a**（目录名是 eosA 本身）：C++ 产出 ['0.01', '0.02', '0.031', '0.041']，脚本查询 ['0.01', '0.02', '0.031', '0.041', '0.051', '0.061']
  → **查了但永不存在的档位 ['0.051', '0.061']**；且理论曲线只用 a=1/49 算**一次**
  （`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:26` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:27`），故除 a=1/49 档外「Theory」线
  并非该档理论值（扫描范围 a = 0.50/49 ~ 2.00/49）。
- **Tr**：C++ 只扫到 0.85，理论曲线 `linspace(0.6,0.92)` 画到
  0.92（`tr_theory_exceeds_sweep` = True）。

以上三条是**可程序化复现的验证面缺陷**（不是主观评价），也解释了为什么本仓的
「热力学一致性」结论只能当**定性**证据。

### 6.3 标定不自洽（实测数值后果）

`d2law2D` 里两套标定互不相容：物性用真实水表值 ρ_l = 947.13、ρ_v = 0.9643 kg/m³
（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:89` · `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:92`），T_c = 647 K、Tr = 0.6
（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:80`）；但单位换算用**硬编码**格点液密度 8.72518
（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:96`），而该值本应由同函数内的 Maxwell 构造算出
（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:388`）。实测硬编码 vs Maxwell 相对差 5.4e-07
（Tr=0.6 时无害，但换 Tr/EOS 就静默错，而 `thermodynamicConsistency` 正是改 Tr 的算例）。

数值后果：格点 ρ_v = 0.0102185 × 换算因子 108.55 =
**1.109 kg/m³**，而算例自述物理汽密度 0.9643 → 相对差
**15.0%**。即 EOS 标定与物性表标定互不自洽，量级 15%，
正卡在我们「>15% 不可接受」的门槛上（见 8.3）。相应密度比：自述物理比
982.2 vs 格点 Maxwell 比 853.9 —— 引用时必须自己算，不能抄论文口径。

### 6.4 运行时缺陷（我们复现的）

- **Carnahan-Starling 分支不可用**：`CarnahanStarlingInteractionPotential` 未定义 `rhoc`
  （`phaseChangeExtension/EquationOfStatePython/eos.py:382`），而基类 Maxwell 求根首行就用 `Vc = 1/self.rhoc`（`phaseChangeExtension/EquationOfStatePython/eos.py:140`），
  故 `phaseChangeExtension/EquationOfStatePython/eos.py:416` 必然抛 `AttributeError: 'CarnahanStarlingInteractionPotential' object has no attribute 'rhoc'`。
  → C++ 侧 CS EOS（`phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:191`）比 Python 侧可用性高，两边不同步。
- 仓库卫生：**12 个 `.tmpfile`**（构建期依赖文件，含作者本机绝对路径
  `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/.tmpfile:1`）被误提交。

---

## 七、与我们功能点的映射（feature_ledger）

| feature_ledger slug | transfer | 本仓对应 tag |
|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE` | `reference` | `sigma-corrected-shan-chen-force` |
| `MCMP 高密度比` | `adapt` | `maxwell-consistent-eos-coexistence` |
| `自由面（free surface）` | `reference` | `absent-single-component-pseudopotential` |
| `相场 Allen-Cahn` | `reference` | `absent-no-phase-field` |
| `GPU 融合 kernel` | `reference` | `absent-cpu-templates-only` |
| `分布式多卡` | `reference` | `mpi-inherited-from-openlb` |
| `稀疏/多孔` | `adapt` | `absent-but-wall-density-wetting` |
| `案例组织` | `adapt` | `per-example-makefile-triplet` |
| `测试与验证面` | `reference` | `parameter-sweep-plus-manual-pyplot` |
| `CI` | `reference` | `none` |
| `NEW: 半隐式热求解器（LBM 扩散替换为显式 FD）` | `adapt` | `semi-hybrid-thermal-solver` |
| `NEW: Maxwell 等面积构造（共存密度标定器）` | `adapt` | `maxwell-equal-area-gsl` |
| `NEW:  thermodynamic-consistency 扫描面（Tr×σ×a）` | `adapt` | `parametric-consistency-sweep` |

- **`GAP-LBM-MCMP-POISEUILLE`（reference）**：无第二组分，做不了分层两组分 Poiseuille；
  可借的是 σ 修正力格式与 ψ 字段落位（`phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:83` · `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:143`）；
  「界面位置/两相流量分配」这类验收本仓不提供。
- **`MCMP 高密度比`（adapt）**：**本仓核心价值**。吸收「EOS(T)→Maxwell 等面积→共存密度对→ψ 场」
  闭环（`phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:150` · `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:116` · `phaseChangeExtension/EquationOfStatePython/eos.py:128`）与「潜热经 (∂p/∂T)_ρ 进温度
  方程」（`phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:185`）；不吸收它的标定方式（6.3）与无断言验证面（6.1）。
- **`自由面`（reference）**：**无**。仅提供「弥散界面由 EOS 自然出现」的对照物
  （`phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:106`）。
- **`相场 Allen-Cahn`（reference）**：**无**。唯一可借的是「温度场当第二套 DDF」的组织
  （`phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.h:30`），与相场无关。
- **`GPU 融合 kernel`（reference）**：无 GPU 代码；借融合边界（`phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:180` ·
  `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:197`）。
- **`分布式多卡`（reference）**：只有 OpenLB MPI cuboid 的继承用法（`phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:356` ·
  `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:364`）。
- **`稀疏/多孔`（adapt）**：无多孔模型；但 ρ_wall 润湿 + 可变密度反弹壁 = 「固体边界可调亲和力」
  的现成范式（`phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:161` · `phaseChangeExtension/phaseChangeSource/miscFeatures.h:94`）。
- **`案例组织`（adapt）**：Makefile + definitions.mk + module.mk 三件套 + 目录名即参数
  （`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/definitions.mk:29` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/module.mk:29` · `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:290`），构建约定可直接抄
  （这部分是构建脚本约定，不涉 GPL 代码）。
- **`测试与验证面`（reference）**：**反面教材**（6.1 / 6.2）。
- **`CI`（reference）**：无（6.1；另有误提交的 `.tmpfile`，`phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/.tmpfile:1`）。

---

## 八、Top3 吸收 + 明确不吸收 + 误差判定

### 8.1 Top3（按对齐缺口账本的价值排序）

1. **EOS → Maxwell → 共存密度对 的标定链（自研实现，不抄代码）**：补 `MCMP 高密度比` 的
   「给定 T 求 ρ_l/ρ_v」缺环；本仓 C++ / Python 双实现可互为交叉校验源。
   移植验收判据：等面积残差 < 1e-10、共存密度随 T 单调、临界点附近退化行为合理。
   证据 `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:140` · `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:282` · `phaseChangeExtension/EquationOfStatePython/eos.py:128`。
2. **半隐式（semi-hybrid）变物性热求解器**：把热扩散率从 τ_T 解耦、允许 k(ρ)；实现成本低
   （一个显式 FD 项 + 一个链式项），适配我们变物性两相换热。
   证据 `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:48` · `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:217` · `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:220` ·
   `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:221`；注意显式扩散 CFL 代价。
3. **σ 修正力格式 + 两遍缓存耦合组织**：高密度比不崩的直接原因，实现上只是在 Guo 移位上加一项
   （`phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:83` · `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:35`）；两遍缓存（`phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:79`）正是 GPU 融合
   kernel 的天然切分边界。

### 8.2 明确不吸收

- **代码本身**（GPL 派生 + 扩展无独立授权）：一行都不抄。
- **它的验证面**（写死结果数组、理论曲线不随参数、无断言、无 CI）：进我们的反向清单。
- **硬编码标定**（`8.72518`、`g×10⁴` 之类）：我们一律由 EOS/物性参数程序化推导并留 provenance。
- **两套 Maxwell 各自为政**（C++/Python 无交叉测试）：我们要求单一实现 + 交叉校验测试。

### 8.3 误差判定（关键量 vs 基准，逐项显式回答「是否 < 3%」）

| 关键量 | 我们的值 | 基准 | 相对误差 | &lt;3%? |
|---|---|---|---|---|
| Maxwell 等面积残差（构造自洽性） | 1.2e-12（相对） | 0 | ~1e-12 | **是** |
| 格点 PR 共存 ρ_l（Tr=0.60） | 8.98779 | 本仓硬编码 8.72518 | 5.4e-05% | **是** |
| 物理汽密度（由格点 ρ_v 换算出，Tr=0.60） | 1.109 kg/m³ | 算例自述 0.9643 | 15.0% | **否**（且 >15%，不可接受） |
| 真实水液密度（real-PR, Tr=0.60） | 789.4 kg/m³ | 表值 947.13 | 16.7% | **否**（>15%，不可接受） |
| 真实水汽密度（real-PR, Tr=0.60） | 0.9256 kg/m³ | 表值 0.9643 | 4.0% | **否**（>15%，不可接受） |
| ρc/Zc 差异（0.307 vs 0.3074）对共存密度 | 逐位相同 | —— | 0e+00% | **是**（不敏感） |

**结论（不粉饰）**：本仓**内部自洽性极好**（Maxwell 残差 ~1e-12、ρc/Zc 不敏感），
但**外部精度不合格**：Peng-Robinson 对 Tr=0.6 水的液相密度偏低 16.7%
（PR 的已知缺陷），且示例内两套标定之间差 15.0% —— 均远超 3%，也超过
「>15% 不可接受」的界线。因此本仓只能作**方法参考**，不能作**精度基准**；
我们若用 EOS 标定密度比，必须接 IAPWS/查表界面或标定过的 EOS，并把
「共存密度 vs 参考表」做成硬门（<3%）。

### 8.4 honest notes（未做 / 做不到）

- **未构建、未运行 C++**：仓内无 OpenLB 依赖，且需改 OpenLB 本体 + 装 GSL；判为不经济。
  因此本报告的 C++ 结论全部是 **source-confirmed**（读码 + needle 定位），
  数值结论来自**本仓自带 Python 模块的实跑**（runtime-confirmed）。性质已在各条标注。
- **活跃度元数据**：未取 GitHub stars/commits API（不必要的限流风险）；活跃度按树内容 +
  commit 时间判，结论已在第一节写明。
- **未评估 MPI 正确性**：扩展自身无通信代码，跨块耦合正确性依赖 OpenLB 同步次序，
  没有 OpenLB 就无法验证（WITHHELD）。
- 派发单给的三个映射类中，「性能类=GPU 融合 kernel/分布式/稀疏多孔」在本仓**全为缺失或
  仅间接继承**（第五节），故这三条只能给 `reference`/`adapt`，不能给 `fits`。

---

## 九、方法与可复现性（机器块自证）

- 源码获取：`git clone --depth 1 https://github.com/JuliusWeinmiller/ThermalMultiphaseOpenLB`
  成功（1.9 MB，118 文件），解包在 `/tmp`，**未** clone 进 TensorPlatform 仓；
  commit `769efd471ac903dd8c70feb0369d2fc810f5561a`。
- 指针：370 条 needle，全部解析成功（缺失或歧义即构建失败）；
  报告里不存在手抄行号。
- 数字：`python tools/oss_inventory/tmo_eos_probe.py --src <tree> --out
  artifacts/oss_inventory/tmo_eos_probe.json` 运行**本仓** `EquationOfStatePython/eos.py`
  （依赖 numpy/scipy，只读导入，未拷贝进我们仓）。
- 图：`artifacts/oss_inventory/tmo_inventory.png`（4 面板：体量分布 / Maxwell 共存密度与密度比 /
  等面积残差自洽性 / real-PR 与算例表值对比），由同一 probe 数据绘制。
- 复现测试：`pytest tests/test_oss_inventory_tmo.py`（结构 + 指针 + 数字一致性）。

---

## 十、机器块

```text
[OSS-INV]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
commit: 769efd471ac903dd8c70feb0369d2fc810f5561a (2026-09-12)
lang: C++ (OpenLB template extension; no Fortran; Python only for EOS/Maxwell + plots)
license: NO LICENSE file; new sources carry only an 'OpenLB Extension ... for his thesis' comment (no licence text), build files inherited from OpenLB are GPL-2.0-or-later -> derivative of OpenLB with no explicit grant for the extension (research-only: do not copy code)
dep: needs an OpenLB checkout at ../.. (ROOT:=../../..), -lgsl -lgslcblas, and a manual patch of OpenLB src/dynamics/dynamics.h (BounceBack private->protected)
methods: lattice=D2Q9/D3Q19 (flow) + D2Q5/D3Q7 (temperature) | flow collision=BGK with Li/Luo/Li sigma-corrected velocity shift u+=sigma(F-F_ext)rho/psi^2 on top of Guo forcing | temperature DDF=first-order equilibrium + phi/dphi/d(Tu) source corrections (PRE 96,063303) | temperature MRT available (flow MRT NOT available) | semi-hybrid variant replaces LBM diffusion by explicit FD with k(rho)
multiphase: SINGLE-component Shan-Chen pseudopotential; NO MCMP, NO colour gradient, NO free surface, NO phase field; high density ratio comes from a temperature-dependent EOS (Peng-Robinson / Carnahan-Starling, psi=sqrt(6(p-rho/3)/G)) whose coexistence densities are fixed by a Maxwell equal-area construction (C++ GSL; python scipy re-implementation); latent heat enters through (dp/dT)_rho in the temperature equation
boundary: BounceBack with variable wall density (wetting = rho_wall, SC fluid-solid coupling), BounceBackVariableRho subclass, OpenLB advection-diffusion boundary conditions for the temperature lattice, setPeriodicity for periodic boxes; no contact-angle input, no free-slip, no moving wall, no IBM
gpu: NONE (no CUDA/HIP/OpenMP/SYCL in the extension); parallel = inherited OpenLB MPI cuboid decomposition + HeuristicLoadBalancer only
verify: no unit tests, no CI, no regression baselines; 12 example directories with hard-coded parameters and 6 matplotlib scripts that embed the results as literal arrays; the thermodynamic-consistency script queries sigma folders the sweep never produces and compares every case against a theory curve computed with a fixed a=1/49
summary: Master-thesis OpenLB extension coupling a sigma-corrected Shan-Chen pseudopotential to a temperature DDF with a compressibility/latent-heat source (boiling/evaporation demos). Absorb: EOS->Maxwell->coexistence-density closure, the semi-hybrid FD diffusion for k(rho), and the sigma-corrected force scheme. Do not absorb: its verification surface and hard-coded calibrations.
[/OSS-INV]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: GAP-LBM-MCMP-POISEUILLE
tag: sigma-corrected-shan-chen-force
method: 力格式用 Li/Luo/Li(PRE 86, 016709) 的 σ 修正：速度移位 = F/2 + σ(F−F_ext)ρ/ψ²，σ∈[0.3,0.35] 由参数扫描选定；单组分伪势，无第二组分、无 recoloring。两组分分层 Poiseuille 不可直接复用，可用的是「力格式 + ψ 场缓存」的落位方式。
files: `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:35`, `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:83`, `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:86`, `phaseChangeExtension/phaseChangeSource/modifiedGuoBGKDynamics.hh:74`, `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:143`, `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:161`
conventions: G=-1; σ=0.3~0.35; ψ 存在 descriptor 字段 PSI_PSEUDO_RHO，每步由交互势重算
transfer: reference
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: MCMP 高密度比
tag: maxwell-consistent-eos-coexistence
method: 密度比不来自互作用势强度，而来自「温度相关状态方程 + Maxwell 等面积构造」定出共存密度对：ψ=sqrt(6(p−ρ/3)/G)，p 由 Peng-Robinson/Carnahan-Starling 给出并随 T/Tc 变；潜热通过 (∂p/∂T)_ρ 进入温度方程而不是显式 L_v。可用的是这套闭环 + 我们的密度比验收。
files: `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:104`, `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:93`, `phaseChangeExtension/phaseChangeSource/interactionPotentialExtended.hh:116`, `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:150`, `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:282`, `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:386`, `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:388`, `phaseChangeExtension/EquationOfStatePython/eos.py:308`, `phaseChangeExtension/EquationOfStatePython/eos.py:343`
conventions: Tr=T/Tc=0.6~0.85; 格点 PR 用 a=2/49,b=2/21,G=-1; 共存密度由 Maxwell 构造给出
transfer: adapt
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: 自由面（free surface）
tag: absent-single-component-pseudopotential
method: **没有**自由面模型：气相是被伪势方程描述的稀疏相（ρ_v>0），不是空单元；无 ε=mass/ρ 记账、无 cell_type 位标志。界面靠密度梯度的弥散界面表达。
files: `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:106`, `phaseChangeExtension/phaseChangeExamples/poolBoiling2D/poolBoiling2D.cpp:81`, `phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:19`
conventions: 界面宽度由初始化噪声/界面宽度参数控制（如 liquidInterfaceWidth=8Δx）
transfer: reference
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: 相场 Allen-Cahn
tag: absent-no-phase-field
method: **没有**相场：单套分布函数 + 温度方程，无 φ 序参量、无 Cahn-Hilliard/Allen-Cahn、无接触角几何项。
files: `phaseChangeExtension/phaseChangeSource/pseudopotentialAdvectionDiffusionDynamics.h:30`, `phaseChangeExtension/phaseChangeSource/descriptorFieldExtended.h:20`, `phaseChangeExtension/phaseChangeSource/phaseChangeExtension.h:26`
conventions: 温度场亦用 DDF（D2Q5/D3Q7 加 PHI_THERMAL/PREV_T_V/PREV_PHI 三个附加字段）
transfer: reference
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: GPU 融合 kernel
tag: absent-cpu-templates-only
method: **无 GPU**：全仓是 OpenLB CPU 模板核（逐格 post-processor 双循环），无 CUDA/HIP/OpenMP 标注；fusion 机会在 OpenLB 侧而不在本扩展。对我们只有「热-流耦合的两趟循环可融合」这个结构提示。
files: `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:79`, `phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor2D.hh:50`, `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:180`
conventions: 耦合 post-processor 每次先开 (nx+2)(ny+2) 缓存场（rho/psi/u/T），再遍历内点
transfer: reference
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: 分布式多卡
tag: mpi-inherited-from-openlb
method: 并行完全继承 OpenLB：cuboid 几何按 MPI 进程数切分 + HeuristicLoadBalancer；扩展自身不做 halo 交换（psi/温度场只在单块内缓存，跨块靠 OpenLB 的 SuperLattice 同步）。
files: `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:356`, `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:364`, `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:361`
conventions: noOfCuboids = mpi size; setPeriodicity(true,true)
transfer: reference
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: 稀疏/多孔
tag: absent-but-wall-density-wetting
method: 无多孔介质模型；但润湿性通过「壁面密度 ρ_wall」实现（伪势的流-固作用强度），壁面用 BounceBack(可变密度) —— 「固体=高 ρ_wall 壁」是搭多孔/绕流验证面的现成范式。
files: `phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:11`, `phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:161`, `phaseChangeExtension/phaseChangeExamples/wallWettability2D/wallWettability2D.cpp:399`, `phaseChangeExtension/phaseChangeSource/miscFeatures.h:90`, `phaseChangeExtension/phaseChangeSource/miscFeatures.h:94`, `phaseChangeExtension/phaseChangeExamples/poolBoiling2D/poolBoiling2D.cpp:79`
conventions: ρ_wall 越大越亲液；pairBoiling 用 ρ_wall=5.0，wallWettability 扫 ρ_wall 看接触角
transfer: adapt
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: 案例组织
tag: per-example-makefile-triplet
method: 每个算例一个目录，含 Makefile + definitions.mk（ROOT/SRC/OUTPUT 三个变量）+ module.mk；参数全部硬编码在 .cpp 顶部常量区，无输入文件、无 CLI 参数。
files: `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/definitions.mk:28`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/definitions.mk:29`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/module.mk:29`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/Makefile:95`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:290`, `phaseChangeExtension/phaseChangeExamples/d2law2D/d2law2D.cpp:338`
conventions: ROOT 指回 OpenLB 根；输出目录 ./results*/ + gnuplotData/data/*.dat
transfer: adapt
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: 测试与验证面
tag: parameter-sweep-plus-manual-pyplot
method: **无单元测试、无 CI**。验证面 = 6 个物理算例 + 4 个 matplotlib 脚本，脚本里直接写死仿真结果数组、理论曲线用固定 a=1/49；扫描参数与脚本查询档位不一致（见第六节）。
files: `phaseChangeExtension/phaseChangeExamples/laplace2D/resultsEvalLaplacePressure.py:15`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:27`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:26`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:23`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:21`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:25`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/resultsEvalThermoConsist2D.py:47`, `phaseChangeExtension/phaseChangeExamples/laplace2D/resultsEvalLaplacePressure.py:28`
conventions: 判据为人眼读图；无断言、无容差、无回归基线
transfer: reference
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: CI
tag: none
method: **无 CI**：无 .github/、无测试目录、无 lint；`.tmpfile` 依赖文件被误提交（含作者本机绝对路径）。
files: `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/.tmpfile:1`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/Makefile:45`
conventions: 构建靠手工 make；GSL 需手工加到 global.mk
transfer: reference
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: NEW: 半隐式热求解器（LBM 扩散替换为显式 FD）
tag: semi-hybrid-thermal-solver
method: 把温度方程的 LBM 扩散项整体扣掉（−k·∇²T，k 由 τ_T 反算），再显式加回 ∇·(k(ρ)∇T)：k(ρ) 线性插值 + dk/dρ∇ρ·∇T 链式项。这样热扩散率可与 ν 解耦、且可随密度变 —— 对我们「变物性两相换热」是一条低成本可复用路径（代价：显式步长受扩散 CFL 限制）。
files: `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:48`, `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:49`, `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:180`, `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:197`, `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:217`, `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:220`, `phaseChangeExtension/phaseChangeSource/combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh:221`, `phaseChangeExtension/phaseChangeSource/fluidProperties.hh:76`, `phaseChangeExtension/phaseChangeSource/fluidProperties.hh:81`
conventions: constant_k=(1/τ_T−0.5)/invCs2(D2Q5); T 的 DDF 用一阶平衡态
transfer: adapt
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: NEW: Maxwell 等面积构造（共存密度标定器）
tag: maxwell-equal-area-gsl
method: C++ 侧用 GSL：Brent 求根 + QAGS 自适应积分，求 p_sat 使 ∫(p−p_sat)dV=0，输出 ρ 液/气；Python 侧独立实现同一构造（scipy），可直接当交叉校验器。我们做高密度比 MCMP 时「给定 T 求共存密度对」正缺这一块。
files: `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:150`, `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:88`, `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:140`, `phaseChangeExtension/phaseChangeSource/maxwellConstruction.hh:282`, `phaseChangeExtension/phaseChangeSource/maxwellConstruction.h:45`, `phaseChangeExtension/EquationOfStatePython/eos.py:128`, `phaseChangeExtension/EquationOfStatePython/eos.py:288`
conventions: 以体积 V=1/ρ 积分；accuracy 1e-6、maxIter 200、积分工作区 1000
transfer: adapt
[/OSS-SOL]
```

```text
[OSS-SOL]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
slug: NEW:  thermodynamic-consistency 扫描面（Tr×σ×a）
tag: parametric-consistency-sweep
method: 把「仿真共存密度 vs Maxwell 构造解析值」做成三点扫描（Tr 0.60~0.85 × σ 0.33~0.35 × a 0.5/49~2/49），输出 gnuplotData/data/density.dat 供后处理。可作为我们 MCMP 密度比验收的扫描模板。
files: `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:13`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:285`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:286`, `phaseChangeExtension/phaseChangeExamples/thermodynamicConsistency2D/thermodynamicConsistency2D.cpp:287`
conventions: 目录键 a_/sigma_/tr_ 三级（setprecision(2)）；每算例只落 density.dat 末行做判读
transfer: adapt
[/OSS-SOL]
```
