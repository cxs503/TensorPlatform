# Fix: fail_stage 未填充缺陷修复

## 问题
所有失败任务（158/158）的 `fail_stage` 字段为 NULL，导致无法结构化查询失败原因。

## 根因
`ops/tp_worker.py` 在调用 `/tensor/missions/{id}/complete` 时未传递 `fail_stage` 字段，
尽管后端 `CompleteRequest` 模型已定义该字段。

## 修复
在两个失败路径的 complete 调用中添加 `"fail_stage": "tool_loop"`:
- 第 642 行: worker 内部异常 → fail_stage = "tool_loop"
- 第 665 行: 证据不足（未调用 finish 或无改动）→ fail_stage = "tool_loop"

## 文件
- `ops/tp_worker.py` (2 行新增)

## 验证
修复后新失败任务将正确填充 fail_stage 字段，可通过 SQL 查询:
```sql
SELECT fail_stage, count(*) FROM tensor_dev_missions WHERE status='failed' GROUP BY fail_stage;
```
