<template>
  <div class="page-container">
    <PageHeader title="笔记本管理" desc="Jupyter笔记本管理、创建、启动与监控" />

    <!-- Stats -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6">
        <StatCard
          title="笔记本总数"
          :value="stats.totalNotebooks"
          icon="Document"
          icon-color="#818cf8"
          bg-color="rgba(99,102,241,0.12)"
          :loading="loading"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="运行中"
          :value="stats.runningSessions"
          icon="VideoPlay"
          icon-color="#10b981"
          bg-color="rgba(16,185,129,0.12)"
          :loading="loading"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="已停止"
          :value="stats.stoppedCount"
          icon="VideoPause"
          icon-color="#f59e0b"
          bg-color="rgba(245,158,11,0.12)"
          :loading="loading"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="活跃内核"
          :value="stats.activeKernels"
          icon="Cpu"
          icon-color="#ef4444"
          bg-color="rgba(239,68,68,0.12)"
          :loading="loading"
        />
      </el-col>
    </el-row>

    <!-- Tabs -->
    <el-tabs v-model="activeTab" class="main-tabs">
      <!-- Notebook List -->
      <el-tab-pane label="笔记本列表" name="notebooks">
        <DataTable
          title="笔记本"
          :data="filteredNotebooks"
          :loading="loading"
          :error="error"
          empty-text="暂无笔记本"
        >
          <template #header-actions>
            <el-input
              v-model="notebookSearch"
              placeholder="搜索笔记本..."
              style="width: 200px"
              clearable
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-select
              v-model="notebookFilter.status"
              placeholder="状态筛选"
              clearable
              style="width: 120px"
            >
              <el-option label="运行中" value="running" />
              <el-option label="已停止" value="stopped" />
              <el-option label="错误" value="error" />
            </el-select>
            <el-button type="primary" @click="showCreateNotebookDialog">
              <el-icon><Plus /></el-icon> 新建笔记本
            </el-button>
            <el-button @click="refreshAll" :loading="loading">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </template>

          <el-table-column prop="name" label="笔记本名称" min-width="200">
            <template #default="{ row }">
              <div class="notebook-name">
                <el-icon class="notebook-icon"><Document /></el-icon>
                <span>{{ row.name }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="kernel" label="内核" width="120">
            <template #default="{ row }">
              <el-tag size="small" :type="getKernelType(row.kernel)">{{ row.kernel }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <div class="status-cell">
                <span class="status-dot" :class="row.status"></span>
                <span>{{ getStatusLabel(row.status) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="cpu_usage" label="CPU" width="100">
            <template #default="{ row }">
              <el-progress :percentage="row.cpu_usage || 0" :stroke-width="6" :show-text="false" />
              <span class="progress-text">{{ row.cpu_usage || 0 }}%</span>
            </template>
          </el-table-column>
          <el-table-column prop="memory_usage" label="内存" width="100">
            <template #default="{ row }">
              <el-progress :percentage="row.memory_usage || 0" :stroke-width="6" :show-text="false" />
              <span class="progress-text">{{ row.memory_usage || 0 }}%</span>
            </template>
          </el-table-column>
          <el-table-column prop="last_activity" label="最后活动" width="180">
            <template #default="{ row }">
              {{ formatTime(row.last_activity) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" link @click="openNotebook(row)">
                <el-icon><View /></el-icon> 打开
              </el-button>
              <el-button v-if="row.status === 'stopped'" type="success" link @click="startNotebook(row)">
                <el-icon><VideoPlay /></el-icon> 启动
              </el-button>
              <el-button v-else type="warning" link @click="stopNotebook(row)">
                <el-icon><VideoPause /></el-icon> 停止
              </el-button>
              <el-button type="danger" link @click="deleteNotebook(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- Kernel Status -->
      <el-tab-pane label="内核状态" name="kernels">
        <DataTable
          title="内核列表"
          :data="kernels"
          :loading="kernelLoading"
          :error="kernelError"
          empty-text="暂无活跃内核"
        >
          <template #header-actions>
            <el-button @click="loadKernels" :loading="kernelLoading">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </template>

          <el-table-column prop="id" label="内核ID" width="200">
            <template #default="{ row }">
              <code class="kernel-id">{{ row.id?.substring(0, 8) }}</code>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="内核名称" width="150" />
          <el-table-column prop="language" label="语言" width="100">
            <template #default="{ row }">
              <el-tag size="small">{{ row.language }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <div class="status-cell">
                <span class="status-dot" :class="row.status"></span>
                <span>{{ getKernelStatusLabel(row.status) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="connections" label="连接数" width="100" />
          <el-table-column prop="notebook" label="关联笔记本" min-width="180" />
          <el-table-column prop="started_at" label="启动时间" width="180">
            <template #default="{ row }">
              {{ formatTime(row.started_at) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" link @click="restartKernel(row)">
                <el-icon><RefreshRight /></el-icon> 重启
              </el-button>
              <el-button type="warning" link @click="interruptKernel(row)">
                <el-icon><VideoPause /></el-icon> 中断
              </el-button>
              <el-button type="danger" link @click="killKernel(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>
    </el-tabs>

    <!-- Create Notebook Dialog -->
    <el-dialog v-model="createNotebookDialog" title="新建笔记本" width="500px">
      <el-form :model="notebookForm" :rules="notebookRules" ref="notebookFormRef" label-width="100px">
        <el-form-item label="笔记本名称" prop="name">
          <el-input v-model="notebookForm.name" placeholder="请输入笔记本名称" />
        </el-form-item>
        <el-form-item label="内核类型" prop="kernel">
          <el-select v-model="notebookForm.kernel" style="width: 100%">
            <el-option label="Python 3" value="python3" />
            <el-option label="R" value="r" />
            <el-option label="Scala" value="scala" />
          </el-select>
        </el-form-item>
        <el-form-item label="资源规格">
          <el-select v-model="notebookForm.resource_tier" style="width: 100%">
            <el-option label="小型 (1核2G)" value="small" />
            <el-option label="中型 (2核4G)" value="medium" />
            <el-option label="大型 (4核8G)" value="large" />
            <el-option label="GPU (1GPU16G)" value="gpu" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="notebookForm.description" type="textarea" :rows="2" placeholder="可选描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createNotebookDialog = false">取消</el-button>
        <el-button type="primary" @click="createNotebook" :loading="creating">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Refresh, Search, Document, View, VideoPlay, VideoPause, Delete, RefreshRight,
} from '@element-plus/icons-vue'
import { get, post } from '@/api/legacy'
import { useAsyncData } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatCard from '@/components/StatCard.vue'
import DataTable from '@/components/DataTable.vue'

const activeTab = ref('notebooks')
const notebookSearch = ref('')
const notebookFilter = reactive({ status: '', kernel: '' })
const createNotebookDialog = ref(false)
const creating = ref(false)

const notebookForm = reactive({
  name: '',
  kernel: 'python3',
  resource_tier: 'medium',
  description: '',
})
const notebookFormRef = ref()
const kernels = ref<any[]>([])

// Main notebooks data
const { data: notebookData, loading, error, refresh: refreshAll } = useAsyncData<any>(() => get('/notebooks'))

const notebooks = computed(() => {
  const items = notebookData.value?.items || notebookData.value || []
  return Array.isArray(items) ? items : []
})

const stats = computed(() => ({
  totalNotebooks: notebooks.value.length,
  runningSessions: notebooks.value.filter(n => n.status === 'running').length,
  stoppedCount: notebooks.value.filter(n => n.status === 'stopped').length,
  activeKernels: 0,
}))

// Kernels
const { loading: kernelLoading, error: kernelError, refresh: loadKernels } = useAsyncData(async () => {
  const res = await get<any>('/notebooks/kernels').catch(() => ({ items: [] }))
  kernels.value = res.items || res || []
  stats.value.activeKernels = kernels.value.filter((k: any) => k.status === 'running' || k.status === 'busy').length
  return res
})

const filteredNotebooks = computed(() => {
  return notebooks.value.filter((n: any) => {
    const matchSearch = !notebookSearch.value || n.name?.toLowerCase().includes(notebookSearch.value.toLowerCase())
    const matchStatus = !notebookFilter.status || n.status === notebookFilter.status
    const matchKernel = !notebookFilter.kernel || n.kernel === notebookFilter.kernel
    return matchSearch && matchStatus && matchKernel
  })
})

// Helpers
function getKernelType(kernel: string) {
  const map: Record<string, string> = { python3: 'success', python2: 'info', r: 'warning', scala: 'primary' }
  return map[kernel] || ''
}

function getStatusLabel(status: string) {
  const map: Record<string, string> = { running: '运行中', stopped: '已停止', error: '错误', idle: '空闲' }
  return map[status] || status
}

function getKernelStatusLabel(status: string) {
  const map: Record<string, string> = { running: '运行中', idle: '空闲', busy: '忙碌', dead: '已终止' }
  return map[status] || status
}

function formatTime(timeStr: string) {
  if (!timeStr) return '-'
  return new Date(timeStr).toLocaleString('zh-CN')
}

// Notebook actions
function showCreateNotebookDialog() {
  Object.assign(notebookForm, { name: '', kernel: 'python3', resource_tier: 'medium', description: '' })
  createNotebookDialog.value = true
}

async function createNotebook() {
  if (!notebookForm.name) { ElMessage.warning('请输入笔记本名称'); return }
  creating.value = true
  try {
    await post('/notebooks', { name: notebookForm.name })
    ElMessage.success('笔记本创建成功')
    createNotebookDialog.value = false
    refreshAll()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e?.message || e))
  } finally {
    creating.value = false
  }
}

function openNotebook(row: any) {
  window.open(`/notebook/${row.id}`, '_blank')
}

async function startNotebook(row: any) {
  try {
    await post(`/notebooks/${row.id}/start`)
    ElMessage.success('笔记本启动中...')
    refreshAll()
  } catch (e: any) {
    ElMessage.error('启动失败: ' + (e?.message || e))
  }
}

async function stopNotebook(row: any) {
  try {
    await ElMessageBox.confirm('确定停止该笔记本吗？未保存的更改可能丢失。', '确认停止', { type: 'warning' })
    await post(`/notebooks/${row.id}/stop`)
    ElMessage.success('笔记本已停止')
    refreshAll()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('停止失败: ' + (e?.message || e))
  }
}

async function deleteNotebook(row: any) {
  try {
    await ElMessageBox.confirm('确定删除该笔记本吗？此操作不可恢复。', '确认删除', { type: 'warning' })
    await post(`/notebooks/${row.id}/delete`)
    ElMessage.success('笔记本已删除')
    refreshAll()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('删除失败: ' + (e?.message || e))
  }
}

// Kernel actions
async function restartKernel(row: any) {
  try {
    await ElMessageBox.confirm('确定重启该内核吗？所有变量状态将丢失。', '确认重启', { type: 'warning' })
    await get('/notebooks/jupyter/status')
    ElMessage.success('内核重启中...')
    loadKernels()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('重启失败: ' + (e?.message || e))
  }
}

async function interruptKernel(row: any) {
  try {
    await get('/notebooks/jupyter/status')
    ElMessage.success('内核已中断')
  } catch (e: any) {
    ElMessage.error('中断失败: ' + (e?.message || e))
  }
}

async function killKernel(row: any) {
  try {
    await ElMessageBox.confirm('确定终止该内核吗？', '确认终止', { type: 'warning' })
    await get('/notebooks/jupyter/status')
    ElMessage.success('内核已终止')
    loadKernels()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('终止失败: ' + (e?.message || e))
  }
}
</script>

<style scoped>
.page-container {
  padding: 20px;
}

.stats-row {
  margin-bottom: 24px;
}

.main-tabs :deep(.el-tabs__header) {
  margin-bottom: 20px;
}

.notebook-name {
  display: flex;
  align-items: center;
  gap: 8px;
}
.notebook-icon {
  color: var(--el-color-primary-light-3);
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.running { background: #10b981; }
.status-dot.stopped { background: #64748b; }
.status-dot.error, .status-dot.dead { background: #ef4444; }
.status-dot.idle { background: #f59e0b; }
.status-dot.busy { background: #3b82f6; }

.progress-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-left: 4px;
}

.kernel-id {
  font-family: monospace;
  font-size: 12px;
  color: var(--el-color-primary-light-1);
}
</style>
