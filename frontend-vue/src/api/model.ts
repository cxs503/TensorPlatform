import http from './request'
import { get, post } from './legacy'

// 模型 API
export const modelApi = {
  getList: (params?: any) =>
    http.get('/models', params),

  get: (id: number) =>
    http.get(`/models/${id}`),

  create: (data: any) =>
    http.post('/models', data),

  update: (id: number, data: any) =>
    http.put(`/models/${id}`, data),

  delete: (id: number) =>
    http.delete(`/models/${id}`),

  getVersions: (id: number) =>
    http.get(`/models/${id}/versions`)
}

// 模型分类 API —— 按用户选择的业务文件夹组织训练产物
export const modelGroupApi = {
  getGrouped: () =>
    http.get('/models/grouped-by-folder'),

  getFolders: () =>
    http.get('/models/folders'),

  createFolder: (data: { name: string; description?: string }) =>
    http.post('/models/folders', data),

  deleteFolder: (id: number) =>
    http.delete('/models/folders/' + id),
}

// 训练 API
export const trainingApi = {
  getList: (params?: any) =>
    get('/training', params),

  get: (id: number) =>
    http.get(`/training/${id}`),

  create: (data: any) =>
    http.post('/training', data),

  start: (id: number) =>
    http.post(`/training/${id}/start`),

  stop: (id: number) =>
    http.post(`/training/${id}/stop`),

  getLogs: (id: number) =>
    http.get(`/training/${id}/logs`)
}

// 模型训练 API (增强版)
export const modelTrainingApi = {
  getList: (params?: any) =>
    http.get('/real-training/jobs', params),

  get: (id: number) =>
    http.get(`/real-training/jobs/${id}`),

  create: (data: any) =>
    http.post('/real-training/jobs-from-dataset', data),

  update: (id: number, data: any) =>
    http.put(`/real-training/jobs/${id}`, data),

  delete: (id: number) =>
    http.delete(`/real-training/jobs/${id}`),

  start: (id: number) =>
    http.post(`/model-training/jobs/${id}/start`),

  stop: (id: number) =>
    http.post(`/model-training/jobs/${id}/stop`),

  getLogs: (id: number) =>
    http.get(`/real-training/jobs/${id}/logs`),

  getMetrics: (id: number) =>
    http.get(`/real-training/jobs/${id}/metrics`),

  getResources: (id: number) =>
    http.get(`/real-training/jobs/${id}`),

  saveModel: (id: number, data: any) =>
    http.post(`/real-training/jobs/${id}/predict`, data)
}

// 超参优化 API
export const hyperoptApi = {
  getTasks: () =>
    http.get('/hyperopt/tasks'),

  createTask: (data: any) =>
    http.post('/hyperopt/tasks', data),

  getResult: (id: number) =>
    http.get(`/hyperopt/tasks/${id}`),

  getBestParams: (id: number) =>
    http.get(`/hyperopt/tasks/${id}/best-params`)
}

// 模型版本管理 API
export const modelVersionApi = {
  // 版本管理
  createVersion: (data: any) =>
    http.post('/model-version/versions', data),

  getVersions: (modelId: string) =>
    http.get(`/model-version/models/${modelId}/versions`),

  getVersion: (modelId: string, version: string) =>
    http.get(`/model-version/models/${modelId}/versions/${version}`),

  getCurrentVersion: (modelId: string) =>
    http.get(`/model-version/models/${modelId}/versions/current`),

  setCurrentVersion: (modelId: string, version: string) =>
    http.post(`/model-version/models/${modelId}/versions/${version}/set-current`),

  rollbackVersion: (modelId: string, version: string) =>
    http.post(`/model-version/models/${modelId}/versions/${version}/rollback`),

  compareVersions: (modelId: string, version1: string, version2: string) =>
    http.get(`/model-version/models/${modelId}/compare`, { version1, version2 }),

  // A/B测试
  createABTest: (data: any) =>
    http.post('/model-version/ab-tests', data),

  getABTests: (status?: string) =>
    http.get('/model-version/ab-tests', { status }),

  getABTest: (testId: string) =>
    http.get(`/model-version/ab-tests/${testId}`),

  getABTestResults: (testId: string) =>
    http.get(`/model-version/ab-tests/${testId}/results`),

  stopABTest: (testId: string) =>
    http.post(`/model-version/ab-tests/${testId}/stop`),

  recordABResult: (testId: string, version: string, success: boolean, latency?: number) =>
    http.post(`/model-version/ab-tests/${testId}/record`, null, { params: { version, success, latency } })
}

// 模型评估 API
export const modelEvaluationApi = {
  evaluate: (data: any) =>
    http.post('/model-evaluation/evaluate', data),

  getResults: (params?: any) =>
    http.get('/model-evaluation/results', params),

  getResult: (id: number) =>
    http.get(`/model-evaluation/results/${id}`),

  deleteResult: (id: number) =>
    http.delete(`/model-evaluation/results/${id}`)
}

// 标注服务 API
export const labelingApi = {
  // 标签管理
  getLabels: (category?: string, status?: string) =>
    http.get('/labeling/labels', { category, status }),

  createLabel: (data: any) =>
    http.post('/labeling/labels', data),

  getLabel: (labelId: string) =>
    http.get(`/labeling/labels/${labelId}`),

  updateLabel: (labelId: string, data: any) =>
    http.put(`/labeling/labels/${labelId}`, data),

  deleteLabel: (labelId: string) =>
    http.delete(`/labeling/labels/${labelId}`),

  // 标签组
  getLabelGroups: () =>
    http.get('/labeling/groups'),

  createLabelGroup: (data: any) =>
    http.post('/labeling/groups', data),

  addLabelToGroup: (groupId: string, labelId: string) =>
    http.post(`/labeling/groups/${groupId}/labels`, null, { params: { label_id: labelId } }),

  removeLabelFromGroup: (groupId: string, labelId: string) =>
    http.delete(`/labeling/groups/${groupId}/labels/${labelId}`),

  // 标注任务
  getTasks: (params?: { assignee_id?: string; status?: string; dataset_id?: string }) =>
    http.get('/labeling/tasks', params),

  createTask: (data: any) =>
    http.post('/labeling/tasks', data),

  getTask: (taskId: string) =>
    http.get(`/labeling/tasks/${taskId}`),

  assignTask: (taskId: string, assigneeId: string) =>
    http.post(`/labeling/tasks/${taskId}/assign`, null, { params: { assignee_id: assigneeId } }),

  updateTaskStatus: (taskId: string, status: string) =>
    http.put(`/labeling/tasks/${taskId}/status`, null, { params: { status } }),

  // 标注结果
  submitAnnotation: (data: any) =>
    http.post('/labeling/annotations', data),

  getAnnotations: (params?: { task_id?: string; annotator_id?: string; status?: string }) =>
    http.get('/labeling/annotations', params),

  getAnnotation: (annotationId: string) =>
    http.get(`/labeling/annotations/${annotationId}`),

  reviewAnnotation: (annotationId: string, data: any) =>
    http.post(`/labeling/annotations/${annotationId}/review`, data),

  // 统计
  getStats: (taskId?: string) =>
    http.get('/labeling/stats', { task_id: taskId })
}

// 模型解释 API
export const modelExplainApi = {
  shap: (data: any) =>
    http.post('/model-explain/shap', data),

  lime: (data: any) =>
    http.post('/model-explain/lime', data),

  featureImportance: (data: any) =>
    http.post('/model-explain/feature-importance', data),

  partialDependence: (data: any) =>
    http.post('/model-explain/partial-dependence', data),

  permutationImportance: (data: any) =>
    http.post('/model-explain/permutation-importance', data),

  getMethods: () =>
    http.get('/model-explain/methods')
}

// ML流水线 API
export const pipelineApi = {
  getPipelines: (params?: any) =>
    http.get('/pipelines/', params),

  getPipeline: (id: string) =>
    http.get(`/pipelines/${id}`),

  createPipeline: (data: any) =>
    http.post('/pipelines/', data),

  updatePipeline: (id: string, data: any) =>
    http.put(`/pipelines/${id}`, data),

  deletePipeline: (id: string) =>
    http.delete(`/pipelines/${id}`),

  runPipeline: (id: string, params?: any) =>
    http.post(`/pipelines/${id}/run`, params),

  validatePipeline: (id: string, data?: any) =>
    http.post(`/pipelines/${id}/validate`, data),

  getRuns: (params?: any) =>
    http.get('/pipelines/runs', params),

  getRun: (id: string) =>
    http.get(`/pipelines/runs/${id}`),

  getRunLogs: (id: string, taskId?: string) =>
    http.get(`/pipelines/runs/${id}/logs`, taskId ? { task_id: taskId } : {}),

  cancelRun: (id: string) =>
    http.post(`/pipelines/runs/${id}/cancel`),

  getTemplates: () =>
    http.get('/pipelines/templates'),

  getStatistics: () =>
    http.get('/pipelines/statistics'),

  setSchedule: (id: string, cron: string) =>
    http.post(`/pipelines/${id}/schedule`, null, { params: { cron } }),

  removeSchedule: (id: string) =>
    http.delete(`/pipelines/${id}/schedule`)
}

// 特征存储 API (扩展)
export const featureStoreApi = {
  // 特征组
  getGroups: (params?: any) =>
    http.get('/feature-store/feature-groups', params),

  getGroup: (id: string) =>
    http.get(`/feature-store/feature-groups/${id}`),

  createGroup: (data: any) =>
    http.post('/feature-store/feature-groups', data),

  updateGroup: (id: string, data: any) =>
    http.put(`/feature-store/feature-groups/${id}`, data),

  deleteGroup: (id: string) =>
    http.delete(`/feature-store/feature-groups/${id}`),

  syncToOnline: (id: string) =>
    http.post(`/feature-store/feature-groups/${id}/sync`),

  setDataSource: (id: string, config: any) =>
    http.post(`/feature-store/feature-groups/${id}/data-source`, config),

  // 特征
  getFeatures: (params?: any) =>
    http.get('/feature-store/features', params),

  getFeature: (id: string) =>
    http.get(`/feature-store/features/${id}`),

  createFeature: (data: any) =>
    http.post('/feature-store/features', data),

  // 在线/离线查询
  getOnlineFeatures: (data: any) =>
    http.post('/feature-store/online', data),

  getOfflineFeatures: (data: any) =>
    http.post('/feature-store/offline', data),

  getEntityHistory: (entityType: string, entityId: string, params?: any) =>
    http.get(`/feature-store/entity/${entityType}/${entityId}/history`, params),

  getFeatureLineage: (featureName: string) =>
    http.get(`/feature-store/lineage/${featureName}`),

  validateFeatures: (groupId: string, data: any) =>
    http.post(`/feature-store/validate`, { group_id: groupId, ...data }),

  getStatistics: () =>
    http.get('/feature-store/statistics/feature-groups')
}

// 分布式训练 API
export const distributedTrainingApi = {
  getStatus: () =>
    http.get('/distributed-training/status'),

  getJobs: (params?: any) =>
    http.get('/distributed-training/jobs', params),

  getJob: (id: string) =>
    http.get(`/distributed-training/jobs/${id}`),

  createJob: (data: any) =>
    http.post('/distributed-training/jobs', data),

  startJob: (id: string) =>
    http.post(`/distributed-training/jobs/${id}/start`),

  stopJob: (id: string) =>
    http.post(`/distributed-training/jobs/${id}/stop`),

  getJobLogs: (id: string, lines?: number) =>
    http.get(`/distributed-training/jobs/${id}/logs`, { lines }),

  getJobMetrics: (id: string) =>
    http.get(`/distributed-training/jobs/${id}/metrics`),

  registerNode: (data: any) =>
    http.post('/distributed-training/nodes/register', data),

  getNodes: () =>
    http.get('/distributed-training/nodes'),

  getStrategies: () =>
    http.get('/distributed-training/strategies')
}

// 真实训练 API (对接 backend/api/real_training.py)
export const realTrainingApi = {
  health: () =>
    http.get('/real-training/health'),

  getSupportedModels: () =>
    http.get('/real-training/models/supported'),

  getAvailableModels: () =>
    http.get('/real-training/models/available'),

  getScenarios: () =>
    http.get('/real-training/scenarios'),

  train: (data: any) =>
    http.post('/real-training/train', data),

  createJob: (data: any) =>
    http.post('/real-training/jobs-from-dataset', data),

  getJobs: (params?: any) =>
    http.get('/real-training/jobs', params),

  getJob: (jobId: string) =>
    http.get(`/real-training/jobs/${jobId}`),

  predict: (jobId: string, data: any) =>
    http.post(`/real-training/jobs/${jobId}/predict`, data)
}

// ONNX 导出与推理加速 API (对接 backend/api/onnx_api.py)
export const onnxApi = {
  health: () =>
    http.get('/onnx/health'),

  exportModel: (data: any) =>
    http.post('/onnx/export', data),

  getModels: () =>
    http.get('/onnx/models'),

  predict: (data: any) =>
    http.post('/onnx/predict', data),

  benchmark: (data: any) =>
    http.post('/onnx/benchmark', data)
}
