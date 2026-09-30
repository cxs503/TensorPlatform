import http, { request } from './request'

// 算法商店 API
export const marketplaceApi = {
  getList: (params?: any) =>
    http.get('/marketplace/items', params),

  get: (id: number) =>
    http.get(`/marketplace/items/${id}`),

  getVersions: (id: number) =>
    http.get(`/marketplace/items/${id}/versions`),

  deploy: (id: number, data: any) =>
    http.post(`/marketplace/items/${id}/deploy`, data),

  getReviews: (id: number) =>
    http.get(`/marketplace/items/${id}/reviews`),

  addReview: (id: number, data: any) =>
    http.post(`/marketplace/items/${id}/reviews`, data),

  getCategories: () =>
    http.get('/marketplace/categories')
}

// 算法仓库 API
export const algorithmStoreApi = {
  getAdapters: () =>
    http.get('/algorithm-store/adapters'),

  getList: (params?: any) =>
    http.get('/algorithm-store/models', params),

  get: (id: number) =>
    http.get(`/algorithm-store/models/${id}`),

  getCompatibleDatasets: (id: number) =>
    http.get(`/algorithm-store/models/${id}/compatible-datasets`, { page: 1, size: 100 }),

  train: (id: number, data: any) =>
    http.post(`/algorithm-store/models/${id}/train`, data),

  // 控制辨识/MPC仿真可能需要数分钟，不能沿用普通页面的30秒请求超时。
  runControl: (id: number, data: any) =>
    request.post(`/algorithm-store/models/${id}/control-runs`, data, { timeout: 30 * 60 * 1000 }),

  getControlRuns: (id: number) =>
    http.get(`/algorithm-store/models/${id}/control-runs`),

  getAllControlRuns: () =>
    http.get('/algorithm-store/control-runs'),

  deleteControlRun: (runId: string) =>
    http.delete(`/algorithm-store/control-runs/${runId}`),

  predict: (id: number, data: any) =>
    http.post(`/algorithm-store/models/${id}/predict`, data),

  buildDocker: (id: number) =>
    http.post(`/algorithm-store/models/${id}/docker/build`),

  getDockerStatus: (id: number) =>
    http.get(`/algorithm-store/models/${id}/docker/status`),

  runDocker: (id: number) =>
    http.post(`/algorithm-store/models/${id}/docker/run`),

  predictDocker: (id: number, data: any) =>
    http.post(`/algorithm-store/models/${id}/docker/predict`, data),

  stopDocker: (id: number) =>
    http.post(`/algorithm-store/models/${id}/docker/stop`),

  create: (data: any) =>
    http.post('/algorithm-store/models', data),

  upload: (data: FormData) =>
    request.post('/algorithm-store/models/upload', data, {
      // 不手动指定 multipart Content-Type；浏览器需要自动附带 boundary。
      timeout: 10 * 60 * 1000
    }),

  uploadManual: (id: number, data: FormData) =>
    request.post(`/algorithm-store/models/${id}/manual`, data, { timeout: 2 * 60 * 1000 }),

  update: (id: number, data: any) =>
    http.put(`/algorithm-store/models/${id}`, data),

  delete: (id: number) =>
    http.delete(`/algorithm-store/models/${id}`),

  publish: (id: number) =>
    http.post(`/algorithm-store/algorithms/${id}/publish`),

  unpublish: (id: number) =>
    http.post(`/algorithm-store/algorithms/${id}/unpublish`),

  download: (id: number) =>
    http.post(`/algorithm-store/algorithms/${id}/download`),

  getVersions: (id: number) =>
    http.get(`/algorithm-store/algorithms/${id}/versions`),

  createVersion: (id: number, data: any) =>
    http.post(`/algorithm-store/algorithms/${id}/versions`, data),

  deleteVersion: (id: number, version: string) =>
    http.delete(`/algorithm-store/algorithms/${id}/versions/${version}`),

  downloadVersion: (id: number, version: string) =>
    http.post(`/algorithm-store/algorithms/${id}/versions/${version}/download`),

  // 依赖环境
  getDependencies: () =>
    http.get('/algorithm-store/dependencies'),

  getDependency: (id: number) =>
    http.get(`/algorithm-store/dependencies/${id}`),

  createDependency: (data: any) =>
    http.post('/algorithm-store/dependencies', data),

  updateDependency: (id: number, data: any) =>
    http.put(`/algorithm-store/dependencies/${id}`, data),

  deleteDependency: (id: number) =>
    http.delete(`/algorithm-store/dependencies/${id}`),

  getRuntimeBases: () =>
    http.get('/algorithm-store/runtime-bases'),

  createRuntimeBase: (data: any) =>
    http.post('/algorithm-store/runtime-bases', data),

  updateRuntimeBase: (id: number, data: any) =>
    http.put(`/algorithm-store/runtime-bases/${id}`, data),

  deleteRuntimeBase: (id: number) =>
    http.delete(`/algorithm-store/runtime-bases/${id}`),

  getDependencySets: () =>
    http.get('/algorithm-store/dependency-sets'),

  getSpecialPackages: () =>
    http.get('/algorithm-store/special-packages'),

  createDependencySet: (data: any) =>
    http.post('/algorithm-store/dependency-sets', data),

  updateDependencySet: (id: number, data: any) =>
    http.put(`/algorithm-store/dependency-sets/${id}`, data),

  deleteDependencySet: (id: number) =>
    http.delete(`/algorithm-store/dependency-sets/${id}`),

  getRuntimeBuilds: () =>
    http.get('/algorithm-store/runtime-builds'),

  createRuntimeBuild: (data: any) =>
    http.post('/algorithm-store/runtime-builds', data),

  rebuildRuntimeBuild: (id: number) =>
    http.post(`/algorithm-store/runtime-builds/${id}/rebuild`),

  decideRuntimeSourceInstall: (id: number, approved: boolean) =>
    http.post('/algorithm-store/runtime-builds/' + id + '/source-install-decision?approved=' + approved),

  deleteRuntimeBuild: (id: number) =>
    http.delete(`/algorithm-store/runtime-builds/${id}`),

  // 使用统计
  getUsageStats: (params?: any) =>
    http.get('/algorithm-store/stats/usage', params),
};

// 标准化组件 API
export const componentApi = {
  getComponents: (params?: any) =>
    http.get('/algorithm-store/components', params),

  getComponent: (id: number) =>
    http.get(`/algorithm-store/components/${id}`),

  createComponent: (data: any) =>
    http.post('/algorithm-store/components', data),

  updateComponent: (id: number, data: any) =>
    http.put(`/algorithm-store/components/${id}`, data),

  deleteComponent: (id: number) =>
    http.delete(`/algorithm-store/components/${id}`),

  submitForReview: (id: number) =>
    http.post(`/algorithm-store/components/${id}/submit-review`),

  approveComponent: (id: number, data?: any) =>
    http.post(`/algorithm-store/components/${id}/approve`, data || {}),

  rejectComponent: (id: number, data?: any) =>
    http.post(`/algorithm-store/components/${id}/reject`, data || {})
};

// 笔记本管理 API
export const notebookApi = {
  // 笔记本管理
  getList: (params?: { status?: string; kernel?: string }) =>
    http.get('/notebook', params),

  get: (id: string) =>
    http.get(`/notebook/${id}`),

  create: (data: { name: string; kernel?: string; resource_tier?: string; description?: string }) =>
    http.post('/notebook', data),

  delete: (id: string) =>
    http.delete(`/notebook/${id}`),

  start: (id: string) =>
    http.post(`/notebook/${id}/start`),

  stop: (id: string) =>
    http.post(`/notebook/${id}/stop`),

  // 内核管理
  getKernels: () =>
    http.get('/notebook/kernels'),

  restartKernel: (kernelId: string) =>
    http.post(`/notebook/kernels/${kernelId}/restart`),

  interruptKernel: (kernelId: string) =>
    http.post(`/notebook/kernels/${kernelId}/interrupt`),

  killKernel: (kernelId: string) =>
    http.delete(`/notebook/kernels/${kernelId}`),

  // 文件管理
  getFiles: (params?: { path?: string }) =>
    http.get('/notebook/files', params),

  createFolder: (data: { path: string; name: string }) =>
    http.post('/notebook/folders', data),

  deleteFile: (path: string) =>
    http.delete(`/notebook/files/${encodeURIComponent(path)}`),

  // 共享
  share: (notebookId: string, data: { users: string[]; permission: string; expires_at?: Date }) =>
    http.post(`/notebook/${notebookId}/share`, data),

  getShared: () =>
    http.get('/notebook/shared'),

  revokeShare: (notebookId: string) =>
    http.delete(`/notebook/${notebookId}/share`),

  // 统计
  getStats: () =>
    http.get('/notebook/stats')
}
