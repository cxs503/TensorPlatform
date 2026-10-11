<template>
  <div class="labeling-page">
    <PageHeader title="数据标注" desc="使用 Label Studio 完成标注，平台负责准备待标注数据并保存已标注结果。">
      <template #actions><el-button @click="loadAll">刷新</el-button><el-button type="primary" :disabled="!labelStudioStatus.up" @click="openLabelStudio">打开 Label Studio</el-button></template>
    </PageHeader>

    <el-alert :title="labelStudioStatus.up ? 'Label Studio 已就绪。请先下载任务文件、导入 Label Studio 完成标注，再将导出的 JSON 上传回平台。' : 'Label Studio 未就绪：请先启动服务后再进行标注；平台已有数据不会受影响。'" :type="labelStudioStatus.up ? 'success' : 'warning'" :closable="false" show-icon class="note" />

    <el-row :gutter="16" class="steps">
      <el-col :xs="24" :md="8"><el-card shadow="hover"><template #header><b>1. 准备标注任务</b></template><p>选择一个平台数据集，下载带有平台行标识的 Label Studio JSON 任务文件。</p></el-card></el-col>
      <el-col :xs="24" :md="8"><el-card shadow="hover"><template #header><b>2. 在 Label Studio 标注</b></template><p>将任务 JSON 导入 Label Studio，配置标签并完成标注，然后导出 JSON 结果。</p></el-card></el-col>
      <el-col :xs="24" :md="8"><el-card shadow="hover"><template #header><b>3. 导入平台归档</b></template><p>上传导出的 JSON，平台会生成一个新的“已标注数据集”，原数据不会被覆盖。</p></el-card></el-col>
    </el-row>

    <el-row :gutter="18">
      <el-col :xs="24" :lg="12"><el-card class="workflow-card" shadow="never">
        <template #header><div class="card-header"><b>下载 Label Studio 标注任务</b><el-tag effect="plain">平台 → Label Studio</el-tag></div></template>
        <el-form label-width="95px"><el-form-item label="平台数据集" required><el-select v-model="exportDatasetId" filterable placeholder="选择待标注的数据集" style="width:100%"><el-option v-for="dataset in datasets" :key="dataset.id" :label="`#${dataset.id} ${dataset.name}（${dataset.row_count || 0} 行）`" :value="dataset.id" /></el-select></el-form-item></el-form>
        <el-alert title="下载的任务会附带 __foundation_row_id，用于把 Label Studio 的标注结果准确归回原数据行。请勿删除或修改该字段。" type="info" :closable="false" show-icon />
        <div class="card-actions"><el-button type="primary" :disabled="!exportDatasetId" @click="downloadTasks">下载任务 JSON</el-button><el-button :disabled="!labelStudioStatus.up" @click="openLabelStudio">打开 Label Studio</el-button></div>
      </el-card></el-col>

      <el-col :xs="24" :lg="12"><el-card class="workflow-card" shadow="never">
        <template #header><div class="card-header"><b>导入 Label Studio 标注结果</b><el-tag type="success" effect="plain">Label Studio → 平台</el-tag></div></template>
        <el-form label-width="110px"><el-form-item label="源数据集" required><el-select v-model="importForm.source_dataset_id" filterable placeholder="选择任务对应的原始数据集" style="width:100%" @change="syncImportName"><el-option v-for="dataset in datasets" :key="dataset.id" :label="`#${dataset.id} ${dataset.name}`" :value="dataset.id" /></el-select></el-form-item><el-form-item label="导出结果文件" required><el-upload :auto-upload="false" :limit="1" accept=".json,application/json" :on-change="selectExportFile" :on-remove="clearExportFile"><el-button>选择 Label Studio JSON</el-button><template #tip><div class="upload-tip">上传 Label Studio 的 JSON 导出文件，不是原始任务文件。</div></template></el-upload></el-form-item><el-form-item label="结果数据集名" required><el-input v-model="importForm.name" /></el-form-item><el-form-item label="标签列名" required><el-input v-model="importForm.output_column" placeholder="例如 label" /></el-form-item></el-form>
        <el-alert title="平台会读取分类、多选分类等标签结果；未标注的行会保留为空。" type="info" :closable="false" show-icon />
        <div class="card-actions"><el-button type="primary" :loading="importing" @click="importResults">导入并生成已标注数据集</el-button></div>
      </el-card></el-col>
    </el-row>

    <el-card v-if="lastImport" shadow="never" class="result-card"><template #header><b>最近一次导入结果</b></template><el-descriptions :column="3" border><el-descriptions-item label="生成数据集">#{{ lastImport.dataset?.id }} {{ lastImport.dataset?.name }}</el-descriptions-item><el-descriptions-item label="标签列">{{ lastImport.output_column }}</el-descriptions-item><el-descriptions-item label="写入标签记录">{{ lastImport.labeled_rows || 0 }} 条</el-descriptions-item></el-descriptions><el-alert v-if="lastImport.skipped_tasks" :title="`${lastImport.skipped_tasks} 个任务缺少有效的平台行标识，未参与导入。`" type="warning" :closable="false" show-icon class="result-warning" /></el-card>

    <el-dialog v-model="showLabelStudio" title="Label Studio 标注工具" width="90%" top="3vh" :close-on-click-modal="false"><iframe :src="labelStudioUrl" style="width:100%;height:75vh;border:0;border-radius:8px" /><template #footer><el-button @click="showLabelStudio=false">关闭</el-button></template></el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { downloadFile, get, postForm } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const datasets = ref<any[]>([])
const labelStudioStatus = ref<any>({ up: false })
const exportDatasetId = ref<number | null>(null)
const exportFile = ref<File | null>(null)
const importing = ref(false)
const showLabelStudio = ref(false)
const lastImport = ref<any>(null)
const importForm = ref({ source_dataset_id: null as number | null, name: '', output_column: 'label' })
const labelStudioUrl = `http://${window.location.hostname}:18081`
const items = (result: any) => Array.isArray(result) ? result : (result?.items || [])

async function loadAll() {
  try {
    const [datasetResult, statusResult] = await Promise.all([get('/labeling/datasets'), get('/labeling/labelstudio')])
    datasets.value = items(datasetResult)
    labelStudioStatus.value = statusResult || { up: false }
  } catch (error: any) { ElMessage.error(error?.message || '加载标注配置失败') }
}
function datasetName(id: number | null) { return datasets.value.find(item => item.id === id)?.name || '' }
function syncImportName() { if (!importForm.value.name) importForm.value.name = `${datasetName(importForm.value.source_dataset_id)}（Label Studio标注）` }
async function downloadTasks() {
  if (!exportDatasetId.value) return
  try { await downloadFile(`/labeling/labelstudio/export/${exportDatasetId.value}`, `${datasetName(exportDatasetId.value)}_labelstudio_tasks.json`); ElMessage.success('任务文件已下载，可导入 Label Studio') }
  catch (error: any) { ElMessage.error(error?.message || '下载任务文件失败') }
}
function selectExportFile(file: any) { exportFile.value = file.raw as File }
function clearExportFile() { exportFile.value = null }
async function importResults() {
  if (!importForm.value.source_dataset_id || !importForm.value.name.trim() || !importForm.value.output_column.trim() || !exportFile.value) return ElMessage.warning('请选择源数据集、Label Studio JSON 文件，并填写结果名称和标签列名')
  const form = new FormData()
  form.append('source_dataset_id', String(importForm.value.source_dataset_id))
  form.append('name', importForm.value.name.trim())
  form.append('output_column', importForm.value.output_column.trim())
  form.append('file', exportFile.value)
  importing.value = true
  try { lastImport.value = await postForm('/labeling/labelstudio/import', form); ElMessage.success(`已生成已标注数据集 #${lastImport.value.dataset?.id || '-'}`) }
  catch (error: any) { ElMessage.error(error?.message || '导入 Label Studio 标注结果失败') }
  finally { importing.value = false }
}
function openLabelStudio() { if (!labelStudioStatus.value.up) return ElMessage.warning('Label Studio 服务尚未就绪'); showLabelStudio.value = true }
onMounted(loadAll)
</script>

<style scoped>
.labeling-page { max-width: 1280px; padding: 20px; }
.note { margin-bottom: 12px; }
.steps { margin: 18px 0; }
.steps :deep(.el-card__body) { min-height: 78px; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; }
.steps p { margin: 0; }
.workflow-card { min-height: 400px; }
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-actions { display: flex; gap: 10px; margin-top: 18px; }
.upload-tip { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 6px; }
.result-card { margin-top: 18px; }
.result-warning { margin-top: 14px; }
@media (max-width: 768px) { .labeling-page { padding: 14px; } .workflow-card { min-height: auto; } }
</style>
