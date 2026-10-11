<template>
  <div class="artifact-page">
    <PageHeader
      title="模型制品验证"
      desc="统一保存并验证ONNX、PKL与Docker构建包，确认下载后的文件能够真实加载和推理"
    />

    <el-alert
      title="生成成功只代表文件已经打包；“运行验证通过”才表示该制品可加载、可推理。Docker包会验证包结构、校验和及内部ONNX。"
      type="info"
      :closable="false"
      show-icon
      class="intro"
    />

    <el-row :gutter="16" class="stats">
      <el-col :span="6"><el-card><el-statistic title="制品总数" :value="artifacts.length" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="有效制品" :value="validCount" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="已性能测试" :value="benchmarkedCount" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="格式数量" :value="formatCount" /></el-card></el-col>
    </el-row>

    <el-card shadow="never" class="section">
      <template #header>
        <div class="header-row">
          <span>已保存制品</span>
          <div class="header-actions">
            <el-input v-model="search" clearable placeholder="按模型名称或ID搜索" style="width:220px" />
            <el-button @click="loadArtifacts">刷新</el-button>
            <el-button type="primary" @click="openCreate()">生成制品</el-button>
          </div>
        </div>
      </template>

      <el-table :data="filteredArtifacts" stripe v-loading="loading">
        <el-table-column prop="id" label="制品ID" width="80" />
        <el-table-column label="模型" min-width="190">
          <template #default="{ row }">
            <b>{{ row.model_name || '-' }}</b>
            <div class="muted">模型ID #{{ row.model_id }} · 版本ID #{{ row.model_version_id }}</div>
          </template>
        </el-table-column>
        <el-table-column label="格式" width="105">
          <template #default="{ row }"><el-tag :type="formatTag(row.format)">{{ formatLabel(row.format) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="文件" min-width="210" show-overflow-tooltip>
          <template #default="{ row }">
            <span>{{ row.file_name }}</span>
            <div class="muted">{{ row.size_mb }} MB</div>
          </template>
        </el-table-column>
        <el-table-column label="验证状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.validation_status)">{{ statusLabel(row.validation_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近性能" width="180">
          <template #default="{ row }">
            <template v-if="row.benchmark_result">
              <div>{{ row.benchmark_result.avg_ms }} ms/批</div>
              <div class="muted">{{ row.benchmark_result.throughput }} 样本/秒</div>
            </template>
            <span v-else class="muted">尚未测试</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="生成时间" width="175">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="270" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="openValidation(row)">运行验证</el-button>
            <el-button size="small" @click="downloadArtifact(row)">下载</el-button>
            <el-button size="small" type="danger" plain @click="removeArtifact(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !filteredArtifacts.length" description="暂无模型制品，请先生成" />
    </el-card>

    <el-dialog v-model="showCreate" title="生成模型制品" width="560px">
      <el-form label-width="110px">
        <el-form-item label="模型" required>
          <el-select v-model="createForm.model_id" filterable placeholder="按名称或ID选择模型" style="width:100%">
            <el-option v-for="model in models" :key="model.id" :label="`${model.name} (#${model.id})`" :value="model.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="制品格式" required>
          <el-radio-group v-model="createForm.format">
            <el-radio value="onnx">ONNX</el-radio>
            <el-radio value="pkl">PKL</el-radio>
            <el-radio value="docker">Docker构建包</el-radio>
            <el-radio value="exe" disabled>Windows EXE</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-alert :title="formatNotice" type="info" :closable="false" show-icon />
      </el-form>
      <template #footer>
        <el-button @click="showCreate=false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createArtifact">生成并保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showValidation" title="制品运行验证与性能测试" width="780px">
      <template v-if="validationTarget">
        <el-descriptions :column="3" border class="target-info">
          <el-descriptions-item label="制品ID">#{{ validationTarget.id }}</el-descriptions-item>
          <el-descriptions-item label="模型">{{ validationTarget.model_name }} (#{{ validationTarget.model_id }})</el-descriptions-item>
          <el-descriptions-item label="格式">{{ formatLabel(validationTarget.format) }}</el-descriptions-item>
        </el-descriptions>
        <el-form label-width="110px">
          <el-form-item label="重复次数">
            <el-input-number v-model="validationForm.n_runs" :min="1" :max="10000" />
            <span class="tip">反复运行同一批输入，用于统计延迟和吞吐量</span>
          </el-form-item>
          <el-form-item label="测试数据" required>
            <div style="width:100%">
              <el-input v-model="validationForm.inputText" type="textarea" :rows="9" :placeholder="inputPlaceholder" />
              <div class="tip">
                普通模型可填 <code>[[1, 2, 3]]</code>；需要字段名的自定义模型可填 <code>[{"特征A": 1, "特征时间": "2026-01-01"}]</code>。
              </div>
            </div>
          </el-form-item>
        </el-form>

        <div v-if="validationResult" class="result-panel">
          <el-result icon="success" title="制品运行验证通过" :sub-title="`${validationResult.n_samples}个样本，重复${validationResult.n_runs}次`" />
          <el-row :gutter="12">
            <el-col :span="6"><el-card><el-statistic title="平均耗时" :value="validationResult.avg_ms" suffix="ms" /></el-card></el-col>
            <el-col :span="6"><el-card><el-statistic title="P50延迟" :value="validationResult.p50_ms" suffix="ms" /></el-card></el-col>
            <el-col :span="6"><el-card><el-statistic title="P99延迟" :value="validationResult.p99_ms" suffix="ms" /></el-card></el-col>
            <el-col :span="6"><el-card><el-statistic title="吞吐量" :value="validationResult.throughput" suffix="样本/秒" /></el-card></el-col>
          </el-row>
          <el-card shadow="never" class="prediction-card">
            <template #header>预测结果</template>
            <pre>{{ JSON.stringify(validationResult.predictions, null, 2) }}</pre>
          </el-card>
        </div>
      </template>
      <template #footer>
        <el-button @click="showValidation=false">关闭</el-button>
        <el-button type="primary" :loading="validating" @click="runValidation">开始验证</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del, get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const route = useRoute()
const artifacts = ref<any[]>([])
const models = ref<any[]>([])
const loading = ref(false)
const creating = ref(false)
const validating = ref(false)
const showCreate = ref(false)
const showValidation = ref(false)
const validationTarget = ref<any>(null)
const validationResult = ref<any>(null)
const search = ref('')
const createForm = reactive({ model_id: null as number | null, format: 'onnx' })
const validationForm = reactive({ n_runs: 100, inputText: '' })

const validCount = computed(() => artifacts.value.filter(item => item.validation_status === 'valid').length)
const benchmarkedCount = computed(() => artifacts.value.filter(item => item.benchmark_result).length)
const formatCount = computed(() => new Set(artifacts.value.map(item => item.format)).size)
const filteredArtifacts = computed(() => {
  const value = search.value.trim().toLowerCase()
  if (!value) return artifacts.value
  return artifacts.value.filter(item =>
    String(item.model_name || '').toLowerCase().includes(value)
      || String(item.model_id).includes(value)
      || String(item.id).includes(value),
  )
})
const formatNotice = computed(() => ({
  onnx: '生成标准ONNX模型，校验格式与ONNX Runtime加载；适合跨平台推理。',
  pkl: '保存Python模型对象，校验反序列化和predict接口；运行依赖必须与训练环境兼容。',
  docker: '生成包含Dockerfile、服务代码和ONNX模型的完整构建包，并验证包结构、校验和及内部模型。',
}[createForm.format] || ''))
const inputPlaceholder = computed(() => validationTarget.value?.format === 'pkl'
  ? '数值模型：\n[[1, 2, 3]]\n\n自定义字段模型：\n[{"特征时间":"2026-01-01 00:00:00","特征A":1.0}]'
  : '例如：\n[[1, 2, 3]]')

function formatLabel(value: string) { return ({ onnx: 'ONNX', pkl: 'PKL', docker: 'Docker包' } as any)[value] || value }
function formatTag(value: string) { return ({ onnx: 'success', pkl: 'warning', docker: 'primary' } as any)[value] || 'info' }
function statusLabel(value: string) { return ({ valid: '基础校验通过', invalid: '校验失败', generated: '已生成' } as any)[value] || value }
function statusTag(value: string) { return value === 'valid' ? 'success' : value === 'invalid' ? 'danger' : 'info' }
function formatTime(value: string) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }

async function loadArtifacts() {
  loading.value = true
  try {
    const result: any = await get('/model-artifacts')
    artifacts.value = result.items || result || []
  } catch (error: any) {
    ElMessage.error(`加载制品失败：${error?.message || '未知错误'}`)
  } finally { loading.value = false }
}

async function loadModels() {
  try {
    const result: any = await get('/models', { limit: 500 })
    models.value = (result.items || result || []).filter((item: any) => item.latest_version)
  } catch { models.value = [] }
}

function openCreate(model?: any) {
  createForm.model_id = model?.id || createForm.model_id
  showCreate.value = true
}

async function createArtifact() {
  if (!createForm.model_id) return ElMessage.warning('请选择模型')
  creating.value = true
  try {
    const result: any = await post('/model-artifacts', { ...createForm })
    ElMessage.success(result.validation_status === 'valid' ? '制品已生成并通过基础校验' : '制品已生成，但基础校验未通过')
    showCreate.value = false
    await loadArtifacts()
  } catch (error: any) {
    ElMessage.error(`生成失败：${error?.message || '未知错误'}`)
  } finally { creating.value = false }
}

function openValidation(row: any) {
  validationTarget.value = row
  validationResult.value = null
  validationForm.n_runs = 100
  validationForm.inputText = ''
  showValidation.value = true
}

async function runValidation() {
  if (!validationTarget.value) return
  let X: any
  try { X = JSON.parse(validationForm.inputText) }
  catch { return ElMessage.warning('测试数据不是有效JSON') }
  validating.value = true
  try {
    validationResult.value = await post(`/model-artifacts/${validationTarget.value.id}/validate`, {
      X,
      n_runs: validationForm.n_runs,
    })
    ElMessage.success('制品运行验证通过')
    await loadArtifacts()
  } catch (error: any) {
    ElMessage.error(`验证失败：${error?.message || '未知错误'}`)
  } finally { validating.value = false }
}

async function downloadArtifact(row: any) {
  try {
    const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
    const response = await fetch(`/api/v1/model-artifacts/${row.id}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail || `HTTP ${response.status}`)
    }
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = row.file_name
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error: any) { ElMessage.error(`下载失败：${error?.message || '未知错误'}`) }
}

async function removeArtifact(row: any) {
  try { await ElMessageBox.confirm(`确定删除制品 #${row.id}「${row.file_name}」吗？`, '删除制品', { type: 'warning' }) }
  catch { return }
  try {
    await del(`/model-artifacts/${row.id}`)
    ElMessage.success('制品已删除')
    await loadArtifacts()
  } catch (error: any) { ElMessage.error(`删除失败：${error?.message || '未知错误'}`) }
}

onMounted(async () => {
  await Promise.all([loadArtifacts(), loadModels()])
  const modelId = Number(route.query.model_id)
  if (Number.isInteger(modelId) && modelId > 0) {
    createForm.model_id = modelId
    showCreate.value = true
  }
})
</script>

<style scoped>
.artifact-page { max-width: 1500px; padding: 20px; }
.intro, .stats, .section { margin-bottom: 16px; }
.header-row, .header-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.muted, .tip { color: #909399; font-size: 12px; margin-top: 4px; }
.tip { margin-left: 10px; line-height: 22px; }
.tip code { padding: 2px 4px; background: #f3f4f6; border-radius: 4px; }
.target-info { margin-bottom: 18px; }
.result-panel { margin-top: 16px; }
.prediction-card { margin-top: 14px; }
.prediction-card pre { max-height: 260px; overflow: auto; white-space: pre-wrap; }
</style>
