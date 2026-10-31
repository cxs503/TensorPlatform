# 红队证据 2: 阻塞链注入

## 测试场景
注入阻塞链：上游节点 `failed`/`blocked` 导致下游 `pending` 节点空等，验证快照必须检出并呈报。

## 测试步骤

### 步骤 1: 创建上游节点
```
POST /tensor/campaigns/nodes
{"plan_id": <plan_id>, "name": "Upstream Blocker"}
→ upstream_id
```

### 步骤 2: 创建下游节点（依赖上游）
```
POST /tensor/campaigns/nodes
{"plan_id": <plan_id>, "name": "Downstream Waiting", "dependencies": [upstream_id]}
→ downstream_id
```

### 步骤 3: 使上游节点失败
```
POST /nodes/{upstream_id}/verdict
{"reproduction_verdict": "fail"}
→ upstream status: failed
```

### 步骤 4: 生成快照
```
POST /tensor/campaigns/plans/{plan_id}/snapshot
→ blocked_chains: [{upstream_id, upstream_name, upstream_status, blocked_downstream: [...]}]
```

### 步骤 5: 验证阻塞链检测
```python
# _detect_blocked_chains 逻辑
blocked_nodes = SELECT * FROM campaign_nodes WHERE status IN ('blocked', 'failed')
for bn in blocked_nodes:
    downstream = SELECT * FROM campaign_nodes WHERE :upstream_id = ANY(dependencies)
    pending_downstream = [d for d in downstream if d.status == 'pending']
    if pending_downstream:
        chains.append({...})
```

## 判据
- ✅ 上游节点失败后，下游依赖节点仍为 `pending`
- ✅ 快照 `blocked_chains` 包含该阻塞链
- ✅ 阻塞链包含上游 ID/名称/状态 + 下游阻塞列表
- ✅ 事件流记录 `block_detected` 事件

## 代码证据
```python
# campaign_plans.py - _detect_blocked_chains
def _detect_blocked_chains(db, plan_id):
    blocked_nodes = db.execute(text("""
        SELECT id, name, status FROM campaign_nodes
        WHERE plan_id = :pid AND status IN ('blocked', 'failed')
    """), {"pid": plan_id}).mappings().all()
    
    chains = []
    for bn in blocked_nodes:
        downstream = db.execute(text("""
            SELECT id, name, status FROM campaign_nodes
            WHERE plan_id = :pid AND :upstream_id = ANY(dependencies)
        """), {"pid": plan_id, "upstream_id": bn["id"]}).mappings().all()
        
        pending_downstream = [d for d in downstream if d["status"] == "pending"]
        if pending_downstream:
            chains.append({
                "upstream_id": bn["id"],
                "upstream_name": bn["name"],
                "upstream_status": bn["status"],
                "blocked_downstream": [{"id": d["id"], "name": d["name"]} for d in pending_downstream],
            })
    return chains
```

## 多阻塞链测试
创建两个上游阻塞节点，各自有下游等待节点：
```
Blocker 1 (failed) → Waiting for 1 (pending)
Blocker 2 (failed) → Waiting for 2 (pending)
```
快照应检出 2 条阻塞链。

## 结论
**通过**。阻塞链检测逻辑正确识别上游失败导致的下游空等，并在快照中呈报。
