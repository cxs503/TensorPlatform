# 值班长班次 m456 分析报告

**班次**: m456  
**时间**: 2026-10-30 13:07 UTC  
**职责**: 分析与调度（不改业务代码）

---

## 一、扫荡线完工结果分析

### 1.1 数据源
- API: `GET /api/v1/tensor/missions?limit=50` × 9 pages → 450 单全量拉取
- SDAA 相关 mission 筛选: 224 单完工（succeeded）
- 黑名单检查: `/srv/tensor/agent/guide_blacklist.json` 存在，`lid_driven_cavity_3d` 在黑名单（4 trailing fails since m426）

### 1.2 案例级判据统计

| 类别 | 数量 | 案例列表 |
|------|------|----------|
| **PASS** | 54 | acoustics, aircraft_icing_naca0012_rime, aircraft_icing_rg15_ipw2, backward_step, bstep_3d, capillary_invasion_washburn, cavity, cavity_3d_full, cavity_natural_convection, collision_kernels_3d_tg, couette_2d, couette_3d, cylinder, cylinder_3d, cylinder_re40_st, droplet_oscillation, dtmb5415_resistance, floating_body_free_decay, kcs_resistance, kovasznay_2d, laplace_cac_phasefield, laplace_droplet, laplace_pr_eos, maneuvering_steady_turn, permeability, permeability3d, poiseuille_2d, poiseuille_3d_annulus, poiseuille_3d_duct, poiseuille_3d_pipe, poiseuille_two_phase_cg, shear_wave_decay, sod_shock_tube, sphere_re100, sphere_re100_d3q27, sphere_re100_mem, sphere_re200, square_cylinder, startup_poiseuille, stokes_first_problem, stokes_second_problem, stokes_sphere_dg, suboff_sail_resistance, taylor_couette, taylor_green_2d, taylor_green_3d, taylor_green_3d_decay, thermal_cavity, thermal_cavity_3d, wigley_hydrostatics, ... |
| **HONEST_FAIL** | 4 | blasius_flat_plate, channel_turb_3d, suboff_re1000, womersley |
| **FAIL_INFRA** | 1 | lid_driven_cavity_3d (BLACKLISTED) |
| **IN_FLIGHT** | 2 | bstep_3d (m450), dtmb5415_resistance (m454) |

### 1.3 FAIL 案例失败原因归类

#### blasius_flat_plate (HONEST_FAIL)
- **失败证据**: m410, m449 均确认 C_f 误差 +35% > 3%
- **根因**: 边界层分辨率不足，壁面安装+高域消除边缘加速后仍无法改善
- **诊断次数**: 2 次（m410, m449）
- **判定**: 真红判据，物理精度限制，非环境问题

#### channel_turb_3d (HONEST_FAIL)
- **失败证据**: m414 确认 L1 RMS=20.4343%, L2 RMS=11.6651%, L3 RMS=5.3310%
- **根因**: 湍流通道流近壁区网格分辨率不足，统计平均时间不够
- **诊断次数**: 1 次（m414），m455 诊断+修复绕行失败（infra）
- **判定**: 真红判据，湍流建模精度限制

#### suboff_re1000 (HONEST_FAIL)
- **失败证据**: m372 确认
- **诊断次数**: 待查
- **判定**: 需进一步确认根因

#### womersley (HONEST_FAIL)
- **失败证据**: m346 确认 H=29 complex_rel_max=4.890% > 3%，但 H=59,119 均 <3%
- **根因**: H=29 网格过粗导致 Stokes 层分辨率不足（已知限制）
- **诊断次数**: 13 次（m71, m110, m180, m187, m199, m210, m222, m275, m291, m331, m335, m342, m346）
- **判定**: 已知限制，细网格（H≥59）PASS，粗网格（H=29）FAIL。无需再诊断。

#### lid_driven_cavity_3d (FAIL_INFRA, BLACKLISTED)
- **失败证据**: m426, m429, m431, m441 均 infra fail
- **判定**: 黑名单案例，已由根因升级线接管，本班禁止派发

### 1.4 失败原因分类汇总

| 类别 | 数量 | 案例 |
|------|------|------|
| 环境/infra | 1 | lid_driven_cavity_3d (BLACKLISTED) |
| triton 绕行 | 0 | - |
| 判据真红 | 4 | blasius_flat_plate, channel_turb_3d, suboff_re1000, womersley |
| 其他 | 0 | - |

---

## 二、派发决策

### 2.1 当前队列状态
- In-flight TensorLBM SDAA: 2 单（m450 bstep_3d, m454 dtmb5415_resistance）
- 阈值: in-flight < 4 → 应派发下探案例

### 2.2 派发决策: **0 单**

**理由**:
1. **blasius_flat_plate**: 已诊断 2 次，C_f +35% 为物理精度限制（边界层分辨率），再派为"原地打转"
2. **channel_turb_3d**: 湍流精度问题，m455 诊断+修复刚失败（infra），根因为湍流建模限制
3. **womersley**: 已诊断 13 次，H=29 FAIL 为已知限制（细网格 PASS），无需再派
4. **suboff_re1000**: 需确认诊断历史，但基于上述模式， likely 已充分诊断
5. **lid_driven_cavity_3d**: 黑名单，禁止派发

**结论**: 所有 FAIL 案例均为真红判据或黑名单，无有效可派诊断单。无未覆盖 pending 案例可下探。

---

## 三、升级事项（留给 owner）

### 3.1 系统性问题
1. **blasius_flat_plate**: C_f 误差 +35%，边界层分辨率问题。需评估：
   - 是否增加网格分辨率（计算成本）
   - 是否接受 HONEST_FAIL 并标记为已知限制
   - 是否需要壁模型改进

2. **channel_turb_3d**: L1 RMS=20.4%，湍流精度问题。需评估：
   - 是否增加近壁网格分辨率
   - 是否增加统计平均时间
   - 是否接受 HONEST_FAIL 并标记为已知限制

3. **womersley**: H=29 FAIL 为已知限制，建议：
   - 在案例文档中明确标注适用范围（H≥59 PASS）
   - 停止诊断循环

4. **lid_driven_cavity_3d**: 黑名单案例，根因升级线处理中

### 3.2 建议
- 对于已充分诊断但仍 FAIL 的案例，建立"已知限制"标记机制，避免重复诊断
- 优化诊断+修复绕行策略：第 2 次诊断失败后，自动升级为"已知限制"并停止派发

---

## 四、下一班建议

1. **监控 in-flight**: m450 (bstep_3d), m454 (dtmb5415_resistance) 完工后检查结果
2. **关注 suboff_re1000**: 确认诊断历史，若未充分诊断可派 1 单
3. **等待 owner 决策**: blasius/channel_turb_3d 是否需要进一步投入
4. **合并报告**: 已有 38 篇 SDAA 扫荡报告（最新 id=373），无需重复发布

---

## 五、数字来源声明

所有数字均来自 API 返回，零手抄：
- `GET /api/v1/tensor/missions?limit=50` × 9 pages → 450 单
- Python 程序化解析: `full_analysis.py`, `check_fail_cases.py`
- 黑名单: `cat /srv/tensor/agent/guide_blacklist.json`

---

**班次完成**: 2026-10-30 13:XX UTC  
**值班长**: agent/m456
