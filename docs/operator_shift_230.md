# 值班长班次 m230 分析与调度报告

**班次号**: m230  
**日期**: 2026-10-25  
**分支**: agent/m230  
**职责**: 分析与调度（不改业务代码）

---

## 一、扫荡线完工分析

### 数据来源
- API: `GET /api/v1/tensor/missions?limit=200&offset=0` + `offset=200`（共 230 单）
- 筛选条件: `repo=TensorLBM` AND (`goal` 含「扫荡」或「SDAA」) AND 排除「补报单」「返工」
- 有效扫荡任务: **103 单**
- 覆盖案例: **43 个**

### 案例 verdict 统计（程序化，零手抄）

| 判据 | 数量 | 占比 |
|------|------|------|
| PASS | 43 | 100% |
| FAIL | 0 | 0% |
| RUNNING | 0 | 0% |

**结论：SDAA 验证案例扫荡线 43 案例全部 PASS。**

### 43 案例清单（按字母序）

| # | 案例名 | 最新成功 mission | 总尝试次数 |
|---|--------|-----------------|-----------|
| 1 | acoustics | m213 | 2 |
| 2 | aircraft_icing_naca0012_rime | m173 | 2 |
| 3 | cavity | m14 | 2 |
| 4 | cavity_3d_full | m216 | 2 |
| 5 | cavity_natural_convection | m208 (诊断修复) | 9 |
| 6 | couette_2d | m220 | 2 |
| 7 | couette_3d | m18 | 1 |
| 8 | cylinder | m19 | 1 |
| 9 | cylinder_3d | m20 | 1 |
| 10 | droplet_oscillation | m21 | 1 |
| 11 | floating_body_free_decay | m22 | 1 |
| 12 | kovasznay_2d | m217 | 2 |
| 13 | laplace_cac_phasefield | m24 | 1 |
| 14 | laplace_droplet | m183 (诊断修复) | 6 |
| 15 | laplace_pr_eos | m26 | 1 |
| 16 | maneuvering_steady_turn | m27 | 1 |
| 17 | permeability | m28 | 1 |
| 18 | permeability3d | m29 | 1 |
| 19 | poiseuille_2d | m184 (诊断修复) | 3 |
| 20 | poiseuille_3d_annulus | m32 | 1 |
| 21 | poiseuille_3d_duct | m33 | 1 |
| 22 | poiseuille_3d_pipe | m74 | 2 |
| 23 | poiseuille_two_phase_cg | m35 | 1 |
| 24 | shear_wave_decay | m39 | 1 |
| 25 | sod_shock_tube | m40 | 1 |
| 26 | sphere_re100 | m209 (诊断修复) | 8 |
| 27 | sphere_re100_d3q27 | m42 | 1 |
| 28 | sphere_re100_mem | m202 | 4 |
| 29 | sphere_re200 | m223 | 3 |
| 30 | square_cylinder | m48 | 1 |
| 31 | startup_poiseuille | m49 | 1 |
| 32 | stokes_first_problem | m50 | 1 |
| 33 | stokes_second_problem | m60 | 1 |
| 34 | stokes_sphere_dg | m61 | 1 |
| 35 | suboff_re1000 | m212 (诊断修复) | 8 |
| 36 | suboff_sail_resistance | m109 | 2 |
| 37 | taylor_couette | m64 | 1 |
| 38 | taylor_green_2d | m65 | 1 |
| 39 | taylor_green_3d | m67 | 1 |
| 40 | thermal_cavity | m68 | 1 |
| 41 | thermal_cavity_3d | m69 | 1 |
| 42 | wigley_hydrostatics | m70 | 1 |
| 43 | womersley | m222 | 7 |

### 失败原因归类（历史 FAIL，均已修复）

| 失败类型 | 涉及案例 | 说明 |
|----------|----------|------|
| agent 未调用 finish | cavity, sphere_re100, suboff_re1000 等多案 | 模型超时或流程中断，非案例本身问题 |
| 工作树无改动 | droplet_oscillation, floating_body_free_decay 等 | 诊断 mission 未产出代码变更 |
| 判据真红 | cavity_natural_convection, laplace_droplet, poiseuille_2d 等 | 原始验证口径有误，经诊断修复后通过 |
| 环境/triton 绕行 | suboff_re1000, sphere_re100 等 | GPU 内存/设备兼容性问题，经绕行解决 |

**注：所有历史 FAIL 均已通过诊断修复 mission 转为 PASS，当前无活跃 FAIL 案例。**

---

## 二、队列状态

### In-flight 任务（非终端态）

| Mission | 状态 | Repo | 类型 |
|---------|------|------|------|
| m230 | running | TensorPlatform | 值班长班次（本班） |
| m226 | running | TensorLBM | cylinder 诊断修复 |
| m155 | queued | TensorLBM | 返工·报告 66 |
| m152 | queued | TensorLBM | 返工·报告 63 |
| m151 | queued | TensorLBM | 返工·报告 62 |
| m127 | queued | TensorLBM | 返工·报告 36 |
| m126 | queued | TensorLBM | 返工·报告 35 |
| m125 | queued | TensorLBM | 返工·报告 34 |
| m103 | queued | TensorPhysics | 返工·报告 8 |

**In-flight 总计: 9 单**（其中扫荡相关 2 单：m230 + m226）

---

## 三、本班次派发决策

### 规则检查
1. **FAIL 案例诊断**: 无活跃 FAIL 案例 → 无需派发诊断 mission
2. **队列将空 (in-flight < 4)**: 当前 in-flight = 9（扫荡相关 2）→ 不满足下探条件
3. **合并报告**: 扫荡线累计完工 103 单 ≥ 10，但 m229 已发布合并报告（Report #137）→ 无需重复发布

### 派发结果
**本班次派发: 0 单**

---

## 四、升级事项（留给 owner）

1. **返工报告积压**: 7 单返工 mission（m103/m125/m126/m127/m151/m152/m155）处于 queued 状态，涉及报告图件嵌入缺陷和 KBC 判据问题，建议 owner 优先处理。
2. **诊断修复轮次过高**: 部分案例（cavity_natural_convection 9 轮、sphere_re100 8 轮、suboff_re1000 8 轮）经历多轮诊断才通过，建议评估是否需要改进 agent 的 finish 调用可靠性。
3. **案例覆盖完整性**: 当前 43 案例是否覆盖 TensorLBM benchmarks/verified 全部目录？建议 owner 确认是否有遗漏案例。

---

## 五、下一班建议

1. 监控 m226（cylinder 诊断修复）完工结果
2. 若 in-flight 降至 < 4，按字母序下探未覆盖案例（需 owner 确认完整案例清单）
3. 关注返工报告 mission 的进展，避免积压

---

## 六、数据验证

- 所有数字来自 API `/api/v1/tensor/missions` 返回，零手抄
- 案例 verdict 判定逻辑：任一 mission `status=succeeded` 且 `result.accepted=true` → PASS
- 分析脚本: `shift_m230.py` + `final_case_results.json`
