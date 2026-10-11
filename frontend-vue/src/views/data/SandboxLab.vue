<template>
<div class="workspace-root">
  <section v-if="showRunReport" class="run-report-page">
    <header class="run-report-header">
      <div>
        <el-button @click="closeRunReport">← 返回代码编辑器</el-button>
        <span class="run-report-title">{{ runReport?.training_name || '运行结果' }}</span>
        <el-tag :type="runReportStatusType" effect="dark">{{ runReportStatusLabel }}</el-tag>
      </div>
      <div class="run-report-id">运行 ID：{{ runReport?.run_id || runReport?.id || '-' }}</div>
    </header>

    <div class="run-report-content">
      <div class="run-summary-grid">
        <div class="run-summary-card">
          <span>执行状态</span><strong>{{ runReportStatusLabel }}</strong>
          <small>退出码 {{ runReport?.returncode ?? runReport?.analysis?.returncode ?? '-' }}</small>
        </div>
        <div class="run-summary-card">
          <span>运行耗时</span><strong>{{ formatDuration(runReport?.duration_ms ?? runReport?.analysis?.duration_ms) }}</strong>
          <small>沙箱进程实际执行时间</small>
        </div>
        <div class="run-summary-card">
          <span>运行环境</span><strong>{{ runReport?.environment?.name || '平台默认环境' }}</strong>
          <small>Python {{ runReport?.environment?.python_version || '-' }}</small>
        </div>
        <div class="run-summary-card">
          <span>标准输出</span><strong>{{ runReport?.analysis?.stdout_lines ?? outputLineCount }} 行</strong>
          <small>{{ formatSize(runReport?.analysis?.stdout_bytes || 0) || '0B' }}</small>
        </div>
      </div>

      <el-card shadow="never" class="run-info-card">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="入口文件">{{ runReport?.entry_path || activeFile || '-' }}</el-descriptions-item>
          <el-descriptions-item label="数据输入">{{ reportInputLabel('dataset') }}</el-descriptions-item>
          <el-descriptions-item label="参数输入">{{ reportInputLabel('hyperparameters') }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-alert
        v-if="runReport?.model_registry"
        :title="runReport.model_registry.status === 'success'
          ? `模型 #${runReport.model_registry.model_id} 已保存，可前往模型列表部署`
          : '代码运行成功，但模型没有入库'"
        :description="runReport.model_registry.status === 'success'
          ? `已验证 ${runReport.model_registry.predictor_type || '预测模型'} 制品，并绑定本次沙箱运行环境。`
          : runReport.model_registry.message"
        :type="runReport.model_registry.status === 'success' ? 'success' : 'warning'"
        :closable="false" show-icon style="margin-bottom:16px"
      />

      <el-alert
        v-if="!runMetricRows.length"
        title="本次输出中没有识别到结构化指标"
        description="普通脚本可以没有指标；如需指标图表，请输出 Accuracy、R²、RMSE、MAE、Loss 等“指标名: 数值”。"
        type="info" :closable="false" show-icon
      />

      <div v-else class="run-metric-grid">
        <el-card shadow="never" class="run-metric-panel">
          <template #header><strong>实验指标</strong></template>
          <div class="run-metric-cards">
            <div v-for="item in runMetricRows" :key="item.name" class="run-metric-card">
              <span>{{ item.label }}</span><strong>{{ formatMetric(item.value) }}</strong>
            </div>
          </div>
          <div ref="runMetricChartRef" class="run-chart"></div>
        </el-card>
        <el-card v-if="runSeriesRows.length" shadow="never" class="run-metric-panel">
          <template #header><strong>训练过程</strong></template>
          <div ref="runSeriesChartRef" class="run-chart"></div>
        </el-card>
      </div>

      <el-alert
        v-if="runMetricRows.length && !hasPredictionVisualization"
        title="已识别实验指标，但脚本没有提供真实值和预测值"
        description="如需预测走势图和残差分析，请将 y_true、y_pred 写入环境变量 ODF_RESULT_PATH 指向的JSON文件；新建机器学习和深度学习项目已自动包含该输出。"
        type="warning" :closable="false" show-icon
      />

      <el-card v-if="hasPredictionVisualization" shadow="never" class="run-prediction-section">
        <template #header>
          <div class="run-section-header">
            <strong>预测结果分析</strong>
            <el-tag type="info">{{ runVisualization.sample_count || 0 }} 个样本</el-tag>
          </div>
        </template>
        <div class="run-prediction-grid">
          <div v-if="runVisualization.prediction_trend" class="run-visual-card">
            <h4>真实值与预测值走势</h4><div ref="runTrendChartRef" class="run-chart"></div>
          </div>
          <div v-if="runVisualization.actual_vs_predicted" class="run-visual-card">
            <h4>真实值与预测值拟合</h4><div ref="runScatterChartRef" class="run-chart"></div>
          </div>
          <div v-if="runVisualization.residual_histogram" class="run-visual-card">
            <h4>残差分布</h4><div ref="runResidualChartRef" class="run-chart"></div>
          </div>
          <div v-if="runVisualization.feature_importance" class="run-visual-card">
            <h4>特征重要性</h4><div ref="runImportanceChartRef" class="run-chart"></div>
          </div>
          <div v-if="runVisualization.confusion_matrix" class="run-visual-card">
            <h4>混淆矩阵</h4><div ref="runConfusionChartRef" class="run-chart"></div>
          </div>
          <div v-if="runVisualization.class_distribution" class="run-visual-card">
            <h4>类别分布对比</h4><div ref="runClassDistributionChartRef" class="run-chart"></div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="run-log-card">
        <template #header><strong>运行日志</strong></template>
        <el-tabs>
          <el-tab-pane label="标准输出">
            <pre class="run-report-log">{{ runReport?.output || '无标准输出' }}</pre>
          </el-tab-pane>
          <el-tab-pane label="错误输出">
            <pre class="run-report-log error">{{ runReport?.error || '无错误输出' }}</pre>
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </div>
  </section>

  <!-- 左侧文件树 -->
  <aside v-show="!showRunReport" class="file-sidebar">
    <div class="sidebar-header">
      <h3>📁 {{ currentProject ? '当前项目文件' : '文件' }}</h3>
      <div class="sidebar-actions">
        <el-button size="small" circle :disabled="!currentProject" @click="openAiCreate" title="AI新建脚本">✨</el-button>
        <el-button size="small" circle :disabled="!currentProject" @click="showNewFile=true" title="新建文件">📄</el-button>
        <el-button size="small" circle :disabled="!currentProject" @click="showNewDir=true" title="新建目录">📁</el-button>
        <el-upload :show-file-list="false" :http-request="handleUpload" :disabled="!currentProject" accept="*" style="display:inline">
          <el-button size="small" circle title="上传文件">⬆</el-button>
        </el-upload>
      </div>
    </div>
    <div v-if="currentProject" class="current-project-bar">
      <span :title="currentProject.path">{{ currentProject.name }}</span>
      <el-button link type="primary" @click="leaveCurrentProject">切换项目</el-button>
    </div>
    <div class="file-tree" v-loading="fileLoading">
      <div v-for="f in fileTree" :key="f.path" 
        :class="['tree-item', { active: activeFile===f.path, 'is-dir': f.type==='dir' }]"
        @click="f.type==='dir' ? toggleDir(f) : openFile(f)"
        @contextmenu.prevent="contextMenu($event, f)"
        :style="{ paddingLeft: (f.depth*16+8)+'px' }">
        <span class="tree-icon">{{ f.type==='dir' ? (f.expanded ? '📂' : '📁') : iconForFile(f.name) }}</span>
        <span class="tree-name">{{ f.name }}</span>
        <span class="tree-size" v-if="f.type==='file'">{{ formatSize(f.size) }}</span>
        <span class="tree-actions" v-if="contextTarget===f">
          <el-button size="small" link @click.stop="deleteItem(f)">🗑</el-button>
          <el-button size="small" link @click.stop="renameItem(f)">✏️</el-button>
        </span>
      </div>
      <div v-if="fileTree.length===0 && !fileLoading" class="empty-tree">📂 {{ currentProject ? '当前项目中没有可编辑文件' : '请从下方选择算法，或新建代码项目' }}</div>
    </div>
    
    <!-- 算法商店面板 -->
    <div class="sidebar-section algorithm-section">
      <div class="section-title">
        <h4>🧩 算法商店</h4>
        <small>{{ algorithms.length }} 项</small>
      </div>
      <el-input v-model="algorithmSearch" size="small" clearable placeholder="按名称或 ID 搜索" @keyup.enter="loadAlgorithms" @clear="loadAlgorithms">
        <template #append><el-button @click="loadAlgorithms">搜索</el-button></template>
      </el-input>
      <div class="section-list" v-loading="algorithmsLoading">
        <div v-for="item in algorithms" :key="item.id" :class="['algorithm-item', { active: currentProject?.algorithm_id === item.id }]" @click="importAlgorithm(item)">
          <div class="item-main"><span>#{{ item.id }}</span> {{ item.name }}</div>
          <small>{{ item.asset_type_label }} · {{ item.source_label }}</small>
        </div>
        <div v-if="!algorithmsLoading && algorithms.length===0" class="section-empty">没有匹配的算法</div>
      </div>
    </div>

    <!-- 数据集面板 -->
    <div class="sidebar-section dataset-section">
      <div class="section-title">
        <h4>📊 可载入数据集</h4>
        <small>{{ datasets.length }} 项</small>
      </div>
      <div class="section-list">
        <div v-for="ds in datasets" :key="ds.id" class="ds-item" @click="loadDatasetCode(ds)" :title="ds.description">
          <span>📋</span><span class="item-main">#{{ ds.id }} {{ ds.name }}</span>
          <small>{{ ds.row_count ?? '?' }}行</small>
        </div>
        <div v-if="datasets.length===0" class="section-empty">暂无带真实文件的数据集</div>
      </div>
    </div>
  </aside>

  <!-- 主编辑区 -->
  <main v-show="!showRunReport" class="editor-main">
    <!-- Tab bar -->
    <div class="tab-bar" v-if="openTabs.length>0">
      <div v-for="tab in openTabs" :key="tab.path"
        :class="['tab', { active: activeFile===tab.path }]"
        @click="openFile(tab)">
        <span>{{ tab.name }}</span>
        <span v-if="isDirty(tab.path)" class="dirty-dot" title="有未保存的修改">●</span>
        <span class="tab-close" @click.stop="closeTab(tab)">×</span>
      </div>
      <div class="tab-actions">
        <el-select v-model="selectedEnvironmentId" size="small" style="width:310px" placeholder="选择已构建的运行环境">
          <el-option
            v-for="env in environments"
            :key="env.id"
            :label="environmentLabel(env)"
            :value="env.id"
            :disabled="!env.available"
          >
            <div class="environment-option">
              <span>{{ env.name }}</span>
              <small>{{ env.available ? `Python ${env.actual_python_version} · ${env.dependency_set?.name || '标准库'}` : env.unavailable_reason }}</small>
            </div>
          </el-option>
        </el-select>
        <el-tag v-if="activeDirty" size="small" type="warning">未保存</el-tag>
        <el-button size="small" @click="saveCurrent" :loading="saving" :disabled="!activeFile || activeReadOnly">💾 {{ workspaceSaveLabel }}</el-button>
        <el-button size="small" @click="downloadCurrent" :disabled="!activeFile">⬇ 下载代码</el-button>
        <el-button size="small" @click="openAiCreate">✨ AI新建</el-button>
        <el-button size="small" :type="aiPanelVisible ? 'primary' : 'default'" @click="toggleAiPanel" :disabled="!activeFile">✨ AI助手</el-button>
        <el-button size="small" type="primary" @click="runCurrent" :loading="running" :disabled="!activeFile.endsWith('.py')" title="运行当前编辑内容，不会自动覆盖原文件">▶ 运行</el-button>
        <el-button size="small" type="warning" @click="openControlSimulation" :disabled="!activeFile.endsWith('.py')">◉ 运行仿真任务</el-button>
        <el-button size="small" @click="openRunHistory">🧾 运行记录</el-button>
        <el-button size="small" type="success" @click="openPublishDialog" :disabled="!activeFile.endsWith('.py') || activeReadOnly">＋ 保存为新算法</el-button>
      </div>
    </div>

    <div v-if="activeAlgorithmFile && importedAlgorithm" class="algorithm-context">
      <div>
        <strong>算法商店 #{{ importedAlgorithm.algorithm_id }} · {{ importedAlgorithm.algorithm_name }}</strong>
        <el-tag size="small" type="success">
          {{ importedAlgorithm.source_label }}
        </el-tag>
        <span>允许将当前修改覆盖回上传的 Python 源码</span>
      </div>
      <div class="algorithm-context-actions">
        <el-button v-if="importedAlgorithm.save_policy === 'overwrite_source'" size="small" type="danger" :loading="savingAlgorithm" @click="saveToAlgorithmStore">
          覆盖上传源码
        </el-button>
      </div>
    </div>

    <!-- 编辑器区域 -->
    <div class="editor-workbench" v-if="activeFile">
      <div class="editor-area">
        <div ref="editorContainer" class="monaco-container"></div>
      </div>
      <aside v-if="aiPanelVisible" class="ai-panel">
        <div class="ai-panel-header">
          <div><strong>✨ AI编程助手</strong><small>{{ aiProviderLabel }}</small></div>
          <el-button link @click="toggleAiPanel">×</el-button>
        </div>
        <div class="ai-privacy">只发送当前文件、选中代码和你主动提交的错误日志，不读取数据集内容；AI不会直接保存文件。</div>
        <div class="ai-context">
          <el-tag size="small" :type="aiSelectedCode ? 'success' : 'info'">{{ aiSelectedCode ? `已选中 ${aiSelectedCode.length} 字符` : '当前完整文件' }}</el-tag>
          <span>{{ activeFile }}</span>
        </div>
        <div class="ai-quick-actions">
          <el-button size="small" :loading="aiBusy" @click="requestAi('explain')">解释{{ aiSelectedCode ? '选中代码' : '文件' }}</el-button>
          <el-button size="small" type="warning" :loading="aiBusy" :disabled="!canFixLastError" @click="requestAi('fix')">修复运行错误</el-button>
        </div>
        <div ref="aiMessagesRef" class="ai-messages">
          <div v-if="!aiMessages.length" class="ai-empty">
            可以询问代码逻辑，也可以描述修改要求。AI生成的修改会先展示差异，由你确认后再放入编辑器。
          </div>
          <div v-for="(item,index) in aiMessages" :key="index" :class="['ai-message', item.role]">
            <div class="ai-message-role">{{ item.role === 'user' ? '你' : 'AI助手' }}</div>
            <div class="ai-message-content">{{ item.content }}</div>
          </div>
          <div v-if="aiBusy" class="ai-thinking">正在分析当前代码…</div>
        </div>
        <div class="ai-compose">
          <el-input v-model="aiInstruction" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="例如：把数据读取逻辑改成支持CSV路径参数，并补充错误处理" @keydown.ctrl.enter.prevent="requestAi('modify')" />
          <div class="ai-compose-actions">
            <el-button size="small" :loading="aiBusy" @click="requestAi('ask')">询问</el-button>
            <el-button size="small" type="primary" :loading="aiBusy" :disabled="activeReadOnly" @click="requestAi('modify')">生成修改</el-button>
          </div>
          <small>Ctrl + Enter：生成修改；应用后仍需手动保存或运行。</small>
        </div>
      </aside>
    </div>
    <div v-else class="welcome-area">
      <div class="welcome-card">
        <h2>🧪 个人工作空间</h2>
        <p>编辑、运行和保存代码，也可以从左侧算法商店直接载入算法源码</p>
        <div class="welcome-actions">
          <el-button type="primary" size="large" @click="openCreateProject">＋ 新建代码项目</el-button>
          <el-button size="large" @click="openAiCreate">✨ 根据描述新建脚本</el-button>
          <small>机器学习、数据分析和深度学习仅作为初始代码模板</small>
        </div>
      </div>
    </div>

    <!-- 输出面板 -->
    <div class="output-panel" v-if="output || running">
      <div class="output-header">
        <span>{{ running ? '⏳ 执行中...' : (runStatus==='success' ? '✅ 执行成功' : (runStatus==='stopped' ? '🛑 已停止' : '❌ 执行出错')) }}</span>
        <el-button v-if="running" size="small" type="danger" @click="stopRun">⏹ 停止运行</el-button>
        <el-button size="small" link @click="output='';runStatus=''" v-if="!running">清空</el-button>
      </div>
      <pre class="output-content" :class="{ error: runStatus==='error' }">{{ output }}</pre>
    </div>
  </main>

  <!-- 新建文件对话框 -->
  <el-dialog v-model="showNewFile" title="新建文件" width="400px">
    <el-input v-model="newFileName" placeholder="文件名，如 train.py" @keyup.enter="createFile"/>
    <template #footer><el-button @click="showNewFile=false">取消</el-button><el-button type="primary" @click="createFile">创建</el-button></template>
  </el-dialog>

  <el-dialog v-model="showRunHistory" title="🧾 代码运行记录" width="900px">
    <el-table :data="runHistory" size="small" max-height="400">
      <el-table-column prop="training_name" label="训练名称" min-width="160"><template #default="{row}">{{ row.training_name || row.entry_path || '-' }}</template></el-table-column>
      <el-table-column prop="started_at" label="开始时间" min-width="170" />
      <el-table-column prop="status" label="状态" width="90" />
      <el-table-column label="环境" min-width="140"><template #default="{row}">{{ row.environment?.name || '平台默认' }}</template></el-table-column>
      <el-table-column label="运行输入" min-width="160"><template #default="{row}">{{ runInputSummary(row) }}</template></el-table-column>
      <el-table-column label="输出摘要" min-width="220"><template #default="{row}">{{ (row.output || row.error || '').slice(0, 120) }}</template></el-table-column>
      <el-table-column label="操作" width="100" fixed="right"><template #default="{row}"><el-button link type="primary" @click="viewRunReport(row)">查看结果</el-button></template></el-table-column>
    </el-table>
  </el-dialog>

  <el-dialog v-model="showRunInputs" title="运行配置" width="720px" destroy-on-close>
    <el-form label-width="105px" style="margin-bottom:14px">
      <el-form-item label="训练名称" required>
        <el-input
          v-model="runTrainingName"
          maxlength="128"
          show-word-limit
          placeholder="请输入本次训练名称"
        />
      </el-form-item>
      <el-form-item label="模型分类" v-if="saveToModelRegistry">
        <el-select v-model="runModelFolderId" clearable placeholder="未选择则归入未分类" style="width:100%">
          <el-option label="未分类" :value="null" />
          <el-option v-for="folder in runModelFolders" :key="folder.id" :label="folder.name" :value="folder.id" />
        </el-select>
        <div class="el-upload__tip">仅在保存到模型列表时生效；训练产物会归入所选模型分类。</div>
      </el-form-item>
    </el-form>
    <el-alert
      :title="runInputRequirement.dataset?.required ? '代码缺少可用的数据集' : '确认本次运行配置'"
      :description="runInputRequirement.dataset?.required ? '请选择一次性数据集；文件不会保存到工作区。' : '代码已有可用数据来源；可直接使用脚本默认参数，或为本次运行覆盖超参数。'"
      :type="runInputRequirement.dataset?.required ? 'warning' : 'info'"
      :closable="false"
      show-icon
      style="margin-bottom:16px"
    />
    <div class="input-detection">
      <div><el-tag :type="runInputRequirement.dataset?.required ? 'warning' : 'success'" size="small">数据集</el-tag><span>{{ runInputRequirement.dataset?.reason }}</span></div>
      <div><el-tag type="info" size="small">超参数</el-tag><span>{{ runInputRequirement.parameters?.reason || '可选；留空时使用脚本默认参数' }}</span></div>
    </div>
    <el-form v-if="runInputRequirement.dataset?.required" label-width="105px" style="margin-top:16px">
      <el-form-item label="数据来源" required>
        <el-radio-group v-model="runDatasetSource">
          <el-radio-button value="upload">上传文件</el-radio-button>
          <el-radio-button value="platform">平台数据集</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="runDatasetSource === 'upload'" label="数据集文件" required>
        <el-upload
          drag
          :auto-upload="false"
          :limit="1"
          accept=".csv,.tsv,.txt,.json,.jsonl,.parquet,.xlsx,.xls,.feather"
          :on-change="selectRunDataset"
          :on-remove="() => datasetInputFile = null"
          style="width:100%"
        >
          <div class="el-upload__text">拖入数据集，或<em>点击选择</em></div>
          <template #tip><div class="el-upload__tip">最大100MB；文件仅用于本次运行</div></template>
        </el-upload>
      </el-form-item>
      <el-form-item v-else label="平台数据集" required>
        <el-select
          v-model="selectedRunDatasetId"
          filterable
          clearable
          placeholder="输入数据集ID或名称搜索"
          style="width:100%"
          @change="loadRunDatasetFieldRoles"
        >
          <el-option
            v-for="item in datasets"
            :key="item.id"
            :label="`#${item.id} ${item.name}`"
            :value="item.id"
          >
            <div class="run-dataset-option">
              <span><b>#{{ item.id }}</b> {{ item.name }}</span>
              <small>{{ item.row_count || 0 }} 行</small>
            </div>
          </el-option>
        </el-select>
        <div v-if="!datasets.length" class="el-upload__tip">暂无可用的平台数据集，请先上传文件或进入数据集管理添加数据</div>
        <div v-if="runDatasetDefaultFieldRoles" class="field-roles-run-hint">
          <div class="field-roles-run-title"><span>预测目标</span></div>
          <el-radio-group v-model="runTargetMode" size="small">
            <el-radio-button value="script">使用代码中指定的目标列</el-radio-button>
            <el-radio-button value="dataset">使用数据集推荐目标列</el-radio-button>
            <el-radio-button value="custom">自定义目标列</el-radio-button>
          </el-radio-group>
          <div v-if="runTargetMode === 'script'" class="field-roles-run-summary">
            按当前脚本自身的目标列和特征逻辑运行，平台不会改写代码。
          </div>
          <div v-else-if="runTargetMode === 'dataset'" class="field-roles-run-summary">
            数据集{{ runDatasetFieldRolesSource }}目标列：{{ runDatasetDefaultFieldRoles.target_column || '未识别' }}。
          </div>
          <div v-else class="field-roles-run-form">
            <el-select v-model="runCustomTargetColumn" clearable filterable placeholder="从当前数据集选择预测目标列">
              <el-option v-for="column in runDatasetColumns" :key="`target-${column}`" :label="column" :value="column" />
            </el-select>
          </div>
          <div v-if="runTargetMode !== 'script'" class="el-upload__tip">运行前会再次提示：这会仅在本次运行中覆盖代码的常见目标列配置，若源码的特征、预处理或评估逻辑与新目标不匹配，运行可能报错或结果不可靠。</div>
          <div class="field-roles-run-title" style="margin-top:12px"><span>输入特征（可选）</span></div>
          <el-select
            v-model="runFeatureColumns"
            multiple
            collapse-tags
            collapse-tags-tooltip
            clearable
            filterable
            placeholder="留空按代码默认逻辑；可手动选择"
            style="width:100%"
          >
            <el-option
              v-for="column in runFeatureCandidates"
              :key="`feature-${column}`"
              :label="column"
              :value="column"
            />
          </el-select>
          <div class="el-upload__tip">勾选后仅在本次运行中指定输入特征；平台会传入所选字段，并临时覆盖代码中常见的特征列常量。源码文件不会被修改。</div>
        </div>
      </el-form-item>
    </el-form>
    <el-divider content-position="left">超参数（可选）</el-divider>
    <el-form label-width="105px">
      <el-form-item label="参数来源">
        <el-radio-group v-model="runParameterMode" @change="onRunParameterModeChange">
          <el-radio-button value="default">使用脚本默认值</el-radio-button>
          <el-radio-button value="manual">手动填写</el-radio-button>
          <el-radio-button value="file">上传参数文件</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="runParameterMode === 'default'" label="说明">
        <el-alert title="本次运行不注入参数文件，代码按自身默认值执行" type="info" :closable="false" />
      </el-form-item>
      <el-form-item v-else-if="runParameterMode === 'manual'" label="手动参数" required>
        <el-input v-model="runParameterText" type="textarea" :rows="7" placeholder="支持 JSON，或每行 key=value" />
      </el-form-item>
      <el-form-item v-else label="参数文件" required>
        <div style="width:100%">
          <el-upload
            :auto-upload="false" :limit="1" accept=".json,.txt,.yaml,.yml,.cfg,.conf"
            :on-change="selectRunParameterFile" :on-remove="clearRunParameterFile"
          >
            <el-button>选择参数文件</el-button>
            <template #tip><div class="el-upload__tip">最大1MB；仅用于本次运行</div></template>
          </el-upload>
          <el-input v-if="runParameterText" v-model="runParameterText" type="textarea" :rows="6" readonly style="margin-top:8px" />
        </div>
      </el-form-item>
      <el-alert
        v-if="runParameterMode !== 'default' && !runInputRequirement.parameters?.specified"
        title="当前代码未检测到参数读取入口"
        description="平台仍会注入 ODF_HYPERPARAMS_PATH，但代码需要主动读取该环境变量；否则本次参数不会影响运行结果。"
        type="warning" :closable="false" show-icon
      />
      <el-form-item>
        <div>
          <el-checkbox v-model="saveToModelRegistry">保存到模型列表（训练完成后注册到平台模型库）</el-checkbox>
          <div class="el-upload__tip">代码需将带 predict 方法的对象保存到环境变量 ODF_MODEL_OUT_PATH；平台会验证制品后再入库。</div>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="showRunInputs=false">取消</el-button>
      <el-button type="primary" :loading="running" :disabled="!runInputsReady" @click="runWithInputs">开始运行</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="showControlSimulation" title="运行仿真任务" width="720px" destroy-on-close>
    <el-alert title="将使用当前编辑器中的代码快照运行；无需先保存回算法商店。结果会登记到“控制仿真任务”。" type="info" :closable="false" show-icon style="margin-bottom:16px" />
    <el-form label-width="110px">
      <el-form-item label="仿真名称" required><el-input v-model="controlSimulation.name" maxlength="128" /></el-form-item>
      <el-form-item label="平台数据集" required>
        <el-select v-model="controlSimulation.dataset_id" filterable placeholder="输入数据集ID或名称搜索" style="width:100%" @change="loadControlDatasetColumns">
          <el-option v-for="item in datasets" :key="item.id" :label="`#${item.id} ${item.name}`" :value="item.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="时间列"><el-select v-model="controlSimulation.time_column" clearable filterable placeholder="建议选择时间列" style="width:100%"><el-option v-for="column in controlDatasetColumns" :key="column" :label="column" :value="column" /></el-select></el-form-item>
      <el-form-item label="被控变量/状态"><el-select v-model="controlSimulation.state_columns" multiple filterable style="width:100%"><el-option v-for="column in controlDatasetColumns" :key="column" :label="column" :value="column" /></el-select></el-form-item>
      <el-form-item label="控制变量"><el-select v-model="controlSimulation.control_columns" multiple filterable style="width:100%"><el-option v-for="column in controlDatasetColumns" :key="column" :label="column" :value="column" /></el-select></el-form-item>
      <el-form-item label="扰动变量"><el-select v-model="controlSimulation.disturbance_columns" multiple filterable style="width:100%"><el-option v-for="column in controlDatasetColumns" :key="column" :label="column" :value="column" /></el-select></el-form-item>
      <el-form-item label="设定值"><el-input-number v-model="controlSimulation.setpoint" :precision="4" :step="0.1" /><span class="el-upload__tip" style="margin-left:10px">可选；脚本需主动读取控制配置才会使用。</span></el-form-item>
    </el-form>
    <template #footer><el-button @click="showControlSimulation=false">取消</el-button><el-button type="primary" :loading="controlSimulationRunning" :disabled="!controlSimulation.name.trim() || !controlSimulation.dataset_id" @click="runControlSimulation">开始仿真</el-button></template>
  </el-dialog>

  <el-dialog v-model="showCreateProject" title="新建代码项目" width="500px" destroy-on-close>
    <el-form label-width="90px">
      <el-form-item label="项目名称" required>
        <el-input v-model="projectForm.name" maxlength="64" placeholder="例如：temperature_analysis" @keyup.enter="createProject" />
      </el-form-item>
      <el-form-item label="初始模板">
        <el-select v-model="projectForm.template" style="width:100%">
          <el-option v-for="item in projectTemplateOptions" :key="item.key" :label="item.label" :value="item.key">
            <div class="template-option"><span>{{ item.label }}</span><small>{{ item.desc }}</small></div>
          </el-option>
        </el-select>
      </el-form-item>
      <el-alert
        v-if="projectForm.template === 'deep_learning'"
        title="该选项只生成 PyTorch 示例代码，不会自动配置 GPU、PyTorch 或其他依赖"
        type="warning"
        :closable="false"
      />
    </el-form>
    <template #footer>
      <el-button @click="showCreateProject=false">取消</el-button>
      <el-button type="primary" :loading="creatingProject" @click="createProject">创建项目</el-button>
    </template>
  </el-dialog>

  <!-- 新建目录对话框 -->
  <el-dialog v-model="showNewDir" title="新建目录" width="400px">
    <el-input v-model="newDirName" placeholder="目录名" @keyup.enter="createDir"/>
    <template #footer><el-button @click="showNewDir=false">取消</el-button><el-button type="primary" @click="createDir">创建</el-button></template>
  </el-dialog>

  <!-- 保存为新算法对话框 -->
  <el-dialog v-model="showPublish" title="保存为新算法" width="620px" destroy-on-close>
    <el-alert title="当前 Python 文件将作为新算法源码保存到算法商店，不会覆盖已有算法" type="info" :closable="false" style="margin-bottom:16px" />
    <el-form :model="publishForm" label-width="105px">
      <el-form-item label="算法名称" required><el-input v-model="publishForm.name" maxlength="200" placeholder="例如：生产过程终点温度 MLP"/></el-form-item>
      <el-form-item label="任务类型" required>
        <el-select v-model="publishForm.task_type" style="width:100%">
          <el-option label="分类算法" value="classification" />
          <el-option label="回归算法" value="regression" />
          <el-option label="时序预测" value="time_series_forecasting" />
        </el-select>
      </el-form-item>
      <el-form-item label="算法实现" required>
        <el-select v-model="publishForm.algorithm_type" style="width:100%">
          <el-option label="Random Forest" value="random_forest" />
          <el-option label="XGBoost" value="xgboost" />
          <el-option label="LightGBM" value="lightgbm" />
          <el-option label="MLP 神经网络" value="mlp" />
        </el-select>
      </el-form-item>
      <el-form-item label="框架" required>
        <el-select v-model="publishForm.framework" style="width:100%">
          <el-option label="PyTorch" value="pytorch" />
          <el-option label="TensorFlow" value="tensorflow" />
          <el-option label="Scikit-learn" value="sklearn" />
          <el-option label="XGBoost" value="xgboost" />
          <el-option label="LightGBM" value="lightgbm" />
        </el-select>
      </el-form-item>
      <el-form-item label="训练适配器">
        <el-alert type="success" :closable="false" :title="`${publishAlgorithmLabel} · ${publishTaskLabel}`" description="系统会根据任务类型和算法实现自动配置；当前运行环境会一并关联。" />
      </el-form-item>
      <el-form-item label="版本"><el-input v-model="publishForm.version" placeholder="1.0.0" /></el-form-item>
      <el-form-item label="描述"><el-input v-model="publishForm.description" type="textarea" :rows="2" placeholder="说明算法用途、输入和输出"/></el-form-item>
      <el-form-item label="标签"><el-select v-model="publishForm.tags" multiple filterable allow-create style="width:100%" placeholder="输入后回车添加标签"/></el-form-item>
    </el-form>
    <template #footer><el-button @click="showPublish=false">取消</el-button><el-button type="primary" @click="doPublish" :loading="publishing">保存到算法商店</el-button></template>
  </el-dialog>

  <el-dialog v-model="showAiCreate" title="AI新建脚本" width="620px" destroy-on-close>
    <el-alert title="AI只负责生成建议，确认预览后才会创建文件；同名文件不会被覆盖。" type="info" :closable="false" style="margin-bottom:16px" />
    <el-form label-width="100px">
      <el-form-item label="脚本需求" required>
        <el-input v-model="aiCreateForm.requirement" type="textarea" :rows="6" maxlength="2000" show-word-limit placeholder="例如：创建一个读取CSV文件并输出每列缺失率的Python脚本，数据路径从命令行参数传入" />
      </el-form-item>
      <el-form-item label="脚本语言" required>
        <el-select v-model="aiCreateForm.language" style="width:100%">
          <el-option label="Python" value="python" />
          <el-option label="JavaScript" value="javascript" />
          <el-option label="TypeScript" value="typescript" />
          <el-option label="SQL" value="sql" />
          <el-option label="Shell" value="shell" />
        </el-select>
      </el-form-item>
      <el-form-item label="期望文件名">
        <el-input v-model="aiCreateForm.filename" maxlength="120" placeholder="可留空，由AI建议，例如 analyze_data.py" />
      </el-form-item>
      <el-form-item label="保存目录">
        <el-input v-model="aiCreateForm.directory" maxlength="240" placeholder="留空保存到工作区根目录，例如 experiments" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="showAiCreate=false">取消</el-button>
      <el-button type="primary" :loading="aiCreateBusy" @click="generateAiScript">生成并预览</el-button>
    </template>
  </el-dialog>

  <el-dialog
    v-model="showAiDiff" :title="aiProposalMode === 'create' ? 'AI新脚本预览' : 'AI代码修改预览'" width="92%" top="4vh"
    :close-on-click-modal="false" append-to-body @opened="renderAiDiff" @closed="disposeAiDiff"
  >
    <el-alert :title="aiDiffSummary || 'AI已生成修改建议'" type="info" :closable="false" show-icon style="margin-bottom:12px" />
    <el-alert v-if="aiProposalMode === 'edit' && activeFile !== aiTargetFile" title="当前活动文件已经变化，请切回原文件后再应用" type="warning" :closable="false" style="margin-bottom:12px" />
    <div v-if="aiProposalMode === 'create'" class="ai-create-target">将创建：<strong>{{ aiTargetFile }}</strong></div>
    <div ref="aiDiffContainer" class="ai-diff-container"></div>
    <template #footer>
      <el-button @click="showAiDiff=false">取消</el-button>
      <el-button type="primary" :loading="aiApplying" :disabled="!canApplyAiProposal" @click="applyAiProposal">{{ aiProposalMode === 'create' ? '确认创建文件' : '应用到编辑器（不自动保存）' }}</el-button>
    </template>
  </el-dialog>

  <!-- 右键菜单 -->
  <div v-if="contextVisible" class="context-menu" :style="{left:contextX+'px',top:contextY+'px'}">
    <div class="ctx-item" @click="showNewFile=true;contextVisible=false">📄 新建文件</div>
    <div class="ctx-item" @click="showNewDir=true;contextVisible=false">📁 新建目录</div>
    <div class="ctx-item" @click="deleteItem(contextTarget!);contextVisible=false">🗑 删除</div>
  </div>
</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { get, post, put } from '@/api/legacy'
import { parseHyperparameterText, readHyperparameterFile, type HyperparameterMode } from '@/utils/hyperparameters'

declare const monaco: any

const route = useRoute()
const router = useRouter()

interface FileItem { name: string; path: string; type: string; size: number; modified: string; depth?: number; expanded?: boolean }
interface TabItem { name: string; path: string; type: string }
interface DsItem { id: number; name: string; description: string; row_count: number }
interface AlgorithmItem {
  id: number; name: string; description: string; source_label: string; source_mode: string
  has_original_source: boolean; dependency_id: number | null; artifact_kind?: string; artifact_format?: string
  asset_type: 'uploaded_source'
  asset_type_label: string; sandbox_policy: string; downloadable: boolean
}
interface ImportedAlgorithm {
  algorithm_id: number; algorithm_name: string; project_path: string; entry: string
  source_mode: string; source_label: string; has_original_source: boolean; revision: string | null
  dependency_id?: number | null
  asset_type: 'uploaded_source'
  save_policy: 'overwrite_source'
  read_only_files: string[]; parameter_file?: string | null; download_artifact: boolean
}
interface CurrentProject {
  name: string; path: string; algorithm_id?: number
}

// State
const fileTree = ref<(FileItem & {depth:number;expanded:boolean})[]>([])
const fileLoading = ref(false)
const activeFile = ref('')
const openTabs = ref<TabItem[]>([])
const fileContents = ref<Record<string,string>>({})
const savedContents = ref<Record<string,string>>({})
const output = ref('')
const runStatus = ref('')
const running = ref(false)
const currentRunId = ref('')
const saveToModelRegistry = ref(false)
const runReport = ref<any>(null)
const runMetricChartRef = ref<HTMLElement | null>(null)
const runSeriesChartRef = ref<HTMLElement | null>(null)
const runTrendChartRef = ref<HTMLElement | null>(null)
const runScatterChartRef = ref<HTMLElement | null>(null)
const runResidualChartRef = ref<HTMLElement | null>(null)
const runImportanceChartRef = ref<HTMLElement | null>(null)
const runConfusionChartRef = ref<HTMLElement | null>(null)
const runClassDistributionChartRef = ref<HTMLElement | null>(null)
let runMetricChart: echarts.ECharts | null = null
let runSeriesChart: echarts.ECharts | null = null
let predictionCharts: echarts.ECharts[] = []
const saving = ref(false)
const publishing = ref(false)
const datasets = ref<DsItem[]>([])
const algorithms = ref<AlgorithmItem[]>([])
const algorithmSearch = ref('')
const algorithmsLoading = ref(false)
const importedAlgorithms = ref<ImportedAlgorithm[]>([])
const currentProject = ref<CurrentProject | null>(null)
const savingAlgorithm = ref(false)
const environments = ref<any[]>([])
const selectedEnvironmentId = ref<number | null>(null)
const showRunHistory = ref(false)
const runHistory = ref<any[]>([])
const showRunInputs = ref(false)
const runInputRequirement = ref<any>({ dataset: {} })
const datasetInputFile = ref<File | null>(null)
const runDatasetSource = ref<'upload' | 'platform'>('upload')
const selectedRunDatasetId = ref<number | null>(null)
const runDatasetDefaultFieldRoles = ref<any>(null)
const runDatasetManualFieldRoles = ref<any>({ time_column: null, id_columns: [], feature_columns: [], target_column: null })
const runDatasetFieldRolesSource = ref('系统推荐')
const runDatasetFieldRoleMode = ref<'default' | 'manual'>('default')
const runDatasetColumns = ref<string[]>([])
const runParameterMode = ref<HyperparameterMode>('default')
const runParameterText = ref('')
const runParameterFile = ref<File | null>(null)
const runTargetMode = ref<'script' | 'dataset' | 'custom'>('script')
const runCustomTargetColumn = ref<string | null>(null)
const runFeatureColumns = ref<string[]>([])
const runTrainingName = ref('')
const runModelFolders = ref<any[]>([])
const runModelFolderId = ref<number | null>(null)
const showControlSimulation = ref(false)
const controlSimulationRunning = ref(false)
const controlDatasetColumns = ref<string[]>([])
const controlSimulation = ref({ name: '', dataset_id: null as number | null, time_column: '', state_columns: [] as string[], control_columns: [] as string[], disturbance_columns: [] as string[], setpoint: undefined as number | undefined })
const showCreateProject = ref(false)
const creatingProject = ref(false)
const projectForm = ref({ name: '', template: 'blank' })

type AiAction = 'explain' | 'ask' | 'modify' | 'fix'
interface AiMessage { role: 'user' | 'assistant'; content: string }
const aiPanelVisible = ref(true)
const aiBusy = ref(false)
const aiInstruction = ref('')
const aiMessages = ref<AiMessage[]>([])
const aiSelectedCode = ref('')
const aiMessagesRef = ref<HTMLElement | null>(null)
const aiProvider = ref('')
const showAiDiff = ref(false)
const showAiCreate = ref(false)
const aiCreateBusy = ref(false)
const aiApplying = ref(false)
const aiProposalMode = ref<'edit' | 'create'>('edit')
const aiCreateForm = ref({ requirement:'', language:'python', filename:'', directory:'' })
const aiDiffContainer = ref<HTMLElement | null>(null)
const aiOriginalCode = ref('')
const aiProposedCode = ref('')
const aiDiffSummary = ref('')
const aiTargetFile = ref('')
const lastRunFile = ref('')
let aiDiffEditor: any = null
let aiOriginalModel: any = null
let aiModifiedModel: any = null

const showNewFile = ref(false); const newFileName = ref('')
const showNewDir = ref(false); const newDirName = ref('')
const showPublish = ref(false)
const publishForm = ref({
  name: '', description: '', task_type: 'regression', algorithm_type: 'mlp',
  framework: 'pytorch', version: '1.0.0', tags: [] as string[], file_path: ''
})

const contextVisible = ref(false); const contextX = ref(0); const contextY = ref(0)
const contextTarget = ref<FileItem | null>(null)

const editorContainer = ref<HTMLElement | null>(null)
let editor: any = null

const activeDirty = computed(() => activeFile.value ? isDirty(activeFile.value) : false)
const aiProviderLabel = computed(() => aiProvider.value ? `由 ${aiProvider.value} 提供` : '平台大模型')
const canFixLastError = computed(() => Boolean(
  activeFile.value && lastRunFile.value === activeFile.value && runStatus.value === 'error' && output.value.trim()
))
const canApplyAiProposal = computed(() => aiProposalMode.value === 'create'
  ? Boolean(aiTargetFile.value && aiProposedCode.value)
  : Boolean(activeFile.value === aiTargetFile.value && !activeReadOnly.value && aiProposedCode.value)
)
const importedAlgorithm = computed(() => importedAlgorithms.value.find(
  item => activeFile.value === item.project_path || activeFile.value.startsWith(item.project_path + '/')
) || null)
const activeAlgorithmFile = computed(() => {
  const item = importedAlgorithm.value
  if (!item || !activeFile.value.startsWith(item.project_path + '/')) return false
  const relative = activeFile.value.slice(item.project_path.length + 1)
  return !relative.startsWith('artifacts/')
})
const activeReadOnly = computed(() => {
  const item = importedAlgorithm.value
  if (!item || !activeFile.value.startsWith(item.project_path + '/')) return false
  const relative = activeFile.value.slice(item.project_path.length + 1)
  return relative.startsWith('artifacts/') || (item.read_only_files || []).includes(relative)
})
const workspaceSaveLabel = computed(() => {
  return '覆盖保存'
})
const showRunReport = computed(() => Boolean(runReport.value))
const runReportStatusLabel = computed(() => ({
  success: '运行成功', error: '运行失败', timeout: '运行超时'
} as Record<string,string>)[runReport.value?.status] || '状态未知')
const runReportStatusType = computed(() => ({
  success: 'success', error: 'danger', timeout: 'warning'
} as Record<string,string>)[runReport.value?.status] || 'info')
const metricLabels: Record<string,string> = {
  accuracy: '准确率', precision: '精确率', recall: '召回率', f1: 'F1',
  r2: 'R²', rmse: 'RMSE', mae: 'MAE', mse: 'MSE',
  loss: '训练损失', val_loss: '验证损失',
}
const runMetricRows = computed(() => Object.entries(runReport.value?.analysis?.metrics || {})
  .filter(([, value]) => typeof value === 'number' && Number.isFinite(value))
  .map(([name, value]) => ({ name, label: metricLabels[name] || name, value: value as number })))
const runSeriesRows = computed<any[]>(() => Array.isArray(runReport.value?.analysis?.series)
  ? runReport.value.analysis.series : [])
const runVisualization = computed<any>(() => runReport.value?.analysis?.visualization || {})
const hasPredictionVisualization = computed(() => Boolean(
  runVisualization.value.prediction_trend || runVisualization.value.confusion_matrix
))
const outputLineCount = computed(() => (runReport.value?.output || '').split(/\r?\n/).filter(Boolean).length)
const runInputsReady = computed(() => {
  const datasetReady = !runInputRequirement.value.dataset?.required
    || (runDatasetSource.value === 'upload' ? !!datasetInputFile.value : selectedRunDatasetId.value !== null)
  const parameterReady = runParameterMode.value === 'default'
    || (runParameterMode.value === 'manual' ? !!runParameterText.value.trim() : !!runParameterFile.value)
  return !!runTrainingName.value.trim() && datasetReady && parameterReady
})
const activeRunDatasetFieldRoles = computed(() => {
  const roles = runDatasetDefaultFieldRoles.value
  if (!roles) return null
  const targetColumn = runTargetMode.value === 'script'
    ? null
    : (runTargetMode.value === 'custom' ? runCustomTargetColumn.value : roles.target_column)
  return {
    ...roles,
    feature_columns: [...runFeatureColumns.value],
    target_column: targetColumn || null,
    apply_target_override: runTargetMode.value !== 'script',
    apply_feature_override: runFeatureColumns.value.length > 0,
  }
})
const runFeatureCandidates = computed(() => {
  const configuredTarget = runTargetMode.value === 'custom'
    ? runCustomTargetColumn.value
    : (runTargetMode.value === 'dataset' ? runDatasetDefaultFieldRoles.value?.target_column : null)
  return runDatasetColumns.value.filter(column => column !== configuredTarget)
})
const publishTaskLabel = computed(() => ({
  classification: '分类', regression: '回归', time_series_forecasting: '时序预测'
} as Record<string,string>)[publishForm.value.task_type] || publishForm.value.task_type)
const publishAlgorithmLabel = computed(() => ({
  random_forest: 'Random Forest', xgboost: 'XGBoost', lightgbm: 'LightGBM', mlp: 'MLP 神经网络'
} as Record<string,string>)[publishForm.value.algorithm_type] || publishForm.value.algorithm_type)

const projectTemplateOptions = [
  { key: 'blank', label: '空白项目', desc: '创建一个空白 main.py' },
  { key: 'ml', label: '机器学习模板', desc: 'RandomForest 训练示例' },
  { key: 'data_analysis', label: '数据分析模板', desc: 'Pandas 与 Matplotlib 示例' },
  { key: 'deep_learning', label: '深度学习模板', desc: 'PyTorch 神经网络示例' },
]

// --- File Tree ---
// The sidebar is intentionally scoped to one project.  A personal workspace may
// retain earlier imports, but they must never accumulate in the current file view.
async function loadFileTree(path: string, depth = 0) {
  const r = await get(`/workspace/files?path=${encodeURIComponent(path)}`)
  return (r.files || []).map((f: any) => ({ ...f, depth, expanded: false }))
}

function projectHasUnsavedChanges(projectPath: string) {
  return openTabs.value.some(tab => tab.path.startsWith(projectPath + '/') && isDirty(tab.path))
}

async function selectProject(project: CurrentProject) {
  const previous = currentProject.value
  if (previous?.path === project.path) return
  if (previous && projectHasUnsavedChanges(previous.path)) {
    await ElMessageBox.confirm(
      `“${previous.name}”有未保存修改。切换项目会关闭当前编辑标签，未保存内容不会保留，是否继续？`,
      '切换当前项目', { type: 'warning', confirmButtonText: '继续切换', cancelButtonText: '留在当前项目' }
    )
  }
  currentProject.value = project
  openTabs.value = []
  activeFile.value = ''
  aiSelectedCode.value = ''
  aiPanelVisible.value = false
  await refreshTree()
}

async function leaveCurrentProject() {
  const project = currentProject.value
  if (!project) return
  try {
    if (projectHasUnsavedChanges(project.path)) {
      await ElMessageBox.confirm(
        `“${project.name}”有未保存修改。关闭当前项目视图前请先保存，是否仍要关闭？`,
        '关闭当前项目', { type: 'warning', confirmButtonText: '仍要关闭', cancelButtonText: '取消' }
      )
    }
    currentProject.value = null
    importedAlgorithms.value = []
    openTabs.value = []
    activeFile.value = ''
    fileTree.value = []
  } catch (error) {
    if (error !== 'cancel') ElMessage.error('切换项目失败')
  }
}

async function refreshTree() {
  const project = currentProject.value
  if (!project) {
    fileTree.value = []
    return
  }
  fileLoading.value = true
  try {
    fileTree.value = await loadFileTree(project.path, 0)
  } catch (error: any) {
    fileTree.value = []
    ElMessage.error(error?.message || '当前项目文件加载失败')
  } finally {
    fileLoading.value = false
  }
}

async function toggleDir(dir: FileItem & {depth:number;expanded:boolean}) {
  const idx = fileTree.value.indexOf(dir)
  if (idx < 0) return
  if (dir.expanded) {
    let endIdx = idx + 1
    while (endIdx < fileTree.value.length && fileTree.value[endIdx].depth! > dir.depth!) endIdx++
    fileTree.value.splice(idx + 1, endIdx - idx - 1)
    dir.expanded = false
    return
  }
  fileLoading.value = true
  try {
    const children = await loadFileTree(dir.path, dir.depth + 1)
    dir.expanded = true
    fileTree.value.splice(idx + 1, 0, ...children)
  } catch (error: any) {
    ElMessage.error(error?.message || '文件夹加载失败')
  } finally {
    fileLoading.value = false
  }
}

async function openFile(f: FileItem) {
  if (f.type === 'dir') { toggleDir(f as any); return }
  activeFile.value = f.path
  aiSelectedCode.value = ''
  
  // Add to tabs
  if (!openTabs.value.find(t => t.path === f.path)) {
    openTabs.value.push({ name: f.name, path: f.path, type: f.type })
  }
  
  // Load content if not cached
  if (!Object.prototype.hasOwnProperty.call(fileContents.value, f.path)) {
    try {
      const r = await get(`/workspace/files/read?path=${encodeURIComponent(f.path)}`)
      fileContents.value[f.path] = r.content || ''
      savedContents.value[f.path] = r.content || ''
    } catch(e) {
      fileContents.value[f.path] = '# 无法读取文件'
      savedContents.value[f.path] = fileContents.value[f.path]
    }
  }
  
  // Set editor content
  await nextTick()
  if (editor) {
    editor.updateOptions({ readOnly: activeReadOnly.value })
    editor.setValue(fileContents.value[f.path] || '')
    // Set language based on extension
    const ext = f.name.split('.').pop() || ''
    const langMap: Record<string,string> = { py: 'python', js: 'javascript', ts: 'typescript', json: 'json', yaml: 'yaml', yml: 'yaml', md: 'markdown', html: 'html', css: 'css', sql: 'sql', sh: 'shell', txt: 'plaintext' }
    const lang = langMap[ext] || 'plaintext'
    monaco.editor.setModelLanguage(editor.getModel(), lang)
  }
  
  publishForm.value.file_path = f.path
}

function closeTab(tab: TabItem) {
  const idx = openTabs.value.indexOf(tab)
  openTabs.value.splice(idx, 1)
  if (activeFile.value === tab.path) {
    activeFile.value = openTabs.value[Math.min(idx, openTabs.value.length-1)]?.path || ''
    if (activeFile.value && editor) {
      editor.updateOptions({ readOnly: activeReadOnly.value })
      editor.setValue(fileContents.value[activeFile.value] || '')
    }
  }
}

// --- Monaco Editor ---
function initMonaco() {
  if (!editorContainer.value || typeof monaco === 'undefined') {
    // Monaco not loaded yet, retry
    setTimeout(initMonaco, 500)
    return
  }
  editor = monaco.editor.create(editorContainer.value, {
    value: activeFile.value ? (fileContents.value[activeFile.value] || '') : '',
    language: 'python',
    theme: 'vs-dark',
    fontSize: 14,
    lineNumbers: 'on',
    minimap: { enabled: true },
    automaticLayout: true,
    scrollBeyondLastLine: false,
    tabSize: 4,
    readOnly: activeReadOnly.value,
  })
  
  // Keep edits in browser memory. Persistence only happens after an explicit save action.
  editor.onDidChangeModelContent(() => {
    if (activeFile.value) {
      fileContents.value[activeFile.value] = editor.getValue()
    }
  })
  editor.onDidChangeCursorSelection(() => {
    const selection = editor.getSelection()
    aiSelectedCode.value = selection && !selection.isEmpty()
      ? editor.getModel().getValueInRange(selection)
      : ''
  })
}

function toggleAiPanel() {
  aiPanelVisible.value = !aiPanelVisible.value
  nextTick(() => editor?.layout())
}

function languageForFile(path: string) {
  const extension = path.split('.').pop()?.toLowerCase() || ''
  return ({ py:'python', js:'javascript', ts:'typescript', json:'json', sql:'sql', sh:'shell', html:'html', css:'css', md:'markdown' } as Record<string,string>)[extension] || 'plaintext'
}

function aiActionLabel(action: AiAction) {
  if (action === 'explain') return `解释${aiSelectedCode.value ? '选中代码' : '当前文件'}`
  if (action === 'fix') return '根据最近一次运行错误修复代码'
  return aiInstruction.value.trim()
}

async function scrollAiMessages() {
  await nextTick()
  if (aiMessagesRef.value) aiMessagesRef.value.scrollTop = aiMessagesRef.value.scrollHeight
}

async function requestAi(action: AiAction) {
  if (!activeFile.value || !editor) return ElMessage.warning('请先打开代码文件')
  if (aiBusy.value) return
  const instruction = aiInstruction.value.trim()
  if ((action === 'ask' || action === 'modify') && !instruction) return ElMessage.warning('请先输入问题或修改要求')
  if (action === 'fix' && !canFixLastError.value) return ElMessage.warning('当前文件没有可用于修复的运行错误')
  const requestFile = activeFile.value
  const code = editor.getValue()
  fileContents.value[requestFile] = code
  aiMessages.value.push({ role:'user', content:aiActionLabel(action) })
  aiBusy.value = true
  await scrollAiMessages()
  try {
    const environment = environments.value.find(item => item.id === selectedEnvironmentId.value)
    const result = await post('/workspace/ai-assistant', {
      action,
      code,
      selected_code: aiSelectedCode.value || null,
      instruction: instruction || null,
      error_log: action === 'fix' ? output.value.slice(-12000) : null,
      language: languageForFile(requestFile),
      file_path: requestFile,
      environment_name: environment?.name || null,
    })
    aiProvider.value = result.provider || ''
    if (result.proposed_code) {
      aiProposalMode.value = 'edit'
      aiOriginalCode.value = code
      aiProposedCode.value = result.proposed_code
      aiDiffSummary.value = result.summary || 'AI已生成代码修改建议'
      aiTargetFile.value = requestFile
      aiMessages.value.push({
        role:'assistant',
        content:`${aiDiffSummary.value}\n已生成修改差异，请确认后应用。${result.validation_retry ? '\n首次生成未通过校验，平台已经自动纠正一次。' : ''}`,
      })
      showAiDiff.value = true
    } else {
      aiMessages.value.push({ role:'assistant', content:result.message || 'AI没有返回内容' })
    }
    if (action === 'ask' || action === 'modify') aiInstruction.value = ''
  } catch (error:any) {
    const message = error?.message || 'AI助手调用失败'
    aiMessages.value.push({ role:'assistant', content:`请求失败：${message}` })
    ElMessage.error(message)
  } finally {
    aiBusy.value = false
    await scrollAiMessages()
  }
}

function openAiCreate() {
  aiCreateForm.value = { requirement:'', language:'python', filename:'', directory:'' }
  showAiCreate.value = true
}

function normalizeNewScriptFilename(value: string, language: string) {
  const extension = ({ python:'.py', javascript:'.js', typescript:'.ts', sql:'.sql', shell:'.sh' } as Record<string,string>)[language] || '.txt'
  const name = value.trim()
  if (!name) return ''
  if (name.includes('/') || name.includes('\\') || name === '.' || name === '..') throw new Error('文件名不能包含目录或路径符号')
  if (!/^[\w.\-\u4e00-\u9fa5]+$/.test(name)) throw new Error('文件名只能包含中文、字母、数字、下划线、连字符和扩展名')
  return name.includes('.') ? name : name + extension
}

function normalizeNewScriptDirectory(value: string) {
  const directory = value.trim().replace(/\\/g, '/').replace(/^\/+|\/+$/g, '')
  if (!directory) return ''
  if (directory.split('/').some(part => !part || part === '.' || part === '..' || !/^[\w\-\u4e00-\u9fa5]+$/.test(part))) {
    throw new Error('保存目录只能使用工作区内的中文、字母、数字、下划线和连字符')
  }
  return directory
}

async function generateAiScript() {
  const requirement = aiCreateForm.value.requirement.trim()
  if (!requirement) return ElMessage.warning('请描述需要创建的脚本')
  let requestedFilename = ''
  let directory = ''
  try {
    requestedFilename = normalizeNewScriptFilename(aiCreateForm.value.filename, aiCreateForm.value.language)
    directory = normalizeNewScriptDirectory(aiCreateForm.value.directory)
  } catch (error:any) {
    return ElMessage.warning(error.message)
  }
  aiCreateBusy.value = true
  try {
    const result = await post('/workspace/ai-assistant', {
      action:'create', code:'', selected_code:null, instruction:requirement,
      error_log:null, language:aiCreateForm.value.language,
      file_path:requestedFilename, environment_name:null,
    })
    const generatedFilename = normalizeNewScriptFilename(
      requestedFilename || result.suggested_filename || 'ai_generated_script',
      aiCreateForm.value.language,
    )
    aiProposalMode.value = 'create'
    aiOriginalCode.value = ''
    aiProposedCode.value = result.proposed_code || ''
    aiDiffSummary.value = result.summary || 'AI已生成新脚本'
    if (result.validation_retry) aiDiffSummary.value += '（首次生成未通过校验，平台已自动纠正）'
    aiTargetFile.value = directory ? `${directory}/${generatedFilename}` : generatedFilename
    aiProvider.value = result.provider || ''
    showAiCreate.value = false
    showAiDiff.value = true
  } catch (error:any) {
    ElMessage.error(error?.message || 'AI生成脚本失败')
  } finally {
    aiCreateBusy.value = false
  }
}

function disposeAiDiff() {
  aiDiffEditor?.dispose()
  aiOriginalModel?.dispose()
  aiModifiedModel?.dispose()
  aiDiffEditor = null
  aiOriginalModel = null
  aiModifiedModel = null
}

function renderAiDiff() {
  disposeAiDiff()
  if (!aiDiffContainer.value || typeof monaco === 'undefined') return
  const language = languageForFile(aiTargetFile.value)
  aiOriginalModel = monaco.editor.createModel(aiOriginalCode.value, language)
  aiModifiedModel = monaco.editor.createModel(aiProposedCode.value, language)
  aiDiffEditor = monaco.editor.createDiffEditor(aiDiffContainer.value, {
    theme:'vs-dark', readOnly:true, originalEditable:false, automaticLayout:true,
    renderSideBySide:true, minimap:{enabled:false}, fontSize:13,
  })
  aiDiffEditor.setModel({ original:aiOriginalModel, modified:aiModifiedModel })
}

async function applyAiProposal() {
  if (aiProposalMode.value === 'create') {
    aiApplying.value = true
    try {
      await post('/workspace/files/create', { path:aiTargetFile.value, content:aiProposedCode.value })
      const createdPath = aiTargetFile.value
      showAiDiff.value = false
      await refreshTree()
      await openFile({ name:createdPath.split('/').pop() || createdPath, path:createdPath, type:'file', size:aiProposedCode.value.length, modified:'' })
      aiPanelVisible.value = true
      aiMessages.value.push({ role:'assistant', content:`已根据你的确认创建 ${createdPath}。可以直接运行或继续让AI修改。` })
      ElMessage.success(`脚本 ${createdPath} 已创建`)
      return
    } catch (error:any) {
      ElMessage.error(error?.message || '创建脚本失败')
      return
    } finally {
      aiApplying.value = false
    }
  }
  if (activeFile.value !== aiTargetFile.value) return ElMessage.warning('请先切回AI修改对应的文件')
  if (activeReadOnly.value) return ElMessage.warning('当前文件只读，不能应用修改')
  editor.setValue(aiProposedCode.value)
  fileContents.value[activeFile.value] = aiProposedCode.value
  aiSelectedCode.value = ''
  showAiDiff.value = false
  aiMessages.value.push({ role:'assistant', content:'修改内容已写入编辑器并标记为未保存。请先运行验证，再决定是否覆盖保存。' })
  ElMessage.success('AI修改已应用到编辑器，尚未保存')
  scrollAiMessages()
}

// --- File Operations ---
function currentTargetPath(name: string) {
  const target = contextTarget.value
  if (!target) return currentProject.value ? `${currentProject.value.path}/${name}` : name
  const parent = target.type === 'dir' ? target.path : target.path.split('/').slice(0, -1).join('/')
  return parent ? parent + '/' + name : name
}
function authHeaders() {
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
  return token ? { Authorization: 'Bearer ' + token } : {}
}
async function createFile() {
  if (!currentProject.value) return ElMessage.warning('请先选择算法或新建代码项目')
  if (!newFileName.value) return
  try {
    await post('/workspace/files/save', { path: currentTargetPath(newFileName.value), content: '' })
    showNewFile.value = false
    newFileName.value = ''
    await refreshTree()
    ElMessage.success('文件已创建')
  } catch(e: any) { ElMessage.error(e?.message || '创建失败') }
}

async function createDir() {
  if (!currentProject.value) return ElMessage.warning('请先选择算法或新建代码项目')
  if (!newDirName.value) return
  try {
    await post(`/workspace/files/mkdir?path=${encodeURIComponent(currentTargetPath(newDirName.value))}`)
    showNewDir.value = false
    newDirName.value = ''
    await refreshTree()
    ElMessage.success('目录已创建')
  } catch(e: any) { ElMessage.error(e?.message || '创建失败') }
}

async function handleUpload(opts: any) {
  if (!currentProject.value) return ElMessage.warning('请先选择算法或新建代码项目')
  try {
    const formData = new FormData()
    formData.append('file', opts.file)
    formData.append('dest_path', currentProject.value.path)
    const response = await fetch('/api/v1/workspace/files/upload', { method: 'POST', headers: authHeaders(), body: formData })
    if (!response.ok) throw new Error('HTTP ' + response.status)
    await refreshTree()
    ElMessage.success('上传成功')
  } catch(e: any) { ElMessage.error('上传失败') }
}

async function deleteItem(f: FileItem | null) {
  if (!f) return
  try {
    await ElMessageBox.confirm(`确定删除 ${f.name}？`, '确认删除', { type: 'warning' })
    const method = 'DELETE'
    const response = await fetch(`/api/v1/workspace/files/delete?path=${encodeURIComponent(f.path)}`, { method, headers: authHeaders() })
    if (!response.ok) throw new Error('HTTP ' + response.status)
    if (activeFile.value === f.path) { closeTab({ name: f.name, path: f.path, type: f.type } as TabItem) }
    await refreshTree()
    ElMessage.success('已删除')
  } catch(e: any) { if (e !== 'cancel') ElMessage.error('删除失败') }
}

function renameItem(f: FileItem) {
  ElMessage.info('重命名功能开发中...')
}

async function saveAll() {
  saving.value = true
  try {
    for (const tab of openTabs.value) {
      if (tab.path === activeFile.value && editor) {
        fileContents.value[tab.path] = editor.getValue()
      }
      if (fileContents.value[tab.path] !== undefined) {
        await post('/workspace/files/save', { path: tab.path, content: fileContents.value[tab.path] || '' })
        savedContents.value[tab.path] = fileContents.value[tab.path] || ''
      }
    }
    ElMessage.success('全部保存完成')
  } catch(e: any) { ElMessage.error('保存失败') }
  finally { saving.value = false }
}

function isDirty(path: string) {
  return Object.prototype.hasOwnProperty.call(fileContents.value, path)
    && fileContents.value[path] !== savedContents.value[path]
}

async function saveCurrent() {
  if (!activeFile.value) return
  if (activeReadOnly.value) {
    ElMessage.warning('该文件只读')
    return
  }
  if (editor) fileContents.value[activeFile.value] = editor.getValue()
  try {
    await ElMessageBox.confirm(
      `将当前内容保存到“${activeFile.value}”，是否继续？`,
      '覆盖保存',
      { type: 'warning', confirmButtonText: '确认保存', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  saving.value = true
  try {
    const content = fileContents.value[activeFile.value] || ''
    await post('/workspace/files/save', { path: activeFile.value, content })
    savedContents.value[activeFile.value] = content
    await refreshTree()
    ElMessage.success('已覆盖保存原文件')
  } catch(e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function downloadCurrent() {
  if (!activeFile.value) return
  if (editor) fileContents.value[activeFile.value] = editor.getValue()
  const content = fileContents.value[activeFile.value] || ''
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = activeFile.value.split('/').pop() || 'code.py'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
  ElMessage.success('已下载当前编辑版本，原文件未改变')
}

// --- Code Execution ---
function currentRunPayload() {
  const environment = environments.value.find(item => item.id === selectedEnvironmentId.value)
  return {
    code: fileContents.value[activeFile.value] || '',
    entry_path: activeFile.value,
    files: fileContents.value,
    environment_id: environment?.id ?? null,
    environment_name: environment?.name || null,
  }
}

function applyRunResult(result: any) {
  output.value = (result.output || '') + (result.error ? '\n\n[stderr]\n' + result.error : '')
  runStatus.value = result.status || (result.returncode === 0 ? 'success' : 'error')
  lastRunFile.value = result.entry_path || activeFile.value
  runReport.value = {
    ...result,
    status: runStatus.value,
    entry_path: result.entry_path || activeFile.value,
  }
  if (result.model_registry?.status === 'success') {
    ElMessage.success(`模型 #${result.model_registry.model_id} 已保存到模型列表`)
  } else if (result.model_registry?.status === 'error') {
    ElMessage.warning('代码运行成功，但模型未入库：' + result.model_registry.message)
  }
  nextTick(renderRunCharts)
}

function formatDuration(value: any) {
  const milliseconds = Number(value)
  if (!Number.isFinite(milliseconds)) return '-'
  if (milliseconds < 1000) return `${milliseconds.toFixed(0)} ms`
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 2 : 1)} s`
}

function formatMetric(value: number) {
  if (!Number.isFinite(value)) return '-'
  const absolute = Math.abs(value)
  if ((absolute > 0 && absolute < 0.0001) || absolute >= 100000) return value.toExponential(4)
  return Number(value.toFixed(6)).toString()
}

function reportInputLabel(role: 'dataset' | 'hyperparameters') {
  const input = runReport.value?.inputs?.[role]
  if (!input?.filename) return role === 'dataset' ? '代码内置或工作区文件' : '脚本默认参数'
  return `${input.filename}${input.size_bytes ? `（${formatSize(input.size_bytes)}）` : ''}`
}

function renderRunCharts() {
  runMetricChart?.dispose()
  runSeriesChart?.dispose()
  predictionCharts.forEach(chart => chart.dispose())
  predictionCharts = []
  runMetricChart = null
  runSeriesChart = null
  if (runMetricChartRef.value && runMetricRows.value.length) {
    runMetricChart = echarts.init(runMetricChartRef.value)
    runMetricChart.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 90, right: 35, top: 20, bottom: 35 },
      xAxis: { type: 'value', splitLine: { lineStyle: { color: '#edf2f7' } } },
      yAxis: { type: 'category', data: runMetricRows.value.map(item => item.label) },
      series: [{
        type: 'bar', barMaxWidth: 28,
        data: runMetricRows.value.map(item => item.value),
        itemStyle: { color: '#409eff', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', formatter: ({ value }: any) => formatMetric(Number(value)) },
      }],
    })
  }
  if (runSeriesChartRef.value && runSeriesRows.value.length) {
    runSeriesChart = echarts.init(runSeriesChartRef.value)
    const keys = Array.from(new Set(runSeriesRows.value.flatMap(point =>
      Object.keys(point).filter(key => !['epoch', 'total_epochs'].includes(key))
    )))
    runSeriesChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: keys.map(key => metricLabels[key] || key) },
      grid: { left: 60, right: 25, top: 45, bottom: 45 },
      xAxis: { type: 'category', name: 'Epoch', data: runSeriesRows.value.map(point => point.epoch) },
      yAxis: { type: 'value', scale: true },
      series: keys.map(key => ({
        name: metricLabels[key] || key,
        type: 'line', smooth: true, showSymbol: runSeriesRows.value.length < 30,
        data: runSeriesRows.value.map(point => point[key] ?? null),
      })),
    })
  }
  const visualization = runVisualization.value
  const createChart = (element: HTMLElement | null, option: echarts.EChartsOption) => {
    if (!element) return
    const chart = echarts.init(element)
    chart.setOption(option)
    predictionCharts.push(chart)
  }
  if (visualization.prediction_trend) {
    createChart(runTrendChartRef.value, {
      tooltip: { trigger: 'axis' }, legend: { data: ['真实值', '预测值'], top: 0 },
      grid: { left: 60, right: 24, top: 42, bottom: 48 },
      xAxis: { type: 'category', name: '样本序号', data: visualization.prediction_trend.map((item: any[]) => item[0]) },
      yAxis: { type: 'value', name: '预测目标', scale: true },
      series: [
        { name: '真实值', type: 'line', data: visualization.prediction_trend.map((item: any[]) => item[1]), showSymbol: false, lineStyle: { width: 2 } },
        { name: '预测值', type: 'line', data: visualization.prediction_trend.map((item: any[]) => item[2]), showSymbol: false, lineStyle: { width: 2, type: 'dashed' } },
      ],
    })
  }
  if (visualization.actual_vs_predicted?.length) {
    const values = visualization.actual_vs_predicted.flat().filter(Number.isFinite)
    const minimum = Math.min(...values)
    const maximum = Math.max(...values)
    createChart(runScatterChartRef.value, {
      tooltip: { trigger: 'item', formatter: (item: any) => `真实值：${item.value[0]}<br>预测值：${item.value[1]}` },
      grid: { left: 60, right: 24, top: 30, bottom: 48 },
      xAxis: { type: 'value', name: '真实值', scale: true }, yAxis: { type: 'value', name: '预测值', scale: true },
      series: [
        { type: 'scatter', data: visualization.actual_vs_predicted, symbolSize: 7, itemStyle: { opacity: .65 } },
        { type: 'line', data: [[minimum, minimum], [maximum, maximum]], symbol: 'none', lineStyle: { type: 'dashed', color: '#909399' } },
      ],
    })
  }
  if (visualization.residual_histogram) {
    createChart(runResidualChartRef.value, {
      tooltip: { trigger: 'axis' }, grid: { left: 55, right: 20, top: 24, bottom: 70 },
      xAxis: { type: 'category', name: '残差区间', data: visualization.residual_histogram.labels, axisLabel: { rotate: 38 } },
      yAxis: { type: 'value', name: '样本数' },
      series: [{ type: 'bar', data: visualization.residual_histogram.counts, itemStyle: { color: '#7c3aed', borderRadius: [4, 4, 0, 0] } }],
    })
  }
  if (visualization.feature_importance) {
    createChart(runImportanceChartRef.value, {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, grid: { left: 100, right: 30, top: 20, bottom: 35 },
      xAxis: { type: 'value', name: '重要性' }, yAxis: { type: 'category', inverse: true, data: visualization.feature_importance.labels },
      series: [{ type: 'bar', data: visualization.feature_importance.values, itemStyle: { color: '#0ea5e9', borderRadius: [0, 4, 4, 0] } }],
    })
  }
  if (visualization.confusion_matrix) {
    const labels = visualization.confusion_matrix.labels
    const heatmap = visualization.confusion_matrix.values.flatMap((row: number[], y: number) => row.map((value, x) => [x, y, value]))
    createChart(runConfusionChartRef.value, {
      tooltip: { formatter: (item: any) => `实际：${labels[item.value[1]]}<br>预测：${labels[item.value[0]]}<br>样本数：${item.value[2]}` },
      grid: { left: 75, right: 65, top: 25, bottom: 60 },
      xAxis: { type: 'category', name: '预测类别', data: labels }, yAxis: { type: 'category', name: '实际类别', data: labels },
      visualMap: { min: 0, max: Math.max(1, ...heatmap.map((item: number[]) => item[2])), calculable: true, right: 0, top: 'center', inRange: { color: ['#eff6ff', '#60a5fa', '#1d4ed8'] } },
      series: [{ type: 'heatmap', data: heatmap, label: { show: true } }],
    })
  }
  if (visualization.class_distribution) {
    createChart(runClassDistributionChartRef.value, {
      tooltip: { trigger: 'axis' }, legend: { data: ['实际', '预测'] }, grid: { left: 55, right: 20, top: 42, bottom: 45 },
      xAxis: { type: 'category', data: visualization.class_distribution.labels }, yAxis: { type: 'value', name: '样本数' },
      series: [
        { name: '实际', type: 'bar', data: visualization.class_distribution.actual },
        { name: '预测', type: 'bar', data: visualization.class_distribution.predicted },
      ],
    })
  }
}

function resizeRunCharts() {
  runMetricChart?.resize()
  runSeriesChart?.resize()
  predictionCharts.forEach(chart => chart.resize())
}

function closeRunReport() {
  runMetricChart?.dispose()
  runSeriesChart?.dispose()
  predictionCharts.forEach(chart => chart.dispose())
  predictionCharts = []
  runMetricChart = null
  runSeriesChart = null
  runReport.value = null
  nextTick(() => editor?.layout())
}

function viewRunReport(record: any) {
  showRunHistory.value = false
  runReport.value = record
  output.value = (record.output || '') + (record.error ? '\n\n[stderr]\n' + record.error : '')
  runStatus.value = record.status || 'error'
  lastRunFile.value = record.entry_path || ''
  nextTick(renderRunCharts)
}

async function loadRunModelFolders() {
  try {
    const response = await fetch('/api/v1/models/folders', { headers: authHeaders() })
    runModelFolders.value = response.ok ? await response.json() : []
  } catch { runModelFolders.value = [] }
}

async function runCurrent() {
  if (!activeFile.value || !activeFile.value.endsWith('.py')) return
  if (editor) fileContents.value[activeFile.value] = editor.getValue()
  running.value = true; output.value = ''; runStatus.value = ''
    runTargetMode.value = 'script'
    runCustomTargetColumn.value = null
    runFeatureColumns.value = []
  try {
    const payload = currentRunPayload()
    const preflight = await post('/workspace/run/preflight', payload)
    runInputRequirement.value = preflight
    datasetInputFile.value = null
    selectedRunDatasetId.value = null
    runDatasetDefaultFieldRoles.value = null
    runDatasetManualFieldRoles.value = { time_column: null, id_columns: [], feature_columns: [], target_column: null }
    runDatasetFieldRolesSource.value = '系统推荐'
    runDatasetFieldRoleMode.value = 'default'
    runDatasetColumns.value = []
    runDatasetSource.value = 'upload'
    runParameterMode.value = 'default'
    const filename = activeFile.value.split('/').pop()?.replace(/\.py$/i, '') || '代码'
    runTrainingName.value = `${importedAlgorithm.value?.algorithm_name || filename}-训练`
    clearRunParameterFile()
    runModelFolderId.value = null
    await loadRunModelFolders()
    showRunInputs.value = true
  } catch(e: any) {
    output.value = '错误: ' + (e?.response?.data?.detail || e?.message || '未知')
    runStatus.value = 'error'
  }
  finally { running.value = false }
}

function openControlSimulation() {
  if (!activeFile.value || !activeFile.value.endsWith('.py')) return
  if (editor) fileContents.value[activeFile.value] = editor.getValue()
  const filename = activeFile.value.split('/').pop()?.replace(/\.py$/i, '') || '代码'
  controlSimulation.value = {
    name: `${importedAlgorithm.value?.algorithm_name || filename}-仿真`, dataset_id: null,
    time_column: '', state_columns: [], control_columns: [], disturbance_columns: [], setpoint: undefined,
  }
  controlDatasetColumns.value = []
  showControlSimulation.value = true
}

async function loadControlDatasetColumns(datasetId: number) {
  controlDatasetColumns.value = []
  controlSimulation.value.time_column = ''
  controlSimulation.value.state_columns = []
  controlSimulation.value.control_columns = []
  controlSimulation.value.disturbance_columns = []
  if (!datasetId) return
  try {
    const response = await fetch(`/api/v1/datasets/${datasetId}/rows?offset=0&limit=1`, { headers: authHeaders() })
    const result = await response.json()
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`)
    controlDatasetColumns.value = result.columns || []
  } catch (error: any) {
    ElMessage.error('读取数据集字段失败：' + (error?.message || '未知错误'))
  }
}

async function runControlSimulation() {
  if (!controlSimulation.value.name.trim() || !controlSimulation.value.dataset_id) return ElMessage.warning('请填写仿真名称并选择平台数据集')
  if (editor) fileContents.value[activeFile.value] = editor.getValue()
  const payload = currentRunPayload()
  const formData = new FormData()
  formData.append('code', payload.code)
  formData.append('entry_path', payload.entry_path)
  formData.append('files_json', JSON.stringify(payload.files || {}))
  formData.append('simulation_name', controlSimulation.value.name.trim())
  formData.append('dataset_id', String(controlSimulation.value.dataset_id))
  formData.append('time_column', controlSimulation.value.time_column)
  formData.append('state_columns_json', JSON.stringify(controlSimulation.value.state_columns))
  formData.append('control_columns_json', JSON.stringify(controlSimulation.value.control_columns))
  formData.append('disturbance_columns_json', JSON.stringify(controlSimulation.value.disturbance_columns))
  if (controlSimulation.value.setpoint !== undefined) formData.append('setpoint', String(controlSimulation.value.setpoint))
  if (payload.environment_id !== null) formData.append('environment_id', String(payload.environment_id))
  if (importedAlgorithm.value) {
    formData.append('algorithm_id', String(importedAlgorithm.value.algorithm_id))
    formData.append('algorithm_name', importedAlgorithm.value.algorithm_name)
  }
  controlSimulationRunning.value = true
  try {
    const response = await fetch('/api/v1/workspace/control-simulations', { method: 'POST', headers: authHeaders(), body: formData })
    const result = await response.json()
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`)
    showControlSimulation.value = false
    ElMessage.success(`仿真完成，耗时 ${(Number(result.duration_ms || 0) / 1000).toFixed(1)} 秒；结果已登记到控制仿真任务`)
    router.push({ path: '/model/control-simulations', query: { algorithm_id: String(result.algorithm_id || '') } })
  } catch (error: any) {
    ElMessage.error('仿真任务失败：' + (error?.message || '未知错误'))
  } finally { controlSimulationRunning.value = false }
}

function selectRunDataset(file: any) { datasetInputFile.value = file?.raw || null }

async function loadRunDatasetFieldRoles(datasetId: number | null) {
  runDatasetDefaultFieldRoles.value = null
  runDatasetManualFieldRoles.value = { time_column: null, id_columns: [], feature_columns: [], target_column: null }
  runDatasetFieldRolesSource.value = '系统推荐'
  runDatasetFieldRoleMode.value = 'default'
  runTargetMode.value = 'script'
  runCustomTargetColumn.value = null
  runFeatureColumns.value = []
  runDatasetColumns.value = []
  if (!datasetId) return
  try {
    const [result, columnsResult] = await Promise.all([
      get(`/datasets/${datasetId}/field-roles`),
      fetch(`/api/v1/datasets/${datasetId}/rows?offset=0&limit=1`, { headers: authHeaders() })
        .then(async response => response.ok ? response.json() : { columns: [] })
        .catch(() => ({ columns: [] })),
    ])
    const saved = result?.saved
    const recommended = result?.recommendation
    runDatasetColumns.value = Array.isArray(columnsResult?.columns) ? columnsResult.columns : []
    const roles = saved || recommended || {
      time_column: null, id_columns: [], feature_columns: [], target_column: null,
    }
    runDatasetDefaultFieldRoles.value = {
      time_column: roles.time_column || null,
      id_columns: Array.isArray(roles.id_columns) ? [...roles.id_columns] : [],
      feature_columns: Array.isArray(roles.feature_columns) ? [...roles.feature_columns] : [],
      target_column: roles.target_column || null,
    }
    runDatasetManualFieldRoles.value = {
      time_column: runDatasetDefaultFieldRoles.value.time_column,
      id_columns: [...runDatasetDefaultFieldRoles.value.id_columns],
      feature_columns: [...runDatasetDefaultFieldRoles.value.feature_columns],
      target_column: runDatasetDefaultFieldRoles.value.target_column,
    }
    runDatasetFieldRolesSource.value = saved ? '已保存的' : (recommended ? '系统推荐的' : '未设置的')
  } catch (error: any) {
    ElMessage.warning('未能读取数据集字段用途：' + (error?.message || '将按代码自身逻辑运行'))
  }
}

async function confirmRunTargetOverride() {
  if (runDatasetSource.value !== 'platform' || runTargetMode.value === 'script') return true
  const target = activeRunDatasetFieldRoles.value?.target_column
  if (!target) {
    ElMessage.warning('请先选择可用的预测目标列')
    return false
  }
  try {
    await ElMessageBox.confirm(
      `本次将临时用“${target}”覆盖代码中常见的目标列配置。若源码的特征、预处理或评估逻辑仍针对原目标，运行可能报错或结果不可靠；源码文件不会被保存或改动。`,
      '确认覆盖本次预测目标',
      { confirmButtonText: '确认并运行', cancelButtonText: '返回检查', type: 'warning' },
    )
    return true
  } catch {
    return false
  }
}

function onRunParameterModeChange() { clearRunParameterFile() }

async function selectRunParameterFile(fileItem: any) {
  try {
    const file = fileItem?.raw as File
    const parsed = await readHyperparameterFile(file)
    runParameterFile.value = file
    runParameterText.value = parsed.text
    ElMessage.success(`已读取参数文件 ${file.name}`)
  } catch (error: any) {
    clearRunParameterFile()
    ElMessage.error(error?.message || '参数文件读取失败')
  }
}

function clearRunParameterFile() {
  runParameterText.value = ''
  runParameterFile.value = null
}

async function runWithInputs() {
  if (!runTrainingName.value.trim()) return ElMessage.warning('请输入训练名称')
  if (!runInputsReady.value) return ElMessage.warning('请上传文件或选择一个平台数据集')
  if (editor) fileContents.value[activeFile.value] = editor.getValue()
  const payload = currentRunPayload()
  if (!await confirmRunTargetOverride()) return
  const formData = new FormData()
  formData.append('code', payload.code)
  formData.append('entry_path', payload.entry_path)
  formData.append('files_json', JSON.stringify(payload.files || {}))
  formData.append('training_name', runTrainingName.value.trim())
  if (payload.environment_id !== null) formData.append('environment_id', String(payload.environment_id))
  if (payload.environment_name) formData.append('environment_name', payload.environment_name)
  if (runDatasetSource.value === 'upload' && datasetInputFile.value) {
    formData.append('dataset_file', datasetInputFile.value)
  } else if (runDatasetSource.value === 'platform' && selectedRunDatasetId.value !== null) {
    formData.append('dataset_id', String(selectedRunDatasetId.value))
    if (activeRunDatasetFieldRoles.value) {
      formData.append('field_roles_json', JSON.stringify(activeRunDatasetFieldRoles.value))
    }
  }

  try {
    if (runParameterMode.value === 'manual') {
      const params = parseHyperparameterText(runParameterText.value)
      formData.append('hyperparameters_file', new File(
        [JSON.stringify(params, null, 2)], 'manual_hyperparameters.json', { type: 'application/json' }
      ))
    } else if (runParameterMode.value === 'file' && runParameterFile.value) {
      formData.append('hyperparameters_file', runParameterFile.value)
    }
  } catch (error: any) {
    return ElMessage.error(error?.message || '超参数格式错误')
  }

  if (saveToModelRegistry.value) {
    formData.append('save_model', 'true')
    if (runModelFolderId.value !== null) formData.append('model_folder_id', String(runModelFolderId.value))
  }
  running.value = true; output.value = ''; runStatus.value = ''
  try {
    const response = await fetch('/api/v1/workspace/run/with-inputs', {
      method: 'POST', headers: authHeaders(), body: formData,
    })
    const startResult = await response.json()
    if (!response.ok) throw new Error(startResult.detail || `HTTP ${response.status}`)
    showRunInputs.value = false
    const runId = startResult.run_id
    if (!runId) throw new Error('未返回 run_id')
    currentRunId.value = runId

    // 轮询 stdout 增量（实时滚动终端）
    let offset = 0
    await new Promise<void>((resolve) => {
      const poll = setInterval(async () => {
        try {
          const res = await fetch(`/api/v1/workspace/runs/${runId}/stdout?offset=${offset}`, { headers: authHeaders() })
          const data = await res.json()
          if (data.content) { output.value += data.content; offset = data.offset }
          if (data.eof) { clearInterval(poll); resolve() }
        } catch { /* 忽略轮询网络错误，继续 */ }
      }, 800)
    })

    // 运行结束，获取完整结果（指标/图表）
    const resultRes = await fetch(`/api/v1/workspace/runs/${runId}/result`, { headers: authHeaders() })
    const result = await resultRes.json()
    applyRunResult(result)
  } catch (e: any) {
    if (e?.message === 'stopped') {
      runStatus.value = 'stopped'
      applyRunResult({
        status: 'stopped', returncode: -1, output: output.value, error: '用户手动停止运行', duration_ms: 0,
        analysis: { status: 'stopped', returncode: -1, duration_ms: 0, metrics: {}, series: [], stdout_lines: 0 },
        inputs: {}, entry_path: activeFile.value,
      })
    } else {
    const message = '错误: ' + (e?.message || '未知')
    applyRunResult({
      status: 'error', returncode: -1, output: output.value, error: message, duration_ms: 0,
      analysis: { status: 'error', returncode: -1, duration_ms: 0, metrics: {}, series: [], stdout_lines: 0 },
      inputs: {}, entry_path: activeFile.value,
    })
    ElMessage.error('运行失败: ' + (e?.message || '未知错误'))
    }
  } finally { running.value = false; currentRunId.value = '' }
}

async function stopRun() {
  if (!currentRunId.value) return
  try {
    await fetch(`/api/v1/workspace/runs/${currentRunId.value}/stop`, { method: 'POST', headers: authHeaders() })
    output.value += '\n\n🛑 正在停止运行...'
  } catch { /* ignore */ }
}

function runInputSummary(row: any) {
  const names = [row.inputs?.dataset?.filename, row.inputs?.hyperparameters?.filename].filter(Boolean)
  return names.length ? names.join(' / ') : '代码内置或工作区文件'
}

// --- Dataset Integration ---
async function loadDatasets() {
  try {
    const r = await get('/workspace/datasets')
    datasets.value = r.items || []
  } catch(e) { /* ignore */ }
}

// --- Algorithm Store Integration ---
async function loadAlgorithms() {
  algorithmsLoading.value = true
  try {
    const query = algorithmSearch.value.trim()
    const r = await get(`/workspace/algorithms${query ? `?search=${encodeURIComponent(query)}` : ''}`)
    algorithms.value = r.items || []
  } catch(e: any) {
    algorithms.value = []
    ElMessage.error('算法商店加载失败: ' + (e?.message || '未知错误'))
  } finally {
    algorithmsLoading.value = false
  }
}

async function importAlgorithm(item: AlgorithmItem) {
  algorithmsLoading.value = true
  try {
    const result = await post(`/workspace/algorithms/${item.id}/import`)
    const imported: ImportedAlgorithm = {
      ...result,
      algorithm_id: item.id,
      algorithm_name: item.name,
      dependency_id: result.dependency_id ?? item.dependency_id,
    }
    try {
      await selectProject({ name: `#${item.id} ${item.name}`, path: imported.project_path, algorithm_id: item.id })
    } catch (error) {
      if (error === 'cancel') return
      throw error
    }
    // Only the selected algorithm remains active in the editor context.
    importedAlgorithms.value = [imported]

    const configuredEnvironment = environments.value.find(env => env.id === imported.dependency_id)
    if (configuredEnvironment?.available) {
      selectedEnvironmentId.value = configuredEnvironment.id
    } else if (imported.dependency_id && configuredEnvironment && !configuredEnvironment.available) {
      selectedEnvironmentId.value = environments.value.find(env => env.available)?.id ?? 0
      ElMessage.warning(`算法关联环境不可用：${configuredEnvironment.unavailable_reason}；已切换到可用环境`)
    }

    const entryPath = `${imported.project_path}/${imported.entry}`
    const fakeFile: FileItem = {
      name: imported.entry.split('/').pop() || 'main.py', path: entryPath,
      type: 'file', size: 0, modified: ''
    }
    await openFile(fakeFile)
    ElMessage.success(`已载入 ${imported.source_label}，可编辑并覆盖上传源码`)
  } catch(e: any) {
    ElMessage.error('算法载入失败: ' + (e?.message || '未知错误'))
  } finally {
    algorithmsLoading.value = false
  }
}

async function saveToAlgorithmStore() {
  const item = importedAlgorithm.value
  if (!item || !activeAlgorithmFile.value || item.save_policy !== 'overwrite_source') return
  if (editor) fileContents.value[activeFile.value] = editor.getValue()
  const relativePath = activeFile.value.slice(item.project_path.length + 1)
  try {
    await ElMessageBox.confirm(
      `覆盖算法商店中的原始 Python 源码：#${item.algorithm_id} ${item.algorithm_name}。是否继续？`,
      '覆盖算法原源码',
      { type: 'warning', confirmButtonText: '确认保存', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  savingAlgorithm.value = true
  try {
    const content = fileContents.value[activeFile.value] || ''
    await post('/workspace/files/save', { path: activeFile.value, content })
    const result = await put(`/workspace/algorithms/${item.algorithm_id}/source`, {
      project_path: item.project_path,
      relative_path: relativePath,
      content,
      expected_revision: item.revision,
    })
    savedContents.value[activeFile.value] = content
    item.revision = result.revision
    item.source_mode = result.source_mode
    item.source_label = result.source_label
    item.has_original_source = result.has_original_source
    await loadAlgorithms()
    ElMessage.success(result.message || '算法源码已保存')
  } catch(e: any) {
    ElMessage.error('保存到算法商店失败: ' + (e?.message || '未知错误'))
  } finally {
    savingAlgorithm.value = false
  }
}

async function loadEnvironments() {
  try {
    const r = await get('/workspace/environments')
    environments.value = r.items || []
    const preferred = environments.value.find(item => item.available && item.system && String(item.actual_python_version || '').startsWith('3.11'))
      || environments.value.find(item => item.available)
    if (selectedEnvironmentId.value === null && preferred) {
      selectedEnvironmentId.value = preferred.id
    }
  } catch(e) {
    ElMessage.error('运行环境加载失败，请稍后重试')
  }
}

function environmentLabel(env: any) {
  return env.available ? `${env.name} · Python ${env.actual_python_version}` : `${env.name}（不可用）`
}

async function openRunHistory() {
  try {
    const r = await get('/workspace/runs?limit=50')
    runHistory.value = r.items || []
    showRunHistory.value = true
  } catch(e) { ElMessage.error('运行记录加载失败') }
}

async function loadDatasetCode(ds: DsItem) {
  try {
    const attached = await post(`/workspace/datasets/${ds.id}/attach`)
    const code = `# 加载平台数据集 #${ds.id}: ${ds.name}
from pathlib import Path
import pandas as pd

DATASET_PATH = Path.cwd() / ${JSON.stringify(attached.path)}
df = pd.read_csv(DATASET_PATH)

print(f"数据集: ${ds.name}")
print(f"形状: {df.shape}")
print(df.head())
print("\\n列信息:")
print(df.dtypes)
print("\\n基本统计:")
print(df.describe())
`
    const fname = `explore_dataset_${ds.id}_${ds.name.replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g,'_')}.py`
    await post('/workspace/files/save', { path: fname, content: code })
    fileContents.value[fname] = code
    savedContents.value[fname] = code
    const fakeFile: FileItem = { name: fname, path: fname, type: 'file', size: code.length, modified: '' }
    await refreshTree()
    await openFile(fakeFile)
    ElMessage.success(`数据集 #${ds.id} 已复制到工作区，可直接运行`)
  } catch(e: any) {
    ElMessage.error('数据集载入失败: ' + (e?.message || '未知错误'))
  }
}

// --- Publish ---
function openPublishDialog() {
  if (!activeFile.value.endsWith('.py')) return ElMessage.warning('请选择一个 Python 文件')
  const basename = activeFile.value.split('/').pop()?.replace(/\.py$/i, '') || '新算法'
  publishForm.value = {
    name: basename,
    description: '',
    task_type: 'regression',
    algorithm_type: 'mlp',
    framework: 'pytorch',
    version: '1.0.0',
    tags: [],
    file_path: activeFile.value,
  }
  showPublish.value = true
}

async function doPublish() {
  if (!publishForm.value.name.trim()) { ElMessage.warning('请输入算法名称'); return }
  publishing.value = true
  try {
    // Save current file first
    if (editor && activeFile.value) {
      fileContents.value[activeFile.value] = editor.getValue()
      await post('/workspace/files/save', { path: activeFile.value, content: fileContents.value[activeFile.value] || '' })
    }
    
    const result = await post('/algorithm-store/publish-from-code', {
      name: publishForm.value.name,
      description: publishForm.value.description,
      code: fileContents.value[activeFile.value] || '',
      language: 'python',
      filename: activeFile.value.split('/').pop() || 'main.py',
      task_type: publishForm.value.task_type,
      algorithm_type: publishForm.value.algorithm_type,
      framework: publishForm.value.framework,
      version: publishForm.value.version,
      tags: publishForm.value.tags,
      dependency_id: selectedEnvironmentId.value,
    })
    await loadAlgorithms()
    ElMessage.success(`新算法 #${result.id} 已保存到算法商店`)
    showPublish.value = false
  } catch(e: any) { ElMessage.error('发布失败: ' + (e?.message || '')) }
  finally { publishing.value = false }
}

// --- Project Creation ---
function openCreateProject() {
  projectForm.value = { name: `code_project_${Date.now().toString(36)}`, template: 'blank' }
  showCreateProject.value = true
}

async function createProject() {
  const name = projectForm.value.name.trim()
  if (!name) {
    ElMessage.warning('请输入项目名称')
    return
  }
  if (!/^[\w\-\u4e00-\u9fa5]+$/.test(name)) {
    ElMessage.warning('项目名称只能包含中文、字母、数字、下划线和连字符')
    return
  }
  creatingProject.value = true
  try {
    const r = await post('/workspace/projects', { name, template: projectForm.value.template })
    ElMessage.success(`项目 "${r.project}" 创建成功`)
    showCreateProject.value = false
    await selectProject({ name, path: r.project })
    importedAlgorithms.value = []
    // Open first file
    if (r.files && r.files.length > 0) {
      const firstFile = r.project + '/' + r.files[0]
      const fakeFile: FileItem = { name: r.files[0], path: firstFile, type: 'file', size: 0, modified: '' }
      await openFile(fakeFile)
    }
  } catch(e: any) {
    ElMessage.error('创建失败: ' + (e?.message || '未知错误'))
  } finally {
    creatingProject.value = false
  }
}

// --- Context Menu ---
function contextMenu(e: MouseEvent, f: FileItem) {
  e.preventDefault()
  contextTarget.value = f
  contextX.value = e.clientX
  contextY.value = e.clientY
  contextVisible.value = true
  document.addEventListener('click', () => { contextVisible.value = false }, { once: true })
}

// --- Helpers ---
function iconForFile(name: string) {
  const ext = name.split('.').pop() || ''
  const map: Record<string,string> = { py: '🐍', js: '📜', ts: '📘', json: '📋', yaml: '⚙️', yml: '⚙️', md: '📝', html: '🌐', css: '🎨', sql: '🗄', txt: '📄', csv: '📊', ipynb: '📓', pkl: '📦', pt: '🧠', pth: '🧠', onnx: '🔷' }
  return map[ext] || '📄'
}

function formatSize(bytes: number) {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1048576) return (bytes/1024).toFixed(1) + 'KB'
  return (bytes/1048576).toFixed(1) + 'MB'
}

// --- Lifecycle ---
onMounted(async () => {
  window.addEventListener('resize', resizeRunCharts)
  await refreshTree()
  await loadDatasets()
  await loadEnvironments()
  await loadAlgorithms()
  const requestedAlgorithmId = Number(route.query.algorithm_id)
  if (Number.isInteger(requestedAlgorithmId) && requestedAlgorithmId > 0) {
    const requested = algorithms.value.find(item => item.id === requestedAlgorithmId)
    if (requested) await importAlgorithm(requested)
    else ElMessage.warning(`算法 #${requestedAlgorithmId} 不存在或不可在沙箱编辑`)
  }
  // Load Monaco from CDN
  if (typeof monaco === 'undefined') {
    // Monaco loader
    const script = document.createElement('script')
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js'
    script.onload = () => {
      ;(window as any).require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' } })
      ;(window as any).require(['vs/editor/editor.main'], () => {
        initMonaco()
      })
    }
    document.head.appendChild(script)
  } else {
    initMonaco()
  }
})

onUnmounted(() => {
  if (editor) editor.dispose()
  disposeAiDiff()
  window.removeEventListener('resize', resizeRunCharts)
  runMetricChart?.dispose()
  runSeriesChart?.dispose()
  predictionCharts.forEach(chart => chart.dispose())
})

watch(activeReadOnly, value => {
  if (editor) editor.updateOptions({ readOnly: value })
})
</script>

<style scoped>
.workspace-root { display:flex; height:calc(100vh - 60px); background:#1e1e1e; color:#d4d4d4; }
.run-report-page { width:100%; height:100%; overflow:auto; background:#f3f6fa; color:#1f2937; }
.run-report-header { position:sticky; top:0; z-index:5; min-height:64px; padding:0 24px; display:flex; align-items:center; justify-content:space-between; gap:16px; background:#fff; border-bottom:1px solid #e5e7eb; box-shadow:0 1px 4px rgba(15,23,42,.05); }
.run-report-header > div:first-child { display:flex; align-items:center; gap:12px; }
.run-report-title { font-size:20px; font-weight:700; color:#111827; }
.run-report-id { color:#64748b; font-size:12px; font-family:monospace; }
.run-report-content { max-width:1440px; margin:0 auto; padding:22px 24px 40px; display:flex; flex-direction:column; gap:18px; }
.run-summary-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }
.run-summary-card { min-height:112px; padding:17px 19px; display:flex; flex-direction:column; gap:7px; background:#fff; border:1px solid #e5e7eb; border-radius:10px; box-shadow:0 2px 8px rgba(15,23,42,.04); }
.run-summary-card span { color:#64748b; font-size:13px; }
.run-summary-card strong { color:#0f172a; font-size:23px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.run-summary-card small { color:#94a3b8; }
.run-info-card, .run-metric-panel, .run-log-card { border:none; border-radius:10px; }
.run-prediction-section { border:none; border-radius:10px; }
.run-section-header { display:flex; align-items:center; justify-content:space-between; }
.run-prediction-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
.run-visual-card { min-width:0; padding:12px 14px 4px; border:1px solid #e5e7eb; border-radius:8px; background:#fff; }
.run-visual-card h4 { margin:2px 0 4px; color:#334155; font-size:14px; }
.run-metric-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; align-items:stretch; }
.run-metric-grid > :only-child { grid-column:1 / -1; }
.run-metric-cards { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:8px; }
.run-metric-card { min-width:130px; padding:10px 13px; display:flex; flex-direction:column; gap:4px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:7px; }
.run-metric-card span { color:#64748b; font-size:12px; }
.run-metric-card strong { color:#2563eb; font-size:19px; }
.run-chart { width:100%; height:320px; }
.run-report-log { min-height:100px; max-height:360px; margin:0; padding:14px; overflow:auto; border-radius:7px; background:#111827; color:#d1d5db; font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.run-report-log.error { color:#fca5a5; }
@media (max-width:1100px) {
  .run-summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .run-metric-grid { grid-template-columns:1fr; }
  .run-prediction-grid { grid-template-columns:1fr; }
}
.file-sidebar { width:300px; min-width:300px; background:#252526; border-right:1px solid #3e3e42; display:flex; flex-direction:column; overflow:hidden; }
.sidebar-header { padding:12px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #3e3e42; }
.sidebar-header h3 { margin:0; font-size:14px; color:#ccc; }
.sidebar-actions { display:flex; gap:4px; }
.current-project-bar { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:7px 10px; color:#cbd5e1; background:#202124; border-bottom:1px solid #3e3e42; font-size:12px; }
.current-project-bar span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.current-project-bar :deep(.el-button) { flex:none; }
.file-tree { flex:1; min-height:130px; overflow-y:auto; padding:4px 0; }
.tree-item { display:flex; align-items:center; padding:6px 8px; cursor:pointer; font-size:13px; white-space:nowrap; overflow:hidden; user-select:none; }
.tree-item:hover { background:#2a2d2e; }
.tree-item.active { background:#37373d; }
.tree-icon { margin-right:6px; font-size:14px; flex-shrink:0; }
.tree-name { overflow:hidden; text-overflow:ellipsis; flex:1; }
.tree-size { font-size:10px; color:#888; margin-left:8px; }
.tree-actions { display:flex; gap:2px; }
.empty-tree { text-align:center; color:#888; padding:40px 20px; font-size:13px; }
.sidebar-section { border-top:1px solid #3e3e42; padding:8px; }
.sidebar-section h4 { margin:4px 0 8px; font-size:12px; color:#888; text-transform:uppercase; }
.section-title { display:flex; align-items:center; justify-content:space-between; }
.section-title small { color:#777; font-size:11px; }
.section-list { max-height:160px; overflow-y:auto; margin-top:6px; }
.algorithm-section .section-list { max-height:210px; }
.algorithm-item { padding:7px 8px; cursor:pointer; border-radius:4px; font-size:12px; }
.algorithm-item:hover { background:#2a2d2e; }
.algorithm-item.active { background:#173a5e; box-shadow:inset 2px 0 0 #409eff; }
.algorithm-item .item-main { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#e5e7eb; }
.algorithm-item .item-main span { color:#60a5fa; margin-right:4px; }
.algorithm-item small { display:block; color:#67c23a; margin-top:2px; }
.algorithm-item small.companion { color:#e6a23c; }
.section-empty { padding:12px 6px; text-align:center; color:#777; font-size:11px; }
.ds-item { display:flex; align-items:center; gap:6px; padding:6px 8px; cursor:pointer; font-size:12px; border-radius:4px; }
.ds-item:hover { background:#2a2d2e; }
.ds-item .item-main { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ds-item small { margin-left:auto; color:#888; }

.editor-main { flex:1; display:flex; flex-direction:column; overflow:hidden; }
.tab-bar { display:flex; align-items:center; background:#2d2d30; border-bottom:1px solid #3e3e42; padding:0 8px; min-height:36px; overflow-x:auto; }
.tab { display:flex; align-items:center; padding:6px 12px; cursor:pointer; font-size:12px; border-right:1px solid #3e3e42; white-space:nowrap; }
.tab:hover { background:#37373d; }
.tab.active { background:#1e1e1e; border-bottom:2px solid #409EFF; }
.dirty-dot { margin-left:7px; color:#e6a23c; font-size:9px; }
.tab-close { margin-left:8px; color:#888; font-size:14px; }
.tab-close:hover { color:#fff; }
.tab-actions { margin-left:auto; display:flex; gap:4px; padding:2px 8px; }
.environment-option { display:flex; flex-direction:column; line-height:18px; padding:3px 0; }
.environment-option small { color:#909399; max-width:420px; overflow:hidden; text-overflow:ellipsis; }
.input-detection { display:flex; flex-direction:column; gap:10px; padding:12px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; }
.input-detection > div { display:flex; align-items:center; gap:10px; color:#475569; font-size:13px; }
.input-detection .el-tag { width:62px; justify-content:center; flex:none; }
.run-dataset-option { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.run-dataset-option b { color:#409eff; }
.run-dataset-option small { color:#909399; }
.template-option { display:flex; flex-direction:column; line-height:18px; padding:3px 0; }
.template-option small { color:#909399; }
.algorithm-context { min-height:38px; padding:6px 12px; display:flex; align-items:center; justify-content:space-between; gap:12px; background:#2a2118; border-bottom:1px solid #6b4f2a; font-size:12px; }
.algorithm-context > div { display:flex; align-items:center; gap:8px; min-width:0; }
.algorithm-context strong { color:#f3f4f6; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.algorithm-context span { color:#d6b98c; }
.editor-workbench { flex:1; min-height:0; display:flex; overflow:hidden; }
.editor-area { flex:1; min-width:0; overflow:hidden; }
.monaco-container { width:100%; height:100%; }
.ai-panel { width:380px; min-width:340px; max-width:42%; display:flex; flex-direction:column; background:#202124; border-left:1px solid #3e3e42; color:#d4d4d4; }
.ai-panel-header { min-height:46px; display:flex; align-items:center; justify-content:space-between; padding:6px 12px; border-bottom:1px solid #3e3e42; }
.ai-panel-header>div { display:flex; flex-direction:column; gap:2px; }
.ai-panel-header strong { color:#f3f4f6; font-size:13px; }
.ai-panel-header small { color:#8ab4f8; font-size:10px; }
.ai-privacy { margin:10px; padding:8px 10px; border:1px solid #34506f; border-radius:6px; background:#172334; color:#a8c7fa; font-size:11px; line-height:1.55; }
.ai-context { display:flex; align-items:center; gap:8px; padding:0 10px 8px; min-width:0; }
.ai-context span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#9ca3af; font-size:11px; }
.ai-quick-actions { display:flex; gap:8px; padding:0 10px 10px; border-bottom:1px solid #34363a; }
.ai-messages { flex:1; min-height:120px; overflow-y:auto; padding:10px; display:flex; flex-direction:column; gap:10px; }
.ai-empty { padding:16px 12px; border:1px dashed #4b5563; border-radius:7px; color:#9ca3af; font-size:12px; line-height:1.7; }
.ai-message { max-width:94%; border-radius:8px; padding:8px 10px; font-size:12px; line-height:1.65; }
.ai-message.user { align-self:flex-end; background:#174b7a; color:#e5f1ff; }
.ai-message.assistant { align-self:flex-start; background:#303134; color:#e5e7eb; border:1px solid #414347; }
.ai-message-role { margin-bottom:3px; color:#93c5fd; font-size:10px; font-weight:600; }
.ai-message-content { white-space:pre-wrap; overflow-wrap:anywhere; }
.ai-thinking { color:#8ab4f8; font-size:12px; animation:aiPulse 1.2s ease-in-out infinite; }
.ai-compose { padding:10px; border-top:1px solid #3e3e42; background:#252526; }
.ai-compose-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:8px; }
.ai-compose>small { display:block; margin-top:6px; color:#777; font-size:10px; }
.ai-diff-container { width:100%; height:70vh; min-height:480px; border:1px solid #3e3e42; }
.ai-create-target { margin-bottom:12px; padding:9px 12px; border-radius:6px; background:#f0f9eb; border:1px solid #b3e19d; color:#3d6b24; font-size:13px; }
@keyframes aiPulse { 0%,100%{opacity:.45} 50%{opacity:1} }
.welcome-area { flex:1; display:flex; align-items:center; justify-content:center; }
.welcome-card { text-align:center; padding:40px; }
.welcome-card h2 { font-size:28px; margin-bottom:8px; }
.welcome-card p { color:#888; }
.welcome-actions { margin-top:28px; display:flex; flex-direction:column; align-items:center; gap:12px; }
.welcome-actions small { color:#777; }
.template-card { cursor:pointer; padding:20px; text-align:center; transition:transform .2s; }
.template-card:hover { transform:translateY(-4px); }
.template-icon { font-size:36px; margin-bottom:8px; }
.template-card h4 { margin:8px 0 4px; }
.template-card p { color:#888; font-size:12px; margin:0; }

.output-panel { border-top:1px solid #3e3e42; max-height:200px; overflow:auto; }
.output-header { display:flex; justify-content:space-between; align-items:center; padding:8px 12px; background:#2d2d30; font-size:12px; }
.output-content { margin:0; padding:12px; font-size:13px; font-family:monospace; white-space:pre-wrap; background:#1e1e1e; color:#d4d4d4; }
.output-content.error { color:#f48771; }

.context-menu { position:fixed; background:#2d2d30; border:1px solid #3e3e42; border-radius:4px; padding:4px 0; z-index:9999; min-width:150px; box-shadow:0 4px 12px rgba(0,0,0,0.5); }
.ctx-item { padding:8px 16px; cursor:pointer; font-size:13px; }
.ctx-item:hover { background:#37373d; }
</style>
