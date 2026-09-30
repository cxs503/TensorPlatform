<template>
  <div class="page-root">
    <div class="page-header">
      <div>
        <h2>📦 模型管理</h2>
        <div class="desc">
          按模型分类文件夹组织训练产物：查看 / 对比 / 部署 / Stage / 导出 / 删除 ·
          {{ folderCount }} 个模型分类 / {{ totalGrouped }} 个已归类模型 / 共 {{ totalModels }} 个
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <el-button type="primary" @click="openCreateFolder">新建文件夹</el-button>
        <el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <!-- 左：模型分类文件夹 -->
      <el-col :xs="24" :md="6">
        <el-card shadow="never" class="group-card" v-loading="loading">
          <template #header><span>模型分类文件夹</span></template>
          <div class="group-list">
            <div
              v-for="g in groups"
              :key="String(g.folder_id)"
              class="group-item"
              :class="{ active: selectedGroup === g, ungrouped: g.folder_id === null }"
              @click="selectGroup(g)"
            >
              <div class="gi-name">
                <span class="gi-title">{{ g.folder_name }}</span>
                <el-tag v-if="g.folder_description" size="small" effect="plain">{{ g.folder_description }}</el-tag>
              </div>
              <span class="gi-count">{{ g.model_count }} 个版本</span>
            </div>
            <el-empty v-if="!groups.length && !loading" description="暂无模型" :image-size="60" />
          </div>
        </el-card>
      </el-col>

      <!-- 右：选中文件夹的版本下拉 + 详情 + 对比 -->
      <el-col :xs="24" :md="18">
        <template v-if="selectedGroup">
          <el-card shadow="never" class="main-card">
            <template #header>
              <div class="main-head">
                <span class="main-title">{{ selectedGroup.folder_name }} · {{ selectedGroup.model_count }} 个版本</span>
                <el-button v-if="selectedGroup.folder_id !== null" size="small" type="danger" plain @click="deleteSelectedFolder">删除分类</el-button>
                <el-select
                  v-model="selectedModelId"
                  filterable
                  placeholder="下拉选择该文件夹下的版本"
                  style="width: 460px"
                >
                  <el-option v-for="m in selectedGroup.models" :key="m.id" :value="m.id" :label="modelLabel(m)" />
                </el-select>
              </div>
            </template>

            <el-descriptions v-if="selectedModel" :column="3" border size="small" class="detail">
              <el-descriptions-item label="分类版本"><el-tag size="small" type="primary">v{{ selectedModel.folder_version || 1 }}</el-tag></el-descriptions-item>
              <el-descriptions-item label="ID">#{{ selectedModel.id }}</el-descriptions-item>
              <el-descriptions-item label="名称">{{ selectedModel.name }}</el-descriptions-item>
              <el-descriptions-item label="Stage">
                <el-tag size="small" :type="stageTag(selectedModel.stage)">{{ selectedModel.stage || 'development' }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="框架">{{ selectedModel.framework || '-' }}</el-descriptions-item>
              <el-descriptions-item label="类型">{{ selectedModel.model_type || '-' }}</el-descriptions-item>
              <el-descriptions-item label="创建">{{ fmtDate(selectedModel.created_at) }}</el-descriptions-item>
              <el-descriptions-item label="部署">
                <el-tag v-if="isDeployed(selectedModel.id)" type="success" size="small">已部署</el-tag>
                <span v-else style="color: #c0c4cc">未部署</span>
              </el-descriptions-item>
              <el-descriptions-item label="MLflow Run" :span="3">
                <code>{{ selectedModel.mlflow_run_id || '-' }}</code>
              </el-descriptions-item>
              <el-descriptions-item label="指标" :span="3">
                <el-tag v-for="(v, k) in (selectedModel.metrics || {})" :key="k" size="small" style="margin: 2px">
                  {{ k }}: {{ fmtNum(v) }}
                </el-tag>
                <span v-if="!selectedModel.metrics || !Object.keys(selectedModel.metrics).length" style="color: #c0c4cc">-</span>
              </el-descriptions-item>
            </el-descriptions>

            <div class="actions">
              <el-button size="small" type="primary" :disabled="!canAct(selectedModel)" @click="openDeployPopup">🚀 部署</el-button>
              <el-button size="small" :disabled="!canPromote(selectedModel)" @click="promoteStage">⬆ Stage 晋升</el-button>
              <el-button size="small" :disabled="!canAct(selectedModel)" @click="openArtifacts">📦 导出制品</el-button>
              <el-button size="small" :disabled="!selectedModel" @click="openMoveFolder">📁 归入文件夹</el-button>
              <el-button size="small" type="danger" plain @click="deleteModel">删除</el-button>
              <el-button size="small" link @click="gotoDeploy">去部署管理</el-button>
              <el-button size="small" link @click="gotoMlflow">MLflow 追踪</el-button>
            </div>
          </el-card>

          <!-- 组内对比 -->
          <el-card shadow="never" class="compare-card">
            <template #header>
              <div class="compare-head">
                <span>📊 组内对比（高亮各指标最优）</span>
                <el-button size="small" link type="primary" @click="gotoCompare">打开模型对比 →</el-button>
              </div>
            </template>
            <el-table
              :data="selectedGroup.models"
              size="small"
              max-height="380"
              stripe
              :row-class-name="({ row }) => (row.id === selectedModelId ? 'cur-row' : '')"
              @row-click="(row: any) => (selectedModelId = row.id)"
            >
              <el-table-column label="版本" width="72"><template #default="{ row }"><el-tag size="small" type="primary" effect="plain">v{{ row.folder_version || 1 }}</el-tag></template></el-table-column>
              <el-table-column prop="id" label="ID" width="64" />
              <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
              <el-table-column prop="framework" label="框架" width="90" />
              <el-table-column label="Stage" width="110">
                <template #default="{ row }">
                  <el-tag size="small" :type="stageTag(row.stage)">{{ row.stage || 'development' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="创建" width="120">
                <template #default="{ row }">{{ fmtDate(row.created_at).slice(0, 10) }}</template>
              </el-table-column>
              <el-table-column label="部署" width="80">
                <template #default="{ row }">
                  <el-tag v-if="isDeployed(row.id)" type="success" size="small">已部署</el-tag>
                  <span v-else style="color: #c0c4cc">-</span>
                </template>
              </el-table-column>
              <el-table-column v-for="k in metricKeys" :key="k" :label="k" width="110">
                <template #default="{ row }">
                  <span :class="{ best: isBest(row, k) }">{{ fmtNum(row.metrics?.[k]) }}</span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </template>

        <el-card v-else shadow="never">
          <el-empty description="选择左侧模型分类，查看其中的训练模型" />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="showFolderDialog" title="新建模型文件夹" width="460px" destroy-on-close>
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="分类名称" required>
          <el-input v-model="folderForm.name" maxlength="128" show-word-limit placeholder="例如：生产设备温度预测、工艺参数优化优化" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="folderForm.description" type="textarea" :rows="3" maxlength="300" show-word-limit placeholder="可选，用于说明该模型分类的业务用途" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showFolderDialog=false">取消</el-button>
        <el-button type="primary" :loading="creatingFolder" @click="createFolder">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showMoveFolder" title="归入模型分类" width="460px" destroy-on-close>
      <el-form label-width="82px">
        <el-form-item label="模型"><span v-if="selectedModel">#{{ selectedModel.id }} {{ selectedModel.name }}</span></el-form-item>
        <el-form-item label="目标文件夹" required>
          <el-select v-model="moveFolderId" filterable placeholder="请选择模型分类" style="width:100%">
            <el-option v-for="folder in selectableFolders" :key="folder.folder_id" :label="folder.folder_name" :value="folder.folder_id" />
          </el-select>
          <div class="el-upload__tip">此操作只调整模型管理分类，不影响训练、模型版本或部署。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMoveFolder=false">取消</el-button>
        <el-button type="primary" :loading="movingFolder" :disabled="moveFolderId === null" @click="moveModelToFolder">确认归类</el-button>
      </template>
    </el-dialog>

    <!-- 部署弹窗：选模型分类文件夹 + 选版本 -->
    <el-dialog v-model="showDeploy" title="🚀 部署模型" width="520px">
      <el-form label-width="80px">
        <el-form-item label="文件夹">
          <el-select v-model="deployFolderId" style="width:100%" @change="onDeployFolderChange">
            <el-option v-for="g in groups" :key="String(g.folder_id)" :value="g.folder_id" :label="g.folder_name" />
          </el-select>
          <div style="font-size:12px;color:#909399;margin-top:4px">模型分类仅用于整理资产；部署始终只针对当前选定的模型版本。</div>
        </el-form-item>
        <el-form-item label="版本">
          <el-select v-model="deployModelId" filterable style="width:100%">
            <el-option v-for="m in deployFolderModels" :key="m.id" :value="m.id" :label="modelLabel(m)" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDeploy = false">取消</el-button>
        <el-button type="primary" :loading="deploying" :disabled="!deployModelId" @click="confirmDeploy">部署</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { modelGroupApi } from '@/api/model'
import http from '@/api/request'

interface GroupModel {
  id: number
  name: string
  folder_version?: number
  model_type?: string
  framework?: string
  stage?: string
  created_at?: string
  metrics?: Record<string, number | string> | null
  mlflow_run_id?: string | null
}
interface Group {
  folder_id: number | null
  folder_name: string
  folder_description?: string
  model_count: number
  models: GroupModel[]
}

const router = useRouter()
const loading = ref(false)
const groups = ref<Group[]>([])
const selectedGroup = ref<Group | null>(null)
const selectedModelId = ref<number | null>(null)
const showFolderDialog = ref(false)
const creatingFolder = ref(false)
const folderForm = ref({ name: '', description: '' })
const showMoveFolder = ref(false)
const movingFolder = ref(false)
const moveFolderId = ref<number | null>(null)

// 部署状态: model_id -> 是否有活跃部署 / 对应 deployment_id(用于"一文件夹一部署"切换)
const deployedModelIds = ref<Set<number>>(new Set())
const depIdByModelId = ref<Map<number, number>>(new Map())
const deploying = ref(false)
function isDeployed(id: number) { return deployedModelIds.value.has(id) }

// Stage
const stages = ['development', 'staging', 'production', 'archived']
function getLevel(s?: string) { return stages.indexOf(s || 'development') }
type TagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'
function stageTag(s?: string): TagType {
  return ({ development: 'info', staging: 'warning', production: 'success', archived: 'danger' } as Record<string, TagType>)[s || 'development'] || 'info'
}
function canAct(m?: GroupModel | null) { return !!m && (m.stage || 'development') !== 'archived' }
function canPromote(m?: GroupModel | null) { return !!m && getLevel(m.stage) < stages.length - 1 }

// 指标方向：>0 越大越好，<0 越小越好，0 未知
const HIGHER_BETTER = new Set(['r2', 'r2_score', 'accuracy', 'f1', 'f1_score', 'precision', 'recall', 'auc', 'rocauc'])
const LOWER_BETTER = new Set(['rmse', 'mae', 'mse', 'mape', 'rmsle', 'loss', 'error'])
function metricDirection(key: string): number {
  const k = String(key).toLowerCase()
  if (HIGHER_BETTER.has(k)) return 1
  if (LOWER_BETTER.has(k)) return -1
  return 0
}

const folderCount = computed(() => groups.value.filter(g => g.folder_id !== null).length)
const totalGrouped = computed(() => groups.value.filter(g => g.folder_id !== null).reduce((a, g) => a + g.model_count, 0))
const totalModels = computed(() => groups.value.reduce((a, g) => a + g.model_count, 0))
const selectableFolders = computed(() => groups.value.filter(group => group.folder_id !== null))

const metricKeys = computed<string[]>(() => {
  const s = new Set<string>()
  for (const m of selectedGroup.value?.models || []) for (const k of Object.keys(m.metrics || {})) s.add(k)
  return [...s].slice(0, 8)
})

function bestValue(key: string): number | undefined {
  const dir = metricDirection(key)
  let best: number | undefined
  for (const m of selectedGroup.value?.models || []) {
    const v = m.metrics?.[key]
    if (typeof v !== 'number') continue
    if (best === undefined) best = v
    else if (dir >= 0 ? v > best : v < best) best = v
  }
  return best
}
function isBest(row: GroupModel, key: string): boolean {
  const v = row.metrics?.[key]
  const b = bestValue(key)
  return typeof v === 'number' && b !== undefined && v === b
}

const selectedModel = computed(() => selectedGroup.value?.models.find(m => m.id === selectedModelId.value) || null)

function fmtNum(v: unknown): string {
  if (typeof v === 'number') return Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(4)
  return v == null ? '-' : String(v)
}
function fmtDate(s?: string): string { return s ? s.replace('T', ' ').slice(0, 19) : '-' }
function primaryMetric(m: GroupModel): string {
  const met = m.metrics || {}
  for (const k of ['r2', 'accuracy', 'f1', 'rmse', 'mae']) if (typeof met[k] === 'number') return `${k}=${fmtNum(met[k])}`
  const first = Object.entries(met).find(([, v]) => typeof v === 'number')
  return first ? `${first[0]}=${fmtNum(first[1])}` : ''
}
function modelLabel(m: GroupModel): string {
  const pm = primaryMetric(m)
  const d = fmtDate(m.created_at).slice(0, 10)
  return `v${m.folder_version || 1} · #${m.id} ${m.name}${pm ? ' · ' + pm : ''}${d ? ' · ' + d : ''}`
}

async function loadDeployments() {
  try {
    const r: any = await http.get('/deployments')
    const list = r?.items || r || []
    const ids = new Set<number>()
    const map = new Map<number, number>()
    for (const d of list) {
      if (d?.model_id != null && d?.id != null) { ids.add(d.model_id); if (!map.has(d.model_id)) map.set(d.model_id, d.id) }
    }
    deployedModelIds.value = ids
    depIdByModelId.value = map
  } catch { /* 部署信息非关键, 忽略 */ }
}

async function load() {
  loading.value = true
  try {
    const [g] = await Promise.all([modelGroupApi.getGrouped(), loadDeployments()])
    groups.value = g
    if (groups.value.length) selectGroup(groups.value[0])
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '加载模型分组失败')
  } finally { loading.value = false }
}

// 刷新分组但尽量保持当前选中文件夹/版本(删除/晋升后用)
async function refreshKeepingSelection() {
  try {
    const prevFolder = selectedGroup.value?.folder_id
    const prevModel = selectedModelId.value
    const [g] = await Promise.all([modelGroupApi.getGrouped(), loadDeployments()])
    groups.value = g
    const same = prevFolder !== undefined ? groups.value.find(x => x.folder_id === prevFolder) : null
    if (same) {
      selectedGroup.value = same
      selectedModelId.value = same.models.some(m => m.id === prevModel) ? prevModel : (same.models[0]?.id ?? null)
    } else if (groups.value.length) selectGroup(groups.value[0])
  } catch { /* ignore */ }
}

function openCreateFolder() {
  folderForm.value = { name: '', description: '' }
  showFolderDialog.value = true
}

async function createFolder() {
  const name = folderForm.value.name.trim()
  if (!name) { ElMessage.warning('请输入模型分类名称'); return }
  creatingFolder.value = true
  try {
    const folder = await modelGroupApi.createFolder({ name, description: folderForm.value.description.trim() })
    showFolderDialog.value = false
    await refreshKeepingSelection()
    const created = groups.value.find(group => group.folder_id === folder?.id)
    if (created) selectGroup(created)
    ElMessage.success(`模型分类「${name}」已创建`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '创建模型分类失败')
  } finally { creatingFolder.value = false }
}

async function deleteSelectedFolder() {
  const folder = selectedGroup.value
  if (!folder || folder.folder_id === null) return
  try {
    await ElMessageBox.confirm(
      '确定删除模型分类「' + folder.folder_name + '」吗？其中 ' + folder.model_count + ' 个模型会移至“未分类”，模型、版本和部署均不会删除。',
      '删除模型分类', { type: 'warning' },
    )
    const result: any = await modelGroupApi.deleteFolder(folder.folder_id)
    ElMessage.success(result?.message || '模型分类已删除')
    await refreshKeepingSelection()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || error?.message || '删除失败')
  }
}

function openMoveFolder() {
  if (!selectedModel.value) return
  if (!selectableFolders.value.length) {
    ElMessage.warning('请先新建一个模型文件夹')
    return
  }
  moveFolderId.value = selectedGroup.value?.folder_id ?? null
  showMoveFolder.value = true
}

async function moveModelToFolder() {
  const model = selectedModel.value
  if (!model || moveFolderId.value === null) return
  movingFolder.value = true
  try {
    await http.put(`/models/${model.id}`, { folder_id: moveFolderId.value })
    const targetFolderId = moveFolderId.value
    showMoveFolder.value = false
    await refreshKeepingSelection()
    const targetGroup = groups.value.find(group => group.folder_id === targetFolderId)
    if (targetGroup) {
      selectedGroup.value = targetGroup
      selectedModelId.value = model.id
    }
    ElMessage.success(`模型 #${model.id} 已归入所选文件夹`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '模型归类失败')
  } finally { movingFolder.value = false }
}

function selectGroup(g: Group) {
  selectedGroup.value = g
  selectedModelId.value = g.models[0]?.id ?? null
}

// ── 部署弹窗 ──
const showDeploy = ref(false)
const deployFolderId = ref<number | null | undefined>(undefined)
const deployModelId = ref<number | null>(null)
const deployFolderModels = computed(() => groups.value.find(g => g.folder_id === deployFolderId.value)?.models || [])
function openDeployPopup() {
  deployFolderId.value = selectedGroup.value?.folder_id ?? null
  deployModelId.value = selectedModelId.value
  showDeploy.value = true
}
function onDeployFolderChange() { deployModelId.value = deployFolderModels.value[0]?.id ?? null }
async function confirmDeploy() {
  if (!deployModelId.value) { ElMessage.warning('请选择版本'); return }
  const existingDeploymentId = depIdByModelId.value.get(deployModelId.value)
  deploying.value = true
  try {
    if (existingDeploymentId) {
      await http.put(`/deployments/${existingDeploymentId}/redeploy`, { model_id: deployModelId.value })
      ElMessage.success('已重新部署所选模型版本')
    } else {
      await http.post('/deployments', { model_id: deployModelId.value, strategy: 'rolling' })
      ElMessage.success('模型已部署')
    }
    showDeploy.value = false
    await loadDeployments()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '部署失败')
  } finally { deploying.value = false }
}

// ── Stage 晋升 / 导出 / 删除(从 Models.vue 搬入, 用 stage 判断可用性) ──
async function promoteStage() {
  const m = selectedModel.value; if (!m || !canPromote(m)) return
  const next = stages[getLevel(m.stage) + 1]
  try {
    await http.put('/models/' + m.id, { stage: next })
    ElMessage.success('已晋升到 ' + next); m.stage = next
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '操作失败') }
}
function openArtifacts() {
  const m = selectedModel.value; if (!m) return
  router.push({ path: '/deploy/artifacts', query: { model_id: m.id } })
}
async function deleteModel() {
  const m = selectedModel.value; if (!m) return
  try {
    await ElMessageBox.confirm(
      `确定删除模型 #${m.id}「${m.name}」吗？模型版本、本地模型文件和 MLflow 注册项将同步清理，此操作不可恢复。`,
      '删除模型',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning', distinguishCancelAndClose: true },
    )
  } catch { return }
  try {
    await http.delete('/models/' + m.id)
    ElMessage.success(`模型 #${m.id} 已删除`)
    await refreshKeepingSelection()
  } catch (e: any) {
    ElMessage.error(`删除失败：${e?.response?.data?.detail || e?.message || '未知错误'}`)
  }
}

function gotoDeploy() { router.push('/deploy/deployment') }
function gotoMlflow() { router.push('/model/mlflow') }
function gotoCompare() { router.push('/model/model-comparison') }

onMounted(load)
</script>

<style scoped>
.page-root { padding: 16px 20px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.page-header h2 { margin: 0 0 4px; font-size: 18px; }
.desc { color: #909399; font-size: 12px; }
.group-card { height: 100%; }
.group-list { display: flex; flex-direction: column; gap: 6px; max-height: 70vh; overflow-y: auto; }
.group-item { border: 1px solid #ebeef5; border-radius: 8px; padding: 8px 10px; cursor: pointer; transition: all 0.2s; }
.group-item:hover { border-color: #409eff; background: #f5f9ff; }
.group-item.active { border-color: #409eff; background: #ecf5ff; box-shadow: 0 0 0 1px #409eff inset; }
.group-item.ungrouped { background: #fafafa; }
.gi-name { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.gi-title { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gi-count { color: #909399; font-size: 12px; }
.main-card { margin-bottom: 14px; }
.main-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.main-title { font-weight: 600; }
.detail { margin-top: 4px; }
.actions { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }
.compare-head { display: flex; justify-content: space-between; align-items: center; }
.best { color: #67c23a; font-weight: 700; }
:deep(.cur-row) { background: #ecf5ff !important; }
</style>
