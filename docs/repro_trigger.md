# 自动 repro 子 mission 触发器（repro_trigger v1）

> mission m5120 交付 · 模块 `src/services/repro_trigger.py` · 测试 `tests/test_repro_trigger.py`
> 证据等级：**REAL**（模块逻辑 + 数据库落库，SQLite 行为级验证）／**MOCK**（"mission 完成即触发"的端到端自动性，见 §5 降级方案）

## 1. 为什么需要它

`finish` 路径目前是**自述制**：mission 自己声明"机器判据全绿"，平台没有独立复算。
m533 / m583 / m578 的判例（见 `backend/migrations/095_tensor_repro_integrity.sql` 头部）
全部源于这个缺口。平台已经有：

- `tensor_repro_verdicts`（append-only，DB 触发器禁 UPDATE/DELETE）；
- `runner='repro'` 的子 mission 形态；
- 手工入口 `POST /tensor/repro/queue`（`backend/api/tensor_repro.py::enqueue_repro`）。

缺的是**自动性**：没有人在 finish 之后去点这个入口，队列天然为空。本模块补这一环。

## 2. 机制

```
mission 完成 (status=succeeded, runner != 'repro')
      │  on_mission_complete(session, mission_id)   ← 在流内挂点（推荐）
      │  或 scan_completed_missions(session)        ← cron 每 5 分钟兜底
      ▼
INSERT tensor_dev_missions (runner='repro', parent_mission_id=母ID,
                            status='queued', model≠作者 model,
                            budget={40 tool calls, 3600 s, 1.5M tokens})
      │
      ▼
repro mission 完成 → on_mission_complete(session, repro_id, verdict='pass'|'partial'|'fail'|'cheat')
      ▼
INSERT tensor_repro_verdicts (mission_id = **母 mission id**, append-only)
UPDATE 母 mission: pass/partial → succeeded；fail/cheat → revoked；result.repro 记录判定
UPDATE repro mission: status='succeeded', finished_at=now
```

### 关键设计

| 点 | 做法 | 理由 |
|---|---|---|
| 盲单 | `repro_model_for(author)` 保证 repro model ≠ 作者 model（含 `repro-agent-default`/`-alt` 两名池冲突） | 独立复现，防自证 |
| 幂等 | `find_existing_repro(parent_id)` 存在即跳过；在流与 cron 可同时跑 | 双通道不重复插入 |
| 不递归 | 对 `runner='repro'` 的 mission 永不再生成 repro | 防 repro-of-repro 爆炸 |
| 只认 succeeded | `TRIGGERABLE_STATUSES = {'succeeded'}` | failed 是 honest-FAIL，无需复算；revoked/stopped 是处置态 |
| 判定落库键 | verdict 行的 `mission_id` = **母** mission | 与 `POST /tensor/repro/verdicts`（`VerdictCreate.mission_id`）语义一致，KPI/审计不劈叉 |
| 处置一致 | pass/partial → succeeded；fail/cheat → revoked | 与 `backend/api/tensor_repro.py::create_verdict` 的纪律同构（`revoked ≠ failed`） |
| 幂等判定 | 已有 verdict 则 `ValueError`（除非 `allow_duplicate=True`） | verdict 表 append-only，重放必须显式 |
| 无 API 依赖 | 模块只依赖 SQLAlchemy Core `text()`；不 import FastAPI | 可在 dispatcher / cron / 测试里直接调用 |
| 方言 | PG 走 `CAST(:p AS jsonb)`，SQLite 走 `:p`；时间用 `CURRENT_TIMESTAMP` | 生产 PG / CI SQLite 同一份 SQL |

## 3. 接入方式

**推荐（在流内，REAL）**：把 finish 路径里裸的

```python
UPDATE tensor_dev_missions SET status='succeeded', finished_at=now() WHERE id=:mid
```

换成

```python
from services.repro_trigger import complete_mission
complete_mission(session, mission_id, status="succeeded", result=result_dict)
```

它对 `on_mission_complete()` 的返回值**永不抛异常**（不合格/已排队 → `{"action":"noop"}`），
所以不会把 finish 打挂。

**兜底（cron，MOCK）**：

```bash
# 每 5 分钟
*/5 * * * * cd /path/to/TensorPlatform && PYTHONPATH=src \
  python3 -m services.repro_trigger --scan --dsn "$DATABASE_URL" >> /var/log/repro_trigger.log 2>&1
```

单条手动重放：

```bash
python3 -m services.repro_trigger --mission-id 123 --dsn "$DATABASE_URL"
python3 -m services.repro_trigger --mission-id 456 --verdict pass --dsn "$DATABASE_URL"
```

## 4. 测试与验收

```
$ python3 -m pytest tests/test_repro_trigger.py -v
17 passed
```

| 机器判据 | 覆盖用例 | 结果 |
|---|---|---|
| 完成 1 条 mission → DB 新增 **1** 条 `parent_mission_id` 关联的 repro mission | `test_complete_creates_exactly_one_queued_repro_mission` | ✅ |
| 新 mission `status='queued'`、`runner='repro'`、model≠作者 | 同上 + `test_repro_model_never_equals_author_even_for_default_names` | ✅ |
| verdict 写入 `tensor_repro_verdicts` | `test_pass_verdict_row_and_mother_kept_succeeded` | ✅ |
| 母 mission 状态更新 | pass→succeeded / fail→revoked / cheat→revoked（3 用例） | ✅ |
| 幂等（在流 + cron 双通道） | `test_trigger_is_idempotent`、`test_scan_and_inflow_do_not_duplicate` | ✅ |
| append-only 语义（重复判定拒绝） | `test_duplicate_verdict_rejected_append_only` | ✅ |
| 非 repro mission 不能收判定 | `test_non_repro_mission_cannot_receive_verdict` | ✅ |
| cron CLI 端到端 | `test_cron_cli_scan_enqueues`（子进程 `--scan --dsn sqlite:///...`） | ✅ |
| PG 方言占位符 | `test_json_param_cast_is_postgres_only` | ✅ |

测试库为 SQLite `:memory:`（`StaticPool`）+ 独立连接上的 `ensure_schema()`；每次用例前清表。

## 5. 降级方案（honest-FAIL 声明）

计划中的"mission complete 即触发"要求在**生产 finish 路径加一行调用**。当前
TensorPlatform 处于冻结期（2026-11-02 owner 令：平台改动只报告不改），因此本单**没有**
改动 `backend/` 既有 finish 流程，端到端自动性按下述口径交付：

- 模块级能力（建子 mission / 写判定 / 更新母状态 / 幂等 / 盲单）= **REAL**，已在 SQLite 上行为级验证；
- "mission 完成这一刻自动触发" = **MOCK（定时扫描）**：`--scan` cron 每 5 分钟扫一次
  `status='succeeded' AND runner<>'repro' AND 无 repro 子 mission`，最长 5 分钟延迟，
  不是事件驱动；日志里有 `enqueued` 计数可对账。
- 解冻后把 §3 的一行调用接到 finish 路径即可升级为 REAL，无需改本模块。

其他未验证项：PostgreSQL 真库未连（本机无凭据），`jsonb` cast / `RETURNING` 分支只有
占位符生成单测覆盖；建议解冻后在一套 PG 测试库跑一次同一组用例（DSN 换成
`TEST_DATABASE_URL` 即可，模块方言自适应）。

## 6. 与既有组件的关系

| 组件 | 关系 |
|---|---|
| `backend/api/tensor_repro.py::enqueue_repro` | 手工入口；本模块是其自动化前置，建出的子 mission 形态完全一致（`runner='repro'`、预算同量级） |
| `backend/api/tensor_repro.py::create_verdict` | 判定语义来源（pass/partial/fail/cheat，fail/cheat→revoked）；本模块是其无 HTTP 版本 |
| `tensor_repro_verdicts` append-only 触发器 | 本模块只用 INSERT，不触碰 UPDATE/DELETE，触发器不会被冒犯 |
| `scan_completed_missions` | 与 `backend/migrations/095` 的 repro rate KPI 天然对齐：队列不再常空 |