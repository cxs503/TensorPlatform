<template>
  <div class="notification-page">
    <div class="page-header">
      <h2>🔔 告警通知</h2>
      <div>
        <el-button @click="fetchAlerts">刷新</el-button>
        <el-button type="primary" @click="showRuleDialog = true">+ 新建规则</el-button>
      </div>
    </div>

    <el-row :gutter="16" class="stats-row">
      <el-col :span="6"><el-card><el-statistic title="活跃告警" :value="activeAlerts" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="告警规则" :value="rules.length" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="通知渠道" :value="channels.length" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="系统状态" :value="sysStatus" /></el-card></el-col>
    </el-row>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="活跃告警" name="alerts">
        <el-card>
          <el-table :data="alerts" stripe v-loading="loading">
            <el-table-column prop="title" label="告警标题" min-width="180" />
            <el-table-column prop="level" label="级别" width="100">
              <template #default="{row}">
                <el-tag :type="row.level==='critical'?'danger':row.level==='warning'?'warning':'info'" size="small">{{ row.level }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="source" label="来源" width="120" />
            <el-table-column prop="created_at" label="触发时间" width="170" />
            <el-table-column label="操作" width="120">
              <template #default="{row}">
                <el-button size="small" type="success" @click="resolveAlert(row.id)" :disabled="row.status==='resolved'">确认解决</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="告警规则" name="rules">
        <el-card>
          <el-table :data="rules" stripe>
            <el-table-column prop="name" label="规则名" width="160" />
            <el-table-column prop="condition" label="条件" width="100" />
            <el-table-column prop="threshold" label="阈值" width="100" />
            <el-table-column prop="level" label="级别" width="100" />
            <el-table-column label="通知渠道" width="140"><template #default="{row}">{{ (row.channels||[]).join(',') || '-' }}</template></el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{row}">
                <el-button size="small" type="danger" @click="deleteRule(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="通知渠道" name="channels">
        <el-card>
          <el-table :data="channels" stripe>
            <el-table-column prop="name" label="渠道名" width="150" />
            <el-table-column prop="type" label="类型" width="100">
              <template #default="{row}"><el-tag size="small">{{ row.type }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="config" label="配置" min-width="200" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{row}">
                <el-tag :type="row.status==='active'?'success':'danger'" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="showRuleDialog" title="新建告警规则" width="500px">
      <el-form :model="ruleForm" label-width="100px">
        <el-form-item label="规则名称"><el-input v-model="ruleForm.name" placeholder="CPU使用率告警" /></el-form-item>
        <el-form-item label="监控指标">
          <el-select v-model="ruleForm.metric" style="width:100%">
            <el-option v-for="m in ['cpu_usage','memory_usage','gpu_usage','api_latency','error_rate','model_drift']" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="条件"><el-select v-model="ruleForm.condition" style="width:100%"><el-option v-for="c in ['>','<','>=','<=','==']" :key="c" :label="c" :value="c" /></el-select></el-form-item>
        <el-form-item label="阈值"><el-input-number v-model="ruleForm.threshold" :min="0" style="width:100%" /></el-form-item>
        <el-form-item label="严重级别">
          <el-select v-model="ruleForm.severity" style="width:100%">
            <el-option v-for="s in ['critical','warning','info']" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="通知渠道"><el-select v-model="ruleForm.channel" style="width:100%"><el-option v-for="c in channels" :key="c.id" :label="c.name" :value="c.id" /></el-select></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRuleDialog=false">取消</el-button>
        <el-button type="primary" @click="createRule">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, del } from '@/api/legacy'

const loading = ref(false)
const activeTab = ref('alerts')
const showRuleDialog = ref(false)
const alerts = ref<any[]>([])
const rules = ref<any[]>([])
const channels = ref<any[]>([])
const sysStatus = ref('正常')
const ruleForm = ref({ name:'', metric:'cpu_usage', condition:'>', threshold:80, severity:'warning', channel:'' })

const activeAlerts = computed(() => alerts.value.filter(a=>a.status!=='resolved').length)

onMounted(() => { fetchAlerts(); fetchRules(); fetchChannels(); fetchStatus() })

async function fetchAlerts() {
  loading.value = true
  try { const d = await get('/notifications/alerts'); alerts.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){ console.error(e) }
  finally { loading.value = false }
}
async function fetchRules() {
  try { const d = await get('/notifications/rules'); rules.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){ console.error(e) }
}
async function fetchChannels() {
  try { const d = await get('/notifications/channels'); channels.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){ console.error(e) }
}
async function fetchStatus() {
  try { const d = await get('/notifications/status'); sysStatus.value = d?.status || '正常' }
  catch(e:any){}
}

async function resolveAlert(id: string) {
  try { await post(`/notifications/alerts/${id}/resolve`); ElMessage.success('已确认解决'); fetchAlerts() }
  catch(e:any){ ElMessage.error(e?.message||'操作失败') }
}
async function createRule() {
  try {
    const f = ruleForm.value
    const condMap: any = { '>':'gt', '<':'lt', '>=':'gte', '<=':'lte', '==':'eq' }
    // 映射到后端契约 CreateRuleReq:{name, condition, threshold, level, channels[], enabled}
    const payload = {
      name: f.name,
      condition: condMap[f.condition] || 'gt',
      threshold: f.threshold,
      level: f.severity,
      channels: f.channel ? [f.channel] : [],
      enabled: true,
    }
    await post('/notifications/rules', payload)
    ElMessage.success('规则创建成功'); showRuleDialog.value=false; fetchRules()
  }
  catch(e:any){ ElMessage.error(e?.message||'创建失败') }
}
async function deleteRule(id: string) {
  await ElMessageBox.confirm('确认删除？', '警告', {type:'warning'})
  try { await del(`/notifications/rules/${id}`); ElMessage.success('已删除'); fetchRules() }
  catch(e:any){ ElMessage.error(e?.message||'删除失败') }
}
</script>

<style scoped>
.notification-page { padding: 24px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 20px; }
.stats-row { margin-bottom: 20px; }
</style>
