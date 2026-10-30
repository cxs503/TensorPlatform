# 值班长班次 m102 分析报告

## 班次信息
- **班次号**: 102
- **日期**: 2026-10-29
- **操作员**: eco_admin (ADMIN)
- **职责**: 分析与调度（不改业务代码）

## 1. SDAA 扫荡线完工分析

### 数据获取
- 通过 POST /api/v1/auth/login 获取 token
- 通过 GET /api/v1/tensor/missions?limit=200 拉取近期 mission
- 筛选条件：repo=TensorLBM, goal 含 "SDAA 验证案例扫荡", 排除补报单

### 统计结果（逐单读 result 摘要，零手抄）

| 类别 | 数量 | 占比 |
|------|------|------|
| PASS | 36 | 83.7% |
| FAIL (honest-FAIL) | 6 | 14.0% |
| 执行失败 | 1 | 2.3% |
| **总计** | **43** | **100%** |

### PASS 案例（36 例）
acoustics (m12), cavity (m14), cavity_3d_full (m15), cavity_natural_convection (m51), couette_2d (m17), couette_3d (m18), cylinder (m19), cylinder_3d (m53), droplet_oscillation (m21), floating_body_free_decay (m22), kovasznay_2d (m23), laplace_cac_phasefield (m24), laplace_pr_eos (m26), maneuvering_steady_turn (m27), permeability (m28), permeability3d (m29), poiseuille_2d (m73), poiseuille_3d_annulus (m32), poiseuille_3d_duct (m33), poiseuille_3d_pipe (m74), poiseuille_two_phase_cg (m35), shear_wave_decay (m39), sod_shock_tube (m40), sphere_re100_d3q27 (m42), sphere_re200 (m47), square_cylinder (m48), startup_poiseuille (m49), stokes_first_problem (m50), stokes_second_problem (m60), stokes_sphere_dg (m61), taylor_couette (m64), taylor_green_2d (m65), taylor_green_3d (m67), thermal_cavity (m68), thermal_cavity_3d (m69), wigley_hydrostatics (m70)

### FAIL 案例（6 例，honest-FAIL）

| 案例 | Mission | 失败原因归类 |
|------|---------|-------------|
| aircraft_icing_naca0012_rime | m13 | 判据真红（6/11 PASS，5 项未过） |
| laplace_droplet | m52 | 判据真红 |
| sphere_re100 | m75 | 环境/硬件（OOM 与 timeout） |
| sphere_re100_mem | m46 | 判据真红 |
| suboff_sail_resistance | m63 | 环境/硬件（显存 14.94 GiB 不足） |
| womersley | m71 | 判据真红（H=29 α=8 C3 误差 4.890%） |

### 执行失败案例（1 例）
| 案例 | Mission | 原因 |
|------|---------|------|
| suboff_re1000 | m62 | 模型未调用 finish |

## 2. 处置（派发清单）

本班次派发 3 单（硬上限），均为 TensorLBM 扫荡类 goal：

| 新 Mission | 目标案例 | 类型 | 说明 |
|-----------|---------|------|------|
| m108 | suboff_re1000 | 重试 | m62 执行失败（模型未调用 finish），重派执行 |
| m109 | suboff_sail_resistance | 诊断+修复绕行（第 3 轮） | m63 honest-FAIL（显存不足），尝试降网格/优化内存 |
| m110 | womersley | 诊断+修复绕行（第 2 轮） | m71 honest-FAIL（精度超标），尝试调参改善 |

**派发依据**：
- FAIL 案例 → 派「诊断+修复绕行」mission
- 执行失败案例 → 派重试 mission
- in-flight 计数：m102-m107 共 6 单在途（≥4），不派新探索案例

## 3. 合并报告

已 POST /api/v1/tensor/reports 发布论文级合并报告：
- **Report ID**: 31
- **标题**: SDAA 扫荡合并报告：TensorLBM 验证案例全量 43 单完工判据汇总（2026-10-29）
- **内容**: 逐案例判据表 + FAIL 如实 + 设备证据汇总 + 局限 + 升级事项
- **状态**: published

## 4. 升级事项（留给 owner）

1. **显存升级**：SDAA 卡显存仅 14.94 GiB，suboff_sail_resistance、sphere_re100 等大网格案例因 OOM 无法完成。建议升级至 ≥24 GiB。
2. **精度优化**：womersley 案例在 H=29 α=8 C3 网格级别误差 4.890%，超过判据阈值。需算法层面优化或接受为硬件限制。
3. **agent 稳定性**：m62、m86 等执行失败案例反映 agent 未调用 finish 的问题，需平台侧改进 agent 可靠性。
4. **triton 绕行**：部分案例需 triton 算子绕行至 numpy/cupy，建议平台侧统一提供绕行模板。

## 5. 下一班建议

1. **跟踪在途任务**：m108/m109/m110 完工后检查 verdict，若仍 FAIL 则记录为最终 honest-FAIL
2. **新案例探索**：若 in-flight < 4，可按字母序派下探案例（检查 benchmarks/verified/ 中未扫荡的案例）
3. **补报检查**：检查是否有已完工但缺报告的 mission，派发补报单
4. **硬件监控**：持续监控 SDAA 卡显存使用，为显存升级提供数据支撑
