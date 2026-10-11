/**
 * API historical records contain both timezone-aware timestamps and legacy
 * PostgreSQL TIMESTAMP values. The latter were written while the platform
 * ran in UTC, but are serialized without an offset. Convert only those
 * values to an explicit China-time ISO string: both date formatters and
 * legacy tables that render the raw value then show the same clock time.
 */
export const PLATFORM_TIMEZONE = 'Asia/Shanghai'

const NAIVE_UTC_TIMESTAMP = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$/

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Object.prototype.toString.call(value) === '[object Object]'
}

function utcToPlatformIso(value: string): string {
  const utcDate = new Date(value.replace(' ', 'T') + 'Z')
  if (Number.isNaN(utcDate.getTime())) return value
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: PLATFORM_TIMEZONE,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(utcDate)
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(item => item.type === type)?.value || '00'
  const fraction = value.match(/(\.\d{1,6})$/)?.[1] || ''
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}:${part('second')}${fraction}+08:00`
}

export function normalizeApiTimeStrings<T>(payload: T): T {
  const seen = new WeakSet<object>()

  const visit = (value: unknown): unknown => {
    if (typeof value === 'string' && NAIVE_UTC_TIMESTAMP.test(value)) {
      return utcToPlatformIso(value)
    }
    if (!value || typeof value !== 'object') return value
    if (seen.has(value)) return value
    seen.add(value)

    if (Array.isArray(value)) {
      for (let index = 0; index < value.length; index += 1) value[index] = visit(value[index]) as never
      return value
    }
    if (isPlainObject(value)) {
      for (const key of Object.keys(value)) value[key] = visit(value[key])
    }
    return value
  }

  return visit(payload) as T
}

let timezoneDisplayInstalled = false

/**
 * The platform is operated in China time. Apply that display timezone once at
 * startup so legacy pages that already use Date#toLocaleString are consistent
 * without changing their business-time calculations.
 */
export function installPlatformTimeZone(): void {
  if (timezoneDisplayInstalled) return
  timezoneDisplayInstalled = true
  const nativeToLocaleString = Date.prototype.toLocaleString
  Date.prototype.toLocaleString = function (
    locales?: Intl.LocalesArgument,
    options?: Intl.DateTimeFormatOptions,
  ): string {
    return nativeToLocaleString.call(this, locales || "zh-CN", {
      timeZone: PLATFORM_TIMEZONE,
      ...(options || {}),
    })
  }
}
