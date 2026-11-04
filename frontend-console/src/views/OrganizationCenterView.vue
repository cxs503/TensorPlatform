<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>组织中心 <span class="tf-hero-mint">· mission &rarr; capability</span></template>
      <template #sub>
        组织运行面：任务在册/状态分布、待人审裁定、活跃会话与事件账——全部来自真实后端数据，逐项可溯源。
      </template>
      <template #extra>
        <span>任务在册 <b class="tf-hero-mint">{{ missions.length }}</b></span>
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="6">
        <MetricCard label="任务在册" :value="loaded ? missions.length : '—'" hint="tensor_dev_missions 实测" />
      </el-col>
      <el-col :span="6">
        <MetricCard label="待人审裁定" :value="loaded ? pendingApprovals.length : '—'"
                    :hint="pendingApprovals.length ? '需要 ADMIN 裁定' : '队列空'" />
      </el-col>
      <el-col :span="6">
        <MetricCard label="进行中会话" :value="loaded ? activeSessions : '—'" hint="claimed/running agent 会话" />
      </el-col>
      <el-col :span="6">
        <MetricCard label="事件账" :value="loaded ? (stats.events_total ?? 0) : '—'" hint="append-only 事件流累计" />
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="14">
        <div class="tf-card">
          <div class="tf-card-title">任务状态分布</div>
          <div v-if="!byStatus.length" class="tf-ov-empty">暂无任务（派发后此处出现真实分布）。</div>
          <div v-else class="org-status-row">
            <div v-for="s in byStatus" :key="s.status" class="org-status-cell">
              <StatusBadge :status="s.status" />
              <span class="org-status-n">{{ s.n }}</span>
            </div>
          </div>
          <el-divider style="margin: 12px 0" />
          <div class="tf-card-title">最近任务</div>
          <el-table :data="missions.slice(0, 8)" size="small">
            <el-table-column prop="id" label="#" width="70">
              <template #default="{ row }"><span class="tf-mono">m{{ row.id }}</span></template>
            </el-table-column>
            <el-table-column prop="repo" label="仓库" width="120" />
            <el-table-column prop="goal" label="目标" min-width="220" show-overflow-tooltip />
            <el-table-column label="状态" width="100">
              <template #default="{ row }"><StatusBadge :status="row.status" /></template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
      <el-col :span="10">
        <div class="tf-card">
          <div class="tf-card-title">待人审裁定（组织层阻塞点）</div>
          <el-table :data="pendingApprovals" size="small">
            <el-table-column prop="id" label="#" width="60" />
            <el-table-column prop="kind" label="类型" width="130" show-overflow-tooltip />
            <el-table-column prop="reason" label="理由" min-width="200" show-overflow-tooltip />
          </el-table>
          <div v-if="!pendingApprovals.length" class="tf-ov-empty">人审队列空——无阻塞。</div>
          <router-link class="tf-link" to="/approvals" style="display: inline-block; margin-top: 8px">
            前往人审队列 →
          </router-link>
        </div>
        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">最近会话</div>
          <el-table :data="sessions.slice(0, 6)" size="small">
            <el-table-column prop="id" label="#" width="60" />
            <el-table-column prop="agent_name" label="Agent" min-width="120" show-overflow-tooltip />
            <el-table-column label="状态" width="100">
              <template #default="{ row }"><StatusBadge :status="row.status" /></template>
            </el-table-column>
            <el-table-column prop="event_count" label="事件" width="70" />
          </el-table>
          <div v-if="!sessions.length" class="tf-ov-empty">尚无 agent 会话。</div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { missionApi, approvalApi, sessionApi, activityApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import MetricCard from '@/components/MetricCard.vue'

const loaded = ref(false)
const missions = ref<any[]>([])
const pendingApprovals = ref<any[]>([])
const sessions = ref<any[]>([])
const stats = ref<any>({})

const byStatus = computed(() => {
  const m = new Map<string, number>()
  for (const row of missions.value) m.set(row.status, (m.get(row.status) || 0) + 1)
  return [...m.entries()].map(([status, n]) => ({ status, n }))
})
const activeSessions = computed(
  () => sessions.value.filter((s) => ['claimed', 'running'].includes(s.status)).length
)

onMounted(async () => {
  const [m, a, s, act] = await Promise.all([
    missionApi.list({ limit: 200 }).catch(() => null),
    approvalApi.list({ status: 'pending' }).catch(() => null),
    sessionApi.list({ limit: 50 }).catch(() => null),
    activityApi.overview(1).catch(() => null),
  ])
  missions.value = m?.items || []
  pendingApprovals.value = a?.items || []
  sessions.value = s?.items || []
  stats.value = act?.stats || {}
  loaded.value = true
})
</script>

<style scoped>
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.org-status-row { display: flex; flex-wrap: wrap; gap: 14px; }
.org-status-cell { display: flex; align-items: center; gap: 6px; }
.org-status-n { font-weight: 600; font-size: 15px; }
</style>
