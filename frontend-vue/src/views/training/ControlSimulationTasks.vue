<template>
  <div class="control-simulation-page">
    <div class="page-header">
      <h1 class="page-title"><el-icon><DataAnalysis /></el-icon> 控制仿真任务</h1>
      <p class="page-desc">集中查看 FOPDT、MPC 等控制算法的辨识、历史仿真与结果文件。</p>
    </div>

    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :sm="6"><el-card><div class="stat-value">{{ runs.length }}</div><div class="stat-label">全部任务</div></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card><div class="stat-value success">{{ successfulRuns }}</div><div class="stat-label">已完成</div></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card><div class="stat-value warning">{{ runningRuns }}</div><div class="stat-label">运行中</div></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card><div class="stat-value">{{ algorithms.length }}</div><div class="stat-label">涉及算法</div></el-card></el-col>
    </el-row>

    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="algorithmFilter" filterable style="width:260px">
          <el-option label="全部算法" value="all" />
          <el-option v-for="item in algorithms" :key="item.id" :label="`#${item.id} ${item.name}`" :value="String(item.id)" />
        </el-select>
        <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width:140px">
          <el-option label="运行中" value="running" />
          <el-option label="已完成" value="success" />
          <el-option label="失败" value="failed" />
        </el-select>
        <el-button @click="loadRuns"><el-icon><Refresh /></el-icon> 刷新</el-button>
      </div>

      <div v-if="visibleAlgorithmGroups.length" class="algorithm-folders" v-loading="loading">
        <el-card v-for="group in visibleAlgorithmGroups" :key="String(group.id)" shadow="never" class="algorithm-folder">
          <template #header>
            <div class="folder-header">
              <span class="folder-title"><el-icon><FolderOpened /></el-icon>{{ group.label }}</span>
              <el-tag size="small" type="info">{{ group.runs.length }} 条仿真任务</el-tag>
            </div>
          </template>
          <el-table :data="group.runs" border stripe style="width:100%">
            <el-table-column prop="name" label="仿真名称" min-width="180" />
            <el-table-column label="数据集" min-width="160"><template #default="{ row }">#{{ row.dataset_id }} {{ row.dataset_name }}</template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="耗时" width="105"><template #default="{ row }">{{ row.duration_ms ? `${(Number(row.duration_ms) / 1000).toFixed(1)} 秒` : '-' }}</template></el-table-column>
            <el-table-column label="创建时间" width="170"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
            <el-table-column label="操作" width="170" fixed="right">
              <template #default="{ row }">
                <el-button text type="primary" @click="openDetail(row)">查看结果</el-button>
                <el-button text @click="goToAlgorithm(row)" :disabled="!row.algorithm_id">算法</el-button>
                <el-button text type="danger" @click="deleteRun(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
      <el-empty v-else-if="!loading" description="暂无符合条件的控制仿真任务；请从算法商店或代码编辑器发起控制仿真。" :image-size="80" />
    </el-card>

    <el-dialog v-model="showDetail" :title="selectedRun ? `${selectedRun.name} · 仿真结果` : '仿真结果'" width="960px" destroy-on-close>
      <el-descriptions v-if="selectedRun" :column="3" border>
        <el-descriptions-item label="控制算法">{{ algorithmLabel(selectedRun) }}</el-descriptions-item>
        <el-descriptions-item label="数据集">#{{ selectedRun.dataset_id }} {{ selectedRun.dataset_name }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ statusLabel(selectedRun.status) }}</el-descriptions-item>
        <el-descriptions-item label="时间列">{{ selectedRun.config?.time_column || '-' }}</el-descriptions-item>
        <el-descriptions-item label="控制变量">{{ (selectedRun.config?.control_columns || []).join('、') || '-' }}</el-descriptions-item>
        <el-descriptions-item label="被控变量">{{ (selectedRun.config?.state_columns || []).join('、') || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-divider>结果文件</el-divider>
      <div v-if="selectedRun?.outputs?.length" class="files">
        <el-button v-for="(output, index) in selectedRun.outputs" :key="output.path" type="primary" plain @click="downloadOutput(selectedRun, index, output)">
          <el-icon><Download /></el-icon>{{ output.name }}
        </el-button>
      </div>
      <el-empty v-else description="该次任务未生成结果文件" :image-size="80" />
      <el-divider>JSON 结果摘要</el-divider>
      <pre class="result-box">{{ JSON.stringify(selectedRun?.json_results || {}, null, 2) }}</pre>
      <el-divider>算法运行日志</el-divider>
      <pre class="result-box">{{ selectedRun?.runtime_log_tail || selectedRun?.stdout_tail || '暂无运行日志' }}</pre>
      <el-button plain :loading="loadingRuntimeLog" style="margin-top:10px" @click="loadFullRuntimeLog(selectedRun)">查看完整运行日志</el-button>
      <pre v-if="fullRuntimeLog" class="result-box" style="margin-top:10px">{{ fullRuntimeLog }}</pre>
      <pre v-if="selectedRun?.status === 'failed' && selectedRun?.stderr_tail" class="result-box error">{{ selectedRun.stderr_tail }}</pre>
      <el-button v-if="selectedRun?.status === 'failed'" type="danger" plain :loading="loadingFullError" style="margin-top:10px" @click="loadFullError(selectedRun)">查看完整报错</el-button>
      <pre v-if="fullErrorLog" class="result-box error" style="margin-top:10px">{{ fullErrorLog }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DataAnalysis, Download, Refresh, FolderOpened } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import { api } from '@/api'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const runs = ref<any[]>([])
const algorithmFilter = ref(String(route.query.algorithm_id || 'all'))
const statusFilter = ref('')
const showDetail = ref(false)
const selectedRun = ref<any>(null)
const fullErrorLog = ref('')
const loadingFullError = ref(false)
const fullRuntimeLog = ref('')
const loadingRuntimeLog = ref(false)

const algorithmGroups = computed(() => {
  const map = new Map<string, { id: number, name: string, runs: any[] }>()
  for (const run of runs.value) {
    const id = Number(run.algorithm_id) || 0
    if (!map.has(String(id))) map.set(String(id), { id, name: run.algorithm_name || '', runs: [] })
    map.get(String(id))!.runs.push(run)
  }
  return Array.from(map.values()).map(group => ({
    ...group,
    label: group.id > 0 ? `#${group.id} ${group.name || '未命名算法'}` : '来源未关联（历史记录）',
  }))
})
const algorithms = computed(() => algorithmGroups.value
  .filter(group => group.id > 0)
  .map(group => ({ id: group.id, name: group.name }))
  .sort((a, b) => a.id - b.id)
)
const visibleAlgorithmGroups = computed(() => algorithmGroups.value
  .filter(group => algorithmFilter.value === 'all' || String(group.id) === algorithmFilter.value)
  .map(group => ({ ...group, runs: group.runs.filter(run => !statusFilter.value || run.status === statusFilter.value) }))
  .filter(group => group.runs.length > 0)
)

const successfulRuns = computed(() => runs.value.filter(run => run.status === 'success').length)
const runningRuns = computed(() => runs.value.filter(run => run.status === 'running').length)

function formatDate(value: string) { return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-' }
function algorithmLabel(run: any) {
  return Number(run?.algorithm_id) > 0
    ? `#${run.algorithm_id} ${run.algorithm_name || '未命名算法'}`
    : '来源未关联（历史记录）'
}
function statusLabel(status: string) { return status === 'success' ? '已完成' : status === 'running' ? '运行中' : '失败' }
function statusType(status: string) { return status === 'success' ? 'success' : status === 'running' ? 'warning' : 'danger' }

async function loadRuns() {
  loading.value = true
  try {
    const result = await api.algorithmStore.getAllControlRuns()
    runs.value = result?.items || []
  } catch (error: any) {
    ElMessage.error('加载控制仿真任务失败：' + (error?.message || '未知错误'))
  } finally { loading.value = false }
}
function openDetail(run: any) { selectedRun.value = run; fullErrorLog.value = ''; fullRuntimeLog.value = ''; showDetail.value = true }
async function loadFullRuntimeLog(run: any) {
  if (!run) return
  loadingRuntimeLog.value = true
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token') || ''
  try {
    const response = await fetch(`/api/v1/algorithm-store/models/${run.algorithm_id}/control-runs/${encodeURIComponent(run.id)}/logs/runtime`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!response.ok) throw new Error(`读取完整运行日志失败（HTTP ${response.status}）`)
    fullRuntimeLog.value = await response.text() || '该任务未输出运行日志。'
  } catch (error: any) { ElMessage.error(error?.message || '读取完整运行日志失败') }
  finally { loadingRuntimeLog.value = false }
}
async function loadFullError(run: any) {
  loadingFullError.value = true
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token') || ''
  try {
    const response = await fetch(`/api/v1/algorithm-store/models/${run.algorithm_id}/control-runs/${encodeURIComponent(run.id)}/logs/stderr`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!response.ok) throw new Error(`读取完整报错失败（HTTP ${response.status}）`)
    fullErrorLog.value = await response.text() || '该任务未输出 stderr 错误日志。'
  } catch (error: any) { ElMessage.error(error?.message || '读取完整报错失败') }
  finally { loadingFullError.value = false }
}
function goToAlgorithm(run: any) { router.push({ path: '/model/algorithm-store', query: { algorithm_id: String(run.algorithm_id) } }) }
async function deleteRun(run: any) {
  try {
    await ElMessageBox.confirm(`确定删除“${run.name}”吗？该操作会同时删除本次仿真的 JSON、图表和日志文件。`, '删除控制仿真任务', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
    await api.algorithmStore.deleteControlRun(run.id)
    if (selectedRun.value?.id === run.id) { showDetail.value = false; selectedRun.value = null }
    ElMessage.success('控制仿真任务已删除')
    await loadRuns()
  } catch (error: any) {
    if (error === 'cancel' || error?.message === 'cancel') return
    ElMessage.error('删除失败：' + (error?.message || '未知错误'))
  }
}
async function downloadOutput(run: any, index: number, output: any) {
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token') || ''
  try {
    const response = await fetch(`/api/v1/algorithm-store/models/${run.algorithm_id}/control-runs/${encodeURIComponent(run.id)}/outputs/${index}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!response.ok) throw new Error(`下载失败（HTTP ${response.status}）`)
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = output?.name || 'control-output'; document.body.appendChild(anchor); anchor.click(); anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error: any) { ElMessage.error(error?.message || '下载结果文件失败') }
}
watch(() => route.query.algorithm_id, value => { algorithmFilter.value = String(value || 'all') })
onMounted(loadRuns)
</script>

<style scoped lang="scss">
.control-simulation-page { padding: 20px; }
.page-header { margin-bottom: 20px; }.page-title { display:flex; align-items:center; gap:8px; margin:0; font-size:24px; }.page-desc { color:#64748b; margin:8px 0 0; }
.stats-row { margin-bottom:16px; }.stat-value { font-size:28px; font-weight:700; color:#334155; }.stat-value.success { color:#16a34a; }.stat-value.warning { color:#d97706; }.stat-label { color:#64748b; margin-top:4px; }
.toolbar { display:flex; gap:12px; margin-bottom:16px; flex-wrap:wrap; }.algorithm-folders { display:flex; flex-direction:column; gap:14px; }.algorithm-folder { border:1px solid #dbeafe; }.folder-header { display:flex; justify-content:space-between; align-items:center; gap:12px; }.folder-title { display:flex; align-items:center; gap:7px; font-weight:600; color:#1e3a5f; }.files { display:flex; gap:10px; flex-wrap:wrap; }.result-box { max-height:300px; overflow:auto; white-space:pre-wrap; word-break:break-word; background:#f8fafc; border:1px solid #e2e8f0; border-radius:4px; padding:12px; font-size:12px; line-height:1.55; }.result-box.error { color:#b42318; background:#fff6f5; }
</style>
