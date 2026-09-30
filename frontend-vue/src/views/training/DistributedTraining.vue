<template>
  <div class="dt-page">
    <PageHeader title="并行参数搜索" desc="Ray 集群 · 使用真实数据执行表格模型的参数并行搜索" />

    <!-- 集群状态 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value" :class="clusterInfo.status === 'ready' ? 'green' : 'red'">{{ clusterInfo.status === 'ready' ? '在线' : clusterInfo.status === 'checking' ? '连接中' : '离线' }}</div>
          <div class="stat-label">Ray 集群</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ clusterInfo.cpus }}</div>
          <div class="stat-label">CPU 核心</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ clusterInfo.nodes }}</div>
          <div class="stat-label">节点数</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ activeJobs }}</div>
          <div class="stat-label">运行中任务</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 创建任务 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span>创建参数搜索任务</span>
          <el-button type="primary" @click="showCreate = !showCreate">
            <el-icon><Plus /></el-icon> {{ showCreate ? '收起' : '创建任务' }}
          </el-button>
        </div>
      </template>

      <el-alert v-if="showCreate" type="info" :closable="false" show-icon class="search-help">
        <template #title>为什么有些算法不能直接选择？</template>
        <div>这里用于“参数并行搜索”：平台会让多个 Worker 用同一算法的不同参数组合同时运行，再比较结果。</div>
        <ul>
          <li><b>可直接选择</b>：内置算法已有可搜索参数；或上传源码已识别训练入口，并声明了学习率、训练轮次等可调整参数。</li>
          <li><b>显示“需声明可搜索参数”</b>：源码可以正常训练，但没有向平台公开可比较的训练参数。请在代码编辑器或算法商店为源码补充参数配置后再进行搜索。</li>
          <li><b>控制仿真算法</b>：例如 FOPDT/MPC，应在“控制仿真任务”中运行，不参与监督模型的参数搜索。</li>
        </ul>
      </el-alert>
      <el-form v-if="showCreate" :model="createForm" label-width="110px" inline class="create-form">
        <el-form-item label="任务名称">
          <el-input v-model="createForm.name" placeholder="如: hyperopt-搜索" style="width:200px" />
        </el-form-item>
        <el-form-item label="算法">
          <el-select v-model="createForm.algorithm_id" placeholder="请选择可执行算法" style="width:260px" filterable>
            <el-option v-for="algorithm in algorithms" :key="algorithm.id"
              :label="'[' + algorithm.id + '] ' + algorithm.name + ' · ' + algorithm.algorithm_type + sourceAlgorithmHint(algorithm)"
              :value="algorithm.id"
              :disabled="algorithm.execution_kind === 'simulation' || (algorithm.execution_kind === 'source' && (!algorithm.source_contract_ready || !algorithm.searchable_parameters?.length))" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据集">
          <el-select v-model="createForm.dataset_id" placeholder="可选" clearable style="width:200px" filterable>
            <el-option v-for="d in datasets" :key="d.id" :label="`[${d.id}] ${d.name}`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="预测目标">
          <el-select v-model="createForm.target_column" placeholder="请选择目标列" :disabled="!createForm.dataset_id" style="width:200px" filterable>
            <el-option v-for="column in datasetColumns" :key="column" :label="column" :value="column" />
          </el-select>
        </el-form-item>
        <el-form-item label="模型类型">
          <el-select v-model="createForm.model_type" style="width:150px">
            <el-option label="回归" value="regression" />
            <el-option label="分类" value="classification" />
          </el-select>
        </el-form-item>
        <el-form-item label="Worker数">
          <el-input-number v-model="createForm.num_workers" :min="1" :max="8" style="width:120px" />
        </el-form-item>
        <el-form-item label="执行方式">
          <el-tag type="success">参数并行搜索</el-tag>
          <el-tag v-if="selectedAlgorithm?.execution_kind === 'source'" type="warning" effect="plain" style="margin-left:8px">运行上传源码</el-tag>
        </el-form-item>
        <el-form-item label="参数组合（可选）" class="grid-input">
          <el-input v-model="createForm.param_grid_text" type="textarea" :rows="2" style="width:560px"
            :placeholder="paramGridHint" />
        </el-form-item>
        <el-form-item>
          <el-button type="success" @click="createJob" :loading="creating">提交</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 任务列表 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span>训练任务 ({{ jobs.length }})</span>
          <el-button text @click="loadAll">刷新</el-button>
        </div>
      </template>

      <el-table :data="jobs" v-loading="loading" empty-text="暂无任务，创建第一个吧">
        <el-table-column prop="job_id" label="任务ID" width="140">
          <template #default="{ row }"><code>{{ row.job_id?.slice(0, 14) }}</code></template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="parallel_strategy" label="策略" width="100" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="参数进度" width="100">
          <template #default="{ row }">{{ row.metrics?.progress || '-' }}</template>
        </el-table-column>
        <el-table-column label="最佳得分" width="120">
          <template #default="{ row }">
            <span v-if="row.metrics?.best_score" style="color:#67C23A;font-weight:600">
              {{ row.metrics.best_score.toFixed(4) }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button v-if="row.status === 'pending'" size="small" type="success" @click="startJob(row)">启动</el-button>
            <el-button v-if="row.status === 'running'" size="small" type="danger" @click="stopJob(row)">停止</el-button>
            <el-button size="small" @click="viewLogs(row)">日志</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 日志对话框 -->
    <el-dialog v-model="logDialog" title="训练日志" width="700px">
      <div class="log-area">
        <div v-for="(l, i) in currentLogs" :key="i" class="log-line">{{ l }}</div>
        <el-empty v-if="!currentLogs.length" description="暂无日志" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const jobs = ref<any[]>([])
const loading = ref(false)
const creating = ref(false)
const showCreate = ref(false)
const logDialog = ref(false)
const currentLogs = ref<string[]>([])
const datasets = ref<any[]>([])
const algorithms = ref<any[]>([])
const datasetColumns = ref<string[]>([])
const clusterInfo = reactive({ status: 'checking', nodes: 0, cpus: 0, gpus: 0 })

const activeJobs = computed(() => jobs.value.filter(j => j.status === 'running').length)
const selectedAlgorithm = computed(() => algorithms.value.find(item => item.id === createForm.algorithm_id))
function sourceAlgorithmHint(algorithm: any) {
  if (algorithm.execution_kind === 'simulation') return '（控制仿真：请至控制仿真任务运行）'
  if (algorithm.execution_kind !== 'source') return ''
  if (!algorithm.source_contract_ready) return '（未识别训练入口）'
  if (!algorithm.searchable_parameters?.length) return '（需声明可搜索参数）'
  return '（上传源码）'
}

const paramGridHint = computed(() => {
  const selected = selectedAlgorithm.value
  if (!selected) return '可留空：平台将使用该算法的默认参数组合'
  if (selected.execution_kind === 'source') {
    const defaults = selected.parameter_defaults || {}
    return Object.keys(defaults).length
      ? '可留空：根据源码已声明参数自动生成组合；或输入 JSON 数组，例如 [{"learning_rate":0.0005},{"learning_rate":0.001}]'
      : '该源码未声明可搜索参数，请先在算法源码中声明训练常量'
  }
  return '可留空：平台将使用该内置算法的默认参数组合；或输入 JSON 数组覆盖'
})

const createForm = reactive({
  name: '超参搜索',
  algorithm_id: null as number | null,
  dataset_id: null as number | null,
  target_column: '',
  param_grid_text: '',
  model_type: 'regression',
  num_workers: 4,
  parallel_strategy: 'data_parallel',
})

async function loadAll() {
  loading.value = true
  try {
    const [sRes, jRes] = await Promise.all([get('/distributed-training/status'), get('/distributed-training/jobs')])
    Object.assign(clusterInfo, sRes.cluster || {})
    clusterInfo.status = sRes.status || 'unknown'
    jobs.value = jRes.items || []
  } catch { /* ignore */ }
  finally { loading.value = false }
}

async function createJob() {
  if (!createForm.name.trim()) return ElMessage.warning('请填写任务名称')
  if (!createForm.algorithm_id) return ElMessage.warning('请选择算法')
  if (!createForm.dataset_id) return ElMessage.warning('请选择数据集')
  if (!createForm.target_column) return ElMessage.warning('请选择预测目标列')
  let paramGrid: any = undefined
  if (createForm.param_grid_text.trim()) {
    try {
      paramGrid = JSON.parse(createForm.param_grid_text)
      if (!Array.isArray(paramGrid) || !paramGrid.every(item => item && typeof item === 'object' && !Array.isArray(item))) throw new Error()
    } catch {
      return ElMessage.warning('参数组合格式应为 JSON 对象数组')
    }
  }
  creating.value = true
  try {
    const { param_grid_text, ...payload } = createForm
    await post('/distributed-training/jobs', { ...payload, param_grid: paramGrid })
    ElMessage.success('任务已创建')
    showCreate.value = false
    loadAll()
  } catch { ElMessage.error('创建失败') }
  finally { creating.value = false }
}

async function startJob(row: any) {
  try {
    await post(`/distributed-training/jobs/${row.job_id}/start`)
    ElMessage.success('已启动')
    // 定时刷新
    const timer = setInterval(async () => {
      const j = await get(`/distributed-training/jobs/${row.job_id}`)
      const idx = jobs.value.findIndex(x => x.job_id === row.job_id)
      if (idx >= 0) jobs.value[idx] = j
      if (j.status === 'completed' || j.status === 'failed' || j.status === 'stopped') {
        clearInterval(timer)
        ElMessage.info(`训练${j.status === 'completed' ? '完成' : j.status}`)
        loadAll()
      }
    }, 2000)
    setTimeout(() => clearInterval(timer), 120000)
    loadAll()
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '启动失败') }
}

async function stopJob(row: any) {
  try {
    await post(`/distributed-training/jobs/${row.job_id}/stop`)
    ElMessage.success('已停止')
    loadAll()
  } catch { ElMessage.error('停止失败') }
}

async function viewLogs(row: any) {
  try {
    const res = await get(`/distributed-training/jobs/${row.job_id}/logs`)
    currentLogs.value = res.logs || []
  } catch { currentLogs.value = [] }
  logDialog.value = true
}

function statusType(s: string) {
  const m: Record<string, string> = { pending: 'info', running: 'warning', completed: 'success', failed: 'danger', stopped: '' }
  return m[s] || 'info'
}

function formatTime(t: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

watch(() => createForm.dataset_id, async (datasetId) => {
  createForm.target_column = ''
  datasetColumns.value = []
  if (!datasetId) return
  try {
    const response = await get('/data-analysis/datasets/' + datasetId + '/columns')
    datasetColumns.value = response.columns || []
  } catch {
    ElMessage.error('无法读取数据集字段，请确认数据集可用')
  }
})

onMounted(async () => {
  loadAll()
  try {
    const [dRes, aRes] = await Promise.all([
      get('/datasets'),
      get('/distributed-training/algorithms'),
    ])
    datasets.value = dRes.items || dRes || []
    algorithms.value = aRes.items || []
  } catch { /* ignore */ }
})
</script>

<style scoped>
.dt-page { max-width: 1200px; padding: 20px; }
.stats-row { margin-bottom: 16px; }
.stat-card { text-align: center; }
.stat-value { font-size: 28px; font-weight: 700; }
.stat-value.green { color: #67C23A; }
.stat-value.red { color: #F56C6C; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.section-card { margin-bottom: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.search-help { margin-bottom: 12px; line-height: 1.65; }
.search-help ul { margin: 6px 0 0; padding-left: 18px; }
.create-form { padding: 10px 0; }
.grid-input { width: 100%; align-items: flex-start; }
.log-area { max-height: 400px; overflow-y: auto; background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 4px; font-family: monospace; font-size: 13px; }
.log-line { line-height: 1.8; }
</style>
