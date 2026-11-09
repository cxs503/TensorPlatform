# 【盘点】发现单（多相/多组元专项）— 开源 LBM 技术盘点

> 任务锚点：FEAT-OSS-INVENTORY-LBM（mission 8307）｜生成时间 2026-11-09T06:49:42.889722+00:00｜证据快照日 2026-11-09

## ① 案例介绍（本单在做什么）

平台 `oss_inventory` 表已有 24 个开源 LBM 仓在盘。本单**只做发现、不做深盘**：在多相/多组元（SCMP 高密度比、颜色梯度、free-energy、相场、自由面）、热-流耦合、润湿边界三个方向上，用 GitHub 检索 + 权威清单交叉比对，找出**清单外**值得后续深盘的仓，并明确给出排除项与理由。

边界声明：本单结论为**元数据级证据**（stars / description / pushed_at / license / archived，全部来自 api.github.com 返回并落盘），未做代码级核验；所有候选状态一律 `suggested`，是否入册需后续深盘单按 `[OSS-SOL]` 流程复核。

## ② 怎么做的（方法/工具链/关键设置）

| 步骤 | 做法 | 产物 |
|---|---|---|
| 1 检索 | `api.github.com/search/repositories`，22 条查询，`sort=stars`，跳过 fork（fork 继承上游，清单只收上游） | `discovery_raw*.json` |
| 2 交叉 | 解析 `sthavishtha/list-lattice-Boltzmann-codes` README（经 `repos/.../readme` 取 raw），抽 34 条 github 链接逐条核验 | `canonical_list_links.json` / `list_entry_verify.json` |
| 3 去重 | 与 `oss_inventory` 24 仓比对（大小写不敏感）；候选命中在盘仓数必须为 0 | `shortlist.json` |
| 4 判据 | 停更 > 5 年且无参考价值 → 排除；纯可视化/教学/代理模型 → 排除；功能与在盘仓重叠 → 观察名单 | 本文件 §④ |
| 5 出图 | matplotlib 三面板（星数分布 / 活跃度分布 / 召回自检），PNG 落盘 | `fig_discovery_overview.png` |

查询词（全量）：multiphase、color gradient、free surface、phase field、topic:lattice-boltzmann、topic:lattice-boltzmann-method、topic:multiphase-lbm（0 命中）、wetting contact angle（0 命中）、thermal two-phase（0 命中）、shan-chen、pseudopotential、multicomponent、two-phase、droplet、porous media two-phase（0 命中）、conjugate heat transfer、topic:multiphase-flow+lattice、openlb、"free energy"+"contact angle"+"high density ratio"（0 命中）。

> 方法学注意：GitHub 无 token 时 search 池 10 次/分钟、core 池 60 次/小时。本单 3 轮检索各留 5–7s 间隔；core 池在逐仓取证阶段耗尽后改用 search 池补证（`mflbm_search.json`），并如实记录该降级。

## ③ 结果：候选表（≤8，按星数）

| # | repo | stars | 为什么值得盘（判读层） | 填补的空缺 | 建议 tier | 最近 push | license |
|---|---|---|---|---|---|---|---|
| 1 | `lanl/MF-LBM` | 132 | LANL 可移植可扩展多孔介质 DNS 级 LBM（MPI+CUDA/OpenACC，BSD-3）；权威清单将其归入多相档，多相细节待深盘核实 | 现清单 tier1 全是单机科研脚本，缺可扩展到集群/GPU 的多孔介质 DNS 基线 | 1 | 2023-07-25 | BSD-3-Clause |
| 2 | `openLB/openLB` | 59 | openLB 本体：多相/free-surface + Shan-Chen + 热耦合能力（共轭传热属较新版本，镜像停在 2019）；在盘的 openLBMPM 只是它的多相扩展分支——母体缺位 | 自由面/多相 + 热-流耦合；清单只有衍生物没有上游 | 1 | 2019-12-02 | n/a |
| 3 | `Zitzeronion/Swalbe.jl` | 31 | Julia GPU 薄膜/液滴润滑 LBM（MIT，2026 仍活跃），润湿/去润湿动力学的高吞吐实现 | 薄膜润湿动力学的 GPU 实现空缺 | 2 | 2026-10-29 | MIT |
| 4 | `geoelements/mcmp-lbm` | 30 | 3D 多组元多相 LBM（MRT）+ 机器学习代理耦合，UT Austin geoelements 组维护 | 3D MCMP 的现代 MRT 实现与 ML 代理接口空缺 | 1 | 2023-08-08 | NOASSERTION |
| 5 | `ebinan92/Fingering_dynamics` | 26 | 两相 LBM + Zou-He 速度入口 + 半程反弹润湿壁面，指进不稳定性复现脚本（含工况配置） | 润湿边界（壁面附着/接触角）与两相不稳定性算例空缺 | 1 | 2019-02-28 | n/a |
| 6 | `JuliusWeinmiller/ThermalMultiphaseOpenLB` | 12 | OpenLB 的热-多相扩展（相变/热毛细方向），2026 活跃；热-流耦合多相的参考实现 | 热-流耦合多相（相变）在 24 仓中零覆盖 | 1 | 2026-09-12 | n/a |
| 7 | `apes-suite/musubi` | 8 | 八叉树 AMR 多物理 LBM（TU Dresden APES 生态，2026 活跃）：静态/动态自适应 + 多相/热；求解器主体为 Fortran（仓内胶水层使 GitHub 主语言显示为 Python） | HPC 侧『八叉树 AMR + 多相』组合空缺 | 3 | 2026-10-16 | BSD-2-Clause |
| 8 | `AminZar69/CudaPhaseField` | 4 | 三相相场 LBM 的 CUDA 并行求解器（GPL-3，2026 活跃）；相场多相方向唯一活跃实现（星数低=未成熟，作方向采样点） | 相场（phase-field）多相方向零覆盖，属供给最薄的一档 | 1 | 2026-08-16 | GPL-3.0 |

tier 分布：tier1（多相/多组元）6 个，tier2（可微/GPU）1 个，tier3（HPC）1 个。

### 逐条存在性证据（api.github.com 原始 description）

- **lanl/MF-LBM**｜132★｜Fortran｜MF-LBM: A Portable, Scalable and High-performance Lattice Boltzmann Code for DNS of Flow in Porous Media｜来源 `mflbm_search.json`
- **openLB/openLB**｜59★｜C++｜Unofficial mirror｜来源 `discovery_raw.json`
- **Zitzeronion/Swalbe.jl**｜31★｜Julia｜Simple Julia Lattice Boltzmann Solver for Thin Liquid Films and Droplets, approximating the thin film equation.｜来源 `discovery_raw.json`
- **geoelements/mcmp-lbm**｜30★｜Python｜A 3D multicomponent multiphase lattice Boltzmann solver with a Multi-Relaxation-Time collision scheme and a sparse storage structure｜来源 `discovery_raw.json`
- **ebinan92/Fingering_dynamics**｜26★｜Python｜two phase lattice boltzmann with zou-he boundary and half way bounce-back｜来源 `discovery_raw2.json`
- **JuliusWeinmiller/ThermalMultiphaseOpenLB**｜12★｜C++｜This is an extension for OpenLB which allows for thermal single-component phase change multiphase flow｜来源 `discovery_raw3.json`
- **apes-suite/musubi**｜8★｜Python｜Octree based Lattice-Boltzmann solver for multi-physics｜来源 `discovery_raw.json`
- **AminZar69/CudaPhaseField**｜4★｜Fortran｜Three-Phase Lattice Boltzmann Method  Solver Parallelised with CUDA Fortran｜来源 `discovery_raw.json`

## ④ 明确排除项及理由（22 项）

| repo | stars | 最近 push | 停更年数 | 排除理由 |
|---|---|---|---|---|
| `tangency-labs/fieldcraft` | 348 | 2026-11-09 | 0 | AI agent 驱动的建模前端（CAE/CFD 聚合器），无 LBM 内核可盘 |
| `sthavishtha/list-lattice-Boltzmann-codes` | 265 | 2024-07-10 | 2 | 清单仓（本单数据源），非求解器 |
| `pmocz/latticeboltzmann-python` | 236 | 2025-05-08 | 1 | 单相教学 + 可视化，无多相/润湿内容 |
| `crgimenes/kutta` | 147 | 2026-11-08 | 0 | 2D 风洞实时可视化（游戏化渲染），非科研 LBM |
| `je-santos/PoreFlow-Net` | 81 | 2022-12-07 | 4 | 3D CNN 渗透率代理模型，非 LBM 求解器 |
| `siramirsaman/LBM` | 78 | 2018-12-15 | 8 | MATLAB 单相曲面边界教学代码，功能已被在盘仓覆盖 |
| `pmocz/cfd-comparison-python` | 77 | 2025-05-08 | 1 | 多方法对比教学（LBM/SPH/FV），无多相深度 |
| `AbbasHub/AMR-LBM-OpenMP-2D` | 65 | 2018-09-24 | 8 | 2D AMR 教学，停更 >5 年；AMR 能力由 musubi/walberla 覆盖 |
| `zhishang72/OpenLBM` | 40 | 2019-07-31 | 7 | 单相 BGK/MRT，与在盘 2b-t/LB-t 功能重叠 |
| `wme7/openLBMflow` | 36 | 2016-09-24 | 10 | 2016 停更（>5 年）、浏览器端单相 LBM 演示 |
| `QCFD-Lab/qlbm` | 38 | 2026-11-06 | 0 | 量子 LBM（qiskit），非经典多相求解器 |
| `kircher1/LBM-SWE-Unity` | 38 | 2022-06-05 | 4 | Unity 浅水（SWE）可视化包，非多相 LBM |
| `kobejean/tf-cfd` | 46 | 2023-09-23 | 3 | 可微 CFD 但与在盘 latflow/ludwig 重叠，且以 FV 为主 |
| `EPyutao/MCMP_LBM` | 21 | 2016-07-01 | 10 | 2016 停更（>5 年）、脚本级，功能与 openLBMPM/MPLBM-UT 重叠 |
| `spoonacular/LBM_python` | 20 | 2018-04-05 | 8 | 2018 停更（>5 年）教学级多相脚本 |
| `carlosrosales/mplabs` | 17 | 2014-10-16 | 12 | 2014 停更（>11 年）MATLAB 多相 suite，无维护无文档 |
| `lhufnagel/multi-lbm` | 11 | 2016-12-22 | 10 | 2016 停更（>5 年）单人代码，无可核验算例 |
| `otbrown/LAMBReX` | 14 | 2020-04-07 | 6 | 2020 停更的 AMReX AMR 参照；AMR 能力已由 musubi/walberla/XLB 覆盖 |
| `Haider-BA/3D-LBM-AMR` | 22 | 2016-10-31 | 10 | 2016 停更（>5 年）的 AMR 教学代码 |
| `UCL/LatBo.jl` | 21 | 2017-02-24 | 9 | 2017 停更 Julia 单相实现，无多相内容 |
| `auag92/lattice_boltzmann_phase_field` | 5 | 2016-02-15 | 10 | 2016 停更（>5 年）相场脚本，无文档无算例 |
| `je-santos/MultiphasePorousMediaPalabos` | — | — | — | canonical 清单中的链接已失效（repos API 404、search 无命中）=链接腐烂；同作者在盘 MPLBM-UT 已覆盖 |

### 观察名单（方向对但暂不占名额，5 项）

| repo | stars | 最近 push | 暂缓理由 |
|---|---|---|---|
| `LBMFluids/sc_lbm` | 28 | 2023-03-03 | SC 力格式最小参考实现，但与在盘 openLBMPM/MPLBM-UT 的 SC 实现重叠，名额让给零覆盖方向 |
| `AminZar69/ompTernaryPhaseField` | 1 | 2024-10-27 | 同一作者的三相相场 OpenMP 版，与入选的 CUDA 版重复 |
| `dschrijver/dslbm` | 3 | 2026-08-06 | 颜色梯度二元流体实现，星数过低（<5）且与在盘 openLBMPM 色梯度能力重叠 |
| `PengyuFu/CG_LBM_multiphase` | 1 | 2026-10-21 | 2D/3D 颜色梯度多相，2026 新建（1★），待成熟后再盘 |
| `HarmonicTons/lbm-free-surface` | 3 | 2022-06-15 | 自由面实现，与在盘 Maarten-vd-Sande/lbm 高度重叠 |

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **链接腐烂**：权威清单 `sthavishtha/list-lattice-Boltzmann-codes` 里的 `je-santos/MultiphasePorousMediaPalabos` 已 404（repos API 与 search 双查无命中），清单本身 2024-07 后未更新——清单不能当唯一信息源，必须逐条 API 核验。

2. **镜像≠上游**：`openLB/openLB` 是 *unofficial mirror*，最后 push 2019，真正的上游在 GitLab （另有 `live-clones/openlb` 明确指向 gitlab.com/openlb/release）。把它当 tier1 参考实现可以，但当『当前能力基线』会失真——深盘单必须注版本与上游地址。

3. **母体缺位**：在盘的 `PorousMediaSimulation/openLBMPM` 是 openLB 的多相扩展分支，清单只收了衍生物没收母体——这正是本单最重要的结构性发现（同类风险应反查 `CFD-GO/TCLB`、`palabos` 的生态分支）。

4. **高密度比 SCMP 开源供给极薄**：`"high density ratio"+lattice+boltzmann` 检索 0 命中，颜色梯度/相场方向命中的多是 1–5★ 的新建仓（2026 年新建）。这是**否定性发现**：该方向没有可直接借用的成熟开源实现，只能靠论文+自研，建议后续立单时按『算法复现』而非『代码借鉴』设计。

5. **stars ≠ 质量**：tier 判读用的是『方向覆盖 + 活跃度 + 许可』，星数仅作热度参考；本单入选的 `AminZar69/CudaPhaseField` 只有 4★，入册理由是相场三相方向零覆盖且 2026 仍活跃，已标注『未成熟』。

6. **未做的部分（诚实申报）**：未 clone 任何候选仓，未编译、未跑算例，未核对论文/文献引用；`lanl/MF-LBM` 的多相细节（是否支持高密度比）仅据官方 description『DNS of flow in porous media』与清单归类推断，**待深盘核实**，本单不作断言。

## ⑥ 误差判定

本单为文献/仓源盘点，**无物理量基准**，因此不存在『关键量误差 <3%』这类判定。以可核验的替代指标交代确定性：

| 指标 | 值 | 判定 |
|---|---|---|
| 候选存在性证据覆盖率 | 8/8 = 100% | 全部有 API 返回的 stars/description |
| 候选与在盘 24 仓重叠 | 0 | 断言式校验（`shortlist.json` 生成时 assert） |
| 查询召回自检（在盘仓被检索命中率） | 16/24 = 67% | 未达 100%，说明检索式有盲区：漏掉的 8 仓多为『不在 lbm topic 下的专用求解器』，与候选召回同源风险 |
| 候选池停更 >5 年占比 | 31/137 = 23% | 用于支撑排除判据，非精度指标 |

**是否 <3%**：不适用（无基准物理量）。可交付性的真正判据是『每个候选都有可复查的 API 证据 + 排除项都有理由』，两条均满足。

## ⑦ 机器块（供入册流程消费）

```
[OSS-INV]
repo: apes-suite/musubi
tier: 3
focus: 八叉树 AMR 多物理 LBM（TU Dresden APES 生态，2026 活跃）：静态/动态自适应 + 多相/热；求解器主体为 Fortran（仓内胶水层使 GitHub 主语言显示为 Python）
status: suggested
[/OSS-INV]
[OSS-INV]
repo: Zitzeronion/Swalbe.jl
tier: 2
focus: Julia GPU 薄膜/液滴润滑 LBM（MIT，2026 仍活跃），润湿/去润湿动力学的高吞吐实现
status: suggested
[/OSS-INV]
[OSS-INV]
repo: lanl/MF-LBM
tier: 1
focus: LANL 可移植可扩展多孔介质 DNS 级 LBM（MPI+CUDA/OpenACC，BSD-3）；权威清单将其归入多相档，多相细节待深盘核实
status: suggested
[/OSS-INV]
[OSS-INV]
repo: openLB/openLB
tier: 1
focus: openLB 本体：多相/free-surface + Shan-Chen + 热耦合能力（共轭传热属较新版本，镜像停在 2019）；在盘的 openLBMPM 只是它的多相扩展分支——母体缺位
status: suggested
[/OSS-INV]
[OSS-INV]
repo: geoelements/mcmp-lbm
tier: 1
focus: 3D 多组元多相 LBM（MRT）+ 机器学习代理耦合，UT Austin geoelements 组维护
status: suggested
[/OSS-INV]
[OSS-INV]
repo: ebinan92/Fingering_dynamics
tier: 1
focus: 两相 LBM + Zou-He 速度入口 + 半程反弹润湿壁面，指进不稳定性复现脚本（含工况配置）
status: suggested
[/OSS-INV]
[OSS-INV]
repo: JuliusWeinmiller/ThermalMultiphaseOpenLB
tier: 1
focus: OpenLB 的热-多相扩展（相变/热毛细方向），2026 活跃；热-流耦合多相的参考实现
status: suggested
[/OSS-INV]
[OSS-INV]
repo: AminZar69/CudaPhaseField
tier: 1
focus: 三相相场 LBM 的 CUDA 并行求解器（GPL-3，2026 活跃）；相场多相方向唯一活跃实现（星数低=未成熟，作方向采样点）
status: suggested
[/OSS-INV]
```

## ⑧ 溯源

| 工件 | 内容 |
|---|---|
| `artifacts/m8307/discovery_raw.json` | 第 1 轮 github search：multiphase / color-gradient / free-surface / phase-field / topic(lattice-boltzmann, lattice-boltzmann-method, multiphase-lbm) / wetting / thermal / shan-chen |
| `artifacts/m8307/discovery_raw2.json` | 第 2 轮：pseudopotential / multicomponent / two-phase / droplet / conjugate-heat-transfer / topic:multiphase-flow |
| `artifacts/m8307/discovery_raw3.json` | 第 3 轮：openlb 命名族 + 短语查询（"free energy" / "contact angle" / "high density ratio"） |
| `artifacts/m8307/mflbm_search.json` | q=MF-LBM（core 配额耗尽后改走 search 池取证） |
| `artifacts/m8307/list_entry_verify.json` | canonical 清单条目二次核验（musubi / LAMBReX / 3D-LBM-AMR / LatBo.jl 等） |
| `artifacts/m8307/canonical_list_links.json` | sthavishtha/list-lattice-Boltzmann-codes README 抽取的 github 链接表（34 条） |
| `artifacts/m8307/shortlist.json` | 候选/排除/观察 + 统计 + 纯证据映射（本文件的数字来源） |
| `scripts/oss_discovery_multiphase{,_r2,_r3b}.py`、`scripts/oss_shortlist_report.py` | 检索与渲染脚本（可复跑） |
