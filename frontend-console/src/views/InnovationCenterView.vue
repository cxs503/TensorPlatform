<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>创新中心 <span class="tf-hero-mint">· 提案 &amp; 吸收率</span></template>
      <template #sub>
        创新实体（提案受理 / 自主探索任务线 / 创新产出账）<b>未实现</b>——本页如实标注，不用假数据占位；
        可测量的只有<b>代理面</b>：可复用增量吸收率（定义见下，后端 <span class="tf-mono">definitions.absorption_rate</span>）。
      </template>
      <template #extra>
        <span>实体实现 <b class="tf-hero-mint">{{ implementedLabel }}</b></span>
        <span>未实现面 <b class="tf-hero-mint">{{ notImplemented.length }}</b></span>
      </template>
    </HeroPanel>

    <CenterBoard :payload="payload" :load-error="loadError">
      <div class="tf-card" style="margin-top: 16px">
        <div class="tf-card-title">吸收证据 · 可复用增量流水（代理面，非创新实体本体）</div>
        <el-table :data="absorptionEvidence" size="small" empty-text="无同类增量（台账为空）">
          <el-table-column prop="id" label="#" width="70" />
          <el-table-column prop="category" label="类别" width="180" show-overflow-tooltip />
          <el-table-column prop="summary" label="增量摘要" min-width="260" show-overflow-tooltip />
          <el-table-column prop="mission_repo" label="来源仓库" width="130" show-overflow-tooltip />
          <el-table-column label="来源任务" width="110">
            <template #default="{ row }"><span class="tf-mono">m{{ row.mission_id }}</span></template>
          </el-table-column>
        </el-table>
      </div>

      <div class="tf-card" style="margin-top: 16px">
        <div class="tf-card-title">相邻的真实机制（已上线）</div>
        <div v-for="l in links" :key="l.to" class="innov-link">
          <router-link class="tf-link" :to="l.to">{{ l.label }}</router-link>
          <span class="innov-note">{{ l.desc }}</span>
        </div>
      </div>
    </CenterBoard>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { centersApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import CenterBoard from '@/components/CenterBoard.vue'

const payload = ref<any>(null)
const loadError = ref<string | null>(null)

const links = [
  { to: '/center/capability', label: '能力中心', desc: '能力增量台账（吸收分子的唯一真值源）' },
  { to: '/missions', label: '开发任务', desc: '任务在册（创新任务未来落此平面）' },
  { to: '/papers', label: '进展报告', desc: '研究产出线（已运行）' },
]

const implementedLabel = computed(() => {
  if (!payload.value) return '—'
  return payload.value.implemented ? '已实现' : '未实现'
})
const notImplemented = computed(() => (payload.value && payload.value.not_implemented) || [])
const absorptionEvidence = computed(() => {
  const lists = (payload.value && payload.value.lists) || {}
  return lists.absorption_evidence || []
})

onMounted(async () => {
  try {
    payload.value = await centersApi.innovation()
  } catch (e: any) {
    loadError.value = e?.message || String(e)
  }
})
</script>

<style scoped>
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.innov-note { color: #a5aeb8; font-size: 12px; margin-left: 8px; }
.innov-link { padding: 6px 0; }
</style>