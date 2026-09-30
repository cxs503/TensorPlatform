import http from './request'

// MLflow 实验追踪 API (对接 backend/api/mlflow_api.py)
export const mlflowTrackingApi = {
  getStatus: () =>
    http.get('/mlflow/status'),

  getExperiments: () =>
    http.get('/mlflow/experiments'),

  getExperiment: (experimentId: string) =>
    http.get(`/mlflow/experiments/${experimentId}`),

  getRuns: (experimentId: string) =>
    http.get(`/mlflow/runs/${experimentId}`),

  getRunDetail: (runId: string) =>
    http.get(`/mlflow/runs/${runId}/detail`),

  getRunMetrics: (runId: string) =>
    http.get(`/mlflow/runs/${runId}/metrics`),

  getRunMetricHistory: (runId: string, metricKey: string) =>
    http.get(`/mlflow/runs/${runId}/metrics/${metricKey}`),

  getRunArtifacts: (runId: string) =>
    http.get(`/mlflow/runs/${runId}/artifacts`),

  getRegisteredModels: () =>
    http.get('/mlflow/models')
}

// 漂移检测 API
export const driftDetectionApi = {
  // 数据漂移
  getDataDriftTasks: () =>
    http.get('/drift-detection/data-drift/tasks'),

  getDataDriftTask: (id: number) =>
    http.get(`/drift-detection/data-drift/tasks/${id}`),

  createDataDriftTask: (data: any) =>
    http.post('/drift-detection/data-drift/tasks', data),

  deleteDataDriftTask: (id: number) =>
    http.delete(`/drift-detection/data-drift/tasks/${id}`),

  runDataDriftCheck: (id: number) =>
    http.post(`/drift-detection/data-drift/tasks/${id}/check`),

  getDataDriftResult: (id: number) =>
    http.get(`/drift-detection/data-drift/tasks/${id}/result`),

  // 模型漂移
  getModelDriftMonitors: () =>
    http.get('/drift-detection/model-drift/monitors'),

  getModelDriftMonitor: (id: number) =>
    http.get(`/drift-detection/model-drift/monitors/${id}`),

  createModelDriftMonitor: (data: any) =>
    http.post('/drift-detection/model-drift/monitors', data),

  deleteModelDriftMonitor: (id: number) =>
    http.delete(`/drift-detection/model-drift/monitors/${id}`),

  checkModelDrift: (id: number) =>
    http.post(`/drift-detection/model-drift/monitors/${id}/check`),

  getModelDriftHistory: (id: number) =>
    http.get(`/drift-detection/model-drift/monitors/${id}/history`),

  // 告警规则
  getAlertRules: () =>
    http.get('/drift-detection/alert-rules'),

  createAlertRule: (data: any) =>
    http.post('/drift-detection/alert-rules', data),

  updateAlertRule: (id: number, data: any) =>
    http.put(`/drift-detection/alert-rules/${id}`, data),

  deleteAlertRule: (id: number) =>
    http.delete(`/drift-detection/alert-rules/${id}`),

  // 报告
  getReports: (params?: any) =>
    http.get('/drift-detection/reports', params),

  generateReport: () =>
    http.post('/drift-detection/reports/generate'),

  downloadReport: (id: number) =>
    http.get(`/drift-detection/reports/${id}/download`)
}
