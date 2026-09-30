import http from './request'

// 项目 API
export const projectApi = {
  getList: (params?: any) =>
    http.get('/projects', params),

  get: (id: number) =>
    http.get(`/projects/${id}`),

  create: (data: any) =>
    http.post('/projects', data),

  update: (id: number, data: any) =>
    http.put(`/projects/${id}`, data),

  delete: (id: number) =>
    http.delete(`/projects/${id}`)
}

// 数据集 API
export const datasetApi = {
  getList: (params?: any) =>
    http.get('/datasets', params),

  get: (id: number) =>
    http.get(`/datasets/${id}`),

  getRows: (id: number, params?: any) =>
    http.get(`/datasets/${id}/rows`, params),

  create: (data: any) =>
    http.post('/datasets', data),

  update: (id: number, data: any) =>
    http.put(`/datasets/${id}`, data),

  delete: (id: number) =>
    http.delete(`/datasets/${id}`),

  upload: (formData: FormData) =>
    http.post('/datasets/upload', formData),

  getPreview: (id: number) =>
    http.get(`/datasets/${id}/preview`)
}

// 虚拟数据 API
export const virtualDataApi = {
  getDataTypes: () =>
    http.get('/virtual-data/data-types'),

  generateData: (dataType: string, count?: number) =>
    http.post(`/virtual-data/generate/${dataType}`, { count }),

  generateAllData: () =>
    http.post('/virtual-data/generate-all'),

  getStoredData: (dataType?: string) =>
    http.get(dataType ? `/virtual-data/stored/${dataType}` : '/virtual-data/stored'),

  trainModel: (businessType: string) =>
    http.post(`/virtual-data/train/${businessType}`),

  getTrainingTasks: () =>
    http.get('/virtual-data/training-tasks'),

  deployModel: (taskId: number) =>
    http.post(`/virtual-data/deploy/${taskId}`),

  getDeployments: () =>
    http.get('/virtual-data/deployments'),

  predict: (deploymentId: number, data: any) =>
    http.post(`/virtual-data/inference/${deploymentId}`, data)
}

// 数据源 API
export const dataSourceApi = {
  getList: () =>
    http.get('/datasources'),

  add: (data: any) =>
    http.post(`/datasources/${data.type || data.source_type}`, data),

  testConnection: (id: number) =>
    http.post(`/datasources/${id}/test`),

  sync: (id: number) =>
    http.post(`/datasources/${id}/import`)
}

// 数据治理 API
export const dataGovernanceApi = {
  getTables: () =>
    http.get('/governance/tables'),

  getLineage: (tableId?: number) =>
    http.get('/governance/lineage', { table_id: tableId }),

  getMetadata: (tableId: number) =>
    http.get(`/governance/metadata/${tableId}`),

  getStandards: () =>
    http.get('/governance/standards')
}

// 数据目录 API
export const dataCatalogApi = {
  // 资产
  getAssets: (params?: any) =>
    http.get('/catalog/assets', params),

  getAsset: (id: number) =>
    http.get(`/catalog/assets/${id}`),

  createAsset: (data: any) =>
    http.post('/catalog/assets', data),

  updateAsset: (id: number, data: any) =>
    http.put(`/catalog/assets/${id}`, data),

  deleteAsset: (id: number) =>
    http.delete(`/catalog/assets/${id}`),

  batchDeleteAssets: (ids: number[]) =>
    http.post('/catalog/assets/batch/delete', ids),

  batchTagAssets: (ids: number[], tags: string[]) =>
    http.post('/catalog/assets/batch/tag', { asset_ids: ids, tags }),

  // 元数据
  getMetadata: (assetId: number) =>
    http.get(`/catalog/assets/${assetId}/metadata`),

  addMetadata: (assetId: number, data: any) =>
    http.post(`/catalog/assets/${assetId}/metadata`, data),

  deleteMetadata: (assetId: number, metadataId: number) =>
    http.delete(`/catalog/assets/${assetId}/metadata/${metadataId}`),

  // 分类
  getClassifications: () =>
    http.get('/catalog/classifications'),

  createClassification: (data: any) =>
    http.post('/catalog/classifications', data),

  updateClassification: (id: number, data: any) =>
    http.put(`/catalog/classifications/${id}`, data),

  deleteClassification: (id: number) =>
    http.delete(`/catalog/classifications/${id}`),

  assignClassification: (assetId: number, classificationId: number) =>
    http.post(`/catalog/assets/${assetId}/classifications/${classificationId}`),

  // 标签
  getTags: (category?: string) =>
    http.get('/catalog/tags', { category }),

  createTag: (data: any) =>
    http.post('/catalog/tags', data),

  updateTag: (id: number, data: any) =>
    http.put(`/catalog/tags/${id}`, data),

  deleteTag: (id: number) =>
    http.delete(`/catalog/tags/${id}`),

  // 血缘
  getLineage: (assetId: number, depth?: number) =>
    http.get(`/catalog/assets/${assetId}/lineage`, { depth }),

  addLineage: (assetId: number, data: any) =>
    http.post(`/catalog/assets/${assetId}/lineage`, data),

  deleteLineage: (lineageId: number) =>
    http.delete(`/catalog/lineage/${lineageId}`),

  // 搜索
  search: (query: string, type?: string) =>
    http.get('/catalog/search', { q: query, type }),

  // 统计
  getStats: () =>
    http.get('/catalog/stats')
}

// 数据血缘 API
export const lineageApi = {
  getNodes: (params?: any) =>
    http.get('/lineage/nodes', params),

  getNode: (id: string) =>
    http.get(`/lineage/nodes/${id}`),

  createNode: (data: any) =>
    http.post('/lineage/nodes', data),

  updateNode: (id: string, data: any) =>
    http.put(`/lineage/nodes/${id}`, data),

  deleteNode: (id: string) =>
    http.delete(`/lineage/nodes/${id}`),

  getEdges: (params?: any) =>
    http.get('/lineage/edges', params),

  createEdge: (data: any) =>
    http.post('/lineage/edges', data),

  getGraph: (params?: any) =>
    http.get('/lineage/graph', params),

  getSubgraph: (nodeId: string, params?: any) =>
    http.get(`/lineage/graph/sub/${nodeId}`, params),

  getUpstream: (nodeId: string, params?: any) =>
    http.get(`/lineage/upstream/${nodeId}`, params),

  getDownstream: (nodeId: string, params?: any) =>
    http.get(`/lineage/downstream/${nodeId}`, params),

  getStats: () =>
    http.get('/lineage/stats'),

  syncTraining: (limit = 200) =>
    http.post(`/lineage/sync/training?limit=${limit}`),

  syncAll: (limit = 1000) =>
    http.post(`/lineage/sync/all?limit=${limit}`),

  extractSql: (data: any) =>
    http.post('/lineage/extract/sql', data),

  getChangelog: (params?: any) =>
    http.get('/lineage/changelog', params)
}

// 数据质量 API
export const qualityApi = {
  getRules: (params?: any) =>
    http.get('/quality/rules', params),

  createRule: (data: any) =>
    http.post('/quality/rules', data),

  updateRule: (id: number, data: any) =>
    http.put(`/quality/rules/${id}`, data),

  deleteRule: (id: number) =>
    http.delete(`/quality/rules/${id}`),

  runCheck: (data: any) =>
    http.post('/quality/check', data),

  getResults: (params?: any) =>
    http.get('/quality/results', params),

  getAnomalies: (params?: any) =>
    http.get('/quality/anomalies', params),

  resolveAnomaly: (id: number, data?: any) =>
    http.put(`/quality/anomalies/${id}/resolve`, data),

  getDashboard: () =>
    http.get('/quality/dashboard')
}

// 数据安全 API
export const securityApi = {
  // 脱敏规则
  getMaskingRules: (params?: any) =>
    http.get('/masking/rules', params),

  createMaskingRule: (data: any) =>
    http.post('/masking/rules', data),

  updateMaskingRule: (id: number, data: any) =>
    http.put(`/masking/rules/${id}`, data),

  deleteMaskingRule: (id: number) =>
    http.delete(`/masking/rules/${id}`),

  applyMasking: (data: any) =>
    http.post('/masking/apply', data),

  // 访问策略
  getPolicies: (params?: any) =>
    http.get('/security/policies', params),

  createPolicy: (data: any) =>
    http.post('/security/policies', data),

  updatePolicy: (id: number, data: any) =>
    http.put(`/security/policies/${id}`, data),

  deletePolicy: (id: number) =>
    http.delete(`/security/policies/${id}`),

  // 审计日志
  getAuditLogs: (params?: any) =>
    http.get('/security/audit', params)
}

// 数据脱敏 API
export const dataMaskingApi = {
  // 脱敏规则
  getRules: (params?: { is_active?: number; skip?: number; limit?: number }) =>
    http.get('/masking/rules', params),

  createRule: (data: any) =>
    http.post('/masking/rules', data),

  getRule: (ruleId: number) =>
    http.get(`/masking/rules/${ruleId}`),

  updateRule: (ruleId: number, data: any) =>
    http.put(`/masking/rules/${ruleId}`, data),

  deleteRule: (ruleId: number) =>
    http.delete(`/masking/rules/${ruleId}`),

  // 脱敏操作
  applyMasking: (data: any[]) =>
    http.post('/masking/apply', { data }),

  maskSingle: (record: any) =>
    http.post('/masking/mask-single', record),

  getMaskingTypes: () =>
    http.get('/masking/types'),

  analyzeSensitivity: (data: any[]) =>
    http.post('/masking/analyze', { data }),

  // 访问策略
  getPolicies: (params?: { is_active?: number; skip?: number; limit?: number }) =>
    http.get('/masking/policies', params),

  createPolicy: (data: any) =>
    http.post('/masking/policies', data),

  updatePolicy: (policyId: number, data: any) =>
    http.put(`/masking/policies/${policyId}`, data),

  deletePolicy: (policyId: number) =>
    http.delete(`/masking/policies/${policyId}`),

  checkAccess: (data: { user_id: number; resource_type: string; resource: string; action: string }) =>
    http.post('/masking/check-access', data),

  // 审计日志
  getAuditLogs: (params?: { user_id?: number; resource_type?: string; result?: string; start_time?: string; end_time?: string; skip?: number; limit?: number }) =>
    http.get('/masking/audit', params),

  logAudit: (data: any) =>
    http.post('/masking/audit', data),

  getAuditStatistics: (params?: { start_time?: string; end_time?: string }) =>
    http.get('/masking/audit/statistics', params)
}

// 数据采集 API
export const dataCollectionApi = {
  // 采集任务
  getTasks: (status?: string) =>
    http.get('/collection/tasks', { status }),

  createTask: (data: any) =>
    http.post('/collection/tasks', data),

  getTask: (taskId: string) =>
    http.get(`/collection/tasks/${taskId}`),

  startTask: (taskId: string) =>
    http.post(`/collection/tasks/${taskId}/start`),

  pauseTask: (taskId: string) =>
    http.post(`/collection/tasks/${taskId}/pause`),

  stopTask: (taskId: string) =>
    http.post(`/collection/tasks/${taskId}/stop`),

  deleteTask: (taskId: string) =>
    http.delete(`/collection/tasks/${taskId}`),

  // 流水线
  getPipelines: () =>
    http.get('/collection/pipelines'),

  createPipeline: (data: any) =>
    http.post('/collection/pipelines', data),

  // 数据获取
  getLatestData: (source?: string, limit?: number) =>
    http.get('/collection/data', { source, limit }),

  queryHistoricalData: (params: { start_time: string; end_time: string; source?: string; limit?: number }) =>
    http.get('/collection/data/query', params),

  processData: (data: { pipeline_id?: string; data: any[] }) =>
    http.post('/collection/data/process', data),

  // 统计
  getStats: () =>
    http.get('/collection/stats')
}

// 数据订阅 API
export const dataSubscriptionApi = {
  // 订阅管理
  createSubscription: (data: any) =>
    http.post('/data-subscription/subscriptions', data),

  getSubscriptions: (subscriberId?: string) =>
    http.get('/data-subscription/subscriptions', { subscriber_id: subscriberId }),

  getSubscription: (subscriptionId: string) =>
    http.get(`/data-subscription/subscriptions/${subscriptionId}`),

  updateSubscription: (subscriptionId: string, data: any) =>
    http.put(`/data-subscription/subscriptions/${subscriptionId}`, data),

  deleteSubscription: (subscriptionId: string) =>
    http.delete(`/data-subscription/subscriptions/${subscriptionId}`),

  // 数据共享
  shareDataset: (data: any) =>
    http.post('/data-subscription/shares', data),

  getSharesByMe: (ownerId: string) =>
    http.get('/data-subscription/shares/by-me', { owner_id: ownerId }),

  getSharesWithMe: (tenantId: string) =>
    http.get('/data-subscription/shares/with-me', { tenant_id: tenantId }),

  revokeShare: (shareId: string, ownerId: string) =>
    http.delete(`/data-subscription/shares/${shareId}`, { owner_id: ownerId }),

  updateSharePermission: (shareId: string, ownerId: string, permission: string) =>
    http.put(`/data-subscription/shares/${shareId}`, { owner_id: ownerId, permission })
}
