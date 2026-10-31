# Report Gate (RPT-GATE) - 报告门禁系统

## 概述

报告门禁系统确保每个完结的 mission 都有论文级进展报告，解决 10-31 完结单零报告问题。

**核心原则**：报告成为欠账显性化，不阻断交付流。

## 功能

### 1. Finish/Complete 端点接线

**位置**: `backend/api/tensor_missions.py` - `complete_mission()`

**逻辑**:
- `CompleteRequest` 新增 `report_id` 字段（可选）
- 当 `status="succeeded"` 且 `report_id=None` 时：
  - 状态照旧变为 `succeeded`（不阻断交付）
  - 设置 `report_owed=true` 标记
  - 进入补账队列
- 当 `status="succeeded"` 且 `report_id=<id>` 时：
  - 设置 `report_owed=false`
  - 关联 `report_id` 到 mission

**代码片段**:
```python
# Report gate: succeeded missions without report_id get report_owed flag
report_owed = (body.status == "succeeded" and body.report_id is None)
db.execute(
    text("""
        UPDATE tensor_dev_missions
        SET status = :st, finished_at = now(), claim_token = NULL,
            result = result || CAST(:summary AS jsonb),
            report_owed = :report_owed,
            report_id = :report_id
        WHERE id = :mid
    """),
    {"st": body.status, "mid": mission_id,
     "summary": _dumps({**body.summary, "evidence": evidence}),
     "report_owed": report_owed,
     "report_id": body.report_id},
)
```

### 2. 欠账队列 API

**端点**:
- `GET /tensor/missions/report-owed/queue` - 列出所有 report_owed 的 mission
- `GET /tensor/missions/report-owed/count` - 返回欠账数量（用于看板徽章）
- `POST /tensor/missions/{id}/link-report` - 关联报告到 mission，清除欠账标记

**响应示例**:
```json
{
  "items": [
    {
      "mission_id": 123,
      "goal": "优化船舶阻力...",
      "repo": "TensorLBM",
      "headline": "阻力优化完成",
      "finished_at": "2026-10-30T10:00:00Z",
      "days_without_report": 2.5
    }
  ],
  "total": 1
}
```

**消费方**: `report_watch` 守护进程直接消费此队列，派发补报单。

### 3. Mission 模板 FOOTER 注入

**位置**: `tools/agent/dispatch_helper.py`

**机制**:
- 在 `REPORT_GATE_FOOTER` 常量中定义报告要求文本
- 在 `main()` 中注入到 goal 尾部
- 平台派单侧统一，不依赖派单人自觉

**注入内容**:
```
★报告纪律（owner 常设指令，每单必报）：finish 前必须发布论文级进展报告...
六要素=①案例介绍；②怎么做的；③计算结果；④结果图；⑤注意点；⑥误差判定...
报告未发=本单未完成（守望线会自动派补报单并记欠账）。
```

### 4. Report QA 论文级最低字段检查

**位置**: `backend/api/tensor_reports.py`

**端点**:
- `POST /tensor/reports/qa/check` - 对单个报告执行 QA 检查
- `GET /tensor/reports/qa/pending` - 列出待 QA 的报告

**检查项**（缺一打回）:
1. **物理表述** (physics_statement): 物理背景/案例介绍/几何参数
2. **判据可溯源** (traceable_criteria): 方法/流程 + 验证/对标/误差
3. **图** (figures): 场量云图/比较曲线（或纯文档任务写明无图理由）
4. **局限** (limitations): 踩过的坑/适用范围/展望

**响应示例**:
```json
{
  "report_id": 456,
  "qa_status": "rejected",
  "passed": false,
  "issues": [
    "missing_physics_statement: 缺少物理背景/案例介绍",
    "missing_validation: 缺少验证/对标"
  ],
  "checks": {
    "physics_statement": false,
    "method": true,
    "validation": false,
    "figures": true,
    "limitations": true
  }
}
```

## 数据库迁移

**迁移文件**: `backend/migrations/095_report_gate.sql`

**新增字段**:
- `tensor_dev_missions.report_owed` (BOOLEAN) - 是否欠报告
- `tensor_dev_missions.report_id` (INT) - 关联的报告 ID
- `tensor_reports.qa_status` (VARCHAR) - QA 状态 (pending/passed/rejected)
- `tensor_reports.qa_rejection_reason` (TEXT) - 打回原因
- `tensor_reports.qa_checked_at` (TIMESTAMPTZ) - QA 检查时间

**索引**:
- `ix_tensor_missions_report_owed` - 欠账队列查询优化
- `ix_tensor_reports_qa_pending` - QA 待审队列查询优化

**执行迁移**:
```bash
cd backend
python migrations/run_migration.py 095_report_gate.sql
```

## 使用流程

### Agent 侧（完结 mission）

```bash
# 1. 发布报告
curl -X POST $TENSOR_API_BASE/tensor/reports \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d @/tmp/report.json

# 响应包含 report_id
# {"id": 456, ...}

# 2. 完结 mission 时传入 report_id
curl -X POST $TENSOR_API_BASE/tensor/missions/123/complete \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "claim_token": "...",
    "status": "succeeded",
    "acceptance": true,
    "report_id": 456,
    "summary": {...}
  }'
```

### Owner 侧（查看欠账）

```bash
# 查看欠账队列
curl $TENSOR_API_BASE/tensor/missions/report-owed/queue \
  -H "Authorization: Bearer $TOKEN"

# 查看欠账数量
curl $TENSOR_API_BASE/tensor/missions/report-owed/count \
  -H "Authorization: Bearer $TOKEN"

# 关联报告到 mission（补报后）
curl -X POST $TENSOR_API_BASE/tensor/missions/123/link-report \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"report_id": 456}'
```

### QA 检查

```bash
# 检查报告质量
curl -X POST $TENSOR_API_BASE/tensor/reports/qa/check \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"report_id": 456}'

# 查看待 QA 报告
curl $TENSOR_API_BASE/tensor/reports/qa/pending \
  -H "Authorization: Bearer $TOKEN"
```

## 机器判据验证

### 1. Finish 无报告单确实入队

```python
# 端到端测试
# 1. 创建 mission
# 2. Complete without report_id
# 3. 查询 report-owed/queue，确认 mission 在队列中
# 4. 查询 report-owed/count，确认计数+1
```

**验证脚本**: `backend/tests/test_report_gate_standalone.py`

### 2. 欠账数 API 可查且与 SQL 一致

```sql
-- 直接查询
SELECT COUNT(*) FROM tensor_dev_missions
WHERE report_owed = TRUE AND status = 'succeeded';

-- API 查询
GET /tensor/missions/report-owed/count
-- 应返回相同数值
```

### 3. Report QA 对缺字段样例报告打回

```python
# 红队自检
markdown = """
## 方法
使用了LBM方法。
## 验证
误差<3%。
"""
# 缺少：物理背景、图表、局限
result = _check_report_quality(markdown, [])
assert not result["passed"]
assert len(result["issues"]) >= 3
```

**验证脚本**: `backend/tests/test_report_gate_standalone.py::test_missing_*`

## 测试

运行独立测试（无需数据库）:
```bash
cd backend
python tests/test_report_gate_standalone.py
```

预期输出:
```
✓ test_missing_physics passed
✓ test_missing_method passed
✓ test_missing_validation passed
✓ test_missing_figures passed
✓ test_missing_limitations passed
✓ test_complete_report_passes passed
✓ test_report_gate_logic: succeeded without report_id → report_owed=true
✓ test_report_gate_logic: succeeded with report_id → report_owed=false
✓ test_report_gate_logic: failed mission → no report_owed
✓ test_report_owed_queue_logic passed

✅ All tests passed!
```

## 禁止事项

❌ **禁止**：把报告要求做成只在 UI 提示不进数据流  
✅ **必须**：报告要求进入数据流（report_owed 标记 + 欠账队列 + QA 检查）

## 后续集成

### report_watch 守护进程

`report_watch` 应：
1. 定期轮询 `/tensor/missions/report-owed/queue`
2. 对 `days_without_report > 7` 的 mission 派发补报单
3. 补报单完成后调用 `/tensor/missions/{id}/link-report` 清除欠账

### 看板集成

前端看板应：
1. 显示 `report-owed/count` 作为徽章
2. 点击徽章展开欠账队列详情
3. 提供快捷入口关联已有报告

## 变更文件清单

- `backend/migrations/095_report_gate.sql` - 数据库迁移
- `backend/api/tensor_missions.py` - CompleteRequest + complete_mission + 欠账队列 API
- `backend/api/tensor_reports.py` - Report QA 检查 API
- `tools/agent/dispatch_helper.py` - FOOTER 注入
- `backend/tests/test_report_gate_standalone.py` - 独立测试
- `docs/REPORT_GATE.md` - 本文档

## 迁移说明

1. **执行数据库迁移**:
   ```bash
   cd backend
   python migrations/run_migration.py 095_report_gate.sql
   ```

2. **重启后端服务**:
   ```bash
   # 重启以加载新端点
   systemctl restart tensor-backend
   ```

3. **验证迁移**:
   ```bash
   # 检查新字段
   psql $DATABASE_URL -c "\d tensor_dev_missions" | grep report_owed
   psql $DATABASE_URL -c "\d tensor_reports" | grep qa_status
   ```

4. **运行测试**:
   ```bash
   cd backend
   python tests/test_report_gate_standalone.py
   ```

5. **端到端验证**:
   - 创建一个测试 mission
   - Complete without report_id
   - 查询 `/tensor/missions/report-owed/queue` 确认入队
   - 发布报告并关联，确认欠账清除

## 总结

报告门禁系统通过以下机制确保论文级报告：
1. **数据流强制**：finish 端点检查 report_id，缺失则标记 report_owed
2. **显性化欠账**：欠账队列 API + 看板徽章，让缺失报告可见
3. **平台侧注入**：dispatch_helper 统一注入 FOOTER，不依赖派单人自觉
4. **质量门禁**：report_qa 检查四要素，缺一打回

**不阻断交付流**：mission 可以正常完结，但欠账显性化，由 report_watch 跟进补报。
