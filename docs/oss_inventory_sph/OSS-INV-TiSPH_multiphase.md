# OSS-SPH 深盘·sakamotoyan/TiSPH_multiphase

> **campaign**: osssph-w2  
> **mission_id**: 8686  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8686  
> **仓库**: sakamotoyan/TiSPH_multiphase  
> **tier**: 3（专项能力——隐式多相 SPH 研究代码）

---

## 一、案例介绍

### 1.1 仓库概况

| 维度 | 值 | 来源 |
|---|---|---|
| **语言** | Python (Taichi) | GitHub API (m8638 普查) + 源码确认 |
| **License** | **无 LICENSE 文件** | `find . -name LICENSE` 空结果 |
| **Stars** | 5 | GitHub API (m8638 普查 2026-11-09) |
| **最近提交** | 2024-03-21 | git log |
| **代码行数** | ~1,592 行（basic_solvers/）+ ~400 行（ARCHIVED solvers） | `wc -l` |
| **平台** | Taichi（CPU/CUDA 一键切换） | README + `ti.init(arch=ti.cuda)` |
| **依赖** | taichi, numpy, matplotlib, OpenGL/Vulkan(3D GUI) | README |
| **CI** | 无 | `.github/workflows/` 不存在 |
| **测试** | 无 | `find . -name "test_*"` 空结果 |

### 1.2 物理背景

TiSPH_multiphase 是一个**隐式多相 SPH 求解器**的研究原型，基于 Taichi 框架实现。核心创新点在于提出了 **ISM（Implicit Mixture Solver）**——一种基于 DFSPH 压力投影的隐式多相流方法，支持任意数量流体相（通过 `phase_num` 配置），每相有独立密度、速度、体积分数。代码同时实现了 **JL21 基线方法**（WCSPH + drag-coefficient 漂移模型）作为对照。

---

## 二、源码级方法清单

### 2.1 SPH 变体

| # | 方法 | 文件:行 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | **ISM (Implicit Mixture Solver)** | `ti_sph/basic_solvers/Solver_ism.py:14-84` | **活跃·核心** | 提出的方法：DFSPH 压力投影 + 相速度分解 + drift/diffusion 体积分数输运。Cd 参数控制压力加速度在各相间的分配（`ditribute_acc_pressure_2_phase`）。 |
| 2 | **JL21 (WCSPH multiphase + drag)** | `ti_sph/basic_solvers/Solver_JL21.py:1-141` | **活跃·基线** | WCSPH 多相 + drag coefficient kd 漂移模型 + lambda 格式体积分数更新（迭代保正）。 |
| 3 | **DFSPH (Divergence-free SPH)** | `ti_sph/basic_solvers/Solver_df.py:1-261` | **活跃** | 实现 Bender & Koschier SCA'15 的 divergence-free + incompressibility 双投影。含 volume-fraction 变体 (`vf_update_vel_adv_from_kappa_*`)。 |
| 4 | **WCSPH** | `ti_sph/basic_solvers/Solver_wcsph.py:1-54` | **活跃（简化版）** | 标准 WCSPH 数密度计算 + 压力加速度。Tait EOS 在 ARCHIVED 版本中（`ARCHIVED/solver/WCSPH.py`, gamma=7）。 |
| 5 | **ISPH_Elastic** | `ti_sph/ARCHIVED/solver/ISPH_Elastic.py:1-40+` | **归档** | Peer et al. CGF'18 隐式弹性固体 SPH。 |
| 6 | **Akinci12 边界耦合** | `ti_sph/ARCHIVED/solver/Akinci12.py` | **归档** | Akinci et al. TOG'12 刚体-流体耦合（空壳）。 |
| 7 | **DF_coupling** | `ti_sph/ARCHIVED/solver/DF_coupling.py` | **归档** | DFSPH 多层耦合（static/elastic/fluid）。 |

### 2.2 核函数与SPH算子

| 算子 | 文件:行 | 说明 |
|---|---|---|
| **Cubic spline W(r,h)** | `ti_sph/basic_solvers/sph_funcs.py:37-50` (`spline_W`) | 标准三次样条核，2D sig=40/7π, 3D sig=8/π |
| **Gradient ∇W** | `ti_sph/basic_solvers/sph_funcs.py:67-78` (`grad_spline_W`) | 核函数梯度 |
| **Laplacian ∇²A** | `ti_sph/basic_solvers/sph_funcs.py:87-104` (`artificial_Laplacian_spline_W`) | 人工拉普拉斯算子（用于粘性/扩散），公式来自 SPH-Tutorial Eq.26 |
| **密度求和** | `ti_sph/basic_solvers/Solver_sph.py:55-60` (`inloop_accumulate_density`) | Σ m_j W_ij |
| **数密度** | `ti_sph/basic_solvers/Solver_sph.py:62-67` (`inloop_accumulate_number_density`) | Σ m_i W_ij（JL21 用） |
| **压缩比** | `ti_sph/basic_solvers/Solver_sph.py:69-74` (`inloop_accumulate_compression_ratio`) | Σ V_j W_ij（DFSPH 用） |
| **粘性力** | `ti_sph/basic_solvers/Solver_JL21.py:20-28` | 人工粘性 Π_ij = -k_vis min(0, A_ij·x_ij)/(r²+εh²) |

### 2.3 边界处理

| 方法 | 状态 | 说明 |
|---|---|---|
| **SDF 边界** | **归档** (`ti_sph/ARCHIVED/solver/SDF_boundary.py`, `ARCHIVED/data_struct/SDF_boundary/`) | 旧版有 SDF 边界处理，当前活跃代码**无边界处理器** |
| **虚粒子/固定粒子** | **无显式实现** | 场景 demo 中仅用流体粒子，无壁面边界 |

**结论**：当前活跃代码**不支持固壁边界**。所有 demo 为自由空间多相流动（碰撞/旋转/分离）。

### 2.4 时间步控制

| 方法 | 文件:行 | 说明 |
|---|---|---|
| **CFL 自适应** | `ti_sph/basic_solvers/Solver_multiphase.py:~230-237` (`cfl_dt`) | dt = min(max_dt, particle_size / max_vel × cfl_factor)；遍历所有相速度取最大 |
| **固定 dt** | `ti_sph/basic_solvers/sph_funcs.py:113-115` (`fixed_dt`) | dt = size / cs × cfl_factor |
| **实际使用** | 场景文件中 `max_time_step = part_size/100`；CFL 调用被注释掉 | 实际用固定小时间步 |

### 2.5 多相/自由面能力

| 能力 | 实现 | 文件:行 |
|---|---|---|
| **多相（N 相）** | 体积分数模型：每粒子存储 `phase.val_frac[part_id, phase_id]`，Σ val_frac = 1 | `Solver_multiphase.py:regularize_val_frac` |
| **相速度分解** | v_phase = v_mixture + v_drift；Cd 控制漂移量 | `Solver_ism.py:inloop_add_phase_acc_vis` |
| **体积分数输运** | 扩散(Cf) + 漂移(drift velocity) 两项 | `Solver_multiphase.py:inloop_update_phase_change_from_drift/diffuse` |
| **Lambda 保正格式** | JL21 的迭代 λ 方案，防止负体积分数 | `Solver_multiphase.py:update_val_frac_lamb` |
| **密度/质量更新** | ρ = Σ α_k ρ_k; m = ρV | `Solver_multiphase.py:update_rest_density_and_mass` |
| **自由面** | 隐式支持（SPH 天然自由面），无显式追踪 | — |

### 2.6 GPU/MPI 并行策略

| 维度 | 实现 | 说明 |
|---|---|---|
| **GPU** | **Taichi 后端** | `ti.init(arch=ti.cuda)` / `ti.init(arch=ti.cpu)` 一键切换；所有 kernel 用 `@ti.kernel` / `@ti.func` 装饰 |
| **MPI** | **无** | 无 MPI 相关代码 |
| **多 GPU** | **无** | Taichi 单设备 |
| **并行策略** | Taichi JIT → CUDA/OpenMP | 依赖 Taichi 编译器自动并行化 |

---

## 三、验证面盘点

### 3.1 自带算例

| 算例 | 文件 | 维度 | 相数 | 描述 |
|---|---|---|---|---|
| 2D 多相碰撞 | `scene_2D_multiphhase_collide_no_GUI.py` | 2D | 2 | 两团流体对撞混合 |
| 2D 多相旋转 | `scene_2D_multiphhase_rotate_no_GUI.py` | 2D | 3 | 环形域内多相无旋旋转 |
| 3D 多相分离 | `scene_3D_multiphhase_separate.py` | 3D | 3 | 方池中多相重力分离 |

### 3.2 测试框架

- **无单元测试**：`find . -name "test_*"` 空结果
- **无 CI 管线**：`.github/workflows/` 不存在
- **无基准数据**：无解析解对比、无实验数据对比、无收敛性研究

### 3.3 守恒量监控

代码内含统计量打印（非自动化验证）：
- 线性动量：`statistics_linear_momentum_and_kinetic_energy()`（`Solver_multiphase.py`）
- 角动量：`statistics_angular_momentum()`（`Solver_multiphase.py`）
- 体积分数检查：`debug_check_val_frac()`（打印各相总量）
- 负体积分数检测：`debug_check_negative_phase()`

**结论**：验证面极度薄弱。仅有可视化 demo，无量化验证。

---

## 四、可迁移方案

### 4.1 License 合规判定

**仓库无 LICENSE 文件**。根据 GitHub ToS，无 license 的公开仓库默认保留所有权利（all rights reserved），**不可直接复用代码**。仅可作为**参考（reference-only）**，不可 adapt/fit 进闭源或不同 license 的项目。

> ⚠️ 若需 adapt/fit，须先联系作者获取 license 授权。以下方案分级基于「假设获得兼容 license」的假设。

### 4.2 可迁移方案

[OSS-SOL]{"repo":"sakamotoyan/TiSPH_multiphase","id":"SPHSOL-tisphmultiphase-01","kind":"reference","summary":"ISM 隐式多相混合求解器的相速度分解思路（v_phase = v_mixture + v_drift, Cd 参数分配压力加速度）可作为 TensorSPH 多相模块的算法参考。核心创新：在 DFSPH 压力投影框架内实现多相 drift/diffusion 输运，避免显式界面追踪。参考文件：Solver_ism.py 的 ditribute_acc_pressure_2_phase 和 inloop_add_phase_acc_vis。","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"sakamotoyan/TiSPH_multiphase","id":"SPHSOL-tisphmultiphase-02","kind":"reference","summary":"Lambda 保正格式（update_val_frac_lamb）提供了一种迭代保正体积分数输运方案：当检测到负体积分数时自动减半时间步长并重算，直到所有粒子体积分数非负。可作为 TensorSPH 多相输运模块的参考实现，尤其适用于密度比大的多相流。参考文件：Solver_multiphase.py 的 update_val_frac_lamb + check_negative_lamb。","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"sakamotoyan/TiSPH_multiphase","id":"SPHSOL-tisphmultiphase-03","kind":"reference","summary":"Taichi 后端 SPH 代码架构模式：@ti.data_oriented 类 + @ti.kernel 函数 + template 参数传递，实现 CPU/GPU 一键切换。可作为 TensorSPH 若采用 Taichi 后端的架构参考。注意：Taichi 的 template 机制与 C++ template 不同，是编译期特化。参考文件：ti_sph/basic_solvers/ 全部。","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

---

## 五、注意点

1. **License 风险**：无 LICENSE = all rights reserved。任何代码复用须先获授权。
2. **代码成熟度低**：5 stars，最后更新 2024-03，研究原型级别。
3. **无边界处理**：活跃代码无固壁边界，限制了工程应用。
4. **ARCHIVED 代码**：大量归档代码（WCSPH/DFSPH/ISPH_Elastic/Akinci12/SDF_boundary）表明项目经历了重大重构，旧版可能更完整但已弃用。
5. **拼写错误**：代码中 `ditribute`（应为 distribute）、`multiphhase`（应为 multiphase）等拼写错误频现，反映代码质量不高。
6. **CFL 被注释**：实际 demo 用固定小时间步，CFL 自适应未启用。
7. **相变代码注释**：`update_phase_change` 在 ISM 中被注释掉，说明相变功能未完成。

---

## 六、误差判定

**无基准数据可供对比**。仓库未提供任何解析解、实验数据或文献对比。守恒量（动量/角动量）仅打印不验证。无法判定精度。

**结论**：该仓库作为研究原型有算法参考价值（ISM 多相分解 + lambda 保正），但无量化验证、无 license、无边界处理，不可直接用于生产。

---

## 机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"sakamotoyan/TiSPH_multiphase","tier":3,"methods":["ISM(Implicit-Mixture-Solver)","JL21(WCSPH-multiphase-drag)","DFSPH(divergence-free+incompressibility)","WCSPH(weakly-compressible)"],"boundary":"none-active(SDF-boundary-archived)","gpu":"Taichi-backend(CPU/CUDA-switch)","mpi":"none","validation":"none(no-tests,no-benchmarks,no-quantitative-comparison)","license":"unlicensed(no-LICENSE-file)","kernel":"cubic-spline(spline_W)","timestepping":"CFL-adaptive(commented-out)+fixed-dt","multiphase":"volume-fraction(N-phase)+drift-diffusion+lambda-positivity","transferable":["SPHSOL-tisphmultiphase-01","SPHSOL-tisphmultiphase-02","SPHSOL-tisphmultiphase-03"]}[/OSS-INV]

[OSS-SOL]{"repo":"sakamotoyan/TiSPH_multiphase","id":"SPHSOL-tisphmultiphase-01","kind":"reference","summary":"ISM implicit multiphase mixture solver: phase velocity decomposition with Cd-controlled pressure acceleration distribution in DFSPH framework","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"sakamotoyan/TiSPH_multiphase","id":"SPHSOL-tisphmultiphase-02","kind":"reference","summary":"Lambda positivity-preserving scheme for volume fraction transport: iterative halving until all phase fractions non-negative","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"sakamotoyan/TiSPH_multiphase","id":"SPHSOL-tisphmultiphase-03","kind":"reference","summary":"Taichi-backend SPH architecture pattern: @ti.data_oriented + @ti.kernel + template for CPU/GPU portability","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]
