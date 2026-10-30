# 值班长 m221 班次 — SDAA 验证案例扫荡线完工审计与调度（论文级）
**班次号 m221** · repo=TensorPlatform · 分支 agent/m221 · 数据源：平台 API `/api/v1/tensor/missions`（mission 全量 221 单，列表返回 200 单，扫荡线 mission 135 单）
## ① 案例介绍（物理背景 / 几何 / 参数）
SDAA 验证案例扫荡线是 TensorLBM `benchmarks/verified/` 目录下 43 个已固化验证案例在**国产 SDAA 设备后端（/dev/tcaicard*）**上的独立复跑工程：每个案例有冻结的 README 判据（门数、阈值、参考值来源，典型阈值 |err| ≤ 3%），扫荡线只允许「复跑 + 直采证据 + 如实判定」，任何判据红不得改口径。

覆盖的物理族（案例名直接取自 API goal 字段，未人工增删）：解析流（poiseuille_2d/3d_duct/3d_pipe/3d_annulus、stokes_first/second_problem、womersley、couette_2d/3d、taylor_couette、startup_poiseuille）、经典基准（kovasznay_2d、cavity、cavity_3d_full、cylinder、cylinder_3d、square_cylinder、sphere_re100/200/re100_d3q27/re100_mem、taylor_green_2d/3d、shear_wave_decay、sod_shock_tube、acoustics）、多相/相场（laplace_droplet、laplace_cac_phasefield、laplace_pr_eos、droplet_oscillation、poiseuille_two_phase_cg）、多孔介质（permeability、permeability3d）、热对流（thermal_cavity、thermal_cavity_3d、cavity_natural_convection）、船舶总体性能相关（suboff_re1000、suboff_sail_resistance、wigley_hydrostatics、maneuvering_steady_turn、floating_body_free_decay、aircraft_icing_naca0012_rime、stokes_sphere_dg）。
| 统计项 | 数值（API 程序化） |
|---|---|
| 扫荡线 mission 总数 | 135 |
| 其中 TensorLBM 扫荡 mission（完工） | 109 |
| 其中值班长班次单 | 25 |
| succeeded / failed | 80 / 29 |
| 流程断点 mission（gate=no-evidence：未调用 finish / 工作树无改动） | 29（占 26.6%） |
| 带 `/dev/tcaicard` 设备证据行的 mission | 16 |
| 案例数 | 43 |
## ② 怎么做的（方法 / 流程 / 工具链）
1. `curl POST /auth/login`（eco_admin）取 JWT；`curl GET /tensor/missions?limit=500` 拉 mission 列表（API 实际最多返回 200 条，`total` 字段给出全量）。
2. 以 goal 含「SDAA 验证案例扫荡」筛扫荡线，逐单 `GET /tensor/missions/{id}` 取完整 `result`（headline / checks / evidence.gate / summary）。
3. 分类器 `scripts/operator_shift_m221_analyze.py`：
   - **L1 mission 结局** = API `status`；**流程断点** = `status==failed` 且 `evidence.checks.reason` 命中「未调用 finish / 工作树无任何改动」或 headline 以「未完成」开头（只看平台落盘字段，不扫正文，避免正文里诊断他人失败造成假阳）。
   - **L2 mission 结论** = result 正文中的**严格判据标记**：FAIL 级（honest-FAIL / 判定：FAIL / verdict=FAIL / overall FAIL / 0·n PASS / 如实保留 FAIL）优先，其次 PASS 级（判定：PASS / verdict=PASS / ALL PASS / x/y 判据 PASS / 全绿 / VERIFIED / <3% 达标），否则 WITHHELD。**FAIL 级优先——绝不因同段出现 PASS 字样而粉饰真红。**
   - **L3 案例终判** = 案例内最新一条带严格标记的 mission 的 L2 结论（保守：若最新一轮只做了流程修复而未明示判据，则沿用上一条明示结论并打 pf 标记）。
   - **honest-FAIL 归因** = 只看该案例终判 mission 正文关键词：Triton / OOM·内存·墙钟预算·设备 / 误差超阈 / 其余。
4. 数字零手抄：本报告全部表格由 JSON 渲染；图由 matplotlib 从同一 JSON 生成。
## ③ 计算结果（数字表）
### 3.1 案例终判分布
| 终判 | 案例数 |
|---|---|
| PASS | 18 |
| HONEST_FAIL | 20 |
| WITHHELD | 5 |

### 3.2 honest-FAIL 归因分类（判据真红不因归因是环境而转绿）
| 归因 | 案例数 |
|---|---|
| 其他/未分类 | 8 |
| 判据真红(误差超阈) | 7 |
| 环境(内存/OOM/设备) | 4 |
| triton 绕行 | 1 |

### 3.3 逐案例判据表（43 案例，全部来自 API）
| 案例 | 终判 | 判定 mission | mission 状态 | 该 mission 是否流程断点 | 案例 mission 数(succ/fail) | 设备证据行 | 归因 |
|---|---|---|---|---|---|---|---|
| acoustics | PASS | 213 | succeeded | 否 | 2(2/0) | 无 | - |
| aircraft_icing_naca0012_rime | PASS | 173 | succeeded | 否 | 3(2/1) | 无 | - |
| cavity | PASS | 101 | succeeded | 否 | 2(1/1) | 无 | - |
| cavity_3d_full | PASS | 216 | succeeded | 否 | 2(2/0) | 无 | - |
| cavity_natural_convection | PASS | 208 | succeeded | 否 | 7(4/3) | 无 | - |
| couette_2d | PASS | 220 | succeeded | 否 | 2(2/0) | 无 | - |
| couette_3d | HONEST_FAIL | 107 | failed | 是 | 1(0/1) | 无 | 其他/未分类 |
| cylinder | HONEST_FAIL | 112 | succeeded | 否 | 1(1/0) | 无 | 环境(内存/OOM/设备) |
| cylinder_3d | PASS | 196 | failed | 是 | 6(4/2) | 无 | - |
| droplet_oscillation | HONEST_FAIL | 114 | succeeded | 否 | 1(1/0) | 有 | 其他/未分类 |
| floating_body_free_decay | HONEST_FAIL | 116 | succeeded | 否 | 2(2/0) | 有 | 其他/未分类 |
| kovasznay_2d | PASS | 217 | succeeded | 否 | 3(3/0) | 有 | - |
| laplace_cac_phasefield | HONEST_FAIL | 119 | succeeded | 否 | 2(2/0) | 无 | 判据真红(误差超阈) |
| laplace_droplet | HONEST_FAIL | 165 | failed | 是 | 6(3/3) | 有 | 判据真红(误差超阈) |
| laplace_pr_eos | HONEST_FAIL | 130 | succeeded | 否 | 2(2/0) | 有 | 其他/未分类 |
| maneuvering_steady_turn | PASS | 131 | succeeded | 否 | 2(2/0) | 无 | - |
| permeability | HONEST_FAIL | 132 | succeeded | 否 | 2(2/0) | 有 | 其他/未分类 |
| permeability3d | HONEST_FAIL | 133 | succeeded | 否 | 2(2/0) | 有 | triton 绕行 |
| poiseuille_2d | HONEST_FAIL | 73 | succeeded | 否 | 3(2/1) | 有 | 判据真红(误差超阈) |
| poiseuille_3d_annulus | HONEST_FAIL | 134 | succeeded | 否 | 2(2/0) | 有 | 判据真红(误差超阈) |
| poiseuille_3d_duct | HONEST_FAIL | 135 | failed | 是 | 2(1/1) | 有 | 判据真红(误差超阈) |
| poiseuille_3d_pipe | PASS | 74 | succeeded | 否 | 2(1/1) | 有 | - |
| poiseuille_two_phase_cg | HONEST_FAIL | 136 | succeeded | 否 | 2(2/0) | 无 | 判据真红(误差超阈) |
| shear_wave_decay | WITHHELD | None | None | 否 | 2(2/0) | 无 | - |
| sod_shock_tube | PASS | 40 | succeeded | 否 | 2(2/0) | 无 | - |
| sphere_re100 | HONEST_FAIL | 75 | succeeded | 否 | 8(3/5) | 无 | 环境(内存/OOM/设备) |
| sphere_re100_d3q27 | PASS | 143 | succeeded | 否 | 2(2/0) | 无 | - |
| sphere_re100_mem | HONEST_FAIL | 202 | succeeded | 否 | 4(4/0) | 无 | 其他/未分类 |
| sphere_re200 | HONEST_FAIL | 150 | succeeded | 否 | 2(2/0) | 无 | 环境(内存/OOM/设备) |
| square_cylinder | PASS | 48 | succeeded | 否 | 2(2/0) | 有 | - |
| startup_poiseuille | PASS | 49 | succeeded | 否 | 2(1/1) | 无 | - |
| stokes_first_problem | PASS | 158 | succeeded | 否 | 2(2/0) | 有 | - |
| stokes_second_problem | WITHHELD | None | None | 否 | 1(1/0) | 无 | - |
| stokes_sphere_dg | WITHHELD | None | None | 否 | 1(1/0) | 无 | - |
| suboff_re1000 | PASS | 194 | succeeded | 否 | 8(2/6) | 无 | - |
| suboff_sail_resistance | HONEST_FAIL | 109 | succeeded | 否 | 2(2/0) | 有 | 环境(内存/OOM/设备) |
| taylor_couette | WITHHELD | None | None | 否 | 1(1/0) | 无 | - |
| taylor_green_2d | PASS | 65 | succeeded | 否 | 1(1/0) | 无 | - |
| taylor_green_3d | WITHHELD | None | None | 否 | 1(1/0) | 无 | - |
| thermal_cavity | HONEST_FAIL | 68 | succeeded | 否 | 1(1/0) | 无 | 其他/未分类 |
| thermal_cavity_3d | HONEST_FAIL | 69 | succeeded | 否 | 1(1/0) | 有 | 其他/未分类 |
| wigley_hydrostatics | PASS | 70 | succeeded | 否 | 1(1/0) | 无 | - |
| womersley | HONEST_FAIL | 187 | failed | 是 | 6(3/3) | 有 | 判据真红(误差超阈) |

### 3.4 honest-FAIL 案例的证据摘要（API result.headline 原文截取，未改写）
| 案例 | mission | 归因 | 证据原文（API） |
|---|---|---|---|
| couette_3d | 107 | 其他/未分类 | 未完成：工作树无任何改动，不能报成功 |
| cylinder | 112 | 环境(内存/OOM/设备) | Mission 19 补报完成：论文级报告 docs/report_m19.md 已写入并 POST 至平台，report id=32。报告如实记录 SDAA cylinder sweep 5/5 FAIL（honest-FAIL），根因为 |
| droplet_oscillation | 114 | 其他/未分类 | Mission 21 补报完成：为 TensorLBM droplet_oscillation SDAA sweep 撰写论文级进展报告并成功发布，POST 返回 report id=33。报告如实记录 honest-FAIL 结论——SD |
| floating_body_free_decay | 116 | 其他/未分类 | Mission 22 补报完成：论文级报告已写入 docs/report_m22.md 并通过 POST /tensor/reports 发布，report ID=39，status=published。报告如实保留 honest-FAIL |
| laplace_cac_phasefield | 119 | 判据真红(误差超阈) | Mission 24 补报完成：为 TensorLBM laplace_cac_phasefield SDAA 扫荡写论文级报告并 POST 成功，report id=40。报告如实保留 honest-FAIL 结论（物理判据 4/4 PA |
| laplace_droplet | 165 | 判据真红(误差超阈) | 未完成：工作树无任何改动，不能报成功 |
| laplace_pr_eos | 130 | 其他/未分类 | Mission 26 补报完成：已成功发布论文级进展报告（report id=26）。报告涵盖 SDAA sweep 验证 PR-EOS SCMP 模型的完整结果：12 算例（3 网格档 × 4 半径），六门判决全 PASS，表面张力 σ  |
| permeability | 132 | 其他/未分类 | Mission 28 补报完成：论文级报告 docs/report_m28.md 已写入并通过 POST /tensor/reports 发布，返回 report id=15。报告覆盖 SDAA 渗透率验证全部门控数据（φ=0.3 PASS |
| permeability3d | 133 | triton 绕行 | Mission 29 补报完成：论文级报告已写入 docs/report_m29.md 并通过 POST /tensor/reports 发布，返回 report id=45。报告核心结论：SDAA 后端在 permeability3d ( |
| poiseuille_2d | 73 | 判据真红(误差超阈) | ## SDAA 验证案例 poiseuille_2d 诊断与修复完成 ### 完成的工作 1. **诊断问题**： - 上一轮 m31 脚本不存在（FileNotFoundError），需要重新创建 - 确认 SDAA 设备 |
| poiseuille_3d_annulus | 134 | 判据真红(误差超阈) | Mission 32 补报完成：论文级报告已 POST 至平台，report id=60。报告涵盖 SDAA 设备运行 poiseuille_3d_annulus (R_o=20, 3000步) 的六要素分析，honest-FAIL（dir |
| poiseuille_3d_duct | 135 | 判据真红(误差超阈) | 未完成：工作树无任何改动，不能报成功 |
| poiseuille_two_phase_cg | 136 | 判据真红(误差超阈) | Mission 35 补报完成：论文级报告已写入 docs/report_m35.md 并通过 POST 发布，report id=57。报告如实保留 honest-FAIL 结论（relL2 11.76%/6.25% 超 3% 阈值，根因 |
| sphere_re100 | 75 | 环境(内存/OOM/设备) | ## SDAA 验证案例 sphere_re100 诊断完成 - FAIL ### 产物清单 1. **scripts/sdaa_sweep_sphere_re100.py** (9.5K) - SDAA 扫掠脚本 2. **artif |
| sphere_re100_mem | 202 | 其他/未分类 | ## 任务完成总结 ### 1. 诊断分析 - **已有数据**：D=40 (Cd=1.12198, err=2.77%)，D=60 (Cd=1.11946, err=2.54%) - **收敛性**：误差单调下降，收敛阶 p≈0.21（ |
| sphere_re200 | 150 | 环境(内存/OOM/设备) | Mission 47 补报完成：论文级报告已写入 docs/report_m47.md 并通过 POST /tensor/reports 发布，返回 report id=72。报告如实记录 honest-FAIL（Cd 误差 +4.70%  |
| suboff_sail_resistance | 109 | 环境(内存/OOM/设备) | ## SDAA 验证案例扫荡 R3: suboff_sail_resistance — 完成 ### 判定：HONEST-FAIL（SDAA 内存分配器瓶颈） ### 诊断结论 冻结阶梯 L∈{90,100,110} 在 SDAA 14 |
| thermal_cavity | 68 | 其他/未分类 | ## SDAA Sweep 完成: thermal_cavity ### 完成的工作 1. **读取案例文档**: 分析了 `benchmarks/verified/thermal_cavity/` 的 README、run.py、dr |
| thermal_cavity_3d | 69 | 其他/未分类 | ## SDAA 验证案例扫荡 — thermal_cavity_3d (W9-A) ### 做了什么 1. **读案例文档**：阅读 `benchmarks/verified/thermal_cavity_3d/README.md`、`r |
| womersley | 187 | 判据真红(误差超阈) | 未完成：工作树无任何改动，不能报成功 |

## ④ 结果图
> 图件标签统一用 ASCII：本环境 matplotlib 仅装 Noto Sans（无 CJK 字形），中文会渲染为方框；上一版报告 id=133 存在该图件缺陷，本版已修正。
![fig1](fig1)

**图 1** 逐案例 mission 结局（绿=succeeded，红=failed 全为流程断点）。
![fig2](fig2)

**图 2** 左：43 案例终判分布；右：honest-FAIL 根因归类。
**为何没有场量云图**：本班是「分析 / 调度」班次（不改业务代码、不跑新的数值算例），产出是证据统计而非新场解；云图与「数值 vs 解析解」比较曲线属于各案例单（其报告内已内嵌）。本报告的图全部由 API 派生 JSON 程序化绘制，属于同一证据链的统计视图。
## ⑤ 注意点（坑 / 局限 / 适用范围）
- **流程断点是最大噪声源**：109 张完工 TensorLBM 扫荡单中 29 张 failed，且 29 张（26.6%）的失败原因是 gate=no-evidence（未调用 finish / 工作树无改动），与数值算法无关。任何「按 mission status 统计案例 PASS 率」的做法都会严重低估扫荡线真实进度。
- **文本抽取的局限**：平台 result 正文自由文本，历史口径不一致（有人写「判定：PASS」，有人只写「6/11 PASS」）。本班用严格标记 + FAIL 优先，必然存在「实际已 PASS 但正文未明示 → 记为 WITHHELD」的保守偏差，不会出现把真红记成 PASS 的白饰偏差（后者才是不可接受的）。
- **案例结论 ≠ 维度结论**：如 laplace_droplet 的 2D 三半径在 m183 报 PASS，但同一 mission 同时记录 3D 截距 3.02% FAIL，本班按 FAIL 级标记判为 HONEST_FAIL（保守）。
- **mission 列表上限**：`limit=500` 实际返回 200 条、total=221，早期 mission（id<22）不在返回体内，故「累计完工」以 API 可见窗口为准。
- 适用范围：仅对本平台 mission/report 数据成立；不含 SDAA 设备实时状态（未做设备巡检）。
## ⑥ 误差判定（逐项 vs 基准，并显式回答是否 <3%）
**本节声明**：本班次未做新的数值算例，故不产生新的误差量；下表列出 API 已记录、且仍判红的案例判据误差（原样引用 API 摘要，未重算、未换口径）。本班**没有修改任何阈值、任何判据、任何参考值**。

| 案例 | API 记录的判据证据 | 是否 <3% |
|---|---|---|
| laplace_cac_phasefield | Mission 24 补报完成：为 TensorLBM laplace_cac_phasefield SDAA 扫荡写论文级报告并 POST 成功，report id=40。报告如实保留 honest-FAIL 结论（物理判据 4/4 PA | **否（honest-FAIL 保留）** |
| laplace_droplet | 未完成：工作树无任何改动，不能报成功 | **否（honest-FAIL 保留）** |
| poiseuille_2d | ## SDAA 验证案例 poiseuille_2d 诊断与修复完成 ### 完成的工作 1. **诊断问题**： - 上一轮 m31 脚本不存在（FileNotFoundError），需要重新创建 - 确认 SDAA 设备 | **否（honest-FAIL 保留）** |
| poiseuille_3d_annulus | Mission 32 补报完成：论文级报告已 POST 至平台，report id=60。报告涵盖 SDAA 设备运行 poiseuille_3d_annulus (R_o=20, 3000步) 的六要素分析，honest-FAIL（dir | **否（honest-FAIL 保留）** |
| poiseuille_3d_duct | 未完成：工作树无任何改动，不能报成功 | **否（honest-FAIL 保留）** |
| poiseuille_two_phase_cg | Mission 35 补报完成：论文级报告已写入 docs/report_m35.md 并通过 POST 发布，report id=57。报告如实保留 honest-FAIL 结论（relL2 11.76%/6.25% 超 3% 阈值，根因 | **否（honest-FAIL 保留）** |
| womersley | 未完成：工作树无任何改动，不能报成功 | **否（honest-FAIL 保留）** |

其余 honest-FAIL 案例（环境/triton/其他归因）在 API 摘要中未给出可比的单一误差数字，按纪律**不臆造数字**，其判据状态以各案例单报告为准。
## ⑦ 派发清单（本班次，硬上限 3 单，全部 TensorLBM 扫荡类）
| 新 mission | 源证据 mission | 案例 | 类型 | 诊断方向 |
|---|---|---|---|---|
| 222 | 187 | womersley | 诊断+修复绕行 | δ≈1.8 lu 壁面欠分辨；H=58/116 空间收敛阶梯 + 壁面半格/二阶壁面修正，给收敛阶与达标最小 H |
| 223 | 150 | sphere_re200 | 诊断+修复绕行 | 2500/12000 步未达统计稳态；checkpoint 分段长跑 6k/9k/12k 出 Cd 收敛表 + D/阻塞比分解 |
| 224 | 134 | poiseuille_3d_annulus | 诊断+修复绕行 | 3000 步未达稳态（3.24%>3%）；稳态判据(ΔQ/Q)+步数/分辨率双阶梯收敛表 |

未派「下探补证」单：派发前 in-flight = 9 单（≥4），按班次规则队列未将空，不下探。
## ⑧ 下一班建议
1. **WITHHELD 5 案例优先下探**（正文无明示判定，需补证以晋级）：shear_wave_decay、stokes_second_problem、stokes_sphere_dg、taylor_couette、taylor_green_3d。
2. **归因「其他/未分类」的 8 个 honest-FAIL 案例**在下一轮写报告时必须补一句「红门名称 + 数值 + 阈值」，否则后续班次无法自动归因。
3. 关注本班派出的 222/223/224 三单是否再次掉进「未调用 finish」——若再红，说明流程断点未真正修复，需 owner 层介入（见升级事项）。
## ⑨ 升级事项（留给 owner，本班未处理）
1. **流程断点占全部失败 100%**（29/29）：这是 agent 基础设施缺陷（长跑耗尽预算 / 落到工作树检查前的 gate 拦截），不是算法缺陷。建议 owner 层面：给扫荡类 mission 强制「第一分钟落盘 + checkpoint + finish 兜底」模板，或在 runner 侧对 no-evidence 单自动降级为「流程失败」而非「案例失败」，避免统计污染。
2. **案例终判口径不统一**：同一案例在不同班次被判为 PASS/honest-FAIL（如 laplace_droplet 2D PASS 与 3D FAIL 混述、cylinder_3d 的 VERIFIED 出现在一张 failed mission 内）。建议在 result 里落机器可读字段（`verdict`, `gate_values`, `thresholds`, `device`），班次分析即可零猜测。
3. **`/tensor/missions?limit=N` 实际截断到 200 条**：累计统计口径依赖返回窗口，建议提供 `offset` 或游标分页，并暴露「累计完工数」聚合字段。
4. **判据阈值不止 3%**：poiseuille_2d 的冻结门限为 1%（m73 记录 1.2% 判红）。任何跨案例的「<3%」汇总都可能与单案例口径冲突，建议报告模板强制写明每案例门限值。

---
**道具与可复现**：`scripts/operator_shift_m221_analyze.py`（API → `/tmp/m221_analysis.json`）、`scripts/operator_shift_m221_report.py`（JSON → 图 + 本报告）。所有数字程序化生成，零手抄。

---

## 附录 A · 本班派发单（程序化，来自 POST /tensor/missions 响应）

| 新 mission | 状态 | repo | runner | 源证据 mission | 案例 |
|---|---|---|---|---|---|
| 222 | queued | TensorLBM | native | 187 | womersley |
| 223 | queued | TensorLBM | native | 150 | sphere_re200 |
| 224 | queued | TensorLBM | native | 134 | poiseuille_3d_annulus |

派发约束自查：本班共派 3 单（上限 3）✅；全部 repo=TensorLBM、goal 含「SDAA 验证案例扫荡」✅；
未派其他类型、未改审批、未触碰 GitHub ✅。

## 附录 B · 产物与复现

| 产物 | 路径 | 生成方式 |
|---|---|---|
| 分析 JSON（API 派生） | `artifacts/operator_shift_m221/sweep_analysis_m221.json` | `scripts/operator_shift_m221_analyze.py` |
| 图 1（逐案例 mission 结局） | `artifacts/operator_shift_m221/fig1_case_missions.png` | `scripts/operator_shift_m221_report.py`（matplotlib） |
| 图 2（终判分布 + honest-FAIL 归因） | `artifacts/operator_shift_m221/fig2_verdict_reason.png` | 同上 |
| 平台报告 | report **id=134**（修正图件版；id=133 因 CJK 字形缺失作废） | `POST /api/v1/tensor/reports`，scenario_key=`sdaa_sweep_shift_m221` |

复现：
```bash
python3 scripts/operator_shift_m221_analyze.py   # API -> /tmp/m221_analysis.json
python3 scripts/operator_shift_m221_report.py    # JSON -> 图 + markdown + POST /tensor/reports
```
