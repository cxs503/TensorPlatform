<template>
  <div class="deploy-page">
    <PageHeader title="部署管理" desc="将已训练模型部署为可用的推理服务" />

    <el-row :gutter="16" class="stats">
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv">{{ deployments.length }}</div><div class="sl">部署总数</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv ok">{{ healthyCount }}</div><div class="sl">运行正常</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv">{{ stoppedCount }}</div><div class="sl">已停止</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv warn">{{ abnormalCount }}</div><div class="sl">运行异常</div></el-card></el-col>
    </el-row>

    <el-card shadow="never" class="section">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>部署列表</span>
        </div>
      </template>
      <el-collapse v-model="expandedFolderKeys" class="deployment-folders">
        <el-collapse-item v-for="folder in deploymentFolders" :key="folder.key" :name="folder.key">
          <template #title>
            <div class="folder-title">
              <div><b>📁 {{ folder.name }}</b><el-tag size="small" effect="plain">{{ folder.deployments.length }} 条部署记录</el-tag></div>
              <div v-if="folder.front" class="front-summary">
                <el-tag type="success" size="small">当前前台</el-tag>
                <span>#{{ folder.front.model_id }} {{ folder.front.model_name || '-' }} · {{ folder.front.model_version || folder.front.version || '-' }}</span>
                <el-tag :type="statusTag(folder.front.status)" size="small">{{ statusLabel(folder.front.status) }}</el-tag>
              </div>
            </div>
          </template>
          <div class="deployment-record-title">部署记录（{{ folder.deployments.length }}）</div>
          <el-table :data="folder.deployments" size="small" max-height="300" stripe class="deployment-record-table">
            <el-table-column prop="id" label="部署ID" width="82" />
            <el-table-column label="模型" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">#{{ row.model_id }} {{ row.model_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="版本" width="112"><template #default="{ row }">{{ row.model_version || row.version || '-' }}</template></el-table-column>
            <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="创建时间" width="130"><template #default="{ row }">{{ formatDate(row.created_at || row.updated_at) }}</template></el-table-column>
            <el-table-column label="操作" width="250" fixed="right">
              <template #default="{ row }">
                <el-button size="small" @click.stop="openDepDetail(row)">详情</el-button>
                <el-button size="small" type="success" @click.stop="openPack(row)">打包</el-button>
                <el-popconfirm
                  title="删除该部署记录并停止其推理端点？模型、模型版本和训练记录不会删除。"
                  confirm-button-text="删除部署"
                  cancel-button-text="取消"
                  @confirm="removeDep(row)"
                >
                  <template #reference><el-button size="small" type="danger" plain @click.stop>删除部署</el-button></template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-if="!deployments.length" description="暂无部署" :image-size="60" />
    </el-card>

    <el-dialog v-model="showDepDetail" :title="selectedDep ? `部署详情 #${selectedDep.id}` : '部署详情'" width="680px" destroy-on-close>
      <el-descriptions v-if="selectedDep" :column="2" border size="small">
        <el-descriptions-item label="模型名称">{{ selectedDep.model_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型ID">#{{ selectedDep.model_id }}</el-descriptions-item>
        <el-descriptions-item label="版本">{{ selectedDep.model_version || selectedDep.version || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag :type="statusTag(selectedDep.status)" size="small">{{ statusLabel(selectedDep.status) }}</el-tag></el-descriptions-item>
      </el-descriptions>
      <el-alert title="部署服务已就绪，可在平台内通过在线推理或批量推理使用该模型。" type="info" :closable="false" show-icon style="margin-top:12px" />
      <template #footer><el-button @click="showDepDetail=false">关闭</el-button></template>
    </el-dialog>

    <el-dialog v-model="showCreate" title="新建部署" width="520px">
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="模型">
          <el-select v-model="createForm.model_id" placeholder="请选择有可用权重的模型" style="width:100%" @change="syncLatestVersion">
            <el-option v-for="model in models" :key="model.id" :label="`${model.name} (#${model.id})`" :value="model.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="版本">
          <el-input :model-value="selectedModelVersion || '自动使用最新版本'" disabled />
        </el-form-item>
        <el-form-item label="运行环境">
          <el-select v-model="createForm.python_path" placeholder="默认（后端 Python）" clearable style="width:100%">
            <el-option label="后端默认（/opt/venv, numpy 1.24 + torch 2.1）" value="" />
            <el-option v-for="env in sandboxEnvs" :key="env.path" :label="env.label" :value="env.path" />
          </el-select>
          <div style="font-size:12px;color:#909399;margin-top:4px">沙箱训练的模型选择训练时用的环境；内置算法选默认</div>
        </el-form-item>
        <el-alert title="创建后将生成该模型版本的推理服务" type="info" :closable="false" show-icon />
      </el-form>
      <template #footer>
        <el-button @click="showCreate=false">取消</el-button>
        <el-button type="primary" @click="createDeployment" :loading="creating">创建</el-button>
      </template>
    </el-dialog>

    <!-- 打包对话框 -->
    <el-dialog v-model="showPack" title="📦 生成部署制品" width="650px">
      <el-form label-width="100px">
        <el-form-item label="部署ID"><el-tag>{{ packTarget?.id }}</el-tag></el-form-item>
        <el-form-item label="打包格式">
          <el-radio-group v-model="packFormat">
            <el-radio value="docker">🐳 Docker构建包</el-radio>
            <el-radio value="onnx">🔷 ONNX模型</el-radio>
            <el-radio value="pkl">📦 PKL模型文件</el-radio>
            <el-radio value="exe" disabled>💻 Windows EXE（未配置构建节点）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-alert :title="packFormatNotice" type="info" :closable="false" show-icon style="margin-bottom:14px" />
        <el-form-item label="生成结果" v-if="packResult">
          <pre class="pack-code">{{ packResult }}</pre>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPack=false">关闭</el-button>
        <el-button type="primary" @click="doPack" :loading="packing">生成并保存</el-button>
        <el-button v-if="savedArtifact" @click="openArtifactValidation">验证打包文件</el-button>
        <el-button v-if="savedArtifact" type="success" @click="downloadPack">⬇ 下载文件</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showArtifactValidation" title="打包文件验证" width="760px" destroy-on-close>
      <template v-if="savedArtifact">
        <el-alert
          title="此操作会真实加载本次生成的打包文件，并以输入数据执行推理；通过后才说明该文件可用于后续使用。"
          type="info" :closable="false" show-icon
        />
        <el-descriptions :column="3" border size="small" class="artifact-target">
          <el-descriptions-item label="制品ID">#{{ savedArtifact.id }}</el-descriptions-item>
          <el-descriptions-item label="格式">{{ String(savedArtifact.format || '').toUpperCase() }}</el-descriptions-item>
          <el-descriptions-item label="文件">{{ savedArtifact.file_name }}</el-descriptions-item>
        </el-descriptions>
        <el-form label-width="95px">
          <el-form-item label="重复次数">
            <el-input-number v-model="artifactValidationForm.n_runs" :min="1" :max="10000" />
            <span class="validation-tip">同一批输入重复执行，用于统计耗时和吞吐量</span>
          </el-form-item>
          <el-form-item label="测试数据" required>
            <div style="width:100%">
              <el-input v-model="artifactValidationForm.inputText" type="textarea" :rows="8" placeholder='例如：[[1, 2, 3]]；需要字段名的模型可填：[{"特征A": 1, "特征时间": "2026-01-01"}]' />
              <div class="validation-tip">请填写 JSON 数组。测试数据需符合该模型训练时所需的输入特征。</div>
            </div>
          </el-form-item>
        </el-form>
        <div v-if="artifactValidationResult" class="validation-result">
          <el-result icon="success" title="打包文件验证通过" :sub-title="`${artifactValidationResult.n_samples} 个样本，重复 ${artifactValidationResult.n_runs} 次`" />
          <el-row :gutter="12">
            <el-col :span="6"><el-card><el-statistic title="平均耗时" :value="artifactValidationResult.avg_ms" suffix="ms" /></el-card></el-col>
            <el-col :span="6"><el-card><el-statistic title="P50 延迟" :value="artifactValidationResult.p50_ms" suffix="ms" /></el-card></el-col>
            <el-col :span="6"><el-card><el-statistic title="P99 延迟" :value="artifactValidationResult.p99_ms" suffix="ms" /></el-card></el-col>
            <el-col :span="6"><el-card><el-statistic title="吞吐量" :value="artifactValidationResult.throughput" suffix="样本/秒" /></el-card></el-col>
          </el-row>
          <el-card shadow="never" class="validation-predictions"><template #header>预测结果</template><pre>{{ JSON.stringify(artifactValidationResult.predictions, null, 2) }}</pre></el-card>
        </div>
      </template>
      <template #footer>
        <el-button @click="showArtifactValidation=false">关闭</el-button>
        <el-button type="primary" :loading="artifactValidating" @click="runArtifactValidation">开始验证</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, del } from '@/api/legacy'
import http from '@/api/request'
import PageHeader from '@/components/PageHeader.vue'

const deployments = ref<any[]>([])
const models = ref<any[]>([])
const selectedDep = ref<any>(null)
const showDepDetail = ref(false)
const showCreate = ref(false)
const creating = ref(false)
const createForm = reactive({ model_id: null as number | null, model_version_id: null as number | null, python_path: '' })
const sandboxEnvs = ref<{label:string,path:string}[]>([])

// 文件夹分组 + 内联回退
const groups = ref<any[]>([])           // /models/grouped-by-folder
const expandedFolderKeys = ref<string[]>([])
const switchingFolderKey = ref<string | null>(null)
const rollbackSelect = ref<Record<number, number | null>>({})
const selectedModelVersion = computed(() => {
  const model = models.value.find(item => item.id === createForm.model_id)
  return model?.latest_version?.version_number || ''
})
const normalStatuses = ['running', 'healthy', 'active', 'deployed', 'serving']
const healthyCount = computed(() => deployments.value.filter(d => normalStatuses.includes(String(d.status || '').toLowerCase())).length)
const stoppedCount = computed(() => deployments.value.filter(d => ['stopped', 'cancelled'].includes(String(d.status || '').toLowerCase())).length)
const abnormalCount = computed(() => deployments.value.length - healthyCount.value - stoppedCount.value)

function statusLabel(status: string) {
  const value = String(status || 'running').toLowerCase()
  const labels: Record<string, string> = {
    running: '运行中', healthy: '运行正常', active: '运行中', deployed: '已部署',
    serving: '服务中', stopped: '已停止', cancelled: '已停止',
    unhealthy: '运行异常', failed: '部署失败', error: '运行异常'
  }
  return labels[value] || status || '运行中'
}

function statusTag(status: string) {
  const value = String(status || 'running').toLowerCase()
  if (normalStatuses.includes(value)) return 'success'
  if (['stopped', 'cancelled'].includes(value)) return 'info'
  return 'danger'
}

async function loadAll() {
  // 页面当前只展示已有部署与可切换模型。新建部署入口已隐藏，因而不再在首屏
  // 探测所有沙箱 Python 环境（每个环境导入 numpy/torch 会阻塞数秒）。
  // 两份展示数据彼此独立，必须并行请求，避免将网络等待时间相加。
  const [deploymentResult, groupResult] = await Promise.allSettled([
    get('/deployments'),
    http.get('/models/grouped-by-folder'),
  ])
  deployments.value = deploymentResult.status === 'fulfilled'
    ? ((deploymentResult.value as any).items || deploymentResult.value || [])
    : []
  groups.value = groupResult.status === 'fulfilled'
    ? ((groupResult.value as any) || [])
    : []
  expandedFolderKeys.value = deploymentFolders.value.map(folder => folder.key)
}

function syncLatestVersion(modelId: number) {
  const model = models.value.find(item => item.id === modelId)
  createForm.model_version_id = model?.latest_version?.id || null
}

function selectDep(row: any) { selectedDep.value = row }
function openDepDetail(row: any) { selectedDep.value = row; showDepDetail.value = true }

function endpointUrl(row: any) {
  const path = row?.endpoint || (row?.id ? `/deployments/${row.id}/predict` : '')
  if (!path) return '-'
  const apiPath = path.startsWith('/api/') ? path : `/api/v1${path}`
  return `${window.location.origin}${apiPath}`
}

async function copyEndpoint(row: any) {
  try {
    await navigator.clipboard.writeText(endpointUrl(row))
    ElMessage.success('推理地址已复制')
  } catch {
    ElMessage.error('复制失败，请手动复制地址')
  }
}

async function createDeployment() {
  if (!createForm.model_id || !createForm.model_version_id) {
    ElMessage.warning('请选择已有模型版本')
    return
  }
  creating.value = true
  try {
    await post('/deployments', { ...createForm, strategy: 'rolling' })
    ElMessage.success('部署已创建')
    showCreate.value = false
    loadAll()
  } catch (error: any) {
    ElMessage.error('创建失败: ' + (error?.message || '未知错误'))
  }
  finally { creating.value = false }
}

async function removeDep(row: any) {
  try { await del('/deployments/'+row.id); ElMessage.success('已删除'); loadAll() }
  catch { ElMessage.error('删除失败') }
}
// 当前部署模型在同一模型文件夹中的其它候选模型。
function siblingsOf(modelId: number): any[] {
  for (const group of groups.value) {
    if (group.folder_id !== null && Array.isArray(group.models) && group.models.some((model: any) => model.id === modelId)) {
      return group.models.filter((model: any) => model.id !== modelId)
    }
  }
  return []
}
function candidateLabel(m: any): string {
  const met = m.metrics || {}
  const mk = ['r2','accuracy','f1','rmse','mae'].find(k => typeof met[k] === 'number')
    || Object.keys(met).find(k => typeof met[k] === 'number')
  const pm = mk ? ` · ${mk}=${typeof met[mk] === 'number' ? (met[mk] as number).toFixed(4) : met[mk]}` : ''
  const d = m.created_at ? ' · ' + String(m.created_at).replace('T',' ').slice(0,10) : ''
  return `#${m.id} ${m.name}${pm}${d}`
}
// 部署所属的算法文件夹名(未归类→"未分类")
function folderNameOf(modelId: number): string {
  for (const group of groups.value) {
    if (Array.isArray(group.models) && group.models.some((model: any) => model.id === modelId)) return group.folder_name || '未分类'
  }
  return '未分类'
}
const deploymentFolders = computed(() => {
  const buckets = new Map<string, { key: string; name: string; deployments: any[]; versions: any[]; front: any | null }>()
  for (const deployment of deployments.value) {
    const group = groups.value.find((item: any) => Array.isArray(item.models) && item.models.some((model: any) => model.id === deployment.model_id))
    const folderId = group?.folder_id ?? null
    const key = folderId === null ? 'unclassified' : `folder-${folderId}`
    if (!buckets.has(key)) buckets.set(key, { key, name: group?.folder_name || '未分类', deployments: [], versions: group?.models || [], front: null })
    buckets.get(key)!.deployments.push(deployment)
  }
  for (const folder of buckets.values()) {
    const active = folder.deployments.filter(item => normalStatuses.includes(String(item.status || '').toLowerCase()))
    const candidates = active.length ? active : folder.deployments
    folder.front = [...candidates].sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')) || Number(b.id) - Number(a.id))[0] || null
  }
  return [...buckets.values()].sort((a, b) => a.key === 'unclassified' ? 1 : b.key === 'unclassified' ? -1 : a.name.localeCompare(b.name, 'zh-CN'))
})
// 内联回退: 选同夹兄弟版本 -> redeploy
function candidateMetric(model: any): string {
  const metrics = model?.metrics || {}
  const key = ['r2', 'accuracy', 'f1', 'rmse', 'mae'].find(item => typeof metrics[item] === 'number') || Object.keys(metrics).find(item => typeof metrics[item] === 'number')
  return key ? `${key}=${Number(metrics[key]).toFixed(4)}` : '-'
}
function formatDate(value?: string): string { return value ? String(value).replace('T', ' ').slice(0, 10) : '-' }

async function setFolderFront(folder: any, model: any) {
  if (!folder.front) return ElMessage.warning('该文件夹没有可切换的部署端点')
  switchingFolderKey.value = folder.key
  try {
    await http.put(`/deployments/${folder.front.id}/redeploy`, { model_id: model.id })
    ElMessage.success(`已将「${model.name}」设为 ${folder.name} 的前台版本`)
    await loadAll()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '切换前台版本失败')
  } finally { switchingFolderKey.value = null }
}

async function inlineRollback(row: any, modelId: number) {
  if (!modelId) return
  try {
    await http.put(`/deployments/${row.id}/redeploy`, { model_id: modelId })
    ElMessage.success('已回退到所选版本')
    rollbackSelect.value[row.id] = null
    loadAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '回退失败')
    rollbackSelect.value[row.id] = null
  }
}

onMounted(loadAll)

// --- Packaging ---
const showPack = ref(false)
const packTarget = ref<any>(null)
const packFormat = ref('docker')
const packResult = ref('')
const savedArtifact = ref<any>(null)
const packing = ref(false)
const showArtifactValidation = ref(false)
const artifactValidating = ref(false)
const artifactValidationResult = ref<any>(null)
const artifactValidationForm = reactive({ n_runs: 100, inputText: '' })
const packFormatNotice = computed(() => ({
  docker: '生成可直接执行 docker build 的完整构建包：标准模型使用ONNX运行时，含自定义特征处理的脚本模型会保留PKL、配套源码及锁定依赖。',
  onnx: '生成标准ONNX模型；生成后可直接在本窗口验证加载与推理性能。',
  pkl: '下载部署所选版本的PKL。该文件需要兼容的Python依赖，PyTorch模型还依赖数字化底座运行类。',
  exe: '真正的Windows EXE必须由Windows x64构建节点生成，当前Linux服务器不提供伪EXE。',
} as Record<string,string>)[packFormat.value] || '')

function openPack(row: any) {
  packTarget.value = row
  packFormat.value = 'docker'
  packResult.value = ''
  savedArtifact.value = null
  showPack.value = true
}

async function doPack() {
  if (!packTarget.value) return
  packing.value = true
  try {
    const r = await post('/model-artifacts', {
      model_id: packTarget.value.model_id,
      format: packFormat.value,
    })
    savedArtifact.value = r
    packResult.value = '# 制品已保存\n制品ID: ' + r.id
      + '\n格式: ' + String(r.format || '').toUpperCase()
      + '\n文件: ' + r.file_name
      + '\n大小: ' + r.size_mb + ' MB'
      + '\n基础校验: ' + (r.validation_status === 'valid' ? '通过' : '未通过')
      + '\n\n可点击“验证打包文件”进行加载与性能测试。'
    ElMessage.success('制品已生成，可直接验证或下载')
  } catch (e: any) {
    ElMessage.error('打包失败: ' + (e?.message || ''))
  }
  finally { packing.value = false }
}

function openArtifactValidation() {
  if (!savedArtifact.value) return
  artifactValidationForm.n_runs = 100
  artifactValidationForm.inputText = ''
  artifactValidationResult.value = null
  showArtifactValidation.value = true
}

async function runArtifactValidation() {
  if (!savedArtifact.value) return
  let X: any
  try { X = JSON.parse(artifactValidationForm.inputText) }
  catch { return ElMessage.warning('测试数据不是有效的 JSON 数组') }
  artifactValidating.value = true
  try {
    artifactValidationResult.value = await post(`/model-artifacts/${savedArtifact.value.id}/validate`, {
      X,
      n_runs: artifactValidationForm.n_runs,
    })
    ElMessage.success('打包文件验证通过')
  } catch (e: any) {
    ElMessage.error('验证失败: ' + (e?.message || ''))
  } finally { artifactValidating.value = false }
}

async function downloadPack() {
  if (!savedArtifact.value) return
  try {
    const token=localStorage.getItem('foundation_token')||localStorage.getItem('foundation_access_token')
    const response=await fetch(`/api/v1/model-artifacts/${savedArtifact.value.id}/download`,{headers:token?{Authorization:'Bearer '+token}:{}})
    if(!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail || '下载失败：'+response.status)
    }
    const anchor=document.createElement('a'); anchor.href=URL.createObjectURL(await response.blob())
    anchor.download=savedArtifact.value.file_name; anchor.click(); URL.revokeObjectURL(anchor.href)
  } catch(e:any) { ElMessage.error(e?.message||'下载失败') }
}

</script>

<style scoped>
.deploy-page { max-width: 1300px; padding: 20px; }
.stats { margin-bottom: 16px; }
.sv { font-size: 28px; font-weight: 700; text-align: center; }
.sv.ok { color: #67C23A; } .sv.warn { color: #E6A23C; }
.sl { font-size: 12px; color: #909399; margin-top: 4px; text-align: center; }
.section { margin-bottom: 16px; }
.deployment-record-title { margin: 16px 0 8px; color: #475569; font-size: 13px; font-weight: 600; }
.artifact-target { margin: 16px 0; }
.validation-tip { margin-left: 10px; color: #909399; font-size: 12px; line-height: 22px; }
.validation-result { margin-top: 16px; }
.validation-predictions { margin-top: 14px; }
.validation-predictions pre { max-height: 240px; overflow: auto; white-space: pre-wrap; }
</style>
