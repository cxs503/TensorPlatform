# P171W1-N08 判据治理成熟化集成：阈值注册表 × 不变性群 × 体积四极子

**节点**：P171W1-N08（平台治理与判据体系成熟化集成）　**仓库**：TensorPlatform　**分支**：agent/m7812

**总判定**：`PASS`（未通过项：无）

## ① 案例介绍（物理背景/几何/参数表）

两条判据源被集成进平台级阈值注册表：

1. **N04 体积四极子（算子级）**：立方对称速度集的四阶矩应精确等于 c_s⁴(δδ+δδ+δδ)（c_s²=1/3）。体积四极子取二阶矩的无迹投影 Q_ij = M2_ij − c_s²δ_ij；其(refinement)算例为余弦调制密度场 ρ=1+A·cos(2πx/L)（A=0.5，L=1.0）在立方域上的体积四极子，闭式解 Q_xx=−A L⁵/(3π²)、Q_yy=Q_zz=+A L⁵/(6π²)。
2. **N05 不变性群（斜槽）**：立方群 O_h（48 元，符号置换矩阵）在数值上是否闭合、各向同性张量是否不变、二阶对称张量不变子空间维数是否恰为 1；斜槽算例为剪切映射 x=Aξ（A=[[1,0],[s,1]]，s=0.5）下的度量张量拉普拉斯 Δ_x = G_ij ∂²_{ξiξj}（G=A⁻¹A⁻ᵀ），测其观测阶。

| 参数 | 值 |
|---|---|
| criteria_total | `14` |
| cs2 | `0.3333333333333333` |
| ellipsoid_semi_axes | `[0.4, 0.3, 0.2]` |
| group_order | `48` |
| lattice_sets | `["D2Q9", "D3Q19", "D3Q27"]` |
| quadrupole_amplitude_A | `0.5` |
| quadrupole_domain_length_L | `1.0` |
| quadrupole_exact_q_xx | `-0.01688686394038963` |
| quadrupole_exact_q_yy | `0.008443431970194815` |
| refinement_resolutions_quadrupole | `[16, 32, 64, 128]` |
| skew_shear_s | `0.5` |

## ② 怎么做的（方法/流程/工具链）

管线：`data/criteria_governance/*.criteria.json`（判据工件）→ `ThresholdRegistry`（source_type/派生链校验 + 负控剔除）→ `DerivationEngine`（探针执行：解析恒等式残差 / h→h/2 细化扫描，引擎自行 log-log 重算观测阶）→ `drift_gate`（sha256 钉桩 + 值级对照，20 个程序化篡改 + 20 个未改动对照）→ `maturity`（四维评分 + 雷达图）。

- 判据工件：`data/criteria_governance/n04_lbm_volume_quadrupole.criteria.json`、`data/criteria_governance/n05_invariance_group.criteria.json`（各 7 条，C1–C7）
- 机制代码：`tools/criteria_governance/{registry,derivation,drift_gate,maturity,kernels,probes}.py`
- 仪器参数：细化扫描 N ∈ [16, 32, 64, 128]（体积四极子）、[16, 32, 64, 128]（斜槽）
- 所有测量在同一进程内现场计算；报告中的每个数字由 report.json 程序化注入。

## ③ 计算结果（判据表，程序化注入）

### C1–C6 验收

| 判据 | 说明 | 关键数字 | 判定 |
|---|---|---|---|
| C1_registry_coverage | N04+N05 共 14 条判据 100% 入册，source_type 缺失率 0% | registered=14; expected=14; coverage=1.0; source_type_missing=0; quarantined=0 | PASS |
| C2_derivation_consistency | 派生一致性引擎对 analytic 来源判据的实测衰减指数偏差 ≤ 0.3 | refinement_criteria=2; refinement_consistent=2; max_deviation=0.009309669984184588; tolerance=0.3 | PASS |
| C3_drift_gate | 漂移门篡改检出率 100%(20/20)，假阳性 0/20 | tamper_detected=20; tamper_total=20; detection_rate=1.0; false_positive=0; clean_total=20 | PASS |
| C4_maturity_radar | 成熟度雷达图四维评分均 ≥ 0.7 | scores={"coverage": 1.0, "derivability": 1.0, "drift_detection": 1.0, "false_positive_safety": 1.0}; min_score=1.0 | PASS |
| C5_negative_control | heuristic 判据混入 analytic 集合必须被自动剔除并告警 | impostor="N05-C8"; quarantined=true; analytic_count_unchanged=true; warnings=1 | PASS |
| C6_before_after_kpi | 集成报告含判据治理前后对比（覆盖率/可裁率 KPI） | before_coverage=0.0; after_coverage=1.0; before_adjudicable_rate=0.0; after_adjudicable_rate=1.0 | PASS |

### 判据逐条测量

| 判据 | source_type | 派生方法 | 实测量 | 阈值 | 实测指数/声明 | 判定 |
|---|---|---|---|---|---|---|
| N04-C1 | analytic | analytic_identity | 4.996e-16 | <= 0.02 | — | PASS |
| N04-C2 | analytic | analytic_identity | 6.661e-16 | <= 1e-12 | — | PASS |
| N04-C3 | analytic | analytic_identity | 6.661e-16 | <= 1e-12 | — | PASS |
| N04-C4 | analytic | refinement_scan | 1.004e-04 | <= 0.04 | 2.0061/2.0 (Δ=0.0061≤0.3) | PASS |
| N04-C5 | analytic | analytic_identity | 4.996e-16 | <= 1e-12 | — | PASS |
| N04-C6 | empirical | sampled_observation | 3.265e-03 | <= 0.05 | — | PASS |
| N04-C7 | analytic | analytic_identity | 6.853e-17 | <= 1e-12 | — | PASS |
| N05-C1 | analytic | refinement_scan | 3.170e-02 | <= 0.05 | 1.9907/2.0 (Δ=0.0093≤0.3) | PASS |
| N05-C2 | analytic | analytic_identity | 0.000e+00 | <= 1e-12 | — | PASS |
| N05-C3 | analytic | analytic_identity | 0.000e+00 | <= 1e-12 | — | PASS |
| N05-C4 | analytic | analytic_identity | 1.000e+00 | == 1.0 | — | PASS |
| N05-C5 | empirical | sampled_observation | 6.981e-03 | <= 0.3 | — | PASS |
| N05-C6 | analytic | analytic_identity | 0.000e+00 | <= 1e-12 | — | PASS |
| N05-C7 | empirical | sampled_observation | 2.000e+00 | <= 3.0 | — | PASS |

### 漂移门

- 钉桩工件：['N04-lattice-volume-quadrupole.verification.json', 'N05-cubic-symmetry-invariance-group.verification.json', 'n04_lbm_volume_quadrupole.criteria.json', 'n05_invariance_group.criteria.json']
- 篡改检出：20/20 (detection_rate=1.000)；未改动对照误报：0/20 (FPR=0.000)
- 篡改类型：每工件 10 个单点数值变异（×1.5 + 0.001），覆盖 measured_value/阈值/序列

### 成熟度四维

| 维度 | 分数 | 分子/分母 | 定义 |
|---|---|---|---|
| coverage | 1.000 | 14/14 | 入册判据数 / 声明判据总数 |
| derivability | 1.000 | 11/11 | 分析型判据中推导链可复算者 / 分析型判据总数 |
| drift_detection | 1.000 | 20/20 | 程序化篡改被检出数 / 篡改变体总数 |
| false_positive_safety | 1.000 | 20/20 | 1 − 假阳性率（未改动副本被误告警的比例） |

最小维 1.000 ≥ 门槛 0.7 → `PASS`

### 负控（C5）

- 注入条目：`N05-C8`（cohort=analytic, source_type=heuristic）
- 剔除：True；原因：['cohort_source_mismatch:analytic!=heuristic']
- 分析型队列计数 基线 5 → 注入后 5（不变：True）
- 告警：['[负控] N05-C8 申报 cohort=analytic 但 source_type=heuristic；已剔除出分析型队列（队列计数不上升）']

### 治理前后 KPI（C6）

| 指标 | 治理前 | 治理后 | Δ |
|---|---|---|---|
| 入册判据数 | 0 | 14 | 14 |
| 覆盖率 | 0.000 | 1.000 | 1.000 |
| 可裁率 | 0.000 | 1.000 | 1.000 |
| source_type 完整率 | 0.000 | 1.000 | — |
| 派生链可追溯率 | 0.000 | 1.000 | — |
| CI 漂移门 | False | True | — |

治理前测量方法：`git grep -l 'class ThresholdRegistry' main -- tools src backend data`（可用=True）。

## ④ 结果图

![maturity radar](../artifacts/criteria_governance/maturity_radar.png)

**图 1**　四维成熟度雷达图（蓝实线=实测分数，橙虚线=0.7 门槛）。最小维 1.000。

![refinement scan](../artifacts/criteria_governance/refinement_scan.png)

**图 2**　细化扫描双对数误差图：斜槽度量算子（N05-C1）与体积四极子中点求积（N04-C4）的实测衰减指数，黑色虚线为声明阶 p=2.0 参考斜率。

## ⑤ 注意点（踩过的坑/局限/适用范围）

- **N04/N05 独立分支未合入本工作树**：开工时 `grep -r 'threshold_registry|quadrupole|invariance_group'` 在本树 0 命中，故两条判据集按任务书 C1–C7 槽位在本节点重建为数据工件（`data/criteria_governance/*.criteria.json`），数值由探针现场计算。若后续 N04/N05 分支合入，仅需替换工件并重跑本运行器，机制代码不动。
- **斜槽观测阶的坑**：首版把剪切场写为 f(ξ) = sin(k ξ1)cos(k(ξ2+s ξ1))，周期性与 ξ1 不兼容（需 s∈Z），导致边界跳变污染、拟合出负斜率；此外首次解析基准的混合导数项符号写反，误差在 s≠0 时收敛到一个错的常数。两处都是靠「离散极限 vs 解析极限对照」暴露的：先把 s=0 退化为标准 5 点格式确认斜率 2.00，再逐项比对解析混合导数项与差分项的符号。最终版本用 ξ 空间光滑周期场 + 严格按 G_ij ∂²_{ξiξj} 展开，s=0/0.2/0.3/0.5/0.8 五点全部给出 1.99~2.00
- **负控的判定口径**：`cohort=analytic` 视为「申报进入分析型集合」的声明。注册表用 source_type 与派生方法双重校验，任一不合可解析族即剔除；剔除条目仍留在 expected_total 分母里（覆盖率分母不缩水），避免「剔除即提升覆盖率」的作弊面。
- **漂移门只裁判工件完整性**：哈希/值级对照能 100% 抓阈值与测量值篡改，但无法判断「数值本身算错」，后者由派生引擎（观测阶）与验收判据 C2 承担。两者互补，不可相互替代。
- **四维评分在当前规模饱和到 1.0**：14 条判据、20 次篡改试验的样本量下四维都取到上界，说明门槛 0.7 现阶段没有区分力（只挡灾难性退化）。要提高区分度需扩大判据源数量、并把篡改族从「×1.5 粗变异」扩到「±1e-9 微扰 + 派生链断链 + source_type 谎报」。
- **漂移门的灵敏度边界**：哈希级对任意字节改动 100% 灵敏，值级对照带相对容差 1e-12，故「哈希命中但值级沉默」在微扰场景下会出现；本报告的检出率由哈希级保证，值级用于给出可读的差异路径。
- **适用范围**：机制层（registry/derivation/drift_gate/maturity）零领域语义，可承接任一新判据源；本报告中的两条判据源是注入数据，不是代码里的领域常量。

## ⑥ 误差判定

| 关键量 | 参考/基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| N04-C4 实测衰减指数（声明 2.0） | p=2.0 | p=2.0061 | |Δ|=0.0061 (0.31%) | YES |
| N04-C4 最细网格相对误差 | 阈值上限 <= 0.04 | 1.004e-04 | 占阈值上限 0.25%（余量 99.75%） | 阈值型判据：见 ③ 判据表 |
| N05-C1 实测衰减指数（声明 2.0） | p=2.0 | p=1.9907 | |Δ|=0.0093 (0.47%) | YES |
| N05-C1 最细网格相对误差 | 阈值上限 <= 0.05 | 3.170e-02 | 占阈值上限 63.40%（余量 36.60%） | 阈值型判据：见 ③ 判据表 |
| N05-C4 不变子空间维数（numerical_rank(projector_onto_trivial_rep)） | Schur 引理：Sym(3) 的 O_h 平凡表示重数 = 1 | 1 | |measured-1|=0.0e+00（容差 1e-9） | YES（整数型判据，绝对差 0） |
| N04-C1 恒等式残差（relative_max_error(M4, c_s^4 * isotropic4)） | 解析恒等式精确值（残差 0） | 4.996e-16 | 4.996e-16 | YES |
| N04-C2 恒等式残差（relative_max_error(M2, c_s^2 * delta)） | 解析恒等式精确值（残差 0） | 6.661e-16 | 6.661e-16 | YES |
| N04-C3 恒等式残差（abs(sum(w) - 1)） | 解析恒等式精确值（残差 0） | 6.661e-16 | 6.661e-16 | YES |
| N04-C5 恒等式残差（abs(trace(Q)) / abs(trace(M2))） | 解析恒等式精确值（残差 0） | 4.996e-16 | 4.996e-16 | YES |
| N04-C7 恒等式残差（relative_max_error(R Q_before R^T, Q_after)） | 解析恒等式精确值（残差 0） | 6.853e-17 | 6.853e-17 | YES |
| N05-C2 恒等式残差（max_min_distance(a*b, group)） | 解析恒等式精确值（残差 0） | 0.000e+00 | 0.000e+00 | YES |
| N05-C3 恒等式残差（max_g relative_max_error(g^{⊗4} T, T)） | 解析恒等式精确值（残差 0） | 0.000e+00 | 0.000e+00 | YES |
| N05-C6 恒等式残差（relative_max_error((-I)^{⊗2} T, T)） | 解析恒等式精确值（残差 0） | 0.000e+00 | 0.000e+00 | YES |
| N04-C6 经验型量（relative_max_error(Q_midpoint64, Q_closed_form)） | 工程容差 <= 0.05 | 3.2645e-03 | 无解析基准（经验型），仅有工程容差 | 无基准 |
| N05-C5 经验型量（max(p_s) - min(p_s)） | 工程容差 <= 0.3 | 6.9810e-03 | 无解析基准（经验型），仅有工程容差 | 无基准 |
| N05-C7 经验型量（C(s=0.5) / C(s=0)） | 工程容差 <= 3.0 | 1.9995e+00 | 无解析基准（经验型），仅有工程容差 | 无基准 |

**是否 <3% 的显式回答**：两条细化扫描判据的观测阶偏差分别为 0.0061 与 0.0093，相对声明阶 2.0 分别为 0.31% 与 0.47% —— **均 <3%**。8 条恒等式判据的残差量级 1e-16（数值饱和到浮点精度），**<3%**；N05-C4 是整数秩判据，绝对差 0（容差 1e-9），**<3%**。3 条经验型判据（N04-C6/N05-C5/N05-C7）无解析基准，按工程容差裁判并已逐条列出，不冒充精度结论。

