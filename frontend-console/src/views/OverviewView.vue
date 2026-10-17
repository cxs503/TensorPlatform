<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>科研总览 <span class="tf-hero-mint">· science factory</span></template>
      <template #sub>
        场景阶梯、Run 登记、执行与审批的全部状态，皆来自平台 API 实测，无任何手抄数字。
      </template>
      <template #extra>
        <span>Run 总数 <b class="tf-hero-mint">{{ runTotal }}</b></span>
        <span>待人审 <b class="tf-hero-mint">{{ health.pending_approvals ?? '—' }}</b></span>
        <span>Executor
          <b class="tf-hero-mint">{{ aliveExecutors }}/{{ health.executors?.length ?? 0 }}</b> 存活
        </span>
      </template>
    </HeroPanel>

    <el-row :gutter="16" class="tf-ov-row">
      <el-col :span="16">
        <div class="tf-card">
          <div class="tf-card-title">科研场景（阶梯实况）</div>
          <div v-if="!scenarios.length && !loading" class="tf-ov-empty">
            暂无场景。场景行由已注册且验证通过的 Run 实值生成（fail-closed）。
          </div>
          <div
            v-for="s in scenarios"
            :key="s.scenario_key"
            class="tf-ov-scenario"
            @click="$router.push(`/scenario/${s.scenario_key}`)"
          >
            <div class="tf-ov-s-head">
              <span class="tf-ov-s-name">{{ s.display_name || s.scenario_key }}</span>
              <StatusBadge :status="s.stage_status?.[s.current_stage]?.state || 'pending'"
                           :label="`${s.current_stage} 进行中`" />
            </div>
            <div class="tf-ov-s-meta">
              <span class="tf-mono">{{ s.scenario_key }}</span>
              <span v-if="s0checks(s) !== null">
                S0 检查 <b>{{ s.stage_status?.S0?.checks_passed }}/{{ s.stage_status?.S0?.checks_total }}</b>
              </span>
              <span>证据 run <span class="tf-mono">{{ s.stage_status?.S0?.evidence_run_id || '—' }}</span></span>
            </div>
          </div>
        </div>
      </el-col>

      <el-col :span="8">
        <div class="tf-card">
          <div class="tf-card-title">栈健康（实测）</div>
          <div class="tf-ov-health">
            <div class="tf-ov-h-row">
              <span>数据库</span>
              <StatusBadge :status="health.database?.ok ? 'succeeded' : 'failed'"
                           :label="health.database?.ok ? `${health.database.latency_ms} ms` : '不可用'" />
            </div>
            <div class="tf-ov-h-row">
              <span>对象存储</span>
              <StatusBadge :status="health.object_store?.ok ? 'succeeded' : 'failed'"
                           :label="health.object_store?.ok ? `${health.object_store.latency_ms} ms` : '不可用'" />
            </div>
            <div class="tf-ov-h-row" v-for="e in health.executors || []" :key="e.executor_id">
              <span class="tf-mono">{{ e.executor_id }}</span>
              <StatusBadge :status="e.alive ? 'succeeded' : 'failed'"
                           :label="e.alive ? '心跳正常' : '失联'" />
            </div>
          </div>
        </div>

        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">Run 登记（按状态）</div>
          <div class="tf-ov-h-row" v-for="(n, st) in health.runs_by_status || {}" :key="st">
            <StatusBadge :status="st" />
            <b>{{ n }}</b>
          </div>
        </div>
      </el-col>
    </el-row>

    <ScienceWarning
      v-if="warnings.length"
      :limitations="warnings"
      style="margin-top: 16px"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { scenarioApi, systemApi, runApi } from '@/api'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ScienceWarning from '@/components/ScienceWarning.vue'

const scenarios = ref<any[]>([])
const health = ref<any>({})
const runTotal = ref(0)
const loading = ref(false)

const aliveExecutors = computed(
  () => (health.value.executors || []).filter((e: any) => e.alive).length
)

const warnings = computed(() => {
  const out: string[] = []
  const h = health.value
  if (h.object_store && !h.object_store.ok) {
    out.push(`对象存储不可达（${h.object_store.error || '未知错误'}）——工件上传/下载将失败。`)
  }
  if (h.executors && h.executors.length && aliveExecutors.value === 0) {
    out.push('全部 executor 心跳失联——新触发的执行将排队等待。')
  }
  return out
})

function s0checks(s: any): number | null {
  const st = s.stage_status?.S0
  return st && st.checks_total ? st.checks_total : null
}

async function refresh() {
  const [s, h, r] = await Promise.all([
    scenarioApi.list(),
    systemApi.health(),
    runApi.list({ limit: 1 }),
  ])
  scenarios.value = s.items || []
  health.value = h
  runTotal.value = r.total ?? 0
}

const { start } = usePolling(refresh, 10000)
onMounted(async () => {
  loading.value = true
  await refresh().catch(() => {})
  loading.value = false
  start()
})
</script>

<style scoped lang="scss">
.tf-ov-row { margin-top: 4px; }
.tf-ov-scenario {
  border: 1px solid var(--tf-line-200);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: border-color 0.15s;
  &:hover { border-color: var(--tf-mint-600); }
}
.tf-ov-s-head { display: flex; justify-content: space-between; align-items: center; }
.tf-ov-s-name { font-weight: 600; color: var(--tf-navy-700); }
.tf-ov-s-meta { margin-top: 8px; display: flex; gap: 18px; font-size: 12.5px; color: var(--tf-ink-500); flex-wrap: wrap; }
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 12px 0; }
.tf-ov-health { display: flex; flex-direction: column; gap: 8px; }
.tf-ov-h-row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
</style>
