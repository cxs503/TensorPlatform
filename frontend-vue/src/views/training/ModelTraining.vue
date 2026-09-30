<template>
  <div class="model-training-page">
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><Cpu /></el-icon>
        模型训练
      </h1>
      <p class="page-desc">管理训练任务、资源配置、监控训练进度</p>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6" v-for="stat in stats" :key="stat.key">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-icon" :style="{ background: stat.bgColor }">
            <el-icon :size="24"><component :is="stat.icon" /></el-icon>
          </div>
          <div class="stat-content">
            <div class="stat-value">{{ stat.value }}</div>
            <div class="stat-label">{{ stat.label }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 工具栏 -->
    <el-card class="toolbar-card" shadow="never">
      <el-row :gutter="16" align="middle">
        <el-col :xs="24" :sm="16">
          <el-select v-model="filterStatus" placeholder="状态筛选" clearable style="width: 150px; margin-right: 12px">
            <el-option label="全部" value="" />
            <el-option label="运行中" value="running" />
            <el-option label="已完成" value="completed" />
            <el-option label="失败" value="failed" />
            <el-option label="排队中" value="pending" />
          </el-select>
          <el-select v-model="filterFramework" placeholder="框架筛选" clearable style="width: 150px">
            <el-option label="全部" value="" />
            <el-option label="PyTorch" value="pytorch" />
            <el-option label="TensorFlow" value="tensorflow" />
            <el-option label="Scikit-learn" value="sklearn" />
          </el-select>
        </el-col>
        <el-col :xs="24" :sm="8" class="text-right">
          <el-button type="primary" @click="showCreateDialog = true" v-if="false">
            <el-icon><Plus /></el-icon>
            新建训练
          </el-button>
        </el-col>
      </el-row>
    </el-card>

    <!-- 训练任务列表 -->
    <el-card class="tasks-card" shadow="never">
      <el-table :data="pagedTasks" style="width: 100%" v-loading="tasksLoading">
        <el-table-column prop="name" label="任务名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span style="font-weight:600">{{ row.name || row.config?.name || row.job_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="job_id" label="任务ID" width="150">
          <template #default="{ row }">
            <span style="font-family:monospace;font-size:12px">{{ row.job_id?.replace('train_','') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="模型ID" width="90">
          <template #default="{ row }">{{ row.registered_model_id || '-' }}</template>
        </el-table-column>
        <el-table-column label="数据集ID" width="95">
          <template #default="{ row }">{{ row.config?.dataset_id ?? row.dataset_id ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="训练指标" min-width="210">
          <template #default="{ row }">
            <div v-if="metricEntries(row).length" class="task-metrics">
              <span
                v-for="metric in metricEntries(row).slice(0, 3)"
                :key="metric.key"
                class="task-metric"
              >
                {{ metric.label }}={{ formatMetricValue(metric.value) }}
              </span>
              <el-tooltip
                v-if="metricEntries(row).length > 3"
                :content="metricTooltip(row)"
                placement="top"
              >
                <span class="more-metrics">+{{ metricEntries(row).length - 3 }}项</span>
              </el-tooltip>
            </div>
            <span v-else style="color:#94a3b8">-</span>
          </template>
        </el-table-column>
        <el-table-column label="执行进度" width="145">
          <template #default="{ row }">
            {{ progressUnitLabel(row) }} {{ row.current_epoch || 0 }}/{{ row.total_epochs || 0 }}
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="160">
          <template #default="{ row }">
            <el-progress
              :percentage="normalizedProgress(row)"
              :status="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'exception' : ''"
              :striped="isActiveTask(row)"
              :striped-flow="isActiveTask(row)"
              :stroke-width="16"
            />
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="160">
          <template #default="{ row }">
            {{ formatDate(row.started_at) }}
          </template>
        </el-table-column>
        <el-table-column label="完成时间" width="160">
          <template #default="{ row }">
            {{ formatDate(row.finished_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button text size="small" @click="viewTask(row)">
              <el-icon><View /></el-icon>
            </el-button>
            <el-button text size="small" type="success" @click="deployModel(row)" v-if="row.status==='completed'">
              <el-icon><Upload /></el-icon>
            </el-button>
            <el-button text size="small" type="danger" @click="deleteTask(row)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-container">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>

    <!-- 创建训练任务对话框 -->
    <el-dialog v-model="showCreateDialog" title="新建训练任务" width="700px" destroy-on-close>
      <el-form :model="createForm" label-width="120px">
        <el-form-item label="任务名称" required>
          <el-input v-model="createForm.name" placeholder="请输入任务名称" />
        </el-form-item>
        <el-form-item label="选择模型" required>
          <el-select v-model="createForm.model_type" placeholder="请选择算法模型" style="width: 100%">
            <el-option-group label="平台内置算法">
              <el-option v-for="m in builtinModels" :key="m.id" :label="`${m.name} (${m.framework})`" :value="m.id" />
            </el-option-group>
            <el-option-group label="算法商店（自有/上传算法）">
              <el-option v-for="m in storeModels" :key="m.id" :label="`#${m.id} ${m.name} · ${m.trainable ? '可训练' : '需配置训练适配器'}`" :value="m.id" :disabled="!m.trainable" />
            </el-option-group>
          </el-select>
          <div v-if="selectedModel?.source==='algorithm_store'" style="margin-top:6px;font-size:12px;color:#909399">
            来源：算法商店 · 训练适配器：{{ selectedModel.adapter_name || selectedModel.adapter || selectedModel.reason }}
          </div>
        </el-form-item>
        <el-form-item label="选择数据集" required>
          <el-select v-model="createForm.dataset_id" placeholder="请选择数据集" style="width: 100%" @change="onDatasetChange">
            <el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name}`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标列">
          <el-select v-model="createForm.target_column" placeholder="选择要预测的列（留空自动取最后数值列）" clearable filterable style="width: 100%">
            <el-option v-for="col in datasetColumns" :key="col" :label="col" :value="col" />
          </el-select>
        </el-form-item>
        <el-form-item label="特征列(可选)">
          <el-select v-model="createForm.feature_columns" multiple placeholder="留空=除目标列外所有列都作特征" filterable style="width: 100%">
            <el-option v-for="col in datasetColumns" :key="col" :label="col" :value="col" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务类型" required>
          <el-select v-model="createForm.task_type" style="width: 100%">
            <el-option label="回归 (Regression)" value="regression" />
            <el-option label="分类 (Classification)" value="classification" />
            <el-option label="时序预测 (Time Series Forecasting)" value="time_series_forecasting" />
          </el-select>
        </el-form-item>
        <el-form-item label="超参数配置">
          <div style="width:100%">
            <el-radio-group v-model="createForm.hyperparameter_mode" @change="onCreateParamModeChange">
              <el-radio-button value="default">使用默认值</el-radio-button>
              <el-radio-button value="manual">手动填写</el-radio-button>
              <el-radio-button value="file">上传参数文件</el-radio-button>
            </el-radio-group>
            <el-alert
              v-if="createForm.hyperparameter_mode === 'default'"
              title="不覆盖参数，按照算法脚本或平台训练器中定义的默认值运行"
              type="info" :closable="false" style="margin-top:10px"
            />
            <el-input
              v-else-if="createForm.hyperparameter_mode === 'manual'"
              v-model="createForm.hyperparams" type="textarea" :rows="7"
              placeholder="支持 JSON，或每行 key=value"
              style="margin-top:10px"
            />
            <el-upload
              v-else
              :auto-upload="false" :limit="1" accept=".json,.txt,.yaml,.yml,.cfg,.conf"
              :on-change="selectCreateParamFile" :on-remove="clearCreateParamFile"
              style="margin-top:10px"
            >
              <el-button>选择参数文件</el-button>
              <template #tip><div class="el-upload__tip">最大1MB；支持JSON或扁平的 key=value / key: value</div></template>
            </el-upload>
            <el-input
              v-if="createForm.hyperparameter_mode === 'file' && createForm.hyperparams"
              v-model="createForm.hyperparams" type="textarea" :rows="7" readonly
              :placeholder="createParamFileName" style="margin-top:8px"
            />
          </div>
        </el-form-item>
        <el-divider content-position="left">资源配置</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="CPU核心">
              <el-input-number v-model="createForm.cpu_cores" :min="1" :max="64" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="内存(GB)">
              <el-input-number v-model="createForm.memory_gb" :min="1" :max="256" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="GPU数量">
              <el-input-number v-model="createForm.gpu_count" :min="0" :max="8" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="GPU类型">
              <el-select v-model="createForm.gpu_type" style="width: 100%">
                <el-option label="V100" value="V100" />
                <el-option label="A100" value="A100" />
                <el-option label="RTX 3090" value="RTX3090" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createTask" :loading="creating">创建</el-button>
      </template>
    </el-dialog>

    <!-- 任务详情对话框 -->
    <el-dialog v-model="showDetailDialog" title="训练详情" width="800px" destroy-on-close>
      <el-descriptions :column="2" border v-if="currentTask">
        <el-descriptions-item label="任务ID">{{ currentTask?.job_id || currentTask?.id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="任务名称">{{ currentTask?.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型ID">{{ currentTask?.registered_model_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="数据集ID">{{ currentTask?.config?.dataset_id ?? currentTask?.dataset_id ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型">{{ currentTask?.model_type || currentTask?.config?.model_type || currentTask?.model_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="数据集">{{ currentTask?.config?.dataset_name || currentTask?.dataset_name || (currentTask?.config?.dataset_id != null ? '数据集 #' + currentTask.config.dataset_id : (currentTask?.dataset_id != null ? '数据集 #' + currentTask.dataset_id : '-')) }}</el-descriptions-item>
        <el-descriptions-item label="框架">{{ currentTask?.framework || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="getStatusType(currentTask?.status)">{{ getStatusLabel(currentTask?.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="progressUnitLabel(currentTask)">
          {{ currentTask?.current_epoch || 0 }} / {{ currentTask?.total_epochs || 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ formatDate(currentTask?.started_at || currentTask?.start_time) }}</el-descriptions-item>
        <el-descriptions-item label="结束时间">{{ formatDate(currentTask?.finished_at || currentTask?.end_time) }}</el-descriptions-item>
      </el-descriptions>

      <el-divider content-position="left">训练指标</el-divider>
      <el-row :gutter="16" v-if="detailMetrics && Object.keys(detailMetrics).length">
        <el-col :span="8" v-for="(value, key) in detailMetrics" :key="key">
          <el-card shadow="hover" class="metric-card">
            <div class="metric-label">{{ key }}</div>
            <div class="metric-value">{{ typeof value === 'number' ? value.toFixed(4) : value }}</div>
          </el-card>
        </el-col>
      </el-row>
      <div v-else style="color:#94a3b8;text-align:center;padding:20px">暂无指标</div>
    </el-dialog>

    <!-- 日志查看对话框 -->
    <el-dialog v-model="showLogsDialog" title="训练日志" width="900px" destroy-on-close>
      <div class="logs-container" v-loading="logsLoading">
        <pre class="logs-content">{{ logsContent }}</pre>
      </div>
      <template #footer>
        <el-button @click="refreshLogs">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button @click="showLogsDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Cpu,
  Plus,
  View,
  Document,
  More,
  Refresh,
  Clock,
  CircleCheck,
  Warning,
  Timer
} from '@element-plus/icons-vue'
import { api } from '@/api'
import { readHyperparameterFile, serializeHyperparameters, type HyperparameterMode } from '@/utils/hyperparameters'
import dayjs from 'dayjs'
import { get, post } from '@/api/legacy'

// 状态
const creating = ref(false)
const logsLoading = ref(false)
const showCreateDialog = ref(false)
const showDetailDialog = ref(false)
const showLogsDialog = ref(false)
const filterStatus = ref('')
const filterFramework = ref('')
const currentPage = ref(1)
const pageSize = ref(10)

const currentTask = ref<any>(null)
const createParamFileName = ref('')

// 统计卡片:从真实任务数据计算,不再硬编码 0
const stats = computed(() => {
  const ts = tasks.value || []
  return [
    { key: 'total', label: '总任务数', value: ts.length, icon: 'Cpu', bgColor: 'rgba(59, 130, 246, 0.15)' },
    { key: 'running', label: '运行中', value: ts.filter((t: any) => ['running', 'training', 'pending'].includes(t.status)).length, icon: 'Timer', bgColor: 'rgba(16, 185, 129, 0.15)' },
    { key: 'completed', label: '已完成', value: ts.filter((t: any) => t.status === 'completed').length, icon: 'CircleCheck', bgColor: 'rgba(34, 197, 94, 0.15)' },
    { key: 'failed', label: '失败', value: ts.filter((t: any) => t.status === 'failed').length, icon: 'Warning', bgColor: 'rgba(239, 68, 68, 0.15)' }
  ]
})

const createForm = ref({
  name: '',
  model_type: 'random_forest',
  dataset_id: null as number | null,
  task_type: 'regression',
  target_column: '',
  feature_columns: [] as string[],
  hyperparameter_mode: 'default' as HyperparameterMode,
  hyperparams: '',
  cpu_cores: 4,
  memory_gb: 16,
  gpu_count: 1,
  gpu_type: 'V100'
})

watch(() => createForm.value.task_type, () => {
  if (!models.value.some((model: any) => model.id === createForm.value.model_type)) {
    createForm.value.model_type = ''
  }
})

// 真实训练任务通过共享 run 状态刷新。轮询比旧 model-training WebSocket
// 更可靠：上传源码、内置训练和多 worker 部署都走同一条列表接口。
let tasksPollTimer: number | null = null
let tasksPollInFlight = false

// 计算属性
const filteredTasks = computed(() => {
  let result = tasks.value
  if (filterStatus.value) {
    result = result.filter(t => t.status === filterStatus.value)
  }
  // Note: real-training API doesn't return framework field; skip filter
  // if (filterFramework.value) {
  //   result = result.filter(t => t.framework === filterFramework.value)
  // }
  return result
})

// 列表接口返回全量轻量摘要；分页必须在筛选后的结果上切片，
// 不能仅改变 el-pagination 的页码。
const pagedTasks = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredTasks.value.slice(start, start + pageSize.value)
})

watch([filterStatus, filterFramework], () => {
  currentPage.value = 1
})
watch(() => filteredTasks.value.length, (count) => {
  const lastPage = Math.max(1, Math.ceil(count / pageSize.value))
  if (currentPage.value > lastPage) currentPage.value = lastPage
})
function handlePageSizeChange() {
  currentPage.value = 1
}

// 方法
function getStatusType(status: string) {
  const map: Record<string, string> = {
    running: 'primary',
    completed: 'success',
    failed: 'danger',
    pending: 'warning',
    stopped: 'info'
  }
  return map[status] || 'info'
}

function getStatusLabel(status: string) {
  const map: Record<string, string> = {
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    pending: '排队中',
    stopped: '已停止'
  }
  return map[status] || status
}

function formatDate(date: string) {
  if (!date) return '-'
  return dayjs(date).format('YYYY-MM-DD HH:mm:ss')
}

function isActiveTask(row: any) {
  return ['pending', 'running', 'training', 'queued'].includes(String(row?.status || '').toLowerCase())
}

function normalizedProgress(row: any) {
  if (String(row?.status || '').toLowerCase() === 'completed') return 100
  const value = Number(row?.progress || 0)
  return Math.max(0, Math.min(100, Number.isFinite(value) ? Math.round(value) : 0))
}

function progressUnitLabel(row: any) {
  const kind = row?.progress_kind || row?.config?.source_progress_kind
  if (kind === 'parameter_search') return '参数搜索'
  if (kind === 'script') return '执行步骤'
  return '训练轮次'
}

const metricLabels: Record<string, string> = {
  r2: 'R²', rmse: 'RMSE', mae: 'MAE', mae_test: '测试MAE',
  accuracy: '准确率', f1: 'F1', precision: '精确率', recall: '召回率',
  loss: '损失', val_loss: '验证损失', hit_rate: '命中率', auc: 'AUC',
  mse: 'MSE', mape: 'MAPE', score: '得分'
}
const metricPriority = [
  'r2', 'accuracy', 'f1', 'rmse', 'mae', 'mae_test', 'hit_rate',
  'precision', 'recall', 'auc', 'val_loss', 'loss', 'mse', 'mape', 'score'
]

function metricEntries(row: any) {
  let metrics = row?.best_metrics || row?.metrics || {}
  if (metrics?.metrics && typeof metrics.metrics === 'object') metrics = metrics.metrics
  return Object.entries(metrics)
    .filter(([key, value]) => !['epoch', 'step', 'progress'].includes(key) && Number.isFinite(Number(value)))
    .map(([key, value]) => ({ key, label: metricLabels[key.toLowerCase()] || key, value: Number(value) }))
    .sort((a, b) => {
      const ai = metricPriority.indexOf(a.key.toLowerCase())
      const bi = metricPriority.indexOf(b.key.toLowerCase())
      return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi)
    })
}

function formatMetricValue(value: number) {
  const abs = Math.abs(value)
  if (abs !== 0 && (abs >= 10000 || abs < 0.0001)) return value.toExponential(2)
  if (abs >= 100) return value.toFixed(2)
  return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
}

function metricTooltip(row: any) {
  return metricEntries(row)
    .map(metric => `${metric.label}=${formatMetricValue(metric.value)}`)
    .join('；')
}

// Data fetching with useAsyncData
const { data: tasksData, loading: tasksLoading, refresh: refreshTasks } = useAsyncData(async () => {
  return await get('/real-training/jobs')
})

const tasks = computed(() => {
  const items = tasksData.value?.items || tasksData.value?.tasks || tasksData.value?.jobs || []
  return [...items].sort((a: any, b: any) => {
    const aTime = Date.parse(a.created_at || a.started_at || '') || 0
    const bTime = Date.parse(b.created_at || b.started_at || '') || 0
    return bTime - aTime
  })
})
const total = computed(() => filteredTasks.value.length)

const { data: modelsData } = useAsyncData(async () => {
  const res = await api.realTraining.getAvailableModels()
  return res
})
const models = computed(() => {
  if (!modelsData.value) return []
  const all = modelsData.value.items || []
  const seen = new Set()
  return all.filter((m: any) => (m.task_types || []).includes(createForm.value.task_type)).filter((m: any) => { const k = m.id || m.name; if (seen.has(k)) return false; seen.add(k); return true })
})
const builtinModels = computed(() => models.value.filter((m: any) => m.source === 'builtin'))
const storeModels = computed(() => models.value.filter((m: any) => m.source === 'algorithm_store'))
const selectedModel = computed(() => models.value.find((m: any) => m.id === createForm.value.model_type))

const { data: datasetsData } = useAsyncData(async () => {
  const res = await api.dataset.getList()
  return res
})
const datasets = computed(() => datasetsData.value?.items || datasetsData.value?.datasets || [])
const datasetColumns = ref<string[]>([])
async function onDatasetChange(id: number | null) {
  createForm.value.target_column = ''
  createForm.value.feature_columns = []
  datasetColumns.value = []
  if (!id) return
  try {
    const preview = await get(`/datasets/${id}/rows?offset=0&limit=5`)
    datasetColumns.value = preview?.columns || []
  } catch { datasetColumns.value = [] }
}

const logsContent = ref('')
async function loadLogs(taskId: number) {
  logsLoading.value = true
  const { data: logsData, loading, error } = useAsyncData(async () => {
    const res = await api.modelTraining.getLogs(taskId)
    return res
  })
  // Manual refresh for on-demand log loading
  const res = await api.modelTraining.getLogs(taskId).catch(() => null)
  logsContent.value = res?.logs || '暂无日志'
  logsLoading.value = false
}

async function createTask() {
  if (!createForm.value.name || !createForm.value.model_type || !createForm.value.dataset_id || !createForm.value.task_type) {
    ElMessage.warning('请填写必填项')
    return
  }

  creating.value = true
  try {
    const selected: any = selectedModel.value
    if (!selected) {
      throw new Error('请选择与当前任务类型匹配的模型')
    }
    if (selected?.source === 'algorithm_store' && !selected?.trainable) {
      throw new Error(selected.reason || '该算法无法自动配置训练适配器')
    }
    const effectiveType = selected?.runtime_model_type || selected?.adapter || createForm.value.model_type
    const hp = serializeHyperparameters(createForm.value.hyperparameter_mode, createForm.value.hyperparams)
    const payload = {
      name: createForm.value.name,
      task_type: createForm.value.task_type,
      model_type: effectiveType,
      algorithm_id: selected?.algorithm_id || null,
      dataset_id: createForm.value.dataset_id,
      target_column: createForm.value.target_column || '',
      feature_columns: createForm.value.feature_columns.length ? createForm.value.feature_columns : null,
      hyperparams: hp,
      hyperparameter_source: createForm.value.hyperparameter_mode,
    }
    await post('/real-training/jobs-from-dataset', payload)
    ElMessage.success('创建成功')
    showCreateDialog.value = false
    resetCreateForm()
    refreshTasks()
  } catch (error: any) {
    ElMessage.error('创建失败: ' + error.message)
  } finally {
    creating.value = false
  }
}

function onCreateParamModeChange() {
  createForm.value.hyperparams = ''
  createParamFileName.value = ''
}

async function selectCreateParamFile(uploadFile: any) {
  try {
    const file = uploadFile?.raw as File
    const parsed = await readHyperparameterFile(file)
    createForm.value.hyperparams = parsed.text
    createParamFileName.value = file.name
    ElMessage.success(`已读取参数文件 ${file.name}`)
  } catch (error: any) {
    clearCreateParamFile()
    ElMessage.error(error?.message || '参数文件读取失败')
  }
}

function clearCreateParamFile() {
  createForm.value.hyperparams = ''
  createParamFileName.value = ''
}

function resetCreateForm() {
  createForm.value = {
    name: '',
    model_type: 'random_forest',
    dataset_id: null,
    task_type: 'regression',
    target_column: '',
    feature_columns: [],
    hyperparameter_mode: 'default',
    hyperparams: '',
    cpu_cores: 4,
    memory_gb: 16,
    gpu_count: 1,
    gpu_type: 'V100'
  }
  createParamFileName.value = ''
}

async function viewTask(row: any) {
  currentTask.value = row
  showDetailDialog.value = true
  // 拉取单任务详情补全字段(config.model_type / dataset_id / best_metrics 等)
  const jid = row.job_id || row.id
  if (!jid) return
  try {
    const detail = await api.modelTraining.get(jid)
    if (detail) {
      currentTask.value = { ...row, ...detail }
    }
  } catch (e) {
    // 详情接口失败时沿用列表行数据
  }
}

// 详情弹窗指标:API 返回 best_metrics(优先)/metrics,统一读取并过滤掉非数值展示项
const detailMetrics = computed(() => {
  const t = currentTask.value
  if (!t) return {}
  return t.best_metrics || t.metrics || {}
})

async function viewLogs(row: any) {
  currentTask.value = row
  showLogsDialog.value = true
  await loadLogs(row.id)
}

function refreshLogs() {
  if (currentTask.value) {
    loadLogs(currentTask.value.id)
  }
}

async function handleCommand(command: string, row: any) {
  switch (command) {
    case 'stop':
      await stopTask(row)
      break
    case 'deploy':
      await deployModel(row)
      break
    case 'delete':
      await deleteTask(row)
      break
  }
}

async function stopTask(row: any) {
  try {
    await ElMessageBox.confirm('确定要停止该训练任务吗？', '确认停止', { type: 'warning' })
    await api.modelTraining.stop(row.id)
    ElMessage.success('已停止')
    refreshTasks()
  } catch (error) {
    // 取消
  }
}

async function deployModel(row: any) {
  try {
    await ElMessageBox.confirm('确定要部署该模型吗？', '确认部署', { type: 'info' })
    let modelId = row.registered_model_id || row.model_id || row.config?.registered_model_id
    let modelName = row.model_name || row.name
    // 列表使用轻量摘要；旧任务或跨 worker 场景下若缺少关联字段，
    // 再读取一次任务详情，而不是向部署接口发送空 model_id。
    if (!modelId && row.job_id) {
      const detail = await api.modelTraining.get(row.job_id)
      modelId = detail?.registered_model_id || detail?.model_id || detail?.config?.registered_model_id
      modelName = modelName || detail?.model_name || detail?.name
    }
    if (!modelId) {
      throw new Error('该训练任务尚未登记模型，请等待训练完成或检查模型注册状态')
    }
    await api.deployment.create({
      model_id: Number(modelId),
      name: modelName || `模型 #${modelId}`,
    })
    ElMessage.success('部署成功')
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error('部署失败: ' + error.message)
    }
  }
}

async function deleteTask(row: any) {
  try {
    await ElMessageBox.confirm('确定要删除该训练任务吗？', '确认删除', { type: 'warning' })
    await api.modelTraining.delete(row.job_id)
    ElMessage.success('删除成功')
    refreshTasks()
  } catch (error) {
    // 取消
  }
}

async function pollTrainingTasks() {
  if (tasksPollInFlight || document.hidden) return
  tasksPollInFlight = true
  try {
    tasksData.value = await get('/real-training/jobs')
  } catch {
    // 静默保留上一帧数据；用户主动刷新时仍由 useAsyncData 展示错误状态。
  } finally {
    tasksPollInFlight = false
  }
}

function hasActiveTrainingTask() {
  return (tasksData.value?.items || []).some((task: any) =>
    ['pending', 'running', 'training', 'queued'].includes(String(task?.status || '').toLowerCase())
  )
}

onMounted(() => {
  void pollTrainingTasks()
  // Keep active-job progress responsive, but do not continuously refresh a
  // static history list and compete with other pages for backend resources.
  tasksPollTimer = window.setInterval(() => {
    if (hasActiveTrainingTask()) void pollTrainingTasks()
  }, 2000)
})

onUnmounted(() => {
  if (tasksPollTimer !== null) window.clearInterval(tasksPollTimer)
})
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.model-training-page {
  .task-metrics {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 8px;
    line-height: 1.35;
  }

  .task-metric {
    color: #10b981;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }

  .more-metrics {
    color: #909399;
    cursor: help;
    font-size: 12px;
  }

  .page-header {
    margin-bottom: 24px;

    .page-title {
      font-size: 24px;
      font-weight: 600;
      color: $text-primary;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .page-desc {
      color: $text-secondary;
      font-size: 14px;
    }
  }

  .stats-row {
    margin-bottom: 20px;
  }

  .stat-card {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 16px;

    :deep(.el-card__body) {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 20px;
    }

    .stat-icon {
      width: 56px;
      height: 56px;
      border-radius: $radius-lg;
      display: flex;
      align-items: center;
      justify-content: center;
      color: $primary-light;
    }

    .stat-content {
      .stat-value {
        font-size: 24px;
        font-weight: 700;
        color: $text-primary;
      }

      .stat-label {
        font-size: 14px;
        color: $text-secondary;
        margin-top: 4px;
      }
    }
  }

  .toolbar-card {
    margin-bottom: 20px;

    :deep(.el-card__body) {
      padding: 16px 20px;
    }

    .text-right {
      text-align: right;
    }
  }

  .tasks-card {
    .task-name {
      display: flex;
      align-items: center;
      gap: 8px;

      .task-icon {
        color: $primary-light;
      }
    }

    .pagination-container {
      margin-top: 20px;
      display: flex;
      justify-content: flex-end;
    }
  }

  .metric-card {
    text-align: center;
    margin-bottom: 16px;

    :deep(.el-card__body) {
      padding: 16px;
    }

    .metric-label {
      font-size: 12px;
      color: $text-secondary;
      margin-bottom: 8px;
    }

    .metric-value {
      font-size: 20px;
      font-weight: 600;
      color: $primary-light;
    }
  }

  .logs-container {
    background: $bg-dark;
    border-radius: $radius-md;
    padding: 16px;
    max-height: 500px;
    overflow: auto;

    .logs-content {
      margin: 0;
      font-family: 'Monaco', 'Menlo', monospace;
      font-size: 13px;
      line-height: 1.6;
      color: $text-secondary;
      white-space: pre-wrap;
      word-break: break-all;
    }
  }
}
</style>
