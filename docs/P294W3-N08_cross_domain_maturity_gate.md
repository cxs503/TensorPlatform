# 跨域一致性验证成熟化门：水动力-结构-稳性-绿色航行耦合接口契约与端到端回归（节点 p294w3-n08）

**Case pack**: `ship_coupling_gate_v1` v1.0.0 · spec sha256 `6d97d602f43f6a4d…` · evaluator sha256 `8cc15c37e38ae673…`

**Overall verdict**: `PASS`

## ① 案例介绍（物理背景 / 几何 / 参数表）

**物理/工程背景。** 船舶总体性能的四个学科在同一条计算链上互为上下游：耐波算出的波浪载荷是稳性 GZ 曲线的修正输入，波浪压力是结构板格应力的外载，污底引起的阻力增量是绿色航行 CII 的输入。任一环节的接口约定（单位、坐标系、采样分辨率、线性化阶数）出错，单学科测试依然全绿，而耦合结果系统性偏掉。本节点作为战役成熟化门，不比单点算法精度，而比**接口契约在组合场景下是否仍与独立复算一致**。

**几何与工况（全部合成，无实测几何）。**

| 用途 | 几何/工况 | 数值 |
|---|---|---|
| C1 水动力→稳性 | 直壁棱柱船体 L×B×T×D | 200 × 32 × 12 × 24 m，KG=11 m（GM=2.11 m）|
| C1 波浪 | 长峰规则波波长/振幅 | λ=300 m，a=0.6 m，相位 0 |
| C1 比较网格 | 接口采样 / 独立复算 | 41 站 + 1 步 Newton / 401 站 + 二分至 1e-10 |
| C2 水动力→结构 | 简支矩形板格 a×b×t | 2.4 × 1.2 × 0.012 m，ν=0.3 |
| C2 载荷 | 静水头 + 指数衰减动压 | 7 m 水头，动压幅值 ρg·1.0·e^(-kz) |
| C3 阻力→绿色航行 | 设计工况 | V=7.7 m/s，L=200 m，T=12 m，Cb=0.80，(1+k)=1.15，C_A=3e-4 |
| C3 污底 | 粗糙度附加系数 | ΔC_F=1.2e-4（中度污底，ΔR/R≈7.6%）|
| E2E 全链路 | 5 个典型船型 | Panamax 散货 / Aframax 油轮 / 支线集装箱 / VLCC / RoPax 渡轮 |

**判据（机器可算，先于动手固定）。** C1 ≤2%，C2 ≤3%，C3 ≤1%，端到端 ≤5%，契约覆盖率 100%。

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**（1）机制层与领域层分离。** 平台侧新增 `src/cross_domain_coupling`（零领域语义）：只做四件事——case pack 的结构校验（未知键=配置错、`SpecError`，绝不悄悄降级成判据失败）、单位/量纲换算登记、接口契约覆盖率（字段完整性 + 量纲可换算 + 求值器已接线）、以及「primary（接口传输律）vs independent（独立复算）」的逐点相对误差与三值裁定（PASS/FAIL/WITHHELD，WITHHELD 绝不抬成 PASS）。领域内容全部外置在 `domains/ship_coupling_gate_v1/`（`evaluators.py` 物理模型 + `spec.json` 契约与容差 + `baseline.json` 冻结基线），平台核心不含任何船型词条、物理常数或容差。

**（2）三条耦合的 primary/independent 配对。**

| 耦合 | primary（接口传输律） | independent（独立复算） | 差异来源 |
|---|---|---|---|
| C1 波浪→GZ | 41 站中点采样 + 浮态平衡**单步** Newton | 401 站解析波面 + 二分收敛 1e-10 | 采样分辨率 + 平衡迭代残差 |
| C2 压力→板格应力 | Navier 双三角级数截断 11 项 | 同级数 199 项（收敛解） | 级数截断误差 |
| C3 污底→CII | 闭式比值律 (1+k)ΔC_F/C_T·(1+κ) | 清洁/污底两端点全链数值复算（ITTC-57 + 形状因子 + 推进效率载荷敏感 κ + SFOC→CO2→CII） | 传输律线性化误差 |

**（3）C1 的物理内核。** 直壁船体被倾斜自由表面 z=(η(x)+ζ)/cosθ − y·tanθ 切割，逐站精确求截面面积与一阶矩（分段线性精确积分，非数值积分），再以「浸没体积 = 排水体积」定浮态 ζ（策略：二分为独立解，单步 Newton 为接口解），最终 GZ = y_B·cosθ − (KG−z_B)·sinθ。

**（4）端到端与基线。** 5 船型走完整链（阻力→污底增量→CII；波浪→GZ 修正；压力→板格应力），管线用 ITTC-57 摩擦线，基线用 **Schoenherr 1957** 摩擦线（两条经典平板线，互不依赖），基线一次性冻结成 `baseline.json` 并钉 sha256，之后每次跑都是对钉桩工件的回归。

**（5）工具链。** 纯 Python + numpy/matplotlib；`python -m src.cross_domain_coupling --spec … --out …` 一条命令产出 JSON；pytest 26 项；全部计算无需集群（单次 <5 s）。

## ③ 计算结果（数字表，程序化注入）

### 判据总表（程序化注入，零手抄）

| criterion | tolerance_pct | observed_max_pct | margin_pct | verdict |
|---|---|---|---|---|
| interface_contract_coverage |  | 100 |  | PASS |
| coupling:C1 | 2 | 0.0102714 | 1.98973 | PASS |
| coupling:C2 | 3 | 2.3196 | 0.680396 | PASS |
| coupling:C3 | 1 | 0.688206 | 0.311794 | PASS |
| end_to_end_regression | 5 | 0.0543882 | 4.94561 | PASS |

### coupling:C1 — wave load -> GZ curve correction (hydrodynamics -> stability)

| i | primary | independent | err_pct | tol_pct |
|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 2 |
| 1 | 0.670521 | 0.670452 | 0.0102714 | 2 |
| 2 | 1.36701 | 1.36687 | 0.0102714 | 2 |
| 3 | 2.11794 | 2.11772 | 0.0102714 | 2 |
| 4 | 2.95723 | 2.95692 | 0.0102714 | 2 |
| 5 | 3.92827 | 3.92787 | 0.0102714 | 2 |
| 6 | 5.08995 | 5.08943 | 0.0102714 | 2 |

### coupling:C2 — wave pressure -> plate panel stress (hydrodynamics -> structures)

| i | primary | independent | err_pct | tol_pct |
|---|---|---|---|---|
| 0 | 384.807 | 393.945 | 2.3196 | 3 |

### coupling:C3 — fouling resistance increment -> CII impact (resistance -> green navigation)

| i | primary | independent | err_pct | tol_pct |
|---|---|---|---|---|
| 0 | 7.57027 | 7.62273 | 0.688206 | 1 |

### end_to_end_regression — 全链路 vs 基线

| metric | pipeline | baseline | error_pct |
|---|---|---|---|
| bulker_panamax.ct_clean | 0.00199472 | 0.00199549 | 0.0385774 |
| bulker_panamax.delta_cii_pct | 12.8315 | 12.8265 | 0.0390425 |
| bulker_panamax.gz_corrected_m | 2.19108 | 2.19108 | 0 |
| bulker_panamax.gz_correction_mm | 1.77438 | 1.77438 | 0 |
| bulker_panamax.panel_stress_mpa | 393.945 | 393.945 | 0 |
| bulker_panamax.rf_over_v | 50.9155 | 50.9352 | 0.0385774 |
| container_feeder.ct_clean | 0.00205838 | 0.00205876 | 0.0187916 |
| container_feeder.delta_cii_pct | 12.4301 | 12.4278 | 0.0190075 |
| container_feeder.gz_corrected_m | 1.60502 | 1.60502 | 0 |
| container_feeder.gz_correction_mm | 3.06889 | 3.06889 | 0 |
| container_feeder.panel_stress_mpa | 393.945 | 393.945 | 0 |
| container_feeder.rf_over_v | 25.2864 | 25.2912 | 0.0187916 |
| ferry_ropax.ct_clean | 0.00198361 | 0.00198443 | 0.0413164 |
| ferry_ropax.delta_cii_pct | 12.9041 | 12.8987 | 0.0418184 |
| ferry_ropax.gz_corrected_m | 2.15392 | 2.15392 | 0 |
| ferry_ropax.gz_correction_mm | 3.52466 | 3.52466 | 0 |
| ferry_ropax.panel_stress_mpa | 393.945 | 393.945 | 0 |
| ferry_ropax.rf_over_v | 25.9889 | 25.9997 | 0.0413164 |
| tanker_aframax.ct_clean | 0.00198302 | 0.00198384 | 0.0414574 |
| tanker_aframax.delta_cii_pct | 12.908 | 12.9026 | 0.0419613 |
| tanker_aframax.gz_corrected_m | 2.90746 | 2.90746 | 0 |
| tanker_aframax.gz_correction_mm | 1.63439 | 1.63439 | 0 |
| tanker_aframax.panel_stress_mpa | 393.945 | 393.945 | 0 |
| tanker_aframax.rf_over_v | 63.9401 | 63.9666 | 0.0414574 |
| tanker_vlcc.ct_clean | 0.00191139 | 0.00191242 | 0.0537049 |
| tanker_vlcc.delta_cii_pct | 13.3977 | 13.3904 | 0.0543882 |
| tanker_vlcc.gz_corrected_m | 3.97412 | 3.97412 | 0 |
| tanker_vlcc.gz_correction_mm | 1.09192 | 1.09192 | 0 |
| tanker_vlcc.panel_stress_mpa | 393.945 | 393.945 | 0 |
| tanker_vlcc.rf_over_v | 127.733 | 127.802 | 0.0537049 |

### interface_contract_coverage — 契约覆盖率

| coupling | quantity | unit | consumer_unit | unit_conversion_factor | covered |
|---|---|---|---|---|---|
| C1 | righting_arm_correction | mm | mm | 1 | True |
| C2 | plate_bending_stress_max | MPa | Pa | 1.0000e+06 | True |
| C3 | delta_cii | pct | pct | 1 | True |
## ④ 结果图

![fig1_C1_gz_wave_correction.png](artifacts/cross-domain-coupling-gate/figures/fig1_C1_gz_wave_correction.png)

*图1 C1 波浪→GZ：静水/波浪 GZ 曲线（independent）与波浪修正量（mm，右轴）。修正量为二阶小量，曲线几乎重合。*

![fig2_C1_sampling_convergence.png](artifacts/cross-domain-coupling-gate/figures/fig2_C1_sampling_convergence.png)

*图2 C1 接口采样分辨率收敛性：41 站已远低于 2% 判据（0.0103%），再加密收益递减。*

![fig3_C2_series_truncation.png](artifacts/cross-domain-coupling-gate/figures/fig3_C2_series_truncation.png)

*图3 C2 压力→板格应力：级数截断误差随项数收敛；契约声明的 11 项对应 2.32%（<3%）。*

![fig4_C3_validity_envelope.png](artifacts/cross-domain-coupling-gate/figures/fig4_C3_validity_envelope.png)

*图4 C3 污底→CII 传输律适用域：线性化误差随 ΔR/R 增长；κ=0.1 时 1% 判据边界约在 ΔR/R≈9%，设计点 7.6% 在域内。*

![fig5_end_to_end_errors.png](artifacts/cross-domain-coupling-gate/figures/fig5_end_to_end_errors.png)

*图5 端到端 5 船型 × 6 指标 vs 冻结基线（ITTC-57 管线 vs Schoenherr 基线）逐项误差，最大 0.054%。*

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

**（1）合成几何，无实验数据。** 五条船型为合成主尺度，没有模型试验或实船测量对照。因此本节点的「误差」度量的是**接口实现与线性化保真度**，不是物理有效性——所有判据数字都不构成对真实船舶精度的声明。

**（2）一个真实的物理发现（改变了接口约定）。** 对直壁棱柱船体，波浪对 GZ 的一阶影响恒为零：逐站看，波面在站内沿 y 方向与倾斜水线平行，因此各站增量层的 y 一阶矩为 0，GZ 的修正量只能是二阶量（本例中 0.7–5.1 mm 量级）。这意味着若在传输律里塞进经典的「e^(-kT) 波面局部斜率一阶修正」，零倾角处会出现一个 ~2.7 cm 的虚假复原臂偏置。本节点据此把 C1 契约定义为**修正量（mm）**而非「修正后的 GZ」，并要求独立复算在同定义下吻合——这条经验已写进 pack 的 notes。

**（3）C2 的 3% 判据是「工程截断」的判据，不是级数解的精度。** 截断 11 项时接口值为 384.81 MPa，收敛值 393.94 MPa（差 2.32%）；7 项 4.55%（超限）。若工程上需要 ≤1%，需 ≥21 项——这说明该接口若只报「板格应力」而不声明级数阶数，下游无法判断可信度，契约里必须带阶数。

**（4）C3 的 1% 判据只在适用域内成立。** 闭式比值律的误差近似 κ·ΔR/R 的二阶项，ΔR/R 增大则线性化失效：κ=0.1 时 ΔR/R≈9% 附近就顶到 1% 线。本节点把设计点选在 ΔR/R=7.6%（误差 0.69%），并在图中给出 κ=0.05/0.1/0.2 三条适用域边界。重度污底（ΔR/R>12%）必须改用二阶传输律。

**（5）端到端判据只有 0.05%，信息量集中在链上而非线上。** ITTC-57 与 Schoenherr 在 Re≈1e9 区间几乎重合（0.04%），所以端到端 5% 判据实际检验的是链的管道（单位换算、耦合组装、指标拼接）而非阻力线差异；如需更强的外部锚，应接入实船/模型试验数据。

**（6）未覆盖的物理。** 不计纵倾、不建模粘性阻尼与横摇动力学（本节点只做静水力学平衡）、污底只以粗糙度附加系数表达（无膜厚/局部剥落）、结构只做静压+动压静力解（无疲劳/屈曲）。

## ⑥ 误差判定

关键量 vs 独立复算误差逐项：C1 = 0.010%（判据 2%）、C2 = 2.32%（判据 3%）、C3 = 0.688%（判据 1%）、端到端最大 = 0.054%（判据 5%）、契约覆盖率 = 100%（判据 100%）。**是否<3%：是，全部判据均 <3%**（C3 更严的 1% 判据亦满足）。全部前置节点接口在本节点组合场景下通过一致性验证，门禁判定 PASS；基线 sha256 与 spec/evaluator sha256 已钉入报告，可复现。

| criterion | max error % | tolerance % | < 3% ? | verdict |
|---|---|---|---|---|
| coupling:C1 | 0.0102714 | 2 | YES | PASS |
| coupling:C2 | 2.3196 | 3 | YES | PASS |
| coupling:C3 | 0.688206 | 1 | YES | PASS |
| end_to_end_regression | 0.0543882 | 5 | YES | PASS |

- overall_verdict: **PASS**
- report_sha256: `146d81662d8858cbbba4d15bb6bc87845371286644a07328ff4b52c4cf5ad1d9`