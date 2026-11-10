# P167W2-N09 — 数值通道回填与入库门成熟化

> 生成方式：`python3 -m tools.repro_audit.generators.report`（本文件由 ledger 现算生成，
> 表格里的每个数字都来自工件 JSON，无一处手抄）。
> 清单指纹 `manifest_sha256 = cdc931189bf56f5a306752ddfa5cdbefbb69b0d5fe6adcf5a6d70675f033e0af`，
> 引擎指纹 `engine_sha256 = 2d34f27c92f443c9a031ecb746bb9625e5028133cf4769aa932b3b1ee485c556`，
> 审计时工作树 HEAD = `7fcd327b32a6074b08166df1d9cca3065ef2ee59`（提交前基线；入库门不判此字段，
> 只判清单/引擎指纹与逐通道等级，因此重放不受它影响）。

## ① 案例介绍（这是什么、为什么算能力缺口）

平台的历史验收声明里有大量「关键数字」——通过数、通过率、准确率、误差百分比、
记录条数——它们是报告与判据的原子单位。此前这些数字有两个结构性弱点：

1. **无法重放**：数字只存在于工件 JSON 里，没有「用明细算一遍」的通道，
   因此工件一旦被手改或明细被裁剪，没有任何机器判据能察觉；
2. **生成器是 MOCK**：`tools/repro_audit/generators/gen_*.py` 共
   27 个历史生成器虽然映射到 27 个工件，
   但它们多数直接返回**自造文档**（字段名与已提交工件都不同），
   属于「看起来有生成器」而不是「能重放」。

本节点把这件事做成可机械裁决的能力：**数值通道（channel）** = 一个已提交工件里的
一个关键数字 + 一条在提交树内能重放它的派生式；覆盖率只在
「重放一致 ∧ 幂等 ∧ 身份齐全 ∧ 无字面量回抄 ∧ 对输入敏感」五项全过（=L3）时才算数。

- 通道样本：**36 条**（`artifacts/repro_audit/channels.json`，先冻结后审计）
- 覆盖范围：14 个工件，跨 36 条断言通道
- 通道类型：tally（明细归约）30 条 / formula（派生量重算）6 条

## ② 怎么做的（方法 / 工具链 / 关键设置）

**派生引擎** `tools/repro_audit/generators/derive.py`
- 路径解析：`cases.status` 这类路径穿过 list 时自动映射成序列；
- 归约白名单：`len / count_eq / count_in / count_true / count_false /
  count_not_none / count_gt / distinct / sum / mean / min / max / get / abs`；
- 表达式求值走 AST 白名单（只许常量、名字 `n`、白名单归约调用、四则运算），
  拒绝属性链、下划线名字、内建函数的任意调用 —— 可审计、无任意执行面。

**冻结清单** `tools/repro_audit/generators/channel_specs.py`：36 条通道，
每条只写 `artifact / target / expr / kind`，**不写期望值**（期望值只能从工件读）。

**评级（`maturity.py`）**
| 等级 | 判据 |
|---|---|
| L0 | 工件缺失 / 目标路径缺失 / 派生报错 / 重放不一致 |
| L1 | 派生值 == 工件记录值（容差 1e-9 相对，近零 1e-12 绝对） |
| L2 | + 两次派生逐字节相等；+ engine/spec/source 三个 sha256 齐全 |
| L3 | + 派生式里没有等于被断言值的数值常量（无回抄）；+ 扰动后输出必变（非常函数） |

**扰动家族（全部机械、零领域知识）**：结构性削尾（first/mid/last）·
分类改写（把字符串叶子改成表达式里的分类值）· 布尔翻转 · 极值叶子定向扰动
（min/max 专用）· 数值缩放 1.001。本波共施加 103 次扰动，
每条通道都至少有一个「见证」扰动使其输出变化。

**入库门（fail-closed，`--gate`）** 通过条件（全满足才 exit 0）：
1. 台账存在可解析；2. 台账里的 manifest/engine/spec 指纹 == 当前树（否则判
**台账陈旧** = 工件不是当前生成器产出的）；3. 每条通道等级 == 新鲜审计等级；
4. 每条通道 ≥ 门槛等级；5. 成熟通道数 ≥ 门槛（本节点 24/36）且样本数 ≥ 36。
另有**报表引用门**：报告里出现的每个 `artifacts/*.json` 都必须有 ≥1 条成熟通道，
否则 fail-closed。

## ③ 计算结果

**覆盖率：36/36（100.0%），验收线 24/36 达标。**
等级分布：{'L0': 0, 'L1': 0, 'L2': 0, 'L3': 36}；历史注册表同口径基线：{'L0': 32, 'L1': 0, 'L2': 4, 'L3': 0, 'none': 0}。

| 通道 | 类型 | 工件 | 记录值 | 派生值 | 相对残差 | 等级 | 敏感性见证 |
|---|---|---|---|---|---|---|---|
| `benchmark_validation#total_cases` | tally | `artifacts/benchmark_validation.json` | 12 | 12 | 0.00e+00 | L3 | structural |
| `benchmark_validation#summary.passed` | tally | `artifacts/benchmark_validation.json` | 5 | 5 | 0.00e+00 | L3 | structural |
| `benchmark_validation#summary.failed` | tally | `artifacts/benchmark_validation.json` | 7 | 7 | 0.00e+00 | L3 | structural |
| `m1388_benchmark_audit#total_cases` | tally | `artifacts/m1388/benchmark_audit.json` | 12 | 12 | 0.00e+00 | L3 | structural |
| `m1388_benchmark_audit#status_counts.CONSISTENT` | tally | `artifacts/m1388/benchmark_audit.json` | 4 | 4 | 0.00e+00 | L3 | structural |
| `m1388_benchmark_audit#status_counts.PARTIAL` | tally | `artifacts/m1388/benchmark_audit.json` | 8 | 8 | 0.00e+00 | L3 | structural |
| `m1388_chain#SUBOFF.summary.total_cases` | tally | `artifacts/m1388/chain_results.json` | 4 | 4 | 0.00e+00 | L3 | structural |
| `m1388_chain#SUBOFF.summary.passed` | tally | `artifacts/m1388/chain_results.json` | 3 | 3 | 0.00e+00 | L3 | structural |
| `m1388_chain#DTMB-5415.summary.pass_rate` | formula | `artifacts/m1388/chain_results.json` | 0 | 0 | 0.00e+00 | L3 | boolean |
| `m1388_friction#Re1e6.rel_diff_pct` | formula | `artifacts/m1388/friction_crosscheck.json` | 6.306181 | 6.306181 | 0.00e+00 | L3 | extremum |
| `m1388_friction#Re1e9.rel_diff_pct` | formula | `artifacts/m1388/friction_crosscheck.json` | 0.021213 | 0.021213 | 0.00e+00 | L3 | extremum |
| `m642_batch#total_cases` | tally | `artifacts/m642/batch_report.json` | 130 | 130 | 0.00e+00 | L3 | structural |
| `m642_batch#success_count` | tally | `artifacts/m642/batch_report.json` | 125 | 125 | 0.00e+00 | L3 | structural |
| `m642_batch#failed_count` | tally | `artifacts/m642/batch_report.json` | 5 | 5 | 0.00e+00 | L3 | categorical |
| `pilot_verification#total_questions` | tally | `artifacts/pilot_verification_report.json` | 15 | 15 | 0.00e+00 | L3 | structural |
| `pilot_verification#pass_count` | tally | `artifacts/pilot_verification_report.json` | 15 | 15 | 0.00e+00 | L3 | structural |
| `pilot_verification#fail_count` | tally | `artifacts/pilot_verification_report.json` | 0 | 0 | 0.00e+00 | L3 | categorical |
| `timing_baseline#total_records` | tally | `artifacts/timing_baseline.json` | 55 | 55 | 0.00e+00 | L3 | structural |
| `timing_baseline#baseline.stability.count` | tally | `artifacts/timing_baseline.json` | 5 | 5 | 0.00e+00 | L3 | structural |
| `capability_ledger_resolved#summary.capabilities` | tally | `artifacts/capability_ledger/capability_ledger_resolved.json` | 21 | 21 | 0.00e+00 | L3 | structural |
| `capability_ledger_resolved#summary.with_evidence` | tally | `artifacts/capability_ledger/capability_ledger_resolved.json` | 3 | 3 | 0.00e+00 | L3 | structural |
| `capability_ledger_resolved#summary.without_evidence` | tally | `artifacts/capability_ledger/capability_ledger_resolved.json` | 18 | 18 | 0.00e+00 | L3 | structural |
| `capability_ledger_resolved#divergence_tally.UNVERIFIED` | tally | `artifacts/capability_ledger/capability_ledger_resolved.json` | 15 | 15 | 0.00e+00 | L3 | structural |
| `optimizer_toy#optimization_DE.n_eval` | tally | `artifacts/optimizer_toy.json` | 1023 | 1023 | 0.00e+00 | L3 | structural |
| `optimizer_toy#optimization_DE.best_fun` | tally | `artifacts/optimizer_toy.json` | 981.46 | 981.46 | 0.00e+00 | L3 | extremum |
| `optimizer_toy#surrogate_verification.error_pct` | formula | `artifacts/optimizer_toy.json` | 52.085664 | 52.085664 | 0.00e+00 | L3 | extremum |
| `planner_golden#total` | tally | `artifacts/planner/golden_annotation_set.json` | 32 | 32 | 0.00e+00 | L3 | structural |
| `planner_golden#train_size` | tally | `artifacts/planner/golden_annotation_set.json` | 20 | 20 | 0.00e+00 | L3 | structural |
| `planner_scoring#train.accuracy` | formula | `artifacts/planner/scoring_report.json` | 0.65 | 0.65 | 0.00e+00 | L3 | extremum |
| `planner_scoring#train.correct` | tally | `artifacts/planner/scoring_report.json` | 13 | 13 | 0.00e+00 | L3 | extremum |
| `planner_scoring#held_out.correct` | tally | `artifacts/planner/scoring_report.json` | 7 | 7 | 0.00e+00 | L3 | extremum |
| `lineage_test#total_executions` | tally | `artifacts/lineage_test.json` | 10 | 10 | 0.00e+00 | L3 | structural |
| `lineage_test#completeness_rate` | formula | `artifacts/lineage_test.json` | 1 | 1 | 0.00e+00 | L3 | extremum |
| `benchmark_integrity#n_cases` | tally | `artifacts/benchmark_integrity/audit.json` | 12 | 12 | 0.00e+00 | L3 | structural |
| `benchmark_integrity#verdict_counts.WITHHELD` | tally | `artifacts/benchmark_integrity/audit.json` | 8 | 8 | 0.00e+00 | L3 | structural |
| `baseline_integrity#summary.cases` | tally | `artifacts/baseline_integrity/audit_canon_v3.json` | 12 | 12 | 0.00e+00 | L3 | structural |

**历史注册表基线（同口径，前 12 条；其余 24 条见工件）**

| 通道 | 历史生成器 | 等级 | 原因 |
|---|---|---|---|
| `benchmark_validation#total_cases` | `tools/repro_audit/generators/gen_benchmark_validation.py` | L2 | no_external_input |
| `benchmark_validation#summary.passed` | `tools/repro_audit/generators/gen_benchmark_validation.py` | L0 | replay_mismatch |
| `benchmark_validation#summary.failed` | `tools/repro_audit/generators/gen_benchmark_validation.py` | L0 | replay_mismatch |
| `m1388_benchmark_audit#total_cases` | `tools/repro_audit/generators/gen_m1388_audit.py` | L0 | replay_mismatch |
| `m1388_benchmark_audit#status_counts.CONSISTENT` | `tools/repro_audit/generators/gen_m1388_audit.py` | L0 | replay_mismatch |
| `m1388_benchmark_audit#status_counts.PARTIAL` | `tools/repro_audit/generators/gen_m1388_audit.py` | L0 | replay_mismatch |
| `m1388_chain#SUBOFF.summary.total_cases` | `tools/repro_audit/generators/gen_m1388_chain.py` | L0 | replay_mismatch |
| `m1388_chain#SUBOFF.summary.passed` | `tools/repro_audit/generators/gen_m1388_chain.py` | L0 | replay_mismatch |
| `m1388_chain#DTMB-5415.summary.pass_rate` | `tools/repro_audit/generators/gen_m1388_chain.py` | L0 | replay_mismatch |
| `m1388_friction#Re1e6.rel_diff_pct` | `tools/repro_audit/generators/gen_m1388_friction.py` | L0 | replay_mismatch |
| `m1388_friction#Re1e9.rel_diff_pct` | `tools/repro_audit/generators/gen_m1388_friction.py` | L0 | replay_mismatch |
| `m642_batch#total_cases` | `tools/repro_audit/generators/gen_m642_batch.py` | L0 | replay_mismatch |

**入库门 fail-closed 实证**（`artifacts/repro_audit/gate_provocation.json`）：
把 `artifacts/lineage_test.json` 的 `total_lineage_records`
由 10 手改为 9（明细不动）：

- 改动前 sha256 = `33a4ab7269ed0ce8…`，改动后 `04920e9faaf8a780…`
- 门判定：passed=False（2 failures），
  失败项：replay_grade_drift; below_min_grade
- `git checkout` 复原后 sha256 == 原值（True），门重新转绿
  （passed=True）
- 结论：`fail_closed_demonstrated = True`

**幂等**：同一棵树连续两次 `--audit` 输出逐字节相等（`cmp` 通过，见 §⑥）。

## ④ 结果图

- `artifacts/repro_audit/fig1_grade_distribution.png`
  —— 基线 vs 回填后的等级分布堆叠柱（含 24/36 验收线）。基线 L0=32、
  L2=4、L3=0；回填后 L3=36。
- `artifacts/repro_audit/fig2_derivation_soundness.png`
  —— 左：逐通道重放相对残差（对数轴，全部落在 1e-9 容差线以下，最大 0.00e+00）；
  右：敏感性见证的扰动家族分布 {'structural': 25, 'boolean': 1, 'extremum': 8, 'categorical': 2}。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **两类通道的证据强度不同**：`formula` 通道是「用工件里独立记录的量重算派生断言」
   （分子/分母都在工件里）；`tally` 通道是「头部数字 ↔ 明细行一致」。
   后者证明的是**自洽**，不是「明细本身算得对」—— 明细对不对要靠各领域仓的物理判据，
   本模块不越界判物理。
2. **不构造独立数据源**：通道不重跑生成工件的业务流程（那会引入 DB / 网络 / 随机性），
   因此它保证「数字与它声明的入量一致且可重放」，不保证「入量本身是这次业务真实产出的」。
3. **清单是人工策展的**：36 条是本波声明的审计采样面，不是平台上全部数字；
   覆盖率因此是「本采样面的成熟率」，不可外推成全平台 100%。
4. **历史生成器不是没有价值**：它们能跑、能返回结构完整的文档，但与已提交工件字段对不上，
   同口径下 L3=0；保留它们会给出「已有生成器」的错觉 ——
   所以本节点的门**只认台账里 L3 的通道**，不认「文件存在」。
5. **扰动敏感性是必要不充分条件**：常函数一定不敏感（能抓 MOCK），但敏感也可能是
   「无关但随机的耦合」；因此与「无字面量回抄」并用。
6. **门是 fail-closed 的代价**：任何生成器/清单/工件改动都会让台账陈旧、门转红
   （本波开发中真实发生过：改 `derive.py` 后门立即红，重跑 `--audit` 才恢复）。
   这是设计意图，不是缺陷。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 判据 | 实测 | 是否达标 |
|---|---|---|---|
| 通道覆盖率 | ≥ 24/36 | 36/36 | 是 |
| 逐通道重放相对残差 | ≤ 1e-9（相对） | 最大 0.00e+00 | 是（0.00e+00 ≪ 3%──**是否<3%：是**） |
| 幂等性 | 两次运行逐字节相等 | 相等 | 是 |
| 通道等级下限 | 全部 L3 | L0=0, L1=0, L2=0, L3=36 | 是 |
| 门禁效力 | 篡改后必须 exit 非零且复原后转绿 | 篡改→2 failures；复原→passed=True | 是 |
| 基线对照 | 历史注册表同口径 L3 | 0/36 | 对照成立（0 → 36） |

**是否 <3%：是。** 全部 36 条通道的重放残差最大 0.00e+00，远低于 1e-9 门槛，
更远低于 3% 的精度红线；这是确定性算术派生，误差只来自浮点表示，不含模型误差。

**honest FAIL / 未覆盖部分**
- 历史 `gen_*.py` 注册表中的 27 个生成器**未做改造**（本波只冻结基线、不动它们）：
  同口径 L3 = 0，属已知 MOCK 面，后续节点可逐个替换为派生式。
- 平台其余工件未纳入本次 36 条采样面（见注意点 3）。
- `baseline_integrity/audit_canon_v3.json` 的 `summary.pass = 2` 无法由
  `case_results[*].verdict` 派生（12 行全为 `PARTIAL`，`count_eq('case_results.verdict','PASS') = 0`）：
  该头部数字目前**无通道支撑**，需补口径说明或修正工件。该条**未**计入 36 条采样面。
