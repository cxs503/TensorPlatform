<template>
  <div class="data-source-page">
    <PageHeader title="生产数据接入" desc="连接实际生产环境数据源（MySQL / TDengine / InfluxDB / OPC UA），管理数据采集任务" />

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :sm="6">
        <StatCard
          title="数据源总数"
          :value="stats.totalSources"
          icon="Connection"
          icon-color="#409EFF"
          bg-color="rgba(64,158,255,0.12)"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="已连接"
          :value="stats.connectedSources"
          icon="Link"
          icon-color="#67C23A"
          bg-color="rgba(103,194,58,0.12)"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="采集任务"
          :value="stats.totalTasks"
          icon="Collection"
          icon-color="#E6A23C"
          bg-color="rgba(230,162,60,0.12)"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="运行中任务"
          :value="stats.runningTasks"
          icon="VideoPlay"
          icon-color="#67C23A"
          bg-color="rgba(103,194,58,0.12)"
        />
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab" class="main-tabs">
      <!-- 数据源连接管理 -->
      <el-tab-pane label="数据源连接" name="sources">
        <DataTable
          title="已配置数据源"
          :data="dataSources"
          :loading="loadingSources"
          :error="sourcesError"
          :empty="(dataSources?.length ?? 0) === 0"
          empty-text="暂无数据源，点击上方按钮添加"
          @retry="loadSources"
        >
          <template #header-actions>
            <el-button size="small" @click="loadSources">
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
            <el-button v-if="userStore.hasPermission('datasets:connect')" type="primary" size="small" @click="openAddDialog">
              <el-icon><Plus /></el-icon>
              添加数据源
            </el-button>
          </template>

          <el-table-column label="数据源名称" min-width="180">
            <template #default="{ row }">
              <div class="source-name">
                <el-icon class="source-icon" :style="{ color: getTypeColor(row.type) }">
                  <component :is="getTypeIcon(row.type)" />
                </el-icon>
                <span>{{ row.name }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="120">
            <template #default="{ row }">
              <el-tag :type="getTypeTagType(row.type)" size="small">{{ getTypeLabel(row.type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="连接地址" width="220">
            <template #default="{ row }">
              <span v-if="row.type === 'opcua'">{{ row.endpoint || '-' }}</span>
              <span v-else>{{ row.host || '-' }}:{{ row.port || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="getSourceStatusType(row.status)" size="small">
                {{ getSourceStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="340" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="userStore.hasPermission('datasets:connect') && row.status !== 'connected'"
                text size="small" type="success"
                @click="connectSource(row)"
                :loading="connecting === row.id"
              >
                <el-icon><Link /></el-icon>
                连接
              </el-button>
              <el-button
                v-else-if="userStore.hasPermission('datasets:connect')"
                text size="small" type="warning"
                @click="disconnectSource(row)"
                :loading="disconnecting === row.id"
              >
                <el-icon><SwitchButton /></el-icon>
                断开
              </el-button>
              <el-button v-if="userStore.hasPermission('datasets:connect')" text size="small" @click="testSource(row)" :loading="testing === row.id">
                <el-icon><Connection /></el-icon>
                测试
              </el-button>
              <el-button v-if="userStore.hasPermission('datasets:connect')" text size="small" @click="showQueryDialog(row)">
                <el-icon><Search /></el-icon>
                查询
              </el-button>
              <el-button text size="small" @click="showTables(row)" :loading="loadingTables === row.id">
                <el-icon><Grid /></el-icon>
                表
              </el-button>
              <el-button v-if="userStore.hasPermission('datasets:connect') && userStore.hasPermission('datasets:upload') && ['mysql', 'tdengine'].includes(row.type)" text size="small" type="primary" @click="openImportDialog(row)">
                <el-icon><Download /></el-icon>
                导入
              </el-button>
              <el-button v-if="userStore.hasPermission('datasets:connect')" text size="small" type="danger" @click="deleteSource(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- 采集任务管理 -->
      <el-tab-pane label="采集任务" name="collection">
        <DataTable
          title="采集任务列表"
          :data="collectionTasks"
          :loading="loadingTasks"
          :error="tasksError"
          :empty="(collectionTasks?.length ?? 0) === 0"
          empty-text="暂无采集任务"
          @retry="loadTasks"
        >
          <template #header-actions>
            <el-button size="small" @click="loadTasks">
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
            <el-button type="primary" size="small" @click="openCreateTaskDialog">
              <el-icon><Plus /></el-icon>
              新建任务
            </el-button>
          </template>

          <el-table-column prop="name" label="任务名称" min-width="150" />
          <el-table-column prop="source_type" label="数据源类型" width="120">
            <template #default="{ row }">
              <el-tag size="small">{{ getSourceTypeLabel(row.source_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="采集间隔" width="100">
            <template #default="{ row }">{{ row.collection_interval }}秒</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="getTaskStatusType(row.status)" size="small">
                {{ getTaskStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="已采集" width="100" align="center">
            <template #default="{ row }">{{ formatNumber(row.total_collected) }}</template>
          </el-table-column>
          <el-table-column label="错误数" width="80" align="center">
            <template #default="{ row }">
              <span :class="{ 'error-text': row.error_count > 0 }">{{ row.error_count }}</span>
            </template>
          </el-table-column>
          <el-table-column label="最后采集" width="160">
            <template #default="{ row }">
              {{ formatTime(row.last_collection_time) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status !== 'running'"
                text size="small" type="success"
                @click="startTask(row)"
                :loading="taskActionLoading === row.id"
              >
                <el-icon><VideoPlay /></el-icon>
                启动
              </el-button>
              <el-button
                v-if="row.status === 'running'"
                text size="small" type="warning"
                @click="pauseTask(row)"
                :loading="taskActionLoading === row.id"
              >
                <el-icon><VideoPause /></el-icon>
                暂停
              </el-button>
              <el-button
                v-if="row.status === 'running'"
                text size="small" type="danger"
                @click="stopTask(row)"
                :loading="taskActionLoading === row.id"
              >
                <el-icon><Close /></el-icon>
                停止
              </el-button>
              <el-button text size="small" type="danger" @click="deleteTask(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>
    </el-tabs>

    <!-- 添加数据源对话框 -->
    <el-dialog v-model="showAddDialog" title="添加数据源" width="600px" destroy-on-close>
      <el-form :model="addForm" label-width="100px">
        <el-form-item label="数据源名称" required>
          <el-input v-model="addForm.name" placeholder="请输入数据源名称" />
        </el-form-item>
        <el-form-item label="数据源类型" required>
          <el-select v-model="addForm.type" placeholder="请选择数据源类型" style="width: 100%" @change="onTypeChange">
            <el-option
              v-for="t in sourceTypes"
              :key="t.id"
              :label="t.name + (t.available === false ? '（驱动未安装）' : '')"
              :value="t.id"
              :disabled="t.available === false"
            />
          </el-select>
        </el-form-item>

        <!-- MySQL 配置 -->
        <template v-if="addForm.type === 'mysql'">
          <el-form-item label="主机地址" required>
            <el-input v-model="addForm.mysql.host" placeholder="localhost" />
          </el-form-item>
          <el-form-item label="端口">
            <el-input-number v-model="addForm.mysql.port" :min="1" :max="65535" style="width: 100%" />
          </el-form-item>
          <el-form-item label="数据库名" required>
            <el-input v-model="addForm.mysql.database" placeholder="database" />
          </el-form-item>
          <el-form-item label="用户名" required>
            <el-input v-model="addForm.mysql.username" placeholder="root" />
          </el-form-item>
          <el-form-item label="密码" required>
            <el-input v-model="addForm.mysql.password" type="password" placeholder="********" show-password />
          </el-form-item>
        </template>

        <!-- InfluxDB 配置 -->
        <template v-if="addForm.type === 'influxdb'">
          <el-form-item label="主机地址" required>
            <el-input v-model="addForm.influxdb.host" placeholder="localhost" />
          </el-form-item>
          <el-form-item label="端口">
            <el-input-number v-model="addForm.influxdb.port" :min="1" :max="65535" style="width: 100%" />
          </el-form-item>
          <el-form-item label="数据库名" required>
            <el-input v-model="addForm.influxdb.database" placeholder="bucket/database" />
          </el-form-item>
          <el-form-item label="用户名">
            <el-input v-model="addForm.influxdb.username" placeholder="用户名（可选）" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="addForm.influxdb.password" type="password" placeholder="密码（可选）" show-password />
          </el-form-item>
        </template>

        <!-- OPC UA 配置 -->
        <template v-if="addForm.type === 'opcua'">
          <el-form-item label="端点地址" required>
            <el-input v-model="addForm.opcua.endpoint" placeholder="opc.tcp://host:port" />
          </el-form-item>
          <el-form-item label="用户名">
            <el-input v-model="addForm.opcua.username" placeholder="用户名（可选）" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="addForm.opcua.password" type="password" placeholder="密码（可选）" show-password />
          </el-form-item>
        </template>

        <!-- TDengine 配置（taosAdapter REST） -->
        <template v-if="addForm.type === 'tdengine'">
          <el-form-item label="主机地址" required>
            <el-input v-model="addForm.tdengine.host" placeholder="tdengine" />
          </el-form-item>
          <el-form-item label="REST端口">
            <el-input-number v-model="addForm.tdengine.port" :min="1" :max="65535" style="width:100%" />
          </el-form-item>
          <el-form-item label="数据库名" required>
            <el-input v-model="addForm.tdengine.database" placeholder="foundation_iot" />
          </el-form-item>
          <el-form-item label="用户名" required>
            <el-input v-model="addForm.tdengine.username" placeholder="root" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="addForm.tdengine.password" type="password" show-password placeholder="taosdata" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" @click="addDataSource" :loading="adding">
          添加
        </el-button>
      </template>
    </el-dialog>

    <!-- 创建采集任务对话框 -->
    <el-dialog v-model="showCreateTaskDialog" title="创建采集任务" width="600px" destroy-on-close>
      <el-form :model="taskForm" label-width="120px">
        <el-form-item label="任务名称" required>
          <el-input v-model="taskForm.name" placeholder="输入任务名称" />
        </el-form-item>
        <el-form-item label="数据源类型" required>
          <el-select v-model="taskForm.source_type" placeholder="选择数据源类型" style="width: 100%">
            <el-option label="工业设备" value="industrial_device" />
            <el-option label="数据库" value="database" />
            <el-option label="API接口" value="api" />
            <el-option label="消息队列" value="message_queue" />
            <el-option label="文件系统" value="file_system" />
            <el-option label="IoT设备" value="iot_device" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">连接配置</el-divider>
        <el-form-item label="主机地址">
          <el-input v-model="taskForm.source_config.host" placeholder="主机地址/IP" />
        </el-form-item>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="端口">
              <el-input v-model="taskForm.source_config.port" placeholder="端口号" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="数据库/Topic">
              <el-input v-model="taskForm.source_config.database" placeholder="数据库名/Topic" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="用户名">
              <el-input v-model="taskForm.source_config.username" placeholder="用户名" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="密码">
              <el-input v-model="taskForm.source_config.password" type="password" placeholder="密码" show-password />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">采集配置</el-divider>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="采集间隔(秒)">
              <el-input-number v-model="taskForm.collection_interval" :min="1" :max="86400" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="批次大小">
              <el-input-number v-model="taskForm.batch_size" :min="1" :max="10000" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="showCreateTaskDialog = false">取消</el-button>
        <el-button type="primary" @click="createTask" :loading="creatingTask">创建</el-button>
      </template>
    </el-dialog>

    <!-- 查询对话框 -->
    <el-dialog v-model="showQueryDialogVisible" title="执行查询" width="700px" destroy-on-close>
      <el-form label-width="80px">
        <el-form-item label="数据源">
          <el-tag>{{ currentQuerySource?.name }}</el-tag>
        </el-form-item>
        <el-form-item label="SQL 语句">
          <el-input
            v-model="queryForm.sql"
            type="textarea"
            :rows="4"
            :placeholder="getQueryPlaceholder(currentQuerySource?.type)"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="runQuery" :loading="runningQuery">
            <el-icon><Search /></el-icon>
            执行
          </el-button>
        </el-form-item>
      </el-form>
      <el-table v-if="queryResults.length > 0" :data="queryResults" style="width: 100%" max-height="300">
        <el-table-column
          v-for="col in queryColumns"
          :key="col"
          :prop="col"
          :label="col"
          min-width="120"
        />
      </el-table>
      <div v-if="queryTotal > 0" class="query-result-total">
        共 {{ queryTotal }} 条结果
      </div>
    </el-dialog>

    <!-- 表结构对话框 -->
    <el-dialog v-model="showTablesDialogVisible" :title="`${currentTablesSource?.name} - 表/测量`" width="600px" destroy-on-close>
      <el-table :data="tableList" style="width: 100%" v-loading="loadingTables">
        <el-table-column prop="name" label="名称" min-width="200" />
        <el-table-column v-if="tableList.length > 0 && tableList[0].type" prop="type" label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ row.type || 'table' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 导入数据集对话框 -->
    <el-dialog v-model="showImportDialogVisible" :title="`从 ${getTypeLabel(currentImportSource?.type)} 导入数据集`" width="600px" destroy-on-close>
      <el-form :model="importForm" label-width="110px">
        <el-form-item label="数据源">
          <el-tag>{{ currentImportSource?.name }}</el-tag>
        </el-form-item>
        <el-form-item label="选择数据表" required>
          <el-select v-model="importForm.table" filterable placeholder="请选择要导入的数据表" style="width:100%" :loading="loadingImportTables">
            <el-option v-for="item in importTableList" :key="item.name" :label="item.name" :value="item.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据集名称">
          <el-input v-model="importForm.dataset_name" placeholder="默认使用 数据源名称-表名" />
        </el-form-item>
        <el-form-item label="最大导入行数">
          <el-input-number v-model="importForm.limit" :min="1" :max="10000" :step="100" style="width:100%" />
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon title="导入使用只读 SELECT，不会修改源数据库；单次最多导入 10,000 行。TDengine超级表会同时返回子表数据与标签。" />
      </el-form>
      <template #footer>
        <el-button @click="showImportDialogVisible=false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="importDataset">开始导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Connection, Plus, Refresh, Delete, Link, SwitchButton, Search, Grid,
  VideoPlay, VideoPause, Close, Coin, Timer, Monitor, Collection, Download
} from '@element-plus/icons-vue'
import { get, post, del } from '@/api/legacy'
import { useAsyncData } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import DataTable from '@/components/DataTable.vue'
import StatCard from '@/components/StatCard.vue'
import dayjs from 'dayjs'
import { useUserStore } from '@/stores/user'

// ===== 状态 =====
const activeTab = ref('sources')
const adding = ref(false)
const creatingTask = ref(false)
const runningQuery = ref(false)
const connecting = ref<string | null>(null)
const disconnecting = ref<string | null>(null)
const testing = ref<string | null>(null)
const loadingTables = ref<string | null>(null)
const taskActionLoading = ref<string | null>(null)

const showAddDialog = ref(false)
const showCreateTaskDialog = ref(false)
const userStore = useUserStore()

const showQueryDialogVisible = ref(false)
const showTablesDialogVisible = ref(false)
const showImportDialogVisible = ref(false)
const currentQuerySource = ref<any>(null)
const currentTablesSource = ref<any>(null)
const currentImportSource = ref<any>(null)

const queryForm = ref({ sql: '' })
const queryResults = ref<any[]>([])
const queryColumns = ref<string[]>([])
const queryTotal = ref(0)
const tableList = ref<any[]>([])
const importTableList = ref<any[]>([])
const loadingImportTables = ref(false)
const importing = ref(false)
const importForm = reactive({ table: '', dataset_name: '', limit: 10000 })

// ===== 数据 =====
const {
  data: dataSources,
  loading: loadingSources,
  error: sourcesError,
  refresh: loadSources
} = useAsyncData(async () => {
  const res = await get<any>('/datasources')
  return res?.items || []
})

const {
  data: collectionTasks,
  loading: loadingTasks,
  error: tasksError,
  refresh: loadTasks
} = useAsyncData(async () => {
  const res = await get<any>('/collection/tasks')
  if (Array.isArray(res)) return res
  return res?.tasks || res?.items || []
})

// 数据源类型列表（从接口获取，fallback 到静态定义）
const sourceTypes = ref<{ id: string; name: string; description: string; available?: boolean }[]>([])
async function loadSourceTypes() {
  try {
    const res = await get<any>('/datasources/types')
    sourceTypes.value = res?.types || [
      { id: 'mysql', name: 'MySQL', description: '关系型数据库' },
      { id: 'influxdb', name: 'InfluxDB', description: '时序数据库' },
      { id: 'opcua', name: 'OPC UA', description: '工业设备协议' },
      { id: 'tdengine', name: 'TDengine', description: '工业时序数据库' },
    ]
  } catch {
    sourceTypes.value = [
      { id: 'mysql', name: 'MySQL', description: '关系型数据库' },
      { id: 'influxdb', name: 'InfluxDB', description: '时序数据库' },
      { id: 'opcua', name: 'OPC UA', description: '工业设备协议' },
      { id: 'tdengine', name: 'TDengine', description: '工业时序数据库' },
    ]
  }
}
loadSourceTypes()

// 统计计算
const stats = computed(() => {
  const sources = dataSources.value || []
  const tasks = collectionTasks.value || []
  return {
    totalSources: sources.length,
    connectedSources: sources.filter((s: any) => s.status === 'connected').length,
    totalTasks: tasks.length,
    runningTasks: tasks.filter((t: any) => t.status === 'running').length,
  }
})

// ===== 添加表单 =====
const addForm = reactive({
  name: '',
  type: '',
  mysql: { host: 'localhost', port: 3306, database: '', username: 'root', password: '' },
  influxdb: { host: 'localhost', port: 8086, database: '', username: '', password: '' },
  opcua: { endpoint: 'opc.tcp://localhost:4840', username: '', password: '' },
  tdengine: { host: 'tdengine', port: 6041, database: 'foundation_iot', username: 'root', password: 'taosdata' },
})

function onTypeChange() {
  // reset form fields on type change
}

function openAddDialog() {
  addForm.name = ''
  addForm.type = ''
  addForm.mysql = { host: 'localhost', port: 3306, database: '', username: 'root', password: '' }
  addForm.influxdb = { host: 'localhost', port: 8086, database: '', username: '', password: '' }
  addForm.opcua = { endpoint: 'opc.tcp://localhost:4840', username: '', password: '' }
  addForm.tdengine = { host: 'tdengine', port: 6041, database: 'foundation_iot', username: 'root', password: 'taosdata' }
  showAddDialog.value = true
}

async function addDataSource() {
  if (!addForm.name || !addForm.type) {
    ElMessage.warning('请填写数据源名称和类型')
    return
  }

  const type = addForm.type
  let payload: any = { name: addForm.name }

  if (type === 'mysql') {
    if (!addForm.mysql.host || !addForm.mysql.database) {
      ElMessage.warning('请填写 MySQL 主机地址和数据库名')
      return
    }
    payload = { ...payload, ...addForm.mysql }
  } else if (type === 'influxdb') {
    if (!addForm.influxdb.host || !addForm.influxdb.database) {
      ElMessage.warning('请填写 InfluxDB 主机地址和数据库名')
      return
    }
    payload = { ...payload, ...addForm.influxdb }
  } else if (type === 'opcua') {
    if (!addForm.opcua.endpoint) {
      ElMessage.warning('请填写 OPC UA 端点地址')
      return
    }
    payload = { ...payload, ...addForm.opcua }
  } else if (type === 'tdengine') {
    if (!addForm.tdengine.host || !addForm.tdengine.database || !addForm.tdengine.username) {
      ElMessage.warning('请填写 TDengine 主机、数据库和用户名')
      return
    }
    payload = { ...payload, ...addForm.tdengine }
  }

  adding.value = true
  try {
    await post(`/datasources/${type}`, payload)
    ElMessage.success('数据源添加成功')
    showAddDialog.value = false
    await loadSources()
  } catch (e: any) {
    ElMessage.error('添加失败: ' + (e?.message || e))
  } finally {
    adding.value = false
  }
}

// ===== 数据源操作 =====
async function connectSource(row: any) {
  connecting.value = row.id
  try {
    await post(`/datasources/${row.id}/connect`)
    ElMessage.success('连接成功')
    await loadSources()
  } catch (e: any) {
    ElMessage.error('连接失败: ' + (e?.message || e))
  } finally {
    connecting.value = null
  }
}

async function disconnectSource(row: any) {
  disconnecting.value = row.id
  try {
    await post(`/datasources/${row.id}/disconnect`)
    ElMessage.success('已断开连接')
    await loadSources()
  } catch (e: any) {
    ElMessage.error('断开失败: ' + (e?.message || e))
  } finally {
    disconnecting.value = null
  }
}

async function testSource(row: any) {
  testing.value = row.id
  try {
    const res = await post<any>(`/datasources/${row.id}/test`)
    if (res?.ok) {
      ElMessage.success(res.message || '只读连接测试成功')
    } else {
      ElMessage.warning('连接测试未通过 [' + (res?.code || 'unknown') + ']: ' + (res?.message || '未知错误'))
    }
    await loadSources()
  } catch (e: any) {
    // test endpoint may return non-2xx on failure
    ElMessage.error('连接测试失败: ' + (e?.message || e))
  } finally {
    testing.value = null
  }
}

async function deleteSource(row: any) {
  try {
    await ElMessageBox.confirm(`确定要删除数据源「${row.name}」吗？`, '确认删除', { type: 'warning' })
    await del(`/datasources/${row.id}`)
    ElMessage.success('删除成功')
    await loadSources()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e?.message || e))
    }
  }
}

function showQueryDialog(row: any) {
  currentQuerySource.value = row
  queryForm.value.sql = ''
  queryResults.value = []
  queryColumns.value = []
  queryTotal.value = 0
  showQueryDialogVisible.value = true
}

async function runQuery() {
  if (!queryForm.value.sql.trim()) {
    ElMessage.warning('请输入 SQL 查询语句')
    return
  }
  runningQuery.value = true
  try {
    const res = await post<any>(`/datasources/${currentQuerySource.value.id}/query`, {
      sql: queryForm.value.sql
    })
    const items = res?.items || []
    queryResults.value = items
    queryTotal.value = res?.total || items.length
    if (items.length > 0) {
      queryColumns.value = Object.keys(items[0])
    }
  } catch (e: any) {
    ElMessage.error('查询失败: ' + (e?.message || e))
  } finally {
    runningQuery.value = false
  }
}

async function showTables(row: any) {
  currentTablesSource.value = row
  loadingTables.value = row.id
  showTablesDialogVisible.value = true
  try {
    const res = await get<any>(`/datasources/${row.id}/tables`)
    tableList.value = (res?.items || []).map((item: any) => typeof item === 'string' ? { name: item } : item)
  } catch (e: any) {
    ElMessage.error('获取表列表失败: ' + (e?.message || e))
    tableList.value = []
  } finally {
    loadingTables.value = null
  }
}

async function openImportDialog(row: any) {
  currentImportSource.value = row
  importForm.table = ''
  importForm.dataset_name = ''
  importForm.limit = 10000
  importTableList.value = []
  showImportDialogVisible.value = true
  loadingImportTables.value = true
  try {
    const res = await get<any>(`/datasources/${row.id}/tables`)
    importTableList.value = (res?.items || []).map((item: any) => typeof item === 'string' ? { name: item } : item)
    if (importTableList.value.length === 1) importForm.table = importTableList.value[0].name
  } catch (e: any) {
    ElMessage.error('读取数据表失败: ' + (e?.message || e))
  } finally {
    loadingImportTables.value = false
  }
}

async function importDataset() {
  if (!currentImportSource.value || !importForm.table) {
    ElMessage.warning('请选择要导入的数据表')
    return
  }
  importing.value = true
  try {
    const res = await post<any>(`/datasources/${currentImportSource.value.id}/import`, {
      table: importForm.table,
      dataset_name: importForm.dataset_name || null,
      limit: importForm.limit,
    })
    ElMessage.success(`${res.message}，数据集 ID: ${res.dataset_id}`)
    showImportDialogVisible.value = false
  } catch (e: any) {
    ElMessage.error('导入失败: ' + (e?.message || e))
  } finally {
    importing.value = false
  }
}

// ===== 采集任务操作 =====
const taskForm = reactive({
  name: '',
  source_type: 'industrial_device',
  source_config: { host: '', port: '', database: '', username: '', password: '' },
  collection_interval: 5,
  batch_size: 100,
})

function openCreateTaskDialog() {
  taskForm.name = ''
  taskForm.source_type = 'industrial_device'
  taskForm.source_config = { host: '', port: '', database: '', username: '', password: '' }
  taskForm.collection_interval = 5
  taskForm.batch_size = 100
  showCreateTaskDialog.value = true
}

async function createTask() {
  if (!taskForm.name) {
    ElMessage.warning('请填写任务名称')
    return
  }
  creatingTask.value = true
  try {
    await post('/collection/tasks', {
      name: taskForm.name,
      source_type: taskForm.source_type,
      source_config: taskForm.source_config,
      collection_interval: taskForm.collection_interval,
      batch_size: taskForm.batch_size,
    })
    ElMessage.success('任务创建成功')
    showCreateTaskDialog.value = false
    await loadTasks()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e?.message || e))
  } finally {
    creatingTask.value = false
  }
}

async function startTask(row: any) {
  taskActionLoading.value = row.id
  try {
    await post(`/collection/tasks/${row.id}/start`)
    ElMessage.success('任务已启动')
    await loadTasks()
  } catch (e: any) {
    ElMessage.error('启动失败: ' + (e?.message || e))
  } finally {
    taskActionLoading.value = null
  }
}

async function pauseTask(row: any) {
  taskActionLoading.value = row.id
  try {
    await post(`/collection/tasks/${row.id}/pause`)
    ElMessage.success('任务已暂停')
    await loadTasks()
  } catch (e: any) {
    ElMessage.error('暂停失败: ' + (e?.message || e))
  } finally {
    taskActionLoading.value = null
  }
}

async function stopTask(row: any) {
  taskActionLoading.value = row.id
  try {
    await post(`/collection/tasks/${row.id}/stop`)
    ElMessage.success('任务已停止')
    await loadTasks()
  } catch (e: any) {
    ElMessage.error('停止失败: ' + (e?.message || e))
  } finally {
    taskActionLoading.value = null
  }
}

async function deleteTask(row: any) {
  try {
    await ElMessageBox.confirm(`确定要删除任务「${row.name}」吗？`, '确认删除', { type: 'warning' })
    await del(`/collection/tasks/${row.id}`)
    ElMessage.success('删除成功')
    await loadTasks()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e?.message || e))
    }
  }
}

// ===== 工具函数 =====
function getTypeIcon(type: string) {
  const map: Record<string, any> = {
    mysql: Coin,
    influxdb: Timer,
    opcua: Monitor,
    tdengine: Timer,
  }
  return map[type] || Coin
}

function getTypeColor(type: string) {
  const map: Record<string, string> = {
    mysql: '#3b82f6',
    influxdb: '#10b981',
    opcua: '#f59e0b',
    tdengine: '#0f9d8a',
  }
  return map[type] || '#6b7280'
}

function getTypeTagType(type: string) {
  const map: Record<string, string> = {
    mysql: 'primary',
    influxdb: 'success',
    opcua: 'warning',
    tdengine: 'success',
  }
  return map[type] || 'info'
}

function getTypeLabel(type: string) {
  const map: Record<string, string> = {
    mysql: 'MySQL',
    influxdb: 'InfluxDB',
    opcua: 'OPC UA',
    tdengine: 'TDengine',
  }
  return map[type] || type
}

function getSourceStatusType(status: string) {
  if (status === 'connected') return 'success'
  if (status === 'error') return 'danger'
  return 'info'
}

function getSourceStatusLabel(status: string) {
  if (status === 'connected') return '已连接'
  if (status === 'error') return '连接异常'
  return '未连接'
}

function getSourceTypeLabel(type: string) {
  const map: Record<string, string> = {
    industrial_device: '工业设备',
    database: '数据库',
    api: 'API接口',
    message_queue: '消息队列',
    file_system: '文件系统',
    iot_device: 'IoT设备',
  }
  return map[type] || type
}

function getTaskStatusType(status: string) {
  const map: Record<string, string> = {
    running: 'success',
    paused: 'warning',
    stopped: 'info',
    pending: 'info',
    failed: 'danger',
  }
  return map[status] || 'info'
}

function getTaskStatusLabel(status: string) {
  const map: Record<string, string> = {
    running: '运行中',
    paused: '已暂停',
    stopped: '已停止',
    pending: '等待中',
    failed: '失败',
  }
  return map[status] || status
}

function getQueryPlaceholder(type: string) {
  if (type === 'mysql') return 'SELECT * FROM table_name LIMIT 10;'
  if (type === 'influxdb') return 'SELECT * FROM "measurement" LIMIT 10'
  if (type === 'tdengine') return 'SELECT * FROM furnace_metrics ORDER BY ts DESC LIMIT 10'
  if (type === 'opcua') return 'opcua 使用浏览模式，不支持 SQL 查询'
  return '请输入查询语句'
}

function formatNumber(n: any): string {
  if (n == null) return '0'
  return Number(n).toLocaleString()
}

function formatTime(t: any): string {
  if (!t) return '-'
  return dayjs(t).format('YYYY-MM-DD HH:mm:ss')
}
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.data-source-page {
  .stats-row {
    margin-bottom: 20px;
  }

  .main-tabs {
    :deep(.el-tabs__header) {
      margin-bottom: 16px;
    }
  }

  .source-name {
    display: flex;
    align-items: center;
    gap: 8px;

    .source-icon {
      font-size: 18px;
      flex-shrink: 0;
    }
  }

  .error-text {
    color: #f56c6c;
    font-weight: 600;
  }

  .query-result-total {
    margin-top: 8px;
    font-size: 12px;
    color: $text-secondary;
  }
}
</style>
