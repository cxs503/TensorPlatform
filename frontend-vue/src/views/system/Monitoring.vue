<template>
  <div class="page-container">
    <PageHeader title="监控告警" desc="系统监控、告警管理与性能分析">
      <template #actions>
        <el-button type="primary" @click="showRuleDialog = true">
          <el-icon><Setting /></el-icon> 告警规则
        </el-button>
        <el-button @click="loadAll">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
        <el-input v-model="searchText" placeholder="搜索告警" style="width: 250px; margin-left: 16px" clearable>
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="filterLevel" placeholder="告警级别" style="width: 120px; margin-left: 8px" clearable>
          <el-option label="严重" value="critical" />
          <el-option label="警告" value="warning" />
          <el-option label="信息" value="info" />
        </el-select>
        <el-select v-model="filterStatus" placeholder="状态" style="width: 120px; margin-left: 8px" clearable>
          <el-option label="未解决" value="unresolved" />
          <el-option label="已解决" value="resolved" />
        </el-select>
      </template>
    </PageHeader>

    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6">
        <StatCard title="告警总数" :value="alertStats?.total" icon="Bell" iconColor="#3b82f6" bgColor="rgba(59, 130, 246, 0.15)" :loading="statsLoading" />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard title="严重告警" :value="alertStats?.critical" icon="Warning" iconColor="#ef4444" bgColor="rgba(239, 68, 68, 0.15)" :loading="statsLoading" />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard title="待处理" :value="alertStats?.active" icon="Clock" iconColor="#f59e0b" bgColor="rgba(245, 158, 11, 0.15)" :loading="statsLoading" />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard title="已解决" :value="alertStats?.resolved" icon="CircleCheck" iconColor="#10b981" bgColor="rgba(16, 185, 129, 0.15)" :loading="statsLoading" />
      </el-col>
    </el-row>

    <!-- 系统资源监控 -->
    <el-row :gutter="20" class="resource-row">
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <template #header><span>CPU使用率</span></template>
          <div v-if="resource.cpu !== null" class="gauge-container">
            <el-progress type="dashboard" :percentage="resource.cpu" :color="getProgressColor(resource.cpu)" />
            <div class="gauge-label">{{ resource.cpu }}%</div>
          </div>
          <el-empty v-else :description="resourceError || 'CPU指标不可用'" :image-size="54" />
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <template #header><span>内存使用率</span></template>
          <div v-if="resource.memory !== null" class="gauge-container">
            <el-progress type="dashboard" :percentage="resource.memory" :color="getProgressColor(resource.memory)" />
            <div class="gauge-label">{{ resource.memory }}%</div>
          </div>
          <el-empty v-else :description="resourceError || '内存指标不可用'" :image-size="54" />
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <template #header><span>磁盘使用率</span></template>
          <div v-if="resource.disk !== null" class="gauge-container">
            <el-progress type="dashboard" :percentage="resource.disk" :color="getProgressColor(resource.disk)" />
            <div class="gauge-label">{{ resource.disk }}%</div>
          </div>
          <el-empty v-else :description="resourceError || '磁盘指标不可用'" :image-size="54" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 告警列表 -->
    <ChartCard title="告警列表" :loading="alertsLoading" :error="alertsError" @retry="loadAlerts">
      <template #header-actions>
        <el-button text size="small" @click="batchResolve" :disabled="selectedAlerts.length === 0">
          批量解决 ({{ selectedAlerts.length }})
        </el-button>
      </template>
      <template #default>
        <el-table :data="filteredAlerts" style="width: 100%" @selection-change="handleSelectionChange">
          <el-table-column type="selection" width="50" />
          <el-table-column prop="title" label="告警标题" min-width="200">
            <template #default="{ row }">
              <div class="alert-title">
                <el-icon :color="getLevelColor(row.level)"><Warning /></el-icon>
                <span>{{ row.title }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="level" label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="getLevelType(row.level)" size="small">{{ getLevelLabel(row.level) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="来源" width="120" />
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="getStatusType(row.status)" size="small">{{ getStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="时间" width="160">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="viewAlert(row)">详情</el-button>
              <el-button size="small" type="success" v-if="row.status !== 'resolved'" @click="resolveAlert(row)">解决</el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="totalAlerts"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          style="margin-top: 16px; justify-content: flex-end"
        />
      </template>
    </ChartCard>

    <!-- 告警详情对话框 -->
    <el-dialog v-model="showDetailDialog" title="告警详情" width="600px">
      <el-descriptions :column="2" border v-if="currentAlert">
        <el-descriptions-item label="告警ID">{{ currentAlert.id }}</el-descriptions-item>
        <el-descriptions-item label="级别">
          <el-tag :type="getLevelType(currentAlert.level)">{{ getLevelLabel(currentAlert.level) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="来源">{{ currentAlert.source }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="getStatusType(currentAlert.status)">{{ getStatusLabel(currentAlert.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间" :span="2">{{ formatDate(currentAlert.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ currentAlert.message || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 告警规则对话框 -->
    <el-dialog v-model="showRuleDialog" title="告警规则配置" width="800px">
      <div class="rule-toolbar"><el-button type="primary" size="small" @click="editRule()">新建规则</el-button></div>
      <el-alert v-if="rulesError" :title="rulesError" type="error" :closable="false" style="margin-bottom:12px" />
      <el-table :data="alertRules" size="small" v-loading="rulesLoading" empty-text="暂无告警规则">
        <el-table-column prop="name" label="规则名称" min-width="150" />
        <el-table-column prop="metric" label="监控指标" width="120" />
        <el-table-column prop="condition" label="条件" width="100">
          <template #default="{ row }">{{ row.operator }} {{ row.threshold }}</template>
        </el-table-column>
        <el-table-column prop="severity" label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="getLevelType(row.severity)" size="small">{{ getLevelLabel(row.severity) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="enabled" label="状态" width="80">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" size="small" @change="toggleRule(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button text size="small" @click="editRule(row)">编辑</el-button>
            <el-button text size="small" type="danger" @click="deleteRule(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="showRuleEditDialog" :title="ruleForm.id ? '编辑告警规则' : '新建告警规则'" width="520px">
      <el-form label-width="100px">
        <el-form-item label="规则名称" required><el-input v-model="ruleForm.name" /></el-form-item>
        <el-form-item label="监控指标" required><el-input v-model="ruleForm.metric" placeholder="例如 cpu_usage" /></el-form-item>
        <el-form-item label="判断条件" required>
          <el-select v-model="ruleForm.operator" style="width:100px"><el-option v-for="item in ['>','>=','<','<=','==']" :key="item" :label="item" :value="item" /></el-select>
          <el-input-number v-model="ruleForm.threshold" style="margin-left:8px" />
        </el-form-item>
        <el-form-item label="告警级别"><el-select v-model="ruleForm.severity"><el-option label="严重" value="critical" /><el-option label="警告" value="warning" /><el-option label="信息" value="info" /></el-select></el-form-item>
        <el-form-item label="启用"><el-switch v-model="ruleForm.enabled" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showRuleEditDialog=false">取消</el-button><el-button type="primary" :loading="ruleSaving" @click="saveRule">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { View, Bell, Warning, Clock, CircleCheck, Setting, Refresh, Search } from '@element-plus/icons-vue'
import { get, post, put, del } from '@/api/legacy'
import { useAsyncData } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatCard from '@/components/StatCard.vue'
import ChartCard from '@/components/ChartCard.vue'

const searchText = ref('')
const filterLevel = ref('')
const filterStatus = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const selectedAlerts = ref<any[]>([])

// 告警统计数据
const { data: alertStats, loading: statsLoading, refresh: refreshStats } = useAsyncData(async () => {
  const stats = await get<any>('/alerts/stats')
  return {
    total: stats?.total ?? 0,
    critical: stats?.by_level?.critical ?? 0,
    active: stats?.unresolved ?? 0,
    resolved: stats?.resolved ?? 0,
  }
})

// 告警列表
const alertsData = ref<any[]>([])
const totalAlerts = ref(0)
const alertsLoading = ref(false)
const alertsError = ref<string | null>(null)

async function loadAlerts() {
  alertsLoading.value = true
  alertsError.value = null
  try {
    const qs = new URLSearchParams({ limit: String(pageSize.value) })
    if (filterStatus.value) qs.set('status', filterStatus.value)
    if (filterLevel.value) qs.set('level', filterLevel.value)
    const res = await get<any>(`/alerts?${qs.toString()}`)
    alertsData.value = (res?.items || []).map((item: any) => ({
      ...item,
      level: item.severity,
      status: item.is_resolved ? 'resolved' : 'active',
    }))
    totalAlerts.value = res?.total || 0
  } catch (e: any) {
    alertsError.value = e?.message || '加载告警失败'
  } finally {
    alertsLoading.value = false
  }
}

const filteredAlerts = computed(() => {
  let result = alertsData.value
  if (searchText.value) {
    const search = searchText.value.toLowerCase()
    result = result.filter(a => a.title?.toLowerCase().includes(search))
  }
  if (filterLevel.value) result = result.filter(a => a.level === filterLevel.value)
  if (filterStatus.value) result = result.filter(a => a.status === filterStatus.value)
  return result
})

// 资源监控
const resource = ref<{cpu:number|null;memory:number|null;disk:number|null}>({ cpu: null, memory: null, disk: null })
const resourceError = ref('')
async function loadResource() {
  try {
    const res = await get<any>('/monitoring/metrics')
    const metrics = res?.metrics || res
    const next = {
      cpu: typeof metrics?.cpu?.usage_percent === 'number' ? metrics.cpu.usage_percent : null,
      memory: typeof metrics?.memory?.usage_percent === 'number' ? metrics.memory.usage_percent : null,
      disk: typeof metrics?.disk?.usage_percent === 'number' ? metrics.disk.usage_percent : null,
    }
    resource.value = next
    resourceError.value = Object.values(next).some(value => value !== null) ? '' : '监控接口没有返回有效指标'
  } catch (error: any) {
    resource.value = { cpu: null, memory: null, disk: null }
    resourceError.value = error?.message || '资源监控接口不可用'
  }
}

// 告警详情
const showDetailDialog = ref(false)
const currentAlert = ref<any>(null)

// 告警规则
const showRuleDialog = ref(false)
const alertRules = ref<any[]>([])
const rulesLoading = ref(false)
const rulesError = ref('')
const showRuleEditDialog = ref(false)
const ruleSaving = ref(false)
const emptyRule = () => ({ id: null as number|null, name: '', metric: '', operator: '>', threshold: 80, severity: 'warning', enabled: true })
const ruleForm = ref(emptyRule())

async function loadRules() {
  rulesLoading.value = true
  rulesError.value = ''
  try {
    const result = await get<any>('/monitoring/alert-rules')
    alertRules.value = result?.items || []
  } catch (error: any) {
    alertRules.value = []
    rulesError.value = error?.message || '告警规则加载失败'
  } finally {
    rulesLoading.value = false
  }
}

async function loadAll() {
  await Promise.all([refreshStats(), loadAlerts(), loadResource(), loadRules()])
}

async function resolveAlert(row: any) {
  try {
    await post(`/alerts/${row.id}/resolve?user_id=admin`)
    ElMessage.success('已解决')
    loadAll()
  } catch { ElMessage.error('操作失败') }
}

async function batchResolve() {
  try {
    await post('/alerts/batch-resolve', { ids: selectedAlerts.value.map(a => a.id) })
    ElMessage.success('批量解决成功')
    selectedAlerts.value = []
    loadAll()
  } catch { ElMessage.error('操作失败') }
}

function viewAlert(row: any) { currentAlert.value = row; showDetailDialog.value = true }
function handleSelectionChange(selection: any[]) { selectedAlerts.value = selection }
async function toggleRule(rule: any) {
  try {
    await put(`/monitoring/alerts/${rule.id}`, { enabled: rule.enabled })
    ElMessage.success(`规则${rule.enabled ? '已启用' : '已禁用'}`)
  } catch (error: any) {
    rule.enabled = !rule.enabled
    ElMessage.error(error?.message || '规则状态保存失败')
  }
}
function editRule(rule?: any) {
  ruleForm.value = rule ? { ...rule } : emptyRule()
  showRuleEditDialog.value = true
}
async function saveRule() {
  if (!ruleForm.value.name.trim() || !ruleForm.value.metric.trim()) return ElMessage.warning('请填写规则名称和监控指标')
  ruleSaving.value = true
  try {
    const payload = { ...ruleForm.value }
    if (payload.id) await put(`/monitoring/alerts/${payload.id}`, payload)
    else await post('/monitoring/alerts', payload)
    showRuleEditDialog.value = false
    await loadRules()
    ElMessage.success('告警规则已保存')
  } catch (error: any) {
    ElMessage.error(error?.message || '告警规则保存失败')
  } finally {
    ruleSaving.value = false
  }
}
async function deleteRule(rule: any) {
  try {
    await ElMessageBox.confirm(`确认删除规则“${rule.name}”？`, '删除告警规则', { type: 'warning' })
    await del(`/monitoring/alerts/${rule.id}`)
    await loadRules()
    ElMessage.success('规则已删除')
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '规则删除失败')
  }
}

function getLevelType(level: string) { const m: Record<string, string> = { critical: 'danger', warning: 'warning', info: 'info' }; return m[level] || 'info' }
function getLevelLabel(level: string) { const m: Record<string, string> = { critical: '严重', warning: '警告', info: '信息' }; return m[level] || level }
function getLevelColor(level: string) { const m: Record<string, string> = { critical: '#ef4444', warning: '#f59e0b', info: '#3b82f6' }; return m[level] || '#909399' }
function getStatusType(status: string) { const m: Record<string, string> = { active: 'danger', acknowledged: 'warning', resolved: 'success' }; return m[status] || 'info' }
function getStatusLabel(status: string) { const m: Record<string, string> = { active: '活跃', acknowledged: '已确认', resolved: '已解决' }; return m[status] || status }
function getProgressColor(value: number) { if (value >= 90) return '#ef4444'; if (value >= 70) return '#f59e0b'; return '#10b981' }
function formatDate(date: string) { if (!date) return '-'; return new Date(date).toLocaleString('zh-CN') }

onMounted(loadAll)
</script>

<style lang="scss" scoped>
.page-container {
  padding: 20px;

  .stats-row, .resource-row {
    margin-bottom: 20px;
  }

  .gauge-container {
    text-align: center;
    padding: 20px 0;
    .gauge-label { font-size: 24px; font-weight: 700; margin-top: 10px; }
  }

  .alert-title {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .rule-toolbar { display: flex; justify-content: flex-end; margin-bottom: 12px; }
}
</style>
