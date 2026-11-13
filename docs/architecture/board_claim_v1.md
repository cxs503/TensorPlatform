# 任务榜直领 v1（FEAT-BOARD-CLAIM）

## 1 问题：造单住在 dispatcher 里 = 协调者注意力单点

领单早就是 PULL（`POST /tensor/missions/claim`），但**造单**（挑锚 + 去重 + 组装 goal + 记账）
住在 dispatcher 侧守护进程（`case_feeder` 供算例、`selfstarter` 钉席自驱、taskboard）。于是
「agent 有空 + 锚开着」只有 dispatcher 醒着才变成任务——断流两天的实证即由此而来。
锚与工位都在库里有台账，缺的只是一个**服务端造单入口**。

## 2 交付

| 件 | 位置 |
|---|---|
| 直领端点 | `backend/api/tensor_board.py`（挂在 `api/domain/tensor.py` → `/api/v1/tensor/board`） |
| 锚占用表（共享） | `backend/services/anchor_leases.py` + 表 `anchor_leases` |
| worker 兜底轮询 | `ops/taskplane/tp_worker.py`（队列空 → board/claim） |
| 单测（goal 判据，无需库） | `backend/tests/test_board_claim_goal.py` |
| 红队矩阵（PG 门控） | `backend/tests/test_board_claim_pg.py`（A–F） |
| 数据库级并发证据 | `artifacts/board_claim/redteam_psql_evidence.md` |

## 3 端点契约

```
POST /api/v1/tensor/board/claim
{ "domain"?, "repo"?, "anchor"?, "worker_role"?, "model"?, "holder"?, "runner"?: "native",
  "ttl_seconds"?: 21600, "budget"?, "dry_run"?: false, "organization_id"? }

200 {"claimed": true, "mission_id": 123, "session_id": 456, "anchor": {...}, "repo": "...",
     "task_key": "anchor-<slug>", "runner": "native", "budget": {...}, "lease": {...}, "goal": "..."}
200 {"claimed": false, "reason": "no_open_anchor" | "all_candidates_contended", "tried": [...]}
```
配套：`GET /tensor/board/anchors`（可领锚清单，与 claim 同一口径）、`GET /tensor/board/leases`、
`POST /tensor/board/leases/{slug}/release`（holder 校验）、`POST /tensor/board/reap`（过期回收）。

## 4 服务端四步（每步都可失败，失败不留孤儿单）

1. **挑锚**：`feature_ledger.status ∈ ('open','planned')`，排除
   (a) 有活租约的；(b) 已被在飞单覆盖的（`task_key='anchor-<slug>'` 或 goal 含 `【锚点=<slug>】`，状态 ∈ queued/claimed/running）；
   (c) `feature_ledger.mission_id` 指向非终态单的；(d) 同锚已有在册非终态单的（`_create_mission` 内再查一次，事务内）。
2. **锚级租约**：`anchor_leases` 部分唯一索引 `(anchor_slug) WHERE released_at IS NULL`
   → 并发抢同一锚在数据库里分出唯一赢家（不依赖进程内锁，进程崩了也不会幽灵占用）。
   默认 TTL 6h（300s–24h 可调），到期自动可抢。
3. **组装 goal**：`build_goal()` 纯函数，模板对齐 `case_feeder.goal_of`：锚 + 验收判据**原文**
   + ref 出处零手抄 + 开工三查 + cluster 路由（禁指定节点）+ 报告六段 + 记忆回路 + 台账等级纪律。
4. **落 mission**：`runner=native` 默认、`origin=agent`，写 session + `plan` 事件，回填
   `anchor_leases.mission_id`；`task_key='anchor-<slug>'`（返修接棒时 `-r2/-r3…`）。

## 5 防双供 / 与 anchor_feeder 共用占用表

`anchor_leases` 是**跨供单者的唯一占用表**，`anchor_feeder`（m17102）、`case_feeder`、
dispatcher 兜底都可以直接 `from services import anchor_leases as AL` 后 `acquire/release`，
不必各自造表。契约：`source` 字段自报供单者身份（`board-claim`/`anchor-feeder`/`case-feeder`/`dispatcher`），
`mission_id` 回填后「谁供的哪单」双向可查。即使某个供单者仍用 task_key 锚定（不改代码），
claim 端的「在飞单覆盖」判据也会跳过它 → 双供在两条路径上都被挡住。

## 6 红队结果（诚实）

* **同锚并发 10 领 → 只成 1**：数据库级已证（`winners=1`，活租约行数=1，证据见 artifacts 文件）。
* **租约过期回收**：已证（拨过 `expires_at` 后旧租约标 `expired`、新持有者拿到新租约）。
* **LEDGER-GATE 兼容**：goal 组装不制造台账等级断言 → `ledger_gate(goal)` 的 `violations=[]`、
  `unregistered_ids=[]`（单测 `test_goal_does_not_trip_report_ledger_gate` 已跑绿）。gate 的 `ok=False`
  仅因台账自身 `DEGRADED(PIN_MISSING)`，与本端点无关。
* **未跑**：`test_board_claim_pg.py` A–F（含 TestClient 端点契约、-r2 返修接棒、在飞单跳过）
  本沙箱无真 PG 驱动（psycopg 为 stub）→ skip；待有驱动节点补跑（命令见文件头）。**不声明为通过。**

## 7 部署与回滚

* 表由端点首调 `ensure_schema()` 幂等自建（advisory lock 串行），无迁移依赖；回滚 = 摘掉
  `api/domain/tensor.py` 里的一行 `router.include_router(tensor_board_router)`，表可留（无外键、无副作用）。
* `tp_worker` 兜底默认开、`TP_BOARD_FALLBACK=0` 可一键关断（对照组）。
* dispatcher 不删：`selfstarter`（席位唤醒）与 `case_feeder`（算例供给）降级为兜底/推式供给，
  主路径 = agent 直领。
