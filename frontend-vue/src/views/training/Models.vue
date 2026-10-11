<template>
  <div class="models-page">
    <PageHeader title="模型管理" desc="模型注册 · Stage生命周期 · 模型导出 · 部署" />

    <el-row :gutter="16" class="stats">
      <el-col :xs="8" :sm="6" v-for="s in stageStats" :key="s.label">
        <el-card shadow="hover" class="stat-card" :style="{borderTop:'3px solid '+s.color}">
          <div class="sv" :style="{color:s.color}">{{ s.value }}</div>
          <div class="sl">{{ s.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>
        <div style="display:flex;justify-content:space-between">
          <span>模型列表 ({{ modelTotal }})</span>
          <el-input v-model="search" placeholder="搜索..." clearable size="small" style="width:200px" />
        </div>
      </template>
      <el-table :data="filteredModels" size="small" max-height="500" stripe @row-click="selectModel">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="model_type" label="类型" width="100" />
        <el-table-column prop="framework" label="框架" width="90" />
        <el-table-column label="部署" width="100">
          <template #default="{row}">
            <el-tag v-if="row.is_deployed" type="success" size="small">
              {{ row.deployment_count }} 个运行中
            </el-tag>
            <span v-else style="color:#c0c4cc">未部署</span>
          </template>
        </el-table-column>
        <el-table-column label="Stage" width="150">
          <template #default="{row}">
            <el-tag :type="stageTag(row.stage)" size="small">{{ row.stage || 'development' }}</el-tag>
            <div class="stage-dots">
              <span v-for="s in stages" :key="s" class="dot"
                :style="{background: getLevel(row.stage) >= getLevel(s) ? colorMap[s] : '#e0e0e0'}" />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="指标" width="120">
          <template #default="{row}">
            <span v-if="row.metrics && row.metrics.r2" style="color:#67C23A">R2={{ row.metrics.r2.toFixed(3) }}</span>
            <span v-else style="color:#c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="350" fixed="right">
          <template #default="{row}">
            <el-button size="small" @click.stop="promoteStage(row)" :disabled="row.stage==='archived'"
              :type="row.stage==='production'?'danger':'success'">
              {{ row.stage==='production'?'归档':'晋升' }}
            </el-button>
            <el-button size="small" type="primary" :disabled="!canDeploy(row)" @click.stop="openDeploy(row)">
              {{ canDeploy(row) ? '部署' : '不可部署' }}
            </el-button>
            <el-button
              size="small"
              type="warning"
              plain
              :disabled="!canExport(row)"
              @click.stop="openArtifacts(row)"
            >生成制品</el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="deletingId === row.id"
              @click.stop="deleteModel(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="showDetail" title="模型详情" size="55%">
      <template v-if="selectedModel">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="ID">{{ selectedModel.id }}</el-descriptions-item>
          <el-descriptions-item label="Stage">
            <el-tag :type="stageTag(selectedModel.stage)">{{ selectedModel.stage || 'development' }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="创建">{{ fmt(selectedModel.created_at) }}</el-descriptions-item>
        </el-descriptions>
        <el-divider>Stage 流转</el-divider>
        <div class="stage-flow">
          <div v-for="(s,i) in stages" :key="s" class="sn"
            :class="{done: getLevel(selectedModel.stage) > i, current: getLevel(selectedModel.stage) === i}">
            <div class="sc" :style="{background: getLevel(selectedModel.stage) >= i ? colorMap[s] : '#e0e0e0'}">
              <el-icon v-if="getLevel(selectedModel.stage) > i"><Check /></el-icon>
              <span v-else>{{ i+1 }}</span>
            </div>
            <div class="sl2">{{ s }}</div>
          </div>
        </div>
        <div style="margin-top:16px;display:flex;gap:8px">
          <el-button v-for="s in nextStages" :key="s" :type="s==='archived'?'danger':'success'"
            @click="doPromote(s)">{{ s==='production'?'晋升到生产':'归档' }}</el-button>
        </div>
      </template>
    </el-drawer>

    <el-dialog v-model="showDeploy" title="部署模型" width="500px">
      <el-form :model="deployForm" label-width="100px">
        <el-form-item label="模型"><el-input :value="deployName" disabled /></el-form-item>
        <el-alert title="将使用该模型的最新可用版本创建推理服务" type="info" :closable="false" show-icon />
      </el-form>
      <template #footer>
        <el-button @click="showDeploy=false">取消</el-button>
        <el-button type="primary" @click="doDeploy" :loading="deploying">部署</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del as remove, get, post, put } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'
import { useRouter } from 'vue-router'

const models = ref<any[]>([])
const modelTotal = ref(0)
const selectedModel = ref<any>(null)
const showDetail = ref(false)
const showDeploy = ref(false)
const deployName = ref('')
const deploying = ref(false)
const deletingId = ref<number | null>(null)
const router = useRouter()
const search = ref('')
const stages = ['development','staging','production','archived']
const colorMap: Record<string,string> = { development:'#909399', staging:'#E6A23C', production:'#67C23A', archived:'#F56C6C' }
const deployForm = reactive({ model_id: 0 })

const stageStats = computed(() => stages.map(s => ({
  label: s, value: models.value.filter(m => (m.stage||'development')===s).length,
  color: colorMap[s]
})))

const filteredModels = computed(() => {
  if (!search.value) return models.value
  const q = search.value.toLowerCase()
  return models.value.filter(m => m.name?.toLowerCase().includes(q))
})

const nextStages = computed(() => {
  const s = selectedModel.value?.stage || 'development'
  const idx = stages.indexOf(s)
  const next: string[] = []
  if (idx < stages.length - 1) next.push(stages[idx+1])
  if (s === 'production' && idx === 2) next.push('archived')
  return next
})

function stageTag(s: string) { const m: Record<string,string> = { development:'info', staging:'warning', production:'success', archived:'danger' }; return m[s]||'info' }
function getLevel(s: string) { return stages.indexOf(s||'development') }
function canDeploy(row: any) {
  return ['READY', 'DEPLOYED'].includes(String(row?.status || '').toUpperCase())
    && row?.stage !== 'archived'
}
function canExport(row: any) {
  return ['READY', 'DEPLOYED'].includes(String(row?.status || '').toUpperCase())
}

async function loadAll() {
  try {
    const r: any = await get('/models', { limit: 200 })
    models.value = r.items || r || []
    modelTotal.value = r.total ?? models.value.length
  }
  catch { /* */ }
}

function selectModel(row: any) { selectedModel.value = row; showDetail.value = true }

function promoteStage(row: any) {
  selectedModel.value = row
  const idx = getLevel(row.stage)
  if (idx < stages.length - 1) doPromote(stages[idx+1])
}

async function doPromote(stage: string) {
  try {
    await put('/models/'+selectedModel.value.id, { stage })
    ElMessage.success('已晋升到 ' + stage)
    selectedModel.value.stage = stage
    loadAll()
  } catch { ElMessage.error('操作失败') }
}

function openDeploy(row: any) { deployForm.model_id = row.id; deployName.value = row.name; showDeploy.value = true }

async function doDeploy() {
  deploying.value = true
  try {
    const payload: any = { model_id: deployForm.model_id, strategy: 'rolling' }
    await post('/deployments', payload)
    ElMessage.success('部署已创建')
    showDeploy.value = false
  } catch (error: any) {
    ElMessage.error('部署失败：' + (error?.message || '未知错误'))
  }
  finally { deploying.value = false }
}

function openArtifacts(row: any) {
  router.push({ path: '/deploy/artifacts', query: { model_id: row.id } })
}

async function deleteModel(row: any) {
  if (Number(row.deployment_count || 0) > 0) {
    ElMessage.error(`模型仍有 ${row.deployment_count} 个运行中或待启动的部署，请先在部署管理中停止`)
    return
  }
  const references = []
  if (Number(row.training_job_count || 0) > 0) references.push(`${row.training_job_count} 条训练任务历史将保留`)
  if (Number(row.deployment_record_count || 0) > 0) references.push(`${row.deployment_record_count} 条已停止部署记录将清理`)
  const detail = references.length ? `\n关联处理：${references.join('；')}。` : ''
  try {
    await ElMessageBox.confirm(
      `确定删除模型 #${row.id}「${row.name}」吗？${detail}\n模型版本、本地模型文件和 MLflow 注册项将同步清理，此操作不可恢复。`,
      '删除模型',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning', distinguishCancelAndClose: true },
    )
  } catch {
    return
  }

  deletingId.value = Number(row.id)
  try {
    const result: any = await remove(`/models/${row.id}`)
    const cleanupErrors = [
      ...(result?.artifact_cleanup?.errors || []),
      ...(result?.mlflow_cleanup?.errors || []),
    ]
    if (cleanupErrors.length) ElMessage.warning('模型记录已删除，部分外部文件清理失败，请查看服务日志')
    else ElMessage.success(`模型 #${row.id} 已删除`)
    if (selectedModel.value?.id === row.id) {
      showDetail.value = false
      selectedModel.value = null
    }
    await loadAll()
  } catch (error: any) {
    ElMessage.error(`删除失败：${error?.message || '未知错误'}`)
  } finally {
    deletingId.value = null
  }
}

function fmt(t: string) { return t ? new Date(t).toLocaleString('zh-CN') : '-' }

onMounted(loadAll)
</script>

<style scoped>
.models-page { max-width: 1300px; padding: 20px; }
.stats { margin-bottom: 16px; }
.stat-card .sv { font-size: 28px; font-weight: 700; text-align: center; }
.stat-card .sl { font-size: 12px; color: #909399; margin-top: 4px; text-align: center; }
.stage-dots { display: flex; gap: 3px; margin-top: 2px; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.stage-flow { display: flex; gap: 30px; justify-content: center; padding: 20px 0; }
.sn { text-align: center; }
.sc { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; margin: 0 auto 8px; }
.sl2 { font-weight: 600; font-size: 13px; text-transform: capitalize; }
</style>
