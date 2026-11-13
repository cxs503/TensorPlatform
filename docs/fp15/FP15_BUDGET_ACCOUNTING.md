# FP15-BUDGET-ACCOUNTING：预算记账落库 + 帽-成功率曲线

> 锚点 FP15-BUDGET-ACCOUNTING（规划线第 15 班实测授权）。全部数字由脚本产出，
> 零手抄：`docs/fp15/fp15_intel.py`（SQL 只读取证）+ `scripts/budget_cap_curve.py`
> （API 全量复算）。两份 JSON 工件各带自己的窗口起止时间戳与生成 SQL/查询原文。

## ① 案例介绍（问题与背景）

舰队终态单的"花了多少"过去**完全没有记账**：`tensor_dev_missions.budget_used`
100% 为 `{}`。于是终局裁决（`budget_exhausted` / `no_progress`）外部不可复核——
说不清"为什么判定预算耗尽"，也就无法判断是 cap 配小了还是 agent 空转。

实测窗口（`docs/fp15/fp15_intel.json`，`finished_at ∈ [2026-11-12T05:20:27+00:00,
2026-11-13T05:20:27+00:00]`）：

| 指标 | 数值 | 出处 |
|---|---|---|
| 终态单 | 1286 | `queries.window_totals` |
| `budget_used` 非空 | **0（0.0%）** | 同上 |
| `budget_used` 含 `tool_calls` | 0 | 同上 |
| 可从 result 事实回算 tool_calls | 1279 | `queries.usage_fact_availability.from_result_tool_calls` |
| 连事实都没有（不可回填） | 7 | `queries.backfill_classification` |

终局裁决分布（`queries.verdict_distribution`）：`null` 907、`budget_exhausted+forced_finish`
192、`no_progress` 82、`budget_exhausted` 37、`llm_error` 32、
`no_progress+forced_finish` 29、`agent_lost` 6、`wall_exhausted+forced_finish` 1。
→ budget 家族 229（17.8%）、no_progress 家族 111（8.6%）：每 5~6 单就有 1 单的终局
裁决当时无法复核。

> 口径澄清：任务书引用的原始 24h 快照（381 终态 / 43 budget / 96 no_progress /
> cap `{80:197, 60:182, 100:2}`）对应的 `docs/fp15/fp15_intel.json` 在本 worktree 里
> **不存在**（历史工件未入库），故其上数字不可复算；本报告改用同口径脚本在**当前**
> 窗口重算，并给出上表实数。两类计数（历史/新生）都在下面逐项给数，不粉饰。

## ② 怎么做的（方法/工具链）

1. **记账服务（单一真源）** `backend/services/budget_accounting.py`
   - 契约四键 `{tool_calls, tokens, wall_seconds, source}`；逐字段 `sources` 映射。
   - 出处优先级：agent 自报 `usage` → `result.tool_calls` → `result.progress.tool_calls`
     （心跳）→ `result.detail` 里的 `tool_calls=N`（老 runner 字符串）→ 服务端时钟
     `now - created_at`（含排队的上界，如实标注 `server_clock_upper_bound`）。
   - **fail-open**：空/缺字段/脏类型（负数、NaN、bool、字符串、非 mapping）
     一律降级为 0 + `unavailable`，绝不抛异常阻断终态转移。
   - 同时镜像历史键 `tool_calls_used / tokens_used / wall_seconds_used`，
     使 suspend/resume 的预算续算与 write-off 的 `credit_ledger` 不再恒 0。
2. **接线**（`backend/api/tensor_missions.py`、`backend/api/tensor_mission_lifecycle.py`）
   - `POST /tensor/missions/{id}/complete`：两条路径（`awaiting_acceptance` 与终态）
     都写 `budget_used`；`result` 先与本次 summary 合并再推导，保证落库后
     `budget_used->>'tool_calls' = result->>'tool_calls'` 可被外部 SQL 对拍。
   - `/{id}/stop`（owner 停）、claim 惰性收割（`agent_lost`）、orphan-queue 处理、
     `/{id}/write-off`：全部补账；write-off 顺手把推导值写进 `credit_ledger`。
   - `GET /tensor/missions`、`GET /tensor/missions/{id}` 投影新增 `budget_used` /
     `fail_stage`——持久化但 API 看不见等于没落账。
3. **帽曲线** `scripts/budget_cap_curve.py`：从 missions API 拉**全部终态单**
   （按契约词表分状态分页），按 `budget.max_tool_calls` 分档，输出
   `{cap, n, succ, fail, rate, near_cap_rate}` + `at_cap_rate` / `usage_known` /
   `tool_calls_p50_succ`；自带一致性自检（分档 n 之和 == 群体 n；群体 n == API
   各状态 total 之和，delta 必须 ±0），不过即退出码 2。
4. **取证** `docs/fp15/fp15_intel.py`：只读 SQL + 可回填性分级（可回填 / 不可回填），
   生产原文 SQL 一起落盘。

## ③ 计算结果（数字表，程序注入）

### 帽-成功率曲线（24h 窗口，`artifacts/fp15/budget_cap_curve.json`）

| cap | n | succ | fail | rate% | near_cap% | at_cap% | usage_known | p50 tool_calls（成功单） |
|---|---|---|---|---|---|---|---|---|
| 60 | 832 | 696 | 136 | 83.65 | **77.08** | **40.77** | 829 | **59.0** |
| 80 | 426 | 382 | 44 | 89.67 | 48.58 | 17.06 | 422 | 75.0 |
| 100 | 22 | 20 | 2 | 90.91 | 31.82 | 9.09 | 22 | 92.5 |
| 400 | 9 | 9 | 0 | 100.0 | 0.0 | 0.0 | 9 | 40.0 |

`near_cap = tool_calls >= cap - 5`（与任务书口径一致）；`rate = succ / n`，
`near_cap_rate` 的分母是**有记账的行**（`usage_known`），没数据时输出 `null` 而不是 0。

全量群体（all-time，同一脚本 `population` 节）：n=16013（与 API 各终态 total 之和
16013 相差 **0**）；其中 cap=400 档 n=5427、rate=37.83%（`usage_known` 仅 2485）、
cap=220 档 n=2551、rate=91.14%。**帽与任务类/模型强相关（cap 非随机分配）**，
跨档成功率差异不能读成因果。

### 校验（一致性自检原文）

```json
{"api_total": 16013, "population_n": 16013, "population_delta_vs_api": 0,
 "buckets_sum_equals_population": true, "window_buckets_sum_equals_window_total": true,
 "window_rows": 1289, "window_is_subset": true, "all_zero_delta": true}
```

## ④ 结果图

`artifacts/fp15/fig_cap_curve.png`：上误差棒图给出各档 `rate`，下柱状图给出
`near_cap_rate`/`at_cap_rate` 与成功单 p50 工作量（数据全部来自上面的 JSON，脚本注入）。
纯后端/后端指标任务，此图即"场量"结束面：cap=60 档的成功单 p50=59 ≈ 帽值本身，
是本报告最直观的结构性证据。

## ⑤ 注意点（坑与适用边界）

* **jsonb 合并不可用 sqlite 假装**：`result || CAST(:summary AS jsonb)` 在 sqlite 上
  退化成字符串拼接，`tool_calls == result.tool_calls` 的对拍会变成假绿。所以端到端
  用例走 **真 Postgres 临时库**（`TENSOR_TEST_PG_DSN`，指向 `tensorplatform` 直接
  skip），未设变量时如实 skip 而不是标 passed。
* **快照时刻不同 → 数字不同**：intel（05:20:27）与 cap_curve（稍后）窗口内新增了
  3 张单（1286 → 1289）。每个工件自带窗口时间戳，严禁跨工件拼数。
* **`fail_stage` 列 24h 内 100% 为 NULL**：裁决实际落在 `result.evidence.checks.fail_stage`
  与 `result.stop_condition`；取证脚本三处 COALESCE 后才读到分布。这是"列写了没人填"
  的同型缺口，本单不修（超出半径），登记在此。
* **历史回填未执行**：1279 单可从 `result.tool_calls` 回填、7 单连事实都没有。
  `budget_used` 属证据面列，回填不可逆，按纪律只出数、等 owner 决策。
* `tokens` 目前恒 `unavailable`：harness 未上报 token 用量，记账层不伪造 0 为"用过 0 token"。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答）

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 新生成终态单 `budget_used` 非空率 | 100% | 100.0%（12/12，id 见 `artifacts/fp15/new_terminal_budget_used_ids.json`） | 0.00 pp | **是（精确 0）** |
| `budget_used.tool_calls` vs `result.tool_calls` | 恒等 | 12/12 单逐单相等 | 0（整数精确相等） | **是（精确 0）** |
| 帽曲线总 n vs API 终态计数 | 相等 | 16013 vs 16013 | delta 0 | **是（精确 0）** |
| 分档 n 之和 vs 群体 n | 相等 | 相等（两种窗口均成立） | 0 | **是（精确 0）** |
| 历史 24h 窗口 `budget_used` 非空率 | 无基准（缺陷实测） | 0.0%（0/1286） | — | 不适用；缺陷实数 |

计数型判据为整数精确相等，无需置信区间；曲线类结论（cap 影响成功率）为观察性关联，
不做显著性/因果声明——真正因果需随机化 cap 的 A/B（建议见下）。

## 建议（cap 配置）

1. **cap=60 档是结构性瓶颈**：成功单 p50 工具调用 = 59（≈帽值），77.1% 的行落在帽值
   5 次以内，40.8% 打满帽；同窗口 cap=80 档成功率 89.67% 高于 60 档 83.65%。
   建议把实现类任务的 `max_tool_calls` 下限从 60 抬到 **80**，并在下一波用**随机化
   分档 A/B**（同任务类同模型，cap 60/80 随机）验证，消除"cap 与任务难度相关"的混淆。
2. **记账默认开启**：新代码对 `complete/stop/reap/orphan/write-off` 全路径落账，
   `tokens` 待 harness 上报后即自动由 `unavailable` 转为 `agent_declared`。
3. **历史回填待裁决**：1279 可回填 / 7 不可回填；建议按 `source=backfill:result_fact`
   标注后由 owner 批（本单未动库）。