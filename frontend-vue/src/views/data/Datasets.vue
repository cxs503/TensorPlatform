<template>
  <div class="datasets-page">
    <PageHeader title="数据集管理" desc="数据资产全景 — 从原始采集到AI就绪的全生命周期">
      <template #actions>
        <el-button v-if="userStore.hasPermission('datasets:upload')" type="primary" @click="openCreateDialog">
          <el-icon><Plus /></el-icon>
          创建数据集
        </el-button>
      </template>
    </PageHeader>

    <el-tabs v-model="datasetTab" class="dataset-tabs">
      <el-tab-pane label="数据集管理" name="manage">

    <el-row :gutter="12" class="stats-row">
      <el-col :xs="12" :sm="4"><StatCard title="原始数据" :value="lifecycleStats.raw" icon="Connection" :loading="listLoading" /></el-col>
      <el-col :xs="12" :sm="4"><StatCard title="特征工程" :value="lifecycleStats.engineered" icon="MagicStick" :loading="listLoading" /></el-col>
      <el-col :xs="12" :sm="4"><StatCard title="已标注" :value="lifecycleStats.labeled" icon="Edit" :loading="listLoading" /></el-col>
      <el-col :xs="12" :sm="4"><StatCard title="检查通过" :value="lifecycleStats.passed" icon="CircleCheck" :loading="listLoading" /></el-col>
      <el-col :xs="12" :sm="4"><StatCard title="检查未通过" :value="lifecycleStats.failed" icon="Warning" :loading="listLoading" /></el-col>
      <el-col :xs="12" :sm="4"><StatCard title="治理报告" :value="governanceReports.length" icon="Document" :loading="listLoading" /></el-col>
    </el-row>
    <div class="data-flow-note">推荐流程：初始数据 → 基础清洗 → 分割训练/测试 → 训练与评估</div>

    <div v-if="listLoading" class="loading-block">
      <el-skeleton :rows="4" animated />
    </div>

    <div v-else-if="listError" class="error-block">
      <el-result icon="error" :title="listError">
        <template #extra><el-button type="primary" @click="refreshList">重试</el-button></template>
      </el-result>
    </div>

    <el-card v-else-if="simpleMode" shadow="never" class="simple-dataset-card source-folder-card">
      <template #header>
        <div class="simple-list-header">
          <div>
            <b>数据源文件夹</b>
            <span class="source-folder-hint">同一原始数据及其全部衍生结果归在同一文件夹内。</span>
          </div>
          <el-tag effect="plain">{{ sourceFolders.length }} 个数据源</el-tag>
        </div>
      </template>

      <el-empty v-if="!sourceFolders.length" description="暂无数据集，请先导入或创建数据集" />
      <el-collapse v-else v-model="expandedSourceFolders" class="source-folder-list">
        <el-collapse-item v-for="folder in pagedSourceFolders" :key="folder.root.id" :name="String(folder.root.id)">
          <template #title>
            <div class="source-folder-title">
              <el-icon class="source-folder-icon"><FolderOpened /></el-icon>
              <el-tag size="small" :type="stageTagType(folder.root.stage)">#{{ folder.root.id }}</el-tag>
              <b>{{ folder.root.name }}</b>
              <span>{{ folder.items.length }} 个版本</span>
            </div>
          </template>
          <div class="source-folder-items">
            <article v-for="dataset in folder.items" :key="dataset.id" class="simple-dataset-item source-folder-dataset-row">
              <div class="source-folder-dataset-main">
                <div class="simple-dataset-top">
                  <div class="simple-dataset-name" @click="openDetail(dataset)">
                    <el-tag size="small" :type="stageTagType(dataset.stage)">#{{ dataset.id }}</el-tag>
                    <b>{{ dataset.name }}</b>
                  </div>
                  <el-tag size="small" :type="simpleDatasetStatus(dataset).type" effect="light">{{ stageLabel(dataset.stage) }}</el-tag>
                </div>
                <div class="simple-dataset-meta">{{ (dataset.row_count || 0).toLocaleString() }} 行 · {{ formatSize(dataset.size_bytes) }} · {{ formatDate(dataset.created_at) }}</div>
                <div v-if="dataset.id !== folder.root.id" class="simple-dataset-origin">直属来源 #{{ dataset.governed_from || folder.root.id }}</div>
              </div>
              <div class="simple-dataset-actions source-folder-dataset-actions">
                <el-button v-if="userStore.hasPermission('datasets:process') && canCleanSource(dataset) && dataset.has_file" type="primary" size="small" :loading="processingId === dataset.id" @click="openCleaningDialog(dataset)">清洗分析</el-button>
                <el-button v-else type="primary" plain size="small" @click="openDetail(dataset)">查看数据</el-button>
                <el-button v-if="dataset.has_file" size="small" text type="primary" @click="openFieldRolesDialog(dataset)">时间与标识</el-button>
                <el-button v-if="userStore.hasPermission('datasets:process') && canSplitDataset(dataset)" size="small" text type="success" @click="openSplitDialog(dataset)">分割数据</el-button>
                <el-button v-if="simpleDatasetReport(dataset)" size="small" text type="primary" @click="openReport(simpleDatasetReport(dataset))">查看报告</el-button>
                <el-button size="small" text @click="openDetail(dataset)">详情</el-button>
                <el-button v-if="userStore.hasPermission('datasets:download') && dataset.has_file" size="small" text @click="downloadDataset(dataset)">下载</el-button>
                <el-popconfirm v-if="userStore.hasPermission('datasets:delete')" title="删除该数据集？关联结果和报告会保留，但会解除来源关系。" @confirm="deleteDataset(dataset)">
                  <template #reference><el-button size="small" text type="danger">删除</el-button></template>
                </el-popconfirm>
              </div>
            </article>
          </div>
        </el-collapse-item>
      </el-collapse>
      <div v-if="sourceFolders.length" class="simple-pagination">
        <span>共 {{ sourceFolders.length }} 个数据源文件夹，第 {{ sourceFolderPage }}/{{ sourceFolderPageCount }} 页</span>
        <el-pagination v-model:current-page="sourceFolderPage" v-model:page-size="sourceFolderPageSize" background layout="sizes, prev, pager, next" :page-sizes="[8, 16, 32]" :total="sourceFolders.length" @size-change="sourceFolderPage = 1" />
      </div>
    </el-card>

    <el-card v-else shadow="never" class="lifecycle-card">
        <template #header>
          <div class="lifecycle-header">
            <div>
              <b>完整处理过程</b>

            </div>
            <div class="lifecycle-view-actions">
              <el-tag effect="plain">{{ lifecycleRows.length }} 条链路</el-tag>
              <el-button size="small" plain @click="simpleMode = true">返回简单视图</el-button>
            </div>
          </div>
        </template>
        <el-table :data="pagedLifecycleRows" row-key="key" stripe>
          <el-table-column label="原始与衍生数据" min-width="340">
            <template #default="{ row }">
              <div class="asset-cell">
                <div class="asset-info">
                  <div class="asset-name" @click="openDetail(row.source)">
                    <el-tag size="small" :type="stageTagType(row.source.stage)">#{{ row.source.id }} {{ stageLabel(row.source.stage) }}</el-tag>
                    <b>{{ row.source.name }}</b>
                  </div>
                  <div class="asset-meta">{{ (row.source.row_count || 0).toLocaleString() }} 行 · {{ formatSize(row.source.size_bytes) }} · {{ formatDate(row.source.created_at) }}</div>
                  <div v-if="isDownstreamSource(row.source) && row.source.governed_from" class="asset-origin">来源数据集 #{{ row.source.governed_from }}</div>
                  <div class="dataset-status">
                    <el-tag size="small" :type="datasetStatus(row.source, row).type" effect="light">{{ datasetStatus(row.source, row).title }}</el-tag>
                    <span>{{ datasetStatus(row.source, row).hint }}</span>
                  </div>
                </div>
                <div class="asset-side-actions">
                  <el-button v-if="userStore.hasPermission('datasets:process') && canCleanSource(row.source)" text size="small" type="primary" :loading="processingId === row.source.id" @click="openCleaningDialog(row.source)">清洗分析</el-button>
                  <el-button v-if="userStore.hasPermission('datasets:process') && canSplitDataset(row.source)" text size="small" type="success" @click="openSplitDialog(row.source)">分割数据</el-button>
                  <el-button v-if="userStore.hasPermission('datasets:download')" text size="small" @click="downloadDataset(row.source)">下载</el-button>
                  <el-popconfirm v-if="userStore.hasPermission('datasets:delete')" title="删除该数据集？关联结果和报告会保留，但会解除来源关系。" @confirm="deleteDataset(row.source)">
                    <template #reference><el-button text size="small" type="danger">删除</el-button></template>
                  </el-popconfirm>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="清洗结果" min-width="330">
            <template #default="{ row }">
              <template v-if="row.result">
                <div class="asset-cell">
                  <div class="asset-info">
                    <div class="asset-name" @click="openDetail(row.result)">
                      <el-tag size="small" type="success">#{{ row.result.id }}</el-tag>
                      <b>{{ row.result.name }}</b>
                    </div>
                    <div class="asset-meta">{{ (row.result.row_count || 0).toLocaleString() }} 行 · 质量 {{ row.result.quality_score ?? '—' }} · {{ formatDate(row.result.created_at) }}</div>
                    <div class="dataset-status"><el-tag size="small" type="success" effect="light">已生成新数据</el-tag><span>原数据未修改，可继续训练、评估或再次处理。</span></div>
                  </div>
                  <div class="asset-side-actions">
                    <el-button v-if="userStore.hasPermission('datasets:process') && canSplitDataset(row.result)" text size="small" type="success" @click="openSplitDialog(row.result)">分割数据</el-button>
                    <el-button v-if="userStore.hasPermission('datasets:download')" text size="small" @click="downloadDataset(row.result)">下载</el-button>
                    <el-popconfirm v-if="userStore.hasPermission('datasets:delete')" title="删除该清洗结果？治理报告会保留。" @confirm="deleteDataset(row.result)">
                      <template #reference><el-button text size="small" type="danger">删除</el-button></template>
                    </el-popconfirm>
                  </div>
                </div>
              </template>
              <el-alert v-else-if="row.report?.status === 'failed'" type="error" :closable="false" show-icon title="检查未通过，未生成新数据" description="可以调整清洗规则后重新执行；原始数据仍保持不变。" />
              <span v-else class="empty-cell">尚未处理。可从左侧点击“清洗分析”开始。</span>
            </template>
          </el-table-column>
          <el-table-column label="治理报告" min-width="310">
            <template #default="{ row }">
              <template v-if="row.report">
                <div class="asset-cell">
                  <div class="asset-info">
                    <div class="report-summary">
                      <el-tag :type="row.report.status === 'passed' ? 'success' : 'danger'">{{ row.report.status === 'passed' ? '通过' : '未通过' }}</el-tag>
                      <b>{{ row.report.summary?.profile_name || row.report.profile }}</b>
                      <span>得分 {{ row.report.score }}</span>
                    </div>
                    <div class="asset-meta">{{ row.report.summary?.checks_passed ?? '—' }}/{{ row.report.summary?.checks_total ?? '—' }} 项通过 · {{ formatDate(row.report.checked_at) }}</div>
                  </div>
                  <div class="asset-side-actions">
                    <el-button text size="small" type="primary" @click="openReport(row.report)">查看</el-button>
                    <el-popconfirm v-if="userStore.hasPermission('datasets:process')" title="只删除治理报告？原始数据和清洗结果不会删除。" @confirm="deleteGovernanceReport(row.report)">
                      <template #reference><el-button text size="small" type="danger">删除</el-button></template>
                    </el-popconfirm>
                  </div>
                </div>
              </template>
              <span v-else class="empty-cell">尚无报告</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!lifecycleRows.length" description="暂无数据集" />
        <div v-else class="lifecycle-pagination">
          <span>共 {{ lifecycleRows.length }} 条，第 {{ lifecyclePage }}/{{ lifecyclePageCount }} 页</span>
          <el-pagination
            v-model:current-page="lifecyclePage"
            v-model:page-size="lifecyclePageSize"
            background
            layout="sizes, prev, pager, next, jumper"
            :page-sizes="[10, 20, 50]"
            :total="lifecycleRows.length"
            @size-change="lifecyclePage = 1"
          />
        </div>
    </el-card>

    </el-tab-pane>

      <el-tab-pane label="数据版本 (DVC)" name="version">
        <el-row :gutter="16" class="stats-row">
          <el-col :span="6"><el-card><el-statistic title="版本库" :value="dvcDatasets.length" /></el-card></el-col>
          <el-col :span="6"><el-card><el-statistic title="总版本数" :value="totalDvcVersions" /></el-card></el-col>
          <el-col :span="6"><el-card><el-statistic title="远程存储" value="MinIO" /></el-card></el-col>
          <el-col :span="6"><el-card><el-statistic title="DVC版本" value="3.67.1" /></el-card></el-col>
        </el-row>
        <el-card>
          <div style="margin-bottom:12px;display:flex;gap:8px">
            <el-input v-model="dvcDataset" placeholder="数据集名称" style="width:200px" size="small" />
            <el-button type="primary" size="small" @click="dvcInit">初始化</el-button>
            <el-button size="small" @click="dvcCommit">提交版本</el-button>
            <el-button size="small" @click="dvcPush">推送到MinIO</el-button>
            <el-button size="small" @click="dvcPull">从MinIO拉取</el-button>
          </div>
          <el-table :data="dvcVersions" stripe v-loading="dvcLoading" max-height="400">
            <el-table-column prop="id" label="版本ID" width="120" />
            <el-table-column prop="version" label="版本号" width="120" />
            <el-table-column prop="message" label="提交信息" min-width="200" />
            <el-table-column prop="created_at" label="时间" width="170" />
            <el-table-column label="操作" width="100">
              <template #default="{row}">
                <el-button size="small" @click="dvcCheckout(row.version)">切换</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!dvcLoading && !dvcVersions.length" description="暂无版本记录，请先初始化DVC仓库" :image-size="80" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 创建对话框 -->
    <el-dialog v-model="showCreateDialog" title="创建数据集" width="500px">
      <el-form :model="createForm" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" placeholder="例如: 生产过程温度训练集" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="createForm.type" style="width: 100%">
            <el-option label="时序数据" value="timeseries" />
            <el-option label="表格数据" value="tabular" />
            <el-option label="图像数据" value="image" />
            <el-option label="文本数据" value="text" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="上传文件">
          <el-upload :auto-upload="false" :limit="1" :on-change="handleFileChange">
            <el-button size="small" type="primary">选择文件</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createDataset">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showFieldRolesDialog" title="时间与标识字段" width="680px" destroy-on-close>
      <div v-loading="fieldRolesLoading">
        <el-alert type="info" :closable="false" show-icon title="这里只保存数据本身稳定的时间列和标识列；预测目标、输入特征请在具体训练任务中选择。" />
        <el-radio-group v-model="fieldRolesMode" style="margin:16px 0"><el-radio-button label="auto">使用系统推荐</el-radio-button><el-radio-button label="manual">自主选择</el-radio-button></el-radio-group>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="数据类型">{{ fieldRoleRecommendation?.data_kind === 'timeseries' ? '时序数据' : '表格数据' }}</el-descriptions-item>
          <el-descriptions-item label="推荐时间列">{{ fieldRoleRecommendation?.time_column || '未识别' }}</el-descriptions-item>
        </el-descriptions>
        <el-form v-if="fieldRolesMode === 'manual'" label-width="90px" style="margin-top:16px">
          <el-form-item label="时间列"><el-select v-model="fieldRolesForm.time_column" clearable style="width:100%"><el-option v-for="c in fieldRoleColumns" :key="c" :label="c" :value="c" /></el-select></el-form-item>
          <el-form-item label="标识列"><el-select v-model="fieldRolesForm.id_columns" multiple clearable style="width:100%"><el-option v-for="c in fieldRoleColumns" :key="c" :label="c" :value="c" /></el-select></el-form-item>
        </el-form>
      </div>
      <template #footer><el-button @click="showFieldRolesDialog=false">取消</el-button><el-button type="primary" :loading="fieldRolesSaving" @click="saveFieldRoles">保存并采用</el-button></template>
    </el-dialog>


    <el-dialog v-model="showSplitDialog" title="分割数据集" width="620px" destroy-on-close>
      <el-descriptions v-if="splitDataset" :column="2" border size="small" style="margin-bottom:16px">
        <el-descriptions-item label="来源数据集">#{{ splitDataset.id }} {{ splitDataset.name }}</el-descriptions-item>
        <el-descriptions-item label="总行数">{{ (splitDataset.row_count || 0).toLocaleString() }}</el-descriptions-item>
        <el-descriptions-item label="训练数据">{{ splitPreview.trainRows }} 行 · {{ splitDataset.name }}训练</el-descriptions-item>
        <el-descriptions-item label="测试数据">{{ splitPreview.testRows }} 行 · {{ splitDataset.name }}测试</el-descriptions-item>
      </el-descriptions>
      <el-alert
        title="训练数据包含后续训练流程使用的训练部分和验证部分；测试数据保持独立，模型评估时整份使用。"
        type="info" :closable="false" show-icon style="margin-bottom:16px"
      />
      <el-form label-width="100px">
        <el-form-item label="分割比例">
          <el-input model-value="训练（含验证）90% / 测试10%" disabled />
        </el-form-item>
        <el-form-item label="分割方式">
          <el-radio-group v-model="splitStrategy">
            <el-radio value="random">固定随机分割</el-radio>
            <el-radio value="chronological">按原顺序分割</el-radio>
          </el-radio-group>
          <div class="split-hint">
            {{ splitStrategy === 'chronological' ? '适合时序数据：末尾约10%作为测试数据。' : '适合普通表格：固定随机种子，结果可复现。' }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSplitDialog=false">取消</el-button>
        <el-button v-if="userStore.hasPermission('datasets:download')" :loading="splitWorking === 'download'" :disabled="!!splitWorking" @click="downloadDatasetSplit">下载ZIP</el-button>
        <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" :loading="splitWorking === 'save'" :disabled="!!splitWorking" @click="saveDatasetSplit">保存至数据集管理</el-button>
      </template>
    </el-dialog>

    <!-- 低代码清洗分析向导 -->
    <el-dialog v-model="showCleaningDialog" title="清洗分析" width="760px" destroy-on-close>
      <el-steps :active="cleaningWizardStep" simple class="cleaning-wizard-steps">
        <el-step title="选择方式" />
        <el-step title="查看影响" />
        <el-step title="完成" />
      </el-steps>

      <template v-if="cleaningWizardStep === 0">
        <div class="cleaning-wizard-choices">
          <button
            type="button"
            class="cleaning-wizard-choice"
            :class="{ active: cleaningPlan === 'auto' }"
            @click="selectCleaningPlan('auto')"
          >
            <b>自动清洗</b>
            <span>使用平台默认规则处理常见空值和无效记录。</span>
          </button>
          <button
            type="button"
            class="cleaning-wizard-choice"
            :class="{ active: cleaningPlan === 'custom' }"
            @click="selectCleaningPlan('custom')"
          >
            <b>使用已有方案</b>
            <span>选择已创建的清洗规则。</span>
          </button>
        </div>

        <el-form v-if="cleaningPlan === 'custom'" label-width="92px" class="cleaning-wizard-form">
          <el-form-item label="清洗方案" required>
            <el-select
              v-model="selectedCleaningRule"
              filterable
              :filter-method="filterCleaningRules"
              placeholder="请选择已有方案"
              style="width:100%"
              @change="cleaningPreview = null"
            >
              <el-option
                v-for="rule in displayedCleaningRules"
                :key="rule.id"
                :label="'#' + rule.id + ' ' + rule.name"
                :value="rule.id"
              />
              <template #footer>
                <div class="select-pagination" @click.stop>
                  <span>{{ filteredCleaningRules.length }} 条方案</span>
                  <el-pagination small background layout="prev, pager, next" :page-size="dropdownPageSize" :total="filteredCleaningRules.length" :current-page="cleaningRulePage" @current-change="cleaningRulePage = $event" />
                </div>
              </template>
            </el-select>
          </el-form-item>
        </el-form>

        <el-collapse v-model="cleaningAdvancedPanels" class="cleaning-advanced">
          <el-collapse-item title="高级设置（可选）" name="advanced">
            <el-form label-width="92px">
              <el-form-item label="完整率下限">
                <div class="threshold-slider">
                  <el-slider v-model="governanceThresholds.min_completeness" :min="80" :max="100" :step="1" show-input @change="cleaningPreview = null" />
                  <span>%</span>
                </div>
              </el-form-item>
              <el-form-item label="允许重复率">
                <div class="threshold-slider">
                  <el-slider v-model="governanceThresholds.max_duplicate_rate" :min="0" :max="20" :step="1" show-input @change="cleaningPreview = null" />
                  <span>%</span>
                </div>
              </el-form-item>
              <el-form-item label="附加规则">
                <el-select
                  v-model="selectedQualityRuleIds"
                  multiple filterable collapse-tags clearable
                  :filter-method="filterQualityRules"
                  placeholder="可选"
                  style="width:100%"
                  @change="cleaningPreview = null"
                >
                  <el-option v-for="rule in displayedQualityRules" :key="rule.id" :label="'#' + rule.id + ' ' + rule.name" :value="rule.id" />
                  <template #footer>
                    <div class="select-pagination" @click.stop>
                      <span>{{ filteredQualityRules.length }} 条规则</span>
                      <el-pagination small background layout="prev, pager, next" :page-size="dropdownPageSize" :total="filteredQualityRules.length" :current-page="qualityRulePage" @current-change="qualityRulePage = $event" />
                    </div>
                  </template>
                </el-select>
              </el-form-item>
              <el-form-item label="结果名称">
                <el-input v-model="cleaningOutputName" placeholder="默认使用原数据集名称加“清洗结果”" />
              </el-form-item>
            </el-form>
          </el-collapse-item>
        </el-collapse>
      </template>

      <template v-else-if="cleaningWizardStep === 1">
        <div v-if="cleaningPreview" class="process-preview">
          <el-row :gutter="12">
            <el-col :span="8"><el-statistic title="清洗前" :value="cleaningPreview.summary.rows_before" /></el-col>
            <el-col :span="8"><el-statistic title="清洗后" :value="cleaningPreview.summary.rows_after" /></el-col>
            <el-col :span="8"><el-statistic title="预计删除" :value="cleaningPreview.summary.rows_removed" /></el-col>
          </el-row>
          <el-alert
            style="margin-top:14px"
            :type="cleaningPreview.governance_report?.passed ? 'success' : 'error'"
            :closable="false"
            show-icon
            :title="cleaningPreview.governance_report?.passed ? '预览检查通过' : '预览检查未通过'"
            :description="'得分 ' + (cleaningPreview.governance_report?.score ?? '—') + '；通过 ' + (cleaningPreview.governance_report?.checks_passed ?? 0) + '/' + (cleaningPreview.governance_report?.checks_total ?? 0) + ' 项。'"
          />
          <el-table :data="cleaningPreview.summary.operations" size="small" border max-height="180" style="margin-top:14px">
            <el-table-column prop="name" label="处理项目" min-width="220" />
            <el-table-column prop="rows_removed" label="删除行" width="90" />
            <el-table-column prop="columns_removed" label="删除列" width="90" />
            <el-table-column prop="changed_cells" label="修改项" width="90" />
          </el-table>
        </div>
      </template>

      <template v-else>
        <el-result
          :icon="cleaningExecutionResult?.status === 'success' ? 'success' : 'warning'"
          :title="cleaningExecutionResult?.status === 'success' ? '清洗完成' : '已完成检查，未生成新数据'"
          :sub-title="cleaningExecutionResult?.message || ''"
        >
          <template #extra>
            <el-button v-if="cleaningExecutionResult?.report_id" type="primary" plain @click="openCleaningExecutionReport">查看报告</el-button>
            <el-button type="primary" @click="showCleaningDialog = false">完成</el-button>
          </template>
        </el-result>
      </template>

      <template #footer>
        <template v-if="cleaningWizardStep === 0">
          <el-button @click="showCleaningDialog = false">取消</el-button>
          <el-button type="primary" :loading="processPreviewing" @click="startCleaningPreview">查看影响</el-button>
        </template>
        <template v-else-if="cleaningWizardStep === 1">
          <el-button @click="cleaningWizardStep = 0">上一步</el-button>
          <el-button :loading="processPreviewing" @click="startCleaningPreview">重新预览</el-button>
          <el-button type="primary" :loading="processingId === currentProcessDataset?.id" @click="executeCleaning">执行清洗</el-button>
        </template>
      </template>
    </el-dialog>

    <el-dialog v-model="showLifecycleReport" title="治理报告" width="820px" destroy-on-close>
      <template v-if="currentLifecycleReport">
        <el-descriptions :column="3" border size="small" style="margin-bottom:16px">
          <el-descriptions-item label="报告ID" :span="3">{{ currentLifecycleReport.id }}</el-descriptions-item>
          <el-descriptions-item label="结论">
            <el-tag :type="currentLifecycleReport.status === 'passed' ? 'success' : 'danger'">{{ currentLifecycleReport.status === 'passed' ? '通过' : '未通过' }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="得分">{{ currentLifecycleReport.score }}</el-descriptions-item>
          <el-descriptions-item label="检查时间">{{ formatDateTime(currentLifecycleReport.checked_at) }}</el-descriptions-item>
          <el-descriptions-item label="清洗规则">{{ currentLifecycleReport.summary?.cleaning_rule_name || '历史流程' }}</el-descriptions-item>
          <el-descriptions-item label="治理标准" :span="2">{{ currentLifecycleReport.summary?.profile_name || currentLifecycleReport.profile }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="currentLifecycleReport.summary?.checks || []" size="small" border max-height="420">
          <el-table-column label="结果" width="75">
            <template #default="{ row }"><el-tag size="small" :type="row.passed ? 'success' : 'danger'">{{ row.passed ? '通过' : '未通过' }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="name" label="检查项" min-width="140" />
          <el-table-column prop="expected" label="要求" min-width="190" />
          <el-table-column prop="actual" label="实际" min-width="180" />
          <el-table-column prop="score" label="得分" width="75" />
        </el-table>
      </template>
    </el-dialog>

    <!-- 已清洗数据治理验收 -->
    <el-dialog v-model="showGovernanceDialog" title="治理验收" width="800px" destroy-on-close>
      <el-descriptions v-if="currentProcessDataset" :column="2" border size="small" style="margin-bottom:16px">
        <el-descriptions-item label="数据集">#{{ currentProcessDataset.id }} {{ currentProcessDataset.name }}</el-descriptions-item>
        <el-descriptions-item label="当前阶段">已清洗</el-descriptions-item>
        <el-descriptions-item label="数据行数">{{ (currentProcessDataset.row_count || 0).toLocaleString() }}</el-descriptions-item>
        <el-descriptions-item label="验收原则">只检查，不修改数据内容</el-descriptions-item>
      </el-descriptions>

      <el-form label-width="110px">
        <el-form-item label="验收标准" required>
          <el-select v-model="selectedGovernanceProfile" style="width:100%" @change="governanceReport = null">
            <el-option
              v-for="profile in governanceProfiles"
              :key="profile.id"
              :label="profile.name + ' — ' + profile.description"
              :value="profile.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="附加质量规则">
          <el-select
            v-model="selectedQualityRuleIds"
            multiple filterable collapse-tags clearable
            placeholder="可选；只选择与当前数据字段匹配的规则"
            style="width:100%"
            @change="governanceReport = null"
          >
            <el-option
              v-for="rule in governanceQualityRules"
              :key="rule.id"
              :label="'#' + rule.id + ' ' + rule.name + '（字段：' + rule.field + '）'"
              :value="rule.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <el-alert
        type="info" :closable="false" show-icon
        title="治理验收不会删除、填充或改写任何数据；验收通过后，仅把当前数据集阶段更新为“已治理”。"
      />

      <div v-if="governanceReport" class="process-preview">
        <el-alert
          :type="governanceReport.passed ? 'success' : 'error'"
          :closable="false" show-icon
          :title="governanceReport.passed ? '预览结果：可以通过治理验收' : '预览结果：暂不符合治理标准'"
          :description="'得分 ' + governanceReport.score + '；通过 ' + governanceReport.checks_passed + '/' + governanceReport.checks_total + ' 项'"
        />
        <el-table :data="governanceReport.checks" size="small" border max-height="320" style="margin-top:14px">
          <el-table-column label="结果" width="70">
            <template #default="{ row }">
              <el-tag :type="row.passed ? 'success' : 'danger'" size="small">{{ row.passed ? '通过' : '未通过' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="检查项" min-width="150" />
          <el-table-column prop="expected" label="标准" min-width="180" />
          <el-table-column prop="actual" label="实际结果" min-width="160" />
          <el-table-column prop="score" label="得分" width="80" />
        </el-table>
      </div>

      <template #footer>
        <el-button @click="showGovernanceDialog = false">取消</el-button>
        <el-button :loading="processPreviewing" @click="previewAcceptance">预览验收报告</el-button>
        <el-button
          type="success"
          :loading="processingId === currentProcessDataset?.id"
          :disabled="!governanceReport"
          @click="acceptGovernance"
        >正式验收</el-button>
      </template>
    </el-dialog>

    <!-- 数据集详情抽屉 -->
    <el-drawer v-model="showDetailDrawer" :title="detailData?.name || '数据集详情'" size="60%" destroy-on-close>
      <div v-loading="detailLoading">
        <!-- 基本信息 -->
        <el-descriptions title="基本信息" :column="2" border size="small" style="margin-bottom: 20px">
          <el-descriptions-item label="名称">{{ detailData?.name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="阶段">
            <el-tag size="small">{{ stageLabel(detailData?.stage) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="行数">{{ (detailData?.row_count || 0).toLocaleString() }}</el-descriptions-item>
          <el-descriptions-item label="大小">{{ formatSize(detailData?.size_bytes) }}</el-descriptions-item>
          <el-descriptions-item label="质量评分">
            <el-tag v-if="detailData?.quality_score != null" size="small"
              :type="detailData.quality_score >= 90 ? 'success' : detailData.quality_score >= 70 ? 'warning' : 'danger'">
              {{ detailData.quality_score }}
            </el-tag>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(detailData?.created_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="detailData?.description" label="描述" :span="2">
            {{ detailData.description }}
          </el-descriptions-item>
        </el-descriptions>

        <div style="margin-bottom: 8px;">
          <el-button v-if="userStore.hasPermission('datasets:download')" size="small" type="primary" plain :disabled="!detailData?.has_file"
            @click="downloadDataset(detailData)">
            <el-icon><Download /></el-icon> 下载
          </el-button>
        </div>

        <!-- 列信息 -->
        <h4 class="detail-section-title">列信息 ({{ detailColumns.length }})</h4>
        <el-table v-if="detailColumns.length" :data="detailColumns" size="small" border max-height="240" style="margin-bottom: 20px">
          <el-table-column prop="name" label="列名" min-width="140" />
          <el-table-column prop="dtype" label="类型" width="110" />
          <el-table-column label="缺失率" width="110">
            <template #default="{ row }">
              <span v-if="row.missing_pct != null">{{ (row.missing_pct).toFixed(1) }}%</span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="唯一值数" width="100">
            <template #default="{ row }">{{ row.unique_values ?? '—' }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="暂无列信息（数据集未经治理分析）" :image-size="50" style="padding: 12px 0" />

        <!-- 数据预览 -->
        <h4 class="detail-section-title">数据预览（前 {{ previewRows.length }} 行）</h4>
        <el-table v-if="previewColumns.length" :data="previewRows" size="small" border max-height="320" style="margin-bottom: 20px">
          <el-table-column type="index" label="#" width="50" fixed />
          <el-table-column
            v-for="col in previewColumns" :key="col.prop"
            :prop="col.prop" :label="col.label" min-width="120" show-overflow-tooltip />
        </el-table>
        <el-empty v-else description="暂无数据预览（数据集未上传文件）" :image-size="50" style="padding: 12px 0" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Download, FolderOpened } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import StatCard from '@/components/StatCard.vue'
import { del as apiDel, get, post } from '@/api/legacy'
import { request } from '@/api/request'
import { useAsyncData } from '@/composables/useAsyncData'
import dayjs from 'dayjs'
import { useUserStore } from '@/stores/user'

const stages = [
  { key: 'raw', label: '原始数据', icon: 'Connection', color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', emptyText: '暂无原始数据，请先创建数据集或从数据源导入' },
  { key: 'cleaned', label: '已清洗', icon: 'Brush', color: '#3b82f6', bg: 'rgba(59,130,246,0.12)', emptyText: '暂无已清洗数据，请先对原始数据执行清洗' },
  { key: 'governed', label: '已治理', icon: 'SetUp', color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)', emptyText: '暂无已治理数据，请对已清洗数据执行治理验收' },
  { key: 'engineered', label: '特征工程', icon: 'MagicStick', color: '#06b6d4', bg: 'rgba(6,182,212,0.12)', emptyText: '暂无特征工程数据，请在特征工程模块创建派生特征' },
  { key: 'labeled', label: '已标注', icon: 'Edit', color: '#10b981', bg: 'rgba(16,185,129,0.12)', emptyText: '暂无已标注数据，可在特征工程中合并标签列' },
  { key: 'split_train', label: '训练数据', icon: 'DataAnalysis', color: '#2563eb', bg: 'rgba(37,99,235,0.12)', emptyText: '' },
  { key: 'split_test', label: '测试数据', icon: 'Aim', color: '#dc2626', bg: 'rgba(220,38,38,0.12)', emptyText: '' },
]

const userStore = useUserStore()

const showCreateDialog = ref(false)
const datasetTab = ref('manage')
const showFieldRolesDialog = ref(false)
const fieldRolesLoading = ref(false)
const fieldRolesSaving = ref(false)
const fieldRolesMode = ref<'auto' | 'manual'>('auto')
const fieldRoleDataset = ref<DatasetItem | null>(null)
const fieldRoleRecommendation = ref<any>(null)
const fieldRoleColumns = ref<string[]>([])
const fieldRolesForm = ref<any>({ time_column: '', id_columns: [] })
const simpleMode = ref(true)

// ── DVC 数据版本 ──
const dvcLoading = ref(false)
const dvcDataset = ref('')
const dvcVersions = ref<any[]>([])
const dvcDatasets = ref<any[]>([])
const totalDvcVersions = ref(0)

async function dvcInit() {
  if (!dvcDataset.value) { ElMessage.warning('请输入数据集名称'); return }
  dvcLoading.value = true
  try {
    await post('/dvc/commit', { dataset: dvcDataset.value, message: 'init repo', files: [] })
    ElMessage.success('DVC仓库已初始化')
    dvcListVersions()
  } catch(e:any) { ElMessage.error(e?.message||'初始化失败') }
  finally { dvcLoading.value = false }
}
async function dvcListVersions() {
  if (!dvcDataset.value) return
  dvcLoading.value = true
  try {
    const d = await get(`/dvc/versions/${dvcDataset.value}`)
    dvcVersions.value = d?.versions || []
    totalDvcVersions.value = d?.total || 0
  } catch(e:any){}
  finally { dvcLoading.value = false }
}
async function dvcCommit() {
  if (!dvcDataset.value) { ElMessage.warning('请输入数据集名称'); return }
  try {
    await post('/dvc/commit', { dataset: dvcDataset.value, message: prompt('提交信息:') || 'auto', files: [] })
    ElMessage.success('版本已提交')
    dvcListVersions()
  } catch(e:any) { ElMessage.error(e?.message||'提交失败') }
}
async function dvcPush() {
  if (!dvcDataset.value) { ElMessage.warning('请输入数据集名称'); return }
  try { await post(`/dvc/push?dataset=${dvcDataset.value}`); ElMessage.success('已推送到MinIO') }
  catch(e:any) { ElMessage.error(e?.message||'推送失败') }
}
async function dvcPull() {
  if (!dvcDataset.value) { ElMessage.warning('请输入数据集名称'); return }
  try { await post(`/dvc/pull?dataset=${dvcDataset.value}`); ElMessage.success('已从MinIO拉取') }
  catch(e:any) { ElMessage.error(e?.message||'拉取失败') }
}
async function dvcCheckout(version: string) {
  if (!dvcDataset.value) return
  try { await post('/dvc/checkout', { dataset: dvcDataset.value, version }); ElMessage.success(`已切换到版本 ${version}`) }
  catch(e:any) { ElMessage.error(e?.message||'切换失败') }
}

const creating = ref(false)
const processingId = ref<number | null>(null)
const currentProcessDataset = ref<DatasetItem | null>(null)
const cleaningRules = ref<any[]>([])
const showCleaningDialog = ref(false)
const selectedCleaningRule = ref<string | number>('default')
const cleaningOutputName = ref('')
const cleaningPreview = ref<any>(null)
const processPreviewing = ref(false)
const cleaningWizardStep = ref(0)
const cleaningPlan = ref<'auto' | 'custom'>('auto')
const cleaningAdvancedPanels = ref<string[]>([])
const cleaningExecutionResult = ref<any>(null)
const dropdownPageSize = 8
const cleaningRuleQuery = ref('')
const cleaningRulePage = ref(1)
const qualityRuleQuery = ref('')
const qualityRulePage = ref(1)
const activeCleaningRules = computed(() => cleaningRules.value.filter(rule => rule.is_active))
const filteredCleaningRules = computed(() => {
  const query = cleaningRuleQuery.value.trim().toLowerCase()
  if (!query) return activeCleaningRules.value
  return activeCleaningRules.value.filter(rule =>
    String(rule.id).includes(query)
    || String(rule.name || '').toLowerCase().includes(query)
    || String(rule.description || '').toLowerCase().includes(query)
  )
})
const displayedCleaningRules = computed(() => {
  const start = (cleaningRulePage.value - 1) * dropdownPageSize
  const pageItems = filteredCleaningRules.value.slice(start, start + dropdownPageSize)
  const selected = typeof selectedCleaningRule.value === 'number'
    ? activeCleaningRules.value.find(rule => rule.id === selectedCleaningRule.value)
    : null
  return selected && !pageItems.some(rule => rule.id === selected.id)
    ? [selected, ...pageItems]
    : pageItems
})
const selectedCleaningRuleDetail = computed(() =>
  typeof selectedCleaningRule.value === 'number'
    ? activeCleaningRules.value.find(rule => rule.id === selectedCleaningRule.value)
    : null
)
const showGovernanceDialog = ref(false)
const governanceProfiles = ref<any[]>([])
const governanceQualityRules = ref<any[]>([])
const selectedGovernanceProfile = ref('basic')
const governanceThresholds = ref({ min_completeness: 95, max_duplicate_rate: 5 })
const selectedQualityRuleIds = ref<number[]>([])
const governanceReport = ref<any>(null)
const filteredQualityRules = computed(() => {
  const query = qualityRuleQuery.value.trim().toLowerCase()
  if (!query) return governanceQualityRules.value
  return governanceQualityRules.value.filter(rule =>
    String(rule.id).includes(query)
    || String(rule.name || '').toLowerCase().includes(query)
    || String(rule.field || '').toLowerCase().includes(query)
  )
})
const displayedQualityRules = computed(() => {
  const start = (qualityRulePage.value - 1) * dropdownPageSize
  const pageItems = filteredQualityRules.value.slice(start, start + dropdownPageSize)
  const selectedItems = governanceQualityRules.value.filter(rule => selectedQualityRuleIds.value.includes(rule.id))
  return [...selectedItems, ...pageItems].filter((rule, index, items) =>
    items.findIndex(candidate => candidate.id === rule.id) === index
  )
})
function filterCleaningRules(query: string) {
  cleaningRuleQuery.value = query
  cleaningRulePage.value = 1
}
function filterQualityRules(query: string) {
  qualityRuleQuery.value = query
  qualityRulePage.value = 1
}

const createForm = ref({ name: '', type: 'tabular', description: '', file: null as File | null })

interface DatasetItem {
  id: number; name: string; stage: string; row_count: number
  size_bytes: number; quality_score: number | null; governed_from: number | null; created_at: string
  has_file?: boolean
}
interface ListResponse { items: DatasetItem[] }

const governanceReports = ref<any[]>([])
const { data: listData, loading: listLoading, error: listError, refresh: refreshList } = useAsyncData<ListResponse>(
  async () => {
    const [datasetsResult, reportsResult] = await Promise.all([
      get<ListResponse>('/datasets'),
      get<any>('/dataset-governance/reports', { limit: 500 }),
    ])
    governanceReports.value = reportsResult.items || []
    return datasetsResult
  }
)
const allDatasets = computed(() => listData.value?.items || [])

const stageDatasets = (stage: string) => allDatasets.value.filter(d => d.stage === stage)
const stageCounts = computed(() => {
  const c: Record<string, number> = {}
  for (const s of stages) c[s.key] = stageDatasets(s.key).length
  return c
})

const isAIReady = (stage: string) => ['governed', 'engineered', 'labeled'].includes(stage)
const downstreamSourceStages = new Set(['engineered', 'labeled', 'split_train', 'split_test'])
function isDownstreamSource(dataset: DatasetItem) {
  return downstreamSourceStages.has(dataset.stage)
}
function isLifecycleSource(dataset: DatasetItem) {
  return !dataset.governed_from || isDownstreamSource(dataset)
}
function canCleanSource(dataset: DatasetItem) {
  return ['raw', 'engineered', 'labeled', 'split_train', 'split_test'].includes(dataset.stage)
}
function canSplitDataset(dataset: DatasetItem) {
  return Boolean(dataset?.has_file)
}
function stageTagType(stage: string) {
  if (stage === 'split_train') return 'primary'
  if (stage === 'split_test') return 'danger'
  if (stage === 'engineered') return 'primary'
  if (stage === 'labeled') return 'success'
  if (stage === 'governed') return 'success'
  return 'warning'
}

function datasetStatus(dataset: DatasetItem, row?: any) {
  if (row?.report?.status === 'failed') {
    return { type: 'danger', title: '需要处理', hint: '上次检查未通过，可调整规则后重新清洗。' }
  }
  if (!dataset.has_file) {
    return { type: 'warning', title: '等待上传文件', hint: '先上传数据文件，才能预览、清洗或训练。' }
  }
  if (dataset.stage === 'governed') {
    return { type: 'success', title: '检查通过', hint: '数据已满足基础检查，可用于后续工作。' }
  }
  if (dataset.stage === 'engineered') {
    return { type: 'primary', title: '已增加计算列', hint: '建议再进行一次清洗分析，确认新列质量。' }
  }
  if (dataset.stage === 'labeled') {
    return { type: 'success', title: '已补充标签', hint: '建议进行清洗分析后再用于训练。' }
  }
  if (dataset.stage === 'split_train') {
    return { type: 'primary', title: '训练数据', hint: '用于训练和每轮验证。' }
  }
  if (dataset.stage === 'split_test') {
    return { type: 'info', title: '测试数据', hint: '仅用于模型最终评估。' }
  }
  return { type: 'warning', title: '未拆分数据', hint: '可按需要清洗分析，或直接分割为训练数据和测试数据。' }
}

const latestGovernanceReportByDataset = computed(() => {
  const latest = new Map<number, any>()
  for (const report of governanceReports.value) {
    const sourceId = Number(report.source_dataset_id || report.summary?.source_dataset_id || 0)
    if (!sourceId) continue
    const previous = latest.get(sourceId)
    if (!previous || String(report.checked_at || '').localeCompare(String(previous.checked_at || '')) > 0) latest.set(sourceId, report)
  }
  return latest
})
function simpleDatasetReport(dataset: DatasetItem) {
  return latestGovernanceReportByDataset.value.get(dataset.id)
    || (dataset.governed_from ? latestGovernanceReportByDataset.value.get(dataset.governed_from) : undefined)
}
function simpleDatasetStatus(dataset: DatasetItem) {
  const report = latestGovernanceReportByDataset.value.get(dataset.id)
  return datasetStatus(dataset, report ? { report } : undefined)
}
const expandedSourceFolders = ref<string[]>([])

/**
 * 找到数据集可追溯到的最上游来源。派生数据可能来自清洗结果、
 * 特征工程结果或分割结果，因此不能只查看一次 governed_from。
 */
const sourceFolders = computed(() => {
  const byId = new Map(allDatasets.value.map(dataset => [dataset.id, dataset]))
  const folders = new Map<number, { root: DatasetItem; items: DatasetItem[] }>()

  const findRoot = (dataset: DatasetItem) => {
    let current = dataset
    const visited = new Set<number>()
    while (current.governed_from && !visited.has(current.id)) {
      visited.add(current.id)
      const parent = byId.get(current.governed_from)
      if (!parent) break
      current = parent
    }
    return current
  }

  for (const dataset of allDatasets.value) {
    const root = findRoot(dataset)
    const folder = folders.get(root.id) || { root, items: [] }
    folder.items.push(dataset)
    folders.set(root.id, folder)
  }

  return [...folders.values()]
    .map(folder => ({
      ...folder,
      items: [...folder.items].sort((a, b) => {
        if (a.id === folder.root.id) return -1
        if (b.id === folder.root.id) return 1
        return String(b.created_at || '').localeCompare(String(a.created_at || ''))
      }),
      latestAt: folder.items.reduce((latest, item) =>
        String(item.created_at || '').localeCompare(String(latest || '')) > 0 ? item.created_at : latest,
        folder.root.created_at,
      ),
    }))
    .sort((a, b) => String(b.latestAt || '').localeCompare(String(a.latestAt || '')))
})
const sourceFolderPage = ref(1)
const sourceFolderPageSize = ref(8)
const sourceFolderPageCount = computed(() => Math.max(1, Math.ceil(sourceFolders.value.length / sourceFolderPageSize.value)))
const pagedSourceFolders = computed(() => {
  const start = (sourceFolderPage.value - 1) * sourceFolderPageSize.value
  return sourceFolders.value.slice(start, start + sourceFolderPageSize.value)
})
watch([() => sourceFolders.value.length, sourceFolderPageSize], () => {
  if (sourceFolderPage.value > sourceFolderPageCount.value) sourceFolderPage.value = sourceFolderPageCount.value
})
watch(sourceFolders, folders => {
  const allowed = new Set(folders.map(folder => String(folder.root.id)))
  expandedSourceFolders.value = expandedSourceFolders.value.filter(id => allowed.has(id))
  if (!expandedSourceFolders.value.length && folders.length) expandedSourceFolders.value = [String(folders[0].root.id)]
}, { immediate: true })


const lifecycleRows = computed(() => {
  const datasetsById = new Map(allDatasets.value.map(dataset => [dataset.id, dataset]))
  const childrenBySource = new Map<number, DatasetItem[]>()
  for (const dataset of allDatasets.value) {
    if (dataset.governed_from && !isDownstreamSource(dataset)) {
      const children = childrenBySource.get(dataset.governed_from) || []
      children.push(dataset)
      childrenBySource.set(dataset.governed_from, children)
    }
  }
  const reportsBySource = new Map<number, any[]>()
  for (const report of governanceReports.value) {
    const sourceId = Number(report.source_dataset_id || report.summary?.source_dataset_id || 0)
    if (!sourceId) continue
    const reports = reportsBySource.get(sourceId) || []
    reports.push(report)
    reportsBySource.set(sourceId, reports)
  }
  const rows: any[] = []
  const sources = allDatasets.value.filter(isLifecycleSource)
  for (const source of sources) {
    const reports = (reportsBySource.get(source.id) || []).sort((a, b) =>
      String(b.checked_at || '').localeCompare(String(a.checked_at || ''))
    )
    const matchedOutputs = new Set<number>()
    for (const report of reports) {
      const outputId = Number(report.output_dataset_id || report.summary?.output_dataset_id || 0)
      const result = outputId ? datasetsById.get(outputId) || null : null
      if (result) matchedOutputs.add(result.id)
      rows.push({ key: `report-${report.id}`, source, result, report })
    }
    for (const result of childrenBySource.get(source.id) || []) {
      if (!matchedOutputs.has(result.id)) {
        rows.push({ key: `result-${result.id}`, source, result, report: null })
      }
    }
    if (!reports.length && !(childrenBySource.get(source.id) || []).length) {
      rows.push({ key: `source-${source.id}`, source, result: null, report: null })
    }
  }
  return rows.sort((a, b) => {
    const aTime = a.report?.checked_at || a.result?.created_at || a.source.created_at || ''
    const bTime = b.report?.checked_at || b.result?.created_at || b.source.created_at || ''
    return String(bTime).localeCompare(String(aTime))
  })
})
const lifecyclePage = ref(1)
const lifecyclePageSize = ref(10)
const lifecyclePageCount = computed(() => Math.max(1, Math.ceil(lifecycleRows.value.length / lifecyclePageSize.value)))
const pagedLifecycleRows = computed(() => {
  const start = (lifecyclePage.value - 1) * lifecyclePageSize.value
  return lifecycleRows.value.slice(start, start + lifecyclePageSize.value)
})
watch([() => lifecycleRows.value.length, lifecyclePageSize], () => {
  if (lifecyclePage.value > lifecyclePageCount.value) lifecyclePage.value = lifecyclePageCount.value
})
const lifecycleStats = computed(() => ({
  raw: allDatasets.value.filter(dataset => dataset.stage === 'raw').length,
  engineered: allDatasets.value.filter(dataset => dataset.stage === 'engineered').length,
  labeled: allDatasets.value.filter(dataset => dataset.stage === 'labeled').length,
  passed: governanceReports.value.filter(report => report.status === 'passed').length,
  failed: governanceReports.value.filter(report => report.status === 'failed').length,
}))

const showLifecycleReport = ref(false)
const currentLifecycleReport = ref<any>(null)
function openReport(report: any) {
  currentLifecycleReport.value = report
  showLifecycleReport.value = true
}

const formatSize = (bytes: number) => {
  if (!bytes) return '—'
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / (1024 * 1024)).toFixed(1) + 'MB'
}
const formatDate = (d: string) => d ? dayjs(d).format('MM-DD HH:mm') : '—'
const formatDateTime = (d: string) => d ? dayjs(d).format('YYYY-MM-DD HH:mm') : '—'
const stageLabel = (stage?: string) => stages.find(s => s.key === stage)?.label || stage || '—'

// ── 数据集详情抽屉 ──
const showDetailDrawer = ref(false)
const detailLoading = ref(false)
const detailData = ref<any>(null)
const detailColumns = ref<any[]>([])
const previewColumns = ref<{ prop: string; label: string }[]>([])
const previewRows = ref<any[]>([])

function parseColumnsInfo(raw: any): any[] {
  if (!raw) return []
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string') {
    try { const p = JSON.parse(raw); return Array.isArray(p) ? p : [] } catch { return [] }
  }
  return []
}


async function openFieldRolesDialog(dataset: DatasetItem) {
  showFieldRolesDialog.value = true; fieldRolesLoading.value = true; fieldRoleDataset.value = dataset
  try {
    const result = await get('/datasets/' + dataset.id + '/field-roles')
    fieldRoleRecommendation.value = result?.recommendation || null
    const recommended = result?.recommendation || {}
    const saved = result?.saved
    fieldRoleColumns.value = [...new Set([...(recommended.feature_columns || []), ...(recommended.id_columns || []), ...(recommended.candidates?.time_columns || []), ...(recommended.candidates?.target_columns || [])])]
    fieldRolesForm.value = { time_column: saved?.time_column || recommended.time_column || '', id_columns: [...(saved?.id_columns || recommended.id_columns || [])] }
    fieldRolesMode.value = saved ? 'manual' : 'auto'
  } catch (e: any) { ElMessage.error(e?.message || '字段用途分析失败') }
  finally { fieldRolesLoading.value = false }
}
async function saveFieldRoles() {
  const dataset = fieldRoleDataset.value
  if (!dataset) return
  const source = fieldRolesMode.value === 'auto' ? fieldRoleRecommendation.value : fieldRolesForm.value
  const settings = { time_column: source?.time_column || null, id_columns: [...(source?.id_columns || [])] }
  fieldRolesSaving.value = true
  try {
    await request.put('/datasets/' + dataset.id + '/field-roles', settings)
    ElMessage.success('字段用途已保存'); showFieldRolesDialog.value = false
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败') }
  finally { fieldRolesSaving.value = false }
}
async function openDetail(ds: DatasetItem) {
  showDetailDrawer.value = true
  detailLoading.value = true
  detailData.value = ds
  detailColumns.value = parseColumnsInfo((ds as any).columns_info)
  previewColumns.value = []
  previewRows.value = []
  try {
    // 拉取最新详情（columns_info 等)
    const detail = await get(`/datasets/${ds.id}`)
    if (detail && typeof detail === 'object') {
      detailData.value = detail
      detailColumns.value = parseColumnsInfo(detail.columns_info)
    }
  } catch (e: any) {
    // 详情接口失败时沿用列表数据，不阻断
  }
  // 拉取行预览（与详情并行容错)
  try {
    const preview = await get(`/datasets/${ds.id}/rows`, { offset: 0, limit: 10 })
    const cols: string[] = preview?.columns || []
    previewColumns.value = cols.map((c, i) => ({ prop: `c${i}`, label: c }))
    const rows: any[][] = preview?.rows || []
    previewRows.value = rows.map(r => {
      const o: Record<string, any> = {}
      cols.forEach((_, i) => { o[`c${i}`] = r[i] })
      return o
    })
  } catch (e: any) {
    // 预览失败（无文件等）保持空态
  } finally {
    detailLoading.value = false
  }
}

async function createDataset() {
  if (!createForm.value.name) { ElMessage.warning('请输入名称'); return }
  creating.value = true
  try {
    // 1. 创建数据集记录
    const res = await post('/datasets', { name: createForm.value.name, type: createForm.value.type, description: createForm.value.description })
    const datasetId = res?.id || res?.dataset_id
    // 2. 如果有文件，上传解析
    if (datasetId && createForm.value.file) {
      const formData = new FormData()
      formData.append('file', createForm.value.file)
      await request.post(`/datasets/${datasetId}/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
    }
    ElMessage.success('创建成功' + (createForm.value.file ? '，数据已导入' : ''))
    showCreateDialog.value = false; refreshList()
  } catch (e: any) { ElMessage.error(e?.message || '创建失败') }
  finally { creating.value = false }
}

async function openCleaningDialog(row: DatasetItem) {
  currentProcessDataset.value = row
  selectedCleaningRule.value = 'default'
  cleaningOutputName.value = row.name + '_清洗结果'
  cleaningPreview.value = null
  cleaningWizardStep.value = 0
  cleaningPlan.value = 'auto'
  cleaningAdvancedPanels.value = []
  cleaningExecutionResult.value = null
  selectedGovernanceProfile.value = 'basic'
  governanceThresholds.value = { min_completeness: 95, max_duplicate_rate: 5 }
  selectedQualityRuleIds.value = []
  cleaningRuleQuery.value = ''
  cleaningRulePage.value = 1
  qualityRuleQuery.value = ''
  qualityRulePage.value = 1
  showCleaningDialog.value = true
  try {
    const [rules, profiles, qualityRules] = await Promise.all([
      get<any>('/data-cleaning/rules', { limit: 500 }),
      get<any>('/dataset-governance/profiles'),
      get<any>('/dataset-governance/quality-rules'),
    ])
    cleaningRules.value = rules.items || []
    governanceProfiles.value = profiles.items || []
    governanceQualityRules.value = qualityRules.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || '清洗和治理规则加载失败')
  }
}

function selectCleaningPlan(plan: 'auto' | 'custom') {
  cleaningPlan.value = plan
  if (plan === 'auto') selectedCleaningRule.value = 'default'
  cleaningPreview.value = null
}
async function startCleaningPreview() {
  if (cleaningPlan.value === 'auto') selectedCleaningRule.value = 'default'
  if (cleaningPlan.value === 'custom' && typeof selectedCleaningRule.value !== 'number') {
    ElMessage.warning('请选择已有清洗方案')
    return
  }
  await previewCleaning()
  if (cleaningPreview.value) cleaningWizardStep.value = 1
}
async function previewCleaning() {
  if (!currentProcessDataset.value) return
  processPreviewing.value = true
  try {
    cleaningPreview.value = await post('/data-cleaning/preview', {
      dataset_id: currentProcessDataset.value.id,
      rule_id: selectedCleaningRule.value === 'default' ? null : selectedCleaningRule.value,
      sample_size: 500,
      governance_profile: selectedGovernanceProfile.value,
      governance_thresholds: {
        min_completeness: governanceThresholds.value.min_completeness / 100,
        max_duplicate_rate: governanceThresholds.value.max_duplicate_rate / 100,
      },
      quality_rule_ids: selectedQualityRuleIds.value,
    })
  } catch (e: any) {
    cleaningPreview.value = null
    ElMessage.error(e?.message || '规则预览失败')
  } finally {
    processPreviewing.value = false
  }
}

async function executeCleaning() {
  const row = currentProcessDataset.value
  if (!row || !cleaningPreview.value) return
  try {
    await ElMessageBox.confirm(
      '系统将清洗完整数据并立即执行治理检查。只有全部通过才生成结果，当前源数据不会被覆盖。是否继续？',
      '确认清洗与检查',
      { type: 'warning' },
    )
  } catch { return }
  processingId.value = row.id
  try {
    const result = await post<any>('/data-cleaning/execute', {
      dataset_id: row.id,
      rule_id: selectedCleaningRule.value === 'default' ? null : selectedCleaningRule.value,
      output_name: cleaningOutputName.value || null,
      governance_profile: selectedGovernanceProfile.value,
      governance_thresholds: {
        min_completeness: governanceThresholds.value.min_completeness / 100,
        max_duplicate_rate: governanceThresholds.value.max_duplicate_rate / 100,
      },
      quality_rule_ids: selectedQualityRuleIds.value,
    })
    cleaningExecutionResult.value = result
    cleaningWizardStep.value = 2
    if (result.status === 'rejected') {
      cleaningPreview.value = { ...cleaningPreview.value, governance_report: result.report }
      ElMessage.warning(result.message)
    } else {
      ElMessage.success(result.message + '，结果数据集 ID：' + result.output_dataset.id)
    }
    await refreshList()
  } catch (e: any) {
    ElMessage.error(e?.message || '清洗失败')
  } finally {
    processingId.value = null
  }
}

function openCleaningExecutionReport() {
  const result = cleaningExecutionResult.value
  if (!result?.report_id) return
  const stored = governanceReports.value.find(report => String(report.id) === String(result.report_id))
  openReport(stored || {
    ...result.report,
    id: result.report_id,
    checked_at: new Date().toISOString(),
    summary: { ...(result.report || {}), checks: result.report?.checks || [] },
  })
}
async function openGovernanceDialog(row: DatasetItem) {
  currentProcessDataset.value = row
  selectedGovernanceProfile.value = 'basic'
  selectedQualityRuleIds.value = []
  governanceReport.value = null
  showGovernanceDialog.value = true
  try {
    const [profiles, rules] = await Promise.all([
      get<any>('/dataset-governance/profiles'),
      get<any>('/dataset-governance/quality-rules'),
    ])
    governanceProfiles.value = profiles.items || []
    governanceQualityRules.value = rules.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || '治理验收标准加载失败')
  }
}

async function previewAcceptance() {
  if (!currentProcessDataset.value) return
  processPreviewing.value = true
  try {
    governanceReport.value = await post('/dataset-governance/preview', {
      dataset_id: currentProcessDataset.value.id,
      profile: selectedGovernanceProfile.value,
      quality_rule_ids: selectedQualityRuleIds.value,
    })
  } catch (e: any) {
    governanceReport.value = null
    ElMessage.error(e?.message || '治理验收预览失败')
  } finally {
    processPreviewing.value = false
  }
}

async function acceptGovernance() {
  const row = currentProcessDataset.value
  if (!row || !governanceReport.value) return
  processingId.value = row.id
  try {
    const result = await post<any>('/dataset-governance/accept', {
      dataset_id: row.id,
      profile: selectedGovernanceProfile.value,
      quality_rule_ids: selectedQualityRuleIds.value,
    })
    if (result.report.passed) {
      ElMessage.success(result.message)
      showGovernanceDialog.value = false
    } else {
      ElMessage.warning(result.message)
      governanceReport.value = result.report
    }
    refreshList()
  } catch (e: any) {
    ElMessage.error(e?.message || '治理验收失败')
  } finally {
    processingId.value = null
  }
}

async function downloadDataset(row: DatasetItem) {
  const token = localStorage.getItem('foundation_access_token')
  try {
    const resp = await fetch(`/api/v1/datasets/${row.id}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}
    })
    if (!resp.ok) throw new Error('下载失败 (HTTP ' + resp.status + ')')
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = (row.name || 'dataset').replace(/[^a-zA-Z0-9\u4e00-\u9fa5._-]/g, '_') + '.csv'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('下载成功')
  } catch (e: any) {
    ElMessage.error('下载失败: ' + (e.message || '未知错误'))
  }
}

const showSplitDialog = ref(false)
const splitDataset = ref<DatasetItem | null>(null)
const splitStrategy = ref<'random' | 'chronological'>('random')
const splitWorking = ref<'' | 'save' | 'download'>('')
const splitPreview = computed(() => {
  const total = Number(splitDataset.value?.row_count || 0)
  const testRows = total ? Math.max(1, Math.ceil(total * 0.1)) : 0
  return { trainRows: Math.max(0, total - testRows), testRows }
})

function openSplitDialog(row: DatasetItem) {
  splitDataset.value = row
  splitStrategy.value = 'random'
  splitWorking.value = ''
  showSplitDialog.value = true
}

async function saveDatasetSplit() {
  if (!splitDataset.value) return
  splitWorking.value = 'save'
  try {
    const result: any = await post(`/datasets/${splitDataset.value.id}/split/save`, {
      strategy: splitStrategy.value,
    })
    ElMessage.success(`${result.train_name}（${result.train_rows}行）和${result.test_name}（${result.test_rows}行）已保存`)
    showSplitDialog.value = false
    await refreshList()
  } catch (e: any) {
    ElMessage.error(e?.message || '数据集分割保存失败')
  } finally {
    splitWorking.value = ''
  }
}

async function downloadDatasetSplit() {
  if (!splitDataset.value) return
  splitWorking.value = 'download'
  try {
    const token = localStorage.getItem('foundation_access_token')
    const response = await fetch(`/api/v1/datasets/${splitDataset.value.id}/split/download`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ strategy: splitStrategy.value }),
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail || `下载失败 (HTTP ${response.status})`)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${splitDataset.value.name}分割.zip`.replace(/[^a-zA-Z0-9\u4e00-\u9fa5._-]/g, '_')
    anchor.click()
    URL.revokeObjectURL(url)
    ElMessage.success('训练数据和测试数据ZIP已下载')
  } catch (e: any) {
    ElMessage.error(e?.message || '数据集分割下载失败')
  } finally {
    splitWorking.value = ''
  }
}
async function deleteDataset(row: DatasetItem) {
  try {
    await apiDel('/datasets/' + row.id)
    ElMessage.success('数据集已删除')
    await refreshList()
  } catch (e: any) {
    ElMessage.error('删除失败：' + (e?.message || '未知错误'))
  }
}
async function deleteGovernanceReport(report: any) {
  try {
    await apiDel('/dataset-governance/reports/' + report.id)
    ElMessage.success('治理报告已删除')
    await refreshList()
  } catch (e: any) {
    ElMessage.error('报告删除失败：' + (e?.message || '未知错误'))
  }
}
function handleFileChange(file: any) { createForm.value.file = file.raw }
function openCreateDialog() { showCreateDialog.value = true }
</script>

<style scoped lang="scss">
@use '@/assets/styles/variables.scss' as *;

.dataset-status { display:flex; align-items:center; flex-wrap:wrap; gap:6px; margin-top:7px; color:var(--el-text-color-secondary); font-size:12px; line-height:1.45; }
.stats-row { margin-bottom: 8px; }
.data-flow-note { margin:0 0 20px; color:var(--el-text-color-secondary); font-size:13px; }

.loading-block, .error-block { padding: 40px 0; }
.process-preview { margin-top:16px; padding:14px; border:1px solid var(--border); border-radius:8px; }
.cleaning-wizard-steps { margin-bottom:22px; }
.cleaning-wizard-choices { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }
.cleaning-wizard-choice { padding:18px; text-align:left; cursor:pointer; border:1px solid var(--el-border-color); border-radius:10px; background:var(--el-fill-color-blank); color:var(--el-text-color-primary); }
.cleaning-wizard-choice:hover, .cleaning-wizard-choice.active { border-color:var(--el-color-primary); background:var(--el-color-primary-light-9); }
.cleaning-wizard-choice b { display:block; margin-bottom:7px; font-size:15px; }
.cleaning-wizard-choice span { color:var(--el-text-color-secondary); font-size:13px; line-height:1.5; }
.cleaning-wizard-form { margin-top:18px; }
.cleaning-advanced { margin-top:18px; }
.threshold-slider { display:flex; align-items:center; width:100%; gap:8px; }
.threshold-slider :deep(.el-slider) { flex:1; }
.threshold-slider span { color:var(--el-text-color-secondary); }
.simple-dataset-card { overflow:hidden; }
.simple-list-header { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.simple-list-header b { color:var(--el-text-color-primary); }
.simple-list-controls { display:flex; align-items:center; gap:8px; }
.simple-dataset-grid { margin-top:-4px; }
.simple-dataset-grid :deep(.el-col) { margin-top:14px; }
.simple-dataset-item { min-height:176px; padding:16px; border:1px solid var(--el-border-color-lighter); border-radius:10px; background:var(--el-fill-color-blank); transition:box-shadow .2s, border-color .2s; }
.simple-dataset-item:hover { border-color:var(--el-color-primary-light-5); box-shadow:0 4px 14px rgba(0,0,0,.06); }
.simple-dataset-top { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.simple-dataset-name { display:flex; align-items:center; min-width:0; gap:7px; cursor:pointer; }
.simple-dataset-name b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--el-text-color-primary); }
.simple-dataset-meta { margin-top:10px; color:var(--el-text-color-secondary); font-size:12px; }
.simple-dataset-origin { margin-top:6px; color:var(--el-color-primary); font-size:12px; }
.simple-dataset-actions { display:flex; align-items:center; gap:4px; margin-top:13px; }
.simple-dataset-actions :deep(.el-button + .el-button) { margin-left:0; }
.simple-pagination { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:20px 4px 2px; color:var(--el-text-color-secondary); font-size:13px; }
.source-folder-card :deep(.el-card__body) { padding-top: 8px; }
.source-folder-hint { margin-left: 10px; color: var(--el-text-color-secondary); font-size: 12px; font-weight: 400; }
.source-folder-list { border-top: 0; border-bottom: 0; }
.source-folder-list :deep(.el-collapse-item__header) { min-height: 52px; padding: 0 8px; }
.source-folder-list :deep(.el-collapse-item__wrap) { border-bottom: 0; }
.source-folder-list :deep(.el-collapse-item__content) { padding: 4px 8px 14px; }
.source-folder-title { display:grid; grid-template-columns:24px 66px minmax(0, 1fr) 86px; align-items:center; column-gap:8px; min-width:0; flex:1; }
.source-folder-title b { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.source-folder-title span { color:var(--el-text-color-secondary); font-size:12px; font-weight:400; text-align:right; white-space:nowrap; }
.source-folder-icon { color:var(--el-color-warning); font-size:19px; }
.source-folder-items { display:flex; flex-direction:column; gap:8px; padding:2px 0; }
.source-folder-dataset-row { display:grid; grid-template-columns:minmax(0, 1fr) auto; align-items:center; min-height:0; padding:12px 14px; }
.source-folder-dataset-main { min-width:0; }
.source-folder-dataset-row .simple-dataset-meta { margin-top:7px; }
.source-folder-dataset-actions { justify-content:flex-end; flex-wrap:wrap; max-width:410px; margin-top:0; padding-left:14px; }
@media (max-width: 820px) {
  .source-folder-title { grid-template-columns:24px 60px minmax(0, 1fr); }
  .source-folder-title span { display:none; }
  .source-folder-dataset-row { grid-template-columns:1fr; align-items:start; }
  .source-folder-dataset-actions { justify-content:flex-start; max-width:none; margin-top:10px; padding-left:0; }
}
.lifecycle-card { overflow: hidden; }
.lifecycle-header { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.lifecycle-view-actions { display:flex; align-items:center; flex:0 0 auto; gap:8px; }
.lifecycle-hint { margin-top:5px; color:var(--el-text-color-secondary); font-size:12px; font-weight:400; }
@media (max-width: 640px) {
  .simple-list-header, .lifecycle-header, .simple-pagination { align-items:flex-start; flex-direction:column; }
  .simple-list-controls { width:100%; justify-content:space-between; }
  .simple-dataset-top { flex-direction:column; }
  .cleaning-wizard-choices { grid-template-columns:1fr; }
}
.lifecycle-pagination { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 4px 4px; color:var(--el-text-color-secondary); font-size:13px; }
.asset-name { display:flex; align-items:center; gap:8px; cursor:pointer; min-width:0; }
.asset-name b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.asset-meta { margin-top:7px; color:var(--el-text-color-secondary); font-size:12px; }
.asset-origin { margin-top:5px; color:var(--el-color-primary); font-size:12px; }
.asset-cell { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:58px; }
.asset-info { flex:1; min-width:0; }
.asset-side-actions { display:flex; flex-direction:column; align-items:flex-end; flex:0 0 auto; padding-left:10px; border-left:1px solid var(--el-border-color-lighter); }
.asset-side-actions :deep(.el-button) { margin:0; min-height:24px; padding:2px 5px; }
.report-summary { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.report-summary span { color:var(--el-text-color-secondary); font-size:12px; }
.empty-cell { color:var(--el-text-color-placeholder); font-size:13px; }
.select-pagination { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:4px 2px; color:var(--el-text-color-secondary); font-size:12px; }
.split-hint { width:100%; margin-top:6px; color:var(--el-text-color-secondary); font-size:12px; line-height:1.5; }

.stages-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stage-card {
  border-left: 4px solid transparent;
  transition: border-color .3s;

  &.ai-ready { border-left-color: #10b981; }
  &:not(.ai-ready) { border-left-color: #f59e0b; }
}

.stage-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stage-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15px;
}

.stage-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.stage-card-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.stage-empty {
  padding: 20px 0;
}

.dataset-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dataset-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
  transition: background .2s;

  &:hover { background: var(--el-fill-color); }
}

.dataset-item-main { flex: 1; min-width: 0; }
.dataset-item-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  margin-bottom: 4px;
}

.dataset-item-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.meta-sep { color: var(--el-text-color-placeholder); }

.dataset-item-time {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;
}

.dataset-item-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  margin-left: 12px;
}

.detail-section-title {
  font-size: 14px;
  font-weight: 600;
  margin: 16px 0 8px;
  color: var(--el-text-color-primary);
}
</style>
