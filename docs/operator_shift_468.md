# 值班长班次报告 — m468

**班次号**: 468
**角色**: 值班长（分析与调度，不改业务代码）
**时间**: 2026-10-30

---

## 一、扫荡线完工分析

### 数据来源
- API: `GET /api/v1/tensor/missions?limit=50`
- 过滤条件: goal 含「SDAA」且含「扫荡」且不含「值班长」
- 有效扫荡单: **27 单**（26 完工 + 1 运行中）

### 完工统计

| 指标 | 数值 |
|------|------|
| 总完工单 | 26 |
| PASS | 16 |
| FAIL | 10 |
| 运行中 | 1 (cavity_3d_full, ID=465) |
| 覆盖唯一案例 | 14 |

### PASS 案例清单（16 单）

| Mission ID | 案例 | 备注 |
|------------|------|------|
| 467 | blasius_flat_plate | 诊断绕行，C_f 误差 +35% (HONEST-FAIL) |
| 462 | bstep_3d | 诊断绕行成功 |
| 460 | kcs_resistance | SDAA 设备验证通过 |
| 459 | backward_step | 诊断绕行·第2轮，checkpoint/resume 验证 |
| 458 | channel_turb_3d | 诊断绕行·第2轮 |
| 454 | dtmb5415_resistance | 诊断绕行 |
| 449 | blasius_flat_plate | 诊断绕行 |
| 448 | thermal_cavity_3d | 诊断绕行·第2轮，Ra1e3/1e4/1e5 均 ≤3% |
| 444 | backward_step | 诊断绕行 |
| 443 | collision_kernels_3d_tg | PR #312 已合入 |
| 439 | kcs_resistance | 下探未覆盖 |
| 438 | dtmb5415_resistance | 下探未覆盖 |
| 437 | cylinder_re40_st | W11-A2 四网格 <3% |
| 433 | sod_shock_tube | 网格收敛通过 |
| 432 | laplace_droplet | 三半径收敛 <3% |
| 427 | taylor_green_3d_decay | R²≥0.999995 |

### FAIL 案例清单（10 单）与根因归类

| Mission ID | 案例 | 失败原因 | 归类 |
|------------|------|----------|------|
| 466 | bstep_3d | 无代码改动 | 执行流程 |
| 455 | channel_turb_3d | 无代码改动 | 执行流程 |
| 453 | blasius_flat_plate | 无代码改动 | 执行流程 |
| 450 | bstep_3d | 模型未调用 finish | 执行流程 |
| 445 | thermal_cavity_3d | triton 绕行 | 环境 |
| 441 | lid_driven_cavity_3d | 无代码改动 | 执行流程 |
| 431 | lid_driven_cavity_3d | agent_lost | 其他 |
| 429 | lid_driven_cavity_3d | 无代码改动 | 执行流程 |
| 426 | lid_driven_cavity_3d | 模型未调用 finish | 执行流程 |
| 425 | backward_step | 无代码改动 | 执行流程 |

### 失败原因分布

| 原因 | 次数 | 占比 |
|------|------|------|
| 无代码改动 | 6 | 60% |
| 模型未调用 finish | 2 | 20% |
| triton 绕行 | 1 | 10% |
| agent_lost | 1 | 10% |

### 黑名单检查
- `guide_blacklist.json` 存在，含 `lid_driven_cavity_3d`（4 次连续失败，since m426）
- **本班禁止派发 lid_driven_cavity_3d 扫荡重试单** ✓ 已遵守

---

## 二、本班次派发清单（3 单，硬上限）

| 新 Mission ID | 类型 | 案例 | 说明 |
|---------------|------|------|------|
| 469 | 诊断+修复绕行 | bstep_3d | 最新 FAIL（ID=466），reason=无代码改动 |
| 470 | 下探未覆盖 | acoustics | 字母序第 1 未探索案例（benchmarks/verified/） |
| 471 | 下探未覆盖 | aircraft_icing_naca0012_rime | 字母序第 2 未探索案例（benchmarks/verified/） |

**派发约束检查**:
- ✅ 仅派 TensorLBM 扫荡类 goal
- ✅ runner=native
- ✅ 未派黑名单案例
- ✅ 未超 3 单上限
- ✅ 未改审批、未碰 GitHub

---

## 三、合并报告

- **已发布**: Report ID=388
- **标题**: 「SDAA 扫荡线合并报告——TensorLBM 验证案例系统性评估（班次 m468）」
- **触发条件**: 扫荡线累计完工 ≥10 单（实际 26 单）✓
- **内容**: 逐案例判据表 + FAIL 如实 + 设备证据汇总 + 局限 + 升级事项

---

## 四、下一班建议

1. **跟踪 bstep_3d 重试结果**（ID=469）：若再次 FAIL，考虑加入黑名单或升级
2. **关注 cavity_3d_full**（ID=465）：当前运行中，完工后需判定
3. **关注新探索案例**（ID=470 acoustics, ID=471 aircraft_icing_naca0012_rime）：首次运行，可能需多轮调试
4. **未探索案例池**（按字母序）:
   - verified: acoustics✓, aircraft_icing_naca0012_rime✓, cavity_natural_convection, couette_2d, couette_3d, cylinder_3d, droplet_oscillation, floating_body_free_decay, kovasznay_2d, laplace_cac_phasefield, laplace_pr_eos, maneuvering_steady_turn, poiseuille_2d, poiseuille_3d_duct, poiseuille_3d_pipe, poiseuille_two_phase_cg, shear_wave_decay, sphere_re100_d3q27, square_cylinder, startup_poiseuille, stokes_first_problem, stokes_sphere_dg, suboff_re1000, suboff_sail_resistance, taylor_couette, taylor_green_2d, wigley_hydrostatics, womersley
   - pending: aircraft_icing_rg15_ipw2, capillary_invasion_washburn, dam_break_2d_multimodel, dam_break_3d_mm, dam_break_sc, dit_turbulence, forchheimer, hadamard_rybczynski_droplet, naca_0012, permeability3d, permeability_fibrous, poiseuille_3d_annulus, poiseuille_3d_ellipse, propeller_openwater, rayleigh_benard, rayleigh_taylor, series60_resistance, stokes_second_problem, suboff_highre_resistance, taylor_aris_dispersion, two_phase_poiseuille
5. **升级事项**（留给 owner）:
   - `lid_driven_cavity_3d` 根因未明，4 次连续失败，需专门升级线处理
   - Agent "无代码改动" 失败占比 60%，建议优化 agent prompt 或增加 pre-check
   - `blasius_flat_plate` C_f 误差 +35% 超限，需算法层面修复

---

## 五、纪律声明

- ✅ 所有数字来自 API 返回，零手抄
- ✅ 未伪造任何判据结果
- ✅ 未改判据口径
- ✅ 黑名单案例未派发
- ✅ 仅派 TensorLBM 扫荡类 goal，未超 3 单上限
- ✅ 合并报告已发布（ID=388）
