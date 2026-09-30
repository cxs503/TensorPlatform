<template>
  <div class="pub-page">
    <PageHeader title="验证数据资产库" desc="开放底座示范、公开基准与虚拟工厂数据；用于可复现的离线验证，不替代现场 M&V。" />
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :sm="6"><StatCard title="已发布资产" :value="datasets.length" icon="Collection" iconColor="#818cf8" bgColor="rgba(99,102,241,.1)" :loading="loading" /></el-col>
      <el-col :xs="12" :sm="6"><StatCard title="数据行数" :value="totalRows.toLocaleString()" icon="Document" iconColor="#34d399" bgColor="rgba(16,185,129,.1)" :loading="loading" /></el-col>
      <el-col :xs="12" :sm="6"><StatCard title="工艺设备能效" :value="efficiencyCount" icon="Cpu" iconColor="#22d3ee" bgColor="rgba(6,182,212,.1)" :loading="loading" /></el-col>
      <el-col :xs="12" :sm="6"><StatCard title="能源管控" :value="controlCount" icon="TrendCharts" iconColor="#fbbf24" bgColor="rgba(245,158,11,.1)" :loading="loading" /></el-col>
    </el-row>
    <el-alert type="info" :closable="false" class="notice">每项资产均显示来源和使用许可。复制到工作区后形成当前组织独立的数据副本，才能治理、训练或派生。</el-alert>
    <div v-loading="loading" class="pub-grid">
      <el-card v-for="ds in datasets" :key="ds.id" shadow="hover" class="pub-card">
        <template #header><div class="card-h"><div><div class="card-name">{{ ds.name }}</div><span class="card-domain">{{ ds.domain }}</span></div><div class="card-actions"><el-button size="small" @click="preview(ds)">预览</el-button><el-button size="small" @click="download(ds)">下载</el-button><el-button size="small" type="primary" :loading="copyingId === ds.id" @click="copyToWorkspace(ds)">复制到工作区</el-button></div></div></template>
        <p class="card-desc">{{ ds.description }}</p>
        <div class="tag-line"><el-tag v-for="tag in ds.tags || []" :key="tag" size="small" effect="plain">{{ tag }}</el-tag></div>
        <div class="card-meta"><span>📊 {{ (ds.row_count || 0).toLocaleString() }} 行</span><span>📦 {{ formatSize(ds.size_bytes) }}</span><span>{{ ds.granularity || '粒度待补充' }}</span></div>
        <div class="tasks">推荐：{{ (ds.recommended_tasks || []).join(' · ') || '数据验证' }}</div>
        <div class="provenance"><span>来源：{{ ds.source || '待补充' }}</span><span>许可：{{ ds.license || '待核验' }}</span><a v-if="ds.source_url" :href="ds.source_url" target="_blank" rel="noopener">来源链接</a></div>
      </el-card>
    </div>
    <el-empty v-if="!loading && !datasets.length" description="暂无已发布验证数据资产。请先执行数据资产库初始化迁移。" />
    <el-dialog v-model="previewVisible" :title="previewDataset?.name || '数据预览'" width="860px"><el-table :data="previewRows" size="small" border max-height="420"><el-table-column v-for="(column, index) in previewColumns" :key="column" :label="column" min-width="130" show-overflow-tooltip><template #default="{ row }">{{ row[index] }}</template></el-table-column></el-table><el-empty v-if="!previewLoading && !previewColumns.length" description="暂无可预览数据" /></el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'
import { useAsyncData } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatCard from '@/components/StatCard.vue'

interface CuratedDataset { id: number; name: string; description?: string; tags?: string[]; domain?: string; source?: string; license?: string; source_url?: string; row_count?: number; size_bytes?: number; granularity?: string; recommended_tasks?: string[] }
const { data, loading } = useAsyncData<{ items: CuratedDataset[] }>(() => get('/curated-datasets'))
const datasets = computed(() => data.value?.items || [])
const totalRows = computed(() => datasets.value.reduce((total, item) => total + (item.row_count || 0), 0))
const efficiencyCount = computed(() => datasets.value.filter(item => item.domain?.includes('能效')).length)
const controlCount = computed(() => datasets.value.filter(item => item.domain?.includes('管控')).length)
const copyingId = ref<number | null>(null)
const previewVisible = ref(false), previewLoading = ref(false), previewDataset = ref<CuratedDataset | null>(null)
const previewColumns = ref<string[]>([]), previewRows = ref<any[][]>([])
function formatSize(bytes?: number) { if (!bytes) return '—'; if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + 'KB'; return (bytes / 1024 / 1024).toFixed(1) + 'MB' }
function authHeaders() { const token = localStorage.getItem('foundation_access_token'); return token ? { Authorization: 'Bearer ' + token } : {} }
async function download(ds: CuratedDataset) {
  try { const response = await fetch('/api/v1/curated-datasets/' + ds.id + '/download', { headers: authHeaders() }); if (!response.ok) throw new Error('HTTP ' + response.status); const url = URL.createObjectURL(await response.blob()); const anchor = document.createElement('a'); anchor.href = url; anchor.download = ds.name + '.csv'; anchor.click(); URL.revokeObjectURL(url) }
  catch (error: any) { ElMessage.error('下载失败：' + (error.message || '未知错误')) }
}
async function copyToWorkspace(ds: CuratedDataset) { copyingId.value = ds.id; try { await post('/curated-datasets/' + ds.id + '/copy-to-workspace'); ElMessage.success('已复制到当前组织的数据集管理，可继续治理和训练') } catch (error: any) { ElMessage.error(error?.message || '复制失败') } finally { copyingId.value = null } }
async function preview(ds: CuratedDataset) { previewVisible.value = true; previewLoading.value = true; previewDataset.value = ds; previewColumns.value = []; previewRows.value = []; try { const result = await get('/curated-datasets/' + ds.id + '/rows', { limit: 10 }); previewColumns.value = result?.columns || []; previewRows.value = result?.rows || [] } catch (error: any) { ElMessage.error(error?.message || '预览失败') } finally { previewLoading.value = false } }
</script>

<style scoped>
.pub-page { padding:20px; }.stats-row { margin-bottom:20px; }.notice { margin-bottom:20px; }.pub-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:16px; }.pub-card { border-radius:8px; }.card-h { display:flex; justify-content:space-between; gap:12px; align-items:center; }.card-name { font-weight:600; font-size:15px; }.card-domain { color:var(--el-text-color-secondary); font-size:12px; }.card-actions { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }.card-desc { min-height:40px; font-size:13px; line-height:1.55; }.tag-line { display:flex; flex-wrap:wrap; gap:5px; margin:10px 0; }.card-meta,.tasks,.provenance { font-size:12px; color:var(--el-text-color-secondary); display:flex; gap:10px; flex-wrap:wrap; margin-top:8px; }.provenance { border-top:1px solid var(--el-border-color-lighter); padding-top:8px; }.provenance a { color:var(--el-color-primary); }
</style>
