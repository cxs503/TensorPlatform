<template>
  <div class="environment-manager">
    <el-alert
      title="基础环境定义 Python/CPU/GPU/CUDA；依赖配置只定义包；两者构建成功后才会出现在沙箱环境选择器中。"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom:16px"
    />

    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="基础环境" name="bases">
        <div class="toolbar">
          <span>已安装 Python 3.8、3.10、3.11；CUDA环境在无GPU节点上会标记为不可运行。</span>
          <el-button type="primary" size="small" @click="openBaseDialog">新建基础环境</el-button>
        </div>
        <el-table :data="bases" v-loading="loading" size="small">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="环境名称" min-width="210" />
          <el-table-column label="Python" width="130">
            <template #default="{row}">{{ row.actual_python_version || row.python_version }}</template>
          </el-table-column>
          <el-table-column label="计算资源" width="110">
            <template #default="{row}"><el-tag size="small">{{ String(row.compute_type || 'cpu').toUpperCase() }}</el-tag></template>
          </el-table-column>
          <el-table-column label="CUDA" width="100">
            <template #default="{row}">{{ row.cuda_version || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" min-width="180">
            <template #default="{row}">
              <el-tag :type="row.schedulable ? 'success' : 'warning'" size="small">{{ row.schedulable ? '可构建运行' : '当前节点不可运行' }}</el-tag>
              <div v-if="row.status_reason" class="reason">{{ row.status_reason }}</div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{row}">
              <el-button v-if="!row.system" text size="small" type="danger" @click="removeBase(row)">删除</el-button>
              <el-tag v-else size="small" type="info">平台预置</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="依赖配置" name="dependencies">
        <div class="toolbar">
          <span>新建时即可选择 Python 基础环境并提前安装；沙箱运行时不会临时下载。</span>
          <el-button type="primary" size="small" @click="openDependencyDialog()">新建并安装依赖</el-button>
        </div>
        <el-table :data="dependencySets" v-loading="loading" size="small">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="配置名称" min-width="180" />
          <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
          <el-table-column prop="package_count" label="声明包数" width="100" />
          <el-table-column label="依赖内容" min-width="220">
            <template #default="{row}"><code>{{ dependencyPreview(row.requirements) }}</code></template>
          </el-table-column>
          <el-table-column label="安装状态" min-width="250">
            <template #default="{row}">
              <div v-if="dependencyBuilding(row)" class="progress-cell">
                <el-progress :percentage="buildProgress(dependencyBuilding(row))" :stroke-width="8" />
                <div class="progress-stage">{{ dependencyBuilding(row).progress_stage || '正在安装' }}</div>
                <div class="progress-detail">{{ dependencyBuilding(row).progress_detail || '等待安装输出' }}</div>
              </div>
              <el-tag v-else :type="dependencyInstallState(row).type" size="small">{{ dependencyInstallState(row).text }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220">
            <template #default="{row}">
              <template v-if="!row.system">
                <el-button text size="small" @click="openDependencyDialog(row)">编辑</el-button>
                <el-button text size="small" type="primary" @click="openBuildDialog(row)">安装</el-button>
                <el-button text size="small" type="danger" @click="removeDependency(row)">删除</el-button>
              </template>
              <el-tag v-else size="small" type="info">平台预置</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="可运行环境" name="builds">
        <div class="toolbar">
          <span>沙箱只展示这里构建成功并且当前节点可调度的环境。</span>
          <div>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button type="primary" size="small" @click="openBuildDialog">构建运行环境</el-button>
          </div>
        </div>
        <el-table :data="builds" v-loading="loading" size="small">
          <el-table-column prop="id" label="ID" width="75" />
          <el-table-column prop="name" label="运行环境" min-width="230" />
          <el-table-column label="Python" width="105"><template #default="{row}">{{ row.python_version }}</template></el-table-column>
          <el-table-column label="依赖配置" min-width="150"><template #default="{row}">{{ row.dependency_set?.name }}</template></el-table-column>
          <el-table-column prop="package_count" label="已安装包" width="95" />
          <el-table-column label="状态" min-width="270">
            <template #default="{row}">
              <div v-if="row.status === 'building'" class="progress-cell">
                <el-progress :percentage="buildProgress(row)" :stroke-width="8" />
                <div class="progress-stage">{{ row.progress_stage || '正在构建' }}</div>
                <div class="progress-detail">{{ row.progress_detail || '等待构建输出' }}</div>
              </div>
              <template v-else>
                <el-tag :type="buildTag(row)" size="small">{{ buildLabel(row) }}</el-tag>
                <div v-if="row.unavailable_reason" class="reason">{{ row.unavailable_reason }}</div>
              </template>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="290">
            <template #default="{row}">
              <el-button text size="small" @click="showLog(row)">构建日志</el-button>
              <el-button v-if="row.status === 'awaiting_source_approval'" text size="small" type="warning" @click="confirmSourceInstall(row)">确认源码安装</el-button>
              <template v-if="!row.auto_base">
                <el-button text size="small" :disabled="row.status === 'building'" @click="rebuild(row)">重建</el-button>
                <el-button text size="small" type="danger" :disabled="row.status === 'building'" @click="removeBuild(row)">删除</el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="showBase" title="新建基础环境" width="500px" destroy-on-close>
      <el-form label-width="105px">
        <el-form-item label="环境名称" required><el-input v-model="baseForm.name" /></el-form-item>
        <el-form-item label="Python版本" required>
          <el-select v-model="baseForm.python_version" style="width:100%">
            <el-option label="Python 3.8" value="3.8" />
            <el-option label="Python 3.10" value="3.10" />
            <el-option label="Python 3.11" value="3.11" />
          </el-select>
        </el-form-item>
        <el-form-item label="计算类型"><el-radio-group v-model="baseForm.compute_type"><el-radio value="cpu">CPU</el-radio><el-radio value="gpu">GPU</el-radio></el-radio-group></el-form-item>
        <el-form-item v-if="baseForm.compute_type === 'gpu'" label="CUDA版本" required><el-input v-model="baseForm.cuda_version" placeholder="例如 12.1" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showBase=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveBase">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="showDependency" :title="editingDependency ? '编辑依赖配置' : '新建并安装依赖'" width="680px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="配置名称" required><el-input v-model="dependencyForm.name" /></el-form-item>
        <el-form-item label="依赖包">
          <el-input v-model="dependencyForm.requirements" type="textarea" :rows="9" placeholder="每行一个包，例如：&#10;numpy==1.24.4&#10;pandas>=2.0,<3.0&#10;scikit-learn==1.3.0" />
        </el-form-item>
        <el-form-item v-if="specialPackages.length" label="公司特殊包">
          <el-checkbox-group v-model="dependencyForm.special_packages">
            <el-checkbox v-for="item in specialPackages" :key="item.id" :label="item.id">
              <span>{{ item.name }}</span><small class="special-package-help">{{ item.description }}</small>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="dependencyForm.description" type="textarea" :rows="2" /></el-form-item>
        <template v-if="!editingDependency">
          <el-form-item label="立即安装">
            <el-switch v-model="dependencyForm.install_now" active-text="保存后立即构建" />
          </el-form-item>
          <el-form-item v-if="dependencyForm.install_now" label="安装到" required>
            <el-select v-model="dependencyForm.base_environment_ids" multiple collapse-tags collapse-tags-tooltip style="width:100%" placeholder="选择一个或多个 Python 基础环境">
              <el-option
                v-for="row in bases"
                :key="row.id"
                :label="`${row.name} · Python ${row.actual_python_version || row.python_version}${row.schedulable ? '' : '（当前不可运行）'}`"
                :value="row.id"
                :disabled="!row.schedulable"
              />
            </el-select>
          </el-form-item>
        </template>
      </el-form>
      <el-alert title="点击保存并安装后，平台会立即创建隔离环境并下载依赖；不会等到沙箱运行时安装。" type="success" :closable="false" />
      <el-alert title="普通依赖不接受 -r、--index-url、Git地址或任意安装脚本；公司特殊包使用平台审核过的固定私有源，构建成功后会锁定实际版本。" type="warning" :closable="false" style="margin-top:8px" />
      <template #footer><el-button @click="showDependency=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveDependency">{{ editingDependency ? '保存' : dependencyForm.install_now ? '保存并开始安装' : '仅保存清单' }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="showBuild" title="构建可运行环境" width="520px" destroy-on-close>
      <el-form label-width="105px">
        <el-form-item label="环境名称"><el-input v-model="buildForm.name" placeholder="留空则自动生成" /></el-form-item>
        <el-form-item label="基础环境" required>
          <el-select v-model="buildForm.base_environment_id" style="width:100%">
            <el-option v-for="row in bases" :key="row.id" :label="`${row.name}${row.schedulable ? '' : '（当前不可运行）'}`" :value="row.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="依赖配置" required>
          <el-select v-model="buildForm.dependency_set_id" style="width:100%"><el-option v-for="row in dependencySets" :key="row.id" :label="`${row.name}（${row.package_count}项）`" :value="row.id" /></el-select>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="showBuild=false">取消</el-button><el-button type="primary" :loading="saving" @click="submitBuild">开始构建</el-button></template>
    </el-dialog>

    <el-dialog v-model="showBuildLog" title="环境构建日志" width="760px"><pre class="build-log">{{ activeLog }}</pre></el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'

const emit = defineEmits<{ (event: 'ready-change', rows: any[]): void }>()
const activeTab = ref('bases')
const loading = ref(false)
const saving = ref(false)
const bases = ref<any[]>([])
const dependencySets = ref<any[]>([])
const specialPackages = ref<any[]>([])
const builds = ref<any[]>([])
const showBase = ref(false)
const showDependency = ref(false)
const showBuild = ref(false)
const showBuildLog = ref(false)
const activeLog = ref('')
const activeLogBuildId = ref<number | null>(null)
const editingDependency = ref<any>(null)
const baseForm = ref({ name: '', python_version: '3.11', compute_type: 'cpu', cuda_version: '' })
const dependencyForm = ref({ name: '', requirements: '', special_packages: [] as string[], description: '', install_now: true, base_environment_ids: [] as number[] })
const buildForm = ref({ name: '', base_environment_id: null as number | null, dependency_set_id: null as number | null })
let pollTimer: number | null = null

function errorText(error: any) { return error?.response?.data?.detail || error?.message || '未知错误' }
function dependencyPreview(value: string) { return value ? value.split('\n').slice(0, 3).join(', ') : '无额外依赖' }
function dependencyInstallState(row: any) {
  const related = builds.value.filter(item => Number(item.dependency_set_id) === Number(row.id))
  const ready = related.filter(item => item.available).length
  const building = related.filter(item => item.status === 'building').length
  const failed = related.filter(item => item.status === 'failed').length
  if (building) return { type: 'warning', text: `安装中 ${building}` }
  if (ready) return { type: 'success', text: `已安装 ${ready} 套` }
  if (failed) return { type: 'danger', text: `失败 ${failed} 套` }
  return { type: 'info', text: '尚未安装' }
}
function dependencyBuilding(row: any) {
  return builds.value.find(item => Number(item.dependency_set_id) === Number(row.id) && item.status === 'building') || null
}
function buildProgress(row: any) {
  const value = Number(row?.progress || 0)
  return Math.max(0, Math.min(100, Number.isFinite(value) ? Math.round(value) : 0))
}
function buildTag(row: any) { return row.available ? 'success' : row.status === 'failed' ? 'danger' : ['building', 'awaiting_source_approval'].includes(row.status) ? 'warning' : 'info' }
function buildLabel(row: any) { return row.available ? '可用于沙箱' : row.status === 'building' ? '构建中' : row.status === 'awaiting_source_approval' ? '等待源码安装确认' : row.status === 'cancelled' ? '已取消' : row.status === 'failed' ? '构建失败' : row.status === 'ready' ? '当前节点不可运行' : '不可用' }

async function loadAll() {
  loading.value = true
  try {
    const [baseResult, dependencyResult, specialPackageResult, buildResult] = await Promise.all([
      api.algorithmStore.getRuntimeBases(), api.algorithmStore.getDependencySets(),
      api.algorithmStore.getSpecialPackages(), api.algorithmStore.getRuntimeBuilds()
    ])
    bases.value = baseResult.items || []
    dependencySets.value = dependencyResult.items || []
    specialPackages.value = specialPackageResult.items || []
    builds.value = buildResult.items || []
    emit('ready-change', builds.value.filter(item => item.available))
    schedulePoll()
  } catch (error: any) { ElMessage.error('环境配置加载失败: ' + errorText(error)) }
  finally { loading.value = false }
}

function mergeBuildRows(rows: any[]) {
  const current = new Map(builds.value.map(item => [Number(item.id), item]))
  builds.value = rows.map(row => {
    const existing = current.get(Number(row.id))
    if (existing) {
      Object.assign(existing, row)
      return existing
    }
    return row
  })
}

async function refreshBuildProgress() {
  try {
    const result = await api.algorithmStore.getRuntimeBuilds()
    mergeBuildRows(result.items || [])
    emit('ready-change', builds.value.filter(item => item.available))
    if (showBuildLog.value && activeLogBuildId.value !== null) {
      const active = builds.value.find(item => Number(item.id) === activeLogBuildId.value)
      if (active) activeLog.value = active.build_log || '暂无构建日志'
    }
  } catch (error: any) {
    console.warn('构建进度刷新失败:', errorText(error))
  } finally {
    schedulePoll()
  }
}

function schedulePoll() {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = builds.value.some(item => item.status === 'building') ? window.setTimeout(refreshBuildProgress, 1200) : null
}
function openBaseDialog() { baseForm.value = { name: '', python_version: '3.11', compute_type: 'cpu', cuda_version: '' }; showBase.value = true }
async function saveBase() {
  if (!baseForm.value.name.trim()) return ElMessage.warning('请输入基础环境名称')
  saving.value = true
  try { await api.algorithmStore.createRuntimeBase(baseForm.value); showBase.value = false; ElMessage.success('基础环境已保存'); await loadAll() }
  catch (error: any) { ElMessage.error('保存失败: ' + errorText(error)) }
  finally { saving.value = false }
}
async function removeBase(row: any) {
  try { await ElMessageBox.confirm(`删除基础环境“${row.name}”？`, '确认删除', {type:'warning'}); await api.algorithmStore.deleteRuntimeBase(row.id); await loadAll() }
  catch (error: any) { if (error !== 'cancel' && error?.message) ElMessage.error(errorText(error)) }
}
function openDependencyDialog(row?: any) {
  editingDependency.value = row || null
  const preferredBase = bases.value.find(item => item.schedulable && item.compute_type === 'cpu' && String(item.python_version).startsWith('3.11')) || bases.value.find(item => item.schedulable)
  dependencyForm.value = row
    ? { name: row.name, requirements: row.requirements || '', special_packages: row.special_packages || [], description: row.description || '', install_now: false, base_environment_ids: [] }
    : { name: '', requirements: '', special_packages: [], description: '', install_now: true, base_environment_ids: preferredBase ? [preferredBase.id] : [] }
  showDependency.value = true
}
async function saveDependency() {
  if (!dependencyForm.value.name.trim()) return ElMessage.warning('请输入依赖配置名称')
  if (!editingDependency.value && dependencyForm.value.install_now && !dependencyForm.value.base_environment_ids.length) return ElMessage.warning('请选择依赖要安装到的基础环境')
  saving.value = true
  try {
    if (editingDependency.value) {
      await api.algorithmStore.updateDependencySet(editingDependency.value.id, dependencyForm.value)
      showDependency.value = false; ElMessage.success('依赖配置已保存')
    } else {
      const saved = await api.algorithmStore.createDependencySet({
        name: dependencyForm.value.name,
        requirements: dependencyForm.value.requirements,
        special_packages: dependencyForm.value.special_packages,
        description: dependencyForm.value.description,
      })
      if (dependencyForm.value.install_now) {
        await Promise.all(dependencyForm.value.base_environment_ids.map(baseId => {
          const base = bases.value.find(item => Number(item.id) === Number(baseId))
          return api.algorithmStore.createRuntimeBuild({
            name: `${saved.name} · ${base?.name || `环境${baseId}`}`,
            base_environment_id: baseId,
            dependency_set_id: saved.id,
          })
        }))
        activeTab.value = 'builds'
        ElMessage.success('依赖已保存，正在后台安装')
      } else ElMessage.success('依赖清单已保存，尚未安装')
      showDependency.value = false
    }
    await loadAll()
  } catch (error: any) { ElMessage.error('保存失败: ' + errorText(error)) }
  finally { saving.value = false }
}
async function removeDependency(row: any) {
  try { await ElMessageBox.confirm(`删除依赖配置“${row.name}”？`, '确认删除', {type:'warning'}); await api.algorithmStore.deleteDependencySet(row.id); await loadAll() }
  catch (error: any) { if (error !== 'cancel' && error?.message) ElMessage.error(errorText(error)) }
}
function openBuildDialog(dependency?: any) {
  buildForm.value = { name: '', base_environment_id: bases.value.find(item => item.schedulable)?.id || null, dependency_set_id: dependency?.id || dependencySets.value[0]?.id || null }
  showBuild.value = true
}
async function submitBuild() {
  if (!buildForm.value.base_environment_id || !buildForm.value.dependency_set_id) return ElMessage.warning('请选择基础环境和依赖配置')
  saving.value = true
  try { await api.algorithmStore.createRuntimeBuild(buildForm.value); showBuild.value = false; activeTab.value = 'builds'; ElMessage.success('环境构建已提交'); await loadAll() }
  catch (error: any) { ElMessage.error('构建提交失败: ' + errorText(error)) }
  finally { saving.value = false }
}
async function confirmSourceInstall(row: any) {
  const requests = Array.isArray(row.source_install_requests) ? row.source_install_requests : []
  const packageList = requests.map((item: any) => String(item.requirement || '')).filter(Boolean).join('、') || '未知包'
  try {
    await ElMessageBox.confirm('以下依赖没有可用预编译安装包，需要执行包自带的安装脚本：\n' + packageList + '\n\n仅确认来源可信时继续。本次构建会在隔离环境中进行。', '确认源码安装', { type: 'warning', confirmButtonText: '允许本次构建', cancelButtonText: '取消构建' })
    await api.algorithmStore.decideRuntimeSourceInstall(row.id, true)
    ElMessage.success('已允许本次源码安装，正在继续构建')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') {
      try { await api.algorithmStore.decideRuntimeSourceInstall(row.id, false); ElMessage.info('已取消本次源码安装') } catch (decisionError: any) { ElMessage.error(errorText(decisionError)) }
    } else ElMessage.error(errorText(error))
  } finally { await loadAll() }
}
async function rebuild(row: any) { try { await api.algorithmStore.rebuildRuntimeBuild(row.id); ElMessage.success('重建已提交'); await loadAll() } catch (error: any) { ElMessage.error(errorText(error)) } }
async function removeBuild(row: any) {
  try { await ElMessageBox.confirm(`删除运行环境“${row.name}”？`, '确认删除', {type:'warning'}); await api.algorithmStore.deleteRuntimeBuild(row.id); await loadAll() }
  catch (error: any) { if (error !== 'cancel' && error?.message) ElMessage.error(errorText(error)) }
}
function showLog(row: any) {
  activeLogBuildId.value = Number(row.id)
  activeLog.value = row.build_log || '暂无构建日志'
  showBuildLog.value = true
}

onMounted(loadAll)
onUnmounted(() => { if (pollTimer) window.clearTimeout(pollTimer) })
</script>

<style scoped>
.toolbar { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:12px; color:#606266; font-size:13px; }
.reason { color:#909399; font-size:11px; margin-top:4px; line-height:1.35; }
.progress-cell { min-width:210px; }
.progress-stage { margin-top:5px; color:#606266; font-size:12px; font-weight:600; }
.progress-detail { margin-top:2px; color:#909399; font-size:11px; line-height:1.35; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.special-package-help { display:block; margin-top:3px; color:#909399; font-size:11px; line-height:1.4; }
code { color:#475569; white-space:normal; }
.build-log { max-height:460px; overflow:auto; margin:0; padding:14px; color:#d1d5db; background:#111827; border-radius:6px; white-space:pre-wrap; font-size:12px; }
</style>
