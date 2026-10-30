# 值班长班次 m201 — SDAA 扫荡线分析 / 调度交付

| 项 | 值 |
|---|---|
| 班次 | m201（分析与调度，不改业务代码） |
| 分支 | agent/m201 |
| 合并报告 | report id=118（标题含「SDAA 扫荡」，mission_id=201，3 张图内嵌） |
| 分析口径 | 逐单读 `result`（headline+checks+evidence.summary+declared_summary），正则抽取判定词与数值，零手抄 |
| 数据源 | `GET /tensor/missions?limit=50&offset=0..200`（4 页，全量 201 单）+ `GET /tensor/missions/{id}`×75 |
| 本班派发 | 1 单（m202，TensorLBM，runner=native，扫荡类） |

## 1. 扫荡线完工统计（75 单 / 43 案例）

| 判定桶 | mission 数 | 说明 |
|---|---|---|
| PASS | 34 | 判据绿（accepted 且正文无 honest-FAIL） |
| HONEST_FAIL | 18 | 判据红（正文自判 honest-FAIL，含 checks 字段） |
| INFRA_FINISH | 17 | 流程红-A：模型未调用 finish |
| INFRA_GATE | 3 | 流程红-B：门禁判「工作树无任何改动」 |
| RUNNING | 3 | 在途 |
| **合计** | **75** | 43 案例，判据绿 31 / 判据真红 12 |

## 2. 失败原因归类（任务要求四类归口 + 明细）

| 类别 | 单数 | mission id |
|---|---|---|
| 其他/证据不足(WITHHELD) | 4 | m15, m29, m33, m47 |
| 判据真红-多门未过(5/5 或 6/11) | 1 | m19 |
| 判据真红-数值超门限 | 5 | m32, m52, m71, m73, m110 |
| 判据真红-数值超门限(T2 档未达/入口钳制主导) | 2 | m46, m174 |
| 判据真红-未达稳态/收敛性 | 2 | m17, m69 |
| 在途(running) | 3 | m198, m199, m200 |
| 工具链 Triton 绕行(TRITON_INTERPRET=1) | 1 | m13 |
| 流程-A 未调用 finish | 17 | m16, m25, m31, m34, m41, m62, m108, m145, m172, m178, m179, m180, m186, m188, m190, m191, m192 |
| 流程-B 门禁未识别工作树改动（产物写绝对路径） | 3 | m187, m195, m196 |
| 环境-A 显存不足(OOM)/超时 | 3 | m63, m75, m109 |

四类归口：**环境 3 单**（OOM/超时/eager 性能）、**Triton 绕行 1 单**、**判据真红 10 单**、**其他 27 单**（流程红 20 + WITHHELD 4 + 在途 3）。

## 3. 逐案例判定表（43 案例，字母序）

| # | 案例 | 单数 | 判据结论单 | 判据结论 | 其后流程红 | 在途 | 案例结论 | 红单证据 |
|---|---|---|---|---|---|---|---|---|
| 1 | acoustics | 1 | m12 | PASS | - | - | 判据绿 | - |
| 2 | aircraft_icing_naca0012_rime | 2 | m173 | PASS | - | - | 判据绿 | m13 |
| 3 | cavity | 1 | m14 | PASS | - | - | 判据绿 | - |
| 4 | cavity_3d_full | 1 | m15 | honest-FAIL | - | - | 判据真红 | m15 |
| 5 | cavity_natural_convection | 5 | m182 | PASS | m192, m195 | - | 判据绿 | - |
| 6 | couette_2d | 1 | m17 | honest-FAIL | - | - | 判据真红 | m17 |
| 7 | couette_3d | 1 | m18 | PASS | - | - | 判据绿 | - |
| 8 | cylinder | 1 | m19 | honest-FAIL | - | - | 判据真红 | m19 |
| 9 | cylinder_3d | 5 | m53 | PASS | m188, m196 | m200 | 判据绿 | - |
| 10 | droplet_oscillation | 1 | m21 | PASS | - | - | 判据绿 | - |
| 11 | floating_body_free_decay | 1 | m22 | PASS | - | - | 判据绿 | - |
| 12 | kovasznay_2d | 1 | m23 | PASS | - | - | 判据绿 | - |
| 13 | laplace_cac_phasefield | 1 | m24 | PASS | - | - | 判据绿 | - |
| 14 | laplace_droplet | 5 | m183 | PASS | - | - | 判据绿(3D 档仍红) | m52(4.01%) |
| 15 | laplace_pr_eos | 1 | m26 | PASS | - | - | 判据绿 | - |
| 16 | maneuvering_steady_turn | 1 | m27 | PASS | - | - | 判据绿 | - |
| 17 | permeability | 1 | m28 | PASS | - | - | 判据绿 | - |
| 18 | permeability3d | 1 | m29 | honest-FAIL | - | - | 判据真红 | m29(0.004%) |
| 19 | poiseuille_2d | 3 | m184 | PASS | - | - | 判据绿 | m73(1.2%) |
| 20 | poiseuille_3d_annulus | 1 | m32 | honest-FAIL | - | - | 判据真红 | m32(3.237%) |
| 21 | poiseuille_3d_duct | 1 | m33 | honest-FAIL | - | - | 判据真红 | m33 |
| 22 | poiseuille_3d_pipe | 2 | m74 | PASS | - | - | 判据绿 | - |
| 23 | poiseuille_two_phase_cg | 1 | m35 | PASS | - | - | 判据绿 | - |
| 24 | shear_wave_decay | 1 | m39 | PASS | - | - | 判据绿 | - |
| 25 | sod_shock_tube | 1 | m40 | PASS | - | - | 判据绿 | - |
| 26 | sphere_re100 | 6 | m75 | honest-FAIL | m179, m186, m191 | m198 | 判据真红 | m75 |
| 27 | sphere_re100_d3q27 | 1 | m42 | PASS | - | - | 判据绿 | - |
| 28 | sphere_re100_mem | 2 | m174 | honest-FAIL | - | - | 判据真红 | m46, m174(2.7708%) |
| 29 | sphere_re200 | 1 | m47 | honest-FAIL | - | - | 判据真红 | m47 |
| 30 | square_cylinder | 1 | m48 | PASS | - | - | 判据绿 | - |
| 31 | startup_poiseuille | 1 | m49 | PASS | - | - | 判据绿 | - |
| 32 | stokes_first_problem | 1 | m50 | PASS | - | - | 判据绿 | - |
| 33 | stokes_second_problem | 1 | m60 | PASS | - | - | 判据绿 | - |
| 34 | stokes_sphere_dg | 1 | m61 | PASS | - | - | 判据绿 | - |
| 35 | suboff_re1000 | 6 | m194 | PASS | - | - | 判据绿 | - |
| 36 | suboff_sail_resistance | 2 | m109 | honest-FAIL | - | - | 判据真红 | m63, m109 |
| 37 | taylor_couette | 1 | m64 | PASS | - | - | 判据绿 | - |
| 38 | taylor_green_2d | 1 | m65 | PASS | - | - | 判据绿 | - |
| 39 | taylor_green_3d | 1 | m67 | PASS | - | - | 判据绿 | - |
| 40 | thermal_cavity | 1 | m68 | PASS | - | - | 判据绿 | - |
| 41 | thermal_cavity_3d | 1 | m69 | honest-FAIL | - | - | 判据真红 | m69(0.1%) |
| 42 | wigley_hydrostatics | 1 | m70 | PASS | - | - | 判据绿 | - |
| 43 | womersley | 5 | m110 | honest-FAIL | m180, m187 | m199 | 判据真红 | m71(4.89%), m110(4.89%) |

## 4. 关键量误差 vs 门限（14 项，零手抄）

| mission | 案例 | 关键量 | 数值 | 门限 | 判定 | 是否<3% |
|---|---|---|---|---|---|---|
| m48 | square_cylinder | Cd err (square_cylinder Re=100) | +2.84% | 3% | PASS | 是 |
| m48 | square_cylinder | St err (square_cylinder Re=100) | +0.53% | 3% | PASS | 是 |
| m52 | laplace_droplet | intercept_frac 2D | +0.65% | 3% | PASS | 是 |
| m52 | laplace_droplet | intercept_frac 3D | +4.01% | 3% | FAIL | 否 |
| m70 | wigley_hydrostatics | max|err| (wigley) | +2.246% | 3% | PASS | 是 |
| m110 | womersley | complex_rel_max (womersley H=29 a=8) | +4.89% | 3% | FAIL | 否 |
| m73 | poiseuille_2d | err (poiseuille_2d) | +1.2% | 1% | FAIL | 不适用(案例门限 1%) |
| m174 | sphere_re100_mem | Cd err T1 D=40 (sphere_re100_mem) | +2.771% | 3% | PASS | 是 |
| m176 | laplace_droplet | intercept_fraction 3D (laplace_droplet) | +4.01% | 3% | FAIL | 否 |
| m194 | suboff_re1000 | Cd_tot err (suboff_re1000) | -1.15% | 3% | PASS | 是 |
| m196 | cylinder_3d | Cd err (cylinder_3d, declared) | +2.46% | 3% | PASS | 是 |
| m182 | cavity_natural_convection | Nu err @N=128 (cavity_natural_convection) | +1.36% | 3% | PASS | 是 |
| m28 | permeability | SDAA vs CUDA 偏差 (permeability) | +0.0884% | 3% | PASS | 是 |
| m24 | laplace_cac_phasefield | per-arm err (laplace_cac_phasefield) | +0.0567% | 3% | PASS | 是 |

**显式结论**：10 项达标(<3%)、4 项不达标——laplace_droplet 3D intercept 4.01%、womersley H=29 α=8 4.890%、poiseuille_3d_annulus 3.237%、poiseuille_2d 1.2%（超该案例 1% 门限，后由 m184 闭环）。WITHHELD 3 案例（permeability3d/poiseuille_3d_duct/sphere_re200）API 文本无超限数值，不臆断。

## 5. 本班次派发清单（硬上限 3，实际 1）

| # | mission | repo/runner | 案例 | goal 类型 | 失败证据（写入 goal） | 状态 |
|---|---|---|---|---|---|---|
| 1 | 202 | TensorLBM / native | sphere_re100_mem | 诊断+修复绕行 第3轮（判据真红攻坚） | m46/m174 两轮 honest-FAIL；T1(D=40) Cd=1.121980 err=+2.7708%，T2 档未达判据；根因指向入口 Dirichlet 钳制 | queued |

**未用满 3 单的理由**：派发后 in-flight = 3 running(m198/m199/m200) + 1 queued(m202) = 4，已达阈值（in-flight<4 才继续派），故停在 1 单；额度留待下一班顶上。

**下一班派发队列（案例字母序，尚无第 2 轮）**：acoustics → cavity → cavity_3d_full → couette_2d → couette_3d → cylinder → droplet_oscillation → floating_body_free_decay → …

## 6. 里程碑

- 扫荡线累计完工 ≥10 单条件早已满足；历史合并报告已有 8 个版本（m167/m171/m175/m177/m185/m189/m193/m197 及 report 108/106/99/98/76/70）。
- 本班次发布 **report id=118**（标题「【SDAA 扫荡】m201 值班长合并分析报告 — 43 案例 判据绿 31 / 判据真红 12；75 单 PASS 34 / honest-FAIL 18 / 流程红 20 / 在途 3」），含 3 张图（判定分布 / 案例×mission 时序 / 误差-门限）与逐案例判据表。
- 图件 PNG 亦落盘：`artifacts/m201/fig1_verdict_distribution.png`、`fig2_case_timeline.png`、`fig3_error_vs_gate.png`。

## 7. 下一班建议

1. 先收 m198(sphere_re100)/m199(womersley)/m200(cylinder_3d)/m202(sphere_re100_mem) 结果；若仍是「未调用 finish / 工作树无改动」，**禁止再派同一 goal**（m62→m108→m145→m172→m190、m41→m179→m186→m191 已证明同 goal 复现同一红），改派 m194 式「极简验证+合规落盘」。
2. m202 若判据仍红，按 DEFECT-3 升级为算法/边界处理改造（半步反弹 vs 全步松弛入口、D 阶梯到 160），不要继续重试同一脚本。
3. 刷新合并报告的条件：完工数变化 ≥3 单或任一在途单转绿；避免每班一版导致口径漂移。
4. 优先补证 WITHHELD 3 案例（permeability3d / poiseuille_3d_duct / sphere_re200）的逐门数值，否则线级 PASS 率只能是区间估计。
5. 派发前先查 in-flight：若 ≥4，只用 FAIL 优先额度，不做下探。

## 8. 升级事项（系统性，留给 owner）

1. **[DEFECT-1｜门禁与判据正交]** `accepted=true` 与判据结论无关：本线 18 单 succeeded/`accepted=true` 却在正文自判 honest-FAIL。历史报告 115 记「38 PASS / 2 honest-FAIL」，本次读全 `checks` 字段后为「34 PASS / 18 honest-FAIL」——同一批数据、不同字段口径。建议 result 增加结构化 `criteria[]`（案例/门名/数值/门限/verdict），并由它决定 accepted。
2. **[DEFECT-2｜工作树比对]** m187/m195/m196 三单 `changed_files` 全为 `/srv/tensor/agent/work/mNNN/...` 绝对路径，gate 判「工作树无任何改动」→ failed；其中 m196 声明 `Cd=1.5370 (+2.46%)` 已达判据。建议 gate 路径归一化，或在派发模板里强制相对路径。
3. **[DEFECT-3｜重复派发]** 同 case 同 goal 连续重派 5 轮 100% 复现红（suboff_re1000/ sphere_re100/ cavity_natural_convection）。建议同 goal 哈希去重 + 两轮失败后强制变更策略才允许再派。
4. **[DEFECT-4｜headline 截断]** `result.headline`、`evidence.summary.headline` 均截断在 180 字符，判据表无法从 API 复核，直接导致本报告 3 案例 WITHHELD。
5. **[DEFECT-5｜图件不可复核]** `POST /tensor/reports` 接受 `figures[].png_base64`，但 `GET /tensor/reports/118`（含 `?include_figures=true`）返回的 `png_base64` 长度为 0，仅有 name/caption → 内嵌图件无法从 API 侧验证（历史「图件未实际嵌入」返工单同源）。建议回传 sha256/尺寸或独立 `GET /tensor/reports/{id}/figures/{idx}`。
6. **[环境]** suboff_sail_resistance 冻结阶梯 L∈{90,100,110} 在 14.94 GiB 卡上全 OOM（m63/m109）、sphere_re100 m75 OOM+timeout：属设备上限，不是算法错误，建议线内单列「设备不可达」类别并停止重试派发。

