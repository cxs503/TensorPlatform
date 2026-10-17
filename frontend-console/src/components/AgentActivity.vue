<template>
  <div class="tf-card">
    <div class="tf-card-title">
      <span class="tf-live-dot" :class="{ off: !anyAlive }" />
      <span>智能体实时活动</span>
      <span class="tf-aa-sub">{{ aliveCount }}/{{ executors.length }} 执行器在线</span>
    </div>

    <div v-if="!executors.length" class="tf-aa-empty">尚无执行器注册——触发执行后由守护进程自动上线。</div>

    <div class="tf-aa-exes">
      <div v-for="e in executors" :key="e.executor_id" class="tf-aa-ex" :class="{ dead: !e.alive }">
        <span class="tf-aa-pulse" :class="{ on: e.alive }" />
        <div class="tf-aa-ex-body">
          <div class="tf-aa-ex-name tf-mono">{{ e.executor_id }}</div>
          <div class="tf-aa-ex-meta">
            <span>心跳 {{ e.heartbeat_age_s != null ? Math.max(0, Math.round(e.heartbeat_age_s)) + ' 秒前' : '—' }}</span>
            <span v-if="e.version">v{{ e.version }}</span>
            <span v-for="c in e.capabilities || []" :key="c" class="tf-aa-chip tf-mono">{{ c }}</span>
          </div>
        </div>
        <StatusBadge :status="e.alive ? 'succeeded' : 'failed'" :label="e.alive ? '在线' : '失联'" />
      </div>
    </div>

    <!-- ── 当前执行（进行中） ── -->
    <div v-if="current" class="tf-aa-current" @click="$router.push(`/executions/${current.id}`)">
      <div class="tf-aa-cur-head">
        <span class="tf-mono tf-aa-cur-key">{{ current.execution_key }}</span>
        <StatusBadge :status="current.status" />
      </div>
      <div class="tf-aa-cur-bar">
        <div class="tf-aa-cur-fill" :class="{ over: pct > 100, indet: elapsed == null }"
             :style="{ width: (elapsed == null ? '100%' : Math.min(pct, 100) + '%') }" />
      </div>
      <div class="tf-aa-cur-meta">
        <template v-if="elapsed != null">
          <span>已运行 <b>{{ fmtDur(elapsed) }}</b></span>
          <span>预算 {{ budget }} 秒</span>
          <span v-if="pct > 90" class="tf-aa-warn">接近预算上限</span>
        </template>
        <template v-else>
          <span>排队中 · 等待执行器领取</span>
        </template>
        <span v-if="current.status === 'cancel_requested'" class="tf-aa-warn">取消请求已下发</span>
      </div>
      <pre v-if="tailLines" class="tf-aa-tail tf-mono">{{ tailLines }}</pre>
    </div>

    <!-- ── 空闲态 ── -->
    <div v-else class="tf-aa-idle">
      <div class="tf-aa-idle-line"><span class="tf-aa-pulse dim" /> 执行器空闲 · 等待任务</div>
      <div v-if="last" class="tf-aa-idle-last" @click="$router.push(`/executions/${last.id}`)">
        <span class="tf-aa-idle-cap">上一单</span>
        <span class="tf-mono">{{ last.execution_key }}</span>
        <StatusBadge :status="last.status" />
        <span v-if="lastChecks" class="tf-aa-idle-checks">{{ lastChecks }}</span>
        <span v-if="lastDur != null">耗时 {{ fmtDur(lastDur) }}</span>
        <span class="tf-aa-idle-ago">{{ fmtAgo(now, lastFinished) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'
import { fmtDur, fmtAgo, parseT } from '@/utils/time'

const props = defineProps<{
  executors: any[]
  activeExecutions: any[]
  recentExecutions: any[]
  now: number
}>()

const executors = computed(() => props.executors || [])
const aliveCount = computed(() => executors.value.filter((e: any) => e.alive).length)
const anyAlive = computed(() => aliveCount.value > 0)
const current = computed(() => (props.activeExecutions || [])[0] || null)
const last = computed(() => (props.recentExecutions || [])[0] || null)

const budget = computed(() => {
  const b = current.value?.budget?.max_wall_seconds
  return b || 900
})

const elapsed = computed(() => {
  const t = parseT(current.value?.started_at)
  return t == null ? null : (props.now - t) / 1000
})

const pct = computed(() =>
  elapsed.value == null ? 0 : (elapsed.value / budget.value) * 100
)

const tailLines = computed(() => {
  const raw = current.value?.log_tail || ''
  const lines = raw.split('\n').filter((l: string) => l.trim())
  return lines.slice(-3).join('\n')
})

const lastChecks = computed(() => {
  const s = last.value?.result_summary
  return s?.checks_total != null ? `${s.checks_passed}/${s.checks_total} 检查` : ''
})

const lastDur = computed(() => {
  const a = parseT(last.value?.started_at), b = parseT(last.value?.finished_at)
  return a != null && b != null ? (b - a) / 1000 : null
})

const lastFinished = computed(() => parseT(last.value?.finished_at))
</script>

<style scoped lang="scss">
.tf-aa-sub { font-size: 12px; color: var(--tf-ink-500); font-weight: 400; margin-left: auto; }
.tf-aa-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }

.tf-live-dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--tf-mint-600);
  animation: tf-pulse 1.6s ease-in-out infinite;
  &.off { background: #b9c2cc; animation: none; }
}

.tf-aa-exes { display: flex; flex-direction: column; gap: 8px; }
.tf-aa-ex {
  display: flex; align-items: center; gap: 10px;
  border: 1px solid var(--tf-line-200); border-radius: 8px; padding: 9px 12px;
  &.dead { background: #fafbfc; }
}
.tf-aa-ex-body { flex: 1; min-width: 0; }
.tf-aa-ex-name { color: var(--tf-navy-700); }
.tf-aa-ex-meta {
  margin-top: 3px; display: flex; gap: 10px; font-size: 12px;
  color: var(--tf-ink-500); flex-wrap: wrap; align-items: center;
}
.tf-aa-chip {
  background: rgba(109, 225, 191, 0.16); color: #1d7a5f;
  padding: 1px 7px; border-radius: 999px; font-size: 11px;
}

.tf-aa-pulse {
  width: 8px; height: 8px; border-radius: 50%; background: #c8d0d9; flex-shrink: 0;
  &.on { background: var(--tf-mint-400); animation: tf-pulse 1.6s ease-in-out infinite; }
  &.dim { background: var(--tf-mint-600); opacity: 0.5; animation: tf-pulse 3s ease-in-out infinite; }
}
@keyframes tf-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(109, 225, 191, 0.55); }
  55% { box-shadow: 0 0 0 6px rgba(109, 225, 191, 0); }
}

.tf-aa-current {
  margin-top: 12px; border: 1px solid rgba(109, 225, 191, 0.6);
  background: rgba(109, 225, 191, 0.06);
  border-radius: 8px; padding: 12px 14px; cursor: pointer;
  &:hover { border-color: var(--tf-mint-600); }
}
.tf-aa-cur-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.tf-aa-cur-key { color: var(--tf-navy-700); word-break: break-all; }
.tf-aa-cur-bar {
  margin-top: 10px; height: 8px; border-radius: 999px;
  background: var(--tf-line-200); overflow: hidden;
}
.tf-aa-cur-fill {
  height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, var(--tf-mint-600), var(--tf-mint-400));
  transition: width 1s linear;
  &.over { background: var(--tf-danger-600); }
  &.indet { background: repeating-linear-gradient(45deg, #c8d0d9 0 8px, #e3e9ef 8px 16px); animation: none; }
}
.tf-aa-cur-meta {
  margin-top: 8px; display: flex; gap: 14px; font-size: 12.5px;
  color: var(--tf-ink-500); flex-wrap: wrap;
  b { color: var(--tf-navy-700); }
}
.tf-aa-warn { color: var(--tf-amber-700); font-weight: 600; }
.tf-aa-tail {
  margin: 10px 0 0; background: var(--tf-navy-900); color: #9fc4b5;
  border-radius: 6px; padding: 8px 10px; font-size: 11px; line-height: 1.5;
  max-height: 90px; overflow: hidden; white-space: pre-wrap; word-break: break-all;
}

.tf-aa-idle {
  margin-top: 12px; border: 1px dashed var(--tf-line-200); border-radius: 8px; padding: 12px 14px;
}
.tf-aa-idle-line {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; color: var(--tf-ink-500);
}
.tf-aa-idle-last {
  margin-top: 10px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  font-size: 12.5px; color: var(--tf-ink-500); cursor: pointer;
  .tf-mono { color: var(--tf-navy-700); }
  &:hover .tf-mono { text-decoration: underline; }
}
.tf-aa-idle-cap {
  font-size: 11px; color: #fff; background: var(--tf-navy-700);
  padding: 1px 6px; border-radius: 4px;
}
.tf-aa-idle-checks { font-variant-numeric: tabular-nums; }
.tf-aa-idle-ago { margin-left: auto; }
</style>
