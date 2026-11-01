import axios, { AxiosInstance, AxiosRequestConfig, AxiosError } from 'axios'

// 科研运行台专用 token 键族（与旧前端的 foundation_* 键互不干扰）
const TOKEN_KEY = 'tensor_factory_token'
const REFRESH_KEY = 'tensor_factory_refresh_token'
const USER_KEY = 'tensor_factory_user'

const request: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(USER_KEY)
}

export function saveTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(TOKEN_KEY, accessToken)
  localStorage.setItem(REFRESH_KEY, refreshToken)
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

/** 免登录：用服务端 auto-login 换令牌（TP_AUTO_LOGIN=1 时可用），成功返回 true */
export async function tryAutoLogin(): Promise<boolean> {
  if (getToken()) return true
  try {
    const base = import.meta.env.VITE_API_BASE_URL || '/api/v1'
    const res = await axios.post(base + '/auth/auto-login', {}, { timeout: 15000 })
    const d: any = res.data || {}
    if (!d.access_token) return false
    saveTokens(d.access_token, d.refresh_token || '')
    if (d.user) {
      const u = { ...d.user, role: String(d.user.role || 'VIEWER').toUpperCase() }
      localStorage.setItem(USER_KEY, JSON.stringify(u))
    }
    return true
  } catch {
    return false
  }
}

function redirectToLogin(): void {
  // 免登录模式：token 失效时先清空并整页重载，由路由守卫自动重新 auto-login；
  // 连续失败 2 次才回落到登录页（避免无限重载）。
  const tries = Number(sessionStorage.getItem('tp_auto_login_tries') || '0')
  clearAuth()
  if (tries < 2) {
    sessionStorage.setItem('tp_auto_login_tries', String(tries + 1))
    window.location.reload()
    return
  }
  sessionStorage.removeItem('tp_auto_login_tries')
  const base = import.meta.env.BASE_URL
  const loginPath = base + 'login'
  if (window.location.pathname === loginPath) return
  const currentPath = '/' + window.location.pathname.slice(base.length) + window.location.search
  const redirect = currentPath !== '/' ? '?redirect=' + encodeURIComponent(currentPath) : ''
  window.location.href = loginPath + redirect
}

request.interceptors.request.use((config) => {
  if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
    const headers: any = config.headers
    if (typeof headers?.setContentType === 'function') headers.setContentType(false)
    else if (headers) {
      delete headers['Content-Type']
      delete headers['content-type']
    }
  }
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

request.interceptors.response.use(
  (response) => response.data,
  async (error: AxiosError) => {
    const originalRequest = error.config as AxiosRequestConfig & { _retry?: boolean }
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true
      const refreshToken = localStorage.getItem(REFRESH_KEY)
      if (refreshToken) {
        try {
          const res = await axios.post((import.meta.env.VITE_API_BASE_URL || '/api/v1') + '/auth/refresh', { refresh_token: refreshToken })
          saveTokens(res.data.access_token, res.data.refresh_token)
          if (originalRequest.headers) {
            originalRequest.headers.Authorization = `Bearer ${res.data.access_token}`
          }
          return request(originalRequest)
        } catch (refreshError) {
          clearAuth()
          redirectToLogin()
          return Promise.reject(refreshError)
        }
      }
      clearAuth()
      redirectToLogin()
    }

    const payload = error.response?.data as any
    const detail = payload?.message ?? payload?.detail
    const message = Array.isArray(detail)
      ? detail.map((item: any) => {
          const field = Array.isArray(item?.loc)
            ? item.loc.filter((part: any) => part !== 'body').join('.')
            : ''
          return field && item?.msg ? field + '：' + item.msg : (item?.msg || JSON.stringify(item))
        }).join('；')
      : (typeof detail === 'string'
          ? detail
          : (detail ? JSON.stringify(detail) : (error.message || '请求失败')))
    return Promise.reject(new Error(message))
  }
)

const http = {
  get: <T = any>(url: string, params?: any): Promise<T> => request.get(url, { params }),
  // 二进制图件必须走裸实例的 axios config（responseType），经包装层会被误作查询参数
  getBlob: (url: string): Promise<Blob> =>
    request.get(url, { responseType: 'blob' }) as unknown as Promise<Blob>,
  post: <T = any>(url: string, data?: any): Promise<T> => request.post(url, data),
  put: <T = any>(url: string, data?: any): Promise<T> => request.put(url, data),
  delete: <T = any>(url: string, data?: any): Promise<T> => request.delete(url, { data })
}

// 认证下载（携带 Bearer），落盘为浏览器下载
export async function authDownload(url: string, filename: string): Promise<void> {
  const res = await request.get(url, { responseType: 'blob' })
  const blobUrl = window.URL.createObjectURL(new Blob([res as any]))
  const a = document.createElement('a')
  a.href = blobUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(blobUrl)
}

export default http
export { request }
