# 【盘点】hietwll/LBM_Taichi — 开源 LBM 技术深盘

**仓库**: https://github.com/hietwll/LBM_Taichi  
**盘点日期**: 2026-11-09  
**盘点人**: agent/m8295  
**Tier**: 2（Taichi GPU LBM 教学级实现）

---

## 一、概况

| 属性 | 值 |
|------|-----|
| **语言** | Python (Taichi) |
| **代码量** | 1 文件 185 行 (`lbm_solver.py`) |
| **许可证** | **无 LICENSE 文件**（GitHub 页面亦未标注；默认 all-rights-reserved，**不可直接复用**） |
| **活跃度** | 极低：仅 1 次 commit（2023-06-18 "Upgrade to taichi 1.6.0"），无 issue/PR |
| **Stars/Forks** | GitHub API 受限无法获取（安全策略拦截），估计 <50 |
| **定位** | **教学演示级** D2Q9-BGK 单相 LBM 求解器，~200 行实现顶盖驱动腔 + Karman 涡街 |

**许可证可用性判断**: ❌ **不可直接吸收代码**。无 SPDX 标识，按版权法默认保留所有权利。仅可提取**思路/算法模式**作为参考实现，不可 copy-paste 进 TensorLBM。

---

## 二、方法谱系

### 2.1 格子模型

| 格子 | 实现位置 | 说明 |
|------|----------|------|
| **D2Q9** | `lbm_solver.py:41-42` | 唯一支持的格子。离散速度 `e[k]` 和权重 `w[k]` 硬编码为 `ti.types.vector(9,float)` / `ti.types.matrix(9,2,int)` |

**无 D3Q19/D3Q27 支持**。纯二维。

### 2.2 碰撞模型

| 模型 | 实现位置 | 说明 |
|------|----------|------|
| **BGK (SRT)** | `lbm_solver.py:71` | `f_new = (1-inv_tau)*f_old + feq*inv_tau`，单松弛时间 `tau = 3*niu + 0.5`（`:35`） |

**无 TRT/MRT/KBC/RR/Cascaded**。仅 BGK 单松弛。

### 2.3 平衡态分布

| 项目 | 位置 | 公式 |
|------|------|------|
| `f_eq` | `lbm_solver.py:48-51` | `w*ρ*(1 + 3(e·u) + 4.5(e·u)² - 1.5|u|²)` — 标准 D2Q9 二阶展开 |

### 2.4 力项

**无**。未实现任何外力项（Guo / Shan-Chen / 体积力）。不可用于多相流或含体力问题。

### 2.5 宏观量更新

| 项目 | 位置 |
|------|------|
| 密度 ρ = Σf | `lbm_solver.py:80` |
| 速度 u = Σ(e·f)/ρ | `lbm_solver.py:81-83` |

标准无修正宏观量（无力项速度修正）。

---

## 三、边界处理

### 3.1 外边界

| 边界类型 | 实现位置 | 方法 |
|----------|----------|------|
| **Dirichlet (速度给定)** | `lbm_solver.py:123-124` | `vel[ibc] = bc_value[dr]`，直接赋值 |
| **Neumann (零梯度)** | `lbm_solver.py:126-127` | `vel[ibc] = vel[inb]`，复制内侧节点速度 |
| **密度外推** | `lbm_solver.py:129` | `rho[ibc] = rho[inb]` |
| **分布函数外推** | `lbm_solver.py:130` | `f_old[ibc] = f_eq(ibc) - f_eq(inb) + f_old[inb]` — **非标准 Zou-He**，是平衡态差值外推（类似 Neumann 对 f） |

**注意**: 这不是经典的 Zou-He 压力/速度边界（基于未知分布函数解析求解），而是一种**简单的平衡态外推**方法。精度为一阶。

### 3.2 固体边界（圆柱障碍物）

| 项目 | 位置 | 方法 |
|------|------|------|
| 圆柱掩码 | `lbm_solver.py:60-62` | 圆形区域 `mask=1` |
| 固体处理 | `lbm_solver.py:106-118` | 速度归零 + 从邻近流体节点外推 f（**非标准 bounce-back**，是简化 IBM 风格处理） |

**非真正 IBM**（无力计算/扩散），非标准 bounce-back（无镜面反射），是**速度归零+平衡态外推**的简化处理。

### 3.3 缺失的边界方法

- ❌ 标准 half-way bounce-back
- ❌ Zou-He 压力/速度边界
- ❌ 曲率适配边界
- ❌ 移动边界
- ❌ 周期性边界

---

## 四、多相/多组元

### **⚠️ 无。本仓完全没有多相/多组元实现。**

逐项确认：

| 方法 | 状态 | 证据 |
|------|------|------|
| Shan-Chen (SCMP) | ❌ 无 | 全仓无伪势/互作用势代码 |
| MCMP (多分量) | ❌ 无 | 仅单密度场 `rho`（`:31`） |
| Free-energy | ❌ 无 | 无化学势/序参量方程 |
| Phase-field (Allen-Cahn/Cahn-Hilliard) | ❌ 无 | 无相场变量 |
| 颜色梯度 (Gunstleasen-Spalding) | ❌ 无 | 无颜色场/重分布算子 |
| Free-surface | ❌ 无 | 无流体分数函数 |
| 密度比能力 | N/A | 单相，ρ 恒≈1 |
| Spurious current 处理 | N/A | 无多相界面 |

**结论**: 本仓对多相流功能点**零覆盖**。标题中"多相/SPH 杂交"的描述与实际代码不符——代码仅为单相不可压 LBM。

---

## 五、GPU / 并行

### 5.1 GPU 后端

| 项目 | 位置 | 说明 |
|------|------|------|
| Taichi 初始化 | `lbm_solver.py:12` | `ti.init(arch=ti.gpu)` — 自动选择 CUDA/Metal/Vulkan |
| Kernel 标注 | `:64,73,85` | `@ti.kernel` 装饰器，Taichi JIT 编译为 GPU 代码 |
| 设备函数 | `:47,120` | `@ti.func` 用于内联函数（f_eq, apply_bc_core） |

### 5.2 数据布局

| 项目 | 位置 | 布局 |
|------|------|------|
| 分布函数 | `:37-38` | `ti.Vector.field(9, float, shape=(nx,ny))` — **AoS**（每节点 9 分量向量） |
| 速度 | `:32` | `ti.Vector.field(2, float, shape=(nx,ny))` — AoS |
| 密度 | `:31` | `ti.field(float, shape=(nx,ny))` — 标量场 |

**AoS 布局**：Taichi 内部可能优化为 AoS→SoA 转换（取决于后端），但用户代码层面是 AoS。

### 5.3 并行特性

| 特性 | 状态 |
|------|------|
| 单 GPU | ✅ Taichi kernel 自动并行 |
| 多 GPU / MPI | ❌ 无 |
| 通信隐藏 | N/A（单节点） |
| 内存合并 | Taichi 运行时处理，用户未显式控制 |
| 稀疏/自适应 | ❌ 无（均匀网格） |

### 5.4 时间推进

`lbm_solver.py:136-139`: 每帧 10 步 collide-stream → update_macro → apply_bc，**双缓冲** `f_old`/`f_new`（`:37-38`），stream 后交换（`:79`）。

---

## 六、可微 / 代码生成

| 特性 | 状态 | 说明 |
|------|------|------|
| Taichi autograd | ⚠️ 可用但未用 | Taichi 支持 `ti.ad`，但本仓未使用任何可微功能 |
| 符号推导 | ❌ 无 | 所有公式硬编码 |
| Kernel 生成 | ✅ Taichi JIT | Taichi 自动将 `@ti.kernel` 编译为 CUDA/Metal/GLSL |
| 代码模板 | ❌ 无 | 无格子/碰撞参数化生成 |

**可微友好度**: 低。虽然 Taichi 框架支持 autograd，但本仓代码结构（显式双缓冲、非可微 BC 处理）不适合直接做可微 LBM。

---

## 七、与功能点映射

| feature_ledger slug | 本仓覆盖 | 证据 |
|---------------------|----------|------|
| **GAP-LBM-MCMP-POISEUILLE** | ❌ 无 | 无多组元、无 Poiseuille 算例 |
| **MCMP 高密度比** | ❌ 无 | 单相 |
| **自由面** | ❌ 无 | 无 |
| **相场 Allen-Cahn** | ❌ 无 | 无 |
| **GPU 融合 kernel** | ⚠️ 部分 | Taichi 自动融合，但用户未显式控制（`:64-71` collide+stream 合一） |
| **分布式多卡** | ❌ 无 | 无 MPI/多 GPU |
| **稀疏/多孔** | ❌ 无 | 均匀网格，仅简单圆柱掩码 |
| **案例组织** | ⚠️ 极简 | 2 个算例（顶盖腔 + Karman），命令行切换（`:162-185`） |
| **测试与验证面** | ⚠️ 极简 | Ghia 1982 参考数据（`data/ghia1982.dat`），但无自动化验证脚本 |
| **CI** | ❌ 无 | 无 GitHub Actions / 测试框架 |

**NEW 提案**:
- `NEW:taichi-jit-gpu-lbm` — Taichi JIT 编译 LBM kernel 到多后端的模式（参考架构）
- `NEW:equilibrium-extrapolation-bc` — 平衡态差值外推边界（`:130`，简单但非高精度）

---

## 八、可吸收 Top3 + 风险

### Top 1: Taichi GPU LBM 极简架构模式（参考）

**可吸收点**: 185 行实现完整 D2Q9-BGK 求解器的代码组织模式——`@ti.data_oriented` 类封装、`@ti.kernel` 计算、`@ti.func` 内联、双缓冲时间推进。

**对我们的价值**: TensorLBM 若考虑 Taichi 后端或教学演示模块，可参考此极简架构。

**风险**: 无许可证，不可直接复制代码；仅可参考架构思路。

### Top 2: 平衡态外推边界模式

**可吸收点**: `f_old[ibc] = f_eq(ibc) - f_eq(inb) + f_old[inb]`（`:130`）——一种简单的一阶外推边界，实现成本极低。

**对我们的价值**: 可作为 TensorLBM 快速原型/教学模块的简单边界选项。

**风险**: 精度仅一阶，不适合高精度需求；非标准方法，文献支持少。

### Top 3: Ghia 1982 验证数据格式

**可吸收点**: `data/ghia1982.dat` 的纯文本格式（18 行，12 列，含多 Re 数中心线速度）。

**对我们的价值**: TensorLBM 验证面可直接复用此数据格式作为顶盖腔基准。

**风险**: 数据本身无版权问题（公开发表的数值数据），但需注意来源标注。

---

## 总结

**hietwll/LBM_Taichi 是一个教学演示级的 D2Q9-BGK 单相 LBM 求解器**，~200 行 Taichi 代码实现顶盖驱动腔和 Karman 涡街两个经典算例。

**核心发现**:
1. **无多相/多组元**：标题声称的"多相/SPH 杂交"与实际代码严重不符
2. **无高级碰撞模型**：仅 BGK，无 TRT/MRT/KBC
3. **边界处理简化**：非标准 Zou-He / bounce-back，是平衡态外推
4. **无许可证**：不可直接复用代码
5. **GPU 后端**：依赖 Taichi JIT，用户层面无显式控制

**对我们的价值**: ⚠️ **极低**。仅可作为 Taichi LBM 架构参考和教学示例，无可直接吸收的多相/高性能/高级边界技术。

---

## 机器块

```
[OSS-INV]
repo: hietwll/LBM_Taichi
lang: Python (Taichi)
license: 无（all-rights-reserved，不可直接复用）
methods: D2Q9×BGK
multiphase: 无
boundary: equilibrium-extrapolation(lbm_solver.py:130); velocity-reset-IBM-style(lbm_solver.py:106-118)
gpu: Taichi-JIT(arch=ti.gpu, lbm_solver.py:12); AoS-Vector.field(9)
summary: 教学级 D2Q9-BGK 单相 LBM，185 行 Taichi 代码；无多相/高级碰撞/标准边界；无许可证不可直接复用
[/OSS-INV]
```

```
[OSS-SOL]
repo: hietwll/LBM_Taichi
slug: NEW:taichi-jit-gpu-lbm
tag: taichi-jit-single-phase-lgk
method: Taichi @ti.kernel 自动 JIT 编译 D2Q9-BGK collide-stream 到 GPU；双缓冲 f_old/f_new；AoS Vector.field(9) 布局
files: lbm_solver.py:12, lbm_solver.py:64-71
conventions: dx=dy=dt=1.0 (lattice units); tau=3*niu+0.5; Re=U*L/niu
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: hietwll/LBM_Taichi
slug: NEW:equilibrium-extrapolation-bc
tag: equilibrium-difference-extrapolation
method: 边界 f = f_eq(boundary) - f_eq(inner) + f_old(inner)；一阶外推，非标准 Zou-He
files: lbm_solver.py:130
conventions: 密度从内侧复制(:129)；速度按 bc_type 指定 Dirichlet/Neumann
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: hietwll/LBM_Taichi
slug: NEW:ghia-1982-cavity-validation-data
tag: ghia-1982-lid-cavity-reference
method: 纯文本 18×12 矩阵：y/u(Re=[100..10000])/x/v(Re=[100..10000])；顶盖腔中心线速度基准
files: data/ghia1982.dat
conventions: 无量纲坐标 y,x∈[0,1]；速度无量纲化
transfer: fits
[/OSS-SOL]
```
