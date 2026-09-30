<template>
<div class="pipeline-root">
  <div class="pipeline-header">
    <div class="pipeline-titlebar">
      <h2>🔧 低代码ML流水线</h2>
      <el-input v-model="workflowName" size="small" class="workflow-name" placeholder="输入工作流名称" />
    </div>
    <div class="header-actions">
      <el-dropdown :disabled="running" @command="loadTemplate">
        <el-button>从模板创建 <el-icon><ArrowDown/></el-icon></el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>系统模板</el-dropdown-item>
            <el-dropdown-item command="tabular">表格预测流程</el-dropdown-item>
            <el-dropdown-item command="timeseries">时序预测流程</el-dropdown-item>
            <el-dropdown-item command="direct">直接训练流程</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button :disabled="running" @click="openTemplateLibrary">📚 流程模板库</el-button>
      <el-button :disabled="running" @click="openWorkflowImport">📥 导入流程</el-button>
      <el-button :disabled="running || !nodes.length" @click="autoLayout">自动排版</el-button>
      <el-button :disabled="running" @click="checkPipeline">运行前检查</el-button>
      <el-button :disabled="running || !nodes.length" @click="saveDraft">保存草稿</el-button>
      <el-button type="primary" @click="runPipeline" :loading="running">▶ 运行</el-button>
      <el-button :disabled="running" @click="openRunHistory">🕘 运行记录</el-button>
      <el-button :loading="workflowDownloading" :disabled="running" @click="downloadCurrentWorkflow">
        ⬇ 完整工作流
      </el-button>
    </div>
  </div>

  <section v-if="currentRunId" class="pipeline-runtime-panel">
    <div class="runtime-progress">
      <div class="runtime-progress-title">
        <strong>运行进度 · Run #{{ currentRunId }}</strong>
        <el-tag :type="statusTagType(currentRunStatus || 'pending')">{{ statusLabel(currentRunStatus || 'pending') }}</el-tag>
        <span>{{ runProgress.completed }} / {{ runProgress.total }} 个节点已结束</span>
      </div>
      <el-progress
        :percentage="runProgress.percent"
        :status="currentRunStatus === 'failed' ? 'exception' : currentRunStatus === 'success' ? 'success' : undefined"
        :stroke-width="10"
      />
      <span class="runtime-active">{{ runProgress.activeLabel }}</span>
    </div>
    <div class="runtime-result">
      <span>训练结果</span>
      <div class="runtime-result-lines">
        <template v-for="item in currentTrainingResultItems" :key="'train-' + item.label">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </template>
      </div>
    </div>
    <div class="runtime-result">
      <span>测试结果</span>
      <div class="runtime-result-lines">
        <template v-for="item in currentEvaluationResultItems" :key="'eval-' + item.label">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </template>
      </div>
    </div>
  </section>

  <div class="pipeline-body">
    <aside class="node-library">
      <div class="library-title">
        <h3>节点库</h3>
        <span>拖拽或点击添加</span>
      </div>
      <section class="library-group">
        <h4>输入</h4>
        <el-button draggable="true" :disabled="running" @dragstart="onNodeDragStart($event, 'data')" @click="addNode('data')">📥 平台数据集</el-button>
      </section>
      <section class="library-group">
        <h4>数据处理</h4>
        <el-button draggable="true" :disabled="running" @dragstart="onNodeDragStart($event, 'process')" @click="addNode('process')">⚙️ 清洗与治理</el-button>
      </section>
      <section class="library-group">
        <h4>模型实验</h4>
        <el-button draggable="true" :disabled="running" @dragstart="onNodeDragStart($event, 'train')" @click="addNode('train')">🧠 模型训练</el-button>
        <el-button draggable="true" :disabled="running" @dragstart="onNodeDragStart($event, 'eval')" @click="addNode('eval')">📊 模型评估</el-button>
      </section>
      <section class="library-group library-future">
        <h4>后续节点</h4>
        <span>特征工程、模型对比、部署输出、条件判断将在后续阶段接入。</span>
      </section>

      <section class="artifact-settings">
        <div class="artifact-settings-title">
          <h4>运行产物</h4>
          <span>按需保存并命名</span>
        </div>
        <template v-if="hasProcessNode">
          <el-checkbox v-model="workflowOutputOptions.saveDataset">保存清洗后的数据集</el-checkbox>
          <el-input
            v-if="workflowOutputOptions.saveDataset"
            v-model="workflowOutputOptions.datasetName"
            maxlength="128"
            size="small"
            placeholder="保存的数据集名称"
          />
        </template>
        <template v-if="hasTrainNode">
          <el-checkbox v-model="workflowOutputOptions.saveModel" @change="onModelPersistenceChange">保存训练后的模型</el-checkbox>
          <el-input
            v-if="workflowOutputOptions.saveModel"
            v-model="workflowOutputOptions.modelName"
            maxlength="128"
            size="small"
            placeholder="保存的模型名称"
          />
        </template>
        <template v-if="hasEvaluationNode">
          <el-checkbox v-model="workflowOutputOptions.saveEvaluationRecord" :disabled="!workflowOutputOptions.saveModel">保存测试结果到模型评估记录</el-checkbox>
          <el-input
            v-if="workflowOutputOptions.saveEvaluationRecord"
            v-model="workflowOutputOptions.evaluationName"
            maxlength="128"
            size="small"
            placeholder="保存的评测记录名称"
          />
        </template>
        <span v-if="!hasProcessNode && !hasTrainNode && !hasEvaluationNode" class="artifact-empty">添加处理、训练或评估节点后，在这里配置保存方式。</span>
        <span v-else class="artifact-help">未勾选的产物仅供本次流程使用，结束后自动清理。</span>
      </section>
    </aside>
    <!-- Flow Canvas -->
    <div class="flow-canvas" @dragover.prevent="onCanvasDragOver" @drop="onCanvasDrop">
      <div class="canvas-toolbar">
        <span>从左侧拖入节点；仅相同类型的端口可连接。点击连线可断开。</span>
        <span v-if="nodes.length">{{ nodes.length }} 个节点 · {{ edges.length }} 条连接</span>
      </div>
      <VueFlow
        id="pipeline-flow"
        v-model:nodes="nodes"
        v-model:edges="edges"
        :default-viewport="{ zoom: 1 }"
        :min-zoom="0.2"
        :max-zoom="4"
        @node-click="onNodeClick"
        @edge-click="onEdgeClick"
        @pane-click="clearCanvasSelection"
        @connect="onConnect"
        fit-view-on-init
      >
        <Background />
        <Controls />
        <MiniMap />

        <template #node-custom="nodeProps">
          <div class="custom-node" :class="['node-' + nodeProps.data.type, nodeProps.data.runStatus ? 'run-' + nodeProps.data.runStatus : '', nodeProps.data.validationErrors?.length ? 'node-invalid' : '']">
            <div class="node-header">
              <span>{{ nodeProps.data.icon }}</span>
              <span>{{ nodeProps.data.label }}</span>
              <el-tag v-if="nodeProps.data.runStatus" size="small" :type="statusTagType(nodeProps.data.runStatus)">
                {{ statusLabel(nodeProps.data.runStatus) }}
              </el-tag>
              <el-tag v-if="nodeProps.data.validationErrors?.length" size="small" type="danger">待修正</el-tag>
              <el-button size="small" link @click="removeNode(nodeProps.id)">✕</el-button>
            </div>
            <div class="node-body">
              <div v-if="nodeProps.data.type==='data'" class="node-config">
                <el-select
                  v-model="nodeProps.data.dataset"
                  placeholder="选择数据集"
                  size="small"
                  style="width:100%"
                  @change="loadDataNodeColumns(nodeProps.data)"
                >
                  <el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name}`" :value="d.id"/>
                </el-select>
              </div>
              <div v-if="nodeProps.data.type==='process'" class="node-config">
                <el-select v-model="nodeProps.data.operation" placeholder="选择处理方式" size="small" style="width:100%">
                  <el-option label="基础数据治理" value="govern"/>
                  <el-option label="执行自定义清洗规则" value="clean_rule"/>
                </el-select>
                <template v-if="nodeProps.data.operation === 'clean_rule'">
                  <el-select
                    v-model="nodeProps.data.cleaningRuleId"
                    placeholder="选择已启用的清洗规则"
                    size="small"
                    style="width:100%"
                  >
                    <el-option
                      v-for="rule in activeCleaningRules"
                      :key="rule.id"
                      :label="'#' + rule.id + ' ' + rule.name + '（' + (rule.operation_count || 0) + ' 步）'"
                      :value="rule.id"
                    />
                  </el-select>
                  <span v-if="!activeCleaningRules.length" class="node-rule-hint">
                    暂无已启用规则，请先在数据清洗中新建并启用规则
                  </span>
                </template>
              </div>
              <div v-if="nodeProps.data.type==='train'" class="node-config">
                <el-select
                  v-model="nodeProps.data.algorithm"
                  placeholder="选择训练算法"
                  size="small"
                  style="width:100%"
                  @change="onPipelineAlgorithmChange(nodeProps.data, nodeProps.id)"
                >
                  <el-option-group label="平台内置算法">
                    <el-option label="RandomForest" value="rf"/>
                    <el-option label="XGBoost" value="xgb"/>
                    <el-option label="LightGBM" value="lgbm"/>
                    <el-option label="MLP" value="mlp"/>
                  </el-option-group>
                  <el-option-group v-if="trainableAlgorithms.length" label="上传且可训练的算法">
                    <el-option
                      v-for="algorithm in trainableAlgorithms"
                      :key="algorithm.id"
                      :label="'#' + algorithm.id + ' ' + algorithm.name + '（' + algorithm.label + '）'"
                      :value="'uploaded:' + algorithm.id"
                    />
                  </el-option-group>
                </el-select>
                <template v-if="selectedUploadedAlgorithm(nodeProps.data.algorithm)?.kind === 'uploaded_source'">
                  <el-select
                    v-model="nodeProps.data.targetColumn"
                    placeholder="选择预测目标列"
                    clearable
                    filterable
                    :disabled="!targetColumnsForNode(nodeProps.id).length"
                    size="small"
                    style="margin-top:4px; width:100%"
                  >
                    <el-option
                      v-for="column in targetColumnsForNode(nodeProps.id)"
                      :key="column"
                      :label="column"
                      :value="column"
                    />
                  </el-select>
                  <el-select
                    v-model="nodeProps.data.featureColumns"
                    multiple
                    collapse-tags
                    collapse-tags-tooltip
                    clearable
                    filterable
                    :disabled="!targetColumnsForNode(nodeProps.id).length"
                    placeholder="特征列（可选，留空自动选择）"
                    size="small"
                    style="margin-top:4px; width:100%"
                  >
                    <el-option
                      v-for="column in featureColumnsForNode(nodeProps.id, nodeProps.data.targetColumn)"
                      :key="`feature-${column}`"
                      :label="column"
                      :value="column"
                    />
                  </el-select>
                  <span v-if="!targetColumnsForNode(nodeProps.id).length" class="node-rule-hint">
                    请先连接数据节点并选择数据集，才能选择预测目标列
                  </span>
                  <el-collapse
                    v-if="selectedUploadedAlgorithm(nodeProps.data.algorithm)?.parameter_candidates?.length"
                    class="pipeline-param-collapse"
                  >
                    <el-collapse-item :title="'参数配置（已识别 ' + selectedUploadedAlgorithm(nodeProps.data.algorithm).parameter_candidates.length + ' 项）'">
                      <div
                        v-for="candidate in selectedUploadedAlgorithm(nodeProps.data.algorithm).parameter_candidates"
                        :key="candidate.key"
                        class="pipeline-param-field"
                      >
                        <span>{{ candidate.label || candidate.key }}</span>
                        <el-switch
                          v-if="candidate.type === 'boolean'"
                          v-model="nodeProps.data.parameterValues[candidate.key]"
                        />
                        <el-select
                          v-else-if="candidate.type === 'select'"
                          v-model="nodeProps.data.parameterValues[candidate.key]"
                          size="small"
                        >
                          <el-option
                            v-for="option in candidate.options || []"
                            :key="String(option)"
                            :label="String(option)"
                            :value="option"
                          />
                        </el-select>
                        <el-input-number
                          v-else-if="candidate.type === 'number' || candidate.type === 'integer'"
                          v-model="nodeProps.data.parameterValues[candidate.key]"
                          :step="candidate.type === 'integer' ? 1 : 0.001"
                          :precision="candidate.type === 'integer' ? 0 : 4"
                          controls-position="right"
                          size="small"
                        />
                        <el-input
                          v-else
                          :model-value="formatParameterValue(nodeProps.data.parameterValues[candidate.key])"
                          :placeholder="candidate.type === 'array' ? 'JSON 数组' : ''"
                          size="small"
                          @update:model-value="value => setParameterValue(nodeProps.data.parameterValues, candidate, value)"
                        />
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </template>
                <el-input
                  v-model="nodeProps.data.params"
                  placeholder="高级参数覆盖（可选）：epochs=100"
                  size="small"
                  style="margin-top:4px"
                />
                <span v-if="selectedUploadedAlgorithm(nodeProps.data.algorithm)" class="node-rule-hint">
                  未修改的识别参数，以及未填写的目标列，均使用源码默认配置
                </span>
              </div>
              <div v-if="nodeProps.data.type==='eval'" class="node-config">
                <el-select
                  v-model="nodeProps.data.evaluationDatasetId"
                  placeholder="选择独立测试数据集"
                  size="small"
                  filterable
                  style="width:100%"
                  @change="loadEvaluationDatasetColumns(nodeProps.data)"
                >
                  <el-option v-for="d in datasets" :key="d.id" :label="'#' + d.id + ' ' + d.name" :value="d.id"/>
                </el-select>
                <el-select
                  v-model="nodeProps.data.evaluationTargetColumn"
                  placeholder="测试集预测目标列（可选，默认最后一列）"
                  clearable
                  filterable
                  :disabled="!nodeProps.data.evaluationDataColumns.length"
                  size="small"
                  style="margin-top:4px; width:100%"
                >
                  <el-option v-for="column in nodeProps.data.evaluationDataColumns" :key="'eval-target-' + column" :label="column" :value="column"/>
                </el-select>
                <el-select
                  v-model="nodeProps.data.evaluationFeatureColumns"
                  multiple
                  collapse-tags
                  collapse-tags-tooltip
                  clearable
                  filterable
                  :disabled="!nodeProps.data.evaluationDataColumns.length"
                  placeholder="测试集特征列（可选，留空自动选择）"
                  size="small"
                  style="margin-top:4px; width:100%"
                >
                  <el-option v-for="column in evaluationFeatureColumnsForNode(nodeProps.data)" :key="'eval-feature-' + column" :label="column" :value="column"/>
                </el-select>
                <el-select
                  v-model="nodeProps.data.metrics"
                  :placeholder="evaluationTaskKind(nodeProps.id) === 'classification' ? '选择分类评估指标' : '选择回归评估指标'"
                  size="small"
                  multiple
                  style="margin-top:4px; width:100%"
                >
                  <el-option
                    v-for="option in evaluationMetricOptions(nodeProps.id)"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
                <span class="node-rule-hint">测试集将完整参与评估，不会再被二次切分。</span>
              </div>
            </div>
            <div v-if="nodeProps.data.runStatus === 'success' || nodeProps.data.lastError" class="node-result-preview" :class="{ 'is-error': nodeProps.data.lastError }">
              {{ nodeResultSummary(nodeProps.data, nodeProps.id) }}
            </div>
            <div class="node-port-summary">
              <span>输入：{{ nodeInputActual(nodeProps.data, nodeProps.id) }}</span>
              <span>输出：{{ nodeOutputActual(nodeProps.data, nodeProps.id) }}</span>
            </div>
            <Handle
              v-if="nodeInputPortType(nodeProps.data.type)"
              type="target"
              :id="'input-' + nodeInputPortType(nodeProps.data.type)"
              :position="Position.Top"
              :class="'port-' + nodeInputPortType(nodeProps.data.type)"
            />
            <Handle
              v-if="nodeOutputPortType(nodeProps.data.type)"
              type="source"
              :id="'output-' + nodeOutputPortType(nodeProps.data.type)"
              :position="Position.Bottom"
              :class="'port-' + nodeOutputPortType(nodeProps.data.type)"
            />
          </div>
        </template>
      </VueFlow>
    </div>

    <!-- Properties Panel -->
    <div class="props-panel" v-if="selectedNode || selectedEdge">
      <template v-if="selectedEdge">
        <h3>连接属性</h3>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="上游节点">{{ nodeLabel(selectedEdge.source) }}</el-descriptions-item>
          <el-descriptions-item label="下游节点">{{ nodeLabel(selectedEdge.target) }}</el-descriptions-item>
          <el-descriptions-item label="传递内容">{{ edgeTransferLabel(selectedEdge) }}</el-descriptions-item>
        </el-descriptions>
        <el-alert
          class="edge-help"
          type="info"
          :closable="false"
          title="点击画布中的连线后，可在这里断开。断开后两个节点将不再传递数据或模型。"
        />
        <el-button type="danger" plain style="width:100%" :disabled="running" @click="removeEdge(selectedEdge.id)">
          断开连接
        </el-button>
      </template>
      <template v-else-if="selectedNode">
        <h3>节点属性</h3>
        <el-form label-width="80px" size="small">
          <el-form-item label="名称">
            <el-input v-model="selectedNode.data.label"/>
          </el-form-item>
          <el-form-item label="类型">
            <el-tag>{{ selectedNode.data.type }}</el-tag>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="selectedNode.data.description" type="textarea" :rows="2"/>
          </el-form-item>
          <el-form-item v-if="requiresDatasetInput(selectedNode.data.type)" label="数据来源">
            <el-select v-model="selectedNode.data.inputBindings.datasetFrom" :disabled="!inputCandidates(selectedNode.id, 'dataset').length" placeholder="选择上游数据集节点" style="width:100%">
              <el-option v-for="candidate in inputCandidates(selectedNode.id, 'dataset')" :key="candidate.id" :label="candidate.label" :value="candidate.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="requiresModelInput(selectedNode.data.type)" label="模型来源">
            <el-select v-model="selectedNode.data.inputBindings.modelFrom" :disabled="!inputCandidates(selectedNode.id, 'model').length" placeholder="选择上游训练节点" style="width:100%">
              <el-option v-for="candidate in inputCandidates(selectedNode.id, 'model')" :key="candidate.id" :label="candidate.label" :value="candidate.id" />
            </el-select>
          </el-form-item>
        </el-form>
        <el-divider/>
        <h4>输入与输出</h4>
        <el-descriptions :column="1" border size="small" class="node-io">
          <el-descriptions-item label="实际输入">{{ nodeInputActual(selectedNode.data, selectedNode.id) }}</el-descriptions-item>
          <el-descriptions-item label="实际输出">{{ nodeOutputActual(selectedNode.data, selectedNode.id) }}</el-descriptions-item>
          <el-descriptions-item label="节点来源">{{ upstreamNodeLabels(selectedNode.id) || '未连接' }}</el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="selectedNode.data.validationErrors?.length"
          class="node-validation-alert"
          type="error"
          :closable="false"
          :title="selectedNode.data.validationErrors.join('；')"
        />
        <el-divider/>
        <h4>最近节点结果</h4>
        <pre v-if="selectedNode.data.lastOutput || selectedNode.data.lastError" class="node-output-detail">{{ nodeResultDetail(selectedNode.data) }}</pre>
        <div v-else class="empty-log">该节点尚未运行</div>
        <el-divider/>
        <h4>运行日志</h4>
        <div class="run-log" v-if="runLog.length">
          <div v-for="(log,i) in runLog" :key="i" :class="'log-' + log.level">{{ log.msg }}</div>
        </div>
        <div v-else class="empty-log">点击运行查看日志</div>
      </template>
    </div>
  </div>

  <el-drawer v-model="showTemplateLibrary" title="流程模板库" size="760px">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="模板保留节点、连线和当前配置；加载后可重新选择数据集、算法、参数和产物名称。仅当对应流程运行成功后，模板才会进入此库；模板本身不保存模型、数据或运行结果。"
      style="margin-bottom:14px"
    />
    <el-table :data="workflowTemplates" empty-text="暂无流程模板">
      <el-table-column prop="name" label="模板名称" min-width="220" show-overflow-tooltip />
      <el-table-column label="流程结构" min-width="180">
        <template #default="scope">{{ (scope.row.tasks || []).length }} 个节点 · {{ templateEdgeCount(scope.row) }} 条连接</template>
      </el-table-column>
      <el-table-column label="创建时间" min-width="170">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="scope">
          <el-button link type="primary" @click="loadSavedTemplate(scope.row)">加载</el-button>
          <el-button link type="danger" @click="deleteWorkflowTemplate(scope.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-drawer>

  <el-dialog v-model="showWorkflowImport" title="导入工作流封装" width="650px" destroy-on-close>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="支持完整工作流 ZIP、pipeline.json，以及本平台导出的 API/SDK Python 文件。导入只恢复节点、连接和配置，需由您确认后才会加载到画布，不会自动运行。"
      style="margin-bottom:14px"
    />
    <el-upload
      drag
      :auto-upload="false"
      :limit="1"
      accept=".zip,.json,.py"
      :on-change="inspectWorkflowImport"
      :on-remove="clearWorkflowImport"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">拖入封装文件，或 <em>点击选择</em></div>
      <template #tip><div class="el-upload__tip">仅接收 ZIP、JSON 或 Python 文件，单个文件不超过 2 MiB。</div></template>
    </el-upload>
    <div class="release-link-import">
      <el-divider>或粘贴工作流复现链接</el-divider>
      <el-input v-model="workflowReleaseUrl" placeholder="粘贴导出的工作流复现链接">
        <template #append><el-button :loading="workflowImportLoading" @click="inspectWorkflowReleaseUrl">识别链接</el-button></template>
      </el-input>
    </div>
    <el-descriptions v-if="workflowImportPreview" :column="1" border size="small" style="margin-top:16px">
      <el-descriptions-item label="来源">{{ workflowImportPreview.source_filename || workflowImportPreview.release?.release_id || '-' }}</el-descriptions-item>
      <el-descriptions-item label="识别结果">{{ workflowImportPreview.source_type }}</el-descriptions-item>
      <el-descriptions-item label="流程名称">{{ workflowImportPreview.definition?.name || workflowImportPreview.release?.name || '未命名工作流' }}</el-descriptions-item>
      <el-descriptions-item label="流程结构">{{ workflowImportPreview.node_count || 0 }} 个节点 · {{ workflowImportPreview.definition ? templateEdgeCount(workflowImportPreview.definition) : '-' }} 条连接</el-descriptions-item>
    </el-descriptions>
    <template #footer>
      <el-button @click="showWorkflowImport=false">取消</el-button>
      <el-button type="primary" :disabled="!workflowImportPreview" :loading="workflowImportLoading" @click="applyWorkflowImport">加载到画布</el-button>
    </template>
  </el-dialog>

  <el-drawer v-model="showRuns" title="Pipeline 历史运行记录" size="88%">
    <el-table :data="runHistory" v-loading="runsLoading" empty-text="暂无运行记录">
      <el-table-column prop="pipeline_name" label="流水线" min-width="170" show-overflow-tooltip />
      <el-table-column prop="id" label="Run ID" width="85" />
      <el-table-column prop="status" label="状态" width="95">
        <template #default="scope">
          <el-tag :type="historyStatusType(scope.row.status)">{{ historyStatusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="实验指标" min-width="330">
        <template #default="scope">
          <div v-if="metricEntries(scope.row).length" class="history-metrics">
            <span v-for="metric in metricEntries(scope.row)" :key="metric[0]">
              {{ metricLabel(metric[0]) }} {{ formatMetricValue(metric[1]) }}
            </span>
          </div>
          <span v-else class="muted-text">暂无指标</span>
        </template>
      </el-table-column>
      <el-table-column label="模型 ID" width="95">
        <template #default="scope">{{ artifactValue(scope.row, 'model_id') || '-' }}</template>
      </el-table-column>
      <el-table-column label="耗时" width="85">
        <template #default="scope">{{ formatDuration(scope.row.duration_seconds) }}</template>
      </el-table-column>
      <el-table-column label="创建时间" min-width="165">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="225" fixed="right">
        <template #default="scope">
          <el-button link type="primary" @click="openRunDetail(scope.row)">查看结果</el-button>
          <el-button
            link type="primary"
            :loading="downloadingPipelineId === Number(scope.row.pipeline_id)"
            @click="downloadHistoryWorkflow(scope.row)"
          >下载工作流</el-button>
          <el-button
            v-if="['PENDING', 'RUNNING'].includes(String(scope.row.status).toUpperCase())"
            link type="primary" @click="resumeHistoryRun(scope.row)"
          >恢复</el-button>
          <el-button
            v-else link type="danger" @click="deleteHistoryRun(scope.row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-alert class="run-center-note" type="info" :closable="false" show-icon>
      这里仅保留执行成功的历史运行；实验指标取自每次运行的模型评估节点。
    </el-alert>
  </el-drawer>

  <el-dialog
    v-model="showRunDetail"
    :title="selectedHistoryRun ? `运行结果 · Run #${selectedHistoryRun.id}` : '运行结果'"
    width="820px"
  >
    <template v-if="selectedHistoryRun">
      <el-descriptions :column="3" border class="run-summary">
        <el-descriptions-item label="流水线">{{ selectedHistoryRun.pipeline_name }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="historyStatusType(selectedHistoryRun.status)">
            {{ historyStatusLabel(selectedHistoryRun.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="耗时">{{ formatDuration(selectedHistoryRun.duration_seconds) }}</el-descriptions-item>
        <el-descriptions-item label="数据集 ID">{{ artifactValue(selectedHistoryRun, 'dataset_id') || '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型 ID">{{ artifactValue(selectedHistoryRun, 'model_id') || '-' }}</el-descriptions-item>
        <el-descriptions-item label="训练任务">{{ artifactValue(selectedHistoryRun, 'job_id') || '-' }}</el-descriptions-item>
      </el-descriptions>

      <h4 class="result-title">实验指标</h4>
      <div v-if="metricEntries(selectedHistoryRun).length" class="metric-cards">
        <div v-for="metric in metricEntries(selectedHistoryRun)" :key="metric[0]" class="metric-card">
          <span>{{ metricLabel(metric[0]) }}</span>
          <strong>{{ formatMetricValue(metric[1]) }}</strong>
        </div>
      </div>
      <el-empty v-else description="该运行没有产生实验指标" :image-size="70" />

      <h4 class="result-title">节点结果</h4>
      <el-table :data="selectedHistoryRun.task_runs || []" border size="small">
        <el-table-column prop="task_name" label="节点" min-width="120" />
        <el-table-column label="类型" min-width="120">
          <template #default="scope">{{ taskTypeLabel(scope.row.task_type) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="scope">
            <el-tag :type="historyStatusType(scope.row.status)" size="small">
              {{ historyStatusLabel(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输出" min-width="300">
          <template #default="scope"><div class="task-output-text">{{ taskOutputSummary(scope.row) }}</div></template>
        </el-table-column>
      </el-table>
      <el-alert
        v-if="selectedHistoryRun.error_message"
        class="run-error"
        type="error"
        :title="selectedHistoryRun.error_message"
        :closable="false"
      />
    </template>
  </el-dialog>

  <el-dialog v-model="showValidation" title="运行前检查" width="620px">
    <el-result
      v-if="!validationIssues.length"
      icon="success"
      title="检查通过"
      sub-title="节点配置与连接关系均满足当前流水线的运行条件。"
    />
    <template v-else>
      <el-alert type="warning" :closable="false" show-icon title="请先修正以下问题，再运行流水线。" />
      <el-table :data="validationIssues" size="small" style="margin-top:14px">
        <el-table-column prop="nodeLabel" label="节点" width="150" />
        <el-table-column prop="message" label="需要处理的问题" />
      </el-table>
    </template>
    <template #footer><el-button @click="showValidation=false">关闭</el-button></template>
  </el-dialog>

  <!-- Reproducible workflow export dialog -->
  <el-dialog v-model="showExport" :title="exportFormat === 'api' ? '🔗 工作流复现链接' : '📦 工作流复现 SDK'" width="700px">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      :title="exportFormat === 'api' ? '此链接对应一份不可变的工作流快照，包含流程配置、训练数据快照和上传算法快照。接收方在平台的“导入流程”中粘贴链接即可复制并重新训练。' : 'SDK 文件指向同一份工作流快照。接收方在“导入流程”上传此文件即可恢复流程、数据和算法快照。'"
      style="margin-bottom:14px"
    />
    <div v-if="exportFormat === 'api' && exportApiEndpoint" class="api-endpoint-panel">
      <div class="api-endpoint-label">工作流复现链接</div>
      <el-input :model-value="exportApiEndpoint" readonly>
        <template #append><el-button @click="copyApiEndpoint">复制链接</el-button></template>
      </el-input>
      <div class="api-endpoint-tip">这是可重新导入的复现链接，不是内部运行接口，也不需要展示 API 源码。</div>
    </div>
    <pre v-if="exportFormat === 'sdk'" class="code-block"><code>{{ exportCode }}</code></pre>
    <template #footer>
      <el-button @click="showExport=false">关闭</el-button>
      <el-button v-if="exportFormat === 'sdk'" type="success" @click="downloadReproducibleSdk">⬇ 下载 SDK</el-button>
      <el-button v-if="exportFormat === 'sdk'" type="primary" @click="copyCode">📋 复制 SDK</el-button>
    </template>
  </el-dialog>
</div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del, downloadFile, get, post, postForm, put } from '@/api/legacy'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { Handle, Position } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const nodes = ref<any[]>([])
const edges = ref<any[]>([])
const { screenToFlowCoordinate } = useVueFlow('pipeline-flow')
const selectedNode = ref<any>(null)
const selectedEdge = ref<any>(null)
const running = ref(false)
const workflowOutputOptions = reactive({
  saveDataset: true,
  saveModel: true,
  saveEvaluationRecord: true,
  datasetName: '',
  modelName: '',
  evaluationName: '',
})
const currentRunId = ref<number | null>(null)
const currentRunStatus = ref<RunStatus | ''>('')
const runLog = ref<any[]>([])
const showExport = ref(false)
const exportFormat = ref('api')
const exportCode = ref('')
const exportApiEndpoint = ref('')
const exportPipelineId = ref<number | null>(null)
const apiTestLoading = ref(false)
const apiTestResult = ref<any>(null)
const datasets = ref<any[]>([])
const cleaningRules = ref<any[]>([])
const activeCleaningRules = computed(() => cleaningRules.value.filter(rule => rule?.is_active !== false))
const trainableAlgorithms = ref<any[]>([])
const persistedPipelineId = ref<number | null>(null)
const persistedSignature = ref('')
const showRuns = ref(false)
const showTemplateLibrary = ref(false)
const showWorkflowImport = ref(false)
const workflowImportLoading = ref(false)
const workflowImportPreview = ref<any>(null)
const workflowReleaseUrl = ref('')
const runsLoading = ref(false)
const runHistory = ref<any[]>([])
const showRunDetail = ref(false)
const selectedHistoryRun = ref<any>(null)
const workflowDownloading = ref(false)
const downloadingPipelineId = ref<number | null>(null)
const workflowName = ref('未命名工作流')
const workflowTemplates = ref<any[]>([])
const exportFormatLabel = computed(() => exportFormat.value === 'api' ? ' API' : ' SDK')
const showValidation = ref(false)
const validationIssues = ref<any[]>([])
let nodeId = 0

type RunStatus = 'pending' | 'running' | 'success' | 'failed' | 'skipped'

interface Graph {
  order: any[]
  incoming: Map<string, string[]>
}

const hasProcessNode = computed(() => nodes.value.some((node: any) => node.data?.type === 'process'))
const hasTrainNode = computed(() => nodes.value.some((node: any) => node.data?.type === 'train'))
const hasEvaluationNode = computed(() => nodes.value.some((node: any) => node.data?.type === 'eval'))

const nodeTypeConfig: Record<string, any> = {
  data: { icon: '📥', label: '数据输入', color: '#3b82f6' },
  process: { icon: '⚙️', label: '数据处理', color: '#f59e0b' },
  train: { icon: '🧠', label: '模型训练', color: '#10b981' },
  eval: { icon: '📊', label: '模型评估', color: '#8b5cf6' },
}

function nodeInputLabel(type: string) {
  return {
    data: '无（选择平台数据集）',
    process: '数据集',
    train: '数据集',
    eval: '已训练模型 + 独立测试集',
  }[type] || '未定义'
}

function nodeOutputLabel(type: string) {
  return {
    data: '数据集 + 字段清单',
    process: '清洗后数据集 + 清洗摘要',
    train: '模型 + 训练指标 + 日志',
    eval: '测试指标 + 评估结果',
  }[type] || '未定义'
}

function savedArtifactName(value: any, kind: string): string {
  const name = String(value || '').trim()
  if (!name) throw new Error('请为要保存的' + kind + '填写名称')
  return name
}

function validateSavedArtifactNames() {
  if (hasProcessNode.value && workflowOutputOptions.saveDataset) {
    savedArtifactName(workflowOutputOptions.datasetName, '数据集')
  }
  if (hasTrainNode.value && workflowOutputOptions.saveModel) {
    savedArtifactName(workflowOutputOptions.modelName, '模型')
  }
  if (hasEvaluationNode.value && workflowOutputOptions.saveEvaluationRecord) {
    savedArtifactName(workflowOutputOptions.evaluationName, '评测记录')
  }
}

function onModelPersistenceChange(value: boolean) {
  if (!value && workflowOutputOptions.saveEvaluationRecord) {
    workflowOutputOptions.saveEvaluationRecord = false
    ElMessage.info('未保存模型时，测试记录无法关联到可查询模型，已同时取消保存测试记录')
  }
}

function nodeInputPortType(type: string): string | null {
  return { process: 'dataset', train: 'dataset', eval: 'model' }[type] || null
}

function nodeOutputPortType(type: string): string | null {
  return { data: 'dataset', process: 'dataset', train: 'model' }[type] || null
}

function requiresDatasetInput(type: string) {
  return nodeInputPortType(type) === 'dataset'
}

function requiresModelInput(type: string) {
  return nodeInputPortType(type) === 'model'
}

function ensureNodeData(node: any) {
  if (!node?.data) return
  if (!node.data.inputBindings || typeof node.data.inputBindings !== 'object') {
    node.data.inputBindings = { datasetFrom: '', modelFrom: '' }
  }
  if (!Array.isArray(node.data.validationErrors)) node.data.validationErrors = []
}

function inputCandidates(nodeId: string, resourceType: string) {
  return edges.value
    .filter((edge: any) => edge.target === nodeId)
    .map((edge: any) => nodes.value.find((node: any) => node.id === edge.source))
    .filter((node: any) => node && nodeOutputPortType(node.data?.type) === resourceType)
    .map((node: any) => ({ id: node.id, label: nodeLabel(node.id) }))
}

function setDefaultInputBinding(nodeId: string) {
  const node = nodes.value.find((item: any) => item.id === nodeId)
  if (!node) return
  ensureNodeData(node)
  const inputType = nodeInputPortType(node.data.type)
  if (!inputType) return
  const candidates = inputCandidates(nodeId, inputType)
  const bindingKey = inputType === 'dataset' ? 'datasetFrom' : 'modelFrom'
  const selected = String(node.data.inputBindings[bindingKey] || '')
  if (!candidates.some((candidate: any) => candidate.id === selected)) {
    node.data.inputBindings[bindingKey] = candidates.length === 1 ? candidates[0].id : ''
  }
}

const regressionMetricOptions = [
  { label: '均方误差（MSE）', value: 'mse' },
  { label: '均方根误差（RMSE）', value: 'rmse' },
  { label: '平均绝对误差（MAE）', value: 'mae' },
  { label: '决定系数（R²）', value: 'r2' },
]
const classificationMetricOptions = [
  { label: '准确率', value: 'accuracy' },
  { label: '精确率', value: 'precision' },
  { label: '召回率', value: 'recall' },
  { label: 'F1', value: 'f1' },
]

function evaluationTaskKind(nodeId: string): 'regression' | 'classification' {
  const evaluationNode = nodes.value.find((node: any) => node.id === nodeId)
  const selectedSource = String(evaluationNode?.data?.inputBindings?.modelFrom || '')
  const sourceId = selectedSource || edges.value.find((edge: any) => edge.target === nodeId)?.source
  const trainingNode = nodes.value.find((node: any) => node.id === sourceId)
  const uploaded = selectedUploadedAlgorithm(trainingNode?.data?.algorithm)
  const taskType = String(uploaded?.task_type || trainingNode?.data?.taskType || '').toLowerCase()
  return taskType === 'classification' ? 'classification' : 'regression'
}

function evaluationMetricOptions(nodeId: string) {
  return evaluationTaskKind(nodeId) === 'classification'
    ? classificationMetricOptions
    : regressionMetricOptions
}

function normalizeEvaluationMetrics(node: any) {
  if (!node || node.data?.type !== 'eval') return
  const allowed = new Set(evaluationMetricOptions(node.id).map((option: any) => option.value))
  const selected = Array.isArray(node.data.metrics)
    ? node.data.metrics.map((metric: any) => String(metric).toLowerCase()).filter((metric: string) => allowed.has(metric))
    : []
  node.data.metrics = selected.length
    ? [...new Set(selected)]
    : evaluationMetricOptions(node.id).map((option: any) => option.value)
}

function edgeTransferLabel(edge: any) {
  const source = nodes.value.find((node: any) => node.id === edge?.source)
  const target = nodes.value.find((node: any) => node.id === edge?.target)
  const output = nodeOutputPortType(source?.data?.type)
  const input = nodeInputPortType(target?.data?.type)
  if (output && output === input) return output === 'dataset' ? '数据集' : '已训练模型'
  return '不兼容连接'
}

function metricText(output: any) {
  const metrics = output?.metrics
  if (!metrics || typeof metrics !== 'object') return ''
  const entries = Object.entries(metrics).filter(([, value]) => value !== null && value !== undefined)
  return entries.map(([key, value]) => metricLabel(key) + ' ' + formatMetricValue(value)).join('，')
}

function datasetText(datasetId: any, recordedName?: any) {
  const name = String(recordedName || '').trim()
  if (datasetId === null || datasetId === undefined || datasetId === '') {
    return name ? '数据集 · ' + name : '未指定数据集'
  }
  const dataset = datasets.value.find((item: any) => String(item.id) === String(datasetId))
  const displayName = name || dataset?.name || ''
  return '数据集 #' + datasetId + (displayName ? ' · ' + displayName : '')
}

function modelText(modelId: any, recordedName?: any) {
  const name = String(recordedName || '').trim()
  if (modelId === null || modelId === undefined || modelId === '') {
    return name ? '模型 · ' + name : '未指定模型'
  }
  return '模型 #' + modelId + (name ? ' · ' + name : '')
}

function runtimeStateText(status: any) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'pending') return '等待上游节点'
  if (normalized === 'running') return '正在执行'
  if (normalized === 'skipped') return '已跳过'
  if (normalized === 'failed') return '执行失败'
  return '尚未运行'
}

function nodeInputActual(data: any, nodeId: string) {
  if (data?.type === 'data') return datasetText(data?.dataset)
  const inputType = nodeInputPortType(data?.type)
  if (!inputType) return '无'
  const bindingKey = inputType === 'dataset' ? 'datasetFrom' : 'modelFrom'
  const selectedSource = String(data?.inputBindings?.[bindingKey] || '')
  const sourceId = selectedSource || edges.value.find((edge: any) => edge.target === nodeId)?.source
  const source = nodes.value.find((node: any) => node.id === sourceId)
  if (!source) return '未连接上游节点'
  const output = source.data?.lastOutput || {}
  if (inputType === 'dataset') {
    const datasetId = output.dataset_id ?? source.data?.dataset
    return nodeLabel(source.id) + ' → ' + datasetText(datasetId, output.dataset_name)
  }
  const modelId = output.model_id
  return nodeLabel(source.id) + (modelId ? ' → ' + modelText(modelId, output.model_name) : '（' + runtimeStateText(source.data?.runStatus) + '）')
}

function nodeOutputActual(data: any, nodeId?: string) {
  if (data?.lastError) return '失败：' + String(data.lastError)
  const status = String(data?.runStatus || '').toLowerCase()
  if (status !== 'success') return runtimeStateText(status)
  const output = data?.lastOutput || {}
  if (data?.type === 'data') {
    return datasetText(output.dataset_id ?? data?.dataset, output.dataset_name) + '，' + (output.row_count ?? '-') + ' 行，' + (output.column_count ?? '-') + ' 列'
  }
  if (data?.type === 'process') {
    const change = '清洗 ' + (output.removed_rows ?? 0) + ' 行，' + (output.removed_columns ?? 0) + ' 列'
    return datasetText(output.dataset_id, output.dataset_name) + '，' + change
  }
  if (data?.type === 'train') {
    const identifiers = modelText(output.model_id, output.model_name) + '，训练任务 ' + (output.job_id ?? '-')
    return identifiers + (metricText(output) ? '；' + metricText(output) : '')
  }
  if (data?.type === 'eval') {
    return datasetText(output.dataset_id, output.dataset_name) + '，' + (metricText(output) || '测试已完成')
  }
  return '已完成'
}

const runProgress = computed(() => {
  const total = nodes.value.length
  const finished = nodes.value.filter((node: any) => ['success', 'failed', 'skipped'].includes(String(node.data?.runStatus || '').toLowerCase()))
  const active = nodes.value.filter((node: any) => String(node.data?.runStatus || '').toLowerCase() === 'running')
  const pending = nodes.value.filter((node: any) => String(node.data?.runStatus || '').toLowerCase() === 'pending')
  return {
    total,
    completed: finished.length,
    percent: total ? Math.round((finished.length / total) * 100) : 0,
    activeLabel: active.length
      ? '正在执行：' + active.map((node: any) => node.data.label).join('、')
      : pending.length
        ? '等待执行：' + pending.map((node: any) => node.data.label).join('、')
        : finished.length
          ? '所有节点已结束'
          : '尚未开始',
  }
})

type RuntimeResultItem = { label: string; value: string }

function runtimeResultItems(type: 'train' | 'eval'): RuntimeResultItem[] {
  const node = nodes.value.find((item: any) => item.data?.type === type)
  if (!node) return [{ label: '状态', value: type === 'train' ? '未配置训练节点' : '未配置评估节点' }]
  const data = node.data || {}
  if (data.lastError) return [{ label: '状态', value: '失败：' + String(data.lastError) }]
  if (String(data.runStatus || '').toLowerCase() !== 'success') {
    return [{ label: '状态', value: runtimeStateText(data.runStatus) }]
  }
  const output = data.lastOutput || {}
  const items: RuntimeResultItem[] = []
  if (type === 'train') {
    items.push({ label: '模型', value: modelText(output.model_id, output.model_name) })
    items.push({ label: '训练任务', value: String(output.job_id ?? '-') })
  } else {
    items.push({ label: '测试数据集', value: datasetText(output.dataset_id, output.dataset_name) })
  }
  const metrics = output.metrics && typeof output.metrics === 'object' ? output.metrics : {}
  for (const [key, value] of Object.entries(metrics)) {
    if (value !== null && value !== undefined) {
      items.push({ label: metricLabel(key), value: formatMetricValue(value) })
    }
  }
  return items.length ? items : [{ label: '状态', value: type === 'train' ? '训练已完成' : '测试已完成' }]
}

const currentTrainingResultItems = computed(() => runtimeResultItems('train'))
const currentEvaluationResultItems = computed(() => runtimeResultItems('eval'))

function nodeResultSummary(data: any, nodeId?: string) {
  return nodeOutputActual(data, nodeId)
}

function nodeResultDetail(data: any) {
  if (data?.lastError) return '执行失败\n' + String(data.lastError)
  try {
    return JSON.stringify(data?.lastOutput || {}, null, 2)
  } catch {
    return '节点输出无法展示'
  }
}

function onNodeDragStart(event: DragEvent, type: string) {
  if (running.value) return
  event.dataTransfer?.setData('application/x-foundation-node', type)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function onCanvasDragOver(event: DragEvent) {
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}

function onCanvasDrop(event: DragEvent) {
  const type = event.dataTransfer?.getData('application/x-foundation-node') || ''
  if (!nodeTypeConfig[type] || running.value) return
  const position = screenToFlowCoordinate({ x: event.clientX, y: event.clientY })
  addNode(type, position)
}

function upstreamNodeLabels(nodeId: string) {
  const labels = edges.value
    .filter((edge: any) => edge.target === nodeId)
    .map((edge: any) => nodeLabel(edge.source))
  return labels.join('、')
}

function clearValidationState() {
  validationIssues.value = []
  for (const node of nodes.value) node.data.validationErrors = []
}

function autoLayout() {
  try {
    const graph = buildGraph()
    const levels = new Map<string, number>()
    const groups = new Map<number, any[]>()
    for (const node of graph.order) {
      const parents = graph.incoming.get(node.id) || []
      const level = parents.length
        ? Math.max(...parents.map(parentId => levels.get(parentId) || 0)) + 1
        : 0
      levels.set(node.id, level)
      const group = groups.get(level) || []
      group.push(node)
      groups.set(level, group)
    }
    for (const [level, group] of groups) {
      group.forEach((node: any, index: number) => {
        node.position = { x: 70 + level * 300, y: 90 + index * 220 }
      })
    }
    ElMessage.success('已按执行顺序完成自动排版')
  } catch (error: any) {
    ElMessage.error(error?.message || '自动排版失败')
  }
}

async function loadWorkflowTemplates() {
  try {
    const response: any = await get('/pipelines/templates')
    workflowTemplates.value = Array.isArray(response?.items) ? response.items : []
  } catch {
    workflowTemplates.value = []
  }
}

function clearWorkflowImport() {
  workflowImportPreview.value = null
}

function openWorkflowImport() {
  clearWorkflowImport()
  workflowReleaseUrl.value = ''
  showWorkflowImport.value = true
}

async function inspectWorkflowReleaseUrl() {
  const raw = String(workflowReleaseUrl.value || '').trim()
  const matched = raw.match(/\/api\/v1\/pipelines\/reproducible-releases\/([0-9a-f-]{36})(?:[/?#].*)?$/i)
  if (!matched) {
    ElMessage.error('请输入平台导出的完整工作流复现链接')
    return
  }
  workflowImportLoading.value = true
  clearWorkflowImport()
  try {
    const summary: any = await get('/pipelines/reproducible-releases/' + matched[1])
    workflowImportPreview.value = {
      source_type: '工作流复现链接',
      release_token: matched[1],
      node_count: summary?.node_count || 0,
      release: summary,
    }
    ElMessage.success('已识别复现链接，请确认后导入')
  } catch (error: any) {
    ElMessage.error(error?.message || '复现链接无效或已失效')
  } finally {
    workflowImportLoading.value = false
  }
}

async function inspectWorkflowImport(uploadFile: any) {
  const file = uploadFile?.raw as File | undefined
  clearWorkflowImport()
  if (!file) return
  const filename = String(file.name || '')
  if (!/\.(zip|json|py)$/i.test(filename)) {
    ElMessage.error('仅支持 ZIP、JSON 或 Python 格式的工作流封装')
    return
  }
  if (file.size > 2 * 1024 * 1024) {
    ElMessage.error('工作流封装文件不能超过 2 MiB')
    return
  }
  workflowImportLoading.value = true
  try {
    const form = new FormData()
    form.append('file', file, filename)
    const preview: any = await postForm('/pipelines/import-definition', form)
    if (!preview?.definition || !Array.isArray(preview.definition.tasks)) {
      throw new Error('封装文件中没有可加载的流程定义')
    }
    workflowImportPreview.value = preview
    ElMessage.success('已识别流程定义，请确认后加载到画布')
  } catch (error: any) {
    ElMessage.error(error?.message || '解析工作流封装失败')
  } finally {
    workflowImportLoading.value = false
  }
}

async function applyWorkflowImport() {
  const preview = workflowImportPreview.value
  if (!preview?.definition) return
  if (nodes.value.length) {
    try {
      await ElMessageBox.confirm(
        '加载导入流程会替换当前画布的节点、连接和配置。当前画布尚未保存的内容将丢失，是否继续？',
        '加载导入流程',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  workflowImportLoading.value = true
  try {
    const imported: any = preview.release_token
      ? await post('/pipelines/reproducible-releases/' + preview.release_token + '/import', {})
      : { definition: preview.definition }
    await applySavedWorkflowTemplate(imported.definition)
    showWorkflowImport.value = false
    ElMessage.success(preview.release_token
      ? '复现快照已导入：数据集和算法已复制，可修改配置后重新运行'
      : '工作流已加载到画布，可修改配置后再运行')
  } catch (error: any) {
    ElMessage.error(error?.message || '加载工作流失败')
  } finally {
    workflowImportLoading.value = false
  }
}

function clearWorkflowCanvas() {
  nodes.value = []
  edges.value = []
  selectedNode.value = null
  selectedEdge.value = null
  clearValidationState()
  nodeId = 0
}

async function applySavedWorkflowTemplate(template: any) {
  const taskTypeToNodeType: Record<string, string> = {
    data_load: 'data', data_source: 'data', dataset: 'data',
    data_process: 'process', model_train: 'train', train: 'train',
    model_evaluate: 'eval',
  }
  const builtinAlgorithmByModelType: Record<string, string> = {
    random_forest: 'rf', xgboost: 'xgb', lightgbm: 'lgbm', mlp: 'mlp',
  }
  const trainingReservedKeys = new Set([
    'input_bindings', 'save_model', 'name', 'model_type', 'algorithm_id',
    'target_column', 'feature_columns', 'feature_columns_explicit',
    'effective_hyperparameters', 'hyperparameter_source',
  ])
  const serializedAdvancedParams = (config: Record<string, any>) => Object.entries(config)
    .filter(([key]) => !trainingReservedKeys.has(key))
    .map(([key, value]) => key + '=' + (typeof value === 'object' ? JSON.stringify(value) : String(value)))
    .join('\n')
  const tasks = Array.isArray(template?.tasks) ? [...template.tasks] : []
  tasks.sort((left: any, right: any) => Number(left.order_index || 0) - Number(right.order_index || 0))
  if (!tasks.length) throw new Error('该流程模板没有节点')
  clearWorkflowCanvas()
  const mapping = new Map<string, string>()
  const taskByNodeId = new Map<string, any>()
  for (const task of tasks) {
    const type = taskTypeToNodeType[String(task?.task_type || '')]
    if (!type) throw new Error('流程模板包含暂不支持的节点类型：' + String(task?.task_type || '-'))
    addNode(type)
    const node = nodes.value[nodes.value.length - 1]
    mapping.set(String(task.name), node.id)
    taskByNodeId.set(node.id, task)
  }
  for (const task of tasks) {
    const target = mapping.get(String(task.name))
    for (const dependency of Array.isArray(task.dependencies) ? task.dependencies : []) {
      const source = mapping.get(String(dependency))
      if (!source || !target) throw new Error('流程模板连接关系无效')
      const sourceNode = nodes.value.find((node: any) => node.id === source)
      const targetNode = nodes.value.find((node: any) => node.id === target)
      const portType = nodeOutputPortType(sourceNode?.data?.type)
      if (!portType || portType !== nodeInputPortType(targetNode?.data?.type)) {
        throw new Error('流程模板含有不兼容的连接')
      }
      edges.value.push({
        id: 'edge_' + source + '_' + target,
        source, target,
        sourceHandle: 'output-' + portType,
        targetHandle: 'input-' + portType,
        animated: true, selectable: true, interactionWidth: 24,
        style: { stroke: portType === 'model' ? '#8b5cf6' : '#409EFF' },
      })
    }
  }

  for (const node of nodes.value) {
    const task = taskByNodeId.get(node.id)
    const config = task?.config && typeof task.config === 'object' ? task.config : {}
    ensureNodeData(node)
    if (node.data.type === 'data') {
      node.data.dataset = config.dataset_id ?? null
      if (node.data.dataset) await loadDataNodeColumns(node.data)
    } else if (node.data.type === 'process') {
      node.data.operation = String(config.operation || '')
      node.data.cleaningRuleId = config.cleaning_rule_id ?? null
      if (config.save_dataset !== undefined) workflowOutputOptions.saveDataset = Boolean(config.save_dataset)
      if (config.name) workflowOutputOptions.datasetName = String(config.name)
    } else if (node.data.type === 'train') {
      const algorithmId = Number(config.algorithm_id)
      node.data.algorithm = Number.isInteger(algorithmId) && algorithmId > 0
        ? 'uploaded:' + algorithmId
        : (builtinAlgorithmByModelType[String(config.model_type || '')] || '')
      if (node.data.algorithm) onPipelineAlgorithmChange(node.data, node.id)
      node.data.targetColumn = String(config.target_column || '')
      node.data.featureColumns = Array.isArray(config.feature_columns) ? config.feature_columns.map(String) : []
      Object.assign(node.data.parameterValues, config.effective_hyperparameters || {})
      node.data.params = serializedAdvancedParams(config)
      if (config.save_model !== undefined) workflowOutputOptions.saveModel = Boolean(config.save_model)
      if (config.name) workflowOutputOptions.modelName = String(config.name)
    } else if (node.data.type === 'eval') {
      node.data.evaluationDatasetId = config.evaluation_dataset_id ?? null
      if (node.data.evaluationDatasetId) await loadEvaluationDatasetColumns(node.data)
      node.data.evaluationTargetColumn = String(config.target_column || '')
      node.data.evaluationFeatureColumns = Array.isArray(config.feature_columns) ? config.feature_columns.map(String) : []
      node.data.metrics = Array.isArray(config.metrics) ? config.metrics.map(String) : []
      if (config.save_evaluation_record !== undefined) workflowOutputOptions.saveEvaluationRecord = Boolean(config.save_evaluation_record)
      if (config.evaluation_name) workflowOutputOptions.evaluationName = String(config.evaluation_name)
    }
    const bindings = config.input_bindings && typeof config.input_bindings === 'object' ? config.input_bindings : {}
    node.data.inputBindings.datasetFrom = mapping.get(String(bindings.dataset_from || '')) || ''
    node.data.inputBindings.modelFrom = mapping.get(String(bindings.model_from || '')) || ''
  }
  if (!workflowOutputOptions.saveModel) workflowOutputOptions.saveEvaluationRecord = false
  for (const node of nodes.value) setDefaultInputBinding(node.id)
  workflowName.value = String(template.name || '未命名流程模板')
  autoLayout()
}

async function loadTemplate(template: string) {
  if (nodes.value.length) {
    try {
      await ElMessageBox.confirm(
        '从模板创建会清空当前画布。已保存的草稿不会受影响，是否继续？',
        '从模板创建',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  if (String(template).startsWith('saved:')) {
    const id = Number(String(template).slice('saved:'.length))
    const saved = workflowTemplates.value.find((item: any) => Number(item.id) === id)
    if (!saved) {
      ElMessage.error('流程模板不存在或已无权访问，请刷新后重试')
      return
    }
    try {
      await applySavedWorkflowTemplate(saved)
      ElMessage.success('已加载流程模板，可按需要修改本次运行配置')
    } catch (error: any) {
      ElMessage.error(error?.message || '加载流程模板失败')
    }
    return
  }

  clearWorkflowCanvas()

  const types = template === 'direct'
    ? ['data', 'train', 'eval']
    : ['data', 'process', 'train', 'eval']
  for (const type of types) addNode(type)
  const created = nodes.value
  if (template === 'timeseries') {
    workflowName.value = '时序预测流程'
    created[1].data.label = '时序数据清洗'
  } else if (template === 'direct') {
    workflowName.value = '直接训练流程'
  } else {
    workflowName.value = '表格预测流程'
  }
  const processNode = created.find((node: any) => node.data.type === 'process')
  if (processNode) processNode.data.operation = 'govern'
  for (let index = 1; index < created.length; index++) {
    edges.value.push({
      id: 'edge_' + created[index - 1].id + '_' + created[index].id,
      source: created[index - 1].id,
      target: created[index].id,
      sourceHandle: 'output-' + nodeOutputPortType(created[index - 1].data.type),
      targetHandle: 'input-' + nodeInputPortType(created[index].data.type),
      animated: true,
      selectable: true,
      interactionWidth: 24,
      style: { stroke: created[index - 1].data.type === 'train' ? '#8b5cf6' : '#409EFF' },
    })
  }
  for (const node of created) setDefaultInputBinding(node.id)
  autoLayout()
  ElMessage.success('已创建模板，请依次配置数据集、算法与测试集')
}

async function saveDraft() {
  try {
    const graph = buildGraph()
    const pipelineId = await persistPipeline(graph)
    ElMessage.success('工作流草稿已保存（Pipeline #' + pipelineId + '）')
  } catch (error: any) {
    ElMessage.error('保存草稿失败：' + (error?.message || '画布结构无效'))
  }
}

function checkPipeline() {
  clearValidationState()
  try {
    const graph = buildGraph()
    validationIssues.value = collectValidationIssues(graph)
  } catch (error: any) {
    validationIssues.value = [{ nodeId: '', nodeLabel: '画布结构', message: error?.message || '连接关系无效' }]
  }
  applyValidationIssues()
  showValidation.value = true
}


function addNode(type: string, position?: { x: number, y: number }) {
  const config = nodeTypeConfig[type]
  const id = `node_${++nodeId}`
  // Calculate position to avoid overlap
  const existingCount = nodes.value.filter((n: any) => n.data.type === type).length
  const x = 250 + (existingCount % 3) * 250
  const y = 100 + Math.floor(existingCount / 3) * 200
  
  nodes.value.push({
    id,
    type: 'custom',
    position: position || { x, y },
    data: {
      ...config,
      type,
      dataset: null,
      dataColumns: [],
      operation: '',
      cleaningRuleId: null,
      algorithm: '',
      targetColumn: '',
      featureColumns: [],
      parameterValues: {},
      params: '',
      evaluationDatasetId: null,
      evaluationDataColumns: [],
      evaluationTargetColumn: '',
      evaluationFeatureColumns: [],
      metrics: [],
      description: '',
      inputBindings: { datasetFrom: '', modelFrom: '' },
      validationErrors: [],
      lastOutput: null,
      lastError: '',
      lastLogs: '',
      runStatus: undefined,
    },
  })
}

function removeNode(id: string) {
  nodes.value = nodes.value.filter((n: any) => n.id !== id)
  edges.value = edges.value.filter((e: any) => e.source !== id && e.target !== id)
  for (const node of nodes.value) setDefaultInputBinding(node.id)
  if (selectedNode.value?.id === id) selectedNode.value = null
  if (selectedEdge.value?.source === id || selectedEdge.value?.target === id) selectedEdge.value = null
}

function nodeLabel(id: string) {
  const node = nodes.value.find((item: any) => item.id === id)
  return node ? (node.data?.label || id) + '（' + id + '）' : id
}

function onNodeClick({ node }: any) {
  ensureNodeData(node)
  selectedNode.value = node
  selectedEdge.value = null
}

function onEdgeClick({ edge }: any) {
  selectedEdge.value = edge
  selectedNode.value = null
}

function clearCanvasSelection() {
  selectedNode.value = null
  selectedEdge.value = null
}

function removeEdge(id: string) {
  const before = edges.value.length
  const removed = edges.value.find((edge: any) => edge.id === id)
  edges.value = edges.value.filter((edge: any) => edge.id !== id)
  if (removed?.target) setDefaultInputBinding(removed.target)
  selectedEdge.value = null
  if (edges.value.length !== before) ElMessage.success('已断开连接')
}

function onConnect(connection: any) {
  if (!connection.source || !connection.target) return
  if (connection.source === connection.target) {
    ElMessage.warning('节点不能连接到自身')
    return
  }
  if (edges.value.some((edge: any) => edge.source === connection.source && edge.target === connection.target)) {
    ElMessage.warning('连接已存在')
    return
  }
  const source = nodes.value.find((node: any) => node.id === connection.source)
  const target = nodes.value.find((node: any) => node.id === connection.target)
  const outputType = nodeOutputPortType(source?.data?.type)
  const inputType = nodeInputPortType(target?.data?.type)
  if (!outputType || !inputType || outputType !== inputType) {
    ElMessage.warning('端口类型不兼容：数据集只能连接数据集输入，模型只能连接模型输入')
    return
  }
  const edge = {
    id: 'edge_' + connection.source + '_' + connection.target,
    source: connection.source,
    target: connection.target,
    sourceHandle: 'output-' + outputType,
    targetHandle: 'input-' + inputType,
    animated: true,
    selectable: true,
    interactionWidth: 24,
    style: { stroke: outputType === 'model' ? '#8b5cf6' : '#409EFF' },
  }
  try {
    buildGraph([...edges.value, edge])
    edges.value.push(edge)
    setDefaultInputBinding(connection.target)
  } catch (error: any) {
    ElMessage.error(error?.message || '该连接会产生循环依赖')
  }
}

function buildGraph(graphEdges: any[] = edges.value): Graph {
  const nodeMap = new Map(nodes.value.map((node: any) => [node.id, node]))
  const incoming = new Map<string, string[]>()
  const outgoing = new Map<string, string[]>()
  const indegree = new Map<string, number>()

  for (const node of nodes.value) {
    incoming.set(node.id, [])
    outgoing.set(node.id, [])
    indegree.set(node.id, 0)
  }

  const seen = new Set<string>()
  for (const edge of graphEdges) {
    if (!nodeMap.has(edge.source) || !nodeMap.has(edge.target)) {
      throw new Error('流水线包含指向已删除节点的连接')
    }
    if (edge.source === edge.target) throw new Error('流水线存在自循环')
    const sourceNode = nodeMap.get(edge.source)
    const targetNode = nodeMap.get(edge.target)
    if (nodeOutputPortType(sourceNode?.data?.type) !== nodeInputPortType(targetNode?.data?.type)) {
      throw new Error('流水线存在端口类型不兼容的连接')
    }
    const key = edge.source + '->' + edge.target
    if (seen.has(key)) continue
    seen.add(key)
    incoming.get(edge.target)!.push(edge.source)
    outgoing.get(edge.source)!.push(edge.target)
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1)
  }

  const queue = nodes.value.filter((node: any) => indegree.get(node.id) === 0)
  const order: any[] = []
  while (queue.length) {
    const node = queue.shift()!
    order.push(node)
    for (const childId of outgoing.get(node.id) || []) {
      const nextDegree = (indegree.get(childId) || 0) - 1
      indegree.set(childId, nextDegree)
      if (nextDegree === 0) queue.push(nodeMap.get(childId))
    }
  }
  if (order.length !== nodes.value.length) throw new Error('流水线存在循环依赖，请先调整连线')
  return { order, incoming }
}

function datasetColumnNames(dataset: any): string[] {
  const raw = dataset?.columns_info ?? dataset?.columns ?? []
  let columns = raw
  if (typeof columns === 'string') {
    try { columns = JSON.parse(columns) } catch { columns = [] }
  }
  if (!Array.isArray(columns)) return []
  return columns
    .map(item => typeof item === 'string' ? item : item?.name)
    .filter((name): name is string => typeof name === 'string' && Boolean(name.trim()))
}

function targetColumnsForNode(nodeId: string): string[] {
  const visited = new Set<string>()
  const datasetIds = new Set<number>()
  const visit = (id: string) => {
    if (visited.has(id)) return
    visited.add(id)
    const node = nodes.value.find((item: any) => item.id === id)
    if (node?.data?.type === 'data' && node.data.dataset !== null && node.data.dataset !== undefined) {
      datasetIds.add(Number(node.data.dataset))
    }
    edges.value.filter((edge: any) => edge.target === id).forEach((edge: any) => visit(edge.source))
  }
  const targetNode = nodes.value.find((item: any) => item.id === nodeId)
  ensureNodeData(targetNode)
  const selectedDatasetSource = String(targetNode?.data?.inputBindings?.datasetFrom || '')
  visit(selectedDatasetSource || nodeId)
  const directColumns = [...visited].flatMap(id => {
    const node = nodes.value.find((item: any) => item.id === id)
    return node?.data?.type === 'data' && Array.isArray(node.data.dataColumns)
      ? node.data.dataColumns : []
  })
  const metadataColumns = [...datasetIds].flatMap(id => {
    const dataset = datasets.value.find(item => Number(item.id) === id)
    return datasetColumnNames(dataset)
  })
  return [...directColumns, ...metadataColumns]
    .filter((value, index, all) => typeof value === 'string' && Boolean(value.trim()) && all.indexOf(value) === index)
}

async function loadDataNodeColumns(data: any) {
  const datasetId = Number(data?.dataset)
  data.dataColumns = []
  if (!Number.isInteger(datasetId) || datasetId <= 0) return
  try {
    const response: any = await get('/datasets/' + datasetId + '/rows', { limit: 1 })
    data.dataColumns = Array.isArray(response?.columns) ? response.columns.map(String) : []
    if (!data.dataColumns.length) {
      ElMessage.warning('该数据集没有可读取的字段，无法选择预测目标列')
    }
  } catch (error: any) {
    ElMessage.error(error?.message || '读取数据集字段失败')
  }
}

async function loadEvaluationDatasetColumns(data: any) {
  const datasetId = Number(data?.evaluationDatasetId)
  data.evaluationDataColumns = []
  data.evaluationTargetColumn = ''
  data.evaluationFeatureColumns = []
  if (!Number.isInteger(datasetId) || datasetId <= 0) return
  try {
    const response: any = await get('/datasets/' + datasetId + '/rows', { limit: 1 })
    data.evaluationDataColumns = Array.isArray(response?.columns) ? response.columns.map(String) : []
    if (!data.evaluationDataColumns.length) ElMessage.warning('该测试数据集没有可读取的字段')
  } catch (error: any) {
    ElMessage.error(error?.message || '读取测试数据集字段失败')
  }
}

function evaluationFeatureColumnsForNode(data: any): string[] {
  const target = String(data?.evaluationTargetColumn || '').trim()
  const columns = Array.isArray(data?.evaluationDataColumns) ? data.evaluationDataColumns : []
  return columns.filter((column: any) => String(column) !== target)
}

function formatParameterValue(value: any): string {
  return typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '')
}

function setParameterValue(target: Record<string, any>, candidate: any, value: any) {
  if (candidate?.type === 'array' || candidate?.type === 'object') {
    try { target[candidate.key] = JSON.parse(value) } catch { target[candidate.key] = value }
    return
  }
  target[candidate.key] = value
}

function parameterOverrides(algorithm: any, values: Record<string, any>): Record<string, any> {
  const result: Record<string, any> = {}
  for (const candidate of algorithm?.parameter_candidates || []) {
    const value = values?.[candidate.key]
    if (value === undefined) continue
    if (JSON.stringify(value) !== JSON.stringify(candidate.default)) result[candidate.key] = value
  }
  return result
}

function onPipelineAlgorithmChange(data: any, nodeId: string) {
  const algorithm = selectedUploadedAlgorithm(data.algorithm)
  data.parameterValues = {}
  data.featureColumns = []
  if (!algorithm) return
  for (const candidate of algorithm.parameter_candidates || []) {
    data.parameterValues[candidate.key] = candidate.default
  }
  if (algorithm.kind === 'uploaded_source' && !data.targetColumn) {
    const target = String(algorithm.target_column || '').trim()
    if (target && targetColumnsForNode(nodeId).includes(target)) data.targetColumn = target
  }
}

function featureColumnsForNode(nodeId: string, targetColumn?: string): string[] {
  const target = String(targetColumn || '').trim()
  return targetColumnsForNode(nodeId).filter(column => column !== target)
}

function selectedUploadedAlgorithm(value: any) {
  const matched = String(value || '').match(/^uploaded:(\d+)$/)
  if (!matched) return null
  return trainableAlgorithms.value.find(item => Number(item.id) === Number(matched[1])) || null
}

function parseParams(raw: string): Record<string, any> {
  const text = String(raw || '').trim()
  if (!text) return {}
  if (text.startsWith('{')) {
    const parsed = JSON.parse(text)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('训练参数 JSON 必须是对象')
    }
    return parsed
  }
  const result: Record<string, any> = {}
  for (const item of text.split(/[,;\n]+/).map(v => v.trim()).filter(Boolean)) {
    const separator = item.indexOf('=')
    if (separator <= 0) throw new Error(`训练参数格式错误: ${item}，请使用 key=value`)
    const key = item.slice(0, separator).trim()
    const rawValue = item.slice(separator + 1).trim()
    if (!key || !rawValue) throw new Error(`训练参数格式错误: ${item}`)
    if (/^-?\d+(\.\d+)?$/.test(rawValue)) result[key] = Number(rawValue)
    else if (rawValue === 'true' || rawValue === 'false') result[key] = rawValue === 'true'
    else result[key] = rawValue
  }
  return result
}

function inputBindingConfig(node: any) {
  ensureNodeData(node)
  const bindings: Record<string, string> = {}
  const datasetFrom = String(node.data.inputBindings?.datasetFrom || '').trim()
  const modelFrom = String(node.data.inputBindings?.modelFrom || '').trim()
  if (datasetFrom) bindings.dataset_from = datasetFrom
  if (modelFrom) bindings.model_from = modelFrom
  return Object.keys(bindings).length ? { input_bindings: bindings } : {}
}

function validateInputBinding(node: any, resourceType: string): string | null {
  ensureNodeData(node)
  const candidates = inputCandidates(node.id, resourceType)
  if (!candidates.length) return '请连接一个提供' + (resourceType === 'dataset' ? '数据集' : '模型') + '的上游节点'
  const bindingKey = resourceType === 'dataset' ? 'datasetFrom' : 'modelFrom'
  const selected = String(node.data.inputBindings[bindingKey] || '')
  if (candidates.length === 1) {
    node.data.inputBindings[bindingKey] = candidates[0].id
    return null
  }
  if (!selected) return '存在多个上游' + (resourceType === 'dataset' ? '数据集' : '模型') + '，请选择要使用的来源'
  if (!candidates.some((candidate: any) => candidate.id === selected)) {
    return '所选输入来源已不在当前连接中，请重新选择'
  }
  return null
}

function collectValidationIssues(graph: Graph) {
  const issues: Array<{ nodeId: string, nodeLabel: string, message: string }> = []
  const capabilities = new Map<string, Set<string>>()
  for (const node of graph.order) {
    const available = new Set<string>()
    for (const parentId of graph.incoming.get(node.id) || []) {
      for (const capability of capabilities.get(parentId) || []) available.add(capability)
    }
    const label = String(node.data.label || node.id)
    const addIssue = (message: string) => issues.push({ nodeId: node.id, nodeLabel: label, message })

    if (node.data.type === 'data') {
      if (node.data.dataset === null || node.data.dataset === undefined || node.data.dataset === '') {
        addIssue('请选择要读取的平台数据集')
      }
      available.add('dataset_id')
    } else if (node.data.type === 'process') {
      if (!available.has('dataset_id')) addIssue('请连接一个提供数据集的上游节点')
      const datasetBindingIssue = validateInputBinding(node, 'dataset')
      if (datasetBindingIssue) addIssue(datasetBindingIssue)
      if (!node.data.operation) addIssue('请选择处理操作')
      if (node.data.operation === 'clean_rule' && !node.data.cleaningRuleId) {
        addIssue('请选择已启用的清洗规则')
      }
    } else if (node.data.type === 'train') {
      if (!available.has('dataset_id')) addIssue('请连接一个提供数据集的上游节点')
      const trainDatasetBindingIssue = validateInputBinding(node, 'dataset')
      if (trainDatasetBindingIssue) addIssue(trainDatasetBindingIssue)
      if (!node.data.algorithm) addIssue('请选择训练算法')
      try {
        parseParams(node.data.params)
      } catch (error: any) {
        addIssue(error?.message || '高级参数格式不正确')
      }
      available.add('job_id')
      available.add('model_id')
    } else if (node.data.type === 'eval') {
      // 旧工作流可能保存了不匹配的分类/回归指标；运行前统一修正为上游模型适用的指标。
      normalizeEvaluationMetrics(node)
      if (!available.has('model_id')) addIssue('请连接一个训练模型的上游节点')
      const modelBindingIssue = validateInputBinding(node, 'model')
      if (modelBindingIssue) addIssue(modelBindingIssue)
      if (!Number.isInteger(Number(node.data.evaluationDatasetId)) || Number(node.data.evaluationDatasetId) <= 0) {
        addIssue('请选择独立测试数据集')
      }
      if (!Array.isArray(node.data.metrics) || !node.data.metrics.length) {
        addIssue('请至少选择一个评估指标')
      }
    } else {
      addIssue('不支持的节点类型：' + node.data.type)
    }
    capabilities.set(node.id, available)
  }
  return issues
}

function applyValidationIssues() {
  for (const node of nodes.value) node.data.validationErrors = []
  for (const issue of validationIssues.value) {
    const node = nodes.value.find((item: any) => item.id === issue.nodeId)
    if (node) node.data.validationErrors.push(issue.message)
  }
}

function validatePipeline(graph: Graph) {
  validationIssues.value = collectValidationIssues(graph)
  applyValidationIssues()
  if (validationIssues.value.length) throw new Error(validationIssues.value[0].nodeLabel + '：' + validationIssues.value[0].message)
}

function displayText(value: any): string {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'string') return value
  if (value instanceof Error) return displayText(value.message)
  if (typeof value === 'object') {
    try { return JSON.stringify(value, null, 2) } catch { return '无法展示的结构化结果' }
  }
  return String(value)
}

function appendLogs(lines: any[], level: 'info' | 'success' | 'error' = 'info') {
  for (const raw of lines) {
    const text = displayText(raw)
    for (const line of text.split('\n')) {
      if (line.trim()) runLog.value.push({ level, msg: '  ' + line })
    }
  }
}

function reusableWorkflowTemplate(graph: Graph, name: string) {
  return {
    name,
    tasks: graph.order.map((node: any) => ({
      name: node.id,
      task_type: TASK_TYPE_MAP[node.data.type],
      dependencies: graph.incoming.get(node.id) || [],
      // These are editable defaults only. No execution result is placed in a template.
      config: serializableConfig(node),
    })),
  }
}

function templateEdgeCount(template: any): number {
  return (Array.isArray(template?.tasks) ? template.tasks : [])
    .reduce((total: number, task: any) => total + (Array.isArray(task?.dependencies) ? task.dependencies.length : 0), 0)
}

async function openTemplateLibrary() {
  await loadWorkflowTemplates()
  showTemplateLibrary.value = true
}

async function loadSavedTemplate(template: any) {
  showTemplateLibrary.value = false
  await loadTemplate('saved:' + template.id)
}

async function deleteWorkflowTemplate(template: any) {
  const id = Number(template?.id)
  if (!Number.isInteger(id) || id <= 0) return
  try {
    await ElMessageBox.confirm(
      '确定删除流程模板“' + String(template.name || id) + '”吗？不会影响任何已经运行的记录。',
      '删除流程模板', { type: 'warning' },
    )
    await del('/pipelines/templates/' + id)
    await loadWorkflowTemplates()
    ElMessage.success('流程模板已删除')
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '删除流程模板失败')
  }
}

type TemplateSaveRequest = { name: string; payload: Record<string, any> }

async function askToSaveWorkflowTemplate(graph: Graph): Promise<TemplateSaveRequest | null | undefined> {
  try {
    await ElMessageBox.confirm(
      '是否将当前流程在执行成功后保存为可复用模板？模板会保留当前节点、连线、数据集、算法和参数等默认配置；之后加载时仍可修改。运行结果不会保存到模板。',
      '保存流程模板',
      { confirmButtonText: '成功后保存模板并运行', cancelButtonText: '不保存直接运行', distinguishCancelAndClose: true, type: 'info' },
    )
  } catch (action: any) {
    return action === 'cancel' ? null : undefined
  }
  try {
    const result = await ElMessageBox.prompt(
      '请输入流程模板名称。模板只会在本次流程成功后写入模板库。',
      '命名流程模板',
      {
        inputValue: (String(workflowName.value || '').trim() || '未命名工作流') + '模板',
        inputPlaceholder: '例如：工艺参数优化训练与测试流程',
        inputPattern: /\S/,
        inputErrorMessage: '模板名称不能为空',
        confirmButtonText: '运行',
        cancelButtonText: '取消运行',
      },
    )
    const name = String(result.value || '').trim()
    return { name, payload: reusableWorkflowTemplate(graph, name) }
  } catch {
    return undefined
  }
}

async function runPipeline() {
  let graph: Graph
  let templateSaveRequest: TemplateSaveRequest | null = null
  try {
    graph = buildGraph()
    validatePipeline(graph)
    validateSavedArtifactNames()
    const templateDecision = await askToSaveWorkflowTemplate(graph)
    if (templateDecision === undefined) return
    templateSaveRequest = templateDecision
  } catch (error: any) {
    ElMessage.error(error?.message || '流水线配置无效')
    return
  }

  running.value = true
  runLog.value = []
  for (const node of nodes.value) {
    ensureNodeData(node)
    node.data.runStatus = 'pending'
    node.data.lastOutput = null
    node.data.lastError = ''
    node.data.lastLogs = ''
  }

  try {
    const pipelineId = await persistPipeline(graph)
    const accepted: any = await post(`/pipelines/${pipelineId}/run`, { validation_context: {} })
    const runId = Number(accepted?.run_id)
    const acceptedStatus = normalizedStatus(accepted?.status)
    if (!Number.isInteger(runId) || runId <= 0) throw new Error('后端未返回有效 run_id')
    currentRunId.value = runId
    currentRunStatus.value = (TASK_RUN_STATUS_MAP[acceptedStatus] || 'pending') as RunStatus
    if (!['pending', 'running'].includes(acceptedStatus)) {
      throw new Error(`后端未接受流水线运行: ${acceptedStatus || 'empty'}`)
    }
    runLog.value.push({ level: 'info', msg: `▶ Pipeline #${pipelineId} / Run #${runId} 已持久化并受理` })
    await pollPipelineRun(runId)
    runLog.value.push({ level: 'success', msg: '✅ 后端确认流水线所有节点执行成功' })
    if (templateSaveRequest) {
      try {
        await post('/pipelines/templates', templateSaveRequest.payload)
        await loadWorkflowTemplates()
        runLog.value.push({ level: 'success', msg: '✅ 流程模板已写入模板库：' + templateSaveRequest.name })
      } catch (templateError: any) {
        appendLogs(['流程已成功，但模板保存失败：' + (templateError?.message || '未知错误')], 'error')
        ElMessage.warning('流水线执行成功，但模板未保存，请稍后重试')
      }
    }
    ElMessage.success(templateSaveRequest ? '流水线执行成功，模板已保存' : '流水线执行成功')
  } catch (error: any) {
    currentRunStatus.value = 'failed'
    for (const node of nodes.value) {
      if (node.data.runStatus === 'running') node.data.runStatus = 'failed'
      else if (node.data.runStatus === 'pending') node.data.runStatus = 'skipped'
    }
    appendLogs([error?.message ?? error ?? '流水线运行失败'], 'error')
    ElMessage.error('流水线执行失败，请查看节点日志')
  } finally {
    running.value = false
  }
}

// 前端节点 type(data/process/train/eval) → 后端 execute-node 的 task_type
// 后端按 task_type 分发真实执行(data_load/model_train/...),否则走 "custom" 通用占位
const TASK_TYPE_MAP: Record<string, string> = {
  data: 'data_load',
  process: 'data_process',
  train: 'model_train',
  eval: 'model_evaluate',
}

const ALGORITHM_MAP: Record<string, string> = {
  rf: 'random_forest',
  xgb: 'xgboost',
  lgbm: 'lightgbm',
  mlp: 'mlp',
}

function normalizedStatus(value: any): string {
  return String(value || '').trim().toLowerCase()
}

const TASK_RUN_STATUS_MAP: Record<string, RunStatus> = {
  pending: 'pending', running: 'running', success: 'success',
  failed: 'failed', skipped: 'skipped',
}

async function pollPipelineRun(runId: number): Promise<void> {
  const lastSnapshot = new Map<string, string>()
  const expectedTaskNames = new Set(nodes.value.map((node: any) => node.id))
  for (let attempt = 0; attempt < 1800; attempt++) {
    const run: any = await get(`/pipelines/runs/${runId}`)
    const runStatus = normalizedStatus(run?.status)
    currentRunStatus.value = (TASK_RUN_STATUS_MAP[runStatus] || (['failed', 'cancelled'].includes(runStatus) ? 'failed' : 'pending')) as RunStatus
    const taskRuns = Array.isArray(run?.task_runs) ? run.task_runs : []
    const returnedTaskNames = new Set(taskRuns.map((task: any) => String(task?.task_name || '')))
    if (taskRuns.length !== expectedTaskNames.size
      || [...expectedTaskNames].some(taskName => !returnedTaskNames.has(taskName))) {
      throw new Error(`Run #${runId} 返回的任务集合与已持久化 DAG 不一致`)
    }
    for (const task of taskRuns) {
      const taskName = String(task?.task_name || '')
      const status = normalizedStatus(task?.status)
      const mapped = TASK_RUN_STATUS_MAP[status]
      const node = nodes.value.find((item: any) => item.id === taskName)
      if (!node || !mapped) throw new Error(`Run #${runId} 返回未知节点或状态: ${taskName}/${status}`)
      ensureNodeData(node)
      node.data.runStatus = mapped
      node.data.lastOutput = task?.outputs || null
      node.data.lastError = task?.error || ''
      node.data.lastLogs = task?.logs || ''
      const metricsText = taskMetricsLog(task)
      const snapshot = JSON.stringify([status, task?.logs || '', task?.error || '', task?.outputs?.metrics || {}])
      if (lastSnapshot.get(taskName) !== snapshot) {
        lastSnapshot.set(taskName, snapshot)
        const level = status === 'failed' ? 'error' : status === 'success' ? 'success' : 'info'
        appendLogs([node.data.label + ': ' + status, task?.logs || '', metricsText, task?.error || ''], level)
      }
    }
    if (runStatus === 'success') {
      if (taskRuns.some((item: any) => normalizedStatus(item?.status) !== 'success')) {
        throw new Error(`Run #${runId} 终态与节点状态不一致`)
      }
      return
    }
    if (['failed', 'cancelled'].includes(runStatus)) {
      throw new Error(run?.error_message || `Run #${runId} ${runStatus}`)
    }
    if (!['pending', 'running'].includes(runStatus)) {
      throw new Error(`Run #${runId} 返回未知状态: ${runStatus || 'empty'}`)
    }
    await new Promise(resolve => window.setTimeout(resolve, 2000))
  }
  throw new Error(`Run #${runId} 等待超过 1 小时`)
}

async function exportPipeline(format: string) {
  try {
    const graph = buildGraph()
    validatePipeline(graph)
    const pipelineId = await persistPipeline(graph)
    const response: any = await post('/pipelines/' + pipelineId + '/reproducible-release', {})
    if (!response?.release_id || !response?.api_path || !response?.sdk_code) {
      throw new Error('后端未生成完整的工作流复现快照')
    }
    exportFormat.value = format
    exportApiEndpoint.value = format === 'api' ? window.location.origin + response.api_path : ''
    exportCode.value = format === 'sdk' ? response.sdk_code : ''
    exportPipelineId.value = pipelineId
    apiTestResult.value = null
    showExport.value = true
    ElMessage.success('已冻结工作流复现快照：' + response.dataset_count + ' 个数据集、' + response.algorithm_count + ' 个上传算法')
  } catch (error: any) {
    exportCode.value = ''
    exportApiEndpoint.value = ''
    exportPipelineId.value = null
    apiTestResult.value = null
    showExport.value = false
    ElMessage.error('导出失败: ' + (error?.message || '无法生成可复现快照'))
  }
}

async function downloadWorkflowPackage(pipelineId: number) {
  if (!Number.isInteger(pipelineId) || pipelineId <= 0) throw new Error('无效 Pipeline ID')
  downloadingPipelineId.value = pipelineId
  try {
    const filename = await downloadFile(
      `/pipelines/${pipelineId}/export-package`,
      `pipeline_${pipelineId}_workflow.zip`,
    )
    ElMessage.success(`完整工作流已下载：${filename}`)
  } finally {
    downloadingPipelineId.value = null
  }
}

async function downloadCurrentWorkflow() {
  workflowDownloading.value = true
  try {
    const graph = buildGraph()
    validatePipeline(graph)
    const pipelineId = await persistPipeline(graph)
    await downloadWorkflowPackage(pipelineId)
  } catch (error: any) {
    ElMessage.error(`下载失败：${error?.message || '工作流配置无效'}`)
  } finally {
    workflowDownloading.value = false
  }
}

async function downloadHistoryWorkflow(run: any) {
  try {
    await downloadWorkflowPackage(Number(run?.pipeline_id))
  } catch (error: any) {
    ElMessage.error(`下载失败：${error?.message || '历史工作流不存在'}`)
  }
}

function serializableConfig(node: any): Record<string, any> {
  if (node.data.type === 'data') return { dataset_id: node.data.dataset }
  if (node.data.type === 'process') {
    return {
      ...inputBindingConfig(node),
      operation: node.data.operation,
      save_dataset: workflowOutputOptions.saveDataset,
      ...(workflowOutputOptions.saveDataset
        ? { name: String(workflowOutputOptions.datasetName || '').trim() }
        : {}),
      ...(node.data.operation === 'clean_rule'
        ? { cleaning_rule_id: node.data.cleaningRuleId }
        : {}),
    }
  }
  if (node.data.type === 'train') {
    const advancedOverrides = parseParams(node.data.params)
    const uploaded = selectedUploadedAlgorithm(node.data.algorithm)
    if (uploaded?.kind === 'uploaded_source') {
      const configuredParameters = {
        ...parameterOverrides(uploaded, node.data.parameterValues || {}),
        ...advancedOverrides,
      }
      return {
        ...inputBindingConfig(node),
        save_model: workflowOutputOptions.saveModel,
        ...(workflowOutputOptions.saveModel
          ? { name: String(workflowOutputOptions.modelName || '').trim() }
          : {}),
        algorithm_id: uploaded.id,
        target_column: String(node.data.targetColumn || '').trim(),
        feature_columns: Array.isArray(node.data.featureColumns)
          ? node.data.featureColumns.map(String).filter((column: string) => Boolean(column.trim()))
          : [],
        feature_columns_explicit: Array.isArray(node.data.featureColumns) && node.data.featureColumns.length > 0,
        effective_hyperparameters: configuredParameters,
        hyperparameter_source: Object.keys(configuredParameters).length ? 'manual' : 'default',
      }
    }
    if (uploaded?.kind === 'finetune_model') {
      return {
        ...inputBindingConfig(node),
        save_model: workflowOutputOptions.saveModel,
        ...(workflowOutputOptions.saveModel
          ? { name: String(workflowOutputOptions.modelName || '').trim() }
          : {}),
        algorithm_id: uploaded.id,
        ...advancedOverrides,
      }
    }
    return {
      ...inputBindingConfig(node),
      save_model: workflowOutputOptions.saveModel,
      ...(workflowOutputOptions.saveModel
        ? { name: String(workflowOutputOptions.modelName || '').trim() }
        : {}),
      model_type: ALGORITHM_MAP[node.data.algorithm] || node.data.algorithm,
      ...advancedOverrides,
    }
  }
  if (node.data.type === 'eval') {
    const selectedFeatures = Array.isArray(node.data.evaluationFeatureColumns)
      ? node.data.evaluationFeatureColumns.map(String).filter((column: string) => Boolean(column.trim()))
      : []
    return {
      ...inputBindingConfig(node),
      save_evaluation_record: workflowOutputOptions.saveEvaluationRecord,
      ...(workflowOutputOptions.saveEvaluationRecord
        ? { evaluation_name: String(workflowOutputOptions.evaluationName || '').trim() }
        : {}),
      metrics: node.data.metrics,
      evaluation_dataset_id: Number(node.data.evaluationDatasetId),
      ...(String(node.data.evaluationTargetColumn || '').trim()
        ? { target_column: String(node.data.evaluationTargetColumn).trim() }
        : {}),
      ...(selectedFeatures.length ? { feature_columns: selectedFeatures } : {}),
    }
  }
  return {}
}

function persistedDefinition(graph: Graph) {
  return {
    name: String(workflowName.value || '').trim() || ('低代码流水线-' + nodes.value.length + '节点'),
    description: '由低代码画布持久化；运行时按 DAG 传播数据集、训练任务和模型输出',
    trigger_type: 'manual',
    tasks: graph.order.map((node: any) => ({
      name: node.id,
      task_type: TASK_TYPE_MAP[node.data.type],
      dependencies: graph.incoming.get(node.id) || [],
      config: serializableConfig(node),
    })),
  }
}

async function persistPipeline(graph: Graph): Promise<number> {
  const definition = persistedDefinition(graph)
  const signature = JSON.stringify(definition)
  if (persistedPipelineId.value && persistedSignature.value === signature) {
    return persistedPipelineId.value
  }
  const response: any = persistedPipelineId.value
    ? await put(`/pipelines/${persistedPipelineId.value}`, definition)
    : await post('/pipelines', definition)
  const id = Number(response?.id ?? response?.pipeline_id ?? persistedPipelineId.value)
  if (!Number.isInteger(id) || id <= 0) throw new Error('流水线保存后未返回有效 ID')
  persistedPipelineId.value = id
  persistedSignature.value = signature
  return id
}

async function openRunHistory() {
  showRuns.value = true
  await loadRunHistory()
}

async function loadRunHistory() {
  runsLoading.value = true
  try {
    const response: any = await get('/pipelines/runs', { limit: 100 })
    if (!Array.isArray(response?.items)) throw new Error('运行记录响应格式无效')
    runHistory.value = response.items
  } catch (error: any) {
    runHistory.value = []
    ElMessage.error(`加载运行记录失败: ${error?.message || '未知错误'}`)
  } finally {
    runsLoading.value = false
  }
}

async function deleteHistoryRun(run: any) {
  const runId = Number(run?.id)
  if (!Number.isInteger(runId) || runId <= 0) {
    ElMessage.error('无效 Run ID')
    return
  }
  if (['PENDING', 'RUNNING'].includes(String(run.status).toUpperCase())) {
    ElMessage.warning('运行中的流水线不能删除')
    return
  }
  try {
    await ElMessageBox.confirm(
      '确定删除流水线运行记录 #' + runId + ' 吗？此操作只删除该次运行的日志和节点结果，不删除流水线、模型或数据集。',
      '删除流水线运行记录', { type: 'warning' },
    )
    await del('/pipelines/runs/' + runId)
    if (selectedHistoryRun.value?.id === runId) {
      selectedHistoryRun.value = null
      showRunDetail.value = false
    }
    ElMessage.success('流水线运行记录已删除')
    await loadRunHistory()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '删除失败')
  }
}

function openRunDetail(run: any) {
  selectedHistoryRun.value = run
  showRunDetail.value = true
}

function evaluationMetrics(run: any): Record<string, any> {
  const tasks = Array.isArray(run?.task_runs) ? run.task_runs : []
  const evaluation = [...tasks].reverse().find((task: any) => task?.task_type === 'model_evaluate')
  if (evaluation?.outputs?.metrics && typeof evaluation.outputs.metrics === 'object') {
    return evaluation.outputs.metrics
  }
  const training = [...tasks].reverse().find((task: any) => task?.task_type === 'model_train')
  return training?.outputs?.metrics && typeof training.outputs.metrics === 'object'
    ? training.outputs.metrics
    : {}
}

function metricEntries(run: any): [string, any][] {
  return Object.entries(evaluationMetrics(run)).filter(([, value]) => value !== null && value !== undefined)
}

function taskMetricsLog(task: any): string {
  const metrics = task?.outputs?.metrics
  if (!metrics || typeof metrics !== 'object') return ''
  const entries = Object.entries(metrics).filter(([, value]) => value !== null && value !== undefined)
  if (!entries.length) return ''
  const label = task?.task_type === 'model_evaluate' ? '测试指标' : '训练指标'
  const dataset = task?.task_type === 'model_evaluate' && task?.outputs?.dataset_id
    ? '（测试集 #' + task.outputs.dataset_id + '）'
    : ''
  return label + dataset + '：' + entries.map(([key, value]) => metricLabel(key) + ' ' + formatMetricValue(value)).join('，')
}

function artifactValue(run: any, key: string): any {
  const tasks = Array.isArray(run?.task_runs) ? run.task_runs : []
  for (const task of [...tasks].reverse()) {
    const value = task?.outputs?.[key]
    if (value !== null && value !== undefined && value !== '') return value
  }
  return null
}

function metricLabel(key: string) {
  const labels: Record<string, string> = {
    r2: 'R²', R2: 'R²', rmse: 'RMSE', RMSE: 'RMSE',
    mae: 'MAE', MAE: 'MAE', mse: 'MSE', MSE: 'MSE',
    accuracy: '准确率', f1: 'F1', auc: 'AUC', loss: 'Loss',
  }
  return labels[key] || key
}

function formatMetricValue(value: any) {
  if (typeof value === 'object' && value !== null) return displayText(value)
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return displayText(value)
  return Math.abs(numeric) >= 1000 ? numeric.toLocaleString() : numeric.toFixed(4)
}

function formatDuration(seconds: any) {
  const value = Number(seconds)
  if (!Number.isFinite(value)) return '-'
  return value < 60 ? `${value}秒` : `${Math.floor(value / 60)}分${value % 60}秒`
}

function formatDateTime(value: any) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

function historyStatusLabel(status: any) {
  const labels: Record<string, string> = {
    pending: '等待中', running: '运行中', success: '成功',
    failed: '失败', cancelled: '已取消', skipped: '已跳过',
  }
  return labels[normalizedStatus(status)] || String(status || '-')
}

function taskTypeLabel(type: any) {
  const labels: Record<string, string> = {
    data_load: '数据加载', data_process: '数据处理',
    feature_engineering: '特征处理', model_train: '模型训练', model_evaluate: '模型评估',
  }
  return labels[String(type || '')] || String(type || '-')
}

function taskOutputSummary(task: any) {
  const outputs = task?.outputs || {}
  if (task?.error) return displayText(task.error)
  if (task?.task_type === 'data_load') return `${datasetText(outputs.dataset_id, outputs.dataset_name)}，${outputs.row_count ?? '-'} 行`
  if (task?.task_type === 'data_process') return `生成${datasetText(outputs.dataset_id, outputs.dataset_name)}，质量分 ${outputs.quality_score ?? '-'}`
  if (task?.task_type === 'model_train') return `${modelText(outputs.model_id, outputs.model_name)}，任务 ${outputs.job_id ?? '-'}`
  if (task?.task_type === 'model_evaluate') {
    return metricEntries({ task_runs: [task] }).map(([key, value]) => `${metricLabel(key)} ${formatMetricValue(value)}`).join('，') || '评估完成'
  }
  return displayText(task?.logs) || '-'
}

async function resumeHistoryRun(run: any) {
  const runId = Number(run?.id)
  if (!Number.isInteger(runId) || runId <= 0) {
    ElMessage.error('无效 Run ID')
    return
  }
  try {
    const response: any = await post(`/pipelines/runs/${runId}/resume`, {})
    if (response?.accepted !== true) throw new Error('后端未确认恢复请求')
    ElMessage.success(`Run #${runId} 已重新进入协调队列`)
    await loadRunHistory()
  } catch (error: any) {
    ElMessage.error(`恢复失败: ${error?.message || '运行状态不允许恢复'}`)
  }
}

function historyStatusType(status: any) {
  const value = normalizedStatus(status)
  if (value === 'success') return 'success'
  if (value === 'failed' || value === 'cancelled') return 'danger'
  if (value === 'running') return 'warning'
  return 'info'
}

async function runExportApiTest() {
  const pipelineId = Number(exportPipelineId.value)
  if (!Number.isInteger(pipelineId) || pipelineId <= 0) {
    ElMessage.error('未找到可测试的流程 ID，请重新导出 API')
    return
  }
  try {
    await ElMessageBox.confirm(
      '网页测试会真实运行一次当前流程，并按流程配置创建相应产物。是否继续？',
      '测试 API',
      { type: 'warning' },
    )
  } catch {
    return
  }
  apiTestLoading.value = true
  apiTestResult.value = null
  try {
    const result: any = await post('/pipelines/' + pipelineId + '/run?wait=true&timeout=3600', {})
    apiTestResult.value = result
    const status = String(result?.status || '').toUpperCase()
    if (status === 'SUCCESS') ElMessage.success('API 调用成功，已返回最终结果')
    else if (result?.waited === false) ElMessage.warning('API 已受理任务，但等待超时；可在运行记录继续查看')
    else ElMessage.error('API 调用已完成，但流程执行失败；请查看下方节点结果')
  } catch (error: any) {
    ElMessage.error(error?.message || 'API 测试请求失败')
  } finally {
    apiTestLoading.value = false
  }
}

function downloadReproducibleSdk() {
  const content = String(exportCode.value || '')
  if (!content) {
    ElMessage.warning('暂无可下载的 SDK')
    return
  }
  const blob = new Blob([content], { type: 'text/x-python;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'foundation_workflow_reproducible_sdk.py'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
  ElMessage.success('工作流复现 SDK 已下载')
}

async function copyApiEndpoint() {
  const endpoint = String(exportApiEndpoint.value || '')
  if (!endpoint) {
    ElMessage.warning('暂无可复制的 API 调用地址')
    return
  }
  try {
    let copied = false
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(endpoint)
        copied = true
      } catch {
        copied = false
      }
    }
    if (!copied) {
      const textarea = document.createElement('textarea')
      textarea.value = endpoint
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      textarea.style.pointerEvents = 'none'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      copied = document.execCommand('copy')
      textarea.remove()
    }
    if (!copied) throw new Error('浏览器拒绝复制操作')
    ElMessage.success('API 调用地址已复制')
  } catch (error: any) {
    ElMessage.error(error?.message || '复制失败，请手动选择链接')
  }
}

async function copyCode() {
  const content = String(exportCode.value || '')
  if (!content) {
    ElMessage.warning('暂无可复制的导出代码')
    return
  }
  try {
    let copied = false
    // Clipboard API only works in a secure browser context. The platform is
    // commonly opened over intranet HTTP, so use a DOM fallback as well.
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(content)
        copied = true
      } catch {
        copied = false
      }
    }
    if (!copied) {
      const textarea = document.createElement('textarea')
      textarea.value = content
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      textarea.style.pointerEvents = 'none'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      copied = document.execCommand('copy')
      textarea.remove()
    }
    if (!copied) throw new Error('浏览器拒绝复制操作')
    ElMessage.success('已复制到剪贴板')
  } catch (error: any) {
    ElMessage.error(error?.message || '复制失败，请手动选择代码')
  }
}

function statusTagType(status: RunStatus) {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'skipped') return 'info'
  if (status === 'running') return 'warning'
  return 'info'
}

function statusLabel(status: RunStatus) {
  return {
    pending: '等待上游',
    running: '运行中',
    success: '已完成',
    failed: '失败',
    skipped: '已跳过',
  }[status]
}

onMounted(async () => {
  // 这三项互不依赖：上传算法列表暂时不可用时，不能连带让数据集、清洗规则也无法使用。
  const [datasetResult, ruleResult, algorithmResult, templateResult] = await Promise.allSettled([
    get('/datasets'),
    get<any>('/data-cleaning/rules', { limit: 500 }),
    get<any>('/pipelines/trainable-algorithms'),
    get<any>('/pipelines/templates'),
  ])

  const failedResources: string[] = []
  if (datasetResult.status === 'fulfilled') {
    const response: any = datasetResult.value
    datasets.value = Array.isArray(response) ? response : (response?.items || [])
  } else {
    failedResources.push('数据集')
  }

  if (ruleResult.status === 'fulfilled') {
    cleaningRules.value = (ruleResult.value as any)?.items || []
  } else {
    failedResources.push('清洗规则')
  }

  if (algorithmResult.status === 'fulfilled') {
    trainableAlgorithms.value = (algorithmResult.value as any)?.items || []
  } else {
    failedResources.push('上传算法')
  }

  if (templateResult.status === 'fulfilled') {
    workflowTemplates.value = (templateResult.value as any)?.items || []
  } else {
    failedResources.push('流程模板')
  }

  if (failedResources.length) {
    ElMessage.warning(failedResources.join('、') + '加载失败，请刷新后重试')
  }
})
</script>

<style scoped>
.pipeline-root { display:flex; flex-direction:column; height:calc(100vh - 60px); background:#f8fafc; }
.pipeline-header { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:12px 24px; background:#fff; border-bottom:1px solid #e2e8f0; }
.pipeline-titlebar { display:flex; align-items:center; gap:12px; min-width:270px; }
.pipeline-header h2 { margin:0; font-size:18px; white-space:nowrap; }
.workflow-name { width:190px; }
.header-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; }
.pipeline-runtime-panel { display:grid; grid-template-columns:minmax(330px,1.45fr) minmax(220px,1fr) minmax(220px,1fr); gap:12px; align-items:stretch; padding:10px 24px; border-bottom:1px solid #dbeafe; background:#fff; }
.runtime-progress, .runtime-result { min-width:0; padding:9px 12px; border:1px solid #dbeafe; border-radius:7px; background:#f8fbff; }
.runtime-progress-title { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-bottom:6px; color:#475569; font-size:12px; }
.runtime-progress-title strong { color:#1e293b; font-size:13px; }
.runtime-active { display:block; margin-top:5px; overflow:hidden; color:#64748b; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.runtime-result { display:flex; flex-direction:column; gap:7px; }
.runtime-result > span { color:#64748b; font-size:12px; }
.runtime-result-lines { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:5px 9px; align-items:start; }
.runtime-result-lines span { color:#64748b; font-size:12px; white-space:nowrap; }
.runtime-result-lines strong { min-width:0; color:#1e293b; font-size:12px; font-weight:600; line-height:1.45; white-space:normal; overflow-wrap:anywhere; word-break:break-word; }
.pipeline-body { flex:1; display:flex; overflow:hidden; }
@media (max-width:1100px) { .pipeline-runtime-panel { grid-template-columns:1fr; } }
.node-library { width:250px; flex:0 0 250px; padding:14px 12px; overflow-y:auto; background:#fff; border-right:1px solid #e2e8f0; }
.library-title { display:flex; align-items:baseline; justify-content:space-between; gap:6px; margin-bottom:14px; }
.library-title h3 { margin:0; font-size:15px; color:#1e293b; }
.library-title span { font-size:11px; color:#94a3b8; }
.library-group { display:flex; flex-direction:column; gap:7px; margin:0 0 18px; }
.library-group h4 { margin:0; color:#64748b; font-size:12px; }
.library-group .el-button { justify-content:flex-start; margin:0; }
.library-future { border-top:1px dashed #cbd5e1; padding-top:14px; }
.library-future span { color:#94a3b8; font-size:12px; line-height:1.6; }
.artifact-settings { display:flex; flex-direction:column; gap:8px; margin-top:4px; padding:12px 10px; border:1px solid #bfdbfe; border-radius:8px; background:#f8fbff; }
.artifact-settings-title { display:flex; align-items:baseline; justify-content:space-between; gap:6px; }
.artifact-settings-title h4 { margin:0; color:#1e3a5f; font-size:13px; }
.artifact-settings-title span { color:#64748b; font-size:11px; }
.artifact-settings :deep(.el-checkbox) { height:auto; margin-right:0; color:#334155; white-space:normal; line-height:1.4; }
.artifact-help, .artifact-empty { color:#64748b; font-size:11px; line-height:1.5; }
.flow-canvas { position:relative; flex:1; background:#f0f4f8; }
.canvas-toolbar { position:absolute; z-index:5; top:12px; left:14px; right:14px; display:flex; justify-content:space-between; gap:16px; padding:8px 10px; border:1px solid #dbeafe; border-radius:7px; background:rgba(255,255,255,.92); color:#64748b; font-size:12px; pointer-events:none; }
.props-panel { width:280px; background:#fff; border-left:1px solid #e2e8f0; padding:16px; overflow-y:auto; }
.props-panel h3 { margin:0 0 12px; font-size:15px; }
.props-panel h4 { margin:0 0 8px; font-size:13px; color:#64748b; }

.custom-node { background:#fff; border:2px solid #e2e8f0; border-radius:8px; min-width:220px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }
.custom-node.node-invalid { border-color:#ef4444; box-shadow:0 0 0 2px rgba(239,68,68,.1); }
.custom-node.node-data { border-color:#3b82f6; }
.custom-node.node-process { border-color:#f59e0b; }
.custom-node.node-train { border-color:#10b981; }
.custom-node.node-eval { border-color:#8b5cf6; }
.node-header { display:flex; align-items:center; gap:6px; padding:8px 10px; background:#f8fafc; border-radius:6px 6px 0 0; font-size:13px; font-weight:600; }
.node-body { padding:8px; }
.node-port-summary { display:flex; flex-direction:column; gap:2px; padding:6px 8px; border-top:1px solid #eef2f7; color:#64748b; font-size:10px; line-height:1.35; }
.node-result-preview { margin:0 8px 7px; padding:5px 6px; overflow:hidden; border:1px solid #bbf7d0; border-radius:4px; background:#f0fdf4; color:#166534; font-size:10px; white-space:nowrap; text-overflow:ellipsis; }
.node-result-preview.is-error { border-color:#fecaca; background:#fef2f2; color:#b91c1c; }
.node-output-detail { max-height:180px; margin:0 0 10px; padding:8px; overflow:auto; border-radius:4px; background:#0f172a; color:#e2e8f0; font-size:11px; line-height:1.45; white-space:pre-wrap; word-break:break-word; }
:deep(.vue-flow__handle.port-dataset) { width:11px; height:11px; border-color:#1d4ed8; background:#60a5fa; }
:deep(.vue-flow__handle.port-model) { width:11px; height:11px; border-color:#6d28d9; background:#a78bfa; }
.node-config { display:flex; flex-direction:column; gap:4px; }
.node-rule-hint { color:#b45309; font-size:11px; line-height:1.35; }
.pipeline-param-collapse { margin-top:4px; border-top:1px solid #e2e8f0; border-bottom:1px solid #e2e8f0; }
.pipeline-param-field { display:grid; grid-template-columns:minmax(72px,1fr) minmax(105px,1.4fr); align-items:center; gap:8px; margin:7px 0; color:#475569; font-size:12px; }
.pipeline-param-field .el-input-number, .pipeline-param-field .el-select { width:100%; }
.node-io { margin:8px 0 10px; }
.node-validation-alert { margin:10px 0; }
.edge-help { margin:14px 0; }
:deep(.vue-flow__edge-path) { cursor:pointer; }

.run-log { background:#1e1e1e; color:#d4d4d4; padding:8px; border-radius:4px; font-family:monospace; font-size:12px; max-height:300px; overflow:auto; }
.log-info { color:#4fc3f7; }
.log-success { color:#66bb6a; }
.log-error { color:#ef5350; }
.empty-log { color:#94a3b8; font-size:12px; text-align:center; padding:20px; }

.run-center-note { margin-top:16px; }
.history-metrics { display:flex; flex-wrap:wrap; gap:4px 12px; color:#334155; font-variant-numeric:tabular-nums; }
.history-metrics span { white-space:nowrap; }
.task-output-text { line-height:1.55; white-space:normal; overflow-wrap:anywhere; word-break:break-word; }
.muted-text { color:#94a3b8; }
.run-summary { margin-bottom:18px; }
.result-title { margin:18px 0 10px; color:#334155; }
.metric-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; }
.metric-card { padding:12px 14px; border:1px solid #dbeafe; border-radius:8px; background:#f8fbff; display:flex; flex-direction:column; gap:5px; }
.metric-card span { color:#64748b; font-size:12px; }
.metric-card strong { color:#0f172a; font-size:20px; font-variant-numeric:tabular-nums; }
.run-error { margin-top:14px; }

.release-link-import { margin-top:14px; }
.api-endpoint-panel { margin:0 0 14px; padding:12px; border:1px solid #bfdbfe; border-radius:6px; background:#eff6ff; }
.api-endpoint-label { margin-bottom:7px; color:#1d4ed8; font-size:13px; font-weight:600; }
.api-endpoint-tip { margin-top:7px; color:#64748b; font-size:12px; line-height:1.5; }
.code-block { background:#1e1e1e; color:#d4d4d4; padding:16px; border-radius:4px; font-family:monospace; font-size:12px; white-space:pre-wrap; max-height:400px; overflow:auto; }
</style>

