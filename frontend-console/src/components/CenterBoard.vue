<template>
  <div class="center-board">
    <el-alert v-if="loadError" type="error" :closable="false" show-icon
              :title="`数据不可用（不补假值）：${loadError}`" />

    <el-row :gutter="16">
      <el-col v-for="m in headlineMetrics" :key="m.key" :span="6">
        <MetricCard :label="m.label" :value="display(m)" :hint="m.source" />
      </el-col>
    </el-row>

    <div class="tf-card" style="margin-top: 16px">
      <div class="tf-card-title">全部指标 · 口径可复核（后端实测 → SQL 口径同屏）</div>
      <el-table :data="payload ? payload.metrics : []" size="small" empty-text="等待后端载荷">
        <el-table-column label="指标" min-width="220">
          <template #default="{ row }">
            <span>{{ row.label }}</span>
            <span class="cb-dim tf-mono">{{ row.key }}</span>
          </template>
        </el-table-column>
        <el-table-column label="值" width="120" align="right">
          <template #default="{ row }">
            <span :class="['cb-val', row.status === 'not_implemented' ? 'cb-notimpl' : '']">{{ display(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="口径 / 数据源" min-width="320">
          <template #default="{ row }">
            <span class="tf-mono cb-src">{{ row.source }}</span>
            <div v-if="row.note" class="cb-dim">{{ row.note }}</div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="gates.length" class="tf-card" style="margin-top: 16px">
      <div class="tf-card-title">质量门态（逐门可解释）</div>
      <el-table :data="gates" size="small">
        <el-table-column prop="label" label="门" width="180" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span :class="['cb-gate', gateClass(row.state)]">{{ row.state }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="detail" label="门内计数" min-width="220" />
        <el-table-column prop="source" label="数据源" min-width="200">
          <template #default="{ row }"><span class="tf-mono cb-src">{{ row.source }}</span></template>
        </el-table-column>
      </el-table>
    </div>

    <div class="tf-card" style="margin-top: 16px">
      <div class="tf-card-title">未实现面（如实标注，不以图表占位）</div>
      <div v-if="!notImplemented.length" class="cb-empty">
        本中心无「未实现」声明项——所有指标均有实体支撑。
      </div>
      <div v-for="n in notImplemented" :key="n.key" class="cb-notimpl-row">
        <el-tag type="info" size="small">未实现</el-tag>
        <span class="cb-notimpl-label">{{ n.label }}</span>
        <span class="cb-dim">{{ n.reason }}</span>
      </div>
    </div>

    <div v-if="definitions.length" class="tf-card" style="margin-top: 16px">
      <div class="tf-card-title">口径定义（机器可读，来自后端）</div>
      <div v-for="[k, v] in definitions" :key="k" class="cb-def-row">
        <span class="tf-mono cb-def-key">{{ k }}</span>
        <span class="cb-dim">{{ v }}</span>
      </div>
    </div>

    <slot />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MetricCard from '@/components/MetricCard.vue'

interface CenterMetric {
  key: string
  label: string
  value: number | string | null
  unit?: string | null
  source: string
  note?: string | null
  status?: string
}

interface CenterPayload {
  center: string
  implemented?: boolean
  metrics: CenterMetric[]
  gates?: Array<Record<string, any>>
  not_implemented?: Array<Record<string, any>>
  definitions?: Record<string, string>
}

const props = defineProps<{
  payload: CenterPayload | null
  loadError?: string | null
}>()

/** 展示值：全部来自后端 payload；未实现 → 「未实现」，缺测 → 「—」，绝不用 0 冒充。 */
function display(m: CenterMetric): string {
  if (!m) return '—'
  if (m.status === 'not_implemented') return '未实现'
  if (m.value === null || m.value === undefined) return '—'
  if (m.unit === 'ratio' && typeof m.value === 'number') {
    return (m.value * 100).toFixed(1) + '%'
  }
  return String(m.value)
}

function gateClass(state: string): string {
  if (['有积压', '有欠账', '未实现', '无数据'].includes(state)) return 'cb-gate-warn'
  return 'cb-gate-ok'
}

const headlineMetrics = computed<CenterMetric[]>(() => (props.payload?.metrics || []).slice(0, 4))
const gates = computed(() => props.payload?.gates || [])
const notImplemented = computed(() => props.payload?.not_implemented || [])
const definitions = computed(() => Object.entries(props.payload?.definitions || {}))
</script>

<style scoped>
.cb-dim { color: #a5aeb8; font-size: 12px; }
.cb-src { font-size: 12px; color: #5b6b7c; }
.cb-val { font-weight: 600; font-variant-numeric: tabular-nums; }
.cb-notimpl { color: #b0740a; }
.cb-empty { font-size: 13px; color: #a5aeb8; padding: 6px 0; }
.cb-gate { font-weight: 600; }
.cb-gate-ok { color: #1c7f4b; }
.cb-gate-warn { color: #b0740a; }
.cb-notimpl-row { display: flex; align-items: baseline; gap: 10px; padding: 5px 0; font-size: 13px; }
.cb-notimpl-label { font-weight: 600; }
.cb-def-row { padding: 4px 0; font-size: 12px; line-height: 1.7; }
.cb-def-key { color: var(--tf-navy-700); margin-right: 8px; }
</style>