# 值班长班次报告 — m177

## 班次信息

- **班次号**: 177
- **执行时间**: 2026-10-29
- **操作员**: eco_admin (ADMIN)
- **职责**: 分析与调度（不改业务代码）

---

## 一、扫荡线完工分析

### 数据来源
- API: `GET /api/v1/tensor/missions?limit=200`
- 筛选条件: goal 以 "SDAA 验证案例扫荡" 开头
- 总检索 mission 数: 177（数据库总量），实际分析 SDAA 扫荡类 57 单次执行

### 逐案例完工结果（按最新轮次）

| 案例 | 最新 Mission | 状态 | 判定 | 失败原因归类 |
|------|-------------|------|------|-------------|
| acoustics | m12 | succeeded | ✅ PASS | - |
| aircraft_icing_naca0012_rime | m173 | succeeded | ✅ PASS | - |
| cavity | m14 | succeeded | ✅ PASS | - |
| cavity_3d_full | m15 | succeeded | ✅ PASS | - |
| cavity_natural_convection | m51 | succeeded | ✅ PASS | - |
| couette_2d | m17 | succeeded | ✅ PASS | - |
| couette_3d | m18 | succeeded | ✅ PASS | - |
| cylinder | m19 | succeeded | ✅ PASS | - |
| cylinder_3d | m53 | succeeded | ✅ PASS | - |
| droplet_oscillation | m21 | succeeded | ✅ PASS | - |
| floating_body_free_decay | m22 | succeeded | ✅ PASS | - |
| kovasznay_2d | m23 | succeeded | ✅ PASS | - |
| laplace_cac_phasefield | m24 | succeeded | ✅ PASS | - |
| laplace_droplet | m176 | succeeded | ❌ FAIL | 案例未达标（intercept_fraction=4.010% > 3%） |
| laplace_pr_eos | m26 | succeeded | ✅ PASS | - |
| maneuvering_steady_turn | m27 | succeeded | ✅ PASS | - |
| permeability | m28 | succeeded | ✅ PASS | - |
| permeability3d | m29 | succeeded | ✅ PASS | - |
| poiseuille_2d | m73 | succeeded | ✅ PASS | - |
| poiseuille_3d_annulus | m32 | succeeded | ✅ PASS | - |
| poiseuille_3d_duct | m33 | succeeded | ✅ PASS | - |
| poiseuille_3d_pipe | m74 | succeeded | ✅ PASS | - |
| poiseuille_two_phase_cg | m35 | succeeded | ✅ PASS | - |
| shear_wave_decay | m39 | succeeded | ✅ PASS | - |
| sod_shock_tube | m40 | succeeded | ✅ PASS | - |
| sphere_re100 | m75 | succeeded | ❌ FAIL | 案例未达标 |
| sphere_re100_d3q27 | m42 | succeeded | ✅ PASS | - |
| sphere_re100_mem | m174 | succeeded | ❌ FAIL | honest-FAIL（判据真红） |
| sphere_re200 | m47 | succeeded | ✅ PASS | - |
| square_cylinder | m48 | succeeded | ✅ PASS | - |
| startup_poiseuille | m49 | succeeded | ✅ PASS | - |
| stokes_first_problem | m50 | succeeded | ✅ PASS | - |
| stokes_second_problem | m60 | succeeded | ✅ PASS | - |
| stokes_sphere_dg | m61 | succeeded | ✅ PASS | - |
| suboff_re1000 | m172 | running | 🔄 进行中 | - |
| suboff_sail_resistance | m109 | succeeded | ❌ FAIL | honest-FAIL（SDAA 内存分配器瓶颈） |
| taylor_couette | m64 | succeeded | ✅ PASS | - |
| taylor_green_2d | m65 | succeeded | ✅ PASS | - |
| taylor_green_3d | m67 | succeeded | ✅ PASS | - |
| thermal_cavity | m68 | succeeded | ✅ PASS | - |
| thermal_cavity_3d | m69 | succeeded | ✅ PASS | - |
| wigley_hydrostatics | m70 | succeeded | ✅ PASS | - |
| womersley | m110 | succeeded | ❌ FAIL | honest-FAIL（complex_rel_max=4.890% > 3%） |

### 统计汇总

| 指标 | 数值 |
|------|------|
| 唯一案例数 | 43 |
| PASS | 37 (86.0%) |
| FAIL | 5 (11.6%) |
| 进行中 | 1 (2.3%) |

### 失败原因归类

| 归类 | 案例数 | 案例列表 |
|------|--------|---------|
| 判据真红（honest-FAIL） | 3 | sphere_re100_mem, suboff_sail_resistance, womersley |
| 案例未达标 | 2 | laplace_droplet, sphere_re100 |
| 环境/triton 绕行 | 0 | - |
| 其他（mission_failed） | 0 | （早期 m16/m25/m31/m34/m41/m62/m108/m145 已在后续轮次修复） |

---

## 二、本班次派发清单

本班次共派发 **3 单**（硬上限），全部为 TensorLBM 扫荡类 goal：

| 新 Mission ID | 案例 | 类型 | 派发依据 |
|--------------|------|------|---------|
| m178 | laplace_droplet | 诊断+修复绕行（第3轮） | m176 诊断确认 intercept_fraction=4.010% 超阈值，需修复绕行 |
| m179 | sphere_re100 | 诊断+修复绕行（第2轮） | m75 诊断完成但仍 FAIL，m41 曾未完成 |
| m180 | womersley | 诊断+修复绕行（第2轮） | m110 确认 complex_rel_max=4.890% > 3%，需尝试更细网格/调参 |

**派发约束检查**:
- ✅ 全部 repo=TensorLBM
- ✅ 全部 runner=native
- ✅ 全部为 SDAA 扫荡类 goal
- ✅ 未超过 3 单硬上限
- ✅ 未改审批、未碰 GitHub

---

## 三、合并报告发布

- **报告 ID**: 106
- **标题**: SDAA 扫荡线合并报告 — 班次 m177（论文级）
- **scenario_key**: sdaa-sweep-consolidated
- **发布方式**: POST /api/v1/tensor/reports
- **触发条件**: 扫荡线累计完工 ≥10 单（实际 43 单）✅

---

## 四、下一班建议

1. **跟踪 m172 (suboff_re1000)**: 当前进行中，完工后需确认 PASS/FAIL 状态
2. **跟踪 m178/m179/m180**: 本班次派发的 3 单诊断+修复绕行，需关注完工结果
3. **FAIL 案例后续**:
   - laplace_droplet: 若 m178 仍 FAIL，建议升级至 LBM 团队审查 3D 界面重构算法
   - sphere_re100: 若 m179 仍 FAIL，建议对比 sphere_re100_d3q27 (m42 PASS) 的差异根因
   - womersley: 若 m180 仍 FAIL，建议评估是否调整判据阈值或接受 honest-FAIL
   - sphere_re100_mem: 已二阶诊断完成，建议 owner 决策是否接受 honest-FAIL
   - suboff_sail_resistance: 已三阶诊断完成（内存瓶颈），建议 owner 决策显存扩容或网格降阶
4. **图件补齐**: 多数扫荡 mission 未产出场量云图与比较曲线，建议后续班次补充
5. **新案例探索**: 43 个案例已覆盖，建议检查 TensorLBM benchmarks/verified 是否还有未覆盖案例

---

## 五、升级事项（留给 owner）

1. **suboff_sail_resistance 内存瓶颈**: SDAA 14.94 GiB 显存在 L=90~110 网格级别全部 OOM，建议评估显存扩容或案例网格降阶方案
2. **laplace_droplet 3D 界面捕捉**: intercept_fraction=4.010% 超 3% 阈值，根因为 SDAA eager 模式性能限制，建议 LBM 团队审查 3D 界面重构算法
3. **womersley 复数速度剖面误差**: α=8 C3 网格 complex_rel_max=4.890% 超阈值，建议审查 Stokes 层厚度与网格分辨率匹配性
4. **agent finish 调用纪律**: 早期多单 mission（m16/m25/m31/m34/m41/m62/m108/m145）因 agent 未调用 finish 而失败，建议加强 agent 执行纪律检查
5. **Triton 编译依赖**: 部分案例在 SDAA 设备上需 Triton 绕行，建议评估 Triton 对 SDAA 设备的支持完善

---

*报告生成: 2026-10-29*
*数据来源: API /api/v1/tensor/missions (零手抄，程序化注入)*
*合并报告: POST /api/v1/tensor/reports → Report #106*
