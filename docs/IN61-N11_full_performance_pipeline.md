# IN61-N11 跨域集成验证：全性能评估流程端到端串联

> 节点：IN61-N11（战役 DAG，物理域/跨域集成）　分支：`agent/m6984`　
> 生成时间：2026-11-08T10:22:17+00:00　提交：`f13b7687c8dc`
> 产物：`artifacts/IN61-N11/integration_report.json`、`artifacts/IN61-N11/fig*.png`、本文件
> 结论：**四项机器判据全部通过**

## 1. 案例介绍（物理背景 / 几何 / 参数）

本节点把 7 个物理域模块串成一条完整的船舶全性能评估流程，验证**跨模块数据流、接口契约、
端到端可用性**，而不是单点精度：

| 模块 | 物理内容 | 关键输入 | 关键输出 |
|---|---|---|---|
| N04 导航 | 大圆/恒向线几何、航时 | 起讫经纬、航速 | 航程、航向、ETA |
| N05 风载 | 视风合成、三分量风载、参考点力矩 | 真风、航速、受风面积、形心 | 纵/横向力、艏摇/横倾力矩 |
| N02 污底 | ITTC-57 摩擦线 + ITTC-78 粗糙度补贴 | ks、服役天数、湿表面积 | Cf、ΔCF、摩擦阻力 |
| N01 失速 | 波浪增阻（动量通量渐近）+ 风增阻 + 功率平衡 | 摩擦阻力、功率、海况 | 可达航速、失速%、总阻力 |
| N08 砰击 | 附加质量率冲击压力 + 高斯超越概率 | 升角、波高/周期、σ_v | 峰值压力、砰击概率 |
| N09 拖曳 | 悬链线张力 + 系柱拉力利用率 | 总阻力、拖缆、BP | 顶端张力、利用率、判据 |
| N10 甲板积水 | 积水量 → 自由液面修正 → 有效 GM → 横倾 | 积水体积、舱区几何 | ΔGM、GM_eff、横倾角 |

几何与海况全部为**合成数据**（`config/synthetic_pipeline_config.json`，`SYNTH-A/B/C` 船型、
`SEA-1/2/3` 海况），零真实船资料：避免把任何实船数据混入平台仓，也让"数字来源"可审计。

主要参数（SYNTH-A / B / C）：Lpp = 180 / 120 / 260 m，B = 30 / 20 / 44 m，
湿表面积 6800 / 3300 / 13500 m²，排水量 4.0×10⁴ / 1.2×10⁴ / 1.6×10⁵ t，
可用推进功率 6000 / 2600 / 16000 kW，设计航速 14 / 13 / 15 kn。
海况 SEA-1/2/3：Hs = 1.5 / 4.0 / 6.0 m，Tp = 6.5 / 9.0 / 11.0 s，真风速 8 / 14 / 20 m/s。

## 2. 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**DAG 接线**：17 条边、9 个节点，
其中 Cfg 节点（`CFG-vessel` / `CFG-environment`）负责把配置摊平为契约字段，
**在审计中与"上游模块输出"分开统计**（避免把注入值冒充跨模块数据流）。

1. `CFG-*` → `N04` 导航（航线几何、遭遇航速起始值）；
2. `N04` → `N05` 风载（航速/首向改名接线）与 `N02` 污底（航速→雷诺数）；
3. `N02` 摩擦阻力 + `N05` 纵向风载 + 配置海况 → `N01` 失速（功率平衡求可达航速）；
4. `N01` → `N08` 砰击 / `N09` 拖曳（总阻力）/ `N10` 甲板积水；
5. **反馈边** `N01.achievable_speed → N04.speed`（遭遇速度自洽），欠松弛 ω=0.5 固定点迭代，
   收敛判据为**固定点残差** |V_R(V_fb) − V_fb| < 1e-7 m/s。

**接口契约层**（`contracts.py`）：每个模块声明 `requires/provides` 字段表（名/单位/类型），
可导出 JSON-Schema；接线时逐字段判 `native / adapted（登记换算）/ unit_mismatch / missing`。
单位换算只允许 `ADAPTERS` 白名单（kn↔m/s、h↔s、deg↔rad、N↔kN…），
**未登记换算一律判违约**——不允许"隐式凑量纲"。

**证据等级登记**（`evidence`）：逐字段 REAL / WITHHELD / MOCK / STUB，模块级取最弱项。
物理式优先取可自行推导的形式（球面几何、动量通量、附加质量率、静水力学、悬链线解析解），
经验系数一律外置到 config 并标 WITHHELD。

**工具链**：纯 Python 标准库（无 numpy/scipy 依赖）+ matplotlib 出图 + pytest 用例；
一条命令 `python3 domains/full_performance_pipeline/run_all.py` 复算全部算例与报告。

## 3. 计算结果（程序化注入，无手抄）

| 算例 | 航程 nmi | V_ref m/s | V 可达 m/s | 失速 % | R_total kN | P_req kW | ETA 延误 h | 砰击 kPa | GM_eff m | 横倾 ° | 拖曳利用率/判据 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SYNTH-A_SEA-1_calm | 388.5 | 7.20 | 6.39 | 11.3 | 582.0 | 6000 | 3.52 | 62.1 | -0.17 | 90.0 | 0.73 / 0 |
| SYNTH-A_SEA-2_storm | 388.5 | 7.20 | 5.49 | 23.7 | 677.2 | 6000 | 8.63 | 230.3 | -0.17 | 90.0 | 0.85 / 0 |
| SYNTH-B_SEA-2 | 350.2 | 6.69 | 5.27 | 21.2 | 286.0 | 2600 | 7.23 | 171.9 | +0.13 | 29.8 | 0.36 / 1 |
| SYNTH-C_SEA-3 | 528.4 | 7.72 | 5.82 | 24.5 | 1868.1 | 16000 | 11.45 | 444.4 | +1.25 | 1.0 | 2.34 / 0 |

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| J1 端到端链路完整 | 全模块跑通、载荷校验通过、固定点收敛 | 17 边；残差 7.87e-08 m/s | PASS |
| J2 数据流延迟 | < 1000 ms | 最坏 20.4 ms（单 stage 最坏 1.31 ms） | PASS |
| J3 接口契约 | 输入 schema 与上游输出全匹配 | 63 字段：wired 58（原生）+ 0（换算）+ config 5；缺失/错配 0 | PASS |
| J4 证据统计 | 报告含 REAL/WITHHELD/MOCK 占比 | 字段 100：REAL 87 / WITHHELD 13 / MOCK 0 / STUB 0（占比 87.0% / 13.0% / 0% / 0%） | PASS |

**证据等级统计**（逐字段 100 项）：

| 等级 | 字段数 | 占比 |
|---|---|---|
| REAL | 87 | 87.0% |
| WITHHELD | 13 | 13.0% |

模块级（取最弱字段）：CFG-vessel=REAL, CFG-environment=REAL, N04-navigation=REAL, N05-wind-load=WITHHELD, N02-fouling=WITHHELD, N01-speed-loss=WITHHELD, N08-slamming=WITHHELD, N09-towing=REAL, N10-deck-water=REAL

**证据门自检**（把 N01 包成 MOCK、N02 包成 STUB，检验门禁是否真的降级）：
MOCK 字段 7、STUB 字段 6；
N01 等级 WITHHELD → MOCK，N02 → STUB；
毒化后流程仍能跑通但结论被门禁否决（失速% 1.0 vs 干净
23.7）→ `gate_ok = True`。

## 4. 结果图

- `artifacts/IN61-N11/fig1_pipeline_dag_latency.png` —— DAG 接线（17 边/1 反馈）与逐模块/端到端时延（最坏 ~20 ms ≪ 1000 ms 判据）
- `artifacts/IN61-N11/fig2_speed_loss_vs_hs.png` —— 三合成船型的非自愿失速随 Hs 变化（κ=0.35；模型为渐近式 → 绝对量级 WITHHELD）
- `artifacts/IN61-N11/fig3_resistance_budget.png` —— 各算例阻力分解：摩擦(N02)/剩余/波浪增阻(N01)/风增阻(N05→N01)
- `artifacts/IN61-N11/fig4_dcf_vs_ks.png` —— ITTC-78 粗糙度补贴随 ks 与 L 变化；点线为解析适用下界 ks_min=(0.64/105)³L
- `artifacts/IN61-N11/fig5_slamming.png` —— 砰击峰值压力 vs 入水速度（升角族）与单周期超越概率 vs σ_v
- `artifacts/IN61-N11/fig6_deck_water.png` —— 甲板积水自由液面对有效 GM 与平衡横倾的影响（全宽 vs 8 m 积水区对照）

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **反馈环路会振荡**：直接迭代（不欠松弛）时 V 在 5.48↔5.50 间以 0.79 的压缩比反复振荡，
   20 次迭代不收敛；引入 ω=0.5 欠松弛后 6~13 次收敛到 1e-7 m/s。契约上把这条边显式标
   `feedback=True`，避免把"迭代量"当"单向数据流"统计。
2. **单位不是靠猜**：全链路统一 SI，`adapted=0`（无隐式换算）；kn→m/s 等换算只有在
   `ADAPTERS` 白名单登记后才允许自动接线，测试另行验证 `adapted` 与 `unit_mismatch` 两条分支。
3. **ITTC-78 式有适用下界**：ΔCF = (105(ks/L)^{1/3} − 0.64)×10⁻³ 在 ks/L < (0.64/105)³
   处为负，实现里按 0 截断并在图上标出下界（L=180 m 时 ks_min ≈ 3.9 µm）——"光滑船体"不是该式的区间。
4. **波浪增阻是渐近模型**：短波反射动量通量渐近 + 长波截断（λ≫L 不反射），
   κ∈[0.20, 0.50] 使绝对量级离散 86%（±43%），**不声明绝对精度**（见第 6 节 honest 项）。
5. **甲板积水在合成配置下直接翻船**：SYNTH-A 甲板 60×24 m 全宽自由液面 ΔGM=1.77 m > GM=1.60 m
   → GM_eff<0，模块输出"倾覆极限 90°"而不是伪角度。若把积水限制在 8 m 宽舱区，ΔGM 仅 0.02 m、
   横倾 <1°——说明该模块对**自由液面宽度**极敏感，这是物理真实而非数值问题。
6. **拖曳张力是下限**：悬链线为静力等高端点解，未含风流、拖缆水动力阻尼与动态张力放大。
7. **N01/N02/N05/N08 模块级为 WITHHELD**：算法真实但缺实验基准（波浪增阻、污损生长律、
   风压系数、砰击峰值系数），只能声明"实现正确"，不能声明"物理量级正确"。

## 6. 误差判定（逐项 vs 基准，显式回答是否 <3%）

判定口径：`analytic`=解析解/恒等式；`literature`=文献值；`self`=标度律自洽；
`judgement`=判据线。共 47 项进入判定，
通过 47 项、未通过 0 项，
最大相对误差 2.55%（仅在精度类对照上统计；Schlichting 平板式 vs ITTC-57 相关线，
本身即模型差异，仍 < 3%）。

| 项 | 模块 | 基准类型 | 计算值 | 基准值 | 相对误差 | <3% |
|---|---|---|---|---|---|---|
| nav_gc_equator | N04-navigation | analytic | 1.11195e+06 | 1.11195e+06 | 0.00e+00 | ✅ |
| nav_gc_meridian | N04-navigation | analytic | 5.00378e+06 | 5.00378e+06 | 0.00e+00 | ✅ |
| nav_gc_antipodal | N04-navigation | analytic | 2.00151e+07 | 2.00151e+07 | 0.00e+00 | ✅ |
| nav_rhumb_eq_gc_meridian | N04-navigation | analytic | 2.2239e+06 | 2.2239e+06 | 0.00e+00 | ✅ |
| nav_rhumb_eq_gc_equator | N04-navigation | analytic | 1.33434e+06 | 1.33434e+06 | 0.00e+00 | ✅ |
| nav_course_east | N04-navigation | analytic | 90 | 90 | 0.00e+00 | ✅ |
| nav_eta_identity | N04-navigation | analytic | 33.3072 | 33.3072 | 0.00e+00 | ✅ |
| cf_ittc57_at_1e9 | N02-fouling | literature | 0.00153061 | 0.001531 | 2.53e-04 | ✅ |
| cf_vs_schlichting | N02-fouling | literature | 0.00153061 | 0.0015706 | 2.55e-02 | ✅ |
| dcf_validity_root | N02-fouling | analytic | 4.529e-05 | 4.529e-05 | 5.98e-16 | ✅ |
| dcf_at_150um_L200 | N02-fouling | reported | 0.000313988 |  | — | （不判） |
| dcf_cube_root_scaling | N02-fouling | self | 0 | 0 | 0.00e+00 | ✅ |
| friction_identity | N02-fouling | analytic | 414053 | 414053 | 0.00e+00 | ✅ |
| spectrum_m0 | N01-speed-loss | analytic | 0.999992 | 1 | 7.81e-06 | ✅ |
| raw_hs_squared | N01-speed-loss | self | 4 | 4 | 0.00e+00 | ✅ |
| raw_kappa_linear | N01-speed-loss | self | 2 | 2 | 0.00e+00 | ✅ |
| raw_heading_cos15 | N01-speed-loss | self | 0.805927 | 0.805927 | 0.00e+00 | ✅ |
| raw_beam_zero | N01-speed-loss | self | 0 | 0 | 0.00e+00 | ✅ |
| raw_quadrature_converged | N01-speed-loss | self | 160556 | 160555 | 7.46e-07 | ✅ |
| power_cubic_vs_bisection | N01-speed-loss | analytic | 7.84828 | 7.84828 | 1.13e-16 | ✅ |
| power_balance_residual | N01-speed-loss | analytic | -2.50356e-16 | 0 | 2.50e-16 | ✅ |
| power_linear_limit | N01-speed-loss | analytic | 11.625 | 11.625 | 0.00e+00 | ✅ |
| wind_apparent_static | N05-wind-load | analytic | 10 | 10 | 0.00e+00 | ✅ |
| wind_apparent_static_dir | N05-wind-load | analytic | 0 | 0 | 0.00e+00 | ✅ |
| wind_apparent_head_on | N05-wind-load | analytic | 15 | 15 | 0.00e+00 | ✅ |
| wind_apparent_cross | N05-wind-load | analytic | 270 | 270 | 0.00e+00 | ✅ |
| wind_parallel_axis | N05-wind-load | analytic | -206916 | -206916 | 1.41e-16 | ✅ |
| wind_heeling_identity | N05-wind-load | analytic | 827664 | 827664 | 0.00e+00 | ✅ |
| wind_dynamic_pressure | N05-wind-load | analytic | 238.794 | 238.794 | 0.00e+00 | ✅ |
| slam_v_squared | N08-slamming | self | 4 | 4 | 0.00e+00 | ✅ |
| slam_deadrise_ratio | N08-slamming | self | 2.14451 | 2.14451 | 2.07e-16 | ✅ |
| slam_deadrise_monotone | N08-slamming | self | 1 | 1 | 0.00e+00 | ✅ |
| slam_flat_plate_out_of_scope | N08-slamming | scope |  |  | — | （不判） |
| slam_prob_zero_threshold | N08-slamming | analytic | 0.5 | 0.5 | 0.00e+00 | ✅ |
| slam_prob_matches_erfc | N08-slamming | analytic | 0.00460239 | 0.00460239 | 0.00e+00 | ✅ |
| catenary_roundtrip | N09-towing | analytic | 400 | 400 | 0.00e+00 | ✅ |
| catenary_sag_vs_ode | N09-towing | analytic | 0.887688 | 0.887688 | 2.17e-12 | ✅ |
| catenary_shallow_limit | N09-towing | self | 675896 | 675900 | 6.57e-06 | ✅ |
| tow_top_tension_identity | N09-towing | analytic | 675927 | 675927 | 0.00e+00 | ✅ |
| fs_inertia_numeric | N10-deck-water | analytic | 69120 | 69119.6 | 6.25e-06 | ✅ |
| fs_dgm_analytic | N10-deck-water | analytic | 1.7712 | 1.7712 | 0.00e+00 | ✅ |
| ponding_depth | N10-deck-water | analytic | 0.222222 | 0.222222 | 0.00e+00 | ✅ |
| heel_small_angle | N10-deck-water | analytic | 0.000113663 | 0.000113663 | 2.38e-16 | ✅ |
| deck_capsize_flag | N10-deck-water | self | 1 | 1 | 0.00e+00 | ✅ |
| e2e_latency[SYNTH-A_SEA-1_calm] | pipeline | judgement | 9.95174 | 1000 | 9.95e-03 | ✅ |
| e2e_latency[SYNTH-A_SEA-2_storm] | pipeline | judgement | 9.69416 | 1000 | 9.69e-03 | ✅ |
| e2e_latency[SYNTH-B_SEA-2] | pipeline | judgement | 8.43087 | 1000 | 8.43e-03 | ✅ |
| e2e_latency[SYNTH-C_SEA-3] | pipeline | judgement | 20.4103 | 1000 | 2.04e-02 | ✅ |
| e2e_latency_worst | pipeline | judgement | 20.4103 | 1000 | 2.04e-02 | ✅ |

**是否 <3%？** —— 凡有解析解/文献值可对照的关键量（导航几何、ITTC-57 摩擦线、ΔCF 适用下界、
功率平衡解析根、视风合成、平行轴力矩、谱面积归一、砰击 V² 标度、悬链线弧长/垂度、自由液面惯性矩、
时延判据）**全部 < 3%**（多数到 1e-12 量级）。

**明确不满足 <3%（无实验基准，honest 声明，不粉饰）**：

| 项 | 模块 | 原因 |
|---|---|---|
| raw_added_resistance_absolute | N01-speed-loss | 波浪增阻为短波渐近模型，κ∈[0.20,0.50] 造成 137619 N 离散（相对 κ=0.35 基准 86%）；本仓无模型试验/CFD 基准 → 绝对量级不声明精度 |
| wind_load_coefficients | N05-wind-load | 风压系数由配置注入（未做风洞/模型试验拟合），纵向/横向力与艏摇力矩的绝对量级不可判精度 |
| fouling_growth_law | N02-fouling | ks(t)=ks0+γ·cov·t 为线性占位律，无实船污损时序标定；ΔCF 对 ks^(1/3) 敏感 → 生长律误差按立方根衰减但仍无基准 |
| slamming_peak_pressure_absolute | N08-slamming | 附加质量率模型压力峰值系数无试验标定；入水速度 +13% 使压力升至 293311 Pa（+28%），说明对速度输入极敏感 → 绝对压力仅级次可信 |
| slamming_sigma_v | N08-slamming | 相对速度幅值比与砰击阈值速度为参数化占位（无耐波性试验/RAO 计算输入） |
| towing_no_current_wind | N09-towing | 拖曳模型未含风流与拖缆水动力阻尼；悬链线为静力等高端点解 → 张力为下限估计 |

## 7. 局限与后续

- 7 个领域模块是从本节点在 `domains/full_performance_pipeline/` **新建实现**的：
  IN61-N01/N02/N04/N05/N08/N09/N10 的上游实现未随本仓（TensorPlatform）落盘，
  因此本节点验证的是"这些契约下的端到端可用性"，**不能**对上游节点的历史证据等级背书。
- 绝对量级要变成 REAL，需要：波浪增阻的模型试验/CFD 标定、污损生长时序数据、
  风压系数风洞拟合、砰击峰值压力试验；四者都已在报告里标为 WITHHELD 并列出所需数据。
- 甲板积水目前是准静态自由液面 + 静平衡横倾，无时域进水/排水与横摇耦合；
  拖曳无动态张力放大；砰击无结构响应（重击屈曲）耦合。
