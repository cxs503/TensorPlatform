# OSS-SPH 深盘 wave-2：Xiangyu-Hu/SPHinXsim 源码级盘点

> **scenario_key**: osssph-w2  ·  **campaign**: osssph-w2（集团军） ·  **mission_id**: 8665
> **target**: `Xiangyu-Hu/SPHinXsim` (tier 2)  ·  **盘点日期**: 2026-11-09  ·  **agent**: agent/m8665
> **HEAD**: `5241a8902019e2b61559460748342dd9c29bd2a9` (2026-11-09T12:32:20Z, branch `main`)
> **机器产物**: `artifacts/oss-sph/w2/Xiangyu-Hu_SPHinXsim.inv.json`（唯一数字来源，脚本生成）
> **生成脚本**: `scripts/oss_audit/sphinxsim_inventory.py`（JSON + 图 + 表全部程序化产出，零手抄）
> **图表**: `docs/oss_inventory_sph/figures/fig1_sphinxsim_method_matrix.png`、`fig2_sphinxsim_scale_validation.png`
> **协议**: 与 LBM 深盘同构（GitHub API 实查 → 源码级方法清单 → 验证面 → 可迁移方案 → GAP 锚）；不建 PR

---

## ① 案例介绍（对象背景 / 结构 / 参数表）

### 1.1 这是什么

SPHinXsim 不是一个新的 SPH 求解器，而是 **SPHinXsys 的「agent-ready」外围平台**：JSON-first 的算例输入契约 +
CAD 几何生成 + 原生 C++ 配置编译 + 持久化作业管理 + MCP/CLI/LLM 接口 + PyVista 可视化，C++ 数值核心以
**vendored（整棵源码入库）** 方式随仓携带（`sphinxsim/sphinxsys/`，自带 Apache-2.0 LICENSE 与 TUM 版权头）。

> **对 m8638 普查初判的更正（重要）**：普查把本仓记为「SPHinXsys 简化/教学版」。实查反驳该判断——
> 仓内 vendored 的是**完整上游核心**：608 个 C++ 文件 / **84 847 行**（`sphinxsim/sphinxsys/src/`），
> 含 WCSPH + Riemann 耗散 + 密度正则化 + 多相 + TLSPH/FSI + Eulerian/FVM 分支 + 70 个 2D、44 个 3D 算例目录。
> 「简化」的只是**对外暴露面**（JSON 契约 + builder 层 6 783 行把算法固定成可配置组合），不是数值内核。

### 1.2 仓库元数据（GitHub API 实查，2026-11-09）

| 字段 | 值 |
|---|---|
| full_name | `Xiangyu-Hu/SPHinXsim`（fork=false, archived=false） |
| language（API 主语言） | **C++**（字节数：C++ 7 091 004 / Python 1 077 007 / CMake 238 682 …） |
| license（API/文件） | **MIT**（根 `LICENSE`, Copyright (c) 2026 Xiangyu Hu） |
| stars / forks / open_issues | **19 / 11 / 25** |
| size | 69 196 KB |
| created / pushed | 2026-03-01T12:34:25Z / **2026-11-09T12:33:13Z（当日活跃）** |
| default_branch | `main`（HEAD 见文首） |
| homepage | https://xiangyu-hu.github.io/SPHinXsim/ |
| description | "The python, graphic UI and host-agnostic agentic interface for the C++ multi-physics library SPHinXsys" |

**近 8 次提交节奏**（`GET /commits?per_page=8`）：当日 5 次（`Merge #215`, `introduce agent`, `Merge #208 
codex/sphinxsim-agent-integration`），前一日 3 次（CI 下载重试、agent guides、native editor 测试）→ 处于
「agent 集成」冲刺期，非停滞仓。

### 1.3 目录与分层（tracked 2 499 文件）

| 层 | 路径 | 规模 | 性质 |
|---|---|---|---|
| 数值核心（vendored 上游） | `sphinxsim/sphinxsys/src/` | 608 文件 / 84 847 行 | SPHinXsys Apache-2.0 全量（含 `shared_ck/` 新版 CK API 140 文件） |
| 上游算例库（vendored） | `sphinxsim/sphinxsys/tests/` | 1 487 文件（70×2D + 44×3D 目录，857 回归台文件） | C++ 算例 + XML 自参照回归 |
| 配置编译胶水 | `sphinxsim/sph_simulation/` | 39 文件 / 6 783 行 | builder 层：把 JSON 契约映射成 `sphinxsim_project.cpp` 的算法组合 |
| Python 平台 | `sphinxsim/`（`runtime/`, `llm/`, `desktop/`, `config/`, `bindings/`） | 39 文件 / 12 966 行 | CLI/MCP/作业/结果/LLM provider 适配 |
| 自身测试 | `tests/`（15 py + 2 C++ 仿真类） | — | schema/CLI 冒烟，无容差断言 |
| CI | `.github/workflows/ci.yml`（Linux/macOS/Windows）、`docs.yml` | — | ctest + pytest，**上游物理算例库不构建** |

### 1.4 默认数值参数（源码取值，非我的设定）

| 参数 | 值 | 定位 |
|---|---|---|
| 声学 CFL | **0.6** | `fluid_dynamics/fluid_time_step.h:48` `AcousticTimeStep(body, acousticCFL=0.6)` |
| 对流 CFL | **0.25** | 同文件 `:132` `AdvectionTimeStep(body, U_ref, advectionCFL=0.25)` |
| 声速比 | `c_f = 10 × U_ref`（算例级约定） | `tests/2d_examples/test_2d_dambreak/Dambreak.cpp:25` |
| Riemann 限制器 | `TruncatedLinear`（CK 默认）/ `NoLimiter`（全耗散） | `shared_ck/.../riemann_solver_ck.h:172-173` |
| 多时间步结构 | advection 外层触发 + acoustic 内层自适应 | `shared_ck/particle_dynamics/sph_solver.h:64`；循环 `fluid_simulation_builder.cpp:122` |

---

## ② 怎么做的（方法 / 流程 / 工具链 / 关键设置）

1. **GitHub API 实查（禁凭记忆）**：`GET /repos/Xiangyu-Hu/SPHinXsim`、`/languages`、`/commits?per_page=8`。
   未认证限额 60 请求/小时，中途 `remaining=0`（限额重置 2026-11-09T15:16Z 后补查 `fork/archived/open_issues`）。
2. **限额期改用 git 协议取源码**（不占 API 配额，也不依赖第三方镜像）：
   `git clone --depth 1 --filter=blob:none --no-checkout` → `git sparse-checkout`（排除上游 `tests/`、`tutorials/`、
   `docs/`、`assets/`，即只落核心 `src/` + Python 平台 + 配置）。
   *并行的整树 tarball 下载在共享带宽下反复中断（36 MB 不完整），故采用 blobless clone；上游算例文件按需 `git show` 取单文件。*
3. **源码级方法定位**：对每个方法族做定向 `grep -rniE` + `grep -n`，把命中写入 `evidence[{file,line,what}]`
   （全部相对仓库根，HEAD 5241a890 可复算）。否定结论也用同一手段取证（`isph|pcisph|dfsph|iisph|poisson` → 0 命中）。
4. **验证面盘点**：`git ls-tree -r HEAD --name-only` 统计算例目录 / 回归台文件；抽查算例源码是否含解析解或实验数据比对；
   检查 CI（`.github/workflows/ci.yml`）实际跑什么、构建开关是否把上游算例库关掉。
5. **license 判定**：读根 `LICENSE`（MIT）与 `sphinxsim/sphinxsys/LICENSE`（Apache-2.0）及文件头版权声明；
   查 `sphinxsim/sphinxsys/.gitmodules`（唯一子模块 `3rd_party/wasmtbb`，内容未检出 → 标 insufficient）。
6. **证据机器复核**：脚本内置 `--verify`，对 JSON 中每条证据回查 `git show HEAD:<file>` 的行内容 / 对零命中类断言按 `grep_pattern` 重跑 grep。本轮 **51 条证据全部 PASS、0 FAIL**（复核中修正了 4 处真错：`hemisphere` 误匹配 `isph`、`polar decomposition … based on` 误匹配 PBD 模式、2 条算例 JSON 路径写错——均已改正后重跑）。
7. **产物生成**：`scripts/oss_audit/sphinxsim_inventory.py --checkout /tmp/sxs2` 一次性产出
   JSON（事实+证据+判定）、两张图、两张数字表；本报告的表格与图数字全部来自该脚本输出（零手抄）。

复算命令（任一环境可跑）：

```bash
python3 scripts/oss_audit/sphinxsim_inventory.py --checkout <clone-dir> \
        --out artifacts/oss-sph/w2 --figdir docs/oss_inventory_sph/figures
```

---

## ③ 计算结果（程序化产出，零手抄）

### 3.1 规模与构成

| 项 | 数值 |
|---|---:|
| tracked 文件（HEAD） | 2 499 |
| vendored 上游核心 | 84 847 行 / 608 文件（**98.0%** 的 C++ 代码量） |
| SPHinXsim C++ 配置胶水 | 6 783 行 / 39 文件 |
| SPHinXsim Python 平台 | 12 966 行 / 39 文件 |
| vendored 上游测试资产 | 1 487 文件 |
| SYCL 后端文件 | 11 |

### 3.2 方法矩阵（逐条 file:line 见 JSON `methods[].evidence`）

| 方法/能力 | 证据等级 | 关键定位 |
|---|---|---|
| WCSPH (weakly compressible, Tait EOS) | REAL(实现) | `weakly_compressible_fluid.cpp:17` |
| Riemann-solver dissipation (低耗散 Riemann WCSPH) | REAL(实现) | `riemann_solver.h:83` |
| Density relaxation / compression regularization | REAL(实现) | `density_summation.h:45` |
| Symplectic (kick-drift-kick) two-half acoustic integration | REAL(实现) | `fluid_integration.h:87` |
| Adaptive multi-trigger time stepping (CFL) | REAL(实现) | `fluid_time_step.h:48` |
| Boundary handling (buffer/inflow/outflow/emitter/free-stream/wall contact) | REAL(实现) | `fluid_boundary.h:65` |
| Multiphase / multi-species | REAL(实现) | `weakly_compressible_fluid.cpp:96` |
| Solid / FSI (TLSPH + 壳/杆 + 流体-结构接触) | REAL(实现) | `solid_dynamics_builder.hpp:60` |
| ISPH / PCISPH / DFSPH / IISPH (projection / implicit incompressible) | **NOT_SUPPORTED** | — |
| δ-SPH (explicit density diffusion) | **NOT_SUPPORTED** | — |
| PBD / position-based fluids | **NOT_SUPPORTED** | — |
| Eulerian / FVM branch (MUSCL-HLLC) | REAL(实现) | `eulerian_riemann_solver.h:1` |

补充要点（每条都给定位，详见 JSON）：

- **自由面**：`density_summation.h:131`（`DensitySummation<Inner<FreeSurface,…>>`）+ `density_regularization.h:200`
  （`Regularization<FreeSurface>`）/`:222`（`Regularization<FreeStream>`）→ 自由面/远场分流处理，非单一阈值。
- **壁面边界**：不用 ghost 粒子，而是「固体粒子 + `Wall` 接触核」，壁面侧压力按镜像取（`acoustic_step_1st_half.hpp:176`），
  另有 `NearWallDistance/DistanceFromWall/BoundingFromWall`（`near_wall_boundary.h:42,57,68`）与
  `FreeStreamVelocityCorrection`/`DampingBoundaryCondition`（`fluid_boundary.h:129,174`）。
- **并行**：无 MPI（`grep -rli 'mpi.h|MPI_Init' src/` → 0）；共享内存走 **TBB**（`sphinxsys_tbb.h`）；
  GPU 走 **SYCL**，但 `CMakeLists.txt:16` 默认 OFF、`:115` 仅 IntelLLVM、`:137` 默认 `spir64_x86_64`，
  且 SPHinXsim 自身构建**强制 OFF**（`CMakeLists.txt:62`）→ GPU 路径在 Python 发行物里不可用。
- **多相**：`WeaklyCompressibleMultiPhase`（`.cpp:96`，`addPurePhases` :109）+ `WeaklyCompressibleMultiSpecies`（:40）+
  `ImpedanceModel<WeaklyCompressibleMixture,…>`（`riemann_solver_ck.h:134`）→ 相界面靠密度/阻抗跳变维持，无界面重构。

### 3.3 验证面

| 验证资产 | 数量 | 性质 |
|---|---:|---|
| 上游 2D 算例目录 | 70 | 可运行 C++ 算例（**默认不构建**） |
| 上游 3D 算例目录 | 44 | 同上 |
| 回归台文件 | 857 | 自参照回归（XML `ensemble_averaged_mean_variance` + `dtwdistance` + `runtimes.dat`） |
| 上游 unit_tests_src | 27 cpp / 43 txt | 单元测试源 |
| SPHinXsim Python 测试 | 15 | schema/CLI/LLM 胶水测试 |
| **数值容差断言** | **0** | 零 → 无自动精度判据 |
| C++ 仿真类 gtest | 1 | 仅 `TEST(simulations, has_json)`（`tests/test_simulation/test_2d_simulation/simulation.cpp:115`） |

命名基准：`test_2d_dambreak`、`test_3d_dambreak`、`test_2d_two_phase_dambreak`、`test_2d_taylor_green`、
`test_2d_eulerian_taylor_green_LG`、`test_2d_poiseuille_flow`、`test_1d_shock_tube`、
`test_2d_FVM_flow_around_cylinder`、`test_2d_FVM_double_mach_reflection`。

**关键判定（诚实）**：算例库存在，但 (a) 顶层 `CMakeLists.txt:60` 把 `SPHINXSYS_BUILD_TESTS` 强制 OFF、
`pyproject.toml` 设 `SPHINXSIM_BUILD_SIMULATION_TESTS=OFF`，CI 的 `ctest` 只跑仿真类冒烟；
(b) 回归基线是**与自身历史比**，不是与解析解/实验比；(c) 抽查 `poiseuille_flow.cpp:24` 与 `Dambreak.cpp:17`
内**没有**解析剖面/实验数据比对代码。→ 量化验证数据：**无自动误差判据**；上游 benchmark 图仅以静态 png 存在
（`sphinxsim/sphinxsys/docs/assets/img/benchmark-dambreak-2d/3d.png`，本单未复算）。

### 3.4 license 判定（显式）

| 部分 | SPDX | 依据 | 合规结论 |
|---|---|---|---|
| 根仓 / Python 胶水 / 契约 | **MIT** | `LICENSE`（c2026 Xiangyu Hu） | adapt 可用（保留声明） |
| `sphinxsim/sphinxsys/`（C++ 核心） | **Apache-2.0** | 该目录自带 `LICENSE` + 文件头 "Portions copyright (c) 2017-2025 TUM" | adapt 须 Apache-2.0 合规（保留 LICENSE/NOTICE、声明改动）；**不得**把核心当 MIT |
| `3rd_party/wasmtbb`（子模块） | **unknown** | `.gitmodules` 指向 hpcwasm/wasmtbb，内容未检出 | **reference-only / 不用** |

**结论**：本仓是 MIT 外壳 + Apache-2.0 内核的混合体；对本平台的合规路径 =
Python 胶水与契约可 adapt（MIT）；数值核心代码级搬运走 Apache-2.0 合规；
`wasmtbb` 未读到 license → 不碰。

---

## ④ 结果图

**图 1** `fig1_sphinxsim_method_matrix.png` —— 12 个方法族的源码级支持矩阵（绿=REAL / 黄=部分 / 红=NOT_SUPPORTED，
数字为该族 grep 证据条数）。直接可见结构性事实：**WCSPH 一族全绿、不可压投影一族（ISPH/PCISPH/DFSPH/IISPH）
与 δ-SPH、PBD 全红（0 命中）**。

**图 2** `fig2_sphinxsim_scale_validation.png` —— 左：代码构成（vendored 核心 84 847 行 vs 胶水 6 783 行 vs
Python 12 966 行）；右：验证资产（2D 70 / 3D 44 算例目录、857 回归台文件（图内 /10）、15 Python 测试、
**0** 数值容差断言）。右侧最后一根「0」是本单最重要的负向结论。

两图均由 `scripts/oss_audit/sphinxsim_inventory.py` 从 JSON 程序化绘制（matplotlib，dpi=160），无手工数据点。

---

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **m8638 初判需更正**：不是「简化/教学版」。它 = 完整上游核心 vendor + 强约束配置面。若按「教学版」定 tier
   会低估其算法参考价值（也低估 license 风险：核心是 Apache-2.0 而非 MIT）。
2. **API 限额是真坑**：未认证 60/h，本单中途耗尽（`remaining=0`），且共享 IP 下多次耗尽。
   → 规避：git 协议 `--filter=blob:none` clone（本单做法），只在必要时打 API。
3. **tarball 下载不可靠**：共享带宽下 36 MB 后反复中断（`gzip -t` 报 unexpected end of file）；不要用「下载 zip 再解包」
   作为唯一路径，要有 blobless clone 兜底。
4. **「有测试」≠「有验证」**：本仓 1 487 个测试资产文件 + 三平台 CI，但数值容差断言 0、上游算例库构建被显式关闭。
   任何「SPHinXsim 已通过 N 个基准」的说法都必须给出构建开关与比对对象的证据，否则视为夸大。
5. **适用范围**：本单只做静态源码审计（读 + grep + 结构统计），**未在本机构建**（需 vcpkg/Qt/oneAPI 工具链，
   时间与带宽成本超出盘子）；因此所有性能/精度类结论标记为 insufficient，不给数字。
6. **CK 双 API 并存**：`src/shared/`（旧）与 `src/shared/shared_ck/`（新 continuum-kernel）两套动力学并存，
   SPHinXsim 胶水走 **CK** 一套（`AcousticTimeStepCK`、`RiemannSolver<…,TruncatedLinear>`）。照旧 API 写代码会
   与平台实际执行路径不一致——迁移时必须按 CK 定位对齐。

---

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| tracked 文件数 2 499 | `git ls-tree -r HEAD --name-only \| wc -l` | 0（同源计数） | **是** |
| vendored 核心 608 文件 / 84 847 行 | 脚本 `os.walk` 计数（同 checkout） | 0 | **是** |
| 2D/3D 算例目录 70 / 44 | `git ls-tree` 去重计数 | 0 | **是** |
| 回归台文件 857 | `grep -c "regression_test_tool/"` | 0 | **是** |
| grep 命中 0（ISPH/δ-SPH/PBD/MPI） | 重复 grep 复核（`-rniE` 全 `src/`） | 不可比 → 计数类**精确** | **是（精确 0）** |
| SPH 数值精度（如 dambreak 波前/压力） | 实验/解析解 | **无基准** | **否/不适用** —— 本仓无自动误差数据，本单亦未构建运行；不给数字，避免编造 |

**证据完整性机器复核**：`python3 scripts/oss_audit/sphinxsim_inventory.py --checkout <clone> --verify` → `verify: 51 evidence checks PASS, 0 FAIL`。

**显式回答**：本单所有**计数类**关键量误差 0%（命令可复算）；**物理精度类**关键量**没有基准**，
故不作「<3%」声称——这是 honest-FAIL 边界，已在 ⑥ 与 `insufficient[]` 中如实登记。

---

## ⑦ 可迁移方案（≥3，含 license 合规判定与 GAP 锚）

### SPHSOL-1 · reference —— 上游算例库与 XML 回归台作 WCSPH 验证面蓝本
- **gap**: `GAP-SPH-WCSPH-VALIDATION`
- **做法**：只取「算什么 / 观测什么」（dambreak 压力探针 + 总机械能、Poiseuille 剖面、Taylor-Green 衰减率、
  two-phase dambreak 相界面），作 TensorSPH 验证清单与观测器命名约定；不复制 `.cpp` 驱动。
- **license**：算例参数/观测器命名=事实性数据，reference 无碍；若照抄驱动代码则须 Apache-2.0 合规。
- **证据**：`tests/2d_examples/` 70 目录、`tests/3d_examples/` 44 目录、857 回归台文件。

### SPHSOL-2 · adapt —— 以压缩正则化策略分层承载显式 δ-SPH 项
- **gap**: `GAP-SPH-DELTA-SPH`
- **做法**：本项目证明「压缩度量（`CompressionSummation`/`AverageCompression`）→ 策略特化
  （`Regularization<Internal|FreeSurface|FreeStream>`）」的接口形状可直接承载 δ-SPH 密度扩散项，并能做自由面分流
  以避免自由面处过度扩散——这正是 TensorSPH 加 δ 项时最易踩的坑。
- **license**：adapt 上游核心 = Apache-2.0（保留头注释 + 声明修改）；接口形状本身不受版权保护。
- **证据**：`shared_ck/.../density_regularization.h:112,150,200,222`；反面证据：δ-SPH 未实现（0 命中）。

### SPHSOL-3 · fit —— JSON-first 输入契约对接多相算例
- **gap**: `GAP-SPH-MULTIPHASE`
- **做法**：把两相溃坝/多相混合的 phase 参数组（密度-声速-阻抗 + `ImpedanceModel<WeaklyCompressibleMixture>` 语义）
  映射到 TensorPhysics 多相配置，做**契约层 fit**（字段语义对齐），不做数值层移植。
- **license**：MIT 胶水 → 字段语义可自由采用；schema 文件级复制保留 MIT 声明。
- **证据**：`runtime/schemas/contracts-v1.json`、`tests/.../two_phase_dambreak.json`、`riemann_solver_ck.h:134`。

### SPHSOL-4 · reference-only —— SYCL 执行策略层作后端抽象参照（不含性能证据）
- **gap**: `GAP-SPH-GPU-SCALING`
- **做法**：借鉴 `implementation_sycl.h` 把 `parallel_for`/迭代器与物理核解耦的分层；**但** Intel-only 编译器绑定 +
  wheel 强制 `SPHINXSYS_USE_SYCL=OFF` + 仓内零 scaling 数据 → 不可作 GPU 基线，TensorSPH 的 scaling 结论必须自测。
- **license**：reference-only（Apache-2.0，如改写须合规）。
- **证据**：`src_sycl/` 11 文件；`CMakeLists.txt:16,115,137,62`。

### SPHSOL-5 · adapt —— 「壁面=固体接触核」边界策略作 FSI/壁面对照实现
- **gap**: `GAP-SPH-FSI-BENCHMARK`
- **做法**：把 wall-contact 积分形式（壁面侧镜像压力耗散，`acoustic_step_1st_half.hpp:176`）迁到 TensorSPH 壁面/FSI 面，
  与 ghost/buffer 法在同一算例下做精度/稳定性对照。
- **license**：adapt = Apache-2.0 合规（声明修改）；仅借公式不必复制代码。
- **证据**：`acoustic_step_1st_half.hpp:176`、`fluid_structure_interaction/force_on_structure.h`。

### SPHSOL-6 · reference —— 作业/provenance 契约作可微扫描实验管理模板
- **gap**: `GAP-SPH-DIFFERENTIABLE`
- **做法**：`runtime/jobs.py` + `identity.py` + `contracts-v1.json`（作业/输出契约）可作「nu/dt/kernel 组合扫描 +
  梯度对比」实验的台账模板。**诚实声明**：本项目无任何 AD 实现（无 torch/jax/autodiff 痕迹），
  本方案只贡献实验管理层，不贡献梯度来源。
- **license**：MIT（采用其契约实现即 MIT 合规）。
- **证据**：`sphinxsim/runtime/jobs.py`、`identity.py`、`schemas/contracts-v1.json`。

---

## ⑧ 机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"Xiangyu-Hu/SPHinXsim","tier":2,"methods":["WCSPH","Riemann-dissipation","density-relaxation","symplectic-two-half","adaptive-CFL-timestep","buffer/emitter/outflow/free-stream/wall-contact-boundary","multiphase-multispecies","TLSPH-FSI-shell-rod","eulerian-FVM-MUSCL-HLLC"],"unsupported":["ISPH","PCISPH","DFSPH","IISPH","delta-SPH","PBD"],"boundary":"Wall 作为固体粒子+Wall 接触核（acoustic_step_1st_half.hpp:176 镜像压力耗散）；NearWallDistance/DistanceFromWall/BoundingFromWall(near_wall_boundary.h:42/57/68)；FlowVelocityBuffer/InflowVelocityCondition/FreeStreamVelocityCorrection/DampingBoundaryCondition/EmitterInflowCondition/DisposerOutflowDeletion(fluid_boundary.h:65/87/129/174/192/246)；FVM 分支另有 ghost 重构(fvm_ghost_boundary_2d.cpp)","gpu":"SYCL only (Intel DPC++), default OFF (sphinxsys/CMakeLists.txt:16), Intel-only FATAL(:115), target spir64_x86_64(:137), SPHinXsim wheel forces OFF (CMakeLists.txt:62), 11 src_sycl files; no MPI (0 hits); shared-memory=TBB; no scaling data in repo","validation":"70 2D + 44 3D upstream example dirs + 857 regression_test_tool files (self-referential XML ensemble baselines); named: dambreak/2d+3d, two_phase_dambreak, taylor_green, eulerian_taylor_green_LG, poiseuille_flow, 1d_shock_tube, FVM cylinder, FVM double-mach; unit_tests_src 27 cpp; SPHinXsim own tests 15 py (0 numeric-tolerance assertions) + 1 gtest smoke TEST(simulations, has_json); upstream physics suite NOT built (CMakeLists.txt:60 SPHINXSYS_BUILD_TESTS OFF); no automated analytic/experimental error metric => quantitative validation data: absent","license":"root MIT (c2026 Xiangyu Hu) / vendored sphinxsim/sphinxsys/ Apache-2.0 (TUM 2017-2025) => mixed: Python glue+contracts MIT-adaptable, C++ core requires Apache-2.0 compliance; 3rd_party/wasmtbb submodule license unreadable => reference-only","stars":19,"forks":11,"open_issues":25,"head":"5241a8902019e2b61559460748342dd9c29bd2a9","pushed_at":"2026-11-09T12:33:13Z","loc":{"vendored_core":84847,"cpp_glue":6783,"python":12966},"transferable":["SPHSOL-sphinxsim-01","SPHSOL-sphinxsim-02","SPHSOL-sphinxsim-03","SPHSOL-sphinxsim-04","SPHSOL-sphinxsim-05","SPHSOL-sphinxsim-06"],"note":"m8638 初判'SPHinXsys 简化/教学版'被反驳：vendored 完整上游核心 608 文件/84847 行"}[/OSS-INV]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsim","id":"SPHSOL-sphinxsim-01","kind":"reference","summary":"上游 70×2D+44×3D 算例目录与 857 文件 XML 自参照回归台作 TensorSPH WCSPH 验证清单与观测器约定（dambreak 压力探针+总机械能、Poiseuille 剖面、Taylor-Green 衰减率），只取算什么/观测什么不取代码","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsim","id":"SPHSOL-sphinxsim-02","kind":"adapt","summary":"以 CompressionSummation/AverageCompression + Regularization<Internal|FreeSurface|FreeStream> 的策略分层为骨架，为 TensorSPH 加显式 δ-SPH 密度扩散项并做自由面分流；本项目同时证明 δ-SPH 本身未实现（0 命中）","gap":"GAP-SPH-DELTA-SPH"}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsim","id":"SPHSOL-sphinxsim-03","kind":"fit","summary":"用 JSON-first 输入契约（contracts-v1.json + two_phase_dambreak/multi_phase_mixing 参数组 + ImpedanceModel<WCMixture> 语义）对齐 TensorPhysics 多相算例配置，契约层 fit 不做数值移植","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsim","id":"SPHSOL-sphinxsim-04","kind":"reference","summary":"SYCL 执行策略层（11 文件）作后端抽象设计参照；Intel-only + wheel 强制 USE_SYCL=OFF + 仓内零 scaling 数据 => 不作 GPU 性能基线，GPU-SCALING 结论须自测","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsim","id":"SPHSOL-sphinxsim-05","kind":"adapt","summary":"壁面=固体粒子+Wall 接触核（镜像压力耗散）迁移为 TensorSPH 壁面/FSI 对照实现，与 ghost/buffer 法同算例比精度与稳定性","gap":"GAP-SPH-FSI-BENCHMARK"}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsim","id":"SPHSOL-sphinxsim-06","kind":"reference","summary":"runtime/jobs.py+identity.py+contracts-v1.json 的作业/provenance 契约作可微参数扫描实验管理模板；本项目无任何 AD 实现，故 GAP-SPH-DIFFERENTIABLE 只贡献实验管理层","gap":"GAP-SPH-DIFFERENTIABLE"}[/OSS-SOL]

---

### 附：未完成 / insufficient（honest）

- `3rd_party/wasmtbb` 子模块 license 与实际用途：sparse+blobless clone 下内容未检出 → **insufficient**，按 reference-only 处理。
- 上游算例库能否在本仓构建运行：CI 显式关闭 `SPHINXSYS_BUILD_TESTS`，本单未本地构建（缺 vcpkg/Qt/oneAPI 工具链）→ **未验证**。
- 物理精度与 GPU 加速比：仓内无可复算数据，本单未运行 → **不给数字**。
- 未覆盖：`sphinxsim/llm/`（LLM provider 适配）、`desktop/`（Qt GUI）与 `bindings/sphinxsys_python.cpp`（pybind11 面）
  的逐行审计——与本单 SPH 方法盘点目标弱相关，留待需要时补。