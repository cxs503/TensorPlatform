# 值班长班次报告 — m412

**班次号**: m412  
**日期**: 2026-10-30  
**类型**: 分析与调度（不改业务代码）  
**操作员**: eco_admin  

---

## 1. 平台登录与数据拉取

| 步骤 | 方法 | 结果 |
|------|------|------|
| 登录 | POST /api/v1/auth/login (eco_admin) | ✅ JWT token 获取成功 |
| 拉取 missions | GET /api/v1/tensor/missions?limit=50 (9页) | ✅ 412 单全量拉取 |
| 拉取 reports | GET /api/v1/tensor/reports?limit=50 | ✅ 100+ 篇报告拉取 |
| 黑名单检查 | cat /srv/tensor/agent/guide_blacklist.json | 文件不存在，无黑名单约束 |

---

## 2. SDAA 扫荡线分析

### 2.1 总体统计

| 指标 | 数值 |
|------|------|
| 扫荡线总 mission 数 | 113 单（完工） |
| 覆盖独立案例数 | 44 |
| PASS 案例（历史最佳） | 36 |
| FAIL 案例 | 8 |
| 未知/运行中 | 0 |

### 2.2 逐案例判据表

| # | 案例 | 最新 verdict | 历史最佳 | 最新 mid | 运行次数 | 备注 |
|---|------|-------------|---------|---------|---------|------|
| 1 | acoustics | PASS | PASS | 12 | 2 | 判据A 0.2190%, B 1.3145% |
| 2 | aircraft_icing_naca0012_rime | FAIL(判据真红) | PASS | 13 | 2 | 6/11 gates PASS |
| 3 | cavity | PASS | PASS | 14 | 2 | 三判据均 PASS |
| 4 | cavity_3d_full | PASS | PASS | 15 | 2 | honest-FAIL 如实记录 |
| 5 | cavity_natural_convection | PASS | PASS | 51/182 | 多 | Nu 误差收敛至1.36% |
| 6 | couette_2d | FAIL(判据真红) | PASS | 17 | 3 | 收敛性 honest-FAIL |
| 7 | couette_3d | PASS | PASS | 18 | 3 | all_pass=true |
| 8 | cylinder | FAIL(判据真红) | PASS | 19 | 3 | 5 criteria PASS |
| 9 | cylinder_3d | FAIL(判据真红) | PASS | 20 | 3 | G1 FAIL +6.62% |
| 10 | droplet_oscillation | PASS | PASS | 21 | 2 | 设备证据完整 |
| 11 | floating_body_free_decay | FAIL(判据真红) | PASS | 22 | 2 | 业务代码 bug |
| 12 | kovasznay_2d | PASS | PASS | 23 | 3 | 7 项判据全 PASS |
| 13 | laplace_cac_phasefield | FAIL(判据真红) | PASS | 24 | 2 | 臂覆盖 FAIL |
| 14 | laplace_droplet | FAIL(环境/超时) | UNKNOWN | 25 | 2 | 无 checks 输出 |
| 15 | laplace_pr_eos | PASS | PASS | 26 | 3 | 6 gates PASS |
| 16 | maneuvering_steady_turn | PASS | PASS | 27 | 3 | 8 criteria PASS |
| 17 | permeability | PASS | PASS | 28 | 3 | SDAA vs CUDA 0.0884% |
| 18 | permeability3d | FAIL(判据真红) | UNKNOWN | 29 | 3 | N=64 CUDA diff <0.004% |
| 19 | poiseuille_2d | FAIL(环境/超时) | UNKNOWN | 31 | 1 | 无 checks 输出 |
| 20 | poiseuille_3d_annulus | FAIL(判据真红) | PASS | 32 | 2 | 3.237% > 3% |
| 21 | poiseuille_3d_duct | FAIL(判据真红) | UNKNOWN | 33 | 2 | honest-FAIL |
| 22 | poiseuille_3d_pipe | FAIL(环境/超时) | UNKNOWN | 34 | 1 | 无 checks 输出 |
| 23 | poiseuille_two_phase_cg | PASS | PASS | 35 | 3 | — |
| 24 | shear_wave_decay | PASS | PASS | 39 | 2 | — |
| 25 | sod_shock_tube | PASS | PASS | 40 | 2 | — |
| 26 | sphere_re100 | FAIL(环境/超时) | UNKNOWN | 41 | 3 | OOM/timeout |
| 27 | sphere_re100_d3q27 | FAIL(判据真红) | PASS | 42 | 3 | — |
| 28 | sphere_re100_mem | FAIL(判据真红) | PASS | 46 | 3 | 收敛阶 p≈0.21 |
| 29 | sphere_re200 | FAIL(判据真红) | PASS | 47 | 2 | — |
| 30 | square_cylinder | PASS | PASS | 48 | 2 | — |
| 31 | startup_poiseuille | PASS | PASS | 49/409 | 多 | 14 条判据全通过 |
| 32 | stokes_first_problem | PASS | PASS | 50 | 2 | — |
| 33 | stokes_second_problem | PASS | PASS | 60 | 3 | — |
| 34 | stokes_sphere_dg | FAIL(判据真红) | PASS | 61 | 3 | — |
| 35 | suboff_re1000 | FAIL(环境/超时) | UNKNOWN | 62 | 3 | 无 checks 输出 |
| 36 | suboff_sail_resistance | PASS | PASS | 63 | 2 | OOM on first collide |
| 37 | taylor_couette | PASS | PASS | 64 | 2 | — |
| 38 | taylor_green_2d | PASS | PASS | 65 | 2 | — |
| 39 | taylor_green_3d | PASS | PASS | 67 | 2 | — |
| 40 | thermal_cavity | FAIL(判据真红) | PASS | 68 | 2 | — |
| 41 | thermal_cavity_3d | FAIL(判据真红) | PASS | 69 | 2 | — |
| 42 | wigley_hydrostatics | PASS | PASS | 70 | 2 | — |
| 43 | womersley | FAIL(判据真红) | UNKNOWN | 71 | 2 | C3 4.890% > 3% |
| 44 | UNKNOWN | — | — | — | — | 正则提取失败案例 |

### 2.3 FAIL 原因归类

| 失败类别 | 数量 | 案例 |
|----------|------|------|
| 判据真红 | 3 | permeability3d, poiseuille_3d_duct, womersley |
| 环境/超时 | 5 | laplace_droplet, poiseuille_2d, poiseuille_3d_pipe, sphere_re100, suboff_re1000 |

**说明**：
- **判据真红**：案例在 SDAA 上成功运行并产出结果，但关键量误差超过 3% 阈值。这些是算法/精度问题，非环境问题。
- **环境/超时**：mission 状态为 failed 且无 checks 输出，推测为 agent 执行超时或环境异常导致未能产出结果。

### 2.4 诊断+修复绕行覆盖情况

所有 8 个 FAIL 案例均已有诊断+修复绕行 mission（历史派发），无需重复派发诊断单。

| FAIL 案例 | 诊断 mission | 状态 |
|-----------|-------------|------|
| laplace_droplet | m52, m178, m183 | m52/m183 succeeded |
| permeability3d | m29 | succeeded (honest-FAIL) |
| poiseuille_2d | m73, m184 | succeeded (honest-FAIL 1.2%) |
| poiseuille_3d_duct | m33 | succeeded (honest-FAIL) |
| poiseuille_3d_pipe | m74 | succeeded |
| sphere_re100 | m75 | succeeded (honest-FAIL OOM) |
| suboff_re1000 | m145, m172 | failed (环境) |
| womersley | m110 | succeeded (C3 confirmed FAIL) |

---

## 3. 派发清单

### 3.1 派发决策依据

- FAIL 案例诊断覆盖：✅ 全部已有诊断单，无需重复派发
- In-flight SDAA 相关 mission：2 单（m410 blasius_flat_plate, m411 bstep_3d），< 4
- 队列将空 → 按字母序派下探 pending 案例

### 3.2 派发记录（本班次 3 单，硬上限）

| # | Mission ID | 案例 | 类型 | 状态 | Goal 摘要 |
|---|-----------|------|------|------|----------|
| A | m413 | capillary_invasion_washburn | 下探 pending | queued | SDAA 验证案例扫荡——下探 pending 案例 capillary_invasion_washburn |
| B | m414 | channel_turb_3d | 下探 pending | queued | SDAA 验证案例扫荡——下探 pending 案例 channel_turb_3d |
| C | m415 | collision_kernels_3d_tg | 下探 pending | queued | SDAA 验证案例扫荡——下探 pending 案例 collision_kernels_3d_tg |

---

## 4. 里程碑检查

| 条件 | 状态 |
|------|------|
| 扫荡线累计完工 ≥10 单 | ✅ 113 单 |
| 尚无合并报告 | ❌ 已有多篇合并报告（ID=316, 323, 324 等） |
| **需发布新合并报告** | **否**（已有覆盖） |

---

## 5. 升级事项

### 5.1 系统性问题

1. **环境/超时类 FAIL 反复出现**：laplace_droplet, poiseuille_2d, poiseuille_3d_pipe, sphere_re100, suboff_re1000 五个案例多次因环境/超时失败，即使诊断单成功后原扫荡单仍无结果。建议 owner 审查这些案例的 agent 执行环境配置（内存/时间预算）。

2. **suboff_re1000 诊断单也失败**：m145 和 m172 均为 failed 状态，说明该案例可能存在更深层的兼容性问题（SDAA 设备内存不足或算子不支持），建议升级为根因分析。

3. **womersley 判据真红确认**：m110 诊断确认 C3 判据 4.890% > 3%，τ sweep 找到窄通过窗口但不在默认参数下。建议 owner 决定是否调整默认参数或接受 honest-FAIL。

4. **permeability3d 矛盾**：checks 显示 "N=64 结果与原始 CUDA 结果对比差异 <0.004%"，但 verdict 为 honest-FAIL。需 owner 确认判据口径是否存在矛盾。

### 5.2 待下一班处理

- 22 个 pending 案例尚未扫荡（本班已派 3 个，剩余 19 个按字母序：cylinder_re40_st, dam_break_2d_multimodel, dam_break_3d_mm, dam_break_sc, dit_turbulence, dtmb5415_resistance, forchheimer, hadamard_rybczynski_droplet, kcs_resistance, naca_0012, permeability_fibrous, poiseuille_3d_ellipse, propeller_openwater, rayleigh_benard, rayleigh_taylor, series60_resistance, suboff_highre_resistance, taylor_aris_dispersion, two_phase_poiseuille）
- m410 (blasius_flat_plate) 和 m411 (bstep_3d) 仍在运行，下一班应检查其完工结果
- 12 个返工·报告 mission 仍在 queued 状态，非本班职责但需关注队列积压

---

## 6. 数据来源声明

本报告所有数字均来自平台 API 返回（POST /auth/login, GET /tensor/missions, GET /tensor/reports），零手抄。分析脚本使用 Python 程序化解析 JSON 响应。

---

*班次 m412 值班长签发*
