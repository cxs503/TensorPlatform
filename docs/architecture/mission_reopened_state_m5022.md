# REOPENED 状态正式化（SPEC §4.2 / 战役 IN19-Q7 / m5022）

状态：**已实现，机器验收通过**（契约审计 22 项 diff=0；新面 pytest 24 项全绿）
适用仓库：TensorPlatform（平台底座；本改动零领域语义，词表/夹具均为治理级合成名）

## 1. 问题（为什么要一个状态，而不是一个旗标）

平台的生命周期终态是 `succeeded / failed / stopped / revoked / orphan`。
终态一旦落定，**没有正式回头路**。真实世界里回头是常事：

* 合并后回归红了（PR 已进 main，测试/证据在合并后才红）；
* 验收所依据的证据失效（参考数据被撤回、算例复跑不一致）；
* 审查结论被推翻（复审判定原验收不成立）。

历史上的做法是「库外复活」：`ops/taskplane/tp_pusher.py` 用私有旗标
`main_red_hold` 把「PR CI 红但 main 亦红」的单挂在 pusher 自己的状态文件里，
mission 行仍是终态 `succeeded`；其它守护/人工也有直接把状态改回可执行态的做法。
后果有四条，全部是治理层面的债：

1. **词表看不见**：没有哪个状态词表示「这张单回来了」；
2. **主体不可查**：谁重开的、是不是产出者本人给自己重开（规避验收，SPEC §10）；
3. **证据不可查**：推翻了哪份验收记录、理由是什么，全无记录；
4. **静默失败**：私有旗标不落库，重启/换机器即丢，且没有任何审计行。

SPEC §4.2 的要求正好是这三件事：`REOPENED` 由**独立治理侧（非生产者本人）**
触发、**必须引用原验收记录**并**说明理由**；随后由主体与理由记录把「正当重开」
和「频繁关闭重开以规避验收」区分开。

## 2. 做法（单一契约 → 两侧派生 → 机器对拍）

| 层 | 文件 | 作用 |
|---|---|---|
| 词表契约 | `backend/contracts/mission_status_vocab.v1.json` v2→**v3** | `statuses` 增 `reopened`（非终态），`platform_only` 增 `reopened`；终态集不变 |
| 映射契约 | `backend/contracts/mission_task_status_map.v1.json` v2→**v3** | `reopened` 记入 `platform_only`（TensorPhysics 侧无任务态像，故意不映射） |
| 迁移图契约 | `backend/contracts/mission_state_machine.v1.json` **v1（新）** | 12 个状态的迁移图 + `workable` 干活态集 + `reopen` 规则块（入口集/目标集/治理角色/引用正则/理由最短长度） |
| 装载器 | `backend/api/domain/status_contracts.py` | 增 `mission_state_machine_contract()`（依赖自由） |
| 后端实现 | `backend/api/domain/mission_state_machine.py` **（新）** | 从契约派生 `STATUSES/TERMINAL/TRANSITIONS/REOPEN_*`；`plan_reopen()` 纯函数判据、`assert_transition()` 迁移守卫、`plan_release()` |
| 端点 | `backend/api/tensor_mission_lifecycle.py` | `POST /tensor/missions/{id}/reopen`、`POST /tensor/missions/{id}/reopen-release` |
| 迁移 | `backend/migrations/103_mission_reopened.sql` | 状态 CHECK 增补；`reopened_from/reopen_ref/reopen_reason/reopened_at` 四列；**DB 级证据约束**；`mission_lifecycle_log.action` 棘轮增 `reopen` |
| ops 侧实现 | `ops/taskplane/mission_reopen.py` **（新）** | 同一规则块的瘦派生读者；`apply_reopen()` 单语句原子落库（状态守卫 + 审计行） |
| 守护切换 | `ops/taskplane/tp_pusher.py` | 继承红分支改写正式 `reopened`；私有旗标清零 |
| 存量迁移 | `ops/taskplane/migrate_reopen_state.py` **（新）** | 旧旗标存量 → 正式重开（默认 dry-run） |
| 普查器 | `tools/audit/reopen_census.py` **（新）** | 历史复活四口径普查，输出可复算 JSON |
| 审计器 | `tools/contracts/vocab_contract_audit.py` | 增 G-A 三项：`state_machine_closure`、`reopen_back_edge_only`、`terminal_no_workable_edge` |

### 2.1 迁移图（契约派生，节选关键不变量）

```
queued → claimed → running → awaiting_acceptance → succeeded
                        ↘ awaiting_pr_merge ↗ ／ ↘ failed
succeeded|failed|stopped|revoked|orphan  --(治理重开：引用验收记录+理由)-->  reopened
reopened --(治理放行 release)--> queued → …（返工）
```

机器判据（`backend/tests/test_mission_reopened_state.py::TestContract`）：

* 图节点集 == 词表状态集；每条边的两端都在词表内（闭包）；
* `reopen.from` == 终态集；指向 `reopened` 的来源 == 终态集（**非终态不许进重开**）；
* **终态没有任何直通「干活态」（`queued/claimed/running`）的边**——这就是
  「不许库外守护悄悄复活」的结构性证明；`succeeded→awaiting_pr_merge/failed`
  是 PR 记账边（迁移 100/101），不是复活，故不在禁列；
* `reopened` 是 parked 态（不自动被 claim 捡走），返工必须治理侧显式 release。

### 2.2 重开判据（`plan_reopen`，后端与 ops 逐格同判）

| 序 | 判据 | 拒绝码 |
|---|---|---|
| 1 | `from_status` 在词表内 | `unknown_status` |
| 2 | `from_status ∈ reopen.from`（终态） | `not_reopenable_from` |
| 3 | `actor_role ∈ {ADMIN, MANAGER, GOVERNANCE}` | `not_governance_actor` |
| 4 | `actor_identity != producer_identity`（或未记录生产者） | `producer_cannot_reopen` |
| 5 | `acceptance_ref` 非空 | `acceptance_ref_missing` |
| 6 | 匹配 `^(acceptance\|review\|pr\|evidence):…` | `acceptance_ref_malformed` |
| 7 | `len(reason) >= 8` | `reason_too_short` |
| 8 | `target ∈ reopen.targets` 且边存在 | `target_not_allowed` |

### 2.3 审计链（三份证据，缺一不可）

1. **行内证据**：`tensor_dev_missions.reopened_from / reopen_ref / reopen_reason /
   reopened_at`；迁移 103 的 `tensor_dev_missions_reopen_evidence_check` 让
   「`status='reopened'` 却没有 from/ref/reason」在 DB 层就写不进去——不靠代码自觉。
2. **append-only 审计行**：`mission_lifecycle_log(action='reopen', detail={from,to,
   acceptance_ref, reason, actor, actor_role, at})`；action CHECK 走棘轮（101 pr_gate、
   102 custody_requeue 的历史词全保留，只增不缩），测试
   `TestMigration103::test_lifecycle_action_ratchet_keeps_history` 锁死。
3. **会话事件流**：`append_event(kind='decision', subtype='mission_reopened' |
   'mission_reopen_released')`，复用既有事件词（G-B 单源不动，无跨仓契约churn）。

### 2.4 守护切换（`main_red_hold` → 正式状态）

`tp_pusher` 的继承红分支现在：

```python
rec["ci"] = mission_reopen.REOPENED                     # 正式词（契约派生）
rec["reopen"] = _reopen_inherited_red(mid, full, run_url) # 引用验收记录 + 理由 + 审计行
```

* `acceptance_ref`：优先 `acceptance:<tensor_approval_requests.id>`（原验收记录本体），
  取不到才退到 `pr:<owner/repo>:inherited-red`；
* 落库失败不打断推送主循环（异常收敛为 `reopen.code=error:*`，记录保留，行为与旧挂起等价）；
* **切换无漏的机器判据**：`test_guard_switch_leaves_no_legacy_sentinel` 用 `tokenize`
  剥掉注释与字符串后扫描 `ops/ backend/api/ tools/ scripts/` 的生产 `.py`——
  旧旗标作为**代码符号**出现次数为 0（历史说明允许在注释/文档里提及）；
* **存量不漏**：`migrate_reopen_state.py --dry-run` 实测 **117** 条旧旗标待办
  （`artifacts/m5022/reopen_state_migration.json`），全部可经正式通道重开
  （dry-run 已逐单解析出 `acceptance_ref`）。

## 3. 结果

| 判据 | 目标 | 实测 | 结论 |
|---|---|---|---|
| 词表契约更新 | `statuses` 含 `reopened`，版本 bump | v3，12 态（+1），终态 5 态不变 | ✅ |
| 迁移图契约 | 图闭在词表上、唯一复活回边 | 46 条边，闭包通过，回边仅 `terminal→reopened` | ✅ |
| DB 迁移 | 终态 CHECK == 契约；证据约束；action 棘轮 | `103_mission_reopened.sql` 三项齐备 | ✅ |
| 状态机测试 | pytest ≥ 6 | **24 passed / 0 failed**（`pytest_junit.xml`） | ✅ |
| 契约审计 | 22 项 diff=0 | `verdict=PASS diff_total=0` | ✅ |
| 守护切换无漏 | 生产面旧旗标 0 处 | 0 处；存量待办 117 条可迁移 | ✅ |
| 双实现不漂移 | 全矩阵同判 | 12 状态 × 5 角色 × 3 引用 × 4 理由 × 2 身份 = **1560 格同判** | ✅ |
| 零领域语义 | 新面域词 0 | 0（夹具用 `gov-acme` / `worker-bravo` 合成名） | ✅ |
| 历史路径记账 | 既有 refill 一步复活显式豁免 | `audited_retry_edges` 3 条，未声明后门 0 | ✅ |
| 历史复活口径 | 说明文档 + 可复算 | 四口径普查 + 可复跑 SQL（见同目录 census 文档） | ✅（口径数字见下） |

图：`artifacts/m5022/fig1_state_machine.png`（契约派生状态机，红=唯一复活回边）、
`artifacts/m5022/fig2_reopen_census.png`（四口径普查）。

## 4. 注意点 / 局限

1. **迁移未在本工作树执行**。`103_mission_reopened.sql` 只入仓，不 DDL 打活库；
   活库在部署释放时才迁移。因此在迁移前，ops 侧 `apply_reopen` 会因缺列/缺词
   返回 `error:*` 并退化为「保持挂起」（与旧行为等价，不会误改状态）。
2. **活守护未被热替换**。线上 `tp_pusher.py` 跑在 `/root/tp-agent/`（工作树之外），
   本单只改工作树内的 `ops/taskplane/`；热替换属发布动作，不在本单范围。
3. **TensorPhysics 侧镜像未同步**。两侧契约是字节镜像对拍；本仓已 bump 到
   vocab v3 / map v3，TensorPhysics 的 `src/tensorphysics/contracts/` 副本需随行
   同步，否则其契约测试会红一边（设计如此，不允许单边漂移）。
4. **普查口径与战役引用的 19 例不重合**（详见 `historical_revival_census_m5022.md`）：
   实测四口径为 33 / 7 / 13 / 117，任何单调口径都不等于 19，判为**引数为播种时点
   快照、口径未写明**——本单据此把口径定义、可复跑 SQL 与实测数字一并入档，
   而不是把 19 硬凑成某个统计量。
5. **`reopened` 不自动派发**是刻意选择：自动派发会把「重开」变成绕过验收的
   自动复活通道（SPEC §10）。返工必须治理侧 release，这一步也留审计行。
6. **既有的 `refill` 一步复活未被本单改造**（重要）：`POST /tensor/missions/{id}/refill`
   仍把 `succeeded/failed/stopped → queued` 一步写回（附 `result.refill` 审计，但无验收
   引用、无 REOPENED 锚点）。本单**没有**静默放宽判据来掩盖它：契约里把它记成
   `audited_retry_edges: ["succeeded->queued","failed->queued","stopped->queued"]`，
   机器判据变成「**未声明**的终态→干活态后门为 0」+「豁免边必须真在图里（不许空头豁免）」。
   改造 refill 走 reopen+release 属发布后的下一步（需先落迁移 103，否则 action CHECK
   未放宽会 500——与 102 custody_requeue 的事故同型），故本单只记账不越界。
7. 端口/服务侧局限：本单验证均在 sqlite 内存库（契约/状态机/端点注册）与活库
   只读普查上完成；端点级 PG 端到端（含 CHECK 触发）未在 CI 覆盖——这是 CI 与
   生产方言差异的既有债（迁移 102 的注释里已记过同类事故），本单用
   `test_reopened_requires_cited_evidence_in_db` 做 SQL 文本级对拍兜底。