<template>
  <div class="dashboard-page">
    <PageHeader title="数据大盘" desc="近期数据资产与模型资产，一眼掌握平台最新产出" />

    <div class="dashboard-intro">
      <div class="intro-icon">⌁</div>
      <div><b>真实资产动态</b><span>所有内容均直接来自平台数据库，并按创建时间倒序展示。</span></div>
    </div>

    <el-row :gutter="16" class="content-row">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never" class="asset-card">
          <template #header>
            <div class="card-header">
              <div><b>近期数据集</b><span>最近 {{ assets.datasets.length }} 个</span></div>
              <el-button text type="primary" @click="go('/data/new-datasets')">查看数据集管理</el-button>
            </div>
          </template>
          <el-table :data="assets.datasets" v-loading="loading" stripe class="asset-table">
            <el-table-column label="ID" width="76"><template #default="{ row }">#{{ row.id }}</template></el-table-column>
            <el-table-column prop="name" label="数据集名称" min-width="150" show-overflow-tooltip />
            <el-table-column label="类型" width="100"><template #default="{ row }"><el-tag size="small" effect="plain">{{ stageLabel(row.stage) }}</el-tag></template></el-table-column>
            <el-table-column label="数据量" width="95"><template #default="{ row }">{{ Number(row.row_count || 0).toLocaleString() }} 行</template></el-table-column>
            <el-table-column label="创建时间" width="135"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
            <template #empty><el-empty description="暂无真实数据集" :image-size="56" /></template>
          </el-table>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="12">
        <el-card shadow="never" class="asset-card">
          <template #header>
            <div class="card-header">
              <div><b>近期模型</b><span>最近 {{ assets.models.length }} 个</span></div>
              <el-button text type="primary" @click="go('/model/versions')">查看模型管理</el-button>
            </div>
          </template>
          <el-table :data="assets.models" v-loading="loading" stripe class="asset-table">
            <el-table-column label="ID" width="76"><template #default="{ row }">#{{ row.id }}</template></el-table-column>
            <el-table-column prop="name" label="模型名称" min-width="145" show-overflow-tooltip />
            <el-table-column label="类型" width="92"><template #default="{ row }">{{ modelTypeLabel(row.model_type) }}</template></el-table-column>
            <el-table-column label="核心指标" min-width="110"><template #default="{ row }">{{ metricSummary(row.metrics) }}</template></el-table-column>
            <el-table-column label="创建时间" width="135"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
            <template #empty><el-empty description="暂无真实模型" :image-size="56" /></template>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import { get } from '@/api/legacy'
import { useAsyncData } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
const assets = reactive<{ datasets: any[]; models: any[] }>({ datasets: [], models: [] })
const { data, loading } = useAsyncData<any>(() => get('/dashboard/recent-assets?limit=8'))
watch(data, value => {
  assets.datasets = Array.isArray(value?.datasets) ? value.datasets : []
  assets.models = Array.isArray(value?.models) ? value.models : []
}, { immediate: true })

const go = (path: string) => router.push(path)
const formatDate = (value?: string) => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—'
const stageLabel = (stage?: string) => ({ raw: '原始', governed: '清洗通过', engineered: '特征工程', labeled: '已标注', split_train: '训练数据', split_test: '测试数据', derived: '衍生数据', masked: '脱敏数据' } as Record<string, string>)[stage || ''] || stage || '—'
const modelTypeLabel = (type?: string) => ({ regression: '回归', classification: '分类', forecasting: '时序预测', time_series_forecasting: '时序预测', control: '控制仿真', user_script: '用户源码模型' } as Record<string, string>)[type || ''] || type || '未标注'
function metricSummary(metrics: unknown) {
  if (!metrics || typeof metrics !== 'object') return '—'
  const entry = Object.entries(metrics as Record<string, unknown>).find(([, value]) => typeof value === 'number' && Number.isFinite(value))
  if (!entry) return '—'
  const [name, value] = entry as [string, number]
  return `${name.toUpperCase()} ${value.toFixed(4)}`
}
</script>

<style scoped>
.dashboard-page { padding: 0; }
.dashboard-intro { display:flex; align-items:center; gap:12px; padding:13px 16px; margin-bottom:18px; color:#475569; background:linear-gradient(100deg, #f8faff, #f5fbff); border:1px solid #e5edff; border-radius:12px; }
.dashboard-intro b { display:block; margin-bottom:3px; color:#263956; font-size:14px; }
.dashboard-intro span { font-size:12px; }
.intro-icon { display:grid; place-items:center; width:30px; height:30px; color:#fff; font-size:20px; line-height:1; background:linear-gradient(135deg,#6366f1,#38bdf8); border-radius:9px; box-shadow:0 4px 10px rgba(99,102,241,.2); }
.content-row { margin-top:0; }
.asset-card { min-height:356px; overflow:hidden; border:1px solid #edf1f7; border-radius:14px; }
.asset-card :deep(.el-card__header) { padding:15px 18px; background:linear-gradient(180deg,#fff,#fbfcff); border-bottom:1px solid #edf1f7; }
.asset-card :deep(.el-card__body) { padding:4px 12px 12px; }
.card-header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.card-header > div { display:flex; align-items:baseline; gap:8px; }
.card-header b { color:#253552; font-size:15px; }
.card-header span { color:var(--el-text-color-secondary); font-size:12px; font-weight:400; }
.asset-table :deep(.el-table__inner-wrapper::before) { display:none; }
.asset-table :deep(.el-table__cell) { padding:13px 4px; border-bottom-color:#f0f3f8; }
.asset-table :deep(.el-table__row:hover > td.el-table__cell) { background:#f7f9ff; }
.asset-name-cell { display:flex; align-items:center; gap:10px; min-width:0; }
.asset-name-cell > div { min-width:0; }
.asset-name-cell b { display:block; overflow:hidden; color:#34425c; font-size:13px; text-overflow:ellipsis; white-space:nowrap; }
.asset-name-cell span:not(.asset-id) { display:block; margin-top:4px; color:#94a3b8; font-size:11px; }
.asset-id { display:grid; place-items:center; flex:0 0 auto; min-width:37px; height:25px; padding:0 5px; color:#4f46e5; font-size:11px; font-weight:600; background:#eef2ff; border-radius:7px; }
.model-id { color:#047857; background:#ecfdf5; }
.asset-number { color:#334155; font-size:15px; font-weight:650; }
.asset-number span { margin-left:3px; color:#94a3b8; font-size:11px; font-weight:400; }
.metric-value { display:inline-block; max-width:132px; overflow:hidden; color:#047857; font-size:12px; font-weight:650; text-overflow:ellipsis; white-space:nowrap; }
.asset-time { color:#94a3b8; font-size:11px; white-space:nowrap; }
@media (max-width: 768px) {
  .content-row :deep(.el-col) + :deep(.el-col) { margin-top:16px; }
  .dashboard-intro { align-items:flex-start; }
  .asset-card { min-height:auto; }
}
</style>
