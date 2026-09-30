/**
 * 旧脚本版 API 辅助
 */
const API = '/api/v1'

import { normalizeApiTimeStrings } from '@/utils/platformTime'

// 从 localStorage 读取 token,构造带 Authorization 的 headers
function _authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const t = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
  return t ? { Authorization: 'Bearer ' + t, ...extra } : { ...extra }
}

// 把非 2xx 响应转成抛错,避免 4xx/5xx 被 .json() 静默吞成"成功"
async function _unwrap(r: Response, normalizeTime = true) {
  if (r.status === 401) {
    // token 失效/过期 → 清掉并跳登录页
    localStorage.removeItem('foundation_token')
    localStorage.removeItem('foundation_access_token')
    localStorage.removeItem('foundation_refresh_token')
    if (location.pathname !== '/login') location.href = '/login'
  }
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`
    try { const j = await r.json(); detail = j.detail || j.message || JSON.stringify(j).slice(0, 120) || detail } catch { /* 非 JSON 错误体 */ }
    throw new Error(detail)
  }
  const payload = await r.json()
  return normalizeTime ? normalizeApiTimeStrings(payload) : payload
}

export async function get<T = any>(p: string, params?: Record<string, any>, options?: { normalizeTime?: boolean }): Promise<T> {
  let url = API + p
  if (params) url += '?' + new URLSearchParams(params).toString()
  const r = await fetch(url, { headers: _authHeaders() })
  return _unwrap(r, options?.normalizeTime !== false)
}

export async function post<T = any>(
  p: string,
  d?: any,
  options?: { signal?: AbortSignal }
): Promise<T> {
  const r = await fetch(API + p, {
    method: 'POST',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(d ?? {}),
    signal: options?.signal
  })
  return _unwrap(r)
}

// multipart/form-data 提交(FormData 由浏览器自动设置 boundary,不要手动加 Content-Type)
export async function postForm<T = any>(p: string, fd: FormData): Promise<T> {
  const r = await fetch(API + p, {
    method: 'POST',
    headers: _authHeaders(),
    body: fd
  })
  return _unwrap(r)
}

export async function put<T = any>(p: string, d?: any): Promise<T> {
  const r = await fetch(API + p, {
    method: 'PUT',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(d ?? {})
  })
  return _unwrap(r)
}

export async function del<T = any>(p: string): Promise<T> {
  const r = await fetch(API + p, { method: 'DELETE', headers: _authHeaders() })
  return _unwrap(r)
}

export async function downloadFile(p: string, fallbackName: string): Promise<string> {
  const r = await fetch(API + p, { headers: _authHeaders() })
  if (!r.ok) {
    await _unwrap(r)
    throw new Error('文件下载失败')
  }
  const disposition = r.headers.get('Content-Disposition') || ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  let filename = fallbackName
  try { filename = encoded ? decodeURIComponent(encoded) : (plain || fallbackName) } catch { /* use fallback */ }
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
  return filename
}

export function fmt(n: any): string {
  return n == null ? '-' : Number(n).toLocaleString()
}

export function L<T = any>(url: string): Promise<T> {
  const t = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
  return fetch(API + url, t ? { headers: { Authorization: 'Bearer ' + t } } : {}).then(r => r.json())
}
