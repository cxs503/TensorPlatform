import http from './request'

// 部署 API
export const deploymentApi = {
  getList: (params?: any) =>
    http.get('/deployments', params),

  get: (id: number) =>
    http.get(`/deployments/${id}`),

  create: (data: any) =>
    http.post('/deployments', data),

  update: (id: number, data: any) =>
    http.put(`/deployments/${id}`, data),

  delete: (id: number) =>
    http.delete(`/deployments/${id}`),

  start: (id: number) =>
    http.post(`/deployments/${id}/start`),

  stop: (id: number) =>
    http.post(`/deployments/${id}/stop`),

  getMetrics: (id: number) =>
    http.get(`/deployments/${id}/metrics`)
}
