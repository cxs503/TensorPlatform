import axios, { AxiosInstance, AxiosRequestConfig, AxiosError } from 'axios'
import { normalizeApiTimeStrings } from '@/utils/platformTime'

// 创建 axios 实例
const request: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 清除本地认证状态（避免循环依赖 stores/user）
function clearAuth(): void {
  localStorage.removeItem('foundation_token')
  localStorage.removeItem('foundation_refresh_token')
  localStorage.removeItem('foundation_access_token')
  localStorage.removeItem('foundation_user')
}

function saveTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem('foundation_token', accessToken)
  localStorage.setItem('foundation_refresh_token', refreshToken)
  localStorage.setItem('foundation_access_token', accessToken)  // 兼容旧代码
}

function redirectToLogin(): void {
  // 使用 window.location 避免循环依赖 router
  if (window.location.pathname === '/login') return
  const currentPath = window.location.pathname + window.location.search
  const redirect = currentPath !== '/' ? '?redirect=' + encodeURIComponent(currentPath) : ''
  window.location.href = '/login' + redirect
}

// 请求拦截器 — 自动注入 Bearer token
request.interceptors.request.use(
  (config) => {
    // Axios 实例默认携带 application/json；文件上传必须让浏览器根据 FormData
    // 自动生成 multipart/form-data 的 boundary，否则 FastAPI 无法解析 file/name 字段。
    if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
      const headers: any = config.headers
      // AxiosHeaders 的 false 会明确禁止沿用实例默认的 application/json，
      // 由浏览器在发送 FormData 时生成带 boundary 的 multipart Content-Type。
      if (typeof headers?.setContentType === 'function') headers.setContentType(false)
      else if (typeof headers?.delete === 'function') {
        headers.delete('Content-Type')
        headers.delete('content-type')
      } else if (headers) {
        delete headers['Content-Type']
        delete headers['content-type']
      }
    }
    const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token') || (import.meta.env.DEV ? 'dev-fallback-token' : '')
    if (token && token !== 'dev-fallback-token') {
      config.headers.Authorization = `Bearer ${token}`
    }
    // Dev mode: inject dummy token for whitelisted paths
    if (!token || token === 'dev-fallback-token') {
      const p = config.url || ''
      if (p.includes('/dashboard/') || p.includes('/mlflow/')) {
        config.headers.Authorization = 'Bearer dev-fallback-token'
      }
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器 — 统一处理 401 与 token 刷新
request.interceptors.response.use(
  (response) => normalizeApiTimeStrings(response.data),
  async (error: AxiosError) => {
    const originalRequest = error.config as AxiosRequestConfig & { _retry?: boolean }

    const isDashboardApi = (originalRequest.url || '').includes('/dashboard/') || (originalRequest.url || '').includes('/mlflow/')

    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true

      // Dashboard API: 不重定向，静默失败
      if (isDashboardApi) {
        return Promise.reject(new Error('Dashboard API 需要登录'))
      }

      // 尝试刷新 token
      const refreshToken = localStorage.getItem('foundation_refresh_token')
      if (refreshToken) {
        try {
          const res = await axios.post('/api/v1/auth/refresh', {
            refresh_token: refreshToken
          })

          const newAccessToken = res.data.access_token
          const newRefreshToken = res.data.refresh_token

          saveTokens(newAccessToken, newRefreshToken)

          // 重试原请求
          if (originalRequest.headers) {
            originalRequest.headers.Authorization = `Bearer ${newAccessToken}`
          }
          return request(originalRequest)
        } catch (refreshError) {
          // 刷新失败，清除认证并跳转登录
          clearAuth()
          redirectToLogin()
          return Promise.reject(refreshError)
        }
      } else {
        // 没有 refresh token，清除认证并跳转登录
        clearAuth()
        redirectToLogin()
      }
    }

    const payload = error.response?.data as any
    const detail = payload?.message ?? payload?.detail
    const message = Array.isArray(detail)
      ? detail.map((item: any) => {
          const field = Array.isArray(item?.loc) ? item.loc.filter((part: any) => part !== 'body').join('.') : ''
          return field && item?.msg ? field + '：' + item.msg : (item?.msg || JSON.stringify(item))
        }).join('；')
      : (typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : (error.message || '请求失败')))
    return Promise.reject(new Error(message))
  }
)

// HTTP 方法
const http = {
  get: <T = any>(url: string, params?: any): Promise<T> =>
    request.get(url, { params }),

  post: <T = any>(url: string, data?: any): Promise<T> =>
    request.post(url, data),

  put: <T = any>(url: string, data?: any): Promise<T> =>
    request.put(url, data),

  delete: <T = any>(url: string, data?: any): Promise<T> =>
    request.delete(url, { data }),

  patch: <T = any>(url: string, data?: any): Promise<T> =>
    request.patch(url, data)
}

export default http
export { request }
