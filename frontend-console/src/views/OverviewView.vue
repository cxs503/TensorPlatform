<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>科研总览 <span class="tf-hero-mint">· science factory</span></template>
      <template #sub>
        场景阶梯、智能体活动、Run 登记与审批队列的实时全景——所有数字来自平台 API 实测，无任何手抄。
      </template>
      <template #extra>
        <span class="tf-ov-live"><span class="tf-ov-live-dot" />LIVE</span>
        <span>Run <b class="tf-hero-mint">{{ stats.runs_total }}</b></span>
        <span>工件 <b class="tf-hero-mint">{{ stats.artifacts }}</b></span>
        <span>执行器 <b class="tf-hero-mint">{{ aliveExecutors }}/{{ activity.executors?.length || 0 }}</b></span>
        <span>待人审 <b class="tf-hero-mint">{{ stats.pending_approvals ?? 0 }}</b></span>
      </template>
    </HeroPanel>

    <!-- 指标带 -->
    <el-row :gutter="16">
      <el-col :span="6"><MetricCard label="Run 登记" :value="stats.runs_total ?? '—'"
        :hint="`校验通过 ${stats.runs_passed ?? 0}`" /></el-col>
      <el-col :span="6"><MetricCard label="执行记录"
        :value="`${execSucceeded ?? 0}/${execFailed ?? 0}`" suffix="成功/失败"
        :hint="queuedNow ? `${queuedNow} 个在队列/执行中` : '当前无排队执行'" /></el-col>
      <el-col :span="6"><MetricCard label="智能体事件" :value="stats.events_total ?? '—'"
        :hint="`审计链 append-only · 展示最近 ${activity.recent_events?.length || 0} 条`" /></el-col>
      <el-col :span="6"><MetricCard label="审批队列" :value="stats.pending_approvals ?? 0"
        :hint="stats.pending_approvals ? '需要 ADMIN 裁定' : '无待审事项'" /></el-col>
    </el-row>

    <!-- 主行：智能体活动 + 场景阶梯 -->
    <el-row :gutter="16" class="tf-ov-row">
      <el-col :span="15">
        <AgentActivity
          :executors="activity.executors || []"
          :active-executions="activity.active_executions || []"
          :recent-executions="activity.recent_executions || []"
          :now="now"
        />
      </el-col>
      <el-col :span="9">
        <div class="tf-card tf-ov-ladder">
          <div class="tf-card-title">
            <span>科研阶梯</span>
            <span v-if="scenario" class="tf-ov-ladder-sub"
                  @click="$router.push(`/scenario/${scenario.scenario_key}`)">详情 →</span>
          </div>
          <template v-if="scenario">
            <div class="tf-ov-scen-name">{{ scenario.display_name || scenario.scenario_key }}</div>
            <StageLadder :current-stage="scenario.current_stage" :stage-status="scenario.stage_status || {}" />
          </template>
          <div v-else class="tf-ov-empty">暂无场景。场景行由已注册且验证通过的 Run 实值生成（fail-closed）。</div>
        </div>
      </el-col>
    </el-row>

    <!-- 次行：时间线 + 求解器矩阵 -->
    <el-row :gutter="16" class="tf-ov-row">
      <el-col :span="15">
        <EventTimeline :events="timelineEvents" :now="now" />
      </el-col>
      <el-col :span="9">
        <SolverMatrix :runs="runs" :now="now" />
      </el-col>
    </el-row>

    <!-- 事件流 -->
    <div class="tf-ov-row">
      <EventFeed :events="activity.recent_events || []" :now="now" />
    </div>

    <ScienceWarning v-if="warnings.length" :limitations="warnings" class="tf-ov-row" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { scenarioApi, activityApi, runApi } from '@/api'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import MetricCard from '@/components/MetricCard.vue'
import ScienceWarning from '@/components/ScienceWarning.vue'
import StageLadder from '@/components/StageLadder.vue'
import AgentActivity from '@/components/AgentActivity.vue'
import EventTimeline from '@/components/EventTimeline.vue'
import EventFeed from '@/components/EventFeed.vue'
import SolverMatrix from '@/components/SolverMatrix.vue'
import type { AgentEvent } from '@/utils/events'

const activity = ref<any>({})
const scenarios = ref<any[]>([])
const runs = ref<any[]>([])
const now = ref(Date.now())

const stats = computed(() => activity.value.stats || {})
const scenario = computed(() => scenarios.value[0] || null)

const aliveExecutors = computed(
  () => (activity.value.executors || []).filter((e: any) => e.alive).length
)
const execSucceeded = computed(() => stats.value.executions_by_status?.succeeded ?? 0)
const execFailed = computed(
  () => (stats.value.executions_by_status?.failed ?? 0) + (stats.value.executions_by_status?.cancelled ?? 0)
)
const queuedNow = computed(() =>
  (activity.value.active_executions || []).length || 0
)

// 时间线只用事件负载本身（created_at 全部落在轮询窗口内）
const timelineEvents = computed<AgentEvent[]>(() => activity.value.recent_events || [])

const warnings = computed(() => {
  const out: string[] = []
  const ex = activity.value.executors || []
  if (ex.length && ex.every((e: any) => !e.alive)) {
    out.push('全部 executor 心跳失联——新触发的执行将排队等待，请检查宿主守护进程（run_executor.sh）。')
  }
  const badRuns = (activity.value.stats?.runs_total ?? 0) - (activity.value.stats?.runs_passed ?? 0)
  if (badRuns > 0) {
    out.push(`${badRuns} 个已注册 Run 校验未通过——在 Run 列表按 validation 状态筛选查看。`)
  }
  return out
})

let nowTimer: number | undefined

async function refreshFast() {
  activity.value = await activityApi.overview()
}
async function refreshSlow() {
  const [s, r] = await Promise.all([
    scenarioApi.list(),
    runApi.list({ limit: 40 }),
  ])
  scenarios.value = s.items || []
  runs.value = r.items || []
}

const fast = usePolling(refreshFast, 5000)
const slow = usePolling(refreshSlow, 15000)

onMounted(async () => {
  nowTimer = window.setInterval(() => (now.value = Date.now()), 1000)
  await Promise.allSettled([refreshFast(), refreshSlow()])
  fast.start()
  slow.start()
})
onUnmounted(() => {
  if (nowTimer) window.clearInterval(nowTimer)
})
</script>

<style scoped lang="scss">
.tf-ov-row { margin-top: 16px; }
.tf-ov-live {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; font-weight: 700; letter-spacing: 1px;
  color: var(--tf-mint-400);
  border: 1px solid rgba(109, 225, 191, 0.45); border-radius: 999px;
  padding: 2px 9px;
}
.tf-ov-live-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--tf-mint-400);
  animation: tf-ov-blink 1.6s ease-in-out infinite;
}
@keyframes tf-ov-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
.tf-ov-ladder { height: 100%; }
.tf-ov-ladder-sub { margin-left: auto; font-size: 12px; color: var(--tf-mint-600); font-weight: 500; cursor: pointer; }
.tf-ov-scen-name { font-weight: 600; color: var(--tf-navy-700); margin-bottom: 6px; }
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 12px 0; }
</style>
