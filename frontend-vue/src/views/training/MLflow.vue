<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="page-title"><el-icon><Monitor /></el-icon> 实验追踪</h1>
      <p class="page-desc">MLflow实验追踪与管理 - 记录参数、指标和模型</p>
    </div>

    <!-- 服务状态 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="24" :sm="6">
        <el-card shadow="hover" class="stat-card" :class="{ 'connected': mlflowStatus?.available }">
          <div class="stat-value">
            <el-icon v-if="mlflowStatus?.available" color="#67C23A"><CircleCheck /></el-icon>
            <el-icon v-else color="#F56C6C"><CircleClose /></el-icon>
            {{ mlflowStatus?.available ? '已连接' : '未连接' }}
          </div>
          <div class="stat-label">MLflow服务</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ (experiments || []).length }}</div>
          <div class="stat-label">实验数量</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ totalRuns }}</div>
          <div class="stat-label">运行次数</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ registeredModels }}</div>
          <div class="stat-label">注册模型</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 操作栏 -->
    <el-row :gutter="20" class="action-row">
      <el-col :span="24">
        <el-button type="primary" @click="showCreateExperiment = true">
          <el-icon><Plus /></el-icon> 创建实验
        </el-button>
        <el-button @click="loadAll">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
        <el-button @click="openMLflowUI">
          <el-icon><Link /></el-icon> 打开MLflow
        </el-button>
      </el-col>
    </el-row>

    <!-- 模型注册与追踪：复用模型管理的业务文件夹，只展示其已登记到 MLflow 的版本记录。 -->
    <el-row :gutter="16" class="tracking-layout">
      <el-col :xs="24" :md="5">
        <el-card shadow="never" class="tracking-folder-card" v-loading="modelGroupsLoading">
          <template #header><span>模型分类文件夹</span></template>
          <div class="tracking-folder-list">
            <button
              v-for="folder in trackingFolders"
              :key="String(folder.folder_id)"
              type="button"
              class="tracking-folder-item"
              :class="{ active: selectedTrackingFolder === folder.folder_id }"
              @click="selectTrackingFolder(folder)"
            >
              <span class="folder-name"><el-icon><FolderOpened /></el-icon>{{ folder.folder_name }}</span>
              <el-tag size="small" type="info" effect="plain">{{ folder.registry_count }}</el-tag>
            </button>
          </div>
          <el-empty v-if="!trackingFolders.length && !modelsLoading" description="暂无追踪模型" :image-size="52" />
          <p class="folder-hint">文件夹与“模型管理”共用；移动模型后此处会同步归类。</p>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="19">
        <el-card shadow="never" class="table-card">
      <template #header>
        <div class="card-header">
          <div class="registry-title">
            <span>{{ activeTrackingFolder?.folder_name || '模型注册表与训练追踪' }}</span>
            <el-tag type="info" size="small">{{ filteredRegistryModels.length }} 个模型</el-tag>
          </div>
          <div class="registry-actions">
            <el-input v-model="modelSearchText" clearable placeholder="搜索模型" style="width:220px">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-button type="primary" :disabled="selectedCompareModels.length < 2" @click="openCompareDialog">
              多模型对比（{{ selectedCompareModels.length }}）
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="filteredRegistryModels"
        style="width: 100%"
        v-loading="modelsLoading"
        row-key="name"
        @selection-change="handleModelSelection"
      >
        <el-table-column type="selection" width="46" :selectable="row => Boolean(row.model_id)" />
        <el-table-column label="模型" min-width="230">
          <template #default="{ row }">
            <div class="model-name-cell">
              <div class="model-name"><el-icon><Box /></el-icon>{{ row.display_name || row.name }}</div>
              <el-text type="info" size="small">{{ row.name }}</el-text>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="model_id" label="模型 ID" width="90">
          <template #default="{ row }">{{ row.model_id || '-' }}</template>
        </el-table-column>
        <el-table-column label="框架 / 类型" width="150">
          <template #default="{ row }">
            <div>{{ row.framework || '-' }}</div>
            <el-text type="info" size="small">{{ row.model_type || '-' }}</el-text>
          </template>
        </el-table-column>
        <el-table-column prop="version_count" label="版本数" width="90" />
        <el-table-column label="最新版本" width="130">
          <template #default="{ row }">
            <el-tag :type="stageTagType(latestVersion(row)?.stage)" size="small">
              v{{ latestVersion(row)?.version || '-' }} · {{ stageText(latestVersion(row)?.stage) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最新创建时间" width="180">
          <template #default="{ row }">
            {{ formatDate(latestVersion(row)?.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="460" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="viewCodeSnapshot(row)">代码快照</el-button>
            <el-button size="small" @click="viewParameters(row)">参数配置</el-button>
            <el-button size="small" @click="viewTrainingLogs(row)">训练日志</el-button>
            <el-button size="small" type="success" plain :disabled="!row.model_id" :loading="reproducingName === row.name" @click="reproduceModel(row)">复现</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!modelsLoading && !filteredRegistryModels.length" description="该文件夹暂无 MLflow 追踪模型" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="showParametersDialog" :title="`参数配置 - ${parameterModel?.display_name || ''}`" width="860px">
      <div v-loading="trackingDetailLoading">
        <div class="dialog-toolbar">
          <span>模型版本</span>
          <el-select v-model="selectedParameterVersion" style="width:160px">
            <el-option v-for="version in parameterVersions" :key="version.version" :label="`v${version.version}`" :value="version.version" />
          </el-select>
        </div>
        <template v-if="currentTrackingVersion">
          <el-descriptions :column="2" border size="small" class="run-detail">
            <el-descriptions-item label="Run ID">{{ currentTrackingVersion.run_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="训练任务">{{ currentTrackingVersion.training_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="训练任务编号">{{ currentTrackingVersion.training_job_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="数据集">
              {{ currentTrackingVersion.dataset_id ? `#${currentTrackingVersion.dataset_id} ${currentTrackingVersion.dataset_name || '未知数据集'}` : (currentTrackingVersion.dataset_name || '-') }}
            </el-descriptions-item>
          </el-descriptions>
          <h4 class="section-title">训练参数</h4>
          <div v-if="trackingEntries(currentTrackingVersion.params).length" class="config-grid">
            <div v-for="[key, value] in trackingEntries(currentTrackingVersion.params)" :key="key" class="config-item">
              <span>{{ key }}</span><code>{{ formatTrackingValue(value) }}</code>
            </div>
          </div>
          <el-empty v-else description="该版本未记录参数" :image-size="50" />
          <h4 class="section-title">训练指标</h4>
          <div class="tracking-tags">
            <el-tag v-for="[key, value] in trackingEntries(currentTrackingVersion.metrics)" :key="key" type="success" effect="plain">
              {{ cleanMetricName(key) }}: {{ formatTrackingValue(value) }}
            </el-tag>
          </div>
        </template>
      </div>
    </el-dialog>

    <el-dialog v-model="showCodeDialog" :title="`代码快照 - ${codeSnapshotModel?.display_name || ''}`" width="900px">
      <div v-loading="codeSnapshotLoading">
        <template v-if="codeSnapshot?.available">
          <el-alert
            v-if="codeSnapshot.notice"
            :title="codeSnapshot.notice"
            :type="codeSnapshot.reconstructed ? 'warning' : 'info'"
            :closable="false"
            show-icon
            class="snapshot-notice"
          />
          <el-descriptions :column="4" border size="small" class="run-detail">
            <el-descriptions-item label="算法">{{ codeSnapshot.algorithm?.name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="算法版本">{{ codeSnapshot.algorithm?.version || '-' }}</el-descriptions-item>
            <el-descriptions-item label="文件">{{ codeSnapshot.filename || '-' }}</el-descriptions-item>
            <el-descriptions-item label="快照类型">{{ snapshotTypeText(codeSnapshot.snapshot_type) }}</el-descriptions-item>
          </el-descriptions>
          <pre class="source-code"><code>{{ codeSnapshot.source_code }}</code></pre>
        </template>
        <el-result v-else icon="warning" title="没有可用的代码快照" :sub-title="codeSnapshot?.reason || '正在读取快照'" />
      </div>
    </el-dialog>

    <el-dialog v-model="showTrainingLogsDialog" :title="`训练日志 - ${trainingLogModel?.display_name || ''}`" width="980px" destroy-on-close>
      <div v-loading="trainingLogLoading">
        <div class="dialog-toolbar">
          <div class="log-version-control">
            <span>模型版本</span>
            <el-select v-model="selectedTrainingLogVersion" style="width:160px" @change="loadSelectedTrainingLogs">
              <el-option v-for="version in trainingLogVersions" :key="version.version" :label="`v${version.version}`" :value="version.version" />
            </el-select>
          </div>
          <el-button :disabled="!selectedTrainingLogVersion" @click="loadSelectedTrainingLogs">刷新</el-button>
        </div>
        <template v-if="trainingLog?.available">
          <el-alert :title="trainingLog.notice" type="info" :closable="false" show-icon class="snapshot-notice" />
          <el-descriptions :column="4" border size="small" class="run-detail">
            <el-descriptions-item label="训练任务编号">{{ trainingLog.training_job_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="Run ID">{{ trainingLog.run_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="runStatusType(trainingLog.status)">{{ runStatusText(trainingLog.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="训练轮次">{{ trainingLog.current_epoch || 0 }} / {{ trainingLog.total_epochs || 0 }}</el-descriptions-item>
            <el-descriptions-item label="日志来源" :span="4">{{ trainingLog.source_text || '-' }}</el-descriptions-item>
          </el-descriptions>
          <pre class="training-log-content"><code>{{ trainingLog.content }}</code></pre>
        </template>
        <el-result v-else-if="!trainingLogLoading" icon="warning" title="没有可用的训练日志" sub-title="该版本未关联平台训练任务或历史记录已经不存在" />
      </div>
      <template #footer>
        <el-button :disabled="!trainingLog?.content" @click="downloadTrainingLogs">下载日志</el-button>
        <el-button @click="showTrainingLogsDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showCompareDialog" title="多模型同数据集对比" width="980px">
      <el-alert title="所有模型使用同一个独立测试数据集，并对整份数据执行真实推理评估；不会随机拆分或重新训练。" type="info" :closable="false" show-icon />
      <div class="selected-models">
        <el-tag v-for="model in selectedCompareModels" :key="model.name" size="large">
          {{ model.display_name || model.name }}
        </el-tag>
      </div>
      <el-form :model="compareForm" label-width="100px" class="compare-form">
        <el-form-item label="测试数据集" required>
          <el-select v-model="compareForm.dataset_id" filterable placeholder="请选择所有模型共用的数据集" style="width:100%">
            <el-option v-for="dataset in datasets" :key="dataset.id" :label="`#${dataset.id} ${dataset.name}`" :value="dataset.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务类型">
          <el-radio-group v-model="compareForm.task_type">
            <el-radio-button value="regression">回归</el-radio-button>
            <el-radio-button value="classification">分类</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="目标列">
          <el-select v-model="compareForm.target_column" filterable placeholder="请选择要比较的目标列" style="width:100%">
            <el-option v-for="column in compareDatasetColumns" :key="column" :label="column" :value="column" />
          </el-select>
        </el-form-item>
      </el-form>
      <el-table v-if="compareResults.length" :data="compareResults" border class="compare-results">
        <el-table-column label="模型" min-width="180">
          <template #default="{ row }">#{{ row.model_id }} {{ row.model_name || '未知模型' }}</template>
        </el-table-column>
        <el-table-column label="结果" width="100">
          <template #default="{ row }"><el-tag :type="row.status === 'completed' ? 'success' : 'danger'">{{ row.status === 'completed' ? '完成' : '失败' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="指标" min-width="380">
          <template #default="{ row }">
            <el-tag v-for="[key, value] in trackingEntries(row.metrics)" :key="key" class="metric-tag">
              {{ key }}: {{ formatTrackingValue(value) }}
            </el-tag>
            <el-text v-if="row.error" type="danger">{{ row.error }}</el-text>
          </template>
        </el-table-column>
        <el-table-column prop="test_samples" label="测试样本" width="100" />
      </el-table>
      <template #footer>
        <el-button @click="showCompareDialog = false">关闭</el-button>
        <el-button type="primary" :loading="comparing" @click="runModelComparison">开始对比测试</el-button>
      </template>
    </el-dialog>

    <!-- 实验列表 -->
    <el-card shadow="never" class="table-card">
      <template #header>
        <div class="card-header">
          <span>实验列表</span>
          <el-input v-model="searchText" placeholder="搜索实验" style="width: 200px" clearable>
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
      </template>

      <el-table :data="filteredExperiments" style="width: 100%" v-loading="loading">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="experiment-detail" v-if="row.runs?.length">
              <h4>运行记录</h4>
              <el-table :data="row.runs" size="small">
                <el-table-column prop="id" label="Run ID" width="200" />
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{ row: r }">
                    <el-tag :type="r.status === 'FINISHED' ? 'success' : r.status === 'FAILED' ? 'danger' : 'warning'" size="small">
                      {{ r.status }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="主要指标">
                  <template #default="{ row: r }">
                    <el-tag v-for="(v, k) in r.metrics" :key="k" size="small" class="metric-tag">
                      {{ k }}: {{ typeof v === 'number' ? v.toFixed(4) : v }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="start_time" label="开始时间" width="160">
                  <template #default="{ row: r }">{{ formatDate(r.start_time) }}</template>
                </el-table-column>
              </el-table>
            </div>
            <el-empty v-else description="暂无运行记录" />
          </template>
        </el-table-column>
        <el-table-column prop="name" label="实验名称" min-width="200">
          <template #default="{ row }">
            <div class="experiment-name">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ row.name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="实验ID" width="120" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="运行数" width="80">
          <template #default="{ row }">{{ row.run_count || 0 }}</template>
        </el-table-column>
        <el-table-column prop="artifact_location" label="存储位置" min-width="200">
          <template #default="{ row }">
            <el-text truncated>{{ row.artifact_location }}</el-text>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="275" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="viewExperiment(row)">
              <el-icon><View /></el-icon> 查看
            </el-button>
            <el-button size="small" type="primary" @click="createRun(row)">
              <el-icon><VideoPlay /></el-icon> 运行
            </el-button>
            <el-button v-if="String(row.id) !== '0'" size="small" type="danger" plain @click="deleteExperiment(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建实验对话框 -->
    <el-dialog v-model="showCreateExperiment" title="创建实验" width="500px">
      <el-form :model="experimentForm" :rules="formRules" ref="formRef" label-width="100px">
        <el-form-item label="实验名称" prop="name">
          <el-input v-model="experimentForm.name" placeholder="如: lstm-training" />
        </el-form-item>
        <el-form-item label="存储路径">
          <el-input v-model="experimentForm.artifact_location" placeholder="留空使用默认路径" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="experimentForm.tags" multiple style="width: 100%" placeholder="选择标签">
            <el-option label="分类任务" value="classification" />
            <el-option label="回归任务" value="regression" />
            <el-option label="时序预测" value="time_series" />
            <el-option label="深度学习" value="deep_learning" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateExperiment = false">取消</el-button>
        <el-button type="primary" @click="createExperiment">创建</el-button>
      </template>
    </el-dialog>

    <!-- 实验详情对话框 -->
    <el-dialog v-model="showDetailDialog" :title="`实验详情 - ${currentExperiment?.name}`" width="900px">
      <el-tabs v-model="detailTab">
        <el-tab-pane label="概览" name="overview">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="实验ID">{{ currentExperiment?.experiment_id }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="currentExperiment?.status === 'active' ? 'success' : 'info'">
                {{ currentExperiment?.status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDate(currentExperiment?.creation_time) }}</el-descriptions-item>
            <el-descriptions-item label="最后更新">{{ formatDate(currentExperiment?.last_update_time) }}</el-descriptions-item>
            <el-descriptions-item label="存储位置" :span="2">{{ currentExperiment?.artifact_location }}</el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>

        <el-tab-pane label="运行记录" name="runs">
          <el-table :data="currentExperiment?.runs || []" size="small" max-height="320">
            <el-table-column label="Run ID" min-width="260"><template #default="{row}">{{ row.run_id || row.id || '-' }}</template></el-table-column>
            <el-table-column prop="status" label="状态" width="110" />
            <el-table-column label="操作" width="80">
              <template #default="{row}"><el-button link type="danger" @click="deleteMlflowRun(row)">删除</el-button></template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!(currentExperiment?.runs || []).length" description="暂无运行记录" :image-size="54" />
        </el-tab-pane>

        <el-tab-pane label="运行对比" name="compare">
          <div ref="compareChartRef" style="height: 300px;"></div>
        </el-tab-pane>

        <el-tab-pane label="参数分布" name="params">
          <div ref="paramsChartRef" style="height: 300px;"></div>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Monitor, Plus, Refresh, Link, Search, View, VideoPlay, FolderOpened, CircleCheck, CircleClose, Box } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import http from '@/api/request'
import { modelGroupApi } from '@/api/model'
import { useAsyncData } from '@/composables/useAsyncData'

// 数据
const searchText = ref('')

// 统计
const totalRuns = computed(() => (experiments.value || []).reduce((sum: number, e: any) => sum + (e.run_count || e.runs?.length || 0), 0))

const { data: mlflowStatus, loading: statusLoading, refresh: loadStatus } = useAsyncData(async () => {
  return await http.get('/mlflow/status')
})

const { data: experiments, loading: experimentsLoading, refresh: loadExperiments } = useAsyncData(async () => {
  const res = await http.get('/mlflow/experiments')
  return res.items || res.experiments || []
})

const { data: registryModels, loading: modelsLoading, refresh: loadRegistry } = useAsyncData(async () => {
  const res = await http.get('/mlflow/registry')
  return res.models || []
})

// 和模型管理完全共用同一份文件夹数据；MLflow 本身只保存 Run/版本，
// 不再维护另一套容易失同步的分类字段。
const { data: modelGroupsData, loading: modelGroupsLoading, refresh: loadModelGroups } = useAsyncData(async () => {
  return await modelGroupApi.getGrouped()
})
const selectedTrackingFolder = ref<number | null | undefined>(undefined)
const trackingFolders = computed(() => {
  const groups = (modelGroupsData.value || []) as any[]
  const folderByModelId = new Map<number, number | null>()
  for (const group of groups) {
    for (const model of group.models || []) folderByModelId.set(Number(model.id), group.folder_id ?? null)
  }
  const registered = registryModels.value || []
  const folders = groups
    .filter(group => group.folder_id !== null)
    .map(group => ({
      folder_id: group.folder_id,
      folder_name: group.folder_name,
      registry_count: registered.filter((model: any) => Number(model.model_id) && folderByModelId.get(Number(model.model_id)) === group.folder_id).length,
    }))
  const ungroupedCount = registered.filter((model: any) => !model.model_id || folderByModelId.get(Number(model.model_id)) == null).length
  if (ungroupedCount) folders.push({ folder_id: null, folder_name: '未分类', registry_count: ungroupedCount })
  return folders
})
const activeTrackingFolder = computed(() =>
  trackingFolders.value.find(folder => folder.folder_id === selectedTrackingFolder.value) || null
)
watch(trackingFolders, (folders) => {
  if (!folders.length) { selectedTrackingFolder.value = undefined; return }
  if (!folders.some(folder => folder.folder_id === selectedTrackingFolder.value)) {
    selectedTrackingFolder.value = folders[0].folder_id
  }
}, { immediate: true })

const { data: datasetsData } = useAsyncData(async () => {
  const res = await http.get('/datasets')
  return res.items || res.datasets || []
})
const datasets = computed(() => datasetsData.value || [])
const compareDatasetColumns = ref<string[]>([])

const modelSearchText = ref('')
const selectedCompareModels = ref<any[]>([])
const showParametersDialog = ref(false)
const trackingDetailLoading = ref(false)
const parameterModel = ref<any>(null)
const parameterVersions = ref<any[]>([])
const selectedParameterVersion = ref<number | string>('')
const showCodeDialog = ref(false)
const codeSnapshotLoading = ref(false)
const codeSnapshotModel = ref<any>(null)
const codeSnapshot = ref<any>(null)
const showTrainingLogsDialog = ref(false)
const trainingLogLoading = ref(false)
const trainingLogModel = ref<any>(null)
const trainingLogVersions = ref<any[]>([])
const selectedTrainingLogVersion = ref<number | string>('')
const trainingLog = ref<any>(null)
const showCompareDialog = ref(false)
const comparing = ref(false)
const compareResults = ref<any[]>([])
const reproducingName = ref('')
const compareForm = ref({
  dataset_id: null as number | null,
  task_type: 'regression',
  target_column: '',
})

watch(() => compareForm.value.dataset_id, async (datasetId) => {
  compareDatasetColumns.value = []
  compareForm.value.target_column = ''
  if (!datasetId) return
  try {
    const preview = await http.get(`/datasets/${datasetId}/rows`, { limit: 1 })
    compareDatasetColumns.value = preview.columns || []
    compareForm.value.target_column = compareDatasetColumns.value[compareDatasetColumns.value.length - 1] || ''
  } catch {
    ElMessage.error('读取数据集列名失败')
  }
})

const filteredRegistryModels = computed(() => {
  const groups = (modelGroupsData.value || []) as any[]
  const folderByModelId = new Map<number, number | null>()
  for (const group of groups) for (const model of group.models || []) folderByModelId.set(Number(model.id), group.folder_id ?? null)
  let models = registryModels.value || []
  if (selectedTrackingFolder.value !== undefined) {
    models = models.filter((model: any) => {
      const folderId = model.model_id ? (folderByModelId.get(Number(model.model_id)) ?? null) : null
      return folderId === selectedTrackingFolder.value
    })
  }
  const search = modelSearchText.value.trim().toLowerCase()
  if (!search) return models
  return models.filter((model: any) =>
    `${model.display_name || ''} ${model.name || ''} ${model.model_id || ''}`.toLowerCase().includes(search)
  )
})

function selectTrackingFolder(folder: { folder_id: number | null }) {
  selectedTrackingFolder.value = folder.folder_id
  // 避免切换文件夹后把不可见模型误带入对比。
  selectedCompareModels.value = []
}

const currentTrackingVersion = computed(() =>
  parameterVersions.value.find(item => String(item.version) === String(selectedParameterVersion.value)) || null
)

function latestVersion(model: any) {
  return (model?.versions || []).reduce((latest: any, current: any) =>
    !latest || Number(current.version) > Number(latest.version) ? current : latest
  , null)
}

function handleModelSelection(rows: any[]) {
  selectedCompareModels.value = rows
}

async function loadModelTracking(model: any) {
  const res = await http.get(`/mlflow/registry/${encodeURIComponent(model.name)}/versions`)
  return res.versions || []
}

async function viewParameters(model: any) {
  parameterModel.value = model
  parameterVersions.value = []
  selectedParameterVersion.value = ''
  showParametersDialog.value = true
  trackingDetailLoading.value = true
  try {
    parameterVersions.value = await loadModelTracking(model)
    selectedParameterVersion.value = parameterVersions.value[0]?.version || ''
  } catch (error: any) {
    ElMessage.error('加载参数配置失败: ' + (error?.message || '未知错误'))
  } finally {
    trackingDetailLoading.value = false
  }
}

async function viewCodeSnapshot(model: any) {
  const version = latestVersion(model)?.version
  if (!version) return
  codeSnapshotModel.value = model
  codeSnapshot.value = null
  showCodeDialog.value = true
  codeSnapshotLoading.value = true
  try {
    codeSnapshot.value = await http.get(
      `/mlflow/registry/${encodeURIComponent(model.name)}/versions/${version}/snapshot`
    )
  } catch (error: any) {
    codeSnapshot.value = { available: false, reason: error?.message || '读取代码快照失败' }
  } finally {
    codeSnapshotLoading.value = false
  }
}

async function viewTrainingLogs(model: any) {
  trainingLogModel.value = model
  trainingLogVersions.value = []
  selectedTrainingLogVersion.value = ''
  trainingLog.value = null
  showTrainingLogsDialog.value = true
  trainingLogLoading.value = true
  try {
    trainingLogVersions.value = await loadModelTracking(model)
    selectedTrainingLogVersion.value = trainingLogVersions.value[0]?.version || ''
    if (selectedTrainingLogVersion.value) await loadSelectedTrainingLogs()
  } catch (error: any) {
    ElMessage.error('加载训练日志失败: ' + (error?.response?.data?.detail || error?.message || '未知错误'))
  } finally {
    trainingLogLoading.value = false
  }
}

async function loadSelectedTrainingLogs() {
  if (!trainingLogModel.value?.name || !selectedTrainingLogVersion.value) return
  trainingLogLoading.value = true
  trainingLog.value = null
  try {
    trainingLog.value = await http.get(
      `/mlflow/registry/${encodeURIComponent(trainingLogModel.value.name)}/versions/${selectedTrainingLogVersion.value}/logs`
    )
  } catch (error: any) {
    ElMessage.error('读取训练日志失败: ' + (error?.response?.data?.detail || error?.message || '未知错误'))
  } finally {
    trainingLogLoading.value = false
  }
}

function downloadTrainingLogs() {
  if (!trainingLog.value?.content) return
  const modelName = String(trainingLogModel.value?.display_name || trainingLogModel.value?.name || 'model')
    .replace(/[\\/:*?"<>|\s]+/g, '_')
  const version = selectedTrainingLogVersion.value || 'unknown'
  const blob = new Blob([trainingLog.value.content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${modelName}_v${version}_training.log`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function reproduceModel(model: any) {
  const version = latestVersion(model)?.version
  if (!version) return
  try {
    await ElMessageBox.confirm(
      `将使用 ${model.display_name || model.name} v${version} 原有的数据集、算法和超参数创建一条新训练任务，是否继续？`,
      '确认复现',
      { type: 'warning', confirmButtonText: '开始复现', cancelButtonText: '取消' }
    )
    reproducingName.value = model.name
    const res = await http.post(
      `/mlflow/registry/${encodeURIComponent(model.name)}/versions/${version}/reproduce`,
      {}
    )
    ElMessage.success(`${res.message}，任务编号：${res.training?.job_id || '-'}`)
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error('复现失败: ' + (error?.response?.data?.detail || error?.message || '未知错误'))
    }
  } finally {
    reproducingName.value = ''
  }
}

function openCompareDialog() {
  compareResults.value = []
  const taskTypes = new Set(selectedCompareModels.value.map(item => item.model_type).filter(Boolean))
  if (taskTypes.size === 1) compareForm.value.task_type = String([...taskTypes][0])
  showCompareDialog.value = true
}

async function runModelComparison() {
  if (!compareForm.value.dataset_id) {
    ElMessage.warning('请选择用于统一测试的数据集')
    return
  }
  if (!compareForm.value.target_column) {
    ElMessage.warning('请选择目标列')
    return
  }
  comparing.value = true
  compareResults.value = []
  try {
    const res = await http.post('/mlflow/compare', {
      model_ids: selectedCompareModels.value.map(model => model.model_id),
      dataset_id: compareForm.value.dataset_id,
      task_type: compareForm.value.task_type,
      target_column: compareForm.value.target_column || null,
    })
    compareResults.value = res.results || []
    if (res.completed) ElMessage.success(`对比完成：${res.completed} 个模型成功`)
    else ElMessage.error('所选模型均未完成评估，请查看失败原因')
  } catch (error: any) {
    ElMessage.error('模型对比失败: ' + (error?.response?.data?.detail || error?.message || '未知错误'))
  } finally {
    comparing.value = false
  }
}

// "注册模型"统计卡:从 registry 结果算真实数量
const registeredModels = computed(() => (registryModels.value || []).length)

const loading = computed(() => statusLoading.value || experimentsLoading.value || modelsLoading.value)

async function loadAll() {
  await Promise.all([loadStatus(), loadExperiments(), loadRegistry(), loadModelGroups()])
}

// 表单
const showCreateExperiment = ref(false)
const formRef = ref()
const experimentForm = ref({
  name: '',
  artifact_location: '',
  tags: [] as string[]
})
const formRules = {
  name: [{ required: true, message: '请输入实验名称', trigger: 'blur' }]
}

// 详情
const showDetailDialog = ref(false)
const currentExperiment = ref<any>(null)
const detailTab = ref('overview')
const compareChartRef = ref()
const paramsChartRef = ref()

// 计算属性
const filteredExperiments = computed(() => {
  if (!searchText.value) return experiments.value
  const search = searchText.value.toLowerCase()
  return experiments.value.filter(e => e.name?.toLowerCase().includes(search))
})

// 方法
async function createExperiment() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  try {
    await http.post('/mlflow/experiments', experimentForm.value)
    ElMessage.success('实验创建成功')
    showCreateExperiment.value = false
    experimentForm.value = { name: '', artifact_location: '', tags: [] }
    loadExperiments()
  } catch (error) {
    ElMessage.error('创建实验失败')
  }
}

async function viewExperiment(row: any) {
  try {
    // 并行拉取实验详情 + 运行列表;实验详情接口只回 id/name/status,
    // 需要从 list 行和 runs 端点补齐 experiment_id / artifact_location / creation_time 等字段。
    const [detail, runsRes] = await Promise.all([
      http.get(`/mlflow/experiments/${row.id}`).catch(() => null),
      http.get(`/mlflow/experiments/${row.id}/runs`).catch(() => ({ items: [] })),
    ])
    const runs = runsRes?.items || []
    const runStarts = runs.map((r: any) => r.start_time).filter(Boolean) as number[]
    currentExperiment.value = {
      experiment_id: detail?.id || row.id,
      id: detail?.id || row.id,
      name: detail?.name || row.name,
      status: detail?.status || row.status,
      // list_experiments 返回 created_at;详情接口没有,优先用行里的 created_at
      creation_time: row.created_at || (runStarts.length ? Math.min(...runStarts) : null),
      last_update_time: runStarts.length ? Math.max(...runStarts) : null,
      artifact_location: row.artifact_location || '',
      runs,
    }
    showDetailDialog.value = true
    detailTab.value = 'overview'
  } catch (error) {
    ElMessage.error('加载详情失败')
  }
}

async function deleteExperiment(row: any) {
  try {
    await ElMessageBox.confirm(
      '确定删除 MLflow 实验「' + row.name + '」吗？该操作会软删除其追踪运行，但不会删除平台模型、训练任务或数据集。',
      '删除 MLflow 实验', { type: 'warning' },
    )
    await http.delete('/mlflow/experiments/' + row.id)
    if (currentExperiment.value?.id === row.id || currentExperiment.value?.experiment_id === row.id) {
      currentExperiment.value = null
      showDetailDialog.value = false
    }
    ElMessage.success('MLflow 实验已删除')
    await loadAll()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || error?.message || '删除失败')
  }
}

async function deleteMlflowRun(run: any) {
  const runId = String(run?.run_id || run?.id || '')
  if (!runId) {
    ElMessage.error('运行记录缺少 Run ID')
    return
  }
  try {
    await ElMessageBox.confirm(
      '确定删除 MLflow 运行记录 ' + runId + ' 吗？该操作只删除追踪记录，不删除平台模型或训练任务。',
      '删除 MLflow 运行记录', { type: 'warning' },
    )
    await http.delete('/mlflow/runs/' + encodeURIComponent(runId))
    if (currentExperiment.value) {
      currentExperiment.value.runs = (currentExperiment.value.runs || []).filter((item: any) => String(item.run_id || item.id) !== runId)
    }
    ElMessage.success('MLflow 运行记录已删除')
    await loadAll()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || error?.message || '删除失败')
  }
}

async function createRun(row: any) {
  ElMessage.info('请在代码中使用 mlflow.start_run() 开始运行')
}

function openMLflowUI() {
  window.open("http://" + window.location.hostname + ":5000", "_blank")
}

// 图表
watch([showDetailDialog, detailTab], async ([show, tab]) => {
  if (show && currentExperiment.value) {
    await nextTick()
    if (tab === 'compare') renderCompareChart()
    if (tab === 'params') renderParamsChart()
  }
})

function renderCompareChart() {
  if (!compareChartRef.value || !currentExperiment.value?.runs) return

  const runs = currentExperiment.value.runs || []
  const chart = echarts.init(compareChartRef.value)

  const metrics: Record<string, number[]> = {}
  runs.forEach((run: any) => {
    Object.entries(run.metrics || {}).forEach(([k, v]) => {
      if (!metrics[k]) metrics[k] = []
      metrics[k].push(v as number)
    })
  })

  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: Object.keys(metrics) },
    xAxis: { type: 'category', data: runs.map((r: any, i: number) => `Run ${i + 1}`) },
    yAxis: { type: 'value' },
    series: Object.entries(metrics).map(([name, data]) => ({
      name, type: 'line', data, smooth: true
    }))
  })
}

function renderParamsChart() {
  if (!paramsChartRef.value || !currentExperiment.value?.runs) return

  const runs = currentExperiment.value.runs || []
  const chart = echarts.init(paramsChartRef.value)

  const params: Record<string, Set<any>> = {}
  runs.forEach((run: any) => {
    Object.entries(run.params || {}).forEach(([k, v]) => {
      if (!params[k]) params[k] = new Set()
      params[k].add(v)
    })
  })

  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: Object.keys(params) },
    yAxis: { type: 'value', name: '唯一值数量' },
    series: [{
      type: 'bar',
      data: Object.values(params).map(s => s.size),
      itemStyle: { color: '#409EFF' }
    }]
  })
}

// 工具函数
function formatDate(date: string | number) {
  if (!date) return '-'
  if (typeof date === 'number') {
    return new Date(date).toLocaleString('zh-CN')
  }
  return new Date(date).toLocaleString('zh-CN')
}

function trackingEntries(values: Record<string, any> | null | undefined) {
  return Object.entries(values || {})
}

function cleanMetricName(name: string) {
  return name.replace(/^metrics\./, '')
}

function formatTrackingValue(value: any) {
  const numeric = Number(value)
  if (value !== '' && Number.isFinite(numeric)) {
    return Math.abs(numeric) >= 1000 ? numeric.toFixed(2) : Number(numeric.toFixed(6)).toString()
  }
  if (typeof value === 'object' && value !== null) return JSON.stringify(value)
  return String(value ?? '-')
}

function runStatusType(status?: string) {
  const normalized = (status || '').toUpperCase()
  if (normalized === 'FINISHED' || normalized === 'READY' || normalized === 'COMPLETED' || normalized === 'SUCCESS') return 'success'
  if (normalized === 'FAILED' || normalized === 'ERROR' || normalized === 'KILLED') return 'danger'
  return 'warning'
}

function runStatusText(status?: string) {
  const labels: Record<string, string> = {
    FINISHED: '训练完成', READY: '版本就绪', RUNNING: '训练中',
    COMPLETED: '训练完成', SUCCESS: '训练完成', FAILED: '训练失败',
    ERROR: '训练失败', KILLED: '已终止', SCHEDULED: '等待中',
    PENDING: '等待中'
  }
  return labels[(status || '').toUpperCase()] || status || '未知'
}

function stageText(stage?: string) {
  const labels: Record<string, string> = {
    Production: '生产版本', Staging: '测试版本', Archived: '已归档', None: '未分阶段'
  }
  return labels[stage || 'None'] || stage || '未分阶段'
}

function stageTagType(stage?: string) {
  if (stage === 'Production') return 'success'
  if (stage === 'Staging') return 'warning'
  return 'info'
}

function snapshotTypeText(type?: string) {
  const labels: Record<string, string> = {
    immutable_runtime: '训练时不可变快照',
    reconstructed_runtime: '历史适配器重建',
    uploaded_reference: '上传源码参考',
  }
  return labels[type || ''] || '-'
}

</script>

<style lang="scss" scoped>
.page-container {
  padding: 20px;

  .page-header {
    margin-bottom: 24px;

    .page-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 24px;
      margin: 0 0 8px 0;
    }

    .page-desc {
      color: #909399;
      margin: 0;
    }
  }

  .stats-row {
    margin-bottom: 20px;

    .stat-card {
      text-align: center;
      padding: 16px 0;

      .stat-value {
        font-size: 20px;
        font-weight: 600;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
      }

      .stat-label {
        color: #909399;
        font-size: 14px;
        margin-top: 8px;
      }

      &.connected .stat-value { color: #67C23A; }
    }
  }

  .action-row {
    margin-bottom: 16px;
  }

  .tracking-layout { margin-top: 16px; }

  .tracking-folder-card {
    height: 100%;

    .tracking-folder-list { display: flex; flex-direction: column; gap: 6px; }
    .tracking-folder-item {
      display: flex; width: 100%; align-items: center; justify-content: space-between;
      gap: 8px; border: 1px solid #ebeef5; border-radius: 6px; background: #fff;
      padding: 10px; color: #303133; cursor: pointer; text-align: left;
    }
    .tracking-folder-item:hover, .tracking-folder-item.active {
      border-color: #409eff; background: #ecf5ff;
    }
    .folder-name { display: flex; min-width: 0; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .folder-hint { margin: 12px 2px 0; color: #909399; font-size: 12px; line-height: 1.6; }
  }

  .table-card {
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .registry-title {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .registry-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .model-name-cell {
      .model-name {
        display: flex;
        align-items: center;
        gap: 7px;
        font-weight: 600;
        margin-bottom: 3px;
      }
    }

    .model-summary {
      margin-bottom: 16px;
    }

    .deployed-tag {
      display: block;
      width: fit-content;
      margin-top: 5px;
    }

    .stage-text {
      margin-top: 5px;
      color: #909399;
      font-size: 12px;
    }

    .experiment-name {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .metric-tag {
      margin: 2px;
    }
  }

  .experiment-detail {
    padding: 16px;

    h4 {
      margin: 0 0 12px 0;
      font-size: 14px;
      color: #606266;
    }
  }

  .version-detail {
    padding: 12px 20px 20px;

    .tracking-block {
      margin-bottom: 16px;

      h4 {
        margin: 0 0 10px;
        color: #606266;
      }
    }

    .tracking-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .run-detail {
      margin-top: 8px;
    }
  }

  .dialog-toolbar {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    margin-bottom: 14px;
  }

  .log-version-control {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-right: auto;
  }

  .section-title {
    margin: 18px 0 10px;
    color: #606266;
  }

  .config-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;

    .config-item {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 9px 12px;
      border: 1px solid #ebeef5;
      border-radius: 4px;
      background: #fafafa;

      span { color: #606266; }
      code { color: #303133; text-align: right; overflow-wrap: anywhere; }
    }
  }

  .source-code {
    max-height: 560px;
    overflow: auto;
    margin: 14px 0 0;
    padding: 16px;
    border-radius: 6px;
    background: #172033;
    color: #e5e7eb;
    line-height: 1.55;
    white-space: pre-wrap;
  }

  .training-log-content {
    max-height: 520px;
    overflow: auto;
    margin: 14px 0 0;
    padding: 16px;
    border-radius: 6px;
    background: #111827;
    color: #d1fae5;
    font-family: Monaco, Menlo, Consolas, monospace;
    font-size: 13px;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .snapshot-notice { margin-bottom: 14px; }

  .selected-models {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 16px 0;
  }

  .compare-form { max-width: 720px; }
  .compare-results { margin-top: 16px; }
}
</style>
