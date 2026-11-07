<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>组织中心 <span class="tf-hero-mint">· 舰队 &amp; 战役实况</span></template>
      <template #sub>
        组织运行面：执行器舰队、Agent 会话、战役节点实况（in-flight / blocked / done）与任务在册——
        全部来自 <span class="tf-mono">/tensor/centers/organization</span> 的后端实测，每项指标同屏给出 SQL 口径。
      </template>
      <template #extra>
        <span>载荷生成 <b class="tf-hero-mint">{{ payload ? payload.generated_at : '—' }}</b></span>
        <span>指标项 <b class="tf-hero-mint">{{ payload ? payload.metrics.length : '—' }}</b></span>
      </template>
    </HeroPanel>

    <CenterBoard :payload="payload" :load-error="loadError">
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="12">
          <div class="tf-card">
            <div class="tf-card-title">战役阻塞节点（block_reason 原文）</div>
            <el-table :data="blockedNodes" size="small" empty-text="无阻塞节点（campaign_nodes.status=blocked 计数为 0）">
              <el-table-column prop="id" label="#" width="70" />
              <el-table-column prop="name" label="节点" min-width="180" show-overflow-tooltip />
              <el-table-column prop="block_reason" label="阻塞理由" min-width="200" show-overflow-tooltip />
              <el-table-column prop="consecutive_fails" label="连败" width="70" />
            </el-table>
          </div>
        </el-col>
        <el-col :span="12">
          <div class="tf-card">
            <div class="tf-card-title">战役在飞节点</div>
            <el-table :data="inflightNodes" size="small" empty-text="无在飞节点">
              <el-table-column prop="id" label="#" width="70" />
              <el-table-column prop="name" label="节点" min-width="180" show-overflow-tooltip />
              <el-table-column prop="reproduction_verdict" label="复现裁定" min-width="140" show-overflow-tooltip />
              <el-table-column prop="plan_id" label="计划" width="80" />
            </el-table>
          </div>
        </el-col>
      </el-row>

      <div class="tf-card" style="margin-top: 16px">
        <div class="tf-card-title">最近任务（tensor_dev_missions）</div>
        <el-table :data="recentMissions" size="small" empty-text="任务表为空">
          <el-table-column prop="id" label="#" width="80">
            <template #default="{ row }"><span class="tf-mono">m{{ row.id }}</span></template>
          </el-table-column>
          <el-table-column prop="repo" label="仓库" width="140" />
          <el-table-column prop="goal" label="目标" min-width="260" show-overflow-tooltip />
          <el-table-column label="状态" width="130">
            <template #default="{ row }"><StatusBadge :status="row.status" /></template>
          </el-table-column>
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
import StatusBadge from '@/components/StatusBadge.vue'

const payload = ref<any>(null)
const loadError = ref<string | null>(null)

const lists = computed(() => (payload.value && payload.value.lists) || {})
const blockedNodes = computed(() => lists.value.blocked_nodes || [])
const inflightNodes = computed(() => lists.value.inflight_nodes || [])
const recentMissions = computed(() => lists.value.recent_missions || [])

onMounted(async () => {
  try {
    payload.value = await centersApi.organization()
  } catch (e: any) {
    loadError.value = e?.message || String(e)
  }
})
</script>

<style scoped>
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
</style>