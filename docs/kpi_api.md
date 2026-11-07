# KPI Guide Tracker API 文档

## 概述

`GET /api/v1/tensor/kpi/guide_tracker` 返回 12 项 KPI 实时值，从 `tensor_dev_missions`、`tensor_reports`、`ship_samples`、`metered_usage`、`integrity_events` 表计算。

## 端点

```
GET /api/v1/tensor/kpi/guide_tracker
```

**认证**：需要 Bearer Token（`require_auth`）

## 响应格式

```json
{
  "kpis": [
    {
      "key": "total_missions",
      "label": "任务总数",
      "current": 42,
      "target": 1000,
      "pct": 4.2
    },
    ...
  ],
  "count": 12,
  "cached": false
}
```

每项 KPI 包含：
- `key`：KPI 唯一标识符
- `label`：中文标签
- `current`：当前实际值
- `target`：预设目标值
- `pct`：完成百分比（current / target × 100，target=0 时为 0）

## 12 项 KPI 定义

| # | key | label | 数据来源 | target |
|---|-----|-------|---------|--------|
| 1 | total_missions | 任务总数 | `COUNT(*) FROM tensor_dev_missions` | 1000 |
| 2 | active_missions | 进行中任务 | `COUNT(*) WHERE status IN ('claimed','running')` | 50 |
| 3 | completed_missions | 已完成任务 | `COUNT(*) WHERE status = 'completed'` | 500 |
| 4 | failed_missions | 失败任务 | `COUNT(*) WHERE status = 'failed'` | 20 |
| 5 | total_reports | 报告总数 | `COUNT(*) FROM tensor_reports` | 800 |
| 6 | published_reports | 已发布报告 | `COUNT(*) WHERE status = 'published'` | 400 |
| 7 | total_samples | 样本总数 | `COUNT(*) FROM ship_samples` | 2000 |
| 8 | passed_samples | 通过验证样本 | `COUNT(*) WHERE passed = true` | 1500 |
| 9 | mission_success_rate | 任务成功率(%) | `completed / (completed+failed) × 100` | 90 |
| 10 | avg_mission_duration | 平均任务耗时(秒) | `AVG(completed_at - created_at)` | 3600 |
| 11 | total_tool_calls | 总工具调用次数 | `SUM(tool_calls) FROM metered_usage` | 50000 |
| 12 | integrity_events | 完整性事件(未解决) | `COUNT(*) WHERE resolved = false` | 5 |

## 缓存策略

- **类型**：内存缓存（进程内 dict）
- **TTL**：300 秒（5 分钟）
- **线程安全**：使用 `threading.Lock` 保护
- **缓存命中**：响应中 `cached: true`
- **缓存未命中**：响应中 `cached: false`，重新计算并写入缓存

### 为什么需要缓存

全表扫描（尤其 `tensor_dev_missions` 和 `tensor_reports` 在大表时）可能导致 API 超时。5 分钟 TTL 在实时性和性能之间取得平衡。

### 优化项（honest-FAIL 标注）

若生产环境中表数据量持续增长导致 5 分钟 TTL 仍不足，可考虑：
1. 增加 TTL 至 10-15 分钟
2. 引入 Redis 等外部缓存
3. 对高频查询列添加数据库索引
4. 使用物化视图预聚合

## 测试

```bash
cd backend
pytest tests/test_kpi_api.py -v
```

测试覆盖：
1. 返回 12 项 KPI
2. 每项含 current/target/pct 字段
3. KPI 键集合完整性
4. 数值与 DB 直接查询一致（误差=0）
5. pct 计算正确性
6. 缓存机制验证
7. 认证要求验证

## 文件位置

- API 代码：`backend/api/kpi.py`
- 测试：`backend/tests/test_kpi_api.py`
- 路由注册：`backend/api/domain/tensor.py`（已包含 kpi_router）
