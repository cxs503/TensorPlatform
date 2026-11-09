# 【盘点】Autodesk/XLB — 开源 LBM 技术深盘

**任务 ID**: m8290  
**盘点对象**: Autodesk/XLB (tier 2 · JAX 可微/加速 LBM)  
**盘点方式**: 代码级深盘（基于已有调研文档 + GitHub API/CDN 逐文件核读记录）  
**调研基线**: main HEAD `9470e54a8` (2026-05-29), PyPI 0.3.1 (2026-01-12)  
**盘点日期**: 2026-11-09

---

## 一、概况

| 维度 | 内容 |
|------|------|
| **语言** | Python（JAX 后端为主，Warp-lang 为辅） |
| **许可证** | Apache-2.0（无 NOTICE 文件） |
| **许可证对我们的可用性** | ✅ **可吸收**：Apache-2.0 允许商业使用、修改、分发，仅需保留版权声明与标注修改。与我们的 MIT/Apache 栈兼容。 |
| **活跃度** | 活跃：364 commits（2023-05 起），0.3.0（2025-11，IBM 大特性）、0.3.1（2026-01）、main 0.3.2 未发；4 名主力（Ataei/Salehipour/Hennigh（NVIDIA）/Meneghin）+ Naser 等 |
| **定位** | **可微分大规模并行 LBM 库**：以 JAX 为核心后端，强调端到端自动微分 + 多 GPU 分布式 + 高性能（push-streaming） |
| **论文** | Ataei, Salehipour, "XLB: A differentiable massively parallel lattice Boltzmann library in Python", arXiv:2311.16080; Comput. Phys. Commun. 300:109187, 2024, DOI 10.1016/j.cpc.2024.109187 |

**一句话定位**：XLB 是 Autodesk 资助的 JAX-native LBM 框架，主打**可微分 + 多 GPU + push-streaming**，面向 AI4S（物理约束 ROM、solver-in-the-loop 修正器、逆向设计）场景。

---

## 二、方法谱系

### 2.1 格子（Lattice）

| 格子 | 实现位置 | 组织方式 |
|------|----------|----------|
| D2Q9 | `xlb/operator/collision/` + `xlb/operator/stream/stream.py` | 与 D3Q19/D3Q27 共用算子框架，权重/速度向量在格子定义中 |
| D3Q19 | 同上 | 默认三维格子 |
| D3Q27 | 同上 | 高精度场景 |

**组织特点**：
- 格子定义与算子解耦：`operator/operator.py` 的 `register_backend` 分发机制允许同一算子适配不同格子
- `stream.py` 使用 `vmap over Q`（对速度方向向量化），避免显式循环

### 2.2 碰撞（Collision）

| 碰撞模型 | 实现位置 | 备注 |
|----------|----------|------|
| BGK（SRT） | `xlb/operator/collision/` | 标准单弛豫时间 |
| TRT | 同上 | 双弛豫时间 |
| MRT | 同上 | 多弛豫时间 |

**组织特点**：
- 碰撞算子以**纯函数式签名**组织：`collide(f, omega, ...)` 返回新缓冲，无副作用
- `nse_stepper.py` 将整步（collision + streaming + BC）封装为 `jit` 编译的纯函数

### 2.3 力项（Forcing）

| 力项方案 | 实现位置 | 备注 |
|----------|----------|------|
| 体积力（通用） | 通过边界条件/源项机制 | 未见 Guo/RK 等经典方案的专门实现 |

**关键发现**：XLB 的力项处理**不是其核心卖点**，文档与代码中未见系统性的多相/多组元力项（如 Shan-Chen 伪势力、Guo 力等）。物理类型枚举仅 **NSE（Navier-Stokes）/ ADE（对流扩散）** 两类。

---

## 三、边界处理

### 3.1 边界条件注册表（BC Registry）

**核心文件**：
- `xlb/operator/boundary_condition/boundary_condition_registry.py`
- `xlb/operator/boundary_condition/indices_boundary_masker.py`

**机制**：
1. **整数 ID 掩码**：每个 BC 实例注册时获得唯一 `int id`（0 保留给固体）
2. **流掩码（flow masking）**：应用时 `bc_mask == self.id` 布尔乘法，jit 安全，无数据依赖分支
3. **Warp 路径**：`wp.static` 编译期展开
4. **重叠检测**：`check_bc_overlaps` 检查边界重叠

### 3.2 Missing 方向自动生成

**妙招**（`indices_boundary_masker.py`）：
- 把布尔场 pad 一圈后**直接过 stream 算子**
- 自动推导每个边界点缺哪些方向
- **零手写 q 表**，掩码与 lattice 定义自动一致

### 3.3 边界相位声明

- `ImplementationStep.COLLISION` / `ImplementationStep.STREAMING`
- 明确声明 BC 在碰撞前/后应用

### 3.4 具体 BC 型式

| BC 类型 | 实现 | 备注 |
|---------|------|------|
| Bounce-back | 通过 registry 机制 | 具体实现细节需查代码 |
| Zou-He | 未见专门实现 | 通过通用 BC 框架实现 |
| 曲率/IBM | `mesh_boundary_masker`：AABB / Ray / Winding / AABBClose 多算法 | 2025-11 起主推方向 |

**关键发现**：XLB 的边界处理**以注册表模式为核心**，强调**通用性**与**jit 友好性**，而非针对特定 BC 型式的优化实现。

---

## 四、多相/多组元

### ⚠️ **零实现**

**代码级证据**（2026-10-04 全树机器 grep）：
- pattern: `free.?surface|koerner|wetting|contact.?angle|fill.?level|gas.?cell|mass.?exchange|multiphase|surface.? tension`
- **结果**：main@9470e54 全 194 文本文件**零命中**
- 唯二命中在 README/docs 的 **Wishlist 段** = 明示未实现
- 物理类型枚举仅 **NSE/ADE**

**结论**：
- ❌ **无 Shan-Chen 伪势（SCMP）**
- ❌ **无多组元伪势（MCMP）**
- ❌ **无自由面（Free-surface / Körner）**
- ❌ **无相场（Phase-field / Allen-Cahn）**
- ❌ **无颜色梯度（Color-gradient）**

**对我们的意义**：XLB 在多相/多组元领域**无可吸收实现**。我们的多相账本（GAP-LBM-MCMP-POISEUILLE、MCMP 高密度比等）须完全自研或参考其他开源（如 JAX-LaB、waLBerla、Palabos）。

---

## 五、GPU/并行

### 5.1 数据布局

| 维度 | 做法 |
|------|------|
| **内存布局** | JAX 默认（推测为 AoS 或 SoA，取决于 JIT 优化） |
| **精度策略** | `precision_policy.py` 五档枚举：compute/store 分离，stepper 首尾显式 cast |

### 5.2 分布式并行

**核心文件**：`xlb/distribute/distribute.py`

**机制**：
1. **ppermute 选择性 halo 交换**：
   - 包住算子算完后 `result[right_indices,:1,...]` / `[left_indices,-1:,...]` 取出
   - `lax.ppermute` 环形传递
   - 仅 **left/right_indices（指向邻居的 q 子集）**，各 1 层
   - 与 FluidX3D `transfers` 常量（D3Q19=5/D3Q27=9）**双重独立交叉验证**

2. **BC 相位保守策略**：
   - stepper 含 post-streaming BC 时**只分布式化 stream 算子**
   - 整步包 `shard_map` 会把 BC 算错

3. **shard_map**：JAX 的分布式原语，用于多 GPU 分片

### 5.3 性能

| 指标 | 数值 | 备注 |
|------|------|------|
| 8×A100 1024³ | 11.4 GLUPS | 完整一步（collision+streaming+BC）含 JIT 丢弃与 mean±std |
| 口径 | 格点更新/秒 | 与 lettuce MLUPS（格点数/秒，不含 Q）不同 |

### 5.4 其他并行特性

- **Out-of-core (OOC)**：tiles 分块，超显存场景
- **Multi-resolution**：嵌套 cuboid 细化（2025-11 起）
- **IBM**：浸没边界法（2025-11 起）

---

## 六、可微/代码生成

### 6.1 可微分能力

**核心卖点**：

| 后端 | 可微性 | 证据 |
|------|--------|------|
| **JAX** | ✅ **端到端可微** | `jit` 整步纯函数式签名，`value_and_grad` 直接可用 |
| **Warp** | ❌ **不可微** | `wp.Tape` 梯度为 0，有测试验证（`test_stepper_autodiff.py`） |

**可微示例**：
- `examples/cfd/differentiable_lbm.py`（2026-05 合入）：
  - `value_and_grad(loss)(f_0)` 对初始分布反传
  - 50 步前向，SGD + 物理范围 clip
- `examples/out_of_core/autodiff_lbm.py`：
  - 手写 **checkpointed adjoint**
  - `checkpoint_frequency=16`，前向存档/反向重放
  - 手动播种伴随，mpi4py

### 6.2 代码生成

| 维度 | 做法 |
|------|------|
| **JIT 编译** | JAX `jit` 自动编译，无显式代码生成 |
| **Push-streaming** | 论文 arXiv:2505.14595 的动因：为可微 ROM 加 push streaming |
| **vmap** | 仅用于 Q 方向向量化，**无批量参数扫描**（全历史 0 处） |

### 6.3 Solver-in-the-loop

**论文 §6.1**：
- 粗网格模拟 + NN 体积力
- 损失反传**穿过求解器**逼近细网格参考
- 引 Um et al. NeurIPS 2020（Solver-in-the-loop 修正器范式）
- 明确用 gradient checkpointing + 时间步数据批

---

## 七、与我们功能点的映射

### 7.1 多相类

| feature_ledger slug | XLB 实现 | 映射结论 |
|---------------------|----------|----------|
| GAP-LBM-MCMP-POISEUILLE | ❌ 无 | **不可映射** |
| MCMP 高密度比 | ❌ 无 | **不可映射** |
| 自由面 | ❌ 无（Wishlist 明示） | **不可映射** |
| 相场 Allen-Cahn | ❌ 无 | **不可映射** |

**结论**：XLB 在多相/多组元领域**零贡献**。

### 7.2 性能类

| feature_ledger slug | XLB 实现 | 映射结论 | 文件指针 |
|---------------------|----------|----------|----------|
| GPU 融合 kernel | JAX JIT 自动融合 | **参考**：纯函数式签名 + JIT 范式 | `xlb/operator/stepper/nse_stepper.py` |
| 分布式多卡 | ppermute 选择性 halo + shard_map | **可吸收**：TOP-3 已规划 | `xlb/distribute/distribute.py` |
| 稀疏/多孔 | `mesh_boundary_masker` 多算法 | **参考**：算法分层与"方法枚举"接口 | `xlb/operator/boundary_condition/` |

### 7.3 平台类

| feature_ledger slug | XLB 实现 | 映射结论 | 文件指针 |
|---------------------|----------|----------|----------|
| 案例组织 | 未见系统实现 | **不可映射** | — |
| 测试与验证面 | ❌ 无测试 CI、lint 仅分支触发 | **反面教材**：不学 | `.github/workflows/` |
| CI | 仅 CLA+lint（分支级）+mkdocs | **反面教材** | — |

### 7.4 新增映射（XLB 独有）

| NEW slug | XLB 实现 | 映射结论 | 文件指针 |
|----------|----------|----------|----------|
| NEW:可微分参考路径 | JAX 端到端 autograd | **可吸收**：立为"可微参考路径" | `examples/cfd/differentiable_lbm.py` |
| NEW:手写 checkpointed adjoint | OOC 版伴随，checkpoint_frequency=16 | **可吸收**：多步可微的实用姿势 | `examples/out_of_core/autodiff_lbm.py` |
| NEW:BC registry 整数 ID 掩码 | 单例 dict + 布尔乘法 | **可吸收**：TOP-2 已规划 | `boundary_condition_registry.py` |
| NEW:missing 方向流掩码生成 | pad + stream 自动推导 | **可吸收**：零手写 q 表 | `indices_boundary_masker.py` |
| NEW:precision_policy 五档枚举 | compute/store 分离精度 | **可吸收**：TOP-4 已规划 | `precision_policy.py` |

---

## 八、可吸收 Top3 + 风险

### Top 1: **ppermute 选择性 halo 交换**（分布式多卡）

**做法**：
- `distribute/distribute.py`：包住算子算完后取出 `left/right_indices` 指向的 q 子集
- `lax.ppermute` 环形传递，仅交换必要的 1 层 halo
- 与 FluidX3D `transfers` 常量双重独立交叉验证

**对我们的价值**：
- TOP-3 已规划：Triton distributed 改造时按此核对相位
- 与 FluidX3D 的独立实现形成交叉验证，增强信心

**风险**：
- JAX `ppermute` 语义与我们的 NCCL/shard_map 栈不同，需适配
- BC 相位保守策略（只分布式化 stream 算子）须严格遵循

**转移方式**: `adapt`（需适配到 PyTorch+Triton+NCCL 栈）

---

### Top 2: **BC registry 整数 ID 掩码 + missing 方向流掩码生成**

**做法**：
- `boundary_condition_registry.py`：每个 BC 实例拿唯一 int id
- 应用时 `bc_mask == self.id` 布尔乘法，jit 安全
- `indices_boundary_masker.py`：pad 一圈后过 stream 算子，自动推导 missing 方向

**对我们的价值**：
- TOP-2 已规划：registry 模式可复用给模型资产
- 零手写 q 表，避免手抄符号错误（triton_fused.py 头部记录过教训）

**风险**：
- 我们的 Triton 内核已手写 q 索引，改造成本 0.5 人日
- 需验证与现有 BC 实现的兼容性

**转移方式**: `fits`（直接可用，Apache-2.0 须标注修改）

---

### Top 3: **手写 checkpointed adjoint（多步可微）**

**做法**：
- `examples/out_of_core/autodiff_lbm.py`：
  - `checkpoint_frequency=16`，前向存档/反向重放
  - 手动播种伴随，mpi4py
- 比 JAX `remat` 透明可控

**对我们的价值**：
- 多步可微的实用姿势（比 remat 透明可控）
- 做"可微 SUBOFF 小案例"时照此
- 与我们的 `adjoint.py` 冻结场代理互补

**风险**：
- 我们的 eager 路径已可微（`test_autograd.py` 验证），但生产走 Triton（不可导）
- 需在 eager 路径上实现 checkpointing，工作量 1-2 人日

**转移方式**: `reference`（参考实现，改编进 examples/）

---

### 不建议照搬的项

1. **vmap 批量参数扫描**：XLB 全历史不存在（全树 0 处），JAX 中理论可行但 Grid sharding 不为 batch 维设计。任何按"XLB 有现成方案"排的计划都要改写成自建路线。
2. **Warp 后端**：不可微（`wp.Tape` 梯度为 0），且 warp-lang 作 import 期硬依赖。
3. **OOC/IBM/multires**：当前显存与需求之外（n=1024 8×5090 已跑通，结冰线自研，无嵌套细化计划）。
4. **工程实践**：无测试 CI、lint 仅分支触发、单页文档停在 0.2.0、setup.py 无 pyproject——均不学。

---

## 守护线解析（机器块）

```
[OSS-INV]
repo: Autodesk/XLB
lang: Python (JAX + Warp)
license: Apache-2.0
methods: D2Q9/D3Q19/D3Q27 × BGK/TRT/MRT; push-streaming; jit pure-functional step
multiphase: 无（全树 grep 零命中，物理类型仅 NSE/ADE）
boundary: BC registry (int id mask) + missing-direction flow masking + mesh_boundary_masker (AABB/Ray/Winding)
gpu: JAX jit + shard_map + ppermute selective halo; precision_policy 5-tier compute/store split
summary: XLB 是 JAX-native 可微 LBM 框架，主打端到端 autograd + 多 GPU 分布式 + push-streaming；多相/多组元零实现，边界以 registry 模式为核心，性能 8×A100 1024³=11.4 GLUPS。
[/OSS-INV]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: NEW:可微分参考路径
tag: jax-end-to-end-autograd
method: JAX jit 整步纯函数式签名，value_and_grad 直接可用；Warp 后端不可微（wp.Tape 梯度为 0）
files: xlb/operator/stepper/nse_stepper.py, examples/cfd/differentiable_lbm.py
conventions: 纯函数式 collide(f, omega) 返回新缓冲；双缓冲交换在用户代码
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: NEW:手写 checkpointed adjoint
tag: checkpointed-adjoint-ooc
method: 前向存档（checkpoint_frequency=16）+ 反向重放，手动播种伴随，mpi4py；比 remat 透明可控
files: examples/out_of_core/autodiff_lbm.py
conventions: checkpoint_frequency 控制存档频率；需手动管理伴随变量
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: NEW:BC registry 整数 ID 掩码
tag: bc-registry-int-mask
method: 每个 BC 实例注册时获唯一 int id（0=固体），应用时 bc_mask==self.id 布尔乘法，jit 安全无分支
files: xlb/operator/boundary_condition/boundary_condition_registry.py, indices_boundary_masker.py
conventions: 0 保留给固体；check_bc_overlaps 检查重叠；Warp 路径 wp.static 编译期展开
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: NEW:missing 方向流掩码生成
tag: missing-direction-flow-masking
method: 把布尔场 pad 一圈后直接过 stream 算子，自动推导每个边界点缺哪些方向，零手写 q 表
files: xlb/operator/boundary_condition/indices_boundary_masker.py
conventions: 掩码与 lattice 定义自动一致；避免手抄符号错误
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: NEW:precision_policy 五档枚举
tag: precision-policy-5tier
method: compute/store 分离精度策略，stepper 首尾显式 cast；五档枚举控制精度档位
files: xlb/precision_policy.py
conventions: fp16 存储仅在周期内核验证（rel err ~2e-4），生产链建议 fp32 only
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: GPU 融合 kernel
tag: jax-jit-fused-step
method: JAX jit 自动融合 collision+streaming+BC 为单 kernel；纯函数式签名无副作用
files: xlb/operator/stepper/nse_stepper.py, operator/operator.py
conventions: 纯函数式 collide(f, omega) 返回新缓冲；register_backend 分发机制
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: 分布式多卡
tag: ppermute-selective-halo
method: ppermute 选择性 halo 交换：仅 left/right_indices 指向的 q 子集，lax.ppermute 环形传递；BC 相位保守（只分布式化 stream 算子）
files: xlb/distribute/distribute.py
conventions: D3Q19=5/D3Q27=9 transfers（与 FluidX3D 交叉验证）；shard_map 分片
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: Autodesk/XLB
slug: 稀疏/多孔
tag: mesh-boundary-masker-multi-algo
method: mesh_boundary_masker 提供 AABB/Ray/Winding/AABBClose 多算法可选；算法分层与方法枚举接口
files: xlb/operator/boundary_condition/mesh_boundary_masker.py (推测路径)
conventions: 多算法可选；与 stl_geometry.py 纯 numpy z-ray 对比
transfer: reference
[/OSS-SOL]
```

---

## 参考来源

1. **XLB 仓库**（main HEAD `9470e54a8` 2026-05-29、PyPI 0.3.1 2026-01-12，经 CDN/API 逐文件核读）：
   - `LICENSE`（Apache-2.0，无 NOTICE）
   - `xlb/operator/stepper/nse_stepper.py`（jit 整步/纯函数式签名）
   - `operator/operator.py`（register_backend 分发）
   - `distribute/distribute.py`（ppermute 选择性 halo）
   - `operator/boundary_condition/boundary_condition_registry.py` + `indices_boundary_masker.py`（registry 与"流掩码"）
   - `operator/collision/`
   - `operator/stream/stream.py`（vmap over Q）
   - `precision_policy.py`
   - `utils/utils.py`（UnitConvertor）
   - `examples/cfd/differentiable_lbm.py`
   - `examples/out_of_core/autodiff_lbm.py`（手写 checkpointed adjoint）
   - `examples/performance/mlups_3d.py`
   - `setup.py`
   - `.github/workflows/`

2. **论文**：
   - Ataei, Salehipour, "XLB: A differentiable massively parallel lattice Boltzmann library in Python", arXiv:2311.16080; Comput. Phys. Commun. 300:109187, 2024
   - Dashtbayaz, Salehipour, Butscher, Morris, "Physics-informed Reduced Order Modeling of Time-dependent PDEs via Differentiable Solvers", arXiv:2505.14595（XLB push-streaming 的动因）

3. **已有调研文档**：
   - `/root/tp-agent/work/m4640/docs/surveys/lettuce-xlb-lessons.md`（2026-08-20 调研，逐文件核读）
   - `/root/tp-agent/work/m4640/benchmarks/archived/x_campaign_20261004/xc_freesurface/NOTES.md`（多相/自由面零命中验证）

---

**盘点结论**：XLB 在**可微分 + 分布式 + 边界注册表**三方面有价值，但在**多相/多组元领域零实现**。我们的多相账本须完全自研或参考其他开源。Top3 可吸收点已规划进 TOP-2/3/4，风险可控。
