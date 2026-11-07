# 批量任务 API（`POST/GET /tensor/missions/batch`）

交付物：`src/api/routes/batch_missions.py`（路由）、`tests/test_batch_api.py`（pytest）、
`docs/proposals/104_tensor_mission_batches.sql`（schema 提案，**未应用**）、
`artifacts/m5115/prod_probe.py`（生产 schema 探针，事务内运行后回滚）。

## 1. 为什么需要它

当前"一次提交 N 条任务"是外部脚本循环调 `POST /tensor/missions`：非原子（中途失败留下半批）、
无批次标识（无法问"这一波跑到哪了"）、无幂等（重跑脚本会重复入库）。批量端点把这三件事
变成平台契约。

## 2. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/tensor/missions/batch` | 请求体是 **JSON 数组**（元素为任务 spec，1..500 条），返回 `batch_id` |
| GET | `/tensor/missions/batch/{batch_id}` | 批次进度摘要（状态计数 / 完成百分比） |

POST 请求体（数组元素）：

```json
[
  {"goal": "verify resistance kernel", "repo": "TensorLBM", "base_ref": "main",
   "budget": {"max_tool_calls": 40}, "model": null, "runner": null,
   "co_repos": null, "parent_mission_id": null}
]
```

可选头 `Idempotency-Key`（缺省见 §4）；可选 query `organization_id`。

POST 201 响应：

```json
{
  "batch_id": "1bfc3089-e0e7-480d-b085-272eb7f4d509",
  "status": "queued", "count": 50, "requested": 50,
  "mission_ids": [4764, 4765, "..."],
  "mode": "degraded_sequential", "evidence": "MOCK", "atomic": false,
  "idempotency_key": "auto:6f7c…", "created_at": "2026-11-07T…",
  "schema": {"batch_id_column": false, "batch_table": false,
             "mode": "degraded_sequential",
             "schema_migration_required": "docs/proposals/104_tensor_mission_batches.sql"},
  "notice": "platform schema lacks batch_id/tensor_mission_batches: … (script-equivalent, MOCK)…"
}
```

GET 200 响应（`?include_missions=false` 省略逐条列表）：

```json
{"batch_id": "…", "batch_status": "in_progress", "mode": "atomic", "evidence": "REAL",
 "atomic": true, "requested": 50, "count": 50, "terminal": 4, "progress_percent": 8.0,
 "status_counts": {"queued": 46, "claimed": 0, "running": 1, "awaiting_acceptance": 0,
                   "succeeded": 4, "failed": 0, "stopped": 0},
 "missing_rows": 0, "missions": [{"id": 4764, "status": "succeeded"}, "…"]}
```

未知 `batch_id` → 404 `{"error": "batch_not_found"}`；空数组 / 非数组 / 字段缺失 → 422；
无凭据 → 401。

## 3. 两种运行模式（证据分级）

路由在**每次请求时探测数据库**（`capabilities()`），据此选择模式，并把模式写进响应，
调用方无法把它误当成原子批次：

| 模式 | 触发条件 | 语义 | `atomic` | 证据 |
|---|---|---|---|---|
| `atomic` | `tensor_dev_missions.batch_id` 列 **且** `tensor_mission_batches` 表存在 | 单事务：批次行 + N 条任务 + `mission_ids` 一起提交，任一失败整批回滚 | `true` | **REAL** |
| `degraded_sequential` | 上述任一缺失（= 当前生产库） | 降级为"脚本批量提交"：逐条单条 INSERT + 每条单独 commit，批次登记在模块自建的 SQLite sidecar（不改平台 schema） | `false` | **MOCK** |

**生产现状（`artifacts/m5115/prod_probe_result.json`，只读探测 + 事务回滚）**：
`tensor_dev_missions` 有 4763 行，无 `batch_id` 列，无 `tensor_mission_batches` 表 →
生产实际走 `degraded_sequential`（MOCK）。要拿到 REAL 需要 owner 批准并应用
`docs/proposals/104_tensor_mission_batches.sql`（`ALTER TABLE … ADD COLUMN batch_id` +
建登记表）。该文件**故意放在 `docs/proposals/` 而不是 `backend/migrations/`**：
`backend/migrations/run_migration.py` 会自动应用 `NNN_*.sql`，平台 schema 变更必须人工审批。

降级模式的诚实边界：不是原子操作。中途失败时已提交的行留在库里，响应 502 并给出
`{"atomic": false, "applied": [...], "failed_index": i}`（见测试
`test_degraded_mode_partial_failure_is_reported_honestly`）。sidecar 登记表是**单节点**文件，
多副本部署需换成共享存储（应用迁移后可彻底弃用）。

## 4. 幂等性

`Idempotency-Key` 头优先；缺省时用「组织 id + 数组内容规范化 JSON 的 sha256」（前缀 `auto:`）。
重复提交同一 key → **409**，响应体带已存在批次的 `batch_id` 与 `mission_ids`，且**不插入任何行**
（`atomic` 模式由 `idempotency_key UNIQUE` 兜底，并发竞争也只会有一个赢家）。

注意：缺省内容哈希意味着"内容完全相同的合法重投"也会被判重复；需要重投请显式换一个
`Idempotency-Key`。登记表暂无 TTL/GC。

## 5. 挂载方式

```python
import sys; sys.path.insert(0, "<repo>/src")
from api.routes.batch_missions import router as batch_router
app.include_router(batch_router)      # 必须在单任务 router 之前 include
```

本交付**没有**修改 `backend/main.py`：TensorPlatform 当前处于冻结期（平台开发需 owner 解冻），
路由以独立模块 + 一行挂载的形式交付，接线动作留给解冻后的 owner。模块在 backend 包内被导入时
使用真实 `api.deps.require_auth` / `database.config.get_db`；独立导入（文档/工具）时退化为
等价的 Bearer 守卫（同样 401），认证契约不会静默消失（`AUTH_PROVIDER` 标明实际来源）。

## 6. 验证

```
pytest tests/test_batch_api.py -v     # 13 passed
python3 artifacts/m5115/prod_probe.py # 生产 PostgreSQL 方言 + 回滚洁净性
```

| 判据 | 结果 |
|---|---|
| `pytest tests/test_batch_api.py -v` 全过 | 13 passed |
| 提交 50 条 → DB 查 50 条 `status=queued` | atomic：SQLite 实测 50/50；degraded：SQLite 50/50，生产 PostgreSQL 事务内 50/50 |
| `batch_id` 非空 | UUIDv4，`uuid.UUID()` 解析通过 |
| 幂等：重复提交同批次被拒 | 409 `batch_already_submitted`，行数不变（两种模式各一测） |
| 原子性正/负控 | 中途 CHECK 违约 → 502 且 0 行入库（atomic）；降级模式 1 行留存并如实上报 |
| 生产库未被污染 | 探针前后均为 4763 行，残留 0 行 |

SQL 方言：模块对 sqlite/PostgreSQL 分别生成 `CAST(:budget AS jsonb)` 与裸参数，插入用
`RETURNING id`；生产探针已实测 PostgreSQL 分支可执行（含 bigint `created_by`——最初传用户名
字符串会被 PostgreSQL 拒绝，据此改为传数字 user id）。