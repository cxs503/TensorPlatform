# 值班长班次 m313 分析报告

## 班次信息
- **班次号**: m313
- **时间**: 2026-10-30
- **职责**: 扫荡线（SDAA 验证案例扫荡）分析与调度

---

## 一、扫荡线完工结果统计

### 1.1 原始扫荡单统计
| 状态 | 数量 |
|------|------|
| PASSED | 9 |
| FAILED | 4 |
| **合计** | **13** |

### 1.2 诊断+修复绕行单统计
| 状态 | 数量 |
|------|------|
| PASSED | 57 |
| FAILED | 34 |
| **合计** | **91** |

### 1.3 失败原因归类（原始扫荡单）
| 失败原因 | 数量 | 案例 |
|----------|------|------|
| 环境: 模型未调用 finish | 4 | sphere_re100 (x2), suboff_re1000, cavity |

**说明**: 所有4个原始扫荡单失败原因均为环境问题（模型未调用 finish 工具），非业务逻辑失败。

---

## 二、案例完成情况

### 2.1 已完成案例（30个）
acoustics, aircraft_icing_naca0012_rime, cavity, cavity_3d_full, cavity_natural_convection, couette_2d, couette_3d, cylinder, cylinder_3d, droplet_oscillation, floating_body_free_decay, kovasznay_2d, laplace_cac_phasefield, laplace_droplet, laplace_pr_eos, poiseuille_2d, poiseuille_3d_annulus, poiseuille_3d_duct, poiseuille_3d_pipe, poiseuille_two_phase_cg, sphere_re100, sphere_re100_mem, sphere_re200, stokes_second_problem, suboff_re1000, suboff_sail_resistance, taylor_green_3d, thermal_cavity, thermal_cavity_3d, womersley

### 2.2 未完成案例（13个，按字母序）
maneuvering_steady_turn, permeability, permeability3d, shear_wave_decay, sod_shock_tube, sphere_re100_d3q27, square_cylinder, startup_poiseuille, stokes_first_problem, stokes_sphere_dg, taylor_couette, taylor_green_2d, wigley_hydrostatics

---

## 三、队列状态

| 状态 | 数量 |
|------|------|
| PENDING | 0 |
| RUNNING | 2 |
| CLAIMED | 0 |
| **in-flight** | **2** |

**判断**: in-flight < 4，队列将空，需要派发新任务。

---

## 四、本班次派发清单

### 4.1 派发任务（3单）

| Mission ID | 案例 | 类型 | 状态 |
|------------|------|------|------|
| 314 | maneuvering_steady_turn | 扫荡下探 | queued |
| 315 | permeability | 扫荡下探 | queued |
| 316 | permeability3d | 扫荡下探 | queued |

### 4.2 派发理由
- 队列 in-flight = 2 < 4，需要补充任务
- 按未处理案例字母序依次派发
- 本班次硬上限：最多3单

### 4.3 未派发诊断修复的原因
- 4个原始扫荡单失败原因均为"环境: 模型未调用 finish"
- 属于环境问题，非业务逻辑失败
- 建议重试而非诊断修复

---

## 五、合并报告状态

**已有合并报告**: 13份
- 最新: ID=220 【SDAA 扫荡】值班长 m309 班次合并报告 — 22 案例审计（PASS 17 / FAIL 5）+ 3 单诊断派

**本班次是否需要发布**: 否（已有合并报告）

---

## 六、升级事项

### 6.1 系统性问题
1. **环境问题频发**: 多起 mission 因"模型未调用 finish"失败，建议检查 agent 工具调用逻辑
2. **部分案例反复失败**: sphere_re100 多次失败后仍未完全解决，建议深入排查

### 6.2 建议
1. 继续按字母序派发未完成案例
2. 关注新派发任务执行情况
3. 考虑对环境问题进行系统性排查

---

## 七、下一班建议

1. **优先处理**: 继续派发未完成案例（shear_wave_decay, sod_shock_tube 等）
2. **关注队列**: in-flight 降至 4 以下时及时补充
3. **失败案例**: 4个环境失败案例建议重试扫荡

---

## 八、交付物

- 本文档: docs/operator_shift_m313.md
- 派发任务: 3单 (ID: 314, 315, 316)