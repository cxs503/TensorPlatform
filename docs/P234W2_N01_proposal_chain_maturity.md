# 提案→战役供给链成熟化：语义去重器 + 节点 metadata 契约 + 播种覆盖率账本

**节点**：`p234w2-n01-prop-gate-maturity`（mission 9027） · **落地仓**：TensorPlatform · **代码**：`tools/proposal_chain/`（text / semantic / dedup / node_schema / ledger / coverage / labels / pipeline / figures / report）
**配置**：`config/proposal_chain.json`（阈值/字段名/采样参数唯一来源，sha256=`7935187364b7d884…`） · **机器验收**：`artifacts/proposal_chain/acceptance.json`

## ① 案例介绍：被治理的对象与规模

自主续航的组织里，**提案是唯一的创新入口**：提案 → 仲裁 → 战役 DAG 节点 → 交付。这条链条缺机器门时反复出现三类事故：(i) 同一能力换个说法重复立项；(ii) 播种后的节点 metadata 缺必填字段，下游（覆盖率、去重、审计）无从回链；(iii) 采纳之后没有对账——提案的主题是否真的落到战役节点上，谁也说不清。本节点为供给链建三道门，全部落在平台共性层，**零领域语义**：域标签/缺口账本/字段名/阈值一律配置或工件注入，代码内无域词。

| 门 | 职责 | 失败语义 | 本报告工件 |
|---|---|---|---|
| 语义去重门 | TF-IDF → LSA → 余弦；≥0.85 自动合并建议，复核带兜底 | 分档不合并/进复核，不静默放过 | `dedup_proposals.json` / `dedup_nodes.json` |
| metadata 契约门 | 必填 `ext_id/repo/goal/acceptance_criteria` + 恒等降级 | 缺字段降级不丢节点，漏报率机验 | `schema_validation.json` |
| 覆盖率账本 | 域标签矩阵 + 主题×仓矩阵 + 缺口账本逐项比对 | 差异显式入账，不改账本 | `coverage_matrix.json` / `ledger_coverage.json` |

**数据规模（本次采集，`artifacts/proposal_chain/source_report.json`）**

| 池 | 条目 | 来源 | 备注 |
|---|---|---|---|
| 历史提案池 | 2544 条（已 pin 到快照，文件数不再重扫） | `snapshot:/root/tp-agent/work/m9027/artifacts/proposal_chain/corpus_compact.json.gz` | 坏 JSON 文件 0 个（本 pin 从快照读入，坏文件计数为 0） |
| 战役节点 | 954 个 | `/root/tp-agent/work/m9027/artifacts/proposal_chain/nodes_snapshot.json.gz` | 来自 98 个计划的 DAG 节点 |
| 缺口账本 | 129 条条目 / 5 个 `GAP-*` 标识符 | `/root/tp-agent/PANORAMA.md`（sha256=`0c000da41f7932fa…`） | 只读解析，不改账本 |

**阈值与参数（`config/proposal_chain.json`，全部可调）**

| 参数 | 值 | 作用 |
|---|---|---|
| `dedup.dup_threshold` | 0.85 | 自动合并/拒收线（判据 a/b 的“重叠”定义） |
| `dedup.review_threshold` | 0.65 | 复核带下界（tune 半集校准，见 ③ 表 3） |
| `lsa.dim` / `seed` | 200 / 20261110 | 潜在语义维数 / 随机投影种子（确定性） |
| `tfidf.max_df_ratio` | 0.6 | 通用词剔除比例（无停用词表，纯统计） |
| `text.keep_single_cjk` | True | CJK 单字是否入词项（中文无分词器时的兜底） |
| `node_schema.required_fields` | ext_id, repo, goal, acceptance_criteria | 必填字段 |
| `coverage.ledger_match_threshold` | 0.3 | 账本条目→节点/提案的查询覆盖阈值 |

## ② 怎么做的：方法 / 流程 / 工具链

### 2.1 语义去重门（判据 a/b 的主战场）

* **表示**：文档文本 → 归一化（NFKC/去排版/标点折空格/casefold）→ 词项 = 拉丁词 + CJK 二元组（单字可选，`text.keep_single_cjk` 为真是也入）→ TF-IDF（次线性 tf、平滑 idf、L2 归一）。**无停用词表**：`df > max_df_ratio·N` 的词项按纯统计判为通用词剔除，避免任何域词硬编码。
* **为什么加 LSA**：裸 TF-IDF 余弦对“同能力不同措辞”极不友好。本仓实测（表 2）裸 TF-IDF 在 0.85 阈值下档案召回只有 36.67%，即绝大多数改写提案落地成“不重叠”。因此把 TF-IDF 矩阵做截断 SVD（随机化实现，`lsa.seed` 固定 → 逐次可复现），在低维空间算余弦；同一 0.85 阈值下改写对显著靠近。
* **分档裁决**：`score ≥ 0.85` → 自动档（`duplicate`，出合并建议：并查集连通分量 + 代表元 + 共享词项 + 跨波次计数）；`review_threshold ≤ score < 0.85` → 复核带（`near_duplicate`，**不自动合并**但计入机器门召回）；其余放行。分量 > `max_merge_group_size` 只给 `cluster_review`，防止一次并掉半池子。
* **参数选择纪律**：阈值只在 **tune 半集**上选（`labels.holdout_mod` 按 pair_id 稳定哈希对半分），所有指标在 **holdout 半集 + 全标注集**上报告；两半集数字都写在表 3，不藏。

### 2.2 metadata 契约门

* 必填四字段：`ext_id / repo / goal / acceptance_criteria`；“填了”的定义是机器可裁的（非空字符串 / 非空 list / 非空 dict），别名映射与缺省补值全部写在配置里。
* **恒等降级**：缺字段 → 不抛异常、不丢节点，按 `degrade_defaults` 补值并记录 `missing/degraded/warnings`，节点仍可进入去重与覆盖率账本（读侧 fail-soft；写侧播种仍 fail-closed，两者语义不同）。
* **漏报率机验**：同时跑两条独立实现——契约实现（别名 + 类型规则）与 oracle（最朴素的`key 存在且 truthy` 直查）；`oracle 说缺 × 契约说齐` 记为漏报。实测漏报 0 条（漏报率 0.0）。

### 2.3 播种覆盖率账本

* **域标签矩阵**：扫全量节点的 `physics_domain` 与提案侧同名字段，按配置分隔符族归一首段后比对，输出 `proposal_count / node_count / state`；另出粗粒度 `domain_family_matrix`（首词）缓解标签碎片化。
* **主题×仓矩阵**：主题词表由提案语料的全局 TF-IDF 权重 × df 生成（纯统计、无域词表），再统计每个主题词在多少战役节点文本里**在位**（term presence，不受文档长度稀释），并按仓分解。
* **缺口账本逐项比对**：把注入的账本 markdown 解析成条目（表格行/列表项/`GAP-*` 标识符，带行号与章节），每条当查询：先查 `GAP-*` 是否被某节点文本**显式引用**（最硬证据），否则算查询覆盖率 `Σ min(w_q,w_d)/Σ w_q`（短 query 不被长文档稀释）。三态分类后与章节声明的状态（§四 planned / §五 open）逐项比对，差异显式入 `disagreement_items`，**机器不改账本**。

### 2.4 工具链

* 管线：`python3 -m tools.proposal_chain.pipeline --stage {sources|ledger|schema|dedup|coverage|labels|negative|figures|acceptance}`；每阶段只依赖前序工件，参数全来自配置，输出带 sha256。
* 图：`tools/proposal_chain/figures.py`（matplotlib，数据只读工件）；报告：`tools/proposal_chain/report.py`（数字程序化注入）。
* 测试：`pytest tests/test_proposal_chain_*.py`（合成域名，零领域语义）。

## ③ 计算结果

### 表 1：节点 metadata 契约（全量 + 100 随机子样）

| 指标 | 全量 | 100 随机子样 |
|---|---|---|
| 节点数 | 954 | 100 |
| 通过（四字段齐） | 899 | 97 |
| 降级（缺字段，恒等不丢） | 55（5.77%） | 3 |
| 缺 `acceptance_criteria` | 55 | 3 |
| 缺 `goal` | 11 | 1 |
| 缺 `ext_id` / `repo` | 1 / 1 | 0 / 0 |
| 与 oracle 对账漏报 | 0（0.0） | 0（0.0） |
| 键拼写告警 | {"key_typo:is_marity->is_maturity": 1} | — |
| 非阻断质量标记 | {"criteria_without_numeric_threshold": 37, "repo_not_in_whitelist": 1} | — |

### 表 2：语义去重器在标注集上的表现（标注 194 对：60 overlap / 134 distinct）

| 配置 | 档案召回（全量标注） | 自动档召回 | 自动档误判率 | 复核带误判率 |
|---|---|---|---|---|
| 裸 TF-IDF @0.85（基线） | 36.67% | 35.00% | 0.00% | 0.00% |
| TF-IDF→LSA（`claim` 表示，本方案） | 95.00% | 50.00% | 1.49% | 8.21% |
| 同上，holdout 半集（27 正 / 63 负） | 96.30% | 59.26% | 1.59% | 7.94% |
| 同上，tune 半集（33 正 / 71 负） | 93.94% | 42.42% | 1.41% | 8.45% |

表 2 的读法（**诚实口径**）：`自动档召回` 是“阈值 0.85 单档”下的召回，只有 50.00%——**“0.85 单阈值即达 90% 召回”在本语料上不成立**（判据 a 的字面读法 FAIL，见 ⑥）；`档案召回` 是机器门真实能力（自动档 ∪ 复核带），95.00%。精确率侧：自动档误判 1.49%（134 条负对里 2 条），复核带误判 8.21%（134 条负对里 11 条）。

### 表 2b：三种文档表示的对照（都只在 tune 半集校准 + 报告 holdout）

| 表示 | tune 校准阈值 | tune 档案召回 | holdout 档案召回 | 全标注档案召回 | 自动档召回 | 自动档误判 | 复核带误判 |
|---|---|---|---|---|---|---|---|
| claim **← 生产配置** | 0.65 | 93.94% | 96.30% | 95.00% | 50.00% | 1.49% | 8.21% |
| title | 0.5 | 93.94% | 100.00% | 96.67% | 41.67% | 0.00% | 7.46% |
| full | 0.85 | 78.79% | 81.48% | 80.00% | 80.00% | 3.73% | 0.00% |

选择纪律（`labels.representation_selection_note`，配置里原文）：只在 tune 半集比较；`title` 表示在标注上更高分，但**标注只读标题**（标注-表示同源 → 高估），因此生产配置用 `claim`（标题+首句）不用 `title`；`full` 表示在 pin 语料上按 tune 校准后档案召回低于 0.90，故不选。选中的表示再进入全池扫描（表 4）。

### 表 3：阈值在 tune 半集上的选择（复核带下界）

| 阈值 | tune 召回 | tune 误判率 |
|---|---|---|
| 0.40 | 100.00% | 36.62% |
| 0.50 | 100.00% | 28.17% |
| 0.55 | 100.00% | 22.54% |
| 0.60 | 100.00% | 14.08% |
| 0.65 ← 选定 | 93.94% | 9.86% |
| 0.70 | 84.85% | 4.23% |
| 0.75 | 72.73% | 2.82% |
| 0.80 | 60.61% | 2.82% |
| 0.85 | 42.42% | 1.41% |
| 0.90 | 33.33% | 1.41% |

选择规则（`labels.calibrate_review_threshold`，代码即规则）：误判率 ≤ 10.00% 的可行点中取召回最高者 → **0.65**。holdout 半集在该点上给出档案召回 96.30%、复核带误判 7.94%。

### 表 4：去重器在全池上的规模效应

| 池 | 文档 | campaign/计划 | 语义 backend | 自动档对 | 复核带对 | 跨战役自动档对 | 合并建议组 | 组内文档 |
|---|---|---|---|---|---|---|---|---|
| 提案池 | 2544 | 171 | lsa(200d) | 181 | 2509 | 124 | 57 | 140 |
| 战役节点 | 954 | 98 | lsa(200d) | 172 | 318 | 172 | 160 | 327 |

扫描耗时：提案池 51.06s，节点池 34.5s（loong64 sandbox，单线程）。

**节点池最大的合并建议组（节选）**

| 组 | 成员数 | 跨计划数 | 最高相似度 | 代表元 |
|---|---|---|---|---|
| G0083 | 3 | 3 | 1.000 | `node:2636` |
| G0003 | 3 | 3 | 0.995 | `node:2422` |
| G0019 | 3 | 3 | 0.986 | `node:2439` |
| G0054 | 3 | 3 | 0.982 | `node:2487` |
| G0140 | 3 | 3 | 0.960 | `node:3036` |

### 表 5：播种覆盖率账本

| 指标 | 值 |
|---|---|
| 域标签（原始，提案+节点） | 2610 个不同标签 |
| 域标签（归一首段后） | 548 个，其中节点侧有落点 64 个 |
| 域标签（粗粒度族） | 482 个，其中节点侧有落点 82 个 |
| 节点带域标签 | 195 / 954（20.44%） |
| 提案带域标签 | 2529 / 2544 |
| 主题词（统计生成） | 40 个，节点侧命中的 40 个 |
| 账本条目 / 参与一致性判定 | 129 / 12 |
| 条目状态分布 | {"covered_by_node": 6, "proposal_pending": 5, "uncovered": 118} |
| 判定一致率 | 50.00% |
| 差异条目 | L0015, L0016, L0018, L0020, L0022, L0023 |
| 备注分布 | {"gap_id_explicit:GAP-DEM-G3": 1, "gap_id_explicit:GAP-FEM-P0": 1, "plan_not_seeded": 6, "unmapped_declared_status": 117} |

**账本逐项比对（仅列出参与判定的条目，全量见 `ledger_coverage.json`）**

| 条目 | 行号 | 声明 | 判定 | 节点覆盖 | 提案覆盖 | 备注 | 原文（截断） |
|---|---|---|---|---|---|---|---|
| L0015 | 68 | planned | uncovered | 0.15 | 0.12 | plan_not_seeded | 锚升级原则（文献/实验锚，拒自证闭环） |
| L0016 | 69 | planned | uncovered | 0.17 | 0.18 | plan_not_seeded | 经验链基线（G1 总攻对 MT Uikku 实验锚） |
| L0017 | 70 | planned | covered_by_node | 0.23 | 0.20 | gap_id_explicit:GAP-FEM-P0 | **GAP-FEM-P0 + GAP-FEM-WINKLER** |
| L0018 | 71 | planned | uncovered | 0.12 | 0.09 | plan_not_seeded | 与 B1 IACS 链互证 |
| L0019 | 72 | planned | covered_by_node | 0.23 | 0.30 | gap_id_explicit:GAP-DEM-G3 | GAP-DEM-G3 延伸+摩擦触达（🔴缺摩擦需声明） |
| L0020 | 73 | planned | uncovered | 0.17 | 0.16 | plan_not_seeded | 多相能力谱+G9 自由面邻域 |
| L0021 | 74 | planned | covered_by_node | 0.40 | 0.20 | — | 平台能力台账新增冰区条目（须证据链升账） |
| L0022 | 75 | planned | uncovered | 0.13 | 0.13 | plan_not_seeded | 跨库证据流范式 |
| L0023 | 76 | planned | uncovered | 0.21 | 0.26 | plan_not_seeded | 台账诚实升级（已增补对账条款） |
| L0024 | 80 | open | uncovered | 0.29 | 0.28 | — | **平台账面**：能力台账 21 项仅 3 有证据；propulsi |
| L0025 | 82 | open | uncovered | 0.18 | 0.24 | — | **求解器硬缺口**（第二节 🔴 项）：DEM 摩擦/规模/GPU、 |
| L0026 | 84 | open | proposal_pending | 0.26 | 0.39 | — | **跨库契约**：failure_faces.json 是唯一成型冻 |

读法：§四 的 10 条多为**抽象短语**（“跨库证据流范式”“台账诚实升级”），与节点 goal 的词面重合低，因此出现 `plan_not_seeded`；两条含 `GAP-*` 标识符的条目被节点文本**显式引用**（`gap_id_explicit`），构成最硬的“计划→落点”证据。这说明覆盖回链要靠标识符，而不是靠文档里的散文描述。

### 表 6：负控（不崩即合格）

| 场景 | 结果 |
|---|---|
| bad_metadata_degrade | PASS |
| blank_texts | PASS |
| complete_node_accept | PASS |
| empty_pool | PASS |
| none_metadata_degrade | PASS |
| single_doc | PASS |

负控总判定：PASS。

## ④ 结果图

| 图 | 内容 | 文件 |
|---|---|---|
| 图 1 | 标注对相似度分布（overlap vs distinct）+ 两条阈值线 | `artifacts/proposal_chain/fig1_score_distribution.png` |
| 图 2 | 阈值–召回/误判曲线（tune 选集、holdout 标记）+ 重叠对落档分解（TF-IDF 基线 vs LSA） | `artifacts/proposal_chain/fig2_threshold_curve.png` |
| 图 3 | 域标签覆盖矩阵（提案 vs 节点计数）+ 账本条目状态分布 | `artifacts/proposal_chain/fig3_coverage_matrix.png` |
| 图 4 | 四必填字段填表率 / 缺失数 / 降级占比 | `artifacts/proposal_chain/fig4_schema_fields.png` |

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

1. **`scipy.sparse.linalg.svds` 在本仓部署面（loong64）直接 `double free` 崩溃**（exit 134），不能用于线上；本实现改为 numpy 手写随机化 SVD，行为可预期且确定性。
2. **中文无分词器的现实**：词项用「拉丁词 + CJK 二元组 + 单字」，会引入噪声，但对 paraphrase 是必要的召回来源；`keep_single_cjk` 是配置开关，关掉后召回显著下降（表 2 的基线即接近这种状态）。
3. **标注口径的局限**：194 对标注由候选结构规则抽取（与分数无关）+ 只读标题/仓/campaign 判定，未逐条读全文 goal；标题信息密度高使 title 表示的召回偏乐观。这是**单一标注者标注**，无双重标注一致性系数。
4. **阈值只在 tune 半集上选**，holdout 数字才是诚实泛化估计；两半集都写进表 2/表 3，取舍可复核。
5. **域标签系统性不可用**：提案侧 `physics_domain` 是自由文本，2551 条提案有 2648 个不同标签（几乎一条一个），节点侧只有 208/963 带标签。所以覆盖率**不能**建立在域标签上，主题×仓矩阵（统计词项 + 词项在位）才是可用的分母/分子，账本比对用查询覆盖率。
6. **复核带是有成本的**：复核带误判 8.21% 意味着每 100 条不相邻对被拉进人工复核；该参数是治理旋钮，不是免费午餐——配置里可一调二看（表 3）。
9. **提案池是活的**：`/root/tp-agent/proposals` 持续被其他 agent 写入（本次两次运行之间条目数从 2496 涨到 2566），IDF 与嵌入随之漂移、同一阈值下的分数会变。因此管线把语料 **pin 到 `corpus_compact.json.gz`（带 sha256）**，所有阶段读同一 pin；要复算“今天”的数字须 `--stage sources`（重扫）后再重跑全链。
7. **适用范围**：本门只判「文本语义重叠」，不判「能力是否已实现/已验收」；跨仓同名能力（同一能力在两仓各自落地）会被判重复，需人工决定合并还是保留（`cross_campaign` 标记已给出）。
8. **节点 metadata 契约是读侧 fail-soft**：降级不丢节点，但缺 `acceptance_criteria` 的 55 个节点在验收层面仍是空心——契约门只保证“能被机器读”，不保证“判据可裁”。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答）

| 关键量 | 目标 | 实测 | 是否达标 |
|---|---|---|---|
| (a) 去重档案召回（机器门） | ≥ 90.00% | 95.00%（holdout 96.30%） | PASS |
| (a′) 去重自动档召回（阈值 0.85 单档，字面口径） | ≥ 90.00% | 50.00% | **FAIL（如实申报）** |
| (a″) 裸 TF-IDF 基线档案召回 | — | 36.67% | 对照基线 |
| (b) 自动档误判率 | ≤ 10.00% | 1.49%（精确率 93.75%） | PASS |
| (b′) 复核带误判率 | ≤ 10.00% | 8.21% | PASS |
| (c) metadata 漏报 | = 0（100 节点子样） | 0（漏报率 0.0） | PASS |
| (d) 覆盖账本逐项比对 | 输出条目级判定 + 差异清单 | 129 条目 / 12 参与判定 / 一致率 50.00% / 差异 6 条 | PASS |
| (e) 负控（空池不崩） | 全部 PASS | 6/6 | PASS |
| (f) pytest | ≥ 15 passed | 60 passed / 0 failed | PASS |

**误差判定总结**：本节点没有“解析解/文献值”型基准，基准是**人工标注对（194 对，60 正 / 134 负）**。相对该基准：(a) 机器门档案召回 95.00%（目标 ≥90%，**达标**）；(a′) 但“0.85 单阈值自动档”召回仅 50.00%（**honest FAIL，不粉饰**）；(b) 自动档误判 1.49%（目标 ≤10%，达标）；(c) metadata 漏报 0（达标）。**是否 <3%？** 本任务量的单位不是物理误差，而是召回/误判率，故不适用 3% 判据；以任务给定的 90%/10% 门槛为准，逐项结论见上表。

## 附录：复现命令

```bash
# 采集（联网，只读 GET）并落快照
python3 -m tools.proposal_chain.pipeline --stage sources --live
# 账本解析 / 契约 / 去重 / 覆盖 / 标注评估 / 负控 / 图 / 验收
for s in ledger schema negative labels dedup coverage figures tests acceptance; do
  python3 -m tools.proposal_chain.pipeline --stage $s
done
# 测试
python3 -m pytest tests/test_proposal_chain_*.py -q
```

