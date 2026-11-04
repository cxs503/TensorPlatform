<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>质量中心 <span class="tf-hero-mint">· append-only 审计 + QA</span></template>
      <template #sub>
        组织操作账本（只增不改，PG 触发器级保障）与安全敏感事件、报告 QA 待检队列——真相不粉饰。
      </template>
      <template #extra>
        <span>审计事件 <b class="tf-hero-mint">{{ stats.total ?? '—' }}</b></span>
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="8">
        <MetricCard label="审计事件总数" :value="stats.total ?? '—'" hint="audit_logs 实测" />
      </el-col>
      <el-col :span="8">
        <MetricCard label="安全敏感事件" :value="loaded ? security.length : '—'"
                    :hint="`近 ${days} 天 · deny/越权/篡改类`" />
      </el-col>
      <el-col :span="8">
        <MetricCard label="报告 QA 待检" :value="loaded ? qaPending.length : '—'"
                    :hint="qaPending.length ? '等待质检裁定' : '无积压'" />
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="14">
        <div class="tf-card">
          <div class="tf-card-title">操作账本（append-only）</div>
          <el-table :data="logs" size="small">
            <el-table-column prop="id" label="#" width="70" />
            <el-table-column label="时间" width="160">
              <template #default="{ row }"><span class="tf-mono">{{ fmtTime(row.created_at) }}</span></template>
            </el-table-column>
            <el-table-column prop="action" label="动作" width="150" show-overflow-tooltip />
            <el-table-column prop="user_name" label="操作者" width="110" show-overflow-tooltip />
            <el-table-column prop="entity_type" label="对象类型" width="110" show-overflow-tooltip />
          </el-table>
          <div v-if="!logs.length" class="tf-ov-empty">账本空（或审计面不可用——如实显示，不造数）。</div>
        </div>
      </el-col>
      <el-col :span="10">
        <div class="tf-card">
          <div class="tf-card-title">安全敏感事件</div>
          <el-table :data="security" size="small">
            <el-table-column label="时间" width="150">
              <template #default="{ row }"><span class="tf-mono">{{ fmtTime(row.time) }}</span></template>
            </el-table-column>
            <el-table-column prop="type" label="类型" min-width="150" show-overflow-tooltip />
            <el-table-column prop="user" label="操作者" width="100" show-overflow-tooltip />
          </el-table>
          <div v-if="!security.length" class="tf-ov-empty">近 {{ days }} 天无安全敏感事件。</div>
        </div>
        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">报告 QA 待检队列</div>
          <el-table :data="qaPending" size="small">
            <el-table-column prop="report_id" label="报告" width="70" />
            <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
            <el-table-column label="建报" width="150">
              <template #default="{ row }"><span class="tf-mono">{{ fmtTime(row.created_at) }}</span></template>
            </el-table-column>
          </el-table>
          <div v-if="!qaPending.length" class="tf-ov-empty">QA 队列空——无待检报告。</div>
        </div>
        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">按动作分布（top）</div>
          <div v-if="!topActions.length" class="tf-ov-empty">无数据。</div>
          <div v-for="a in topActions" :key="a.action" class="qa-action-row">
            <span class="tf-mono">{{ a.action }}</span>
            <span class="qa-action-n">{{ a.n }}</span>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { auditApi, reportApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import MetricCard from '@/components/MetricCard.vue'

const loaded = ref(false)
const stats = ref<any>({})
const logs = ref<any[]>([])
const security = ref<any[]>([])
const qaPending = ref<any[]>([])
const days = 7

const topActions = computed(() =>
  Object.entries(stats.value.by_action || {})
    .map(([action, n]) => ({ action, n: n as number }))
    .sort((x, y) => y.n - x.n)
    .slice(0, 8)
)

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

onMounted(async () => {
  const [st, lg, sec, qa] = await Promise.all([
    auditApi.stats().catch(() => null),
    auditApi.logs({ limit: 20 }).catch(() => null),
    auditApi.security(days).catch(() => null),
    reportApi.qaPending().catch(() => null),
  ])
  stats.value = st || {}
  logs.value = lg?.items || []
  security.value = sec?.items || []
  qaPending.value = qa?.items || []
  loaded.value = true
})
</script>

<style scoped>
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.qa-action-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 13px; }
.qa-action-n { font-weight: 600; }
</style>
