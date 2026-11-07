<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>质量中心 <span class="tf-hero-mint">· 评价流 &amp; 质量门态</span></template>
      <template #sub>
        评价流＝报告进入 QA 后 <span class="tf-mono">qa_status</span> 的三态流转（pending → passed / rejected）；
        质量门态＝四道平台级机器门（报告 QA / 报告欠账 / 战役里程碑 / 复现裁定）的当前状态，
        全部由 <span class="tf-mono">/tensor/centers/quality</span> 后端实测并在门内计数处可复核。
      </template>
      <template #extra>
        <span>载荷生成 <b class="tf-hero-mint">{{ payload ? payload.generated_at : '—' }}</b></span>
        <span>门数 <b class="tf-hero-mint">{{ gates.length }}</b></span>
      </template>
    </HeroPanel>

    <CenterBoard :payload="payload" :load-error="loadError">
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="14">
          <div class="tf-card">
            <div class="tf-card-title">评价流 · QA 待检队列（最近入队）</div>
            <el-table :data="qaQueue" size="small" empty-text="QA 队列空——无 pending 报告">
              <el-table-column prop="report_id" label="报告" width="80">
                <template #default="{ row }"><span class="tf-mono">#{{ row.report_id }}</span></template>
              </el-table-column>
              <el-table-column prop="title" label="标题" min-width="260" show-overflow-tooltip />
              <el-table-column prop="qa_status" label="QA" width="100" />
              <el-table-column prop="status" label="报告状态" width="110" />
            </el-table>
          </div>
        </el-col>
        <el-col :span="10">
          <div class="tf-card">
            <div class="tf-card-title">评价流 · 已裁定（最近）</div>
            <el-table :data="recentDecided" size="small" empty-text="尚无 QA 裁定">
              <el-table-column prop="report_id" label="#" width="70" />
              <el-table-column prop="qa_status" label="裁定" width="100" />
              <el-table-column prop="qa_rejection_reason" label="打回理由" min-width="160" show-overflow-tooltip />
            </el-table>
          </div>
        </el-col>
      </el-row>
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

const gates = computed(() => (payload.value && payload.value.gates) || [])
const lists = computed(() => (payload.value && payload.value.lists) || {})
const qaQueue = computed(() => lists.value.qa_queue || [])
const recentDecided = computed(() => lists.value.recent_decided || [])

onMounted(async () => {
  try {
    payload.value = await centersApi.quality()
  } catch (e: any) {
    loadError.value = e?.message || String(e)
  }
})
</script>