# 待验收队列证据化分诊：156 张待验收单的可机器判定证据链审计（mission 2075）

**一句话**：对 `/api/v1/tensor/missions?status=awaiting_acceptance` 返回的全部 **156** 张单逐张做证据化分诊（会话 / 报告 / commit sha / 机器可算判据 / 绿测回执），产出分诊队列与排序表，**全程只读、未执行任何裁决写入**；核心发现是待验收积压的根因不是「空壳单」，而是 **96% 的单来自同一「自找活」模板、goal 里根本没有机器可判定判据**，以及报告↔mission 溯源链（mission_id / commit sha）大面积缺失。

## 1. 案例介绍（数据背景与参数表）

本单不是水动力/耐波算例，**没有物理场量**：被审对象是平台自身的任务账本（`tensor_dev_missions` + `tensor_reports`）。之所以属于船舶总体性能平台工作，是因为平台对每个能力/结论的 `REAL/MOCK/STUB/WITHHELD` 证据分级依赖「报告 + commit + 可判据」这条溯源链；溯源链断裂会直接把 MOCK 洗成 REAL，也会让真实成果被当成空壳。故本单审计的是**证据链本身**。

| 参数 | 取值 | 来源 |
|---|---|---|
| missions 端点 | `GET /api/v1/tensor/missions?status=awaiting_acceptance`（limit≤200, offset 分页） | API |
| reports 端点 | `GET /api/v1/tensor/reports?mission_id=<id>`；`GET /tensor/reports/{id}` 取正文 | API |
| 待验收总数（API total） | 156 | acceptance_queue.json |
| 分诊条目数 | 156 | acceptance_queue.json |
| 仓库分布 | TensorPlatform: 151 / ship-performance-platform: 5 | acceptance_queue.json |
| 模型分布 | MiniMax-M2.5-AWQ: 1 / Qwen3.8-27B: 3 / deepseek-v4-flash: 134 / qwen3.6-plus: 15 / qwen3.7-plus: 3 | acceptance_queue.json |
| 输入摘要 sha256 | `fde67317fc8369122fbdf9532b28faddde445479223d9d7e899b79da3d200aca` | acceptance_queue.json |
| 快照可复现 | 两次重放字节相同=True，与落盘工件相同=True（168885 B） | tools/m2075_publish_report.py |
| 裁决动作取值域 | accept / verify / rerelease / revoke | acceptance_triage.py |

## 2. 怎么做的（方法 / 流程 / 判据规则）

### 2.1 流程（`tools/acceptance_triage.py`，只读）

1. 分页拉全 `awaiting_acceptance`（服务端 limit 上限 200，按 offset 翻页，收集去重）；
2. 逐单拉 `reports?mission_id=<id>`（该端点无 offset，故用 per-mission 查询取全，避免 limit≤100 的截断）；
3. 无直连报告时，从 result 正文里解析「报告 id=NNN」并用 `GET /tensor/reports/{id}` 验存在性（`report_link = mentioned`）；
4. 证据抽取：commit sha（正则 + 上下文线索词）、绿测回执（`<n> passed` / `全绿` / `0 failed`）、会话 UUID（列 + result 内嵌）；
5. 按固定优先级套 5 条规则得 `suggested_action` 与非空 `reason`；
6. 排序（accept → verify → rerelease → revoke）后落 `artifacts/acceptance_queue.json`、`.md`、png、以及**输入快照**（可离线逐字节重放）。

**只读性**：`HttpTransport` 只暴露 `get`，模块内唯一的 POST 是 `/auth/login` 换 token；分诊器从不对 mission 端点发写请求（pytest 用 mock 断言）。

### 2.2 证据定义（E0）

- `has_session`：`session_id`/`agent_session_id` 列非空，**或** result / result.evidence.summary 内嵌 `agent_session_id` UUID（发现 F1）；
- `has_report`：存在 `mission_id` 直连的报告，**或** result 正文提及的报告 id 能经 `/tensor/reports/{id}` 查到（发现 F2）；
- `commit_evidence`：从 result / 报告 summary / 报告正文解析到的 sha token（7–40 hex、需在线索词 24 字符内、非纯数字、非英文 hex 词），**解析不到即 `unknown`，绝不猜**；只声明「文本里可解析」，不代表 git 对象存在（F3）；
- `criteria_computable`：goal 命中 C1–C6 任一机器可算判据（误差阈值 / 用例计数 / 差值为 0 / 逐字节相同 / 覆盖率 / 指标阈值）。

### 2.3 裁决规则（first match wins，已写入 JSON 的 `rules.precedence`）

| # | 条件 | action |
|---|---|---|
| 1 | no report evidence and no session evidence and no commit evidence | revoke |
| 2 | no report evidence and (session evidence or commit evidence) | rerelease |
| 3 | report evidence and commit_evidence == unknown | rerelease |
| 4 | report + commit found + criteria_computable + green_receipt | accept |
| 5 | report + commit found | verify |

### 2.4 验证手段（机器可算）

- pytest：`24 passed, 1 warning in 0.40s`（覆盖：覆盖率、动作域、reason 非空、幂等字节相同、三反例 revoke/verify/rerelease、只读性 mock、sha 解析正负例、C1–C6 正负例、accept 需绿测、排序、分布合计、快照重放）；
- 覆盖：条目数 156 == API total 156，差值 0；
- 幂等：同一快照重放两次 JSON 逐字节相同 = True，与已落盘工件逐字节相同 = True。

## 3. 计算结果（全量分诊分布）

### 3.1 action × repo

| action | TensorPlatform | ship-performance-platform | 合计 |
|---|---|---|---|
| accept | 0 | 1 | 1 |
| verify | 96 | 0 | 96 |
| rerelease | 55 | 4 | 59 |
| revoke | 0 | 0 | 0 |
| 合计 | 151 | 5 | 156 |

### 3.2 action × model

| action | MiniMax-M2.5-AWQ | Qwen3.8-27B | deepseek-v4-flash | qwen3.6-plus | qwen3.7-plus | 合计 |
|---|---|---|---|---|---|---|
| accept | 0 | 0 | 1 | 0 | 0 | 1 |
| verify | 0 | 2 | 94 | 0 | 0 | 96 |
| rerelease | 1 | 1 | 39 | 15 | 3 | 59 |
| revoke | 0 | 0 | 0 | 0 | 0 | 0 |
| 合计 | 1 | 3 | 134 | 15 | 3 | 156 |

### 3.3 证据覆盖（逐级衰减）

| 证据层级 | 张数 | 占队列 |
|---|---|---|
| 全量待验收 | 156 | 100% |
| 有会话证据 | 156 | 100.0% |
| 有报告证据 | 124 | 79.5% |
| 可解析 commit sha | 112 | 71.8% |
| goal 含机器可算判据 | 4 | 2.6% |
| result 含绿测回执 | 145 | 92.9% |
| accept（四证齐全） | 1 | 0.6% |

### 3.4 结构与判据可算性（本单最重要的结论）

| 分组 | 张数 | 含机器可算判据 | accept | verify | rerelease | revoke |
|---|---|---|---|---|---|---|
| 「自找活」模板单 | 150 | 1 | 0 | 95 | 55 | 0 |
| 具体工程单 | 6 | 3 | 1 | 1 | 4 | 0 |

- 模板单占比 **96.2%**（150/156），其 goal 是同一段自找活流程说明，只要求「测试绿 + 报告 + commit」，**没有任何数字阈值/用例计数**，因此机器无法判 accept，只能落到 verify（人工复算）。这是待验收积压的结构性原因，而非单张单的质量问题。
- 具体工程单仅 6 张，其中 3 张带机器可算判据；全队列唯一可机器 accept 的 1 张即来自此组。

### 3.5 判据/证据判定的误差量化（人工复核，程序化汇总）

| 项 | 结果 |
|---|---|
| criteria 抽样复核 | n=20（seed=7），一致 19，漏判 1，漏判率 5.0% |
| crit=True 全量复核 | n=4，动作判定正确 4/4，命中片段确为判据 2/4 |
| commit 解析窗口敏感性 | 窗口 12/24/40/60 字符 → 可解析 sha 的单 103 / 108 / 111 / 114（相对波动 10.2%） |

漏判的唯一实例是 mission 2027：goal 里写的是「computed==reference → PASS、+20% → FAIL、恰好 15% → PASS(<=)」这类**无「误差/用例计数」关键词的判据**，C1–C6 正则未覆盖 → 漏判（判为不可算）。crit=True 的两例命中片段不精确（2071 命中的是缺陷陈述 `ΔA=0`、2028 命中的是 Cb 扫描区间而非 `>5%` 判据），结论正确但理由需人工修正。

## 4. 结果图

![分诊分布](../artifacts/acceptance_queue_actions.png)

图 1：`suggested_action` 在 repo 与 model 两个维度上的分布（n=156）。verify 占绝对多数，revoke 为 0 —— 说明「从未占用会话的空壳单」在证据层面并不存在（见 5.1 F1）。

![证据漏斗与 sha 敏感性](../artifacts/m2075_evidence_coverage.png)

图 2 左：证据漏斗（全量 → 会话 → 报告 → commit → 判据 → 绿测 → accept），瓶颈在「判据」一级（模板单无判据）而非「会话」一级；图 2 右：commit 解析对线索词窗口参数的敏感性，用于界定该判据的稳定域。

## 5. 注意点（发现与局限）

### 5.1 三个数据完整性发现（可复现）

**F1 顶层会话列不可信**：队列中 `session_id` 列非空的只有 6 张，但 result 内嵌 `agent_session_id` UUID 的有 150 张，合计 156 张——即「session_id IS NULL ⇒ 从未占用会话」的推断不成立（任务书背景里的「148 张从未真正占用会话」据此需修正）。若只按列判定，会把已交付真成果误判为空壳。

**F2 报告↔mission 链接缺失**：有报告证据 124 张，其中 mission_id 直连 123 张、仅靠 result 正文提及才可溯源 1 张；`tensor_reports.mission_id` 为 NULL 会让「有报告」统计系统性偏低。

**F3 sha 只是文本 token**：本轮可解析 commit 证据 112 张，但未与 git 对象比对（他席分支在本 worktree 不可见），伪造 sha 同样会被记为 found；故 accept 仍保留人工抽检。

### 5.2 局限（如实声明）

1. **commit 证据可解析率未与真值比对**：解析不到即 unknown，会低估有 sha 的单（窗口敏感性 10.2%，见 3.5）；
2. **正则判据误判率未在全量上量化**：只做了 20 张抽样人工复核（漏判率 5.0%），未做全量人工标注，故该率不能外推；
3. **报告正文按需拉取**：仅当 result/summary/title 里没有 sha 时才取正文，若 sha 只出现在正文深处且该单已被前序证据判定为 found，则不会额外拉取（不影响本队列的 0/1 判定，但会影响 shas 列表完整度）；
4. **reports 端点无 offset**：单张单报告数 >100 时会被截断（本轮最大为 2 张，未触发）；
5. 本单只出队列、**不做任何裁决写入**：accept/revoke 需 owner 或 verifier 依队列执行；队列随任务流变化，重跑即刷新（幂等性以外不承诺时间稳定性）。

## 6. 误差判定（关键量 vs 基准，逐项回答是否 <3%）

| 关键量 | 基准/要求 | 实测 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| 分诊条目数 vs API total | 差值 = 0 | 156 vs 156（差值 0） | 0.00% | 是（0） |
| 幂等性（连跑两次） | JSON 逐字节相同 | 相同=True，与落盘工件相同=True | 0 B 差异 | 是（0） |
| 动作域 / reason 完整性 | 100% 合法且非空 | pytest 断言 100% | 0% | 是（0） |
| 有报告单的 commit 可解析判定 | 与真值一致（无真值） | 112/156，窗口敏感性 ±10.2% | 10.2% | **否** —— 参数敏感性 7% 量级，超 3% 门限，已如实声明 |
| criteria 判定 | 人工抽样一致 | 19/20 一致，漏判 1（5.0%） | 5.0% | **否** —— 抽样漏判率 5%，且未全量量化 |
| pytest 验收例数 | ≥8 例全绿 | 24 passed, 1 warning in 0.40s | — | 是（例数达标） |

结论：**机制类指标（覆盖、幂等、动作域）为精确 0 误差；语义类指标（commit 可解析、判据可算）不满足 <3%**，原因分别是参数敏感性与正则覆盖不足，已在 5.2 声明，未做粉饰。

