<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>目标平台<span class="tf-hero-mint">智能体协作平台</span></template>
      <template #sub>从明确目标到可验收交付，查看任务进展、执行记录与待处理事项。</template>
      <template #extra><el-button type="primary" @click="$router.push('/missions')">进入任务中心</el-button></template>
    </HeroPanel>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="updated" class="updated">数据更新时间：{{ updated }}</p>
    <div class="metrics">
      <div v-for="card in cards" :key="card.name" class="tf-card">
        <span>{{ card.name }}</span><strong>{{ card.value }}</strong>
      </div>
    </div>
    <div class="tf-card">
      <div class="tf-card-title">最近任务</div>
      <el-table :data="missions" empty-text="暂无任务。进入任务中心创建第一条任务。">
        <el-table-column label="目标" min-width="240"><template #default="{ row }">
          <router-link :to="`/missions/${row.id}`">{{ row.goal }}</router-link>
        </template></el-table-column>
        <el-table-column prop="repo" label="项目" width="180" />
        <el-table-column label="状态" width="150"><template #default="{ row }"><StatusBadge :status="row.status" /></template></el-table-column>
      </el-table>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { factoryApi, missionApi } from '@/api'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
const summary = ref<any>(null)
const missions = ref<any[]>([])
const error = ref('')
const updated = ref('')
const cards = computed(() => {
  const s = summary.value?.missions_by_status
  return [
    { name: '在线智能体 session', value: summary.value?.online_agent_sessions ?? '—' },
    { name: '排队任务', value: s ? s.queued || 0 : '—' },
    { name: '执行中', value: s ? (s.running || 0) + (s.claimed || 0) : '—' },
    { name: '待验收', value: summary.value?.pending_approvals ?? '—' },
    { name: '已交付', value: s ? s.succeeded || 0 : '—' },
  ]
})
async function load() {
  try {
    const [s, m] = await Promise.all([factoryApi.overview(), missionApi.list({ limit: 12 })])
    summary.value = s; missions.value = m.items; error.value = ''
    updated.value = new Date().toLocaleString()
  } catch (e: any) { error.value = `读取失败，显示的可能是上次结果：${e.message}` }
}
const { start } = usePolling(load, 8000)
onMounted(() => { load(); start() })
</script>
<style scoped>
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; }
.metrics strong { display: block; font-size: 32px; margin-top: 14px; color: var(--tf-navy-700); }
.updated { color: #687a8e; font-size: 12px; }
</style>
