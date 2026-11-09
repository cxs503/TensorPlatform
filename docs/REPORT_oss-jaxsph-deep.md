# OSS-SPH 深盘波2：tumaer/jax-sph 代码审计报告

**任务 ID**: m8662 | **Campaign**: osssph-w2 | **日期**: 2026-11-09

---

## 一、案例介绍（物理背景/仓库概况）

**tumaer/jax-sph** 是一个基于 JAX 的可微光滑粒子流体动力学（SPH）框架，定位为"可微 SPH 反问题/ML 耦合的桥梁"。该仓库于 2023-09-18 创建，MIT 许可证，截至 m8638 普查时 84 stars、8 forks。

**物理定位**：弱可压 SPH（WCSPH）为主，扩展 δ-SPH 密度扩散与 Riemann SPH 通量求解，支持热传导耦合。核心卖点是 JAX 自动微分（AD）使整个 SPH 求解器可微，可用于梯度优化、反问题求解、Solver-in-the-Loop 训练。

**安装**：`pip install jax-sph`（PyPI 发布），支持 CPU/GPU（`jax[cuda12]`）。

---

## 二、方法清单（源码级定位）

### 2.1 SPH 变体

| 变体 | 求解器 key | 文件:行 | 描述 |
|---|---|---|---|
| **WCSPH** (标准弱可压) | `SPH` | `jax_sph/solver.py` | 密度演化 per Adami et al. 2013；Tait EoS；可选人工黏度 |
| **δ-SPH** | `DELTA` | `jax_sph/solver.py:rho_evol_fn_delta_wrapper` | Marrone et al. 2011 密度扩散项；δ=0.1, α=0.01 |
| **Riemann SPH** | `RIE` | `jax_sph/solver.py:rho_evol_riemann_fn_wrapper` | Zhang et al. 2017 线性 Riemann 求解器；壁面 BC per Yang 2020 |
| **Transport Velocity SPH** | `SPH+tvf` | `jax_sph/solver.py:_acceleration_tvf_fn` | 背景压力项控制拉伸不稳定性；tvf∈[0,1] |

### 2.2 核函数库（`jax_sph/kernel.py`）

| 缩写 | 全称 | 截断半径 | 维度支持 |
|---|---|---|---|
| CSK | Cubic Spline (Monaghan) | 2h | 1D/2D/3D |
| QSK | Quintic Spline (Morris) | 3h | 1D/2D/3D |
| WC2K | Wendland C2 | 2h | 1D/2D/3D |
| WC4K | Wendland C4 | 2h | 2D/3D |
| WC6K | Wendland C6 | 2h | 2D/3D |
| GK | Gaussian | ∞(数值截断) | — |
| SGK | Super Gaussian | ∞(数值截断) | — |

### 2.3 状态方程（`jax_sph/eos.py`）

| EoS | 文件 | 描述 |
|---|---|---|
| Tait | `jax_sph/eos.py:TaitEoS` | 标准弱可压 EoS，p = B[(ρ/ρ₀)^γ - 1] |
| RIEMANN | `jax_sph/eos.py:RIEMANNEoS` | Riemann SPH 专用 EoS |

### 2.4 边界处理

- **GWBC** (Generalized Wall Boundary Conditions)：per Zhang et al. 2017，默认 3 层壁面粒子
- **Free-slip**：可选自由滑移边界
- **Riemann velocity BCs**：RIE 求解器专用，壁面速度镜像处理

### 2.5 时间步控制

- **CFL 自适应**：`cfg.solver.cfl = 0.25`，基于声速和粒子速度自动计算 dt
- **固定 dt**：`cfg.solver.dt` 可显式指定

### 2.6 GPU/并行策略

- **JAX JIT 编译**：`jax.jit` 自动 XLA 编译，CPU/GPU 无缝切换
- **vmap/scan 向量化**：粒子对交互通过 `jax.vmap` 批量处理
- **邻居搜索**：`jax_md` 库提供 `jaxmd_vmap`、`jaxmd_scan`、`matscipy` 三种后端
- **GPU 支持**：`pip install "jax[cuda12]"`，CUDA 12 兼容

### 2.7 多相/自由面/热传导

- **自由面**：通过 fluid_mask 标识，无特殊多相模型
- **热传导**：`cfg.solver.heat_conduction = True` 开启，含温度场 T 和热导率 κ
- **多相**：❌ 不支持（无颜色梯度/多流体模型）

---

## 三、验证面盘点

### 3.1 自带算例（`cases/`）

| 算例 | 缩写 | 物理场景 |
|---|---|---|
| Channel Flow | `cf` | 渠道流动 |
| Couette Flow | `cw` | 库埃特流动 |
| Dam Break | `db` | 溃坝 |
| Heat Transfer | `ht` | 热传导（热底壁渠道流） |
| Lid-Driven Cavity | `ldc` | 顶盖驱动方腔 |
| Poiseuille Flow | `pf` | 泊肃叶流动 |
| Rotating Poiseuille | `rpf` | 旋转泊肃叶 |
| Taylor-Green Vortex | `tgv` | 泰勒-格林涡 |
| Unknown | `ut` | 待确认 |

### 3.2 测试框架（`tests/`）

- `test_cf2d.py` — 2D 渠道流测试
- `test_kernel.py` — 核函数单元测试
- `test_neighbors.py` — 邻居搜索性能对比
- `test_pf2d.py` — 2D 泊肃叶流测试

### 3.3 量化验证数据（`validation/ref/`）

| 参考文件 | 对标算例 |
|---|---|
| `Buchner_pressureProfile.csv` | 渠道流压力分布 |
| `dambreak_data.py` | 溃坝实验数据 |
| `ldc_data.py`, `ldc_data_u_vel.csv`, `ldc_data_v_vel.csv` | LDC 速度剖面 |
| `poiseuille_data.py` | 泊肃叶解析解 |
| `rpf_t_*.csv` | 旋转泊肃叶时间序列 |
| `tgv3d_ref_*.txt` | 3D TGV 参考解 |
| `tgv_analytical_sol.py` | TGV 解析解 |

### 3.4 CI/CD

- `.github/workflows/tests.yml` — 测试流水线
- `.github/workflows/ruff.yml` — 代码风格检查
- `.github/workflows/publish.yml` — PyPI 发布
- `.codecov.yml` — 代码覆盖率
- `.readthedocs.yaml` — 文档构建

### 3.5 ICLR 2024 相关 Notebook

- `iclr24_grads.ipynb` — 梯度验证（AD vs 有限差分）
- `iclr24_inverse.ipynb` — 反问题求解（100 步 SPH 初始状态反演）
- `iclr24_sitl.ipynb` — Solver-in-the-Loop 训练（LagrangeBench 库）

---

## 四、对 TensorPhysics SPH 线的可迁移方案

### 方案 1：δ-SPH 可微耗散模块（adapt）

- **GAP 锚**：`GAP-SPH-DELTA-SPH`
- **分级**：adapt（需适配 TensorSPH 架构）
- **License**：MIT ✅ 合规
- **内容**：`jax_sph/solver.py:rho_evol_fn_delta_wrapper` 实现了 Marrone et al. 2011 的 δ-SPH 密度扩散项，且完全 JAX 可微。可直接移植为 TensorSPH 的可微耗散模块，用于自由面精度提升和数值耗散量化研究。

### 方案 2：Riemann SPH 参考实现（reference）

- **GAP 锚**：`GAP-SPH-WCSPH-VALIDATION`
- **分级**：reference（参考实现，不直接移植）
- **License**：MIT ✅ 合规
- **内容**：`jax_sph/solver.py:rho_evol_riemann_fn_wrapper` 实现了 Zhang et al. 2017 的线性 Riemann 求解器。可作为 TensorSPH Riemann SPH 验证面的参考实现，对标压力振荡抑制效果。

### 方案 3：可微 SPH 梯度验证管线（adapt）

- **GAP 锚**：`GAP-SPH-DIFFERENTIABLE`
- **分级**：adapt（需适配 TensorPhysics 状态机）
- **License**：MIT ✅ 合规
- **内容**：`notebooks/iclr24_grads.ipynb` 提供了完整的 AD vs 有限差分梯度验证流程。可适配为 TensorPhysics 可微求解器线的标准验证面，用于黏度辨识、边界重构等反问题收敛性测试。

### 方案 4：多核函数库（fit）

- **GAP 锚**：`GAP-SPH-WCSPH-VALIDATION`
- **分级**：fit（几乎可直接复用）
- **License**：MIT ✅ 合规
- **内容**：`jax_sph/kernel.py` 包含 7 种核函数的 JAX 实现（Cubic/Quintic/WendlandC2/C4/C6/Gaussian/SuperGaussian），支持 1D/2D/3D 归一化。可直接复用到 TensorSPH 作为核函数库。

### 方案 5：验证面数据集（reference）

- **GAP 锚**：`GAP-SPH-WCSPH-VALIDATION`
- **分级**：reference（数据引用）
- **License**：MIT ✅ 合规
- **内容**：`validation/ref/` 包含 dam-break、LDC、Poiseuille、TGV 的参考数据（实验/解析解）。可作为 TensorSPH 精度对标的基准数据集。

---

## 五、注意点（踩坑/局限/适用范围）

### 5.1 网络限制

- GitHub API 速率限制（未认证 10 req/min），部分查询通过 HTML 解析获取
- 部分文件内容通过 GitHub blob API 获取，受速率限制影响
- **诚实声明**：stars 数来自 m8638 普查（84），本次未实时获取；last_commit 来自 m8638（2025-10-29）

### 5.2 方法局限

- ❌ **无 ISPH/PCISPH/DFSPH/IISPH**：仅 WCSPH/δ-SPH/Riemann SPH，不可压 SPH 家族缺失
- ❌ **无多相模型**：无颜色梯度/多流体/相变能力
- ❌ **无 FSI**：无流固耦合实现
- ⚠️ **邻居搜索**：依赖 jax_md，非自研；matscipy 后端需额外安装
- ⚠️ **核函数**：默认 QSK/WC2K，其他核函数在 check_cfg 中未启用（断言限制）

### 5.3 适用范围

- ✅ 适合：可微 SPH 研究、反问题优化、ML 耦合、单相自由面流动
- ⚠️ 不适合：多相流、FSI、高 Re 湍流、工业级大规模模拟（粒子数受限 JAX 内存）

---

## 六、误差判定

本单为代码审计（非精度验证），无计算结果需对标基准。

**诚实声明**：
- ✅ 完成：GitHub API 实查（目录结构/文件内容/CI）、源码级方法清单、验证面盘点、5 条可迁移方案
- ⚠️ 部分完成：stars/last_commit 来自 m8638 缓存（GitHub API 速率限制）
- ⚠️ 未覆盖：未运行实际测试（需 JAX 环境）；未逐行审计所有源文件

---

## 机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"tumaer/jax-sph","tier":2,"methods":["WCSPH(Adami2013)","δ-SPH(Marrone2011)","RiemannSPH(Zhang2017)","TVF"],"boundary":"GWBC(Zhang2017)+3wallLayers+freeSlip+RiemannVelBC","gpu":"JAX+CUDA(jax[cuda12])+vmap/scan+jax_md","validation":"9cases(cf/cw/db/ht/ldc/pf/rpf/tgv/ut)+4tests+6valScripts+refData","license":"MIT","transferable":["δ-SPH(adapt/GAP-SPH-DELTA-SPH)","RiemannSPH(ref/GAP-SPH-WCSPH-VALIDATION)","AD-grads(adapt/GAP-SPH-DIFFERENTIABLE)","kernels(fit/GAP-SPH-WCSPH-VALIDATION)","valData(ref/GAP-SPH-WCSPH-VALIDATION)"]}[/OSS-INV]

[OSS-SOL]{"repo":"tumaer/jax-sph","id":"SPHSOL-jaxsph-01","kind":"adapt","summary":"δ-SPH density diffusion (Marrone et al. 2011) — JAX-differentiable, portable as differentiable dissipation module for TensorSPH","gap":"GAP-SPH-DELTA-SPH"}[/OSS-SOL]

[OSS-SOL]{"repo":"tumaer/jax-sph","id":"SPHSOL-jaxsph-02","kind":"reference","summary":"Riemann SPH linear solver (Zhang et al. 2017) — reference implementation for TensorSPH Riemann flux SPH validation","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"tumaer/jax-sph","id":"SPHSOL-jaxsph-03","kind":"adapt","summary":"JAX AD + SPH gradient validation pipeline (iclr24_grads.ipynb) — differentiable SPH inverse problem framework for TensorPhysics","gap":"GAP-SPH-DIFFERENTIABLE"}[/OSS-SOL]

[OSS-SOL]{"repo":"tumaer/jax-sph","id":"SPHSOL-jaxsph-04","kind":"fit","summary":"Multi-kernel library (Cubic/Quintic/WendlandC2/C4/C6/Gaussian/SuperGaussian) — 7 kernels in JAX, directly reusable","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"tumaer/jax-sph","id":"SPHSOL-jaxsph-05","kind":"reference","summary":"Validation dataset (dam-break/LDC/Poiseuille/TGV reference data) — quantitative benchmark for TensorSPH accuracy对标","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
