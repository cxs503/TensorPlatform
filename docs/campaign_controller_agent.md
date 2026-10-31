# 战役总管 Agent 职责卡（Controller Agent）

> **Mission**: m612 — CAMP-CTRL 战役总管：主计划入库 + 实时进度把控
> **Runner**: `controller`（影子模式先跑，不实际派单）
> **权限阶梯**: advisory → shadow → 低风险建议生效 → 升权留 owner

---

## 1. 定位

战役级 controller——规划师（m604）管「这单该不该派、怎么写」，总管管「整场战役走到哪、哪里卡、下一步是什么」。

复用基础：`tensor_dev_missions` 已有 `campaign_id` 列；`guide_tracker` 30min 快照模式可复制。

## 2. 输入

| 输入源 | 说明 |
|--------|------|
| 主计划 | `campaign_plans` + `campaign_milestones` + `campaign_nodes` |
| 实时快照 | `campaign_snapshots`（30min 级） |
| 近期事件流 | `campaign_events`（近 7 天） |

## 3. 输出

结构化周报级 JSON，包含：

### 3.1 计划 vs 实际偏差分析

```json
{
  "total_nodes": 24,
  "done_nodes": 8,
  "in_flight_nodes": 5,
  "blocked_nodes": 3,
  "failed_nodes": 2,
  "planned_progress_pct": 45,
  "actual_progress_pct": 33,
  "progress_deviation": -12,
  "milestone_count": 6,
  "milestone_done": 2
}
```

### 3.2 下波建议任务清单

```json
[
  {
    "node_id": 15,
    "name": "阻力复现验证",
    "node_type": "task",
    "milestone_id": 3,
    "priority": "high",
    "reason": "dependencies satisfied"
  }
]
```

### 3.3 需 owner 裁决项

```json
[
  {
    "type": "spinning_node",
    "node_id": 7,
    "node_name": "耐波性 CFD 网格",
    "consecutive_fails": 3,
    "recommendation": "escalate to root cause analysis"
  },
  {
    "type": "blocked_chain",
    "upstream_id": 4,
    "upstream_name": "几何验证",
    "blocked_downstream": [{"id": 9, "name": "阻力计算"}, {"id": 10, "name": "推进计算"}],
    "recommendation": "resolve upstream blocker or re-plan downstream"
  }
]
```

## 4. 权限阶梯

| 级别 | 权限 | 说明 |
|------|------|------|
| advisory | 只读建议 | 输出报告，不触发任何动作 |
| shadow | 影子运行 | 模拟派单，记录但不执行 |
| low-risk | 低风险建议生效 | 自动派发非关键路径任务 |
| escalated | 升权 | 需 owner 手动批准 |

## 5. 核心检测逻辑

### 5.1 阻塞检测

- **上游滞留下游空等**：上游节点 `failed`/`blocked` → 下游 `pending` 节点依赖未满足
- **呈报格式**：`blocked_chains` 数组，含上游 ID/名称/状态 + 下游阻塞列表

### 5.2 打转检测

- **连败 ≥2**：节点 `consecutive_fails >= 2` → 根因升级单
- **呈报格式**：`spinning_nodes` 数组，含节点 ID/名称/连败次数

### 5.3 游击系数

- **定义**：计划外单占比 = `(总 mission 数 - 计划内 mission 数) / 总 mission 数`
- **阈值**：>0.2 需关注，>0.5 需 owner 裁决

## 6. 节点状态定义

| 状态 | 含义 | 转换条件 |
|------|------|----------|
| pending | 等待中 | 初始状态 |
| in-flight | 执行中 | mission 已 claim |
| done | 完成 | **复现判决 pass** |
| blocked | 阻塞 | 上游依赖未满足 |
| failed | 失败 | 复现判决 fail |
| orphan | 孤儿 | mission 不存在或已删除 |

## 7. API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/tensor/campaigns/plans` | 创建主计划 |
| GET | `/tensor/campaigns/plans` | 列出计划 |
| GET | `/tensor/campaigns/plans/{id}` | 获取计划详情 |
| POST | `/tensor/campaigns/plans/{id}/activate` | 激活计划 |
| POST | `/tensor/campaigns/milestones` | 创建里程碑 |
| GET | `/tensor/campaigns/plans/{id}/milestones` | 列出里程碑 |
| POST | `/tensor/campaigns/nodes` | 创建节点 |
| GET | `/tensor/campaigns/plans/{id}/nodes` | 列出节点 |
| POST | `/tensor/campaigns/nodes/{id}/verdict` | 更新复现判决 |
| POST | `/tensor/campaigns/plans/{id}/snapshot` | 生成快照 |
| GET | `/tensor/campaigns/plans/{id}/snapshots` | 列出快照 |
| GET | `/tensor/campaigns/plans/{id}/events` | 列出事件 |
| POST | `/tensor/campaigns/plans/{id}/controller-report` | 生成总管报告 |

## 8. 与 guide_tracker 的接线

- 快照行进 guide_tracker 同源看板
- 接线由编排侧完成（本模块提供 API 和数据模型）
- 30min 定时任务调用 `POST /tensor/campaigns/plans/{id}/snapshot`

## 9. 红队验证

| 测试场景 | 预期结果 |
|----------|----------|
| 注入伪造「完成」节点（无复现判决） | 快照不算 done |
| 注入阻塞链 | 必须检出并呈报 |
| 注入计划外单 | 游击系数必须上升 |
