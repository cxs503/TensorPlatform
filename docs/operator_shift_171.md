# 值班长班次报告 — m171

**班次号**: 171  
**日期**: 2026-10-29  
**值班类型**: 分析与调度（不改业务代码）

---

## 1. 扫荡线完工分析

### 1.1 总体统计

| 指标 | 数值 |
|------|------|
| 扫荡任务总数 | 53 |
| 独立案例数 | 43 |
| 成功任务 (succeeded) | 45 |
| 失败任务 (failed) | 8 |
| In-flight | 1 (本班次) |

### 1.2 逐案例判定表

| # | 案例 | 最新ID | 判定 | 尝试 | 备注 |
|---|------|--------|------|------|------|
| 1 | acoustics | 12 | PASS | 1 | |
| 2 | aircraft_icing_naca0012_rime | 13 | honest-FAIL | 1 | 6/11判据PASS |
| 3 | cavity | 14 | PASS | 1 | |
| 4 | cavity_3d_full | 15 | PASS | 1 | |
| 5 | cavity_natural_convection | 51 | PASS | 2 | |
| 6 | couette_2d | 17 | PASS | 1 | |
| 7 | couette_3d | 18 | honest-FAIL | 1 | 判据未全过 |
| 8 | cylinder | 19 | PASS | 1 | |
| 9 | cylinder_3d | 53 | honest-FAIL | 2 | 判据未全过 |
| 10 | droplet_oscillation | 21 | PASS | 1 | |
| 11 | floating_body_free_decay | 22 | PASS | 1 | |
| 12 | kovasznay_2d | 23 | PASS | 1 | |
| 13 | laplace_cac_phasefield | 24 | PASS | 1 | |
| 14 | laplace_droplet | 52 | honest-FAIL | 2 | 判据未全过 |
| 15 | laplace_pr_eos | 26 | PASS | 1 | |
| 16 | maneuvering_steady_turn | 27 | PASS | 1 | |
| 17 | permeability | 28 | PASS | 1 | |
| 18 | permeability3d | 29 | PASS | 1 | |
| 19 | poiseuille_2d | 73 | PASS | 2 | |
| 20 | poiseuille_3d_annulus | 32 | PASS | 1 | |
| 21 | poiseuille_3d_duct | 33 | PASS | 1 | |
| 22 | poiseuille_3d_pipe | 74 | PASS | 2 | |
| 23 | poiseuille_two_phase_cg | 35 | PASS | 1 | |
| 24 | shear_wave_decay | 39 | PASS | 1 | |
| 25 | sod_shock_tube | 40 | PASS | 1 | |
| 26 | sphere_re100 | 75 | honest-FAIL | 2 | Cd偏差超阈值 |
| 27 | sphere_re100_d3q27 | 42 | PASS | 1 | |
| 28 | sphere_re100_mem | 46 | honest-FAIL | 1 | Cd偏差超阈值 |
| 29 | sphere_re200 | 47 | PASS | 1 | |
| 30 | square_cylinder | 48 | PASS | 1 | |
| 31 | startup_poiseuille | 49 | PASS | 1 | |
| 32 | stokes_first_problem | 50 | PASS | 1 | |
| 33 | stokes_second_problem | 60 | PASS | 1 | |
| 34 | stokes_sphere_dg | 61 | PASS | 1 | |
| 35 | suboff_re1000 | 145 | FAIL | 3 | agent未调用finish×3轮 |
| 36 | suboff_sail_resistance | 109 | honest-FAIL | 2 | SDAA OOM |
| 37 | taylor_couette | 64 | PASS | 1 | |
| 38 | taylor_green_2d | 65 | PASS | 1 | |
| 39 | taylor_green_3d | 67 | PASS | 1 | |
| 40 | thermal_cavity | 68 | PASS | 1 | |
| 41 | thermal_cavity_3d | 69 | PASS | 1 | |
| 42 | wigley_hydrostatics | 70 | PASS | 1 | |
| 43 | womersley | 110 | honest-FAIL | 2 | complex_rel=4.890% |

### 1.3 判定汇总

| 判定 | 数量 | 占比 |
|------|------|------|
| PASS | 34 | 79.1% |
| honest-FAIL | 8 | 18.6% |
| FAIL | 1 | 2.3% |

### 1.4 失败原因归类

#### Mission-level FAIL (8 单, 1 独立案例)

| Mission ID | 案例 | 轮次 | 失败原因 | 分类 |
|------------|------|------|---------|------|
| 62 | suboff_re1000 | 1 | 模型未调用 finish (80次调用) | 环境/Agent |
| 108 | suboff_re1000 | 2 | 模型未调用 finish (14次调用) | 环境/Agent |
| 145 | suboff_re1000 | 3 | 模型未调用 finish (35次调用) | 环境/Agent |
| 16 | cavity_natural_convection | 1 | 模型未调用 finish (80次调用) | 环境/Agent |
| 25 | laplace_droplet | 1 | 模型未调用 finish (13次调用) | 环境/Agent |
| 31 | poiseuille_2d | 1 | 模型未调用 finish (37次调用) | 环境/Agent |
| 34 | poiseuille_3d_pipe | 1 | 模型未调用 finish (80次调用) | 环境/Agent |
| 41 | sphere_re100 | 1 | 模型未调用 finish (80次调用) | 环境/Agent |

**失败原因分类**：
- **环境/Agent 问题**: 8/8 (100%) — 全部为 agent 未调用 finish 工具导致任务中断
- **Triton 绕行问题**: 0/8
- **判据真红**: 0/8
- **其他**: 0/8

**注意**: 其中 5 个案例（cavity_natural_convection, laplace_droplet, poiseuille_2d, poiseuille_3d_pipe, sphere_re100）已在第 2 轮成功完成，仅 suboff_re1000 连续 3 轮均失败。

#### honest-FAIL (8 案例, mission 成功但判据未全过)

| 案例 | 最新ID | 根因 | 分类 |
|------|--------|------|------|
| aircraft_icing_naca0012_rime | 13 | 6/11 内部一致性判据未过 | 判据真红 |
| couette_3d | 18 | 部分判据未过 | 判据真红 |
| cylinder_3d | 53 | 部分判据未过 | 判据真红 |
| laplace_droplet | 52 | 多相 Laplace 压差偏差 | 判据真红 |
| sphere_re100 | 75 | Cd 偏差超阈值 | 判据真红 |
| sphere_re100_mem | 46 | Cd 偏差超阈值 | 判据真红 |
| suboff_sail_resistance | 109 | SDAA 显存不足 OOM | 环境/硬件 |
| womersley | 110 | complex_rel_max=4.890%>3% | 判据真红 |

---

## 2. 派发清单

本班次共派发 3 单（硬上限 3 单，仅 TensorLBM 扫荡类）：

| # | Mission ID | 案例 | 类型 | Goal 摘要 |
|---|-----------|------|------|----------|
| 1 | 172 | suboff_re1000 | FAIL→诊断+修复绕行 | 第4轮，3轮agent未finish，需复用残留脚本或从零写，注意显存 |
| 2 | 173 | aircraft_icing_naca0012_rime | 下探 | 第2轮，6/11判据PASS，分析FAIL根因并尝试收敛 |
| 3 | 174 | sphere_re100_mem | 下探 | 第2轮，判据FAIL，分析根因并尝试收敛 |

**派发理由**：
- m172: FAIL 案例必须派诊断+修复绕行（规则要求）
- m173/m174: In-flight=1<4，按案例字母序派 honest-FAIL 下探案例

---

## 3. 里程碑

- ✅ 扫荡线累计完工 45 单 ≥ 10 单
- ✅ 已发布合并报告（POST /api/v1/tensor/reports，标题含「SDAA 扫荡」）
- 合并报告覆盖全量 43 案例，含逐案例判据表、FAIL 如实记录、设备证据汇总、局限声明

---

## 4. 升级事项（留给 owner）

1. **Agent finish 机制排查（P0）**: suboff_re1000 连续 3 轮 agent 未调用 finish，这是系统性问题。建议：
   - 排查 agent 模型在长任务/大网格案例上的 finish 调用率
   - 考虑在 agent 框架层增加超时强制 finish 或 finish 提醒机制
   - 评估是否需要为 suboff_re1000 指定更稳定的模型

2. **SDAA 大网格显存瓶颈（P1）**: suboff_sail_resistance OOM，14.94 GiB 卡无法运行 L≥90 网格。建议：
   - 评估多卡并行方案
   - 或为 SDAA 验证线配备更大显存卡

3. **honest-FAIL 案例深度分析（P2）**: 8 个 honest-FAIL 案例需逐个分析根因，部分可能需要：
   - 更长模拟时间
   - 更细网格分辨率
   - 算法参数调优（如 womersley 的 τ 值）

---

## 5. 下一班建议

1. **关注 m172 (suboff_re1000)**: 若第 4 轮仍因 agent 问题失败，建议 owner 直接介入或更换模型
2. **关注 m173/m174 下探结果**: 若 honest-FAIL 案例在第 2 轮仍无法收敛，需评估是否为 SDAA 硬件/驱动层面的数值精度问题
3. **队列状态**: 派发后 in-flight 约 4 单（含本班次），下一班可视情况补派更多下探案例
4. **合并报告已发布**: 下一班无需再发合并报告，但可更新 v2 版本补充新完工案例

---

*报告生成: 2026-10-29 | 数据来源: TensorPlatform API | 零手抄*
