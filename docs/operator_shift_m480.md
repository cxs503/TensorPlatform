# 值班长班次 m480 分析报告

## 一、任务统计

| 状态 | 数量 |
|------|------|
| 成功 (succeeded) | 104 |
| 失败 (failed) | 40 |
| 运行中 (running) | 1 |
| **总计** | **145** |

## 二、扫荡线任务状态统计

### 2.1 SDAA 扫荡线任务状态

| 状态 | 数量 |
|------|------|
| succeeded | 104 |
| failed | 40 |
| running | 1 |

### 2.2 失败原因归类

| 失败原因 | 数量 |
|----------|------|
| 工作树无任何改动，不能报成功 | 29 |
| 模型未调用 finish | 6 |
| 模型未调用 finish（终局: llm_error） | 2 |
| 模型未调用 finish（终局: wall_exhausted） | 1 |
| unknown | 2 |

## 三、案例分析

### 3.1 PASS 案例统计

| 案例名 | 状态 |
|--------|------|
| acoustics | PASS |
| aircraft_icing_naca0012_rime | PASS |
| backward_step | PASS |
| blasius_flat_plate | PASS |
| bstep_3d | PASS |
| capillary_invasion_washburn | PASS |
| channel_turb_3d | PASS |
| collision_kernels_3d_tg | PASS |
| couette_3d | PASS |
| cylinder_3d | PASS |
| dtmb5415_resistance | PASS |
| kcs_resistance | PASS |
| laplace_cac_phasefield | PASS |
| laplace_droplet | PASS |
| maneuvering_steady_turn | PASS |
| permeability | PASS |
| permeability3d | PASS |
| poiseuille_2d | PASS |
| poiseuille_3d_duct | PASS |
| poiseuille_3d_pipe | PASS |
| sod_shock_tube | PASS |
| sphere_re100 | PASS |
| sphere_re100_mem | PASS |
| startup_poiseuille | PASS |
| stokes_second_problem | PASS |
| suboff_re1000 | PASS |
| thermal_cavity | PASS |
| thermal_cavity_3d | PASS |
| womersley | PASS |

### 3.2 FAIL 案例详情

| Mission ID | 案例 | 状态 | 失败原因 |
|------------|------|------|----------|
| 478 | bstep_3d | failed | 工作树无任何改动，不能报成功 |
| 474 | couette_3d | failed | 工作树无任何改动，不能报成功 |
| 473 | aircraft_icing_naca0012_rime | failed | 模型未调用 finish（终局: llm_error） |
| 466 | bstep_3d | failed | 工作树无任何改动，不能报成功 |
| 463 | PASS/FAIL、失败原因归类 | failed | 模型未调用 finish（终局: wall_exhausted） |
| 457 | channel_turb_3d | failed | 工作树无任何改动，不能报成功 |
| 455 | channel_turb_3d | failed | 工作树无任何改动，不能报成功 |
| 453 | blasius_flat_plate | failed | 工作树无任何改动，不能报成功 |
| 450 | bstep_3d | failed | 模型未调用 finish（终局: llm_error） |
| 445 | thermal_cavity_3d | failed | 工作树无任何改动，不能报成功 |
| 442 | PASS/FAIL、失败原因归类 | failed | 工作树无任何改动，不能报成功 |
| 441 | lid_driven_cavity_3d | failed | 工作树无任何改动，不能报成功 |
| 435 | PASS/FAIL、失败原因归类 | failed | unknown |
| 431 | lid_driven_cavity_3d | failed | 工作树无任何改动，不能报成功 |
| 430 | PASS/FAIL、失败原因归类 | failed | 工作树无任何改动，不能报成功 |
| 429 | lid_driven_cavity_3d | failed | 工作树无任何改动，不能报成功 |
| 426 | lid_driven_cavity_3d | failed | 模型未调用 finish |
| 425 | backward_step | failed | 工作树无任何改动，不能报成功 |
| 417 | PASS/FAIL、失败原因归类 | failed | 工作树无任何改动，不能报成功 |
| 373 | startup_poiseuille | failed | 模型未调用 finish |

## 四、黑名单检查

- **lid_driven_cavity_3d**: 已在黑名单中（4次失败），由根因升级线接管，本班禁止再派扫荡重试单。

## 五、派发清单

本班次派发 **3 单**下探任务：

| Mission ID | 案例 | 类型 |
|------------|------|------|
| 481 | cavity | 下探 |
| 482 | cavity_natural_convection | 下探 |
| 483 | droplet_oscillation | 下探 |

## 六、下一班建议

1. 继续下探未覆盖案例：cylinder_3d, floating_body_free_decay
2. 关注失败案例的根因分析，特别是"工作树无任何改动"类失败
3. 检查 agent 执行能力是否满足复杂案例需求
4. 验证扫荡线累计完工 ≥10 单且尚无合并报告的情况

## 七、升级事项

- **系统性问题**：部分案例多次失败但未产生有效证据，建议增加 agent 最小产出约束
- **黑名单机制**：lid_driven_cavity_3d 已由根因升级线接管，本班禁止再派扫荡重试单