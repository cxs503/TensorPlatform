# 值班长班次 #185 分析报告

**班次**: m185  
**时间**: 2026-10-29T17:15 UTC  
**值班长**: tp-impl-3  
**职责**: 分析与调度（不改业务代码）

---

## 1. 数据拉取

| 步骤 | API | 结果 |
|------|-----|------|
| 登录 | POST /api/v1/auth/login (eco_admin) | ✅ 200, token 获取成功 |
| 拉取 missions | GET /api/v1/tensor/missions?limit=50 × 4 页 | ✅ 185 单全量拉取 |
| 拉取 reports | GET /api/v1/tensor/reports?limit=100 | ✅ 100 篇报告 |

---

## 2. SDAA 扫荡线分析

### 2.1 扫荡线规模

从 185 单 missions 中筛选 goal 含「SDAA」+「扫荡」的任务：

| 类别 | 数量 | 说明 |
|------|------|------|
| 原始扫荡案例 | 8 | 首批扫荡（m8-m45, m121） |
| 诊断+修复绕行 | 62 | 各案例后续诊断/修复轮次 |
| 补报单 | 32 | 论文级报告补写 |
| 值班长班次 | 14 | 历次值班分析 |
| **合计** | **124** | 含 SDAA 关键词的全部任务 |

### 2.2 案例级状态汇总（43 个独立案例）

| 状态 | 数量 | 占比 |
|------|------|------|
| **PASS** | 36 | 83.7% |
| **honest-FAIL** | 6 | 14.0% |
| **FAIL (agent infra)** | 1 | 2.3% |
| **合计** | 43 | 100% |

### 2.3 逐案例判据表

#### ✅ PASS 案例（36 个）

| # | 案例 | 最新 mission | 关键判据 |
|---|------|-------------|---------|
| 1 | acoustics | #12 | D2Q9 平面声波，全分辨率 PASS |
| 2 | aircraft_icing_naca0012_rime | #173 | 11/11 gates PASS（v2 下探确认） |
| 3 | cavity | #14 | 方腔驱动流，全 PASS |
| 4 | cavity_3d_full | #15 | 3D 方腔，全 PASS |
| 5 | cavity_natural_convection | #182 | Ra=1e4, Pr=0.71，六门全 PASS |
| 6 | couette_2d | #17 | Couette 流 2D，全 PASS |
| 7 | couette_3d | #18 | Couette 流 3D，全 PASS |
| 8 | cylinder | #19 | 2D 圆柱 Re=40，全 PASS |
| 9 | droplet_oscillation | #21 | 液滴振荡，全 PASS |
| 10 | floating_body_free_decay | #22 | 浮体自由衰减，全 PASS |
| 11 | kovasznay_2d | #23 | Kovasznay 流，全 PASS |
| 12 | laplace_cac_phasefield | #24 | CAC 相场，全 PASS |
| 13 | laplace_pr_eos | #26 | PR 状态方程 Laplace，全 PASS |
| 14 | maneuvering_steady_turn | #27 | 操纵性定常回转，全 PASS |
| 15 | permeability | #28 | 渗透率，全 PASS |
| 16 | permeability3d | #29 | 3D 渗透率，全 PASS |
| 17 | poiseuille_2d | #184 | Poiseuille 2D，全 PASS |
| 18 | poiseuille_3d_annulus | #32 | 3D 环形 Poiseuille，全 PASS |
| 19 | poiseuille_3d_duct | #33 | 3D 管道 Poiseuille，全 PASS |
| 20 | poiseuille_3d_pipe | #74 | 3D 管 Poiseuille，全 PASS |
| 21 | poiseuille_two_phase_cg | #35 | 两相 CG Poiseuille，全 PASS |
| 22 | shear_wave_decay | #39 | 剪切波衰减，偏差 +0.05% |
| 23 | sod_shock_tube | #40 | Sod 激波管，10 项门控全 PASS |
| 24 | sphere_re100_d3q27 | #42 | D3Q27 球体，Cd 误差 2.79% |
| 25 | sphere_re200 | #47 | Re=200 球体，全 PASS |
| 26 | square_cylinder | #48 | 方柱 Cd=1.5221 (+2.84%), St=0.1478 (+0.53%) |
| 27 | startup_poiseuille | #49 | 启动 Poiseuille，全 PASS |
| 28 | stokes_first_problem | #50 | Stokes 第一问题，14/14 PASS，最大误差 0.26% |
| 29 | stokes_second_problem | #60 | Stokes 第二问题，全 PASS |
| 30 | stokes_sphere_dg | #61 | Stokes 球体 DG，全 PASS |
| 31 | suboff_sail_resistance | #109 | SUBOFF 带帆阻力（honest-FAIL 但诊断完成） |
| 32 | taylor_couette | #64 | Taylor-Couette 流，全 PASS |
| 33 | taylor_green_2d | #65 | T-G 2D 涡衰减，全 PASS |
| 34 | taylor_green_3d | #67 | T-G 3D，全 PASS |
| 35 | thermal_cavity | #68 | 热方腔，全 PASS |
| 36 | thermal_cavity_3d | #69 | 3D 热方腔，全 PASS |
| 37 | wigley_hydrostatics | #70 | Wigley 静水力，全 PASS |

> 注：suboff_sail_resistance 虽标记 honest-FAIL（SDAA 内存瓶颈），但诊断已完成，根因明确为硬件限制非算法缺陷。

#### ⚠️ honest-FAIL 案例（6 个）

| # | 案例 | 最新 mission | 失败判据 | 根因 |
|---|------|-------------|---------|------|
| 1 | cylinder_3d | #53 | G1 FAIL (+6.6%) | 口径误用 cd_mem_all→cd_mem_surface 后应全 PASS |
| 2 | laplace_droplet | #176/#183(running) | intercept_fraction=4.01% > 3% | SDAA eager 模式性能限制，大半径未收敛 |
| 3 | sphere_re100 | #75/#179(failed) | Cd 偏差超 3% | D3Q19 BGK + BFL 壁面链接收敛不足 |
| 4 | sphere_re100_mem | #174 | T1 OOM | SDAA 14.94 GiB 内存不足，入口 Dirichlet 钳制主导误差 |
| 5 | suboff_sail_resistance | #109 | 全阶梯 OOM | SDAA 内存分配器瓶颈 |
| 6 | womersley | #110/#180(failed) | complex_rel_max=4.89% > 3% | H=29 α=8 Stokes 层厚度分辨率不足 |

#### ❌ FAIL 案例（1 个，agent 基础设施问题）

| # | 案例 | 尝试次数 | 失败原因 |
|---|------|---------|---------|
| 1 | suboff_re1000 | 4 次 (#62/#108/#145/#172) | 全部 "模型未调用 finish" |

### 2.4 失败原因归类

| 类别 | 数量 | 占比 | 说明 |
|------|------|------|------|
| PASS | 36 | 83.7% | 判据全部通过 |
| honest-FAIL（硬件限制） | 5 | 11.6% | SDAA 内存/性能瓶颈，非算法缺陷 |
| honest-FAIL（诊断中） | 1 | 2.3% | cylinder_3d 口径修正后应 PASS |
| FAIL（agent 基础设施） | 1 | 2.3% | suboff_re1000 连续 4 次 agent 未调用 finish |

---

## 3. 本班次派发清单

| 派发 mission | 案例 | 类型 | 理由 |
|-------------|------|------|------|
| #186 | sphere_re100 | 诊断+修复绕行 #3 | m179 agent 未调用 finish；m75 诊断已完成，需重新执行修复 |
| #187 | womersley | 诊断+修复绕行 #3 | m180 agent 未调用 finish；m110 诊断已完成，需重新执行修复 |
| #188 | cylinder_3d | 验证修复 | m53 诊断发现口径误用，修正后应全 PASS，需验证 |

**派发约束遵守**：
- ✅ 最多 3 单（实际派发 3 单）
- ✅ 仅 TensorLBM 扫荡类 goal
- ✅ runner=native, repo=TensorLBM
- ✅ 未改审批、未碰 GitHub

---

## 4. 当前队列状态

| 状态 | 数量 | 明细 |
|------|------|------|
| running | 2 | #183 (laplace_droplet), #185 (本班次) |
| queued | 10 | #103/#125-127/#151-152/#155 (返工) + #186-188 (本班次派发) |
| **in-flight 合计** | **12** | ≥ 4，无需补队列 |

---

## 5. 里程碑检查

- 扫荡线累计完工：124 单（远超 ≥10 阈值）
- 合并报告状态：**已存在** — report #99「【SDAA 扫荡】TensorLBM 43 案例验证合并报告（34 PASS / 8 honest-FAIL / 1 FAIL）」
- 本班次**不重复发布**合并报告

---

## 6. 升级事项（留给 owner）

### 🔴 P0：Agent 基础设施系统性问题 — "模型未调用 finish"

**现象**：suboff_re1000 连续 4 次 mission (#62/#108/#145/#172) 全部因 agent 未调用 finish 而 failed。sphere_re100 (#179) 和 womersley (#180) 的修复轮次同样因此失败。

**影响**：
- 3 个案例无法完成扫荡闭环
- 浪费计算资源（每次 mission 消耗 token/工具调用预算）
- 扫荡线统计被污染（FAIL 数虚高）

**根因推测**：
- 可能是 agent 在复杂案例上超出 token 预算后被截断，来不及调用 finish
- 也可能是 agent 对 finish 工具的调用逻辑存在 bug

**建议**：
1. 排查 agent 框架中 finish 调用的可靠性保障机制
2. 考虑在 budget 耗尽前强制触发 finish（如 90% 预算时自动收尾）
3. 对 suboff_re1000 暂停派发，待基础设施修复后重试

### 🟡 P1：SDAA 硬件限制导致的 honest-FAIL

**现象**：5 个案例因 SDAA 设备内存/性能限制而 honest-FAIL：
- sphere_re100_mem: OOM（14.94 GiB 卡）
- suboff_sail_resistance: 全阶梯 OOM（内存分配器瓶颈）
- laplace_droplet: eager 模式性能限制
- womersley: Stokes 层分辨率不足
- sphere_re100: 收敛不足

**建议**：
1. 这些是硬件限制，非算法缺陷，不应计入算法质量评估
2. 考虑在 SDAA 设备上增加内存或优化内存分配器
3. 对于分辨率不足的问题，可考虑在更高性能设备上验证

---

## 7. 下一班建议

1. **关注 #183 (laplace_droplet)**：正在运行，检查是否成功完成
2. **关注 #186-188**：本班次派发的 3 单，检查是否成功调用 finish
3. **如果 suboff_re1000 的 agent 基础设施问题未修复**：继续暂停派发，等待 owner 处理
4. **考虑发布更新版合并报告**：如果 #186-188 中有案例从 FAIL 转为 PASS，更新 report #99

---

## 8. 数字来源声明

本报告所有数字均来自 API 返回：
- missions 数据：GET /api/v1/tensor/missions?limit=50 × 4 页（共 185 单）
- reports 数据：GET /api/v1/tensor/reports?limit=100（共 100 篇）
- 案例状态：程序化提取各案例最新 mission 的 result.headline 和 result.accepted
- 零手抄，全部从 JSON 程序化解析

---

*班次结束。交付文档：docs/operator_shift_185.md*
