# P44-N09 honest-FAIL 欠账结构化提取：从报告误差表与 pending 终态判词到可再派 backlog

**任务**：把报告里的 FAIL/WITHHELD 行变成结构化、去重、可再派的 backlog（离线提取与评测，不改 daemon、不接线派单）。**仓库**：TensorPlatform（新域目录 `tools/evidence/backlog_extraction/`）。

---

## ① 案例介绍：语料、物理/工程背景与范围

欠账（debt）不是抽象账目，而是**物理量没达标**的记录：砰击峰值压力对 Chuang 型参考差 −21.5%、溃坝前沿位置对 Martin–Moyce 实验差 −19.9%、KVLCC2 总阻力系数对 Tokyo-2015 差 −48%、空气润滑气相分数剖面未复现 published band……这些行散落在验收报告的「误差判定」表与求解器仓`benchmarks/pending/*/terminal_verdict*.md` 的「四门判决」表里，人读得懂、机器读不了，于是同一欠账被反复重派或永远沉睡。本节点的物理/治理目标只有一个：**把这些行变成机器可审计、可去重、可再派的结构化条目**，并给出可复算的抽取质量判据。

**语料（离线冻结，两次运行逐字节可复现）**：报告 31 篇（220496 字符，来自 `tensor_reports` 只读快照）+ pending 终态判词 5 份（来自 `/root/tensor-ecosystem/TensorLBM/benchmarks/pending/*/terminal_verdict*.md`）。

| 语料分组 | 规模 | 覆盖内容 |
|---|---|---|
| plan-21（IN20 续航军，12 节点） | 报告 10/12 | 平台治理单：补给器/放门/合并口/提案通道/停摆自愈/退出仪表盘/D1–D4 求解器续刀/G21 验收门 |
| P21（plan-44 `multi_domain_verification_p21`，9 节点） | 报告 8/9 | 物理单：CAD 几何质量、BEM 面元阶、砰击 Wagner/Chuang、FSI 颤振、空气润滑、甲板上浪、风力风筝 |
| pending 终态判词（TensorLBM 4 benchmark） | 5 份 | dam_break_sc / dit_turbulence / hadamard_rybczynski_droplet / permeability_fibrous 的四门判决 |
| 扩样 12 篇（为达 ≥60 欠账行） | 12 篇 | 操纵性绝对精度、板辐射效率、几何保真契约、阻力基准法则、冰区破损稳性、FuelEU 等含 honest-FAIL 的报告 |

**范围声明**：plan-21 的 12 节点里 m5051（TensorLBM 续刀）与 m5054（TensorSPH 续刀）在库中**无报告**，P21 的 m5353（边界门）同样缺报；这 3 个节点无一手误差表，故不进金标、覆盖率表里标 ✗（**honest gap，不补零外推**）。

## ② 怎么做的：方法、流程与关键设置

### 2.1 欠账 schema（唯一数据契约）

`{repo, case, metric, measured, reference, error_pct, threshold_pct, lt_3pct, verdict, rootcause, requeue_criterion, depends_on, source{kind,file,line,…}, evidence}`，其中 `rootcause ∈ {reference_limited, discretization_resolution, implementation_defect, reference_missing}`（frozen dataclass + `validate()` 拒绝越界取值），指纹 `fingerprint = sha1(repo|case|metric_norm|error_bucket)` 保证同一欠账在不同来源里指纹一致。

### 2.2 提取器（禁宽正则，逐字符状态机）

- **表格解析** `mdtables.py`：GFM 子集，反引号状态机切单元格（`\|` 转义、行内代码里的 `|` 不切），每行保留**物理行号** → 一切结论可回查原文一行。
- **数值解析** `numeric.py`：只从单元格**开头**扫一个数（支持 `±`、Unicode 负号、千分位、科学计数法），整串被完整消耗才算「纯数字」；`10.526` 按 10.526%（不是 1052.6%），`0.169` 按比值→16.9%，带尾部注释的单元不做比值标定（曾经的真实 bug 就在这两处）。
- **列角色推断**：表头同义词（`关键量/quantity/reference/误差/<3%？/是否 <3%/裁定/通过?`），空白与全角先归一；判词列兜底要求「短单元格 + ≥60% 命中」，防止 JSON 证据文本里的 `honest-FAIL` 被当成判词。
- **判词归类**：`否（>3%，但 ≤15% 判据 ✓）`→FAIL、`❌`→FAIL、`succeeded ×9`→非判词、`GATE_REJECT`→非判词（负控被正确拒绝是**通过**）。
- **抑制规则**（防假阳性的三条硬规矩）：负例/负控/演练矩阵小节整体跳过；行内出现「不是误差/归因唯一/负例/负控」跳过；非判决族小节必须自带判词列才解析。
- **多检查点展开**：一行带多个误差列（`T=1/T=2/T=2.96 err%`）时展开成多条，量名 `<行> @ <列>`，再派时定位精确到检查点。
- **根因分类**：可解释 cue 打分（强 cue 3 分/弱 cue 1 分，`PRIORITY` tie-break），「同上」继承上一行基准文本；无 cue 时 3–6% 轻微超差默认归「离散/分辨率」。

### 2.3 金标（人工逐行标注）

金标 = 语料内所有判决族表行（341 行）中被提取器命中或被人工判为欠账的行，**行级**（`文件短键:行号`）标注 verdict 与根因类：
- 金标欠账 **72 行**，金标 ok（负例/覆盖状态/聚合摘要）9 行；
- 逐行标注说明与分歧点写在 `artifacts/backlog_extraction/gold.json:meta.honest_notes`；
- 诚实披露：提取规则在 7 个文件上调过参 （`r6525, r6550, r6521, r5760, r6210, r6231, r5779`），故另报 **holdout** 指标（见 ③）。

### 2.4 去重（双口径）与 dry-run 提案

- **字段口径** = 指纹相等（仓+案例+量+误差分桶）；**语义口径** = 量名 token 的 Jaccard（拉丁词 ≥3 字符或含数字的 token + CJK 二元组），`≥0.85` 且**同案例**才算重复；同案例 + 误差同桶 + token 包含关系 → 粗细粒度重复（如判词表 `a=40` 与报告 `a=40 @ T=1 err%`）。
- 比较对象：`campaign_nodes`（plan-21/44 共 21 个节点，只读 SQL 导出）+ 历史提案池 504 条提案（`/root/tp-agent/proposals/**/*.json`）。
- **dry-run**：生成提案草案 76 条，因近重被抑制 0 条，派单尝试 `False`、网络写请求 `0` → 结论 **ZERO_DUPLICATE_PROPOSALS**。

## ③ 计算结果（程序化注入）

### 3.1 提取与判据门

| 指标 | 值 | 门槛 | 结论 |
|---|---|---|---|
| 金标欠账行 | 72 | ≥60 | ✅ |
| 提取行数（去重后 backlog） | 76 | — | — |
| 行级召回 | 1.0000 | ≥0.90 | ✅ |
| 行级误报率 | 0.0000 | ≤0.10 | ✅ |
| 根因 macro-F1（行级） | 1.0000 | ≥0.80 | ✅ |
| 根因 macro-F1（严格逐行） | 0.8726 | ≥0.80 | ✅ |
| 近重 40 对准确率 | 0.9750 | ≥0.90 | ✅ |
| **holdout 召回**（剔除调参文件） | 1.0 | — | — |
| **holdout 误报率** | 0.0 | — | — |
| **holdout 根因逐行不一致** | 10 行 / gold 47 | — | — |

### 3.2 根因类与仓分布

| 根因类 | 条数 | 精度 | 召回 | F1（严格） |
|---|---|---|---|---|
| reference_limited | 23 | 0.739 | 0.895 | 0.809 |
| discretization_resolution | 15 | 0.800 | 0.857 | 0.828 |
| implementation_defect | 35 | 0.914 | 0.800 | 0.853 |
| reference_missing | 3 | 1.000 | 1.000 | 1.000 |

| 仓 | 欠账条数 |
|---|---|
| TensorLBM | 33 |
| unknown | 26 |
| ship-performance-platform | 9 |
| TensorFEM | 4 |
| TensorDEM | 2 |
| TensorPhysics | 2 |

### 3.3 覆盖率表（节点 × 报告 × 欠账）

**plan-21 / IN20 续航军** — 报告 10/12，欠账 5 条

| 节点 | mission | 报告入库 | 欠账行 | 根因分布 |
|---|---|---|---|---|
| IN20-R1 | m5045 | ✅ | 0 | — |
| IN20-R2 | m5046 | ✅ | 0 | — |
| IN20-R3 | m5104 | ✅ | 0 | — |
| IN20-R4 | m5048 | ✅ | 0 | — |
| IN20-R5 | m5055 | ✅ | 0 | — |
| IN20-R6 | m5049 | ✅ | 0 | — |
| IN20-R7 | m5050 | ✅ | 0 | — |
| IN20-D1 | m5051 | ❌ 缺报 | 0 | — |
| IN20-D2 | m5052 | ✅ | 2 | implementation_defect, reference_missing |
| IN20-D3 | m5053 | ✅ | 3 | discretization_resolution, implementation_defect |
| IN20-D4 | m5054 | ❌ 缺报 | 0 | — |
| IN20-G21 | m5177 | ✅ | 0 | — |

**P21 / plan-44 multi_domain_verification** — 报告 8/9，欠账 18 条

| 节点 | mission | 报告入库 | 欠账行 | 根因分布 |
|---|---|---|---|---|
| P21-N01 | m5345 | ✅ | 0 | — |
| P21-N02 | m5346 | ✅ | 0 | — |
| P21-N03 | m5347 | ✅ | 3 | reference_limited |
| P21-N04 | m5348 | ✅ | 2 | reference_limited |
| P21-N05 | m5349 | ✅ | 4 | discretization_resolution, implementation_defect, reference_limited |
| P21-N06 | m5350 | ✅ | 6 | discretization_resolution, implementation_defect, reference_limited, reference_missing |
| P21-N07 | m5351 | ✅ | 3 | implementation_defect, reference_limited |
| P21-N08 | m5352 | ✅ | 0 | — |
| P21-N09 | m5353 | ❌ 缺报 | 0 | — |

## ④ 图

![fig1_debt_distribution.png](../artifacts/backlog_extraction/figs/fig1_debt_distribution.png)

*图1 欠账分布：仓 × 根因类（n=76）*

![fig2_error_magnitude.png](../artifacts/backlog_extraction/figs/fig2_error_magnitude.png)

*图2 欠账误差幅值分布（对数轴，3% 判据线）*

![fig3_metrics_vs_gates.png](../artifacts/backlog_extraction/figs/fig3_metrics_vs_gates.png)

*图3 验收指标 vs 门槛（召回/误报/根因 F1 两口径/去重准确率）*

![fig4_coverage.png](../artifacts/backlog_extraction/figs/fig4_coverage.png)

*图4 战役节点覆盖率（plan-21 12 节点 + P21 9 节点，标出缺报节点）*

![fig5_rootcause_matrix.png](../artifacts/backlog_extraction/figs/fig5_rootcause_matrix.png)

*图5 根因分类混淆矩阵（行级金标 × 预测）*

## ⑤ 注意点、坑与局限

1. **线上 API 抓不到归档报告**：`GET /tensor/reports?mission_id=5345` 在本平台当前构建里**忽略 mission_id 过滤**（恒返回最新 50 条，`total` 也等于本页条数）。取归档报告只能直连平台库做只读 SELECT（`psql -At`，仅 SELECT）。这是环境事实，已在 `fetch_corpus.py` 里写成两条通道并留注释。
2. **坑：比值/百分数标定**。`10.526` 若被当成比值会读出 1052.6%（历史 bug）；现规则：无 `%` 且 |v|≤0.2 视为比值、其余按字面百分数、带尾部注释一律不标定。单元标定探针 20 例，最大相对偏差 0.000%。
3. **坑：`×` 不是判词**。`succeeded ×9` 里的乘号曾被当成 FAIL 标记，导致「成功计数」被读成失败；同理 `GATE_REJECT`（负控被正确拒绝）不是欠账。
4. **坑：token 化过粗会造出假重复**。再派判据来自四类模板，若并入 token 袋，任意两条同根因欠账Jaccard=1.0（实测 `KVLCC2-01` 与 `KVLCC2-04` 被判 1.0）；现只取量名 + 依赖，并保留含数字 token。
5. **局限：调参集与 holdout**。提取规则是在 7 个文件上反复修出来的（含 1 次因假阳性回改），故主指标存在乐观偏差；holdout（47 行）召回 1.0、误报 0.0、根因逐行不一致 10 行。
6. **局限：去重金标类别不均衡**。40 对里只有 2 对真重复（其余 38 对 distinct）：语料内部已按指纹去重，跨源「同一行记两遍」的情形本来就少。因此该指标主要度量**假重复抑制能力**，不能当作重复召回率的估计。
7. **局限：仓归属**。约 1/3 行的报告正文不含仓名（如论文成稿类报告），`repo=unknown`；去重与提案草案对 unknown 行的区分力下降，已如实保留而不是猜。
8. **局限：不改 daemon、不接线派单**。本节点只产出 backlog 与提案草案（dry-run）；真派单需要人来接线，且必须先过 `evaluate.py` 的判据门。

## ⑥ 误差判定（逐项对照基准，显式回答「是否 <3%」）

本节点是被治理/后端任务，没有物理场量，因此误差判定的「基准」是**金标与人工读数**，不是实验值。凡无物理基准处均显式声明原因，不含糊。

| 关键量 | 本文值 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 数字解析（20 个单元标定） | 最大偏差 0.000% | 人工逐格读数 | 0.000% | **是** |
| 行级召回 | 1.0000 | 金标 72 行 | —（分类指标，非误差量） | **是**（门槛 ≥0.90 达标） |
| 行级误报率 | 0.0000 | 金标 ok 行 | — | **是**（门槛 ≤0.10 达标） |
| 根因 macro-F1（严格） | 0.8726 | 金标根因标注 | — | **是**（门槛 ≥0.80 达标） |
| plan-21 报告覆盖 | 10/12 | 12 单（任务书口径） | 16.7% 缺报 | **否——honest FAIL**（m5051/m5054 库中无报告） |
| P21 报告覆盖 | 8/9 | 9 节点 | 11.1% 缺报 | **否**（m5353 无报告） |
| 物理场量 vs 实验/解析解 | 不适用 | 本节点不产生物理场量 | — | **无基准（声明原因）**：本节点是证据治理，产物为结构化账本与评测数字 |

**honest-FAIL（不可覆盖）**：plan-21 声称「十二单」，实际入库报告只有 10/12；P21 九节点缺 1 篇。这两项覆盖缺口照实记 FAIL，不以外推或缺报补零充数。

## ⑦ 审计链与复现

| 产物 | sha256（前16） | 大小 |
|---|---|---|
| backlog.jsonl | `38029f65262a8697` | 102938 B |
| gold.json | `6f30c41add3c74de` | 23084 B |
| eval.json | `2980528f203cd33f` | 13051 B |
| coverage.json | `326132fba2f30740` | 6569 B |
| dedup_report.json | `fcc1414995122008` | 174 B |
| proposal_drafts.json | `9419dccf47ae4b98` | 79038 B |
| campaign_nodes.json | `194c6ae04ed5c9ce` | 3482 B |
| corpus/index.json | `6fd28603887b006d` | 15373 B |

```bash
# 1) 冻结语料（只读：psql -At SELECT / 文件拷贝）
python3 -m tools.evidence.backlog_extraction.fetch_corpus \
    --source db --missions 5045-5055,5104,5177,5345-5356,5522,5572,5574,5509,5513,5547,5552,5544,5463,5545,5516,5512 \
    --per-mission latest --out artifacts/backlog_extraction/corpus
# 2) 建 backlog + 覆盖率 + 去重 + 提案 dry-run
python3 -m tools.evidence.backlog_extraction.build
# 3) 对金标评测（判据门：不达标退出码 2）
python3 -m tools.evidence.backlog_extraction.evaluate
# 4) 作图 + 生成本报告
python3 -m tools.evidence.backlog_extraction.figures
python3 -m tools.evidence.backlog_extraction.gen_report
# 5) 测试
python3 -m pytest tests/evidence/test_backlog_extraction.py -q
```

## ⑧ 结论

欠账结构化已闭环：76 条去重欠账（含 23 条参考受限、35 条实现缺陷、15 条离散/分辨率、3 条参考缺），每条带行号回溯、根因类与再派判据；提案草案 dry-run 零重复、零派单。判据门全部达标（行级召回/误报/根因 F1 两口径/去重准确率），覆盖率缺口如实记 honest FAIL。
