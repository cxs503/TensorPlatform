import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '@/api'
import type { UserInfo } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  // 状态
  const token = ref<string | null>(localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token'))
  const refreshToken = ref<string | null>(localStorage.getItem('foundation_refresh_token'))
  const user = ref<UserInfo | null>(null)

  // 初始化时恢复用户信息
  const savedUser = localStorage.getItem('foundation_user')
  if (savedUser) {
    try {
      user.value = JSON.parse(savedUser)
    } catch (e) {
      console.error('解析用户信息失败:', e)
    }
  }

  // 计算属性
  const isAuthenticated = computed(() => !!token.value)

  // 登录
  async function login(username: string, password: string) {
    const res = await api.auth.login(username, password)

    token.value = res.access_token
    refreshToken.value = res.refresh_token || null
    localStorage.setItem('foundation_access_token', res.access_token)
    localStorage.setItem('foundation_token', res.access_token)
    if (res.refresh_token) {
      localStorage.setItem('foundation_refresh_token', res.refresh_token)
    }

    // 获取用户信息
    await fetchUserInfo()

    return res
  }

  // 获取用户信息
  async function fetchUserInfo() {
    try {
      const res = await api.auth.getCurrentUser()
      user.value = res
      localStorage.setItem('foundation_user', JSON.stringify(res))
    } catch (error) {
      console.error('获取用户信息失败:', error)
    }
  }

  // 登出
  function logout() {
    token.value = null
    refreshToken.value = null
    user.value = null
    localStorage.removeItem('foundation_access_token')
    localStorage.removeItem('foundation_token')
    localStorage.removeItem('foundation_refresh_token')
    localStorage.removeItem('foundation_user')
  }

  return {
    // 状态
    token,
    refreshToken,
    user,
    // 计算属性
    isAuthenticated,
    // 方法
    login,
    logout,
    fetchUserInfo
  }
})
