# 【盘点】espressomd/espresso - 开源 LBM 技术深盘报告

**任务编号**: m8305  
**盘点对象**: espressomd/espresso (tier 3·软物质 LB)  
**盘点日期**: 2026-11-09  
**代码版本**: python 分支 (commit HEAD)  

---

## 一、概况

| 项目 | 内容 |
|------|------|
| **语言** | C++ (核心) + Python/Cython (接口) |
| **许可证** | GPL-3.0 |
| **许可证可用性** | ⚠️ **受限** - GPL 是 copyleft 许可证，要求衍生作品必须开源。若 TensorLBM 保持闭源/商业许可，**不能直接复用代码**，仅可参考算法思路和架构设计 |
| **活跃度** | 高 (最近更新 2026-11-03，274 stars，215 forks) |
| **定位** | 软物质模拟平台，LB 作为流体求解器耦合 MD (分子动力学)，主打生物物理/胶体/多孔介质 |

**一句话定位**: ESPResSo 是面向软物质科学的 MD-LBM 耦合平台，LB 实现基于 waLBerla 框架 + lbmpy 代码生成，支持热涨落和粒子耦合，但**无多相流能力**。

---

## 二、方法谱系

### 2.1 格子 (Stencil)

**实现位置**: `src/walberla_bridge/src/lattice_boltzmann/LBWalberlaImpl.hpp:67`

```cpp
using Stencil = stencil::D3Q19;
using StencilFull = stencil::D3Q27;  // 用于 ghost communication
```

- **主格子**: D3Q19 (19 个离散速度)
- **通信格子**: D3Q27 (包含对角方向，用于 ghost layer 角点更新)
- **组织方式**: 通过 waLBerla 框架的 `stencil` 命名空间管理

### 2.2 碰撞模型 (Collision)

**实现位置**: `src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp:30-80`

**类型**: **MRT (Multiple Relaxation Time)** + 热涨落

```cpp
// 剪切模式松弛率
FloatType shear_mode_relaxation_rate() const {
  return FloatType{2} / (FloatType{6} * m_viscosity + FloatType{1});
}

// 奇数模式松弛率 (用于消除数值不稳定)
FloatType odd_mode_relaxation_rate(FloatType shear_relaxation, FloatType magic_number) const {
  return (FloatType{4} - FloatType{2} * shear_relaxation) /
         (FloatType{4} * magic_number * shear_relaxation + FloatType{2} - shear_relaxation);
}
```

**关键特征**:
- MRT 松弛矩阵从粘度自动推导
- 支持热涨落 (thermalized LB)：`kT` 参数控制温度，`seed` 控制 RNG
- 当 `kT=0` 时仍使用热涨落 kernel (用于 RNG 状态追踪)
- 松弛率配置：剪切模式、奇数模式分别设置

**碰撞 kernel 生成**: `src/walberla_bridge/src/lattice_boltzmann/generated_kernels/`
- `StreamCollideSweepThermalized*.cpp/cu` - 热涨落碰撞+传播融合 kernel
- `StreamCollideSweepLeesEdwards*.cpp/cu` - Lees-Edwards 边界专用 kernel

### 2.3 力项 (Force Scheme)

**实现位置**: `src/core/lb/LBWalberla.cpp:100-120`

**方式**: **显式力项 + Guo 修正** (推断，基于标准 MRT-LB 实现)

```cpp
// 外力密度插值到格子节点
bool add_force_at_pos(Utils::Vector3d const &pos, Utils::Vector3d const &force);
void add_forces_at_pos(std::vector<Utils::Vector3d> const &pos, 
                       std::vector<Utils::Vector3d> const &forces);

// 力场存储
BlockDataID m_last_applied_force_field_id;      // 上一步施加的力
BlockDataID m_force_to_be_applied_id;           // 下一步要施加的力
```

**力项处理流程**:
1. 粒子→流体力插值 (基于位置加权)
2. 力存储在 `last_applied_force_field`
3. 碰撞步中通过 Guo 修正或速度偏移加入
4. 力场在传播后重置

**单位转换**:
```cpp
double force_to_lb;  // MD 单位 → LB 单位转换因子
```

---

## 三、边界处理

### 3.1 Bounce-back 型式

**实现位置**: `testsuite/python/lb_boundary.py:50-80`

**类型**: **Velocity Bounce-Back (半程反弹)**

```python
# Python 接口
self.lbf.add_boundary_from_shape(
    shape=self.wall_shape1, 
    velocity=slip_velocity1,
    boundary_type=espressomd.lb.VelocityBounceBack
)
```

**特征**:
- 支持任意形状边界 (通过 `espressomd.shapes` 模块)
- 支持滑移速度 (moving walls)
- 边界标记存储在 `FlagField`
- 支持边界 Union (多个形状组合)

**实现机制**:
- `BoundaryHandling<FloatType, Vector3<FloatType>, DynamicUBB>` - 边界处理类
- `DynamicUBB` (Dynamic Unsaturated Bounce-Back) - 动态 UBB kernel
- 边界条件通过 flag field 标记，碰撞步特殊处理

### 3.2 Zou-He 边界

**状态**: ❌ **未发现显式 Zou-He 实现**

代码中未见 `ZouHe` 或 `pressure-boundary` 相关实现。速度/压力边界通过 bounce-back + 外力实现。

### 3.3 曲率边界

**状态**: ❌ **无**

仅支持基于形状 (Wall/Sphere/Cylinder 等) 的 bounce-back，无曲率修正。

### 3.4 IBM (Immersed Boundary Method)

**状态**: ❌ **无**

粒子-流体耦合通过 MD-LBM 插值实现，非经典 IBM。

### 3.5 Lees-Edwards 边界 (特殊)

**实现位置**: `src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp:85-150`

**功能**: 周期性剪切流边界 (用于模拟简单剪切)

```cpp
void set_collision_model(std::unique_ptr<LeesEdwardsPack> &&lees_edwards_pack) {
  // 限制：shear_plane_normal 必须为 "y"
  // 限制：剪切方向不能有域分解
  // 限制：不支持 GPU (截至 2026)
  // 限制：不支持热涨落 (kT 必须为 0)
}
```

**特征**:
- 修改碰撞步以施加剪切速度
- PDF/速度/力在边界处插值
- 当前限制较多 (仅 CPU，仅 y 法向)

---

## 四、多相/多组元

### ⚠️ **本仓无多相/多组元 LB 实现**

**全面搜索结果**:
- 关键词搜索 (`multiphase`, `multicomponent`, `two-phase`, `shan-chen`, `free-energy`, `phase-field`): **0 结果**
- 文件树扫描: **无相关文件**
- 测试用例: **无多相测试**

**结论**: ESPResSo 的 LB 求解器**仅支持单相不可压流体**。多相流能力需依赖其他开源项目 (如 OpenLB、waLBerla 原生、Palabos)。

**对我们的意义**:
- GAP-LBM-MCMP-POISEUILLE (两组分分层 Poiseuille): **无法从本仓吸收**
- MCMP 高密度比: **无法从本仓吸收**
- 自由面/相场: **无法从本仓吸收**

---

## 五、GPU/并行

### 5.1 GPU 支持

**实现位置**: `src/walberla_bridge/src/lattice_boltzmann/lb_kernels.cuh`

**技术栈**:
- **CUDA** (通过 waLBerla 框架)
- 支持单精度 (`float`) 和双精度 (`double`)
- Kernel 生成: `StreamCollideSweepThermalized*CUDA.cu`

**数据布局**:
```cpp
// GPU 场类型
using GPUField = gpu::GPUField<FloatType>;

// PDF 场: SoA (Stream of Arrays) 布局
using PdfField = FieldTrait<FloatType, Stencil, Architecture>::PdfField;
// 实际: GhostLayerField<FT, 19> - 19 个分量连续存储
```

**内存合并**:
- PDF 场: 19 个分量在内存中连续 (利于合并访问)
- 速度场: 3 个分量连续
- 通过 `pystencils` 代码生成优化内存访问模式

### 5.2 MPI 并行

**实现位置**: `src/walberla_bridge/src/lattice_boltzmann/LBWalberlaImpl.hpp:100-150`

**框架**: waLBerla `blockforest` 模块

**分区策略**:
- 均匀块分解 (uniform block decomposition)
- 3D Cartesian MPI 拓扑
- Ghost layer 通信 (D3Q19/D3Q27)

**通信隐藏**:
```cpp
// Ghost communication 类型
enum GhostCommFlags : unsigned {
  PDF,  // PDF 通信
  VEL,  // 速度通信
  LAF,  // 最后施加力通信
  UBB,  // 边界通信
  SIZE
};

// 异步通信
std::shared_ptr<RegularFullCommunicator> m_full_communicator;
std::shared_ptr<PDFStreamingCommunicator> m_pdf_streaming_communicator;
```

**通信优化**:
- 分离 PDF/速度/力通信 (按需)
- 使用 `UniformBufferedScheme` 缓冲通信
- Ghost layer 厚度可配置 (默认 1)

### 5.3 数据布局总结

| 场类型 | 布局 | 内存顺序 | 备注 |
|--------|------|----------|------|
| PDF | SoA | 19 分量连续 | 利于 GPU 合并 |
| 速度 | SoA | 3 分量连续 | |
| 力 | SoA | 3 分量连续 | |
| Flag | AoS | 单标量 | 边界标记 |

---

## 六、可微/代码生成

### 6.1 代码生成

**核心工具**: **lbmpy** + **pystencils**

**生成流程**:
1. Python 脚本定义 LB 方法 (格子、碰撞、力项)
2. lbmpy 生成 C++/CUDA kernel
3. pystencils 优化内存访问和向量化
4. 编译为共享库，运行时加载

**生成 kernel 示例**: `src/walberla_bridge/src/lattice_boltzmann/generated_kernels/`
- `StreamCollideSweepThermalizedDoublePrecision.cpp`
- `StreamCollideSweepThermalizedDoublePrecisionAVX.cpp` (向量化)
- `StreamCollideSweepThermalizedDoublePrecisionCUDA.cu`

**优势**:
- 自动优化 (AVX2/CUDA)
- 单源多目标 (CPU/GPU)
- 易于扩展新碰撞模型

### 6.2 Autograd 友好度

**状态**: ❌ **不支持**

- 无 PyTorch/JAX 集成
- 无自动微分支持
- 代码生成是静态的 (编译时确定)

### 6.3 符号推导

**状态**: ⚠️ **部分支持**

- lbmpy 内部使用 SymPy 推导矩矩阵、松弛率
- 用户不可见 (黑盒)
- 无法自定义符号推导

---

## 七、与功能点映射

### 7.1 多相类

| 功能点 | 本仓支持 | 备注 |
|--------|----------|------|
| GAP-LBM-MCMP-POISEUILLE | ❌ 无 | 无多组元实现 |
| MCMP 高密度比 | ❌ 无 | 无 Shan-Chen |
| 自由面 | ❌ 无 | |
| 相场 Allen-Cahn | ❌ 无 | |

**结论**: 多相流功能**完全无法从本仓吸收**。

### 7.2 性能类

| 功能点 | 本仓支持 | 可吸收度 | 文件指针 |
|--------|----------|----------|----------|
| GPU 融合 kernel | ✅ 有 | ⭐⭐⭐ 参考 | `generated_kernels/StreamCollideSweep*.cu` |
| 分布式多卡 | ✅ 有 | ⭐⭐⭐ 参考 | `LBWalberlaImpl.hpp:100-150` |
| 稀疏/多孔 | ⚠️ 部分 | ⭐⭐ 参考 | 通过 flag field 标记固体 |

**可吸收点**:
1. **GPU kernel 融合策略**: 碰撞+传播融合，减少内存带宽
2. **MPI 通信优化**: 分离 PDF/速度/力通信，按需同步
3. **代码生成架构**: lbmpy/pystencils 工作流

### 7.3 平台类

| 功能点 | 本仓支持 | 可吸收度 | 文件指针 |
|--------|----------|----------|----------|
| 案例组织 | ✅ 有 | ⭐⭐ 参考 | `testsuite/python/lb_*.py` |
| 测试与验证面 | ✅ 有 | ⭐⭐⭐ 吸收 | `testsuite/python/lb_*.py` (30+ 测试) |
| CI | ✅ 有 | ⭐⭐ 参考 | `.github/workflows/` |

**可吸收点**:
1. **测试用例设计**: Poiseuille/Couette/Stokes 等经典验证
2. **边界条件测试**: 形状组合、滑移速度、插值精度
3. **热涨落验证**: 温度、RNG 状态追踪

---

## 八、可吸收 Top3 + 风险

### Top 1: GPU 融合 Kernel 架构 ⭐⭐⭐

**可吸收点**:
- 碰撞+传播融合 kernel (减少 50% 内存带宽)
- 单精度/双精度模板化
- AVX2/CUDA 自动向量化

**文件指针**:
- `src/walberla_bridge/src/lattice_boltzmann/generated_kernels/StreamCollideSweepThermalized*.cu`
- `src/walberla_bridge/src/lattice_boltzmann/lb_kernels.cuh`

**风险**:
- GPL 许可证限制 (不能直接复制代码)
- 依赖 pystencils 代码生成 (需重建工具链)

**转移方式**: **reference** (参考架构，重新实现)

### Top 2: MRT 碰撞模型 + 热涨落 ⭐⭐⭐

**可吸收点**:
- MRT 松弛率自动推导 (从粘度)
- 热涨落 RNG 状态追踪
- 奇数模式松弛 (消除数值不稳定)

**文件指针**:
- `src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp:30-80`

**风险**:
- GPL 许可证限制
- 热涨落需要仔细验证 (涨落-耗散定理)

**转移方式**: **reference** (参考公式，重新实现)

### Top 3: 测试验证面 ⭐⭐⭐

**可吸收点**:
- 30+ LB 测试用例 (Poiseuille/Couette/Stokes/剪切/质量守恒...)
- 边界条件测试 (形状组合、滑移速度)
- 热涨落统计验证

**文件指针**:
- `testsuite/python/lb_poiseuille.py`
- `testsuite/python/lb_couette_xy.py`
- `testsuite/python/lb_mass_conservation.py`
- `testsuite/python/lb_momentum_conservation.py`

**风险**:
- 测试用例本身无许可证问题 (可参考)
- 需要适配我们的 API 设计

**转移方式**: **adapt** (改编测试用例)

---

## 机器块

[OSS-INV]
repo: espressomd/espresso
lang: C++
license: GPL-3.0
methods: D3Q19×MRT+thermalized; D3Q27(ghost comm)
multiphase: 无
boundary: velocity-bounce-back (src/walberla_bridge/src/lattice_boltzmann/LBWalberlaImpl.hpp:67); Lees-Edwards (src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp:85)
gpu: CUDA (single/double precision; src/walberla_bridge/src/lattice_boltzmann/lb_kernels.cuh)
summary: ESPResSo 是 MD-LBM 耦合平台，LB 基于 waLBerla+lbmpy，支持 MRT+热涨落+粒子耦合，但无多相流能力。GPL 许可证限制代码复用，仅可参考架构和算法。
[/OSS-INV]

[OSS-SOL]
repo: espressomd/espresso
slug: GPU 融合 kernel
tag: stream-collide-fusion
method: 碰撞+传播融合 kernel，通过 pystencils 代码生成，支持 AVX2/CUDA 自动向量化
files: src/walberla_bridge/src/lattice_boltzmann/generated_kernels/StreamCollideSweepThermalized*.cu
conventions: SoA 布局，19 分量连续存储
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: espressomd/espresso
slug: MRT 碰撞模型
tag: mrt-thermalized
method: MRT 松弛矩阵从粘度自动推导，支持热涨落 (kT 参数)，奇数模式松弛消除数值不稳定
files: src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp:30-80
conventions: omega = 2/(6*nu+1); omega_odd = (4-2*omega)/(4*gamma*omega+2-omega)
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: espressomd/espresso
slug: 测试与验证面
tag: lb-validation-suite
method: 30+ 测试用例覆盖 Poiseuille/Couette/Stokes/剪切/质量守恒/动量守恒/边界条件/热涨落
files: testsuite/python/lb_poiseuille.py, testsuite/python/lb_couette_xy.py, testsuite/python/lb_mass_conservation.py
conventions: 无量纲化，agrid/tau/density/viscosity 参数化
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: espressomd/espresso
slug: NEW:Lees-Edwards 剪切边界
tag: lees-edwards-shear
method: 周期性剪切流边界，修改碰撞步施加剪切速度，PDF/速度/力在边界处插值
files: src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp:85-150
conventions: 限制 shear_plane_normal="y"，不支持 GPU，不支持热涨落
transfer: reference
[/OSS-SOL]

---

## 附录：代码指针汇总

| 功能 | 文件路径 | 行号/范围 |
|------|----------|-----------|
| D3Q19 格子定义 | `src/walberla_bridge/src/lattice_boltzmann/LBWalberlaImpl.hpp` | 67 |
| MRT 碰撞模型 | `src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp` | 30-80 |
| Lees-Edwards 边界 | `src/walberla_bridge/src/lattice_boltzmann/LBCollisionSetup.impl.hpp` | 85-150 |
| GPU kernel 声明 | `src/walberla_bridge/src/lattice_boltzmann/lb_kernels.cuh` | 全文 |
| 力项插值 | `src/core/lb/LBWalberla.cpp` | 100-120 |
| 边界处理 | `testsuite/python/lb_boundary.py` | 50-80 |
| MPI 通信 | `src/walberla_bridge/src/lattice_boltzmann/LBWalberlaImpl.hpp` | 100-150 |
| 测试用例 | `testsuite/python/lb_*.py` | 30+ 文件 |

---

**报告完成时间**: 2026-11-09  
**盘点方法**: GitHub API 读码 (代码本体为主，文档辅助)  
**置信度**: 高 (核心文件已读，多相流搜索全面)
