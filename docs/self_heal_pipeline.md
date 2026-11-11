# 任务级自愈流水线（停滞检测 → 根因分类 → 有界恢复 → 审计链）

p393w2-n04 · 自主续航 L3 度量 M3 的 `self_heal` 缺口补齐 · 交付于 `tools/self_heal/`、
`backend/api/tensor_self_heal.py`、`backend/migrations/115_self_heal_ledger.sql`。

## 1. 为什么需要它

平台已有的自愈都是**战役/节点级**的（`services/selfheal` 管 DAG 节点重试、
`ops/taskplane/stall_detector.py` 管平台断粮/心跳/脉搏）。**任务级**是空白：
执行器进程死掉后任务永远停在 `running`，调度器不给槽位后任务永远停在
`pending`，而没有机制为它响一声——因为「发现者」和「死去的东西」常常是同一个进程。

本模块把「今晚怎么发现的」变成周期体检 + 有界恢复 + 可审计账本：

```
tensor_dev_missions ──detect──> 停滞任务 ──classify──> 六类根因
                                                   │
                        ┌──────────────────────────┴───────────────────────┐
                        │  infra_lost → reschedule (≤3)                    │
                        │  timeout → extend_timeout ×1.5 (≤2)              │
                        │  criteria_infeasible → escalate(proposer)        │
                        │  duplicate_seed → archive(冗余副本)               │
                        │  resource_contention → degrade_resources (≤2)     │
                        │  unknown → manual_review                         │
                        └──────────────────────────┬───────────────────────┘
                                                   ▼
                                     self_heal_ledger（hash 链 + 幂等键）
```

## 2. 信号（全部来自已持久化字段，无魔法）

| 根因 | 判定证据 | 字段 |
|---|---|---|
| `infra_lost` | 属主 session 已关闭 / 超过 `session_stale_s` 未见 / session 不存在 / 活跃但无属主；或错误串含执行器丢失关键词 | `claimed_by`、`agent_session_id`、`tensor_agent_sessions.status/closed_at`、`result` |
| `timeout` | wall 预算耗尽（`used/limit ≥ 0.98`）或截止/超时关键词 | `budget.max_wall_seconds`、`budget_used.wall_seconds`、`result`、`fail_stage` |
| `criteria_infeasible` | 判据不可达关键词，或验收拒绝 ≥ 3 次 | `result.acceptance.failures`、`result` |
| `duplicate_seed` | 同一 `task_key` 上存在更早的活跃任务（较早者为 owner） | `task_key` + 活跃状态集合 |
| `resource_contention` | 分配失败/容量关键词、调度等待轮次 ≥ 3，或排队超时且队列饱和 | `result.scheduler`、队列快照 |
| `unknown` | 以上皆无正证据 | —— |

决策顺序固定（先命中先返回）：`duplicate → infra → criteria → timeout → resource → unknown`。
顺序本身是被测对象：`tests/test_self_heal.py::test_decoy_infrastructure_row_is_not_misread_as_timeout`
与报告里的 `ambiguity_probe` 就是重叠信号的透明化。

停摆判定两条独立信号：活跃状态心跳年龄 ≥ `stall_after_s`（默认 1800 s；无心跳时回退
`created_at`，即「一次心跳都没发」也算），排队任务等待 ≥ `queue_wait_s`（默认 3600 s）。
无时间戳的行**显式**进 `unobservable` 列表——「看不了」不能算「没问题」。

## 3. 有界恢复与幂等

* 每次动作带尝试号；尝试号只由**更早窗口**的账本条目推导（`window_id` 字典序），
  因此同一窗口重跑得到同一尝试号 → 同一 `idempotency_key` → 动作被跳过。
* `idempotency_key = sha256(window_id, mission_id, action, attempt)`，DB 侧唯一约束兜底。
* 达到上限（重调度 3 / 延时 2 / 降配 2）后升级为 `escalate(manual_review)`，
  账本记录 `recovery_cap_exhausted`，不再重复一个已被证明无效的动作。
* 账本 `hash = sha256(canonical(除 hash 外全部字段，含 prev_hash))`；删行/改序/改字段
  都会被 `verify()` 抓出；API 在链断时返回 409 拒绝追加（fail closed）。
* 冗余副本的「归档」落成 `status='stopped'` + `result.archived=true`：
  `contracts/mission_status_vocab.v1.json` 没有 `archived` 状态，不新造词汇。

## 4. 机器可算验收（`artifacts/self_heal_report.json`）

合成语料：50 条注入停滞任务（六类各 ≥8）+ 35 条对照（30 健康 running + 3 健康排队 +
2 长期 suspended）。数字由 `python -m tools.self_heal.simulate` 程序化生成，
不手抄：

| 判据 | 实测 | 阈值 | 通过 |
|---|---|---|---|
| (a) 检测召回 | 1.00（50/50） | ≥0.95 | ✅ |
| (a) 误报率 | 0.00（0/35） | ≤5% | ✅ |
| (b) 六类 macro-F1 | 0.981（timeout 精确率 0.889、unknown 召回 0.900；1 例错分落盘） | ≥0.80 | ✅ |
| (c) 可恢复类恢复成功率 | 0.80（20/25 动作被受理） | ≥0.70 | ✅ |
| (d) 同窗口重跑 ledger sha256 | 10/10 一致；独立 10 次亦一致 | 10/10 | ✅ |
| (e) 域词黑名单 | 0 命中（14 文件，含报告/图/文档） | 0 | ✅ |
| (f) 30 条健康 running 误检 | 0 | 0 | ✅ |
| (g) 可证伪 | 阈值调到 1e9 → 检测 0 → `honest_fail=true`（用例覆盖） | —— | ✅ |
| 账本 hash 链 | `verify().ok = true` | true | ✅ |

复现：`python -m tools.self_heal.simulate --out artifacts/self_heal_report.json --figure artifacts/self_heal_confusion.png`

## 5. 注意点 / 局限（不许含糊）

1. **合成语料的循环性**：标签与分类器读的是同一套信号词汇，因此高 F1 证明的是
   「决策逻辑自洽且满足契约」，**不是**现场数据分布下的准确率。报告内附完整混淆矩阵、
   每条错分样例的特征快照、以及 5 条重叠信号的 `ambiguity_probe`，供独立评审攻击。
   语料里刻意留了一条「日志里出现过已恢复的 timeout 字样」的未知根因任务，它被关键词规则
   误判成 `timeout`（见 `classification.misclassified`）——这就是该规则已知的过度触发弱点，
   没有被藏起来。
2. **恢复成功率的定义**：「执行器受理动作」（applied/archived）而非「任务最终完成」。
   `deferred`（无调度容量）与 `escalate`（交人）都不计成功。
3. **MTTR 是代理量**：阈值越界时刻（onset 估计）→ 动作受理时刻，不是任务完成时间。
4. **DB 适配器只在 SQLite 上跑通**（API 测试）。PostgreSQL 分支使用
   `CAST(:x AS jsonb)` 与 `ON CONFLICT DO NOTHING`，迁移 `115` 未在本机 PG 实例执行过，
   属未验证项。
5. **活跃 session 没有存活时间戳**：`tensor_agent_sessions` 只有 `status/closed_at`，
   所以 DB 路径的 `infra_lost` 证据是「session 关闭/不存在」，不做「session 静默太久」推断；
   后者需要调用方注入 `session_last_seen`（API 支持，但生产暂无人喂）。
6. **真实调度未接线**：`reschedule` 只把行改回 `queued` 并清 claim/heartbeat，
   真正的派发由既有 dispatcher 在下一次扫描时完成；本交付不复制调度逻辑。
7. **单写者假设**：账本追加没有跨进程锁，靠 DB 唯一约束与 seq 连续性兜底；
   多实例并发运行需要先取平台 advisory lock（未实现，明确列为后续项）。

## 6. 零领域语义

平台仓铁律：域词条/域判据/域算例不得硬编码。本模块的所有输入都是
「状态/时间戳/JSON 结果/队列快照/任务键」，六类根因与动作是**机制词汇**。
`tools/benchmark_runner/domain_guard`（复用平台唯一词表 `tools/evidence_domain_lexicon.json`）
对本交付全部文件扫描 0 命中，结果落在报告 `domain_scan` 段。

## 7. 接口

```
POST /api/v1/tensor/self-heal/run        # 跑一个窗口（默认 dry_run；可注入 missions 语料）
GET  /api/v1/tensor/self-heal/status     # 链头 + 校验 + 最近条目
GET  /api/v1/tensor/self-heal/ledger     # 审计流水（可按 window_id 过滤）
GET  /api/v1/tensor/self-heal/dashboard  # 成功率 / MTTR / 根因分布 / 未闭环升级
GET  /api/v1/tensor/self-heal/config     # 阈值与动作矩阵（只读）
```

CLI 等价物：`python -m tools.self_heal.simulate`（合成语料验收 + 报告 + 图）。