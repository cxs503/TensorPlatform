# 【盘点】geoelements/mcmp-lbm

> **scenario_key**: oss-inventory  
> **mission_id**: 8546  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8546  
> **仓库**: <https://github.com/geoelements/mcmp-lbm>  
> **commit**: `015a26f` (2023-08-08, main 分支唯一 HEAD)  
> **许可证**: MIT (license.md 声明，GitHub 显示 NOASSERTION 因缺标准 LICENSE 文件名)  
> **语言**: Python 100%（1501 行 .py）  
> **Stars/Forks/Issues**: 30 / 4 / 3 open  
> **维护者**: UT Austin geoelements 组——Qiuyu Wang (Amber1995, 16 commits) + Krishna Kumar (kks32, 3 commits)  
> **创建**: 2023-03-24；**最后推送**: 2023-08-08（**已 2+ 年未更新**）

---

## 一、案例介绍（物理背景 / 几何 / 参数表）

### 1.1 项目定位

mcmp-lbm 是一个 **3D 多组元多相 Shan-Chen 型格子 Boltzmann 求解器**，面向**非饱和流体在颗粒介质中的流动**（geoelements 组的核心研究方向）。核心卖点：

1. **Multi-Relaxation-Time (MRT) 碰撞**：D3Q19 Gram-Schmidt 矩空间， relaxation 参数按流体的动力学黏度独立设置；
2. **稀疏存储**：Taichi `pointer` 结构，2×2×2 block，仅激活流体节点分配 PDF/力/速度内存；
3. **双求解器**：`D3Q19_MC`（多组元两流体）+ `D3Q19_SC`（单组元单相）；
4. **Taichi 后端**：CPU/GPU 一键切换（`ti.init(arch=ti.gpu)` 注释态），JIT 编译。

### 1.2 物理模型

| 物理要素 | 实现 | 证据位置 |
|---|---|---|
| 格子 | D3Q19 | `lbm_parameters.py:60-72`（e_xyz_list 19 个离散速度） |
| 碰撞 | MRT（默认）/ BGK（可选） | `lbm_solver.py:567-618`（MC 碰撞）；`lbm_solver.py:878-912`（SC 碰撞） |
| 多相机制 | Shan-Chen 伪势 | `lbm_solver.py:200-210`（Intra_psi / Inter_psi） |
| 状态方程 | 指数型 ψ=1-exp(-ρ)（默认）/ Carnahan-Starling（可选） | `lbm_solver.py:155-195`（Press 函数） |
| 相间力 | F_α = -ψ_α Σ_s w_s ψ_β(x+e_s) G_αβ e_s | `lbm_solver.py:240-290`（update_force kernel） |
| 固壁 | 反弹格式（bounce-back） | `lbm_solver.py:545-550`（streaming 中 REVERSED_E 反射） |
| 边界 | 周期性 | `lbm_solver.py:520-535`（periodic_index） |
| 黏度-松弛 | τ = 3ν + 0.5；MRT 其他模态 s_other = 8(2-s_v)/(8-s_v) | `lbm_parameters.py:80-148`（update_S） |

### 1.3 示例算例

| 算例 | 几何 | 物理 | JSON |
|---|---|---|---|
| droplet | 100×100×50，CS EOS，液滴 R=12 | 接触角/液滴收缩 | `examples/droplet.json` |
| hamburg_sand | 100×100×100，TIFF 三值化砂土 CT | 水注入非饱和颗粒介质 | `examples/hamburg_sand.json` |
| spheres | 200×200×200，球体列表 | 颗粒堆积中的多相流 | `examples/spheres.json` |

---

## 二、代码架构与执行路径

### 2.1 文件结构

```
mcmp-lbm/
├── main.py              (35 行)  入口：读 JSON → 选 MC/SC → init → run
├── lbm_solver.py      (1150 行)  核心：D3Q19_MC / D3Q19_SC 类 + 全局参数 + JSON 读取
├── lbm_parameters.py   (150 行)  MRT 矩阵 M / M⁻¹ / 权重 / 松弛时间表
├── utility.py          (166 行)  几何工具：球/柱放置、位置文件读取、TIFF 读取
├── examples/                    3 个 JSON 算例 + hamburg_sand.tif + spheres_loc_radius
├── screenshots/                 3 个演示 GIF/PNG
├── pyproject.toml               Poetry 依赖（taichi, numpy, scipy, vtk, pyevtk, tifffile, sympy）
├── requirements.txt             pip 等价
└── license.md                   MIT
```

### 2.2 执行路径（MC 模式）

```
main.py:28-34
  → lbm.read_json(input_file)          # lbm_solver.py:68-150 解析 JSON → 全局变量
  → D3Q19_MC(sparse_mem=lbm.sparse)    # lbm_solver.py:222-345 构造
      ├─ Taichi field 声明（rho/psi/v/force/collide/stream/IS_SOLID/pressure）
      ├─ 稀疏/稠密内存布局选择（lbm_solver.py:252-285）
      ├─ MRT 矩阵/权重/离散速度加载
      └─ Gl/Go 互作用参数设置
  → solver.init_field()                # lbm_solver.py:555-580
      ├─ 全场初始化 ρ₁=ρ₂=ρ_s（固体）/ ρ₁=ρ₁/20, ρ₂=ρ₂（流体）
      ├─ 可选：place_fluid_sphere（液滴测试）
      └─ 平衡态 PDF 初始化 f_q = t_q × ρ
  → solver.run(nb_steps)               # lbm_solver.py:680-698 主循环
      ├─ update_psi()     # lbm_solver.py:230-240  ψ=1-exp(-ρ), pressure
      ├─ update_force()   # lbm_solver.py:243-295  Shan-Chen 相间力 + 固相吸附
      ├─ update_velocity()# lbm_solver.py:297-315  含力修正速度
      ├─ collision()      # lbm_solver.py:567-618  MRT: m → m-S(m-m_eq) → M⁻¹m
      ├─ streaming()      # lbm_solver.py:537-550  传播 + bounce-back
      └─ post_collision() # lbm_solver.py:552-560  stream→collide, 重算 ρ
```

### 2.3 关键设计决策

1. **全局变量传参**：所有参数通过 `global` 声明在模块级共享（`lbm_solver.py:1-65`），JSON 读取后直接覆盖全局——**无封装、无参数对象**，kernel 通过闭包直接引用全局。
2. **Taichi static 分支**：MRT/BGK、CS/指数 EOS、稀疏/稠密均用 `ti.static()` 编译期选择——**运行时零开销**，但每次改参需重新 JIT。
3. **双场并行**：MC 模式同时维护 f_1/f_2、ρ_1/ρ_2、v_1/v_2、force_1/force_2——**两套 PDF 独立碰撞、共享力计算**。
4. **稀疏存储**：`ti.root.pointer(ijk, (lx/2, ly/2, lz/2)).dense(ijk, (2,2,2))`——2³ block 粒度，仅流体节点分配 PDF/力/速度；IS_SOLID/ρ/ψ 仍稠密（`lbm_solver.py:260-285`）。

---

## 三、功能清单与证据分级

### 3.1 求解器能力

| 功能点 | 证据等级 | 证据位置 | 备注 |
|---|---|---|---|
| D3Q19 格子 | **REAL** | `lbm_parameters.py:60-72` | 标准 D3Q19 离散速度 + 权重 |
| MRT 碰撞（Gram-Schmidt） | **REAL** | `lbm_parameters.py:8-50`（M 矩阵）；`lbm_solver.py:567-618` | 19×19 变换矩阵 + 对角松弛；s_other 公式正确 |
| BGK 碰撞 | **REAL** | `lbm_solver.py:600-618`（MC）；`lbm_solver.py:895-912`（SC） | 标准 BGK 平衡态 |
| Shan-Chen 多相（指数 ψ） | **REAL** | `lbm_solver.py:210-215`（Inter_psi）；`lbm_solver.py:240-290`（力） | ψ=1-exp(-ρ)，标准 SC 互作用 |
| Shan-Chen 多相（CS EOS） | **REAL** | `lbm_solver.py:155-180`（Press）；`lbm_solver.py:200-210`（Intra_psi） | Carnahan-Starling + Intra_psi=√(-2(p-c_s²ρ)/c_s²) |
| 多组元（两流体） | **REAL** | `lbm_solver.py:222-345`（D3Q19_MC 类） | 双场独立 PDF + 耦合互作用力 |
| 单组元单相 | **REAL** | `lbm_solver.py:700-960`（D3Q19_SC 类） | 单场，支持 MRT/BGK |
| 固壁 bounce-back | **REAL** | `lbm_solver.py:545-550` | 标准反弹，REVERSED_E 索引 |
| 周期性边界 | **REAL** | `lbm_solver.py:520-535` | 六面周期 |
| 稀疏存储 | **REAL** | `lbm_solver.py:260-285` | Taichi pointer 结构，2³ block |
| 接触角/液滴测试 | **REAL** | `lbm_solver.py:555-580`（init_field 中 place_fluid_sphere） | 液滴初始化 + 固壁 |
| TIFF/颗粒结构读取 | **REAL** | `utility.py:120-142`（read_tiff）；`utility.py:82-118`（read_positions） | 三值化 CT 图像 → 固体掩码 |
| VTK 输出 | **REAL** | `lbm_solver.py:620-670`（export_VTK） | pyevtk gridToVTK，输出 ρ/v/ψ/force/pressure |

### 3.2 缺失 / MOCK / STUB 能力

| 功能点 | 证据等级 | 说明 |
|---|---|---|
| 单元测试 / 回归测试 | **NOT_SUPPORTED** | 无任何 test_*.py / pytest / CI 配置 |
| CI / GitHub Actions | **NOT_SUPPORTED** | 无 .github/workflows/ |
| 文档 / API 说明 | **NOT_SUPPORTED** | 仅 README 3 段 + 示例 GIF |
| 高密度比（ρ_l/ρ_g > 100） | **NOT_SUPPORTED** | 指数 ψ 或 CS EOS 均未验证高密度比稳定性；示例 ρ_l/ρ_g ≈ 0.3/0.04 ≈ 7.5 |
| 自由面（free surface） | **NOT_SUPPORTED** | 无气相追踪/VOF/Level-Set |
| 相场（Allen-Cahn / Cahn-Hilliard） | **NOT_SUPPORTED** | 纯 Shan-Chen 伪势，无守恒序参量方程 |
| GPU 融合 kernel | **NOT_SUPPORTED** | Taichi 自动 JIT，但 kernel 粒度 = 一步一核（update_psi / update_force / update_velocity / collision / streaming 各自独立），无手动融合 |
| 分布式 MPI | **NOT_SUPPORTED** | 无 MPI / 多卡支持 |
| 可微 / 伴随 / AD | **NOT_SUPPORTED** | 无 autograd / JAX / Torch 接口 |
| 代码生成 / 符号推导 | **NOT_SUPPORTED** | MRT 矩阵硬编码 numpy，无 sympy 生成 kernel（sympy 在依赖中但未使用） |
| 多孔介质渗透率验证 | **NOT_SUPPORTED** | 有汉堡砂算例但无渗透率对标 |
| `read_voxels` 函数 | **BUG** | `lbm_solver.py:126` 调用 `ut.read_voxels()` 但 `utility.py` 中**未定义**此函数——运行时会 AttributeError |

---

## 四、代码质量与工程成熟度

### 4.1 代码体量

| 文件 | 行数 | 职责 |
|---|---:|---|
| lbm_solver.py | 1150 | 核心求解器 + 全局参数 + JSON 读取 |
| lbm_parameters.py | 150 | MRT 矩阵 / 权重 / 松弛时间 |
| utility.py | 166 | 几何工具 |
| main.py | 35 | 入口 |
| **合计** | **1501** | — |

### 4.2 工程问题

| 问题 | 严重度 | 证据 |
|---|---|---|
| **`read_voxels` 未定义** | 🔴 高 | `lbm_solver.py:126` 调用但 `utility.py` 无此函数 |
| **全局变量传参** | 🟡 中 | `lbm_solver.py:1-65` 全部 `global`，无封装 |
| **MRT 导致 NaN（用户报告）** | 🔴 高 | GitHub Issue #4 (2025-11-04)：hamburg_sand 用 MRT 出 NaN |
| **流体初始化不可控** | 🟡 中 | GitHub Issue #3 (2024-02-05)：无法控制流体初始分布 |
| **无测试** | 🔴 高 | 零 test / 零 CI |
| **无文档** | 🟡 中 | 仅 README，无 API doc / 理论推导 |
| **sympy 依赖未使用** | 🟢 低 | `pyproject.toml` 列 sympy 但代码未 import |
| **`inverse_mellin_transform` 未使用** | 🟢 低 | `main.py:5` import 但从未调用 |
| **注释 "D2Q9" 残留** | 🟢 低 | `lbm_solver.py:115` 注释写 "D2Q9 relaxation parameters" 但实际是 D3Q19 |
| **license 文件名非标准** | 🟢 低 | `license.md` 非 `LICENSE` / `LICENSE.md`，GitHub 识别为 NOASSERTION |

### 4.3 维护状态

- **最后更新**: 2023-08-08（2+ 年前）
- **Issue 响应**: 4 个 issue 中 3 个 open，最新 #4 (2025-11-04) 无人回复
- **贡献者**: 2 人（Qiuyu Wang 主力 + Krishna Kumar 维护）
- **结论**: **低活跃度 / 教学-研究原型级**，非生产就绪

---

## 五、技术细节深盘

### 5.1 MRT 实现

MRT 矩阵 M 为 19×19 Gram-Schmidt 正交化矩阵（`lbm_parameters.py:8-50`），松弛时间设置：

```python
# lbm_parameters.py:80-148
tau_lf = 3.0 * niu_l + 0.5       # 动力学黏度 → 剪切松弛时间
s_lv = 1.0 / tau_lf              # 剪切模态松弛率
s_lother = 8.0 * (2.0 - s_lv) / (8.0 - s_lv)  # 高阶模态松弛率（Lallemand & Luo 2000 推荐）
```

**问题**：MRT 矩阵的模态排序与平衡态矩 `meq_vec`（`lbm_solver.py:320-345`）需严格对应，但代码中 `meq_vec` 的赋值顺序（out[0]=1, out[3]=u_x, out[5]=u_y, out[7]=u_z, out[1]=|u|², ...）与 M 矩阵的行排序是否一致**未验证**——这可能是 Issue #4（MRT NaN）的根源。

### 5.2 Shan-Chen 力项

```python
# lbm_solver.py:240-290 (update_force kernel)
force_1[i] += -psi_2[neighbor] * psi_1[I] * w[s] * Gl[IS_SOLID[neighbor]] * e_xyz[s][i]
            + -Intra_psi(rho_1[neighbor]) * Intra_psi(rho_1[I]) * w[s] * Gl[2] * e_xyz[s][i] * (1 - IS_SOLID[neighbor])
```

- **Gl[0] = G_12**（流体 1-2 互作用）
- **Gl[1] = G_1s**（流体 1-固体吸附）
- **Gl[2] = G_11**（流体 1 内聚，多相开关）

力项采用**标准 Shan-Chen 形式** F = -G ψ_α Σ w_s ψ_β e_s，但**未做力修正**（如 Guo forcing scheme），直接加到速度上（`lbm_solver.py:310-315`）：

```python
self.v_1[I][i] = (temp_vel_1 + self.force_1[I][i] * tau_1f) / self.rho_1[I]
```

这种**简单力添加**在低 τ 或高密度比下可能导致不稳定。

### 5.3 稀疏存储

```python
# lbm_solver.py:260-285
n_mem_partition = 2  # 2×2×2 block
ti.root.dense(ti.ijk, (lx, ly, lz)).place(IS_SOLID, psi_1, rho_1, psi_2, rho_2)  # 稠密
cell = ti.root.pointer(ti.ijk, (lx//2+1, ly//2+1, lz//2+1))
cell.dense(ti.ijk, (2,2,2)).place(force_1, v_1, force_2, v_2, collide_f_1, ...)  # 稀疏
```

- **稠密场**: IS_SOLID / ψ / ρ（每节点都需要，无论固/液）
- **稀疏场**: force / v / PDF（仅流体节点激活）
- **block 粒度**: 2³ = 8 节点，pointer 结构按需分配

**局限**：Taichi 稀疏结构在 CPU 后端性能收益有限（pointer 查表开销）；GPU 后端需验证稀疏激活是否真正节省显存。

### 5.4 状态方程

**指数型（默认）**:
```python
# lbm_solver.py:190-195
psi = 1.0 - exp(-rho)
pressure = cs2 * rho + cs2 * G_1s / 2 * psi^2
```

**Carnahan-Starling（可选）**:
```python
# lbm_solver.py:160-180
Tc = 0.0943; T = T_Tc * Tc
eta = b * rho / 4  # b=4
pressure = rho * R * T * (1 + eta + eta^2 - eta^3) / (1-eta)^3 - a * rho^2  # a=1
```

CS EOS 的 Intra_psi 由 `√(-2(p-c_s²ρ)/c_s²)` 给出（`lbm_solver.py:200-210`），确保力项与 EOS 自洽。

---

## 六、可微 / 代码生成 / ML 耦合

- **可微 / 代码生成：两者都没有。** 无 autograd、无伴随、无 JAX/Torch 接口；sympy 在依赖中但代码未使用（`pyproject.toml:14`）；MRT 矩阵硬编码 numpy，无符号推导。
- **ML 代理耦合：无直接实现。** 但 VTK 输出（`lbm_solver.py:620-670`）可作为 ML 代理的训练数据源；Taichi 后端理论上可嵌入 PyTorch（通过 `ti.from_torch` / `ti.to_torch`），但代码未做。
- **对我们的含义**：无法直接借用 ML 耦合能力；但其 VTK 输出范式 + Taichi 后端可作为「LBM 求解器 → ML 代理」管线的参考实现。

---

## 七、与我们功能点的映射（feature_ledger）

| feature_ledger slug | transfer | 本仓实现方式/证据 |
|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE` | `reference` | **部分命中**：本仓有 MCMP 两流体 + Shan-Chen 互作用力（`lbm_solver.py:240-290`），但**无 Poiseuille 流验证算例**；可参考其力项实现 + 周期性边界（`lbm_solver.py:520-535`）搭建两组分分层 Poiseuille。**风险**：力项未做 Guo 修正，低 τ 下可能不稳定。 〔`lbm_solver.py:240-290`；`lbm_solver.py:520-535`〕 |
| `MCMP 高密度比` | `reference` | **本仓未验证高密度比**：示例 ρ_l/ρ_g ≈ 7.5（`examples/droplet.json`），远低于 100+；CS EOS 理论上支持更高密度比，但**无稳定性验证**。可参考其 CS EOS 实现（`lbm_solver.py:160-180`）+ Intra_psi 自洽力项（`lbm_solver.py:200-210`），但须自建高密度比验收面（spurious current + 密度比扫描）。 〔`lbm_solver.py:160-180`；`lbm_solver.py:200-210`；`examples/droplet.json`〕 |
| `自由面（free surface）` | `NOT_SUPPORTED` | **本仓无自由面模型**：纯 Shan-Chen 多相，无气相追踪 / VOF / Level-Set；不可直接复用。 |
| `相场 Allen-Cahn` | `NOT_SUPPORTED` | **本仓无相场方程**：无 Cahn-Hilliard / Allen-Cahn；纯伪势多相，不可直接复用。 |
| `GPU 融合 kernel` | `reference` | 本仓 kernel 粒度 = 一步一核（update_psi / update_force / update_velocity / collision / streaming 各自独立，`lbm_solver.py:680-698`），**无手动融合**；但 Taichi 后端自动 JIT，理论上可做 kernel 融合（需改 Taichi scope）。可参考其 Taichi field 组织 + 稀疏存储（`lbm_solver.py:260-285`）。 〔`lbm_solver.py:680-698`；`lbm_solver.py:260-285`〕 |
| `分布式多卡` | `NOT_SUPPORTED` | **本仓无 MPI / 多卡支持**：Taichi 单进程 GPU，无域分解；不可直接复用。 |
| `稀疏/多孔` | `reference` | **本仓有稀疏存储 + 多孔介质算例**：Taichi pointer 结构（`lbm_solver.py:260-285`）+ hamburg_sand TIFF 读取（`utility.py:120-142`）+ 球体列表（`utility.py:82-118`）；但**无渗透率验证**，仅演示。可参考其「TIFF → 固体掩码 → bounce-back」范式搭多孔验证面。 〔`lbm_solver.py:260-285`；`utility.py:120-142`；`examples/hamburg_sand.json`〕 |
| `案例组织` | `reference` | 本仓用 JSON 输入（`lbm_solver.py:68-150`）+ 目录即案例（`examples/`），但**无参数化扫描 / 无自动化案例生成**；可参考其 JSON schema 组织参数。 〔`lbm_solver.py:68-150`；`examples/`〕 |
| `测试与验证面` | `NOT_SUPPORTED` | **本仓无任何测试**：零 test / 零 CI / 零回归比对；不可复用。 |
| `CI` | `NOT_SUPPORTED` | **本仓无 CI**：无 .github/workflows/；不可复用。 |

**统计**：reference=4、NOT_SUPPORTED=6、adapt=0、fits=0。

---

## 八、可吸收 Top3 + 风险

### Top1｜Shan-Chen MCMP 两流体 + CS EOS 参考实现（→ TensorLBM 多相模块）

- **价值**：本仓是**最简 MCMP Shan-Chen 实现**（1501 行 Python），双场独立 PDF + 耦合互作用力 + CS EOS 自洽 Intra_psi，代码清晰可读；可作为 TensorLBM 多相模块的**最小参考实现**，快速验证 Shan-Chen 力项 + 状态方程。
- **证据**：`lbm_solver.py:222-345`（D3Q19_MC 类）；`lbm_solver.py:160-210`（CS EOS + Intra_psi）；`lbm_solver.py:240-290`（力项）。
- **风险**：
  1. **力项未做 Guo 修正**（`lbm_solver.py:310-315` 直接加力到速度），低 τ / 高密度比下可能不稳定——Issue #4 报告 MRT NaN 可能与此相关；
  2. **MRT 矩阵与平衡态矩对应关系未验证**（`lbm_parameters.py:8-50` vs `lbm_solver.py:320-345`），可能是 NaN 根源；
  3. **无高密度比验证**（示例 ρ_l/ρ_g ≈ 7.5），移植到 100+ 密度比须重新验证稳定性。
- **吸收策略**：先复现其指数 ψ 算例（droplet），验证力项 + 周期性边界；再扩展 CS EOS + 高密度比扫描；最后对标解析解 / 文献。

### Top2｜Taichi 稀疏存储 + GPU 后端范式（→ TensorLBM 性能优化）

- **价值**：本仓用 Taichi `pointer` 结构实现 2³ block 稀疏存储（`lbm_solver.py:260-285`），仅流体节点分配 PDF/力/速度内存；Taichi 后端支持 CPU/GPU 一键切换（`main.py:10`）。可作为 TensorLBM **稀疏多孔介质求解器**的参考实现，验证 Taichi 稀疏结构在 LBM 中的性能收益。
- **证据**：`lbm_solver.py:260-285`（稀疏布局）；`main.py:10`（`ti.init(arch=ti.cpu)` 注释态 GPU）。
- **风险**：
  1. **CPU 后端稀疏收益有限**（pointer 查表开销）；
  2. **GPU 后端稀疏激活是否真正节省显存未验证**；
  3. **kernel 粒度 = 一步一核**（`lbm_solver.py:680-698`），无手动融合，GPU 性能可能受限于 kernel launch 开销。
- **吸收策略**：在 TensorLBM 复刻其稀疏结构，benchmark 对比 dense vs sparse（CPU/GPU 双后端）；若收益显著，扩展到 3D 多孔介质算例。

### Top3｜TIFF 三值化 CT → 固体掩码 → bounce-back 范式（→ TensorPlatform 多孔验证面）

- **价值**：本仓用 `tifffile` 读取三值化 CT 图像（`utility.py:120-142`），直接映射为 IS_SOLID 掩码，bounce-back 处理固壁（`lbm_solver.py:545-550`）；hamburg_sand 算例（`examples/hamburg_sand.json`）演示真实砂土 CT 中的多相流。可作为 TensorPlatform **多孔介质验证面**的参考实现，快速搭建「CT 图像 → LBM 求解 → VTK 输出」管线。
- **证据**：`utility.py:120-142`（read_tiff）；`lbm_solver.py:545-550`（bounce-back）；`examples/hamburg_sand.json`（算例）。
- **风险**：
  1. **无渗透率验证**：仅演示流动，未对标 Darcy 定律 / 实验渗透率；
  2. **TIFF 预处理（三值化 / 降采样）未说明**：`utility.py:135-138` 直接 `np.sign(grains)`，假设输入已三值化；
  3. **`read_voxels` 函数未定义**（`lbm_solver.py:126` 调用但 `utility.py` 无此函数），运行时会 AttributeError。
- **吸收策略**：在 TensorPlatform 复刻其 TIFF 读取 + bounce-back 管线，补充渗透率验证（Darcy 定律对标）；修复 `read_voxels` bug 或移除调用。

### 8.1 明确不可吸收 / 需自建的清单

- **无测试 / 无 CI** → 测试验证面须自建（解析解 / 文献基准 / 守恒量检查）。
- **无高密度比验证** → 高密度比能力须自建（变密度平衡态 + spurious current 验收）。
- **无自由面 / 相场** → 自由面 + 相场须自建或另找来源。
- **无 MPI / 分布式** → 分布式须自建（域分解 + halo 交换）。
- **无 AD / 代码生成** → 可微 LBM 须自建（JAX / Taichi AD / 符号推导）。
- **力项无 Guo 修正** → 高精度力项须自建（Guo forcing / exact difference method）。
- **MRT 矩阵对应关系未验证** → 须自建 MRT 验收面（解析解 / 守恒量检查）。

---

## 附录 A：GitHub 元数据

```
full_name: geoelements/mcmp-lbm
description: A 3D multicomponent multiphase lattice Boltzmann solver with a Multi-Relaxation-Time collision scheme and a sparse storage structure
language: Python
default_branch: main
size: 218402 KB
stargazers_count: 30
forks_count: 4
open_issues_count: 3
created_at: 2023-03-24
pushed_at: 2023-08-08
license: MIT (NOASSERTION on GitHub)
contributors: Amber1995 (16 commits), kks32 (3 commits)
```

## 附录 B：Open Issues

| # | Title | State | Created |
|---|---|---|---|
| 4 | Why does using MRT result in NAN?? hamburg_sand case | open | 2025-11-04 |
| 3 | how can we control fluid initializations? | open | 2024-02-05 |
| 1 | "3d_hamburg_injection.gif" how to do it? | open | 2023-09-08 |

## 附录 C：Commit 历史（2023-03-24 ~ 2023-08-08）

```
015a26f 2023-08-08 Krishna Kumar: Fix authors
d1abc07 2023-08-05 Krishna Kumar: Update issue link to geoelements repo
c9b7a60 2023-08-05 Krishna Kumar: Add installation instructions
66d525c 2023-04-03 Qiuyu Wang: Add the contributors
53a6c6f 2023-04-03 Qiuyu Wang: Show examples in README
e0af824 2023-04-02 Qiuyu Wang: Move input files to folder
1ab7e25 2023-04-02 Qiuyu Wang: add functions of reading grain structure and examples
2b0b374 2023-04-02 Qiuyu Wang: Rename files and update remote url
fcee089 2023-03-24 Qiuyu Wang: setup
2c0524c 2023-03-24 Amber1995: Initial commit
```

---

[OSS-INV]

```
scenario_key: oss-inventory
title: 【盘点】geoelements/mcmp-lbm
tier: 1
repo: geoelements/mcmp-lbm
commit: 015a26f
language: Python
loc: 1501
license: MIT
stars: 30
last_push: 2023-08-08
maintainer: UT Austin geoelements (kks32, Amber1995)
status: low_activity (2+ years no update)
```

[OSS-SOL]

```
feature: D3Q19 MRT
evidence: REAL
location: lbm_parameters.py:8-50, lbm_solver.py:567-618
notes: Gram-Schmidt 19x19 matrix; s_other = 8(2-s_v)/(8-s_v)
```

[OSS-SOL]

```
feature: Shan-Chen MCMP (exponential psi)
evidence: REAL
location: lbm_solver.py:210-215, 240-290
notes: psi=1-exp(-rho); standard SC interaction force
```

[OSS-SOL]

```
feature: Shan-Chen MCMP (Carnahan-Starling EOS)
evidence: REAL
location: lbm_solver.py:160-180, 200-210
notes: CS EOS + Intra_psi = sqrt(-2(p-c_s^2 rho)/c_s^2)
```

[OSS-SOL]

```
feature: Sparse storage (Taichi pointer)
evidence: REAL
location: lbm_solver.py:260-285
notes: 2x2x2 block; only fluid nodes allocate PDF/force/velocity
```

[OSS-SOL]

```
feature: Bounce-back solid boundary
evidence: REAL
location: lbm_solver.py:545-550
notes: Standard bounce-back with REVERSED_E index
```

[OSS-SOL]

```
feature: TIFF CT image import
evidence: REAL
location: utility.py:120-142
notes: Trinarized CT -> solid mask via np.sign()
```

[OSS-SOL]

```
feature: Unit tests / regression tests
evidence: NOT_SUPPORTED
location: N/A
notes: Zero tests, zero CI
```

[OSS-SOL]

```
feature: High density ratio (>100)
evidence: NOT_SUPPORTED
location: N/A
notes: Demo rho_l/rho_g ~ 7.5; no stability validation
```

[OSS-SOL]

```
feature: Free surface
evidence: NOT_SUPPORTED
location: N/A
notes: No VOF / Level-Set / free surface model
```

[OSS-SOL]

```
feature: Phase field (Allen-Cahn / Cahn-Hilliard)
evidence: NOT_SUPPORTED
location: N/A
notes: Pure pseudopotential, no conserved order parameter
```

[OSS-SOL]

```
feature: GPU fused kernel
evidence: NOT_SUPPORTED
location: N/A
notes: One kernel per step; no manual fusion
```

[OSS-SOL]

```
feature: Distributed MPI
evidence: NOT_SUPPORTED
location: N/A
notes: Single-process Taichi, no domain decomposition
```

[OSS-SOL]

```
feature: Guo forcing correction
evidence: NOT_SUPPORTED
location: N/A
notes: Force added directly to velocity (lbm_solver.py:310-315); may cause instability at low tau
```

[OSS-SOL]

```
feature: MRT matrix-moment consistency validation
evidence: NOT_SUPPORTED
location: N/A
notes: M matrix (lbm_parameters.py:8-50) vs meq_vec (lbm_solver.py:320-345) ordering not verified; may cause NaN (Issue #4)
```
