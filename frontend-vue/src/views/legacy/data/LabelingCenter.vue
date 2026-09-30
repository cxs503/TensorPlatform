<template>
  <div class="labeling-page">
    <PageHeader title="数据标注" desc="为表格和时序数据逐行补充人工标签；审核通过后可导出为新的已标注数据集。" />

    <el-alert
      title="平台内标注适用于表格与时序数据。标签不会覆盖原数据，导出时会生成带新增标签列的新数据集。"
      type="info" :closable="false" show-icon class="page-alert"
    />

    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :md="6"><el-card><el-statistic title="标注总数" :value="stats.total_annotations || 0" /></el-card></el-col>
      <el-col :xs="12" :md="6"><el-card><el-statistic title="待审核" :value="stats.pending_review || 0" /></el-card></el-col>
      <el-col :xs="12" :md="6"><el-card><el-statistic title="审核通过" :value="stats.approved || 0" /></el-card></el-col>
      <el-col :xs="12" :md="6"><el-card><el-statistic title="通过率" :value="stats.approval_rate || 0" suffix="%" /></el-card></el-col>
    </el-row>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="标注任务" name="tasks">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>任务列表</span>
              <div>
                <el-button @click="reloadAll">刷新</el-button>
                <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" @click="openTaskDialog">新建标注任务</el-button>
              </div>
            </div>
          </template>
          <el-empty v-if="!tasks.length && !loading" description="还没有标注任务。请先创建标签组和标签，再新建任务。" :image-size="72" />
          <el-table v-else :data="tasks" stripe v-loading="loading">
            <el-table-column prop="name" label="任务名称" min-width="160" />
            <el-table-column label="数据集" min-width="150">
              <template #default="{ row }">#{{ row.dataset_id }} {{ datasetName(row.dataset_id) }}</template>
            </el-table-column>
            <el-table-column label="标签组" min-width="130">
              <template #default="{ row }">{{ groupName(row.label_group_id) }}</template>
            </el-table-column>
            <el-table-column label="进度" width="180">
              <template #default="{ row }"><el-progress :percentage="taskProgress(row)" :text-inside="true" :stroke-width="16" /></template>
            </el-table-column>
            <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="taskStatusType(row.status)">{{ taskStatusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="170"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
            <el-table-column label="操作" width="190" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openWork(row)">开始标注</el-button>
                <el-button link type="success" @click="openExport(row)">导出结果</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="在线标注" name="work">
        <el-card>
          <template #header><div class="card-header"><span>逐行标注</span><el-button @click="loadTaskItems()">刷新当前页</el-button></div></template>
          <el-form inline class="work-toolbar">
            <el-form-item label="标注任务">
              <el-select v-model="selectedTaskId" filterable placeholder="请选择任务" style="width:320px" @change="loadTaskItems(true)">
                <el-option v-for="task in tasks" :key="task.id" :label="task.name + '（#' + task.dataset_id + '）'" :value="task.id" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="selectedTask" label="标签组"><el-tag>{{ groupName(selectedTask.label_group_id) }}</el-tag></el-form-item>
            <el-form-item v-if="selectedTask && !activeTaskLabels.length"><el-alert title="该任务的标签组尚未添加标签，请先到“标签定义”添加。" type="warning" :closable="false" /></el-form-item>
          </el-form>
          <el-empty v-if="!selectedTaskId" description="请从上方选择一个标注任务" :image-size="72" />
          <template v-else>
            <el-skeleton v-if="itemLoading" :rows="7" animated />
            <el-empty v-else-if="!taskItems.length" description="当前页没有数据行" :image-size="72" />
            <el-table v-else :data="taskItems" stripe border max-height="560" style="width:100%">
              <el-table-column prop="row_number" label="行号" width="72" fixed />
              <el-table-column v-for="column in taskColumns" :key="column" :label="column" min-width="140" show-overflow-tooltip>
                <template #default="{ row }">{{ formatValue(row.values?.[column]) }}</template>
              </el-table-column>
              <el-table-column label="人工标签" min-width="230" fixed="right">
                <template #default="{ row }">
                  <el-checkbox-group v-model="labelDraft[row.data_id]" class="row-labels">
                    <el-checkbox v-for="label in activeTaskLabels" :key="label.id" :label="label.id">{{ label.name }}</el-checkbox>
                  </el-checkbox-group>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="100" fixed="right">
                <template #default="{ row }"><el-tag size="small" :type="annotationStatusType(row.annotation?.status)">{{ annotationStatusLabel(row.annotation?.status) }}</el-tag></template>
              </el-table-column>
              <el-table-column label="操作" width="84" fixed="right">
                <template #default="{ row }"><el-button type="primary" link :loading="savingRow === row.data_id" :disabled="!activeTaskLabels.length" @click="saveRowAnnotation(row)">保存</el-button></template>
              </el-table-column>
            </el-table>
            <div class="pager"><el-pagination v-model:current-page="itemPage" :page-size="itemPageSize" layout="total, prev, pager, next" :total="itemTotal" @current-change="loadTaskItems()" /></div>
          </template>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="审核队列" name="review">
        <el-card>
          <template #header><div class="card-header"><span>待审核标注</span><el-button @click="fetchAnnotations">刷新</el-button></div></template>
          <el-empty v-if="!annotations.length" description="暂无已提交的标注" :image-size="72" />
          <el-table v-else :data="annotations" stripe>
            <el-table-column label="任务" min-width="150"><template #default="{ row }">{{ taskName(row.task_id) }}</template></el-table-column>
            <el-table-column prop="data_id" label="数据行" width="90"><template #default="{ row }">第 {{ Number(row.data_id) + 1 }} 行</template></el-table-column>
            <el-table-column label="标签" min-width="180"><template #default="{ row }">{{ annotationLabels(row) || '—' }}</template></el-table-column>
            <el-table-column prop="annotator_id" label="标注人" width="110" />
            <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="annotationStatusType(row.status)">{{ annotationStatusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="提交时间" width="170"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <template v-if="row.status === 'submitted'">
                  <el-button type="success" link @click="reviewAnnotation(row, true)">通过</el-button>
                  <el-button type="danger" link @click="reviewAnnotation(row, false)">驳回</el-button>
                </template>
                <span v-else>已处理</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="标签定义" name="labels">
        <el-row :gutter="16">
          <el-col :xs="24" :md="9">
            <el-card>
              <template #header><div class="card-header"><span>标签组</span><el-button type="primary" size="small" @click="showGroupDialog = true">新建标签组</el-button></div></template>
              <el-empty v-if="!groups.length" description="先建立一个标签组，例如“质量等级”或“异常类型”。" :image-size="60" />
              <el-table v-else :data="groups" size="small">
                <el-table-column prop="name" label="名称" min-width="130" />
                <el-table-column label="标签数" width="80"><template #default="{ row }">{{ row.labels?.length || 0 }}</template></el-table-column>
                <el-table-column prop="description" label="说明" min-width="130" show-overflow-tooltip />
              </el-table>
            </el-card>
          </el-col>
          <el-col :xs="24" :md="15">
            <el-card>
              <template #header><div class="card-header"><span>标签</span><el-button type="primary" size="small" :disabled="!groups.length" @click="openLabelDialog">新建标签</el-button></div></template>
              <el-empty v-if="!labels.length" description="标签是标注时可勾选的分类值。" :image-size="60" />
              <el-table v-else :data="labels" stripe size="small">
                <el-table-column prop="name" label="标签名" width="130" />
                <el-table-column label="颜色" width="85"><template #default="{ row }"><span class="color-dot" :style="{ backgroundColor: row.color }"></span>{{ row.color }}</template></el-table-column>
                <el-table-column label="所属标签组" min-width="120"><template #default="{ row }">{{ labelGroupName(row.id) }}</template></el-table-column>
                <el-table-column prop="description" label="说明" min-width="160" show-overflow-tooltip />
                <el-table-column label="操作" width="70"><template #default="{ row }"><el-button type="danger" link @click="deleteLabel(row)">删除</el-button></template></el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <el-tab-pane label="协同标注" name="labelstudio">
        <el-card>
          <el-alert :title="labelStudioStatus.up ? 'Label Studio 已就绪。该入口适合图像、文本等复杂标注；表格/时序数据建议使用平台内标注。' : 'Label Studio 当前未就绪，不影响平台内表格标注。'" :type="labelStudioStatus.up ? 'success' : 'warning'" :closable="false" show-icon />
          <el-button style="margin-top:14px" :disabled="!labelStudioStatus.up" @click="showLabelStudio = true">打开 Label Studio</el-button>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="showTaskDialog" title="新建标注任务" width="520px" destroy-on-close>
      <el-form :model="taskForm" label-width="100px">
        <el-form-item label="任务名称" required><el-input v-model="taskForm.name" placeholder="例如：炉温异常标注" /></el-form-item>
        <el-form-item label="数据集" required><el-select v-model="taskForm.dataset_id" filterable style="width:100%"><el-option v-for="dataset in datasets" :key="dataset.id" :label="'#' + dataset.id + ' ' + dataset.name + '（' + dataset.row_count + '行）'" :value="dataset.id" /></el-select></el-form-item>
        <el-form-item label="标签组" required><el-select v-model="taskForm.label_group_id" style="width:100%"><el-option v-for="group in groups" :key="group.id" :label="group.name + '（' + (group.labels?.length || 0) + '个标签）'" :value="group.id" /></el-select></el-form-item>
        <el-form-item label="标注类型"><el-input model-value="分类标注" disabled /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showTaskDialog = false">取消</el-button><el-button type="primary" :loading="savingTask" @click="createTask">创建并开始标注</el-button></template>
    </el-dialog>

    <el-dialog v-model="showLabelDialog" title="新建标签" width="450px" destroy-on-close>
      <el-form :model="labelForm" label-width="84px">
        <el-form-item label="标签名" required><el-input v-model="labelForm.name" /></el-form-item>
        <el-form-item label="标签组" required><el-select v-model="labelForm.group_id" style="width:100%"><el-option v-for="group in groups" :key="group.id" :label="group.name" :value="group.id" /></el-select></el-form-item>
        <el-form-item label="颜色"><el-color-picker v-model="labelForm.color" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="labelForm.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showLabelDialog = false">取消</el-button><el-button type="primary" @click="saveLabel">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="showGroupDialog" title="新建标签组" width="420px" destroy-on-close>
      <el-form :model="groupForm" label-width="84px">
        <el-form-item label="组名" required><el-input v-model="groupForm.name" placeholder="例如：质量等级" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="groupForm.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showGroupDialog = false">取消</el-button><el-button type="primary" @click="saveGroup">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="showExportDialog" title="导出已标注数据集" width="500px" destroy-on-close>
      <el-alert title="只会写入审核通过的标注；原数据集不会被修改。" type="info" :closable="false" style="margin-bottom:14px" />
      <el-form :model="exportForm" label-width="110px">
        <el-form-item label="新数据集名称" required><el-input v-model="exportForm.name" /></el-form-item>
        <el-form-item label="标签列名" required><el-input v-model="exportForm.output_column" placeholder="例如 label" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showExportDialog = false">取消</el-button><el-button type="primary" :loading="exporting" @click="exportTask">导出至数据集管理</el-button></template>
    </el-dialog>

    <el-dialog v-model="showLabelStudio" title="Label Studio 标注工具" width="90%" top="3vh" :close-on-click-modal="false">
      <iframe :src="labelStudioUrl" style="width:100%;height:75vh;border:none;border-radius:8px" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, del } from '@/api/legacy'
import { useUserStore } from '@/stores/user'
import PageHeader from '@/components/PageHeader.vue'

const userStore = useUserStore()
const activeTab = ref('tasks')
const loading = ref(false)
const itemLoading = ref(false)
const savingTask = ref(false)
const savingRow = ref('')
const exporting = ref(false)
const showTaskDialog = ref(false)
const showLabelDialog = ref(false)
const showGroupDialog = ref(false)
const showExportDialog = ref(false)
const showLabelStudio = ref(false)
const tasks = ref<any[]>([])
const datasets = ref<any[]>([])
const groups = ref<any[]>([])
const labels = ref<any[]>([])
const annotations = ref<any[]>([])
const stats = ref<any>({})
const taskItems = ref<any[]>([])
const taskColumns = ref<string[]>([])
const itemTotal = ref(0)
const itemPage = ref(1)
const itemPageSize = 10
const selectedTaskId = ref('')
const labelDraft = ref<Record<string, string[]>>({})
const labelStudioStatus = ref<any>({ up: false })
const labelStudioUrl = 'http://' + window.location.hostname + ':18081'
const taskForm = ref({ name: '', dataset_id: null as number | null, label_group_id: '', annotation_type: 'classification' })
const labelForm = ref({ name: '', group_id: '', color: '#409EFF', description: '' })
const groupForm = ref({ name: '', description: '' })
const exportTaskId = ref('')
const exportForm = ref({ name: '', output_column: 'label' })

const selectedTask = computed(() => tasks.value.find(item => item.id === selectedTaskId.value) || null)
const activeTaskLabels = computed<any[]>(() => groups.value.find(group => group.id === selectedTask.value?.label_group_id)?.labels || [])

const formatDate = (value: any) => value ? String(value).replace('T', ' ').slice(0, 19) : '—'
const formatValue = (value: any) => value === null || value === undefined || value === '' ? '—' : (typeof value === 'number' ? Number(value.toFixed(6)) : String(value))
const datasetName = (id: any) => datasets.value.find(item => String(item.id) === String(id))?.name || '数据集'
const taskName = (id: string) => tasks.value.find(item => item.id === id)?.name || id
const groupName = (id: string) => groups.value.find(item => item.id === id)?.name || '—'
const labelGroupName = (labelId: string) => groups.value.filter(group => (group.labels || []).some((label: any) => label.id === labelId)).map(group => group.name).join('、') || '—'
const taskProgress = (task: any) => task.total_items ? Math.min(100, Math.round((Number(task.completed_items || 0) / Number(task.total_items)) * 100)) : 0
const taskStatusType = (status: string) => status === 'approved' ? 'success' : status === 'submitted' ? 'warning' : status === 'in_progress' ? 'primary' : 'info'
const taskStatusLabel = (status: string) => ({ pending: '待开始', in_progress: '进行中', submitted: '待审核', approved: '已完成', rejected: '已驳回' } as any)[status] || status || '待开始'
const annotationStatusType = (status?: string) => status === 'approved' ? 'success' : status === 'rejected' ? 'danger' : status === 'submitted' ? 'warning' : 'info'
const annotationStatusLabel = (status?: string) => ({ approved: '已通过', rejected: '已驳回', submitted: '待审核' } as any)[status || ''] || '未标注'
const annotationLabels = (annotation: any) => (annotation?.labels || []).map((item: any) => item?.label_name || labels.value.find(label => label.id === item?.label_id)?.name || item?.label_id || item).filter(Boolean).join('、')

async function fetchDatasets() {
  try { const data = await get<any>('/labeling/datasets'); datasets.value = data.items || [] } catch (error: any) { ElMessage.error(error?.message || '加载数据集失败') }
}
async function fetchTasks() {
  loading.value = true
  try { const data = await get<any>('/labeling/tasks'); tasks.value = Array.isArray(data) ? data : (data.items || []) } catch (error: any) { ElMessage.error(error?.message || '加载标注任务失败') } finally { loading.value = false }
}
async function fetchGroups() { try { const data = await get<any>('/labeling/groups'); groups.value = Array.isArray(data) ? data : (data.items || []) } catch {} }
async function fetchLabels() { try { const data = await get<any>('/labeling/labels'); labels.value = Array.isArray(data) ? data : (data.items || []) } catch {} }
async function fetchAnnotations() { try { const data = await get<any>('/labeling/annotations'); annotations.value = Array.isArray(data) ? data : (data.items || []) } catch (error: any) { ElMessage.error(error?.message || '加载审核队列失败') } }
async function fetchStats() { try { stats.value = await get<any>('/labeling/stats') || {} } catch {} }
async function fetchLabelStudioStatus() { try { labelStudioStatus.value = await get<any>('/labeling/labelstudio') || { up: false } } catch { labelStudioStatus.value = { up: false } } }
async function reloadAll() { await Promise.all([fetchDatasets(), fetchGroups(), fetchLabels(), fetchTasks(), fetchAnnotations(), fetchStats(), fetchLabelStudioStatus()]) }

function openTaskDialog() {
  if (!groups.value.length || !labels.value.length) { ElMessage.warning('请先建立标签组，并至少添加一个标签'); activeTab.value = 'labels'; return }
  taskForm.value = { name: '', dataset_id: datasets.value[0]?.id || null, label_group_id: groups.value[0]?.id || '', annotation_type: 'classification' }
  showTaskDialog.value = true
}
async function createTask() {
  if (!taskForm.value.name.trim() || !taskForm.value.dataset_id || !taskForm.value.label_group_id) { ElMessage.warning('请完整填写任务名称、数据集和标签组'); return }
  if (!(groups.value.find(group => group.id === taskForm.value.label_group_id)?.labels || []).length) { ElMessage.warning('所选标签组没有标签，请先添加标签'); return }
  savingTask.value = true
  try {
    const task = await post<any>('/labeling/tasks', taskForm.value)
    ElMessage.success('标注任务已创建')
    showTaskDialog.value = false
    await fetchTasks()
    openWork(task)
  } catch (error: any) { ElMessage.error(error?.message || '创建任务失败') } finally { savingTask.value = false }
}
function openWork(task: any) {
  selectedTaskId.value = task.id
  activeTab.value = 'work'
  loadTaskItems(true)
}
async function loadTaskItems(reset = false) {
  if (!selectedTaskId.value) return
  if (reset) itemPage.value = 1
  itemLoading.value = true
  try {
    const offset = (itemPage.value - 1) * itemPageSize
    const data = await get<any>('/labeling/tasks/' + selectedTaskId.value + '/items?offset=' + offset + '&limit=' + itemPageSize)
    taskItems.value = data.items || []
    taskColumns.value = data.columns || []
    itemTotal.value = Number(data.total || 0)
    const draft: Record<string, string[]> = { ...labelDraft.value }
    taskItems.value.forEach((item: any) => { draft[item.data_id] = (item.annotation?.labels || []).map((label: any) => label?.label_id || label).filter(Boolean) })
    labelDraft.value = draft
  } catch (error: any) { ElMessage.error(error?.message || '读取待标注数据失败'); taskItems.value = [] } finally { itemLoading.value = false }
}
async function saveRowAnnotation(row: any) {
  const ids = labelDraft.value[row.data_id] || []
  if (!ids.length) { ElMessage.warning('请至少选择一个标签'); return }
  savingRow.value = row.data_id
  try {
    const available = activeTaskLabels.value
    await post('/labeling/annotations', {
      task_id: selectedTaskId.value,
      data_id: row.data_id,
      labels: ids.map(id => ({ label_id: id, label_name: available.find(label => label.id === id)?.name || id })),
      metadata: { row_number: row.row_number },
    })
    ElMessage.success('该行标注已保存，等待审核')
    await Promise.all([loadTaskItems(), fetchTasks(), fetchAnnotations(), fetchStats()])
  } catch (error: any) { ElMessage.error(error?.message || '保存标注失败') } finally { savingRow.value = '' }
}
async function reviewAnnotation(annotation: any, approved: boolean) {
  try {
    await post('/labeling/annotations/' + annotation.id + '/review', { approved })
    ElMessage.success(approved ? '已审核通过' : '已驳回')
    await Promise.all([fetchAnnotations(), fetchStats()])
  } catch (error: any) { ElMessage.error(error?.message || '审核失败') }
}
function openExport(task: any) {
  exportTaskId.value = task.id
  exportForm.value = { name: task.name + '(已标注)', output_column: 'label' }
  showExportDialog.value = true
}
async function exportTask() {
  if (!exportForm.value.name.trim() || !exportForm.value.output_column.trim()) { ElMessage.warning('请填写新数据集名称和标签列名'); return }
  exporting.value = true
  try {
    const result = await post<any>('/labeling/tasks/' + exportTaskId.value + '/export', exportForm.value)
    ElMessage.success('已生成数据集 #' + result.dataset.id + '「' + result.dataset.name + '」')
    showExportDialog.value = false
    await fetchDatasets()
  } catch (error: any) { ElMessage.error(error?.message || '导出失败') } finally { exporting.value = false }
}
function openLabelDialog() {
  labelForm.value = { name: '', group_id: groups.value[0]?.id || '', color: '#409EFF', description: '' }
  showLabelDialog.value = true
}
async function saveLabel() {
  if (!labelForm.value.name.trim() || !labelForm.value.group_id) { ElMessage.warning('请填写标签名并选择标签组'); return }
  try {
    const group = groups.value.find(item => item.id === labelForm.value.group_id)
    const label = await post<any>('/labeling/labels', { name: labelForm.value.name, category: group?.name || '默认标签', color: labelForm.value.color, description: labelForm.value.description })
    await post('/labeling/groups/' + labelForm.value.group_id + '/labels?label_id=' + label.id, {})
    ElMessage.success('标签已创建并加入标签组')
    showLabelDialog.value = false
    await Promise.all([fetchGroups(), fetchLabels()])
  } catch (error: any) { ElMessage.error(error?.message || '创建标签失败') }
}
async function saveGroup() {
  if (!groupForm.value.name.trim()) { ElMessage.warning('请输入标签组名称'); return }
  try {
    await post('/labeling/groups', groupForm.value)
    ElMessage.success('标签组已创建')
    showGroupDialog.value = false
    groupForm.value = { name: '', description: '' }
    await fetchGroups()
  } catch (error: any) { ElMessage.error(error?.message || '创建标签组失败') }
}
async function deleteLabel(label: any) {
  try { await del('/labeling/labels/' + label.id); ElMessage.success('标签已删除'); await Promise.all([fetchGroups(), fetchLabels()]) } catch (error: any) { ElMessage.error(error?.message || '删除失败') }
}

onMounted(reloadAll)
</script>

<style scoped>
.labeling-page { padding: 24px; }
.page-alert { margin-bottom: 16px; }
.stats-row { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.work-toolbar { margin-bottom: 12px; }
.row-labels { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.row-labels :deep(.el-checkbox) { margin-right: 0; }
.pager { margin-top: 16px; display: flex; justify-content: flex-end; }
.color-dot { display: inline-block; width: 14px; height: 14px; border-radius: 50%; vertical-align: -2px; margin-right: 6px; }
</style>
