<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="page-title"><el-icon><DocumentChecked /></el-icon> 审计日志</h1>
      <p class="page-desc">系统操作审计、安全事件追踪、日志查询与导出</p>
    </div>

    <!-- Stats Cards -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ stats.totalLogs }}</div>
          <div class="stat-label">总日志数</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ stats.todayLogs }}</div>
          <div class="stat-label">今日日志</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card danger">
          <div class="stat-value">{{ stats.securityEvents }}</div>
          <div class="stat-label">安全事件</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card warning">
          <div class="stat-value">{{ stats.failedActions }}</div>
          <div class="stat-label">失败操作</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Search Filters -->
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" :model="filters" class="filter-form">
        <el-form-item label="用户">
          <el-input v-model="filters.user_id" placeholder="用户ID" clearable style="width: 120px;" />
        </el-form-item>
        <el-form-item label="操作类型">
          <el-select v-model="filters.action" placeholder="全部" clearable style="width: 150px;">
            <el-option v-for="a in actionTypes" :key="a.id" :label="a.name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="资源类型">
          <el-input v-model="filters.resource_type" placeholder="如: user, model" clearable style="width: 120px;" />
        </el-form-item>
        <el-form-item label="结果">
          <el-select v-model="filters.result" placeholder="全部" clearable style="width: 100px;">
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failure" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="searchLogs">
            <el-icon><Search /></el-icon> 搜索
          </el-button>
          <el-button @click="resetFilters">
            <el-icon><Refresh /></el-icon> 重置
          </el-button>
          <el-button type="success" @click="showExportDialog = true">
            <el-icon><Download /></el-icon> 导出
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Main Tabs -->
    <el-tabs v-model="activeTab" class="main-tabs">
      <!-- 操作日志 -->
      <el-tab-pane label="操作日志" name="logs">
        <el-table :data="logs" v-loading="loading" stripe>
          <el-table-column prop="timestamp" label="时间" width="180">
            <template #default="{ row }">
              {{ formatDate(row.timestamp) }}
            </template>
          </el-table-column>
          <el-table-column prop="username" label="用户" width="120" />
          <el-table-column prop="action" label="操作" width="140">
            <template #default="{ row }">
              <el-tag size="small">{{ getActionLabel(row.action) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="resource_type" label="资源类型" width="120" />
          <el-table-column prop="resource_id" label="资源ID" width="180" />
          <el-table-column prop="level" label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="getLevelType(row.level)" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="result" label="结果" width="80">
            <template #default="{ row }">
              <el-tag :type="row.result === 'success' ? 'success' : 'danger'" size="small">
                {{ row.result === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" link @click="viewLogDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-wrapper">
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :total="totalLogs"
            layout="total, prev, pager, next"
            @current-change="handlePageChange"
          />
        </div>
      </el-tab-pane>

      <!-- 安全事件 -->
      <el-tab-pane label="安全事件" name="security">
        <el-table :data="securityEvents" v-loading="loadingSecurity" stripe>
          <el-table-column prop="timestamp" label="时间" width="180">
            <template #default="{ row }">
              {{ formatDate(row.timestamp) }}
            </template>
          </el-table-column>
          <el-table-column prop="username" label="用户" width="120" />
          <el-table-column prop="action" label="事件类型" width="150">
            <template #default="{ row }">
              <el-tag type="danger" size="small">{{ getActionLabel(row.action) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="resource_type" label="目标" width="120" />
          <el-table-column prop="ip_address" label="IP地址" width="140" />
          <el-table-column prop="result" label="结果" width="80">
            <template #default="{ row }">
              <el-tag :type="row.result === 'success' ? 'success' : 'danger'" size="small">
                {{ row.result === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="details" label="详情" min-width="200">
            <template #default="{ row }">
              {{ row.details?.message || '-' }}
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 用户活动 -->
      <el-tab-pane label="用户活动" name="activity">
        <el-card shadow="never">
          <el-form :inline="true">
            <el-form-item label="用户ID">
              <el-input v-model="activityUserId" placeholder="输入用户ID" style="width: 150px;" />
            </el-form-item>
            <el-form-item label="统计天数">
              <el-input-number v-model="activityDays" :min="1" :max="90" style="width: 120px;" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="loadUserActivity">查询</el-button>
            </el-form-item>
          </el-form>
          <el-descriptions v-if="userActivity" :column="3" border>
            <el-descriptions-item label="用户ID">{{ userActivity.user_id }}</el-descriptions-item>
            <el-descriptions-item label="用户名">{{ userActivity.username }}</el-descriptions-item>
            <el-descriptions-item label="统计周期">{{ activityDays }}天</el-descriptions-item>
            <el-descriptions-item label="登录次数">{{ userActivity.login_count || 0 }}</el-descriptions-item>
            <el-descriptions-item label="操作总数">{{ userActivity.action_count || 0 }}</el-descriptions-item>
            <el-descriptions-item label="最后登录">{{ formatDate(userActivity.last_login) }}</el-descriptions-item>
          </el-descriptions>
          <div v-if="userActivity?.actions" class="activity-chart">
            <h4>操作分布</h4>
            <el-table :data="Object.entries(userActivity.actions).map(([k, v]) => ({ action: k, count: v }))" size="small">
              <el-table-column prop="action" label="操作类型">
                <template #default="{ row }">
                  {{ getActionLabel(row.action) }}
                </template>
              </el-table-column>
              <el-table-column prop="count" label="次数" width="100" />
            </el-table>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- Log Detail Dialog -->
    <el-dialog v-model="showDetailDialog" title="日志详情" width="700px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="时间">{{ formatDate(currentLog?.timestamp) }}</el-descriptions-item>
        <el-descriptions-item label="用户">{{ currentLog?.username }} ({{ currentLog?.user_id }})</el-descriptions-item>
        <el-descriptions-item label="操作类型">
          <el-tag size="small">{{ getActionLabel(currentLog?.action) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="级别">
          <el-tag :type="getLevelType(currentLog?.level)" size="small">{{ currentLog?.level }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="资源类型">{{ currentLog?.resource_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="资源ID">{{ currentLog?.resource_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="结果">
          <el-tag :type="currentLog?.result === 'success' ? 'success' : 'danger'" size="small">
            {{ currentLog?.result === 'success' ? '成功' : '失败' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="IP地址">{{ currentLog?.ip_address || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="currentLog?.details" class="log-details">
        <h4>详细信息</h4>
        <pre>{{ JSON.stringify(currentLog.details, null, 2) }}</pre>
      </div>
    </el-dialog>

    <!-- Export Dialog -->
    <el-dialog v-model="showExportDialog" title="导出审计日志" width="500px">
      <el-form :model="exportForm" label-width="100px">
        <el-form-item label="开始日期" required>
          <el-date-picker v-model="exportForm.start_date" type="date" placeholder="选择开始日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="结束日期" required>
          <el-date-picker v-model="exportForm.end_date" type="date" placeholder="选择结束日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="导出格式">
          <el-select v-model="exportForm.format" style="width: 100%">
            <el-option label="JSON" value="json" />
            <el-option label="CSV" value="csv" />
            <el-option label="Excel" value="xlsx" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showExportDialog = false">取消</el-button>
        <el-button type="primary" @click="exportLogs" :loading="exporting">导出</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { DocumentChecked, Search, Refresh, Download } from '@element-plus/icons-vue'
import { api } from '@/api'
import { useAsyncData } from '@/composables/useAsyncData'

const activeTab = ref('logs')
const exporting = ref(false)
const showDetailDialog = ref(false)
const showExportDialog = ref(false)
const currentLog = ref<any>(null)
const activityUserId = ref('')
const activityDays = ref(30)
const currentPage = ref(1)
const pageSize = ref(20)

// 使用 useAsyncData 管理数据获取
const { data: logResponse, loading, error: logError, refresh: refreshLogs } = useAsyncData(async () => {
  return await api.audit.search({
    user_id: filters.user_id || undefined,
    action: filters.action || undefined,
    resource_type: filters.resource_type || undefined,
    result: filters.result || undefined,
    page: currentPage.value,
    page_size: pageSize.value
  })
})
const logs = computed(() => logResponse.value?.items || [])
const totalLogs = computed(() => logResponse.value?.total || logs.value.length || 0)

const { data: securityEvents, loading: loadingSecurity, refresh: refreshSecurityEvents } = useAsyncData(async () => {
  const res = await api.audit.getSecurityEvents(7)
  return res.items || []
})

const { data: actionTypes } = useAsyncData(async () => {
  const res = await api.audit.getActions()
  return res.items || res.actions || []
})

const { data: userActivity, loading: activityLoading, refresh: refreshUserActivity } = useAsyncData(async () => {
  if (!activityUserId.value) throw new Error('用户ID为空')
  return await api.audit.getUserActivity(activityUserId.value, activityDays.value)
}, { immediate: false })

const filters = reactive({
  user_id: '',
  action: '',
  resource_type: '',
  result: ''
})

const exportForm = reactive({
  start_date: null as Date | null,
  end_date: null as Date | null,
  format: 'json'
})

const stats = computed(() => ({
  totalLogs: totalLogs.value,
  todayLogs: logs.value.filter(l => {
    const today = new Date().toDateString()
    return new Date(l.timestamp).toDateString() === today
  }).length,
  securityEvents: (securityEvents.value || []).length,
  failedActions: logs.value.filter(l => l.result === 'failure').length
}))

function getLevelType(level: string) {
  const map: Record<string, string> = { info: 'info', warning: 'warning', error: 'danger', critical: 'danger' }
  return map[level] || 'info'
}

function getActionLabel(action: string) {
  if (!action) return '-'
  const map: Record<string, string> = {
    login: '登录',
    logout: '登出',
    create: '创建',
    update: '更新',
    delete: '删除',
    read: '查看',
    export: '导出',
    import: '导入',
    deploy: '部署',
    train: '训练',
    api_call: 'API调用'
  }
  return map[action] || action.replace(/_/g, ' ').toUpperCase()
}

function formatDate(dateStr: string) {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN')
}

function searchLogs() {
  currentPage.value = 1
  refreshLogs()
}

function resetFilters() {
  filters.user_id = ''
  filters.action = ''
  filters.resource_type = ''
  filters.result = ''
  refreshLogs()
}

function handlePageChange(page: number) {
  currentPage.value = page
  refreshLogs()
}

function viewLogDetail(row: any) {
  currentLog.value = row
  showDetailDialog.value = true
}

async function loadUserActivity() {
  if (!activityUserId.value) {
    ElMessage.warning('请输入用户ID')
    return
  }
  await refreshUserActivity()
}

async function exportLogs() {
  if (!exportForm.start_date || !exportForm.end_date) {
    ElMessage.warning('请选择日期范围')
    return
  }
  exporting.value = true
  try {
    const res = await api.audit.export({
      start_date: exportForm.start_date.toISOString().split('T')[0],
      end_date: exportForm.end_date.toISOString().split('T')[0],
      format: exportForm.format
    })
    if (res.data) {
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `audit_logs_${exportForm.start_date}_${exportForm.end_date}.${exportForm.format}`
      a.click()
      URL.revokeObjectURL(url)
    }
    ElMessage.success('导出成功')
    showExportDialog.value = false
  } catch (e: any) {
    ElMessage.error('导出失败: ' + e.message)
  } finally {
    exporting.value = false
  }
}
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.page-container {
  .page-header {
    margin-bottom: 24px;
    .page-title {
      font-size: 24px;
      font-weight: 600;
      color: $text-primary;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .page-desc {
      color: $text-secondary;
      font-size: 14px;
    }
  }
}

.stats-row {
  margin-bottom: 24px;
}

.stat-card {
  text-align: center;
  cursor: pointer;
  transition: transform 0.2s;
  &:hover {
    transform: translateY(-2px);
  }
  .stat-value {
    font-size: 28px;
    font-weight: bold;
    color: $primary-light;
  }
  &.danger .stat-value {
    color: $error-color;
  }
  &.warning .stat-value {
    color: $warning-color;
  }
  .stat-label {
    color: $text-secondary;
    margin-top: 8px;
    font-size: 14px;
  }
}

.filter-card {
  margin-bottom: 16px;
  .filter-form {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
}

.main-tabs {
  margin-top: 16px;
}

.pagination-wrapper {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.log-details, .activity-chart {
  margin-top: 16px;
  h4 {
    margin-bottom: 12px;
    color: $text-primary;
  }
  pre {
    background: $bg-dark;
    padding: 12px;
    border-radius: $radius-md;
    overflow-x: auto;
    font-size: 12px;
  }
}
</style>
