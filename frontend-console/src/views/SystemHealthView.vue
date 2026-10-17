<template>
  <div class="tf-page" v-if="health.database">
    <HeroPanel>
      <template #title>系统健康 <span class="tf-hero-mint">· live probes</span></template>
      <template #sub>数据库延迟、对象存储、executor 心跳与登记统计，全部逐次实测。</template>
      <template #extra>
        <span>综合 <StatusBadge :status="health.ok ? 'succeeded' : 'failed'"
                               :label="health.ok ? '正常' : '降级'" /></span>
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="8">
        <MetricCard label="数据库延迟" :value="health.database?.latency_ms ?? '—'" suffix="ms"
                    :hint="health.database?.ok ? 'SELECT 1 实测' : '不可用'" />
      </el-col>
      <el-col :span="8">
        <MetricCard label="对象存储延迟" :value="health.object_store?.latency_ms ?? '—'" suffix="ms"
                    :hint="`head_bucket ${health.object_store?.bucket || ''}`" />
      </el-col>
      <el-col :span="8">
        <MetricCard label="待人审" :value="health.pending_approvals ?? 0" hint="pending 审批数" />
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="12">
        <div class="tf-card">
          <div class="tf-card-title">Executors</div>
          <el-table :data="health.executors || []" size="small">
            <el-table-column prop="executor_id" label="ID" min-width="160">
              <template #default="{ row }"><span class="tf-mono">{{ row.executor_id }}</span></template>
            </el-table-column>
            <el-table-column prop="hostname" label="主机" width="120" />
            <el-table-column prop="version" label="版本" width="70" />
            <el-table-column label="心跳" width="170">
              <template #default="{ row }"><span class="tf-mono">{{ fmtTime(row.heartbeat_at) }}</span></template>
            </el-table-column>
            <el-table-column label="存活" width="90">
              <template #default="{ row }">
                <StatusBadge :status="row.alive ? 'succeeded' : 'failed'"
                             :label="row.alive ? '存活' : '失联'" />
              </template>
            </el-table-column>
          </el-table>
          <div v-if="!(health.executors || []).length" class="tf-ov-empty">
            尚无 executor 注册心跳（触发执行将排队等待）。
          </div>
        </div>
      </el-col>
      <el-col :span="12">
        <div class="tf-card">
          <div class="tf-card-title">执行（按状态）</div>
          <div class="tf-h-row" v-for="(n, st) in health.executions_by_status || {}" :key="st">
            <StatusBadge :status="st" />
            <b>{{ n }}</b>
          </div>
          <div class="tf-card-title" style="margin-top: 16px">Run 登记（按状态）</div>
          <div class="tf-h-row" v-for="(n, st) in health.runs_by_status || {}" :key="st">
            <StatusBadge :status="st" />
            <b>{{ n }}</b>
          </div>
        </div>
      </el-col>
    </el-row>

    <ScienceWarning v-if="!health.ok" style="margin-top: 16px"
      :limitations="[`系统降级：${health.object_store?.ok ? '' : '对象存储不可达 ' + (health.object_store?.error || '')}`]" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { systemApi } from '@/api'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import MetricCard from '@/components/MetricCard.vue'
import ScienceWarning from '@/components/ScienceWarning.vue'

const health = ref<any>({})

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

async function refresh() {
  health.value = await systemApi.health()
}

const { start } = usePolling(refresh, 10000)
onMounted(async () => {
  await refresh().catch(() => {})
  start()
})
</script>

<style scoped>
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.tf-h-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; font-size: 13px; }
</style>
