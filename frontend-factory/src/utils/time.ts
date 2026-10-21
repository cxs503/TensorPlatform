/** 时间工具：全部基于调用方传入的 tick 时钟（now ms），保证与轮询节奏一致。 */

export function parseT(iso?: string | null): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? null : t
}

export function fmtAgo(now: number, t: number | null): string {
  if (t == null) return '—'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 5) return '刚刚'
  if (s < 60) return `${s} 秒前`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} 分钟前`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.round(h / 24)} 天前`
}

export function fmtDur(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return '—'
  const s = Math.max(0, Math.round(sec))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分 ${s % 60} 秒`
  return `${Math.floor(m / 60)} 时 ${m % 60} 分`
}

export function fmtClock(t: number): string {
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}
