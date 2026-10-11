<template>
  <div class="model-evaluation-page">
    <PageHeader
      title="模型评估"
      desc="使用完整测试集运行模型，并生成指标、预测表现和数据分布可视化报告"
    />

    <el-row :gutter="20">
      <el-col :xs="24" :lg="7">
        <el-card class="side-card" shadow="never">
          <template #header><span class="card-title">创建评估任务</span></template>
          <el-form :model="evalForm" label-position="top">
            <el-form-item label="模型">
              <el-select v-model="evalForm.model_id" filterable placeholder="选择模型" class="full-width" @change="onModelChange">
                <el-option
                  v-for="model in models"
                  :key="model.id"
                  :label="`#${model.id} ${model.name}`"
                  :value="model.id"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="数据集">
              <el-input v-model="datasetIdInput" placeholder="输入数据集 ID 快速定位" clearable @keyup.enter="selectDatasetById">
                <template #append>
                  <el-button :loading="locatingDataset" @click="selectDatasetById">定位</el-button>
                </template>
              </el-input>
              <el-input v-model="datasetKeyword" class="dataset-search" placeholder="按名称搜索" clearable />
              <el-select
                v-model="evalForm.dataset_id"
                filterable
                clearable
                placeholder="选择完整测试集"
                class="full-width"
                @change="onDatasetChange"
              >
                <el-option
                  v-for="dataset in filteredDatasets"
                  :key="dataset.id"
                  :label="`#${dataset.id} ${dataset.name}`"
                  :value="dataset.id"
                />
              </el-select>
              <div class="field-hint">数据集 100% 用于推理，不训练、不切分。</div>
            </el-form-item>
            <el-form-item label="预测目标列">
              <el-select
                v-model="evalForm.target_column"
                filterable
                clearable
                :loading="targetColumnsLoading"
                :disabled="!evalForm.dataset_id"
                placeholder="选择模型需要预测的列"
                class="full-width"
              >
                <el-option v-for="column in targetColumns" :key="column" :label="column" :value="column" />
              </el-select>
              <div class="field-hint">切换数据集后加载列名；默认使用模型训练时保存的预测目标。</div>
            </el-form-item>

            <el-form-item label="任务类型">
              <el-segmented
                v-model="evalForm.task_type"
                :options="[
                  { label: '回归', value: 'regression' },
                  { label: '分类', value: 'classification' },
                  { label: '时序预测', value: 'time_series_forecasting' }
                ]"
                block
              />
              <div class="field-hint">{{ profileHint }}</div>
            </el-form-item>


            <el-button type="primary" class="full-width" :loading="evaluating" @click="startEvaluation">
              运行评估并生成报告
            </el-button>
          </el-form>
        </el-card>

        <el-card class="side-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span class="card-title">评估历史</span>
              <el-button text type="primary" @click="refreshResults">刷新</el-button>
            </div>
          </template>
          <div v-loading="resultsLoading" class="history-list">
            <button
              v-for="item in evaluationResults || []"
              :key="item.id"
              type="button"
              class="history-item"
              :class="{ active: selectedResult?.id === item.id }"
              @click="selectResult(item)"
            >
              <div class="history-top">
                <strong>#{{ item.id }}</strong>
                <div class="tag-row">
                  <el-tag v-if="item.has_visualization" size="small" type="primary" effect="plain">可视化</el-tag>
                  <el-tag size="small" :type="item.task_type === 'classification' ? 'success' : 'warning'">
                    {{ taskLabel(item.task_type) }}
                  </el-tag>
                </div>
              </div>
              <span>{{ item.name || getModelName(item.model_id) }}</span>
              <span>{{ getDatasetName(item.dataset_id) }}</span>
              <small>{{ formatDate(item.created_at) }}</small>
            </button>
            <el-empty v-if="!resultsLoading && !(evaluationResults || []).length" description="暂无评估记录" :image-size="64" />
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="17">
        <template v-if="selectedResult">
          <el-card class="report-card overview-card" shadow="never">
            <template #header>
              <div class="card-header">
                <div>
                  <span class="card-title">{{ selectedResult.name || ('评估报告 #' + selectedResult.id) }}</span>
                  <span class="report-time">{{ formatDate(selectedResult.created_at) }}</span>
                </div>
                <div>
                  <el-button text type="primary" @click="exportReport"><el-icon><Download /></el-icon>导出 JSON</el-button>
                  <el-button text type="danger" @click="deleteEvaluation"><el-icon><Delete /></el-icon>删除</el-button>
                </div>
              </div>
            </template>
            <div class="overview-grid">
              <div class="overview-block">
                <span>模型</span>
                <strong>{{ getModelName(selectedResult.model_id) }}</strong>
              </div>
              <div class="overview-block">
                <span>测试数据集</span>
                <strong>{{ getDatasetName(selectedResult.dataset_id) }}</strong>
              </div>
              <div class="overview-block">
                <span>任务类型</span>
                <strong>{{ taskLabel(selectedResult.task_type) }}</strong>
              </div>
              <div class="overview-block">
                <span>测试样本</span>
                <strong>{{ visualization?.sample_count ?? selectedResult.test_samples ?? '-' }}</strong>
              </div>
            </div>
          </el-card>

          <el-card class="report-card" shadow="never">
            <template #header>
              <div class="section-heading">
                <div>
                  <span class="card-title">核心指标</span>
                  <p>优先展示训练任务实际上报的指标；全部数值均由本次完整测试集重新计算。</p>
                </div>
              </div>
            </template>
            <div class="metrics-grid">
              <div v-for="[key, value] in primaryMetricEntries" :key="key" class="metric-block primary-metric">
                <span>{{ formatMetricLabel(key) }}</span>
                <strong>{{ formatMetricValue(key, value) }}</strong>
                <small>{{ metricDescription(key) }}</small>
              </div>
            </div>
          </el-card>

          <el-card v-if="supplementaryMetricEntries.length" class="report-card" shadow="never">
            <template #header><div class="section-heading"><div><span class="card-title">补充诊断指标</span><p>用于进一步理解误差和拟合表现，不替代模型训练时选定的核心指标。</p></div></div></template>
            <div class="metrics-grid supplementary-grid">
              <div v-for="[key, value] in supplementaryMetricEntries" :key="key" class="metric-block supplementary-metric">
                <span>{{ formatMetricLabel(key) }}</span><strong>{{ formatMetricValue(key, value) }}</strong><small>{{ metricDescription(key) }}</small>
              </div>
            </div>
          </el-card>

          <el-alert
            v-if="!visualization"
            class="legacy-alert"
            title="这是一条历史评估记录，当时没有保存预测明细。请使用相同模型和数据集重新评估，即可生成下方图表。"
            type="info"
            :closable="false"
            show-icon
          />

          <template v-else>
            <div class="report-section-title">
              <div>
                <h3>模型表现</h3>
                <p>不同任务只展示有实际意义的图表。</p>
              </div>
              <el-tag effect="plain">真实测试结果</el-tag>
            </div>

            <div v-if="isRegressionTask(selectedResult.task_type)" class="chart-grid">
              <ChartCard :title="isTimeSeriesTask(selectedResult.task_type) ? '真实值与预测值时序走势' : '真实值与预测值走势'" height="320px">
                <div ref="trendChartRef" class="chart" />
              </ChartCard>
              <ChartCard title="预测拟合散点" height="320px">
                <div ref="scatterChartRef" class="chart" />
              </ChartCard>
              <ChartCard title="残差分布" height="320px">
                <div ref="residualChartRef" class="chart" />
              </ChartCard>
            </div>

            <div v-else class="chart-grid">
              <ChartCard title="实际类别与预测类别分布" height="320px">
                <div ref="classDistributionChartRef" class="chart" />
              </ChartCard>
              <ChartCard title="混淆矩阵" height="320px">
                <div ref="confusionChartRef" class="chart" />
              </ChartCard>
              <ChartCard
                v-if="visualization.roc"
                :title="`ROC 曲线（AUC ${formatNumber(visualization.roc.auc)}）`"
                height="320px"
              >
                <div ref="rocChartRef" class="chart" />
              </ChartCard>
            </div>

            <div class="report-section-title">
              <div>
                <h3>数据与特征诊断</h3>
                <p>用于理解测试数据结构，不参与重新训练。</p>
              </div>
              <el-tag type="success" effect="plain">最多展示 12 个特征</el-tag>
            </div>

            <div class="chart-grid">
              <ChartCard v-if="visualization.pca" title="PCA 主成分投影" height="340px">
                <div ref="pcaChartRef" class="chart" />
              </ChartCard>
              <ChartCard v-if="visualization.correlation" title="特征相关性热力图" height="340px">
                <div ref="correlationChartRef" class="chart" />
              </ChartCard>
              <ChartCard v-if="visualization.feature_distribution" title="特征分布概览" height="340px">
                <div ref="featureBoxChartRef" class="chart" />
              </ChartCard>
            </div>
          </template>
        </template>

        <el-card v-else class="empty-report" shadow="never">
          <el-empty description="选择一条历史记录，或创建新的模型评估" :image-size="160" />
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="(evaluationResults || []).length >= 2" class="comparison-card" shadow="never">
      <template #header>
        <div class="card-header">
          <div>
            <span class="card-title">历史指标对比</span>
            <span class="report-time">选择两条或更多同类评估记录</span>
          </div>
          <el-button type="primary" :disabled="compareSelection.length < 2" @click="openComparison">查看雷达图</el-button>
        </div>
      </template>
      <el-table :data="evaluationResults || []" size="small" max-height="300" stripe @selection-change="onCompareSelect">
        <el-table-column type="selection" width="50" />
        <el-table-column label="评测记录" min-width="150"><template #default="{ row }">{{ row.name || ('#' + row.id) }}</template></el-table-column>
        <el-table-column label="模型" min-width="180"><template #default="{ row }">{{ getModelName(row.model_id) }}</template></el-table-column>
        <el-table-column label="数据集" min-width="180"><template #default="{ row }">{{ getDatasetName(row.dataset_id) }}</template></el-table-column>
        <el-table-column label="任务" width="100"><template #default="{ row }">{{ taskLabel(row.task_type) }}</template></el-table-column>
        <el-table-column label="指标" min-width="260"><template #default="{ row }">{{ formatMetrics(row) }}</template></el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="showCompare" title="历史评估指标对比" width="760px" destroy-on-close>
      <div ref="radarChartRef" class="radar-chart" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Delete, Download } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { del, get, post } from '@/api/legacy'
import ChartCard from '@/components/ChartCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useAsyncData } from '@/composables/useAsyncData'

const { data: evaluationResults, loading: resultsLoading, refresh: refreshResults } = useAsyncData<any[]>(
  () => get('/model-evaluation/results').then((res: any) => res.items || []).catch(() => [])
)

const evaluating = ref(false)
const locatingDataset = ref(false)
const models = ref<any[]>([])
const datasets = ref<any[]>([])
const targetColumns = ref<string[]>([])
const targetColumnsLoading = ref(false)
const datasetIdInput = ref('')
const datasetKeyword = ref('')
const selectedResult = ref<any>(null)
const selectedProfile = ref<any>(null)
const compareSelection = ref<any[]>([])
const showCompare = ref(false)

const trendChartRef = ref<HTMLElement | null>(null)
const scatterChartRef = ref<HTMLElement | null>(null)
const residualChartRef = ref<HTMLElement | null>(null)
const classDistributionChartRef = ref<HTMLElement | null>(null)
const confusionChartRef = ref<HTMLElement | null>(null)
const rocChartRef = ref<HTMLElement | null>(null)
const pcaChartRef = ref<HTMLElement | null>(null)
const correlationChartRef = ref<HTMLElement | null>(null)
const featureBoxChartRef = ref<HTMLElement | null>(null)
const radarChartRef = ref<HTMLElement | null>(null)
const chartInstances = new Map<string, echarts.ECharts>()

const evalForm = reactive({
  model_id: null as number | null,
  dataset_id: null as number | null,
  task_type: 'regression',
  target_column: ''
})

const visualization = computed(() => selectedResult.value?.visualization || null)
const metricEntries = computed<[string, number][]>(() =>
  Object.entries(selectedResult.value?.metrics || {}).filter((entry): entry is [string, number] => typeof entry[1] === 'number')
)
const primaryMetricKeys = computed<string[]>(() => {
  const configured = selectedResult.value?.primary_metrics || selectedResult.value?.evaluation_profile?.primary_metrics || []
  if (configured.length) return configured
  const task = selectedResult.value?.task_type
  return task === 'classification' ? ['accuracy', 'f1'] : isTimeSeriesTask(task) ? ['MAE', 'RMSE', 'MAPE'] : ['MAE', 'RMSE', 'R2']
})
const primaryMetricEntries = computed<[string, number][]>(() => {
  const wanted = new Set(primaryMetricKeys.value.map(normalizeMetricKey))
  const matched = metricEntries.value.filter(([key]) => wanted.has(normalizeMetricKey(key)))
  if (matched.length) return matched
  // 兼容尚未保存核心指标元数据的历史记录，确保报告至少有一项核心指标。
  return metricEntries.value.slice(0, 4)
})
const supplementaryMetricEntries = computed<[string, number][]>(() => {
  const primary = new Set(primaryMetricEntries.value.map(([key]) => normalizeMetricKey(key)))
  return metricEntries.value.filter(([key]) => !primary.has(normalizeMetricKey(key)))
})
const profileHint = computed(() => selectedProfile.value
  ? `已从训练记录识别为${taskLabel(selectedProfile.value.task_type)}；核心指标：${selectedProfile.value.primary_metrics.map(formatMetricLabel).join('、')}`
  : '选择模型后自动读取训练任务类型与核心指标；无法识别时可手动调整。')
const filteredDatasets = computed(() => {
  const keyword = datasetKeyword.value.trim().toLowerCase()
  if (!keyword) return datasets.value
  return datasets.value.filter(dataset => String(dataset.name || '').toLowerCase().includes(keyword))
})

function taskLabel(taskType: string) {
  if (taskType === 'classification') return '分类'
  if (isTimeSeriesTask(taskType)) return '时序预测'
  return '回归'
}

function isTimeSeriesTask(taskType: string) {
  return ['time_series', 'timeseries', 'forecasting', 'time_series_forecasting'].includes(taskType)
}

function isRegressionTask(taskType: string) {
  return taskType === 'regression' || isTimeSeriesTask(taskType)
}

function normalizeMetricKey(key: string) {
  return String(key || '').toLowerCase().replace('²', '2')
}

function formatDate(date: string) {
  return date ? new Date(date).toLocaleString('zh-CN') : '-'
}

function getModelName(id: number) {
  const model = models.value.find(item => item.id === id)
  return `#${id} ${model?.name || selectedResult.value?.model_name || '未知模型'}`
}

function getDatasetName(id: number) {
  const dataset = datasets.value.find(item => item.id === id)
  return `#${id} ${dataset?.name || selectedResult.value?.dataset_name || '未知数据集'}`
}

function formatMetricLabel(key: string) {
  const labels: Record<string, string> = {
    accuracy: '准确率', precision: '精确率', recall: '召回率', f1: 'F1 分数',
    MSE: '均方误差', RMSE: '均方根误差', MAE: '平均绝对误差', R2: 'R²', MAPE: '平均绝对百分比误差'
  }
  return labels[key] || key
}

function metricDescription(key: string) {
  const descriptions: Record<string, string> = {
    accuracy: '预测正确的样本占比', precision: '预测为某类时的可靠程度',
    recall: '实际样本被识别出来的程度', f1: '精确率与召回率的综合表现',
    MSE: '误差平方的平均值，越小越好', RMSE: '与目标同量纲，越小越好',
    MAE: '平均预测偏差，越小越好', R2: '拟合程度，越接近 1 越好',
    MAPE: '相对真实值的平均百分比误差，越小越好'
  }
  return descriptions[key] || '本次测试集计算结果'
}

function formatNumber(value: any, digits = 4) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(digits) : '-'
}

function formatMetricValue(key: string, value: number) {
  if (key === 'MAPE') return `${formatNumber(value, 2)}%`
  return ['accuracy', 'precision', 'recall', 'f1'].includes(key)
    ? `${(value * 100).toFixed(2)}%`
    : formatNumber(value)
}

function formatMetrics(row: any) {
  return Object.entries(row.metrics || {})
    .filter(([, value]) => typeof value === 'number')
    .map(([key, value]) => `${formatMetricLabel(key)} ${formatMetricValue(key, value as number)}`)
    .join(' · ')
}

async function loadModels() {
  try {
    const res: any = await get('/models')
    models.value = res.models || res.items || []
  } catch (error) {
    console.error('加载模型失败', error)
  }
}

async function onModelChange(modelId: number | null) {
  selectedProfile.value = null
  if (!modelId) return
  try {
    selectedProfile.value = await get(`/model-evaluation/models/${modelId}/profile`)
    evalForm.task_type = selectedProfile.value.task_type
    applyPreferredTarget()
  } catch (error: any) {
    ElMessage.warning(`未能自动识别模型任务类型：${error.message}`)
  }
}

async function loadDatasets() {
  try {
    const res: any = await get('/datasets')
    datasets.value = res.datasets || res.items || []
  } catch (error) {
    console.error('加载数据集失败', error)
  }
}

function applyPreferredTarget() {
  const preferred = String(selectedProfile.value?.target_column || '').trim()
  if (preferred && targetColumns.value.includes(preferred)) {
    evalForm.target_column = preferred
  }
}

async function loadTargetColumns(datasetId: number | null) {
  targetColumns.value = []
  if (!datasetId) {
    evalForm.target_column = ''
    return
  }
  targetColumnsLoading.value = true
  try {
    const preview: any = await get(`/datasets/${datasetId}/rows`, { offset: 0, limit: 1 })
    targetColumns.value = Array.isArray(preview?.columns) ? preview.columns.map((column: any) => String(column)) : []
    if (evalForm.target_column && !targetColumns.value.includes(evalForm.target_column)) {
      evalForm.target_column = ''
    }
    applyPreferredTarget()
  } catch (error: any) {
    targetColumns.value = []
    ElMessage.warning(`未能读取数据集列名：${error.message}`)
  } finally {
    targetColumnsLoading.value = false
  }
}

async function onDatasetChange(datasetId: number | null) {
  syncDatasetId(datasetId)
  await loadTargetColumns(datasetId)
}

function syncDatasetId(datasetId: number | null) {
  datasetIdInput.value = datasetId ? String(datasetId) : ''
}

async function selectDatasetById() {
  const rawId = datasetIdInput.value.trim()
  if (!/^\d+$/.test(rawId) || Number(rawId) <= 0) {
    ElMessage.warning('请输入有效的数据集 ID')
    return
  }
  locatingDataset.value = true
  try {
    const dataset: any = await get(`/datasets/${Number(rawId)}`)
    if (!datasets.value.some(item => item.id === dataset.id)) datasets.value.unshift(dataset)
    evalForm.dataset_id = dataset.id
    await onDatasetChange(dataset.id)
    datasetKeyword.value = ''
    ElMessage.success(`已选择 #${dataset.id} ${dataset.name}`)
  } catch (error: any) {
    evalForm.dataset_id = null
    ElMessage.error(`未找到该数据集：${error.message}`)
  } finally {
    locatingDataset.value = false
  }
}

async function startEvaluation() {
  if (!evalForm.model_id || !evalForm.dataset_id) {
    ElMessage.warning('请选择模型和测试数据集')
    return
  }
  evaluating.value = true
  try {
    const result: any = await post('/model-evaluation/evaluate', {
      model_id: evalForm.model_id,
      dataset_id: evalForm.dataset_id,
      task_type: evalForm.task_type,
      target_column: evalForm.target_column.trim() || null
    })
    await refreshResults()
    await selectResult(result)
    ElMessage.success('评估完成，可视化报告已生成')
  } catch (error: any) {
    ElMessage.error(`评估失败：${error.message}`)
  } finally {
    evaluating.value = false
  }
}

async function selectResult(result: any) {
  selectedResult.value = result
  disposeReportCharts()
  try {
    selectedResult.value = await get(`/model-evaluation/results/${result.id}`)
    await nextTick()
    renderReportCharts()
  } catch (error: any) {
    ElMessage.error(`读取评估详情失败：${error.message}`)
  }
}

async function deleteEvaluation() {
  if (!selectedResult.value) return
  try {
    await ElMessageBox.confirm('确定删除这条评估记录吗？', '删除评估记录', { type: 'warning' })
    await del(`/model-evaluation/results/${selectedResult.value.id}`)
    disposeReportCharts()
    selectedResult.value = null
    await refreshResults()
    ElMessage.success('评估记录已删除')
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(`删除失败：${error.message}`)
  }
}

function exportReport() {
  if (!selectedResult.value) return
  const blob = new Blob([JSON.stringify(selectedResult.value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `evaluation_report_${selectedResult.value.id}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

function setChart(name: string, element: HTMLElement | null, option: echarts.EChartsOption) {
  if (!element) return
  chartInstances.get(name)?.dispose()
  const chart = echarts.init(element)
  chart.setOption(option)
  chartInstances.set(name, chart)
}

function baseAxes(xName = '', yName = '') {
  return {
    grid: { left: 56, right: 24, top: 38, bottom: 48 },
    tooltip: { trigger: 'axis' as const },
    xAxis: { type: 'category' as const, name: xName, nameLocation: 'middle' as const, nameGap: 32 },
    yAxis: { type: 'value' as const, name: yName, scale: true }
  }
}

function renderReportCharts() {
  const data = visualization.value
  if (!data) return

  if (data.prediction_trend) {
    setChart('trend', trendChartRef.value, {
      ...baseAxes('测试样本序号', '预测目标'),
      legend: { data: ['真实值', '预测值'], top: 0 },
      xAxis: { type: 'category', data: data.prediction_trend.map((item: any[]) => item[0]) },
      series: [
        { name: '真实值', type: 'line', data: data.prediction_trend.map((item: any[]) => item[1]), showSymbol: false, lineStyle: { width: 2 } },
        { name: '预测值', type: 'line', data: data.prediction_trend.map((item: any[]) => item[2]), showSymbol: false, lineStyle: { width: 2, type: 'dashed' } }
      ]
    })
  }

  if (data.actual_vs_predicted) {
    const values = data.actual_vs_predicted.flatMap((item: number[]) => item).filter(Number.isFinite)
    const minimum = Math.min(...values)
    const maximum = Math.max(...values)
    setChart('scatter', scatterChartRef.value, {
      grid: { left: 60, right: 24, top: 28, bottom: 48 },
      tooltip: { trigger: 'item', formatter: (item: any) => `真实值：${item.value[0]}<br>预测值：${item.value[1]}` },
      xAxis: { type: 'value', name: '真实值', scale: true },
      yAxis: { type: 'value', name: '预测值', scale: true },
      series: [
        { name: '测试样本', type: 'scatter', data: data.actual_vs_predicted, symbolSize: 7, itemStyle: { opacity: 0.65 } },
        { name: '理想拟合', type: 'line', data: [[minimum, minimum], [maximum, maximum]], symbol: 'none', lineStyle: { type: 'dashed', color: '#909399' } }
      ]
    })
  }

  if (data.residual_histogram) {
    setChart('residual', residualChartRef.value, {
      ...baseAxes('残差区间', '样本数'),
      xAxis: { type: 'category', data: data.residual_histogram.labels, axisLabel: { rotate: 35 } },
      series: [{ type: 'bar', data: data.residual_histogram.counts, itemStyle: { color: '#7c3aed', borderRadius: [4, 4, 0, 0] } }]
    })
  }

  if (data.class_distribution) {
    setChart('classDistribution', classDistributionChartRef.value, {
      ...baseAxes('类别', '样本数'),
      legend: { data: ['实际', '预测'], top: 0 },
      xAxis: { type: 'category', data: data.class_distribution.labels },
      series: [
        { name: '实际', type: 'bar', data: data.class_distribution.actual },
        { name: '预测', type: 'bar', data: data.class_distribution.predicted }
      ]
    })
  }

  if (data.confusion_matrix) {
    const labels = data.confusion_matrix.labels
    const heatmap = data.confusion_matrix.values.flatMap((row: number[], y: number) => row.map((value, x) => [x, y, value]))
    const maximum = Math.max(1, ...heatmap.map((item: number[]) => item[2]))
    setChart('confusion', confusionChartRef.value, {
      grid: { left: 72, right: 60, top: 24, bottom: 60 },
      tooltip: { formatter: (item: any) => `实际：${labels[item.value[1]]}<br>预测：${labels[item.value[0]]}<br>样本数：${item.value[2]}` },
      xAxis: { type: 'category', data: labels, name: '预测类别', splitArea: { show: true } },
      yAxis: { type: 'category', data: labels, name: '实际类别', splitArea: { show: true } },
      visualMap: { min: 0, max: maximum, calculable: true, orient: 'vertical', right: 0, top: 'center', inRange: { color: ['#eff6ff', '#60a5fa', '#1d4ed8'] } },
      series: [{ type: 'heatmap', data: heatmap, label: { show: true } }]
    })
  }

  if (data.roc) {
    setChart('roc', rocChartRef.value, {
      grid: { left: 56, right: 24, top: 28, bottom: 48 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'value', name: '假阳性率', min: 0, max: 1 },
      yAxis: { type: 'value', name: '真阳性率', min: 0, max: 1 },
      series: [
        { name: 'ROC', type: 'line', data: data.roc.points, showSymbol: false, lineStyle: { width: 3 }, areaStyle: { opacity: 0.08 } },
        { name: '随机基准', type: 'line', data: [[0, 0], [1, 1]], symbol: 'none', lineStyle: { type: 'dashed', color: '#909399' } }
      ]
    })
  }

  if (data.pca) renderPca(data.pca)

  if (data.correlation) {
    const features = data.correlation.features
    setChart('correlation', correlationChartRef.value, {
      grid: { left: 80, right: 56, top: 20, bottom: 76 },
      tooltip: { formatter: (item: any) => `${features[item.value[1]]} × ${features[item.value[0]]}<br>相关系数：${formatNumber(item.value[2], 3)}` },
      xAxis: { type: 'category', data: features, axisLabel: { rotate: 35 } },
      yAxis: { type: 'category', data: features },
      visualMap: { min: -1, max: 1, calculable: true, orient: 'vertical', right: 0, top: 'center', inRange: { color: ['#2563eb', '#f8fafc', '#dc2626'] } },
      series: [{ type: 'heatmap', data: data.correlation.values, label: { show: features.length <= 6, formatter: (item: any) => formatNumber(item.value[2], 2) } }]
    })
  }

  if (data.feature_distribution) {
    const distributions = data.feature_distribution.filter((item: any) => item.values?.length === 5)
    setChart('featureBox', featureBoxChartRef.value, {
      grid: { left: 60, right: 24, top: 28, bottom: 82 },
      tooltip: { trigger: 'item' },
      xAxis: { type: 'category', data: distributions.map((item: any) => item.name), axisLabel: { rotate: 35 } },
      yAxis: { type: 'value', name: '特征值', scale: true },
      series: [{ name: '最小值 / 四分位 / 中位数 / 最大值', type: 'boxplot', data: distributions.map((item: any) => item.values), itemStyle: { color: '#dbeafe', borderColor: '#2563eb' } }]
    })
  }
}

function renderPca(pca: any) {
  const variance = pca.explained_variance || [0, 0]
  if (selectedResult.value?.task_type === 'classification') {
    const groups = [...new Set(pca.points.map((item: any[]) => item[2]))]
    setChart('pca', pcaChartRef.value, {
      grid: { left: 56, right: 24, top: 38, bottom: 48 },
      legend: { data: groups, top: 0, type: 'scroll' },
      tooltip: { trigger: 'item' },
      xAxis: { type: 'value', name: `主成分 1（${((variance[0] || 0) * 100).toFixed(1)}%）`, scale: true },
      yAxis: { type: 'value', name: `主成分 2（${((variance[1] || 0) * 100).toFixed(1)}%）`, scale: true },
      series: groups.map(label => ({
        name: label,
        type: 'scatter',
        symbolSize: 7,
        data: pca.points.filter((item: any[]) => item[2] === label).map((item: any[]) => [item[0], item[1]])
      }))
    })
    return
  }
  const points = pca.points.map((item: any[]) => [item[0], item[1], Number(item[2])])
  const targets = points.map((item: number[]) => item[2]).filter(Number.isFinite)
  setChart('pca', pcaChartRef.value, {
    grid: { left: 56, right: 70, top: 28, bottom: 48 },
    tooltip: { formatter: (item: any) => `PC1：${formatNumber(item.value[0])}<br>PC2：${formatNumber(item.value[1])}<br>目标值：${formatNumber(item.value[2])}` },
    xAxis: { type: 'value', name: `主成分 1（${((variance[0] || 0) * 100).toFixed(1)}%）`, scale: true },
    yAxis: { type: 'value', name: `主成分 2（${((variance[1] || 0) * 100).toFixed(1)}%）`, scale: true },
    visualMap: targets.length ? { min: Math.min(...targets), max: Math.max(...targets), dimension: 2, right: 0, top: 'center', text: ['目标值高', '目标值低'], inRange: { color: ['#2563eb', '#f59e0b', '#dc2626'] } } : undefined,
    series: [{ type: 'scatter', data: points, symbolSize: 7 }]
  })
}

function disposeReportCharts() {
  for (const [name, chart] of chartInstances) {
    if (name !== 'radar') {
      chart.dispose()
      chartInstances.delete(name)
    }
  }
}

function onCompareSelect(rows: any[]) {
  compareSelection.value = rows
}

async function openComparison() {
  const taskTypes = new Set(compareSelection.value.map(item => item.task_type))
  if (taskTypes.size > 1) {
    ElMessage.warning('不同任务类型的指标含义不同，请选择同一任务类型的记录')
    return
  }
  showCompare.value = true
  await nextTick()
  renderRadarChart()
}

function renderRadarChart() {
  const rows = compareSelection.value
  if (!radarChartRef.value || rows.length < 2) return
  const keys = [...new Set(rows.flatMap(row => Object.keys(row.metrics || {})))].filter(key =>
    rows.every(row => typeof row.metrics?.[key] === 'number')
  )
  const maxima = Object.fromEntries(keys.map(key => [key, Math.max(...rows.map(row => Math.abs(row.metrics[key])), 1e-9)]))
  setChart('radar', radarChartRef.value, {
    tooltip: {},
    legend: { data: rows.map(row => getModelName(row.model_id)), bottom: 0, type: 'scroll' },
    radar: { indicator: keys.map(key => ({ name: formatMetricLabel(key), max: maxima[key] })), radius: '62%', center: ['50%', '45%'] },
    series: [{
      type: 'radar',
      data: rows.map(row => ({ name: getModelName(row.model_id), value: keys.map(key => Math.abs(row.metrics[key])) }))
    }]
  })
}

function handleResize() {
  chartInstances.forEach(chart => chart.resize())
}

watch(showCompare, visible => {
  if (!visible) {
    chartInstances.get('radar')?.dispose()
    chartInstances.delete('radar')
  }
})

onMounted(() => {
  loadModels()
  loadDatasets()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chartInstances.forEach(chart => chart.dispose())
  chartInstances.clear()
})
</script>

<style scoped lang="scss">
.model-evaluation-page {
  padding: 20px;
  color: #172033;
}

.side-card,
.report-card,
.empty-report,
.comparison-card {
  border: 1px solid #e6ebf2;
  border-radius: 12px;
}

.side-card {
  margin-bottom: 20px;
}

.full-width {
  width: 100%;
}

.dataset-search {
  margin: 10px 0;
}

.field-hint,
.report-time,
.section-heading p,
.report-section-title p {
  color: #7a8498;
  font-size: 12px;
}

.field-hint {
  margin-top: 7px;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
  color: #172033;
}

.card-header,
.section-heading,
.report-section-title,
.history-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.report-time {
  margin-left: 12px;
}

.history-list {
  min-height: 120px;
  max-height: 520px;
  overflow-y: auto;
}

.history-item {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 9px;
  padding: 12px;
  color: #505b70;
  font: inherit;
  font-size: 12px;
  text-align: left;
  background: #fff;
  border: 1px solid #e6ebf2;
  border-radius: 9px;
  cursor: pointer;
  transition: 0.18s ease;
}

.history-item:hover,
.history-item.active {
  border-color: #3b82f6;
  background: #f5f9ff;
  box-shadow: 0 4px 14px rgb(59 130 246 / 8%);
}

.history-item strong {
  color: #1e3a8a;
}

.history-item small {
  color: #98a1b2;
}

.tag-row {
  display: flex;
  gap: 5px;
}

.report-card {
  margin-bottom: 18px;
}

.overview-grid,
.metrics-grid {
  display: grid;
  gap: 12px;
}

.overview-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.metrics-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.overview-block,
.metric-block {
  min-width: 0;
  padding: 16px;
  background: #f7f9fc;
  border: 1px solid #edf0f5;
  border-radius: 10px;
}

.overview-block span,
.metric-block span,
.metric-block small {
  display: block;
  color: #7a8498;
  font-size: 12px;
}

.overview-block strong {
  display: block;
  margin-top: 7px;
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-block {
  background: linear-gradient(145deg, #f8fbff, #f5f7fb);
}

.primary-metric {
  background: linear-gradient(145deg, #eff6ff, #f8fbff);
  border-color: #bfdbfe;
}

.supplementary-metric strong {
  color: #526072;
  font-size: 21px;
}

.metric-block strong {
  display: block;
  margin: 8px 0 7px;
  color: #155eef;
  font-size: 25px;
  letter-spacing: -0.5px;
}

.section-heading p,
.report-section-title p {
  margin: 5px 0 0;
}

.legacy-alert {
  margin-bottom: 18px;
}

.report-section-title {
  margin: 22px 2px 13px;
}

.report-section-title h3 {
  margin: 0;
  font-size: 17px;
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.chart {
  width: 100%;
  height: 100%;
}

.empty-report {
  min-height: 600px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.comparison-card {
  margin-top: 20px;
}

.radar-chart {
  width: 100%;
  height: 480px;
}

@media (max-width: 1280px) {
  .overview-grid,
  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .chart-grid,
  .overview-grid,
  .metrics-grid {
    grid-template-columns: 1fr;
  }
}
</style>
