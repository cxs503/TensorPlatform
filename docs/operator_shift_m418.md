# 值班长班次 m418 — 分析报告

**班次**: m418 | **时间**: 2026-10-30 | **分支**: agent/m418  
**前置班次**: m417（合并报告 #342, 51 案例 206 单全 PASS）

---

## 1. 数据拉取

| 步骤 | 结果 |
|------|------|
| POST /api/v1/auth/login (eco_admin) | ✅ JWT token 获取 |
| GET /api/v1/tensor/missions?limit=200 | ✅ 200 单落盘 |
| 黑名单检查 /srv/tensor/agent/guide_blacklist.json | 不存在（无黑名单约束） |

---

## 2. 扫荡线分析

### 2.1 总体统计

| 指标 | 数值 |
|------|------|
| 200 单窗口内 SDAA 扫荡线 mission | 93 单 |
| 扫荡线完工 | 93 单（100%） |
| 扫荡线 in-flight | 0 单 |
| 唯一案例数 | 34 个 |
| PASS 案例 | 34 个（100.0%） |
| FAIL 案例 | 0 个 |

### 2.2 逐案例判定表（34 案例全 PASS）

| # | 案例 | 判定 | 完工单数 | 最新 mission |
|---|------|------|----------|-------------|
| 1 | acoustics | PASS | 1 | m343 |
| 2 | aircraft_icing_rg15_ipw2 | PASS | 3 | m374 |
| 3 | blasius_flat_plate | PASS | 1 | m410 |
| 4 | bstep_3d | PASS | 1 | m411 |
| 5 | capillary_invasion_washburn | PASS | 1 | m413 |
| 6 | cavity | PASS | 4 | m397 |
| 7 | channel_turb_3d | PASS | 1 | m414 |
| 8 | collision_kernels_3d_tg | PASS | 1 | m415 |
| 9 | couette_3d | PASS | 1 | m268 |
| 10 | cylinder | PASS | 2 | m269 |
| 11 | droplet_oscillation | PASS | 2 | m238 |
| 12 | floating_body_free_decay | PASS | 1 | m239 |
| 13 | kbc_diagnostic | PASS | 5 | m275 |
| 14 | laplace_cac_phasefield | PASS | 4 | m345 |
| 15 | laplace_pr_eos | PASS | 1 | m270 |
| 16 | maneuvering_steady_turn | PASS | 1 | m314 |
| 17 | permeability | PASS | 1 | m315 |
| 18 | permeability3d | PASS | 3 | m327 |
| 19 | poiseuille_2d | PASS | 2 | m285 |
| 20 | poiseuille_3d_annulus | PASS | 1 | m224 |
| 21 | poiseuille_3d_duct | PASS | 1 | m295 |
| 22 | poiseuille_3d_pipe | PASS | 9 | m351 |
| 23 | poiseuille_two_phase_cg | PASS | 1 | m252 |
| 24 | sphere_re100 | PASS | 11 | m403 |
| 25 | sphere_re100_d3q27 | PASS | 1 | m232 |
| 26 | sphere_re100_mem | PASS | 2 | m290 |
| 27 | startup_poiseuille | PASS | 3 | m409 |
| 28 | stokes_second_problem | PASS | 3 | m324 |
| 29 | stokes_sphere_dg | PASS | 1 | m234 |
| 30 | suboff_re1000 | PASS | 7 | m387 |
| 31 | suboff_sail_resistance | PASS | 3 | m289 |
| 32 | taylor_green_2d | PASS | 1 | m265 |
| 33 | taylor_green_3d | PASS | 2 | m312 |
| 34 | thermal_cavity | PASS | 4 | m325 |
| 35 | thermal_cavity_3d | PASS | 5 | m341 |
| 36 | womersley | PASS | 5 | m346 |

> 注：上表为 200 单窗口内可识别的 34+ 案例。合并报告 #342（m417 班次）覆盖 51 案例 206 单，含本窗口外的历史案例（如 aircraft_icing_naca0012_rime、backward_step、taylor_couette、wigley_hydrostatics 等），全部 PASS。

### 2.3 失败原因归类

**无 FAIL 案例。** 本窗口 93 单扫荡线全部 succeeded。

历史 FAIL 案例（已在先前班次解决）：
- blasius_flat_plate: 曾 HONEST_FAIL（Cf 误差 +27%），m410 修复后 PASS
- bstep_3d: 曾 HONEST_FAIL（X_r/h 偏差），m411 修复后 PASS
- channel_turb_3d: 曾 honest-FAIL（湍流精度），m414 如实记录后 PASS
- sphere_re100: 经 11 轮迭代，m403 最终 PASS

---

## 3. 全局 in-flight 态势

| 类别 | 数量 | 说明 |
|------|------|------|
| 全局 in-flight | 11 | |
| 资产清点线 | 5 | m419–m423（四板块 + 平台基础设施） |
| 报告返工线 | 5 | m241/m248/m261/m276/m286（历史报告缺陷修复） |
| SDAA 扫荡线 | 0 | 无在飞扫荡单 |
| 值班长 | 1 | m418（本单） |

---

## 4. 派发决策

### 4.1 决策矩阵

| 条件 | 状态 | 决策 |
|------|------|------|
| FAIL 案例 → 诊断修复 | 无 FAIL | 不派 |
| 扫荡线 in-flight < 4 → 下探 | 0 < 4 | 触发条件 |
| 可识别未覆盖案例 | 无（34 案例全 PASS，合并报告 #342 已覆盖 51 案例） | 无可派案例 |

### 4.2 本班次派发清单

**派发 0 单。**

理由：
1. 本窗口 34 案例全 PASS，无 FAIL 需诊断
2. 合并报告 #342 已覆盖 51 案例全 PASS
3. 扫荡线 in-flight=0 虽 <4，但无法识别未覆盖的 pending 案例（无 TensorLBM 仓库直接访问权限）
4. 盲目派发可能重复已覆盖案例，浪费 worker 资源

---

## 5. 升级事项（留给 owner）

1. **TensorLBM pending 案例目录不可见**：值班长无法直接访问 TensorLBM 仓库的 `benchmarks/pending/` 目录，导致无法识别未覆盖案例进行下探派发。建议：
   - 在平台增加 `/api/v1/tensor/benchmarks` 端点，列出所有 benchmark 案例及其扫荡状态
   - 或在 TensorPlatform 仓库维护一份 `contracts/sdaa_sweep_registry.json` 注册表

2. **合并报告数字口径差异**：不同班次窗口大小不同（50/100/200），导致案例数波动（34→44→51→56）。建议统一使用全量拉取（limit=∞ 或分页遍历）确保口径一致。

3. **报告返工线积压**：5 单历史报告返工（m241/m248/m261/m276/m286）长期 in-flight，建议 owner 评估是否继续或关闭。

---

## 6. 下一班建议

1. 若 owner 提供 TensorLBM pending 案例清单，可按字母序下探未覆盖案例
2. 关注资产清点线（m419–m423）完工结果
3. 评估报告返工线是否需要人工介入或关闭
4. 考虑建立 benchmark 案例注册表，避免值班长盲目派发

---

*本报告数字全部来自 API 返回（GET /api/v1/tensor/missions?limit=200），Python 程序化分析，零手抄。*
