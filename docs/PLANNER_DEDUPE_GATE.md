# 规划线 task_key 查重门（Planner Dedupe Gate）

> m15073 · 2026-11-11 · 规划师必要性门机器化

## 背景

规划师在派发新 mission 前需要执行「必要性门」——确认同 task_key 是否已有在飞/近期任务。
此前 missions 列表 API 不暴露 `task_key` 字段，也无 task_key 过滤参数，规划师只能拉 500 条 mission 后在本地对 goal 做关键词匹配，**结构性无法判定「同 task_key 是否已派/在飞」**（m15068 实测）。

本单为平台共性机制，零领域语义。

## 交付清单

| # | 交付物 | 路径 |
|---|--------|------|
| 1 | `GET /api/v1/tensor/missions` 响应新增 `task_key` 字段 | `backend/api/tensor_missions.py` |
| 2 | `GET /api/v1/tensor/missions?task_key=` 精确过滤 | 同上 |
| 3 | `GET /api/v1/tensor/missions?task_key_like=` 子串过滤 | 同上 |
| 4 | `GET /api/v1/tensor/missions/task-key-conflicts` 冲突检测端点 | 同上 |
| 5 | 测试套件（11 用例全绿） | `tests/test_missions_taskkey_gate.py` |
| 6 | 本文档 | `docs/PLANNER_DEDUPE_GATE.md` |

## API 说明

### 1. 列表响应新增 task_key 字段

```
GET /api/v1/tensor/missions?limit=50&offset=0&task_key=<可选>&task_key_like=<可选>
```

**响应示例**（节选）：

```json
{
  "items": [
    {
      "id": 15073,
      "goal": "W9 平台自优化 · 规划线机器化查重门",
      "repo": "TensorPlatform",
      "status": "running",
      "task_key": "w9_planner_dedupe_gate",
      "created_at": "2026-11-11T17:00:00+00:00",
      ...
    }
  ],
  "total": 1
}
```

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_key` | string (可选) | 精确匹配，返回 task_key 完全相同的 mission |
| `task_key_like` | string (可选) | 子串匹配（SQL LIKE `%value%`） |
| `status` | string (可选) | 按状态过滤 |
| `limit` | int (可选, 默认 50) | 返回条数上限 |
| `offset` | int (可选, 默认 0) | 偏移量 |

两个 task_key 过滤参数可同时使用（AND 关系），也可单独使用。缺省时行为与现有 API 完全一致。

### 2. 冲突检测端点

```
GET /api/v1/tensor/missions/task-key-conflicts?hours=24&repo=<可选>
```

**响应示例**：

```json
{
  "hours": 24,
  "repo": null,
  "conflicts": [
    {
      "task_key": "w9_planner_dedupe_gate",
      "repo": "TensorPlatform",
      "count": 2,
      "mission_ids": [15070, 15073],
      "statuses": ["running", "queued"]
    }
  ]
}
```

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `hours` | int (可选, 默认 24) | 时间窗口（小时），范围 1-720 |
| `repo` | string (可选) | 按仓库过滤 |

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_key` | string | 重复的 task_key |
| `repo` | string | 仓库名 |
| `count` | int | 出现次数（≥2） |
| `mission_ids` | int[] | 涉及的 mission ID 列表 |
| `statuses` | string[] | 各 mission 的状态 |

**排除规则**：
- `task_key` 为 NULL 或空字符串的 mission 不计入冲突
- 按 organization_id 隔离（只返回当前用户组织的冲突）

## 规划师判定规则

### 禁派条件

同 `task_key` 且近 24h 内存在**非终态** mission → **禁派**

```python
# 伪代码
conflicts = GET /api/v1/tensor/missions/task-key-conflicts?hours=24
for c in conflicts["conflicts"]:
    if c["task_key"] == proposed_task_key:
        non_terminal = [s for s in c["statuses"] if s not in TERMINAL_STATUSES]
        if non_terminal:
            raise DedupeError(f"task_key {c['task_key']} has {len(non_terminal)} non-terminal mission(s) in last 24h")
```

### 允许返修

近 24h 内同 `task_key` 的 mission 全部为终态且状态为 `failed` → **允许返修轮**

```python
# 伪代码
conflicts = GET /api/v1/tensor/missions/task-key-conflicts?hours=24
for c in conflicts["conflicts"]:
    if c["task_key"] == proposed_task_key:
        all_failed = all(s == "failed" for s in c["statuses"])
        if all_failed:
            # 允许派发返修轮
            pass
```

### 终态定义

终态（terminal）包括：`completed`, `failed`, `stopped`, `agent_lost`

非终态包括：`queued`, `claimed`, `running`, `awaiting_acceptance`, `suspended`

## curl 示例

```bash
# 1. 查看列表中的 task_key 字段
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/tensor/missions?limit=5" | jq '.items[] | {id, task_key, status}'

# 2. 精确过滤 task_key
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/tensor/missions?task_key=w9_planner_dedupe_gate" | jq '.total'

# 3. 子串过滤
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/tensor/missions?task_key_like=planner" | jq '.items[] | {id, task_key}'

# 4. 冲突检测（默认 24h）
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/tensor/missions/task-key-conflicts" | jq '.'

# 5. 冲突检测（自定义时间窗口 + 仓库过滤）
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/tensor/missions/task-key-conflicts?hours=48&repo=TensorPlatform" | jq '.'
```

## 实测输出

```
$ python -m pytest -q tests/test_missions_taskkey_gate.py
...........                                                              [100%]
11 passed, 6 warnings in 2.17s
```

### 测试覆盖

| 用例 | 验证内容 | 状态 |
|------|----------|------|
| `test_task_key_present_in_list` | task_key 字段出现在列表响应 | ✅ |
| `test_task_key_null_in_list` | 无 task_key 的 mission 返回 null | ✅ |
| `test_exact_filter_returns_match` | task_key= 精确过滤返回匹配项 | ✅ |
| `test_exact_filter_returns_empty` | task_key= 精确过滤无匹配返回空 | ✅ |
| `test_like_filter_matches_substring` | task_key_like= 子串过滤 | ✅ |
| `test_conflict_detected` | conflicts 命中同 key 两单 | ✅ |
| `test_old_conflict_excluded` | 时间窗外（48h）不计入 24h 窗口 | ✅ |
| `test_recent_conflict_included` | 时间窗内冲突正确命中 | ✅ |
| `test_cross_org_not_conflict` | 不同 organization 隔离 | ✅ |
| `test_null_task_key_not_conflict` | NULL task_key 不计冲突 | ✅ |
| `test_empty_string_task_key_not_conflict` | 空字符串 task_key 不计冲突 | ✅ |

## 代码变更摘要

| 文件 | 变更 |
|------|------|
| `backend/api/tensor_missions.py` | `_mission_row()` 新增 `task_key` 字段；`list_missions()` 新增 `task_key`/`task_key_like` 查询参数及过滤逻辑；新增 `task_key_conflicts()` 端点（PostgreSQL/SQLite 双兼容）；`_iso()` 增强处理字符串输入 |
| `tests/test_missions_taskkey_gate.py` | 新增 11 项测试用例 |
| `docs/PLANNER_DEDUPE_GATE.md` | 本文档 |

## 注意点

- **数据库兼容**：冲突检测端点自动检测数据库方言，PostgreSQL 使用 `ARRAY_AGG` + `NOW()`，SQLite 使用 `GROUP_CONCAT` + `datetime('now', ...)`
- **组织隔离**：所有查询均按 `organization_id` 过滤，跨组织不会互相干扰
- **向后兼容**：缺省参数时 API 行为与修改前完全一致，旧调用方不受影响
- **零领域语义**：本单所有代码均为平台共性机制，不含任何船舶/冰区/求解器等域词
