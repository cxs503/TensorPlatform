import http from './request'

// 光伏预测 API
export const photovoltaicApi = {
  getSystems: () =>
    http.get('/photovoltaic/systems'),

  getSystem: (id: number) =>
    http.get(`/photovoltaic/systems/${id}`),

  createSystem: (data: any) =>
    http.post('/photovoltaic/systems', data),

  getRealtime: (id: number) =>
    http.get(`/photovoltaic/systems/${id}/realtime`),

  getPowerCurve: (id: number, date?: string) =>
    http.get(`/photovoltaic/systems/${id}/power-curve`, { date }),

  createForecast: (id: number, forecastType?: string) =>
    http.post(`/photovoltaic/systems/${id}/forecast`, { forecast_type: forecastType }),

  getForecast: (id: number) =>
    http.get(`/photovoltaic/forecast/${id}`)
}

// 储能优化 API
export const storageApi = {
  getSystems: () =>
    http.get('/storage'),

  getSystem: (id: number) =>
    http.get(`/storage/${id}`),

  createSystem: (data: any) =>
    http.post('/storage', data),

  getStatus: (id: number) =>
    http.get(`/storage/${id}/status`),

  getOptimization: (id: number) =>
    http.get(`/storage/${id}/optimize`),

  createOptimization: (id: number, params: any) =>
    http.post(`/storage/${id}/optimize`, params),

  executeOptimization: (id: number) =>
    http.post(`/storage/${id}/execute`),

  getSchedule: (id: number) =>
    http.get(`/storage/${id}/schedule`),

  getRevenue: (id: number) =>
    http.get(`/storage/${id}/revenue`),

  getHistory: (id: number, days?: number) =>
    http.get(`/storage/${id}/history`, { days }),

  getStats: () =>
    http.get('/storage/stats')
}

// 故障预测 API
export const faultPredictionApi = {
  // 设备管理
  getEquipment: (equipmentType?: string) =>
    http.get('/fault-prediction/equipment', { equipment_type: equipmentType }),

  registerEquipment: (data: any) =>
    http.post('/fault-prediction/equipment', data),

  getEquipmentById: (id: string) =>
    http.get(`/fault-prediction/equipment/${id}`),

  // 健康评估
  getHealth: (id: string) =>
    http.get(`/fault-prediction/equipment/${id}/health`),

  getAllHealthStatus: () =>
    http.get('/fault-prediction/health/all'),

  // 故障预测
  predictFault: (equipmentId: string, predictionType?: string) =>
    http.post(`/fault-prediction/equipment/${equipmentId}/predict`, null, { params: { prediction_type: predictionType } }),

  getPrediction: (id: string) =>
    http.get(`/fault-prediction/predictions/${id}`),

  // 异常检测
  detectAnomaly: (equipmentId: string, sensorData: any) =>
    http.post(`/fault-prediction/equipment/${equipmentId}/detect`, { sensor_data: sensorData }),

  getAnomalies: (params?: { equipment_id?: string; status?: string }) =>
    http.get('/fault-prediction/anomalies', params),

  resolveAnomaly: (id: string) =>
    http.post(`/fault-prediction/anomalies/${id}/resolve`),

  // 维护建议
  getMaintenanceSuggestions: (equipmentId: string) =>
    http.get(`/fault-prediction/equipment/${equipmentId}/maintenance`),

  // 统计
  getStats: () =>
    http.get('/fault-prediction/stats')
}

// 碳排放 API
export const carbonApi = {
  getOverview: () =>
    http.get('/carbon/overview'),

  getFactors: () =>
    http.get('/carbon/factors'),

  createFactor: (data: any) =>
    http.post('/carbon/factors', data),

  calculateEmissions: (data: any) =>
    http.post('/carbon/calculate', data),

  getEmissions: (params?: any) =>
    http.get('/carbon/emissions', params),

  getTotalEmissions: () =>
    http.get('/carbon/emissions/total'),

  getTrend: (days?: number) =>
    http.get('/carbon/emissions/trend', { days }),

  getQuotas: () =>
    http.get('/carbon/quotas'),

  createQuota: (data: any) =>
    http.post('/carbon/quotas', data),

  getCredits: () =>
    http.get('/carbon/credits/balance'),

  getTransactions: () =>
    http.get('/carbon/transactions'),

  getReductionSuggestions: () =>
    http.get('/carbon/reduction'),

  getStats: () =>
    http.get('/carbon/stats')
}

// 能耗分析 API
export const energyApi = {
  getOverview: () =>
    http.get('/energy/overview'),

  getConsumption: (params?: any) =>
    http.get('/energy/consumption', params),

  recordConsumption: (data: any) =>
    http.post('/energy/consumption', data),

  getTotalConsumption: (params?: any) =>
    http.get('/energy/consumption/total', params),

  getByCategory: (params?: any) =>
    http.get('/energy/consumption/by-entity', params),

  getTrend: (params?: any) =>
    http.get('/energy/consumption/trend', params),

  getBenchmark: () =>
    http.get('/energy/benchmark/compare'),

  createBenchmark: (data: any) =>
    http.post('/energy/benchmark', data),

  getRanking: () =>
    http.get('/energy/ranking'),

  getStats: () =>
    http.get('/energy/stats')
}

// 负荷预测 API
export const loadForecastingApi = {
  createForecast: (data: any) =>
    http.post('/load-forecast', data),

  getForecasts: (params?: any) =>
    http.get('/load-forecast', params),

  getForecast: (id: number) =>
    http.get(`/load-forecast/${id}`),

  getPeakValley: (entityId?: string) =>
    http.get(`/load-forecast/peak-valley/${entityId || 'default'}`),

  getLoadCurve: (entityId?: string) =>
    http.get(`/load-forecast/curve/${entityId || 'default'}`),

  getDemandResponse: (entityId?: string) =>
    http.get(`/load-forecast/demand-response/${entityId || 'default'}`),

  getStats: () =>
    http.get('/load-forecast/stats')
}

// 智能控制 API
export const controlApi = {
  getSystems: () =>
    http.get('/control/systems'),

  getSystem: (id: number) =>
    http.get(`/control/systems/${id}`),

  createSystem: (data: any) =>
    http.post('/control/systems', data),

  startControl: (id: number) =>
    http.post(`/control/systems/${id}/start`),

  stopControl: (id: number) =>
    http.post(`/control/systems/${id}/stop`),

  executeControl: (id: number, command: any) =>
    http.post(`/control/systems/${id}/execute`, command),

  getControlHistory: (id: number) =>
    http.get(`/control/systems/${id}/history`),

  getOptimizationSuggestions: (id: number) =>
    http.get(`/control/systems/${id}/optimization`),

  getStatus: () =>
    http.get('/control/status')
}

// 数字孪生 API
export const digitalTwinApi = {
  getTwins: (twinLevel?: string) =>
    http.get('/digital-twin', { twin_level: twinLevel }),

  getTwin: (id: number) =>
    http.get(`/digital-twin/${id}`),

  createTwin: (data: any) =>
    http.post('/digital-twin', data),

  updateRealtime: (id: number, data: any) =>
    http.post(`/digital-twin/${id}/realtime`, data),

  getRealtime: (id: number) =>
    http.get(`/digital-twin/${id}/realtime`),

  get3DModel: (id: number) =>
    http.get(`/digital-twin/${id}/3d-model`),

  simulate: (id: number, params: any) =>
    http.post(`/digital-twin/${id}/simulate`, params),

  getSimulationResult: (simId: number) =>
    http.get(`/digital-twin/simulate/${simId}`),

  getPredictions: (id: number) =>
    http.get(`/digital-twin/${id}/predict`),

  getStatistics: (id: number) =>
    http.get(`/digital-twin/${id}/statistics`)
}

// 能效优化 API
export const energyEfficiencyApi = {
  getSystems: () =>
    http.get('/energy-efficiency/systems'),

  getSystem: (id: number) =>
    http.get(`/energy-efficiency/systems/${id}`),

  createSystem: (data: any) =>
    http.post('/energy-efficiency/systems', data),

  optimize: (systemId: number, optimizationType?: string) =>
    http.post(`/energy-efficiency/systems/${systemId}/optimize`, { optimization_type: optimizationType }),

  getOptimizationResult: (resultId: number) =>
    http.get(`/energy-efficiency/optimize/${resultId}`),

  applyOptimization: (systemId: number, resultId: number) =>
    http.post(`/energy-efficiency/systems/${systemId}/apply`, { result_id: resultId }),

  getMetrics: (systemId: number) =>
    http.get(`/energy-efficiency/systems/${systemId}/metrics`),

  getTrend: (systemId: number, days?: number) =>
    http.get(`/energy-efficiency/systems/${systemId}/trend`, { days })
}

// 存储优化 API
export const storageOptimizationApi = {
  // 存储分析
  getAnalysis: () =>
    http.get('/storage-optimization/analysis'),

  getStats: () =>
    http.get('/storage-optimization/stats'),

  // 优化建议
  getRecommendations: () =>
    http.get('/storage-optimization/recommendations'),

  applyRecommendation: (id: string) =>
    http.post(`/storage-optimization/recommendations/${id}/apply`),

  dismissRecommendation: (id: string) =>
    http.delete(`/storage-optimization/recommendations/${id}`),

  // 清理任务
  getCleanupTasks: () =>
    http.get('/storage-optimization/cleanup-tasks'),

  createCleanupTask: (data: any) =>
    http.post('/storage-optimization/cleanup-tasks', data),

  runCleanupTask: (taskId: string) =>
    http.post(`/storage-optimization/cleanup-tasks/${taskId}/run`),

  cancelCleanupTask: (taskId: string) =>
    http.post(`/storage-optimization/cleanup-tasks/${taskId}/cancel`),

  deleteCleanupTask: (taskId: string) =>
    http.delete(`/storage-optimization/cleanup-tasks/${taskId}`),

  quickCleanup: () =>
    http.post('/storage-optimization/cleanup/quick'),

  // 容量预测
  getPrediction: (days?: number) =>
    http.get('/storage-optimization/prediction', { days }),

  // 存储策略
  getPolicies: () =>
    http.get('/storage-optimization/policies'),

  createPolicy: (data: any) =>
    http.post('/storage-optimization/policies', data),

  updatePolicy: (policyId: string, data: any) =>
    http.put(`/storage-optimization/policies/${policyId}`, data),

  deletePolicy: (policyId: string) =>
    http.delete(`/storage-optimization/policies/${policyId}`),

  getPolicyLogs: (policyId?: string) =>
    http.get('/storage-optimization/policies/logs', { policy_id: policyId })
}
