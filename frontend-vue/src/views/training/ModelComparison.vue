<template>
  <div class="comparison-page">
    <PageHeader title="模型对比测试" desc="算法商店完整目录 · 已训练模型可在统一测试数据集上进行真实推理指标对比" />

    <el-alert
      title="所有算法商店条目都会展示。只有已保存模型产物、训练特征和预测目标的条目可参与对比；未训练或缺少训练契约的条目会说明原因。"
      type="info"
      :closable="false"
      show-icon
      class="intro"
    />

    <el-row :gutter="16" class="stats">
      <el-col :span="8"><el-card shadow="never"><el-statistic title="算法商店条目" :value="models.length" /></el-card></el-col>
      <el-col :span="8"><el-card shadow="never"><el-statistic title="已选择" :value="selectedModels.length" suffix=" / 10" /></el-card></el-col>
      <el-col :span="8"><el-card shadow="never"><el-statistic title="历史对比" :value="runs.length" /></el-card></el-col>
    </el-row>

    <el-card shadow="never" class="section">
      <template #header>
        <div class="card-header">
          <span>第一步：选择两个或多个已训练模型</span>
          <el-input v-model="keyword" clearable placeholder="按模型或来源算法搜索" style="width:260px" />
        </div>
      </template>
      <el-table
        ref="modelTableRef"
        :data="filteredModels"
        row-key="comparison_key"
        max-height="330"
        stripe
        v-loading="loading"
        @selection-change="onModelSelection"
      >
        <el-table-column type="selection" width="48" :selectable="canSelectModel" />
        <el-table-column label="模型ID" width="90"><template #default="{row}">{{ row.model_id ? '#' + row.model_id : '-' }}</template></el-table-column>
        <el-table-column label="模型产物" min-width="190" show-overflow-tooltip><template #default="{row}">{{ row.model_name || '-' }}</template></el-table-column>
        <el-table-column label="来源算法" min-width="210">
          <template #default="{row}">#{{ row.algorithm_id }} {{ row.algorithm_name }} <el-tag size="small" type="info">{{ row.algorithm_type }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="training_job_id" label="训练任务" width="130" show-overflow-tooltip />
        <el-table-column label="训练输入" min-width="190" show-overflow-tooltip>
          <template #default="{row}">{{ (row.feature_columns || []).join('、') || '未记录' }}</template>
        </el-table-column>
        <el-table-column label="预测目标" width="120"><template #default="{row}">{{ row.target_column || '未记录' }}</template></el-table-column>
        <el-table-column label="原训练数据集" min-width="170">
          <template #default="{row}">#{{ row.training_dataset_id || '-' }} {{ row.training_dataset_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="framework" label="框架" width="100" />
        <el-table-column prop="version_number" label="模型版本" width="100" />
        <el-table-column label="对比状态" min-width="250">
          <template #default="{row}">
            <el-tag v-if="row.comparison_ready" type="success" size="small">可参与对比</el-tag>
            <template v-else>
              <el-tag type="info" size="small">暂不可对比</el-tag>
              <span class="comparison-reason">{{ row.comparison_reason }}<template v-if="row.linked_model_id">（关联模型 #{{ row.linked_model_id }}）</template></span>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <div class="selected-tags">
        <span>已选择：</span>
        <el-tag v-for="model in selectedModels" :key="model.model_id" closable @close="unselectModel(model)">
          #{{ model.model_id }} {{ model.model_name }}
        </el-tag>
        <el-text v-if="!selectedModels.length" type="info">至少选择两个模型</el-text>
      </div>
    </el-card>

    <el-card shadow="never" class="section">
      <template #header><span>第二步：配置统一测试条件</span></template>
      <el-form :model="form" label-width="105px" class="config-form">
        <el-row :gutter="18">
          <el-col :xs="24" :md="12">
            <el-form-item label="对比名称" required><el-input v-model="form.name" placeholder="例如：生产设备温度模型统一测试" maxlength="256" /></el-form-item>
          </el-col>
          <el-col :xs="24" :md="12">
            <el-form-item label="测试数据集" required>
              <el-select v-model="form.dataset_id" filterable style="width:100%" placeholder="按ID或名称选择数据集" @change="loadDatasetColumns">
                <el-option v-for="dataset in datasets" :key="dataset.id" :label="`#${dataset.id} ${dataset.name} (${dataset.row_count || 0}行)`" :value="dataset.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="12">
            <el-form-item label="任务类型">
              <el-radio-group v-model="form.task_type" @change="onTaskTypeChange">
                <el-radio-button value="regression">回归</el-radio-button>
                <el-radio-button value="classification">分类</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="12">
            <el-form-item label="评选指标">
              <el-select v-model="form.primary_metric" style="width:100%">
                <el-option v-for="metric in metricOptions" :key="metric.value" :label="metric.label" :value="metric.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="12">
            <el-form-item label="目标列" required>
              <el-select v-model="form.target_column" filterable style="width:100%" placeholder="选择真实结果所在列" @change="onTargetChange">
                <el-option v-for="column in datasetColumns" :key="column" :label="column" :value="column" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="模型输入检查">
              <div class="model-checks">
                <div v-for="check in modelChecks" :key="check.model_id" class="model-check">
                  <span>#{{ check.model_id }} {{ check.model_name }}</span>
                  <span class="feature-text">自动输入：{{ check.features.join('、') || '未记录训练特征' }}</span>
                  <el-tag :type="check.type" size="small">{{ check.message }}</el-tag>
                </div>
                <el-text v-if="!selectedModels.length" type="info">选择模型和测试数据集后自动检查</el-text>
              </div>
            </el-form-item>
          </el-col>
        </el-row>
        <el-alert
          title="选中的数据集100%作为测试集。模型可以使用不同输入列，但必须预测同一个目标；系统会按各自训练配置自动取数。"
          type="success"
          :closable="false"
          class="scope-alert"
        />
        <el-button type="primary" size="large" :loading="comparing" @click="runComparison">开始多模型对比</el-button>
      </el-form>
    </el-card>

    <el-card v-if="currentRun" shadow="never" class="section result-card">
      <template #header>
        <div class="card-header">
          <span>对比结果：{{ currentRun.name }}</span>
          <div><el-tag :type="runStatusType(currentRun.status)">{{ runStatusText(currentRun.status) }}</el-tag><span class="duration">耗时 {{ currentRun.duration_seconds ?? '-' }} 秒</span></div>
        </div>
      </template>
      <el-descriptions :column="4" border size="small" class="run-summary">
        <el-descriptions-item label="记录ID">{{ currentRun.id }}</el-descriptions-item>
        <el-descriptions-item label="测试数据集">#{{ currentRun.dataset_id }} {{ currentRun.dataset_name }}</el-descriptions-item>
        <el-descriptions-item label="目标列">{{ currentRun.target_column }}</el-descriptions-item>
        <el-descriptions-item label="评选指标">{{ metricText(currentRun.primary_metric) }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="currentRun.results || []" stripe border class="results-table">
        <el-table-column label="排名" width="70"><template #default="{row}"><el-tag v-if="row.model_id===currentRun.winner_model_id" type="success">最佳</el-tag><span v-else>-</span></template></el-table-column>
        <el-table-column label="模型" min-width="190"><template #default="{row}">#{{ row.model_id }} {{ row.model_name }}</template></el-table-column>
        <el-table-column label="来源算法" min-width="170"><template #default="{row}">#{{ row.algorithm_id }} {{ row.algorithm_name || '-' }}</template></el-table-column>
        <el-table-column label="实际输入" min-width="190" show-overflow-tooltip><template #default="{row}">{{ (row.feature_columns || []).join('、') || '-' }}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{row}"><el-tag :type="row.status==='completed'?'success':'danger'">{{ row.status==='completed'?'完成':'失败' }}</el-tag></template></el-table-column>
        <el-table-column label="测试样本" width="95"><template #default="{row}">{{ row.test_samples ?? '-' }}</template></el-table-column>
        <el-table-column label="指标" min-width="360">
          <template #default="{row}">
            <template v-if="row.status==='completed'">
              <el-tag v-for="(value,key) in row.metrics" :key="key" class="metric-tag" :type="isPrimaryMetric(String(key), currentRun.primary_metric)?'success':'info'">
                {{ key }}：{{ formatNumber(value) }}
              </el-tag>
            </template>
            <el-text v-else type="danger">{{ row.error || '评估失败' }}</el-text>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="section">
      <template #header><div class="card-header"><span>历史对比记录</span><el-button text type="primary" @click="loadRuns">刷新</el-button></div></template>
      <el-table :data="runs" stripe @row-click="viewRun">
        <el-table-column prop="id" label="记录ID" width="85" />
        <el-table-column prop="name" label="名称" min-width="180" />
        <el-table-column label="模型ID" min-width="160"><template #default="{row}">{{ (row.model_ids || []).map((id:any)=>'#'+id).join('、') }}</template></el-table-column>
        <el-table-column label="数据集" min-width="170"><template #default="{row}">#{{ row.dataset_id || '-' }} {{ row.dataset_name || '-' }}</template></el-table-column>
        <el-table-column prop="primary_metric" label="指标" width="90"><template #default="{row}">{{ metricText(row.primary_metric) }}</template></el-table-column>
        <el-table-column label="最佳模型" min-width="170"><template #default="{row}"><span v-if="row.winner_model_id">#{{ row.winner_model_id }} {{ row.winner_model_name }}</span><span v-else>-</span></template></el-table-column>
        <el-table-column label="成功/失败" width="100"><template #default="{row}">{{ row.completed }}/{{ row.failed }}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{row}"><el-tag :type="runStatusType(row.status)">{{ runStatusText(row.status) }}</el-tag></template></el-table-column>
        <el-table-column prop="created_at" label="时间" width="175"><template #default="{row}">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="145"><template #default="{row}"><el-button size="small" @click.stop="viewRun(row)">查看</el-button><el-button size="small" type="danger" text @click.stop="deleteRun(row)">删除</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!runs.length && !loading" description="暂无对比记录" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { del, get, post } from '@/api/legacy'

const models = ref<any[]>([])
const datasets = ref<any[]>([])
const runs = ref<any[]>([])
const selectedModels = ref<any[]>([])
const datasetColumns = ref<string[]>([])
const keyword = ref('')
const loading = ref(false)
const comparing = ref(false)
const currentRun = ref<any>(null)
const modelTableRef = ref<any>(null)
const form = reactive({
  name: '', dataset_id: null as number|null, task_type: 'regression',
  target_column: '', primary_metric: 'r2',
})

const filteredModels = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  if (!key) return models.value
  return models.value.filter(item => [item.model_id,item.model_name,item.algorithm_id,item.algorithm_name,item.training_job_id].some(value => String(value || '').toLowerCase().includes(key)))
})
const metricOptions = computed(() => form.task_type === 'classification'
  ? [{value:'accuracy',label:'准确率（越高越好）'},{value:'f1',label:'F1（越高越好）'},{value:'precision',label:'精确率（越高越好）'},{value:'recall',label:'召回率（越高越好）'}]
  : [{value:'r2',label:'R²（越高越好）'},{value:'rmse',label:'RMSE（越低越好）'},{value:'mae',label:'MAE（越低越好）'}])
const modelChecks = computed(() => selectedModels.value.map(model => {
  const features = model.feature_columns || []
  const missing = features.filter((column:string) => !datasetColumns.value.includes(column))
  const targetMismatch = Boolean(form.target_column && model.target_column && model.target_column !== form.target_column)
  if (!features.length) return {...model, features, type:'danger', message:'未记录训练特征'}
  if (!model.target_column) return {...model, features, type:'danger', message:'未记录训练目标'}
  if (targetMismatch) return {...model, features, type:'danger', message:`训练目标为 ${model.target_column}`}
  if (!form.dataset_id) return {...model, features, type:'info', message:'等待选择测试数据集'}
  if (missing.length) return {...model, features, type:'danger', message:`数据集缺少 ${missing.join('、')}`}
  return {...model, features, type:'success', message:'字段完整'}
}))

function canSelectModel(row: any) {
  if (!row.comparison_ready || !row.model_id) return false
  return selectedModels.value.some(item => item.model_id===row.model_id) || selectedModels.value.length < 10
}
function onModelSelection(rows: any[]) { selectedModels.value = rows }
function unselectModel(model: any) { modelTableRef.value?.toggleRowSelection(model, false) }
function onTaskTypeChange() { form.primary_metric = form.task_type === 'classification' ? 'accuracy' : 'r2' }
function onTargetChange() { /* modelChecks automatically validates every saved target */ }
function metricText(metric: string) { return ({accuracy:'准确率',f1:'F1',precision:'精确率',recall:'召回率',r2:'R²',rmse:'RMSE',mae:'MAE'} as any)[metric] || metric }
function isPrimaryMetric(key: string, metric: string) { return key.toLowerCase() === metric.toLowerCase() }
function formatNumber(value: any) { return Number.isFinite(Number(value)) ? Number(value).toFixed(4) : String(value ?? '-') }
function formatDate(value: string) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }
function runStatusText(status: string) { return ({completed:'全部完成',partial:'部分完成',failed:'失败'} as any)[status] || status }
function runStatusType(status: string) { return ({completed:'success',partial:'warning',failed:'danger'} as any)[status] || 'info' }

async function loadAll() {
  loading.value = true
  try {
    const [modelResult,datasetResult,runResult]: any[] = await Promise.all([
      get('/model-comparison/eligible-models'), get('/datasets?limit=500'), get('/model-comparison/runs?limit=100'),
    ])
    models.value = modelResult?.items || []
    datasets.value = (datasetResult?.items || []).filter((item:any) => item.has_file)
    runs.value = runResult?.items || []
  } catch (error:any) { ElMessage.error(error?.message || '模型对比数据加载失败') }
  finally { loading.value = false }
}

async function loadRuns() {
  try { const result:any = await get('/model-comparison/runs?limit=100'); runs.value = result?.items || [] }
  catch (error:any) { ElMessage.error(error?.message || '历史记录加载失败') }
}

async function deleteRun(run: any) {
  try {
    await ElMessageBox.confirm(
      '确定删除对比记录 #' + run.id + '「' + run.name + '」吗？此操作只删除历史记录，不会删除模型或数据集。',
      '删除对比记录', { type: 'warning' },
    )
    await del('/model-comparison/runs/' + run.id)
    if (currentRun.value?.id === run.id) currentRun.value = null
    ElMessage.success('对比记录已删除')
    await loadRuns()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '删除失败')
  }
}

async function loadDatasetColumns() {
  datasetColumns.value = []; form.target_column = ''
  if (!form.dataset_id) return
  try {
    const result:any = await get(`/datasets/${form.dataset_id}/rows`, {offset:0,limit:1})
    datasetColumns.value = result?.columns || Object.keys((result?.rows || [])[0] || {})
  } catch (error:any) { ElMessage.error(error?.message || '数据集字段加载失败') }
}

async function runComparison() {
  if (selectedModels.value.length < 2) { ElMessage.warning('请至少选择两个已训练模型'); return }
  if (!form.name.trim()) { ElMessage.warning('请输入对比名称'); return }
  if (!form.dataset_id) { ElMessage.warning('请选择统一测试数据集'); return }
  if (!form.target_column) { ElMessage.warning('请选择目标列'); return }
  const failedChecks = modelChecks.value.filter(check => check.type === 'danger')
  if (failedChecks.length) { ElMessage.warning('存在目标不一致、训练特征未记录或字段缺失的模型'); return }
  comparing.value = true
  try {
    const result:any = await post('/model-comparison/compare', {
      ...form, name:form.name.trim(), model_ids:selectedModels.value.map(model => model.model_id),
    })
    currentRun.value = result
    ElMessage.success(result.winner_model_id ? `对比完成，最佳模型为 #${result.winner_model_id}` : '对比完成，但暂无可判定的最佳模型')
    await loadRuns()
  } catch (error:any) { ElMessage.error(error?.message || '模型对比失败') }
  finally { comparing.value = false }
}

function viewRun(run: any) { currentRun.value = {...run, results:run.results || []}; window.scrollTo({top:document.body.scrollHeight/2,behavior:'smooth'}) }
onMounted(loadAll)
</script>

<style scoped>
.comparison-page { padding:20px; max-width:1500px; }
.intro,.stats,.section { margin-bottom:16px; }
.card-header { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.selected-tags { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:14px; font-size:13px; }
.config-form { max-width:1200px; }
.scope-alert { margin-bottom:16px; }
.result-card { border-top:3px solid #16a34a; }
.comparison-reason { margin-left:6px; color:#909399; font-size:12px; }
.duration { margin-left:12px; color:#64748b; font-size:13px; }
.run-summary { margin-bottom:14px; }
.results-table { margin-top:12px; }
.metric-tag { margin:2px 6px 2px 0; }
.model-checks { display:flex; flex-direction:column; gap:8px; width:100%; }
.model-check { display:grid; grid-template-columns:minmax(180px, 0.8fr) minmax(260px, 1.6fr) auto; align-items:center; gap:12px; padding:9px 12px; border:1px solid #e5e7eb; border-radius:7px; }
.feature-text { color:#64748b; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
</style>
