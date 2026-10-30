# 值班长班次 m231 — SDAA 扫荡线分析与调度报告

**班次**: m231  
**日期**: 2026-10-29  
**值班人**: eco_admin (agent)  
**报告 ID**: #143 (published)

---

## 一、执行摘要

| 指标 | 数值 |
|------|------|
| 全量 mission | 231 |
| SDAA 扫荡线 mission 完工 | 127 (53 扫荡 + 74 诊断/补报) |
| 验证案例总数 | 43 |
| 案例判定 PASS | 43 (100%) |
| 案例判定 FAIL | 0 |
| 失败 mission | 33 (agent_no_finish 23 + no_changes 10) |
| 派发前 in-flight | 0 |
| 本班次派发 | 3 (m232/m233/m234) |
| 合并报告 | 已存在 17 篇 (最新 #141) |

---

## 二、扫荡线分析表

### 2.1 失败原因归类（33 单）

| 失败原因 | 数量 | 占比 | 说明 |
|----------|------|------|------|
| agent_no_finish | 23 | 69.7% | 模型未调用 finish，流程断点 |
| no_changes | 10 | 30.3% | 工作树无任何改动，不能报成功 |
| triton_bypass | 0 | 0% | 无 triton 绕行失败 |
| criterion_red | 0 | 0% | 无判据真红 |
| environment | 0 | 0% | 无环境/超时失败 |

**结论**: 33 单失败 100% 为流程断点，非案例判据问题。

### 2.2 逐案例判定表（43 案例）

| # | 案例 | 判定 | 总尝试 | 成功 | 失败 | 最新 mission | 最新状态 |
|---|------|------|--------|------|------|-------------|----------|
| 1 | acoustics | PASS | 3 | 3 | 0 | m213 | succeeded |
| 2 | aircraft_icing_naca0012_rime | PASS | 3 | 2 | 1 | m173 | succeeded |
| 3 | cavity | PASS | 4 | 2 | 2 | m219 | failed |
| 4 | cavity_3d_full | PASS | 3 | 3 | 0 | m216 | succeeded |
| 5 | cavity_natural_convection | PASS | 8 | 4 | 4 | m208 | succeeded |
| 6 | couette_2d | PASS | 3 | 3 | 0 | m220 | succeeded |
| 7 | couette_3d | PASS | 2 | 1 | 1 | m107 | failed |
| 8 | cylinder | PASS | 3 | 3 | 0 | m226 | succeeded |
| 9 | cylinder_3d | PASS | 7 | 5 | 2 | m200 | succeeded |
| 10 | droplet_oscillation | PASS | 3 | 2 | 1 | m227 | failed |
| 11 | floating_body_free_decay | PASS | 3 | 2 | 1 | m228 | failed |
| 12 | kovasznay_2d | PASS | 3 | 3 | 0 | m217 | succeeded |
| 13 | laplace_cac_phasefield | PASS | 2 | 2 | 0 | m119 | succeeded |
| 14 | laplace_droplet | PASS | 6 | 3 | 3 | m183 | succeeded |
| 15 | laplace_pr_eos | PASS | 2 | 2 | 0 | m130 | succeeded |
| 16 | maneuvering_steady_turn | PASS | 2 | 2 | 0 | m131 | succeeded |
| 17 | permeability | PASS | 2 | 2 | 0 | m132 | succeeded |
| 18 | permeability3d | PASS | 2 | 2 | 0 | m133 | succeeded |
| 19 | poiseuille_2d | PASS | 3 | 2 | 1 | m184 | succeeded |
| 20 | poiseuille_3d_annulus | PASS | 3 | 3 | 0 | m224 | succeeded |
| 21 | poiseuille_3d_duct | PASS | 2 | 1 | 1 | m135 | failed |
| 22 | poiseuille_3d_pipe | PASS | 2 | 1 | 1 | m74 | succeeded |
| 23 | poiseuille_two_phase_cg | PASS | 2 | 2 | 0 | m136 | succeeded |
| 24 | shear_wave_decay | PASS | 2 | 2 | 0 | m138 | succeeded |
| 25 | sod_shock_tube | PASS | 2 | 2 | 0 | m142 | succeeded |
| 26 | sphere_re100 | PASS | 8 | 3 | 5 | m209 | succeeded |
| 27 | sphere_re100_d3q27 | PASS | 1 | 1 | 0 | m42 | succeeded |
| 28 | sphere_re100_mem | PASS | 4 | 4 | 0 | m202 | succeeded |
| 29 | sphere_re200 | PASS | 3 | 3 | 0 | m223 | succeeded |
| 30 | square_cylinder | PASS | 2 | 2 | 0 | m156 | succeeded |
| 31 | startup_poiseuille | PASS | 2 | 1 | 1 | m157 | failed |
| 32 | stokes_first_problem | PASS | 2 | 2 | 0 | m158 | succeeded |
| 33 | stokes_second_problem | PASS | 1 | 1 | 0 | m60 | succeeded |
| 34 | stokes_sphere_dg | PASS | 1 | 1 | 0 | m61 | succeeded |
| 35 | suboff_re1000 | PASS | 8 | 2 | 6 | m212 | succeeded |
| 36 | suboff_sail_resistance | PASS | 2 | 2 | 0 | m109 | succeeded |
| 37 | taylor_couette | PASS | 1 | 1 | 0 | m64 | succeeded |
| 38 | taylor_green_2d | PASS | 1 | 1 | 0 | m65 | succeeded |
| 39 | taylor_green_3d | PASS | 1 | 1 | 0 | m67 | succeeded |
| 40 | thermal_cavity | PASS | 1 | 1 | 0 | m68 | succeeded |
| 41 | thermal_cavity_3d | PASS | 1 | 1 | 0 | m69 | succeeded |
| 42 | wigley_hydrostatics | PASS | 1 | 1 | 0 | m70 | succeeded |
| 43 | womersley | PASS | 7 | 4 | 3 | m222 | succeeded |

---

## 三、派发清单

### 派发依据
- in-flight = 0（< 4），队列将空
- 无 FAIL 案例（43 案例全 PASS），无需派诊断+修复绕行
- 按案例字母序选取仅 1 次验证（未复跑）的前 3 个案例

### 派发明细

| Mission ID | 案例 | 类型 | repo | runner | goal 摘要 | 状态 |
|------------|------|------|------|--------|-----------|------|
| m232 | sphere_re100_d3q27 | 下探补证·第2轮稳定性验证 | TensorLBM | native | D3Q27 球体绕流 Re=100 Cd 复跑 | queued |
| m233 | stokes_second_problem | 下探补证·第2轮稳定性验证 | TensorLBM | native | Stokes 第二问题解析解速度剖面复跑 | queued |
| m234 | stokes_sphere_dg | 下探补证·第2轮稳定性验证 | TensorLBM | native | Stokes 绕球阻力系数 DG 方法复跑 | queued |

**硬上限遵守**: 派发 3 单（≤3），全部为 TensorLBM 扫荡类 goal，未改审批，未碰 GitHub。

---

## 四、里程碑检查

- 扫荡线累计完工 ≥10 单：✅ 127 单（远超 10 单门槛）
- 尚无合并报告：❌ 已有 17 篇 SDAA 扫荡报告（最新 #141, m230 班次）
- 结论：无需重复发布合并报告。本班次发布进展报告 #143。

---

## 五、升级事项（留 owner）

### 5.1 系统性问题：agent_no_finish 占失败 69.7%

33 单失败中 23 单为「模型未调用 finish」，这是 agent 执行流程的系统性问题。建议：
- 在 agent 框架层增加 finish 调用保障（如超时自动 finish）
- 或在 mission 完成检查中增加 finish 调用提醒

### 5.2 系统性问题：no_changes 占失败 30.3%

10 单失败为「工作树无任何改动」，多见于补报单和诊断 mission。建议：
- 补报单 mission 应允许无代码改动（纯报告任务）
- 诊断 mission 若确认无 bug 应允许 finish 而不要求改动

### 5.3 高返工率案例关注

以下案例经历了 ≥6 轮返工，虽然最终 PASS，但返工成本高：
- sphere_re100: 8 轮（3 成功 / 5 失败）
- suboff_re1000: 8 轮（2 成功 / 6 失败）
- cavity_natural_convection: 8 轮（4 成功 / 4 失败）
- womersley: 7 轮（4 成功 / 3 失败）
- cylinder_3d: 7 轮（5 成功 / 2 失败）

建议 owner 评估这些案例的验证口径是否过于严格，或 agent 执行环境是否存在间歇性问题。

### 5.4 6 案例最新 mission 失败

以下案例的最新 mission 失败（但案例本身已有早期成功）：
- cavity (m219 failed)
- couette_3d (m107 failed)
- droplet_oscillation (m227 failed)
- floating_body_free_decay (m228 failed)
- poiseuille_3d_duct (m135 failed)
- startup_poiseuille (m157 failed)

建议下一班次关注这些案例的稳定性，必要时派发复跑确认。

---

## 六、下一班建议

1. **监控 m232/m233/m234 完工结果**：这 3 单为下探补证，确认 sphere_re100_d3q27、stokes_second_problem、stokes_sphere_dg 的判据可复现性。
2. **继续按字母序派发下探补证**：剩余 6 个仅 1 次验证的案例（taylor_couette、taylor_green_2d、taylor_green_3d、thermal_cavity、thermal_cavity_3d、wigley_hydrostatics）待复跑。
3. **关注 6 案例最新 mission 失败**：cavity、couette_3d、droplet_oscillation、floating_body_free_decay、poiseuille_3d_duct、startup_poiseuille 的稳定性。
4. **评估高返工率案例**：sphere_re100、suboff_re1000、cavity_natural_convection 是否需要调整验证口径。
5. **合并报告已充分**：17 篇 SDAA 扫荡报告已覆盖，无需重复发布。

---

**数据源**: API `/api/v1/tensor/missions` (231 单全量, 5 页分页, total=231)  
**所有数字从 API 程序化提取，零手抄**  
**班次**: m231 | **报告**: #143 (published)
