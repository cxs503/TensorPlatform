import http from './request'

// 工单 API
export const workOrderApi = {
  getList: (params?: any) =>
    http.get('/work-orders', params),

  get: (id: string) =>
    http.get(`/work-orders/${id}`),

  create: (data: any) =>
    http.post('/work-orders', data),

  assign: (id: string, assignedTo: string) =>
    http.post(`/work-orders/${id}/assign`, null, { params: { assigned_to: assignedTo } }),

  batchAssign: (workOrderIds: string[], assignedTo: string) =>
    http.post('/work-orders/batch-assign', { work_order_ids: workOrderIds, assigned_to: assignedTo }),

  start: (id: string) =>
    http.post(`/work-orders/${id}/start`),

  addComment: (id: string, comment: string, user: string) =>
    http.post(`/work-orders/${id}/comment`, { comment, user }),

  updateProgress: (id: string, progress: number, comment?: string) =>
    http.put(`/work-orders/${id}/progress`, { progress, comment }),

  complete: (id: string, data: any) =>
    http.post(`/work-orders/${id}/complete`, data),

  cancel: (id: string, reason: string) =>
    http.post(`/work-orders/${id}/cancel`, null, { params: { reason } }),

  getMy: (user: string) =>
    http.get(`/work-orders/my/${user}`),

  getStats: () =>
    http.get('/work-orders/stats/overview'),

  getOverdue: () =>
    http.get('/work-orders/stats/overdue')
}

// 远程运维 API
export const remoteOpsApi = {
  // 远程操作
  createOperation: (data: any) =>
    http.post('/remote-ops/operations', data),

  executeOperation: (operationId: string) =>
    http.post(`/remote-ops/operations/${operationId}/execute`),

  getOperation: (operationId: string) =>
    http.get(`/remote-ops/operations/${operationId}`),

  listOperations: (params?: any) =>
    http.get('/remote-ops/operations', params),

  // 远程会话
  createSession: (data: any) =>
    http.post('/remote-ops/sessions', data),

  closeSession: (sessionId: string) =>
    http.post(`/remote-ops/sessions/${sessionId}/close`),

  executeCommand: (sessionId: string, command: string) =>
    http.post(`/remote-ops/sessions/${sessionId}/command`, { command }),

  listSessions: (deviceId?: string) =>
    http.get('/remote-ops/sessions', { device_id: deviceId }),

  // 设备快照
  createSnapshot: (deviceId: string) =>
    http.post(`/remote-ops/devices/${deviceId}/snapshot`),

  getSnapshot: (snapshotId: string) =>
    http.get(`/remote-ops/snapshots/${snapshotId}`),

  listSnapshots: (deviceId?: string) =>
    http.get('/remote-ops/snapshots', { device_id: deviceId }),

  // 批量操作
  batchOperation: (deviceIds: string[], operationType: string, params?: any) =>
    http.post('/remote-ops/batch', { device_ids: deviceIds, operation_type: operationType, params }),

  // 快捷操作
  rebootDevice: (deviceId: string, operator?: string) =>
    http.post(`/remote-ops/devices/${deviceId}/reboot`, null, { params: { operator } }),

  stopDevice: (deviceId: string, operator?: string) =>
    http.post(`/remote-ops/devices/${deviceId}/stop`, null, { params: { operator } }),

  startDevice: (deviceId: string, operator?: string) =>
    http.post(`/remote-ops/devices/${deviceId}/start`, null, { params: { operator } }),

  diagnoseDevice: (deviceId: string) =>
    http.post(`/remote-ops/devices/${deviceId}/diagnose`)
}

// RBAC API
export const rbacApi = {
  // 角色管理
  getRoles: () =>
    http.get('/rbac/roles'),

  getRole: (id: string) =>
    http.get(`/rbac/roles/${id}`),

  createRole: (data: any) =>
    http.post('/rbac/roles', data),

  updateRole: (id: string, data: any) =>
    http.put(`/rbac/roles/${id}`, data),

  deleteRole: (id: string) =>
    http.delete(`/rbac/roles/${id}`),

  // 权限管理
  getPermissions: () =>
    http.get('/rbac/permissions'),

  getPermission: (id: string) =>
    http.get(`/rbac/permissions/${id}`),

  createPermission: (data: any) =>
    http.post('/rbac/permissions', data),

  updatePermission: (id: string, data: any) =>
    http.put(`/rbac/permissions/${id}`, data),

  deletePermission: (id: string) =>
    http.delete(`/rbac/permissions/${id}`),

  // 角色权限分配
  getRolePermissions: (roleId: string) =>
    http.get(`/rbac/roles/${roleId}/permissions`),

  assignPermissions: (roleId: string, permissionIds: string[]) =>
    http.post(`/rbac/roles/${roleId}/permissions`, { permission_ids: permissionIds }),

  // 用户角色分配
  getUserRoles: () =>
    http.get('/rbac/user-roles'),

  getUserRolesByUser: (userId: number) =>
    http.get(`/rbac/users/${userId}/roles`),

  assignRoles: (userId: number, roleIds: string[]) =>
    http.post(`/rbac/users/${userId}/roles`, { role_ids: roleIds }),

  removeRole: (userId: number, roleId: string) =>
    http.delete(`/rbac/users/${userId}/roles/${roleId}`),

  // 审计日志
  getAuditLogs: (params?: any) =>
    http.get('/rbac/audit-logs', params)
}

// 审计日志 API
export const auditApi = {
  log: (data: any) =>
    http.post('/audit/log', data),

  search: (params?: any) =>
    http.get('/audit/search', params),

  getUserActivity: (userId: string, days?: number) =>
    http.get(`/audit/user/${userId}/activity`, { days }),

  getSecurityEvents: (days?: number) =>
    http.get('/audit/security', { days }),

  export: (params: { start_date: string; end_date: string; format?: string }) =>
    http.get('/audit/export', params),

  getActions: () =>
    http.get('/audit/actions')
}

// 密钥管理 API (KMS)
export const kmsApi = {
  // 密钥管理
  getKeys: (params?: { user_id?: string; status?: string; usage?: string }) =>
    http.get('/kms/keys', params),

  getKey: (keyId: string) =>
    http.get(`/kms/keys/${keyId}`),

  createKey: (data: any) =>
    http.post('/kms/keys', data),

  updateKey: (keyId: string, data: any) =>
    http.put(`/kms/keys/${keyId}`, data),

  deleteKey: (keyId: string) =>
    http.delete(`/kms/keys/${keyId}`),

  // 密钥轮换
  rotateKey: (keyId: string) =>
    http.post(`/kms/keys/${keyId}/rotate`),

  getKeyVersions: (keyId: string) =>
    http.get(`/kms/keys/${keyId}/versions`),

  // 加密解密
  encrypt: (data: { key_id: string; plaintext: string; associated_data?: any }) =>
    http.post('/kms/encrypt', data),

  decrypt: (data: { key_id: string; ciphertext: string; version?: number }) =>
    http.post('/kms/decrypt', data),

  // 签名验签
  sign: (data: { key_id: string; data: string }) =>
    http.post('/kms/sign', data),

  verify: (data: { key_id: string; data: string; signature: string }) =>
    http.post('/kms/verify', data),

  // 统计
  getStats: () =>
    http.get('/kms/stats')
}

// 备份恢复 API
export const backupApi = {
  // 备份管理
  getBackups: (params?: any) =>
    http.get('/backup/backups', params),

  getBackup: (backupId: string) =>
    http.get(`/backup/backups/${backupId}`),

  createBackup: (data: any) =>
    http.post('/backup/backups', data),

  deleteBackup: (backupId: string) =>
    http.delete(`/backup/backups/${backupId}`),

  // 恢复管理
  getRestores: (params?: any) =>
    http.get('/backup/restores', params),

  getRestore: (restoreId: string) =>
    http.get(`/backup/restores/${restoreId}`),

  createRestore: (data: any) =>
    http.post('/backup/restores', data),

  // 备份计划
  getSchedules: (enabled?: boolean) =>
    http.get('/backup/schedules', { enabled }),

  createSchedule: (data: any) =>
    http.post('/backup/schedules', data),

  updateSchedule: (scheduleId: string, data: any) =>
    http.put(`/backup/schedules/${scheduleId}`, data),

  deleteSchedule: (scheduleId: string) =>
    http.delete(`/backup/schedules/${scheduleId}`),

  // 备份策略
  getPolicies: () =>
    http.get('/backup/policies'),

  createPolicy: (data: any) =>
    http.post('/backup/policies', data),

  // 统计
  getStats: () =>
    http.get('/backup/stats')
}

// 租户管理 API
export const tenantApi = {
  getList: (params?: any) =>
    http.get('/tenants', params),

  get: (id: string) =>
    http.get(`/tenants/${id}`),

  create: (data: any) =>
    http.post('/tenants', data),

  update: (id: string, data: any) =>
    http.put(`/tenants/${id}`, data),

  delete: (id: string) =>
    http.delete(`/tenants/${id}`),

  // 配额
  getQuota: (id: string) =>
    http.get(`/tenants/${id}/quota`),

  updateQuota: (id: string, data: any) =>
    http.put(`/tenants/${id}/quota`, data),

  // 使用量
  getUsage: (id: string) =>
    http.get(`/tenants/${id}/usage`),

  // 当前用户租户
  getMyTenant: () =>
    http.get('/tenants/my-tenant')
}

// 配置中心 API
export const configApi = {
  // 命名空间
  getNamespaces: () =>
    http.get('/config/namespaces'),

  createNamespace: (data: any) =>
    http.post('/config/namespaces', data),

  // 配置管理
  getConfigs: (params?: any) =>
    http.get('/config/configs', params),

  getConfig: (id: string) =>
    http.get(`/config/configs/${id}`),

  createConfig: (data: any) =>
    http.post('/config/configs', data),

  updateConfig: (id: string, data: any) =>
    http.put(`/config/configs/${id}`, data),

  deleteConfig: (id: string) =>
    http.delete(`/config/configs/${id}`),

  releaseConfig: (id: string) =>
    http.post(`/config/configs/${id}/release`),

  rollbackConfig: (id: string, version?: number) =>
    http.post(`/config/configs/${id}/rollback`, { version }),

  // 版本管理
  getVersions: (configId: string) =>
    http.get(`/config/configs/${configId}/versions`),

  getVersion: (configId: string, version: number) =>
    http.get(`/config/configs/${configId}/versions/${version}`),

  // 监听器
  getListeners: (configId: string) =>
    http.get(`/config/configs/${configId}/listeners`),

  addListener: (configId: string, data: any) =>
    http.post(`/config/configs/${configId}/listeners`, data),

  removeListener: (configId: string, listenerId: string) =>
    http.delete(`/config/configs/${configId}/listeners/${listenerId}`),

  // 统计
  getStats: () =>
    http.get('/config/stats')
}

// GPU调度 API
export const gpuApi = {
  // GPU管理
  getGpus: (status?: string) =>
    http.get('/gpu/gpus', { status }),

  getGpu: (id: string) =>
    http.get(`/gpu/gpus/${id}`),

  registerGpu: (data: { gpu_id: string; name: string; total_memory_mb: number; device_index?: number }) =>
    http.post('/gpu/gpus', data),

  unregisterGpu: (id: string) =>
    http.delete(`/gpu/gpus/${id}`),

  // GPU分配
  allocate: (data: { job_id: string; memory_mb?: number; allocation_type?: string; user_id?: string }) =>
    http.post('/gpu/allocate', data),

  release: (jobId: string) =>
    http.post(`/gpu/release/${jobId}`),

  getJobAllocation: (jobId: string) =>
    http.get(`/gpu/jobs/${jobId}`),

  // vGPU管理
  getVgpus: (userId?: string) =>
    http.get('/gpu/vgpus', { user_id: userId }),

  createVgpu: (data: { name: string; gpu_id: string; memory_mb: number; user_id?: string }) =>
    http.post('/gpu/vgpus', data),

  deleteVgpu: (id: string) =>
    http.delete(`/gpu/vgpus/${id}`),

  // 统计
  getStats: () =>
    http.get('/gpu/stats')
}

// 缓存管理 API
export const cacheApi = {
  // 集群管理
  getClusters: () =>
    http.get('/cache/clusters'),

  createCluster: (data: any) =>
    http.post('/cache/clusters', data),

  // 缓存操作
  get: (key: string, namespace?: string) =>
    http.get('/cache', { key, namespace: namespace || 'default' }),

  set: (data: any) =>
    http.post('/cache', data),

  delete: (key: string, namespace?: string) =>
    http.delete('/cache', { key, namespace: namespace || 'default' }),

  exists: (key: string, namespace?: string) =>
    http.get('/cache/exists', { key, namespace: namespace || 'default' }),

  expire: (key: string, ttl: number, namespace?: string) =>
    http.post('/cache/expire', { key, ttl, namespace: namespace || 'default' }),

  ttl: (key: string, namespace?: string) =>
    http.get('/cache/ttl', { key, namespace: namespace || 'default' }),

  // 批量操作
  mget: (keys: string[], namespace?: string) =>
    http.post('/cache/mget', { keys, namespace: namespace || 'default' }),

  mset: (items: any, ttl?: number, namespace?: string) =>
    http.post('/cache/mset', { items, ttl, namespace: namespace || 'default' }),

  // 模式匹配
  keys: (pattern: string, namespace?: string) =>
    http.get('/cache/keys', { pattern, namespace: namespace || 'default' }),

  deletePattern: (pattern: string, namespace?: string) =>
    http.delete('/cache/keys', { pattern, namespace: namespace || 'default' }),

  // 策略管理
  getPolicies: () =>
    http.get('/cache/policies'),

  createPolicy: (data: any) =>
    http.post('/cache/policies', data),

  // 统计
  getStats: () =>
    http.get('/cache/stats'),

  clear: (namespace?: string) =>
    http.post('/cache/clear', { namespace })
}

// 服务网格 API
export const serviceMeshApi = {
  // 服务管理
  getServices: (namespace?: string) =>
    http.get('/mesh/services', { namespace }),

  registerService: (data: any) =>
    http.post('/mesh/services', data),

  getService: (namespace: string, name: string) =>
    http.get(`/mesh/services/${namespace}/${name}`),

  // 服务实例
  registerInstance: (namespace: string, name: string, data: any) =>
    http.post(`/mesh/services/${namespace}/${name}/instances`, data),

  deregisterInstance: (namespace: string, name: string, instanceId: string) =>
    http.delete(`/mesh/services/${namespace}/${name}/instances/${instanceId}`),

  heartbeat: (namespace: string, name: string, instanceId: string) =>
    http.post(`/mesh/services/${namespace}/${name}/instances/${instanceId}/heartbeat`),

  // 服务发现
  resolveService: (namespace: string, name: string) =>
    http.get(`/mesh/resolve/${namespace}/${name}`),

  // 流量路由
  createRoute: (data: any) =>
    http.post('/mesh/routes', data),

  getRoute: (serviceName: string) =>
    http.get(`/mesh/routes/${serviceName}`),

  updateRoute: (serviceName: string, data: any) =>
    http.put(`/mesh/routes/${serviceName}`, data),

  deleteRoute: (serviceName: string) =>
    http.delete(`/mesh/routes/${serviceName}`),

  // 熔断器
  getCircuitBreakers: () =>
    http.get('/mesh/circuit-breakers'),

  getCircuitBreaker: (serviceName: string) =>
    http.get(`/mesh/circuit-breakers/${serviceName}`),

  recordSuccess: (serviceName: string) =>
    http.post(`/mesh/circuit-breakers/${serviceName}/success`),

  recordFailure: (serviceName: string) =>
    http.post(`/mesh/circuit-breakers/${serviceName}/failure`),

  checkCircuitStatus: (serviceName: string) =>
    http.get(`/mesh/circuit-breakers/${serviceName}/status`),

  // 限流
  createRateLimit: (data: any) =>
    http.post('/mesh/rate-limits', data),

  deleteRateLimit: (serviceName: string) =>
    http.delete(`/mesh/rate-limits/${serviceName}`),

  // 统计
  getStats: () =>
    http.get('/mesh/stats')
}

// API网关 API
export const gatewayApi = {
  // 路由管理
  getRoutes: () =>
    http.get('/gateway/routes'),

  getRoute: (name: string) =>
    http.get(`/gateway/routes/${name}`),

  createRoute: (data: any) =>
    http.post('/gateway/routes', data),

  updateRoute: (name: string, data: any) =>
    http.put(`/gateway/routes/${name}`, data),

  deleteRoute: (name: string) =>
    http.delete(`/gateway/routes/${name}`),

  // 限流
  getRateLimits: () =>
    http.get('/gateway/rate-limits'),

  createRateLimit: (data: any) =>
    http.post('/gateway/rate-limits', data),

  updateRateLimit: (name: string, data: any) =>
    http.put(`/gateway/rate-limits/${name}`, data),

  deleteRateLimit: (name: string) =>
    http.delete(`/gateway/rate-limits/${name}`),

  // 熔断器
  getCircuitBreakers: () =>
    http.get('/gateway/circuit-breakers'),

  createCircuitBreaker: (data: any) =>
    http.post('/gateway/circuit-breakers', data),

  updateCircuitBreaker: (name: string, data: any) =>
    http.put(`/gateway/circuit-breakers/${name}`, data),

  deleteCircuitBreaker: (name: string) =>
    http.delete(`/gateway/circuit-breakers/${name}`),

  // API文档
  getApiDocs: () =>
    http.get('/gateway/docs'),

  // 监控
  getStats: () =>
    http.get('/gateway/stats'),

  getTopRoutes: (limit?: number) =>
    http.get('/gateway/stats/top-routes', { limit }),

  getSlowRequests: (limit?: number) =>
    http.get('/gateway/stats/slow-requests', { limit })
}

// 合规管理 API
export const complianceApi = {
  // 合规检查
  getChecks: (params?: { standard?: string; status?: string }) =>
    http.get('/compliance/checks', params),

  runCheck: (checkId: string) =>
    http.post(`/compliance/checks/${checkId}/run`),

  runAllChecks: (standard?: string) =>
    http.post('/compliance/checks/run', null, { params: { standard } }),

  // 合规报告
  getReports: (standard?: string) =>
    http.get('/compliance/reports', { standard }),

  getReport: (reportId: string) =>
    http.get(`/compliance/reports/${reportId}`),

  generateReport: (data: { name: string; standard?: string }) =>
    http.post('/compliance/reports', data),

  // 安全发现
  getFindings: (params?: { severity?: string; status?: string }) =>
    http.get('/compliance/findings', params),

  resolveFinding: (findingId: string) =>
    http.post(`/compliance/findings/${findingId}/resolve`),

  // 统计和标准
  getStats: () =>
    http.get('/compliance/stats'),

  getStandards: () =>
    http.get('/compliance/standards')
}

// 合规管理 API (扩展)
export const complianceManagementApi = {
  // 合规规则
  getRules: (params?: { standard?: string; enabled?: boolean }) =>
    http.get('/compliance/rules', params),

  getRule: (ruleId: string) =>
    http.get(`/compliance/rules/${ruleId}`),

  createRule: (data: any) =>
    http.post('/compliance/rules', data),

  updateRule: (ruleId: string, data: any) =>
    http.put(`/compliance/rules/${ruleId}`, data),

  deleteRule: (ruleId: string) =>
    http.delete(`/compliance/rules/${ruleId}`),

  runCheck: (ruleId?: string) =>
    http.post('/compliance/checks/run', null, { params: { rule_id: ruleId } }),

  runAllChecks: (standard?: string) =>
    http.post('/compliance/checks/run-all', null, { params: { standard } }),

  // 检查任务
  getTasks: (params?: { standard?: string; status?: string }) =>
    http.get('/compliance/tasks', params),

  createTask: (data: any) =>
    http.post('/compliance/tasks', data),

  deleteTask: (taskId: string) =>
    http.delete(`/compliance/tasks/${taskId}`),

  // 合规报告
  getReports: (standard?: string) =>
    http.get('/compliance/reports', { standard }),

  getReport: (reportId: string) =>
    http.get(`/compliance/reports/${reportId}`),

  generateReport: (data: { name: string; standard?: string; report_type?: string; date_range?: string[] }) =>
    http.post('/compliance/reports', data),

  // 违规发现
  getFindings: (params?: { severity?: string; status?: string }) =>
    http.get('/compliance/findings', params),

  resolveFinding: (findingId: string) =>
    http.post(`/compliance/findings/${findingId}/resolve`),

  // 统计
  getStats: () =>
    http.get('/compliance/stats'),

  getStandards: () =>
    http.get('/compliance/standards')
}

// 模型治理 API (扩展)
export const governanceApi = {
  // 审批流程
  getApprovals: (params?: { status?: string; type?: string; requester?: string }) =>
    http.get('/governance/approvals', params),

  getApproval: (approvalId: string) =>
    http.get(`/governance/approvals/${approvalId}`),

  createApproval: (data: any) =>
    http.post('/governance/approvals', data),

  approveRequest: (approvalId: string, approver: string, comments?: string) =>
    http.post(`/governance/approvals/${approvalId}/approve`, null, { params: { approver, comments } }),

  rejectRequest: (approvalId: string, approver: string, comments: string) =>
    http.post(`/governance/approvals/${approvalId}/reject`, null, { params: { approver, comments } }),

  cancelRequest: (approvalId: string, requester: string) =>
    http.post(`/governance/approvals/${approvalId}/cancel`, null, { params: { requester } }),

  // 审计日志
  getAuditLogs: (params?: any) =>
    http.get('/governance/audit-logs', params),

  createAuditLog: (data: any) =>
    http.post('/governance/audit-logs', data),

  // 模型下线
  getDeprecations: (params?: { model_id?: string; status?: string }) =>
    http.get('/governance/deprecation', params),

  createDeprecation: (data: any) =>
    http.post('/governance/deprecation', data),

  executeDeprecation: (deprecationId: string) =>
    http.post(`/governance/deprecation/${deprecationId}/execute`),

  cancelDeprecation: (deprecationId: string) =>
    http.post(`/governance/deprecation/${deprecationId}/cancel`),

  // 风险评估
  getRiskAssessments: () =>
    http.get('/governance/risk-assessment'),

  getRiskAssessment: (modelId: string) =>
    http.get(`/governance/risk-assessment/${modelId}`),

  createRiskAssessment: (data: any) =>
    http.post('/governance/risk-assessment', data),

  // 合规报告
  generateComplianceReport: (data: any) =>
    http.post('/governance/compliance-report', data),

  getComplianceTemplates: () =>
    http.get('/governance/compliance-report/templates'),

  // 策略管理
  getPolicies: () =>
    http.get('/governance/policies'),

  togglePolicy: (policyId: string, enabled: boolean) =>
    http.post(`/governance/policies/${policyId}/toggle`, null, { params: { enabled } }),

  // 仪表盘
  getDashboard: () =>
    http.get('/governance/dashboard'),

  // 状态
  getStatus: () =>
    http.get('/governance/status')
}

// 流式处理 API
export const streamingApi = {
  // Topic管理
  getTopics: () =>
    http.get('/streaming/topics'),

  createTopic: (data: any) =>
    http.post('/streaming/topics', data),

  deleteTopic: (name: string) =>
    http.delete(`/streaming/topics/${name}`),

  getPredefinedTopics: () =>
    http.get('/streaming/predefined-topics'),

  // 流任务
  getTasks: () =>
    http.get('/streaming/tasks'),

  createTask: (data: any) =>
    http.post('/streaming/tasks', data),

  startTask: (id: string) =>
    http.post(`/streaming/tasks/${id}/start`),

  pauseTask: (id: string) =>
    http.post(`/streaming/tasks/${id}/pause`),

  resumeTask: (id: string) =>
    http.post(`/streaming/tasks/${id}/resume`),

  deleteTask: (id: string) =>
    http.delete(`/streaming/tasks/${id}`),

  // 事件
  publishEvent: (data: any) =>
    http.post('/streaming/events', data),

  getEvents: (eventType: string, limit?: number) =>
    http.get(`/streaming/events/${eventType}`, { limit }),

  // 消费组
  getConsumerGroups: () =>
    http.get('/streaming/consumer-groups'),

  // 配置
  getConfig: () =>
    http.get('/streaming/config'),

  saveConfig: (data: any) =>
    http.post('/streaming/config', data),

  testConnection: (data: any) =>
    http.post('/streaming/test-connection', data)
}
