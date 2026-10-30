# 值班长班次 m428 分析报告

**班次号**: m428 | **时间**: 2026-10-30 | **分支**: agent/m428
**前置报告**: #342 (m417 合并报告, 51 案例 206 单)

---

## 1. 扫荡线完工分析

### 1.1 总体统计

| 指标 | 数值 |
|------|------|
| 全平台 mission 总数 | 429 单 |
| SDAA 扫荡线 mission 总数 | 217 单 |
| 扫荡线完工 | 214 单 |
| 扫荡线 in-flight | 3 单 |
| 唯一案例数 | 53 个 |
| PASS 案例 | 52 个 |
| FAIL 案例 | 1 个 |
| PASS 率 | 98.1% |

### 1.2 失败原因归类

| 类别 | 数量 | 案例 |
|------|------|------|
| 判据真红 | 0 | 无 |
| 环境/超时/未完成 | 1 | lid_driven_cavity_3d |
| triton 绕行 | 0 | 无 |
| 其他 | 0 | 无 |

### 1.3 FAIL 案例详情

| 案例 | 最新 Mission ID | 判定 | 失败原因摘要 |
|------|----------------|------|-------------|
| lid_driven_cavity_3d | 426 | FAIL | 模型未调用 finish（用完 80 次 tool calls，evidence.checks.gate="no-evidence"）— INCOMPLETE-FAIL（非精度问题，agent 执行能力不足） |

### 1.4 案例级判据表（前 20 例）

| 案例 | 总单数 | PASS | FAIL | 最新判定 |
|------|--------|------|------|---------|
| acoustics | 4 | 4 | 0 | PASS |
| aircraft_icing_naca0012_rime | 4 | 3 | 1 | PASS |
| aircraft_icing_rg15_ipw2 | 2 | 2 | 0 | PASS |
| backward_step | 1 | 1 | 0 | PASS |
| blasius_flat_plate | 2 | 2 | 0 | PASS |
| bstep_3d | 2 | 2 | 0 | PASS |
| capillary_invasion_washburn | 1 | 1 | 0 | PASS |
| cavity | 5 | 3 | 2 | PASS |
| cavity_3d_full | 3 | 3 | 0 | PASS |
| cavity_natural_convection | 8 | 4 | 4 | PASS |
| channel_turb_3d | 1 | 1 | 0 | PASS |
| collision_kernels_3d_tg | 1 | 1 | 0 | PASS |
| couette_2d | 3 | 3 | 0 | PASS |
| couette_3d | 3 | 2 | 1 | PASS |
| cylinder | 4 | 4 | 0 | PASS |
| cylinder_3d | 7 | 5 | 2 | PASS |
| droplet_oscillation | 4 | 3 | 1 | PASS |
| floating_body_free_decay | 4 | 3 | 1 | PASS |
| kovasznay_2d | 3 | 3 | 0 | PASS |
| laplace_cac_phasefield | 4 | 3 | 1 | PASS |

（完整 53 案例见 API 原始数据；注：噪声案例名 "R"/"README" 已过滤）

---

## 2. 黑名单检查

- `/srv/tensor/agent/guide_blacklist.json`: **不存在**（无黑名单限制）

---

## 3. 本班次派发清单

| 序号 | Mission ID | 类型 | 案例 | 目标 | 状态 |
|------|-----------|------|------|------|------|
| 1 | 429 | 诊断+修复绕行 | lid_driven_cavity_3d | 诊断 agent 执行能力不足问题，简化验证流程 | queued |

**派发依据**:
- lid_driven_cavity_3d (ID=426) 失败原因：模型未调用 finish（非精度问题，是 agent 执行能力不足）
- 诊断方向：简化验证流程、分段执行、检查 SDAA 设备可用性

**未派发下探单原因**: in-flight 已达 4 单（m425/m427/m428/m429），不满足 in-flight<4 条件

---

## 4. 在飞任务状态

| Mission ID | 案例 | 类型 | 状态 |
|-----------|------|------|------|
| 425 | backward_step | 诊断+修复绕行 | running |
| 427 | taylor_green_3d_decay | 下探 | running |
| 428 | m428 值班长 | 分析调度 | running |
| 429 | lid_driven_cavity_3d | 诊断+修复绕行 | queued |

---

## 5. 合并报告状态

- 最新合并报告: #342 (m417 班次, 51 案例 206 单)
- 自 #342 以来新增完工扫荡单: 1 单 (lid_driven_cavity_3d FAIL)
- **暂不发布新合并报告**（新增完工 < 10 单阈值）

---

## 6. 升级事项（留给 owner）

1. **lid_driven_cavity_3d 执行能力问题**: 该案例在 m424 班次下探时，agent 用完 80 次 tool calls 仍未完成。建议：
   - 检查该 benchmark 脚本复杂度，是否需要拆分验证步骤
   - 考虑增加 tool call 预算或优化 agent 执行策略
   - 若为脚本问题，建议 owner 审查 benchmarks/pending/lid_driven_cavity_3d/ 目录

2. **扫荡线覆盖率**: 当前 53 个有效案例中 52 PASS (98.1%)，仅 1 FAIL（非精度问题）。扫荡线整体健康。

---

## 7. 下一班建议

1. 关注 m429 (lid_driven_cavity_3d 诊断) 结果，若仍失败则考虑增加 tool call 预算
2. 关注 m427 (taylor_green_3d_decay) 结果
3. 若 in-flight < 4，可按字母序下探新案例（建议检查 TensorLBM benchmarks/pending/ 目录中未覆盖案例）
4. 自 #342 以来新增完工仅 1 单，暂不需发布新合并报告；待累计 ≥10 单时触发
