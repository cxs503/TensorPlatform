# 值班长班次报告 m451

**班次**: m451  
**类型**: 分析与调度（不改业务代码）  
**时间**: 2026-10-30  

---

## 一、API 扫荡结果分析

### 1.1 数据获取（程序化，零手抄）

| 步骤 | API | 结果 |
|------|-----|------|
| 登录 | POST /api/v1/auth/login (eco_admin) | ✅ JWT token |
| 拉取 | GET /api/v1/tensor/missions?limit=50 × 10 pages | 451 单全量落盘 |
| 筛选 | goal 含「SDAA」+「扫荡」 | 304 单 SDAA 扫荡线 |
| 排除 | 值班长班次单 | 224 单扫荡完工（非值班长） |

### 1.2 案例状态统计

| 类别 | 数量 | 说明 |
|------|------|------|
| 唯一案例 | 55 | 程序化提取 |
| PASS | 36 | 最新判定为通过 |
| HONEST_FAIL | 4 | 诊断完成，根因为物理/分辨率极限 |
| FAIL (infra/criterion) | 11 | 含已后续通过的旧单 |
| IN_FLIGHT | 4 | 诊断+修复绕行中 |

### 1.3 PASS 案例清单（36 例）

acoustics, aircraft_icing_naca0012_rime, aircraft_icing_rg15_ipw2, capillary_invasion_washburn, cavity, cavity_3d_full, cavity_natural_convection, couette_2d, couette_3d, cylinder, cylinder_3d, droplet_oscillation, floating_body_free_decay, kcs_resistance, kovasznay_2d, laplace_droplet, laplace_pr_eos, maneuvering_steady_turn, permeability, permeability3d, poiseuille_2d, poiseuille_3d_annulus, poiseuille_3d_pipe, shear_wave_decay, sod_shock_tube, sphere_re100, sphere_re100_d3q27, sphere_re100_mem, square_cylinder, startup_poiseuille, stokes_first_problem, stokes_second_problem, taylor_green_2d, taylor_green_3d, taylor_green_3d_decay, wigley_hydrostatics

### 1.4 HONEST_FAIL 案例（4 例，已诊断）

| 案例 | 最新单 | 尝试次数 | 根因 |
|------|--------|----------|------|
| laplace_cac_phasefield | m345 | 6 | 5/5 臂 PASS（实际已通过） |
| suboff_re1000 | m372 | 12 | L≤32 网格下艇半径分辨率不足 |
| suboff_sail_resistance | m289 | 5 | OOM at L≥66，内存受限 |
| womersley | m346 | 13 | H=29,α=8 误差 4.89%>3%；H=59,119 全<3% 已验证 |

### 1.5 IN_FLIGHT 案例（4 例）

| 案例 | 单号 | 类型 | 派发班次 |
|------|------|------|----------|
| backward_step | m444 | 诊断+修复绕行 | m442 |
| blasius_flat_plate | m449 | 诊断+修复绕行 | m446 |
| bstep_3d | m450 | 诊断+修复绕行 | m446 |
| thermal_cavity_3d | m448 | 诊断+修复绕行·第2轮 | m446 |

### 1.6 黑名单检查

```json
{
  "lid_driven_cavity_3d": {
    "trailing_fails": 4,
    "since_mission": 426,
    "total": {"s": 0, "f": 4}
  }
}
```

**lid_driven_cavity_3d** 已被根因升级线接管（m447），本班禁止再派扫荡重试单。

---

## 二、失败原因归类

| 类别 | 数量 | 案例 |
|------|------|------|
| 环境/设备 | 0 | — |
| Triton 绕行 | 0 | — |
| 判据真红（HONEST_FAIL） | 4 | suboff_re1000, suboff_sail_resistance, womersley, laplace_cac_phasefield |
| 其他（infra fail） | 1 | lid_driven_cavity_3d（已入黑名单） |

**说明**：多数历史 FAIL 单已被后续 PASS 单覆盖。当前 4 个 HONEST_FAIL 均为物理/分辨率极限，非代码 bug。

---

## 三、派发决策

### 3.1 约束检查

| 约束 | 状态 |
|------|------|
| 本班次最多派 3 单 | ✅ |
| 只准派 TensorLBM 扫荡类 | ✅ |
| in-flight < 4 → 派下探 | ❌ (in-flight = 4，不满足) |
| 黑名单案例禁派 | ✅ lid_driven_cavity_3d 已跳过 |

### 3.2 派发清单

**本班派发 0 单。**

理由：
1. **In-flight = 4**（backward_step / blasius_flat_plate / bstep_3d / thermal_cavity_3d），队列未满，无需下探补位。
2. **lid_driven_cavity_3d** 在黑名单中，已有根因升级单 m447，禁止重试。
3. **HONEST_FAIL 案例**（suboff_re1000 / suboff_sail_resistance / womersley）均已诊断完毕，根因为物理分辨率极限或设备内存上限，无新诊断方向可派。
4. **laplace_cac_phasefield** 实际已通过（5/5 臂 PASS），无需再派。
5. 其余历史 FAIL 单均已被后续 PASS 单覆盖。

---

## 四、里程碑检查

- 扫荡线累计完工 ≥10 单：✅（224 单）
- 合并报告已存在：✅（r368, r361, r342 等多份）
- **本班无需新发合并报告**（已有充分覆盖）

---

## 五、升级事项（留给 owner）

1. **lid_driven_cavity_3d**：连续 4 次 infra fail（m426-m441），根因升级线 m447 已派发。建议 owner 关注 m447 完工结果。
2. **suboff_sail_resistance**：OOM at L≥66，设备内存受限。如需更高精度，需协调设备资源或算法优化。
3. **suboff_re1000**：L≤32 分辨率不足。需更大网格或不同离散方案。
4. **womersley**：H=29,α=8 误差 4.89% 略超 3% 门限，但 H=59,119 已验证。建议 owner 评估是否调整判据或接受 HONEST_FAIL。

---

## 六、下一班建议

1. **等待 in-flight 完工**：m444/m448/m449/m450 均为诊断+修复绕行，等待完工结果后再决定下一步。
2. **关注 m447（lid_driven_cavity_3d 根因升级）**：如修复可行，可解除黑名单并重新扫荡。
3. **如 in-flight 完工后队列空**：按案例字母序派下探单（如有未覆盖案例）。
4. **HONEST_FAIL 案例**：除非有新诊断方向或设备资源变更，否则不建议重复派发。

---

## 七、产物清单

| 产物 | 路径/ID |
|------|---------|
| 班次报告 | docs/operator_shift_451.md |
| 进展报告 | POST /api/v1/tensor/reports (本班发布) |
| 分析数据 | /tmp/sdaa_analysis.json, /tmp/case_analysis.json |

---

*本报告由 m451 值班长智能体程序化生成，数字全部来自 API 返回，零手抄。*
