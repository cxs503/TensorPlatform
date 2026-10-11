<template>
  <div class="data-lineage-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">
          <el-icon><Share /></el-icon>
          数据血缘
        </h1>
        <p class="page-desc">自动追踪清洗、特征工程、标注、分割、对齐、训练与模型产出；点击节点可查看上下游影响</p>
      </div>
      <div class="page-actions">
        <el-button @click="showCreateNodeDialog = true"><el-icon><Plus /></el-icon>新建节点</el-button>
        <el-button @click="openCreateLineage"><el-icon><Connection /></el-icon>新建关系</el-button>
        <el-button @click="showSqlDialog = true">SQL导入</el-button>
        <el-button :loading="syncLoading" @click="syncTrainingLineage">同步平台血缘</el-button>
        <el-button @click="openChangelog">变更记录</el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" style="color: #409EFF"><Connection /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.totalNodes }}</div>
              <div class="stat-label">数据节点</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" style="color: #67C23A"><Share /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.totalEdges }}</div>
              <div class="stat-label">血缘关系</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" style="color: #E6A23C"><DataLine /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.tableCount }}</div>
              <div class="stat-label">数据表</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" style="color: #F56C6C"><Document /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.reportCount }}</div>
              <div class="stat-label">数据报表</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="main-content">
      <!-- 左侧节点列表 -->
      <el-col :xs="24" :sm="6" class="sidebar">
        <el-card shadow="never" class="filter-card">
          <template #header>
            <div class="card-header">
              <span>筛选条件</span>
              <el-button text size="small" @click="resetFilters">
                <el-icon><RefreshRight /></el-icon>
                重置
              </el-button>
            </div>
          </template>

          <el-form label-width="70px" size="small">
            <el-form-item label="节点类型">
              <el-select v-model="filterNodeType" placeholder="全部类型" clearable style="width: 100%" @change="loadNodes">
                <el-option label="数据表" value="table" />
                <el-option label="数据源" value="source" />
                <el-option label="字段" value="column" />
                <el-option label="报表" value="report" />
                <el-option label="模型" value="model" />
                <el-option label="数据管道/训练任务" value="pipeline" />
                <el-option label="API" value="api" />
              </el-select>
            </el-form-item>
            <el-form-item label="数据源">
              <el-select v-model="filterDataSource" placeholder="全部数据源" clearable style="width: 100%" @change="loadNodes">
                <el-option v-for="ds in dataSources" :key="ds.id" :label="ds.name" :value="ds.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="搜索">
              <el-input v-model="searchQuery" placeholder="搜索节点名称..." clearable @keyup.enter="loadNodes">
                <template #prefix>
                  <el-icon><Search /></el-icon>
                </template>
              </el-input>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" class="nodes-card">
          <template #header>
            <div class="card-header">
              <span>节点列表</span>
              <el-tag size="small">{{ filteredNodes.length }}</el-tag>
            </div>
          </template>

          <el-scrollbar height="400px">
            <div class="node-list">
              <div
                v-for="node in filteredNodes"
                :key="node.id"
                :class="['node-item', { active: selectedNode?.id === node.id }]"
                @click="selectNode(node)"
              >
                <div class="node-icon">
                  <el-icon :class="['type-icon', node.type]">
                    <component :is="getNodeIcon(node.type)" />
                  </el-icon>
                </div>
                <div class="node-info">
                  <div class="node-name">{{ node.name }}</div>
                  <div class="node-meta">
                    <span>{{ getNodeTypeName(node.type) }}</span>
                    <span v-if="node.database">{{ node.database }}</span>
                  </div>
                </div>
                <el-button text size="small" title="查看详情" @click.stop="viewNodeDetail(node)">
                  <el-icon><View /></el-icon>
                </el-button>
              </div>
            </div>
          </el-scrollbar>
        </el-card>
      </el-col>

      <!-- 右侧血缘图谱 -->
      <el-col :xs="24" :sm="18" class="content-area">
        <el-card shadow="never" class="graph-card">
          <template #header>
            <div class="card-header">
              <div class="header-left">
                <span>血缘图谱</span>
                <el-tag v-if="selectedNode" type="primary" size="small">
                  当前节点: {{ selectedNode.name }}
                </el-tag>
              </div>
              <div class="header-right">
                <el-button-group>
                  <el-button :type="graphMode === 'all' ? 'primary' : ''" size="small" @click="setGraphMode('all')">
                    全部
                  </el-button>
                  <el-button :type="graphMode === 'upstream' ? 'primary' : ''" size="small" @click="setGraphMode('upstream')">
                    上游
                  </el-button>
                  <el-button :type="graphMode === 'downstream' ? 'primary' : ''" size="small" @click="setGraphMode('downstream')">
                    下游
                  </el-button>
                </el-button-group>
                <el-select v-model="depthLevel" placeholder="血缘深度" size="small" style="width: 100px" @change="loadLineageGraph">
                  <el-option :value="1" label="1层" />
                  <el-option :value="2" label="2层" />
                  <el-option :value="3" label="3层" />
                  <el-option :value="5" label="5层" />
                </el-select>
                <el-button size="small" @click="refreshGraph">
                  <el-icon><Refresh /></el-icon>
                  刷新
                </el-button>
                <el-button size="small" @click="exportGraph">
                  <el-icon><Download /></el-icon>
                  导出
                </el-button>
              </div>
            </div>
          </template>

          <div ref="chartRef" class="lineage-chart" v-loading="graphLoading"></div>
        </el-card>

        <!-- 影响分析 -->
        <el-row :gutter="16" style="margin-top: 16px">
          <el-col :span="12">
            <el-card shadow="never" class="impact-card">
              <template #header>
                <div class="card-header">
                  <span>上游影响分析</span>
                  <el-tag size="small" type="info">{{ upstreamNodes.length }} 个节点</el-tag>
                </div>
              </template>
              <el-table :data="upstreamNodes.slice(0, 5)" size="small" max-height="200">
                <el-table-column prop="name" label="节点名称" min-width="120" />
                <el-table-column prop="type" label="类型" width="80">
                  <template #default="{ row }">
                    <el-tag size="small" :type="getTypeTagType(row.type)">
                      {{ getNodeTypeName(row.type) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="impact" label="影响程度" width="100">
                  <template #default="{ row }">
                    <el-progress :percentage="row.impact_score ?? 0" :stroke-width="6" size="small" />
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
          <el-col :span="12">
            <el-card shadow="never" class="impact-card">
              <template #header>
                <div class="card-header">
                  <span>下游影响分析</span>
                  <el-tag size="small" type="warning">{{ downstreamNodes.length }} 个节点</el-tag>
                </div>
              </template>
              <el-table :data="downstreamNodes.slice(0, 5)" size="small" max-height="200">
                <el-table-column prop="name" label="节点名称" min-width="120" />
                <el-table-column prop="type" label="类型" width="80">
                  <template #default="{ row }">
                    <el-tag size="small" :type="getTypeTagType(row.type)">
                      {{ getNodeTypeName(row.type) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="impact" label="影响程度" width="100">
                  <template #default="{ row }">
                    <el-progress :percentage="row.impact_score ?? 0" :stroke-width="6" size="small" />
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>
      </el-col>
    </el-row>

    <!-- 节点详情抽屉 -->
    <el-drawer v-model="showDetailDrawer" title="节点详情" size="40%">
      <div class="detail-content" v-if="currentNode">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="节点名称">{{ currentNode.name }}</el-descriptions-item>
          <el-descriptions-item label="节点类型">
            <el-tag :type="getTypeTagType(currentNode.type)">{{ getNodeTypeName(currentNode.type) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="所属系统">{{ currentNode.system || '-' }}</el-descriptions-item>
          <el-descriptions-item label="数据库">{{ currentNode.database || '-' }}</el-descriptions-item>
          <el-descriptions-item label="Schema">{{ currentNode.schema || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(currentNode.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDate(currentNode.updated_at) }}</el-descriptions-item>
          <el-descriptions-item label="关联实体" v-if="currentNode.resource_type">
            <el-tag size="small" type="success">{{ currentNode.resource_type === 'dataset' ? '数据集' : currentNode.resource_type === 'model' ? '模型' : currentNode.resource_type === 'training_job' ? '训练任务' : currentNode.resource_type }}</el-tag>
            <span style="margin-left:6px">#{{ currentNode.resource_id }}<span v-if="currentNode.resource_name" style="color:var(--el-text-color-secondary);margin-left:4px">（{{ currentNode.resource_name }}）</span></span>
          </el-descriptions-item>
        </el-descriptions>

        <div class="detail-section">
          <h4>字段信息</h4>
          <el-table :data="currentNode.columns || []" size="small" max-height="200">
            <el-table-column prop="name" label="字段名" width="120" />
            <el-table-column prop="type" label="类型" width="100" />
            <el-table-column prop="description" label="描述" />
            <el-table-column prop="nullable" label="可空" width="60">
              <template #default="{ row }">
                <el-tag size="small" :type="row.nullable ? 'warning' : 'success'">
                  {{ row.nullable ? '是' : '否' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="detail-section">
          <h4>血缘关系</h4>
          <el-tabs>
            <el-tab-pane :label="`上游 (${upstreamNodes.length})`">
              <div class="relation-list">
                <div v-for="node in upstreamNodes" :key="node.id" class="relation-item" @click="selectNode(node)">
                  <el-icon><Upload /></el-icon>
                  <span>{{ node.name }}</span>
                  <el-tag size="small" type="info">{{ node.relationship || '依赖' }}</el-tag>
                </div>
                <el-empty v-if="upstreamNodes.length === 0" description="暂无上游关系" />
              </div>
            </el-tab-pane>
            <el-tab-pane :label="`下游 (${downstreamNodes.length})`">
              <div class="relation-list">
                <div v-for="node in downstreamNodes" :key="node.id" class="relation-item" @click="selectNode(node)">
                  <el-icon><Download /></el-icon>
                  <span>{{ node.name }}</span>
                  <el-tag size="small" type="info">{{ node.relationship || '影响' }}</el-tag>
                </div>
                <el-empty v-if="downstreamNodes.length === 0" description="暂无下游关系" />
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
      </div>
    </el-drawer>

    <!-- 创建节点对话框 -->
    <el-dialog v-model="showCreateNodeDialog" title="新建血缘节点" width="560px">
      <el-form :model="nodeForm" label-width="100px">
        <el-form-item label="节点名称" required>
          <el-input v-model="nodeForm.name" placeholder="例如：cleaned_temperature_data" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="nodeForm.display_name" placeholder="例如：清洗后温度数据" />
        </el-form-item>
        <el-form-item label="节点类型" required>
          <el-select v-model="nodeForm.node_type" style="width:100%">
            <el-option label="数据源" value="source" />
            <el-option label="数据表" value="table" />
            <el-option label="字段" value="column" />
            <el-option label="报表" value="report" />
            <el-option label="模型" value="model" />
            <el-option label="数据管道/训练任务" value="pipeline" />
            <el-option label="API" value="api" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属系统">
          <el-input v-model="nodeForm.system" placeholder="例如：data_platform" />
        </el-form-item>
        <el-form-item label="数据库">
          <el-input v-model="nodeForm.database" placeholder="可选" />
        </el-form-item>
        <el-form-item label="Schema">
          <el-input v-model="nodeForm.schema_name" placeholder="可选" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="nodeForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateNodeDialog = false">取消</el-button>
        <el-button type="primary" :loading="nodeCreating" @click="createNode">创建</el-button>
      </template>
    </el-dialog>

    <!-- 创建血缘关系对话框 -->
    <el-dialog v-model="showCreateDialog" title="创建血缘关系" width="500px">
      <el-form :model="lineageForm" label-width="100px">
        <el-form-item label="源节点" required>
          <el-select v-model="lineageForm.source_id" filterable placeholder="选择源节点" style="width: 100%">
            <el-option v-for="node in relationNodes" :key="node.id" :label="node.name" :value="node.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标节点" required>
          <el-select v-model="lineageForm.target_id" filterable placeholder="选择目标节点" style="width: 100%">
            <el-option v-for="node in relationNodes" :key="node.id" :label="node.name" :value="node.id" :disabled="node.id === lineageForm.source_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="关系类型">
          <el-select v-model="lineageForm.edge_type" style="width: 100%">
            <el-option label="派生" value="derived" />
            <el-option label="转换" value="transform" />
            <el-option label="合并" value="join" />
            <el-option label="聚合" value="aggregate" />
            <el-option label="复制" value="copy" />
            <el-option label="用于训练" value="train" />
            <el-option label="生成模型" value="produces" />
          </el-select>
        </el-form-item>
        <el-form-item label="转换逻辑">
          <el-input v-model="lineageForm.transformation" type="textarea" :rows="3" placeholder="描述数据转换逻辑或SQL" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createLineage" :loading="creating">创建</el-button>
      </template>
    </el-dialog>

    <!-- SQL血缘导入 -->
    <el-dialog v-model="showSqlDialog" title="从SQL导入血缘" width="760px">
      <el-alert title="支持 INSERT INTO ... SELECT、CREATE TABLE AS SELECT 等语句；解析后会保存表节点与转换关系。" type="info" :closable="false" show-icon />
      <el-form :model="sqlForm" label-width="90px" class="sql-form">
        <el-form-item label="数据库">
          <el-input v-model="sqlForm.database" placeholder="例如：foundation_platform（可选）" />
        </el-form-item>
        <el-form-item label="Schema">
          <el-input v-model="sqlForm.schema_name" placeholder="例如：public（可选）" />
        </el-form-item>
        <el-form-item label="SQL" required>
          <el-input v-model="sqlForm.sql" type="textarea" :rows="9" placeholder="INSERT INTO cleaned_data SELECT * FROM raw_data WHERE value IS NOT NULL" />
        </el-form-item>
      </el-form>
      <el-descriptions v-if="sqlResult" :column="3" border size="small">
        <el-descriptions-item label="解析器">{{ sqlResult.provider || '-' }}</el-descriptions-item>
        <el-descriptions-item label="新增节点">{{ sqlResult.created_node_count || 0 }}</el-descriptions-item>
        <el-descriptions-item label="新增关系">{{ sqlResult.created_edge_count || 0 }}</el-descriptions-item>
        <el-descriptions-item label="结果" :span="3">{{ sqlResult.message }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="showSqlDialog = false">关闭</el-button>
        <el-button type="primary" :loading="sqlLoading" @click="importSqlLineage">解析并保存</el-button>
      </template>
    </el-dialog>

    <!-- 变更记录 -->
    <el-dialog v-model="showChangelogDialog" title="血缘变更记录" width="900px">
      <el-table :data="changelog" v-loading="changelogLoading" max-height="520">
        <el-table-column prop="change_type" label="操作" width="90" />
        <el-table-column label="对象" min-width="220">
          <template #default="{ row }">{{ row.node_id || row.edge_id || '-' }}</template>
        </el-table-column>
        <el-table-column prop="created_by" label="操作人" width="140" />
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Share, Connection, DataLine, Document, RefreshRight, Search, View, Refresh,
  Download, Upload, Files, DataBoard, Timer, Cpu, Plus, Grid
} from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { api } from '@/api'

// 状态
const creating = ref(false)
const nodeCreating = ref(false)
const syncLoading = ref(false)
const sqlLoading = ref(false)
const changelogLoading = ref(false)
const loading = ref(false)
const graphLoading = ref(false)
const nodes = ref<any[]>([])
const relationNodes = ref<any[]>([])
const dataSources = ref<any[]>([])
const stats = ref({ totalNodes: 0, totalEdges: 0, tableCount: 0, reportCount: 0 })
const chartRef = ref<HTMLElement>()
let chartInstance: echarts.ECharts | null = null

const filterNodeType = ref('')
const filterDataSource = ref('')
const searchQuery = ref('')
const graphMode = ref<'all' | 'upstream' | 'downstream'>('all')
const depthLevel = ref(3)

const selectedNode = ref<any>(null)
const currentNode = ref<any>(null)
const upstreamNodes = ref<any[]>([])
const downstreamNodes = ref<any[]>([])

const showDetailDrawer = ref(false)
const showCreateDialog = ref(false)
const showCreateNodeDialog = ref(false)
const showSqlDialog = ref(false)
const showChangelogDialog = ref(false)
const sqlResult = ref<any>(null)
const changelog = ref<any[]>([])

const nodeForm = reactive({
  name: '',
  display_name: '',
  node_type: 'table',
  system: 'data_platform',
  database: '',
  schema_name: '',
  description: ''
})

const lineageForm = reactive({
  source_id: '',
  target_id: '',
  edge_type: 'derived',
  transformation: ''
})

const sqlForm = reactive({
  database: '',
  schema_name: '',
  sql: ''
})

// 计算属性
const filteredNodes = computed(() => {
  let result = nodes.value
  if (filterNodeType.value) {
    result = result.filter(n => n.type === filterNodeType.value)
  }
  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(n => n.name.toLowerCase().includes(query))
  }
  return result
})

// 方法
function getNodeIcon(type: string) {
  const icons: Record<string, any> = {
    table: Grid,
    source: Upload,
    column: Files,
    report: DataBoard,
    model: Cpu,
    pipeline: Timer,
    api: Cpu
  }
  return icons[type] || Grid
}

function getNodeTypeName(type: string) {
  const names: Record<string, string> = {
    table: '数据表',
    source: '数据源',
    column: '字段',
    report: '报表',
    model: '模型',
    pipeline: '数据管道/训练任务',
    api: 'API'
  }
  return names[type] || type
}

function getTypeTagType(type: string) {
  const types: Record<string, string> = {
    table: 'primary',
    source: 'success',
    column: 'info',
    report: 'warning',
    model: 'danger',
    pipeline: 'info',
    api: ''
  }
  return types[type] || 'info'
}

function formatDate(date: string) {
  if (!date) return '-'
  return new Date(date).toLocaleString()
}

async function loadStats() {
  try {
    const res = await api.lineage.getStats()
    stats.value = {
      totalNodes: res?.nodes?.total || 0,
      totalEdges: res?.edges?.total || 0,
      tableCount: res?.nodes?.by_type?.table || 0,
      reportCount: res?.nodes?.by_type?.report || 0
    }
    dataSources.value = Object.entries(res?.nodes?.by_system || {}).map(([system, count]) => ({
      id: system,
      name: `${system}（${count}）`
    }))
  } catch (error) {
    console.error('加载统计失败:', error)
  }
}

async function loadNodes() {
  loading.value = true
  try {
    const params: any = {}
    if (filterNodeType.value) params.node_type = filterNodeType.value
    if (filterDataSource.value) params.system = filterDataSource.value
    if (searchQuery.value) params.search = searchQuery.value

    const res = await api.lineage.getNodes(params)
    nodes.value = (res.items || res || []).map((node: any) => ({ ...node, type: node.type || node.node_type }))
  } catch (error) {
    console.error('加载节点失败:', error)
    nodes.value = []
  } finally {
    loading.value = false
  }
}

async function selectNode(node: any) {
  selectedNode.value = node
  await loadLineageGraph()
}

async function loadLineageGraph() {
  if (!selectedNode.value) return

  graphLoading.value = true
  try {
    const direction = graphMode.value === 'all' ? 'both' : graphMode.value
    const [res, upstream, downstream] = await Promise.all([
      api.lineage.getSubgraph(selectedNode.value.id, { depth: depthLevel.value, direction }),
      graphMode.value === 'downstream'
        ? Promise.resolve({ source_nodes: [], source_edges: [] })
        : api.lineage.getUpstream(selectedNode.value.id, { depth: depthLevel.value }),
      graphMode.value === 'upstream'
        ? Promise.resolve({ downstream_nodes: [], downstream_edges: [] })
        : api.lineage.getDownstream(selectedNode.value.id, { depth: depthLevel.value })
    ])
    const graphNodes = (res.nodes || []).map((node: any) => ({ ...node, type: node.type || node.node_type }))
    const upstreamEdges = new Map((upstream.source_edges || []).map((edge: any) => [edge.source_id, edge]))
    const downstreamEdges = new Map((downstream.downstream_edges || []).map((edge: any) => [edge.target_id, edge]))
    upstreamNodes.value = (upstream.source_nodes || []).map((node: any) => ({
      ...node,
      type: node.type || node.node_type,
      relationship: (upstreamEdges.get(node.id) as any)?.edge_type || '依赖'
    }))
    downstreamNodes.value = (downstream.downstream_nodes || []).map((node: any) => ({
      ...node,
      type: node.type || node.node_type,
      relationship: (downstreamEdges.get(node.id) as any)?.edge_type || '影响'
    }))

    await nextTick()
    renderGraph({ ...res, nodes: graphNodes })
  } catch (error) {
    console.error('加载血缘图谱失败:', error)
    renderEmptyGraph()
  } finally {
    graphLoading.value = false
  }
}

function renderGraph(data: any) {
  if (!chartRef.value) return

  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  const nodes = (data.nodes || []).map((n: any) => ({
    id: n.id,
    name: n.name,
    symbolSize: n.type === 'table' ? 50 : 40,
    category: n.type,
    x: n.x,
    y: n.y,
    itemStyle: {
      color: getNodeColor(n.type)
    }
  }))

  const edges = (data.edges || []).map((e: any) => ({
    source: e.source_id || e.source,
    target: e.target_id || e.target,
    value: e.edge_type || e.relationship,
    lineStyle: {
      color: '#aaa',
      curveness: 0.2
    }
  }))

  const option = {
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          return `${params.data.name}<br/>类型: ${getNodeTypeName(params.data.category)}`
        }
        return `${params.data.source} → ${params.data.target}`
      }
    },
    legend: {
      data: ['source', 'table', 'column', 'pipeline', 'model', 'report', 'api'],
      orient: 'vertical',
      right: 10,
      top: 10,
      formatter: (name: string) => getNodeTypeName(name)
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        data: nodes,
        links: edges,
        categories: [
          { name: 'source', itemStyle: { color: '#67C23A' } },
          { name: 'table', itemStyle: { color: '#409EFF' } },
          { name: 'column', itemStyle: { color: '#14B8A6' } },
          { name: 'pipeline', itemStyle: { color: '#909399' } },
          { name: 'model', itemStyle: { color: '#F56C6C' } },
          { name: 'report', itemStyle: { color: '#E6A23C' } },
          { name: 'api', itemStyle: { color: '#F56C6C' } }
        ],
        roam: true,
        label: {
          show: true,
          position: 'bottom',
          fontSize: 12
        },
        force: {
          repulsion: 200,
          edgeLength: [50, 150],
          gravity: 0.1
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            width: 3
          }
        },
        lineStyle: {
          opacity: 0.9,
          width: 2,
          curveness: 0.2
        }
      }
    ]
  }

  chartInstance.setOption(option)
  chartInstance.off('click')
  chartInstance.on('click', (params: any) => {
    if (params.dataType !== 'node') return
    const node = (data.nodes || []).find((item: any) => item.id === params.data.id)
    if (node) selectNode(node)
  })
}

function renderEmptyGraph() {
  if (!chartRef.value || !chartInstance) return

  chartInstance.setOption({
    title: {
      text: '请选择节点查看血缘关系',
      left: 'center',
      top: 'center',
      textStyle: {
        color: '#909399',
        fontSize: 14
      }
    }
  })
}

function getNodeColor(type: string) {
  const colors: Record<string, string> = {
    source: '#67C23A',
    table: '#409EFF',
    column: '#14B8A6',
    pipeline: '#909399',
    model: '#F56C6C',
    report: '#E6A23C',
    api: '#F56C6C'
  }
  return colors[type] || '#409EFF'
}

function setGraphMode(mode: 'all' | 'upstream' | 'downstream') {
  graphMode.value = mode
  loadLineageGraph()
}

function resetFilters() {
  filterNodeType.value = ''
  filterDataSource.value = ''
  searchQuery.value = ''
  loadNodes()
}

async function viewNodeDetail(node: any) {
  try {
    selectedNode.value = node
    const [res] = await Promise.all([
      api.lineage.getNode(node.id),
      loadLineageGraph()
    ])
    const metadata = res.metadata || {}
    res.columns = res.columns || metadata.columns || []
    currentNode.value = res
    showDetailDrawer.value = true
  } catch (error) {
    console.error('加载节点详情失败:', error)
  }
}

async function openCreateLineage() {
  try {
    const result = await api.lineage.getNodes({ limit: 1000, is_active: true })
    relationNodes.value = (result.items || []).map((node: any) => ({
      ...node,
      type: node.type || node.node_type
    }))
    if (relationNodes.value.length < 2) {
      ElMessage.warning('至少需要两个节点才能创建关系')
      return
    }
    lineageForm.source_id = selectedNode.value?.id || ''
    lineageForm.target_id = ''
    lineageForm.edge_type = 'derived'
    lineageForm.transformation = ''
    showCreateDialog.value = true
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error.message || '读取节点列表失败')
  }
}

async function createNode() {
  if (!nodeForm.name.trim() || !nodeForm.node_type) {
    ElMessage.warning('请填写节点名称和类型')
    return
  }
  nodeCreating.value = true
  try {
    const result = await api.lineage.createNode({
      ...nodeForm,
      name: nodeForm.name.trim(),
      display_name: nodeForm.display_name.trim() || nodeForm.name.trim(),
      database: nodeForm.database || null,
      schema_name: nodeForm.schema_name || null
    })
    ElMessage.success('血缘节点创建成功')
    showCreateNodeDialog.value = false
    Object.assign(nodeForm, {
      name: '', display_name: '', node_type: 'table', system: 'data_platform',
      database: '', schema_name: '', description: ''
    })
    await Promise.all([loadNodes(), loadStats()])
    if (result?.data) await selectNode({ ...result.data, type: result.data.node_type })
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error.message || '创建节点失败')
  } finally {
    nodeCreating.value = false
  }
}

async function createLineage() {
  if (!lineageForm.source_id || !lineageForm.target_id) {
    ElMessage.warning('请选择源节点和目标节点')
    return
  }
  if (lineageForm.source_id === lineageForm.target_id) {
    ElMessage.warning('源节点和目标节点不能相同')
    return
  }

  creating.value = true
  try {
    await api.lineage.createEdge({
      source_id: lineageForm.source_id,
      target_id: lineageForm.target_id,
      edge_type: lineageForm.edge_type,
      transformation: lineageForm.transformation,
      transformation_type: 'manual'
    })
    ElMessage.success('血缘关系创建成功')
    showCreateDialog.value = false
    await Promise.all([loadLineageGraph(), loadStats()])
  } catch (error: any) {
    ElMessage.error(error.message || '创建失败')
  } finally {
    creating.value = false
  }
}

async function syncTrainingLineage() {
  syncLoading.value = true
  try {
    const result = await api.lineage.syncAll(1000)
    ElMessage.success(result.message || '平台血缘同步完成')
    await Promise.all([loadNodes(), loadStats()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error.message || '同步平台血缘失败')
  } finally {
    syncLoading.value = false
  }
}

async function importSqlLineage() {
  if (!sqlForm.sql.trim()) {
    ElMessage.warning('请输入需要解析的SQL')
    return
  }
  sqlLoading.value = true
  sqlResult.value = null
  try {
    sqlResult.value = await api.lineage.extractSql({
      sql: sqlForm.sql,
      database: sqlForm.database || null,
      schema_name: sqlForm.schema_name || null,
      persist: true
    })
    ElMessage.success(sqlResult.value.message || 'SQL血缘导入完成')
    await Promise.all([loadNodes(), loadStats()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error.message || 'SQL血缘导入失败')
  } finally {
    sqlLoading.value = false
  }
}

async function openChangelog() {
  showChangelogDialog.value = true
  changelogLoading.value = true
  try {
    const result = await api.lineage.getChangelog({ limit: 100 })
    changelog.value = result.items || []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error.message || '读取变更记录失败')
  } finally {
    changelogLoading.value = false
  }
}

function refreshGraph() {
  loadLineageGraph()
}

function exportGraph() {
  if (!chartInstance) return

  const url = chartInstance.getDataURL({
    type: 'png',
    pixelRatio: 2,
    backgroundColor: '#fff'
  })

  const link = document.createElement('a')
  link.download = `lineage-${selectedNode.value?.name || 'graph'}.png`
  link.href = url
  link.click()
}

function handleResize() {
  chartInstance?.resize()
}

// 初始化
onMounted(async () => {
  await Promise.all([
    loadStats(),
    loadNodes()
  ])

  await nextTick()
  if (chartRef.value) {
    chartInstance = echarts.init(chartRef.value)
    renderEmptyGraph()
  }

  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chartInstance?.dispose()
})
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.data-lineage-page {
  .page-header {
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 20px;

    .page-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;

      :deep(.el-button + .el-button) { margin-left: 0; }
    }

    .page-title {
      font-size: 20px;
      font-weight: 600;
      color: $text-primary;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .page-desc {
      color: $text-secondary;
      font-size: 14px;
      margin-top: 4px;
    }
  }

  .stats-row {
    margin-bottom: 20px;

    .stat-card {
      .stat-content {
        display: flex;
        align-items: center;
        gap: 12px;

        .stat-icon {
          font-size: 32px;
        }

        .stat-info {
          .stat-value {
            font-size: 24px;
            font-weight: 600;
            color: $text-primary;
          }

          .stat-label {
            font-size: 12px;
            color: $text-secondary;
          }
        }
      }
    }
  }

  .main-content {
    .sidebar {
      .filter-card {
        margin-bottom: 16px;

        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
      }

      .nodes-card {
        margin-bottom: 16px;
        background: #ffffff;

        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }

        :deep(.el-card__header),
        :deep(.el-card__body) {
          background: #ffffff;
        }
      }

      .node-list {
        .node-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 12px;
          border-radius: 6px;
          cursor: pointer;
          transition: all 0.2s;

          &:hover {
            background: #f1f5f9;
          }

          &.active {
            background: rgba(64, 158, 255, 0.1);
            border: 1px solid $primary-color;
          }

          .node-icon {
            .type-icon {
              font-size: 20px;

              &.table { color: #409EFF; }
              &.source { color: #67C23A; }
              &.column { color: #14B8A6; }
              &.report { color: #E6A23C; }
              &.pipeline { color: #909399; }
              &.model { color: #F56C6C; }
              &.api { color: #F56C6C; }
            }
          }

          .node-info {
            flex: 1;
            min-width: 0;

            .node-name {
              font-size: 14px;
              font-weight: 600;
              color: #1f2937;
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
            }

            .node-meta {
              font-size: 12px;
              color: #475569;
              display: flex;
              gap: 8px;
            }
          }
        }
      }
    }

    .content-area {
      .graph-card {
        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;

          .header-left {
            display: flex;
            align-items: center;
            gap: 12px;
          }

          .header-right {
            display: flex;
            align-items: center;
            gap: 8px;
          }
        }

        .lineage-chart {
          height: 500px;
          width: 100%;
        }
      }

      .impact-card {
        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
      }
    }
  }

  .detail-content {
    .detail-section {
      margin-top: 24px;

      h4 {
        margin-bottom: 16px;
        padding-bottom: 8px;
        border-bottom: 1px solid $border-color;
      }

      .relation-list {
        .relation-item {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          margin-bottom: 8px;
          background: $bg-card;
          border-radius: 6px;
          cursor: pointer;
          transition: all 0.2s;

          &:hover {
            background: $bg-hover;
          }
        }
      }
    }
  }

  .sql-form { margin-top: 18px; }

  @media (max-width: 1000px) {
    .page-header {
      flex-direction: column;

      .page-actions { justify-content: flex-start; }
    }
  }
}
</style>
