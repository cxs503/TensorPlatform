import http from './request'

// 认证 API
export const authApi = {
  login: (username: string, password: string) =>
    http.post('/auth/login', { username, password }),

  register: (data: { username: string; password: string; email: string }) =>
    http.post('/auth/register', data),

  logout: () =>
    http.post('/auth/logout'),

  refreshToken: (refreshToken: string) =>
    http.post('/auth/refresh', { refresh_token: refreshToken }),

  getCurrentUser: () =>
    http.get('/auth/me')
}

// 用户 API
export const userApi = {
  getProfile: () =>
    http.get('/auth/me'),

  updateProfile: (data: any) =>
    http.put('/users/me', data),

  changePassword: (data: { old_password: string; new_password: string }) =>
    http.post('/users/me/change-password', data)
}

// API密钥管理 API
export const apiKeyApi = {
  getList: () =>
    http.get('/api-keys'),

  get: (id: string) =>
    http.get(`/api-keys/${id}`),

  create: (data: { name: string; permissions?: string[]; rate_limit?: number; expires_days?: number }) =>
    http.post('/api-keys', data),

  revoke: (id: string) =>
    http.post(`/api-keys/${id}/revoke`),

  delete: (id: string) =>
    http.delete(`/api-keys/${id}`),

  getUsage: (id: string, limit?: number) =>
    http.get(`/api-keys/${id}/usage`, { limit })
}
