<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>能力中心 <span class="tf-hero-mint">· 能力台账 &amp; Delta 面</span></template>
      <template #sub>
        能力台账＝<span class="tf-mono">tensor_capability_deltas</span> 逐任务增量流水（六类词表与
        <span class="tf-mono">/tensor/capability</span> 同一契约）；Delta 面给出最近增量与类别分布，
        覆盖率口径＝已入账的 succeeded 任务 / succeeded 任务总数（不入账不算覆盖）。
      </template>
      <template #extra>
        <span>载荷生成 <b class="tf-hero-mint">{{ payload ? payload.generated_at : '—' }}</b></span>
        <span>流水展示 <b class="tf-hero-mint">{{ recentDeltas.length }}</b></span>
      </template>
    </HeroPanel>

    <CenterBoard :payload="payload" :load-error="loadError">
      <div class="tf-card" style="margin-top: 16px">
        <div class="tf-card-title">Delta 面 · 最近能力增量（逐条可溯源到 mission）</div>
        <el-table :data="recentDeltas" size="small" empty-text="台账为空（无增量入账）">
          <el-table-column prop="id" label="#" width="70" />
          <el-table-column prop="category" label="类别" width="170" show-overflow-tooltip />
          <el-table-column prop="summary" label="增量摘要" min-width="240" show-overflow-tooltip />
          <el-table-column prop="mission_repo" label="来源仓库" width="130" show-overflow-tooltip />
          <el-table-column label="来源任务" width="110">
            <template #default="{ row }"><span class="tf-mono">m{{ row.mission_id }}</span></template>
          </el-table-column>
          <el-table-column prop="reviewer" label="复核人" width="110" show-overflow-tooltip />
        </el-table>
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

const recentDeltas = computed(() => {
  const lists = (payload.value && payload.value.lists) || {}
  return lists.recent_deltas || []
})

onMounted(async () => {
  try {
    payload.value = await centersApi.capability()
  } catch (e: any) {
    loadError.value = e?.message || String(e)
  }
})
</script>