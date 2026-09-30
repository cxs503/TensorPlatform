import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  const loading = ref(false)
  const notifications = ref<any[]>([])

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function setLoading(value: boolean) {
    loading.value = value
  }

  async function fetchNotifications() {
    try {
      const res = await api.alert.getList({ limit: 5 })
      notifications.value = res.alerts || []
    } catch (error) {
      console.error('获取通知失败:', error)
    }
  }

  return {
    sidebarCollapsed,
    loading,
    notifications,
    toggleSidebar,
    setLoading,
    fetchNotifications
  }
})
