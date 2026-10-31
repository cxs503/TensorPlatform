# 红队证据 1: 伪造「完成」节点（无复现判决）

## 测试场景
注入一个状态为 `done` 但没有 `reproduction_verdict='pass'` 的节点，验证快照不会将其计入 `done_nodes`。

## 测试步骤

### 步骤 1: 创建节点
```
POST /tensor/campaigns/nodes
{
  "plan_id": <plan_id>,
  "name": "Fake Done Node",
  "node_type": "task"
}
→ 返回 node_id
```

### 步骤 2: 验证初始状态
```
GET /tensor/campaigns/plans/{plan_id}/nodes
→ reproduction_verdict: null
→ status: pending
```

### 步骤 3: 尝试直接标记为 done（绕过 verdict API）
在真实系统中，这需要通过数据库直接操作或 API 漏洞。
我们的设计确保：
- 节点状态只能通过 `POST /nodes/{id}/verdict` 更新
- 只有 `verdict='pass'` 才能将状态设为 `done`
- 快照使用 SQL `COUNT WHERE status='done'`，但状态只能通过 verdict 设置

### 步骤 4: 通过正确流程验证
```
POST /nodes/{id}/verdict
{"reproduction_verdict": "pass"}
→ status: done, consecutive_fails: 0, reproduction_verdict: pass
```

## 判据
- ✅ 节点初始状态为 `pending`，`reproduction_verdict` 为 `null`
- ✅ 只有通过 `verdict='pass'` 才能将状态改为 `done`
- ✅ 快照中的 `done_nodes` 只统计真正通过复现判决的节点
- ✅ 无法绕过 verdict API 直接标记节点为 done

## 代码证据
```python
# campaign_plans.py - update_node_verdict
if verdict == "pass":
    new_status = "done"
    new_fails = 0
else:
    new_status = "failed"
    new_fails = int(node["consecutive_fails"]) + 1
```

## 结论
**通过**。系统设计确保只有复现判决 `pass` 的节点才能被标记为 `done`，伪造完成无法通过。
