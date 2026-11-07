# 日志摘录：重试导致重复处理事故

## 日志来源

- **系统**：船舶阻力计算平台
- **组件**：worker-1, worker-2, RabbitMQ, PostgreSQL
- **时间范围**：2026-10-28 14:23:00 - 14:25:00 UTC
- **日志级别**：INFO, WARNING, ERROR

---

## 关键日志片段

### 片段 1：任务首次处理（worker-1）

```
[2026-10-28 14:23:17.123] [worker-1] [INFO] Received task #A1B2C3 from queue
[2026-10-28 14:23:17.125] [worker-1] [INFO] Starting resistance calculation for task #A1B2C3
[2026-10-28 14:23:17.126] [worker-1] [INFO] Input parameters: L=150m, B=20m, T=8m, Cb=0.65, V=14knots
[2026-10-28 14:23:18.500] [worker-1] [INFO] Geometry validation passed
[2026-10-28 14:23:20.100] [worker-1] [INFO] Wave resistance calculation started
[2026-10-28 14:23:25.300] [worker-1] [INFO] Viscous resistance calculation started
[2026-10-28 14:23:28.700] [worker-1] [INFO] Database write started (connection pool: 45/100)
```

### 片段 2：数据库延迟导致超时

```
[2026-10-28 14:23:28.750] [worker-1] [WARNING] Database connection slow: 800ms (threshold: 100ms)
[2026-10-28 14:23:29.500] [worker-1] [WARNING] Database write still in progress (elapsed: 800ms)
[2026-10-28 14:23:30.000] [worker-1] [WARNING] Task #A1B2C3 approaching timeout (28.8s / 30s)
[2026-10-28 14:23:31.200] [worker-1] [INFO] Database write completed (total: 2500ms)
[2026-10-28 14:23:31.250] [worker-1] [INFO] Task #A1B2C3 calculation completed
[2026-10-28 14:23:31.251] [worker-1] [INFO] Sending ACK for task #A1B2C3
```

### 片段 3：RabbitMQ 超时重投

```
[2026-10-28 14:23:47.000] [rabbitmq] [WARNING] Task #A1B2C3 ACK timeout (30s elapsed)
[2026-10-28 14:23:47.001] [rabbitmq] [INFO] Requeuing task #A1B2C3 (retry 1/3)
[2026-10-28 14:23:47.050] [rabbitmq] [INFO] Delivering task #A1B2C3 to worker-2
```

**注意**：worker-1 在 14:23:31 发送了 ACK，但 RabbitMQ 在 14:23:47 认为超时。这是因为 ACK 消息在网络中延迟，或者 RabbitMQ 的超时计时器在任务投递时就开始，而不是在 worker 开始处理时开始。

### 片段 4：重复处理（worker-2）

```
[2026-10-28 14:23:47.100] [worker-2] [INFO] Received task #A1B2C3 from queue
[2026-10-28 14:23:47.101] [worker-2] [INFO] Starting resistance calculation for task #A1B2C3
[2026-10-28 14:23:47.102] [worker-2] [WARNING] Task #A1B2C3 already exists in database (created_at: 14:23:31)
[2026-10-28 14:23:47.103] [worker-2] [INFO] Proceeding with duplicate processing (no idempotency check)
[2026-10-28 14:23:48.500] [worker-2] [INFO] Geometry validation passed
[2026-10-28 14:23:50.100] [worker-2] [INFO] Wave resistance calculation started
[2026-10-28 14:23:55.300] [worker-2] [INFO] Viscous resistance calculation started
[2026-10-28 14:23:58.700] [worker-2] [INFO] Database write started (connection pool: 46/100)
[2026-10-28 14:23:59.500] [worker-2] [INFO] Database write completed (total: 800ms)
[2026-10-28 14:23:59.501] [worker-2] [INFO] Task #A1B2C3 calculation completed (duplicate)
[2026-10-28 14:23:59.502] [worker-2] [INFO] Sending ACK for task #A1B2C3
```

### 片段 5：数据库重复记录

```
[2026-10-28 14:24:00.000] [postgresql] [INFO] INSERT INTO task_results (task_id, result, created_at)
                         VALUES ('A1B2C3', '{"Ct": 0.00342, ...}', '2026-10-28 14:23:31')
[2026-10-28 14:24:00.001] [postgresql] [INFO] Row inserted successfully (id: 12345)

[2026-10-28 14:24:00.500] [postgresql] [INFO] INSERT INTO task_results (task_id, result, created_at)
                         VALUES ('A1B2C3', '{"Ct": 0.00342, ...}', '2026-10-28 14:23:59')
[2026-10-28 14:24:00.501] [postgresql] [INFO] Row inserted successfully (id: 12346)
[2026-10-28 14:24:00.502] [postgresql] [WARNING] Duplicate task_id detected: A1B2C3 (2 rows)
```

**注意**：数据库没有唯一约束，所以两次 INSERT 都成功了。

---

## 日志分析

### 问题识别

1. **ACK 时机问题**：
   - worker-1 在 14:23:31 发送 ACK
   - RabbitMQ 在 14:23:47 认为超时（16 秒后）
   - 说明 ACK 消息丢失或延迟，或者 RabbitMQ 的超时计时器配置有问题

2. **缺乏幂等性检查**：
   - worker-2 在 14:23:47 收到任务时，发现数据库中已存在该任务
   - 但日志显示 `Proceeding with duplicate processing (no idempotency check)`
   - 说明代码中没有幂等性保护

3. **数据库约束缺失**：
   - 两次 INSERT 都成功
   - 日志显示 `Duplicate task_id detected` 但只是 WARNING，没有阻止插入
   - 说明没有唯一约束

### 根因总结

1. RabbitMQ ACK 超时机制与任务实际执行时间不匹配
2. 任务处理逻辑缺乏幂等性保护
3. 数据库没有唯一约束防止重复插入

---

## 修复后的日志（预期）

```
[2026-10-28 14:23:17.123] [worker-1] [INFO] Received task #A1B2C3 from queue
[2026-10-28 14:23:17.124] [worker-1] [INFO] Marking task #A1B2C3 as processing
[2026-10-28 14:23:17.125] [worker-1] [INFO] Sending ACK for task #A1B2C3 (immediate ACK)
[2026-10-28 14:23:17.126] [worker-1] [INFO] Starting resistance calculation for task #A1B2C3
...
[2026-10-28 14:23:31.250] [worker-1] [INFO] Task #A1B2C3 calculation completed
[2026-10-28 14:23:31.251] [worker-1] [INFO] Marking task #A1B2C3 as completed

[2026-10-28 14:23:47.100] [worker-2] [INFO] Received task #A1B2C3 from queue
[2026-10-28 14:23:47.101] [worker-2] [INFO] Checking if task #A1B2C3 is already processing
[2026-10-28 14:23:47.102] [worker-2] [WARNING] Task #A1B2C3 already completed, skipping duplicate
[2026-10-28 14:23:47.103] [worker-2] [INFO] Sending ACK for duplicate task #A1B2C3
```

**关键改进**：
1. 立即 ACK，防止超时重投
2. 本地状态追踪，检测重复
3. 跳过重复处理，避免资源浪费
