<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>Run 登记 <span class="tf-hero-mint">· run registry</span></template>
      <template #sub>
        TensorPhysics fail-closed 科研循环登记的全部 run（含工件 sha256 清单）；点行进入详情。
      </template>
    </HeroPanel>

    <div class="tf-card">
      <div class="tf-rl-toolbar">
        <el-input v-model="prefix" placeholder="run_id 前缀（如 wedge-icebreaking）" clearable
                  style="width: 320px" @change="load" />
        <el-select v-model="statusFilter" clearable placeholder="状态" style="width: 140px" @change="load">
          <el-option v-for="s in ['succeeded','failed','running','planned','cancelled']" :key="s" :label="s" :value="s" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </div>

      <el-table :data="items" size="small" @row-click="open">
        <el-table-column label="run_id" min-width="280">
          <template #default="{ row }">
            <span class="tf-mono tf-link">{{ row.run_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }"><StatusBadge :status="row.status" /></template>
        </el-table-column>
        <el-table-column label="验证" width="100">
          <template #default="{ row }"><StatusBadge :status="row.validation_status" /></template>
        </el-table-column>
        <el-table-column label="检查" width="90">
          <template #default="{ row }">
            <b>{{ row.checks_passed ?? '—' }}</b>/{{ row.checks_total ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column label="工件" width="60">
          <template #default="{ row }">{{ row.artifact_count }}</template>
        </el-table-column>
        <el-table-column prop="producer_revision" label="revision" width="110">
          <template #default="{ row }"><span class="tf-mono">{{ (row.producer_revision || '').slice(0, 8) }}</span></template>
        </el-table-column>
        <el-table-column label="登记时间" width="170">
          <template #default="{ row }"><span class="tf-mono">{{ fmtTime(row.created_at) }}</span></template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="total > pageSize"
        layout="prev, pager, next" :total="total" :page-size="pageSize"
        v-model:current-page="page" @current-change="load"
        style="margin-top: 12px; justify-content: flex-end"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { runApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const router = useRouter()
const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const prefix = ref('')
const statusFilter = ref('')

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

async function load() {
  const params: any = { limit: pageSize, offset: (page.value - 1) * pageSize }
  if (prefix.value) params.run_id_prefix = prefix.value.trim()
  if (statusFilter.value) params.status = statusFilter.value
  const res = await runApi.list(params)
  items.value = res.items || []
  total.value = res.total ?? 0
}

function open(row: any) {
  router.push(`/runs/${encodeURIComponent(row.run_id)}`)
}

onMounted(load)
</script>

<style scoped>
.tf-rl-toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
.tf-link { color: var(--tf-navy-700); cursor: pointer; }
</style>
