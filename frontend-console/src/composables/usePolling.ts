import { ref, onUnmounted } from 'vue'

/** 间隔轮询；返回 stop() 供手动停止（终态时用）。 */
export function usePolling(fn: () => Promise<void>, intervalMs = 5000) {
  const timer = ref<number | null>(null)

  function start() {
    stop()
    timer.value = window.setInterval(() => {
      fn().catch(() => {})
    }, intervalMs)
  }

  function stop() {
    if (timer.value !== null) {
      window.clearInterval(timer.value)
      timer.value = null
    }
  }

  onUnmounted(stop)
  return { start, stop }
}
