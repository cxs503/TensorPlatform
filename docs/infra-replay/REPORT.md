# P189-N07 战役损耗自动补偿闭环（成熟化）

> 平台共性机制节点。交付物：判定器 + 执行器 + 熔断 + KPI + 幂等审计；全部数字由
> `artifacts/infra_replay/acceptance.json`、`artifacts/infra_replay/round.json` 程序化注入
> （本文件由 `tools/validation/infra_replay_report.py` 生成，零手抄）。

## ① 案例介绍：补的是哪一段链条

平台上「任务没了」这件事早就有机器诊断（`tools/validation/mission_attribution.py` 输出九类
归因），但诊断只到**记账**：节点照样停在 blocked，战役产出照样掉。本节点把链条补成
**闭环**——判定能不能复活 → 生成重播 mission → 熔断坏模型 → 把「补偿了多少」变成 KPI。

运行环境与数据面（全部现算，非快照）：

| 项 | 值 |
|---|---|
| 全库终态 mission（succeeded/failed/revoked，排除重播自身） | 8096 |
| 其中归因 `infra_runtime` | 706 |
| 判定可复活（白名单运行期丢失 **且** `tool_calls == 0`） | 42 |
| 归档 plan 示例（plan 188）节点数 | 11 |
| 沙箱 schema（真写，生产零写） | `sbx_infra_replay` |

## ② 怎么做的：五个部件

1. **复活判定器**（`classify_replayable`，纯函数）：判定序 kill-switch → 非终态 → 归因类
   （成功/声明证伪/验收拒绝/行政去重/崩溃/无证据/非运行期）→ 白名单 token → 零工具调用。
   `reason_code` 唯一（16 个词条，测试逐条正负控）。
   白名单只有运行期丢失五类：`llm_error` / `agent_lost` / `budget_exhausted` /
   `wall_exhausted` / `no_progress`。
2. **重播执行器**（`replay_one` / `run_round`）：新 mission 带 `parent_mission_id` 指回原节点，
   `task_key = infra_replay:<parent>:<attempt>`；模型走**回退链**（跳黑名单 + yield 降序 +
   同分名字定序，确定性可复跑）。
3. **预算双闸**：每节点 ≤ 2 次；每 plan ≤ 20% 计划节点数
   （小 plan 取 `max(1, floor(20%))`，`raw_plan_cap` 同账登记）。
4. **熔断**：同模型**连续** 3 次 `infra_runtime` → 写 `tensor_model_blacklist` + 事件 + 通知载荷；
   别的模型的失败不串烧，成功一次即打断连击。
5. **度量**：`campaign.yield`（节点口径）/ `mission_level_yield` / `infra_loss_rate` /
   `forced_finish_rate`。

审计落两处 append-only：`mission_lifecycle_log(action='infra_replay')` 与
`campaign_events(event_type='mission_replay' / 'model_blacklisted')`。这两张表的 CHECK 词表
原无上述词条，故附迁移 `backend/migrations/111_infra_replay_action.sql`（棘轮：只增词不缩词）。

## ③ 计算结果

### 判据总表

| # | 判据 | 状态 | 关键数字 |
|---|---|---|---|
| a | 判据 a｜复活判定混淆矩阵（7 格） | **PASS** | `{"cells_correct": "7/7"}` |
| b | 判据 b｜幂等：同节点连调 3 次只建 1 个 mission | **PASS** | `{"missions_created": 1, "call_reason_codes": ["replay_infra_zero_work", "already_replayed", "already_replayed"]}` |
| c | 判据 c｜熔断：同模型连续 3 次 infra_runtime | **PASS** | `{"blacklist_delta": 1, "event_delta": 1, "选中的模型": "modelB", "两次连续负控熔断": false}` |
| d | 判据 d｜预算：每节点第 3 次重播被拒（reason=budget_cap） | **PASS** | `{"third_reason_code": "budget_cap", "audit_rows": 1}` |
| e | 判据 e｜campaign.yield 现算（plan 188） | **PASS** | `{"campaign.yield": 0.5454545454545454, "expected": 0.5454545454545454}` |
| f | 判据 f｜零领域语义扫描 + pytest | **PASS** | `{"domain_hits": 0, "pytest": "28 passed, 1 warning in 0.54s"}` |
| g | 判据 g｜kill-switch 关闭后全判 false | **PASS** | `{"scanned": 8096, "replayable": 0}` |

### 判据 a 明细（7 格混淆矩阵）

| mission | 期望 replayable | 实判 | reason_code | 归因类 | 命中的运行期 token | tool_calls |
|---|---|---|---|---|---|---|
| 6771 | True | True | replay_infra_zero_work | infra_runtime | llm_error,agent_lost | 0 |
| 6816 | True | True | replay_infra_zero_work | infra_runtime | llm_error,agent_lost | 0 |
| 6900 | True | True | replay_infra_zero_work | infra_runtime | llm_error,agent_lost | 0 |
| 7025 | True | True | replay_infra_zero_work | infra_runtime | llm_error,agent_lost | 0 |
| 7044 | True | True | replay_infra_zero_work | infra_runtime | llm_error,agent_lost | 0 |
| 578 | False | False | claim_falsified | claim_falsified | — | 39 |
| 2 | False | False | acceptance_rejected | acceptance_rejected | — | 15 |

### 全库判读表（不靠 7 条样本活命）

| reason_code | 终态 mission 数 | 说明 |
|---|---|---|
| `already_succeeded` | 7005 | 已成功，无需补偿 |
| `work_already_done` | 506 | 已有工具调用（干过活），重播会重复劳动/污染工作树 |
| `admin_dedupe` | 170 | 行政去重停单，不计质量也不补偿 |
| `tool_calls_unknown` | 158 | tool_calls 缺失，无法证明零工作量 |
| `evidence_absent` | 142 | 无证据（零改动/未 finish），不可判定为运行期丢失 |
| `infra_crash_not_whitelisted` | 56 | 基础设施崩溃（仓缺失/异常退出），不属运行期白名单 |
| `replay_infra_zero_work` | 42 | 运行期丢失且零工具调用 → 可复活 |
| `acceptance_rejected` | 14 | 验收判据拒绝（技术失败，重播=掩盖失败） |
| `claim_falsified` | 3 | 声明被独立复核证伪（技术失败，重播=掩盖失败） |

### KPI

| KPI | 分子/分母 | 值 | 口径 |
|---|---|---|---|
| campaign.yield（plan 188，节点口径） | 6/11 | 0.545455 | done/campaign_nodes.total |
| mission_level_yield（plan 188，mission 口径） | 11/11 | 1.000000 | succeeded/tensor_dev_missions.total |
| infra_loss_rate（全库终态） | 706/8096 | 0.087204 | infra_runtime/terminal_mission |
| forced_finish_rate（全库终态） | 1054/8096 | 0.130188 | forced_finish_marker/terminal_mission |

### 真跑一轮补偿（沙箱 schema `sbx_infra_replay_round`）

| 项 | 值 |
|---|---|
| 扫描终态 mission | 8096 |
| 判定可复活 | 42 |
| 实际建重播 mission | 42 |
| 幂等跳过 | 0 |
| 预算拒绝 | 0 |
| 沙箱内 replay mission 行 | 42 |
| 沙箱内 lifecycle 审计行 | 42 |
| 沙箱内 campaign_events 行 | 42 |
| 沙箱 seed（候选 mission） | 42 |

生产库零写入（`SELECT count(*) ... task_key LIKE 'infra_replay:%'` = 0，`tensor_model_blacklist` = 0）。

## ④ 图

`artifacts/infra_replay/fig_acceptance.png`（三面板）：

1. **判读分布**（log 横轴）：全库 8096 条终态 mission 的 reason_code 直方图——
   说明「可复活」只占极小一撮，补偿不是万能药；
2. **KPI 面板**：节点口径 `campaign.yield`=0.545455 与
   mission 口径 1.000000 的**分叉**被显式画出；
3. **混淆矩阵**：判据 a 的 7 格期望×实判，7/7 落在对角。

本节点是平台机制（无语义场量、无网格），故不存在云图/场分布；替代为「判定池分布 + KPI +
混淆矩阵」三张可复算图，这正是本类任务的判据载体。

## ⑤ 注意点（踩过的坑，均已固化为代码或测试）

- **终局标记不在列上**：`tensor_dev_missions.fail_stage` 列全空，真正的标记在
  `result.evidence.checks.fail_stage`（以及 `result.stop_condition`）。只读顶层字段会把
  42 个可复活节点里的绝大多数漏掉（实测漏到只剩 5 个），修好后正确识别 42 个。
- **`origin` 不能当身份**：生产表 CHECK 只允许 `owner`/`agent`，所以重播身份用
  `task_key` 前缀 + `parent_mission_id` + `result.reason_code` 三件套，零 schema 改动。
- **`campaign_events.plan_id` NOT NULL**：未挂节点的 mission 用哨兵 `0`（全局事件），显式记账。
- **审计词表 CHECK**：`mission_lifecycle_log.action` / `campaign_events.event_type` 是白名单
  CHECK，不迁移就 CheckViolation（#600 custody_requeue 同类坑；CI 若走 sqlite 拦不住）。
- **性能陷阱**：全库扫描时若对每个 mission 都开一次 store 连接，一轮补偿会变成上千次往返
  （实测把 120s 预算烧穿）。改为**先判定、命中才碰 store**。
- **归因输入必须完整**：给归因器喂「部分字段」的 mission 会把「revoked 但无 revoked_reason」
  误判成 `claim_falsified`（本节点自查抓到并留了回归桩）。
- **JOIN 放大**：一条 mission 可被多个 campaign_nodes 引用，直接 JOIN 会出现重复 mission（幂等闸
  挡住了，但计数会飘）→ 改 `LATERAL ... LIMIT 1`。
- **适用范围**：白名单只管**零工作量**的运行期丢失。真实舰队里 `work_already_done` 有 506
  条、`tool_calls_unknown` 有 158 条——前者重播会重复劳动/污染工作树，正确解法是
  **保留工作树续跑**（另一条机制，不在本节点范围）；后者需要 harness 补记 `tool_calls` 才能判定。

## ⑥ 误差判定（逐项对基准，显式回答「是否 < 3%」）

| 关键量 | 本实现 | 基准/手算 | 相对误差 | < 3%？ |
|---|---|---|---|---|
| campaign.yield（plan 188） | 0.545455 | 6/11 = 0.545455 | 0.000% | 是（浮点等价） |
| 混淆矩阵正确格 | 7 | 7 | 0% | 是（逐格精确匹配） |
| 幂等：3 次调用建 mission 数 | 1 | 1 | 0% | 是 |
| 熔断：黑名单增量 / 事件增量 | 1 / 1 | 1 / ≥1 | 0% | 是 |
| 预算：第 3 次重播拒绝审计行数 | 1 | 1 | 0% | 是 |
| kill-switch 下 replayable 数 | 0 | 0 | 0% | 是 |
| pytest | 28 passed / 0 failed | 全绿 | — | 是 |

判据数字与产物路径：
- `artifacts/infra_replay/acceptance.json`（判据 a–g 逐项 + 误差 + pytest）
- `artifacts/infra_replay/round.json`（全库一轮补偿真跑：判读直方图 + 沙箱审计计数）
- `artifacts/infra_replay/fig_acceptance.png`（三面板图）

## 与节点书口径的偏差（honest）

1. 节点书写「5 个 llm_error 节点全部 replayable=true」。**实测不是 5 而是 42**：只要
   把终局标记从 `result.evidence.checks.fail_stage` 读出来，零工作量的 `llm_error` 节点有
   42 个；恰好等于 5 的是**只读顶层字段**时的 `agent_lost` 子集。判据 a 的 7 格样本用
   真实 5 条（6771/6816/6900/7025/7044）承载「可复活」侧，逐格精确命中。
2. 节点书写「m6854（honest FAIL）判 false」。**实测 m6854 在库内是 `succeeded`**
   （其报告 id 8329 对应一次真实交付），不是 honest FAIL；本节点改用真实载体：
   `claim_falsified` = 3 条（如 578）、`acceptance_rejected` = 14 条（如 2），并逐格验明 false。
   这条偏差按「honest FAIL 不可覆盖」记录在此，不粉饰。
3. 节点书要求「campaign.yield 对 plan 188 = 6/11」。**现算命中**（6/11），
   但同 plan 的 mission 口径是 11/11：
   5 个节点 status=blocked 而其 mission 已 succeeded（派发面没回写节点状态）。分叉不是
   本节点能修的，故两个口径同时登记、明确标注，不做选择性呈现。
