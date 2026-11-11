# P143W3-N7｜盲预测校准账本与 run-manifest 回填器成熟化（TensorPlatform）

## ① 案例介绍

**问题**：平台的「预测盲」机制（`tools.abm.blind`）此前只保证**流程合规**——先承诺、后观测、事后不可改预测。但预测普遍写成「点值 + 容差」，只能判命中/未命中，无法回答「说 85% 会命中的那批，实际命中率是多少」，因此**预测能力本身不可计分、不可成熟化**。

**对象**（两个数据集，互为对照）：

1. **内部密封盲程序**：140 个问句，覆盖 10 个已交付节点的工件内容属性；概率按预登记规则在**承诺时刻**给出（声明时 git 干净=True），随后才跑确定性检查器。
2. **外部预测源**：`pg` 派发前可行性先验的时间留出背测（变体 `platt_prior`，样本 694，切分 {"calib": 685, "test": 694, "train": 2064}）。它**不是**承诺-揭示盲卡，只作外部效度检验。

**参数表**（全部来自工件/配置）：

| 项 | 值 |
|---|---|
| 节点配置 | `config/blind_calibration_nodes.v1.json` |
| 分箱 | 10 个等宽箱（含空箱统计） |
| 判据（a）可靠性最大偏差 | < 0.1 |
| 判据（b）Brier | < 0.25（随机预测基线 p≡0.5） |
| 判据（c）三分分解恒等残差 | < 1e-10 |
| 自助重采样 | 2000 次，seed=0 |
| 回填封顶 | E1（回填器只有 E1 的授予权） |
| 声明时刻 | 2026-11-10T21:40:00Z |

## ② 怎么做的（方法与关键设置）

**三段严格分离**（这是全部可信度的地基）：

1. **出题+承诺**：`plan` 只按配置里的预登记规则给概率，**不读任何工件内容**（`tests/blind_calibration/test_domain_agnostic.py::test_plan_does_not_read_file_contents` 用monkeypatch 拦截文件读取钉死这一条）；`commit` 把 `sha256(canonical({case_id, kind, target, p, rationale}))` 写进哈希链账本。
2. **观测**：`observe` 跑确定性检查器（file_anchor / utf8_text / json_parse / json_top_is_dict），结局 0/1 与明细写进同一账本。
3. **计分**：`score` 只吃 `(p, y)`，算可靠性曲线、Brier、Murphy 三分分解（`BS = REL − RES + UNC`），并把账本链摘要写进 provenance。

**回填器**（`tools/blind_calibration/evidence.py`）：逐节点判 5 个槽位——

| 槽位 | 内容 | 授予权 |
|---|---|---|
| E1 | 工件内容锚定：当前 sha256 == 「最后改动该文件的提交」里的 blob sha256 | 回填器机检即授予 |
| E2 | 生产者 run-manifest 结构合法 + 摘要可算 | 必须有生产者工件（回填器不自建） |
| E3 | 观测/结论引用（声明给路径+sha，机器复算） | 外部声明 |
| E4 | 可复现记录（提交号在 git 中真实存在 + 命令） | 外部声明 |
| E5 | 独立复核（复核席位 ≠ 生产席位 + 报告 sha 复算） | 外部声明 |

**「回填 E1 封顶」**：等级 = 从 E1 起**连续通过**的槽位数；回填器自己只能把 E1 置为通过，E2–E5 必须有外部证据，否则一律 `withheld` 并给机器可读 reason（fail-closed）。

## ③ 计算结果

### 3.1 判据总表

| 判据 | 阈值 | 实测 | 结果 |
|---|---|---|---|
| (a) 可靠性曲线最大偏差 | < 0.1 | 0.055 （占用箱 3/10） | PASS |
| (b) Brier | < 0.25 | 0.0210 | PASS |
| (c) Murphy 恒等残差 | < 1e-10 | 0.000000000000 | PASS |
| (d) E1–E5 槽位完整率 | = 100% | 100.0% (50/50) | PASS |
| (e) 工件 SHA256 锚定 | 全部匹配 | 52/52 | PASS |
| (g) pytest | 全 PASS | 78 passed（6 个测试文件） | PASS |

### 3.2 计分对照

| 数据集 | n | 占用箱/箱数 | Brier(逐样本) | Brier(分箱) | max\|p−f\| | REL | RES | UNC | 分解残差 | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| sealed blind program | 140 | 3/10 | 0.0210 | 0.0205 | 0.055 | 0.0026 | 0.1096 | 0.1275 | -0.000000000000 | PASS |
| external prior holdout | 694 | 4/10 | 0.3873 | 0.3960 | 0.662 | 0.1820 | 0.0336 | 0.2476 | 0.000000000000 | FAIL |

### 3.3 逐箱可靠性（判据 a 展开）

**sealed blind program**

| 箱 | 区间 | n | 平均概率 p̄ | 实际频率 f | p̄−f |
|---|---|---|---|---|---|
| 0 | [0.00,0.10) | 18 | 0.020 | 0.000 | 0.020 |
| 1 | [0.10,0.20) | 0 | — | — | — |
| 2 | [0.20,0.30) | 0 | — | — | — |
| 3 | [0.30,0.40) | 0 | — | — | — |
| 4 | [0.40,0.50) | 0 | — | — | — |
| 5 | [0.50,0.60) | 0 | — | — | — |
| 6 | [0.60,0.70) | 0 | — | — | — |
| 7 | [0.70,0.80) | 0 | — | — | — |
| 8 | [0.80,0.90) | 18 | 0.880 | 0.833 | 0.047 |
| 9 | [0.90,1.00) | 104 | 0.945 | 1.000 | -0.055 |

**external prior holdout（诚实 FAIL：先验在最新波次上严重漂移）**

| 箱 | 区间 | n | 平均概率 p̄ | 实际频率 f | p̄−f |
|---|---|---|---|---|---|
| 0 | [0.00,0.10) | 0 | — | — | — |
| 1 | [0.10,0.20) | 0 | — | — | — |
| 2 | [0.20,0.30) | 0 | — | — | — |
| 3 | [0.30,0.40) | 0 | — | — | — |
| 4 | [0.40,0.50) | 0 | — | — | — |
| 5 | [0.50,0.60) | 0 | — | — | — |
| 6 | [0.60,0.70) | 1 | 0.662 | 0.000 | 0.662 |
| 7 | [0.70,0.80) | 113 | 0.790 | 0.195 | 0.596 |
| 8 | [0.80,0.90) | 468 | 0.851 | 0.425 | 0.426 |
| 9 | [0.90,1.00) | 112 | 0.912 | 0.821 | 0.091 |

### 3.4 按问句类型拆解

| 问句类型 | n | 平均声明概率 | 实际命中率 | Brier | max\|p−f\| |
|---|---|---|---|---|---|
| file_anchor | 52 | 0.970 | 1.000 | 0.0009 | 0.030 |
| json_parse | 18 | 0.900 | 1.000 | 0.0100 | 0.100 |
| json_top_is_dict | 18 | 0.880 | 0.833 | 0.1411 | 0.047 |
| utf8_text | 52 | 0.615 | 0.654 | 0.0033 | 0.070 |

### 3.5 回填审计表（逐节点 E1–E5）

| 节点 | 等级 | 工件 | 锚定 | E1 | E2 | E3 | E4 | E5 |
|---|---|---|---|---|---|---|---|---|
| m597 | E1 | 4 | 4 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m642 | E1 | 5 | 5 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1386 | E1 | 7 | 7 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1388 | E1 | 7 | 7 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1564 | E1 | 17 | 17 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1882 | E1 | 3 | 3 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1892 | E1 | 1 | 1 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1393 | E1 | 5 | 5 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1444 | E1 | 2 | 2 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |
| m1479 | E1 | 1 | 1 | verified<br/><sub>artifact_blob_matches_commit</sub> | withheld<br/><sub>producer_manifest_missing</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> | withheld<br/><sub>no_declaration</sub> |

## ④ 结果图

- `artifacts/blind_calibration/fig1_reliability_curve.png`
- `artifacts/blind_calibration/fig2_murphy_decomposition.png`
- `artifacts/blind_calibration/fig3_evidence_slots.png`

- **fig1**：左=密封盲程序的可靠性曲线（点面积 ∝ 箱样本数），右=外部先验源。虚线是对角线（完美校准）。
- **fig2**：Murphy 三分分解条形（REL/RES/UNC 与逐样本 Brier），红点线是随机预测基线 0.25。
- **fig3**：10 个已交付节点的 E1–E5 槽位状态热图（颜色=verified/attested/withheld/missing）。

## ⑤ 注意点与局限（不粉饰）

1. **空箱 7/10**：本程序可断言的概率天然集中在高段（0.9+）与近零段（二进制文件不可 UTF-8 解码），中间区间没有可靠先验。我们**拒绝为填满分箱而声明无依据的中间概率**——宁可空箱，不可造箱。因此判据 (a) 的 max|p−f| 是在 3 个非空箱上取的，证据力弱于「10 箱都有样本」的理想情形。
2. **小样本箱**：每箱样本数见 3.3；自助 95% 区间与 Wilson 区间已一并落盘（`reliability_curve.bins[].observed_freq_ci`），单箱 <5 样本时不宜据此下结论。
3. **「盲」只证顺序，不证认知**：`integrity()` 复算「首个 observe 晚于 commit」，能证明记录未被事后改写，**不能**证明操作者事先没有偷看文件。配置里 `prior_knowledge_disclosure` 如实列出了声明前已看过的信息（git status、文件数与扩展名），声明前未读任何文件内容、未解析 JSON、未复算 git blob。
4. **外部源是诚实 FAIL，不是我们的失败**：外部先验在最新波次上Brier=0.3873（> 0.25），逐箱偏差最大 0.662——说明该先验把概率整体压得过高（训练窗验收率高、最新窗仅约一半）。本包的机器**就该把它判成 FAIL**；如果它「看起来通过」，才是校准器坏了。
5. **外部源数据会漂移**：`campaign_nodes` 是活库，重跑 `external` 会采到新样本（本次 694 条）。**权威数据是落盘的账本** `artifacts/blind_calibration/external_ledger.jsonl`（链摘要 `5e5d31ef86b41e767e01d877b558b66a08688c1c87897546c155e19bcbf905a4`）；报告数字均由该账本复算。
6. **真实节点的内容等级停在 E1 是机制的预期产出**：10 个已交付节点的工件目录里没有生产者 manifest（E2 withheld），也没有 E3–E5 的外部声明，故按「连续 + 封顶」纪律等级分布为 {'E1': 10}。`slot_coverage=100%` 的含义是**每槽都有机器可读状态（无静默空缺）**，不是「都通过」。E2–E5 的验收逻辑由`tests/blind_calibration/test_backfill_integration.py` 用合成域夹具证明可达（含自批拦截）。
7. **适用范围**：本包只做计分与证据链判定，不产生预测、不判断预测有没有科学意义；也不替代各域验证器对数值正确性的判断。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 量 | 基准 | 实测误差 | 是否 <3% |
|---|---|---|---|
| Murphy 恒等残差 | 代数恒等（0） | 0.000000000000（相对 REL+UNC 量级 ≈ 0.000000%） | 是（<1e-10 绝对） |
| Brier（盲程序） | 随机基线 0.25 | 相对基线降低 91.6% | 是 |
| Brier（盲程序 vs 气候基准） | 0.1275 | skill=0.8353 | 是 |
| Brier（外部源） | 随机基线 0.25 | 相对基线 54.9%（更差） | 否（诚实 FAIL） |
| 工件哈希对账 | git blob sha256 | 逐文件精确相等 52/52 | 是（0 个字节差异） |

对「是否 <3%」的显式回答：**三分分解恒等式不是近似式**，残差为 0（数值上 ≤1e-12 量级）；Brier 判据是对阈值的比较（< 0.25），盲程序达标；外部源不达标，如实记为 FAIL。哈希对账是精确等值判定，不存在百分比容差。

## 溯源

- 计分账本（盲程序）：`artifacts/blind_calibration/calibration_ledger.jsonl`，链完整=True，链摘要=`698315c4b23401696732d22026535ed37ed5fa6fead78a8f3f75c579bafb650d`，数据集摘要=`793ff6755e36710ca1b3144a47e8240ad3913dc8ef0544669c4361ce689dd628`
- 盲程序摘要：`e97045601648ea2b9cd2fb6b73f3bcd820ebdc03de897545aae8316c440abde4`（承诺时刻 2026-11-10T21:40:00Z）
- 外部源账本：`artifacts/blind_calibration/external_ledger.jsonl`，链摘要=`5e5d31ef86b41e767e01d877b558b66a08688c1c87897546c155e19bcbf905a4`，数据集摘要=`4d147ce28a51cf324b9b6e1813f805e05ca67462e01dd875a8d3be5870e6351e`
- 回填审计：`artifacts/blind_calibration/backfill.json` + `artifacts/blind_calibration/backfill.table.json`
- 复算命令：`python3 -m tools.blind_calibration {plan,commit,observe,score-blind,external,backfill,figures,audit}`

