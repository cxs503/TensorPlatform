# 判级者间一致性门与可靠度置信区间（IN55W3-N07）

> mission 10363 · 分支 `agent/m10363` · 工件 `artifacts/m10363/grader_reliability.json`

## ① 案例介绍

S×E 判级体系给每个成果打 S1–S5 + E0–E4。此前台账里只有一个等级，没有任何「换成另一个判级者会不会给同一等级」的证据，因此所有 S×E 数字都缺可靠性区间。本节点测的是**测量可靠性**（判级者间一致性），不是等级内容本身。

**数据源**：`outcome_evaluations`（只读单条 SELECT），判级者身份 = `reviewer` 字段；缺身份者记 `UNKEYED`，计入覆盖率分母且**不插补**；`S-N`（否决轨）不同构念，不参与序数 α。默认纳入 `confirmed` + `candidate` 两种已判级状态（`candidate` 只是不计入 S×E **分布**统计，它仍是一次真实的判级行为），并在 3.4 给 confirmed-only 的敏感性对照。

| 量 | 值 |
|---|---|
| 记录数 | 4532 |
| 有判级者身份 | 4532 |
| UNKEYED（不插补） | 0 |
| UNKEYED 占比 | 0.000 |
| Mission 数 | 4484 |
| 可配对（≥2 判级者）Mission | 0 |
| 不同判级者键数 | 5 |
| 单 Mission 最多判级者数 | 1 |
| S-N 记录（不入序数尺度） | 48 |
| 多记录 Mission（≥2 条判级） | 0 |
| **跨判级者**配对（真间一致性证据） | 0 |
| 同一判级者键内配对（内部离散） | 0 |

判级者键是**席位复合键**（如 `seat-执行+seat-台账`），不是自然人；因此 α 度量的是复合判级键之间的一致性。

**关键事实**：生产集里**没有任何一个 mission 被两个不同判级者评过**（跨判级者配对 = 0）——所以生产口径下 α 根本不可算。台账里唯一的同成果双评来自「被判据拒收的尝试」（它们与有效记录共享 `artifact_id`），见 3.4b 的探索性区间。

## ② 怎么做的

**系数实现（自写，环境不保证有第三方可靠性库）**：Krippendorff α（ordinal 主指标 / nominal 敏感性）、Gwet AC1/AC2、Fleiss κ、ICC(1,1)/ICC(2,1)/ICC(2,k)，全部带按单元（mission）重采样的 bootstrap CI。系数正确性靠**解析可验证性质**钉住：两判级者名义 α 在大样本下 → Scott π、Fleiss κ(=2 判级者) = Scott π、AC2 = AC1（K=2）、完美一致 α=1、缺格单元不计入 n。

**合成恢复夹具**（可控真值，所有量以「级」为单位）：

```
l_i ~ p(l)                                   # 真实等级, l in {1..5}
r_ig = clip(round(l_i + b_g + e_ig), 1, 5),  e_ig ~ N(0, sigma^2)
```

总体极限 α 用条件分布精确枚举（`P(r=c|l)=Phi((c+.5-l-b)/s)-Phi((c-.5-l-b)/s)`，端类别边界取 ±∞ 以复现 clip），因此 α_true **不含抽样噪声**；再二分校准 σ 命中 α_true∈{0.2,0.5,0.8}。注入实验用 +0.5（lenient）/ −0.5（harsh）级偏置，与基线**同随机数配对**，Δα 的抽样噪声被消掉。

**方差分解** `grade ~ 1 + (1|grader) + (1|mission)`：本台账「一个 mission 基本只被一个判级者评」使 mission 与判级者效应不可分离识别，故只识别 φ_g = σ²_grader/(σ²_grader+σ²_(mission+噪声))（不等组单向随机效应矩估计，Searle 口径）；mission 分量由多评子样本反解并标注可识别性。

**判据与阈值**（单点契约 `tools/eval_reliability/acceptance.py:THRESHOLDS`）：α 门 0.667、可判最少双评单元 50、UNKEYED 上限 0.5、CI 半宽目标 0.1。

## ③ 计算结果

### 3.1 判据裁决

| 判据 | 含义 | 数值 | 阈值 | 状态 |
|---|---|---|---|---|
| J1 | α 真值恢复（3 档×N 重复） | max_abs_bias=0.006<br>max_mae=0.043<br>per_level[3] | max\|bias\|≤0.05 且 max MAE≤0.05 | **PASS** |
| J2 | 95% CI 覆盖率（按单元 bootstrap；BC 主口径 + 百分位敏感性） | min_coverage_bc=0.920<br>min_coverage_percentile=0.905<br>max_halfwidth=0.099<br>per_level[3] | BC 覆盖率 ≥0.9（百分位法并列报出） | **PASS** |
| J3 | ICC 功效（n=100,k=3,ICC=0.3）+ ICC=0 负控 | power=1.000<br>false_positive_rate=0.033<br>n_units=100<br>n_raters=3<br>reps_used=300 | power≥0.8 且 FP≤0.1 | **PASS** |
| J4 | 真实台账可判性（WITHHELD 语义 + 最小审数表） | n_pairable=0<br>unkeyed_share=0.000<br>expected=WITHHELD<br>verdict=WITHHELD<br>min_rating_table_present=True<br>min_table_examples[4] | n_pairable≥50 且 UNKEYED≤0.5 才可判 | **PASS** |
| J5 | 注入 lenient(+0.5)/harsh(−0.5) 判级者的 Δα | delta_alpha_theory=0.083<br>delta_alpha_hat_mean=0.081<br>delta_alpha_hat_sd=0.018<br>relative_error=0.019<br>sigma=0.516<br>reps_used=200 | 相对误差≤0.1 | **PASS** |
| J6 | 决策门工作特性（α=0.8 应放行 / α=0.5 应拦下） | good(alpha_true=0.800, expected=PASS, rate=1.000, reps_used=66)<br>bad(alpha_true=0.500, expected=REQUIRE_THIRD, rate=1.000, reps_used=66) | 放行率≥0.9 且拦下率≥0.9 | **PASS** |

**总裁决**：`PASS`（FAIL=[]，WITHHELD=[]）

### 3.2 恢复实验（真值可控）

| α_true | σ | mean α̂ | bias | MAE | SD | CI 覆盖率(BC) | 覆盖率(百分位) | CI 半宽 | 重复 |
|---|---|---|---|---|---|---|---|---|---|
| 0.200 | 2.146 | 0.198 | -0.0025 | 0.0430 | 0.0547 | 0.920 | 0.905 | 0.099 | 200 |
| 0.500 | 1.097 | 0.494 | -0.0062 | 0.0425 | 0.0548 | 0.925 | 0.910 | 0.098 | 200 |
| 0.800 | 0.516 | 0.796 | -0.0040 | 0.0244 | 0.0292 | 0.945 | 0.950 | 0.057 | 200 |

### 3.3 注入 lenient/harsh（±0.5 级）

| 量 | 值 |
|---|---|
| 基线总体 α（真值） | 0.8000 |
| 注入后总体 α | 0.7170 |
| 理论 Δα | 0.0830 |
| 实测 Δα̂（配对均值） | 0.0814 |
| 实测 Δα̂ 的 SD | 0.0178 |
| 相对误差 | 0.0194 |
| σ | 0.5162 |
| 配对重复数 | 200 |

### 3.4 真实台账一致性系数（探索性；n_pairable 见 ①）

| 系数 | 值 |
|---|---|
| alpha_ordinal | n/a |
| alpha_nominal | n/a |
| alpha_n_pairable_units | 0 |
| gwet_ac1 | n/a（无完整格/无可配对单元） |
| gwet_ac2 | n/a（无完整格/无可配对单元） |
| fleiss_kappa | n/a（无完整格/无可配对单元） |
| icc_1_1 | n/a（无完整格/无可配对单元） |
| icc_2_1 | n/a（无完整格/无可配对单元） |
| icc_2_k | n/a（无完整格/无可配对单元） |
| icc_3_k | n/a（无完整格/无可配对单元） |
| icc_n_units | 0 |
| alpha_ci_lo | n/a（无完整格/无可配对单元） |
| alpha_ci_hi | n/a（无完整格/无可配对单元） |
| alpha_ci_halfwidth | n/a（无完整格/无可配对单元） |
| alpha_ci_status | insufficient_pairable_units |
| Gwet/κ 完整格 | n/a（gwet_ac1 需要至少一个完整单元且判级者数 ≥2） |

**配对结构**（口径 = 含 rejected 的全部判级尝试，见 3.4b）：同 mission 配对共 13 对，其中**跨判级者** 13 对、同一判级者键内 0 对（跨判级者配对平均 |Δ级| = 0.154）。跨判级者配对是唯一能支撑「判级者间一致性」的证据；同一判级者键内的差异属于判级者内部离散，不能混为一谈。

**状态敏感性（结论不依赖 candidate 的取舍）**

| 纳入状态 | 记录数 | 跨判级者配对 | UNKEYED 占比 | α̂(n_pairable) |
|---|---|---|---|---|
| confirmed | 4370 | 0 | 0.000 | n/a (n=0) |
| confirmed,candidate | 4532 | 0 | 0.000 | n/a (n=0) |
| confirmed,candidate,rejected | 4545 | 13 | 0.000 | 0.997 (n=13) |

### 3.4b 探索性：全部判级尝试（含被判据拒收）——台账首份可靠性区间

口径：confirmed+candidate+rejected（含被判据拒收的尝试）；记录 4545 条，可配对（跨判级者）Mission **13** 个，跨判级者配对 13 对。

| 量 | 值 |
|---|---|
| α̂（S 轨，Krippendorff ordinal） | 0.997 |
| S 轨可配对单元 n | 13 |
| S 轨 95% CI（按单元 bootstrap，仅重采样变异性） | [0.981, 0.999]（半宽 0.009） |
| S 轨逐对一致率 + Clopper-Pearson 95% | 12/13 = 0.923，精确区间 [0.640, 0.998] |
| α̂（E 轨） | -0.413（n=13） |
| E 轨逐对一致率 + Clopper-Pearson 95% | 1/13 = 0.077，精确区间 [0.002, 0.360] |

| mission | 判级者 A → B | 等级 | E 档 | \|ΔS\| |
|---|---|---|---|---|
| 3354 | platform-evidence-scan → seat-delivery+seat-exe | S1 / S1 | E1 / E0 | 0 |
| 3355 | platform-evidence-scan → seat-execution+seat-le | S1 / S1 | E1 / E0 | 0 |
| 3393 | platform-evidence-scan → seat-execution+seat-le | S1 / S1 | E1 / E0 | 0 |
| 3433 | platform-evidence-scan → seat-delivery+seat-exe | S1 / S1 | E1 / E0 | 0 |
| 3466 | platform-evidence-scan → seat-execution+seat-le | S1 / S1 | E1 / E0 | 0 |
| 3486 | platform-evidence-scan → seat-ledger+seat-adver | S1 / S1 | E1 / E0 | 0 |
| 3532 | platform-evidence-scan → seat-adversarial+seat- | S1 / S1 | E1 / E0 | 0 |
| 3588 | platform-evidence-scan → seat-delivery+seat-exe | S1 / S1 | E1 / E0 | 0 |
| 3627 | platform-evidence-scan → seat-execution+seat-le | S1 / S1 | E1 / E0 | 0 |
| 3629 | platform-evidence-scan → seat-delivery+seat-exe | S1 / S1 | E1 / E0 | 0 |
| 3632 | platform-evidence-scan → seat-adversarial+seat- | S1 / S1 | E1 / E0 | 0 |
| 3723 | platform-evidence-scan → seat-ledger+seat-adver | S1 / S1 | E1 / E0 | 0 |
| 5025 | seat-adversarial+seat- → tp-reviewer | S5 / S3 | E2 / E2 | 2 |

⚠ rejected 行是被判据拒收的尝试，其等级可能只是拒收前的草稿；但它们是台账中仅有的同成果双评证据，故作为探索性区间报出，生产裁决仍只用 confirmed/candidate。

**这条区间的含义**：它是 S×E 台账**第一次**拿到的可靠性区间，但 n=13 远低于 50 的出数下限，因此**只能作探索性参考，不得作升账依据**（门的裁决仍是 WITHHELD）。

**⚠ 反保守警告（必读）**：在这种「13 对里 12 对完全相同」的稀疏结构下，bootstrap 重采样**造不出未观察到的分歧模式**，α̂ 的 bootstrap CI 半宽只有 0.01 量级，明显小于真实不确定性。因此本报告并报 **Clopper-Pearson 精确二项区间**作为按逐对一致率的保守界——上表两者的差距本身就是「小样本下不要相信窄区间」的证据。

**观察到的机制**：12/13 对的 S 等级**完全相同**（都是 S1），分歧只出现在 E 轨（被判据拒收的尝试给 E0，E0 按规则不可登记）；唯一一对 S 分歧（mission 5025）是 2 档（S3 vs S5）且拒收原因是不足 50 字理由。即：这批数据里 **S 档判级稳定、E 档与理由完备性才是失败点**——这条结论只有做了逐对拆解才看得见。

### 3.5 判级者方差占比 φ_g

| 量 | 值 |
|---|---|
| φ_g（判级者方差占比） | 0.632 |
| σ²_grader | 0.283 |
| σ²_(mission+噪声) | 0.165 |
| σ²_mission（多评子样本反解） | n/a |
| 多评 Mission 数 | 0 |
| mission 分量可识别 | False |

φ_g 的 95% CI（按 mission 聚类 bootstrap）：[0.604, 0.659]

| 判级者键 | 记录数 | 平均 S 等级 | E 档中位数 |
|---|---|---|---|
| seat-execution+seat-ledger+seat-adversarial | 387 | 1.894 | 3 |
| seat-adversarial+seat-delivery+seat-execution | 397 | 1.826 | 3 |
| seat-ledger+seat-adversarial+seat-delivery | 428 | 1.800 | 3 |
| seat-delivery+seat-execution+seat-ledger | 403 | 1.779 | 3 |
| platform-evidence-scan | 2917 | 1.000 | 2 |

**⚠ φ_g 的读法边界**：这里的 φ_g 主要反映「不同席位判级键给出的等级水平差异」，而判级者键与时间段、成果构成高度混杂（每个键主导的窗口不同），因此**不能**读作「判级者个人效应」；mission 分量本身不可识别（多评 Mission = 0）。把它当「判级配置差异的方差份额」而非「人的方差份额」，才是诚实的解释。

### 3.6 决策门裁决

- 状态：**WITHHELD**（enforcement=SUSPENDED）
- 理由：alpha_unavailable; n_pairable=0<50
- 判级配置分布：单评 4484 / 双评 0 / 三评+ 0
- 标签分布：{'UNRELIABLE': 4484}
- 补齐到可判还需 **50** 次独立第二判级

### 3.7 最小审数表（CI 半宽 ≤0.10）

| α_true | 判级者数 | 实测最小 n | 外推 n* | 实测点（n: 半宽） |
|---|---|---|---|---|
| 0.500 | 2 | — | 229 | 25:0.297; 50:0.254; 100:0.149; 200:0.107 |
| 0.500 | 3 | 200 | — | 25:0.211; 50:0.173; 100:0.106; 200:0.075 |
| 0.800 | 2 | 100 | — | 25:0.164; 50:0.111; 100:0.085; 200:0.055 |
| 0.800 | 3 | 50 | — | 25:0.125; 50:0.091; 100:0.056; 200:0.042 |

## ④ 结果图

**图1 J1/J2：α 真值恢复（含 CI 半宽）与 95% CI 覆盖率** — `artifacts/m10363/figures/fig1_alpha_recovery.png`

**图2 J5：注入 ±0.5 级 lenient/harsh 判级者后 Δα 的理论 vs 实测** — `artifacts/m10363/figures/fig2_injection.png`

**图3 真实台账判级画像：各判级者键的等级构成** — `artifacts/m10363/figures/fig3_grader_profile.png`

**图4 J4：CI 半宽 ≤0.10 所需最小评审判数** — `artifacts/m10363/figures/fig4_min_rating_table.png`

**图5 J6：决策门工作特性与真实台账 mission 级标签分布** — `artifacts/m10363/figures/fig5_gate.png`

**图6 系数口径对照（左，生产口径 n_pairable=0 故留空/仅显示可算项）与同 mission 逐对差值（右，口径=全部尝试含 rejected）** — `artifacts/m10363/figures/fig6_coefficients.png`

## ⑤ 注意点（坑与边界）

1. **总体 α 不能照抄样本公式**：把 `D_e` 的 `n(n−1)` 修正搬到总体极限上，会让「完全随机判级」的 α 地板假抬到 1/m（m=4 时 0.25），低 α 档真值整体偏 0.1。本模块总体 α 用 `n²`，并在 N=20000 大样本上与样本 α 对齐到 0.002 以内。
2. **端类别边界必须取 ±∞**：`clip` 把超界概率质量折回端类别，按 0.5/5.5 切边界会漏算折算质量——这一条在 α_true=0.2 档造成 +0.10 的假偏差，是本节点最大的坑。
3. **bootstrap 必须按单元重采样**：按判级值重采样把 n 算大、CI 算窄，覆盖率虚高，是典型的假自信。
4. **φ_g 的可识别性依赖设计**：单评审级使 mission 与判级者效应混杂；本模块只报可识别的 φ_g，mission 分量标 `mission_component_identifiable`，且矩估计 `σ̂²=max(0,·)` 在 σ²_g→0 时下侧偏保守。
5. **α 与边际分布绑定**：序数度量是可分解度量，期望分歧由边际决定。同一 σ=0.8 换先验，总体 α = 0.647（对称）/ 0.753（均匀）/ 0.574（偏斜）——跨批次比较 α 时必须同时报边际。
6. **判级者键≠人**：复合席位键把多个席位合并成一次判级，跨键一致性只说明「不同席位组合的判级口径是否一致」，不能反推个体（人或模型）的可靠性，更不可用于排名或画像（台账纪律：禁加权禁排名）。
7. **AC2 口径**：机会一致率在文献有多种写法，本实现取线性权重 + Gwet 边际口径，只作敏感性指标，不参与决策门。

## ⑥ 误差判定（逐项回答「是否 <3%」）

本节点的「基准」是**可控真值**（生成模型的总体极限 α），不是文献值，因此 3% 相对误差口径不适用：α 是 0–1 系数，3% 相对误差在 α≈0.2 时等同绝对误差 0.006，比抽样 SD 小一个量级，没有判别意义。等价的可判口径是「|bias| ≤0.05 个 α 单位」与「CI 覆盖率 ≥90%」。

| 量 | 值 | 判定 |
|---|---|---|
| 最大 \|bias\| (α̂ vs α_true) | 0.0062 | ≤0.05 达标 |
| 最大 MAE（单次判级典型误差） | 0.0430 | ≤0.05 达标 |
| 最小 CI 覆盖率 | 0.920 | ≥0.90 达标 |
| 注入 Δα 相对误差 | 0.0194 | ≤0.10 达标 |

**是否 <3%**：不适用（理由见上，逐项数字见表）。**真实台账 α 一律 WITHHELD**：可配对 Mission 仅 0 个、UNKEYED 占比 0.000，达不到 50 的下限，故不对**生产口径**的真实 α 报数值区间——这是 honest WITHHELD，不是 PASS；唯一的双评证据（被判据拒收的尝试）以探索性区间 + 保守精确区间的形式在 3.4b 给出，并明确标注不得作升账依据。

## 局限与下一步

1. 真实 α 不可判的根因是**设计**：需给成果配第二个独立判级者；本报告已给出「还需 50 次第二判级」的最小可派工投入量。
2. 判级者身份需拆解为真实独立席位/模型，否则 α 只能解释为「复合键口径一致性」。
3. α 的区间估计在小 n 下用 BC bootstrap；如需更高精度可加 BCa（需要加速度项，本节点先不引入以免小样本下反而失真）。
