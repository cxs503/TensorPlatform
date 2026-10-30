| 案例 | 累计单数 | 失败单数 | 最新单 | 最新判据 | 判据来源 | 证据片段 | 设备 |
|---|---|---|---|---|---|---|---|
| acoustics | 2 | 0 | 213 | PASS | A@report127 | 结论**：SDAA 设备复跑 D2Q9 平面声波基准，两冻结判据均 **PASS | /dev/tcaicard12 |
| aircraft_icing_naca0012_rime | 2 | 0 | 173 | PASS | C-weak | icing_naca0012_rime_v2.py → 11/11 gates PASS; artifacts/ 下5个JSON | /dev/tcaicard24 |
| cavity | 3 | 2 | 219 | WITHHELD | no-token | 未完成：模型未调用 finish | - |
| cavity_3d_full | 2 | 0 | 216 | PASS | A | all_pass=true | /dev/tcaicard5 |
| cavity_natural_convection | 7 | 4 | 208 | FAIL | A@report125 | 结论**: m192/m195/m204 连续 3 轮 FAIL | - |
| couette_2d | 2 | 0 | 220 | PASS | A@report132 | 结论**：所有关键量误差均 **<3%**，判定为 **PASS | /dev/tcaicard10 |
| couette_3d | 1 | 0 | 18 | PASS | A | all_pass=true | - |
| cylinder | 2 | 0 | 226 | FAIL | A@report140 | 结论**：粗网格（D=10-20）确实导致FAIL | /dev/tcaicard20 |
| cylinder_3d | 5 | 2 | 200 | PASS | B | 验证通过 | /dev/tcaicard0 |
| droplet_oscillation | 3 | 1 | 238 | PASS | C-weak | esult.json validation: R128 err=-2.649% PASS, R160 err=-2.404% P | /dev/tcaicard0,/dev/tcaicard1 |
| floating_body_free_decay | 3 | 1 | 239 | PASS | A | verdict.pass=True | /dev/tcaicard0 |
| kovasznay_2d | 2 | 0 | 217 | PASS | A | 判定：PASS | /dev/tcaicard9 |
| laplace_cac_phasefield | 1 | 0 | 24 | FAIL | A | overall FAIL | - |
| laplace_droplet | 5 | 2 | 183 | FAIL | A@report110 | 结论部分已如实记为 FAIL | /dev/accelerator-node-a |
| laplace_pr_eos | 1 | 0 | 26 | PASS | B | All 6 gates PASS | /dev/tcaicard23 |
| maneuvering_steady_turn | 1 | 0 | 27 | PASS | A@report54 | 结论：8/8 PASS | - |
| permeability | 1 | 0 | 28 | PASS | B | 全部 PASS | /dev/tcaicard6 |
| permeability3d | 1 | 0 | 29 | FAIL | C-weak | 抄；N=64 结果与原始 CUDA 结果对比差异 <0.004%；honest-FAIL 如实记录 | /dev/tcaicard8 |
| poiseuille_2d | 3 | 1 | 184 | PASS | B | 验证通过 | /dev/tcaicard6 |
| poiseuille_3d_annulus | 2 | 0 | 224 | PASS | B | All 6 gates PASS | /dev/tcaicard11 |
| poiseuille_3d_duct | 1 | 0 | 33 | PASS | B@report58 | 判据均通过 | /dev/tcaicard15 |
| poiseuille_3d_pipe | 2 | 1 | 74 | PASS | A | 结论：`poiseuille_3d_pipe` 在 SDAA 上 **PASS | /dev/tcaicard26 |
| poiseuille_two_phase_cg | 1 | 0 | 35 | FAIL | A@report57 | VERDICT: FAIL | - |
| shear_wave_decay | 1 | 0 | 39 | PASS | A@report71 | 结论：PASS | - |
| sod_shock_tube | 1 | 0 | 40 | PASS | A@report66 | 结论：全部 10 项门控 PASS | - |
| sphere_re100 | 8 | 5 | 209 | PASS | C-weak | 调用finish"失败，**非算法/物理失败**，是流程断点。m198是唯一稳定PASS轮次。 | - |
| sphere_re100_d3q27 | 2 | 0 | 232 | PASS | A@report144 | 判定：PASS | - |
| sphere_re100_mem | 3 | 0 | 202 | FAIL | A | 诊断结论：honest-FAIL | - |
| sphere_re200 | 2 | 0 | 223 | WITHHELD | no-token | ## SDAA sphere_re200 Round 3 — Statistical Convergence Diagnosis Compl | - |
| square_cylinder | 1 | 0 | 48 | PASS | A@report80 | 结论**: **PASS | /dev/tcaicard18 |
| startup_poiseuille | 1 | 0 | 49 | PASS | B | 全部 PASS | - |
| stokes_first_problem | 1 | 0 | 50 | PASS | A | all_pass=True | /dev/tcaicard25 |
| stokes_second_problem | 2 | 1 | 233 | PASS | A@report145 | VERDICT: PASS | - |
| stokes_sphere_dg | 2 | 0 | 234 | PASS | A@report146 | all_pass: true | - |
| suboff_re1000 | 8 | 6 | 212 | PASS | B | 主判据PASS | - |
| suboff_sail_resistance | 2 | 0 | 109 | FAIL | A | 判定：HONEST-FAIL | /dev/tcaicard25 |
| taylor_couette | 1 | 0 | 64 | WITHHELD | no-token | ## SDAA 扫荡完成：taylor_couette 案例  ### 完成的工作  1. **读取案例文档**：详细阅读了 benchma | - |
| taylor_green_2d | 1 | 0 | 65 | PASS | B | 全部 PASS | - |
| taylor_green_3d | 1 | 0 | 67 | PASS | C-weak | device_evidence present, all 3 criteria PASS, monotone convergen | - |
| thermal_cavity | 1 | 0 | 68 | FAIL | B | 诚实报告 FAIL | - |
| thermal_cavity_3d | 1 | 0 | 69 | WITHHELD | B | NOT_RUN | /dev/tcaicard12 |
| wigley_hydrostatics | 1 | 0 | 70 | PASS | B | all 7 gate criteria PASS | - |
| womersley | 7 | 3 | 222 | WITHHELD | no-token | ## SDAA Womersley v9 诊断+修复绕行 — 完成  ### 基线确认 H=29, α=8, τ=0.80, 标准半格反弹  | /dev/tcaicard23 |
