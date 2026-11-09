"""Markdown template for the ThermalMultiphaseOpenLB OSS inventory report.

Placeholders are substituted by :mod:`tmo_inventory`:

* ``[[pt:key_a|key_b]]`` -> resolved ``path:line`` pointers (joined with " · ")
* ``[[v:name]]``         -> a value from the generator's ``values`` mapping
                            (numbers, tables, progress notes ... all computed)
* ``[[sol_blocks]]``     -> the generated ``[OSS-SOL]`` machine blocks
* ``[[inv_block]]``      -> the generated ``[OSS-INV]`` machine block

Nothing in this file contains a line number or a measured number: those must
come through a placeholder so that ``tests/test_oss_inventory_tmo.py`` can
re-render the document and detect any hand edit.
"""

from __future__ import annotations

TEMPLATE = r"""# 【盘点】ThermalMultiphaseOpenLB（JuliusWeinmiller/ThermalMultiphaseOpenLB）

> OSS-INVENTORY 波2 · scenario_key=`oss-inventory` · repo `JuliusWeinmiller/ThermalMultiphaseOpenLB`
> · commit `[[v:commit_short]]`（[[v:commit_date]]，单 commit 单作者）。
> 派发单标签：OpenLB 的热-多相扩展（相变/热毛细方向），"2026 活跃"。
> **纪律**：本文每个 `文件:行` 由 `tools/oss_inventory/tmo_evidence.py` 的 needle 现场解析
> （`tools/oss_inventory/tmo_inventory.py` 渲染；指针缺失**或歧义**即构建失败）；每个数字由
> `tools/oss_inventory/tmo_eos_probe.py` **运行本仓自带的 Python EOS 模块**得到，零手抄。

---

## 一、概况

- **是什么**：OpenLB 的**外挂扩展**（不是独立求解器），加「单组分热-多相相变」：
  伪势 Shan-Chen 多相 + 温度 DDF + 相变源项，面向池沸腾/液滴蒸发。
  [[pt:readme_intro]]；作者自述为硕士论文产物（TU Delft 论文库）[[pt:readme_thesis|readme_thesis_link]]。
- **体量**（脚本统计 → `artifacts/oss_inventory/tmo_stats.json`）：被跟踪文件 [[v:total_files]] 个；
  扩展代码合计 **[[v:total_loc]] 行**（头文件 [[v:header_files]] 个 / [[v:header_loc]] 行；
  算例 .cpp [[v:example_cpp_files]] 个 / [[v:example_cpp_loc]] 行；
  Python [[v:python_files]] 个 / [[v:python_loc]] 行）。

[[v:groups_table]]

- **依赖与构建**：须把 `phaseChangeExtension/` 放进 OpenLB 根目录（[[pt:readme_install_drop]]）、
  `global.mk` 加 `-lgsl -lgslcblas`（[[pt:readme_install_gsl]]），并**手工改 OpenLB 本体**把
  `BounceBack` 的 `private` 改成 `protected`（[[pt:readme_install_patch]]）；算例用
  `ROOT := ../../..` 指回 OpenLB（[[pt:case_root]]）。→ 它是**需要打补丁的源码级扩展**，
  不是可独立编译的库。
- **版本现实**：README 说目标 OpenLB v1.3-1（[[pt:readme_limit_version]]）且「与 v1.4 不兼容」
  （[[pt:readme_no14]]）；但源码用到 `olb::instances::getBulkMomenta`、`descriptors::tag::MRT`、
  `Cell::defineField` 等 1.4/1.5 期 API（[[pt:ex_d2law_mpi]]、[[pt:tcmrt_tpartner]]）。
  **我们未构建、未运行 C++**（仓内无 OpenLB 依赖），故版本可用性记 **WITHHELD**，不臆造。
- **许可（决定能不能抄代码）**：全仓 **无 LICENSE 文件**（`license_files` = [[v:license_files]]）；
  新增源文件只有「OpenLB Extension ... for his thesis」注释、**无任何许可证文本**；
  Makefile/module.mk 系继承 OpenLB 模板的 **GPL-2.0-or-later** 声明
  （[[v:gpl_header_files]] 个 .h/.mk 带 GPL 段落，例 [[pt:case_makefile_libs]]）。
  → 判定：**OpenLB 的 GPL 派生物**，扩展部分无独立授权。对我们（TensorLBM 非 GPL）：
  **不可复制代码**，只可读思想后自研。这与波1 的 ludwig（BSD-3）处置不同。
- **活跃度**：单 commit `[[v:commit_short]]`（[[v:commit_date]]），无分支/PR/issue 交互；代码里
  留下 `// From python file: 0.3074`（[[pt:eos_rhoc_zc_comment]]）这类「论文落地稿」痕迹。
  按纪律修正派发单先验：**近期有提交，但无工程活动证据 → 学术参考实现，非活跃工程仓。**
- **论文谱系**（作者自列）：[[pt:readme_paper_forcing]] · [[pt:readme_paper_gong]] ·
  [[pt:readme_paper_li2017]] · [[pt:readme_paper_li2013]] · [[pt:readme_boiling_paper]]
  → Li/Luo/Li PRE 86,016709（力格式）；Gong & Cheng IJHMT 2012（相变 LBM）；
  Li/Zhou/Yan PRE 96,063303（改进热 LBM）；Li/Luo/Li PRE 87,053301（大密度比伪势）。

---

## 二、方法谱系

| 维度 | 本仓做法 | 指针 |
|---|---|---|
| 格子 | 流场 D2Q9/D3Q19（带 FORCE/EXTERNAL_FORCE/PSI_PSEUDO_RHO 字段）；温度 D2Q5/D3Q7 | [[pt:ex_laplace_descriptor]] |
| 附加字段 | `PSI_PSEUDO_RHO`（ψ 缓存）、`PHI_THERMAL`（温度源项）、`PREV_T_V`、`PREV_PHI`（时间导数缓存） | [[pt:field_psi]] · [[pt:field_phi]] · [[pt:field_prevtv]] · [[pt:field_prevphi]] |
| 流场碰撞 | **BGK** + Guo 力 + **Li/Luo σ 修正**；σ 被作者称为「热力学一致性」调参 | [[pt:ns_class]] · [[pt:ns_sigma_doc]] · [[pt:ns_bgk]] |
| 力格式 | `_prefactor=σ/(1/ω−0.5)`；先 `u+=F/2`，再 `u+=σ(F−F_ext)ρ/ψ²`，最后 `addExternalForce` | [[pt:ns_prefactor]] · [[pt:ns_half_force_shift]] · [[pt:ns_sigma_shift]] · [[pt:ns_guo_force]] |
| 温度碰撞 | 独立 DDF：一阶平衡态 + φ 源项 + 0.5·∂_tφ + c·∂_t(Tu)（PRE 96,063303） | [[pt:ad_class]] · [[pt:ad_first_order_eq]] · [[pt:ad_phi_source]] · [[pt:ad_dphi_source]] · [[pt:ad_dtv_source]] · [[pt:ad_paper_correction]] |
| 温度 MRT | 仅温度场有 MRT：`invM_S` 由 `descriptors::s` 生成，源项走 `invM(1−S/2)` | [[pt:ad_mrt_class]] · [[pt:ad_mrt_collide]] · [[pt:ad_mrt_correction]] |
| 状态方程 | 抽象基类四钩子：`ψ(ρ)`、`ψ(ρ,T)`、`(∂p/∂T)_ρ`、`p(V)`；实现 **PR** 与 **CS** | [[pt:eos_psi_virtual]] · [[pt:eos_psi_T_virtual]] · [[pt:eos_dpdt_virtual]] · [[pt:eos_pr_ctor]] · [[pt:eos_pr_pressure_T]] · [[pt:eos_cs_psi_T]] |
| ψ 定义 | `ψ = sqrt(6(p−ρ/3)/G)` | [[pt:eos_psi_sqrt]] |
| 共存密度 | **Maxwell 等面积**：GSL Brent 求根 + QAGS 自适应积分，令 ∫(p−p_sat)dV=0；精度 1e-6、迭代 200 | [[pt:mw_operator]] · [[pt:mw_root_brent]] · [[pt:mw_qags]] · [[pt:mw_return_densities]] · [[pt:mw_accuracy]] |
| 热物性 | `FluidProperties`：按 ρ 归一化后在汽/液值线性插值（c_v、k），温度比线性映射 | [[pt:fp_interp]] · [[pt:fp_clamp]] · [[pt:fp_temp_ratio]] |
| 热-流耦合 | 单个组合 post-processor 一次算完：ψ 场缓存 → 伪势+浮力体力 → 温度源项 φ → 速度耦合 | [[pt:tc_psi_temp]] · [[pt:tc_sc_kernel]] · [[pt:tc_force_assembled]] · [[pt:tc_phi_store]] · [[pt:tc_velocity_coupling]] |
| 浮力 | 非 Boussinesq：`(1−ρ̄/ρ)·g` | [[pt:tc_buoyancy]] |
| **没有** | 流场 MRT/KBC/cumulant、IBM、可微/代码生成、GPU、MCMP、自由面、相场 | 见第四、五节与 stats.json |

`InteractionPotentialExtended` 的四虚函数是**真正的扩展点**（换 EOS 不动求解器）：
[[pt:eos_param_struct]] · [[pt:eos_pressure_hook]]。这是做「EOS→共存密度→伪势」链路时
最值得照抄的**接口形态**（代码本身因 GPL 不能抄，见第一节）。

---

## 三、边界 / 壁面 / 润湿

- 壁面 = 标准 `BounceBack`；**润湿性靠壁面密度 ρ_wall**（Shan-Chen 流-固作用的等效实现）：
  [[pt:ex_ww_head]] · [[pt:ex_ww_wall_density]] · [[pt:ex_pool_wall_rho]]。
- OpenLB 的 `BounceBack::_rho` 是 private，作者写了子类 `BounceBackVariableRho` 在运行期改 ρ_wall，
  并需按 README 打补丁：[[pt:bb_variable_rho_class]] · [[pt:bb_variable_rho_doc]] ·
  [[pt:bb_variable_rho_setter]] · [[pt:ex_ww_bb_rho]]。
- 温度边界：用 OpenLB 的 AD 边界条件族（Dirichlet/Neumann）：
  [[pt:ex_d2law_thermal_bc_decl]] · [[pt:ex_d2law_thermal_bc]]。
- 周期域：`cuboidGeometry.setPeriodicity(true,true)`（液滴/Laplace 消壁面影响）：
  [[pt:ex_d2law_periodic]]。
- **没有**：几何接触角输入（只能给 ρ_wall 标量，不能直接指定 θ）、自由滑移/部分滑移、移动壁、
  IBM。→「指定接触角」必须自建（壁面标量无法表达 θ）。

---

## 四、多相 / 相变（本仓的心脏）

### 4.1 类别先钉死

**单组分伪势（SCMP）+ 温度相关 EOS + 相变源项。**

- **不是 MCMP**：只有一套流场分布函数与一组 `PSI_PSEUDO_RHO`（[[pt:field_psi]]）；
  无第二组分 ψ、无 G_αβ 互作用矩阵、无 recoloring。
- **不是自由面**：气相是被 EOS 描述的稀疏相（ρ_v ≫ 0），界面是密度梯度的弥散界面；
  无空单元/`ε=mass/ρ` 记账。
- **不是相场**：无 φ 序参量、无 Cahn-Hilliard/Allen-Cahn；`PHI_THERMAL` 是温度方程的**源项**
  而不是序参量（[[pt:field_phi]] · [[pt:ad_phi_source]]）。

### 4.2 高密度比从哪来（最值得吸收的部分）

1. PR-EOS：`Tc=0.0778/0.45724·a/b/R`、`ρc=pc/0.307/R/Tc`，温度只进 `α(T)`
   —— [[pt:eos_tc_from_ab]] · [[pt:eos_rhoc_zc307]] · [[pt:eos_pr_pressure_T]]；
   `(∂p/∂T)_ρ` 有解析表达式（[[pt:eos_pr_dpdt]]）。
2. 用 Maxwell 等面积构造求该 T 下的共存密度对（[[pt:mw_operator]] · [[pt:mw_return_densities]]）。
3. 算例把这对密度交给 `FluidProperties` 与初始化
   （[[pt:ex_d2law_maxwell_call]] · [[pt:ex_d2law_lattice_liq]] · [[pt:ex_d2law_fluid_props]]）。
4. 潜热**不显式给 L_v**，而是经 `(∂p/∂T)_ρ` 进温度方程：
   `φ = T∇·u − (T/Tc)/(ρc_v)·(∂p/∂T)_ρ·∇·u` —— 压缩功与相变潜热合并成同一个 ∇·u 源项
   （[[pt:tc_nabla_u]] · [[pt:tc_dpdt]] · [[pt:tc_phi_compress]] · [[pt:tc_phi_latent]]）。

**我们实测**（`tmo_eos_probe.py` 调用本仓 `eos.py`；格点 PR：G=−1, a=2/49, b=2/21, ω=0.3443）：

| Tr=T/Tc | ρ_l（格点） | ρ_v（格点） | 密度比 | p_sat（格点） | Maxwell 等面积残差（相对） |
|---:|---:|---:|---:|---:|---:|
[[v:coexist_rows]]

- [[v:convergence_note]] → 可达密度比区间 = **[[v:ratio_range]]**。README 所称「高密度比」
  （[[pt:readme_high_ratio]]）本质是**低 Tr 下 EOS 的共存行为**；σ 修正的作用是让这个密度比下
  **不发散**，而不是把密度比「调大」。
- Maxwell 构造自身自洽：等面积残差 ≤ [[v:maxwell_residual_max]]（相对）——C++ GSL 与 Python scipy
  两套实现都在机器精度闭合。
- 跨实现细节：C++ 用 Zc=0.307、Python 用 0.3074（[[pt:eos_rhoc_zc307]] · [[pt:py_rhoc_zc3074]]）；
  实测把 ρc 换回 0.307 重算，共存密度**逐位不变**（ρ_l 相对差 [[v:zc_liq_diff]]，
  ρ_v [[v:zc_vap_diff]]）——ρc 只当求根初值/区间。**该「不一致」被数值否定，不作缺陷记。**

### 4.3 稳定化手段（可复用清单）

- **σ 修正力格式**：`u += σ(F−F_ext)ρ/ψ²`，σ≈0.33（[[pt:ex_pool_sigma]]）。
  这是伪势在 10³ 密度比下不崩的直接原因。
- **两遍缓存**：先把 ρ/ψ/u/T 摊到 (nx+2)(ny+2) 缓存，再遍历内点算力与源项
  （[[pt:tc_envelope]]），避免「边算边改」的雅可比污染。
- **半隐式热求解器（semi-hybrid）**：把 LBM 自带扩散项扣掉（`−constant_k∇²T`，
  `constant_k=(1/τ_T−0.5)/invCs2`），再显式加回 `∇·(k(ρ)∇T)` 与链式项 `dk/dρ·∇ρ·∇T`：
  [[pt:sh_constant_k]] · [[pt:sh_remove_lbm_diffusion]] · [[pt:sh_add_fd_diffusion]] ·
  [[pt:sh_chain_rule]] · [[pt:sh_comment_phase_change]]。
  目的：热扩散率与 ν 解耦、允许 k(ρ) 变物性；代价：显式 FD 受扩散 CFL 约束（Δt 变小）。

### 4.4 相变算例（是验证面，不是库）

| 算例 | 干什么 | 判据 | 指针 |
|---|---|---|---|
| `laplace2D/3D` | 静止液滴扫半径 | Laplace 律 Δp=σ/R 线性度 + 反算 σ | [[pt:ex_laplace_head]] · [[pt:ex_laplace_sweep]] |
| `d2law2D/3D` | 加热液滴蒸发 | **D² 律**（直径平方线性衰减） | [[pt:ex_d2law_head]] |
| `thermodynamicConsistency2D/3D` | Tr×σ×a 扫描 | 仿真共存密度 vs Maxwell 解析值 | [[pt:ex_tc_head]] · [[pt:ex_tc_tr_sweep]] |
| `poolBoiling2D` | 池沸腾起泡 | 视觉/经验（ρ_wall=5、g×10⁴、噪声 5%） | [[pt:ex_pool_head]] · [[pt:ex_pool_gravity_factor]] · [[pt:ex_pool_noise]] |
| `wallWettability2D/3D` | ρ_wall → 接触角 | 视觉（图像量角） | [[pt:ex_ww_head]] |
| `phaseSeparation3D` / `evaporatingDroplet2D` / `maxwellConstruction` | 旋节分解 / 蒸发起步 / 纯 Maxwell 出表 | — | [[pt:ex_phase_sep_head]] · [[pt:ex_evap_head]] · [[pt:ex_maxwell_head]] · [[pt:ex_maxwell_writes]] |

---

## 五、GPU / 并行 / 测量

- **无 GPU**：扩展全仓 `CUDA / __device__ / OpenMP / #pragma omp / HIP / SYCL` 命中 =
  [[v:gpu_hits]]（脚本统计）。热-流耦合是逐格双循环的 CPU post-processor（[[pt:tc_envelope]]）。
  对我们的「GPU 融合 kernel」：**只借结构边界**（ψ / ∇ρ / ∇T / 力 / 源项在同一次遍历里算完，
  见 [[pt:sh_laplacian]] · [[pt:sh_grad_rho]]），不借代码。
- **并行 = OpenLB 的 MPI 域分解**：`noOfCuboids = mpi().getSize()`（[[pt:ex_d2law_mpi]]）+
  `HeuristicLoadBalancer`（[[pt:ex_d2law_balancer]]）；缓存场是单块内 `BlockData2D`，
  跨块一致性依赖 OpenLB SuperLattice 的同步次序。测得 `singleton::mpi()` 调用点
  [[v:mpi_sites]] 处（全部在算例里，扩展自身无通信代码）。
- **测量/归约**：作者补了三个归约 functor（在 indicator 子集上求和/平均并回传体素数）：
  [[pt:red_sum_loop]] · [[pt:red_voxel_counter]] · [[pt:red_average_class]] ·
  [[pt:red_super_average]] → 这正是「液滴质量/直径/汽化率」这类标量验收量该落的位置
  （d2law 需要它却没接上，见 6.2）。
- 输出：VTM/VTK（[[pt:ex_d2law_vtk]]）+ heatmap（[[pt:ex_d2law_heatmap]]）+ gnuplotData；
  没有内置的标量时间序列写盘（除参数表 [[pt:fp_write_table]]）。

---

## 六、验证面与可复现性（本仓最弱一环，也是我们该反着做的地方）

### 6.1 现有验证面

- **0 个单元测试**（被跟踪文件里 `test/tests/unittest` 命中 [[v:test_like_count]] 个）、
  **0 个 CI**（`.github` / `*.yml` 命中 [[v:ci_count]] 个）。
- 6 个 `resultsEval*.py`（[[v:eval_scripts]]）**把仿真结果数组写死**在脚本里再画图人眼看：
  [[pt:ex_laplace_eval_hardcoded]] · [[pt:ex_laplace_eval_sigma]]；还有硬编码时间门槛
  `if int(time) > 5000:`（[[pt:ex_tc_eval_time_gate]]）。→ 不可回归、不可跨版本比对。

### 6.2 扫描面 vs 评估脚本（脚本解析实测）

C++ 扫描（[[pt:ex_tc_tr_sweep]] · [[pt:ex_tc_sigma_sweep]] · [[pt:ex_tc_a_sweep]]；
目录名用 `std::setprecision(2)`：[[pt:ex_tc_folder_precision]]）对 Python 查询
（[[pt:ex_tc_eval_a_list]] · [[pt:ex_tc_eval_sigma_list]] · [[pt:ex_tc_eval_trs]]）：

- **σ**：C++ 产出 [[v:sigma_produced]]，脚本查询 [[v:sigma_consumed]]
  → **查了但永不存在的档位 [[v:sigma_phantom]]**（脚本静默跳过，图上「缺点」而不报错）。
- **a**（目录名是 eosA 本身）：C++ 产出 [[v:a_produced]]，脚本查询 [[v:a_consumed]]
  → **查了但永不存在的档位 [[v:a_phantom]]**；且理论曲线只用 a=1/49 算**一次**
  （[[pt:ex_tc_eval_pr_fixed_a]] · [[pt:ex_tc_eval_theory]]），故除 a=1/49 档外「Theory」线
  并非该档理论值（扫描范围 a = [[v:a_range]]）。
- **Tr**：C++ 只扫到 [[v:tr_swept_max]]，理论曲线 `linspace(0.6,0.92)` 画到
  [[v:tr_theory_max]]（`tr_theory_exceeds_sweep` = [[v:tr_exceeds]]）。

以上三条是**可程序化复现的验证面缺陷**（不是主观评价），也解释了为什么本仓的
「热力学一致性」结论只能当**定性**证据。

### 6.3 标定不自洽（实测数值后果）

`d2law2D` 里两套标定互不相容：物性用真实水表值 ρ_l = 947.13、ρ_v = 0.9643 kg/m³
（[[pt:ex_d2law_rho_liq]] · [[pt:ex_d2law_rho_vap]]），T_c = 647 K、Tr = 0.6
（[[pt:ex_d2law_tc]]）；但单位换算用**硬编码**格点液密度 8.72518
（[[pt:ex_d2law_hardcoded_lattice]]），而该值本应由同函数内的 Maxwell 构造算出
（[[pt:ex_d2law_lattice_liq]]）。实测硬编码 vs Maxwell 相对差 [[v:hardcode_diff]]
（Tr=0.6 时无害，但换 Tr/EOS 就静默错，而 `thermodynamicConsistency` 正是改 Tr 的算例）。

数值后果：格点 ρ_v = [[v:lattice_vapour]] × 换算因子 [[v:density_factor]] =
**[[v:implied_vapour]] kg/m³**，而算例自述物理汽密度 [[v:stated_vapour]] → 相对差
**[[v:calibration_gap]]**。即 EOS 标定与物性表标定互不自洽，量级 15%，
正卡在我们「>15% 不可接受」的门槛上（见 8.3）。相应密度比：自述物理比
[[v:stated_ratio]] vs 格点 Maxwell 比 [[v:lattice_ratio]] —— 引用时必须自己算，不能抄论文口径。

### 6.4 运行时缺陷（我们复现的）

- **Carnahan-Starling 分支不可用**：`CarnahanStarlingInteractionPotential` 未定义 `rhoc`
  （[[pt:py_cs_class]]），而基类 Maxwell 求根首行就用 `Vc = 1/self.rhoc`（[[pt:py_vc_from_rhoc]]），
  故 [[pt:py_cs_equilibrium]] 必然抛 `[[v:cs_error]]`。
  → C++ 侧 CS EOS（[[pt:eos_cs_psi_T]]）比 Python 侧可用性高，两边不同步。
- 仓库卫生：**[[v:tmpfile_count]] 个 `.tmpfile`**（构建期依赖文件，含作者本机绝对路径
  [[pt:case_tmpfile_absolute]]）被误提交。

---

## 七、与我们功能点的映射（feature_ledger）

| feature_ledger slug | transfer | 本仓对应 tag |
|---|---|---|
[[v:mapping_rows]]

- **`GAP-LBM-MCMP-POISEUILLE`（reference）**：无第二组分，做不了分层两组分 Poiseuille；
  可借的是 σ 修正力格式与 ψ 字段落位（[[pt:ns_sigma_shift]] · [[pt:tc_sc_kernel]]）；
  「界面位置/两相流量分配」这类验收本仓不提供。
- **`MCMP 高密度比`（adapt）**：**本仓核心价值**。吸收「EOS(T)→Maxwell 等面积→共存密度对→ψ 场」
  闭环（[[pt:mw_operator]] · [[pt:eos_pr_dpdt]] · [[pt:py_maxwell]]）与「潜热经 (∂p/∂T)_ρ 进温度
  方程」（[[pt:tc_phi_latent]]）；不吸收它的标定方式（6.3）与无断言验证面（6.1）。
- **`自由面`（reference）**：**无**。仅提供「弥散界面由 EOS 自然出现」的对照物
  （[[pt:tc_temp_from_rho]]）。
- **`相场 Allen-Cahn`（reference）**：**无**。唯一可借的是「温度场当第二套 DDF」的组织
  （[[pt:ad_class]]），与相场无关。
- **`GPU 融合 kernel`（reference）**：无 GPU 代码；借融合边界（[[pt:sh_laplacian]] ·
  [[pt:sh_grad_rho]]）。
- **`分布式多卡`（reference）**：只有 OpenLB MPI cuboid 的继承用法（[[pt:ex_d2law_mpi]] ·
  [[pt:ex_d2law_balancer]]）。
- **`稀疏/多孔`（adapt）**：无多孔模型；但 ρ_wall 润湿 + 可变密度反弹壁 = 「固体边界可调亲和力」
  的现成范式（[[pt:ex_ww_wall_density]] · [[pt:bb_variable_rho_setter]]）。
- **`案例组织`（adapt）**：Makefile + definitions.mk + module.mk 三件套 + 目录名即参数
  （[[pt:case_src]] · [[pt:case_module]] · [[pt:ex_tc_folder_precision]]），构建约定可直接抄
  （这部分是构建脚本约定，不涉 GPL 代码）。
- **`测试与验证面`（reference）**：**反面教材**（6.1 / 6.2）。
- **`CI`（reference）**：无（6.1；另有误提交的 `.tmpfile`，[[pt:case_tmpfile_absolute]]）。

---

## 八、Top3 吸收 + 明确不吸收 + 误差判定

### 8.1 Top3（按对齐缺口账本的价值排序）

1. **EOS → Maxwell → 共存密度对 的标定链（自研实现，不抄代码）**：补 `MCMP 高密度比` 的
   「给定 T 求 ρ_l/ρ_v」缺环；本仓 C++ / Python 双实现可互为交叉校验源。
   移植验收判据：等面积残差 < 1e-10、共存密度随 T 单调、临界点附近退化行为合理。
   证据 [[pt:mw_qags]] · [[pt:mw_return_densities]] · [[pt:py_maxwell]]。
2. **半隐式（semi-hybrid）变物性热求解器**：把热扩散率从 τ_T 解耦、允许 k(ρ)；实现成本低
   （一个显式 FD 项 + 一个链式项），适配我们变物性两相换热。
   证据 [[pt:sh_constant_k]] · [[pt:sh_remove_lbm_diffusion]] · [[pt:sh_add_fd_diffusion]] ·
   [[pt:sh_chain_rule]]；注意显式扩散 CFL 代价。
3. **σ 修正力格式 + 两遍缓存耦合组织**：高密度比不崩的直接原因，实现上只是在 Guo 移位上加一项
   （[[pt:ns_sigma_shift]] · [[pt:ns_prefactor]]）；两遍缓存（[[pt:tc_envelope]]）正是 GPU 融合
   kernel 的天然切分边界。

### 8.2 明确不吸收

- **代码本身**（GPL 派生 + 扩展无独立授权）：一行都不抄。
- **它的验证面**（写死结果数组、理论曲线不随参数、无断言、无 CI）：进我们的反向清单。
- **硬编码标定**（`8.72518`、`g×10⁴` 之类）：我们一律由 EOS/物性参数程序化推导并留 provenance。
- **两套 Maxwell 各自为政**（C++/Python 无交叉测试）：我们要求单一实现 + 交叉校验测试。

### 8.3 误差判定（关键量 vs 基准，逐项显式回答「是否 < 3%」）

[[v:error_table]]

**结论（不粉饰）**：本仓**内部自洽性极好**（Maxwell 残差 ~1e-12、ρc/Zc 不敏感），
但**外部精度不合格**：Peng-Robinson 对 Tr=0.6 水的液相密度偏低 [[v:pr_liquid_err]]
（PR 的已知缺陷），且示例内两套标定之间差 [[v:calibration_gap]] —— 均远超 3%，也超过
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
  commit `[[v:commit]]`。
- 指针：[[v:pointer_count]] 条 needle，全部解析成功（缺失或歧义即构建失败）；
  报告里不存在手抄行号。
- 数字：`python tools/oss_inventory/tmo_eos_probe.py --src <tree> --out
  artifacts/oss_inventory/tmo_eos_probe.json` 运行**本仓** `EquationOfStatePython/eos.py`
  （依赖 numpy/scipy，只读导入，未拷贝进我们仓）。
- 图：`artifacts/oss_inventory/tmo_inventory.png`（4 面板：体量分布 / Maxwell 共存密度与密度比 /
  等面积残差自洽性 / real-PR 与算例表值对比），由同一 probe 数据绘制。
- 复现测试：`pytest tests/test_oss_inventory_tmo.py`（结构 + 指针 + 数字一致性）。

---

## 十、机器块

[[v:inv_block]]

[[v:sol_blocks]]
"""