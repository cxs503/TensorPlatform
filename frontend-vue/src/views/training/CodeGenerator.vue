<template>
  <div class="page">
    <div class="page-header">
      <div><h1>代码生成器</h1><p>生成可运行的模型训练脚本，并可下载或保存到算法商店</p></div>
      <el-tag :type="llmHealth.mode === 'llm' ? 'success' : 'primary'">
        {{ llmHealth.mode === 'llm' ? `外部 LLM · ${llmHealth.provider}` : '本地模板模式' }}
      </el-tag>
    </div>
    <el-alert
      :title="llmHealth.message"
      :type="llmHealth.mode === 'llm' ? 'success' : llmHealth.status === 'ready' ? 'info' : 'warning'"
      :closable="false"
      show-icon
      class="mode-alert"
    />

    <el-row :gutter="20">
      <el-col :xs="24" :lg="10">
        <el-card>
          <template #header><strong>生成配置</strong></template>
          <el-form label-position="top">
            <template v-if="llmHealth.mode !== 'llm'">
              <el-form-item label="任务类型">
                <el-select v-model="config.task" style="width:100%">
                  <el-option v-for="item in tasks" :key="item.value" :label="item.label" :value="item.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="代码框架">
                <el-select v-model="config.framework" style="width:100%">
                  <el-option v-for="item in frameworks" :key="item.value" :label="item.label" :value="item.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="算法实现">
                <el-select v-model="config.algorithm" style="width:100%">
                  <el-option v-for="item in availableAlgorithms" :key="item.value" :label="item.label" :value="item.value" />
                </el-select>
              </el-form-item>
            </template>
            <el-form-item label="需求描述" required>
              <el-input v-model="config.requirement" type="textarea" :rows="llmHealth.mode === 'llm' ? 10 : 5" placeholder="描述需要生成的完整功能、数据入口、目标、算法偏好和输出要求；未指定的部分由大模型自行判断" />
            </el-form-item>
            <el-alert
              v-if="llmHealth.mode === 'llm'"
              title="只需描述需求。生成后平台会自动识别任务、框架、算法、依赖和训练兼容性。"
              type="success" :closable="false" show-icon class="local-note"
            />
            <el-alert
              v-if="llmHealth.mode !== 'llm'"
              title="本地模式按照上面的结构化配置生成代码；自由文本会写入代码说明，但不会被假装成自然语言推理结果。"
              type="info" :closable="false" show-icon class="local-note"
            />
            <el-button type="primary" @click="generate" :loading="loading" :disabled="llmHealth.status !== 'ready'" style="width:100%">
              {{ llmHealth.mode === 'llm' ? '🤖 AI 生成代码' : '生成代码' }}
            </el-button>
          </el-form>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="14">
        <el-card>
          <template #header>
            <div class="result-header"><strong>生成结果</strong><el-tag v-if="code" size="small" type="info">{{ generationModeLabel }}</el-tag></div>
          </template>
          <div v-if="code" class="code-panel"><pre><code>{{ code }}</code></pre></div>
          <el-empty v-else description="填写配置后生成代码" :image-size="80" />
          <el-card v-if="code && analysis" shadow="never" class="analysis-card">
            <template #header><strong>平台自动识别结果</strong></template>
            <el-descriptions :column="3" border size="small">
              <el-descriptions-item label="任务类型">{{ taskLabel(analysis.task_type) }}</el-descriptions-item>
              <el-descriptions-item label="代码框架">{{ frameworkLabel(analysis.framework) }}</el-descriptions-item>
              <el-descriptions-item label="算法实现">{{ algorithmLabel(analysis.algorithm_type) }}</el-descriptions-item>
              <el-descriptions-item label="入口函数">{{ analysis.entry_point || '未识别' }}</el-descriptions-item>
              <el-descriptions-item label="依赖包" :span="2">{{ (analysis.dependencies || []).join('、') || '仅标准库' }}</el-descriptions-item>
            </el-descriptions>
            <el-alert :title="analysis.compatibility_message" :type="analysis.training_adapter_compatible ? 'success' : 'warning'" :closable="false" show-icon class="compatibility-alert" />
          </el-card>
          <div v-if="code" class="actions">
            <el-button size="small" @click="optimize">{{ llmHealth.mode === 'llm' ? '🤖 AI 优化代码' : '语法整理' }}</el-button>
            <el-button size="small" @click="explain">{{ llmHealth.mode === 'llm' ? '🤖 AI 解释代码' : '结构说明' }}</el-button>
            <el-button size="small" @click="copyCode">复制</el-button>
            <el-button size="small" @click="downloadCode">下载代码</el-button>
            <el-button size="small" type="success" @click="openSaveDialog">保存到算法商店</el-button>
          </div>
          <el-alert v-if="explanation" title="处理结果" :description="explanation" type="info" :closable="false" class="result-alert" />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="showSaveDialog" title="保存到算法商店" width="520px">
      <el-form :model="saveForm" label-width="90px">
        <el-form-item label="算法名称" required><el-input v-model="saveForm.name" /></el-form-item>
        <el-form-item label="版本"><el-input v-model="saveForm.version" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="saveForm.description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="任务类型"><el-select v-model="saveForm.task_type" style="width:100%"><el-option v-for="item in saveTasks" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="代码框架"><el-select v-model="saveForm.framework" style="width:100%"><el-option v-for="item in saveFrameworks" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="算法实现"><el-select v-model="saveForm.algorithm_type" style="width:100%"><el-option v-for="item in saveAlgorithms" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      </el-form>
      <el-alert
        :title="saveAdapterCompatible ? '识别结果匹配训练适配器，保存后可以直接发起训练。' : '当前组合未匹配训练适配器；仍可保存源码、下载并在沙箱中编辑运行。'"
        :type="saveAdapterCompatible ? 'success' : 'warning'" :closable="false" show-icon
      />
      <template #footer>
        <el-button @click="showSaveDialog=false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveToStore">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'

const tasks = [
  { label: '回归预测', value: 'regression' },
  { label: '分类预测', value: 'classification' },
  { label: '时序预测', value: 'time_series' },
]
const frameworks = [
  { label: 'Scikit-learn', value: 'sklearn' },
  { label: 'PyTorch', value: 'pytorch' },
  { label: 'TensorFlow', value: 'tensorflow' },
  { label: 'XGBoost', value: 'xgboost' },
  { label: 'LightGBM', value: 'lightgbm' },
]
const saveTasks = [...tasks, { label: '其他任务', value: 'custom' }]
const saveFrameworks = [...frameworks, { label: '通用 Python', value: 'python' }]
const saveAlgorithms = [
  { label: '随机森林', value: 'random_forest' }, { label: 'MLP 神经网络', value: 'mlp' },
  { label: 'XGBoost', value: 'xgboost' }, { label: 'LightGBM', value: 'lightgbm' },
  { label: '其他自定义算法', value: 'custom' },
]
const algorithmsByFramework: Record<string, Array<{label:string;value:string}>> = {
  sklearn: [{ label: '随机森林', value: 'random_forest' }, { label: 'MLP 神经网络', value: 'mlp' }],
  pytorch: [{ label: 'MLP 神经网络', value: 'mlp' }],
  tensorflow: [{ label: 'MLP 神经网络', value: 'mlp' }],
  xgboost: [{ label: 'XGBoost', value: 'xgboost' }],
  lightgbm: [{ label: 'LightGBM', value: 'lightgbm' }],
}

const code = ref('')
const analysis = ref<any>(null)
const explanation = ref('')
const loading = ref(false)
const saving = ref(false)
const showSaveDialog = ref(false)
const generationMode = ref('')
const llmHealth = ref<any>({ status: 'checking', mode: '', provider: '', message: '正在检查代码生成服务...' })
const config = reactive({ task: 'regression', framework: 'sklearn', algorithm: 'random_forest', requirement: '' })
const saveForm = reactive({ name: '', version: '1.0.0', description: '', task_type: 'custom', framework: 'python', algorithm_type: 'custom' })
const availableAlgorithms = computed(() => algorithmsByFramework[config.framework] || [])
const generationModeLabel = computed(() => generationMode.value === 'llm' ? '外部 LLM 生成' : '本地模板生成')
const saveAdapterCompatible = computed(() =>
  ['regression', 'classification', 'time_series'].includes(saveForm.task_type)
  && ['random_forest', 'mlp', 'xgboost', 'lightgbm'].includes(saveForm.algorithm_type)
)

function taskLabel(value:string) { return saveTasks.find(item => item.value === value)?.label || value || '未识别' }
function frameworkLabel(value:string) { return saveFrameworks.find(item => item.value === value)?.label || value || '未识别' }
function algorithmLabel(value:string) { return saveAlgorithms.find(item => item.value === value)?.label || value || '未识别' }

watch(() => config.framework, () => {
  if (!availableAlgorithms.value.some(item => item.value === config.algorithm)) {
    config.algorithm = availableAlgorithms.value[0]?.value || 'random_forest'
  }
})

onMounted(async () => {
  try { llmHealth.value = await get('/code-generator/health') }
  catch (error:any) { llmHealth.value = { status: 'unavailable', mode: '', message: error?.message || '代码生成服务状态检查失败' } }
})

async function generate() {
  if (!config.requirement.trim()) return ElMessage.warning('请填写需求描述')
  loading.value = true
  explanation.value = ''
  analysis.value = null
  try {
    const payload:any = { requirement: config.requirement.trim(), language: 'python' }
    if (llmHealth.value.mode !== 'llm') {
      Object.assign(payload, { task: config.task, framework: config.framework, algorithm: config.algorithm })
    }
    const response = await post('/code-generator/generate', payload)
    if (!response?.code) throw new Error('后端没有返回生成代码')
    code.value = response.code
    analysis.value = response.analysis || null
    generationMode.value = response.mode || 'local_template'
    if (response.warning) ElMessage.warning(response.warning)
    else ElMessage.success(response.mode === 'llm' ? 'LLM代码已生成' : '本地可运行代码已生成')
  } catch (error:any) {
    code.value = ''
    ElMessage.error(`生成失败：${error?.response?.data?.detail || error?.message || error}`)
  } finally { loading.value = false }
}

async function optimize() {
  if (!code.value) return
  try {
    const response = await post('/code-generator/optimize', { code: code.value, language: 'python' })
    code.value = response?.optimized_code || code.value
    await refreshAnalysis()
    explanation.value = response?.explanation || ''
    ElMessage.success(response?.mode === 'llm' ? '代码优化完成' : '语法整理完成')
  } catch (error:any) { ElMessage.error(error?.response?.data?.detail || '处理失败') }
}
async function refreshAnalysis() {
  if (!code.value) return
  try {
    const response = await post('/code-generator/analyze', { code: code.value })
    if (response?.analysis) {
      const detected = response.analysis
      if (detected.task_type === 'custom' && analysis.value?.task_type) detected.task_type = analysis.value.task_type
      analysis.value = detected
    }
  } catch { /* 生成接口已经完成语法校验，分析失败不阻断代码使用 */ }
}
async function explain() {
  if (!code.value) return
  try {
    const response = await post('/code-generator/explain', { code: code.value })
    explanation.value = response?.explanation || '暂无说明'
  } catch (error:any) { ElMessage.error(error?.response?.data?.detail || '说明生成失败') }
}
async function copyCode() {
  if (!code.value) return
  try {
    let copied = false
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(code.value)
        copied = true
      } catch {
        copied = false
      }
    }
    if (!copied) {
      const textarea = document.createElement('textarea')
      textarea.value = code.value
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.left = '-9999px'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      copied = document.execCommand('copy')
      textarea.remove()
      if (!copied) throw new Error('浏览器拒绝复制操作')
    }
    ElMessage.success('代码已复制')
  } catch (error:any) {
    ElMessage.error(error?.message || '复制失败，请使用下载代码')
  }
}
function downloadText(content:string, filename:string) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/x-python;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url)
}
function sourceFilename() {
  const task = analysis.value?.task_type || config.task || 'custom'
  const algorithm = analysis.value?.algorithm_type || config.algorithm || 'custom'
  return `foundation_${task}_${algorithm}.py`
}
function saveSourceFilename() { return `foundation_${saveForm.task_type}_${saveForm.algorithm_type}.py` }
function downloadCode() { if (code.value) downloadText(code.value, sourceFilename()) }

function openSaveDialog() {
  const base = config.requirement.trim().slice(0, 30)
  saveForm.name = base || `${taskLabel(analysis.value?.task_type || config.task)}算法`
  saveForm.task_type = analysis.value?.task_type || config.task || 'custom'
  saveForm.framework = analysis.value?.framework || config.framework || 'python'
  saveForm.algorithm_type = analysis.value?.algorithm_type || config.algorithm || 'custom'
  saveForm.description = `由代码生成器生成；${frameworkLabel(saveForm.framework)} / ${algorithmLabel(saveForm.algorithm_type)}`
  showSaveDialog.value = true
}
async function saveToStore() {
  if (!saveForm.name.trim()) return ElMessage.warning('请填写算法名称')
  saving.value = true
  try {
    const result = await post('/algorithm-store/publish-from-code', {
      name: saveForm.name.trim(), description: saveForm.description.trim(), code: code.value,
      language: 'python', filename: saveSourceFilename(), task_type: saveForm.task_type,
      algorithm_type: saveForm.algorithm_type, framework: saveForm.framework,
      version: saveForm.version.trim() || '1.0.0', tags: ['代码生成器'], source_origin: 'code_generator',
    })
    showSaveDialog.value = false
    ElMessage.success(`${result.message || '已保存到算法商店'}，算法ID ${result.id}`)
  } catch (error:any) {
    ElMessage.error(`保存失败：${error?.response?.data?.detail || error?.message || '未知错误'}`)
  } finally { saving.value = false }
}
</script>

<style scoped>
.page{padding:24px}.page-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px}.page-header h1{font-size:24px;margin:0}.page-header p{color:#606266;margin:8px 0 0}.mode-alert{margin-bottom:16px}.local-note{margin-bottom:14px}.result-header{display:flex;align-items:center;justify-content:space-between}.code-panel{background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:6px;max-height:480px;overflow:auto}.code-panel pre{margin:0;font-size:12px;white-space:pre}.analysis-card{margin-top:12px}.compatibility-alert{margin-top:10px}.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.actions .el-button{margin-left:0}.result-alert{margin-top:12px}
</style>
