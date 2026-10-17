<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>科研运行总览 <span class="tf-hero-mint">· science factory</span></template>
      <template #sub>
        让 Agent 推进科研任务，让真实求解器执行计算，让验证与证据决定下一步。这里展示平台当前可观测到的执行、数据与审批状态。
      </template>
      <template #extra>
        <span class="tf-ov-live" :class="{ 'is-offline': !apiOnline }">
          <span class="tf-ov-live-dot" />{{ apiOnline ? 'API 已连接' : 'API 未连接' }}
        </span>
        <span>Run <b class="tf-hero-mint">{{ stats.runs_total ?? '—' }}</b></span>
        <span>工件 <b class="tf-hero-mint">{{ stats.artifacts ?? '—' }}</b></span>
        <span>执行器 <b class="tf-hero-mint">{{ aliveExecutors }}/{{ activity.executors?.length ?? '—' }}</b></span>
        <span>待审批 <b class="tf-hero-mint">{{ stats.pending_approvals ?? '—' }}</b></span>
      </template>
    </HeroPanel>

    <div class="tf-section-heading">
      <span>运行态势</span>
      <small>{{ lastUpdatedLabel ? '最近同步 ' + lastUpdatedLabel : '等待首次 API 同步' }}</small>
    </div>

    <!-- 指标来自 API；连接失败时显示破折号，不把未知状态伪装成零。 -->
    <el-row :gutter="14" class="tf-ov-metrics">
      <el-col :xs="12" :sm="12" :md="6">
        <MetricCard label="已登记 Run" :value="apiOnline ? (stats.runs_total ?? 0) : '—'"
          :hint="apiOnline ? `验证通过 ${stats.runs_passed ?? 0}` : '等待平台 API'" />
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <MetricCard label="执行记录" :value="apiOnline ? `${execSucceeded ?? 0}/${execFailed ?? 0}` : '—'"
          suffix="成功 / 失败"
          :hint="apiOnline ? (queuedNow ? `${queuedNow} 个排队或执行中` : '当前无活动执行') : '无法获取执行状态'" />
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <MetricCard label="Agent 事件" :value="apiOnline ? (stats.events_total ?? 0) : '—'"
          :hint="apiOnline ? `追加式事件记录 · 最近 ${activity.recent_events?.length || 0} 条` : '等待平台 API'" />
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <MetricCard label="待人工审批" :value="apiOnline ? (stats.pending_approvals ?? 0) : '—'"
          :hint="apiOnline ? (stats.pending_approvals ? '需要有权限的人员裁定' : '暂无待审批事项') : '无法获取审批状态'" />
      </el-col>
    </el-row>

    <div class="tf-section-heading">
      <span>计算工作区</span>
      <small>代码版本 · 分支 · 工作区变更</small>
    </div>
    <div class="tf-ov-row">
      <WorkspaceFamily :ws="workspace" :now="now" />
    </div>

    <div class="tf-section-heading">
      <span>Agent 与科研场景</span>
      <small>活动执行、执行器心跳和阶段门控</small>
    </div>
    <el-row :gutter="14" class="tf-ov-row">
      <el-col :xs="24" :md="15">
        <AgentActivity
          :executors="activity.executors || []"
          :active-executions="activity.active_executions || []"
          :recent-executions="activity.recent_executions || []"
          :now="now"
        />
      </el-col>
      <el-col :xs="24" :md="9">
        <div class="tf-card tf-ov-ladder">
          <div class="tf-card-title">
            <span>科研阶段门控</span>
            <span v-if="scenario" class="tf-ov-ladder-sub"
                  role="button" tabindex="0"
                  @click="$router.push(`/scenario/${scenario.scenario_key}`)"
                  @keydown.enter="$router.push(`/scenario/${scenario.scenario_key}`)">场景详情 →</span>
          </div>
          <template v-if="scenario">
            <div class="tf-ov-scen-name">{{ scenario.display_name || scenario.scenario_key }}</div>
            <StageLadder :current-stage="scenario.current_stage" :stage-status="scenario.stage_status || {}" />
          </template>
          <div v-else class="tf-ov-empty">
            {{ apiOnline ? '暂无可展示的科研场景。阶段晋级应由已登记的计算证据与验证门控决定。' : 'API 未连接，暂时无法读取科研场景。' }}
          </div>
        </div>
      </el-col>
    </el-row>

    <div class="tf-section-heading">
      <span>证据与求解器</span>
      <small>事件时间线 · 求解器运行登记</small>
    </div>
    <el-row :gutter="14" class="tf-ov-row">
      <el-col :xs="24" :md="15">
        <EventTimeline :events="timelineEvents" :now="now" />
      </el-col>
      <el-col :xs="24" :md="9">
        <SolverMatrix :runs="runs" :now="now" />
      </el-col>
    </el-row>

    <div class="tf-section-heading">
      <span>Agent 事件流</span>
      <small>按事件记录追踪决策、执行与验证</small>
    </div>
    <div class="tf-ov-row">
      <EventFeed :events="activity.recent_events || []" :now="now" />
    </div>

    <ScienceWarning v-if="warnings.length" :limitations="warnings" class="tf-ov-row" />
    <div v-if="!apiOnline" class="tf-ov-connection-note" role="status">
      <span class="tf-ov-connection-icon">!</span>
      <span><b>当前无法确认平台实时状态。</b> 页面保留最后一次已获取的数据，但不会把断连状态显示为实时在线。请检查登录状态、后端 API 与网络连接。</span>
      <el-button size="small" plain @click="retryConnection">重新连接</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { scenarioApi, activityApi, runApi, workspaceApi } from '@/api'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import MetricCard from '@/components/MetricCard.vue'
import ScienceWarning from '@/components/ScienceWarning.vue'
import StageLadder from '@/components/StageLadder.vue'
import AgentActivity from '@/components/AgentActivity.vue'
import EventTimeline from '@/components/EventTimeline.vue'
import EventFeed from '@/components/EventFeed.vue'
import SolverMatrix from '@/components/SolverMatrix.vue'
import WorkspaceFamily from '@/components/WorkspaceFamily.vue'
import type { AgentEvent } from '@/utils/events'

const activity = ref<any>({})
const scenarios = ref<any[]>([])
const runs = ref<any[]>([])
const workspace = ref<any>({})
const now = ref(Date.now())
const apiOnline = ref(false)
const lastUpdatedAt = ref<number | null>(null)

const stats = computed(() => activity.value.stats || {})
const scenario = computed(() => scenarios.value[0] || null)
const lastUpdatedLabel = computed(() => lastUpdatedAt.value
  ? new Date(lastUpdatedAt.value).toLocaleTimeString('zh-CN', { hour12: false })
  : '')

const aliveExecutors = computed(
  () => (activity.value.executors || []).filter((e: any) => e.alive).length
)
const execSucceeded = computed(() => stats.value.executions_by_status?.succeeded ?? 0)
const execFailed = computed(
  () => (stats.value.executions_by_status?.failed ?? 0) + (stats.value.executions_by_status?.cancelled ?? 0)
)
const queuedNow = computed(() => (activity.value.active_executions || []).length || 0)
const timelineEvents = computed<AgentEvent[]>(() => activity.value.recent_events || [])

const warnings = computed(() => {
  const out: string[] = []
  const ex = activity.value.executors || []
  if (apiOnline.value && ex.length && ex.every((e: any) => !e.alive)) {
    out.push('全部 executor 心跳失联——新触发的执行可能排队等待，请检查宿主守护进程（run_executor.sh）。')
  }
  const badRuns = (activity.value.stats?.runs_total ?? 0) - (activity.value.stats?.runs_passed ?? 0)
  if (apiOnline.value && badRuns > 0) {
    out.push(`${badRuns} 个已登记 Run 校验未通过——请在 Run 列表检查 validation 状态；登记成功不等于科学验证通过。`)
  }
  return out
})

let nowTimer: number | undefined

async function refreshFast() {
  try {
    activity.value = await activityApi.overview()
    apiOnline.value = true
    lastUpdatedAt.value = Date.now()
  } catch {
    apiOnline.value = false
  }
}
async function refreshSlow() {
  try {
    const [s, r, w] = await Promise.all([
      scenarioApi.list(),
      runApi.list({ limit: 40 }),
      workspaceApi.latest(),
    ])
    scenarios.value = s.items || []
    runs.value = r.items || []
    workspace.value = w || {}
  } catch {
    // Keep the last successfully fetched records; the connection banner describes the live status.
  }
}
async function retryConnection() {
  await Promise.allSettled([refreshFast(), refreshSlow()])
}

const fast = usePolling(refreshFast, 5000)
const slow = usePolling(refreshSlow, 15000)

onMounted(async () => {
  nowTimer = window.setInterval(() => (now.value = Date.now()), 1000)
  await retryConnection()
  fast.start()
  slow.start()
})
onUnmounted(() => {
  if (nowTimer) window.clearInterval(nowTimer)
})
</script>

<style scoped lang="scss">
.tf-ov-row { margin-top: 14px; }
.tf-ov-metrics { row-gap: 14px; }
.tf-ov-live {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .2px;
  color: var(--tf-mint-400);
  border: 1px solid rgba(117, 228, 194, .42);
  border-radius: 999px;
  padding: 5px 10px;
  white-space: nowrap;
}
.tf-ov-live.is-offline {
  color: #ffd2d2;
  border-color: rgba(255, 180, 180, .42);
  background: rgba(189, 62, 75, .12);
}
.tf-ov-live-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--tf-mint-400);
  box-shadow: 0 0 0 3px rgba(117, 228, 194, .12);
}
.tf-ov-live.is-offline .tf-ov-live-dot { background: #ff929b; box-shadow: 0 0 0 3px rgba(255, 146, 155, .12); }
.tf-ov-ladder { height: 100%; }
.tf-ov-ladder-sub {
  margin-left: auto;
  font-size: 12px;
  color: var(--tf-mint-600);
  font-weight: 600;
  cursor: pointer;
  border-radius: 4px;
}
.tf-ov-ladder-sub:focus-visible { outline: 2px solid var(--tf-teal-600); outline-offset: 3px; }
.tf-ov-scen-name { font-weight: 700; color: var(--tf-navy-700); margin-bottom: 8px; overflow-wrap: anywhere; }
.tf-ov-empty { font-size: 13px; color: var(--tf-ink-500); line-height: 1.75; padding: 12px 0; }
.tf-ov-connection-note {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
  padding: 13px 15px;
  border: 1px solid #efc9cd;
  border-radius: var(--tf-radius-md);
  background: #fff7f7;
  color: #79333b;
  font-size: 12px;
  line-height: 1.65;
}
.tf-ov-connection-note > span:nth-child(2) { flex: 1; min-width: 0; }
.tf-ov-connection-icon {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  color: #fff;
  background: var(--tf-danger-600);
  font-weight: 800;
}
@media (max-width: 600px) {
  .tf-ov-connection-note { align-items: flex-start; flex-wrap: wrap; }
  .tf-ov-connection-note :deep(.el-button) { margin-left: 38px; }
}
</style>
