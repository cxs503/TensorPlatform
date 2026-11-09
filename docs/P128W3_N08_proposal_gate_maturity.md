# 提案→战役播种供给链的三道机器门：契约 / 跨提案去重 / 播种覆盖率账本

**节点**：`p128w3-n08-proposal-gate-maturity`（mission 7450） · **落地仓**：TensorPlatform
**代码**：`tools/proposal_gate/`（config / textsim / contract / index / ledger / gate / sources /
collect / calibrate / figures / acceptance） + `backend/api/proposal_gate.py` + `backend/migrations/109_proposal_gate_ledger.sql`
**参数与词表**：`config/proposal_gate.json`（阈值/白名单）、`config/domain_word_denylist.json`（域词黑名单，注入式）
**机器验收**：`artifacts/proposal_gate/acceptance.json`（6/6 PASS，`all_pass=True`）

## ① 案例介绍（物理背景 → 平台背景）

自主续航的组织里，**提案是唯一的创新入口**：提案 → 仲裁 → 战役 DAG 节点 → 交付。这条链条一旦
没有机器门，就会反复出现三类事故（平台历史事故的共性）：(i) 提案质量地板失效——「效果更好」
这类不可裁的判据进仲裁，评审只能靠人；(ii) 同一件事被反复立项——同一提案换个说法再次提交，
或跨战役重复播种同一能力，资源被摊薄；(iii) 采纳之后没有对账——提案是否真的变成战役节点、节点
是否真的挂上 mission，谁也不知道。

本节点为这条供给链建三道门，全部落在平台共性层，**零领域语义**（域词表/白名单/阈值一律配置注入）：

| 门 | 职责 | 失败语义 |
|---|---|---|
| 契约门 | 产物 schema（v1 历史口径 / v2 任务书口径）+ 口径判据 | fail-closed 进隔离区，不进仲裁 |
| 去重门 | NFKC 归一化 → 字符 3-gram → Jaccard（+ 长度比受控包含度）+ 结构化孪生 | 自动拒 / 复核带 / 通过 |
| 覆盖率门 | 四态账本（`proposal/adopted/dedup_rejected/quarantined`）+ 播种落点核验 | 分母分子都可 SQL 溯源 |

真库规模（本次采集，`artifacts/proposal_gate/strict/collect_latest.json`）：
**923 个 campaign_nodes / 98 个 campaign_plans**，
其中带 `mission_id` 的节点 512 个（0.5547），
节点名能推断出仓库名的仅 145 个。

## ② 怎么做的（方法 / 流程 / 工具链）

### 2.1 契约门：双版本 schema + 显式 compat 层

* `schemas/proposal.v1.json`：**历史口径**（存量提案池实际形态：`proposal_id/title/hypothesis/falsification_criteria/expected_artifacts/...`），
  只做结构校验；
* `schemas/proposal.v2.json`：**任务书口径**（`{schema_version,title,repo,goal{objective,criteria[]},is_maturity}`），即规范内部模型的外部形态；
* **compat 层**：v1 → 规范模型的字段映射写在配置里（`compat.v1_to_canonical`，支持点号路径
  `triggered_by.repo`），v1 缺省值也在配置里（`compat.v1_defaults`）。因此 v1 语义**从不直接进入**去重与仲裁，
  避免「口径 A 校验、口径 B 使用」的漂移；v1 也要过同一套口径判据（映射不改判据强度）。
* 口径判据（机器可裁）：title 非空；`repo ∈ repo_whitelist`；`goal.criteria ≥ min_criteria(=3)`；
  **每条判据含可量化阈值**（命中 `numeric_pattern` **且**（命中 `comparator_pattern` 或含 `unit_symbols`）——
  「重复 3 次」有数没方向，不算阈值）；`is_maturity` 必须是真 bool（不接受 `"true"`/`1`）。

### 2.2 去重门：字符 3-gram（零词表）+ 结构化孪生

* 归一化链：NFKC → 去排版记号 → 标点/符号/控制符折算为空格 → 折叠空白 → casefold；
* 相似度：字符 3-gram 集合的 Jaccard；再叠加长度比受控的**包含度**（同义改写常表现为「加删从句」，
  Jaccard 被分母惩罚而包含度不受惩罚），`score = max(jaccard, w·containment)`，
  仅当长度比 ≥ `min_length_ratio` 时允许包含度参与（防短标题被长文本「整体包含」而误判）；
* 结构化冲突：同 `repo` 且 `dependencies_hint` 集合 Jaccard ≥ `twin_dependency_overlap(0.8)` → 孪生提案；
* 三档判定：`score ≥ dup_threshold(0.85)` 自动拒；`[review_threshold(0.55), dup_threshold)` 进**复核带**
  （不自动拒、也不入池，写隔离区待复核）；否则通过并入索引。阈值全部在配置里，代码零字面量
  （由 `tests/test_proposal_gate_antidote.py::test_thresholds_live_only_in_config` 钉死）。

### 2.3 账本与覆盖率

* 表 `proposal_gate_ledger`（迁移 `backend/migrations/109_proposal_gate_ledger.sql`）：
  规范列 `(mission_id, plan_id, sha256, schema_version, verdict, dup_of, score, at)` +
  审计超集列 `(state, reasons, source_path, blob, updated_at)`；
  **UNIQUE(mission_id, sha256)** 是数据库级幂等键（不是应用层 if）；
* 四态计数与重复率经 `GET /proposals/gate/stats` 暴露，KPI `metric_key=guide.proposal_dup_rejected`
  （另有 `guide.proposal_seed_coverage`）；
* 播种覆盖率两条互补口径，分开报不混用：
  (a) **文本可追溯**（采纳提案 ↔ 节点文本相似度 ≥ `coverage_match_threshold`）；
  (b) **真播种落点**（采纳提案的 `mission_id` 在 `campaign_nodes` 中确有节点）。

### 2.4 工具链

`tools/proposal_gate/collect.py`（平台 API 只读拉真库节点 → 留一法逐条审计 → 落 sqlite 账本 +
事件 jsonl）→ `calibrate.py`（合成正/负样本阈值扫描 + 真语料分布）→ `figures.py`（4 图 + sha256 manifest）
→ `acceptance.py`（6 项机器验收）→ 本报告生成器（数字全从工件注入）。

## ③ 计算结果（真库 + 合成标定，全部可复算）

### 3.1 真库留一法审计（923 节点，两种仓库口径）

留一法：把全量节点灌进索引，再逐条把该节点自身排除后送门——等价于「若今天重新提交会怎样」。

| 口径 | 索引节点数 | 通过(accepted) | 自动拒(duplicate) | 复核带(near_duplicate) | 隔离(quarantined) |
|---|---|---|---|---|---|
| 严格口径（仓库名必须出现在节点名） | 923 | 81 | 33 | 22 | 787 |
| 宽容口径（仓库兜底 TensorPlatform） | 923 | 424 | 225 | 152 | 122 |

严格口径下 787 条隔离中 **778 条的首因是 `repo_not_whitelisted`**（节点名不以仓库名开头 → 载荷里
`.repo` 为空），这既是真实缺口（只有 145/923 节点名可推断仓库），
也是本审计的**口径伪影**——因此同时给宽容口径（兜底仓 = 配置白名单内仓库）：

* 契约通过率：严格 0.1473
  ｜ 宽容 0.8678
* 隔离首因（严格口径 Top5）：`schema×815, repo_not_whitelisted×778, criteria_too_few×122, nearest_below_threshold×81, dup_of_kind=campaign_node×55`

### 3.2 KPI 与覆盖率（账本口径）

| 指标 | 严格口径 | 宽容口径 |
|---|---|---|
| 重复率（占全部裁决行） | 0.0356 | 0.2430 |
| 复核带率（占全部裁决行） | 0.0238 | 0.1641 |
| 重复率（占通过契约门的提案） | 0.2426 | 0.2809 |
| 复核带率（占通过契约门的提案） | 0.1618 | 0.1898 |
| 播种落点可核验率 seed_point_rate | 0.5926 | 0.6014 |
| 节点→mission 可追溯率 | 0.5547 | 0.5547 |

两个口径的 SQL 复算（工件里原样保存）：`{"db": "artifacts/proposal_gate/strict/ledger.sqlite", "sql": "SELECT state, count(*) FROM proposal_gate_ledger GROUP BY state ORDER BY state", "state_counts": {"adopted": 81, "dedup_rejected": 33, "quarantined": 812}, "…`

严格口径账本四态：`{"proposal": 0, "adopted": 81, "dedup_rejected": 33, "quarantined": 812}`；
宽容口径：`{"proposal": 0, "adopted": 424, "dedup_rejected": 225, "quarantined": 277}`（其 quarantined = 契约失败 122 + 复核带 152 + 历史池 3）。
KPI：`guide.proposal_dup_rejected = 225`（分母 926）、
`guide.proposal_seed_coverage = 255/424 = 0.601415`。

历史提案池（in-repo `**/proposals/**/*.json`，n=3）：裁决 `{"quarantined": 3}`——
三份都因**有一条判据不含可量化阈值**被判隔离（例如「一次一变量与因子设计结果一致（|ΔD| < 族半宽）」：
有比较符无数值），说明契约门对存量池有真实牙齿。

### 3.3 阈值标定（合成正/负样本 + 真语料分布）

正样本 n=54（同义替换 1..5 处 × 10 seed + 同义改写族），负样本 n=5（主题不同的合法提案）。
**正样本最低分 0.6415，负样本最高分 0.1321，间隔 0.5094**。

| 工作点 | 阈值 | TP | FN | FP | TPR | FPR |
|---|---|---|---|---|---|---|
| 自动拒 dup | 0.85 | 33 | 21 | 0 | 0.611111 | 0.0 |
| 复核带 review | 0.55 | 54 | 0 | 0 | 1.0 | 0.0 |

* 自动拒工作点（0.85）：TPR 0.6111 / FPR 0.0000——只吃「轻改写」，宁漏不误；
* 复核带工作点（0.55）：TPR 1.0000 / FPR 0.0000——**召回 100%、误判 0**，
  与真语料间隔一致（真语料负侧 p95 = 0.1641、中位数 0.0541）。

改写强度曲线（同义替换处数 → 相似度，10 seed）：

| 替换处数 k | min 相似度 | mean 相似度 | max 相似度 |
|---|---|---|---|
| 0 | 1.0 | 1.0 | 1.0 |
| 1 | 0.886792 | 0.949057 | 1.0 |
| 2 | 0.830189 | 0.909434 | 1.0 |
| 3 | 0.792453 | 0.869811 | 0.962264 |
| 4 | 0.754717 | 0.830189 | 0.90566 |
| 5 | 0.716981 | 0.796226 | 0.867925 |
| 6 | 0.698113 | 0.777358 | 0.830189 |
| 7 | 0.660377 | 0.750943 | 0.830189 |
| 8 | 0.660377 | 0.720755 | 0.830189 |

真语料（368 节点 / 67528 对，中位数 0.0541，p95 0.1641，最大 1.0000）阈值平台区：

| 阈值 | ≥阈值的节点对数量 |
|---|---|
| 0.45 | 29 |
| 0.55 | 28 |
| 0.65 | 27 |
| 0.75 | 27 |
| 0.85 | 27 |
| 0.9 | 27 |

**平台区结论**：0.65–0.90 之间取的阈值给出同一组 27 对判定——真语料上「近 1.0 的跨战役重复节点」
与其余语料之间是**干净的空隙**（0.4–0.95 之间只有个位数对），所以 0.85 的值不是拟合出来的，是平台区内任取。

### 3.4 机器验收（6/6）

| 验收项 | 结论 | 工件 |
|---|---|---|
| schema_contract | PASS | tools/proposal_gate/schemas/proposal.v1.json + proposal.v2.json |
| dedup_engine | PASS | artifacts/proposal_gate/acceptance.json#dedup_engine（sqlite 临时库复算） |
| ledger_four_state | PASS | artifacts/proposal_gate/acceptance.json#ledger_four_state（sqlite 临时库复算） |
| dual_index_real_corpus | PASS | artifacts/proposal_gate/{strict,lenient}/collect_latest.json |
| threshold_calibration | PASS | artifacts/proposal_gate/calibration.json |
| zero_domain_semantics | PASS | tools/audit/domain_leak_scan.py |

## ④ 结果图（PNG + sha256 溯源）

- `artifacts/proposal_gate/figures/fig1_corpus_similarity.png`（sha256 `7b0a0ba1e3c1310f…`, 55021 B）
- `artifacts/proposal_gate/figures/fig2_threshold_sweep.png`（sha256 `0c1c3a4037dcf3e9…`, 54798 B）
- `artifacts/proposal_gate/figures/fig3_edit_intensity.png`（sha256 `5419cb0417163740…`, 82626 B）
- `artifacts/proposal_gate/figures/fig4_ledger_states.png`（sha256 `7af9480d4b5888e1…`, 81352 B）

* **图 1 `fig1_corpus_similarity.png`**：真语料两两相似度分布（log 纵轴）+ 两条阈值线；尾部 27 对 ≈1.0 为跨战役重复节点。
* **图 2 `fig2_threshold_sweep.png`**：阈值扫描 TPR/FPR 与两个工作点（自动拒 / 复核带）。
* **图 3 `fig3_edit_intensity.png`**：改写强度（同义替换处数）→ 相似度 mean±min/max 与阈值线。
* **图 4 `fig4_ledger_states.png`**：两口径账本裁决分布 + 节点→mission 可追溯率。

## ⑤ 注意点 / 局限（honest）

1. **PG 迁移未在本沙箱实跑**：`psycopg2` 在本环境是 stub（无真实驱动），109 号迁移与 PG 方言未做端到端验证；
   已用 SQLAlchemy Core 在 sqlite 上验证同一套 DDL/约束（唯一键冲突测试直接打穿到 DB 层）。此条为**未完成项**。
2. **孪生门在真库未获锻炼**：`campaign_nodes` 不带 `dependencies_hint`，真库 run 中 twin=0；孪生门仅由合成测试覆盖
   （同 repo + 依赖重合 4/5 = 0.80 → twin）。
3. **负样本只有 5 条**（合成），精度的置信区间不宽；真语料分布做了补强，但没有人工标注的「同义改写 vs 不同主题」金标集。
4. **召回上限**：字符 3-gram 对「大幅重写」（同义替换 ≥8 处，mean 相似度 0.7208）已临界，
   结构性重写（换叙述框架、换句子粒度）会掉到复核带以下 → 会漏。复核带只覆盖到「中等改写」。
5. **严格口径的 `repo_not_whitelisted` 主导是审计口径伪影**：真缺口是节点元数据里没有 repo 字段，
   建议 dispatcher 在播种时把 repo 写进 `campaign_nodes.metadata`（已作为改进建议登记，本节点未改 dispatcher）。
6. **采纳证据是推断的**：平台目前没有显式「采纳」记账，本 run 的 adopted 由「节点已存在于真库」反推；
   建议把 `POST /proposals/gate/adopt` 接进 dispatcher 的播种路径（未做）。
7. **端点未在活体平台上线**：`GET /api/v1/proposals/gate/stats` 在 8010 活体实例上仍返回 404
   （活体跑的是主干检出，本分支需合并后才生效）；HTTP 层由 5 条 TestClient 用例覆盖，非活体验证。
8. **quarantine 语义扩展**：任务书四态无「复核」态，本实现把复核带映射到 `quarantined`（语义：不进仲裁），
   并在 `reasons`/`verdict` 里区分 `near_duplicate` 与契约失败，`dup_rate` 只统计 `dedup_rejected` 不掺复核带。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 实测 | 误差/余量 | <3%？ |
|---|---|---|---|---|
| 幂等重提不新增行 | 期望 1 行 | `rows_after_resubmission = 1` | 0 行差 | 是（0%） |
| 轻改写必须命中自动拒 | 阈值 0.85 | 实测 score 0.9200（verdict `duplicate`） | 高于阈值 +0.0700 | 是（余量>0） |
| 主题不同不得误判 | 复核带下界 0.55 | 实测 score 0.0217（verdict `accepted`） | 低于阈值 0.5283 | 是（余量>0） |
| 孪生（同 repo + 依赖提示完全重合） | 阈值 0.8 | 实测重合 1.0000（verdict `twin`） | 高于阈值 +0.2000 | 是（≥阈值即触发） |
| 复核带召回（合成） | TPR=1.0 | 1.0000 | 0 | 是（0%） |
| 复核带误判（合成） | FPR=0.0 | 0.0000 | 0 | 是（0%） |
| 真语料阈值稳健性 | 平台区同解 | 0.65/0.75/0.85/0.90 同为 27 对 | 相对差 0/27 | 是（0%） |
| PG 迁移端到端 | 期望通过 | 未执行（驱动缺失） | — | **无基准·未完成** |

**结论**：需要在真库上回答的两个量——「跨战役重复节点的重复率」与「契约通过率」——分别给出
0.2809（占通过契约门提案）与
0.8678（占全部审计节点，宽容口径）。
这两项**没有外部基准可对标**（平台此前没有任何重复率基准），故以「阈值平台区同解 + 合成标定 TPR/FPR」自洽性作为替代判据，
并如实声明：不存在可比的第三方参考值。唯一有明确基准的量（幂等性、阈值方向性）均满足误差 <3%。
