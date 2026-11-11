# 任务级自愈流水线：停滞检测 → 六类根因 → 有界恢复 → 哈希链台账（p393w2-n04）

## ① 案例介绍

**背景**：自主续航 L3 的 M3 度量里 `self_heal` 项缺失。平台已有的自愈都在战役/节点级
（DAG 节点重试）与平台级（断粮/心跳/脉搏 daemon），而在**任务级**存在结构性盲区：
执行器进程死亡后任务永远停在 `running`，调度器不给槽位后任务永远停在 `pending`，
而「发现者」往往与被监控对象同生共死。本节点补齐「停滞任务检测 → 根因分类 →
有界恢复/升级/归档 → 落账审计 → 仪表盘」全链。

**对象与参数**（机制类任务，无物理几何；等价参数表如下）：

| 参数 | 取值 | 含义 |
|---|---|---|
| `stall_after_s` | 1800 s | 活跃任务心跳老化阈值（无心跳回退 created_at） |
| `queue_wait_s` | 3600 s | 排队等待阈值 |
| `session_stale_s` | 900 s | 属主 session 失联阈值（仅当调用方注入 liveness 时生效） |
| 重调度上限 | 3 次 | infra_lost 恢复预算 |
| 超时上浮 | ×1.5，上限 2 次 | timeout 恢复预算 |
| 降配上限 | 2 次 | resource_contention 恢复预算 |
| 合成语料 | 50 条停滞（六类各 ≥8）+ 35 条对照 | 验收输入 |

**证据分级**：检测/分类/恢复策略/台账 = REAL（真实代码、真实阈值、真实哈希链）；
恢复动作执行器两条：`DryRunExecutor`（验收用，零副作用，REAL 逻辑）与
`SqlMissionExecutor`（真实 UPDATE 任务行，仅在 SQLite 上被测试覆盖，PostgreSQL 分支属
WITHHELD——未在本机 PG 实例执行）；「任务最终完成率」= WITHHELD（本交付只度量动作受理率）。

## ② 怎么做的

1. **检测器**（`tools/self_heal/detector.py`）：活跃状态心跳年龄 ≥ 阈值，或排队等待 ≥ 阈值；
   终态/挂起行永不触碰；无时间戳的行显式进 `unobservable`（「看不了」≠「没问题」）。
2. **分类器**（`classifier.py`）：固定优先级 `duplicate_seed → infra_lost →
   criteria_infeasible → timeout → resource_contention → unknown`，每步都要正证据
   （session 关闭/失联、wall 预算耗尽、验收拒绝次数、task_key 重叠、分配失败关键词/排队饱和）；
   无证据即 `unknown`，不做猜测。
3. **恢复策略**（`recovery.py`）：每个根因一个动作 + 上限；尝试号只由**更早窗口**的账本条目
   推导 → 同窗口重跑得到同一动作键 → 幂等。
4. **执行器**（`executor.py`）：dry-run 与 SQL 两种；归档落成 `status='stopped'` +
   `result.archived=true`（`mission_status_vocab` 无 `archived` 状态，不新造词汇）。
5. **审计链**（`ledger.py`）：一行一次动作，`hash = sha256(canonical(除 hash 外全部字段, 含
   prev_hash))`；`idempotency_key = sha256(window_id, mission_id, action, attempt)` 唯一约束兜底；
   链断时 API 返回 409（fail closed）。
6. **API**（`backend/api/tensor_self_heal.py`）：`/run /status /ledger /dashboard /config`；
   迁移 `115_self_heal_ledger.sql` 建表 + append-only 触发器 + 三索引。
7. **验收语料与裁决**（`simulate.py`）：固定 `now` 的 50 停滞 + 35 对照，确定性复现；
   `python -m tools.self_heal.simulate` 生成报告 JSON 与图。

## ③ 计算结果（全部由 `artifacts/self_heal_report.json` 注入）

**检测（50 停滞 + 35 对照）**

| 指标 | 数值 |
|---|---|
| 召回 | 1.0000（50/50） |
| 误报率 | 0.0000（0/35） |
| 检测信号分布 | {"heartbeat_stale": 42, "queue_wait_timeout": 8} |
| 不可观测行 | 0 |

**六类根因（混淆矩阵见 artifacts/self_heal_report.json）**

| 根因 | support | TP | precision | recall | F1 |
|---|---|---|---|---|---|
| `infra_lost` | 8 | 8 | 1.000 | 1.000 | 1.000 |
| `timeout` | 8 | 8 | 0.889 | 1.000 | 0.941 |
| `criteria_infeasible` | 8 | 8 | 1.000 | 1.000 | 1.000 |
| `duplicate_seed` | 8 | 8 | 1.000 | 1.000 | 1.000 |
| `resource_contention` | 8 | 8 | 1.000 | 1.000 | 1.000 |
| `unknown` | 10 | 9 | 1.000 | 0.900 | 0.947 |
| **macro** | 50 | — | — | — | **0.9814** |

错分样例（诚实披露，关键词过触发）：

| mission | 注入标签 | 预测 | 置信度 | 命中关键词 |
|---|---|---|---|---|
| 1050 | `unknown` | `timeout` | 0.95 | {"criteria_infeasible": [], "infra_lost": [], "resource_contention": [], "timeout": ["timeout"]} |

**恢复（可恢复类 = infra_lost / timeout / resource_contention）**

| 指标 | 数值 |
|---|---|
| 动作受理率 | 0.8000（20/25） |
| 分根因 | {"infra_lost": {"accepted": 6, "attempted": 8}, "resource_contention": {"accepted": 7, "attempted": 8}, "timeout": {"accepted": 7, "attempted": 9}} |
| MTTR 代理（阈值越界→动作受理） | 3325.71 s |
| 定义 | accepted = executor acknowledged the action (applied/archived); this measures action acceptance, NOT eventual mission completion |

**动作分布**

| 根因 | 策略 | 本窗口动作数 |
|---|---|---|
| `infra_lost` | reschedule ≤3 → escalate | 8 |
| `timeout` | extend_timeout ×1.5 ≤2 → escalate | 9 |
| `criteria_infeasible` | escalate(proposer) | 8 |
| `duplicate_seed` | archive(redundant) | 8 |
| `resource_contention` | degrade_resources ≤2 → escalate | 8 |
| `unknown` | manual_review | 9 |

**幂等与完整性**

| 指标 | 数值 |
|---|---|
| 同窗口重跑 10 次 digest 一致 | True；skipped = [50, 50, 50, 50, 50, 50, 50, 50, 50, 50] |
| 独立 10 次 digest 一致 | True |
| ledger 行数 / digest | 50 / `7796d2821d44d4e236173ade315d1361dda6c2a25c4dc6d6bca822bcb96caa56` |
| 哈希链校验 | {"entries": 50, "head": "976bdf75ca4d6019449a82c5b65875eb360ff8d1d101bb8df58dcaadc4ad43fa", "ok": true} |
| 域词扫描 | clean，14 文件，0 命中 |

**判据逐项**

| 判据 | 结果 |
|---|---|
| a_detection_recall_ge_0.95 | ✅ true |
| a_false_positive_rate_le_0.05 | ✅ true |
| b_macro_f1_ge_0.80 | ✅ true |
| c_recovery_success_ge_0.70 | ✅ true |
| d_idempotent_10_of_10 | ✅ true |
| d_independent_runs_identical | ✅ true |
| e_zero_domain_semantics | ✅ true |
| f_negative_control_zero_fp | ✅ true |
| g_falsifiable | ✅ true |
| ledger_chain_valid | ✅ true |

**重叠信号优先级探针（不属于 F1 统计，透明化决策顺序）**

| case | 构造 | 期望 → 实际 | 一致 |
|---|---|---|---|
| infra_beats_timeout_decoy | row has both a closed owner session and the string 'timeout' | infra_lost → infra_lost | ✅ |
| duplicate_beats_infra | two active missions share a task_key and the younger owner session is closed | unknown, duplicate_seed → unknown, duplicate_seed | ✅ |
| criteria_beats_timeout | row has repeated acceptance rejections AND wall_exhausted in the error text | criteria_infeasible → criteria_infeasible | ✅ |
| queue_wait_under_saturation | queued row without an allocation keyword but the queue is full | resource_contention → resource_contention | ✅ |
| no_signal_is_unknown | stalled row with a live session and no error signal | unknown → unknown | ✅ |

honest-FAIL 标志：`False`（阈值调到 1e9 的退化用例会被置真，见
`tests/test_self_heal.py::test_honest_fail_flag_is_reachable`）。

## ④ 图

图 1（`artifacts/self_heal_confusion.png`）左：六类根因混淆矩阵（n=50，对角为主，
唯一非对角格是 unknown→timeout 的关键词误触发）；右：三类可恢复动作的尝试/受理柱状图，
标题内联检测召回、macro-F1 与受理率。

## ⑤ 注意点（踩过的坑 / 局限）

1. **合成语料的循环性**：标签与分类器共用信号词汇，高 F1 证明「决策逻辑自洽且满足契约」，
   不等于现场准确率。故报告附混淆矩阵、错分特征快照、5 条重叠信号探针，供独立评审攻击。
2. **关键词过触发是已知弱点**：语料里一条 unknown 任务只因日志里出现「已恢复的 timeout」
   就被判成 timeout（见上表），这条错分**没有被藏掉**。
3. **恢复成功率的语义**：动作受理（applied/archived），不是任务最终完成；`deferred`
   （无调度容量）与 `escalate`（交人）都不计成功。
4. **session 存活证据**：`tensor_agent_sessions` 只有 `status/closed_at`，没有心跳列；
   DB 路径的 infra_lost 只认「session 关闭」或关键词，绝不把「free-form claim 不在 session
   表里」当失联（否则会把健康任务全判成 infra_lost——这是本次实现中修掉的一个真实缺陷）。
5. **PostgreSQL 分支未在真库跑过**（WITHHELD）：`CAST(:x AS jsonb)`、`ON CONFLICT`、
   append-only 触发器均按 PG 语义写，但只做过 SQLite 等价测试。
6. **单写者假设**：多实例并发追加需平台 advisory lock（未实现，后续项）。
7. **真实派发未接线**：reschedule 只把行改回 queued 并清 claim/heartbeat，派发交给既有
   dispatcher；本节点不复制调度逻辑。
8. **第 2 次尝试说明**：本工作树内没有上一次尝试的产物（`tools/self_heal`、报告 JSON 均
   不存在），因此按判据从零重建；上一版失败原因无法从工作树取证，未做「原样重跑」。

## ⑥ 误差判定（显式回答是否 <3%）

| 量 | 相对基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 检测召回 | 契约阈值 0.95 | 0.00 个百分点 | 是（0.00） |
| 误报率 | 契约上限 5% | 0.00 个百分点 | 是（0.00） |
| 六类 macro-F1 | 上界 1.0 | 1.86 个百分点 | 是（1.86） |
| 恢复受理率 | 契约阈值 0.70 | 高出 10.00 个百分点 | 不是精度指标（越高越好） |

**为什么没有物理/解析基准**：本节点交付的是平台机制（检测/分类/恢复/审计），输入是任务行
与调度快照，不存在可对标的实验或解析解；因此「误差」按契约阈值与上界度量，并在此显式声明，
不以「无基准」含糊带过。物理求解器精度判定不适用于本节点。

## 复现

```
python -m tools.self_heal.simulate --out artifacts/self_heal_report.json     --figure artifacts/self_heal_confusion.png --runs 10
python -m pytest tests/test_self_heal.py -q     # 21 passed
```
