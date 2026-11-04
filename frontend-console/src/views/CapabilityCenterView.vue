<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>能力中心 <span class="tf-hero-mint">· capability ledger</span></template>
      <template #sub>
        组织能力账：每个验收任务都应入账一条能力增量（新能力/扩展/缺陷修复/可复用知识/证伪），
        succeeded 未入账会在覆盖率上显半开——不粉饰。
      </template>
      <template #extra>
        <span>覆盖 <b class="tf-hero-mint">{{ coverageLabel }}</b></span>
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="6">
        <MetricCard label="能力增量总数" :value="summary.total_deltas ?? '—'"
                    :hint="`实质 ${summary.substantive_deltas ?? 0} · 无增量 ${summary.no_substantive_deltas ?? 0}`" />
      </el-col>
      <el-col :span="6">
        <MetricCard label="succeeded 任务" :value="summary.succeeded_missions ?? '—'" hint="覆盖分母（本 org）" />
      </el-col>
      <el-col :span="6">
        <MetricCard label="已入账 succeeded" :value="summary.covered_succeeded ?? '—'" hint="覆盖分子" />
      </el-col>
      <el-col :span="6">
        <MetricCard label="验收入账覆盖率" :value="coverageLabel" suffix=""
                    :hint="coverageHint" />
      </el-col>
    </el-row>

    <div class="tf-card" style="margin-top: 16px">
      <div class="tf-card-title">类别分布（六类账本）</div>
      <div v-if="!categoryRows.length" class="tf-ov-empty">
        尚无能力增量入账（任务完成并验收后，能力增量随 mission 回写入账）。
      </div>
      <div v-else class="cap-cat-row">
        <div v-for="c in categoryRows" :key="c.key" class="cap-cat-cell" :class="{ 'cap-cat-zero': !c.n }">
          <div class="cap-cat-n">{{ c.n }}</div>
          <el-tag size="small" :type="c.n ? tagType(c.key) : 'info'" effect="plain">{{ c.label }}</el-tag>
        </div>
      </div>
    </div>

    <div class="tf-card" style="margin-top: 16px">
      <div class="tf-card-title">
        增量流水
        <el-select v-model="category" size="small" clearable placeholder="全部类别"
                   style="margin-left: 12px; width: 170px" @change="load">
          <el-option v-for="c in CATEGORIES" :key="c.key" :label="c.label" :value="c.key" />
        </el-select>
      </div>
      <el-table :data="items" size="small">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column prop="mission_id" label="任务" width="90">
          <template #default="{ row }">
            <router-link class="tf-mono tf-link" :to="`/missions/${row.mission_id}`">m{{ row.mission_id }}</router-link>
          </template>
        </el-table-column>
        <el-table-column prop="mission_repo" label="仓库" width="120" />
        <el-table-column label="类别" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="tagType(row.category)" effect="plain">
              {{ labelOf(row.category) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="mission_title" label="任务成果" min-width="200" show-overflow-tooltip />
        <el-table-column prop="summary" label="能力增量" min-width="240" show-overflow-tooltip />
        <el-table-column prop="reviewer" label="入账人" width="100" show-overflow-tooltip />
        <el-table-column label="时间" width="150">
          <template #default="{ row }"><span class="tf-mono">{{ fmtTime(row.created_at) }}</span></template>
        </el-table-column>
      </el-table>
      <div v-if="!items.length" class="tf-ov-empty">该类别下暂无增量（真实账本，空即空）。</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { capabilityApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import MetricCard from '@/components/MetricCard.vue'

const CATEGORIES = [
  { key: 'new_capability', label: '新能力' },
  { key: 'capability_extension', label: '能力扩展' },
  { key: 'defect_fix', label: '缺陷修复' },
  { key: 'reusable_knowledge', label: '可复用知识' },
  { key: 'falsified_hypothesis', label: '证伪假设' },
  { key: 'no_substantive_delta', label: '无实质增量' },
]

const summary = ref<any>({})
const items = ref<any[]>([])
const category = ref('')

const categoryRows = computed(() =>
  CATEGORIES.map((c) => ({ ...c, n: summary.value.by_category?.[c.key] ?? 0 }))
)
const coverageLabel = computed(() =>
  summary.value.coverage_ratio == null ? '—' : `${Math.round(summary.value.coverage_ratio * 100)}%`
)
const coverageHint = computed(() =>
  summary.value.coverage_ratio == null
    ? '尚无 succeeded 任务'
    : summary.value.coverage_ratio < 1
      ? `${summary.value.covered_succeeded}/${summary.value.succeeded_missions} 入账——缺口即待补账`
      : 'succeeded 全部入账'
)

function labelOf(key: string) {
  return CATEGORIES.find((c) => c.key === key)?.label || key
}
function tagType(key: string) {
  if (key === 'new_capability') return 'success'
  if (key === 'falsified_hypothesis') return 'warning'
  if (key === 'no_substantive_delta') return 'info'
  return ''
}
function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

async function load() {
  const params = category.value ? { category: category.value } : { limit: 100 }
  const [s, d] = await Promise.all([
    capabilityApi.summary().catch(() => null),
    capabilityApi.deltas(params).catch(() => null),
  ])
  summary.value = s || {}
  items.value = d?.items || []
}

onMounted(load)
</script>

<style scoped>
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.cap-cat-row { display: flex; flex-wrap: wrap; gap: 18px; }
.cap-cat-cell { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.cap-cat-cell.cap-cat-zero { opacity: 0.45; }
.cap-cat-n { font-weight: 700; font-size: 20px; }
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
</style>
