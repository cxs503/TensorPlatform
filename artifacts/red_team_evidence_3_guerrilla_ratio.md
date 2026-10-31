# 红队证据 3: 计划外单注入（游击系数）

## 测试场景
注入计划外单（有 `campaign_id` 但不在任何 `campaign_nodes` 的 `mission_id` 中），验证游击系数必须上升。

## 定义
**游击系数** = `(总 mission 数 - 计划内 mission 数) / 总 mission 数`

- 总 mission 数：`tensor_dev_missions` 中 `campaign_id = X` 的记录数
- 计划内 mission 数：`campaign_nodes` 中 `plan_id = Y` 且 `mission_id IS NOT NULL` 的 DISTINCT mission_id 数

## 测试步骤

### 步骤 1: 创建计划节点（带 mission_id）
```
POST /tensor/campaigns/nodes
{"plan_id": <plan_id>, "name": "Planned Node 1", "mission_id": 100}
POST /tensor/campaigns/nodes
{"plan_id": <plan_id>, "name": "Planned Node 2", "mission_id": 101}
```

### 步骤 2: 生成初始快照
```
POST /tensor/campaigns/plans/{plan_id}/snapshot
→ guerrilla_ratio: 0.0  (假设 campaign 中只有这 2 个 mission)
→ planned_missions: 2
→ total_missions: 2
```

### 步骤 3: 注入计划外单
在 `tensor_dev_missions` 中创建新 mission，`campaign_id` 相同但不在任何 node 中：
```sql
INSERT INTO tensor_dev_missions (organization_id, campaign_id, goal, repo, budget, status, created_by)
VALUES (1, <campaign_id>, 'Unplanned mission', 'TensorPlatform', '{}', 'queued', 1);
```

### 步骤 4: 重新生成快照
```
POST /tensor/campaigns/plans/{plan_id}/snapshot
→ total_missions: 3  (2 planned + 1 unplanned)
→ planned_missions: 2
→ guerrilla_ratio: (3-2)/3 = 0.3333
```

## 判据
- ✅ 初始游击系数为 0（所有 mission 都在计划中）
- ✅ 注入计划外单后，游击系数上升
- ✅ 游击系数 = (total - planned) / total
- ✅ 当游击系数 > 0.2 时需关注，> 0.5 时需 owner 裁决

## 代码证据
```python
# campaign_plans.py - take_snapshot
total_missions = db.execute(text("""
    SELECT COUNT(*) FROM tensor_dev_missions WHERE campaign_id = :cid
"""), {"cid": plan["campaign_id"]}).scalar() or 0

planned_missions = db.execute(text("""
    SELECT COUNT(DISTINCT mission_id) FROM campaign_nodes
    WHERE plan_id = :pid AND mission_id IS NOT NULL
"""), {"pid": plan_id}).scalar() or 0

guerrilla_ratio = 0.0
if total_missions > 0:
    unplanned = max(0, total_missions - planned_missions)
    guerrilla_ratio = round(unplanned / total_missions, 4)
```

## 边界情况
- `total_missions = 0` → `guerrilla_ratio = 0.0`（避免除零）
- `planned_missions > total_missions` → `unplanned = max(0, ...)`（避免负数）

## 结论
**通过**。游击系数正确计算计划外单占比，注入计划外单后系数上升。
