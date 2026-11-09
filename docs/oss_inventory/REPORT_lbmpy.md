# 【盘点】lbmpy（lssfau/lbmpy）· 开源 LBM 技术盘点单项目深盘

- **任务锚点**：FEAT-OSS-INVENTORY-LBM（tier 2） / mission 8291
- **盘点对象**：`https://github.com/lssfau/lbmpy`（上游主库为 FAU GitLab `i10git.cs.fau.de/pycodegen/lbmpy`，GitHub 为镜像）
- **代码对象**：HEAD `0afc9d52896e23210874b4d56d2d11f4f934e0e8`，2026-07-22，*Merge branch 'janirudh/stencil-refactoring' into 'master'*
- **取码方式**：`git clone --depth 1 https://github.com/lssfau/lbmpy.git`（成功，exit 0；克隆在 `/tmp/lbmpy_src`，未落入本仓工作树、未推任何分支）
- **证据性质**：**全部为静态源码证据（source-confirmed）**。本机与计算集群均无 `pystencils`（`pip install pystencils` → "No matching distribution found"，集群 `n315` 同缺，且无 PyPI 出口），因此**本报告没有任何运行验证**；凡未读到的内容一律标注未检查，不含「推断当结论」。
- **测量脚本**：`docs/oss_inventory/make_stats.py` → `docs/oss_inventory/lbmpy_stats.json`（计数、行号全部程序化采集，零手抄）

---

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 主语言 | Python（SymPy 符号推导 + `pystencils` 生成 C/CUDA kernel；本仓**不含**求解器运行时） | `README.md:1-30`、`src/lbmpy/methods/abstractlbmethod.py` |
| 规模 | `src/lbmpy` 105 个 `.py`，**23365 行**；测试 84 个 `.py` + 28 个 `.ipynb`；文档 notebook 23 个 | `lbmpy_stats.json`（`n_py_src` / `src_loc_total` / `n_test_files`） |
| 许可证 | **GPL-3.0-or-later** | `pyproject.toml:12` `license = "GPL-3.0-or-later"`；`LICENSE:1-2` "GNU GENERAL PUBLIC LICENSE Version 3" |
| 活跃度 | 活跃：2.0 基于 pystencils 2.0 重构，1.4 legacy 仅 `v1.x` 维护；`CHANGELOG.md:5-6` 已列 "Removed OpenCL support"（未发布）；GitLab CI 8 stage（lint / unit tests / nightly / coverage / docs / deploy） | `README.md:5-8`、`CHANGELOG.md:1-6`、`.gitlab-ci.yml:1-40` |
| 定位一句话 | **方法定义谱系最全的符号式 LBM 前端**：把「格子/平衡态/碰撞算子/力项/边界」抽象成 SymPy 对象，再交外部 `pystencils` 生成并编译 kernel；不自己推进时间、不做域分解、不含多相应用层 | 全库结构 + `updatekernels.py:13` |

### 许可证对我们的可用性判断（关键结论）

GPL-3.0-or-later 是**强 copyleft**，非宽松许可。三种用法分级：

1. **读码提炼算法/公式/参数标定关系** —— ✅ 允许（版权保护表达、不保护思想；重写实现不构成衍生作品）。
2. **复制/改写源码文件并入 TensorLBM 并对外分发** —— ❌ 不可接受：触发 GPL-3.0 传染，等于要求 TensorLBM 整体 GPL-3.0 开源，我们的许可自由度被永久让出。
3. **独立进程/服务调用（进程边界隔离）** —— ⚠️ 风险可控但受限；而 Python `import lbmpy` 属链接式结合，一般认定为衍生作品，**保守按污染处理**。

**执行口径**：本仓对我们 = **参考实现 + 公式/参数校准来源**；落地代码必须自己重写（第七/八节即「实现方式集」摘要）。少数数学常量（cs²=1/3、Guo 修正因子 1−ω/2、Bouzidi 插值式）本身不受版权保护，可直接使用。

---

## 二、方法谱系

### 2.1 格子（stencil）

`velocityspace/standardstencils.py` 共 6 个 `class`（1 抽象基类 + 5 具体）：

| 格子 | 位置 | 备注 |
|---|---|---|
| `StandardStencil`（基类） | `standardstencils.py:6` | 含 `theta`（cs² 可参数化）与 `ordering` |
| **D2Q9** | `standardstencils.py:53` | |
| **D3Q7** | `standardstencils.py:93` | 热/扩散用低阶 |
| **D3Q27** | `standardstencils.py:124` | |
| **D3Q19** | `standardstencils.py:178` | |
| **D3Q15** | `standardstencils.py:241` | |

补充：任意格子走 `velocityspace/customstencil.py`；ghost 层走 `velocityspace/ghstencils.py`；GPU 性能导向的 *Lehmann ordering*（源自 "High Performance Free Surface LBM on GPUs"）见 `fieldaccess.py:373 _get_lehmann_stencil`（支持 D2Q9/D3Q15/D3Q19/D3Q27）。
→ **无内置 D2Q7 / D3Q39**（可用 CustomStencil 自建）。

### 2.2 碰撞算子（谱系最全处）

全部经单一工厂文件 `methods/creationfunctions.py`（13 个 `create_*`，程序化枚举见 `lbmpy_stats.json.families.collision_factories`）：

| 谱系 | 入口 | 位置 |
|---|---|---|
| SRT / BGK | `create_srt` | `methods/creationfunctions.py:227` |
| TRT | `create_trt` | `:255` |
| TRT + magic number（Λ=3/16） | `create_trt_with_magic_number` | `:285` |
| MRT（原始矩） | `create_mrt_raw` | `:310` |
| 中心矩 | `create_central_moment` | `:339` |
| **KBC**（N4 动力学） | `create_trt_kbc` | `:387` |
| MRT 正交化 | `create_mrt_orthogonal` | `:466` |
| **累积量** | `create_cumulant` | `:532` |
| 累积量-单项式 / -多项式 | `create_with_monomial_cumulants` / `create_with_default_polynomial_cumulants` | `:567` / `:589` |
| 符号平衡态组合 | `create_with_discrete_maxwellian_equilibrium` / `_continuous_` / `create_from_equilibrium` | `:81` / `:121` / `:161` |
| 熵条件修正（entropic，牛顿迭代 ω） | `add_entropy_condition` / `add_iterative_entropy_condition` | `methods/momentbased/entropic.py:8` / `:90` |
| 四阶 / 伽利略修正 | `fourth_order_correction.py` / `galilean_correction.py` | `methods/cumulantbased/` |

**组织方式（本仓最值得学的架构）**：`AbstractLbMethod`（`methods/abstractlbmethod.py`）把碰撞形式化为「矩空间松弛」：变换矩阵 `M`/`N` 来自 `moment_transforms/{raw,central,cumulant}transforms.py`，松弛率矩阵 `R` 由用户字典给出（`methods/creationfunctions.py:602 _get_relaxation_info_dict`）。→ **碰撞谱系 = 变换空间 × 松弛率配置**，新增算子不必新增代码。

**RR（recursive regularization）无命名入口**：能力可由累积量/中心矩入口覆盖，但仓内无 `RR` 命名实现（`regularized` grep 无对应类）——按 NOT_SUPPORTED(命名层面) 记录。

### 2.3 力项

`forcemodels.py` 10 个 `class`（1 抽象 + **9 具体**）：

| 模型 | 位置 | 要点 |
|---|---|---|
| `AbstractForceModel` | `forcemodels.py:105` | 四钩子：`forcing_terms` / `moment_space_forcing` / `central_moment_space_forcing` / `equilibrium_velocity_shift` |
| `Simple` | `:179` | 直接加 |
| `CentralMoment` | `:213` | 中心矩空间加力 |
| `Luo` | `:235` | Luo 型（含二阶速度项） |
| **`Guo`** | `:273` | Guo 2002 推广到 MRT；SRT 时退化为 `(1−ω/2)·Luo`（`:283-289`）；平衡态速度与宏观速度均做 F/2 位移 |
| `He` | `:319` | He 型（shifted velocity） |
| `Schiller` | `:441` | 继承 `Guo`（热 LBM） |
| `Buick` | `:453` | Buick 型 |
| `EDM` | `:493` | Exact Differencing Model（lbm_book eq. 6.32） |
| **`ShanChen`** | `:521` | Silva 2020 的 SC 力项矩空间形式：`forcing_terms`（`:528`）给出线性项 `w_i(F_q + 3u_q F_q − u·F)` + 非线性项 `(w_i/2ρ)(3F_q² − F²)`，再由 `moment_space_forcing`（`:556`）按 `(1/cs²)(I−R/2)` 与平方因子投影；`central_moment_space_forcing`（`:568`）、`equilibrium_velocity_shift`（`:585`） |

默认速度位移：`default_velocity_shift = f/(2ρ)`（`:592`）、`default_momentum_density_shift = f/2`（`:596`）。
选型入口：`creationfunctions.py:466` 的 `ForceModel` 枚举 → 类映射。
→ 「分布空间 / 矩空间 / 中心矩空间」三种表示自动互转，是 9 力项 × 13 碰撞算子可任意组合的关键。

---

## 三、边界处理

`boundaries/boundaryconditions.py`，12 个具体类（已排除抽象基类 `LbBoundary:27`）：

| 类 | 位置 | 型式 |
|---|---|---|
| `NoSlip` | `:110` | 半程反弹 bounce-back |
| `NoSlipLinearBouzidi` | `:149` | **线性插值反弹**（Bouzidi），壁距 `q` 存 index vector；无第二层流体时自动退化为 `NoSlip`（`:151-157`） |
| `QuadraticBounceBack` | `:232` | **二阶**插值反弹，需给 `relaxation_rate` 重建碰撞前 PDF |
| `FreeSlip` | `:371` | 自由滑移（镜面） |
| `WallFunctionBounce` | `:500` | 壁函数（湍流壁面），配 `wall_function_models.py` |
| **`UBB`** | `:788` | **速度反弹 = Zou-He 速度型**：`f_ī(x_b,t+Δt) = f*_i(x_b,t) − 2 w_i ρ_w (c_i·u_w)/cs²`（`:790-794`）；支持 `adapt_velocity_to_force`、`calculate_force_on_boundary` |
| `SimpleExtrapolationOutflow` | `:927` | 出流（一阶外推） |
| `ExtrapolationOutflow` | `:977` | 出流（含速度外推） |
| `FixedDensity` | `:1127` | **Zou-He 密度型**（定压/密度入口） |
| `DiffusionDirichlet` | `:1187` | 标量场 Dirichlet（相场/浓度） |
| `NeumannByCopy` | `:1270` | 标量场 Neumann（复制） |
| `StreamInConstant` | `:1294` | 定值流入 |

边界同样走代码生成：`boundaryhandling.py:163 create_lattice_boltzmann_boundary_kernel`（`boundary_functor` 输出赋值，方向符号化 `dir/inv_dir`）；边界力用动量交换：`boundaryhandling.py:100 force_on_boundary`、`:108 _force_on_no_slip`、`:128 _force_on_boundary`（`stencil[dir] * values` 求和）。

**缺口（明写）**：

- **IBM（浸没边界）＝ 无**。全仓 `immersed|IBM` grep 命中 0。替代物是 **PSM 部分饱和格子**：`partially_saturated_cells.py:11 PSMConfig`（`fraction_field` / `object_velocity_field` / `max_particles_per_cell` / `individual_fraction_field` / `object_force_field`）、`:48 get_psm_solid_collision_term`、`:94 get_psm_force_from_solid_collision`、`:115 add_psm_solid_collision_to_collision_rule`、`:144 replace_by_psm_collision_rule`。定位 = **未解析颗粒 / 多孔介质**（固体体积分数 B 加权反弹 + 取力），无 Dirac 插值核、无 Lagrange 标记点，**不是 IBM**。
- **曲率修正 ＝ 无**。曲面精度靠「壁距 q + 线性/二阶插值」（`additional_data` 返回 `('q', ...)`，`:174`、`:259`），即几何插值路线；无 Mei/Luo 型曲率修正力项。

---

## 四、多相 / 多组元（重点）

**总结论**：本仓**有** SCMP/MCMP Shan-Chen（教程 + 逐点回归测试级，但不提供 ψ 势库，ψ 由用户自写）、**有**相场 Cahn-Hilliard（n 相，含高密度比自由能）与 Allen-Cahn（CSF 表面张力/热毛细力链）；**没有**自由面（fill-fraction / VOF）模型，**没有** spurious-current 抑制专项，**接触角在 2.0 处于回归状态（测试被永久 skip）**。

### 4.1 SCMP（单组元两相，Shan-Chen）

不是可实例化的 scenario 类，而是「用户自写 ψ + 自组 force」模板 + 逐点回归测试：

- **互作用势形式**：ψ(ρ) = ρ₀(1 − e^{−ρ/ρ₀})，`tests/shan_chen/test_shan_chen_two_phase.py:33-34`（教程 `doc/notebooks/08_tutorial_shanchen_twophase.ipynb` 同式）
- **力项加法**：`F = −g_aa · ψ(x) · Σ_d w_d ψ(x+c_d) c_d`，`test_shan_chen_two_phase.py:38-39`；`g_aa = -4.7`（`:20`）
- **力项注入**：`force_model=ForceModel.GUO`（`:42`），`kernel_type='collide_only'` + 独立 pull streaming（`:41-72`）
- **单步序列**：`sync ρ → collision → sync pdf → stream → swap`（`:74-83`）—— 力依赖邻居 ρ，故每步前必须 halo 同步 ρ
- **密度比能力（实测自测试参数）**：ρ_liquid/ρ_gas ≈ 2.1/0.15 ≈ **14**（初始条件 `:76-80`，终态剖面 `:85-90`）—— 指数势 SC 的典型上限量级
- **spurious current 处理**：**无专项**（见 4.6）
- **验证方式（值得照抄）**：与外部 C++ 参考实现（`lbm-principles-practice/chapter9/shanchen.cpp`，1000 步）沿中心线的 **64 点密度剖面逐点 `np.allclose`**（`:85-92`），不是"跑通即过"

### 4.2 MCMP（多组分 Shan-Chen）—— 与我们 GAP 最直接对口

`doc/notebooks/09_tutorial_shanchen_twocomponent.ipynb` + `tests/shan_chen/test_shan_chen_two_component.py:12`：

- **互作用矩阵**：`g_aa = g_bb = 0`、`g_ab = g_ba = 6.0`（`:23-25`）→ 组分间排斥产生分相
- **逐组分力**：`F_σ = −ψ_σ(x) Σ_{σ'} g_σσ' Σ_d w_d ψ_σ'(x+c_d) c_d`，`test_shan_chen_two_component.py:55-59`（A/B 各一份）
- **关键耦合约定**：碰撞统一用**重均（barycentric）速度** `u_bary = (ρ_a u_a + ρ_b u_b)/(ρ_a + ρ_b)`（`:45` 建字段、`:73-81` 赋值），两套 `LBMConfig` 都用 `velocity_input=u_bary` + 各自 `density_input`（`:85-89`）—— 分层两相界面动量守恒的前提
- **力项**：两组分各自 `ForceModel.GUO`（`:85`、`:89`）
- **验证**：两组分浓度剖面各自 `np.allclose` 参考实现（`:174-175`）
- 密度比：受同一指数 ψ 限制，MCMP **不提供**高密度比机制

### 4.3 相场 Cahn-Hilliard（守恒型，n 相）

- **自由能解析构造**：`phasefield/analytical.py:101 free_energy_functional_n_phases(num_phases, surface_tensions, ...)` —— 用**对称表面张力矩阵 σ_ij 反解** κ/势垒项（`:154-178`，含 `3/√2·ξ·(σ_kn + σ_ln − σ_kl)` 型三体式）；惩罚项变体 `:64 free_energy_functional_n_phases_penalty_term`
- **化学势与力**：`analytical.py:231 chemical_potentials_from_free_energy`（μ_φ = ∂f/∂φ − ∇·(κ∇φ) 符号化）、`analytical.py:243 force_from_phi_and_mu`
- **内核**：
  - `phasefield/kerneleqs.py:13 mu_kernel`（μ 离散，`dx`/`discretization` 可配）
  - `:24 force_kernel_using_mu`（μ∇φ 路线）
  - `:32 pressure_tensor_kernel` / `:48 pressure_tensor_kernel_pbs`（π_αβ = (f − Σφμ)δ_αβ − κ ∂_αφ ∂_βφ 路线）
  - `:70 force_kernel_using_pressure_tensor`（F = −∇·π，支持 `extra_force` 叠加重力）
  - `:86 cahn_hilliard_fd_eq` + `:92 CahnHilliardFDStep`（FD CH 求解器，含 mobility/dt）
- **驱动类**：`phasefield/phasefieldstep.py:20 PhaseFieldStep`（LBM 流体 + FD 相场耦合步；`:58` 由自由能生成化学势；`:116-118` 力由压力张量 kernel 产生；`:139` 把 `force_field` 作为 LBM 外力传入）；`:90-96` 对力方程施 Neumann 边界（界面对称性）
- **n 相模型**：`phasefield/nphase_nestler.py:46 create_model(domain_size, num_phases, coeff_a, coeff_epsilon, gabd, penalty_factor=0.01, ...)`（Nestler 罚项模型）；`phasefield/n_phase_boyer.py:17 diffusion_coefficients(surface_tensions)`、`:57 psi(k, c, f)`（Boyer 非耦合 n 相，扩相数不需重标定）
- **高密度比自由能**：`phasefield/high_density_ratio_model.py:8 free_energy_high_density_ratio(eos, density, density_gas, density_liquid, c_liquid_1, c_liquid_2, lambdas, kappas)`（Wöhrwag 2018 三元高密度比）。实现要点：`:28 free_energy_from_eos` 积分得 ψ_eos；`:33-34` 用 **Maxwell 等压条件** ψ(ρ_g)=ψ(ρ_l) 以 `sp.solve` 解积分常数；`:37-46` 组 f_bulk = λ/2(ψ−ψ₀)² + 界面项 → **密度比由 EOS 共存密度保证**，不经 ψ(ρ) 势
- **EOS 工具**：`phasefield/eos.py:8 carnahan_starling_eos`、`:19` C-S 临界温度、`:23 van_der_walls_eos`、`:29` vdW 临界温度、`:36 eos_from_free_energy`、`:42 free_energy_from_eos`、`:53 maxwell_construction`（数值求 ρ_g/ρ_l）

### 4.4 Allen-Cahn（非守恒相场，含接触角/热毛细）

- **化学势**：`phasefield_allen_cahn/kernel_equations.py:19 chemical_potential_symbolic(phi_field, stencil, beta, kappa)`；各向同性梯度 `:37 normalized_isotropic_gradient_symbolic`
- **完整 Hydro 力链（结构清晰，可直接照抄分解方式）**：
  - `:55 pressure_force`（ρ(φ) 差生压力力）
  - `:78 viscous_force`（按 τ 与密度比）
  - `:112 surface_tension_force`（**CSF 连续表面张力**）
  - `:133 thermocapillary_surface_tension_force`（σ(T) → 热毛细，配教程 12/13）
  - `:169 hydrodynamic_force` 汇总、`:242 hydrodynamic_force_assignments`、`:321 add_hydrodynamic_force`（并入碰撞规则）
  - `:213 interface_tracking_force` + `:303 add_interface_tracking_force`（AC 方程本身的界面演化力）
- **时间推进**：`phasefield_allen_cahn/numerical_solver.py:15 get_runge_kutta_update_assignments`（相场用 RK + `:80 _get_conduction_gradient`、`:107 _get_stage`）
- **参数标定（工程公式，可直接复用）**：`phasefield_allen_cahn/parameter_calculation.py:227 calculate_parameters_rti`（`density_ratio=3` 默认，`:233`）：`:256` `gravity_lattice_units = L/(T²·A)`、`reference_velocity = √(g·L)`、`:264` `σ = μ_heavy·U/Ca`、`:265` `mobility = U·L/Pe`；`calculate_dimensionless_rising_bubble`（`:277`）默认 `density_ratio=1000`（`:282`），`calculate_parameters_taylor_bubble`（`:322`）直接带**水/空气物性** `water_rho=998 / air_rho=1.2047`（`:341-342`，比 ≈ **828:1**）→ **密度比能力声明到 1000 量级**（代码默认值，非验证过的收敛上限）
- **接触角**：`phasefield_allen_cahn/contact_angle.py` 存在，但测试 `tests/phasefield_allen_cahn/test_contact_angle.py:16` 被 `@pytest.mark.skipif`（`:13-15`，理由 "Contact angle calculation not yet available in lbmpy 2.0"）**永久跳过** → 接触角在 2.0 是 **WITHHELD（回归状态）**，不可当现成能力吸收

### 4.5 自由面

**无**。全仓 `free.surface` grep 仅 1 命中，且是 `fieldaccess.py:374` 引用 Lehmann 论文的 stencil ordering 注释（借用了自由面 LBM 的 27 方向排序，**不含**自由面算法本体）。`partially_saturated_cells.py` 是部分饱和**固体**（颗粒/多孔），不是自由面 fill-fraction。

### 4.6 spurious current

**无专项处理**。`well.?balanced|spurious` grep 命中 0。物理上靠压力张量离散（`kerneleqs.py:32`）与高阶平衡（`equilibrium_order`、`fourth_order_correction`）间接压低；**没有** well-balanced / 平衡态保持格式，也**没有**伪速度量级后处理指标（`phasefield/post_processing.py` 只有几何后处理：`get_angle:9`、`find_intersections:18`、`get_triple_point_centers:73`、`analytic_neumann_angles:135`，无伪速度量级指标）。
→ 若我们做「spurious current 抑制」，此处是真空区，不是抄作业。

---

## 五、GPU / 并行

- **后端**：`pystencils`（外部依赖；本仓只做「符号 → AST → 源码」）。目标 CPU/GPU（CUDA/HIP）；`CHANGELOG.md:5-6` 明确 "Removing OpenCL support"。`src` 内 `Target.GPU` 共 12 处：`advanced_streaming/communication.py:49,:58,:85,:103`、`macroscopic_value_kernels.py:157,:240`、`phasefield/phasefieldstep.py:61` 等。
- **数据布局**：pdf 数组默认 `layout='fzyx'`（`updatekernels.py:146 create_pdf_array`），即**逐方向连续的 SoA**（Q 为最快变化维 → 单方向读写在内存上连续、利于合并访问）。边界壁距/方向信息放 index vector（`boundaries/boundaryconditions.py:174` 与 `:259` 的 `additional_data → ('q', data_type)`）。
  - **AA-pattern**（单数组原地、奇偶步换访问模式，省一半内存、免 swap）：`fieldaccess.py:136 AAEvenTimeStepAccessor` / `:148 AAOddTimeStepAccessor`
  - **esotwist / eso-pull / eso-push** 压缩模式：`fieldaccess.py:160-281`（借 Lehmann ordering 只读写"必要"方向），入口 `fieldaccess.py:373 _get_lehmann_stencil`
- **融合 kernel**：`kernel_type` 选择器 `creationfunctions.py:298`（`default_stream_collide` / `collide_only` / esotwist …），流式模式 `creationfunctions.py:306 streaming_pattern='pull'`；「碰撞 + 宏观量输出 + 流式」一并生成见 `updatekernels.py:13 create_lbm_kernel` / `:87 create_stream_pull_with_output_kernel` / `:61 create_stream_only_kernel` / `:123 create_copy_kernel`。
- **稀疏/多孔**：list-based 内核 `sparse/update_rule_sparse.py:22 create_lb_update_rule_sparse`（AoS 式 `pdf_arr[n_fluid_cells, Q]`，`:8 create_symbolic_list(layout='AoS')`）、宏观量稀疏读写 `:54` / `:67`、索引替换 `:86 _list_substitutions`；映射 `sparse/mapping.py:14 SparseLbMapper` / `:158 SparseLbBoundaryMapper`（`create_index_array`，`tests/test_sparse_lbm.ipynb` 里与 dense 解 `np.testing.assert_almost_equal` 对齐）。注意该 notebook 明确 `raise NotImplementedError("UBB on GPU not working yet")` → 稀疏 + GPU + 速度边界组合**不可用**。
- **MPI / 分区**：本仓**不实现域分解**，交给 pystencils `DataHandling` / waLBerla；本仓只优化**周期边界通信**——`advanced_streaming/communication.py:20 LBMPeriodicityHandling`、`:121 get_communication_slices`、`:185 periodic_pdf_gpu_copy_kernel`（把 ghost 交换编译进生成 kernel；`:37-39` 注释称比 cupy 数组拷贝快近 2×，代价是 D3Q27 编译可达 20 s）。
- **通信隐藏**：**无**。`communication.py` 内无 stream/event 异步重叠逻辑，ghost 交换是同步的。
- **性能证据**：无 CI 性能门（`tests/benchmark/test_benchmark.py` 是生成/测量脚本，含 CSE 选项组合遍历，但无阈值断言）→ 仓内**无可引用的加速比数字**，任何加速比都必须自测。

---

## 六、可微 / 代码生成

- **符号推导**：全库 SymPy 表达式（方法即符号对象，可 print 到 LaTeX）；简化策略 `simplificationfactory.py`（CSE、跳跃点/symmetry 简化）；`moments.py`（969 行）生成任意阶矩/累积量表达式。
- **自动 Chapman-Enskog（最值钱的验证面工具）**：`chapman_enskog/chapman_enskog.py`（`:520` 起按 ε 阶组织方程）、`chapman_enskog_higher_order.py:44 determine_higher_order_moments`、`chapman_enskog_steady_state.py`、`derivative.py:6/:15`（展开与重组）→ 可**符号验证粘性/守恒律恢复**，作为方法层门禁。
- **kernel 生成**：`pystencils.create_kernel`；CSE 选项 `cse_pdfs` / `cse_global`（`tests/benchmark/test_benchmark.py` 遍历 4 种组合）、向量化选项、CPU/GPU target；`lbstep.py` 抽象"一步"（LBM step + 相场 step 组合）；配置序列化 `tests/test_json_serializer.py`（方法可 JSON 快照 diff，作为回归门）。
- **autograd 友好度**：**不友好**。全仓 `torch|jax|autograd|tensorflow` 命中 **0**。方法本身是 SymPy 表达式（理论上可导出为 torch 计算图做可微），但**无现成路径**；生成的是命令式 C/CUDA kernel，不可反向传播。
- **其它可复用抽象**：`scenarios.py:38/:73/:109` 三个预定义场景工厂（全周期流 / 顶盖方腔 / 通道）；`analytical_solutions.py` 解析解（Poiseuille / ShearWave / Taylor-Green 等）→ 验证面现成。

---

## 七、与我们功能点的映射

| 我们 feature_ledger | lbmpy 对位 | 证据（文件:行） | 成熟度 |
|---|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE** | MCMP Shan-Chen 双组分模板：逐组分力 + 重均速度 + 各自 Guo | `doc/notebooks/09_tutorial_shanchen_twocomponent.ipynb`；`tests/shan_chen/test_shan_chen_two_component.py:24,:45,:55-60,:73-81,:85-89,:174-175`；`forcemodels.py:521` | **REAL**（教程 + 逐点回归），可直接当作实现方式集 |
| NEW: MCMP 高密度比 | SC 指数 ψ 密度比 ~14；高密度比走 EOS + Maxwell + Wöhrwag 自由能；ACA 参数默认 1000 | `tests/shan_chen/test_shan_chen_two_phase.py:20,:33`；`phasefield/high_density_ratio_model.py:8-46`；`phasefield/eos.py:53`；`phasefield_allen_cahn/parameter_calculation.py:233,:277-282,:341-342` | SC 路线 REAL 但上限低（~10 级）；相场路线 REAL（工程未验证） |
| NEW: 自由面 | **无** | `grep free.surface` 仅 `fieldaccess.py:374` 注释 | **NOT_SUPPORTED** |
| NEW: 相场 Allen-Cahn | AC 界面追踪 + 压力/粘性/CSF 表面张力/热毛细力链；接触角 2.0 回归 | `phasefield_allen_cahn/kernel_equations.py:19,:55,:78,:112,:133,:169,:213`；`contact_angle.py` + `tests/phasefield_allen_cahn/test_contact_angle.py:13-15`（skipif） | 力链 REAL / 接触角 **WITHHELD** |
| NEW: GPU 融合 kernel | `kernel_type` 融合 + esotwist/AA 压缩布局 + SoA `fzyx` | `creationfunctions.py:298,:306`；`updatekernels.py:13,:87,:146`；`fieldaccess.py:136,:148,:160-281` | REAL（生成器级）；**无性能门/无加速比证据** |
| NEW: 分布式多卡 | 无分区、无通信隐藏；仅 MPI ghost 交换编译优化 | `advanced_streaming/communication.py:20,:121,:185` | **STUB 级**（真分区在 pystencils/waLBerla，本仓无） |
| NEW: 稀疏/多孔 | sparse LBM（list-based AoS）+ PSM 部分饱和固体 | `sparse/mapping.py:14,:158`；`sparse/update_rule_sparse.py:8,:22`；`tests/test_sparse_lbm.ipynb`（与 dense 对齐断言）；`partially_saturated_cells.py:11,:48,:115` | REAL（稀疏+GPU+UBB 组合不可用） |
| NEW: 案例组织 | `LBMConfig` 单点配置对象 + 场景工厂 + `lbstep` 组合 | `creationfunctions.py:105-317`（LBMConfig 全字段）；`scenarios.py:38,:73,:109`；`lbstep.py` | REAL（配置驱动值得学） |
| NEW: 测试与验证面 | 逐点回归 vs 外部参考、解析解库、符号 Chapman-Enskog、84 `.py` + 28 `.ipynb` 测试 | `tests/shan_chen/*:92,:174`；`analytical_solutions.py`；`chapman_enskog/`；`lbmpy_stats.json` | **REAL —— 最值得吸收的一条** |
| NEW: CI | GitLab CI 8 stage（lint / unit / nightly / coverage / docs / deploy） | `.gitlab-ci.yml:1-40` | REAL（绑定 FAU GitLab，不可直接用） |

---

## 八、可吸收 Top3 + 风险

### Top1 — MCMP 双组分「逐组分力 + 重均速度」耦合范式（→ GAP-LBM-MCMP-POISEUILLE）

- **做法**：`F_σ = −ψ_σ Σ_{σ'} g_σσ' Σ_d w_d ψ_σ'(x+c_d) c_d`；两组分各用 Guo 力（`(1−ω/2)` 修正）；碰撞统一用 `u_bary = Σρ_σ u_σ / Σρ_σ`；`collide_only` + 独立 pull streaming；每步前同步 ρ 的 halo。
- **落地价值**：两组分分层 Poiseuille 正是该模板 + 壁面 `NoSlip`/`UBB` + 体力驱动的退化情形，可直接作为我们 MCMP 通路的**验收基准构造**（两相界面位置、各相流量占比可解析对照）。
- **风险**：① 指数 ψ 密度比上限 ~10–14，高密度比必须换相场；② 无 spurious-current 量级控制手段；③ 力项与碰撞的组合在 MRT/累积量下需重新推导（本仓 `ShanChen` 力项虽给出矩空间形式 `forcemodels.py:556-583`，但仓内**只在 SRT/Guo 路径验证过**（`tests/shan_chen/*` 全部 `force_model=ForceModel.GUO`））。

### Top2 — 相场参数标定公式集 + 压力张量力（→ MCMP 高密度比 / 相场 Allen-Cahn）

- **做法**：力由压力张量 `π_αβ = (f − Σφμ)δ_αβ − κ ∂_αφ∂_βφ` 取散度（`kerneleqs.py:32,:70`），μ 由「自由能 + κ∇²φ」符号生成（`analytical.py:231`）；EOS 积分 + Maxwell 等压构造定 ρ_g/ρ_l（`eos.py:42,:53`），高密度比用 Wöhrwag 三元自由能（`high_density_ratio_model.py:8-46`）；参数标定 `gravity = L/(T²A)`、`σ = μU/Ca`、`M = U·L/Pe`（`parameter_calculation.py:262-268`）。
- **落地价值**：这是我们「高密度比 + 相场」缺口的完整实现方式集；公式级可直接复用（重写代码），避免从论文二次推导的标定错误。
- **风险**：① spurious current 无基准可抄，须自建基准（静止液滴伪速度 < 1e-3 量级的判据须自定）；② 接触角能力在 2.0 回归（测试 skip），其接触角实现只能作参考、不可当已验证能力；③ 三元高密度比模型的**工程验证**（工程中未跑过任何算例）未知。

### Top3 — 验证面范式（→ 测试与验证面 / CI）

- **做法**：① 每个能力配「外部参考逐点回归」（SC vs `shanchen.cpp` 的 64 点剖面 `allclose`，`test_shan_chen_two_phase.py:85-92`；MCMP 双剖面 `:174-175`）；② 解析解库 `analytical_solutions.py`（Poiseuille/ShearWave/Taylor-Green）做收敛阶；③ **符号 Chapman-Enskog 自动推导**（`chapman_enskog/`）作方法层门禁——这是一般 LBM 库没有的，能机器证明「新碰撞算子仍恢复正确粘性」；④ 配置对象 `LBMConfig` + JSON 快照 diff（`tests/test_json_serializer.py`）作重构回归。
- **落地价值**：直接把「证据等级」从"跑通"抬到"逐点对基准"，与我们证据纪律（REAL/MOCK/STUB）天然同构。
- **风险**：符号 CE 依赖 sympy 较重（`chapman_enskog.py` 629 行、展开到 ε² 以上易爆炸）→ 建议仅对方法族做**一次性门禁**而非每次提交；references 需自备（外部参考实现有许可证问题，只可取其数值结果作基准数据，不可入库源码）。

### 总风险

1. **GPL-3.0-or-later 强 copyleft**：只能参考重写，禁止复制源码与 `import` 式结合（第一节）。
2. **重依赖 `pystencils` 不在 PyPI**：本环境与本所集群都无法安装 → 我们**没能运行任何 lbmpy 测试**；本报告全部结论为静态源码级，任何"行为正确"的推断都不在本报告担保范围内。
3. **能力回归**：接触角（`test_contact_angle.py:13-15` skipif）、UBB-on-GPU（`test_sparse_lbm.ipynb` NotImplementedError）、OpenCL（已移除）—— 三类都别当现成能力。

---

## 六要素自检（报告纪律）

1. **案例介绍**：对象=lbmpy 2.0 源码树（HEAD `0afc9d5`，2026-07-22），物理背景=LBM 方法定义与多相建模；几何/参数表不适用（非算例），以「方法面清单 + 计数表」替代，见第一/二节与 `lbmpy_stats.json`。
2. **怎么做的**：`git clone --depth 1`（成功）→ `grep -n` / `sed -n` 逐文件读源码本体（文档仅辅助）→ `docs/oss_inventory/make_stats.py` 程序化采集计数与行号 → matplotlib 出图。
3. **计算结果（全部程序化注入，零手抄）**：`src` 105 文件 / 23365 行；格 5 具体类；碰撞工厂 13；力项 9 具体类；边界 12 具体类；测试 84 `.py` + 28 `.ipynb`；多相能力矩阵命中数（SCMP 1 / CH 3 / AC 2 / EOS 3 / 高密度比 1 / 压力张量 3 / **自由面 0** / **spurious-current 0**）。
4. **结果图**：`fig1_surface.png`（能力面计数）、`fig2_modules.png`（模块 LOC 分布，真实 `wc -l`）、`fig3_multiphase.png`（多相覆盖矩阵，0 命中红标 = 已核实缺失）。三图均由 `make_stats.py` 从源码现测数据生成。
5. **注意点**：无 `pystencils` → 零运行验证；GPL-3.0 限制用法；接触角/UBB-on-GPU/OpenCL 均为回归或移除状态；"ForceModel 组合的等价性"未逐一核实（只核对了入口映射 `creationfunctions.py:466`）。
6. **误差判定**：本任务为静态代码盘点，**无任何数值算例**，因此无法给出与实验/参考值的误差百分比 —— **明确声明 N/A（不适用），而非"<3%"**。可给出的定量置信度：报告内每条方法结论均带 `文件:行` 指针，指针经 `grep -n` 复核可复现（脚本可重跑）；"能力缺失"类结论（自由面、spurious-current、IBM、RR）均以**全仓 grep 0 命中**为证据，属可证伪的强否定。

---

## 机器块（守护线解析，放报告末尾）

> 说明：`自由面` 一项核实为 **NOT_SUPPORTED（源码中不存在）**，故**不产出 OSS-SOL 条目**（无可吸收实现，避免污染台账）；其余 9 条映射各配一条 `[OSS-SOL]`。
> 全部 `file:line` 指针由 `docs/oss_inventory/verify_pointers.py` 对源码树全量校验（本报告 106 条 + 机器块 84 条，**0 失败**）。

```text
[OSS-INV]
repo: lssfau/lbmpy
lang: Python (SymPy 符号推导 + pystencils 代码生成; src/lbmpy 105 文件 23365 行; HEAD 0afc9d5 2026-07-22)
license: GPL-3.0-or-later
methods: stencils=D2Q9/D3Q7/D3Q15/D3Q19/D3Q27 + Custom/Lehmann-ordering (velocityspace/standardstencils.py:53,:93,:124,:178,:241); collision=SRT/TRT/TRT-magic/MRT-raw/MRT-orthogonal/KBC/central-moment/cumulant(monomial,polynomial)/entropic-Newton-omega (methods/creationfunctions.py:227,:255,:285,:310,:339,:387,:466,:532,:567,:589; methods/momentbased/entropic.py:8,:90); force=Simple/Luo/Guo/He/Schiller/Buick/EDM/ShanChen/CentralMoment (forcemodels.py:179,:235,:273,:319,:441,:453,:493,:521,:213); equilibrium=discrete+continuous Maxwellian (methods/creationfunctions.py:81,:121); RR 无命名入口
multiphase: ShanChen 力项 forcemodels.py:521; SCMP 指数势 psi=r0(1-exp(-rho/r0)) + Guo 力 tests/shan_chen/test_shan_chen_two_phase.py:33,:38-39 (g_aa=-4.7, 密度比~14); MCMP 两组分逐组分力 + 重均速度 u_bary tests/shan_chen/test_shan_chen_two_component.py:23-25,:55-59,:73-81,:85-89; Cahn-Hilliard n 相解析自由能 phasefield/analytical.py:101,:231,:243 + 内核 phasefield/kerneleqs.py:13,:24,:32,:70,:92 + 驱动 phasefield/phasefieldstep.py:20; 高密度比自由能(EOS积分+Maxwell等压) phasefield/high_density_ratio_model.py:8-46 + phasefield/eos.py:8,:23,:53; n 相 Nestler/Boyer phasefield/nphase_nestler.py:46, n_phase_boyer.py:17,:57; Allen-Cahn 力链 phasefield_allen_cahn/kernel_equations.py:19,:55,:78,:112,:133,:169,:213 + 参数标定 parameter_calculation.py:256,:264,:265,:277-282(密度比1000),:341-342(水/空 828:1); 接触角 2.0 回归 tests/phasefield_allen_cahn/test_contact_angle.py:13-15 skipif; 自由面=无(grep free.surface 仅 fieldaccess.py:374 注释); spurious-current 抑制=无(grep 0 命中)
boundary: NoSlip/NoSlipLinearBouzidi/QuadraticBounceBack/FreeSlip/WallFunctionBounce/UBB(Zou-He 速度型)/FixedDensity(Zou-He 密度型)/SimpleExtrapolationOutflow/ExtrapolationOutflow/DiffusionDirichlet/NeumannByCopy/StreamInConstant (boundaries/boundaryconditions.py:110,:149,:232,:371,:500,:788,:1127,:927,:977,:1187,:1270,:1294); 边界力=动量交换 boundaryhandling.py:100,:108,:128; IBM=无, 替代 PSM 部分饱和固体 partially_saturated_cells.py:11,:48,:115; 曲率修正=无(靠壁距 q 插值 :174,:259)
gpu: pystencils 生成 C/CUDA(HIP), OpenCL 已移除 CHANGELOG.md:5-6; SoA layout fzyx updatekernels.py:146; AA-pattern fieldaccess.py:136,:148; esotwist/eso-pull/eso-push 压缩布局 fieldaccess.py:160-281; 融合 kernel_type creationfunctions.py:298,:306 + updatekernels.py:13,:87; 稀疏 list-based AoS sparse/mapping.py:14,:158 + sparse/update_rule_sparse.py:8,:22; MPI 仅 ghost 交换编译进 kernel advanced_streaming/communication.py:20,:121,:185(无域分解/无通信隐藏); autograd=无(torch/jax 0 命中)
summary: 方法定义谱系最全的符号式 LBM 前端（13 碰撞工厂/9 力项/12 边界/5 格 + 相场与自动 Chapman-Enskog 工具链），但多相只有教程+逐点回归级的 Shan-Chen（指数势，密度比~14）与相场研究代码（CH/AC，密度比声明到 1000），自由面与 spurious-current 抑制完全空白。GPL-3.0-or-later 强 copyleft，对我们只可「参考实现、重写落地」不可复制源码；本环境与集群均无 pystencils（不在 PyPI）故全部结论为静态源码证据，58 个 file:line 指针经 verify_pointers.py 全量校验通过。
[/OSS-INV]

[OSS-SOL]
repo: lssfau/lbmpy
slug: GAP-LBM-MCMP-POISEUILLE
tag: shan-chen-mcmp-barycentric-guo
method: 每组分的力 F_sigma = -psi_sigma(x) * sum_{sigma'} g_{sigma sigma'} * sum_d w_d psi_sigma'(x+c_d) c_d，psi 用指数势 rho0*(1-exp(-rho/rho0))；两套 LBMConfig 各自 density_input 但共享 velocity_input = u_bary = sum(rho_sigma u_sigma)/sum(rho_sigma)，各配 Guo 力项、collide_only 内核 + 独立 pull streaming，每步前同步 rho 的 halo。
files: tests/shan_chen/test_shan_chen_two_component.py:55-59, tests/shan_chen/test_shan_chen_two_component.py:73-81, tests/shan_chen/test_shan_chen_two_component.py:85-89, src/lbmpy/forcemodels.py:521, src/lbmpy/forcemodels.py:273
conventions: g_aa=g_bb=0, g_ab=g_ba=6.0（排斥分相）；D2Q9，cs^2=1/3；rho_a/rho_b 初值 0.1/0.9 分层；omega=1.0；力在碰撞前用邻居 rho 计算，故需 halo 同步；力项 Guo 的 (1-omega/2) 修正；格子单位 dt=dx=1
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:MCMP 高密度比（两组分 Poiseuille 之外的高密度比分层流）
tag: pf-eos-maxwell-high-density-ratio
method: 高密度比不走 psi(rho) 势，而是由 EOS 积分得游离能 psi_eos=int(p/rho^2 drho)，再用 Maxwell 等压条件 psi_eos(rho_g)=psi_eos(rho_l) 解积分常数，得 f_bulk=lambda/2*(psi_eos-psi_0)^2 + kappa 界面项；力由压力张量 pi_ab=(f-sum phi mu)delta_ab - kappa d_a phi d_b phi 取散度施加。
files: src/lbmpy/phasefield/high_density_ratio_model.py:8, src/lbmpy/phasefield/high_density_ratio_model.py:33-46, src/lbmpy/phasefield/eos.py:42, src/lbmpy/phasefield/eos.py:53, src/lbmpy/phasefield/kerneleqs.py:70
conventions: eos 必须只依赖密度一个符号（:25 assert）；lambda/kappa 与 EOS 项/液相项同序；共存密度由 maxwell_construction 数值求得；密度比能力到 1000 量级（parameter_calculation.py:282 默认值，非收敛上限）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:相场 Allen-Cahn（含 CSF 表面张力与热毛细）
tag: allen-cahn-csf-force-chain
method: 化学势 mu = d f/d phi - kappa*laplacian(phi) 符号生成，界面演化用 interface_tracking_force 并入碰撞规则；流体侧力分解为 pressure_force(由 rho(phi) 差) + viscous_force(按 tau 与密度比) + surface_tension_force(CSF) + 可选 thermocapillary_surface_tension_force(sigma(T) 线性)；相场方程用 RK 推进。
files: src/lbmpy/phasefield_allen_cahn/kernel_equations.py:19, src/lbmpy/phasefield_allen_cahn/kernel_equations.py:112, src/lbmpy/phasefield_allen_cahn/kernel_equations.py:133, src/lbmpy/phasefield_allen_cahn/kernel_equations.py:169, src/lbmpy/phasefield_allen_cahn/numerical_solver.py:15
conventions: 界面宽度由 beta/kappa 控制；参数标定 gravity=L/(T^2*A)、sigma=mu*U/Ca、mobility=U*L/Pe（parameter_calculation.py:256,:264,:265）；接触角能力在 2.0 回归，勿用
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:GPU 融合 kernel 与压缩布局
tag: fused-kernel-type-selector
method: 用单一 kernel_type 选择器在「碰撞+宏观量输出+流式」之间融合或分离（default_stream_collide / collide_only / esotwist），pdf 数组按 fzyx（逐方向连续 SoA）布局，AA-pattern 单数组原地省一半内存免 swap，esotwist 只访问必要方向。
files: src/lbmpy/creationfunctions.py:298, src/lbmpy/creationfunctions.py:306, src/lbmpy/updatekernels.py:13, src/lbmpy/updatekernels.py:146, src/lbmpy/fieldaccess.py:136
conventions: layout='fzyx' 时 Q 为最快变化维；AA/esotwist 仅支持 D2Q9/D3Q15/D3Q19/D3Q27 且需 Lehmann ordering；仓内无性能门禁，加速比须自测
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:分布式多卡（ghost 交换融合进生成 kernel）
tag: mpi-ghost-exchange-kernel-fused
method: 本仓不做域分解，只把周期边界 ghost 交换编译进生成 kernel（拿 src/dst slice 直接生成 CUDA copy kernel），注释称比 cupy 数组拷贝快近 2 倍；域分解交给 pystencils DataHandling/waLBerla。无异步通信隐藏。
files: src/lbmpy/advanced_streaming/communication.py:20, src/lbmpy/advanced_streaming/communication.py:121, src/lbmpy/advanced_streaming/communication.py:185
conventions: 仅周期边界；D3Q27 编译耗时可达 20 s（:38-39）；cupy_direct_copy 开关；GPU 下需 target=GPU
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:稀疏/多孔 LBM（list-based AoS）
tag: sparse-list-based-aos-lbm
method: 由 flag 场映射出「流体单元列表 + 每方向邻居索引」index array，pdf 存成 pdf_arr[n_fluid_cells, Q]（AoS），碰撞流式在 1D 索引空间一次完成（streaming 变成索引查表），边界另用 SparseLbBoundaryMapper 出 UBB 赋值 kernel。
files: src/lbmpy/sparse/mapping.py:14, src/lbmpy/sparse/mapping.py:158, src/lbmpy/sparse/update_rule_sparse.py:8, src/lbmpy/sparse/update_rule_sparse.py:22
conventions: index array dtype uint32；需要 ghost_layers=1；稀疏+GPU+UBB 组合未实现（notebook 里显式 NotImplementedError）；与 dense 解对齐用 assert_almost_equal
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:案例组织（单点配置对象 + 场景工厂）
tag: lbmconfig-single-point-config
method: 用一个 dataclass LBMConfig 集中全部方法/数值/输出配置（stencil, method, relaxation_rates, compressible, zero_centered, equilibrium_order, c_s_sq, force_model, force, kernel_type, streaming_pattern, output, velocity_input, density_input ...），场景工厂只负责几何与初边值，step 对象负责时间推进组合。
files: src/lbmpy/creationfunctions.py:105-317, src/lbmpy/scenarios.py:38, src/lbmpy/scenarios.py:109, src/lbmpy/lbstep.py:1
conventions: 配置可 JSON 序列化做快照 diff（tests/test_json_serializer.py）；force 既可为元组也可为字段访问；kernel_type 决定数据布局与是否融合
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:测试与验证面（逐点参考回归 + 解析解 + 符号 Chapman-Enskog）
tag: pointwise-reference-regression
method: 每个物理能力配「与外部参考实现逐点比对」的回归测试（SC 单/双组分各与 C++ 参考实现沿中心线 64 点 rho 剖面 np.allclose），另配解析解库（Poiseuille/ShearWave/Taylor-Green）与自动符号 Chapman-Enskog 推导（按 epsilon 阶展开验证粘性与守恒律恢复）。
files: tests/shan_chen/test_shan_chen_two_phase.py:85-92, tests/shan_chen/test_shan_chen_two_component.py:174-175, src/lbmpy/analytical_solutions.py:1, src/lbmpy/chapman_enskog/chapman_enskog.py:1
conventions: 参考值来自 lbm-principles-practice/chapter9/shanchen.cpp（1000 步）；allclose 默认 rtol/atol；CE 展开到 epsilon^2 以上易爆炸，建议仅做一次性方法族门禁
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: lssfau/lbmpy
slug: NEW:CI 分级门禁
tag: gitlab-ci-staged-gates
method: GitLab CI 分 8 个 stage（Code Quality / Unit Tests / Reporting / Prerelease-Tests / integration / nightly / docs / deploy），lint 用 nox -s lint，单测带 coverage 产物，nightly 由变量开关控制，docs 单独构建。
files: .gitlab-ci.yml:1-8, .gitlab-ci.yml:13, .gitlab-ci.yml:31
conventions: 绑定 FAU 私有 GitLab 镜像与自建 runner（i10git.cs.fau.de:5005）；matplotlib 强制 template backend 以便无头跑测试
transfer: reference
[/OSS-SOL]
```
