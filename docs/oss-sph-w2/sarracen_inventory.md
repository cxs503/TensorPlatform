# OSS-SPH 深盘波2 · ttricco/sarracen 普查报告

> **任务 ID**: m8681 · **战役**: osssph-w2 · **仓**: TensorPlatform  
> **普查时间**: 2026-11-09 · **普查方式**: 集群 git clone + 源码级审读  
> **commit 基线**: adce0da (v1.4.1, 2026-09-10)

---

## 1. 仓库概况（GitHub API 实查）

| 项 | 值 | 来源 |
|---|---|---|
| 仓库 | `ttricco/sarracen` | git remote |
| 语言 | Python (100%) | pyproject.toml / 源码统计 |
| License | **GPL-3.0** | LICENCE 文件全文确认 |
| 最新版本 | v1.4.1 | git tag |
| 总提交 | 466 | git log --oneline \| wc -l |
| 最近提交 | 2026-09-10 (adce0da) "version 1.4.1" | git log -1 |
| 作者 | Andrew Harris, Terrence S. Tricco (Memorial University of Newfoundland, Canada) | docs/paper/paper.md |
| JOSS 论文 | 2023-03-01 发表 | docs/paper/paper.md |
| Python 要求 | ≥3.10 | pyproject.toml |
| 核心依赖 | matplotlib, numba, pandas, numpy, scipy, seaborn, scikit-learn | pyproject.toml + requirements.txt |
| 文档 | ReadTheDocs (sphinx) | docs/conf.py |
| CI | 仅 lint (super-linter/flake8 on PR) | .github/workflows/lint_prs.yaml |
| 源码行数 | ~14,201 行 Python | find sarracen -name "*.py" \| xargs wc |
| 测试行数 | ~4,823 行 | sarracen/tests/ |
| GitHub API 直查 | ⚠️ 集群/本地均无法达 api.github.com（网络策略限制），星数/forks/issues 数未获取 | 如实声明 insufficient |

**注意**：GitHub API 返回 None（集群出口无法达 api.github.com:443），星数/forks/open_issues 等元数据**未获取**，如实标记为 insufficient。仓库元数据从 git 本地 + JOSS 论文提取。

---

## 2. 目录结构与模块架构

```
sarracen/
├── __init__.py              # 公共 API 入口
├── sarracen_dataframe.py    # SarracenDataFrame (pandas.DataFrame 子类, 777 行)
├── render.py                # 渲染/可视化 (973 行)
├── interpolate/
│   ├── __init__.py          # 导出 12 个插值函数
│   ├── base_backend.py      # 后端抽象基类
│   ├── cpu_backend.py       # CPU 后端: numba njit+prange 多线程 (719 行)
│   ├── gpu_backend.py       # GPU 后端: numba CUDA+atomic (807 行)
│   └── interpolate.py       # 插值主逻辑 (1671 行)
├── kernels/
│   ├── base_kernel.py       # 核函数基类 + 柱积分近似
│   ├── cubic_spline.py      # 三次样条核 (默认)
│   ├── cubic_spline_exact.py # 三次样条精确积分 (Petkova 2018, 482 行)
│   ├── quartic_spline.py    # 四次样条核
│   └── quintic_spline.py    # 五次样条核
├── readers/                 # 7 个读取器
│   ├── read_phantom.py      # Phantom SPH (447 行, 主力)
│   ├── read_gasoline.py     # Gasoline (353 行)
│   ├── read_shamrock.py     # Shamrock (355 行)
│   ├── read_marisa.py       # MARISA (331 行)
│   ├── read_gradsph.py      # GradSPH (68 行)
│   ├── read_phantom_ev.py   # Phantom 演化数据 (102 行)
│   ├── read_csv.py          # CSV (33 行)
│   └── read_shamrock_vtk.py # Shamrock VTK (69 行)
├── writers/
│   └── write_phantom.py     # Phantom 写出 (466 行测试)
├── disc/                    # 盘分析
│   ├── surface_density.py   # 面密度/角动量剖面 (514 行)
│   └── utils.py             # 径向分箱工具
├── dust/
│   └── stokes_number.py     # Stokes 数计算 (433 行, KDTree)
├── ptmass/
│   └── sinks.py             # 沉粒子分类 (116 行)
└── tests/                   # 4823 行测试
```

---

## 3. 源码级方法清单

### 3.1 SPH 核函数（kernels/）

| 核 | 文件:行 | 支撑半径 | 归一化 | 备注 |
|---|---|---|---|---|
| Cubic Spline (M4) | `cubic_spline.py:14-22` | q=2 | 1D:2/3, 2D:10/7π, 3D:1/π | **默认核** |
| Quartic Spline | `quartic_spline.py:12-20` | q=2.5 | 1D:1/24, 2D:96/1199π, 3D:1/20π | — |
| Quintic Spline (M5) | `quintic_spline.py:12-20` | q=3 | 1D:1/120, 2D:7/478π, 3D:1/120π | — |
| 精确柱积分 | `cubic_spline_exact.py:1-482` | — | — | Petkova 2018 解析面积/线积分 |
| 柱核近似 | `base_kernel.py:47-73` | — | — | 数值 trapezoid 积分 + 线性插值缓存 |

### 3.2 SPH 插值方法（interpolate/）

核心 SPH 插值公式（来自 JOSS 论文）：

$$A_a = \sum_b \frac{m_b}{\rho_b} A_b W_{ab}(h_b)$$

| 方法 | 文件:行 | 维度 | 说明 |
|---|---|---|---|
| `interpolate_2d` | `interpolate.py:489` | 2D→2D grid | 标准 2D SPH 插值 |
| `interpolate_2d_vec` | `interpolate.py:595` | 2D→2D grid (矢量) | 矢量场插值 |
| `interpolate_2d_line` | `interpolate.py:707` | 2D→1D line | 沿线的 2D 插值 |
| `interpolate_3d_proj` | `interpolate.py:952` | 3D→2D (柱积分) | 视线方向积分 |
| `interpolate_3d_cross` | `interpolate.py:1237` | 3D→2D (切片) | z=const 截面 |
| `interpolate_3d_grid` | `interpolate.py:1513` | 3D→3D grid | 全 3D 网格插值 |
| `interpolate_3d_line` | `interpolate.py:823` | 3D→1D line | 3D 空间线段 |
| 精确渲染 | `cpu_backend.py:317-448`, `gpu_backend.py:347-490` | 2D | Petkova 解析单元积分 |

**关键**：插值分 "fast"（SPH 核在像素中心采样）和 "exact"（解析核在像素面积上积分）两种模式。

### 3.3 边界处理

- **无物理边界条件**：sarracen 是后处理库，不求解 PDE，无边界条件概念
- **插值边界**：`_default_bounds()` (`interpolate.py:89-125`) 自动取数据 min/max 并 snap 到整数
- **旋转边界**：支持 Euler 角/旋转矩阵/四元数/SciPy Rotation 对象 (`interpolate.py:260-385`)

### 3.4 时间步控制

- **无**：sarracen 不推进时间，仅读取各时刻快照

### 3.5 多相/自由面能力

- **多相**：通过 `itype` 列区分气体/尘埃粒子（Phantom 格式），`read_phantom()` 支持 `separate_types` 参数分离
- **尘埃**：`dust/stokes_number.py` 实现 Stokes 数计算，使用 KDTree 查找气体-尘埃邻居
- **盘分析**：`disc/surface_density.py` 实现方位角平均面密度、角动量剖面
- **自由面**：无显式自由面追踪（SPH 天然拉格朗日，自由面由粒子分布隐式表达）

### 3.6 GPU/MPI 并行策略

| 策略 | 实现位置 | 技术细节 |
|---|---|---|
| **CPU 多线程** | `cpu_backend.py` 全文 | `@njit(parallel=True)` + `prange(get_num_threads())`，线程级分片粒子 |
| **GPU CUDA** | `gpu_backend.py` 全文 | `@cuda.jit(fastmath=True)` + `cuda.atomic.add()` 防竞争写 |
| **JIT 编译** | 所有核函数 | `@njit(fastmath=True)` 全核函数 JIT |
| **MPI** | **无** | 不支持分布式并行 |
| **后端切换** | `interpolate.py:1653-1671` `get_backend()` | 运行时选 'cpu' 或 'gpu' |

GPU 实现细节（`gpu_backend.py:245-321`）：
- 每个 CUDA 线程处理一个粒子
- 粒子贡献通过 `cuda.atomic.add(image, (jp, ip), term * wab)` 原子写入像素
- 数据通过 `cuda.to_device()` 传输
- exact 模式使用 `cuda.atomic.sub()` 做解析修正

---

## 4. 验证面盘点

### 4.1 测试框架

- **框架**: pytest（标准 Python 测试）
- **测试规模**: 4,823 行测试代码，覆盖 7 个子模块
- **CI**: 仅 lint（flake8），**无自动化测试 CI**

### 4.2 测试覆盖

| 模块 | 测试文件 | 行数 | 内容 |
|---|---|---|---|
| 插值 | `test_interpolate.py` | 1,564 | 2D/3D 插值精度、exact vs fast 对比 |
| 旋转 | `test_rotation.py` | 533 | 旋转矩阵/四元数/Euler 角 |
| 核函数 | `test_kernels.py` | 194 | 归一化、对称性、支撑域 |
| Phantom 读 | `test_read_phantom.py` | 732 | 二进制格式解析 |
| Phantom 写 | `test_write_phantom.py` | 466 | 写出-读回一致性 |
| 面密度 | `test_surface_density.py` | 234 | 方位角平均 |
| Stokes 数 | `test_stokes_number.py` | 170 | KDTree 邻居查找 |
| 渲染 | `test_render.py` | 371 | matplotlib 输出 |
| DataFrame | `test_sarracen_dataframe.py` | 279 | SarracenDataFrame 功能 |

### 4.3 自带算例/基准

- **Orszag-Tang 涡旋** (`docs/examples/ot.rst`): MHD 验证算例，t=0.5 快照分析
- **Dusty Disc** (`docs/examples/dustydisc.rst`): 含尘吸积盘 + 两颗行星，1.2M 气体 + 600k 尘埃粒子
- **无量化基准套件**：示例为展示性 Jupyter notebook 风格，无误差 vs 解析解的量化对比表

### 4.4 量化验证数据

- **无内置定量验证数据集**
- 测试以功能正确性为主（形状/类型/边界检查），非精度对标
- 插值测试 (`test_interpolate.py`) 含解析函数对比，但未给出系统误差统计

---

## 5. 可迁移方案

### 方案 1：精确 SPH 插值方法（Petkova 2018）

| 项 | 内容 |
|---|---|
| ID | SPHSOL-sarracen-01 |
| 分级 | **adapt** |
| 来源 | `sarracen/kernels/cubic_spline_exact.py` (482 行) |
| 内容 | 解析计算三次样条核在 2D 像素单元上的面积分（line_int, surface_int），避免采样误差 |
| 目标 GAP | **GAP-SPH-WCSPH-VALIDATION** |
| 迁移路径 | 将 exact 积分逻辑移植到 TensorSPH 的后处理验证模块，作为 WCSPH 插值精度的参考基准 |
| License 合规 | GPL-3.0 → 可参考算法思路（数学公式无版权），代码移植需 GPL 兼容 |

### 方案 2：CUDA 原子累加 SPH 渲染模式

| 项 | 内容 |
|---|---|
| ID | SPHSOL-sarracen-02 |
| 分级 | **fit** |
| 来源 | `sarracen/interpolate/gpu_backend.py:245-321` |
| 内容 | 粒子→网格的 CUDA 插值模式：每线程一粒子 + `cuda.atomic.add` 防竞争写 + `fastmath` |
| 目标 GAP | **GAP-SPH-GPU-SCALING** |
| 迁移路径 | TensorSPH GPU 内核可参考此 particle-to-grid 原子累加模式，对比与 grid-to-particle 或共享内存方案的效率差异 |
| License 合规 | GPL-3.0 → 算法模式（粒子→网格+原子操作）为通用技术，可自由参考；具体代码移植需 GPL |

### 方案 3：多格式 SPH I/O 框架设计

| 项 | 内容 |
|---|---|
| ID | SPHSOL-sarracen-03 |
| 分级 | **reference** |
| 来源 | `sarracen/readers/` 全目录 (7 个读取器) |
| 内容 | 统一 SarracenDataFrame 数据模型 + 多格式 reader 架构（Phantom/Gasoline/Shamrock/MARISA/GradSPH/CSV） |
| 目标 GAP | **GAP-SPH-WCSPH-VALIDATION** |
| 迁移路径 | TensorSPH 验证面如需对接外部 SPH 代码输出，可参考此 reader 抽象模式 |
| License 合规 | GPL-3.0 → 架构设计思路可参考，代码复用需 GPL |

---

## 6. 关键判定

### 6.1 定位判定

**sarracen 是 SPH 后处理/可视化库，不是 SPH 求解器。**

- 不求解 Navier-Stokes / Euler 方程
- 不实现时间推进、边界条件、状态方程
- 核心价值 = 读取 SPH 代码输出 → SPH 核插值到规则网格 → matplotlib 可视化
- 类比：ParaView/Splash 的 Python 替代品，非 SPH 求解器

### 6.2 对 TensorSPH 的价值

| 维度 | 价值 | 说明 |
|---|---|---|
| SPH 核实现 | ★★★ | 3 种核 + 精确积分，可直接对标 |
| 插值算法 | ★★★ | 完整的 2D/3D 插值体系 + exact 模式 |
| GPU 模式 | ★★☆ | CUDA 原子累加模式可参考，但非 MPI 扩展 |
| 验证基准 | ★☆☆ | 无量化基准，测试以功能为主 |
| 数据 I/O | ★★☆ | 多格式 reader 架构可参考 |
| 直接复用 | ★☆☆ | GPL-3.0 限制代码直接复用 |

### 6.3 License 合规判定

- **GPL-3.0**：强 copyleft，衍生作品必须同样 GPL
- TensorPlatform/TensorSPH 若为 GPL 兼容 → 可 adapt/fit
- 若为 Apache/MIT → 仅可 reference（参考思路，不复制代码）
- **建议**：算法思路/数学公式无版权限制；代码级复用需确认 license 兼容

---

## 7. 机器可读块

[OSS-INV]{"campaign":"osssph-w2","repo":"ttricco/sarracen","tier":3,"methods":["cubic_spline_kernel","quartic_spline_kernel","quintic_spline_kernel","exact_cubic_spline_integral_Petkova2018","SPH_2d_interpolation","SPH_3d_projection","SPH_3d_cross_section","SPH_3d_grid","SPH_2d_line","SPH_3d_line","azimuthal_average_surface_density","stokes_number_KDTree","sink_particle_classification"],"boundary":"none_postprocessing_library","gpu":"numba_CUDA_atomic_add_particle_to_grid","mpi":"not_supported","validation":"pytest_4823_lines_functional_tests_no_quantitative_benchmark","license":"GPL-3.0","stars":"insufficient_api_unreachable","language":"Python","version":"1.4.1","commits":466,"last_commit":"2026-09-10","lines_of_code":14201,"test_lines":4823,"transferable":[{"id":"SPHSOL-sarracen-01","kind":"adapt","gap":"GAP-SPH-WCSPH-VALIDATION"},{"id":"SPHSOL-sarracen-02","kind":"fit","gap":"GAP-SPH-GPU-SCALING"},{"id":"SPHSOL-sarracen-03","kind":"reference","gap":"GAP-SPH-WCSPH-VALIDATION"}]}[/OSS-INV]

[OSS-SOL]{"repo":"ttricco/sarracen","id":"SPHSOL-sarracen-01","kind":"adapt","summary":"精确三次样条核面积分(Petkova 2018)移植为TensorSPH插值精度参考基准，cubic_spline_exact.py 482行解析积分逻辑","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"ttricco/sarracen","id":"SPHSOL-sarracen-02","kind":"fit","summary":"CUDA原子累加粒子→网格SPH插值模式(gpu_backend.py)为TensorSPH GPU内核设计提供参考，含atomic.add/fastmath/线程分工","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL]{"repo":"ttricco/sarracen","id":"SPHSOL-sarracen-03","kind":"reference","summary":"多格式SPH数据I/O框架(7个reader+统一SarracenDataFrame)为TensorSPH验证面对接外部SPH代码提供架构参考","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

---

## 8. 诚实声明

| 项 | 状态 | 说明 |
|---|---|---|
| GitHub API 星数/forks/issues | ❌ insufficient | 集群/本地均无法达 api.github.com:443 |
| 源码审读 | ✅ 完成 | 全量 Python 源码通过集群 git clone 获取并审读 |
| 测试运行 | ❌ 未执行 | 仅静态审读，未在集群运行 pytest |
| License | ✅ 确认 | LICENCE 文件全文 = GPL-3.0 |
| 方法定位 | ✅ 确认 | 后处理/可视化库，非 SPH 求解器 |
