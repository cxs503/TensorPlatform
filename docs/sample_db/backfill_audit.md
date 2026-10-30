# 回填审计 Backfill Audit

> 生成时间: 2026-10-30 07:10:00
> 扫描路径: /srv/tensor/agent/work/m*/artifacts|runs|scripts/sdaa_*.json
> 统计方式: 脚本自动扫描，零手抄

## 总览

| 指标 | 数值 |
|------|------|
| 工件文件总数 | 90 |
| 可解析文件 | 90 |
| 解析失败 | 0 |
| **可抽取样本总数** | **125** |
| 涉及任务数 (mission_id) | 81 |

## 样本数计算规则

- 每个 JSON 文件 = 至少 1 个样本
- 含 `tiers` 数组: 每个 tier = 额外 1 个样本
- 含多网格 `results` (如 "640"/"960"): 每个额外网格 = 额外 1 个样本
- 含 `sweep_results` 数组: 每个元素 = 额外 1 个样本
- 含 `runs` 数组: 每个元素 = 额外 1 个样本

## 学科分布

| 学科 | 文件数 | 占比 |
|------|--------|------|
| fluid | 90 | 100.0% |

## 工况类型分布 (Top 15)

| 工况类型 | 文件数 |
|----------|--------|
| unknown | 24 |
| suboff | 10 |
| sphere | 9 |
| cavity | 8 |
| poiseuille | 8 |
| couette | 5 |
| womersley | 5 |
| cylinder | 3 |
| taylor | 3 |
| B18 | 2 |
| thermal | 2 |
| stokes | 2 |
| aircraft | 1 |
| SDAA | 1 |
| permeability3d | 1 |

## 判定分布

| 判定 | 文件数 |
|------|--------|
| UNKNOWN | 48 |
| FAIL | 19 |
| PASS | 16 |
| HONEST_FAIL | 6 |
| IN_PROGRESS | 1 |

## 设备分布

| 设备 | 文件数 |
|------|--------|
| sdaa | 11 |
| sdaa:0 | 8 |
| {'available': True, 'type': 's | 3 |
| cpu | 3 |
| {'torch.sdaa.get_device_name(0 | 2 |
| {'name': '/dev/tcaicard1', 'to | 1 |
| {'device_name': '/dev/tcaicard | 1 |
| {'name': '/dev/tcaicard13', 's | 1 |
| {'name': '/dev/accelerator-node-a', 'c | 1 |
| cuda:0 (NVIDIA GPU, from archi | 1 |

## 编译模式分布

| 模式 | 文件数 |
|------|--------|
| eager | 5 |
| default | 2 |
| eager (pure torch, no triton,  | 1 |
| eager (SDAA teco-inductor fall | 1 |
| eager (SDAA torch.compile know | 1 |

## 解析失败文件

无解析失败文件。

## 详细样本清单

| # | 文件路径 | mission_id | 工况 | 样本数 | 学科 | 判定 | 有参考值 | 有门限 |
|---|----------|-----------|------|--------|------|------|----------|--------|
| 1 | /m10/artifacts/sdaa_couette_result.json | 10 | couette_2d_sdaa | 1 | fluid | UNKNOWN |  |  |
| 2 | /m109/artifacts/sdaa_suboff_sail_resistance_v3.json | 109 | suboff_sail_resistance SDAA v3 诊断：SDAA 内 | 1 | fluid | UNKNOWN |  |  |
| 3 | /m110/artifacts/sdaa_womersley_v2.json | 110 | womersley_sdaa_sweep_v2 | 21 | fluid | HONEST_FAIL |  | ✓ |
| 4 | /m12/artifacts/sdaa_acoustics.json | 12 | unknown | 1 | fluid | UNKNOWN |  | ✓ |
| 5 | /m13/artifacts/sdaa_aircraft_icing_naca0012_rime.json | 13 | aircraft_icing_naca0012_rime | 1 | fluid | FAIL |  | ✓ |
| 6 | /m14/artifacts/sdaa_cavity.json | 14 | cavity_re100_ghia_sdaa | 1 | fluid | UNKNOWN | ✓ | ✓ |
| 7 | /m15/artifacts/sdaa_cavity_3d_full.json | 15 | cavity_3d_full | 1 | fluid | UNKNOWN |  | ✓ |
| 8 | /m16/artifacts/sdaa_cavity_natural_convection.json | 16 | cavity_natural_convection_sdaa | 1 | fluid | UNKNOWN | ✓ | ✓ |
| 9 | /m17/artifacts/sdaa_couette_2d.json | 17 | couette_2d | 1 | fluid | FAIL |  | ✓ |
| 10 | /m172/artifacts/sdaa_suboff_re1000.json | 172 | suboff_re1000 | 1 | fluid | FAIL |  | ✓ |
| 11 | /m172/artifacts/sdaa_suboff_re1000_L32.json | 172 | suboff_re1000 | 1 | fluid | FAIL |  | ✓ |
| 12 | /m172/artifacts/sdaa_suboff_re1000_L40.json | 172 | suboff_re1000 | 1 | fluid | FAIL |  | ✓ |
| 13 | /m174/artifacts/sdaa_sphere_re100_mem_v2_criteria.json | 174 | sphere_re100_mem | 1 | fluid | UNKNOWN | ✓ |  |
| 14 | /m176/artifacts/sdaa_laplace_droplet_diagnostic.json | 176 | unknown | 1 | fluid | UNKNOWN | ✓ |  |
| 15 | /m18/artifacts/sdaa_couette_3d.json | 18 | couette_3d | 1 | fluid | PASS |  | ✓ |
| 16 | /m187/artifacts/sdaa_womersley_h29_diagnosis.json | 187 | womersley_sdaa_h29_diagnosis | 1 | fluid | HONEST_FAIL |  | ✓ |
| 17 | /m19/artifacts/sdaa_cylinder.json | 19 | cylinder_family | 1 | fluid | FAIL |  | ✓ |
| 18 | /m198/artifacts/sdaa_sphere_re100_v5_d10_20.json | 198 | unknown | 1 | fluid | UNKNOWN |  |  |
| 19 | /m198/artifacts/sdaa_sphere_re100_v5_d10_200.json | 198 | unknown | 1 | fluid | UNKNOWN |  |  |
| 20 | /m198/artifacts/sdaa_sphere_re100_v5_d10_80.json | 198 | unknown | 1 | fluid | UNKNOWN |  |  |
| 21 | /m198/artifacts/sdaa_sphere_re100_v5_diagnostic.json | 198 | unknown | 1 | fluid | UNKNOWN |  |  |
| 22 | /m20/artifacts/sdaa_cylinder_3d.json | 20 | cylinder_3d_re40 | 1 | fluid | FAIL | ✓ | ✓ |
| 23 | /m202/artifacts/sdaa_sphere_re100_mem_v3_criteria.json | 202 | sphere_re100_mem | 1 | fluid | FAIL | ✓ |  |
| 24 | /m204/artifacts/sdaa_cavity_natural_convection.json | 204 | cavity_natural_convection | 1 | fluid | UNKNOWN |  |  |
| 25 | /m205/artifacts/sdaa_sphere_re100.json | 205 | sphere_re100 | 3 | fluid | UNKNOWN | ✓ |  |
| 26 | /m206/artifacts/sdaa_suboff_re1000.json | 206 | suboff_re1000 | 1 | fluid | PASS |  |  |
| 27 | /m208/artifacts/sdaa_cavity_natural_convection_v4.json | 208 | cavity_natural_convection | 1 | fluid | PASS | ✓ | ✓ |
| 28 | /m209/artifacts/sdaa_sphere_re100_v8.json | 209 | sphere_re100 | 1 | fluid | UNKNOWN |  |  |
| 29 | /m21/artifacts/sdaa_droplet_oscillation.json | 21 | unknown | 1 | fluid | IN_PROGRESS | ✓ | ✓ |
| 30 | /m210/artifacts/sdaa_womersley_v6.json | 210 | unknown | 1 | fluid | UNKNOWN |  |  |
| 31 | /m212/artifacts/sdaa_suboff_re1000_evidence.json | 212 | suboff_re1000 | 1 | fluid | UNKNOWN | ✓ |  |
| 32 | /m213/artifacts/sdaa_acoustics_v2.json | 213 | unknown | 1 | fluid | UNKNOWN |  |  |
| 33 | /m214/artifacts/sdaa_cavity_v2.json | 214 | cavity_family_sdaa_v2 | 1 | fluid | UNKNOWN |  |  |
| 34 | /m216/artifacts/sdaa_cavity_3d_full_v2.json | 216 | cavity_3d_full | 1 | fluid | UNKNOWN |  | ✓ |
| 35 | /m216/artifacts/sdaa_verification_32.json | 216 | unknown | 1 | fluid | UNKNOWN |  |  |
| 36 | /m217/artifacts/sdaa_kovasznay_2d_v2.json | 217 | B18_kovasznay_2d | 1 | fluid | PASS |  | ✓ |
| 37 | /m22/artifacts/sdaa_floating_body_free_decay.json | 22 | unknown | 1 | fluid | FAIL | ✓ |  |
| 38 | /m220/artifacts/sdaa_couette_2d_v2.json | 220 | couette_2d | 1 | fluid | PASS | ✓ | ✓ |
| 39 | /m222/artifacts/sdaa_womersley_v9.json | 222 | womersley | 1 | fluid | UNKNOWN |  | ✓ |
| 40 | /m224/artifacts/sdaa_poiseuille_3d_annulus_v3.json | 224 | poiseuille_3d_annulus | 1 | fluid | PASS |  | ✓ |
| 41 | /m224/artifacts/sdaa_poiseuille_3d_annulus_v3_report.json | 224 | SDAA Round 3: Poiseuille 3D Annulus Stea | 1 | fluid | UNKNOWN |  |  |
| 42 | /m23/artifacts/sdaa_kovasznay_2d.json | 23 | B18_kovasznay_2d_sdaa_sweep | 1 | fluid | PASS |  | ✓ |
| 43 | /m24/artifacts/sdaa_laplace_cac_phasefield.json | 24 | unknown | 1 | fluid | FAIL | ✓ | ✓ |
| 44 | /m26/artifacts/sdaa_laplace_pr_eos.json | 26 | unknown | 1 | fluid | UNKNOWN | ✓ |  |
| 45 | /m268/artifacts/sdaa_couette_3d.json | 268 | couette_3d | 1 | fluid | PASS | ✓ |  |
| 46 | /m269/artifacts/sdaa_cylinder.json | 269 | unknown | 1 | fluid | UNKNOWN |  |  |
| 47 | /m27/artifacts/sdaa_maneuvering_steady_turn.json | 27 | unknown | 1 | fluid | PASS | ✓ | ✓ |
| 48 | /m270/artifacts/sdaa_laplace_pr_eos.json | 270 | unknown | 1 | fluid | UNKNOWN | ✓ |  |
| 49 | /m28/artifacts/sdaa_permeability.json | 28 | unknown | 1 | fluid | FAIL | ✓ | ✓ |
| 50 | /m289/artifacts/sdaa_suboff_sail_resistance_m288.json | 289 | suboff_sail_resistance | 1 | fluid | HONEST_FAIL |  |  |
| 51 | /m289/artifacts/sdaa_suboff_sail_resistance_m289.json | 289 | suboff_sail_resistance | 1 | fluid | HONEST_FAIL |  |  |
| 52 | /m29/artifacts/sdaa_permeability3d.json | 29 | permeability3d | 3 | fluid | FAIL |  | ✓ |
| 53 | /m291/artifacts/sdaa_womersley_m288.json | 291 | womersley_convergence_ladder_m288 | 1 | fluid | UNKNOWN |  |  |
| 54 | /m293/artifacts/sdaa_laplace_cac_phasefield_m292.json | 293 | unknown | 1 | fluid | HONEST_FAIL | ✓ |  |
| 55 | /m294/artifacts/sdaa_thermal_cavity_3d_m292.json | 294 | thermal_cavity_3d | 1 | fluid | HONEST_FAIL |  |  |
| 56 | /m295/artifacts/sdaa_poiseuille_3d_duct_m292.json | 295 | poiseuille_3d_duct | 1 | fluid | UNKNOWN | ✓ |  |
| 57 | /m31/artifacts/sdaa_poiseuille_2d.json | 31 | B13_poiseuille_2d | 1 | fluid | FAIL |  | ✓ |
| 58 | /m32/artifacts/sdaa_poiseuille_3d_annulus.json | 32 | poiseuille_3d_annulus | 1 | fluid | UNKNOWN |  | ✓ |
| 59 | /m323/runs/sdaa_permeability3d_results.json | 323 | unknown | 1 | fluid | UNKNOWN | ✓ |  |
| 60 | /m327/scripts/sdaa_sweep_permeability3d_result.json | 327 | unknown | 1 | fluid | UNKNOWN |  |  |
| 61 | /m33/artifacts/sdaa_poiseuille_3d_duct.json | 33 | unknown | 1 | fluid | UNKNOWN | ✓ | ✓ |
| 62 | /m34/artifacts/sdaa_poiseuille_3d_pipe.json | 34 | poiseuille_3d_pipe | 1 | fluid | PASS |  | ✓ |
| 63 | /m35/artifacts/sdaa_poiseuille_two_phase_cg.json | 35 | poiseuille_two_phase_cg | 1 | fluid | UNKNOWN |  | ✓ |
| 64 | /m39/artifacts/sdaa_shear_wave_decay.json | 39 | unknown | 1 | fluid | PASS | ✓ | ✓ |
| 65 | /m40/artifacts/sdaa_sod_shock_tube.json | 40 | sod_shock_tube_isothermal_d2q9 | 1 | fluid | UNKNOWN | ✓ | ✓ |
| 66 | /m41/artifacts/sdaa_sphere_re100.json | 41 | sphere_re100 SDAA sweep | 3 | fluid | UNKNOWN | ✓ |  |
| 67 | /m42/artifacts/sdaa_sphere_re100_d3q27.json | 42 | sphere_re100_d3q27 | 3 | fluid | UNKNOWN | ✓ |  |
| 68 | /m46/artifacts/sdaa_sphere_re100_mem.json | 46 | sphere_re100_mem | 1 | fluid | FAIL | ✓ | ✓ |
| 69 | /m47/artifacts/sdaa_sphere_re200.json | 47 | sphere_re200 | 1 | fluid | FAIL | ✓ |  |
| 70 | /m48/artifacts/sdaa_square_cylinder.json | 48 | square_cylinder_re100_free_stream_vortex | 1 | fluid | PASS | ✓ | ✓ |
| 71 | /m49/artifacts/sdaa_startup_poiseuille.json | 49 | startup_poiseuille | 1 | fluid | PASS |  | ✓ |
| 72 | /m50/artifacts/sdaa_stokes_first_problem.json | 50 | stokes_first_problem | 1 | fluid | PASS |  | ✓ |
| 73 | /m51/artifacts/sdaa_cavity_natural_convection.json | 51 | cavity_natural_convection | 1 | fluid | UNKNOWN | ✓ | ✓ |
| 74 | /m52/artifacts/sdaa_laplace_droplet.json | 52 | unknown | 1 | fluid | FAIL | ✓ | ✓ |
| 75 | /m53/artifacts/sdaa_cylinder_3d_diag.json | 53 | cylinder_3d_re40 | 1 | fluid | UNKNOWN | ✓ |  |
| 76 | /m60/artifacts/sdaa_stokes_second_problem.json | 60 | stokes_second_problem | 1 | fluid | UNKNOWN |  | ✓ |
| 77 | /m61/artifacts/sdaa_stokes_sphere_dg.json | 61 | benchmarks/verified/stokes_sphere_dg | 2 | fluid | UNKNOWN |  |  |
| 78 | /m62/artifacts/sdaa_suboff_re1000.json | 62 | suboff_re1000 | 1 | fluid | FAIL | ✓ |  |
| 79 | /m63/artifacts/sdaa_suboff_sail_resistance.json | 63 | suboff_sail_resistance | 1 | fluid | UNKNOWN |  | ✓ |
| 80 | /m64/artifacts/sdaa_taylor_couette.json | 64 | taylor_couette | 1 | fluid | FAIL |  |  |
| 81 | /m65/artifacts/sdaa_taylor_green_2d.json | 65 | taylor_green_2d | 1 | fluid | UNKNOWN |  | ✓ |
| 82 | /m67/artifacts/sdaa_taylor_green_3d.json | 67 | taylor_green_3d | 1 | fluid | UNKNOWN | ✓ |  |
| 83 | /m68/artifacts/sdaa_thermal_cavity.json | 68 | unknown | 1 | fluid | UNKNOWN | ✓ | ✓ |
| 84 | /m69/artifacts/sdaa_thermal_cavity_3d.json | 69 | thermal_cavity_3d | 1 | fluid | PASS |  |  |
| 85 | /m70/artifacts/sdaa_wigley_hydrostatics.json | 70 | wigley_hydrostatics | 2 | fluid | UNKNOWN |  | ✓ |
| 86 | /m71/artifacts/sdaa_womersley.json | 71 | womersley | 1 | fluid | FAIL |  |  |
| 87 | /m73/artifacts/sdaa_poiseuille_2d.json | 73 | poiseuille_2d | 1 | fluid | UNKNOWN | ✓ |  |
| 88 | /m74/artifacts/sdaa_poiseuille_3d_pipe.json | 74 | poiseuille_3d_pipe | 1 | fluid | PASS |  | ✓ |
| 89 | /m74/artifacts/sdaa_poiseuille_3d_pipe_case.json | 74 | poiseuille_3d_pipe | 1 | fluid | UNKNOWN |  |  |
| 90 | /m75/artifacts/sdaa_sphere_re100.json | 75 | sphere_re100 | 6 | fluid | UNKNOWN |  |  |

## 结论

- **当前可回填样本上界: 125 条**
- 距离指南目标 10,000 条还差 9875 条
- 需通过以下方式扩充:
  1. 批量重跑历史工况 (参数扫描)
  2. 新增工况覆盖更多船型/Re 范围
  3. 从验证报告 JSON 中提取额外样本
  4. 接入外部基准数据库 (ITTC, DTMB 等)
