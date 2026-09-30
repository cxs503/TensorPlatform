<template>
  <div class="me-page">
    <PageHeader title="模型解释" desc="SHAP / LIME / 特征重要性 — 真实模型分析" />

    <el-card class="config-card" shadow="never">
      <el-form :model="form" label-width="100px">
        <el-form-item label="选择模型">
          <el-select v-model="form.model_id" placeholder="选择模型" style="width: 100%;" filterable>
            <el-option v-for="m in models" :key="m.id" :label="`[${m.id}] ${m.name}`" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="选择数据集">
          <el-select v-model="form.dataset_id" placeholder="选择数据集(可选)" style="width: 100%;"
            clearable filterable @change="onDatasetChange">
            <el-option v-for="d in datasets" :key="d.id" :label="`[${d.id}] ${d.name}`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.dataset_id" label="目标列">
          <el-select v-model="form.target_column" placeholder="选择模型需要预测的目标列"
            style="width: 100%;" filterable>
            <el-option v-for="column in targetColumns" :key="column.name"
              :label="column.derived ? `${column.name}（派生列）` : column.name"
              :value="column.name" />
          </el-select>
          <div class="field-tip">模型输入特征优先采用训练记录；数据集中新增的其他列不会被误传给模型。</div>
        </el-form-item>
        <el-form-item label="分析方法">
          <el-radio-group v-model="form.method">
            <el-radio value="feature_importance">特征重要性</el-radio>
            <el-radio value="shap" :disabled="!methodsAvailable.find(m=>m.name.toLowerCase()==='shap')?.available">SHAP</el-radio>
            <el-radio value="lime" :disabled="!methodsAvailable.find(m=>m.name.toLowerCase()==='lime')?.available">LIME</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="run" :loading="loading">
            <el-icon><DataAnalysis /></el-icon> 分析
          </el-button>
        </el-form-item>
      </el-form>
      <div v-if="methodsAvailable.length" class="methods-status">
        <el-tag v-for="m in methodsAvailable" :key="m.name" size="small"
          :type="m.available ? 'success' : 'info'" style="margin-right:8px">
          {{ m.name }}: {{ m.available ? '可用' : '未安装' }}
        </el-tag>
      </div>
      <el-alert v-if="analysisError" type="error" :title="analysisError"
        :closable="false" show-icon style="margin-top:12px" />
    </el-card>

    <!-- 特征重要性 -->
    <el-card v-if="result && form.method === 'feature_importance'" class="result-card" shadow="never">
      <template #header>
        <span>{{ featureImportanceTitle }} {{ result.model_name ? ' — ' + result.model_name : '' }}</span>
        <el-tag size="small" type="info" style="margin-left:10px">
          {{ result.method === 'permutation_importance' ? '基于所选数据集' : '模型内置' }}
        </el-tag>
      </template>
      <el-alert v-if="result?.note" type="info" :title="result.note" :closable="false"
        show-icon style="margin-bottom:12px" />
      <div ref="fiChartRef" style="height: 400px;"></div>
      <el-divider />
      <el-table :data="result.feature_importance || []" size="small" max-height="360">
        <el-table-column prop="rank" label="排名" width="80" />
        <el-table-column prop="feature" label="特征" />
        <el-table-column prop="importance" label="重要性">
          <template #default="{ row }">{{ Number(row.importance).toFixed(4) }}</template>
        </el-table-column>
        <el-table-column v-if="result.method === 'permutation_importance'" prop="std" label="波动">
          <template #default="{ row }">{{ Number(row.std || 0).toFixed(4) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- SHAP -->
    <el-card v-if="result && form.method === 'shap'" class="result-card" shadow="never">
      <template #header>
        <span>SHAP 特征贡献</span>
        <el-tag v-if="result?.explainer_type" size="small" type="info" style="margin-left:10px">
          {{ result.explainer_type }}
        </el-tag>
      </template>
      <el-alert v-if="result?.note" type="warning" :title="result.note" :closable="false" show-icon style="margin-bottom:12px" />
      <div ref="shapChartRef" style="height: 400px;"></div>
      <el-divider />
      <el-table :data="shapTableData" size="small" max-height="400">
        <el-table-column prop="feature" label="特征" />
        <el-table-column prop="importance" label="平均 |SHAP|" sortable>
          <template #default="{ row }">
            <el-progress :percentage="Math.min(Math.abs(row.importance) * 100, 100)"
              :color="row.importance >= 0 ? '#67C23A' : '#F56C6C'" :show-text="false" :stroke-width="18" />
            <span style="margin-left:8px;font-size:13px;">{{ row.importance.toFixed(4) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- LIME -->
    <el-card v-if="result && form.method === 'lime'" class="result-card" shadow="never">
      <template #header><span>LIME 局部解释</span></template>
      <el-alert v-if="result?.note" type="warning" :title="result.note" :closable="false" show-icon style="margin-bottom:12px" />
      <div ref="limeChartRef" style="height: 400px;"></div>
    </el-card>

    <el-empty v-if="!result && !loading" description="选择模型和方法后点击分析" :image-size="120" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, nextTick, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { DataAnalysis } from '@element-plus/icons-vue'
import { api } from '@/api'
import PageHeader from '@/components/PageHeader.vue'
import * as echarts from 'echarts'

const models = ref<any[]>([])
const datasets = ref<any[]>([])
const form = reactive({
  model_id: null as number | null,
  dataset_id: null as number | null,
  target_column: null as string | null,
  method: 'feature_importance' as string
})
const result = ref<any>(null)
const loading = ref(false)
const analysisError = ref('')
const methodsAvailable = ref<any[]>([])

const shapChartRef = ref<HTMLElement | null>(null)
const limeChartRef = ref<HTMLElement | null>(null)
const fiChartRef = ref<HTMLElement | null>(null)

const shapTableData = computed(() => result.value?.feature_importance || [])
const selectedDataset = computed(() => datasets.value.find(d => Number(d.id) === Number(form.dataset_id)))

function parseColumnsInfo(value: any) {
  let parsed = value
  if (typeof parsed === 'string') {
    try { parsed = JSON.parse(parsed) } catch { parsed = [] }
  }
  if (!Array.isArray(parsed)) return []
  return parsed.map((item: any) => typeof item === 'string'
    ? { name: item, dtype: '', derived: false }
    : { name: String(item?.name || ''), dtype: String(item?.dtype || ''), derived: item?.derived === true }
  ).filter((item: any) => item.name)
}

const datasetColumns = computed(() => parseColumnsInfo(selectedDataset.value?.columns_info))
const targetColumns = computed(() => {
  const numeric = datasetColumns.value.filter((item: any) =>
    /int|float|double|decimal|number/i.test(item.dtype)
  )
  return numeric.length ? numeric : datasetColumns.value
})
const featureImportanceTitle = computed(() =>
  result.value?.method === 'permutation_importance' ? '排列特征重要性' : '特征重要性'
)

function onDatasetChange() {
  form.target_column = null
  if (!form.dataset_id) return
  const columns = targetColumns.value
  const preferred = columns.find((item: any) =>
    /^(target|label|temperature|y)$/i.test(item.name) || /目标|温度/.test(item.name)
  )
  const nonDerived = columns.filter((item: any) => !item.derived)
  form.target_column = preferred?.name || (nonDerived.length === 1 ? nonDerived[0].name : null)
}

onMounted(async () => {
  try {
    const [mRes, dRes, cRes] = await Promise.all([
      api.model.getList(),
      api.dataset.getList(),
      api.modelExplain.getMethods()
    ])
    models.value = mRes.items || mRes || []
    datasets.value = dRes.items || dRes || []
    methodsAvailable.value = cRes.methods || []
  } catch { /* ignore */ }
})

async function run() {
  if (!form.model_id) { ElMessage.warning('请选择模型'); return }
  loading.value = true
  result.value = null
  analysisError.value = ''
  try {
    const body: any = { model_id: form.model_id }
    if (form.dataset_id) body.dataset_id = form.dataset_id
    if (form.target_column) body.target_column = form.target_column

    if (form.method === 'shap') {
      body.max_samples = 200
      result.value = await api.modelExplain.shap(body)
    } else if (form.method === 'lime') {
      body.num_features = 10
      result.value = await api.modelExplain.lime(body)
    } else {
      result.value = await api.modelExplain.featureImportance(body)
    }
    if (result.value?.error) {
      throw new Error(result.value.message || result.value.error)
    }
    await nextTick()
    renderChart()
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.detail
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || e?.message || '分析失败：请检查模型与数据集是否匹配'
    analysisError.value = message
    result.value = null
    ElMessage.error(message)
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (form.method === 'feature_importance' && fiChartRef.value && (result.value?.feature_importance || result.value?.importance)) {
    let entries = []
    if (result.value.feature_importance) {
      entries = result.value.feature_importance.map((f: any) => ({ feature: f.feature, importance: Number(f.importance) }))
    } else if (result.value.importance) {
      entries = Object.entries(result.value.importance).map(([k, v]) => ({ feature: k, importance: Number(v) }))
    }
    renderBarChart(fiChartRef.value, entries, '重要性')
  }
  if (form.method === 'shap' && shapChartRef.value && result.value?.feature_importance) {
    renderBarChart(shapChartRef.value, result.value.feature_importance, '平均 |SHAP|')
  }
  if (form.method === 'lime' && limeChartRef.value && result.value?.explanation) {
    const data = result.value.explanation.map((e: any) => ({ feature: e.feature, importance: e.weight }))
    renderBarChart(limeChartRef.value, data, 'LIME 权重')
  }
}

function renderBarChart(el: HTMLElement, data: any[], title: string) {
  const chart = echarts.init(el)
  const sorted = [...data].sort((a, b) => Math.abs(b.importance) - Math.abs(a.importance))
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 130, right: 30, top: 20, bottom: 30 },
    xAxis: { type: 'value', name: title },
    yAxis: { type: 'category', data: sorted.map(d => d.feature), inverse: true,
      axisLabel: { width: 110, overflow: 'truncate' } },
    series: [{
      type: 'bar',
      data: sorted.map(d => ({
        value: Math.abs(d.importance),
        itemStyle: { color: d.importance >= 0 ? '#67C23A' : '#F56C6C' }
      }))
    }]
  })
}
</script>

<style scoped>
.me-page { max-width: 1000px; padding: 20px; }
.config-card { margin-bottom: 16px; }
.result-card { margin-top: 16px; }
.methods-status { margin-top: 12px; }
.field-tip { margin-top: 6px; color: #606266; font-size: 12px; line-height: 1.5; }
</style>
