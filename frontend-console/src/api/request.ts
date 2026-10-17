import axios, { AxiosInstance, AxiosRequestConfig, AxiosError } from 'axios'

// 科研运行台专用 token 键族（与旧前端的 foundation_* 键互不干扰）
const TOKEN_KEY = 'console_token'
const REFRESH_KEY = 'console_refresh_token'
const USER_KEY = 'console_user'

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

function redirectToLogin(): void {
  if (window.location.pathname === '/login') return
  const currentPath = window.location.pathname + window.location.search
  const redirect = currentPath !== '/' ? '?redirect=' + encodeURIComponent(currentPath) : ''
  window.location.href = '/login' + redirect
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
          const res = await axios.post('/api/v1/auth/refresh', { refresh_token: refreshToken })
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
