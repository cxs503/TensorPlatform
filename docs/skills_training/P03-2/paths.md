# P03-2 速查：逐跳坐标 / 断言边界 / 判据

> 机器可读版调查笔记，正文见 `docs/skills_training/P03-2.md`。所有 `file:line` 取自分支 `agent/m6343`。

## A. 逐跳坐标（dev mission 队列）

| # | 跳 | 坐标 | 关键点 |
|---|---|---|---|
| 1 | 受理 `POST /tensor/missions` | `backend/api/tensor_missions.py:133`（INSERT :148 / session :163 / plan 事件 :180 / 返回 :189） | 200 + `{"status":"queued"}`，不校验消费者 |
| 2 | 领取 `POST /claim` | 同文件 :506 | `FOR UPDATE SKIP LOCKED` |
| 2a | lazy reap | :541-565（boot grace `:42` 300s） | 只动 `claimed/running`，`heartbeat_at < now()-240s` |
| 2b | 过滤器 | :571-585（`runner`/`model(+claim_null_model)`/`mission_id`/`worker_role`/`agent_session_id`/依赖门） | 等值合取，**无兜底** |
| 2c | 未命中返回 | :614 `{"claimed": false}` | 与「空闲」同形，无理由字段 |
| 3 | 启动 `POST /{id}/start` | :666 | 409 unless claimed |
| 4 | 心跳 `POST /{id}/heartbeat` | :695 | 15s；`cancel_requested` → stop order |
| 5 | 完成 `POST /{id}/complete` | :797（守卫 :805 / acceptance :805-900 / 终态 :905-925） | `acceptance=true` → 非终态 `awaiting_acceptance` |
| 6 | 验收 `POST /tensor/approvals/{id}/decide` | `backend/api/tensor_approvals.py:185`（dev_acceptance 分支 :229-300） | ADMIN only，approve→succeeded |
| 7 | PR 收口 | `backend/api/tensor_mission_lifecycle.py:722`（`/pr-merge-queue`） | `awaiting_pr_merge` → succeeded/failed |
| 8 | 回收 `POST /{id}/reclaim` | :998 | 同 principal 重启重新武装 |
| 9 | 消费者 claim 阶梯 | `ops/tp_worker.py:540-586` | `runner="native"` 硬编码；`model=MODEL` |

## B. 同构次队列（`tensor_executions`）

| 跳 | 坐标 | 备注 |
|---|---|---|
| 受理 | `backend/api/tensor_executions.py:105`（`trigger_execution`） | 插入 `status='queued'` + 开 agent session |
| 领取 | 同文件 :250（`claim_execution`） | lazy reap 阈值 **90s**（:277 `heartbeat_at < now()-interval '90 seconds'`） |
| 启动/心跳/完成 | 同文件 `start_execution`/`heartbeat_execution`/`complete_execution`（依次定义于 claim 之后） | `claim_token` 校验；终态 `succeeded|failed|cancelled` |
| 消费者 | `tools/executor/tensor_executor.py:553`（claim 循环） | **无服务化挂载**（无 cron/timer）→ 同类「受理后无消费者」风险面 |

## C. 断言边界（覆盖 / 黑洞）

| 测试 | 覆盖跳 | 断言终点 | 黑洞 |
|---|---|---|---|
| `tests/factory/test_agent_control_plane.py` | 1,2,3,5,6,8 | `sum(claimed)==1`、403、decide 收口 | 从不构造「无匹配消费者」 |
| `tests/factory/test_resident_sessions.py` | 2 | 越权被拒、`busy` | 同上 |
| `tests/factory/test_platform_exploration.py` | 2 | claim 成功/失败两支 | 同上 |
| `tests/factory/test_strategy_campaign.py` | 2,5 | 状态流转 | 以「有 worker 在领」为前提 |
| `backend/tests/test_status_contracts.py`、`backend/tests/test_pr_gate.py:51` | 词表 | 状态集合与契约一致 | 不校可达性/超期 |
| `backend/tests/test_queue_adoption_in27.py` | 审计纯函数 | `orphan_pins==['glm-5.1']`、负控 | 纯逻辑，不在请求路径上 |
| `tools/queue_adoption_audit.py --apply` | — | 人工 healer | 无调度挂载 |

## D. 判据（可 SQL 化）

- **J1 症状**：`status='queued' AND now()-created_at > 240 min`。
- **J2 根因**：J1 的单其车道近 6 h 无 claim 证据（`claimed_by`/`heartbeat_at`）。
- **J3 机制缺口**：全仓无 `queued` 超期→终态写入（reap 只写 `claimed/running`）。
- **J4**：`awaiting_acceptance` 需 ADMIN 决策；**J5**：`awaiting_pr_merge` 需 governor/PR 合并。

## E. 快照（2026-11-07 ~22:00 UTC）

- queued 53（最老 12.07 h）；其中 `model='glm-5.1'` **45**（11.11 h）、`runner='openhands'` **6**（12.07 h）、`model='qwen3.7-plus'` 2（0.08 h，正常）。
- running 20（无 >240s 陈旧心跳）、suspended 11（最老 6d14h）、awaiting_pr_merge 1（24 min）。
- `ps -ef`：20 个 `tp_worker`，`--model` ∈ {qwen3.6-plus, qwen3.7-plus, Qwen3.8-27B, deepseek-v4-flash, DeepSeek-V4-Flash-0731}；**无 glm-5.1**。
- glm-5.1 车道：近 6 h claim 计数 0；最后终态 2026-11-01 04:36 UTC。