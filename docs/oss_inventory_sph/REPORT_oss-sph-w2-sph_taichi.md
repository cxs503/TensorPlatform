# OSS-SPH 深盘波2 · erizmr/SPH_Taichi 源码级盘点报告

> **scenario_key**: oss-sph-w2
> **mission_id**: 8660
> **盘点日期**: 2026-11-09
> **盘点人**: agent/m8660
> **上游**: m8638 普查（`docs/oss_inventory_sph/REPORT_oss-sph-disc.md`，SPH_Taichi = Tier-2 #11）
> **机器产物**: `artifacts/oss_sph_taichi/oss_sph_taichi_inv.json` + 本报告内嵌 [OSS-INV]/[OSS-SOL] 机器块
> **证据纪律**: 零手抄——本报告每个数字均可溯源到 `artifacts/oss_sph_taichi/api_*.json`（GitHub API 原始响应）

---

## 一、案例介绍（物理背景 / 盘点对象 / 参数表）

### 1.1 对象

erizmr/SPH_Taichi —— 基于 Taichi 的 SPH（光滑粒子流体动力学）实现，面向计算机图形学/研究原型场景（流体浴盆、高流体柱、刚体耦合演示）。SPH 为 Lagrangian 无网格粒子法，本仓覆盖 WCSPH（弱可压）/ DFSPH（双相流体）/ IISPH（隐式不可压）三个变体，是 m8638 普查判定的「Taichi 可微 SPH」候选（tier2）。

### 1.2 仓库元数据（GitHub API 实查，2026-11-09，零手抄）

| 字段 | 值 | 溯源 |
|---|---|---|
| full_name | erizmr/SPH_Taichi | api_repo.json |
| stars / forks / watchers | 322 / 55 / 5 | api_repo.json |
| 语言 | Python（151208 bytes，唯一语言） | api_languages.json |
| license | **MIT**（LICENSE.txt；2024-10-05 commit b02e13a7 "add MIT license (#32)" 补加） | api_license.json / api_commits.json |
| 创建 / 最近 push | 2020-06-08 / **2024-10-05**（此后 14 个月无提交） | api_repo.json |
| default_branch / size | master / 71321 KB | api_repo.json |
| topics | gpu, physics-simulation, simulation | api_repo.json |
| open_issues / archived | 13 / false | api_repo.json |
| 贡献者 | erizmr (131 commits), AFutureD (1) | api_contributors.json |
| CI (GitHub Actions) | **0 个 workflow** | api_workflows.json |
| releases | 无 | api_releases.json |
| 文件总数 | **37**（tree 完整，truncated=false） | api_tree.json |

### 1.3 最近提交（api_commits.json，前 15 条）

| 日期 | sha | 摘要 |
|---|---|---|
| 2024-10-05 | b02e13a7 | add MIT license (#32) |
| 2023-02-10 | 4a701fd1 | [export] Refactor export ply and obj (#25) |
| 2023-02-09 | 927df180 | [bug] Fix export ply and obj (#24) |
| 2023-02-09 | 7c958fb7 | [fix] fix output ply (#23) |
| 2023-01-12 | cea17c84 | [misc] Recover the allowed Taichi version to later than 1.2.0 (#19) |
| 2023-01-12 | 24dbbf74 | [bug] Fix rigid body center of mass out of index (#18) |
| 2023-01-12 | 2096aa8b | [misc] Pin the version to 1.2.0 currently (#16) |
| 2023-01-12 | bc643fc1 | [refactor] Change dot transpose to outer product (#15) |
| 2022-11-24 | efb10310 | [docs] Fix typo (#11) |
| 2022-11-12 | 7b0c2542 | [misc] Update taichi version required (#10) |

**判读**：2023-02 后仅 1 次提交（补 license），项目实质处于**维护停滞**状态；活跃期为 2020-06 ~ 2023-02。

---

## 二、怎么做的（方法 / 流程 / 工具链）

1. **GitHub API 实查**（禁凭记忆）：`/search/repositories`（元数据）→ `/repos/{repo}`（元数据）→ `/git/trees/master?recursive=1`（全树 37 文件）→ `/commits?per_page=15`、`/languages`、`/actions/workflows`、`/license`、`/contributors`、`/releases`。原始响应全部落盘 `artifacts/oss_sph_taichi/api_*.json`。
2. **源码获取（受阻，如实记录）**：
   - 本地 `git clone`：SNI 间歇窗内 3 次尝试均断连（`unexpected disconnect while reading sideband packet`）；
   - 本地 codeload tarball：3 次下载全部截断（10.8MB / 7.1MB，`gzip -t` 失败）；
   - 集群节点 n315（cj8/cj19/cj24）：节点 egress 探测 github=200/codeload=301 可达，但 tarball 下载循环 15 分钟未成、git clone 作业 900s 超时；
   - 未认证 API 配额（共享出口 IP 60/h）被并发耗尽，`/contents` 逐文件读取未能执行。
3. **结论**：源码**内容级**读取失败 → 方法清单只能给到**文件名/目录级**证据，file:line 级定位 = **insufficient**（不编造）。

---

## 三、计算结果（源码级方法清单 + 验证面）

### 3.1 方法清单（证据分级：文件名/树级=source-confirmed；内容级=insufficient）

| 能力 | 证据（树级） | 证据级 |
|---|---|---|
| WCSPH | `WCSPH.py` + `data/scenes/high_fluid_wcsph.json` + `legacy/img/WCSPH.gif`、`wcsph_alpha030.gif`（alpha=0.30 变体） | 文件名确认；内容 insufficient |
| DFSPH | `DFSPH.py` + 4 个 `*_dfsph.json` 场景 + `legacy/img/DFSPH.gif` | 文件名确认；内容 insufficient |
| IISPH | `IISPH.py` | 文件名确认；内容 insufficient |
| PCISPH | 仅 `legacy/img/PCISPH.gif`（legacy 引擎） | 推断：legacy 引擎曾有 PCISPH |
| ISPH（一般） | 由 IISPH 覆盖（隐式不可压族） | 推断 |
| δ-SPH | 树内无对应文件 | **未发现** |
| PBD | 树内无对应文件 | **未发现** |
| 刚体耦合 | `data/models/{Dragon_50k,armadillo_small,bunny_sparse,BoxOpenedHole}.obj` + `*_dynamic*.json` 场景 + commit 24dbbf74 "Fix rigid body center of mass" | 文件名+commit 确认 |
| 边界处理 | 树内无专门边界文件；应在 `sph_base.py`/`particle_system.py` 内 | **insufficient**（内容未读） |
| 时间步控制 | 同上，应在 `sph_base.py`/`run_simulation.py` 内 | **insufficient**（内容未读） |
| 多相 | 树内无多相证据（无第二相/界面文件） | **未发现** |
| 自由面 | `dragon_bath`/`armadillo_bath` 浴盆场景 + `high_fluid` 场景 | 场景名推断 |
| GPU 并行 | Taichi 后端（topics:gpu；Taichi 语义=GPU 优先）；无 MPI 文件 | 推断（Taichi 语义）；MPI 未发现 |
| 可微 | Taichi 原生支持 autodiff；树内未见显式梯度/反问题代码 | **推断，未验证**（m8638 普查初判"可微"未经本单证实） |
| 导出 | commit 23/24/25：ply/obj 导出 | commit 确认 |

### 3.2 验证面盘点

| 维度 | 结果 |
|---|---|
| 自带算例 | 7 个演示场景（dragon_bath / armadillo_bath / high_fluid × wcsph/dfsph/dynamic）——**演示性质，非基准** |
| 基准（dam-break/Poiseuille/Taylor-Green/Sedov 等） | **树内无任何解析/实验对标算例** |
| 测试框架 | 无（无 tests/ 目录、无 pytest 配置）；唯一测试样貌文件 `legacy/test_sample.py`（内容未读） |
| 量化验证数据 | **无**（树内无结果数据文件） |
| CI | 无（0 workflows） |

**判定：验证面 ≈ 空**。该仓价值在方法原型与 GPU 原型，不在验证。

### 3.3 目录结构（api_tree.json 全量 37 文件）

```
WCSPH.py  DFSPH.py  IISPH.py  sph_base.py  particle_system.py
config_builder.py  run_simulation.py  demo_high_fluid.py  scan_single_buffer.py
requirements.txt  LICENSE.txt  README.md  .gitignore
legacy/{README.md, scene.py, test_sample.py, engine/{__init__,sph_solver}.py, img/*.gif×5}
data/models/*.obj×4 + bunny.stl   data/scenes/*.json×7   data/gif/*.gif×2
```

---

## 四、对 TensorPhysics SPH 线的可迁移方案（≥3，license 合规显式）

**License 合规总判定**：MIT（宽松许可）→ adapt/fit/reference 三档均合规，仅需保留版权声明；无 copyleft 传染风险。

| ID | 分级 | 方案 | GAP 锚 | 合规 | 证据级 |
|---|---|---|---|---|---|
| SPHSOL-sph_taichi-01 | **fit** | Taichi 单内核 CPU/GPU 一行切换的**后端选择层模式**，移植为 TensorSPH GPU 后端抽象的参考设计（不抄码，抄架构） | GAP-SPH-GPU-SCALING | MIT→fit 合规 | 文件名+topics 级 |
| SPHSOL-sph_taichi-02 | **reference** | `data/scenes/*.json` 声明式场景配置 schema（求解器/刚体/粒子域/导出参数）作为 TensorSPH 验证 harness 场景描述格式参考 | GAP-SPH-WCSPH-VALIDATION | MIT→reference 合规 | 文件名级（schema 细节待内容验证） |
| SPHSOL-sph_taichi-03 | **reference** | IISPH.py + DFSPH.py 的隐式压力求解结构作为 ISPH 收敛研究（密度误差/压力振荡/时间步敏感性）的**对照实现** | GAP-SPH-ISPH-CONVERGENCE | MIT→reference 合规 | 文件名级 |
| SPHSOL-sph_taichi-04 | **reference** | Taichi autodiff 作为可微 SPH 路径的**备选后端**（与 jax-sph 并列），用于 AD-vs-FD 梯度精度验证设计 | GAP-SPH-DIFFERENTIABLE | MIT→reference 合规 | 推断级（本仓未见显式 AD 代码，可微性未证实） |

**负面结论（入账）**：SPH_Taichi 对 GAP-SPH-DELTA-SPH / GAP-SPH-MULTIPHASE / GAP-SPH-ASTRO-SEDOV / GAP-SPH-FSI-BENCHMARK **无可迁移项**（树内无 δ-SPH/多相/天体/FSI 证据）。

---

## 五、注意点（踩坑 / 局限 / 适用范围）

1. **网络三通道全断**（本单最大坑）：本地 SNI 间歇窗 clone 断连、codeload tarball 截断、未认证 API 配额被同 IP 并发耗尽（60/h 共享）；集群节点 egress 可达但下载循环 15 分钟未成。后续波次建议：节点侧 `git clone` 重试 ≥6 次 + 错峰，或申请认证 token。
2. **内容级审计未完成**：方法清单停留在文件名/目录级；边界处理、时间步控制、可微性三项 = insufficient，**不得**据此下"支持/不支持"结论。
3. **可微性存疑**：m8638 普查初判"Taichi 可微 SPH"，本单树级证据**未证实**（无梯度/反问题代码迹象）——普查初判降级为"待验证"。
4. **项目停滞**：2024-10 后无提交，13 个 open issues 未处理；作为 reference 可，作为长期依赖不可。
5. **stars 偏差**：322 stars 主要来自"Taichi 一行切 GPU"的演示效应，非方法深度。

---

## 六、误差判定

本单为**盘点/审计任务，无计算**，无关键量 vs 基准的误差项；「是否<3%」问题**不适用**（无基准、无计算，原因如上）。

**honest-FAIL 声明**：
- ❌ 未完成：源码内容级 file:line 方法清单（网络三通道全断，`/contents` 未执行）；
- ❌ 未完成：可微性验证（需运行 taichi 环境）；
- ✅ 完成：API 元数据全量实查 + 全树 37 文件 + 15 提交 + 验证面判定 + 4 条可迁移方案（含 license 判定）+ 机器块。

---

## 七、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"erizmr/SPH_Taichi","tier":2,"methods":["WCSPH (WCSPH.py; filename-confirmed, content insufficient)","DFSPH (DFSPH.py + 4 dfsph scenes)","IISPH (IISPH.py)","PCISPH (legacy engine only, legacy/img/PCISPH.gif)"],"boundary":"insufficient - no dedicated boundary file in tree, file contents unread","gpu":"Taichi GPU backend (topics:gpu; inference); MPI: none found","validation":"none quantitative - no tests dir, 0 CI workflows, no benchmark data in tree; legacy/test_sample.py only","license":"MIT (LICENSE.txt, commit b02e13a7 2024-10-05)"}[/OSS-INV]

[OSS-SOL]{"repo":"erizmr/SPH_Taichi","id":"SPHSOL-sph_taichi-01","kind":"fit","summary":"Taichi 单内核 CPU/GPU 一行切换的后端选择层模式，移植为 TensorSPH GPU 后端抽象参考设计（抄架构不抄码）","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]
[OSS-SOL]{"repo":"erizmr/SPH_Taichi","id":"SPHSOL-sph_taichi-02","kind":"reference","summary":"data/scenes/*.json 声明式场景配置 schema 作为 TensorSPH 验证 harness 场景描述格式参考","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
[OSS-SOL]{"repo":"erizmr/SPH_Taichi","id":"SPHSOL-sph_taichi-03","kind":"reference","summary":"IISPH.py+DFSPH.py 隐式压力求解结构作为 ISPH 收敛研究（密度误差/压力振荡/时间步敏感性）对照实现","gap":"GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]
[OSS-SOL]{"repo":"erizmr/SPH_Taichi","id":"SPHSOL-sph_taichi-04","kind":"reference","summary":"Taichi autodiff 作为可微 SPH 备选后端（与 jax-sph 并列）用于 AD-vs-FD 梯度精度验证设计；本仓可微性未证实","gap":"GAP-SPH-DIFFERENTIABLE"}[/OSS-SOL]
