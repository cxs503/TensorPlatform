# 值班长班次 m262 分析报告 — SDAA 验证案例扫荡线

**班次号**: m262
**日期**: 2026-10-30
**操作员**: eco_admin (TensorPlatform 智能体)

---

## 一、扫荡线完工结果总览

| 指标 | 数值 |
|------|------|
| 扫荡线累计完工单数 | 49 |
| PASS 案例数 | 28 |
| FAIL 案例数（真红） | 0 |
| AGENT_FAIL（执行失败） | 21 |
| 在飞单数（running+queued） | 5 |
| 已覆盖唯一案例数 | 28 |
| 待覆盖案例数 | 18 |

---

## 二、PASS 案例清单（28 单）

| Mission ID | 案例名 | 状态 |
|------------|--------|------|
| m213 | acoustics | PASS |
| m173 | aircraft_icing_naca0012_rime | PASS |
| m237 | cavity | PASS |
| m216 | cavity_3d_full | PASS |
| m208 | cavity_natural_convection | PASS |
| m182 | cavity_natural_convection | PASS |
| m220 | couette_2d | PASS |
| m200 | cylinder_3d | PASS |
| m238 | droplet_oscillation | PASS |
| m239 | floating_body_free_decay | PASS |
| m217 | kovasznay_2d | PASS |
| m183 | laplace_droplet | PASS |
| m176 | laplace_droplet | PASS |
| m184 | poiseuille_2d | PASS |
| m224 | poiseuille_3d_annulus | PASS |
| m252 | poiseuille_two_phase_cg | PASS |
| m209 | sphere_re100 | PASS |
| m198 | sphere_re100 | PASS |
| m232 | sphere_re100_d3q27 | PASS |
| m243 | sphere_re100_mem | PASS |
| m202 | sphere_re100_mem | PASS |
| m174 | sphere_re100_mem | PASS |
| m223 | sphere_re200 | PASS |
| m234 | stokes_sphere_dg | PASS |
| m212 | suboff_re1000 | PASS |
| m194 | suboff_re1000 | PASS |
| m222 | womersley | PASS |
| m210 | womersley | PASS |

---

## 三、FAIL 案例归类分析

### 3.1 真红 FAIL（案例判据未通过）：0 单

**无**。本班次分析范围内，所有 FAIL 均为 Agent 执行失败，非案例判据真红。

### 3.2 Agent 执行失败（AGENT_FAIL）：21 单

| Mission ID | 案例名 | 失败原因 |
|------------|--------|----------|
| m219 | cavity | 模型未调用 finish / 工具调用耗尽 |
| m214 | cavity | 模型未调用 finish / 工具调用耗尽 |
| m204 | cavity_natural_convection | 模型未调用 finish / 工具调用耗尽 |
| m195 | cavity_natural_convection | 模型未调用 finish / 工具调用耗尽 |
| m192 | cavity_natural_convection | 模型未调用 finish / 工具调用耗尽 |
| m196 | cylinder_3d | 模型未调用 finish / 工具调用耗尽 |
| m188 | cylinder_3d | 模型未调用 finish / 工具调用耗尽 |
| m178 | laplace_droplet | 模型未调用 finish / 工具调用耗尽 |
| m205 | sphere_re100 | 模型未调用 finish / 工具调用耗尽 |
| m191 | sphere_re100 | 模型未调用 finish / 工具调用耗尽 |
| m186 | sphere_re100 | 模型未调用 finish / 工具调用耗尽 |
| m179 | sphere_re100 | 模型未调用 finish / 工具调用耗尽 |
| m233 | stokes_second_problem | 模型未调用 finish / 工具调用耗尽 |
| m206 | suboff_re1000 | 模型未调用 finish / 工具调用耗尽 |
| m190 | suboff_re1000 | 模型未调用 finish / 工具调用耗尽 |
| m172 | suboff_re1000 | 模型未调用 finish / 工具调用耗尽 |
| m244 | suboff_sail_resistance | 模型未调用 finish / 工具调用耗尽 |
| m245 | thermal_cavity | 模型未调用 finish / 工具调用耗尽 |
| m199 | womersley | 模型未调用 finish / 工具调用耗尽 |
| m187 | womersley | 模型未调用 finish / 工具调用耗尽 |
| m180 | womersley | 模型未调用 finish / 工具调用耗尽 |

**失败原因归类统计**：
- 模型未调用 finish：21 单（100%）
- 环境/triton 绕行问题：0 单
- 判据真红：0 单
- 其他：0 单

---

## 四、在飞单（5 单）

| Mission ID | 案例名 | 状态 |
|------------|--------|------|
| m268 | couette_3d | running |
| m269 | cylinder | running |
| m251 | laplace_cac_phasefield | running |
| m270 | laplace_pr_eos | running |
| m250 | thermal_cavity | running |

---

## 五、本班次派发清单（3 单，硬上限）

| Mission ID | 案例名 | 类型 | 派发理由 |
|------------|--------|------|----------|
| m268 | couette_3d | 下探新案例 | 字母序第1未覆盖案例 |
| m269 | cylinder | 下探新案例 | 字母序第2未覆盖案例 |
| m270 | laplace_pr_eos | 下探新案例 | 字母序第3未覆盖案例 |

**派发依据**：在飞 SDAA 扫荡单 < 4（派发前实际 2 单），按案例字母序派下探案例。

---

## 六、未覆盖案例清单（18 例）

couette_3d, cylinder, laplace_pr_eos, maneuvering_steady_turn, permeability, permeability3d, poiseuille_3d_duct, poiseuille_3d_pipe, shear_wave_decay, sod_shock_tube, square_cylinder, startup_poiseuille, stokes_first_problem, taylor_couette, taylor_green_2d, taylor_green_3d, thermal_cavity_3d, wigley_hydrostatics

---

## 七、升级事项（留给 owner）

1. **Agent 执行失败率高**：21/49 完工单（43%）为 AGENT_FAIL，主要原因为模型未调用 finish 或工具调用耗尽。建议：
   - 优化 Agent 预算分配（增加 max_tool_calls 或改进 prompt 引导 finish 调用）
   - 对复杂案例（如 suboff_sail_resistance）考虑拆分任务或增加预算

2. **无真红 FAIL**：所有 28 单 PASS 案例均通过判据，0 单真红 FAIL。这可能表明：
   - 案例本身较简单/已充分验证
   - 或 Agent 在遇到困难时直接放弃（AGENT_FAIL）而非报告 honest-FAIL

3. **合并报告**：上一合并报告为 m249（report 158），覆盖 43 案例 32 PASS / 8 honest-FAIL / 3 WITHHELD。本班次新增完工单需纳入下次合并报告。

---

## 八、下一班建议

1. 优先跟进 m250 (thermal_cavity) 和 m251 (laplace_cac_phasefield) 的完工结果
2. 对 AGENT_FAIL 案例考虑重派（增加预算或改进 goal 描述）
3. 继续按字母序下探剩余 15 个未覆盖案例（扣除本班已派的 3 个）
4. 当累计完工单数达到新里程碑时，发布更新版合并报告
