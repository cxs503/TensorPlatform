# 值班长班次 m242 — SDAA 扫荡线分析与调度

- 班次 mission：242（repo=TensorPlatform，分支 agent/m242）
- 数据源：平台 API（GET /tensor/missions 分页 242 张、GET /tensor/reports、GET /tensor/reports/{id}）
- 复用脚本（可复算）：`scripts/ops/sdaa_sweep_audit.py`（审计）、`scripts/ops/sdaa_verdict.py`（判据阶梯）、
  `scripts/ops/sdaa_shift242_dispatch.py`（派单）
- 中间产物：`docs/_sdaa_sweep_m242.json`、`docs/_sdaa_sweep_m242.md`
- 本班次未改动任何业务代码（本仓仅新增审计/派单脚本与文档）

## 1. 扫荡线总览（API 原值）

| 指标 | 数值 |
|---|---|
| 平台 mission 总数 | 242 |
| 扫荡线 mission 总数 | 101 |
| 已完工 | 100（succeeded 71 / failed 29） |
| 在飞 | [237] |
| 覆盖案例 | 43 |
| 案例最新判据 | PASS 29 / FAIL 9 / WITHHELD 5 / CONFLICT 0 |
| 失败单归因（可多标签） | 流程断点 29、triton绕行 2、判据真红 1、环境 1 |
| 设备证据卡数 | 17 |

判据来源与规则：见 `scripts/ops/sdaa_verdict.py` 头部说明（A/B/C 三级阶梯，同级冲突标 CONFLICT，无显式语句标 WITHHELD，不改口径）。

## 2. 逐案例分析表

| 案例 | 累计单数 | 失败单数 | 最新单 | 最新判据 | 判据来源 | 证据片段 | 设备 |
|---|---|---|---|---|---|---|---|
| acoustics | 2 | 0 | 213 | PASS | A@report127 | 结论**：SDAA 设备复跑 D2Q9 平面声波基准，两冻结判据均 **PASS | /dev/tcaicard12 |
| aircraft_icing_naca0012_rime | 2 | 0 | 173 | PASS | C-weak | icing_naca0012_rime_v2.py → 11/11 gates PASS; artifacts/ 下5个JSON | /dev/tcaicard24 |
| cavity | 3 | 2 | 219 | WITHHELD | no-token | 未完成：模型未调用 finish | - |
| cavity_3d_full | 2 | 0 | 216 | PASS | A | all_pass=true | /dev/tcaicard5 |
| cavity_natural_convection | 7 | 4 | 208 | FAIL | A@report125 | 结论**: m192/m195/m204 连续 3 轮 FAIL | - |
| couette_2d | 2 | 0 | 220 | PASS | A@report132 | 结论**：所有关键量误差均 **<3%**，判定为 **PASS | /dev/tcaicard10 |
| couette_3d | 1 | 0 | 18 | PASS | A | all_pass=true | - |
| cylinder | 2 | 0 | 226 | FAIL | A@report140 | 结论**：粗网格（D=10-20）确实导致FAIL | /dev/tcaicard20 |
| cylinder_3d | 5 | 2 | 200 | PASS | B | 验证通过 | /dev/tcaicard0 |
| droplet_oscillation | 3 | 1 | 238 | PASS | C-weak | esult.json validation: R128 err=-2.649% PASS, R160 err=-2.404% P | /dev/tcaicard0,/dev/tcaicard1 |
| floating_body_free_decay | 3 | 1 | 239 | PASS | A | verdict.pass=True | /dev/tcaicard0 |
| kovasznay_2d | 2 | 0 | 217 | PASS | A | 判定：PASS | /dev/tcaicard9 |
| laplace_cac_phasefield | 1 | 0 | 24 | FAIL | A | overall FAIL | - |
| laplace_droplet | 5 | 2 | 183 | FAIL | A@report110 | 结论部分已如实记为 FAIL | /dev/accelerator-node-a |
| laplace_pr_eos | 1 | 0 | 26 | PASS | B | All 6 gates PASS | /dev/tcaicard23 |
| maneuvering_steady_turn | 1 | 0 | 27 | PASS | A@report54 | 结论：8/8 PASS | - |
| permeability | 1 | 0 | 28 | PASS | B | 全部 PASS | /dev/tcaicard6 |
| permeability3d | 1 | 0 | 29 | FAIL | C-weak | 抄；N=64 结果与原始 CUDA 结果对比差异 <0.004%；honest-FAIL 如实记录 | /dev/tcaicard8 |
| poiseuille_2d | 3 | 1 | 184 | PASS | B | 验证通过 | /dev/tcaicard6 |
| poiseuille_3d_annulus | 2 | 0 | 224 | PASS | B | All 6 gates PASS | /dev/tcaicard11 |
| poiseuille_3d_duct | 1 | 0 | 33 | PASS | B@report58 | 判据均通过 | /dev/tcaicard15 |
| poiseuille_3d_pipe | 2 | 1 | 74 | PASS | A | 结论：`poiseuille_3d_pipe` 在 SDAA 上 **PASS | /dev/tcaicard26 |
| poiseuille_two_phase_cg | 1 | 0 | 35 | FAIL | A@report57 | VERDICT: FAIL | - |
| shear_wave_decay | 1 | 0 | 39 | PASS | A@report71 | 结论：PASS | - |
| sod_shock_tube | 1 | 0 | 40 | PASS | A@report66 | 结论：全部 10 项门控 PASS | - |
| sphere_re100 | 8 | 5 | 209 | PASS | C-weak | 调用finish"失败，**非算法/物理失败**，是流程断点。m198是唯一稳定PASS轮次。 | - |
| sphere_re100_d3q27 | 2 | 0 | 232 | PASS | A@report144 | 判定：PASS | - |
| sphere_re100_mem | 3 | 0 | 202 | FAIL | A | 诊断结论：honest-FAIL | - |
| sphere_re200 | 2 | 0 | 223 | WITHHELD | no-token | ## SDAA sphere_re200 Round 3 — Statistical Convergence Diagnosis Compl | - |
| square_cylinder | 1 | 0 | 48 | PASS | A@report80 | 结论**: **PASS | /dev/tcaicard18 |
| startup_poiseuille | 1 | 0 | 49 | PASS | B | 全部 PASS | - |
| stokes_first_problem | 1 | 0 | 50 | PASS | A | all_pass=True | /dev/tcaicard25 |
| stokes_second_problem | 2 | 1 | 233 | PASS | A@report145 | VERDICT: PASS | - |
| stokes_sphere_dg | 2 | 0 | 234 | PASS | A@report146 | all_pass: true | - |
| suboff_re1000 | 8 | 6 | 212 | PASS | B | 主判据PASS | - |
| suboff_sail_resistance | 2 | 0 | 109 | FAIL | A | 判定：HONEST-FAIL | /dev/tcaicard25 |
| taylor_couette | 1 | 0 | 64 | WITHHELD | no-token | ## SDAA 扫荡完成：taylor_couette 案例  ### 完成的工作  1. **读取案例文档**：详细阅读了 benchma | - |
| taylor_green_2d | 1 | 0 | 65 | PASS | B | 全部 PASS | - |
| taylor_green_3d | 1 | 0 | 67 | PASS | C-weak | device_evidence present, all 3 criteria PASS, monotone convergen | - |
| thermal_cavity | 1 | 0 | 68 | FAIL | B | 诚实报告 FAIL | - |
| thermal_cavity_3d | 1 | 0 | 69 | WITHHELD | B | NOT_RUN | /dev/tcaicard12 |
| wigley_hydrostatics | 1 | 0 | 70 | PASS | B | all 7 gate criteria PASS | - |
| womersley | 7 | 3 | 222 | WITHHELD | no-token | ## SDAA Womersley v9 诊断+修复绕行 — 完成  ### 基线确认 H=29, α=8, τ=0.80, 标准半格反弹  | /dev/tcaicard23 |


### 2.1 FAIL 案例（9 例）

| 案例 | 最新单 | 判据 | 判据来源 | 证据片段（API 文本原文） |
|---|---|---|---|---|
| cavity_natural_convection | 208 | FAIL | A@report125 | 结论**: m192/m195/m204 连续 3 轮 FAIL |
| cylinder | 226 | FAIL | A@report140 | 结论**：粗网格（D=10-20）确实导致FAIL |
| laplace_cac_phasefield | 24 | FAIL | A | overall FAIL |
| laplace_droplet | 183 | FAIL | A@report110 | 结论部分已如实记为 FAIL |
| permeability3d | 29 | FAIL | C-weak | 抄；N=64 结果与原始 CUDA 结果对比差异 <0.004%；honest-FAIL 如实记录 |
| poiseuille_two_phase_cg | 35 | FAIL | A@report57 | VERDICT: FAIL |
| sphere_re100_mem | 202 | FAIL | A | 诊断结论：honest-FAIL |
| suboff_sail_resistance | 109 | FAIL | A | 判定：HONEST-FAIL |
| thermal_cavity | 68 | FAIL | B | 诚实报告 FAIL |

### 2.2 未收口案例（5 例：WITHHELD / CONFLICT）

| 案例 | 最新单 | 判据 | 来源 | 记录摘要（API 文本原文，已截断） |
|---|---|---|---|---|
| cavity | 219 | WITHHELD | no-token | 未完成：模型未调用 finish |
| sphere_re200 | 223 | WITHHELD | no-token | ## SDAA sphere_re200 Round 3 — Statistical Convergence Diagnosis Complete

### Root Cause Confirmed
Mission 15 |
| taylor_couette | 64 | WITHHELD | no-token | ## SDAA 扫荡完成：taylor_couette 案例

### 完成的工作

1. **读取案例文档**：详细阅读了 benchmarks/verified/taylor_couette/README.md 和  |
| thermal_cavity_3d | 69 | WITHHELD | B | NOT_RUN |
| womersley | 222 | WITHHELD | no-token | ## SDAA Womersley v9 诊断+修复绕行 — 完成

### 基线确认
H=29, α=8, τ=0.80, 标准半格反弹 → complex_rel_max = **4.890% > 3%** (FAI |

## 3. 本班次派发清单（3 单，硬上限内；runner=native，repo=TensorLBM）

| mission | 状态 | repo | runner | 目标案例 | 类型 |
|---|---|---|---|---|---|
| 243 | running | TensorLBM | native | sphere_re100_mem | 诊断+修复绕行 |
| 244 | running | TensorLBM | native | suboff_sail_resistance | 诊断+修复绕行 |
| 245 | running | TensorLBM | native | thermal_cavity | 诊断+修复绕行 |

派单依据（判据红优先，队列在飞 1<4 但硬上限 3 单，故全部用于 FAIL 修复；下探案例留待下一班）：
- **sphere_re100_mem**（m202, honest-FAIL）：D=40 err=2.77% / D=60 err=2.54%，收敛阶 p≈0.21，3 轮仍不能稳定进入 3% → 要求 D=80/160 补档 + 统计误差棒，判据不改。
- **suboff_sail_resistance**（m109, HONEST-FAIL）：SDAA allocator 在 L≥64 首个 collide step OOM（14.94 GiB 卡），setup 峰值仅 0.38–4.47 GiB → 显存剖面 + 流式/分块 collide。
- **thermal_cavity**（m68, 0/6 PASS）：步数不足未达稳态 → 延长步数 + 窗口漂移判据 + de Vahl Davis 1983 逐项对比。

## 4. 里程碑判定（合并报告）

- 条件：扫荡线累计完工 ≥10 单（实际 100）**且**尚无合并报告。
- 核查结果：平台已存在多篇标题含「SDAA 扫荡」的合并报告（最近：report id=147/m236、143+142/m231、141/m230、137/m229、134+133/m221）。
- 结论：**不重复发布「合并报告」**，改为发布本班次论文级分析报告（含逐案例判据表、FAIL 如实、设备证据、误差判定、局限），
  并在报告中把「历史合并报告口径互斥」列为升级事项（不改任何口径）。
- 已发布：`POST /api/v1/tensor/reports` → **report id=152**（mission_id=242，2 张 base64 内嵌图：
  `m242_verdict_matrix.png` 案例×轮次判据矩阵、`m242_fail_pareto.png` 判据分布/失败归因/设备分布）。

## 5. 下一班建议

1. **口径统一（最高优先）**：等 owner 裁定案例级判据取数规则后，把 `scripts/ops/sdaa_verdict.py` 的案例级汇总改为结构化 verdict 字段优先，重跑本审计即可得到稳定数字。
2. **下探案例**：本班 3 单全部用于 FAIL 修复，建议下一班按字母序补下探以下未收口/低调案例：cavity, sphere_re200, taylor_couette, thermal_cavity_3d, womersley。
3. **关注在飞**：m237（cavity，第 2 轮，历史 2 张 failed 单均为「未调用 finish」）——若再次流程断点，直接转 infra 升级而非再派同案例。
4. **输出侧**：建议扫描 `docs/_sdaa_sweep_m242.md` 中标 `C-weak` 的案例（当前 5 例）用报告 markdown 全文复核，避免弱证据进入正式判据。
5. **待复核冲突**：无。

## 6. 升级事项（系统性问题，留 owner）

1. 历史合并报告口径互斥（「43 案例全 PASS」 vs 本班「PASS 29/FAIL 9/WITHHELD 5」）——需 owner 定唯一取数规则。
2. 流程断点占失败单 29/29（未调用 finish / 工作树无改动 / 工具调用耗尽）：应从 agent 基础设施侧修复，而非按案例重复派单。
3. `result.headline/checks/approval.reason` 截断 ~200 字符 → 审计不可复核；建议存全量文本或结构化 verdict 字段。
4. SDAA allocator OOM（suboff_sail_resistance L≥64）为环境级瓶颈，已派 m244 诊断，若确认是 allocator 保留策略需平台侧（非案例侧）修复。
5. 报告欠账/重复：平台存在多篇同 mission 的重复报告（如 report 133/134 同 mission 221、142/143 同 mission 231、82/83 同 mission 49），建议去重。
6. **图件不可回读**：本班报告 id=152 POST 返回 200 且 `figures` 的 name/caption 已持久化，但 `GET /tensor/reports/152`
   只回传 name+caption、**不回传 png_base64**（历史报告 128/138 同样如此，149 则为空数组）。与 owner「图件 base64 内嵌可核验」要求不符，
   需平台侧确认是否落盘为独立资源；本班同时把图件 PNG 存于仓库 `docs/figs/` 以保证可复核。
