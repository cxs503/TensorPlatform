# 提案去重证据的可复算门（p337w1-n03）：从纪律文字到 fail-closed 机器门

> 工件：`artifacts/m9953/dedup_evidence_gate_acceptance.json`（本报告全部数字的唯一来源）、`artifacts/m9953/figures/*.png`、`artifacts/m9953/evidence/*.json`（5 份真提案证据）、`artifacts/m9953/replay_records.json`（60 条回放记录）、`artifacts/m9953/snapshot/snapshot.json`（真库快照）。
> 代码：`tools/proposal_gate/dedup_evidence.py`（复算门核心）、`tools/proposal_gate/dedup_evidence_cli.py`（取证/复算 CLI）、`tools/proposal_gate/dedup_evidence_collect.py`（真库快照采集）、`tools/acceptance/dedup_evidence_gate.py`（C1–C6 验收）、`tests/test_dedup_evidence_gate.py`（27 项 pytest）、`config/dedup_evidence.json`（唯一参数来源）。

**判据总览**：C1 True · C2 True · C3 True · C4 True · C5 True · C6 True → all_pass = **True**（验收脚本墙钟 53.68 s）。

## ① 案例介绍

平台治理里有一条长期只靠文字维持的纪律：**提案必须过三重检索**（既有战役节点 / 战役计划 / 提案池），用来防止「换词不换招」的重复提案。问题是这条纪律写在提示词里，提案方只需写一句「我查过了」，收养方无法复核；池子随时间漂移后，连提案方自己也无法重放当时的检索。

本节点把这个环节升级为**可复算的证据门**：检索不再是「声称」，而是三件可机器校验的东西——快照指纹（检索发生在哪个版本的池子上）、结构化查询规格（查了哪张表、哪种检索、哪些词项、top-k 多少）、以及逐规格的命中计数与 top-k 近邻。收养时在同一快照上重跑同样的规格，数字不符即拒。

取证快照来自平台**只读 API**（真库三表，采集时间 2026-11-10T16:31:32Z）：行数与指纹见下表，表名/列名全部来自 `config/dedup_evidence.json`（代码里没有表名之外的领域知识）。

| 表 | 行数 | max(id) | 内容 sha256（前 16）|
|---|---|---|---|
| `campaign_nodes` | 450 | 900797 | `d439188cd10513be…` |
| `campaign_plans` | 51 | 9186 | `ef988740adb08cef…` |
| `proposals` | 400 | 10753 | `88fe65d435ffc1e9…` |

## ② 怎么做的

**流程五步**（全部落在 `tools/proposal_gate/dedup_evidence.py`）

1. **快照指纹**：对每张被检索表取三元组 `(行数, max(id), 内容 sha256)`，按表名排序后合成聚合 `snapshot.sha256`。行序无关（先按 id 排序再哈希），内容哈希覆盖 `(id, text)` 全量。
2. **查询规格**：`QuerySpec = {query_id, table, mode ∈ {ilike, keyword_set, vector}, text, terms[…], top_k, threshold, match_ratio}`，自带 `spec_sha256`。规格集 = **每张必需表 × 每种检索模式**（9 条/份证据），因此每张表都被三种检索各查一遍。
3. **证据工件**：`DedupEvidence = {schema_version, snapshot, specs[], outcomes[]}` + `evidence_sha256`（对内容规范化 JSON 取 sha256）。`outcomes` 记录逐规格的命中计数与带分数的 top-k 近邻。
4. **复算器**（fail-closed，顺序即防线）：结构校验 → 内容哈希自洽 → 规格哈希自洽 → 三重检索覆盖 → 快照指纹内部自洽 → 拉现网快照比对 → 逐规格重算并比对**命中计数（精确相等）**、**top-k 集合（集合相等或 Jaccard ≥ 0.8）**、**分数（1e-9）**。任一超界即 `REJECT` 并给出分类原因码；数据源不可达同样 `REJECT`（不放行）。
5. **动作分级**：证据不一致 → `REJECT_WITH_REASON`；证据一致但近邻互证通过 → `DEEPEN`（建议深化/合并到既有条目，而不是新建孤儿提案）；证据一致且无近邻 → `ACCEPT`。

**两个关键设计选择**（都是为了「同一证据能被别人重算出同样的数字」）

* 相似度全部在**进程内**用确定性纯函数算：NFKC+casefold 归一、字符 3-gram、blake2b 签名特征哈希投射。不复用数据库的 `ILIKE`/全文检索/向量扩展——PG 与 SQLite 的分词、排序规则、扩展版本差异会让「同一证据」复算出不同命中，那正是本门要消灭的东西。数据源只负责按 id 有序取行。
* 词项按**池内稀有度**挑（df 升序、df=0 剔除），而不是按出现顺序挑。只挑高频泛词会让无关文本也轻松过匹配率阈值（本节点早期版本的误报主源，见⑤）；df=0 的词项对本次检索零判别力，剔除后可重放性不受影响（词项显式写进规格，规格哈希钉死）。

**近邻判定 = 逐表模式互证**：同一张表里至少 `neighbor_min_votes` 个模式都给出 ≥ 各自阈值的 top-1 近邻，才判「池中确有近邻」。单模式单表易过杀（合成哈希向量对无关长文本也给高余弦），互证把误报压下去。

**零领域语义**：门禁代码与夹具只有「表名/模式/阈值/词项」这些结构量；夹具用合成域名（foo/bar/widget 族）。域词扫描（`tools/audit/domain_leak_scan.py`，词表来自 `config/domain_word_denylist.json`）命中 **0** 处。

## ③ 计算结果

### C1 复算一致率（5 条真提案）

| 提案 mission | 证据快照 sha256（前 12）| 一致（计数/top-k）| 复算判定 | 单次耗时 ms |
|---|---|---|---|---|
| 10354 | `ab2b8e23e94d…` | True/True | DEEPEN | 177.513 |
| 10434 | `d28fd49c6eca…` | True/True | DEEPEN | 207.931 |
| 10514 | `5ea7b704b5f4…` | True/True | ACCEPT | 171.241 |
| 10594 | `015385c5f823…` | True/True | DEEPEN | 125.352 |
| 10674 | `133f0a7ea17c…` | True/True | ACCEPT | 118.549 |

一致率 = **100%**（n=5；判据要求 100%）。「一致」= 9 条规格的命中计数逐条精确相等，且 top-k 集合 Jaccard = 1.0。

### C2 篡改检出（13 例，分类正确）

| 用例 | 期望原因码 | 实测原因码 | 结论 |
|---|---|---|---|
| `count_edit_without_rehash` | `EVIDENCE_TAMPERED` | `EVIDENCE_TAMPERED` | 检出 |
| `count_edit_rehashed` | `COUNT_MISMATCH` | `COUNT_MISMATCH` | 检出 |
| `neighbor_delete_without_rehash` | `EVIDENCE_TAMPERED` | `EVIDENCE_TAMPERED` | 检出 |
| `neighbor_swap_rehashed` | `TOPK_MISMATCH` | `TOPK_MISMATCH` | 检出 |
| `snapshot_fingerprint_forged` | `SNAPSHOT_MISMATCH` | `SNAPSHOT_MISMATCH` | 检出 |
| `old_snapshot_masquerade` | `SNAPSHOT_MISMATCH` | `SNAPSHOT_MISMATCH` | 检出 |
| `free_text_evidence` | `MALFORMED_EVIDENCE` | `MALFORMED_EVIDENCE` | 检出 |
| `empty_specs` | `MISSING_EVIDENCE` | `MISSING_EVIDENCE` | 检出 |
| `coverage_incomplete` | `SPEC_COVERAGE_INCOMPLETE` | `SPEC_COVERAGE_INCOMPLETE` | 检出 |
| `spec_terms_tampered_rehashed` | `SPEC_TAMPERED` | `SPEC_TAMPERED` | 检出 |
| `score_edit_rehashed` | `SCORE_MISMATCH` | `SCORE_MISMATCH` | 检出 |
| `source_unavailable` | `SOURCE_UNAVAILABLE` | `SOURCE_UNAVAILABLE` | 检出 |
| `missing_evidence_field` | `MISSING_EVIDENCE` | `MISSING_EVIDENCE` | 检出 |

检出 **13/13**（判据要求 ≥10 例全拒且分类正确）。其中「改计数但不重算哈希」「改计数并重算哈希」分别落在 `EVIDENCE_TAMPERED` 与 `COUNT_MISMATCH`，说明防线对「笨篡改」和「精明篡改」都有独立判据。

### C4 回放判别力（真库 60 条记录，dev/holdout 各 30）

| 口径 | TP | FP | FN | TN | precision | recall | F1 |
|---|---|---|---|---|---|---|---|
| dev 选点规则（τ=0.8, votes≥1）@ holdout | 15 | 1 | 0 | 14 | 0.938 | 1.000 | 0.968 |
| 配置运行口径（τ=0.7, votes≥2）@ holdout | 14 | 1 | 1 | 14 | 0.933 | 0.933 | 0.933 |

回放集：三表等距抽样各 20 条候选文本，真库池 900 行；正例（参照判死）31 条、负例 29 条。参照标签 = 既有文本相似门的最近邻判据（dup 阈值 0.85），是 **proxy oracle 而非人工标注**（见⑤）。

holdout recall = 1.000 ≥ 0.8 ⇒ 本门**可以**作否决依据，但按上面的 precision 0.938，运营上仍建议与文本相似门并联（两门独立实现，互为交叉校验）。

### C5 成本与可用性

| 指标 | 数值 |
|---|---|
| 复算样本数 | 65 |
| 均值 / p95 / 最大（ms）| 134.317 / 186.028 / 224.903 |
| 预算（ms）| 2000 |
| 源不可达 | `REJECT`（SOURCE_UNAVAILABLE）—— fail-closed 而非放行 |

规模外推（真快照行复制，合成数据，仅测成本曲线）：

| 快照行数 | 复算 ms | 是否在预算内 |
|---|---|---|
| 901 | 135.345 | True |
| 1802 | 311.389 | True |
| 3604 | 592.839 | True |
| 7208 | 1306.694 | True |

线性外推的预算破线行数 ≈ **11031** 行（成本 ≈ 线性于行数）。现网真库三表合计约 2.3 万行量级 ⇒ **当前实现的全量池复算会超 2s 预算**，属已知能力边界（见⑤，未做的部分如实登记：需要增量指纹缓存 / 每表索引缓存，本节点未实现）。

### C6 无副作用（只读证明）

| 检查项 | 结果 |
|---|---|
| 快照指纹复算前后一致 | True（`c92c3668fdd25690…`）|
| 逐表行哈希前后一致 | True |
| 快照 JSON/SQLite 文件 sha256 前后一致 | True |
| 执行语句全为 SELECT/WITH | True（3 条）|

复算走 `sqlite3` 的 `mode=ro` 只读连接 + `DbapiSource` 的语句前缀白名单（非 SELECT/WITH 直接抛错），并以「快照指纹前后对照 + 文件 sha256 前后对照」三重证明门禁不改任何一行。

## ④ 结果图

![C4 回放判别力：复算门 vs 参照文本相似门的混淆矩阵](artifacts/m9953/figures/fig_confusion_matrix.png)

*C4 回放判别力：复算门 vs 参照文本相似门的混淆矩阵*

![C5 单次复算耗时分布 vs 2s 预算](artifacts/m9953/figures/fig_recompute_cost.png)

*C5 单次复算耗时分布 vs 2s 预算*

![C2 篡改用例逐条检出与原因码分类](artifacts/m9953/figures/fig_tamper_cases.png)

*C2 篡改用例逐条检出与原因码分类*

![C5 复算成本随快照规模的增长与 2s 预算破线外推](artifacts/m9953/figures/fig_scaling_cost.png)

*C5 复算成本随快照规模的增长与 2s 预算破线外推*

![C1 五条真提案复算一致率（逐规格最小 top-k Jaccard）](artifacts/m9953/figures/fig_replay_consistency.png)

*C1 五条真提案复算一致率（逐规格最小 top-k Jaccard）*

## ⑤ 注意点（踩过的坑、局限、适用范围）

**踩坑（都由本轮实测暴露，且已固化成判据或反例测试）**

1. **自匹配把回放判据污染成 100% 误报**：最初把候选提案自己也放在检索池里，top-1 必然是自己、相似度 1.0 ⇒ 所有负例被判死，holdout precision 掉到 0.50。真实收养场景里提案尚未入池，因此取证池必须显式剔除候选自身；修正后 precision 0.9375。
2. **按出现顺序挑词项 = 挑到高频泛词**：早期词项集含「提案/节点/机制」这类泛词，无关文本也能过 0.7 匹配率，是误报的第二个主源。改成「池内 df 升序 + 剔除 df=0」后，词项变成有判别力的稀有 3-gram。
3. **「一表一模式」让互证失效**：最初规格集把模式轮流分配给三张表，导致「同一张表的三种模式互证」无从谈起（互证统计跨了表、等于跨了池）。改成表 × 模式全组合（9 条/份证据）后才真正实现逐表互证。
4. **改表指纹但保留聚合哈希的伪造曾漏检**：只比对「声明聚合哈希 vs 现网聚合哈希」时，篡改者改表内行数而不动聚合字段即可绕过。补了「由表指纹重算聚合哈希」的自洽检查（`SNAPSHOT_MISMATCH`），对应测试与 C2 用例 `snapshot_fingerprint_forged` 现均检出。
5. **指纹/归一化的重复计算吃掉了全部预算**：首版单次复算 794 ms（p95 757 ms），把归一化与向量投射改成进程内缓存（纯函数，语义不变）后降到 224.903 ms（p95 186.028 ms）。
6. **平台 plans 分页不认 offset**：采集器请求 `offset=50/100` 返回重复页，去重后计划表只有 51 行真数据（节点表同理有 880 条重复行被丢弃）。快照规模因此小于真库全量，已在采集 manifest 里记明。

**局限（诚实边界）**

* **参照标签是 proxy oracle**：C4 的「判死」真值来自既有文本相似门（字符 3-gram Jaccard/containment 混合，阈值 0.85）的最近邻判定，不是人工标注。所以 C4 度量的是「与既有关卡的一致度」，既不能证明也不排除两个门共同漏判的情形；precision/recall 的绝对水平不能当作对真实重复率的估计。
* **判别力只在 60 条真样本上度量**（dev/holdout 各 30），置信区间宽；正负例比例由抽样决定，并非线上真实重复率。
* **成本外推用合成复制**：`C5.scaling_synthetic` 是把真行复制成 2/4/8 倍体积测曲线，不代表真实池的内容分布；线性外推的破线行数（11031 行）是量级估计而非实测。全量池（≈2.3 万行）超 2s 预算，这是本节点**未解决**的部分，需要增量指纹缓存。
* **向量模式是确定性哈希投射，不是语义嵌入**：它是「可复现的重叠度」而非「语义相似」，对换词改写会失效（这类改写要靠 keyword/skeleton 类信号）。选它是因为复算必须跨进程逐字稳定，任何学习式嵌入都会带来版本漂移。
* **词项稀有度依赖池**：同一份提案在不同池上的取证会选出不同词项集，因此证据绑定快照指纹，跨池不可直接复用——这是设计意图（防止旧证据冒充），但意味着池频繁变动时提案方需重取证。
* **未接线到 adopt 端点**：本节点交付的是库函数 `enforce_adoption()` + CLI（`bootstrap`/`verify`，退出码 2 = REJECT）与 27 项 pytest，可作为 CI/收养脚本的机器门；把它挂到 `POST /proposals/gate/adopt` 路由上属跨路由/跨仓改动，留给平台归属方决策，本节点未擅自改动线上收养路径。

## ⑥ 误差判定（关键量 vs 基准，显式回答「是否 <3%」）

| 关键量 | 本节点结果 | 基准/参照 | 相对误差 | 是否 < 3% |
|---|---|---|---|---|
| 复算一致率（同一快照重放） | 100% | 100%（确定性判据：逐字相等）| 0 | **是（0）** |
| 篡改检出（含原因分类）| 13/13 | 全部检出（判据）| 0 | **是（0）** |
| 单次复算耗时 | 最大 224.903 ms | 预算 2000 ms | 88.8% 余量 | **是**（未超预算）|
| 回放 recall | 1.000 | proxy oracle 判死集 | — | 见下 |
| 回放 precision | 0.938 | proxy oracle | — | 见下 |

**为什么 C1/C2/C5 的误差是 0**：这三条是确定性判据——复算走同一份快照、同一套纯函数，命中计数与 top-k 集合逐字相等（不是「误差小于 3%，而是位级相等」）；篡改用例由构造保证必被拒。

**为什么 C4 不能报「误差 %」**：判别力没有解析基准或实验真值，参照是另一套启发式关卡（proxy oracle），两者都不等于人的判断。因此本报告只给混淆矩阵与 precision/recall，并显式声明：**不存在可用于计算「相对误差」的真值，拒绝给出百分比误差**。在这一口径下，可说的是「本门与既有文本相似门在 60 条真样本上 recall 1.000、precision 0.938」，不能说的是「本门精度 94%」。

## 附：复跑方式

```bash
# 1) 采真库快照（只读 API）
python3 tools/proposal_gate/dedup_evidence_collect.py --out artifacts/m9953/snapshot \
        --max-plans 120 --max-missions 400
# 2) 跑 C1–C6 验收（数字 + 图 + JSON）
python3 tools/acceptance/dedup_evidence_gate.py
# 3) 单份提案取证 / 复算（退出码 2 = REJECT）
python3 tools/proposal_gate/dedup_evidence_cli.py bootstrap \
        --snapshot artifacts/m9953/snapshot/snapshot.json --text-file /tmp/proposal.txt --out /tmp/ev.json
python3 tools/proposal_gate/dedup_evidence_cli.py verify \
        --snapshot artifacts/m9953/snapshot/snapshot.json --evidence /tmp/ev.json
# 4) 单元测试
python3 -m pytest tests/test_dedup_evidence_gate.py -q
```

