<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>进展报告 <span class="tf-hero-mint">· research progress</span></template>
      <template #sub>
        每个完成的任务/阶段发布一篇进展报告：案例详细计算结果、云图与曲线。
        判据数字与图全部出自运行工件，零手抄。
      </template>
    </HeroPanel>

    <div class="tf-card">
      <el-radio-group v-model="scenarioFilter" @change="load" style="margin-bottom: 12px">
        <el-radio-button value="">全部场景</el-radio-button>
        <el-radio-button v-for="s in scenarioKeys" :key="s" :value="s">{{ s }}</el-radio-button>
      </el-radio-group>
      <el-table :data="items" size="small" @row-click="open" style="cursor: pointer">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="title" label="标题" min-width="240" />
        <el-table-column label="场景/阶段" width="190">
          <template #default="{ row }">
            <el-tag v-if="row.scenario_key" size="small" effect="plain">{{ row.scenario_key }}</el-tag>
            <el-tag v-if="row.stage" size="small" type="success" effect="plain" style="margin-left: 4px">
              {{ row.stage }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="summary" label="摘要" min-width="280" show-overflow-tooltip />
        <el-table-column label="图" width="60">
          <template #default="{ row }">{{ (row.figures || []).length }}</template>
        </el-table-column>
        <el-table-column label="发布时间" width="170">
          <template #default="{ row }">{{ String(row.created_at).replace('T', ' ').slice(0, 19) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div class="tf-card" v-if="detail">
      <div class="rep-head">
        <h2 class="rep-title">{{ detail.title }}</h2>
        <el-button size="small" @click="closeDetail">收起</el-button>
      </div>
      <div class="tf-md" v-html="rendered"></div>
      <div class="rep-figs">
        <figure v-for="f in detail.figures || []" :key="f.name">
          <a :href="figUrl(f.name)" target="_blank" rel="noopener">
            <img v-if="!figErrors[f.name]" :src="figUrl(f.name)" :alt="f.caption || f.name" loading="lazy" @error="onFigError(f.name)" />
          </a>
          <figcaption :class="{ 'fig-err': figErrors[f.name] }">{{ figErrors[f.name] ? `图片加载失败 ${figUrl(f.name)}` : (f.caption || f.name) }}</figcaption>
        </figure>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { reportApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import { renderMarkdown } from '@/utils/markdown'

const items = ref<any[]>([])
const scenarioKeys = ref<string[]>([])
const scenarioFilter = ref('')
const detail = ref<any>(null)
const figErrors = ref<Record<string, boolean>>({})
const curId = ref<number | null>(null)
const rendered = computed(() => (detail.value ? renderMarkdown(detail.value.markdown) : ''))

async function load() {
  try {
    const res: any = await reportApi.list(scenarioFilter.value ? { scenario_key: scenarioFilter.value } : undefined)
    items.value = res.items || []
    if (!scenarioFilter.value) {
      const keys = new Set<string>()
      items.value.forEach((r) => { if (r.scenario_key) keys.add(r.scenario_key) })
      scenarioKeys.value = [...keys]
    }
  } catch {
    ElMessage.error('报告列表加载失败')
  }
}

// 图床端点已免鉴权（owner 批·方案A）：直接 <img src>，不经 JS 取 blob。
// （blob: object URL 在部分受控浏览器不可用；直链 + @error 可见兜底，失败可诊断）
const API_BASE: string = (import.meta as any).env?.VITE_API_BASE_URL || '/tp/api/v1'
function figUrl(name: string) {
  return `${API_BASE}/tensor/reports/${curId.value}/figures/${encodeURIComponent(name)}`
}
function onFigError(name: string) { figErrors.value[name] = true }
async function open(row: any) {
  try {
    detail.value = await reportApi.get(row.id)
    curId.value = row.id
    figErrors.value = {}
  } catch {
    ElMessage.error('报告加载失败')
  }
}

function closeDetail() {
  detail.value = null
  curId.value = null
  figErrors.value = {}
}

onMounted(load)
</script>

<style scoped>
.rep-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.rep-title { margin: 0 0 10px; font-size: 18px; }
.tf-md { line-height: 1.75; font-size: 14px; }
.tf-md :deep(h1) { font-size: 18px; margin: 18px 0 8px; }
.tf-md :deep(h2) { font-size: 16px; margin: 16px 0 6px; }
.tf-md :deep(h3) { font-size: 14px; margin: 12px 0 4px; }
.tf-md :deep(hr) { border: 0; border-top: 1px solid #e4e7ed; margin: 14px 0; }
.tf-md :deep(.tf-md-table) { border-collapse: collapse; margin: 10px 0; width: 100%; }
.tf-md :deep(.tf-md-table th),
.tf-md :deep(.tf-md-table td) { border: 1px solid #dcdfe6; padding: 5px 10px; font-size: 13px; text-align: left; }
.tf-md :deep(.tf-md-table th) { background: #f5f7fa; }
.tf-md :deep(.tf-md-pre) {
  background: #282c34; color: #abb2bf; padding: 10px 12px; border-radius: 6px;
  overflow-x: auto; font-size: 12px; line-height: 1.6;
}
.rep-figs { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; margin-top: 16px; }
.rep-figs figure { margin: 0; }
.rep-figs img { width: 100%; border: 1px solid #e4e7ed; border-radius: 6px; }
.rep-figs figcaption { font-size: 12px; color: #909399; margin-top: 4px; }
.rep-figs .fig-err { color: #f56c6c; word-break: break-all; }
</style>
