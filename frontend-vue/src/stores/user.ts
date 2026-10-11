import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import http from '@/api/request'
import { api } from '@/api'
import type { UserInfo } from '@/types'
import { requiredPermissionForPath } from '@/config/modules'

export const useUserStore = defineStore('user', () => {
  const token = ref<string | null>(localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token'))
  const refreshToken = ref<string | null>(localStorage.getItem('foundation_refresh_token'))
  // 创建时即从 localStorage 恢复(Login.vue 登录后写入 foundation_user), 保证刷新后立即可用
  const userInfo = ref<UserInfo | null>((() => {
    const saved = localStorage.getItem('foundation_user')
    if (!saved) return null
    try { return JSON.parse(saved) as UserInfo } catch { return null }
  })())

  const isLoggedIn = computed(() => !!token.value)

  // 是否管理员(优先用后端计算的 is_admin, 兼容 role 大小写)
  const isAdmin = computed(
    () => !!userInfo.value?.is_admin
      || (userInfo.value?.role || '').split('.').pop()?.toLowerCase() === 'admin'
  )

  // 当前用户是否可访问某功能(path)。未登录/未加载→放行; admin→全放行;
  // features 为空/null→不限(向后兼容); 否则按白名单。
  function hasFeature(path: string): boolean {
    const u = userInfo.value
    if (!u) return true
    if (isAdmin.value) return true
    const feats = u.features
    if (!feats || feats.length === 0) return true
    return feats.some(f => path === f || path.startsWith(f + '/'))
  }

  function hasPermission(permission: string): boolean {
    const permissions = userInfo.value?.permissions
    // 兼容后端尚未返回该字段的短暂升级窗口；空数组则表示明确无权限。
    if (permissions === undefined) return true
    return permissions.includes(permission)
  }

  function canAccessPath(path: string): boolean {
    if (!hasFeature(path)) return false
    if (path.startsWith('/system')) return isAdmin.value
    const permission = requiredPermissionForPath(path)
    return !permission || hasPermission(permission)
  }

  async function login(username: string, password: string) {
    const res = await api.auth.login(username, password)
    token.value = res.access_token
    refreshToken.value = res.refresh_token
    localStorage.setItem('foundation_access_token', res.access_token)
    localStorage.setItem('foundation_token', res.access_token)
    localStorage.setItem('foundation_refresh_token', res.refresh_token || '')
    await fetchUserInfo()
    return res
  }

  async function fetchUserInfo() {
    try {
      const res = await api.user.getProfile()
      userInfo.value = res
      localStorage.setItem('foundation_user', JSON.stringify(res))
    } catch (error) {
      console.error('获取用户信息失败:', error)
    }
  }

  function logout() {
    token.value = null
    refreshToken.value = null
    userInfo.value = null
    localStorage.removeItem('foundation_access_token')
    localStorage.removeItem('foundation_token')
    localStorage.removeItem('foundation_refresh_token')
    localStorage.removeItem('foundation_user')
  }

  function setTokens(accessToken: string, newRefreshToken?: string) {
    token.value = accessToken
    localStorage.setItem('foundation_access_token', accessToken)
    localStorage.setItem('foundation_token', accessToken)
    if (newRefreshToken) {
      refreshToken.value = newRefreshToken
      localStorage.setItem('foundation_refresh_token', newRefreshToken)
    }
  }

  // 更新用户信息
  async function updateUser(data: Partial<UserInfo>) {
    try {
      const res = await http.put('/users/me', data)
      userInfo.value = { ...userInfo.value, ...res } as UserInfo
      localStorage.setItem('foundation_user', JSON.stringify(userInfo.value))
      return res
    } catch (error) {
      throw error
    }
  }

  // 修改密码
  async function changePassword(data: { old_password: string; new_password: string; confirm_password?: string }) {
    const { api } = await import('@/api')
    return api.user.changePassword({ old_password: data.old_password, new_password: data.new_password })
  }

  // 初始化时恢复用户信息
  function init() {
    const savedUser = localStorage.getItem('foundation_user')
    if (savedUser) {
      try {
        userInfo.value = JSON.parse(savedUser)
      } catch (e) {
        console.error('解析用户信息失败:', e)
      }
    }
  }

  return {
    token,
    refreshToken,
    userInfo,
    isLoggedIn,
    isAdmin,
    hasFeature,
    hasPermission,
    canAccessPath,
    login,
    logout,
    fetchUserInfo,
    setTokens,
    updateUser,
    changePassword,
    init
  }
})
