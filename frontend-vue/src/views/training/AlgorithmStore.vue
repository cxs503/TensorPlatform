<template>
  <div class="algorithm-store-page">
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><Collection /></el-icon>
        算法仓库
      </h1>
      <p class="page-desc">统一管理内置算法、Python 算法、可继续训练的 PKL 与 ONNX 推理模型</p>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6" v-for="stat in stats" :key="stat.key">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-icon" :style="{ background: stat.bgColor }">
            <el-icon :size="24"><component :is="stat.icon" /></el-icon>
          </div>
          <div class="stat-content">
            <div class="stat-value">{{ stat.value }}</div>
            <div class="stat-label">{{ stat.label }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab" class="store-tabs">
      <!-- 算法列表 -->
      <el-tab-pane label="算法列表" name="algorithms">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <div class="header-left">
                <el-input v-model="searchQuery" placeholder="搜索算法..." clearable style="width: 250px; margin-right: 12px" @keyup.enter="loadAlgorithms">
                  <template #prefix>
                    <el-icon><Search /></el-icon>
                  </template>
                </el-input>
                <el-select v-model="filterCategory" placeholder="分类筛选" clearable style="width: 150px" @change="loadAlgorithms">
                  <el-option label="全部类型" value="" />
                  <el-option label="内置算法" value="builtin" />
                  <el-option label="上传算法" value="uploaded_source" />
                  <el-option label="可继续训练模型" value="finetune_model" />
                  <el-option label="推理模型" value="inference_model" />
                  <el-option label="Docker部署包" value="deployment_bundle" />
                </el-select>
              </div>
              <el-button type="primary" @click="showUploadDialog = true">
                <el-icon><Upload /></el-icon>
                上传算法
              </el-button>
            </div>
          </template>

          <el-table :data="algorithms" v-loading="algorithmsLoading" style="width: 100%">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="name" label="算法名称" min-width="200">
              <template #default="{ row }">
                <div class="algorithm-name">
                  <el-icon class="algorithm-icon"><Box /></el-icon>
                  <div>
                    <div class="name">{{ row.name }}</div>
                    <div class="desc">{{ row.description }}</div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="asset_type" label="类型" width="140">
              <template #default="{ row }">
                <el-tag size="small" :type="assetTypeTag(row.asset_type)">{{ row.asset_type_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="R²" width="100">
              <template #default="{ row }">
                <el-tag :type="(row.metrics?.r2||0) >= 0.8 ? 'success' : (row.metrics?.r2||0) >= 0.5 ? 'warning' : 'info'" size="small">
                  {{ row.metrics?.r2?.toFixed(3) || 'N/A' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="framework" label="框架" width="100">
              <template #default="{ row }">
                <el-tag size="small">{{ row.framework }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="algorithm" label="算法" width="120">
              <template #default="{ row }">
                <el-tag size="small" type="warning">{{ row.algorithm }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="estimated_training_time" label="训练耗时" width="100">
              <template #default="{ row }">
                <el-tag size="small" type="info">{{ row.estimated_training_time || 'N/A' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" width="160">
              <template #default="{ row }">
                {{ formatDate(row.updated_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="310" fixed="right">
              <template #default="{ row }">
                <el-tag v-if="row.asset_type === 'builtin'" size="small" type="primary">AutoML 内置模板</el-tag>
                <el-button v-else-if="row.asset_type === 'finetune_model'" text size="small" type="success" @click="startTraining(row)">
                  继续训练
                </el-button>
                <el-button v-else-if="row.asset_type === 'inference_model'" text size="small" type="success" @click="openOnnxRun(row)">
                  运行
                </el-button>
                <el-button v-else-if="row.asset_type === 'deployment_bundle'" text size="small" type="primary" @click="openDockerBundle(row)">
                  构建运行
                </el-button>
                <el-button v-else-if="isControlAlgorithmRow(row)" text size="small" type="success" @click="startTraining(row)">
                  运行控制仿真
                </el-button>
                <el-button v-if="isControlAlgorithmRow(row)" text size="small" type="primary" @click="openControlResults(row)">
                  查看仿真任务
                </el-button>
                <el-button v-else-if="row.asset_type === 'uploaded_source' && row.metrics?.artifact_trainable !== false" text size="small" type="success" @click="startTraining(row)">
                  训练
                </el-button>
                <el-tag v-else-if="row.asset_type === 'uploaded_source'" size="small" type="info">仅源码</el-tag>
                <el-button v-if="row.asset_type === 'uploaded_source'" text size="small" type="primary" @click="openInSandbox(row)">沙箱打开</el-button>
                <el-button text size="small" @click="viewAlgorithm(row)">详情</el-button>
                <el-button text size="small" @click="showVersions(row)">版本</el-button>
                <el-dropdown trigger="click" @command="(cmd: string) => handleCommand(cmd, row)">
                  <el-button text size="small">
                    <el-icon><More /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="edit" v-if="row.asset_type !== 'builtin'">编辑信息</el-dropdown-item>
                      <el-dropdown-item command="publish" v-if="row.status === 'trained' || row.status === 'draft'">发布</el-dropdown-item>
                      <el-dropdown-item command="unpublish" v-else>下架</el-dropdown-item>
                      <el-dropdown-item command="stats">使用统计</el-dropdown-item>
                      <el-dropdown-item command="delete" divided v-if="row.asset_type !== 'builtin'">删除</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-container">
            <el-pagination
              v-model:current-page="currentPage"
              v-model:page-size="pageSize"
              :page-sizes="[10, 20, 50]"
              :total="total"
              layout="total, sizes, prev, pager, next"
              @size-change="loadAlgorithms"
              @current-change="loadAlgorithms"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 依赖配置 -->
      <el-tab-pane label="依赖配置" name="dependencies">
        <SandboxEnvironmentManager @ready-change="handleReadyEnvironments" />
      </el-tab-pane>

      <!-- 使用统计 -->
      <el-tab-pane label="使用统计" name="stats">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>算法使用统计</span>
              <div>
                <el-date-picker
                  v-model="statsDateRange"
                  type="daterange"
                  range-separator="至"
                  start-placeholder="开始日期"
                  end-placeholder="结束日期"
                  style="width: 260px; margin-right: 12px"
                  @change="loadUsageStats"
                />
                <el-button @click="exportStats">
                  <el-icon><Download /></el-icon>
                  导出
                </el-button>
              </div>
            </div>
          </template>

          <el-row :gutter="20" class="stats-charts">
            <el-col :span="12">
              <el-card shadow="hover" class="chart-card">
                <template #header>
                  <span>下载量趋势</span>
                </template>
                <div class="chart-placeholder">
                  <el-icon :size="48"><TrendCharts /></el-icon>
                  <p>下载量趋势图</p>
                </div>
              </el-card>
            </el-col>
            <el-col :span="12">
              <el-card shadow="hover" class="chart-card">
                <template #header>
                  <span>使用量分布</span>
                </template>
                <div class="chart-placeholder">
                  <el-icon :size="48"><PieChart /></el-icon>
                  <p>分类使用分布</p>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <el-divider />

          <el-table :data="usageStats" v-loading="statsLoading" style="width: 100%">
            <el-table-column prop="algorithm_name" label="算法名称" min-width="200" />
            <el-table-column prop="downloads" label="下载量" width="120">
              <template #default="{ row }">
                {{ formatNumber(row.downloads) }}
              </template>
            </el-table-column>
            <el-table-column prop="runs" label="运行次数" width="120">
              <template #default="{ row }">
                {{ formatNumber(row.runs) }}
              </template>
            </el-table-column>
            <el-table-column prop="success_rate" label="成功率" width="120">
              <template #default="{ row }">
                <el-progress :percentage="row.success_rate * 100" :stroke-width="10" />
              </template>
            </el-table-column>
            <el-table-column prop="avg_runtime" label="平均运行时间" width="140">
              <template #default="{ row }">
                {{ row.avg_runtime?.toFixed(2) || '-' }}秒
              </template>
            </el-table-column>
            <el-table-column prop="last_used" label="最后使用" width="160">
              <template #default="{ row }">
                {{ formatDate(row.last_used) }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 上传算法对话框 -->
    <el-dialog v-model="showUploadDialog" title="上传算法" width="700px" destroy-on-close>
      <el-form :model="uploadForm" label-width="120px">
        <el-form-item label="算法/模型文件" required>
          <el-upload
            class="upload-area"
            drag
            action="#"
            :auto-upload="false"
            :on-change="handleFileChange"
            :file-list="uploadForm.fileList"
            accept=".py,.zip,.pkl,.onnx,.tar.gz,.tgz"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">
              拖拽文件到此处或 <em>点击选择</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                支持 Python单脚本或ZIP算法项目、平台可继续训练的PKL、ONNX模型和平台Docker部署包
              </div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item v-if="uploadDetection" label="识别结果">
          <el-alert
            :type="uploadDetection.type"
            :title="uploadDetection.title"
            :description="uploadDetection.description"
            :closable="false"
            show-icon
          />
        </el-form-item>
        <el-form-item label="算法名称" required>
          <el-input v-model="uploadForm.name" placeholder="请输入算法名称" />
        </el-form-item>
        <el-form-item v-if="isPythonUpload" label="模型用途" required>
          <el-radio-group v-model="uploadForm.model_mode" @change="onUploadModelModeChange">
            <el-radio-button value="trainable">可训练模型</el-radio-button>
            <el-radio-button value="control_simulation">控制/仿真模型</el-radio-button>
          </el-radio-group>
          <div class="el-upload__tip">控制/仿真模型不生成训练模型，上传后仅可运行仿真并保存结果。</div>
        </el-form-item>
        <el-form-item v-if="isTrainableSourceUpload" label="训练任务类型" required>
          <el-select v-model="uploadForm.task_type" placeholder="请选择训练任务类型" style="width: 100%">
            <el-option label="分类算法" value="classification" />
            <el-option label="回归算法" value="regression" />
            <el-option label="时序预测" value="time_series_forecasting" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="isProjectUpload" :label="isControlSimulationUpload ? '仿真入口文件' : '训练入口文件'">
          <el-input v-model="uploadForm.entry_point" placeholder="可选，例如 train.py；留空自动选择 train.py/main.py" />
        </el-form-item>
        <el-form-item v-if="isPythonUpload" label="运行环境">
          <el-select v-model="uploadForm.dependency_id" clearable placeholder="不选择则使用平台默认Python环境" style="width: 100%">
            <el-option v-for="d in dependencies" :key="d.id" :label="`${d.name}（Python ${d.python_version}）`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-collapse class="upload-more-settings">
          <el-collapse-item title="更多设置（可选）" name="more">
            <el-form-item label="版本号">
              <el-input v-model="uploadForm.version" placeholder="如: 1.0.0" />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="uploadForm.description" type="textarea" rows="3" placeholder="请输入算法描述" />
            </el-form-item>
            <el-form-item label="标签">
              <el-select v-model="uploadForm.tags" multiple filterable allow-create default-first-option placeholder="输入标签" style="width: 100%" />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <el-button @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" @click="uploadAlgorithm" :loading="uploading">上传</el-button>
      </template>
    </el-dialog>

    <!-- ONNX 直接运行 -->
    <el-dialog v-model="showOnnxRunDialog" title="运行 ONNX 推理模型" width="700px" destroy-on-close>
      <el-descriptions :column="2" border style="margin-bottom:16px">
        <el-descriptions-item label="模型">{{ onnxRunModel?.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型ID">{{ onnxRunModel?.metrics?.registered_model_id ? '#' + onnxRunModel.metrics.registered_model_id : '未登记' }}</el-descriptions-item>
        <el-descriptions-item label="输入节点">{{ onnxRunModel?.metrics?.onnx_inputs?.join(', ') || '-' }}</el-descriptions-item>
        <el-descriptions-item label="输入结构">{{ onnxRunShapeText }}</el-descriptions-item>
      </el-descriptions>
      <el-form label-width="100px">
        <el-form-item label="输入数据" required>
          <div style="width:100%">
            <el-input v-model="onnxRunInput" type="textarea" :rows="7" :placeholder="onnxRunPlaceholder" />
            <div class="onnx-run-help">
              单条数据可输入 <code>[1, 10]</code>；多条数据输入 <code>[[1, 10], [2, 20]]</code>。
              <el-button v-if="onnxRunExpectedFeatures" link type="primary" @click="fillOnnxRunExample">填入 {{ onnxRunExpectedFeatures }} 特征示例</el-button>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <el-table v-if="onnxRunRows.length" :data="onnxRunRows" border stripe style="margin-top:12px">
        <el-table-column prop="sample" label="样本" width="80" />
        <el-table-column prop="input" label="输入特征" min-width="220" show-overflow-tooltip />
        <el-table-column prop="output" :label="`预测输出（${onnxRunResult?.output_name || 'output'}）`" min-width="220" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button @click="showOnnxRunDialog=false">关闭</el-button>
        <el-button type="primary" :loading="onnxRunning" @click="runOnnxModel">开始运行</el-button>
      </template>
    </el-dialog>

    <!-- Docker部署包构建与临时运行 -->
    <el-dialog v-model="showDockerDialog" title="Docker部署包构建与运行" width="820px" destroy-on-close @closed="closeDockerDialog">
      <el-descriptions :column="2" border style="margin-bottom:16px">
        <el-descriptions-item label="部署包">{{ dockerModel?.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型ID">{{ dockerModel?.metrics?.registered_model_id ? '#' + dockerModel.metrics.registered_model_id : '未登记' }}</el-descriptions-item>
        <el-descriptions-item label="镜像">{{ dockerJob?.image_name || '尚未构建' }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="dockerStatusType">{{ dockerStatusLabel }}</el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <el-progress
        :percentage="dockerJob?.progress || 0"
        :status="dockerJob?.status === 'success' || dockerJob?.status === 'running' ? 'success' : dockerJob?.status === 'failed' ? 'exception' : undefined"
        :stroke-width="14"
      />
      <div class="docker-stage">{{ dockerJob?.stage || '尚未构建' }}</div>
      <el-alert v-if="dockerJob?.error" :title="dockerJob.error" type="error" :closable="false" show-icon style="margin-top:12px" />

      <el-collapse style="margin-top:16px">
        <el-collapse-item title="构建日志" name="logs">
          <pre class="docker-logs">{{ dockerLogs }}</pre>
        </el-collapse-item>
      </el-collapse>

      <template v-if="dockerJob?.status === 'running'">
        <el-divider content-position="left">容器推理验证</el-divider>
        <el-alert
          title="当前运行的是受限临时容器（1 CPU、512MB内存、只读文件系统、禁止提权）。"
          type="info"
          :closable="false"
          style="margin-bottom:12px"
        />
        <el-form label-width="100px">
          <el-form-item label="输入数据" required>
            <div style="width:100%">
              <el-input v-model="dockerRunInput" type="textarea" :rows="5" :placeholder="dockerRunPlaceholder" />
              <div class="onnx-run-help">单条输入如 <code>[1, 10]</code>，也支持二维批量数组。</div>
            </div>
          </el-form-item>
        </el-form>
        <el-table v-if="dockerRunRows.length" :data="dockerRunRows" border stripe>
          <el-table-column prop="sample" label="样本" width="80" />
          <el-table-column prop="input" label="输入特征" min-width="240" show-overflow-tooltip />
          <el-table-column prop="output" label="Docker预测输出" min-width="240" show-overflow-tooltip />
        </el-table>
        <div v-if="dockerRunResult?.latency_ms != null" class="docker-latency">接口耗时：{{ dockerRunResult.latency_ms }} ms</div>
      </template>

      <template #footer>
        <el-button @click="showDockerDialog=false">关闭</el-button>
        <el-button v-if="dockerJob?.status === 'running'" type="danger" :loading="dockerActionLoading" @click="stopDockerContainer">停止容器</el-button>
        <el-button v-if="dockerJob?.status === 'running'" type="success" :loading="dockerPredicting" @click="predictWithDocker">运行预测</el-button>
        <el-button v-if="['success','stopped'].includes(dockerJob?.status)" type="success" :loading="dockerActionLoading" @click="runDockerContainer">启动临时容器</el-button>
        <el-button v-if="!['queued','building','running'].includes(dockerJob?.status)" type="primary" :loading="dockerActionLoading" @click="buildDockerImage">
          {{ dockerJob?.status === 'success' || dockerJob?.status === 'stopped' ? '重新构建' : '开始构建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 算法详情对话框 -->
    <el-dialog v-model="showDetailDialog" title="算法详情" width="800px" destroy-on-close>
      <template v-if="currentAlgorithm">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="算法名称">{{ currentAlgorithm.name }}</el-descriptions-item>
          <el-descriptions-item label="版本">v{{ currentAlgorithm.version }}</el-descriptions-item>
          <el-descriptions-item label="分类">{{ getCategoryLabel(currentAlgorithm.category) }}</el-descriptions-item>
          <el-descriptions-item label="框架">{{ currentAlgorithm.framework }}</el-descriptions-item>
          <el-descriptions-item label="资产类型">
            <el-tag :type="assetTypeTag(currentAlgorithm.asset_type)" size="small">{{ currentAlgorithm.asset_type_label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="下载量">{{ formatNumber(currentAlgorithm.downloads) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="currentAlgorithm.status === 'published' ? 'success' : currentAlgorithm.status === 'trained' ? 'warning' : 'info'" size="small">
              {{ currentAlgorithm.status === 'published' ? '已发布' : currentAlgorithm.status === 'trained' ? '待发布' : '草稿' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="标签" :span="2">
            <el-tag v-for="tag in currentAlgorithm.tags" :key="tag" size="small" style="margin-right: 4px">{{ tag }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ currentAlgorithm.description }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(currentAlgorithm.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDate(currentAlgorithm.updated_at) }}</el-descriptions-item>
          <template v-if="currentAlgorithm.metrics?.source_filename">
            <el-descriptions-item label="上传文件">{{ currentAlgorithm.metrics.source_filename }}</el-descriptions-item>
            <el-descriptions-item label="产物类型">{{ currentAlgorithm.metrics.artifact_label || currentAlgorithm.metrics.artifact_format }}</el-descriptions-item>
            <el-descriptions-item label="文件大小">{{ formatFileSize(currentAlgorithm.metrics.source_size) }}</el-descriptions-item>
            <el-descriptions-item label="文件校验">
              <el-tag type="success" size="small">{{ currentAlgorithm.metrics.validation_status === 'validated' ? '校验通过' : currentAlgorithm.metrics.validation_status }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item v-if="currentAlgorithm.metrics.registered_model_id" label="评估模型ID">
              #{{ currentAlgorithm.metrics.registered_model_id }}
            </el-descriptions-item>
            <el-descriptions-item v-if="currentAlgorithm.metrics.registered_model_id" label="模型评估">
              <el-tag type="success" size="small">已进入模型列表</el-tag>
            </el-descriptions-item>
          </template>
          <el-descriptions-item v-else label="原始文件" :span="2">
            <el-tag type="info" size="small">该记录没有保存上传文件</el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">参数配置</el-divider>
        <el-table :data="currentAlgorithm.params || []" style="width: 100%">
          <el-table-column prop="name" label="参数名" width="150" />
          <el-table-column prop="type" label="类型" width="100" />
          <el-table-column prop="default" label="默认值" width="120" />
          <el-table-column prop="required" label="必填" width="80">
            <template #default="{ row }">
              <el-tag :type="row.required ? 'danger' : 'info'" size="small">
                {{ row.required ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" />
        </el-table>

        <el-divider content-position="left">依赖环境</el-divider>
        <el-table :data="currentAlgorithm.dependencies || []" style="width: 100%">
          <el-table-column prop="name" label="包名" />
          <el-table-column prop="version" label="版本要求" width="150" />
          <el-table-column prop="type" label="类型" width="100">
            <template #default="{ row }">
              <el-tag size="small">{{ row.type }}</el-tag>
            </template>
          </el-table-column>
        </el-table>

        <el-divider content-position="left">算法说明书</el-divider>
        <div class="manual-panel">
          <template v-if="currentAlgorithm.metrics?.manual">
            <div class="manual-file">
              <el-icon><Document /></el-icon>
              <span>{{ currentAlgorithm.metrics.manual.filename }}</span>
              <span class="manual-meta">{{ formatFileSize(currentAlgorithm.metrics.manual.size_bytes) }} · {{ formatDate(currentAlgorithm.metrics.manual.uploaded_at) }}</span>
              <el-button link type="primary" @click="downloadManual(currentAlgorithm)">下载说明书</el-button>
            </div>
          </template>
          <span v-else class="manual-empty">暂未上传说明书</span>
          <el-upload class="manual-upload" :auto-upload="false" :show-file-list="false" accept=".pdf,.doc,.docx,.txt,.md,.ppt,.pptx,.xls,.xlsx" @change="uploadManual">
            <el-button type="primary" plain :loading="manualUploading">{{ currentAlgorithm.metrics?.manual ? '替换说明书' : '上传说明书' }}</el-button>
          </el-upload>
        </div>
      </template>
      <template #footer>
        <el-button @click="showDetailDialog = false">关闭</el-button>
        <el-button type="primary" :disabled="!currentAlgorithmDownloadable" @click="downloadAlgorithm(currentAlgorithm)">
          <el-icon><Download /></el-icon>
          {{ currentAlgorithm?.asset_type === 'finetune_model' ? '下载原始 PKL' : currentAlgorithm?.asset_type === 'inference_model' ? '下载原始 ONNX' : currentAlgorithm?.asset_type === 'deployment_bundle' ? '下载原始 Docker 包' : (currentAlgorithmDownloadable ? '下载原始源码' : '内置算法不可下载') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 版本列表对话框 -->
    <el-dialog v-model="showVersionsDialog" title="版本历史" width="700px" destroy-on-close>
      <el-table :data="versions" v-loading="versionsLoading" style="width: 100%">
        <el-table-column prop="version" label="版本" width="100">
          <template #default="{ row }">
            <el-tag type="info" size="small">v{{ row.version }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="更新说明" min-width="200" />
        <el-table-column prop="downloads" label="下载量" width="100">
          <template #default="{ row }">
            {{ formatNumber(row.downloads) }}
          </template>
        </el-table-column>
        <el-table-column prop="is_latest" label="最新版本" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.is_latest" type="success" size="small">最新</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="发布时间" width="160">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button text size="small" @click="downloadVersion(row)">下载</el-button>
            <el-button text size="small" type="danger" @click="deleteVersion(row)" v-if="!row.is_latest">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 依赖环境对话框 -->
    <el-dialog v-model="showDependencyDialog" :title="editingDependency ? '编辑依赖环境' : '新建依赖环境'" width="600px" destroy-on-close>
      <el-form :model="dependencyForm" label-width="120px">
        <el-form-item label="环境名称" required>
          <el-input v-model="dependencyForm.name" placeholder="如: PyTorch 2.0 环境" />
        </el-form-item>
        <el-form-item label="Python版本">
          <el-select v-model="dependencyForm.python_version" style="width: 100%">
            <el-option label="Python 3.8" value="3.8" />
            <el-option label="Python 3.9" value="3.9" />
            <el-option label="Python 3.10" value="3.10" />
            <el-option label="Python 3.11" value="3.11" />
          </el-select>
        </el-form-item>
        <el-form-item label="CUDA版本">
          <el-select v-model="dependencyForm.cuda_version" style="width: 100%" clearable>
            <el-option label="无" value="" />
            <el-option label="CUDA 11.7" value="11.7" />
            <el-option label="CUDA 11.8" value="11.8" />
            <el-option label="CUDA 12.0" value="12.0" />
            <el-option label="CUDA 12.1" value="12.1" />
          </el-select>
        </el-form-item>
        <el-form-item label="依赖包">
          <el-input v-model="dependencyForm.packages" type="textarea" rows="6" placeholder="每行一个依赖，格式：package==version&#10;例如：&#10;torch==2.0.1&#10;numpy==1.24.0&#10;pandas==2.0.0" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="dependencyForm.description" type="textarea" rows="2" placeholder="环境描述" />
        </el-form-item>
        <el-divider content-position="left">超参数</el-divider>
        <el-form-item v-for="(v, k, idx) in editForm.hyperparameters" :key="k" :label="k">
          <el-input v-model="editForm.hyperparameters[k]" style="width:180px" size="small" />
          <el-button type="danger" text size="small" @click="delete editForm.hyperparameters[k]" style="margin-left:8px">删除</el-button>
        </el-form-item>
        <el-form-item label="新增参数">
          <el-input v-model="newParamKey" placeholder="参数名" style="width:140px" size="small" />
          <el-input v-model="newParamVal" placeholder="值" style="width:120px;margin-left:8px" size="small" />
          <el-button size="small" @click="addHyperParam" style="margin-left:8px">添加</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDependencyDialog = false">取消</el-button>
        <el-button type="primary" @click="saveDependency" :loading="savingDependency">保存</el-button>
      </template>
    </el-dialog>

    <!-- 使用统计详情对话框 -->
    <el-dialog v-model="showStatsDetailDialog" title="算法使用统计详情" width="900px" destroy-on-close>
      <template v-if="currentAlgorithm">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="算法名称">{{ currentAlgorithm.name }}</el-descriptions-item>
          <el-descriptions-item label="总下载量">{{ formatNumber(currentAlgorithm.stats?.total_downloads || 0) }}</el-descriptions-item>
          <el-descriptions-item label="总运行次数">{{ formatNumber(currentAlgorithm.stats?.total_runs || 0) }}</el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">最近使用记录</el-divider>
        <el-table :data="currentAlgorithm.stats?.recent_usage || []" style="width: 100%">
          <el-table-column prop="user" label="用户" width="120" />
          <el-table-column prop="project" label="项目" width="150" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
                {{ row.status === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="runtime" label="运行时间" width="120">
            <template #default="{ row }">
              {{ row.runtime }}秒
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="时间" width="160">
            <template #default="{ row }">
              {{ formatDate(row.created_at) }}
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>
  </div>
    
    <!-- 编辑算法对话框 -->
    <el-dialog v-model="showEditDialog" title="编辑算法" width="500px" destroy-on-close>
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="editForm.category" style="width:100%">
            <el-option label="钢铁工艺" value="steel_process" />
            <el-option label="能效优化" value="energy_optimization" />
            <el-option label="新能源" value="renewable_energy" />
            <el-option label="负荷预测" value="load_forecasting" />
            <el-option label="预测性维护" value="predictive_maintenance" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务类型">
          <el-select v-model="editForm.task_type" style="width:100%">
            <el-option label="分类" value="classification" />
            <el-option label="回归" value="regression" />
            <el-option label="时序预测" value="time_series_forecasting" />
            <el-option label="控制优化算法" value="control_optimization" />
          </el-select>
        </el-form-item>
        <el-form-item label="算法实现">
          <el-select v-model="editForm.algorithm_type" style="width:100%">
            <el-option label="Random Forest" value="random_forest" />
            <el-option label="XGBoost" value="xgboost" />
            <el-option label="LightGBM" value="lightgbm" />
            <el-option label="MLP 神经网络" value="mlp" />
          </el-select>
        </el-form-item>
        <el-form-item label="适配器">
          <el-tag v-if="resolvedEditAdapter" type="success">{{ resolvedEditAdapter.name }}</el-tag>
          <el-tag v-else type="warning">无法自动匹配</el-tag>
        </el-form-item>
        <el-form-item label="运行环境">
          <el-select v-model="editForm.dependency_id" clearable placeholder="不选择则使用平台默认Python环境" style="width:100%">
            <el-option v-for="d in dependencies" :key="d.id" :label="`${d.name}（Python ${d.python_version}）`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-divider content-position="left">超参数</el-divider>
        <el-form-item v-for="(v, k, idx) in editForm.hyperparameters" :key="k" :label="k">
          <el-input v-model="editForm.hyperparameters[k]" style="width:180px" size="small" />
          <el-button type="danger" text size="small" @click="delete editForm.hyperparameters[k]" style="margin-left:8px">删除</el-button>
        </el-form-item>
        <el-form-item label="新增参数">
          <el-input v-model="newParamKey" placeholder="参数名" style="width:140px" size="small" />
          <el-input v-model="newParamVal" placeholder="值" style="width:120px;margin-left:8px" size="small" />
          <el-button size="small" @click="addHyperParam" style="margin-left:8px">添加</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 控制算法仿真结果 -->
    <el-dialog v-model="showControlResultsDialog" title="控制仿真结果" width="900px" destroy-on-close>
      <el-alert
        :title="controlResultAlgorithm ? `算法：${controlResultAlgorithm.name}` : '控制仿真结果'"
        description="每次控制仿真会保存运行日志、JSON 参数/结果以及生成的图表，可在下方直接下载。"
        type="info" :closable="false" show-icon style="margin-bottom:16px"
      />
      <el-table :data="controlRuns" v-loading="loadingControlRuns" border stripe empty-text="尚无控制仿真记录" style="width:100%">
        <el-table-column prop="name" label="运行名称" min-width="170" />
        <el-table-column label="数据集" min-width="150">
          <template #default="{ row }">#{{ row.dataset_id }} {{ row.dataset_name }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><el-tag :type="row.status === 'success' ? 'success' : row.status === 'running' ? 'warning' : 'danger'">{{ row.status === 'success' ? '成功' : row.status === 'running' ? '运行中' : '失败' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ row.duration_ms ? `${(Number(row.duration_ms) / 1000).toFixed(1)} 秒` : '-' }}</template>
        </el-table-column>
        <el-table-column label="结果文件" min-width="250">
          <template #default="{ row }">
            <el-button v-for="(output, outputIndex) in row.outputs || []" :key="output.path" link type="primary" size="small" @click="downloadControlOutput(row, outputIndex, output)">{{ output.name }}</el-button>
            <span v-if="!(row.outputs || []).length">-</span>
          </template>
        </el-table-column>
        <el-table-column label="运行时间" width="170">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <el-collapse v-if="controlRuns.length" style="margin-top:16px">
        <el-collapse-item v-for="run in controlRuns" :key="run.id" :title="`${run.name} · 结果摘要与日志`" :name="run.id">
          <div v-if="Object.keys(run.json_results || {}).length" class="control-result-json">{{ JSON.stringify(run.json_results, null, 2) }}</div>
          <div v-else class="el-upload__tip">该次运行没有结构化 JSON 结果。</div>
          <div v-if="run.runtime_log_tail || run.stdout_tail" class="control-result-log">{{ run.runtime_log_tail || run.stdout_tail }}</div>
          <div v-if="run.status === 'failed' && run.stderr_tail" class="control-result-log error">{{ run.stderr_tail }}</div>
        </el-collapse-item>
      </el-collapse>
      <template #footer><el-button @click="showControlResultsDialog = false">关闭</el-button></template>
    </el-dialog>

    <!-- 训练模型对话框 -->
    <el-dialog v-model="showTrainDialog" :title="isControlAlgorithm ? '配置控制仿真' : (isResumeTraining ? '配置继续训练' : '配置算法训练')" width="720px" destroy-on-close>
      <el-alert
        v-if="trainTarget"
        :title="isControlAlgorithm ? '将以完整过程数据运行上传的控制算法项目' : (isResumeTraining
          ? (trainTarget.metrics?.training_capability_label || '可继续训练')
          : trainTarget.asset_type === 'uploaded_source' ? '将执行上传的训练源码' : '已配置内置训练适配器')"
        :description="isControlAlgorithm
          ? `算法：${trainTarget.name}。控制仿真不要求预测目标列，不生成伪模型；平台会保存日志、JSON结果和图表。`
          : (isResumeTraining
            ? `基础模型：${trainTarget.name} · ${trainTarget.metrics?.training_capability_reason || trainAdapterName}`
            : trainTarget.asset_type === 'uploaded_source'
              ? sourceManagesData
                ? `算法：${trainTarget.name}。完整数据集将原样交给源码，目标、时间和特征配置由源码管理。`
                : `算法：${trainTarget.name}。源码没有完整声明数据配置，请选择目标列和特征列作为运行兜底。`
              : `算法：${trainTarget.name} · 适配器：${trainAdapterName}`)"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom:16px"
      />
      <el-form :model="trainForm" label-width="120px" v-loading="loadingDatasets">
        <el-form-item label="任务名称" required>
          <el-input v-model="trainForm.name" placeholder="请输入训练任务名称" />
        </el-form-item>
        <el-form-item v-if="!isControlAlgorithm" label="模型分类">
          <el-select v-model="trainForm.model_folder_id" clearable placeholder="未选择则归入未分类" style="width:100%">
            <el-option label="未分类" :value="null" />
            <el-option v-for="folder in modelFolders" :key="folder.id" :label="folder.name" :value="folder.id" />
          </el-select>
          <div class="el-upload__tip">训练成功后，模型会归入这里选择的分类文件夹。</div>
        </el-form-item>
        <template v-if="isControlAlgorithm">
          <el-form-item label="运行方式" required>
            <el-radio-group v-model="trainForm.simulation_mode" @change="onControlSimulationModeChange">
              <el-radio-button v-for="mode in availableControlModes" :key="mode" :value="mode">{{ controlModeLabel(mode) }}</el-radio-button>
            </el-radio-group>
            <div class="el-upload__tip">{{ controlModeDescription }}</div>
          </el-form-item>
        </template>
        <el-form-item v-if="!isControlAlgorithm || trainForm.simulation_mode === 'dataset_replay'" :label="isControlAlgorithm ? '过程数据集' : '训练数据集'" required>
          <el-select
            v-model="trainForm.dataset_id"
            filterable
            placeholder="请选择数据集"
            style="width:100%"
            @change="onTrainDatasetChange"
          >
            <el-option
              v-for="dataset in compatibleDatasets"
              :key="dataset.id"
              :label="`#${dataset.id} ${dataset.name}（${dataset.row_count || '未知'}行）`"
              :value="dataset.id"
            />
          </el-select>
        </el-form-item>
        <template v-if="isControlAlgorithm && trainForm.simulation_mode === 'dataset_replay'">
          <el-form-item label="时间列">
            <el-select v-model="trainForm.time_column" filterable clearable placeholder="可选；时序控制项目建议选择" style="width:100%" :loading="loadingColumns">
              <el-option v-for="column in allDatasetColumns" :key="column" :label="column" :value="column" />
            </el-select>
          </el-form-item>
          <el-form-item label="被控变量/状态">
            <el-select v-model="trainForm.state_columns" multiple filterable placeholder="例如 NOx_out" style="width:100%">
              <el-option v-for="column in allDatasetColumns" :key="column" :label="column" :value="column" />
            </el-select>
          </el-form-item>
          <el-form-item label="控制变量">
            <el-select v-model="trainForm.control_columns" multiple filterable placeholder="例如 NH3_flow" style="width:100%">
              <el-option v-for="column in allDatasetColumns" :key="column" :label="column" :value="column" />
            </el-select>
          </el-form-item>
          <el-form-item label="扰动变量">
            <el-select v-model="trainForm.disturbance_columns" multiple filterable placeholder="例如 NOx_in、Flow、O2" style="width:100%">
              <el-option v-for="column in allDatasetColumns" :key="column" :label="column" :value="column" />
            </el-select>
          </el-form-item>
          <el-form-item label="设定值(可选)">
            <el-input-number v-model="trainForm.setpoint" :precision="4" :step="0.1" style="width:180px" />
            <span class="el-upload__tip" style="margin-left:10px">会以配置文件和环境变量传给支持该约定的控制脚本。</span>
          </el-form-item>
          <el-form-item label="扩展参数">
            <el-input v-model="trainForm.control_parameters" type="textarea" :rows="4" placeholder='可选 JSON，例如 {"prediction_horizon": 10}' />
          </el-form-item>
        </template>
        <template v-else-if="isControlAlgorithm && trainForm.simulation_mode === 'realtime_stream'">
          <el-alert title="默认读取算法包内 mqtt.json 的服务器与订阅主题；账号、密码不会在页面展示或写入运行记录。实时运行只保存结果，不会向现场下发控制指令。" type="warning" :closable="false" style="margin-bottom:16px" />
          <el-form-item label="数据接入方式">
            <el-radio-group v-model="trainForm.realtime_transport">
              <el-radio-button value="mqtt">使用算法包 MQTT 配置</el-radio-button>
              <el-radio-button value="zmq">高级：自定义 ZMQ</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <template v-if="trainForm.realtime_transport === 'mqtt'">
            <el-alert title="运行时会自动读取 conf/mqtt.json（兼容 bin/mqtt.json），并按 subTopic 订阅实时数据。若连接失败，完整错误会保存到仿真任务日志。" type="info" :closable="false" style="margin-bottom:16px" />
          </template>
          <template v-else>
            <el-alert title="仅用于未提供 mqtt.json 的历史项目。请确认外部 ZMQ 地址可从平台服务器访问。" type="warning" :closable="false" style="margin-bottom:16px" />
            <el-form-item label="实时输入地址" required>
              <el-input v-model="trainForm.realtime_input_endpoint" placeholder="例如 tcp://10.0.0.12:40113" />
            </el-form-item>
            <el-form-item label="订阅主题">
              <el-input v-model="trainForm.realtime_input_topic" placeholder="留空接收全部消息；例如 all" />
            </el-form-item>
            <el-form-item label="发布控制输出">
              <el-switch v-model="trainForm.realtime_publish_outputs" />
              <span class="el-upload__tip" style="margin-left:10px">默认关闭。开启后才会把算法输出发布到外部地址。</span>
            </el-form-item>
            <el-form-item v-if="trainForm.realtime_publish_outputs" label="输出地址" required>
              <el-input v-model="trainForm.realtime_output_endpoint" placeholder="例如 tcp://10.0.0.12:40204" />
            </el-form-item>
          </template>
          <el-form-item label="运行时长">
            <el-input-number v-model="trainForm.realtime_duration_seconds" :min="5" :max="1500" :step="5" />
            <span class="el-upload__tip" style="margin-left:10px">受限实时会话最长 25 分钟，结果会保存到仿真任务。</span>
          </el-form-item>
        </template>
        <template v-else-if="isControlAlgorithm && trainForm.simulation_mode === 'parameterized'">
          <el-form-item label="控制配置文件" required>
            <div style="width:100%">
              <el-button size="small" @click="downloadControlConfigTemplate">下载默认配置模板</el-button>
              <el-upload :auto-upload="false" :limit="1" accept=".json,application/json" :on-change="selectControlConfigFile" :on-remove="clearControlConfigFile" style="display:inline-flex;margin-left:10px">
                <el-button type="primary" plain size="small">上传配置文件</el-button>
              </el-upload>
              <div class="el-upload__tip" style="margin-top:8px">必须上传 JSON 配置。请下载项目模板后修改；平台仅在本次临时仿真中使用该文件，不会覆盖算法包内原配置。</div>
              <div v-if="controlConfigFileName" class="el-upload__tip" style="color:#409eff">已加载：{{ controlConfigFileName }}</div>
            </div>
          </el-form-item>
          <el-alert title="控制器参数、过程模型和仿真初始值均以配置文件为准。可在 _foundation_simulation 中设置 steps、sample_time_s、setpoint、initial_cv、initial_mv、disturbance。" type="info" :closable="false" style="margin-bottom:16px" />
        </template>
        <el-form-item v-else-if="sourceManagesData" label="源码数据配置">
          <el-descriptions :column="1" border style="width:100%">
            <el-descriptions-item label="预测目标">{{ trainSourceDataContract.target_column }}</el-descriptions-item>
            <el-descriptions-item v-if="trainSourceDataContract.time_column" label="时间列">{{ trainSourceDataContract.time_column }}</el-descriptions-item>
            <el-descriptions-item label="特征列">源码已声明 {{ trainSourceDataContract.feature_columns?.length || 0 }} 列</el-descriptions-item>
            <el-descriptions-item label="传入方式">完整数据集原样传入，不裁剪辅助列</el-descriptions-item>
          </el-descriptions>
        </el-form-item>
        <el-form-item v-if="!isControlAlgorithm && !sourceManagesData" label="目标列" required>
          <el-select
            v-model="trainForm.target_column"
            filterable
            placeholder="请选择数值目标列"
            style="width:100%"
            :loading="loadingColumns"
            @change="onTargetColumnChange"
          >
            <el-option v-for="column in datasetColumns" :key="column" :label="column" :value="column" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!isControlAlgorithm && !sourceManagesData" label="特征列(可选)">
          <el-select v-model="trainForm.feature_columns" multiple filterable placeholder="留空=除目标列外所有数值列都作特征" style="width:100%">
            <el-option v-for="column in availableFeatureColumns" :key="column" :label="column" :value="column" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!isControlAlgorithm" label="任务类型">
          <el-input :model-value="trainTaskTypeLabel" disabled />
        </el-form-item>
        <el-form-item v-if="!isControlAlgorithm" label="超参数配置">
          <div style="width:100%">
            <el-radio-group v-model="trainForm.hyperparameter_mode" @change="onTrainParamModeChange">
              <el-radio-button value="default">使用默认值</el-radio-button>
              <el-radio-button v-if="trainDetectedParamSchema.length" value="detected">识别并确认</el-radio-button>
              <el-radio-button value="manual">手动填写</el-radio-button>
              <el-radio-button value="file">上传参数文件</el-radio-button>
            </el-radio-group>
            <el-alert
              v-if="trainForm.hyperparameter_mode === 'default'"
              :title="trainDefaultParamRows.length ? '使用算法源码声明的默认参数' : trainTarget?.asset_type === 'uploaded_source' ? '使用源码内部默认参数' : '使用平台训练器默认参数'"
              type="info" :closable="false" style="margin-top:10px"
            />
            <div v-if="trainForm.hyperparameter_mode === 'default' && trainDefaultParamRows.length" class="source-default-params">
              <div v-for="item in trainDefaultParamRows" :key="item.key" class="source-default-param">
                <span>{{ item.label }}</span>
                <code>{{ item.value }}</code>
              </div>
            </div>
            <div v-else-if="trainForm.hyperparameter_mode === 'detected'" class="detected-parameter-form">
              <el-alert title="以下字段由平台静态识别；确认后会写入本次训练的临时源码副本，不修改原始上传代码。" type="warning" :closable="false" style="margin-top:10px" />
              <div v-for="parameter in trainDetectedParamSchema" :key="parameter.key" class="detected-parameter-row">
                <span class="detected-parameter-label">{{ parameter.key }}</span>
                <el-select v-if="parameter.type === 'select'" v-model="trainDetectedParameterValues[parameter.key]" style="width:220px">
                  <el-option v-for="option in parameter.options || []" :key="String(option)" :label="String(option)" :value="option" />
                </el-select>
                <el-switch v-else-if="parameter.type === 'boolean'" v-model="trainDetectedParameterValues[parameter.key]" />
                <el-input-number v-else-if="parameter.type === 'number' || parameter.type === 'integer'" v-model="trainDetectedParameterValues[parameter.key]" :precision="parameter.type === 'integer' ? 0 : undefined" :step="parameter.type === 'integer' ? 1 : 0.1" style="width:180px" />
                <el-input v-else v-model="trainDetectedParameterValues[parameter.key]" style="width:220px" />
                <span class="el-upload__tip">{{ parameter.origin === 'argparse' ? '命令行参数' : parameter.origin === 'top_level_constant' ? '源码常量' : '源码默认值' }}</span>
              </div>
            </div>
            <el-input
              v-else-if="trainForm.hyperparameter_mode === 'manual'"
              v-model="trainForm.hyperparams" type="textarea" :rows="8"
              placeholder="支持 JSON，或每行 key=value" style="margin-top:10px"
            />
            <el-upload
              v-else :auto-upload="false" :limit="1"
              accept=".json,.txt,.yaml,.yml,.cfg,.conf"
              :on-change="selectTrainParamFile" :on-remove="clearTrainParamFile"
              style="margin-top:10px"
            >
              <el-button>选择参数文件</el-button>
              <template #tip><div class="el-upload__tip">最大1MB；支持JSON或扁平的 key=value / key: value</div></template>
            </el-upload>
            <el-input
              v-if="trainForm.hyperparameter_mode === 'file' && trainForm.hyperparams"
              v-model="trainForm.hyperparams" type="textarea" :rows="8" readonly
              :placeholder="trainParamFileName" style="margin-top:8px"
            />
          </div>
        </el-form-item>
      </el-form>
      <div v-if="compatibleDatasets.length===0 && !loadingDatasets && (!isControlAlgorithm || trainForm.simulation_mode === 'dataset_replay')" style="text-align:center;padding:12px;color:#999">暂无可训练数据集</div>
      <el-alert v-if="trainResult" :title="trainResult" :type="trainError ? 'error' : 'success'" :closable="false" style="margin-top:16px" />
      <template #footer>
        <el-button @click="showTrainDialog=false">取消</el-button>
        <el-button type="primary" :loading="trainingSubmitting" :disabled="(requiresTrainDataset && !trainForm.dataset_id) || trainError || (isControlAlgorithm && trainForm.simulation_mode === 'parameterized' && !controlConfigData) || (!isControlAlgorithm && !sourceManagesData && !trainForm.target_column)" @click="doTrain">{{ isControlAlgorithm ? '运行控制仿真' : '开始训练' }}</el-button>
      </template>
    </el-dialog>
</template>



<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Collection,
  Plus,
  Upload,
  Download,
  Search,
  Box,
  More,
  TrendCharts,
  UploadFilled,
  Document
} from '@element-plus/icons-vue'
import { api } from '@/api'
import { get } from '@/api/legacy'
import { modelGroupApi } from '@/api/model'
import { parseHyperparameterText, readHyperparameterFile, type HyperparameterMode } from '@/utils/hyperparameters'
import dayjs from 'dayjs'
import SandboxEnvironmentManager from '@/components/SandboxEnvironmentManager.vue'

// 状态
const router = useRouter()
const activeTab = ref('algorithms')
const algorithmsLoading = ref(false)
const dependenciesLoading = ref(false)
const statsLoading = ref(false)
const versionsLoading = ref(false)
const uploading = ref(false)
const savingDependency = ref(false)

const showUploadDialog = ref(false)
const showDetailDialog = ref(false)
const showVersionsDialog = ref(false)
const showDependencyDialog = ref(false)
const showStatsDetailDialog = ref(false)
const showOnnxRunDialog = ref(false)
const onnxRunning = ref(false)
const onnxRunModel = ref<any>(null)
const onnxRunInput = ref('')
const onnxRunResult = ref<any>(null)
const showDockerDialog = ref(false)
const dockerActionLoading = ref(false)
const dockerPredicting = ref(false)
const dockerModel = ref<any>(null)
const dockerJob = ref<any>({ status: 'not_built', stage: '尚未构建', progress: 0, logs: [] })
const dockerRunInput = ref('')
const dockerRunResult = ref<any>(null)
let dockerPoller: number | undefined

const editingDependency = ref<any>(null)
const currentAlgorithm = ref<any>(null)
const currentAlgorithmDownloadable = computed(() => Boolean(currentAlgorithm.value?.downloadable))
const manualUploading = ref(false)
const showEditDialog = ref(false)
const editingAlgorithm = ref<any>(null)
const editForm = ref<any>({ name: '', description: '', category: '', task_type: '', algorithm_type: '', difficulty: 'intermediate', dependency_id: null as number | null, hyperparameters: {} })

const searchQuery = ref('')
const filterCategory = ref('')
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const statsDateRange = ref(null as any)

const stats = ref([
  { key: 'total', label: '算法总数', value: '0', icon: 'Collection', bgColor: 'rgba(59, 130, 246, 0.15)' },
  { key: 'published', label: '已发布', value: '0', icon: 'Box', bgColor: 'rgba(16, 185, 129, 0.15)' },
  { key: 'downloads', label: '总下载量', value: '0', icon: 'Download', bgColor: 'rgba(245, 158, 11, 0.15)' },
  { key: 'environments', label: '可运行环境', value: '0', icon: 'Setting', bgColor: 'rgba(139, 92, 246, 0.15)' }
])

const algorithms = ref<any[]>([])
const dependencies = ref<any[]>([])
const usageStats = ref<any[]>([])
const versions = ref<any[]>([])
const trainingAdapters = ref<any[]>([])

const uploadForm = ref({
  name: '',
  category: 'custom',
  model_mode: 'trainable' as 'trainable' | 'control_simulation',
  task_type: '',
  algorithm_type: '',
  framework: '',
  version: '1.0.0',
  description: '',
  fileList: [] as any[],
  dependency_id: null as number | null,
  tags: [] as string[],
  entry_point: '',
  params_schema: ''
})
const isOnnxUpload = computed(() => String(uploadForm.value.fileList[0]?.name || '').toLowerCase().endsWith('.onnx'))
const isProjectUpload = computed(() => String(uploadForm.value.fileList[0]?.name || '').toLowerCase().endsWith('.zip'))
const isPythonUpload = computed(() => { const name = String(uploadForm.value.fileList[0]?.name || '').toLowerCase(); return name.endsWith('.py') || name.endsWith('.zip') })
const isControlSimulationUpload = computed(() => isPythonUpload.value && uploadForm.value.model_mode === 'control_simulation')
const isTrainableSourceUpload = computed(() => isPythonUpload.value && !isControlSimulationUpload.value)
const isPklUpload = computed(() => String(uploadForm.value.fileList[0]?.name || '').toLowerCase().endsWith('.pkl'))
const isDockerUpload = computed(() => {
  const name = String(uploadForm.value.fileList[0]?.name || '').toLowerCase()
  return name.endsWith('.tar.gz') || name.endsWith('.tgz')
})
const uploadDetection = computed(() => {
  if (isPythonUpload.value) return {
    type: 'success' as const,
    title: 'Python训练源码',
    description: '平台将自动识别代码框架和参数，并在训练时真实执行该源码。',
  }
  if (isPklUpload.value) return {
    type: 'warning' as const,
    title: 'PKL封装模型',
    description: '仅接受平台能够验证来源且确认支持继续训练的PKL，框架和训练能力由系统识别。',
  }
  if (isOnnxUpload.value) return {
    type: 'info' as const,
    title: 'ONNX推理模型',
    description: '上传后可运行、评估和部署，但不能继续训练。',
  }
  if (isDockerUpload.value) return {
    type: 'info' as const,
    title: 'Docker部署包',
    description: '平台将校验包结构并重建安全运行文件，上传后可构建和运行。',
  }
  return null
})
const onnxRunShape = computed(() => onnxRunModel.value?.metrics?.onnx_input_shapes?.[0] || [])
const onnxRunExpectedFeatures = computed(() => {
  const shape = onnxRunShape.value
  const last = Array.isArray(shape) ? shape[shape.length - 1] : null
  return typeof last === 'number' && last > 0 ? last : null
})
const onnxRunShapeText = computed(() => onnxRunShape.value.length ? `[${onnxRunShape.value.map((value:any) => value ?? '?').join(', ')}]` : '未知')
const onnxRunPlaceholder = computed(() => onnxRunExpectedFeatures.value
  ? `请输入 ${onnxRunExpectedFeatures.value} 个特征，例如：\n[${Array(onnxRunExpectedFeatures.value).fill(0).join(', ')}]`
  : '例如：\n[1, 10]')
const onnxRunRows = computed(() => {
  const inputs = onnxRunResult.value?.input_samples || []
  const outputs = onnxRunResult.value?.predictions || []
  return inputs.map((input:any, index:number) => ({
    sample: index + 1,
    input: JSON.stringify(input),
    output: JSON.stringify(outputs[index] ?? outputs)
  }))
})
const dockerStatusLabel = computed(() => ({
  not_built: '尚未构建', queued: '等待构建', building: '构建中', success: '构建成功',
  failed: '构建失败', running: '临时容器运行中', stopped: '临时容器已停止'
} as Record<string,string>)[dockerJob.value?.status] || dockerJob.value?.status || '未知')
const dockerStatusType = computed(() => ({
  not_built: 'info', queued: 'warning', building: 'warning', success: 'success',
  failed: 'danger', running: 'success', stopped: 'info'
} as Record<string,any>)[dockerJob.value?.status] || 'info')
const dockerLogs = computed(() => (dockerJob.value?.logs || []).join('\n') || '暂无构建日志')
const dockerRunExpectedFeatures = computed(() => {
  const shape = dockerModel.value?.metrics?.onnx_input_shapes?.[0] || []
  const last = shape[shape.length - 1]
  return typeof last === 'number' && last > 0 ? last : null
})
const dockerRunPlaceholder = computed(() => dockerRunExpectedFeatures.value
  ? `请输入 ${dockerRunExpectedFeatures.value} 个特征，例如：\n[${Array(dockerRunExpectedFeatures.value).fill(0).join(', ')}]`
  : '例如：\n[1, 10]')
const dockerRunRows = computed(() => {
  const inputs = dockerRunResult.value?.input_samples || []
  const outputs = dockerRunResult.value?.predictions || []
  return inputs.map((input:any, index:number) => ({
    sample: index + 1,
    input: JSON.stringify(input),
    output: JSON.stringify(outputs[index] ?? outputs)
  }))
})

const resolvedEditAdapter = computed(() => trainingAdapters.value.find(
  (item: any) => item.task_type === editForm.value.task_type
    && item.algorithm_type === editForm.value.algorithm_type
))

async function loadTrainingAdapters() {
  const res = await api.algorithmStore.getAdapters()
  trainingAdapters.value = res.items || []
}

const dependencyForm = ref({
  name: '',
  python_version: '3.10',
  cuda_version: '',
  packages: '',
  description: ''
})

// 方法
function formatDate(date: string) {
  if (!date) return '-'
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

function formatNumber(num: number) {
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K'
  return num?.toString() || '0'
}

function formatFileSize(bytes: number) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function onUploadModelModeChange(mode: 'trainable' | 'control_simulation') {
  if (mode === 'control_simulation') {
    uploadForm.value.task_type = 'control_optimization'
  } else if (uploadForm.value.task_type === 'control_optimization') {
    uploadForm.value.task_type = ''
  }
}

function getCategoryLabel(category: string) {
  const map: Record<string, string> = {
    classification: '分类算法',
    regression: '回归算法',
    clustering: '聚类算法',
    dimensionality_reduction: '降维算法',
    reinforcement_learning: '强化学习',
    deep_learning: '深度学习',
    time_series_forecasting: '时序预测',
    control_optimization: '控制优化算法'
  }
  return map[category] || category
}

function assetTypeTag(assetType: string) {
  const map: Record<string, string> = {
    builtin: 'primary', uploaded_source: 'success', finetune_model: 'warning',
    inference_model: 'info', deployment_bundle: 'primary'
  }
  return map[assetType] || 'info'
}

async function loadAlgorithms() {
  algorithmsLoading.value = true
  try {
    const res = await api.algorithmStore.getList({
      page: currentPage.value,
      size: pageSize.value,
      search: searchQuery.value,
      category: filterCategory.value
    })
    algorithms.value = res.items || []
    total.value = res.total || 0

    stats.value[0].value = (res.total || algorithms.value.length).toString()
    stats.value[1].value = algorithms.value.filter(a => a.status === 'published').length.toString()
    stats.value[2].value = formatNumber(algorithms.value.reduce((sum, a) => sum + (a.downloads || 0), 0))
  } catch (error) {
    console.error('加载算法失败:', error)
    algorithms.value = []
    total.value = 0
    stats.value[0].value = '0'
    stats.value[1].value = '0'
    stats.value[2].value = '0'
  } finally {
    algorithmsLoading.value = false
  }
}

async function loadDependencies() {
  dependenciesLoading.value = true
  try {
    const res = await api.algorithmStore.getRuntimeBuilds()
    dependencies.value = (res.items || []).filter((item: any) => item.available)
    stats.value[3].value = dependencies.value.length.toString()
  } catch (error) {
    console.error('加载依赖环境失败:', error)
    dependencies.value = []
    stats.value[3].value = '0'
  } finally {
    dependenciesLoading.value = false
  }
}

function handleReadyEnvironments(rows: any[]) {
  dependencies.value = rows
  stats.value[3].value = rows.length.toString()
}

async function loadUsageStats() {
  statsLoading.value = true
  try {
    const res = await api.algorithmStore.getUsageStats({
      start_date: statsDateRange.value?.[0],
      end_date: statsDateRange.value?.[1]
    })
    usageStats.value = res.stats || []
  } catch (error) {
    console.error('加载使用统计失败:', error)
    usageStats.value = []
  } finally {
    statsLoading.value = false
  }
}

function handleFileChange(file: any) {
  const originalName = String(file.raw?.name || '')
  const name = originalName.toLowerCase()
  const isDocker = name.endsWith('.tar.gz') || name.endsWith('.tgz')
  const isSource = name.endsWith('.py') || name.endsWith('.zip')
  if (!isSource && !name.endsWith('.pkl') && !name.endsWith('.onnx') && !isDocker) {
    uploadForm.value.fileList = []
    ElMessage.error('算法商店只允许上传 .py、.zip算法项目、可继续训练的 .pkl、.onnx 或Docker .tar.gz部署包')
    return
  }
  uploadForm.value.fileList = [file.raw]
  if (!uploadForm.value.name.trim()) {
    uploadForm.value.name = originalName.replace(/\.tar\.gz$/i, '').replace(/\.(py|zip|pkl|onnx|tgz)$/i, '')
  }
  uploadForm.value.dependency_id = null
  if (isSource) {
    uploadForm.value.framework = 'auto'
    uploadForm.value.algorithm_type = 'auto'
  } else if (name.endsWith('.pkl')) {
    uploadForm.value.framework = 'auto'
    uploadForm.value.algorithm_type = 'packaged_model'
  } else if (name.endsWith('.onnx')) {
    uploadForm.value.framework = 'onnx'
    uploadForm.value.algorithm_type = 'onnx_inference'
  } else if (isDocker) {
    uploadForm.value.framework = 'docker'
    uploadForm.value.algorithm_type = 'docker_bundle'
  }
}

function openOnnxRun(row: any) {
  onnxRunModel.value = row
  onnxRunInput.value = ''
  onnxRunResult.value = null
  showOnnxRunDialog.value = true
}

function fillOnnxRunExample() {
  if (onnxRunExpectedFeatures.value) {
    onnxRunInput.value = JSON.stringify([Array(onnxRunExpectedFeatures.value).fill(0)], null, 2)
  }
}

function parseOnnxRunInput() {
  if (!onnxRunInput.value.trim()) throw new Error('请输入模型输入数据')
  let parsed: any
  try { parsed = JSON.parse(onnxRunInput.value) } catch { throw new Error('输入数据不是有效的 JSON') }
  if (Array.isArray(parsed) && parsed.every((value:any) => typeof value === 'number')) parsed = [parsed]
  if (!Array.isArray(parsed) || !parsed.length || !parsed.every((row:any) => Array.isArray(row) && row.length)) {
    throw new Error('输入必须是一维或二维数值数组')
  }
  const width = parsed[0].length
  if (!parsed.every((row:any) => row.length === width && row.every((value:any) => typeof value === 'number' && Number.isFinite(value)))) {
    throw new Error('每行特征数量必须一致，且只能包含有限数值')
  }
  if (onnxRunExpectedFeatures.value && width !== onnxRunExpectedFeatures.value) {
    throw new Error(`当前模型需要 ${onnxRunExpectedFeatures.value} 个特征，输入了 ${width} 个`)
  }
  return parsed
}

async function runOnnxModel() {
  let X: any[]
  try { X = parseOnnxRunInput() } catch (error:any) { ElMessage.warning(error.message); return }
  onnxRunning.value = true
  try {
    onnxRunResult.value = await api.algorithmStore.predict(onnxRunModel.value.id, { X })
    ElMessage.success('ONNX 模型运行成功')
  } catch (error:any) {
    ElMessage.error('运行失败: ' + (error.response?.data?.detail || error.message || '未知错误'))
  } finally {
    onnxRunning.value = false
  }
}

function stopDockerPolling() {
  if (dockerPoller !== undefined) {
    window.clearInterval(dockerPoller)
    dockerPoller = undefined
  }
}

async function refreshDockerStatus(showError = false) {
  if (!dockerModel.value) return
  try {
    dockerJob.value = await api.algorithmStore.getDockerStatus(dockerModel.value.id)
    if (!['queued', 'building'].includes(dockerJob.value.status)) stopDockerPolling()
  } catch (error:any) {
    stopDockerPolling()
    if (showError) ElMessage.error('读取Docker状态失败: ' + (error.response?.data?.detail || error.message))
  }
}

function startDockerPolling() {
  stopDockerPolling()
  dockerPoller = window.setInterval(() => refreshDockerStatus(), 1000)
}

async function openDockerBundle(row:any) {
  dockerModel.value = row
  dockerJob.value = { status: 'not_built', stage: '尚未构建', progress: 0, logs: [] }
  dockerRunInput.value = ''
  dockerRunResult.value = null
  showDockerDialog.value = true
  await refreshDockerStatus(true)
  if (['queued', 'building'].includes(dockerJob.value.status)) startDockerPolling()
}

function closeDockerDialog() {
  stopDockerPolling()
}

async function buildDockerImage() {
  if (!dockerModel.value) return
  dockerActionLoading.value = true
  dockerRunResult.value = null
  try {
    dockerJob.value = await api.algorithmStore.buildDocker(dockerModel.value.id)
    startDockerPolling()
    ElMessage.success('Docker构建任务已创建')
  } catch (error:any) {
    ElMessage.error('创建构建任务失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    dockerActionLoading.value = false
  }
}

async function runDockerContainer() {
  if (!dockerModel.value) return
  dockerActionLoading.value = true
  try {
    dockerJob.value = await api.algorithmStore.runDocker(dockerModel.value.id)
    ElMessage.success('临时Docker容器已启动并通过健康检查')
  } catch (error:any) {
    ElMessage.error('容器启动失败: ' + (error.response?.data?.detail || error.message))
    await refreshDockerStatus()
  } finally {
    dockerActionLoading.value = false
  }
}

async function stopDockerContainer() {
  if (!dockerModel.value) return
  dockerActionLoading.value = true
  try {
    dockerJob.value = await api.algorithmStore.stopDocker(dockerModel.value.id)
    ElMessage.success('临时Docker容器已停止并删除')
  } catch (error:any) {
    ElMessage.error('停止容器失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    dockerActionLoading.value = false
  }
}

function parseDockerRunInput() {
  if (!dockerRunInput.value.trim()) throw new Error('请输入模型输入数据')
  let parsed:any
  try { parsed = JSON.parse(dockerRunInput.value) } catch { throw new Error('输入数据不是有效的 JSON') }
  if (Array.isArray(parsed) && parsed.every((value:any) => typeof value === 'number')) parsed = [parsed]
  if (!Array.isArray(parsed) || !parsed.length || !parsed.every((row:any) => Array.isArray(row) && row.length)) {
    throw new Error('输入必须是一维或二维数值数组')
  }
  const width = parsed[0].length
  if (!parsed.every((row:any) => row.length === width && row.every((value:any) => typeof value === 'number' && Number.isFinite(value)))) {
    throw new Error('每行特征数量必须一致，且只能包含有限数值')
  }
  if (dockerRunExpectedFeatures.value && width !== dockerRunExpectedFeatures.value) {
    throw new Error(`当前模型需要 ${dockerRunExpectedFeatures.value} 个特征，输入了 ${width} 个`)
  }
  return parsed
}

async function predictWithDocker() {
  let X:any[]
  try { X = parseDockerRunInput() } catch (error:any) { ElMessage.warning(error.message); return }
  dockerPredicting.value = true
  try {
    dockerRunResult.value = await api.algorithmStore.predictDocker(dockerModel.value.id, { X })
    ElMessage.success('Docker容器预测成功')
  } catch (error:any) {
    ElMessage.error('Docker预测失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    dockerPredicting.value = false
  }
}

async function uploadAlgorithm() {
  const sourceFile = uploadForm.value.fileList[0] as File | undefined
  if (!sourceFile) {
    ElMessage.warning('请选择要上传的算法或模型文件')
    return
  }
  const lowerName = sourceFile.name.toLowerCase()
  const isPython = lowerName.endsWith('.py') || lowerName.endsWith('.zip')
  const isDocker = lowerName.endsWith('.tar.gz') || lowerName.endsWith('.tgz')
  if (!isPython && !lowerName.endsWith('.pkl') && !lowerName.endsWith('.onnx') && !isDocker) {
    ElMessage.error('仅支持 .py、.zip算法项目、可继续训练的 .pkl、.onnx 或Docker .tar.gz部署包')
    return
  }
  if (!uploadForm.value.name.trim()) {
    ElMessage.warning('请输入算法名称')
    return
  }
  if (isPython && !isControlSimulationUpload.value && !uploadForm.value.task_type) {
    ElMessage.warning('请选择训练任务类型')
    return
  }
  if (sourceFile.size > 256 * 1024 * 1024) {
    ElMessage.warning('上传文件不能超过256MB')
    return
  }

  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', sourceFile)
    const effectiveTaskType = isControlSimulationUpload.value ? 'control_optimization' : uploadForm.value.task_type
    const inferredCategory = isPython ? effectiveTaskType
      : lowerName.endsWith('.pkl') ? 'model_artifact'
        : lowerName.endsWith('.onnx') ? 'inference_model' : 'deployment_bundle'
    formData.append('name', uploadForm.value.name.trim())
    formData.append('category', inferredCategory)
    formData.append('task_type', isPython ? effectiveTaskType : 'regression')
    formData.append('algorithm_type', uploadForm.value.algorithm_type)
    formData.append('framework', uploadForm.value.framework)
    formData.append('version', uploadForm.value.version)
    formData.append('description', uploadForm.value.description)
    formData.append('tags', JSON.stringify(uploadForm.value.tags))
    if (isPython && uploadForm.value.entry_point.trim()) formData.append('entry_point', uploadForm.value.entry_point.trim())
    if (isPython && uploadForm.value.dependency_id) {
      formData.append('dependency_id', String(uploadForm.value.dependency_id))
    }
    const result = await api.algorithmStore.upload(formData)
    const artifactLabel = result.artifact?.artifact_label || '文件'
    const evaluationMessage = result.registered_model_id
      ? `，已注册评估模型 #${result.registered_model_id}`
      : ''
    const parameterCount = Number(result?.parameter_detection?.control_candidates || 0) + Number(result?.parameter_detection?.training_candidates || 0)
    const parameterMessage = parameterCount ? `，识别到 ${parameterCount} 个可确认参数` : ''
    ElMessage.success(
      lowerName.endsWith('.pkl')
        ? `${artifactLabel}上传成功，已归类为可继续训练模型${evaluationMessage}`
        : lowerName.endsWith('.onnx')
          ? `${artifactLabel}上传成功，可直接运行${evaluationMessage}`
        : isDocker
          ? `${artifactLabel}上传成功，可进行镜像构建与临时运行${evaluationMessage}`
        : isControlSimulationUpload.value
          ? `${artifactLabel}上传成功，可运行控制仿真并查看仿真结果${parameterMessage}`
          : `${artifactLabel}上传成功，可在沙箱中编辑、覆盖和下载${parameterMessage}`
    )
    showUploadDialog.value = false
    resetUploadForm()
    loadAlgorithms()
  } catch (error: any) {
    ElMessage.error('上传失败: ' + error.message)
  } finally {
    uploading.value = false
  }
}

function resetUploadForm() {
  uploadForm.value = {
    name: '',
    category: 'custom',
    model_mode: 'trainable',
    task_type: '',
    algorithm_type: '',
    framework: '',
    version: '1.0.0',
    description: '',
    fileList: [],
    dependency_id: null,
    tags: [],
    entry_point: '',
    params_schema: ''
  }
}

// ── 训练 ──
const showTrainDialog = ref(false)
const showControlResultsDialog = ref(false)
const controlResultAlgorithm = ref<any>(null)
const controlRuns = ref<any[]>([])
const loadingControlRuns = ref(false)
const trainTarget = ref<any>(null)
const compatibleDatasets = ref<any[]>([])
const modelFolders = ref<any[]>([])
const datasetColumns = ref<string[]>([])
const allDatasetColumns = ref<string[]>([])
const loadingDatasets = ref(false)
const loadingColumns = ref(false)
const trainingSubmitting = ref(false)
const trainResult = ref('')
const trainError = ref(false)
const trainForm = ref({
  name: '', model_folder_id: null as number | null, dataset_id: null as number | null, target_column: '', feature_columns: [] as string[],
  time_column: '', state_columns: [] as string[], control_columns: [] as string[], disturbance_columns: [] as string[],
  setpoint: 3.5 as number | undefined, control_parameters: '', simulation_mode: 'dataset_replay' as 'dataset_replay' | 'parameterized' | 'realtime_stream',
  realtime_transport: 'mqtt' as 'mqtt' | 'zmq', realtime_input_endpoint: '', realtime_input_topic: 'all', realtime_duration_seconds: 60, realtime_publish_outputs: false, realtime_output_endpoint: '',
  parameterized_steps: 30, parameterized_sample_time_s: 5, parameterized_initial_cv: 2, parameterized_initial_mv: 1, parameterized_disturbance: 0,
  hyperparameter_mode: 'default' as HyperparameterMode, hyperparams: ''
})
const trainParamFileName = ref('')
const trainDetectedParameterValues = ref<Record<string, any>>({})
const controlParameterValues = ref<Record<string, any>>({})
const controlConfigFileName = ref('')
const controlConfigData = ref<Record<string, any> | null>(null)
const trainRuntimeModel = computed(() => trainTarget.value?.metrics?.runtime_model_type || trainTarget.value?.algorithm || '')
const trainAdapterName = computed(() => trainTarget.value?.metrics?.training_adapter || trainRuntimeModel.value || '未配置')
const availableFeatureColumns = computed(() => datasetColumns.value.filter(column => column !== trainForm.value.target_column))
const isResumeTraining = computed(() => Boolean(trainTarget.value?.metrics?.resume_training_mode))
const isControlAlgorithm = computed(() => String(trainTarget.value?.metrics?.task_type || trainTarget.value?.category || '') === 'control_optimization')
function supportsParameterizedDmcFor(row: any) {
  const files = row?.metrics?.archive_files
  return String(row?.name || '').toLowerCase().includes('dmc')
    || (Array.isArray(files) && files.some((item: any) => String(item).replace(/\\/g, '/').includes('/dmc_controller/')))
}
function controlContractFor(row: any): any {
  const contract = row?.metrics?.control_contract
  return contract && typeof contract === 'object' ? contract : null
}
function supportsRealtimeStreamFor(row: any): boolean {
  const metadata = row?.metrics && typeof row.metrics === 'object' ? row.metrics : {}
  const entry = String(metadata.source_entry_point || row?.source_entry_point || '')
  // 当前实时适配器面向公司 DataDriver 项目；其他项目须先在声明文件中显式开放该模式。
  return /(^|\/)driver_[^/]+\.py$/i.test(entry)
}
function controlModesFor(row: any): ('dataset_replay' | 'parameterized' | 'realtime_stream')[] {
  const modes = controlContractFor(row)?.modes
  if (modes && typeof modes === 'object') {
    const declared = Object.keys(modes).filter((mode): mode is 'dataset_replay' | 'parameterized' | 'realtime_stream' => mode === 'dataset_replay' || mode === 'parameterized' || mode === 'realtime_stream')
    if (declared.length) return declared
  }
  const base: ('dataset_replay' | 'parameterized' | 'realtime_stream')[] = supportsParameterizedDmcFor(row)
    ? ['parameterized', 'dataset_replay']
    : ['dataset_replay']
  if (supportsRealtimeStreamFor(row)) base.push('realtime_stream')
  return base
}
function controlParameterDefaultsFor(row: any): Record<string, any> {
  const parameters = controlContractFor(row)?.parameters
  if (!Array.isArray(parameters)) return {}
  return Object.fromEntries(parameters.filter((item: any) => item?.key).map((item: any) => [item.key, item.default ?? (item.type === 'boolean' ? false : undefined)]))
}
function legacyControlParameterSchemaFor(row: any): any[] {
  const candidates = row?.metrics?.control_parameter_candidates
  return Array.isArray(candidates) ? candidates : []
}
function parameterizedDefaultsFor(row: any): Record<string, any> {
  const parameters = controlContractFor(row)?.parameters || legacyControlParameterSchemaFor(row)
  if (!Array.isArray(parameters)) return {}
  return Object.fromEntries(parameters.filter((item: any) => item?.key).map((item: any) => [item.key, item.default ?? (item.type === 'boolean' ? false : undefined)]))
}
const controlContract = computed(() => controlContractFor(trainTarget.value))
const hasGenericControlContract = computed(() => Boolean(controlContract.value))
const controlParameterSchema = computed<any[]>(() => Array.isArray(controlContract.value?.parameters) ? controlContract.value.parameters : [])
const legacyControlParameterSchema = computed<any[]>(() => legacyControlParameterSchemaFor(trainTarget.value))
const parameterizedParameterSchema = computed<any[]>(() => hasGenericControlContract.value ? controlParameterSchema.value : legacyControlParameterSchema.value)
const usesDynamicParameterizedSchema = computed(() => parameterizedParameterSchema.value.length > 0)
const availableControlModes = computed(() => controlModesFor(trainTarget.value))
const supportsParameterizedSimulation = computed(() => availableControlModes.value.includes('parameterized'))
const controlModeLabel = (mode: string) => mode === 'parameterized' ? '参数化仿真' : mode === 'realtime_stream' ? '实时接入运行' : '数据集回放'
const controlModeDescription = computed(() => hasGenericControlContract.value
  ? (controlContract.value?.description || '算法项目已通过 foundation_algorithm.json 声明运行方式与参数；平台会把配置文件和结果目录传给算法。')
  : '参数化仿真会用上传项目中的 DMC 控制器和默认过程模型生成虚拟过程；数据集回放则把完整过程数据交给算法源码。')
const requiresTrainDataset = computed(() => !isControlAlgorithm.value || trainForm.value.simulation_mode === 'dataset_replay')
const isControlAlgorithmRow = (row: any) => String(row?.metrics?.task_type || row?.category || '') === 'control_optimization'

function openControlResults(row: any) {
  router.push({ path: '/model/control-simulations', query: { algorithm_id: String(row.id) } })
}

async function downloadControlOutput(run: any, outputIndex: number, output: any) {
  const modelId = controlResultAlgorithm.value?.id
  if (!modelId || !run?.id || outputIndex < 0) return
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token') || ''
  try {
    // The backend resolves the saved output by its list index. This avoids ZIP projects'
    // non-ASCII directory names ever crossing the browser download path.
    const response = await fetch(`/api/v1/algorithm-store/models/${modelId}/control-runs/${encodeURIComponent(run.id)}/outputs/${outputIndex}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!response.ok) throw new Error(`下载失败（HTTP ${response.status}）`)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = String(output?.name || 'control-output')
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error: any) {
    ElMessage.error(error?.message || '下载控制仿真输出失败')
  }
}

const trainSourceContract = computed(() => {
  const contract = trainTarget.value?.metrics?.source_contract
  return contract && typeof contract === 'object' ? contract : null
})
const trainSourceDataContract = computed<any>(() => trainSourceContract.value?.data_contract || {})
const sourceManagesData = computed(() => Boolean(
  trainTarget.value?.asset_type === 'uploaded_source'
  && trainSourceDataContract.value?.target_column
  && Array.isArray(trainSourceDataContract.value?.feature_columns)
  && trainSourceDataContract.value.feature_columns.length
))
const trainDefaultParams = computed<Record<string, any>>(() => {
  const defaults = trainSourceContract.value?.declared_defaults || trainSourceContract.value?.defaults
  if (defaults && typeof defaults === 'object') return defaults
  try {
    const schema = JSON.parse(trainTarget.value?.params_schema || '{}')
    if (schema?.defaults && typeof schema.defaults === 'object') return schema.defaults
    const result: Record<string, any> = {}
    Object.entries(schema?.properties || {}).forEach(([key, value]: [string, any]) => {
      if (Object.prototype.hasOwnProperty.call(value || {}, 'default')) result[key] = value.default
    })
    return result
  } catch {
    return {}
  }
})
const trainDetectedParamSchema = computed<any[]>(() => {
  const candidates = trainSourceContract.value?.parameter_candidates
  if (Array.isArray(candidates) && candidates.length) return candidates
  try {
    const schema = JSON.parse(trainTarget.value?.params_schema || '{}')
    return Object.entries(schema?.properties || {}).map(([key, item]: [string, any]) => ({ key, label: key, type: item?.type || 'string', default: item?.default, origin: 'declared_default' }))
  } catch { return [] }
})
function trainDetectedDefaultsFor(row: any): Record<string, any> {
  const candidates = row?.metrics?.source_contract?.parameter_candidates
  if (!Array.isArray(candidates)) return {}
  return Object.fromEntries(candidates.filter((item: any) => item?.key).map((item: any) => [item.key, item.default]))
}
const trainDefaultParamRows = computed(() => {
  return Object.entries(trainDefaultParams.value).map(([key, value]) => ({
    key,
    label: key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
})
const trainTaskTypeLabel = computed(() => {
  const taskType = trainTarget.value?.metrics?.task_type || trainTarget.value?.category || ''
  return taskType === 'classification' ? '分类 (Classification)'
    : taskType === 'time_series_forecasting' ? '时序预测 (Time Series Forecasting)'
      : taskType === 'control_optimization' ? '控制优化 (Control Optimisation)'
        : '回归 (Regression)'
})

async function startTraining(row: any) {
  trainTarget.value = row; trainResult.value = ''; trainError.value = false
  datasetColumns.value = []
  allDatasetColumns.value = []
  const dataContract = row.metrics?.source_contract?.data_contract || {}
  const managedTarget = String(dataContract.target_column || '')
  const managedFeatures = Array.isArray(dataContract.feature_columns) ? dataContract.feature_columns : []
  controlParameterValues.value = parameterizedDefaultsFor(row)
  trainDetectedParameterValues.value = trainDetectedDefaultsFor(row)
  trainForm.value = {
    name: `${row.name}-${row.metrics?.resume_training_mode ? '继续训练' : '训练'}`, model_folder_id: null, dataset_id: null,
    target_column: managedTarget, feature_columns: [...managedFeatures],
    time_column: '', state_columns: [], control_columns: [], disturbance_columns: [], setpoint: 3.5, control_parameters: '',
    simulation_mode: 'dataset_replay', realtime_transport: 'mqtt', realtime_input_endpoint: '', realtime_input_topic: 'all', realtime_duration_seconds: 60, realtime_publish_outputs: false, realtime_output_endpoint: '',
    parameterized_steps: 30, parameterized_sample_time_s: 5, parameterized_initial_cv: 2, parameterized_initial_mv: 1, parameterized_disturbance: 0,
    hyperparameter_mode: 'default', hyperparams: ''
  }
  trainParamFileName.value = ''
  controlConfigFileName.value = ''
  controlConfigData.value = null
  try { modelFolders.value = await modelGroupApi.getFolders() } catch { modelFolders.value = [] }
  showTrainDialog.value = true; loadingDatasets.value = true
  try {
    const res = await api.algorithmStore.getCompatibleDatasets(row.id)
    compatibleDatasets.value = res?.items || []
  } catch (e: any) {
    compatibleDatasets.value = []
    trainError.value = true
    trainResult.value = '数据集加载失败: ' + (e.response?.data?.detail || e.message)
  }
  finally { loadingDatasets.value = false }
}

async function selectControlConfigFile(file: any) {
  const raw = file?.raw as File | undefined
  if (!raw) return
  try {
    if (raw.size > 1024 * 1024) throw new Error('控制配置文件不能超过 1MB')
    const parsed = JSON.parse(await raw.text())
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('控制配置必须是 JSON 对象')
    controlConfigData.value = parsed
    controlConfigFileName.value = raw.name
    trainError.value = false
  } catch (error: any) {
    controlConfigData.value = null
    controlConfigFileName.value = ''
    ElMessage.error(error?.message || '控制配置文件读取失败')
  }
}
function clearControlConfigFile() {
  controlConfigData.value = null
  controlConfigFileName.value = ''
}
async function downloadControlConfigTemplate() {
  if (!trainTarget.value?.id) return
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token') || ''
  try {
    const response = await fetch('/api/v1/algorithm-store/models/' + trainTarget.value.id + '/control-config-template', {
      headers: token ? { Authorization: 'Bearer ' + token } : {},
    })
    if (!response.ok) throw new Error('下载模板失败（HTTP ' + response.status + '）')
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = String(trainTarget.value.name || 'control') + '-控制配置.json'
    document.body.appendChild(anchor); anchor.click(); anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error: any) {
    ElMessage.error(error?.message || '下载控制配置模板失败')
  }
}

function onControlSimulationModeChange(mode: 'dataset_replay' | 'parameterized' | 'realtime_stream') {
  trainError.value = false
  trainResult.value = mode === 'realtime_stream'
    ? '默认将读取算法包 mqtt.json 并订阅实时数据；仅保存结果，不向外部写入控制指令。'
    : mode === 'parameterized'
    ? (hasGenericControlContract.value ? '将按算法项目声明的参数化入口运行，并把配置文件传给源码。' : '将调用上传项目中的 DMC 控制器，并使用默认过程模型生成虚拟过程。')
    : '请选择完整过程数据集，再按源码实际列名配置变量。'
}

function onTargetColumnChange(targetColumn: string) {
  trainForm.value.feature_columns = trainForm.value.feature_columns.filter(column => column !== targetColumn)
}

async function onTrainDatasetChange(datasetId: number) {
  trainForm.value.target_column = sourceManagesData.value ? String(trainSourceDataContract.value.target_column) : ''
  trainForm.value.feature_columns = sourceManagesData.value ? [...trainSourceDataContract.value.feature_columns] : []
  datasetColumns.value = []
  allDatasetColumns.value = []
  if (!datasetId) return
  loadingColumns.value = true
  try {
    const preview = await api.dataset.getRows(datasetId, { offset: 0, limit: 10 })
    const columns: string[] = preview?.columns || []
    const rows: any[][] = preview?.rows || []
    allDatasetColumns.value = columns
    datasetColumns.value = columns.filter((_: string, columnIndex: number) => {
      const values = rows.map(row => row?.[columnIndex]).filter(value => value !== null && value !== undefined && value !== '')
      return values.length > 0 && values.every(value => Number.isFinite(Number(value)))
    })
    if (isControlAlgorithm.value) {
      trainError.value = false
      trainResult.value = `已加载 ${columns.length} 列过程数据；请按控制算法实际列名选择变量。`
      return
    }
    if (sourceManagesData.value) {
      const requiredColumns = [
        trainSourceDataContract.value.target_column,
        trainSourceDataContract.value.time_column,
        ...trainSourceDataContract.value.feature_columns,
      ].filter(Boolean)
      const missingColumns = [...new Set(requiredColumns)].filter(column => !columns.includes(column))
      if (missingColumns.length) {
        trainError.value = true
        trainResult.value = `所选数据集缺少源码声明的数据列：${missingColumns.slice(0, 5).join('、')}${missingColumns.length > 5 ? ` 等${missingColumns.length}列` : ''}`
      } else {
        trainError.value = false
        trainResult.value = '数据集包含源码声明的目标、时间和特征列，可以开始训练'
      }
      return
    }
    const roles = await get(`/datasets/${datasetId}/field-roles`).catch(() => null)
    const saved = roles?.saved
    if (saved?.time_column && allDatasetColumns.value.includes(saved.time_column)) {
      trainForm.value.time_column = saved.time_column
    }
    const preferredTarget = datasetColumns.value.find(column =>
      /(^target$|^y$|label|temperature|temp|output|result)/i.test(column)
    )
    trainForm.value.target_column = preferredTarget || datasetColumns.value[datasetColumns.value.length - 1] || ''
    trainResult.value = '请根据当前算法任务确认预测目标和输入特征'
    if (!trainForm.value.target_column) {
      trainError.value = true
      trainResult.value = '该数据集没有可用的数值目标列，无法用于当前训练任务'
    }
  } catch (e: any) {
    trainError.value = true
    trainResult.value = '数据列加载失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    loadingColumns.value = false
  }
}

async function doTrain() {
  if (!trainTarget.value || (requiresTrainDataset.value && !trainForm.value.dataset_id) || (!isControlAlgorithm.value && !sourceManagesData.value && !trainForm.value.target_column) || !trainForm.value.name.trim()) {
    ElMessage.warning(isControlAlgorithm.value
      ? (trainForm.value.simulation_mode === 'dataset_replay' ? '请填写运行名称并选择过程数据集' : '请填写运行名称')
      : '请填写任务名称，并选择训练数据集和数值目标列')
    return
  }
  if (trainError.value) {
    ElMessage.warning('当前数据集与源码声明不匹配，请重新选择数据集')
    return
  }
  trainResult.value = isControlAlgorithm.value ? '控制仿真运行中...' : '训练启动中...'; trainError.value = false
  trainingSubmitting.value = true
  try {
    if (isControlAlgorithm.value) {
      if (trainForm.value.simulation_mode === 'realtime_stream' && trainForm.value.realtime_transport === 'zmq' && !trainForm.value.realtime_input_endpoint.trim()) throw new Error('请填写 ZMQ 实时输入地址')
      if (trainForm.value.simulation_mode === 'realtime_stream' && trainForm.value.realtime_transport === 'zmq' && trainForm.value.realtime_publish_outputs && !trainForm.value.realtime_output_endpoint.trim()) throw new Error('请填写控制输出地址')
      if (trainForm.value.simulation_mode === 'parameterized' && !controlConfigData.value) throw new Error('请先下载并上传控制配置 JSON 文件')
      const effectiveParameters: Record<string, any> = {}
      const result = await api.algorithmStore.runControl(trainTarget.value.id, {
        name: trainForm.value.name.trim(), simulation_mode: trainForm.value.simulation_mode,
        dataset_id: trainForm.value.simulation_mode === 'dataset_replay' ? trainForm.value.dataset_id : null,
        time_column: trainForm.value.time_column, state_columns: trainForm.value.state_columns,
        control_columns: trainForm.value.control_columns, disturbance_columns: trainForm.value.disturbance_columns,
        setpoint: trainForm.value.setpoint, parameters: effectiveParameters,
        controller_config: controlConfigData.value,
        realtime: { transport: trainForm.value.realtime_transport, input_endpoint: trainForm.value.realtime_input_endpoint.trim(), input_topic: trainForm.value.realtime_input_topic.trim(), duration_seconds: trainForm.value.realtime_duration_seconds, publish_outputs: trainForm.value.realtime_transport === 'zmq' && trainForm.value.realtime_publish_outputs, output_endpoint: trainForm.value.realtime_output_endpoint.trim() },
      })
      trainError.value = result?.status !== 'success'
      trainResult.value = result?.status === 'success'
        ? `控制仿真完成（运行 ${result.id}，耗时 ${(Number(result.duration_ms || 0) / 1000).toFixed(2)} 秒）。日志和 ${result.outputs?.length || 0} 个结果文件已保存。`
        : `控制仿真失败（运行 ${result?.id || '未知'}）：${result?.stderr_tail || '请查看运行日志'}`
      return
    }
    if (trainForm.value.hyperparameter_mode !== 'default' && trainForm.value.hyperparameter_mode !== 'detected' && !trainForm.value.hyperparams.trim()) {
      throw new Error(trainForm.value.hyperparameter_mode === 'file'
        ? '请上传参数文件'
        : '请输入超参数；如需使用默认值请选择“使用默认值”')
    }
    const hyperparams = trainForm.value.hyperparameter_mode === 'default'
      ? {}
      : trainForm.value.hyperparameter_mode === 'detected'
        ? { ...trainDetectedParameterValues.value }
        : parseHyperparameterText(trainForm.value.hyperparams)
    const response = await api.algorithmStore.train(trainTarget.value.id, {
      name: trainForm.value.name.trim(),
      dataset_id: trainForm.value.dataset_id,
      model_folder_id: trainForm.value.model_folder_id,
      target_column: trainForm.value.target_column,
      feature_columns: trainForm.value.feature_columns,
      hyperparams,
      hyperparameter_source: trainForm.value.hyperparameter_mode === 'detected' ? 'manual' : trainForm.value.hyperparameter_mode,
    })
    const jobId = response?.result?.job_id || response?.result?.result?.job_id
    trainResult.value = isResumeTraining.value
      ? '继续训练已启动！完成后会注册为新的训练模型'
      : '训练已启动！可在模型工厂查看进度'
    if (jobId) trainResult.value += `（任务 ${jobId}）`
  } catch (e: any) {
    trainError.value = true
    trainResult.value = '训练失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    trainingSubmitting.value = false
  }
}

function onTrainParamModeChange() {
  if (trainForm.value.hyperparameter_mode === 'detected') {
    trainDetectedParameterValues.value = trainDetectedDefaultsFor(trainTarget.value)
    trainForm.value.hyperparams = ''
  } else {
    trainForm.value.hyperparams = trainForm.value.hyperparameter_mode === 'manual'
      ? JSON.stringify(trainDefaultParams.value, null, 2)
      : ''
  }
  trainParamFileName.value = ''
}

async function selectTrainParamFile(uploadFile: any) {
  try {
    const file = uploadFile?.raw as File
    const parsed = await readHyperparameterFile(file)
    trainForm.value.hyperparams = parsed.text
    trainParamFileName.value = file.name
    ElMessage.success(`已读取参数文件 ${file.name}`)
  } catch (error: any) {
    clearTrainParamFile()
    ElMessage.error(error?.message || '参数文件读取失败')
  }
}

function clearTrainParamFile() {
  trainForm.value.hyperparams = ''
  trainParamFileName.value = ''
}

function viewAlgorithm(row: any) {
  const schema = (() => {
    try { return JSON.parse(row.params_schema || '{}') }
    catch { return {} }
  })()
  const required = new Set(Array.isArray(schema.required) ? schema.required : [])
  const params = Object.entries(schema.properties || {}).map(([name, spec]: [string, any]) => ({
    name,
    type: spec?.type || typeof spec?.default,
    default: Object.prototype.hasOwnProperty.call(spec || {}, 'default')
      ? (typeof spec.default === 'string' ? spec.default : JSON.stringify(spec.default))
      : '-',
    required: required.has(name),
    description: spec?.description || '源码声明参数',
  }))
  currentAlgorithm.value = {
    ...row,
    params,
    dependencies: row.dependencies || [],
  }
  showDetailDialog.value = true
}

async function showVersions(row: any) {
  currentAlgorithm.value = row
  versionsLoading.value = true
  showVersionsDialog.value = true

  try {
    const res = await api.algorithmStore.getVersions(row.id)
    versions.value = res.versions || []
  } catch (error) {
    versions.value = [
      { version: '2.0.1', description: '修复性能问题，优化内存使用', downloads: 2500, is_latest: true, created_at: '2024-01-15 00:00:00' },
      { version: '2.0.0', description: '重大更新，支持分布式训练', downloads: 1800, is_latest: false, created_at: '2024-01-01 00:00:00' },
      { version: '1.5.0', description: '添加GPU加速支持', downloads: 1000, is_latest: false, created_at: '2023-12-01 00:00:00' },
      { version: '1.0.0', description: '初始版本', downloads: 380, is_latest: false, created_at: '2023-10-01 00:00:00' }
    ]
  } finally {
    versionsLoading.value = false
  }
}

function handleCommand(command: string, row: any) {
  switch (command) {
    case 'edit':
      editAlgorithm(row)
      break
    case 'publish':
      publishAlgorithm(row)
      break
    case 'unpublish':
      unpublishAlgorithm(row)
      break
    case 'stats':
      viewStats(row)
      break
    case 'delete':
      deleteAlgorithm(row)
      break
  }
}

function openInSandbox(row: any) {
  router.push({ path: '/model/code-editor', query: { algorithm_id: String(row.id) } })
}

const newParamKey = ref('')
const newParamVal = ref('')

function addHyperParam() {
  if (newParamKey.value && newParamVal.value) {
    editForm.value.hyperparameters[newParamKey.value] = newParamVal.value
    newParamKey.value = ''; newParamVal.value = ''
  }
}

function editAlgorithm(row: any) {
  editingAlgorithm.value = { ...row }
  editForm.value = { 
    name: row.name || '', description: row.description || '', 
    category: row.category || '',
    task_type: row.metrics?.task_type || row.category || '',
    algorithm_type: row.metrics?.algorithm_type || row.algorithm || '',
    difficulty: row.difficulty || 'intermediate',
    dependency_id: row.metrics?.dependency_id ?? null,
    hyperparameters: { ...(row.hyperparameters || {}) }
  }
  showEditDialog.value = true
}

async function saveEdit() {
  try {
    // Convert string values to numbers where possible
    const hp: any = {}
    for (const [k, v] of Object.entries(editForm.value.hyperparameters || {})) {
      const n = Number(v)
      hp[k] = isNaN(n) ? v : n
    }
    await api.algorithmStore.update(editingAlgorithm.value.id, {
      ...editForm.value,
      hyperparameters: hp,
      // 运行环境绑定: 通过 metrics 合并进算法(后端 _adapter_metadata 会保留该 key)
      metrics: { dependency_id: editForm.value.dependency_id ?? null },
    })
    ElMessage.success('更新成功')
    showEditDialog.value = false
    loadAlgorithms()
  } catch (e: any) { ElMessage.error('更新失败: ' + (e.response?.data?.detail || e.message)) }
}

async function publishAlgorithm(row: any) {
  try {
    await api.algorithmStore.publish(row.id)
    ElMessage.success('发布成功')
    loadAlgorithms()
  } catch (error: any) {
    if (error !== 'cancel' && error?.message) {
      ElMessage.error('操作失败: ' + (error.response?.data?.detail || error.message))
    }
  }
}

async function unpublishAlgorithm(row: any) {
  try {
    await ElMessageBox.confirm('确定要下架该算法吗？', '确认下架', { type: 'warning' })
    await api.algorithmStore.unpublish(row.id)
    ElMessage.success('已下架')
    loadAlgorithms()
  } catch (error: any) {
    if (error !== 'cancel' && error?.message) {
      ElMessage.error('操作失败: ' + (error.response?.data?.detail || error.message))
    }
  }
}

function viewStats(row: any) {
  currentAlgorithm.value = {
    ...row,
    stats: {
      total_downloads: row.downloads * 2,
      total_runs: row.downloads * 1.5,
      recent_usage: [
        { user: 'user1', project: '项目A', status: 'success', runtime: 2.5, created_at: '2024-01-15 14:00:00' },
        { user: 'user2', project: '项目B', status: 'success', runtime: 3.1, created_at: '2024-01-15 12:00:00' },
        { user: 'user3', project: '项目C', status: 'failed', runtime: 0.5, created_at: '2024-01-15 10:00:00' }
      ]
    }
  }
  showStatsDetailDialog.value = true
}

async function deleteAlgorithm(row: any) {
  try {
    await ElMessageBox.confirm(`确定要删除算法"${row.name}"吗？`, '确认删除', { type: 'warning' })
    await api.algorithmStore.delete(row.id)
    ElMessage.success('删除成功')
    loadAlgorithms()
  } catch (error: any) {
    if (error !== 'cancel' && error?.message) {
      ElMessage.error('删除失败: ' + (error.response?.data?.detail || error.message))
    }
  }
}

async function uploadManual(uploadFile: any) {
  const raw = uploadFile?.raw
  const algorithm = currentAlgorithm.value
  if (!raw || !algorithm?.id) return
  manualUploading.value = true
  try {
    const form = new FormData()
    form.append('file', raw, raw.name)
    const result = await api.algorithmStore.uploadManual(algorithm.id, form)
    algorithm.metrics = { ...(algorithm.metrics || {}), manual: result?.manual }
    const item = algorithms.value.find((row: any) => row.id === algorithm.id)
    if (item) item.metrics = { ...(item.metrics || {}), manual: result?.manual }
    ElMessage.success('说明书已上传')
  } catch (error: any) {
    ElMessage.error('说明书上传失败：' + (error?.response?.data?.detail || error?.message || '未知错误'))
  } finally {
    manualUploading.value = false
  }
}

async function downloadManual(row: any) {
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
  try {
    const response = await fetch(`/api/v1/algorithm-store/models/${row.id}/manual`, {
      headers: token ? { Authorization: 'Bearer ' + token } : {}
    })
    if (!response.ok) {
      let message = `HTTP ${response.status}`
      try { message = (await response.json()).detail || message } catch { /* non-JSON error */ }
      throw new Error(message)
    }
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = row.metrics?.manual?.filename || `algorithm-${row.id}-manual`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('说明书已开始下载')
  } catch (error: any) {
    ElMessage.error('说明书下载失败：' + (error?.message || '未知错误'))
  }
}

async function downloadAlgorithm(row: any) {
  try {
    await downloadAlgorithmFile(row.id, row.metrics?.source_filename || `algorithm-${row.id}`)
  } catch (error: any) {
    ElMessage.error('下载失败: ' + error.message)
  }
}

async function downloadVersion(row: any) {
  try {
    await downloadAlgorithmFile(currentAlgorithm.value.id, `algorithm-${currentAlgorithm.value.id}-v${row.version}.json`)
  } catch (error: any) {
    ElMessage.error('下载失败: ' + error.message)
  }
}

async function downloadAlgorithmFile(id: number, fallbackName: string) {
  const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
  const response = await fetch(`/api/v1/algorithm-store/models/${id}/download`, {
    headers: token ? { Authorization: 'Bearer ' + token } : {}
  })
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try { message = (await response.json()).detail || message } catch { /* non-JSON error */ }
    throw new Error(message)
  }
  const disposition = response.headers.get('Content-Disposition') || ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  let filename = fallbackName
  try { filename = encoded ? decodeURIComponent(encoded) : (plain || fallbackName) } catch { /* fallback */ }
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
  ElMessage.success(`已下载原始文件：${filename}`)
}

async function deleteVersion(row: any) {
  try {
    await ElMessageBox.confirm(`确定要删除版本v${row.version}吗？`, '确认删除', { type: 'warning' })
    await api.algorithmStore.deleteVersion(currentAlgorithm.value.id, row.version)
    ElMessage.success('删除成功')
    showVersions(currentAlgorithm.value)
  } catch (error) {
    // 取消
  }
}

function viewDependency(row: any) {
  // TODO: 查看依赖详情
  ElMessage.info('查看详情功能开发中')
}

function editDependency(row: any) {
  editingDependency.value = row
  dependencyForm.value = {
    name: row.name,
    python_version: row.python_version,
    cuda_version: row.cuda_version || '',
    packages: row.packages || '',
    description: row.description || ''
  }
  showDependencyDialog.value = true
}

async function saveDependency() {
  if (!dependencyForm.value.name) {
    ElMessage.warning('请输入环境名称')
    return
  }

  savingDependency.value = true
  try {
    if (editingDependency.value) {
      await api.algorithmStore.updateDependency(editingDependency.value.id, dependencyForm.value)
      ElMessage.success('更新成功')
    } else {
      await api.algorithmStore.createDependency(dependencyForm.value)
      ElMessage.success('创建成功')
    }
    showDependencyDialog.value = false
    resetDependencyForm()
    loadDependencies()
  } catch (error: any) {
    ElMessage.error('保存失败: ' + error.message)
  } finally {
    savingDependency.value = false
  }
}

function resetDependencyForm() {
  editingDependency.value = null
  dependencyForm.value = {
    name: '',
    python_version: '3.10',
    cuda_version: '',
    packages: '',
    description: ''
  }
}

async function deleteDependency(row: any) {
  try {
    await ElMessageBox.confirm(`确定要删除环境"${row.name}"吗？`, '确认删除', { type: 'warning' })
    await api.algorithmStore.deleteDependency(row.id)
    ElMessage.success('删除成功')
    loadDependencies()
  } catch (error: any) {
    if (error !== 'cancel' && error?.message) {
      ElMessage.error('删除失败: ' + (error.response?.data?.detail || error.message))
    }
  }
}

function exportStats() {
  ElMessage.success('导出功能开发中...')
}

onMounted(() => {
  loadTrainingAdapters()
  loadAlgorithms()
  loadDependencies()
  loadUsageStats()
})
onBeforeUnmount(stopDockerPolling)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.algorithm-store-page {
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

  .stats-row {
    margin-bottom: 20px;
  }

  .stat-card {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 16px;

    :deep(.el-card__body) {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 20px;
    }

    .stat-icon {
      width: 56px;
      height: 56px;
      border-radius: $radius-lg;
      display: flex;
      align-items: center;
      justify-content: center;
      color: $primary-light;
    }

    .stat-content {
      .stat-value {
        font-size: 24px;
        font-weight: 700;
        color: $text-primary;
      }

      .stat-label {
        font-size: 14px;
        color: $text-secondary;
        margin-top: 4px;
      }
    }
  }

  .store-tabs {
    :deep(.el-tabs__content) {
      padding: 0;
    }
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;

    .header-left {
      display: flex;
      align-items: center;
    }
  }

  .algorithm-name {
    display: flex;
    align-items: center;
    gap: 12px;

    .algorithm-icon {
      color: $primary-light;
      font-size: 24px;
    }

    .name {
      font-size: 15px;
      font-weight: 700;
      line-height: 1.45;
      color: #111827;
      letter-spacing: 0.1px;
      text-shadow: none;
    }

    .desc {
      font-size: 12px;
      color: #374151;
      margin-top: 2px;
    }
  }

  .download-count {
    color: $primary-light;
    font-weight: 500;
  }

  .pagination-container {
    margin-top: 20px;
    display: flex;
    justify-content: flex-end;
  }

  .upload-area {
    width: 100%;

    :deep(.el-upload-dragger) {
      width: 100%;
    }
  }

  .detected-parameter-form {
    display: grid;
    gap: 10px;
    margin-top: 10px;
  }
  .detected-parameter-row {
    display: grid;
    grid-template-columns: minmax(130px, 1fr) 230px minmax(100px, 1fr);
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border: 1px solid #ebeef5;
    border-radius: 6px;
  }
  .detected-parameter-label { color: #303133; font-weight: 500; }

  .source-default-params {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin-top: 10px;
  }

  .source-default-param {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 10px;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    background: #f8fafc;
    font-size: 12px;

    span {
      color: #475569;
    }

    code {
      color: #0f172a;
      font-weight: 600;
      word-break: break-all;
    }
  }

  .onnx-run-help {
    margin-top: 6px;
    color: $text-secondary;
    font-size: 13px;
    line-height: 24px;

    code {
      padding: 2px 5px;
      border-radius: 4px;
      background: rgba(148, 163, 184, 0.14);
    }
  }

  .docker-stage {
    margin-top: 8px;
    color: $text-secondary;
    text-align: center;
    font-size: 13px;
  }

  .docker-logs {
    max-height: 280px;
    margin: 0;
    padding: 12px;
    overflow: auto;
    border-radius: 6px;
    background: #111827;
    color: #d1fae5;
    font-size: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-all;
  }

  .docker-latency {
    margin-top: 10px;
    color: $text-secondary;
    text-align: right;
    font-size: 13px;
  }

  .manual-panel {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 36px;
    flex-wrap: wrap;
  }

  .manual-file {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    color: #334155;
  }

  .manual-meta, .manual-empty {
    color: $text-secondary;
    font-size: 12px;
  }

  .manual-upload { margin-left: auto; }

  .stats-charts {
    margin-bottom: 20px;

    .chart-card {
      :deep(.el-card__body) {
        min-height: 200px;
      }

      .chart-placeholder {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 150px;
        color: $text-muted;

        p {
          margin-top: 8px;
        }
      }
    }
  }
}
</style>

<style scoped>
.control-result-json,.control-result-log{max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#f7f8fa;border:1px solid #ebeef5;border-radius:4px;padding:12px;margin-top:10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.55}.control-result-log.error{color:#b42318;background:#fff6f5}
</style>
