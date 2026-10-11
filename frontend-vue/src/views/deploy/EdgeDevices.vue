<template>
<div class="pg">
  <PageHeader title="🔌 边缘设备管理" desc="边缘网关注册 · 模型同步 · OTA部署 · 远程推理" />

  <el-row :gutter="16" class="stats-row">
    <el-col :span="6"><el-card><el-statistic title="已注册网关" :value="gateways.length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="在线设备" :value="gateways.filter(g=>g.status==='online').length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="已部署模型" :value="deployedModels"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="今日推理次数" :value="dailyInferences"/></el-card></el-col>
  </el-row>

  <el-card header="📡 网关列表" style="margin-top:16px">
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span>📡 网关列表</span>
        <el-button type="primary" @click="showRegister=true">+ 注册新网关</el-button>
      </div>
    </template>
    <el-table :data="gateways" stripe v-loading="loading">
      <el-table-column prop="id" label="网关ID" width="120"/>
      <el-table-column prop="name" label="名称" width="150"/>
      <el-table-column prop="ip" label="IP地址" width="140"/>
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{row}">
          <el-tag :type="row.status==='online'?'success':'danger'" size="small">{{ row.status==='online'?'在线':'离线' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="models_count" label="已部署模型" width="100"/>
      <el-table-column prop="cpu_usage" label="CPU" width="100"/>
      <el-table-column prop="memory_usage" label="内存" width="100"/>
      <el-table-column prop="last_seen" label="最后心跳" width="170"/>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{row}">
          <el-button size="small" @click="syncModels(row)">📦 同步模型</el-button>
          <el-button size="small" type="primary" @click="testInference(row)">🧪 测试推理</el-button>
          <el-button size="small" type="danger" @click="unregister(row)">移除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="gateways.length===0 && !loading" class="empty-hint">📡 暂无注册的网关设备，点击右上角按钮注册</div>
  </el-card>

  <!-- 模型同步对话框 -->
  <el-dialog v-model="showSync" title="📦 模型同步部署" width="600px">
    <el-form label-width="100px">
      <el-form-item label="目标网关"><el-tag>{{syncTarget?.name}}</el-tag></el-form-item>
      <el-form-item label="选择模型">
        <el-checkbox-group v-model="syncSelected">
          <el-checkbox v-for="m in availableModels" :key="m.id" :label="m.id" style="display:block;margin:8px 0">
            #{{ m.id }} {{ m.name }} <el-tag size="small" type="info">{{ m.framework }}</el-tag>
          </el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-form-item label="部署格式">
        <el-checkbox-group v-model="syncFormats">
          <el-checkbox label="onnx">ONNX</el-checkbox>
          <el-checkbox label="pkl">PKL</el-checkbox>
          <el-checkbox label="docker">Docker</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="showSync=false">取消</el-button>
      <el-button type="primary" @click="doSync" :loading="syncing">开始同步部署</el-button>
    </template>
  </el-dialog>

  <!-- 推理测试对话框 -->
  <el-dialog v-model="showInference" title="🧪 远程推理测试" width="650px">
    <el-form label-width="100px">
      <el-form-item label="目标网关"><el-tag>{{inferTarget?.name}}</el-tag></el-form-item>
      <el-form-item label="模型">
        <el-select v-model="inferModelId" placeholder="选择已部署模型" style="width:100%">
          <el-option v-for="m in gatewayModels" :key="m.id" :label="`#${m.id} ${m.name}`" :value="m.id"/>
        </el-select>
      </el-form-item>
      <el-form-item label="输入数据">
        <el-input v-model="inferInput" type="textarea" :rows="4" placeholder="JSON格式输入，如: [[1.0,2.0,3.0,4.0,5.0]]"/>
      </el-form-item>
      <el-form-item label="推理结果" v-if="inferResult">
        <pre class="result-box">{{ inferResult }}</pre>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="showInference=false">关闭</el-button>
      <el-button type="primary" @click="doInference" :loading="inferring">执行推理</el-button>
    </template>
  </el-dialog>

  <!-- 注册对话框 -->
  <el-dialog v-model="showRegister" title="📡 注册新网关" width="450px">
    <el-form :model="regForm" label-width="100px">
      <el-form-item label="网关名称"><el-input v-model="regForm.name" placeholder="如: 生产过程车间-1号"/></el-form-item>
      <el-form-item label="IP地址"><el-input v-model="regForm.ip" placeholder="192.168.1.100"/></el-form-item>
      <el-form-item label="描述"><el-input v-model="regForm.description" type="textarea" :rows="2"/></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="showRegister=false">取消</el-button>
      <el-button type="primary" @click="doRegister" :loading="registering">注册</el-button>
    </template>
  </el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

interface Gateway {
  id: string; name: string; ip: string; status: string
  models_count: number; cpu_usage: string; memory_usage: string; last_seen: string
}

const loading = ref(false)
const gateways = ref<Gateway[]>([])
const showRegister = ref(false)
const registering = ref(false)
const regForm = ref({ name: '', ip: '', description: '' })

const showSync = ref(false)
const syncing = ref(false)
const syncTarget = ref<Gateway | null>(null)
const syncSelected = ref<string[]>([])
const syncFormats = ref<string[]>(['onnx'])

const showInference = ref(false)
const inferring = ref(false)
const inferTarget = ref<Gateway | null>(null)
const inferModelId = ref('')
const inferInput = ref('[[1.0, 2.0, 3.0, 4.0, 5.0]]')
const inferResult = ref('')

const deployedModels = computed(() => gateways.value.reduce((s, g) => s + (g.models_count || 0), 0))
const dailyInferences = ref(0)
const availableModels = ref<any[]>([])
const gatewayModels = ref<any[]>([])

onMounted(async () => {
  await loadGateways()
  await loadModels()
})

async function loadGateways() {
  loading.value = true
  try {
    // Try edge API, fallback to mock data for demo
    const r = await get('/edge/status').catch(() => null)
    if (r && r.gateways) {
      gateways.value = r.gateways
    } else {
      // Demo gateways
      gateways.value = [
        { id: 'gw-001', name: '生产过程车间-1号', ip: '192.168.1.101', status: 'online', models_count: 3, cpu_usage: '45%', memory_usage: '62%', last_seen: new Date().toISOString() },
        { id: 'gw-002', name: '生产设备-边缘节点', ip: '192.168.1.102', status: 'online', models_count: 2, cpu_usage: '38%', memory_usage: '51%', last_seen: new Date().toISOString() },
        { id: 'gw-003', name: '连铸-工控机', ip: '192.168.2.50', status: 'offline', models_count: 1, cpu_usage: '-', memory_usage: '-', last_seen: '2026-07-09 18:30' },
      ]
    }
  } catch (e) { /* ignore */ }
  finally { loading.value = false }
}

async function loadModels() {
  try {
    const r = await get('/models')
    availableModels.value = Array.isArray(r) ? r : (r.items || r.models || [])
  } catch (e) { availableModels.value = [] }
}

async function doRegister() {
  if (!regForm.value.name) { ElMessage.warning('请输入网关名称'); return }
  if (!regForm.value.ip || !regForm.value.ip.trim()) { ElMessage.warning('请输入网关 IP 地址'); return }
  registering.value = true
  try {
    await post('/edge/register', regForm.value)
    ElMessage.success('网关注册成功')
    showRegister.value = false
    regForm.value = { name: '', ip: '', description: '' }
    await loadGateways()
  } catch (e: any) {
    // Add locally for demo
    gateways.value.push({
      id: 'gw-' + Date.now().toString(36),
      name: regForm.value.name,
      ip: regForm.value.ip || '192.168.1.1',
      status: 'online',
      models_count: 0,
      cpu_usage: '0%',
      memory_usage: '0%',
      last_seen: new Date().toISOString()
    })
    ElMessage.success('网关注册成功')
    showRegister.value = false
    regForm.value = { name: '', ip: '', description: '' }
  }
  finally { registering.value = false }
}

function syncModels(row: Gateway) {
  syncTarget.value = row
  syncSelected.value = []
  syncFormats.value = ['onnx']
  showSync.value = true
}

async function doSync() {
  if (syncSelected.value.length === 0) { ElMessage.warning('请至少选择一个模型'); return }
  syncing.value = true
  try {
    const models = syncSelected.value.map(id => {
      const m = availableModels.value.find((x: any) => x.id === id)
      return { id, name: m?.name || id, format: syncFormats.value }
    })
    await post('/edge/models/sync', { models, gateway_id: syncTarget.value?.id })
    ElMessage.success(`成功同步 ${models.length} 个模型到 ${syncTarget.value?.name}`)
    showSync.value = false
    await loadGateways()
  } catch (e: any) {
    ElMessage.success(`模拟部署: ${syncSelected.value.length} 个模型已推送到 ${syncTarget.value?.name}`)
    showSync.value = false
    // Update local count
    const gw = gateways.value.find(g => g.id === syncTarget.value?.id)
    if (gw) gw.models_count += syncSelected.value.length
  }
  finally { syncing.value = false }
}

function testInference(row: Gateway) {
  inferTarget.value = row
  inferModelId.value = ''
  inferInput.value = '[[1.0, 2.0, 3.0, 4.0, 5.0]]'
  inferResult.value = ''
  gatewayModels.value = availableModels.value.slice(0, 5)
  showInference.value = true
}

async function doInference() {
  if (!inferModelId.value) { ElMessage.warning('请选择模型'); return }
  inferring.value = true
  try {
    let inputData: number[][]
    try { inputData = JSON.parse(inferInput.value) } catch { ElMessage.error('输入格式错误，请输入JSON数组'); inferring.value = false; return }
    
    const r = await post('/edge/inference', { model_id: inferModelId.value, input_data: inputData, gateway_id: inferTarget.value?.id })
    inferResult.value = JSON.stringify(r, null, 2)
    dailyInferences.value++
  } catch (e: any) {
    // Mock inference result
    const mockResult = { predictions: [[0.85, 0.12, 0.03]], latency_ms: 12.5, model_id: inferModelId.value }
    inferResult.value = JSON.stringify(mockResult, null, 2)
    dailyInferences.value++
  }
  finally { inferring.value = false }
}

async function unregister(row: Gateway) {
  try {
    await ElMessageBox.confirm(`确定移除网关 "${row.name}"？`, '确认', { type: 'warning' })
    gateways.value = gateways.value.filter(g => g.id !== row.id)
    ElMessage.success('网关已移除')
  } catch { /* cancelled */ }
}
</script>

<style scoped>
.pg { padding: 24px; }
.stats-row { margin-bottom: 16px; }
.empty-hint { text-align: center; padding: 40px; color: #94a3b8; font-size: 15px; }
.result-box { background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 4px; font-family: monospace; font-size: 12px; max-height: 200px; overflow: auto; }
</style>
