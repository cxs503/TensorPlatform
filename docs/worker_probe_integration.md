# Worker Probe 接线文档 (m597)

## 概述

Worker 回查探针 (`GET /tensor/missions/{id}/probe`) 是一个轻量级端点，供 worker 在工具循环中定期回查 mission 状态。如果 mission 不再是 `running` 或 `claimed`，worker 应立即中止循环并释放槽位。

## 端点

```
GET /api/v1/tensor/missions/{mission_id}/probe
Authorization: Bearer <token>
```

### 响应

```json
{
    "id": 123,
    "status": "running",
    "should_continue": true,
    "stop_signal": null
}
```

当 `should_continue` 为 `false` 时，`stop_signal` 包含中止原因（如 `status_is_suspended`、`status_is_stopped`）。

## Worker 侧接线

### tp_worker 集成点

在 `tp_worker` 的工具循环中，每 N 次工具调用后调用探针：

```python
# tp_worker/tool_loop.py (示意)
PROBE_INTERVAL = 5  # 每 5 次工具调用回查一次

class ToolLoop:
    def __init__(self, mission_id, claim_token, api_client):
        self.mission_id = mission_id
        self.claim_token = claim_token
        self.api = api_client
        self.call_count = 0

    def should_continue(self) -> bool:
        """Check if worker should continue its tool loop."""
        self.call_count += 1
        if self.call_count % PROBE_INTERVAL != 0:
            return True
        
        resp = self.api.get(f"/tensor/missions/{self.mission_id}/probe")
        if resp.status_code != 200:
            return True  # 网络问题，继续运行
        
        data = resp.json()
        if not data["should_continue"]:
            logger.warning(
                f"Mission {self.mission_id} probe says stop: "
                f"status={data['status']}, signal={data['stop_signal']}"
            )
            return False
        return True

    def run(self):
        while self.should_continue():
            tool_call = self.get_next_tool_call()
            result = self.execute_tool(tool_call)
            self.report_result(result)
        
        # Loop exited: either naturally or probe said stop
        self.release_slot()
```

### 心跳集成

探针与心跳互补：
- **心跳** (`POST /heartbeat`)：worker → 平台，报告存活 + 预算使用
- **探针** (`GET /probe`)：worker → 平台，查询是否应继续

建议心跳间隔 15s，探针间隔 5 次工具调用（约 30-60s）。

### 状态语义

| 平台状态 | should_continue | 说明 |
|---------|----------------|------|
| `running` | `true` | 正常运行 |
| `claimed` | `true` | 已认领，即将运行 |
| `suspended` | `false` | 被挂起，预算冻结 |
| `stopped` | `false` | 被停止 |
| `failed` | `false` | 已失败 |
| `succeeded` | `false` | 已成功 |
| `orphan` | `false` | 被标记为孤儿 |
| `revoked` | `false` | 被撤销 |

## 安全

- 探针端点需要认证 (`require_auth`)
- 只返回状态信息，不暴露敏感数据
- 轻量级：无行锁，无副作用

## 故障场景

1. **Owner 停单不生效**：Owner 调用 `/stop` → 状态变 `stopped` → worker 下次探针发现 `should_continue=false` → 中止循环
2. **挂起后继续烧预算**：Owner 调用 `/suspend` → 状态变 `suspended` → worker 探针发现停止 → 预算时钟已冻结
3. **僵尸队列**：Orphan 检测自动标记 >48h 无领取的 queued 单为 `orphan` → 探针返回停止
