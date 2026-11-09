# 【盘点】pylbm/pylbm — 开源 LBM 技术深盘

**仓库**: https://github.com/pylbm/pylbm  
**盘点日期**: 2026-11-09  
**commit**: 48c19da (2026-11-09 06:54:26 +0100)  
**盘点方式**: 代码本体阅读（git clone 后逐文件分析）

---

## 一、概况

| 项 | 值 |
|---|---|
| **语言** | Python + Cython + SymPy（代码生成目标：Cython/NumPy/Loo.py） |
| **许可证** | BSD-3-Clause |
| **许可证可用性** | ✅ **完全可用**：BSD-3 与我们的许可兼容，可自由复用代码片段、设计模式、算法实现 |
| **活跃度** | 活跃：最新 commit 2026-11-09（interpolated bounce-back），193 个 .py 文件，有完整 CI |
| **定位** | **通用 LBM 框架**：以 D'Humières 矩形式为核心，用 SymPy 符号推导自动生成 LBM kernel，支持任意格子/任意维数/任意矩基，后端可选 Cython/NumPy/LooPy(GPU) |

**一句话**：pylbm 是一个「以符号推导驱动代码生成」的 LBM 框架，用户用 SymPy 表达式定义格式/平衡态/松弛参数，框架自动生成高性能 kernel——这种「声明式 LBM」设计在开源界独树一帜。

---

## 二、方法谱系

### 2.1 格子（Stencil）

**核心文件**: `pylbm/stencil.py`

- **任意格子支持**：`Velocity` 类（:120-380）通过 `_set_num`/`_set_coord` 实现 D1/D2/D3 任意速度编号↔坐标的双向映射
- **Stencil 构建**：`OneStencil`（:383-）封装单个格式，`Stencil`（:480-）管理多格式组合
- **已用格式**（从 demo/ 提取）：
  - D1: D1Q2, D1Q3, D1Q2Q2
  - D2: D2Q4, D2Q5, D2Q9, D2Q4Q4Q4
  - D3: D3Q15, D3Q19（通过 `velocities: list(range(N))` 声明）
- **关键设计**：格式编号采用螺旋编号方案（`_set_num` :240-310），非标准但内部自洽

### 2.2 碰撞模型

**核心文件**: `pylbm/scheme.py`, `pylbm/algorithm/base.py`

- **形式**：D'Humières 矩形式（`pylbm/scheme.py:1-785`）
- **碰撞实现**：`relaxation_local()` (`algorithm/base.py:590-615`)
  ```python
  relax = (1 - self.s) * m + self.s * eq  # 逐矩松弛
  ```
- **碰撞类型**：
  - **BGK**：所有非守恒矩用同一松弛参数（用户配置）
  - **TRT**：正/负矩分两组松弛参数（`tests/test_linear_bounce_back.py:60`, `doc/_source/codes/scheme_D2Q9_conserved_polynomials.py:17`）
  - **MRT**：每个矩独立松弛参数（`demo/2D/Poiseuille.py:52-53` 中 `s = [0,0,0,s1,s1,s1,s1,s2,s2]`）
  - **KBC/ cumulant**：❌ **未实现**
  - **相对速度**：`rel_vel` 参数支持移动参考系（`algorithm/base.py:80-95`）

### 2.3 力项/源项

**核心文件**: `pylbm/scheme.py:110`, `pylbm/algorithm/base.py:617-710`

- **源项机制**：`source_terms` 字典，对矩施加显式 Euler 积分
  ```python
  # algorithm/base.py:617-660
  def source_term_local(self, m):
      # 对每个源项 (lhs, rhs) 做 m[lhs] += dt * rhs
  ```
- **分布函数源**：`source_distribution`（`algorithm/base.py:662-690`），直接对 f 加值
- **力项模型**：
  - ✅ 通用源项（用户自定义矩空间源）
  - ❌ **Guo 力项**：未专门实现（可通过 source_terms 手动构造）
  - ❌ **RK（Reckinger-Krafczyk）**：未实现
  - ❌ **shifted-velocity**：未实现

---

## 三、边界处理

**核心文件**: `pylbm/boundary.py`（1326 行，非常丰富）

### 3.1 Bounce-back 族

| 类名 | 型式 | 文件:行 | 说明 |
|---|---|---|---|
| `BounceBack` | 标准 BB | `boundary.py:428` | 半程反弹，f_i(x) = f_i*(x + c_i dt) |
| `AntiBounceBack` | 反bounce-back | `boundary.py:649` | 用于自由滑移/对称 |
| `BFLBounceBack` | Bouzidi-Filippova-Lalleman | `boundary.py:500` | 曲壁二阶插值，δ<0.5 和 δ≥0.5 分支 |
| `BFLAntiBounceBack` | BFL 反 BB | `boundary.py:710` | BFL 的反对称版本 |

### 3.2 线性插值 bounce-back 族（2026 新增）

| 类名 | α 公式 | 文件:行 | 说明 |
|---|---|---|---|
| `LinearBounceBack` | 基类 | `boundary.py:810` | 统一框架：f = α·f_eq + (1-α)·f_opposite |
| `CLIBounceBack` | 4/(1+2δ) | `boundary.py:1102` | 中心线性插值，δ=1/2 退化为 BB |
| `YLIBounceBack` | 2/(1+δ) | `boundary.py:1115` | Yu-Mei-Luo-Shyy |
| `ParYLIBounceBack` | 2/(1+δ) + 后碰撞修正 | `boundary.py:1128` | 带 post_collision_correction |
| `ParBFLBounceBack` | BFL + 后碰撞修正 | `boundary.py:1136` | α=2(δ≤1/2), 1/δ(δ>1/2) |
| `LMKCAntiBounceBack` | -4/(1+2δ) | `boundary.py:1148` | 反 bounce-back 中心插值 |
| `ParLMKAAntiBounceBack` | BFL 反 BB + 修正 | `boundary.py:1162` | 带后碰撞修正 |

### 3.3 Neumann 族

| 类名 | 方向 | 文件:行 |
|---|---|---|
| `Neumann` | 法向 | `boundary.py:1175` |
| `NeumannX/Y/Z` | 沿 x/y/z | `boundary.py:1238/1262/1286` |

### 3.4 其他

- **Zou-He**：❌ **未专门实现**（可通过 `value_bc` + 平衡态构造实现）
- **IBM（浸没边界）**：❌ **未实现**
- **曲率处理**：BFL 族通过 `distance` 数组处理任意曲壁（`boundary.py:500-648`）

---

## 四、多相/多组元

### ⚠️ **本仓无任何多相/多组元实现**

经全仓 grep（关键词：multiphase, multicomponent, shan-chen, shanchen, free-energy, phase-field, color-gradient, free-surface, two-phase, pseudo-potential, interaction-force, carnahan-starling, spurious），**零命中**。

- ❌ SCMP（Shan-Chen 单组分多相）
- ❌ MCMP（多组分多相）
- ❌ Free-energy（自由能法）
- ❌ Phase-field（相场法）
- ❌ Color-gradient（颜色梯度法）
- ❌ Free-surface（自由面法）

**结论**：pylbm 是纯单组分单相框架，所有 demo 均为单相流（Poiseuille、lid-driven cavity、Karman vortex、advection 等）。

---

## 五、GPU/并行

### 5.1 数据布局

**核心文件**: `pylbm/storage.py`

| 类 | 布局 | 文件:行 | 说明 |
|---|---|---|---|
| `Array` | 通用（sorder 控制） | `storage.py:1-350` | 基类，sorder 决定维度排列 |
| `SOA` | Structure of Arrays | `storage.py:352-420` | sorder=[0,1,2,3]，nv 最外层 |
| `AOS` | Array of Structures | `storage.py:422-524` | sorder=[dim,0,1,...,dim-1]，nv 最内层 |

- **默认**：NumPy 后端用 SOA，Cython/Loopy 后端用 AOS（`container.py:60,80`）
- **内存合并**：AOS 布局使速度维度连续，利于 GPU 合并读取

### 5.2 GPU 支持

**核心文件**: `pylbm/container.py:95-125`, `pylbm/context.py`

- **后端**：Loo.py（`pylbm/generator/printing/loopy.py`）→ PyOpenCL
- **容器**：`LoopyContainer`（`container.py:95`）设 `gpu_support = True`
- **数据搬运**：`move2gpu()` 用 `pyopencl.array.to_device()`（`container.py:115`）
- **Queue 管理**：全局单例 `queue`（`context.py`），所有模拟共享

### 5.3 MPI 并行

**核心文件**: `pylbm/mpi_topology.py`

- **拓扑**：`MpiTopology` 类（:30-200），用 `mpi4py` 的 `Create_cart` 建笛卡尔拓扑
- **分区**：`mpi.Compute_dims` 自动分区，或用户指定 `npx/npy/npz`
- **通信**：`Array.update()`（`storage.py:180-280`）用 `Irecv/Isend` 非阻塞通信
- **通信隐藏**：❌ **未实现**（通信与计算串行）

---

## 六、可微/代码生成

### 6.1 符号推导框架

**核心文件**: `pylbm/symbolic.py`, `pylbm/scheme.py`

- **SymPy 驱动**：所有矩、平衡态、松弛矩阵均为 SymPy 表达式
- **自动推导**：
  - 矩矩阵 M 和 M⁻¹ 自动计算（`scheme.py:300-400`）
  - 平衡态自动符号化（`scheme.py:200-250`）
  - 松弛算子自动生成（`algorithm/base.py:590-615`）
- **等价方程**：`analysis/equivalent_equation.py` 自动推导修正方程（Chapman-Enskog 展开）
- **稳定性分析**：`analysis/stability.py` 自动计算放大矩阵特征值

### 6.2 代码生成

**核心文件**: `pylbm/generator/`

| 后端 | 文件 | 说明 |
|---|---|---|
| Cython | `generator/printing/cython.py` | 生成 .pyx，编译为 C 扩展 |
| NumPy | `generator/printing/pycode.py` | 生成向量化 NumPy 代码 |
| Loo.py | `generator/printing/loopy.py` | 生成 OpenCL/CUDA kernel |

- **流程**：SymPy 表达式 → `codegen.make_routine()` → 后端 printer → 编译/包装
- **autowrap**：`generator/autowrap.py` 自动编译、导入、缓存

### 6.3 Autograd 友好度

- ❌ **不直接支持 autograd**：生成的代码是数值 kernel，非计算图
- ✅ **符号推导友好**：SymPy 表达式可自动微分（但 pylbm 未利用此能力）

---

## 七、与我们功能点的映射

| 我们的功能点 | pylbm 实现情况 | 映射 |
|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE**（两组分分层 Poiseuille） | ❌ 无多组元 | 不可映射 |
| **MCMP 高密度比** | ❌ 无多相 | 不可映射 |
| **自由面** | ❌ 无 | 不可映射 |
| **相场 Allen-Cahn** | ❌ 无 | 不可映射 |
| **GPU 融合 kernel** | ⚠️ Loo.py 后端生成 GPU kernel，但未做算子融合 | 参考设计 |
| **分布式多卡** | ✅ MPI + PyOpenCL | 可参考 `mpi_topology.py` |
| **稀疏/多孔** | ⚠️ `in_or_out` 数组标记流/固（`algorithm/base.py:406-415`） | 可参考 |
| **案例组织** | ✅ demo/ 按 1D/2D/3D 组织，examples/ 按 domain/geometry/scheme 分类 | **可吸收** |
| **测试与验证面** | ✅ tests/ 有完整 pytest 套件，含 baseline/reference | **可吸收** |
| **CI** | ✅ GitHub Actions（`.github/workflows/ci`） | 可参考 |

---

## 八、可吸收 Top3 + 风险

### Top 1：符号推导驱动的代码生成架构

**价值**：pylbm 用 SymPy 定义 LBM 格式，自动生成 Cython/NumPy/OpenCL kernel——这种「声明式 LBM」设计极大降低了新格式开发成本。

**可吸收点**：
- `pylbm/scheme.py` 的 Scheme 类设计（矩矩阵自动推导）
- `pylbm/generator/` 的多后端代码生成框架
- `pylbm/analysis/` 的等价方程/稳定性自动分析

**风险**：SymPy 推导在大规模格式（D3Q27+）时可能慢；生成的 kernel 性能依赖后端优化。

### Top 2：插值 bounce-back 边界条件族

**价值**：`boundary.py:810-1175` 实现了完整的插值 BB 族（CLI/YLI/BFL/ParBFL），含后碰撞修正——这是曲壁高精度模拟的关键。

**可吸收点**：
- `LinearBounceBack` 基类的统一框架（α 参数化）
- 后碰撞修正机制（`post_collision_correction` 标志）
- BFL 的 δ<0.5/δ≥0.5 分支处理

**风险**：需要我们的 TensorLBM 有类似的边界索引管理机制。

### Top 3：案例组织与测试验证体系

**价值**：`demo/` 和 `tests/` 的组织方式、baseline/reference 数据管理、pytest 套件设计——可直接复用到 TensorLBM。

**可吸收点**：
- `demo/1D/2D/3D/` 按维度组织，`demo/examples/` 按功能组织
- `tests/baseline/` 和 `tests/reference/` 存放验证数据
- `tests/test_*.py` 的 pytest 模式（参数化测试、收敛性测试）

**风险**：需要适配我们的验证判据和证据纪律。

---

## 机器块

```
[OSS-INV]
repo: pylbm/pylbm
lang: Python
license: BSD-3-Clause
methods: D1Q2/D1Q3/D2Q4/D2Q5/D2Q9/D3Q15/D3Q19; BGK/TRT/MRT (via relaxation params); explicit Euler source terms
multiphase: 无
boundary: BounceBack; AntiBounceBack; BFLBounceBack (curved); LinearBounceBack (CLI/YLI/ParBFL); Neumann
gpu: Loo.py backend → PyOpenCL; SOA/AoS storage; MPI via mpi4py
summary: pylbm 是以 SymPy 符号推导驱动代码生成的通用 LBM 框架，支持任意格式/维数/矩基，后端可选 Cython/NumPy/OpenCL；无多相/多组元实现，边界条件丰富（含插值 BB 族），GPU/MPI 支持完整。
[/OSS-INV]
```

```
[OSS-SOL]
repo: pylbm/pylbm
slug: NEW:symbolic-lbm-framework
tag: sympy-driven-codegen
method: 用 SymPy 定义 LBM 格式（速度/多项式/平衡态/松弛参数），自动推导矩矩阵 M/M⁻¹，生成 Cython/NumPy/OpenCL kernel
files: pylbm/scheme.py:1-785, pylbm/generator/generator.py:1-34, pylbm/generator/autowrap.py:1-219
conventions: D'Humières 矩形式；la = dx/dt 为格式速度；松弛参数 s ∈ [0,2]
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: pylbm/pylbm
slug: NEW:interpolated-bounce-back
tag: linear-interpolated-bb
method: 统一框架 LinearBounceBack 实现 α 参数化插值 bounce-back，含 CLI(4/(1+2δ))/YLI(2/(1+δ))/BFL 变体，支持后碰撞修正
files: pylbm/boundary.py:810-1175
conventions: δ 为壁面到流体节点距离（格子单位）；α 为插值系数；post_collision_correction 标志
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: pylbm/pylbm
slug: NEW:equivalent-equation-analysis
tag: chapman-enskog-auto
method: 自动推导 LBM 格式的等价方程（修正方程），计算一阶/二阶修正项，用于精度分析
files: pylbm/analysis/equivalent_equation.py:1-354
conventions: 输入 Scheme 对象，输出 coeff_order1/coeff_order2 符号表达式
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: pylbm/pylbm
slug: NEW:stability-analysis
tag: von-neumann-auto
method: 自动计算 LBM 格式的放大矩阵，对给定波矢计算特征值，判断 L2 稳定性
files: pylbm/analysis/stability.py:1-478
conventions: 输入 Scheme 对象 + 守恒矩初值 + 波矢网格，输出特征值谱
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: pylbm/pylbm
slug: NEW:mpi-topology
tag: cartesian-mpi
method: 基于 mpi4py 的笛卡尔拓扑管理，自动分区（Compute_dims）或用户指定，非阻塞 Irecv/Isend 通信
files: pylbm/mpi_topology.py:1-243
conventions: npx/npy/npz 为各方向进程数；period 为周期性标志列表
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: pylbm/pylbm
slug: NEW:test-organization
tag: pytest-baseline-reference
method: tests/ 目录含 pytest 套件，baseline/ 和 reference/ 存放验证数据，支持参数化测试和收敛性测试
files: tests/test_*.py, tests/baseline/, tests/reference/
conventions: test_*.py 命名；baseline 为数值基准，reference 为解析/文献解
transfer: fits
[/OSS-SOL]
```
