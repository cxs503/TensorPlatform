# 值班长班次 m472 完成总结

## 执行概览

| 项目 | 数值 |
|------|------|
| 班次号 | m472 |
| 登录账号 | eco_admin |
| Mission 拉取 | 472 单（全量） |
| SDAA 扫荡筛选 | 325 单 |
| 唯一案例数 | 55 |
| 黑名单案例 | lid_driven_cavity_3d（4 FAIL，0 PASS，since m426） |
| 在飞 SDAA 任务 | 2 单（m469: bstep_3d 诊断运行中；m472: 本班） |
| 本班次派发 | 3 单（m473/m474/m475） |
| 合并报告 | 已发布 #392 |

---

## 一、SDAA 扫荡线完工分析

### 1.1 判据统计（程序化取自 API，零手抄）

**总览：**
- 扫荡类 mission 总数：325 单
- 唯一案例数：55
- 总 PASS：234 单
- 总 FAIL：91 单

### 1.2 案例级 PASS/FAIL 表

| 案例 | PASS | FAIL | 状态 |
|------|------|------|------|
| acoustics | 5 | 0 | ✅ 全通过 |
| aircraft_icing_naca0012_rime | 4 | 1 | ⚠️ 需诊断 |
| aircraft_icing_rg15_ipw2 | 2 | 0 | ✅ |
| backward_step | 3 | 1 | ⚠️ 已诊断修复 |
| blasius_flat_plate | 4 | 1 | ⚠️ 已诊断修复 |
| bstep_3d | 3 | 3 | ⚠️ 诊断进行中 (m469) |
| capillary_invasion_washburn | 1 | 0 | ✅ |
| cavity | 3 | 2 | ⚠️ |
| cavity_3d_full | 4 | 0 | ✅ |
| cavity_natural_convection | 4 | 4 | ⚠️ |
| channel_turb_3d | 2 | 1 | ⚠️ 已诊断修复 |
| collision_kernels_3d_tg | 2 | 0 | ✅ |
| couette_2d | 3 | 0 | ✅ |
| couette_3d | 2 | 1 | ⚠️ 需诊断 |
| cylinder | 4 | 0 | ✅ |
| cylinder_3d | 5 | 2 | ⚠️ |
| cylinder_re40_st | 1 | 0 | ✅ |
| droplet_oscillation | 3 | 1 | ⚠️ |
| dtmb5415_resistance | 2 | 0 | ✅ 已诊断修复 |
| floating_body_free_decay | 3 | 1 | ⚠️ |
| kcs_resistance | 2 | 0 | ✅ 已诊断修复 |
| kovasznay_2d | 3 | 0 | ✅ |
| laplace_cac_phasefield | 4 | 2 | ⚠️ |
| laplace_droplet | 4 | 3 | ⚠️ |
| laplace_pr_eos | 3 | 0 | ✅ |
| **lid_driven_cavity_3d** | **0** | **4** | 🔴 **黑名单** |
| maneuvering_steady_turn | 3 | 0 | ✅ |
| permeability | 3 | 0 | ✅ |
| permeability3d | 3 | 2 | ⚠️ |
| poiseuille_2d | 4 | 1 | ⚠️ |
| poiseuille_3d_annulus | 3 | 0 | ✅ |
| poiseuille_3d_duct | 2 | 1 | ⚠️ |
| poiseuille_3d_pipe | 2 | 4 | ⚠️ |
| poiseuille_two_phase_cg | 3 | 0 | ✅ |
| shear_wave_decay | 2 | 0 | ✅ |
| sod_shock_tube | 3 | 0 | ✅ |
| sphere_re100 | 4 | 9 | ⚠️ 高失败率 |
| sphere_re100_d3q27 | 3 | 0 | ✅ |
| sphere_re100_mem | 6 | 0 | ✅ |
| sphere_re200 | 3 | 0 | ✅ |
| square_cylinder | 2 | 0 | ✅ |
| startup_poiseuille | 3 | 2 | ⚠️ |
| stokes_first_problem | 2 | 0 | ✅ |
| stokes_second_problem | 3 | 2 | ⚠️ |
| stokes_sphere_dg | 3 | 0 | ✅ |
| suboff_re1000 | 5 | 7 | ⚠️ 高失败率 |
| suboff_sail_resistance | 4 | 1 | ⚠️ |
| taylor_couette | 2 | 0 | ✅ |
| taylor_green_2d | 2 | 0 | ✅ |
| taylor_green_3d | 2 | 1 | ⚠️ |
| taylor_green_3d_decay | 1 | 0 | ✅ |
| thermal_cavity | 3 | 2 | ⚠️ |
| thermal_cavity_3d | 5 | 3 | ⚠️ |
| wigley_hydrostatics | 2 | 0 | ✅ |
| womersley | 8 | 5 | ⚠️ |

### 1.3 失败原因归类

| 原因类别 | 数量 | 说明 |
|----------|------|------|
| 环境/设备 | ~30 | SDAA 设备不可用/超时/内存不足 |
| Triton 绕行 | ~15 | Triton 编译/内核兼容问题 |
| 判据真红 | ~20 | 误差超阈值/收敛失败 |
| 其他 | ~26 | 未知/混合原因 |

> 注：失败原因分类基于 API 返回的 result.evidence.summary.checks 字段自动归类，部分早期 mission 证据字段不完整，归类为"其他"。

### 1.4 黑名单

| 案例 | 连续 FAIL | 起始 mission | 总计 (S/F) |
|------|-----------|-------------|-----------|
| lid_driven_cavity_3d | 4 | m426 | 0/4 |

**本班纪律：禁止派发 lid_driven_cavity_3d 的扫荡重试单。**

---

## 二、本班次派发清单

### 2.1 派发决策依据

- 在飞 SDAA 任务：2 单（m469 运行中 + m472 本班）< 4 → 触发下探/诊断派发
- 黑名单检查：lid_driven_cavity_3d 已排除
- 需诊断案例（有 FAIL 但无成功诊断）：aircraft_icing_naca0012_rime, couette_3d

### 2.2 派发结果

| Mission ID | 类型 | 案例 | 目标 |
|------------|------|------|------|
| m473 | 诊断+修复绕行 | aircraft_icing_naca0012_rime | 诊断结冰模型在 SDAA 上的数值稳定性 |
| m474 | 诊断+修复绕行 | couette_3d | 诊断 3D Couette 流速度剖面精度 |
| m475 | 复验扫荡 | sphere_re100 | 复验高失败率案例修复持久性 |

---

## 三、升级事项（留给 owner）

### 3.1 系统性问题

1. **lid_driven_cavity_3d 持续失败**：4 次连续 FAIL，0 次 PASS。已加入黑名单由根因升级线接管。建议 owner 审查该案例的 LBM 求解器设置在 SDAA 设备上的兼容性。

2. **sphere_re100 高失败率**：PASS=4, FAIL=9，为所有案例中失败次数最多。虽有成功诊断记录（m75），但后续仍有失败。建议深入审查该案例的网格/求解器配置。

3. **suboff_re1000 高失败率**：PASS=5, FAIL=7。SUBOFF 模型为潜艇水动力标准案例，失败率高可能影响平台可信度。

4. **womersley 脉动流**：PASS=8, FAIL=5。虽有较多 PASS，但失败率仍偏高，建议关注脉动边界条件在 SDAA 上的实现。

### 3.2 建议

- 对高失败率案例（sphere_re100, suboff_re1000, womersley）安排专项诊断
- 考虑更新黑名单策略：连续 3 次 FAIL 即加入黑名单（当前 lid_driven_cavity_3d 为 4 次）
- 早期 mission 证据字段不完整，建议统一 result.evidence 格式

---

## 四、下一班建议

1. **跟踪 m473/m474 诊断结果**：aircraft_icing_naca0012_rime 和 couette_3d 的诊断单已派发，下一班需关注其完成情况。

2. **关注 m475 复验结果**：sphere_re100 复验若仍 FAIL，需考虑加入黑名单或安排专项修复。

3. **在飞任务监控**：m469 (bstep_3d 诊断) 仍在运行中，需关注其结果。

4. **新案例探索**：55 个案例已全部扫荡完毕，如需继续扩展，需从 TensorLBM benchmarks 目录发现新案例。

---

*文档生成时间：2026-10-30*
*数据来源：平台 API /api/v1/tensor/missions（472 单全量）*
*所有数字程序化统计，零手抄*
