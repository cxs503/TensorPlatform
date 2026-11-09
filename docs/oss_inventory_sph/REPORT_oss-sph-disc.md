# 开源 SPH 技术盘点·候选普查报告

> **scenario_key**: oss-sph-disc  
> **mission_id**: 8638  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8638  
> **产物**: `artifacts/oss-sph/oss_sph_registry.json`（机器可解析）  
> **方法**: GitHub API (`api.github.com/search/repositories` + `/repos/{owner}/{name}`) 多轮查询 + 人工质量筛选

---

## 一、案例介绍（物理背景 / 普查范围）

### 1.1 任务背景

SPH（Smoothed Particle Hydrodynamics，光滑粒子流体动力学）是 Lagrangian 无网格粒子法的核心代表，自 1977 年 Gingold & Monaghan / Lucy 提出以来，已发展为覆盖天体物理、海岸工程、计算机图形学、生物医学的多尺度方法族。本普查对标 LBM 战役 wave-0 协议，为 TensorSPH 域建立开源技术全景图，为后续深盘（wave-1+）与 feature_ledger GAP 锚点提供候选池。

### 1.2 普查范围

| 维度 | 覆盖 |
|---|---|
| **方法族** | WCSPH · ISPH · PCISPH · DFSPH · IISPH · δ-SPH · PBD |
| **物理域** | 自由面 · 多相 · FSI · 弹性/弹塑性固体 · MHD · 相变 |
| **计算后端** | CPU · CUDA · Taichi · JAX · MPI · OpenMP |
| **应用域** | 海岸工程 · 天体物理 · 计算机图形学 · 工业制造 · 教学 |
| **质量门槛** | ≥5 stars 或方法独特性突出；有实质代码（非纯 README） |

### 1.3 普查方法

1. **GitHub API 多轮搜索**：`sph+smoothed+particle`、`sph+fluid`、`sph+hydrodynamics`、`sph+incompressible`、`sph+gpu+cuda`、`sph+taichi`、`sph+jax+differentiable`、`sph+mpi+parallel`、`sph+fem+coupling`、`sph+astrophysics`、`delta-SPH`、`ISPH+projection` 等 12+ 查询词；
2. **已知重要代码直接查询**：SPlisHSPlasH、DualSPHysics、SPHinXsys、SPH-EXA、Phantom、SWIFT、Shamrock、OpenSPH 等；
3. **质量筛选**：去重 → 去 fork → 去纯 demo/无实质代码 → 按 stars/活跃度/license/语言/方法覆盖度分级。

---

## 二、普查结果总览

### 2.1 分级统计

| Tier | 数量 | 定义 |
|---|---:|---|
| **Tier 1** 方法专家仓 | 10 | 核心 SPH 方法论、生产级求解器、有论文/实验验证 |
| **Tier 2** 可微/框架/平台 | 6 | 现代框架、可微 SPH、跨平台、云仿真 |
| **Tier 3** 专项能力 | 9 | 教学、分析可视化、特定物理（多相/相变/制造） |
| **合计** | **25** | — |

### 2.2 语言分布

| 语言 | 数量 | 代表 |
|---|---:|---|
| C++ | 13 | SPlisHSPlasH, DualSPHysics, SPHinXsys, SPH-EXA |
| Python | 7 | jax-sph, SPH_Taichi, sph-python, sarracen |
| Fortran | 2 | Phantom, SPHERA |
| CUDA/C++ | 1 | CPP-Fluid-Particles |
| C | 2 | SWIFT, sph-blitz |

### 2.3 许可证分布

| License | 数量 | 备注 |
|---|---:|---|
| MIT | 10 | 最宽松，可商用 |
| GPL-3.0 | 5 | 传染性，商用需谨慎 |
| LGPL-2.1/3.0 | 3 | 链接可用 |
| Apache-2.0 | 2 | 宽松+专利保护 |
| CECILL-2.1 | 1 | 法国 GPL 兼容 |
| 未声明/NOASSERTION | 4 | 需确认 |

---

## 三、每仓一句话速览

### Tier 1 — 方法专家仓

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---|---:|---|---|---|
| 1 | **SPlisHSPlasH** | 1959 | C++ | MIT | 图形学 SPH 金标准：WCSPH/PCISPH/DFSPH/IISPH/PBF 全家桶，黏度/表面张力/微极/湍流模型齐全 |
| 2 | **DualSPHysics** | 759 | C++ | LGPL-2.1 | 海岸工程领军：CUDA+MPI，溃坝/波浪/系泊/泥沙，大量实验验证 |
| 3 | **SPHinXsys** | 600 | C++ | Apache-2.0 | 工程多物理 SPH：FSI/多相/多分辨率，C++ API + Python 绑定 |
| 4 | **OpenSPH** | 254 | C++ | MIT | 天体 SPH/N-body + GUI：自引力/碰撞/碎裂，独立应用+库 |
| 5 | **GPUSPH** | 188 | C++ | GPL-3.0 | 首个 CUDA WCSPH：自由面+SA 边界，多 GPU |
| 6 | **Phantom** | 167 | Fortran | GPL-2.0 | 天体 SPH+MHD 生产代码：尘埃/辐射/自引力，论文级 |
| 7 | **SWIFT** | 163 | C | LGPL-3.0 | 宇宙学 SPH：task-based MPI，亚网格物理（冷却/星形成/反馈），拍瓦级 |
| 8 | **SPH-EXA** | 111 | C++ | MIT | C++20 百亿亿 SPH：MPI+CUDA+Octree，领导力级 HPC |
| 9 | **Shamrock** | 69 | C++ | CECILL-2.1 | 多 GPU 天体 SPH 框架：笔记本→超算无缝扩展，MHD |
| 10 | **SPHERA** | 56 | Fortran | GPL-3.0 | 意大利 RSE 工业 LCFD/SPH：溃坝/晃荡/滑坡涌波，实验验证 |

### Tier 2 — 可微/框架/平台

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---|---:|---|---|---|
| 11 | **SPH_Taichi** | 322 | Python | MIT | Taichi 高性能 SPH：CPU/GPU 一行切换，研究原型利器 |
| 12 | **CPP-Fluid-Particles** | 250 | CUDA | GPL-3.0 | CUDA WCSPH/DFSPH/PBD 三求解器，GPU SPH 模式参考 |
| 13 | **jax-sph** | 84 | JAX | MIT | 可微 SPH in JAX：梯度优化/反问题/ML 管线桥接 |
| 14 | **PositionBasedDynamics** | 2294 | C++ | MIT | PBD 框架：DFSPH/IISPH 流体 + FEM 弹性耦合（SPH 近亲） |
| 15 | **inductiva** | 40 | Python | Apache-2.0 | 云仿真平台：API 驱动 DualSPHysics 大规模计算 |
| 16 | **SPHinXsim** | 19 | C++/Py | MIT | SPHinXsys 的 Python/GUI/Agent 接口：工作流自动化 |

### Tier 3 — 专项能力

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---|---:|---|---|---|
| 17 | **SPH-Tutorial** | 200 | C++ | MIT | 最全面 SPH 教程：逐步推导+代码， pedagogical 标杆 |
| 18 | **sph-python** | 66 | Python | GPL-3.0 | 极简 Python 天体 SPH：toy star 坍缩，教学系列 |
| 19 | **sarracen** | 22 | Python | GPL-3.0 | SPH 分析可视化库：宇宙学 SPH 数据渲染 |
| 20 | **GraSPH** | 23 | C++ | LGPL-3.0 | GPU 星形成 SPH：实时显卡模拟 |
| 21 | **WeldForm** | 21 | C++ | GPL-3.0 | WCSPH 弹塑性+热耦合：焊接仿真，并行 |
| 22 | **lammps-sph-multiphase** | 19 | C++ | — | LAMMPS SPH 相变：沸腾/蒸发/颜色梯度多相 |
| 23 | **sph-blitz** | 13 | C | GPL-3.0 | 2D 多相 SPH/SDPD：紧凑 C 实现 |
| 24 | **TiSPH_multiphase** | 5 | Python | — | 隐式多相 SPH：不可压多流体研究代码 |
| 25 | **sigmaSPH-release** | 2 | Python | NOASSERTION | Taichi GPU SPH：流体+弹塑性固体统一框架 |

---

## 四、feature_ledger GAP 锚点提议

对标 LBM 战役 `GAP-LBM-MCMP-*` 命名法，提议 SPH 域 8 个 GAP 锚点：

| GAP ID | 标题 | 优先级 | 锚定仓库 | 验证目标 |
|---|---|---|---|---|
| **GAP-SPH-WCSPH-VALIDATION** | WCSPH 基准验证面 | 🔴 high | SPlisHSPlasH, DualSPHysics, SPHinXsys | 2D dam-break / Poiseuille / Taylor-Green 对标实验/解析解 |
| **GAP-SPH-ISPH-CONVERGENCE** | ISPH/PCISPH/DFSPH 压力收敛 | 🔴 high | SPlisHSPlasH, PBD, jax-sph | 密度误差 <1%、压力振荡量化、时间步敏感性 |
| **GAP-SPH-FSI-BENCHMARK** | 流固耦合基准 | 🔴 high | SPHinXsys, PBD, DualSPHysics | 弹性梁溃坝冲击 / Turek-Hron FSI-3 对标 |
| **GAP-SPH-GPU-SCALING** | GPU/HPC 扩展性基准 | 🟡 medium | SPH-EXA, Shamrock, DualSPHysics | 10⁶–10⁹ 粒子弱强扩展，单卡/多卡/MPI |
| **GAP-SPH-DIFFERENTIABLE** | 可微 SPH 反问题验证 | 🟡 medium | jax-sph, SPH_Taichi | AD vs 有限差分梯度精度；黏度辨识/边界重构收敛 |
| **GAP-SPH-MULTIPHASE** | 多相 SPH 界面验证 | 🟡 medium | SPHinXsys, lammps-sph, sph-blitz | 液滴振荡 / 气泡上升 / spurious current |
| **GAP-SPH-DELTA-SPH** | δ-SPH 数值耗散量化 | 🟡 medium | DualSPHysics, SPHinXsys, SPHERA | 人工扩散对自由面精度影响 / 波传播衰减 |
| **GAP-SPH-ASTRO-SEDOV** | 天体 SPH 标准测试 | 🟢 low | Phantom, OpenSPH, SWIFT, SPH-EXA | Sod / KH / Sedov / Gresho 对标文献 |

---

## 五、注意点（踩坑 / 局限 / 适用范围）

### 5.1 普查局限

1. **stars 非质量唯一指标**：天体 SPH（Phantom/SWIFT）stars 不高但论文影响力极大；工业代码（SPHERA）stars 低但实验验证充分。分级综合考虑了方法覆盖度、验证深度、活跃度。
2. **GitHub API 限制**：未认证请求 10 req/min，部分查询可能遗漏长尾仓库；clone 不通走 trees/contents API。
3. **License 识别**：部分仓库 GitHub 显示 NOASSERTION（如 Phantom 的 Fortran 项目），实际 license 在源码文件中，需逐仓确认。
4. **Fork 去重**：DualSPHysics/SPlisHSPlasH 有大量 fork，已去重只计主仓。
5. **本普查不深盘**：按 LBM 协议，wave-0 只注册候选；深盘（代码审计/精度验证/能力分级）= wave-1+。

### 5.2 与 LBM 战役的对比

| 维度 | LBM 生态 | SPH 生态 |
|---|---|---|
| 领军代码 stars | lbmpy ~500, Taichi-LBM ~300 | SPlisHSPlasH 1959, DualSPHysics 759 |
| 方法多样性 | BGK/MRT/ELBM/ Shan-Chen/相场 | WCSPH/ISPH/PCISPH/DFSPH/IISPH/PBD/δ-SPH |
| GPU 成熟度 | Taichi 主导 | CUDA 主导（DualSPHysics/GPUSPH）+ Taichi/JAX 新兴 |
| 可微 | 较弱 | jax-sph 已建立 |
| 验证面 | 碎片化 | 更碎片化（无跨代码统一基准） |
| HPC | 弱 | 强（SPH-EXA/SWIFT/Shamrock 百亿亿级） |

### 5.3 对 TensorSPH 的启示

1. **优先复用 SPlisHSPlasH 的求解器架构**：WCSPH/PCISPH/DFSPH/IISPH 全家桶 + MIT 许可，是 TensorSPH 验证面的最佳参考；
2. **GPU 路线参考 DualSPHysics/SPH-EXA**：CUDA 是 SPH GPU 的主流，Taichi 是新兴替代；
3. **可微路线参考 jax-sph**：JAX 自动微分 + SPH = 反问题/ML 耦合的天然桥梁；
4. **验证面建设是最大缺口**：8 个 GAP 锚点中 3 个 high priority 都是验证面——这是 TensorSPH 的核心价值主张。

---

## 六、误差判定

本单为普查注册（非精度验证），无计算结果需对标基准。误差判定不适用。

**诚实声明**：
- ✅ 完成：25 仓普查 + 分级 + 8 GAP 锚点提议 + 机器可解析 JSON
- ⚠️ 未完成：未逐仓 clone 审计代码（= wave-1 任务）；部分 license 需源码确认
- ⚠️ 未覆盖：GADGET-2/4（非 GitHub 公开）、SPHYNX（GitLab）、ChaNGa 等天体 SPH 代码未纳入

---

## 七、后续波次建议

| 波次 | 任务 | 优先级 |
|---|---|---|
| wave-1 | 深盘 Tier-1 前 3 仓（SPlisHSPlasH / DualSPHysics / SPHinXsys）：代码审计 + 能力分级 + 验证面搭建 | 🔴 |
| wave-2 | GAP-SPH-WCSPH-VALIDATION 实施：跨代码 dam-break 对比 | 🔴 |
| wave-3 | GPU 扩展性基准（SPH-EXA / Shamrock / DualSPHysics） | 🟡 |
| wave-4 | 可微 SPH 验证（jax-sph vs AD-FD） | 🟡 |

---

**产物清单**：
- `artifacts/oss-sph/oss_sph_registry.json` — 25 仓机器可解析注册 + 8 GAP 锚点
- `docs/oss_inventory_sph/REPORT_oss-sph-disc.md` — 本报告
