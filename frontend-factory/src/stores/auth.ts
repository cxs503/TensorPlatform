import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api'
import { saveTokens, clearAuth, getToken } from '@/api/request'

const USER_KEY = 'tensor_factory_user'

interface PlatformUser {
  id: number
  username: string
  role: string
  is_admin?: boolean
  organization_id?: number | null
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<PlatformUser | null>(loadUser())
  const isLoggedIn = computed(() => !!getToken() && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'ADMIN' || !!user.value?.is_admin)
  const canOperate = computed(() =>
    ['ADMIN', 'MANAGER', 'ENGINEER'].includes(user.value?.role || '')
  )

  function loadUser(): PlatformUser | null {
    try {
      const raw = localStorage.getItem(USER_KEY)
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  }

  async function login(username: string, password: string) {
    const res = await authApi.login(username, password)
    saveTokens(res.access_token, res.refresh_token)
    const u: PlatformUser = res.user ?? {
      id: 0, username, role: String(res.role ?? 'VIEWER').toUpperCase(), is_admin: res.is_admin
    }
    u.role = String(u.role).toUpperCase()
    user.value = u
    localStorage.setItem(USER_KEY, JSON.stringify(u))
  }

  function logout() {
    clearAuth()
    user.value = null
  }

  return { user, isLoggedIn, isAdmin, canOperate, login, logout }
})
