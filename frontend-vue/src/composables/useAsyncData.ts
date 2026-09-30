import { ref, type Ref } from 'vue'

/**
 * 通用异步数据加载 composable
 * 自动管理 loading / error / data 三态
 *
 * @example
 * const { data, loading, error, refresh } = useAsyncData(() => api.getList())
 */
export function useAsyncData<T>(
  fetcher: () => Promise<T>,
  options?: { immediate?: boolean }
) {
  const data: Ref<T | null> = ref(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function refresh() {
    loading.value = true
    error.value = null
    try {
      data.value = await fetcher()
    } catch (e: any) {
      error.value = e?.message || e?.toString() || '加载失败'
      data.value = null
    } finally {
      loading.value = false
    }
  }

  if (options?.immediate !== false) {
    refresh()
  }

  return { data, loading, error, refresh }
}
