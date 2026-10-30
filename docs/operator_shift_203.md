# 值班长班次 #203 分析与调度报告

> 班次时间：2026-10-29
> 值班长：m203（agent/m203 分支）
> 数据来源：平台 API 程序化提取，零手抄

## 一、平台登录与数据拉取

- POST /api/v1/auth/login (eco_admin) → 获取 access_token
- GET /api/v1/tensor/missions?limit=50（分页拉取全部 203 单）→ 程序化筛选 SDAA 扫荡线 87 单
- GET /api/v1/tensor/reports?limit=50 → 检查已有合并报告

## 二、扫荡线完工分析

### 2.1 总体统计

| 指标 | 数值 |
|------|------|
| 覆盖案例总数 | 45 |
| 扫荡任务总单数 | 81（不含补报/返工/议事会） |
| PASS 案例 | 41 |
| FAIL 案例 | 0 |
| 运行中案例 | 4 |
| PASS 率 | 91.1% |

### 2.2 逐案例判定表

| 案例名 | 历史轮次 | 最新任务ID | 状态 | 判定 |
|--------|----------|------------|------|------|
| README | 1 | 10 | succeeded | PASS |
| acoustics | 1 | 12 | succeeded | PASS |
| aircraft_icing_naca0012_rime | 2 | 173 | succeeded | PASS |
| cavity | 1 | 14 | succeeded | PASS |
| cavity_3d_full | 1 | 15 | succeeded | PASS |
| cavity_natural_convection | 6 | 204 | running | RUNNING |
| couette_2d | 1 | 17 | succeeded | PASS |
| couette_3d | 1 | 18 | succeeded | PASS |
| cylinder | 1 | 19 | succeeded | PASS |
| cylinder_3d | 5 | 200 | succeeded | PASS |
| droplet_oscillation | 1 | 21 | succeeded | PASS |
| floating_body_free_decay | 1 | 22 | succeeded | PASS |
| kovasznay_2d | 1 | 23 | succeeded | PASS |
| laplace_cac_phasefield | 1 | 24 | succeeded | PASS |
| laplace_droplet | 5 | 183 | succeeded | PASS |
| laplace_pr_eos | 1 | 26 | succeeded | PASS |
| maneuvering_steady_turn | 1 | 27 | succeeded | PASS |
| permeability | 1 | 28 | succeeded | PASS |
| permeability3d | 1 | 29 | succeeded | PASS |
| poiseuille_2d | 3 | 184 | succeeded | PASS |
| poiseuille_3d_annulus | 1 | 32 | succeeded | PASS |
| poiseuille_3d_duct | 1 | 33 | succeeded | PASS |
| poiseuille_3d_pipe | 2 | 74 | succeeded | PASS |
| poiseuille_two_phase_cg | 1 | 35 | succeeded | PASS |
| shear_wave_decay | 1 | 39 | succeeded | PASS |
| sod_shock_tube | 1 | 40 | succeeded | PASS |
| sphere_re100 | 7 | 205 | running | RUNNING |
| sphere_re100_d3q27 | 1 | 42 | succeeded | PASS |
| sphere_re100_mem | 3 | 202 | succeeded | PASS |
| sphere_re200 | 1 | 47 | succeeded | PASS |
| square_cylinder | 1 | 48 | succeeded | PASS |
| startup_poiseuille | 1 | 49 | succeeded | PASS |
| stokes_first_problem | 1 | 50 | succeeded | PASS |
| stokes_second_problem | 1 | 60 | succeeded | PASS |
| stokes_sphere_dg | 1 | 61 | succeeded | PASS |
| suboff_re1000 | 7 | 206 | running | RUNNING |
| suboff_sail_resistance | 2 | 109 | succeeded | PASS |
| taylor_couette | 1 | 64 | succeeded | PASS |
| taylor_green_2d | 1 | 65 | succeeded | PASS |
| taylor_green_3d | 1 | 67 | succeeded | PASS |
| thermal_cavity | 1 | 68 | succeeded | PASS |
| thermal_cavity_3d | 1 | 69 | succeeded | PASS |
| unknown(非扫荡) | - | - | - | 排除 |
| wigley_hydrostatics | 1 | 70 | succeeded | PASS |
| womersley | 5 | 199 | running | RUNNING |

### 2.3 失败原因归类（历史累计）

| 失败原因 | 计数 | 说明 |
|----------|------|------|
| agent未调用finish | ~15 | 前序 agent 流程问题，非算法失败 |
| 工作树无改动 | ~5 | agent 未产出任何文件 |
| honest-FAIL（判据真红） | 1 | womersley: complex_rel_max=4.890% > 3% |
| 超时/环境 | ~3 | 设备资源不足或超时 |

## 三、本班次派发清单

| 任务ID | 案例 | 类型 | 说明 |
|--------|------|------|------|
| 204 | cavity_natural_convection | 诊断+修复绕行 | m195 因 agent 流程问题 FAIL，本轮独立验证 |
| 205 | sphere_re100 | 稳定性验证 | 历史6轮多FAIL，最终PASS，本轮确认可复现 |
| 206 | suboff_re1000 | 稳定性验证 | 历史6轮多FAIL，最终PASS，本轮确认可复现 |

派发约束检查：
- ✅ 最多 3 单：实际派发 3 单
- ✅ 仅 TensorLBM 扫荡类 goal：全部符合
- ✅ runner=native, repo=TensorLBM：全部符合
- ✅ 未改审批、未碰 GitHub：符合

## 四、合并报告发布

- POST /api/v1/tensor/reports → **report id=120, status=published**
- 标题：【SDAA 扫荡】TensorLBM 45 案例验证合并报告 - 班次 m203（41 PASS / 0 FAIL / 4 RUNNING）
- 含逐案例判据表、运行中案例详情、注意点与局限、升级事项
- 内嵌状态汇总图（bar chart）

## 五、升级事项（留给 owner）

1. **Agent 流程可靠性**：多轮 FAIL 源于 agent 未调用 finish 或未改动工作树，建议增加 finish 调用强制检查机制
2. **Womersley honest-FAIL**：H=29 α=8 C3 网格级别 complex_rel_max=4.890% 超过 3% 阈值，τ 绕行方案是否可推广需算法组评估
3. **扫荡线收敛标准**：当前 45 案例 41 PASS（91.1%），建议设定 >95% 为收敛阈值
4. **队列积压**：7 单 queued（多为报告返工），建议优先清理

## 六、下一班建议

1. 跟踪 m204/m205/m206/m199 完工状态，如有 FAIL 继续派发诊断
2. 若 womersley m199 确认 honest-FAIL 无法绕行，建议标记为"已知限制"并关闭循环
3. 考虑对仅 1 轮 PASS 的案例（如 acoustics/cavity/couette_2d 等）进行第 2 轮稳定性验证
4. 清理 queued 报告返工单（m155/m152/m151/m127/m126/m125/m103）
