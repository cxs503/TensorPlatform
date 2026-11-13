# FP15-BUDGET-ACCOUNTING v2：终态预算记账从「调用点纪律」升级为「数据库不变量」

- 锚点：M-R15-01（381 终态单 `budget_used` 全空，43 单「预算耗尽」裁决外部不可复核）
- 仓：TensorPlatform ｜ 分支：`agent/m18014` ｜ 日期：2026-11-13
- 前置：v1（m17391 / Merge #1310）已交付**推导**（`backend/services/budget_accounting.py`）
  与**逐点落库**（`/complete`、`/stop`、lazy-reap、reclaim），以及帽-成功率曲线脚本。

## ① 案例介绍（问题事实，全库口径，零手抄）

v1 的落库是「每条终态代码路径各自记得写 `budget_used`」。修复前的实测基线
（`artifacts/fp15/budget_used_baseline_before.json`，2026-11-13T11:19Z）：

| 终态词 | 行数 | 其中 `budget_used` 为空 |
|---|---:|---:|
| succeeded | 11604 | 11604 |
| failed | 4221 | 4221 |
| revoked | 588 | 588 |
| stopped | 170 | 170 |
| **合计** | **16583** | **16571（99.93%）** |

根因有两条，v1 一条都没治：

1. **旁路写点**：`tensor_repro` 作弊/驳回撤销（2 处）、`mission_gradient` honest-fail、
   `self_heal` 退役、`demand_intake` 撤单——都不走 `/complete`，全部绕过记账。
   机器扫描（`tools/audit_terminal_budget_writes.py`，本单新增）在仓内找到 **11 个**
   `UPDATE tensor_dev_missions SET status='<终态词>'` 写点，其中**只有 4 处自带记账**。
2. **部署窗口**：v1 合并进 main 后，在跑的服务（:8010）仍是上一版检出，
   所以合并 ≠ 落库。任何「代码侧记账」方案都有一个宽度未知的空窗期。

## ② 怎么做的（方法 / 关键设置 / 工具链）

### 机制主体：终态触发器（`backend/migrations/125_budget_used_terminal_trigger.sql`）

`BEFORE INSERT OR UPDATE ON tensor_dev_missions` 的 plpgsql 触发器
`tp_stamp_budget_used()`：

- 命中条件：行进入终态（vocab 契约 `terminal` ∪ `budget_exhausted`/`no_progress`）
  **且** `budget_used` 为空（NULL 或 `{}`）；
- 推导优先级与 Python 单一真源一致：`result` 事实 > 心跳 `progress` > runner `detail`
  正则 > 服务端时钟上界（`finished_at - created_at`）；
- 输出四键 `{tool_calls, tokens, wall_seconds, source}` + `sources` 明细 + 历史键
  `tool_calls_used/tokens_used/wall_seconds_used` + `recorded_by='terminal_trigger'`；
- **只补空、不覆盖**应用声明的真值；任何异常 `RETURN NEW`（fail-open，记账绝不许
  成为终态流转的单点故障）。

这样「记账」跨过了语言边界与部署窗口：任何写入者（API、CLI 工具、旁路脚本、
还没升级的服务版本）只要把行打进终态，账就在库里。

### 配套三件套

| 工件 | 作用 |
|---|---|
| `tools/audit_terminal_budget_writes.py` | 写点审计/漂移守卫：逐点判定「自带记账 / 触发器兜底 / 裸写」；输出 `artifacts/fp15/budget_write_audit.json` |
| `tools/budget_accounting_coverage.py` | 覆盖率闸：`--since` 之后终态行的四键非空率；`n<5` 退出码 2（未跑≠绿），`rate<90%` 退出码 1 |
| `tools/backfill_budget_used.py` | 历史补录（幂等/可回滚）：只补空行，墙钟用 `finished_at-created_at`，`{id,{old,new}}` 落 ledger |

代码侧同时补齐了仓内两处真缺口：`backend/api/tensor_repro.py` 撤销路径抽出
`_revoke_mission()`（先记账再翻状态），`backend/api/mission_gradient.py` honest-fail
先 `record_budget_used()`。剩余 5 个写点在 `tools/`（self_heal 退役、autonomy 验收等），
由 DB 触发器兜底。

## ③ 计算结果（程序化注入，无手抄）

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| 终态行总数 | 16583 | 16585 |
| `budget_used` 为空的行 | 16571（99.93%） | **0（0.00%）** |
| 机制生效后**新生**终态行 | — | **9 单，9 单非空 = 100%**（全部 `recorded_by=terminal_trigger`） |
| 补录写入 | — | **16570 行**（`artifacts/fp15/budget_backfill_report.json`） |
| 写点自带记账 | 4 / 11 | 6 / 11（其余由触发器兜底，裸写 0） |
| pytest | 27 项（纯推导） | **58 项全绿**（含 17 项真 PG 触发器用例） |

补录出处分布（`recorded_by`）：`backfill_20261113` 16570 行、`terminal_trigger`
新增行实时累加。**每个数字都可用 `artifacts/fp15/*.json` 里的 SQL 溯源**。

## ④ 图

本单为后端机制 + 数据修复，无场量/几何可比量，故不出物理云图；数据面的对照
（修复前 99.93% 空 → 修复后 0%）以上表与两份 JSON 工件呈现。帽-成功率曲线仍由
`scripts/budget_cap_curve.py` 产出（`artifacts/fp15/budget_cap_curve.json`），
其 `near_cap_rate` 分母（`usage_known`）在记账补齐后首次具备统计意义。

## ⑤ 注意点（踩过的坑 / 局限）

1. **SQL_ASCII 集群**：容器集群 `server_encoding=SQL_ASCII`，带中文注释的迁移文件
   必须显式 `client_encoding=UTF8`，否则 psycopg 报 `UnicodeEncodeError`（环境坑，
   不是迁移坑）。测试与工具均按此处理。
2. **扫描器首版 12 条假阳性**：把 `WHERE status='succeeded'` 当成写点。现版只解析
   `UPDATE <mission 表> ... SET ...` 的 SET 子句，并有用例钉死。
3. **补录墙钟口径**：不能用 `now() - created_at`（补录跑在数周后会把耗时虚增成月），
   必须 `finished_at - created_at`；已记账行绝不覆盖。
4. **局限**：触发器只覆盖 `public.tensor_dev_missions`；`sbx_infra_replay*` 等沙箱
   克隆表不挂触发器（沙箱数据不参与账本）。`tool_calls` 在历史行里大量缺失，
   记 0 且 `source=tool_calls=unavailable`——**这不是「花了 0 次」，是「没有证据」**，
   报告与曲线都已按此口径标注。
5. 沙箱克隆（`CREATE TABLE ... LIKE ... INCLUDING ALL`）不会复制触发器，故新建
   沙箱不需要额外清理。

## ⑥ 误差判定

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 终态行 `budget_used` 非空数 | 全量终态行数（16585） | 16585 | 0 | 是（0%） |
| 补录行数 | 修复前空行数（16570） | 16570 写入 | 0 | 是（0%） |
| 触发器推导 `tool_calls` | 同行 `result->>'tool_calls'` | 逐例相等（17 项 PG 用例） | 0 | 是（0%） |
| 触发器 `wall_seconds`（无时间事实时） | `finished_at - created_at` = 600s 夹具 | 实测 590–700s 区间断言 | 由秒级取整引入 ≤1% | 是 |

无「物理量 vs 实验值」类基准（本任务为平台账本机制），故不适用海洋工程 15% 精度线；
上表为机制口径的精确性判定，三项均为 0 误差项。