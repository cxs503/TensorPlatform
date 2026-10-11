<template>
  <div class="scheduler-page">
    <div class="page-header">
      <div>
        <h2>⏰ 定时调度</h2>
        <p>按北京时间执行。选择执行频率和时间即可；系统每 15 秒检查一次到期任务。</p>
      </div>
      <el-button type="primary" @click="openCreate">+ 新建任务</el-button>
    </div>

    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :md="6"><el-statistic title="总任务" :value="schedules.length" /></el-col>
      <el-col :xs="12" :md="6"><el-statistic title="启用中" :value="schedules.filter(s => s.enabled).length" /></el-col>
      <el-col :xs="12" :md="6"><el-statistic title="已暂停" :value="schedules.filter(s => !s.enabled).length" /></el-col>
      <el-col :xs="12" :md="6"><el-statistic title="今日已触发" :value="todayExecs" /></el-col>
    </el-row>

    <el-card class="section-card">
      <el-table :data="schedules" stripe v-loading="loading" empty-text="暂无定时任务">
        <el-table-column prop="name" label="任务名" min-width="160" />
        <el-table-column label="执行计划" min-width="175">
          <template #default="{ row }">
            <div>{{ formatSchedule(row.cron_expression) }}</div>
            <el-text class="cron" size="small">{{ row.cron_expression }}</el-text>
          </template>
        </el-table-column>
        <el-table-column prop="task_type" label="任务类型" width="130">
          <template #default="{ row }"><el-tag size="small">{{ taskLabel(row.task_type) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '已暂停' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="下次执行" min-width="165">
          <template #default="{ row }">{{ formatTime(row.next_run) }}</template>
        </el-table-column>
        <el-table-column label="上次执行" min-width="165">
          <template #default="{ row }">{{ formatTime(row.last_run) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="360" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="executeNow(row)">立即执行</el-button>
            <el-button size="small" @click="toggleSchedule(row)">{{ row.enabled ? '暂停' : '恢复' }}</el-button>
            <el-button size="small" @click="showHistory(row)">记录</el-button>
            <el-button size="small" @click="editSchedule(row)">编辑</el-button>
            <el-button size="small" type="danger" link @click="deleteSchedule(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="showForm" :title="editingId ? '编辑调度任务' : '新建调度任务'" width="680px" destroy-on-close>
      <el-alert type="info" :closable="false" class="dialog-tip">
        请直接选择执行频率和时间。只有需要复杂规则时，才使用“高级 Cron”。
      </el-alert>
      <el-form :model="form" label-width="112px">
        <el-form-item label="任务名称" required>
          <el-input v-model="form.name" placeholder="例如：每日生产设备温度批量推理" maxlength="100" />
        </el-form-item>
        <el-form-item label="执行频率" required>
          <el-select v-model="form.schedule_mode" style="width:220px">
            <el-option label="每天" value="daily" />
            <el-option label="每周" value="weekly" />
            <el-option label="每月" value="monthly" />
            <el-option label="高级 Cron" value="custom" />
          </el-select>
          <div class="form-hint">{{ scheduleModeHint }}</div>
        </el-form-item>
        <template v-if="form.schedule_mode !== 'custom'">
          <el-form-item label="执行时间" required>
            <el-time-picker v-model="form.run_time" value-format="HH:mm" format="HH:mm" placeholder="选择时间" style="width:180px" />
          </el-form-item>
          <el-form-item v-if="form.schedule_mode === 'weekly'" label="星期几">
            <el-select v-model="form.weekday" style="width:180px">
              <el-option label="星期一" value="1" /><el-option label="星期二" value="2" />
              <el-option label="星期三" value="3" /><el-option label="星期四" value="4" />
              <el-option label="星期五" value="5" /><el-option label="星期六" value="6" />
              <el-option label="星期日" value="0" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.schedule_mode === 'monthly'" label="每月日期">
            <el-input-number v-model="form.day_of_month" :min="1" :max="28" controls-position="right" />
            <div class="form-hint">为避免部分月份没有对应日期，暂支持 1 至 28 日。</div>
          </el-form-item>
        </template>
        <el-form-item v-else label="高级 Cron" required>
          <el-input v-model="form.cron_expression" placeholder="例如：*/15 * * * *" />
          <div class="form-hint">五段格式：分 时 日 月 周。例如 <code>*/15 * * * *</code> 表示每 15 分钟。</div>
        </el-form-item>
        <el-form-item label="任务类型" required>
          <el-select v-model="form.task_type" style="width:100%" :disabled="Boolean(editingId)">
            <el-option label="批量推理" value="batch_inference" />
            <el-option label="发起模型训练" value="model_training" />
          </el-select>
          <div class="form-hint">{{ taskHint }}</div>
        </el-form-item>

        <template v-if="form.task_type === 'batch_inference'">
          <el-form-item label="模型 ID" required>
            <el-input-number v-model="form.model_id" :min="1" controls-position="right" />
          </el-form-item>
          <el-form-item label="数据集 ID" required>
            <el-input-number v-model="form.dataset_id" :min="1" controls-position="right" />
          </el-form-item>
          <el-form-item label="结果格式">
            <el-select v-model="form.output_format" style="width:180px">
              <el-option label="JSON" value="json" />
              <el-option label="CSV" value="csv" />
            </el-select>
          </el-form-item>
        </template>

        <template v-else>
          <el-form-item label="数据集 ID" required>
            <el-input-number v-model="form.dataset_id" :min="1" controls-position="right" />
          </el-form-item>
          <el-form-item label="预测目标列" required>
            <el-input v-model="form.target_column" placeholder="例如：temperature" />
          </el-form-item>
          <el-form-item label="训练类型">
            <el-select v-model="form.training_task_type" style="width:180px">
              <el-option label="回归" value="regression" />
              <el-option label="分类" value="classification" />
              <el-option label="时序预测" value="timeseries" />
            </el-select>
          </el-form-item>
          <el-form-item label="模型类型">
            <el-select v-model="form.model_type" style="width:220px">
              <el-option label="随机森林" value="random_forest" />
              <el-option label="XGBoost" value="xgboost" />
              <el-option label="LightGBM" value="lightgbm" />
              <el-option label="MLP" value="mlp" />
            </el-select>
          </el-form-item>
          <el-form-item label="算法 ID">
            <el-input-number v-model="form.algorithm_id" :min="1" controls-position="right" />
            <div class="form-hint">可选。填写后使用对应上传算法；留空则按所选模型类型训练。</div>
          </el-form-item>
          <el-form-item label="超参数 JSON">
            <el-input v-model="form.hyperparams" type="textarea" :rows="3" placeholder='可留空，例如：{"epochs": 100, "learning_rate": 0.001}' />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" @click="saveSchedule" :loading="saving">{{ editingId ? '保存修改' : '创建任务' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showExecs" :title="`执行记录 · ${historyName}`" width="820px">
      <el-table :data="executions" stripe v-loading="historyLoading" empty-text="尚未执行">
        <el-table-column label="开始时间" width="175">
          <template #default="{ row }">{{ formatTime(row.start_time) }}</template>
        </el-table-column>
        <el-table-column label="结束时间" width="175">
          <template #default="{ row }">{{ formatTime(row.end_time) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="executionTagType(row.status)" size="small">{{ executionLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结果 / 错误" min-width="250" show-overflow-tooltip>
          <template #default="{ row }">{{ formatExecution(row) }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del, get, post, put } from '@/api/legacy'

const loading = ref(false)
const saving = ref(false)
const historyLoading = ref(false)
const showForm = ref(false)
const showExecs = ref(false)
const schedules = ref<any[]>([])
const executions = ref<any[]>([])
const todayExecs = ref(0)
const editingId = ref('')
const historyName = ref('')

const defaultForm = () => ({
  name: '',
  cron_expression: '0 2 * * *',
  schedule_mode: 'daily',
  run_time: '02:00',
  weekday: '1',
  day_of_month: 1,
  task_type: 'batch_inference',
  model_id: undefined as number | undefined,
  dataset_id: undefined as number | undefined,
  output_format: 'json',
  target_column: '',
  training_task_type: 'regression',
  model_type: 'random_forest',
  algorithm_id: undefined as number | undefined,
  hyperparams: ''
})
const form = ref(defaultForm())

const taskHint = computed(() => ({
  batch_inference: '到期后使用指定模型，对指定平台数据集发起一次批量推理。',
  model_training: '到期后按此处保存的数据集、目标列与参数创建一条真实训练任务。'
}[form.value.task_type] || ''))

const scheduleModeHint = computed(() => ({
  daily: '每天在指定时间执行一次。',
  weekly: '每周在指定星期和时间执行一次。',
  monthly: '每月在指定日期和时间执行一次。',
  custom: '用于每 15 分钟、工作日等更复杂的执行规则。'
}[form.value.schedule_mode] || ''))

onMounted(fetchSchedules)

function taskLabel(type: string) {
  return ({ batch_inference: '批量推理', model_training: '模型训练' } as Record<string, string>)[type] || type
}
function executionLabel(status: string) {
  return ({ pending: '等待中', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[status] || status
}
function executionTagType(status: string) {
  return ({ completed: 'success', failed: 'danger', running: 'warning', pending: 'info', cancelled: 'info' } as Record<string, any>)[status] || 'info'
}
function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString() : '-'
}
function parseScheduleExpression(expression?: string) {
  const cron = String(expression || '').trim()
  const [minute, hour, day, month, weekday, ...extra] = cron.split(/\s+/)
  const validTime = /^(?:[01]?\d|2[0-3])$/.test(hour || '') && /^(?:[0-5]?\d)$/.test(minute || '')
  const run_time = validTime ? `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}` : '02:00'
  if (!extra.length && validTime && day === '*' && month === '*' && weekday === '*') {
    return { schedule_mode: 'daily', run_time, weekday: '1', day_of_month: 1, cron_expression: cron }
  }
  if (!extra.length && validTime && day === '*' && month === '*' && /^[0-6]$/.test(weekday || '')) {
    return { schedule_mode: 'weekly', run_time, weekday, day_of_month: 1, cron_expression: cron }
  }
  if (!extra.length && validTime && /^(?:[1-9]|[12]\d|3[01])$/.test(day || '') && month === '*' && weekday === '*') {
    return { schedule_mode: 'monthly', run_time, weekday: '1', day_of_month: Number(day), cron_expression: cron }
  }
  return { schedule_mode: 'custom', run_time, weekday: '1', day_of_month: 1, cron_expression: cron }
}
function formatSchedule(expression?: string) {
  const plan = parseScheduleExpression(expression)
  const weekdays: Record<string, string> = { '0': '星期日', '1': '星期一', '2': '星期二', '3': '星期三', '4': '星期四', '5': '星期五', '6': '星期六' }
  if (plan.schedule_mode === 'daily') return `每天 ${plan.run_time}`
  if (plan.schedule_mode === 'weekly') return `每周${weekdays[plan.weekday] || ''} ${plan.run_time}`
  if (plan.schedule_mode === 'monthly') return `每月 ${plan.day_of_month} 日 ${plan.run_time}`
  return '高级执行计划'
}
function buildCronExpression() {
  if (form.value.schedule_mode === 'custom') {
    if (!form.value.cron_expression.trim()) throw new Error('请填写高级 Cron 表达式')
    return form.value.cron_expression.trim()
  }
  const [hour, minute] = String(form.value.run_time || '').split(':')
  if (!/^(?:[01]?\d|2[0-3])$/.test(hour || '') || !/^(?:[0-5]?\d)$/.test(minute || '')) {
    throw new Error('请选择有效的执行时间')
  }
  if (form.value.schedule_mode === 'daily') return `${Number(minute)} ${Number(hour)} * * *`
  if (form.value.schedule_mode === 'weekly') return `${Number(minute)} ${Number(hour)} * * ${form.value.weekday}`
  return `${Number(minute)} ${Number(hour)} ${form.value.day_of_month} * *`
}
function formatExecution(row: any) {
  if (row.error) return row.error
  if (!row.result) return '-'
  return typeof row.result === 'string' ? row.result : JSON.stringify(row.result)
}

async function fetchSchedules() {
  loading.value = true
  try {
    const data: any = await get('/schedules')
    schedules.value = Array.isArray(data) ? data : (data.items || [])
    const today = new Date().toLocaleDateString()
    todayExecs.value = schedules.value.filter((item: any) => item.last_run && new Date(item.last_run).toLocaleDateString() === today).length
  } catch (error: any) {
    ElMessage.error(error?.message || '定时任务加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  form.value = defaultForm()
  showForm.value = true
}

function editSchedule(row: any) {
  const config = row.config || {}
  editingId.value = row.id
  form.value = {
    ...defaultForm(),
    ...parseScheduleExpression(row.cron_expression),
    name: row.name || '',
    task_type: row.task_type || 'batch_inference',
    model_id: config.model_id,
    dataset_id: config.dataset_id,
    output_format: config.output_format || 'json',
    target_column: config.target_column || '',
    training_task_type: config.task_type || 'regression',
    model_type: config.model_type || 'random_forest',
    algorithm_id: config.algorithm_id,
    hyperparams: config.hyperparams || ''
  }
  showForm.value = true
}

function buildConfig() {
  if (form.value.task_type === 'batch_inference') {
    if (!form.value.model_id || !form.value.dataset_id) throw new Error('请填写模型 ID 和数据集 ID')
    return { model_id: form.value.model_id, dataset_id: form.value.dataset_id, output_format: form.value.output_format }
  }
  if (!form.value.dataset_id || !form.value.target_column.trim()) throw new Error('请填写数据集 ID 和预测目标列')
  if (form.value.hyperparams.trim()) {
    try { JSON.parse(form.value.hyperparams) } catch { throw new Error('超参数必须是有效的 JSON') }
  }
  return {
    name: `${form.value.name}-训练`,
    dataset_id: form.value.dataset_id,
    target_column: form.value.target_column.trim(),
    task_type: form.value.training_task_type,
    model_type: form.value.model_type,
    algorithm_id: form.value.algorithm_id || undefined,
    hyperparams: form.value.hyperparams.trim(),
    hyperparameter_source: form.value.hyperparams.trim() ? 'manual' : 'default'
  }
}

async function saveSchedule() {
  saving.value = true
  try {
    if (!form.value.name.trim()) throw new Error('请填写任务名称')
    const cronExpression = buildCronExpression()
    const payload = {
      name: form.value.name.trim(),
      cron_expression: cronExpression,
      ...(editingId.value ? {} : { task_type: form.value.task_type }),
      config: buildConfig()
    }
    if (editingId.value) await put(`/schedules/${editingId.value}`, payload)
    else await post('/schedules', payload)
    ElMessage.success(editingId.value ? '定时任务已更新' : '定时任务创建成功')
    showForm.value = false
    await fetchSchedules()
  } catch (error: any) {
    ElMessage.error(error?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function executeNow(row: any) {
  try {
    const execution: any = await post(`/schedules/${row.id}/execute`)
    ElMessage.success('已加入执行队列，可在执行记录查看状态')
    await showHistory(row)
    const current = executions.value.find((item: any) => item.execution_id === execution.execution_id)
    if (current?.status === 'running') window.setTimeout(() => showHistory(row), 1200)
  } catch (error: any) {
    ElMessage.error(error?.message || '执行失败')
  }
}

async function showHistory(row: any) {
  showExecs.value = true
  historyName.value = row.name
  historyLoading.value = true
  try {
    const data: any = await get(`/schedules/${row.id}/executions`)
    executions.value = Array.isArray(data) ? data : (data.items || [])
  } catch (error: any) {
    ElMessage.error(error?.message || '执行记录加载失败')
  } finally {
    historyLoading.value = false
  }
}

async function toggleSchedule(row: any) {
  try {
    await put(`/schedules/${row.id}`, { enabled: !row.enabled })
    ElMessage.success(row.enabled ? '任务已暂停' : '任务已恢复')
    await fetchSchedules()
  } catch (error: any) {
    ElMessage.error(error?.message || '状态更新失败')
  }
}

async function deleteSchedule(id: string) {
  try {
    await ElMessageBox.confirm('删除后，该任务的执行记录也会一并删除。确认继续吗？', '删除定时任务', { type: 'warning' })
    await del(`/schedules/${id}`)
    ElMessage.success('已删除')
    await fetchSchedules()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '删除失败')
  }
}
</script>

<style scoped>
.scheduler-page { padding: 24px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 20px; }
.page-header h2 { margin: 0 0 6px; font-size: 20px; color: #1f2937; }
.page-header p { margin: 0; color: #64748b; font-size: 13px; line-height: 1.6; }
.stats-row { margin-bottom: 20px; }
.section-card { margin-bottom: 20px; }
.cron { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.dialog-tip { margin-bottom: 18px; }
.form-hint { color: #909399; font-size: 12px; line-height: 1.5; margin-top: 4px; }
@media (max-width: 768px) {
  .scheduler-page { padding: 14px; }
  .page-header { align-items: stretch; flex-direction: column; }
}
</style>
