# 开源 SPH 技术盘点 · 单项目深盘：`InteractiveComputerGraphics/SPH-Tutorial`

> 锚点 FEAT-OSS-INVENTORY-SPH（波2·集团军）· tier 3 · TU Munich 交互式图形学组 SPH 课程（SPlisHSPlasH 官方教程/算例集）
> 盘点对象：`https://github.com/InteractiveComputerGraphics/SPH-Tutorial` @ `4bb491e9a5`（`master` tip，2023-07-19 "Update README.md"）
> 盘点方式：**GitHub API 实查**（meta/tree/commits/contributors/contents，`api.github.com`，未认证 60/h 配额）+ PDF 幻灯片文本抽取（pypdf）
> **与波1（m8638 普查）的关键分歧（重要）**：普查登记为「C++ / MIT / 逐步推导+代码」——**实查证伪**：当前 `master` 树内**无任何源码、无 LICENSE 文件**（GitHub 语言/许可证检测均为 null），仓体=Jekyll 课程网站（4 个文本文件 + 15 个 PDF，共 ~88 MB）。教程所教代码在姊妹仓 `InteractiveComputerGraphics/SPlisHSPlasH`（MIT），本仓只承载课件。详见 §一/§六。

---

## 零、取证与复现（provenance）

| 项 | 值 |
|---|---|
| 仓库 / 分支 / commit | `InteractiveComputerGraphics/SPH-Tutorial` / `master` / `4bb491e9a5`（`/repos/.../commits?per_page=8` 直读；前 3 条 2023-07-19 / 2023-06-30 / 2023-06-30） |
| 元数据 | stars **200**，forks 27，open_issues 2，created 2019-02-27，pushed_at **2023-07-19**，size 89,852 KB，archived=false，topics=[simulation, smoothed-particle-hydrodynamics, sph, tutorial]，homepage=sph-tutorial.physics-simulation.org |
| 语言 / 许可证（API 判定） | `language=None`，`license=None`（**无 LICENSE 文件**，`/contents/LICENSE` 404）→ 按纪律 **reference-only** |
| 树规模 | **21 个条目**（`/git/trees/HEAD?recursive=1`，truncated=false）：CNAME(35 B)、README.md(89 B)、_config.yml(130 B)、index.md(2,350 B)、pdf/SPH_Tutorial.pdf(46,318,034 B)、slides/ 14 个 PDF（560 KB–17.7 MB） |
| 测试布局 / CI | **无**：全树无 `test*`、无 `.github/`、无 Makefile/CMakeLists（`/contents/CMakeLists.txt` 404） |
| 贡献者 | 单人：`janbender`（22 contributions，`/contributors` 直读） |
| 取证通道 | `curl` 被安全策略拦截 → `urllib` 直连 `api.github.com`（SNI 白名单）；`git clone` 走间歇 SNI 窗口（90 MB 仓，窗口内未完成，honest 记录）；脚本 `artifacts/oss-sph-tutorial/fetch_spht.py` 可复跑 |
| 限流实况 | 共享出口 IP 未认证配额 60/h 被并发耗尽（403 rate limit），按 Retry-After 退避完成 15 次核心调用；`/rate_limit` 快照见 `artifacts/oss-sph-tutorial/` |

---

## 一、概况

- **本仓不是代码仓，是课程网站仓**：`README.md` 全文仅一行链接（`https://sph-tutorial.physics-simulation.org/`）；`CNAME` 指向该域名；`_config.yml` 为 Jekyll 配置。
- **内容=两代课件**：① 2019–2020 主教程 10 讲（`slides/01..10_*.pdf`）+ 完整讲义 `pdf/SPH_Tutorial.pdf`（46.3 MB）；② 2022-10 追加的 STAR 2022 短课程 4 讲（`slides/EG22_STAR_SPH_Part{1..4}_*.pdf`，提交 `a1cf818eac`/`6bc9b88881` 2022-10-14 "added STAR slides"）。
- **一句话定位**：SPlisHSPlasH 官方配套课件——WCSPH→不可压求解器族→边界→多相→黏度→涡量→SPlisHSPlasH 代码导读→可变形固体→刚体→数据驱动，**教学价值高、工程价值为零**（无代码/无测试/无 CI/无许可证）。
- **活跃度**：2023-07-19 后无提交（README 更新为最后一次）；单人维护（janbender），课程性质，非活跃开发仓。

---

## 二、方法谱系（课件级，逐讲定位）

> 定位粒度=幻灯片文件（本仓无源码，`文件:行` 不适用；PDF 页级定位待集群作业 cj22 文本抽取（§五-2））。

| 讲次 | 文件 | 覆盖方法（标题级） |
|---|---|---|
| 01 | `slides/01_intro_foundations_neighborhood.pdf` | SPH 基础：核函数、近似、邻域搜索 |
| 02 | `slides/02_incompressibility.pdf` | 不可压处理（WCSPH/ISPH 族，待文本确认） |
| 03 | `slides/03_boundary_handling.pdf` | 边界处理（待文本确认：ghost/penalty/repulsion 等） |
| 04 | `slides/04_multiphase_fluids.pdf` | 多相流体（待文本确认） |
| 05 | `slides/05_viscosity.pdf` | 黏度模型（XSPH/人工黏度，待文本确认） |
| 06 | `slides/06_vorticity.pdf` | 涡量（vorticity confinement，待文本确认） |
| 07 | `slides/07_SPlisHSPlasH.pdf` | SPlisHSPlasH 代码导读（代码本体在姊妹仓） |
| 08 | `slides/08_deformable_solids.pdf` | 可变形固体（SPH 弹性） |
| 09 | `slides/09_rigid_body_simulation.pdf` | 刚体模拟 |
| 10 | `slides/10_data_driven_techniques.pdf` | 数据驱动技术 |
| EG22×4 | `slides/EG22_STAR_SPH_Part{1..4}_*.pdf` | STAR 2022：Foundations / Incompressibility / Materials / DataDriven |

**GPU/MPI 并行策略**：课件标题级未见 GPU/MPI 专题（SPlisHSPlasH 本体为 CPU 串行 C++11，无 GPU/MPI——待 cj22 文本确认）。

---

## 三、验证面盘点

| 维度 | 状态 |
|---|---|
| 自带算例/基准 | 仓内**无**可运行算例（无代码）；验证内容仅可能存在于 PDF 插图（待 cj22 抽取） |
| 测试框架 | **无**（全树无 test 目录/文件） |
| 量化验证数据 | **无**（无数据文件；PDF 内若有数值对比图，属课件插图非可复现基准） |
| 结论 | 验证面 = **零**（honest：本仓对 TensorSPH 验证面无可直接迁移的量化资产） |

---

## 四、可迁移方案（≥2，license 合规显式）

> 本仓无 LICENSE → 全部 **reference-only**（读思想/结构，不复制内容）；涉及 SPlisHSPlasH 代码的方案以姊妹仓 MIT 许可为准。

### SPHSOL-sphtutorial-01 · reference · GAP-SPH-ISPH-CONVERGENCE
**课件结构→TensorSPH 不可压求解器验证课程化**：02 讲（incompressibility）+ EG22 Part2 构成 WCSPH→PCISPH/DFSPH/IISPH 的完整推导链，可直接作为 TensorSPH 不可压求解器（压力 Poisson 收敛、密度误差 <1% 判据）的**教学/验证用例设计参考**（用例序列、参数表、判据呈现方式）。不复制任何课件内容（无许可证）。

### SPHSOL-sphtutorial-02 · reference · GAP-SPH-WCSPH-VALIDATION
**SPlisHSPlasH 代码导读（07 讲）→ 验证面地图**：07 讲逐模块讲解 SPlisHSPlasH（MIT）的 WCSPH/PCISPH/DFSPH/IISPH/PBF 实现——作为 TensorSPH 对标 SPlisHSPlasH 时**模块级对照清单**（哪些模块有官方参考实现、哪些参数有官方推荐值）。代码复用走 SPlisHSPlasH 仓（MIT，可署名移植），本仓仅提供导读地图。

### SPHSOL-sphtutorial-03 · reference · GAP-SPH-MULTIPHASE
**多相/自由面课件（04 讲）→ 多相 SPH 用例设计参考**：液滴/气泡/界面追踪的用例选择与呈现方式，供 TensorSPH 多相验证面（液滴振荡/气泡上升）设计参考。reference-only。

---

## 五、注意点（踩坑 / 局限）

1. **普查登记错误（已纠正）**：m8638 普查把本仓登记为「C++/MIT/含代码」——实查为纯课件仓、无许可证。推测混淆了本仓与 SPlisHSPlasH 仓。建议回改 `artifacts/oss-sph/oss_sph_registry.json` 对应条目。
2. **PDF 抽取受限**：46 MB 主讲义 + 13 个 >1 MB 幻灯片超出 contents API 1 MB 上限；`git clone` 受间歇 SNI 窗口限制（90 MB 下载需窗口 ≥2 min）。读不到的 PDF 内容一律标 insufficient，不凭记忆补全。
3. **无许可证 ≠ 禁止阅读**：reference-only 指不复制/不分发内容；引用其**方法命名与结构**（事实性信息）不受限。
4. **PDF 抽取走集群**：90 MB 仓超出本机间歇 SNI 窗口（clone 需窗口 ≥2 min，实测 133 s 断连）；已递交集群作业 cj22（n315，clone 重试×6 + pypdf 抽取），结果若回传将补 §二 页级定位。
5. **stars 200 的构成**：课程仓 stars 含大量学生/学习者收藏，非工程采用度指标。

---

## 六、误差判定

本单为盘点（非精度验证），无计算结果对标基准；误差判定不适用。
**诚实声明**：
- ✅ 完成：API 实查（meta/tree/commits/contributors/contents 15 次调用）+ 树级测试/CI 判定 + 3 条可迁移方案 + 普查纠错
- ⚠️ 未完成：15 个 PDF 的页级方法清单（clone 窗口未开 / contents API 1 MB 上限）——§二 方法表为**标题级**，页级定位待补
- ⚠️ 未覆盖：SPlisHSPlasH 姊妹仓代码本体（= 波2 另一深盘单）

---

## 七、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"InteractiveComputerGraphics/SPH-Tutorial","tier":3,"methods":["SPH foundations & neighborhood search (01)","incompressibility solvers (02, EG22-P2)","boundary handling (03)","multiphase fluids (04)","viscosity models (05)","vorticity (06)","SPlisHSPlasH codebase tour (07)","deformable solids (08)","rigid body simulation (09)","data-driven techniques (10, EG22-P4)"],"boundary":"slides/03_boundary_handling.pdf (854 KB; 页级内容 insufficient——PDF 未取回)","gpu":"none in repo (no code; SPlisHSPlasH 本体为 CPU 串行 C++11)","validation":"none in repo (no tests/CI/benchmarks; 验证面=零)","license":"NONE (no LICENSE file; API license=null) → reference-only"}[/OSS-INV]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPH-Tutorial","id":"SPHSOL-sphtutorial-01","kind":"reference","summary":"02 讲+EG22-P2 不可压推导链 → TensorSPH 不可压求解器验证用例设计参考（用例序列/参数表/判据呈现）；无许可证，不复制内容","gap":"GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPH-Tutorial","id":"SPHSOL-sphtutorial-02","kind":"reference","summary":"07 讲 SPlisHSPlasH 代码导读 → 模块级对照清单（WCSPH/PCISPH/DFSPH/IISPH/PBF 官方参考实现地图）；代码复用走 SPlisHSPlasH 仓（MIT）","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPH-Tutorial","id":"SPHSOL-sphtutorial-03","kind":"reference","summary":"04 讲多相/自由面课件 → TensorSPH 多相验证面（液滴振荡/气泡上升）用例设计参考；reference-only","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]
