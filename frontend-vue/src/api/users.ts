import http from './request'

// 用户管理 API（仅管理员可用; 后端 require_admin 校验）
export interface UserManageItem {
  id: number
  username: string
  email: string
  full_name?: string | null
  role: string
  is_active: boolean
  features?: string[] | null
  is_admin?: boolean
  created_at?: string
}

export interface UserUpdatePayload {
  features?: string[]
  role?: string
  is_active?: boolean
  full_name?: string
}

export const userMgmtApi = {
  list: () => http.get('/users'),
  get: (id: number) => http.get(`/users/${id}`),
  update: (id: number, data: UserUpdatePayload) => http.put(`/users/${id}`, data),
}
