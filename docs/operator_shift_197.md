# 值班长班次 m197 — SDAA 扫荡线分析与调度报告

**班次号**: 197  
**时间**: 2026-10-29  
**操作员**: eco_admin (TensorPlatform Agent)  
**职责**: 分析与调度（不改业务代码）

---

## 一、扫荡线完工结果分析

### 数据来源
- POST /api/v1/auth/login (eco_admin) → 获取 access_token
- GET /api/v1/tensor/missions?limit=200 → 拉取 197 条 mission
- 程序化筛选 SDAA 验证案例扫荡类 mission（正则匹配 `SDAA 验证案例扫荡——案例 [a-zA-Z_]+`）
- 排除：值班长班次、补报单、非扫荡类任务

### 统计总览

| 指标 | 数值 |
|------|------|
| 总 mission 数 | 197 |
| SDAA 扫荡类 mission | 121 |
| 独立案例数 | 43 |
| 原始扫荡 PASS | 33 |
| 原始扫荡 honest-FAIL | 2 |
| 原始扫荡 FAILED(agent) | 8 |
| 诊断修复后转为 PASS | 5 |
| 仍 FAILED | 3 |
| 在途(in-flight) | 4 (m196/m198/m199/m200) |

### 逐案例判定表（43 案例，按字母序）

| # | 案例名 | 原始 verdict | 最新状态 | 诊断修复后 | 备注 |
|---|--------|-------------|---------|-----------|------|
| 1 | acoustics | PASS | PASS | - | 1 轮通过 |
| 2 | aircraft_icing_naca0012_rime | honest-FAIL | PASS | m173 下探通过 | 原 6/11 判据 PASS，v2 下探 11/11 PASS |
| 3 | cavity | PASS | PASS | - | 1 轮通过 |
| 4 | cavity_3d_full | PASS | PASS | - | 1 轮通过 |
| 5 | cavity_natural_convection | FAILED | PASS | m182 诊断修复 | 原 agent 未调用 finish，诊断后通过 |
| 6 | couette_2d | PASS | PASS | - | 1 轮通过 |
| 7 | couette_3d | PASS | PASS | - | 1 轮通过 |
| 8 | cylinder | PASS | PASS | - | 1 轮通过 |
| 9 | cylinder_3d | FAILED | FAILED | m196 在途 | 多轮 agent 失败，m53 诊断 honest-FAIL |
| 10 | droplet_oscillation | PASS | PASS | - | 1 轮通过 |
| 11 | floating_body_free_decay | PASS | PASS | - | 1 轮通过 |
| 12 | kovasznay_2d | PASS | PASS | - | 1 轮通过 |
| 13 | laplace_cac_phasefield | PASS | PASS | - | 1 轮通过 |
| 14 | laplace_droplet | FAILED | PASS | m183 诊断修复 | 原 agent 未调用 finish，诊断后通过 |
| 15 | laplace_pr_eos | PASS | PASS | - | 1 轮通过 |
| 16 | maneuvering_steady_turn | PASS | PASS | - | 1 轮通过 |
| 17 | permeability | PASS | PASS | - | 1 轮通过 |
| 18 | permeability3d | PASS | PASS | - | 1 轮通过 |
| 19 | poiseuille_2d | FAILED | PASS | m184 诊断修复 | 原 agent 未调用 finish，诊断后通过 |
| 20 | poiseuille_3d_annulus | PASS | PASS | - | 1 轮通过 |
| 21 | poiseuille_3d_duct | PASS | PASS | - | 1 轮通过 |
| 22 | poiseuille_3d_pipe | FAILED | PASS | m74 诊断修复 | 原 agent 未调用 finish，诊断后 PASS |
| 23 | poiseuille_two_phase_cg | PASS | PASS | - | 1 轮通过 |
| 24 | shear_wave_decay | PASS | PASS | - | 1 轮通过 |
| 25 | sod_shock_tube | PASS | PASS | - | 1 轮通过 |
| 26 | sphere_re100 | FAILED | FAILED | m75 诊断仍 FAIL | 多轮 agent 失败，m75 诊断 Cd 误差超标 |
| 27 | sphere_re100_d3q27 | PASS | PASS | - | 1 轮通过 |
| 28 | sphere_re100_mem | honest-FAIL | honest-FAIL | m174 下探 | OOM 两档均 FAIL，GPU 基线四门全过 |
| 29 | sphere_re200 | PASS | PASS | - | 1 轮通过 |
| 30 | square_cylinder | PASS | PASS | - | 1 轮通过 |
| 31 | startup_poiseuille | PASS | PASS | - | 1 轮通过 |
| 32 | stokes_first_problem | PASS | PASS | - | 1 轮通过 |
| 33 | stokes_second_problem | PASS | PASS | - | 1 轮通过 |
| 34 | stokes_sphere_dg | PASS | PASS | - | 1 轮通过 |
| 35 | suboff_re1000 | FAILED | PASS | m194 诊断修复 | 连续 5 轮 agent 失败，第 6 轮诊断修复通过 |
| 36 | suboff_sail_resistance | honest-FAIL | honest-FAIL | m109 R3 | SDAA 内存分配器瓶颈 |
| 37 | taylor_couette | PASS | PASS | - | 1 轮通过 |
| 38 | taylor_green_2d | PASS | PASS | - | 1 轮通过 |
| 39 | taylor_green_3d | PASS | PASS | - | 1 轮通过 |
| 40 | thermal_cavity | PASS | PASS | - | 1 轮通过 |
| 41 | thermal_cavity_3d | PASS | PASS | - | 1 轮通过 |
| 42 | wigley_hydrostatics | PASS | PASS | - | 1 轮通过 |
| 43 | womersley | FAILED | FAILED | m110 诊断 honest-FAIL | H=29 α=8 C3 网格 complex_velocity 判据红 |

### 失败原因归类

| 失败类型 | 案例数 | 案例列表 | 根因 |
|---------|--------|---------|------|
| 环境/设备限制 | 2 | sphere_re100_mem, suboff_sail_resistance | OOM / SDAA 内存分配器瓶颈 |
| agent 流程失败 | 5 | cavity_natural_convection, laplace_droplet, poiseuille_2d, poiseuille_3d_pipe, suboff_re1000 | 未调用 finish / 工作树无改动（已修复） |
| 判据真红 | 2 | sphere_re100, womersley | Cd 误差超标 / complex_velocity 判据红 |
| 在途诊断 | 1 | cylinder_3d | m196 运行中 |

### 关键发现

1. **系统性 agent 流程问题**：8 例原始 FAILED 中，5 例已确认为 agent 流程问题（未调用 finish），非算法/物理失败。通过诊断+修复绕行策略（独立驱动脚本+中途落盘）已全部修复。
2. **honest-FAIL 案例**：2 例（sphere_re100_mem, suboff_sail_resistance）为设备/算法限制导致的真实失败，已如实记录。
3. **顽固 FAIL**：sphere_re100 和 womersley 经历多轮诊断仍未解决，可能需要算法层面修改或判据调整。

---

## 二、本班次派发清单

### 派发原则
- 硬上限：最多 3 单
- 只准派 TensorLBM 扫荡类 goal
- FAIL 案例 → 派「诊断+修复绕行」mission
- 队列将空（in-flight<4）→ 按案例字母序派下探案例

### 派发结果

| 派发序号 | Mission ID | 案例 | 类型 | 目标 |
|---------|-----------|------|------|------|
| 1 | 198 | sphere_re100 | 诊断+修复绕行 | 第5轮诊断，确认 agent 流程 vs 算法问题 |
| 2 | 199 | womersley | 诊断+修复绕行 | 第5轮诊断，τ绕行或分辨率提升尝试 |
| 3 | 200 | cylinder_3d | 诊断+修复绕行 | 第3轮诊断（m196 在途备用） |

### 派发时 in-flight 状态
- m196: cylinder_3d 诊断（running）
- m197: 本班次（running）
- 派发后 in-flight: 5（含本班次）

---

## 三、合并报告状态

- 最新合并报告：m193 发布（43 案例 38 PASS / 5 FAIL）
- 本班次后状态：38 PASS / 2 honest-FAIL / 3 FAILED（含在途）
- 是否需要新合并报告：否（m193 报告仍有效，本班次派发为诊断任务，不改变完工统计）

---

## 四、升级事项（留给 owner）

### 系统性问题

1. **Agent 流程稳定性**：多例「未调用 finish」和「工作树无改动」表明 agent 在长跑任务中存在流程中断风险。建议：
   - 强制要求所有扫荡任务使用独立驱动脚本+中途落盘策略
   - 增加 agent 心跳检测和自动恢复机制

2. **sphere_re100 顽固 FAIL**：经历 5 轮诊断仍未解决，m75 诊断确认 Cd 误差超标。可能需要：
   - 检查网格分辨率是否足够
   - 验证边界条件设置
   - 考虑是否需要调整判据阈值

3. **womersley 判据红**：H=29 α=8 C3 网格级别 complex_velocity 判据红。可能需要：
   - 尝试τ绕行策略
   - 提升分辨率
   - 检查入口边界条件

4. **SDAA 设备内存限制**：sphere_re100_mem 和 suboff_sail_resistance 均因内存问题 FAIL。建议：
   - 评估 SDAA 设备内存容量是否满足大案例需求
   - 考虑分块计算或内存优化策略

---

## 五、下一班建议

1. **跟踪 m198/m199/m200 完工结果**：确认 sphere_re100、womersley、cylinder_3d 的诊断结果
2. **如 m198/m199 仍 FAIL**：考虑升级至算法层面修复或判据调整
3. **持续监控 agent 流程稳定性**：确保新派发任务使用独立驱动策略
4. **考虑发布更新版合并报告**：如 m198/m199/m200 中有案例转为 PASS

---

*报告生成时间：2026-10-29*  
*数据来源：API 程序化拉取，零手抄*
