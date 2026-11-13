# FP15 进展信号清单（Progress Signal Inventory）

## 概述

本文档记录 TensorPlatform agent worker 的进展判定信号集合，包括现有信号、新增信号及缺失信号。

## 现有信号（修复前）

| 信号 | 来源 | 判定逻辑 | 局限 |
|------|------|----------|------|
| 工具调用 | LLM 响应 | 有 tool_calls = 有进展；无 tool_calls + 2 次催办 = no_progress | 集群作业等待期 LLM 可能输出纯文本，误判 |
| 文件变更 | git status | 工作树文件变化 = 有进展 | 长计算期间无文件写入，误判 |
| 心跳存活 | heartbeat API | heartbeat_at 在 240s 内 = 存活 | 只判活不判进展 |

## 新增信号（FP15 修复）

| 信号 | 来源 | 判定逻辑 | 实现位置 |
|------|------|----------|----------|
| **在飞集群作业** | `run_cluster submit/run/shard` 返回的 cjN 作业号 | 有在飞作业 = 有进展（等待期不算停滞） | `harness_gateway/mirror/tp_worker.py` `_IN_FLIGHT_JOBS` |

### 在飞作业跟踪机制

```
submit/run/shard → 解析返回文本中的 cjN → 加入 _IN_FLIGHT_JOBS
fetch/kill → 解析返回文本中的 cjN → 从 _IN_FLIGHT_JOBS 移除
```

### 进展判定流程（修复后）

```
LLM 返回无 tool_calls?
  ├─ 有在飞作业 → 催办查作业状态（不算 no_progress），重置催办计数
  └─ 无在飞作业 → 催办（最多 2 次）→ 仍无工具调用 → no_progress
```

## 缺失信号（未来改进）

| 信号 | 说明 | 优先级 |
|------|------|--------|
| 长命令未超时 | `run_shell` 启动的后台进程仍在运行 | 中 |
| API 调用等待 | `call_ship_api` 等待响应期间 | 低 |
| 模型思考中 | LLM 正在生成响应（streaming 模式） | 低 |

## 心跳载荷扩展

修复后心跳包含 `in_flight_jobs` 字段：

```json
{
  "claim_token": "...",
  "head": "run_cluster @ call 5",
  "tool_calls": 5,
  "elapsed_seconds": 120,
  "in_flight_jobs": ["cj10", "cj20"]
}
```

## 阈值与时间窗

| 参数 | 值 | 说明 |
|------|-----|------|
| 心跳超时 | 240s | 超过此时间无心跳判 agent_lost |
| 催办次数 | 2 | 无工具调用时最多催办 2 次 |
| 在飞作业检查 | 每次 LLM 响应 | 无工具调用时立即检查 |

## 改动对照

| 文件 | 改动行数 | 说明 |
|------|----------|------|
| `harness_gateway/mirror/tp_worker.py` | +55 -3 | 新增在飞作业跟踪、修改 no_progress 判定逻辑、扩展心跳载荷 |
