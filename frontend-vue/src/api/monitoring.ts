import http from './request'

// 仪表盘 API
export const dashboardApi = {
  getStatistics: () =>
    http.get('/dashboard/statistics'),

  getRecentActivities: (limit: number = 10) =>
    http.get('/dashboard/recent-activities', { limit }),

  getOverview: () =>
    http.get('/dashboard/overview')
}

// 告警 API
export const alertApi = {
  getList: (params?: any) =>
    http.get('/alerts', params),

  get: (id: number) =>
    http.get(`/alerts/${id}`),

  acknowledge: (id: number, userId: number) =>
    http.post(`/alerts/${id}/acknowledge`, { user_id: userId }),

  resolve: (id: number, userId: number, comment?: string) =>
    http.post(`/alerts/${id}/resolve`, { user_id: userId, comment }),

  getRules: () =>
    http.get('/alerts/rules'),

  createRule: (data: any) =>
    http.post('/alerts/rules', data),

  getStats: () =>
    http.get('/alerts/stats')
}

// 日志服务 API
export const loggingApi = {
  search: (params?: any) =>
    http.get('/logs', params),

  getRecent: (limit?: number, level?: string) =>
    http.get('/logs/recent', { limit, level }),

  getAccess: (limit?: number) =>
    http.get('/logs/access', { limit }),

  getErrors: (limit?: number) =>
    http.get('/logs/errors', { limit }),

  getAudit: (limit?: number, userId?: string) =>
    http.get('/logs/audit', { limit, user_id: userId }),

  getStatistics: () =>
    http.get('/logs/statistics'),

  logEvent: (eventType: string, message: string, level?: string) =>
    http.post('/logs/event', { event_type: eventType, message, level }),

  logAudit: (action: string, resourceType: string, resourceId: string, details?: any) =>
    http.post('/logs/audit', { action, resource_type: resourceType, resource_id: resourceId, details }),

  getStatus: () =>
    http.get('/logs/status')
}

// 通知服务 API
export const notificationApi = {
  // 告警
  getAlerts: (params?: any) =>
    http.get('/notifications/alerts', params),

  getAlert: (id: string) =>
    http.get(`/notifications/alerts/${id}`),

  createAlert: (data: any) =>
    http.post('/notifications/alerts', data),

  resolveAlert: (id: string) =>
    http.post(`/notifications/alerts/${id}/resolve`),

  // 规则
  getRules: (enabled?: boolean) =>
    http.get('/notifications/rules', { enabled }),

  createRule: (data: any) =>
    http.post('/notifications/rules', data),

  deleteRule: (id: string) =>
    http.delete(`/notifications/rules/${id}`),

  // 渠道和状态
  getChannels: () =>
    http.get('/notifications/channels'),

  getStatus: () =>
    http.get('/notifications/status')
}

// 告警管理 API
export const alertManageApi = {
  // 告警规则
  getRules: (enabled?: boolean) =>
    http.get('/alerts/rules', { enabled }),

  getRule: (ruleId: string) =>
    http.get(`/alerts/rules/${ruleId}`),

  createRule: (data: any) =>
    http.post('/alerts/rules', data),

  updateRule: (ruleId: string, data: any) =>
    http.put(`/alerts/rules/${ruleId}`, data),

  deleteRule: (ruleId: string) =>
    http.delete(`/alerts/rules/${ruleId}`),

  // 告警触发
  triggerAlert: (data: any) =>
    http.post('/alerts/trigger', data),

  // 告警处理
  getAlerts: (params?: any) =>
    http.get('/alerts', params),

  acknowledgeAlert: (alertId: string, userId: string) =>
    http.post(`/alerts/${alertId}/acknowledge`, null, { params: { user_id: userId } }),

  resolveAlert: (alertId: string, userId: string, comment?: string) =>
    http.post(`/alerts/${alertId}/resolve`, null, { params: { user_id: userId, comment } }),

  escalateAlert: (alertId: string, toLevel: string, reason: string) =>
    http.post(`/alerts/${alertId}/escalate`, null, { params: { to_level: toLevel, reason } }),

  getEscalations: (alertId?: string) =>
    http.get('/alerts/escalations', { alert_id: alertId }),

  // 统计
  getStats: () =>
    http.get('/alerts/stats')
}

// 报告服务 API
export const reportApi = {
  getTemplates: () =>
    http.get('/reports/templates'),

  getList: (params?: any) =>
    http.get('/reports', params),

  get: (id: string) =>
    http.get(`/reports/${id}`),

  create: (data: any) =>
    http.post('/reports', data),

  generate: (id: string) =>
    http.post(`/reports/${id}/generate`),

  download: (id: string) =>
    http.get(`/reports/${id}/download`),

  // 快速报表
  quickEquipmentReport: (dateFrom: string, dateTo: string) =>
    http.get('/reports/quick/equipment-status', { date_from: dateFrom, date_to: dateTo }),

  quickEnergyReport: (dateFrom: string, dateTo: string) =>
    http.get('/reports/quick/energy-consumption', { date_from: dateFrom, date_to: dateTo }),

  quickAlertReport: (dateFrom: string, dateTo: string) =>
    http.get('/reports/quick/alert-statistics', { date_from: dateFrom, date_to: dateTo })
}
