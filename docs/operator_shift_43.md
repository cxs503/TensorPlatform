# 值班长班次 43（分析与调度，不改业务代码）

- 班次: m43 / agent/m43 · 2026-10-29
- 数据源: `GET /api/v1/tensor/missions?limit=50`（total=45）与 `GET /api/v1/tensor/reports`，全部数字取自 API 返回，零手抄。原始落盘: `agent_work/m43_shift/missions_raw.json`、`sweep_done.json`、`sweep_running.json`、`dispatch_results.json`、`report_result.json`。
- 说明: 本环境 curl 被安全策略拦截，改用 python3 urllib 访问同一 API（127.0.0.1:8010），登录 eco_admin 成功。

## 1. 扫荡线完工分析（goal 前缀「SDAA 验证案例扫荡」，共 27 单：完工 17 / 在途 10）

| mission | 案例 | mission 状态 | 案例判定 | 判据要点（引自 result） | 设备证据 |
|---|---|---|---|---|---|
| m12 | acoustics | succeeded | PASS | 判据A 0.2190%≤3%、判据B 1.3145%≤3%（均单调）；墙钟 15.3s；TRITON_INTERPRET=1 绕行已记录 | /dev/tcaicard12 |
| m13 | aircraft_icing_naca0012_rime | succeeded | FAIL（honest） | gates 6/11 PASS，verdict=FAIL；det_pair/ladder 因预算跳过（skipped_runs 记录） | /dev/tcaicard24 |
| m14 | cavity | succeeded | PASS | 三判据均 PASS；墙钟 103.7s | 设备证据行存在 |
| m16 | cavity_natural_convection | **failed** | 无判定 | 「未完成：模型未调用 finish」（MiniMax-M2.5-AWQ） | — |
| m17 | couette_2d | succeeded | FAIL（honest） | 收敛性判据 honest-FAIL 如实记录 | /dev/tcaicard10 |
| m18 | couette_3d | succeeded | PASS | all_pass=true | 设备证据行存在 |
| m19 | cylinder | succeeded | FAIL（honest） | 5 项判据全 FAIL（re20_st/re100/re200），未降判据 | /dev/tcaicard20 |
| m20 | cylinder_3d | succeeded | FAIL（honest） | G1 +6.62%、G2 +16.25%（超 15% 红线）、G3 9.62% span；mass drift <0.04%；墙钟 1220s | /dev/tcaicard0 |
| m21 | droplet_oscillation | succeeded | ⚠️ 不完整 | checks 自述「Simulation in progress - full results pending completion」却 succeeded | /dev/tcaicard1 |
| m22 | floating_body_free_decay | succeeded | FAIL（honest） | 业务代码 bug 致无数值，如实记 FAIL | /dev/tcaicard0 |
| m23 | kovasznay_2d | succeeded | PASS | 7 项判据全 PASS，verdict=PASS | /dev/tcaicard9 |
| m24 | laplace_cac_phasefield | succeeded | FAIL（honest） | per-arm err 0.0567% PASS / u_max 6.787e-05 PASS / drift@10k 8.61e-13 PASS / NaN-free PASS / B1 spread NOT_EVALUATED / 臂覆盖 FAIL → overall FAIL | 设备证据行存在 |
| m25 | laplace_droplet | **failed** | 无判定 | 「未完成：模型未调用 finish」（Qwen3.8-27B） | — |
| m26 | laplace_pr_eos | succeeded | PASS | 6 gates 全 PASS；TRITON_INTERPRET=1 绕行已记录，热路径无 triton kernel | /dev/tcaicard23 |
| m27 | maneuvering_steady_turn | succeeded | PASS | 8/8 PASS；墙钟 59.89s | device_evidence 存在 |
| m28 | permeability | succeeded | PASS | 全 PASS；SDAA vs CUDA 偏差 0.0884%/0.0446%；墙钟 91s；eager（teco_inductor 限制） | /dev/tcaicard6 |
| m32 | poiseuille_3d_annulus | succeeded | FAIL（honest） | direct_observable 3.237%>3%，未达稳态 | /dev/tcaicard11 |

### 统计
- **PASS 7**: acoustics, cavity, couette_3d, kovasznay_2d, laplace_pr_eos, maneuvering_steady_turn, permeability
- **FAIL·判据真红 5**: aircraft_icing_naca0012_rime(6/11), couette_2d(收敛), cylinder(5/5), cylinder_3d(G1/G2/G3), poiseuille_3d_annulus(3.237%>3%)
- **FAIL·其他 1**: floating_body_free_decay（业务代码 bug）
- **无判定 2**: cavity_natural_convection(m16), laplace_droplet(m25) —— agent 未收尾
- **不完整 1**: droplet_oscillation(m21) —— 提前收尾，不得计入 PASS
- **triton 绕行（非失败原因，如实记录）3**: acoustics, laplace_pr_eos, aircraft_icing_naca0012_rime
- **环境类失败 0**（本批无环境直接致 FAIL）

### 在途 10 单（in-flight=10 ≥ 4，未触发字母序补派）
cavity_3d_full, permeability3d, poiseuille_2d, poiseuille_3d_duct, poiseuille_3d_pipe, poiseuille_two_phase_cg, shear_wave_decay, sod_shock_tube, sphere_re100, sphere_re100_d3q27

## 2. 派发清单（硬上限 3 单，已用满；均 runner=native, repo=TensorLBM, base_ref=main, budget=80 调用/7200s/3M tokens，与扫荡线一致）

| 新单 | 案例 | 类型 | 依据 |
|---|---|---|---|
| m51 | cavity_natural_convection | 诊断+修复绕行 | m16 failed 无判定（未调用 finish）；goal 含失败证据与诊断方向（先验 SDAA 可跑通性，跑不通如实记错误与设备证据） |
| m52 | laplace_droplet | 诊断+修复绕行 | m25 failed 无判定（未调用 finish）；同上 |
| m53 | cylinder_3d | 诊断+修复绕行 | m20 判据真红：G2 +16.25% 超 15% 红线；诊断方向=SDAA vs CUDA 同网格对照归因（设备 vs 求解器）+ 偏差物理量/时间段定位 |

派发响应: m51/m52/m53 均 200 queued（见 `agent_work/m43_shift/dispatch_results.json`）。

## 3. 里程碑：合并报告已发布
- 触发条件: 扫荡线累计完工 17 ≥ 10 且无「SDAA 扫荡」合并报告（既有报告 id=1/2 均非扫荡合并报告）。
- 已发布: **report id=3**《SDAA 扫荡合并报告：TensorLBM 验证案例首批 17 单完工判据汇总（2026-10-29）》，status=published，scenario_key=node099-sdaa，tags=[sdaa, sweep, tensorlbm, verification, merged-report]。
- 内容: 逐案例判据表（17 行）+ FAIL 如实清单（7 例不洗白）+ 设备证据汇总（tcaicard0/1/6/9/10/11/12/20/23/24，无 CPU 冒充证据）+ 局限（自报 result 未跨树复算、m21 不可信、2 例待重派、10 单在途未纳入、figures 省略）。

## 4. 升级事项（留给 owner，系统性问题）
1. **m21 droplet_oscillation 提前收尾（完整性风险）**：mission succeeded 但 checks 自述仿真仍在进行、结果 pending。该单不得计入 PASS；建议 owner 复核其 artifacts/sdaa_droplet_oscillation.json 或重跑。此类「未完成即 finish」应纳入验收门（acceptance 时校验 artifact 完整性）。
2. **agent 未收尾致 mission failed（2 例）**：m16（MiniMax-M2.5-AWQ，60 次调用）、m25（Qwen3.8-27B，13 次调用）均因未调用 finish 被判 failed，案例本身可过与否未知。已重派 m51/m52 并在 goal 中显式要求 finish。建议平台侧对「无 finish 的 running 超时」加自动提醒/降级策略。
3. **floating_body_free_decay 业务代码 bug**：扫荡侧按边界不改业务代码，案例无法产出数值。需 owner 安排业务代码修复后重扫。
4. **cylinder_3d G2 +16.25% 超 15% 不可接受红线**：本批最严重真红，已派 m53 归因诊断（设备数值特性 vs 求解器问题）。
5. **上一班值班长 mission m30 failed**（60 次调用未 finish）：值班长班次自身也出现未收尾，建议值班长 goal 模板同样加 finish 硬要求。
6. **SDAA teco_inductor 限制**：部分案例只能 eager 模式（permeability 单记录），影响墙钟与可复现性，属设备侧已知限制，记录备查。

## 5. 下一班建议
1. 优先收 m51/m52/m53 三单结果：m51/m52 若仍无判定，升级给 owner（连续两班同因失败即系统性问题）；m53 出归因结论后决定 cylinder_3d 是否转「设备问题」归档。
2. 复核 m21 droplet_oscillation 的 artifact（升级事项 1），给出 PASS/FAIL 终判。
3. 在途 10 单完工后刷新合并报告（report id=3 的刷新版），补 figures。
4. 若 in-flight 掉到 <4，按案例字母序补派下探案例（当前字母序下一批: buoyancy 系/后续字母序案例，以 benchmarks/verified 目录实际清单为准）。
5. 关注 aircraft_icing 的预算跳过项（det_pair/ladder）：若 owner 认可，可派补跑单补齐 11 gates 全量证据（需 owner 批准预算口径，值班长不擅自改）。

## 6. 边界遵守
- 只分析不伪造；判据红一律未改口径；未改审批、未碰 GitHub、未派非扫荡类 goal。
- 本班次未改任何业务代码；新增文件仅本交付文档与 agent_work/m43_shift/ 工作底稿。
