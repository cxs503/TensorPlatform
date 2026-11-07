# 错误码表（契约先行冻结件 v1.0.0）

| HTTP | code | 含义 | 触发条件 | 客户端动作 |
|---|---|---|---|---|
| 401 | UNAUTHENTICATED | 未认证 | 无/非法 Bearer token | 重新登录 |
| 403 | TENANT_MISMATCH | 跨租户或角色不足 | 资源 tenant ≠ 调用方 tenant，或无 `certification.viewer` | 检查租户上下文 |
| 404 | CERT_NOT_FOUND | 认证记录不存在 | certification_id 无记录 | 核对 id |
| 404 | JOB_NOT_FOUND | job 不存在 | job_id 无记录或不属于该认证 | 核对 id |
| 409 | CERT_NOT_EXPORTABLE | 不可导出 | 认证状态 ≠ CERTIFIED | 等待状态机推进 |
| 410 | EXPORT_EXPIRED | 导出已过期 | status=expired（保留期 30 天） | 重新创建导出 |
| 422 | INVALID_PARAMS | 参数非法 | format ∉ {json, zip} 等 | 修正参数 |
| 429 | RATE_LIMITED | 限流 | 超租户速率配额 | 退避重试 |
| 507 | QUOTA_EXCEEDED | 配额超限 | 租户活跃导出 > 500 MB | 等待过期清扫或清理 |
| 500 | INTERNAL | 内部错误 | 未捕获异常（审计留痕） | 上报 |

纪律：实现面新增错误码必须先改本表（契约变更评审），禁止私加。
