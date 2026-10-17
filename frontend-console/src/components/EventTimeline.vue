<template>
  <div class="tf-card">
    <div class="tf-card-title">
      <span>活动时间线</span>
      <span class="tf-et-sub">最近 {{ spanLabel }} · {{ events.length }} 个事件</span>
    </div>
    <div v-if="!events.length" class="tf-et-empty">暂无事件——触发一次执行后，智能体的每一步都会落在时间线上。</div>
    <svg v-else :viewBox="`0 0 ${W} ${H}`" class="tf-et-svg" preserveAspectRatio="xMidYMid meet">
      <!-- 小时网格 -->
      <g v-for="g in grid" :key="g.t">
        <line :x1="g.x" :y1="top" :x2="g.x" :y2="H - axisH + 6" class="tf-et-grid" />
        <text :x="g.x" :y="H - 6" class="tf-et-axis" text-anchor="middle">{{ g.label }}</text>
      </g>

      <!-- 类型行 -->
      <g v-for="(row, ri) in rows" :key="row.kind">
        <rect x="0" :y="rowY(ri) - rowH / 2" :width="W" :height="rowH"
              :class="['tf-et-rowbg', { alt: ri % 2 === 1 }]" rx="4" />
        <text x="8" :y="rowY(ri) + 4" class="tf-et-rowlabel">{{ kindLabel(row.kind) }}</text>
        <circle v-for="d in row.dots" :key="d.e.id"
                :cx="d.x" :cy="rowY(ri)" r="4.5"
                :fill="kindColor(row.kind)" class="tf-et-dot">
          <title>{{ d.title }}</title>
        </circle>
      </g>

      <!-- now 指针 -->
      <line v-if="nowX != null" :x1="nowX" :y1="top - 4" :x2="nowX" :y2="H - axisH + 6"
            class="tf-et-now" />
      <text v-if="nowX != null && nowX < W - 30" :x="nowX + 4" :y="top - 6" class="tf-et-nowlabel">现在</text>
    </svg>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { kindLabel, kindColor, summarizeEvent, type AgentEvent } from '@/utils/events'
import { parseT, fmtClock } from '@/utils/time'

const props = defineProps<{
  events: AgentEvent[]
  now: number
}>()

const W = 860
const rowH = 26
const top = 10
const axisH = 22
const LABEL_W = 100

const KIND_ORDER = [
  'plan', 'execution_started', 'run_registered', 'validation_seen',
  'decision', 'approval_requested', 'approval_decided', 'note', 'error',
]

// 时间窗：覆盖全部事件，下限 45 分钟，上限 6 小时
const window = computed(() => {
  const ts = props.events.map((e) => parseT(e.created_at)).filter((t): t is number => t != null)
  const t1 = props.now
  let t0 = ts.length ? Math.min(...ts) : t1 - 45 * 60 * 1000
  t0 = Math.min(t0, t1 - 45 * 60 * 1000)
  t0 = Math.max(t0, t1 - 6 * 60 * 60 * 1000)
  return { t0, t1 }
})

const spanLabel = computed(() => {
  const m = Math.round((window.value.t1 - window.value.t0) / 60000)
  return m < 60 ? `${m} 分钟` : `${Math.round(m / 60)} 小时`
})

const rows = computed(() => {
  const kinds = KIND_ORDER.filter((k) =>
    props.events.some((e) => e.kind === k)
  )
  return kinds.map((kind) => {
    const dots = props.events
      .filter((e) => e.kind === kind)
      .map((e) => {
        const t = parseT(e.created_at)
        const x = t == null ? null : xOf(t)
        return {
          e,
          x,
          title:
            `${kindLabel(kind)} · ${summarizeEvent(e)}\n` +
            `${new Date(t || 0).toLocaleString('zh-CN', { hour12: false })} · ${e.agent_name || 'agent'}`,
        }
      })
      .filter((d) => d.x != null) as { e: AgentEvent; x: number; title: string }[]
    return { kind, dots }
  })
})

const H = computed(() => top + rows.value.length * rowH + axisH)

function rowY(ri: number): number {
  return top + ri * rowH + rowH / 2
}

function xOf(t: number): number {
  const { t0, t1 } = window.value
  const frac = (t - t0) / Math.max(1, t1 - t0)
  return LABEL_W + frac * (W - LABEL_W - 14)
}

const grid = computed(() => {
  const { t0, t1 } = window.value
  const out: { x: number; label: string; t: number }[] = []
  const hour = 60 * 60 * 1000
  let t = Math.ceil(t0 / hour) * hour
  while (t <= t1) {
    out.push({ x: xOf(t), label: fmtClock(t), t })
    t += hour
  }
  return out
})

const nowX = computed(() => xOf(props.now))
</script>

<style scoped lang="scss">
.tf-et-sub { font-size: 12px; color: var(--tf-ink-500); font-weight: 400; margin-left: auto; }
.tf-et-empty { font-size: 13px; color: #a5aeb8; padding: 12px 0; }
.tf-et-svg { width: 100%; height: auto; display: block; }
.tf-et-grid { stroke: #edf1f6; stroke-width: 1; }
.tf-et-axis { font-size: 11px; fill: #8a95a2; font-family: 'JetBrains Mono', monospace; }
.tf-et-rowbg { fill: transparent; &.alt { fill: #f7f9fb; } }
.tf-et-rowlabel { font-size: 11.5px; fill: var(--tf-ink-500); }
.tf-et-dot { stroke: #fff; stroke-width: 1; opacity: 0.95; }
.tf-et-now { stroke: var(--tf-mint-600); stroke-width: 1.5; stroke-dasharray: 4 3; }
.tf-et-nowlabel { font-size: 11px; fill: var(--tf-mint-600); font-weight: 600; }
</style>
