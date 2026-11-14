# KPI-GATE-100 精度台账：全量对标证据的机器化实测报告

- 生成方式：`python3 scripts/run_precision_ledger.py --scan . --out artifacts/precision_ledger/ledger.json`（所有数字由脚本注入，零手抄）
- 台账：`artifacts/precision_ledger/ledger.json` · 摘要：`artifacts/precision_ledger/summary.md` · schema `precision-ledger/1.0`
- 机检门：`python3 scripts/run_precision_ledger.py --check artifacts/precision_ledger/ledger.json`（字段齐全 / rel_err 复算 ≤1e-6 / WITHHELD 不判过）
- 门限（owner 规则，预登记）：硬门 15% 不可接受，目标 3%
- 判决口径：`mid` — 中值口径：abs(rel_err_mid) <= tol（rel_err_mid 以带中值为参照；本台账采用）

## 1. 案例介绍（被对标的对象）

平台船舶总体性能的对标证据集中在阻力（总阻力系数 Ct / 总阻力 Rt）与推进（桨敞水效率/零推力进速比），另含摩擦线交叉校核。基准来源为三个独立实验/回归数据集：

| 基准集 | 几何 | 来源 | 台账中的用途 |
|---|---|---|---|
| SUBOFF (裸艇体) | L=4.356 m, D=0.508 m, S=5.42 m², Cb≈0.542 | Roddy 1990, DTRC-90/012 | Ct（含 0.0035–0.0040 实验带）/ Rt（R1 反解 92.52 N 起） |
| DTMB-5415 | L=5.72 m, B=0.759 m, T=0.246 m, Cb=0.508 | ITTC V&V Workshop 2010 | Ct 点值；Rt 在正典中被 WITHHELD |
| KVLCC2 | L=320 m, B=58 m, Cb=0.810 | SIMMAN 2008 | Ct 点值；Rt 在正典中被 WITHHELD |
| Wageningen B-series | B3-80 / B4-40 / B4-55 / B4-70 / B5-79 | Oosterveld & van Oossanen (1975) | 桨敞水 η0max / J0 / 点对点 KT，KQ |
| Schoenherr 摩擦线 | 平板 | Schoenherr (1932) | Cf 经验线交叉校核 |

基准池来自正典 `backend/data/benchmark_reference.json` (version 3.0)；该文件自带裁决策略：`仅 reference_status=DERIVED 的行可参与 15% 精度判决；WITHHELD 行如实标注、不出判决`。本台账把这条自然语言策略变成 load-bearing 代码（`baselines.py`）。

SUBOFF 裸艇体在 Fr≈0.39 的 Ct 实验值是**区间**而非单点：[0.0035, 0.004]（Roddy, J.D. (1990), DARPA SUBOFF Model Experiments, DTRC-90/012）。区间量必须先裁定判决口径，见 §3。

## 2. 方法（怎么做的）

### 2.1 扫描与归一化

- 通用别名扫描：遍历 `--scan` 根下全部 `*.json`（跳过 `.git`/`node_modules`/`fixtures`/`golden`/本台账自身输出），识别带 case id 且含已知键别名的记录，归一化为 `Ct`（`calc_ct`/`Ct_calc`/`ct_calc` … vs `ref_ct`/`Ct_exp`/`exp_ct` …）与 `Rt`（`calc_rt_n`/`Rt_calc` … vs `ref_rt_n`/`Rt_exp` …）；只有**记录里真的出现了计算结果**才算一条对标，纯基准声明不算。
- 专用适配器：验证报告（`*verification_report.json`，actual vs expected）、桨敞水表（BEM vs B-series 回归）、摩擦线交叉校核、无基线批量运行（按 file×category 聚合成 1 条，显式 WITHHELD/STUB）。
- **镜像去重**：同一比对常被多份证据副本携带（`report_data.json` 与 `validation_results.json`、`docs/board_audit/*/evidence/artifacts/`、`backend/artifacts/`）。按 (case, 量名, computed, baseline) 归并为 1 条，其余路径记入 `mirrors`。否则通过率会变成「某任务写了多少份副本」的函数。
- **基线重裁决**：源文件自带的 `ref` 值不直接采信，按正典重解。典型：SUBOFF Rt 的 legacy 值 12.5 N 已被 R1（Rt=Ct·½ρV²S）判为不成立，台账改用正典派生值 92.52 N；DTMB-5415 / KVLCC2 的 `rt_status=WITHHELD` → 任何 Rt 对标一律 WITHHELD，不判过。

### 2.2 证据分级（自报）

| 等级 | 判定规则 | 本台账条数 |
|---|---|---|
| REAL | 基线是实验/文献/解析值，且与正典可机验；计算值来自真实算法路径 | 91 |
| MOCK | 对标自指：`expected_result` 与 `actual_result` 逐位相同（oracle 与实现同源） | 15 |
| STUB | 跑完但没有数值结果，无从比对 | 1 |
| WITHHELD | 计算值存在但基线被正典判为不成立/不存在 → 明确不判过 | 16 |
| NOT_SUPPORTED | 该量在构造上不可比（如 BEM 叶素核 vs B 系列回归的点对点 KT/KQ） | 5 |

### 2.3 判决与复算

- 每条记录 `rel_err.low/mid/high = (computed − ref)/ref`，ref 依次取 `baseline.low` / `baseline.value` / `baseline.high`；点值基线的区间退化，三口径相同。
- `pass_15pct = |rel_err.mid| ≤ 0.15`，`pass_3pct = |rel_err.mid| ≤ 0.03`；`--check` 从 `computed`/`baseline` 复算并与存档比对，容差 1e-6。
- WITHHELD / STUB / NOT_SUPPORTED 三条路径强制 `pass_*=false`，`--check` 拦截任何`WITHHELD 同时 pass_15pct=true` 的条目。

## 3. 口径裁定：实验带量怎么定门

锚点：SUBOFF S0 `Ct = 0.003393` vs 实验带 [0.0035, 0.004]（带中值 0.00375）。

来源：computed: artifacts/m1388/chain_results.json SUBOFF-01 calc_ct=0.003392 (ITTC-1957 friction + axisymmetric-body empirical residual), 0.003393 to 4 s.f.; band: Roddy 1990 DTRC-90/012 SUBOFF bare-hull Ct 0.0035-0.0040

| 口径 | 可执行定义 | 统计量 | 15% 门 | 3% 门 | 问题 |
|---|---|---|---|---|---|
| 区间端点口径 | 区间端点口径：max(abs(rel_err_low), abs(rel_err_high)) <= tol，即计算值须同时落在两端点的 tol 邻域内 | 15.17% | FAIL | FAIL | 判据随带宽放大：带越宽越难/越易取决于选哪个端点，且端点选择任意 |
| 中值口径 | 中值口径：abs(rel_err_mid) <= tol（rel_err_mid 以带中值为参照；本台账采用） | 9.52% | PASS | FAIL | 带宽信息不进判据（仍以 rel_err_low/high + band_contained 逐条披露） |
| 带内包含口径 | 带内包含口径：baseline.low <= computed <= baseline.high，容差为 0 | 3.06% | FAIL | FAIL | 带宽被吸收进判据：带内 +9.5% 也判过，无法表达 3% 目标，跨 case 不可聚合 |

**裁定：采用中值口径（`mid`）。** 采用中值口径：(1) 15%/3% 阈值本身以单一参照值定义，相对误差必须有唯一参照点；(2) 端点口径的难度随带宽与端点选择漂移（同一 9.5% 偏差在不同带下判决相反）；(3) 带内包含口径把带宽吸收为免费额度，带内 9.5% 与带外 0.1% 同判，无法表达 3% 目标；(4) 中值口径与带宽解耦、可跨 case 聚合，同时 rel_err_low/high 与 band_contained 仍逐条记录，带宽信息不丢失。band_contained 作为辅助字段披露但不参与判决。

三口径在同一锚点上的具体表现（全部由脚本计算）：端点口径统计量 15.17% → 15% 门 FAIL、3% 门 FAIL（要求计算值同时逼近带的两端，带越宽越吃亏，与精度语义无关）；带内包含口径 → 计算值不在带内，15% 门 FAIL、3% 门 FAIL（带宽被吸收成免费额度）；中值口径统计量 9.52% → 15% 门 PASS、3% 门 FAIL。三口径数值全部保留在 `ledger.json:gate_adjudication`，`aux_pass_15pct_endpoints` / `aux_pass_15pct_containment` 作为辅助（非约束）字段逐条披露，`band_contained` 亦然。band_contained / rel_err_low / rel_err_high 逐条记录，辅助（非约束）判决字段 aux_pass_15pct_endpoints / aux_pass_15pct_containment


## 4. 计算结果

![误差分布](../../artifacts/precision_ledger/figures/error_distribution.png)

*图 1 — 全部可判条目 abs(rel_err_mid) 的分布（对数横轴）：15% 硬门与 3% 目标线；REAL 基线 91 条、MOCK 自指 15 条分开着色。*

| 指标 | 数值 |
|---|---|
| 总条目 | 128 |
| 可判条目（REAL+MOCK，有数值基线） | 106 |
| 其中 REAL | 91 |
| 去重后独立 case 数 | 45 |
| 被折叠的镜像副本 | 141 |
| 过 15% 门（REAL 分母） | 36/91 = 39.56% |
| 过 3% 门（REAL 分母） | 9/91 = 9.89% |
| 过 15% 门（含 MOCK 分母） | 51/106 = 48.11% |
| 过 3% 门（含 MOCK 分母） | 24/106 = 22.64% |
| 违规条目（/rel_err_mid/ > 15%） | 55 |

### 4.1 证据等级分布

| 等级 | 条数 |
|---|---|
| REAL | 91 |
| MOCK | 15 |
| STUB | 1 |
| WITHHELD | 16 |
| NOT_SUPPORTED | 5 |

### 4.2 按证据族（family）

| family | entries | judged | pass 15% | pass 3% |
|---|---|---|---|---|
| artifacts/benchmark_validation.json | 12 | 12 | 5 | 1 |
| artifacts/m1388 | 28 | 20 | 12 | 4 |
| artifacts/m642 | 5 | 0 | 0 | 0 |
| artifacts/pilot_verification_report.json | 15 | 15 | 15 | 15 |
| docs/m1444 | 8 | 8 | 0 | 0 |
| docs/m1536 | 18 | 18 | 0 | 0 |
| docs/m1570 | 4 | 0 | 0 | 0 |
| docs/m1608 | 12 | 12 | 4 | 1 |
| docs/m1753 | 15 | 10 | 10 | 2 |
| docs/verification | 8 | 8 | 4 | 1 |
| docs/verification_m1604 | 3 | 3 | 1 | 0 |

![按族通过率](../../artifacts/precision_ledger/figures/gate_by_family.png)

*图 2 — 按证据族的通过率（分母=该族可判条目）：同一批工况被多个任务/版本反复计算，族间差异反映算法版本差异（含 `docs/m1536` 这一族对 Holtrop-1984 忠实实现与现代船型KVLCC2/KCS/AFRAMAX 的审计），不是基准差异。*


### 4.3 按量名

| 量 | entries | judged | pass 15% | pass 3% |
|---|---|---|---|---|
| 1+k | 3 | 3 | 3 | 3 |
| Cf | 16 | 16 | 16 | 15 |
| Ct | 57 | 57 | 19 | 4 |
| J0 | 5 | 5 | 5 | 0 |
| KT_KQ_pointwise_max_abs_dev | 5 | 0 | 0 | 0 |
| RT | 9 | 9 | 0 | 0 |
| Rt | 23 | 11 | 3 | 0 |
| eta0_max | 5 | 5 | 5 | 2 |
| geometry_batch_result | 1 | 0 | 0 | 0 |
| propeller_batch_result | 1 | 0 | 0 | 0 |
| resistance_batch_result | 2 | 0 | 0 | 0 |
| stability_batch_result | 1 | 0 | 0 | 0 |

![SUBOFF 带口径裁定](../../artifacts/precision_ledger/figures/suboff_band_gate.png)

*图 3 — SUBOFF S0 的带口径裁定示意：实验带、带中值（采用的判决参照）与计算值。*


### 4.4 >15% 违规条目归因

| 归因代码 | 条数 | 含义 |
|---|---|---|
| BELOW_GATE | 51 | passing entries |
| SIMPLIFIED_HOLTROP_VARIANT | 18 | platform uses the simplified Cr = 0.002*(1+2Cb)*Fn^2.5 while the reference stack is a faithful Holtrop-1984 ((1+k)Cf+Cw+Ca) implementation -> total resistance off by ~2-3x on full-form hulls (KVLCC2/KCS/AFRAMAX) |
| WETTED_SURFACE_FORMULA | 16 | wet-surface estimate S = pi*B*L*(0.5+0.25*Cb) under-predicts full hulls (KVLCC2/DTMB-5415), so Ct/Rt are systematically low |
| WITHHELD | 16 | no usable baseline (never judged) |
| RE_PHYSICS_INCONSISTENT | 8 | raw source computed Cf/Cr at a Reynolds number inconsistent with the canonical model scale (docs/m1444 run: Re=5.65e6 vs 1.31e7) and with an out-of-family residual model -> Ct inflated 2-3x |
| HOLTROP_OUT_OF_DOMAIN | 7 | Holtrop-type method applied outside its validity box (submarine/axisymmetric hull or Fr>0.4); the method's residual term has no calibrated coefficient there |
| NOT_SUPPORTED | 5 | comparison out of scope by construction |
| RESIDUAL_FN_BLOWUP | 4 | empirical residual term (Cr ~ Fn^2.5) evaluated far outside its fitted Froude range (Fr>0.8) -> Ct grows monotonically and diverges from the measured plateau |
| RESISTANCE_FORCE_BASIS | 2 | Rt compared against a force baseline whose rho/S basis differs from the run (legacy 12.5 N row vs canonical R1-derived force) |
| STUB_NO_RESULT | 1 | no numeric result |

违规清单（按 abs(rel_err_mid) 降序，前 25 条）：

| case | 量 | computed | baseline | rel_err(mid) | 归因 | 来源 |
|---|---|---|---|---|---|---|
| DTMB5415-04 | Ct | 0.026688 | 0.0038 | 602.32% | WETTED_SURFACE_FORMULA | docs/verification/resistance_benchmark_validation.json |
| DTMB5415-04 | Ct | 0.0266876 | 0.0038 | 602.31% | RESIDUAL_FN_BLOWUP | docs/m1608/outputs/resistance_validation/validation_results.json |
| SUBOFF-04 | Ct | 0.017641 | 0.0044 | 300.93% | RE_PHYSICS_INCONSISTENT | docs/m1444/outputs/suboff_validation/validation_results.json |
| SUBOFF-03 | Ct | 0.015768 | 0.0042 | 275.43% | RE_PHYSICS_INCONSISTENT | docs/m1444/outputs/suboff_validation/validation_results.json |
| KCS@24.0kn | RT | 7394.43 | 2006.94 | 268.44% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KCS@21.0kn | RT | 4732.21 | 1331.56 | 255.39% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| SUBOFF-02 | Ct | 0.013922 | 0.004 | 248.05% | RE_PHYSICS_INCONSISTENT | docs/m1444/outputs/suboff_validation/validation_results.json |
| KCS@24.0kn | Ct | 0.009812 | 0.00290481 | 237.78% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KCS@18.0kn | RT | 2876.8 | 875.893 | 228.44% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KVLCC2@16.5kn | RT | 6851.16 | 2098.26 | 226.52% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KCS@21.0kn | Ct | 0.008201 | 0.00251726 | 225.79% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KVLCC2@15.0kn | RT | 5512.42 | 1719.46 | 220.59% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| DTMB5415-03 | Ct | 0.0111693 | 0.0035 | 219.12% | RESIDUAL_FN_BLOWUP | docs/m1608/outputs/resistance_validation/validation_results.json |
| DTMB5415-03 | Ct | 0.011169 | 0.0035 | 219.11% | WETTED_SURFACE_FORMULA | docs/verification/resistance_benchmark_validation.json |
| KVLCC2@16.5kn | Ct | 0.006723 | 0.00214541 | 213.37% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KVLCC2@13.5kn | RT | 4345.15 | 1396.16 | 211.22% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KVLCC2@15.0kn | Ct | 0.006545 | 0.0021273 | 207.67% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| AFRAMAX-110k@15.0kn | RT | 3242.16 | 1054.09 | 207.58% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| AFRAMAX-110k@13.0kn | RT | 2338.78 | 775.191 | 201.70% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KCS@18.0kn | Ct | 0.006786 | 0.00225379 | 201.09% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KVLCC2@12.0kn | RT | 3339.8 | 1113.41 | 199.96% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KVLCC2@13.5kn | Ct | 0.006369 | 0.00213249 | 198.67% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| AFRAMAX-110k@15.0kn | Ct | 0.00698 | 0.00235214 | 196.75% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| AFRAMAX-110k@13.0kn | Ct | 0.006703 | 0.00230298 | 191.06% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |
| KVLCC2@12.0kn | Ct | 0.006196 | 0.00215235 | 187.87% | SIMPLIFIED_HOLTROP_VARIANT | docs/m1536/outputs/m1536/resistance_audit.json |

（完整 55 条见 `artifacts/precision_ledger/ledger.json:violations`）

### 4.5 不判过条目（WITHHELD / STUB / NOT_SUPPORTED）

| 等级 | case | 量 | computed | 原因 |
|---|---|---|---|---|
| WITHHELD | DTMB5415-01 | Rt | 3.4302 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=8.2 N 得 S_imp=17.49 m²，S_imp/(L·B)=4.028 落在物理带 [1.0, 3.2] 之外 → Rt |
| WITHHELD | DTMB5415-02 | Rt | 12.0908 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=28.5 N 得 S_imp=17.99 m²，S_imp/(L·B)=4.144 落在物理带 [1.0, 3.2] 之外 → R |
| WITHHELD | DTMB5415-03 | Rt | 44.1607 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=95 N 得 S_imp=16.28 m²，S_imp/(L·B)=3.750 落在物理带 [1.0, 3.2] 之外 → Rt  |
| WITHHELD | DTMB5415-04 | Rt | 99.6697 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=210 N 得 S_imp=14.73 m²，S_imp/(L·B)=3.393 落在物理带 [1.0, 3.2] 之外 → Rt |
| WITHHELD | KVLCC2-01 | Rt | 1.500318e+06 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=550000 N 得 S_imp=7389.32 m²，S_imp/(L·B)=0.398 落在物理带 [1.0, 3.2] 之外 |
| WITHHELD | KVLCC2-02 | Rt | 1.716031e+06 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=650000 N 得 S_imp=7099.07 m²，S_imp/(L·B)=0.382 落在物理带 [1.0, 3.2] 之外 |
| WITHHELD | KVLCC2-03 | Rt | 1.829473e+06 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=700000 N 得 S_imp=6929.31 m²，S_imp/(L·B)=0.373 落在物理带 [1.0, 3.2] 之外 |
| WITHHELD | KVLCC2-04 | Rt | 1.946849e+06 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=760000 N 得 S_imp=6840.13 m²，S_imp/(L·B)=0.369 落在物理带 [1.0, 3.2] 之外 |
| WITHHELD | batch/geometry/no_baseline_rows | geometry_batch_result | — | no independent baseline: 15 'geometry' batch rows carry only plausibility bounds (criteria_detail), which are not a benc |
| WITHHELD | batch/propeller/no_baseline_rows | propeller_batch_result | — | no independent baseline: 30 'propeller' batch rows carry only plausibility bounds (criteria_detail), which are not a ben |
| STUB | batch/resistance/failed_rows | resistance_batch_result | — | stub: 5 batch rows in category 'resistance' ended without a numeric result -> nothing to compare |
| WITHHELD | batch/resistance/no_baseline_rows | resistance_batch_result | — | no independent baseline: 40 'resistance' batch rows carry only plausibility bounds (criteria_detail), which are not a be |
| WITHHELD | batch/stability/no_baseline_rows | stability_batch_result | — | no independent baseline: 40 'stability' batch rows carry only plausibility bounds (criteria_detail), which are not a ben |
| WITHHELD | DTMB5415-01 | Rt | 87.1969 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=8.2 N 得 S_imp=17.49 m²，S_imp/(L·B)=4.028 落在物理带 [1.0, 3.2] 之外 → Rt |
| WITHHELD | DTMB5415-02 | Rt | 461.262 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=28.5 N 得 S_imp=17.99 m²，S_imp/(L·B)=4.144 落在物理带 [1.0, 3.2] 之外 → R |
| WITHHELD | DTMB5415-03 | Rt | 5045.77 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=95 N 得 S_imp=16.28 m²，S_imp/(L·B)=3.750 落在物理带 [1.0, 3.2] 之外 → Rt  |
| WITHHELD | DTMB5415-04 | Rt | 27124.8 | canonical rt_status=WITHHELD -> WITHHELD: R1 反解：由记录 Rt=210 N 得 S_imp=14.73 m²，S_imp/(L·B)=3.393 落在物理带 [1.0, 3.2] 之外 → Rt |
| NOT_SUPPORTED | B3-80 | KT_KQ_pointwise_max_abs_dev | 5214.05 | not_supported: BEM kernel is a first-principles blade-element implementation, not a B-series code; pointwise KT/KQ devia |
| NOT_SUPPORTED | B4-40 | KT_KQ_pointwise_max_abs_dev | 2111.17 | not_supported: BEM kernel is a first-principles blade-element implementation, not a B-series code; pointwise KT/KQ devia |
| NOT_SUPPORTED | B4-55 | KT_KQ_pointwise_max_abs_dev | 20912 | not_supported: BEM kernel is a first-principles blade-element implementation, not a B-series code; pointwise KT/KQ devia |
| NOT_SUPPORTED | B4-70 | KT_KQ_pointwise_max_abs_dev | 3518.01 | not_supported: BEM kernel is a first-principles blade-element implementation, not a B-series code; pointwise KT/KQ devia |
| NOT_SUPPORTED | B5-79 | KT_KQ_pointwise_max_abs_dev | 5579.23 | not_supported: BEM kernel is a first-principles blade-element implementation, not a B-series code; pointwise KT/KQ devia |

## 5. 注意点与局限

- **同一批 12 工况被反复计算**：台账里同一 case 有多条条目，分别来自不同任务/版本的算法路径（如 m1444 的 Re/剩余阻力模型、m1608 的 Holtrop 简化式）。它们是对不同实现的独立证据，不是重复计数；`mirrors` 字段把纯副本折叠掉，折叠了 141 份。
- **Rt 基线大面积不成立**：正典 `backend/data/benchmark_reference.json` 中 DTMB-5415/KVLCC2 的 `rt_status=WITHHELD`（R1 反解出的隐含湿面积越出物理带），因此这些 Rt 对标一律不判过 —— 这是「缺基线就不许 PASS」的直接后果，不是平台算得对。
- **旧基线仍在流通**：多个历史任务仍以 SUBOFF Rt=12.5 N / 21 N … 为参照，台账按正典重解为 92.52 N 等派生值，`baseline.note` 逐条记录了这一改判。若下游报告仍引用 12.5 N，需一并更正。
- **MOCK 条目不计入精度结论**：`artifacts/pilot_verification_report.json` 的 15 条对标里 expected 与 actual 逐位相同，通过率 100% 是同源自指，不构成精度证据。
- **NOT_SUPPORTED 不是失败**：桨 BEM 叶素核 vs B 系列回归的点对点 KT/KQ 差异（千分位到百分位千级）属于模型族差异；只有 η0max / J0 这类同物理量才做判决。
- **扫描面**：只覆盖仓库内的机读证据文件（`*.json`）；`*.md` 报告里的表格数字未纳入，因此台账是「机读证据」的下界而非全部文字结论。
- **口径局限**：中值口径假设实验带关于中值近似对称且中值有物理意义；对强不对称带（如对数正态型的试验散布）应改用几何中值，本台账未覆盖此情形。

## 6. 误差判定（逐项，回答两个必答问题）

### 6.1 全量对标是否 < 15%？

**否。** 以 REAL 基线为分母，36/91 条通过 15% 硬门 （39.56%），55 条违规（若把 MOCK 自指条目一并计入，51/106 = 48.11%，仍不满足「全量 <15%」）。违规按归因码分布为 SIMPLIFIED_HOLTROP_VARIANT 18、WETTED_SURFACE_FORMULA 16、RE_PHYSICS_INCONSISTENT 8、HOLTROP_OUT_OF_DOMAIN 7、RESIDUAL_FN_BLOWUP 4、RESISTANCE_FORCE_BASIS 2（每一码的物理含义见 §4.4 与 `ledger.py:ATTRIBUTION_RULES`）：本质是简化 Cr 与湿表面积建模在肥大船/适用域外失效，外加 m1444 那一版 Re/Cr 物理口径不自洽。结论与「>15% 不可接受」规则冲突 → 这些路径按不可接受处理，按船型（REAL 基线的过 15% 门数 / 可判数）：AFRAMAX-110k 0/4、DTMB-5415 6/16、KCS 0/6、KVLCC2 4/20、SUBOFF 12/31、n/a 4/4、propeller 10/10。证据族之间差异极大：最好 `docs/m1753` 10/10 = 100.0% 过 15% 门（3% 门 20.0%），最差 `docs/m1444` 0/8 = 0.0%（3% 门 0.0%）—— 同一仓里既有个位数百分比也有全灭，差距来自方法适用域与湿表面积/剩余阻力建模，不是统一的数值实现问题。

### 6.2 是否 < 3%？

**否。** 9/91 条达到 3% 目标 （9.89%）。剩余阻力是经验式主导项，达到 3% 的条目的量名分布（REAL 基线，过 3% / 可判）为 Cf 3/4、Ct 4/57、eta0_max 2/5；阻力（Ct/Rt）本身几乎不进 3% 带，达标的以解析式/回归可复现量为主。3% 目标在当前方法栈上属于未达成状态，需 CFD/高保真剩余阻力或标定后的 Holtrop-Mennen 才能推进。

### 6.3 关键量误差表（REAL 基线，按 abs(rel_err_mid) 升序取最近 12 条）

| case | 量 | computed | baseline(mid) | rel_err | <15% | <3% | 带内 |
|---|---|---|---|---|---|---|---|
| Cf@Re=1e+09 | Cf | 0.00153061 | 0.00153094 | -0.02% | 是 | 是 | — |
| B4-55 | eta0_max | 0.650377 | 0.65386 | -0.53% | 是 | 是 | — |
| Cf@Re=1e+08 | Cf | 0.00208333 | 0.00207203 | 0.55% | 是 | 是 | — |
| SUBOFF-02 | Ct | 0.003953 | 0.004 | -1.18% | 是 | 是 | — |
| SUBOFF-02 | Ct | 0.00395286 | 0.004 | -1.18% | 是 | 是 | — |
| B4-70 | eta0_max | 0.709179 | 0.695141 | 2.02% | 是 | 是 | — |
| SUBOFF-02 | Ct | 0.003913 | 0.004 | -2.18% | 是 | 是 | — |
| Cf@Re=1e+07 | Cf | 0.003 | 0.00293428 | 2.24% | 是 | 是 | — |
| KVLCC2-02 | Ct | 0.002913 | 0.003 | -2.90% | 是 | 是 | — |
| KVLCC2-01 | Ct | 0.002886 | 0.0028 | 3.07% | 是 | 否 | — |
| DTMB5415-01 | Ct | 0.004343 | 0.0045 | -3.49% | 是 | 否 | — |
| B3-80 | J0 | 0.99202 | 1.03043 | -3.73% | 是 | 否 | — |

### 6.4 本台账自身的可信度（自报分级）

- 台账机制：**REAL**（机检门 `--check` 逐条复算 rel_err ≤1e-6，WITHHELD 不判过）
- 覆盖面：**WITHHELD**（仅机读 JSON；`*.md` 表格未纳入，扫描面是下界）
- 判决口径：**REAL**（三口径可执行定义齐备，采用中值口径并有辅助字段披露带宽）
- 算法精度：**honest-FAIL** —— 「全量 <15%」与「<3%」两个问题都给出明确「否」，逐条归因，未粉饰；本台账不构成任何精度通过声明。
