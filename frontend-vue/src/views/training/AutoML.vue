<template>
  <div class="automl-page">
    <PageHeader title="AutoML 算法选型" desc="在同一数据场景下公平比较多种算法，记录指标并推荐最佳选择" />
    <div class="page-actions">
      <el-button type="primary" plain @click="openSelectionRecords">查看选型记录</el-button>
    </div>

    <!-- 步骤条 -->
    <el-steps :active="step" align-center class="steps-bar">
      <el-step title="选择数据" />
      <el-step title="选择目标" />
      <el-step title="选择模型" />
      <el-step title="对比结果" />
      <el-step title="选型记录" />
    </el-steps>

    <!-- Step 1: 选择数据 -->
    <el-card v-if="step === 0" class="step-card">
      <template #header>📥 选择数据集</template>
      <el-table :data="datasets" @row-click="selectDataset" highlight-current-row height="300">
        <el-table-column prop="id" label="数据集ID" width="95" />
        <el-table-column prop="name" label="数据集" min-width="200" />
        <el-table-column prop="row_count" label="行数" width="100" />
        <el-table-column label="标签" width="150">
          <template #default="{ row }">
            <el-tag v-for="tag in (row.tags||[]).slice(0,3)" :key="tag" size="small" style="margin:1px">{{ tag }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="selectedDataset" class="selected-info">
        已选择: <strong>#{{ selectedDataset.id }} {{ selectedDataset.name }}</strong> ({{ selectedDataset.row_count }} 行)
      </div>
      <el-button type="primary" @click="step = 1" :disabled="!selectedDataset" style="margin-top:12px">下一步 →</el-button>
    </el-card>

    <!-- Step 2: 选择目标列 -->
    <el-card v-if="step === 1" class="step-card">
      <template #header>🎯 选择目标列 (预测什么)</template>
      <el-alert
        title="目标列是模型要预测的字段；数据集中的其他可用数值列将作为输入特征"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:16px"
      />
      <el-radio-group v-model="targetCol" class="target-list">
        <el-radio v-for="col in targetOptions" :key="col" :label="col" border class="target-radio">
          {{ col }}
        </el-radio>
      </el-radio-group>
      <el-divider />
      <el-form label-width="110px">
        <el-form-item label="最佳模型标准">
          <el-select v-model="primaryMetric" style="width:260px">
            <el-option v-for="metric in metricOptions" :key="metric.value" :label="metric.label" :value="metric.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <div style="margin-top:16px">
        <el-button @click="step = 0">← 上一步</el-button>
        <el-button type="primary" @click="step = 2" style="margin-left:12px">下一步 →</el-button>
      </div>
    </el-card>

    <!-- Step 3: 选择模型 -->
    <el-card v-if="step === 2" class="step-card">
      <template #header>🤖 选择模型</template>
      <el-checkbox-group v-model="selectedModels" class="model-list">
        <div v-for="m in modelOptions" :key="m.value" class="model-row">
          <el-checkbox
            :label="m.value" border class="model-check"
            :disabled="selectedModels.length >= 5 && !selectedModels.includes(m.value)"
          >
            <strong>{{ m.label }}</strong>
            <span class="model-desc">{{ m.desc }}</span>
          </el-checkbox>
          <el-button
            v-if="selectedModels.includes(m.value)" link size="small" type="primary" class="tune-btn"
            @click="openTuning(m.value)"
          >
            {{ hasOverride(m.value) ? '✏️ 已调参' : '⚙️ 调参' }}
          </el-button>
        </div>
      </el-checkbox-group>
      <div class="selection-hint">一次最多选择5个候选模型，当前已选择 {{ selectedModels.length }} 个</div>
      <div style="margin-top:16px">
        <el-button @click="step = 1">← 上一步</el-button>
        <el-button type="primary" @click="startTraining" :loading="loading" :disabled="selectedModels.length===0" style="margin-left:12px">
          🚀 开始训练
        </el-button>
      </div>
    </el-card>

    <!-- Step 4: 训练结果 -->
    <el-card v-if="step === 3" class="step-card">
      <template #header>📊 训练结果</template>
      <el-alert
        v-if="results.length"
        :title="`预测目标：${targetCol}　排名标准：${primaryMetricLabel}　最佳模型：${modelLabel(bestModel)}`"
        type="success"
        :closable="false"
        show-icon
        style="margin-bottom:16px"
      />
      <el-table :data="results" stripe v-if="results.length">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="prediction-preview">
              <div class="preview-title">5折交叉验证预测示例（前10条，不是训练拟合值）</div>
              <el-table :data="row.prediction_preview || []" size="small" border>
                <el-table-column prop="index" label="样本" width="80" />
                <el-table-column label="真实值">
                  <template #default="{ row: item }">{{ formatNumber(item.actual) }}</template>
                </el-table-column>
                <el-table-column label="预测值">
                  <template #default="{ row: item }">{{ formatNumber(item.predicted) }}</template>
                </el-table-column>
                <el-table-column label="误差（预测-真实）">
                  <template #default="{ row: item }">{{ formatNumber(item.error) }}</template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column type="index" label="🏆" width="60" />
        <el-table-column label="模型" min-width="170">
          <template #default="{ row }">
            {{ modelLabel(row.model_name) }}
            <el-tag v-if="row.is_best" type="success" size="small" style="margin-left:6px">最佳</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="R²" width="105">
          <template #default="{ row }">
            {{ formatNumber(row.r2_mean) }}
          </template>
        </el-table-column>
        <el-table-column label="RMSE" width="110">
          <template #default="{ row }">{{ formatNumber(row.rmse_mean) }}</template>
        </el-table-column>
        <el-table-column label="MAE" width="110">
          <template #default="{ row }">{{ formatNumber(row.mae_mean) }}</template>
        </el-table-column>
        <el-table-column label="验证波动" width="110">
          <template #default="{ row }">{{ formatNumber(row.cv_std) }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ row.training_time_seconds.toFixed(2) }}s</template>
        </el-table-column>
        <el-table-column label="超参" min-width="150">
          <template #default="{ row }">
            <span v-if="row.estimator_params && Object.keys(row.estimator_params).length" class="param-text">{{ formatParams(row.estimator_params) }}</span>
            <span v-else class="default-param">默认</span>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="loading" class="training-progress">
        <el-progress :percentage="trainingProgress" :stroke-width="18" :status="trainingProgress >= 100 ? 'success' : undefined" />
        <div class="progress-message">
          <span v-if="currentTrainingModel">正在训练：{{ modelLabel(currentTrainingModel) }}</span>
          <span v-else>正在准备训练数据和候选模型</span>
          <span v-if="totalTrainingModels">（已完成 {{ completedTrainingModels }}/{{ totalTrainingModels }} 个模型）</span>
        </div>
      </div>
      <el-empty v-else-if="!results.length" description="暂无训练结果" />
      <div style="margin-top:16px">
        <el-button @click="step = 2">← 重新选择</el-button>
        <el-button type="success" @click="openSelectionRecords" :disabled="results.length===0" style="margin-left:12px">查看选型记录 →</el-button>
      </div>
    </el-card>

    <!-- Step 5: 选型记录 -->
    <el-card v-if="step === 4" class="step-card">
      <template #header>📋 算法选型记录</template>
      <el-table :data="selectionRecords" v-loading="recordsLoading" stripe>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="record-results">
              <div class="preview-title">参与算法的完整指标</div>
              <el-table :data="row.results || []" size="small" border>
                <el-table-column label="算法" min-width="150">
                  <template #default="{ row: item }">{{ modelLabel(item.algorithm) }}</template>
                </el-table-column>
                <el-table-column label="R²"><template #default="{ row: item }">{{ formatNumber(item.metrics?.r2) }}</template></el-table-column>
                <el-table-column label="RMSE"><template #default="{ row: item }">{{ formatNumber(item.metrics?.rmse) }}</template></el-table-column>
                <el-table-column label="MAE"><template #default="{ row: item }">{{ formatNumber(item.metrics?.mae) }}</template></el-table-column>
                <el-table-column label="耗时"><template #default="{ row: item }">{{ formatNumber(item.training_time_seconds) }}s</template></el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="完成时间" width="170">
          <template #default="{ row }">{{ formatDate(row.completed_at) }}</template>
        </el-table-column>
        <el-table-column label="场景" min-width="210">
          <template #default="{ row }">
            <div>{{ row.dataset_id ? `#${row.dataset_id} ${row.dataset_name || '未知数据集'}` : (row.dataset_name || '未知数据集') }}</div>
            <div class="record-sub">预测目标：{{ row.target_column }}</div>
          </template>
        </el-table-column>
        <el-table-column label="参与算法" min-width="230">
          <template #default="{ row }">
            <el-tag v-for="algorithm in row.candidate_algorithms" :key="algorithm" size="small" style="margin:2px">
              {{ modelLabel(algorithm) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="评选标准" width="120">
          <template #default="{ row }">{{ metricShortLabel(row.optimization_metric) }}</template>
        </el-table-column>
        <el-table-column label="最佳算法" min-width="150">
          <template #default="{ row }"><el-tag type="success">{{ modelLabel(row.best_algorithm) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="最佳指标" width="110">
          <template #default="{ row }">{{ formatNumber(row.best_metrics?.[row.optimization_metric]) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }"><el-button link type="danger" @click="deleteSelectionRecord(row)">删除</el-button></template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!recordsLoading && !selectionRecords.length" description="暂无选型记录" />
      <div style="margin-top:16px">
        <el-button @click="step = recordsReturnStep">← 返回</el-button>
        <el-button type="primary" @click="resetSelection" style="margin-left:12px">开始新的算法对比</el-button>
      </div>
    </el-card>

    <!-- 调参抽屉 -->
    <el-drawer v-model="tuningVisible" :title="`调参：${modelLabel(tuningAlgorithm)}`" size="500px" direction="rtl">
      <div class="tuning-section">
        <div class="preset-row">
          <el-select v-model="loadedPresetId" placeholder="加载已存预设" clearable filterable size="small" style="flex:1" @change="applyPreset">
            <el-option v-for="p in presetList" :key="p.id" :label="`${p.name}${p.is_builtin ? '（内置）' : ''}`" :value="p.id" />
          </el-select>
          <el-button size="small" @click="loadPresets(tuningAlgorithm)">刷新</el-button>
        </div>
        <el-divider content-position="left">常用参数</el-divider>
        <el-form label-width="150px" size="small">
          <el-form-item v-for="f in (PARAM_SPECS[tuningAlgorithm] || [])" :key="f.name" :label="f.label || f.name">
            <el-input-number v-if="f.type === 'int' || f.type === 'float'"
              v-model="tuningForm[f.name]" :min="f.min" :max="f.max" :step="f.type === 'float' ? 0.01 : 1"
              :placeholder="f.default === null || f.default === undefined ? '留空=默认' : String(f.default)"
              controls-position="right" style="width:200px" />
            <el-select v-else-if="f.options" v-model="tuningForm[f.name]" clearable style="width:200px">
              <el-option v-for="opt in f.options" :key="opt" :label="opt" :value="opt" />
            </el-select>
            <el-switch v-else-if="f.type === 'bool'" v-model="tuningForm[f.name]" />
            <el-input v-else v-model="tuningForm[f.name]" :placeholder="f.default || ''" style="width:200px" />
          </el-form-item>
        </el-form>
        <el-divider content-position="left">高级：JSON 覆盖</el-divider>
        <el-input v-model="tuningJson" type="textarea" :rows="5" placeholder='{"n_estimators":300}' />
        <div v-if="tuningJsonError" class="json-error">{{ tuningJsonError }}</div>
        <el-divider content-position="left">存为预设</el-divider>
        <div class="preset-save-row">
          <el-input v-model="newPresetName" placeholder="预设名（如 RF-深树）" size="small" style="flex:1" />
          <el-button size="small" type="primary" @click="savePreset">保存</el-button>
        </div>
      </div>
      <template #footer>
        <el-button @click="tuningVisible = false">取消</el-button>
        <el-button type="primary" @click="applyTuning">应用</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del, get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const step = ref(0)
const recordsReturnStep = ref(0)
const loading = ref(false)
const trainingProgress = ref(0)
const currentTrainingModel = ref('')
const completedTrainingModels = ref(0)
const totalTrainingModels = ref(0)
const datasets = ref<any[]>([])
const selectedDataset = ref<any>(null)
const targetCol = ref('')
const primaryMetric = ref('r2')
const selectedModels = ref(['random_forest', 'xgboost', 'lightgbm'])
const results = ref<any[]>([])
const selectionRecords = ref<any[]>([])
const recordsLoading = ref(false)

const targetOptions = ref<string[]>([])
const metricOptions = [
  { value: 'r2', label: 'R²最高（拟合解释能力）' },
  { value: 'rmse', label: 'RMSE最低（更关注较大误差）' },
  { value: 'mae', label: 'MAE最低（平均绝对误差）' },
]
const modelOptions = [
  { value: 'random_forest', label: 'Random Forest', desc: '集成学习，稳健可靠' },
  { value: 'xgboost', label: 'XGBoost', desc: '梯度提升，高精度' },
  { value: 'lightgbm', label: 'LightGBM', desc: '轻量快速' },
  { value: 'gradient_boosting', label: 'Gradient Boosting', desc: '经典集成方法' },
  { value: 'linear_regression', label: 'Linear Regression', desc: '基础线性回归' },
  { value: 'ridge', label: 'Ridge', desc: '线性回归+正则化' },
  { value: 'lasso', label: 'Lasso', desc: 'L1特征选择' },
  { value: 'svm', label: 'Support Vector Machine', desc: '支持向量回归' },
  { value: 'neural_network', label: 'MLP（多层感知机）', desc: '浅层全连接神经网络' },
]

// 各算法可调超参规格（镜像后端 PARAM_SPECS）：专属字段渲染 + 校验提示
const PARAM_SPECS: Record<string, any[]> = {
  random_forest: [
    { name: 'n_estimators', type: 'int', default: 80, min: 10, max: 1000, label: '树数量' },
    { name: 'max_depth', type: 'int', default: null, min: 1, max: 50, label: '最大深度（空=不限）' },
    { name: 'min_samples_split', type: 'int', default: 2, min: 2, label: '最小分裂样本数' },
    { name: 'min_samples_leaf', type: 'int', default: 1, min: 1, label: '叶子最小样本数' },
    { name: 'max_features', type: 'str', default: null, options: ['sqrt', 'log2', 'auto'], label: '最大特征数' },
  ],
  xgboost: [
    { name: 'n_estimators', type: 'int', default: 80, min: 10, max: 1000 },
    { name: 'max_depth', type: 'int', default: 6, min: 1, max: 30 },
    { name: 'learning_rate', type: 'float', default: 0.3, min: 0.01, max: 1.0 },
    { name: 'subsample', type: 'float', default: 1.0, min: 0.5, max: 1.0 },
    { name: 'colsample_bytree', type: 'float', default: 1.0, min: 0.5, max: 1.0 },
  ],
  lightgbm: [
    { name: 'n_estimators', type: 'int', default: 80, min: 10, max: 1000 },
    { name: 'num_leaves', type: 'int', default: 31, min: 2, max: 512 },
    { name: 'learning_rate', type: 'float', default: 0.1, min: 0.01, max: 1.0 },
    { name: 'max_depth', type: 'int', default: -1, min: -1, max: 50 },
    { name: 'min_child_samples', type: 'int', default: 20, min: 1 },
  ],
  gradient_boosting: [
    { name: 'n_estimators', type: 'int', default: 100, min: 10, max: 1000 },
    { name: 'max_depth', type: 'int', default: 3, min: 1, max: 30 },
    { name: 'learning_rate', type: 'float', default: 0.1, min: 0.01, max: 1.0 },
    { name: 'subsample', type: 'float', default: 1.0, min: 0.5, max: 1.0 },
    { name: 'min_samples_split', type: 'int', default: 2, min: 2 },
  ],
  logistic_regression: [
    { name: 'C', type: 'float', default: 1.0, min: 0.0001, label: '正则强度倒数' },
    { name: 'max_iter', type: 'int', default: 500, min: 100, max: 5000 },
    { name: 'penalty', type: 'str', default: 'l2', options: ['l1', 'l2', 'elasticnet', 'none'] },
    { name: 'solver', type: 'str', default: 'lbfgs', options: ['lbfgs', 'liblinear', 'saga', 'newton-cg'] },
  ],
  linear_regression: [
    { name: 'fit_intercept', type: 'bool', default: true, label: '是否计算截距' },
  ],
  ridge: [
    { name: 'alpha', type: 'float', default: 1.0, min: 0.0001, label: 'L2 正则强度' },
    { name: 'fit_intercept', type: 'bool', default: true },
    { name: 'solver', type: 'str', default: 'auto', options: ['auto', 'svd', 'cholesky', 'lsqr', 'sag'] },
  ],
  lasso: [
    { name: 'alpha', type: 'float', default: 0.1, min: 0.0001, label: 'L1 正则强度' },
    { name: 'max_iter', type: 'int', default: 1000, min: 1 },
    { name: 'fit_intercept', type: 'bool', default: true },
    { name: 'selection', type: 'str', default: 'cyclic', options: ['cyclic', 'random'] },
  ],
  svm: [
    { name: 'C', type: 'float', default: 1.0, min: 0.0001, label: '正则强度倒数' },
    { name: 'kernel', type: 'str', default: 'rbf', options: ['linear', 'poly', 'rbf', 'sigmoid'] },
    { name: 'gamma', type: 'str', default: 'scale', options: ['scale', 'auto'] },
    { name: 'degree', type: 'int', default: 3, min: 2, max: 5, label: '仅 poly 有效' },
  ],
  neural_network: [
    { name: 'hidden_layer_sizes', type: 'str', default: '100', label: '隐藏层（逗号分隔，如 64,32）' },
    { name: 'activation', type: 'str', default: 'relu', options: ['relu', 'tanh', 'logistic', 'identity'] },
    { name: 'alpha', type: 'float', default: 0.0001, min: 0.000001, label: 'L2 正则强度' },
    { name: 'max_iter', type: 'int', default: 300, min: 100, max: 2000 },
    { name: 'learning_rate_init', type: 'float', default: 0.001, min: 0.0001, max: 1.0 },
  ],
}

// 调参状态
const modelOverrides = ref<Record<string, any>>({})
const tuningVisible = ref(false)
const tuningAlgorithm = ref('')
const tuningForm = ref<Record<string, any>>({})
const tuningJson = ref('')
const tuningJsonError = ref('')
const presetList = ref<any[]>([])
const loadedPresetId = ref<number | null>(null)
const newPresetName = ref('')

const bestModel = computed(() => results.value[0]?.model_name || '')
const bestMetrics = computed(() => results.value[0] ? {
  r2: results.value[0].r2_mean,
  rmse: results.value[0].rmse_mean,
  mae: results.value[0].mae_mean,
} : null)
const primaryMetricLabel = computed(() => metricOptions.find(item => item.value === primaryMetric.value)?.label || primaryMetric.value)
const bestPrimaryValue = computed(() => bestMetrics.value?.[primaryMetric.value as 'r2' | 'rmse' | 'mae'])

function modelLabel(k: string) {
  const m: Record<string, string> = { random_forest: 'Random Forest', xgboost: 'XGBoost', lightgbm: 'LightGBM', gradient_boosting: 'Gradient Boosting', linear_regression: 'Linear Regression', ridge: 'Ridge', lasso: 'Lasso', svm: 'Support Vector Machine', neural_network: 'MLP' }
  return m[k] || k
}
function r2Color(v: number) { return v >= 0.7 ? '#10b981' : v >= 0.4 ? '#f59e0b' : '#ef4444' }

// ===== 调参（覆盖算法默认超参）=====
function hasOverride(algo: string) {
  return !!modelOverrides.value[algo] && Object.keys(modelOverrides.value[algo]).length > 0
}
function formatParams(p: any) {
  if (!p) return ''
  return Object.entries(p).map(([k, v]) => `${k}=${v as string}`).join(', ')
}
function openTuning(algo: string) {
  tuningAlgorithm.value = algo
  const existing = modelOverrides.value[algo] || {}
  const form: Record<string, any> = {}
  for (const f of (PARAM_SPECS[algo] || [])) {
    if (existing[f.name] !== undefined) form[f.name] = existing[f.name]
  }
  tuningForm.value = form
  tuningJson.value = Object.keys(existing).length ? JSON.stringify(existing, null, 2) : ''
  tuningJsonError.value = ''
  loadedPresetId.value = null
  tuningVisible.value = true
  loadPresets(algo)
}
function collectTuningParams(): Record<string, any> {
  const merged: Record<string, any> = {}
  for (const f of (PARAM_SPECS[tuningAlgorithm.value] || [])) {
    const v = tuningForm.value[f.name]
    if (v !== null && v !== undefined && v !== '') merged[f.name] = v
  }
  if (tuningJson.value.trim()) {
    const extra = JSON.parse(tuningJson.value)
    if (typeof extra !== 'object' || Array.isArray(extra) || extra === null) throw new Error('需为 JSON 对象')
    Object.assign(merged, extra)
  }
  return merged
}
function applyTuning() {
  try {
    const merged = collectTuningParams()
    tuningJsonError.value = ''
    const next = { ...modelOverrides.value }
    if (Object.keys(merged).length) next[tuningAlgorithm.value] = merged
    else delete next[tuningAlgorithm.value]
    modelOverrides.value = next
    tuningVisible.value = false
    ElMessage.success('参数已应用')
  } catch (e: any) {
    tuningJsonError.value = 'JSON 解析失败：' + (e.message || '')
  }
}
async function loadPresets(algo: string) {
  try {
    const res = await get(`/automl/presets?algorithm=${algo}&task_type=regression`)
    presetList.value = res?.items || []
  } catch { presetList.value = [] }
}
function applyPreset(id: any) {
  const p = presetList.value.find(x => x.id === id)
  if (!p) return
  const form: Record<string, any> = {}
  for (const f of (PARAM_SPECS[tuningAlgorithm.value] || [])) {
    if (p.params[f.name] !== undefined) form[f.name] = p.params[f.name]
  }
  tuningForm.value = form
  tuningJson.value = JSON.stringify(p.params, null, 2)
  tuningJsonError.value = ''
}
async function savePreset() {
  if (!newPresetName.value.trim()) { ElMessage.warning('请填预设名'); return }
  let params: Record<string, any> = {}
  try { params = collectTuningParams() } catch (e: any) {
    tuningJsonError.value = 'JSON 解析失败：' + (e.message || ''); return
  }
  try {
    await post('/automl/presets', {
      name: newPresetName.value.trim(),
      task_type: 'regression',
      algorithm: tuningAlgorithm.value,
      params,
    })
    ElMessage.success('预设已保存')
    newPresetName.value = ''
    loadPresets(tuningAlgorithm.value)
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}
function formatNumber(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(4) : '-'
}

onMounted(async () => {
  try {
    const res = await get('/datasets?size=50')
    datasets.value = res?.items || res?.datasets || []
  } catch {}
  loadSelectionRecords()
})

async function loadSelectionRecords() {
  recordsLoading.value = true
  try {
    const response = await get('/automl/selection-records?limit=100')
    selectionRecords.value = response?.items || []
  } catch {
    selectionRecords.value = []
  } finally {
    recordsLoading.value = false
  }
}

async function deleteSelectionRecord(record: any) {
  try {
    await ElMessageBox.confirm(
      '确定删除选型记录 #' + record.id + '「' + (record.name || '未命名') + '」吗？此操作不删除数据集、候选算法或训练模型。',
      '删除选型记录', { type: 'warning' },
    )
    await del('/automl/selection-records/' + record.id)
    ElMessage.success('选型记录已删除')
    await loadSelectionRecords()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.message || '删除失败')
  }
}

function openSelectionRecords() {
  recordsReturnStep.value = results.value.length ? 3 : step.value
  step.value = 4
  loadSelectionRecords()
}

function resetSelection() {
  step.value = 0
  selectedDataset.value = null
  targetCol.value = ''
  targetOptions.value = []
  results.value = []
}

function metricShortLabel(metric: string) {
  const labels: Record<string, string> = { r2: 'R²最高', rmse: 'RMSE最低', mae: 'MAE最低' }
  return labels[metric] || metric
}

function formatDate(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN') : '-'
}

async function selectDataset(row: any) {
  selectedDataset.value = row
  targetCol.value = ''
  try {
    const profile = await get(`/datasets/${row.id}/rows?offset=0&limit=20`)
    const columns: string[] = profile.columns || []
    const rows: any[][] = profile.rows || []
    targetOptions.value = columns.filter((column, columnIndex) => {
      if (/(^id$|_id$|timestamp|time|date|日期|时间)/i.test(column)) return false
      const values = rows
        .map(item => item?.[columnIndex])
        .filter(value => value !== null && value !== undefined && value !== '')
      return values.length > 0 && values.every(value => Number.isFinite(Number(value)))
    })
    targetCol.value = targetOptions.value[targetOptions.value.length - 1] || ''
  } catch { targetOptions.value = [] }
}

async function startTraining() {
  if (!targetCol.value) { ElMessage.warning('请选择有效目标列'); return }
  loading.value = true
  trainingProgress.value = 0
  currentTrainingModel.value = ''
  completedTrainingModels.value = 0
  totalTrainingModels.value = selectedModels.value.length
  step.value = 3
  results.value = []
  try {
    const created = await post('/automl/tasks', {
      name: `AutoML-${selectedDataset.value.name}`,
      task_type: 'regression', dataset_id: String(selectedDataset.value.id),
      target_column: targetCol.value, time_limit_minutes: 30,
      optimization_metric: primaryMetric.value, allowed_models: selectedModels.value,
      model_overrides: Object.keys(modelOverrides.value).length ? modelOverrides.value : undefined,
    })
    await post(`/automl/tasks/${created.task_id}/start`, {})
    for (let attempt = 0; attempt < 600; attempt++) {
      const task = await get(`/automl/tasks/${created.task_id}`)
      trainingProgress.value = Number(task.progress || 0)
      currentTrainingModel.value = task.current_model || ''
      completedTrainingModels.value = Number(task.completed_models || 0)
      totalTrainingModels.value = Number(task.total_models || selectedModels.value.length)
      if (task.status === 'completed') {
        results.value = (task.trials || []).map((trial: any) => ({
          model_name: trial.model,
          type: 'sklearn',
          r2_mean: Number(trial.metrics?.r2),
          rmse_mean: Number(trial.metrics?.rmse),
          mae_mean: Number(trial.metrics?.mae),
          cv_std: Number(trial.metrics?.cv_std),
          primary_value: Number(trial.metrics?.primary_value),
          ranking_score: Number(trial.ranking_score),
          estimator_params: trial.estimator_params || {},
          prediction_preview: trial.prediction_preview || [],
          training_time_seconds: Number(trial.training_time_seconds || 0),
          is_best: trial.model === task.best_model,
          error: trial.metrics?.error || '',
        })).sort((a: any, b: any) => b.ranking_score - a.ranking_score)
        trainingProgress.value = 100
        currentTrainingModel.value = ''
        ElMessage.success(`训练完成！最佳: ${modelLabel(bestModel.value)} (${primaryMetricLabel.value}=${formatNumber(bestPrimaryValue.value)})`)
        return
      }
      if (task.status === 'failed') throw new Error(task.error || 'AutoML 后台任务失败')
      await new Promise(resolve => window.setTimeout(resolve, 1500))
    }
    throw new Error('AutoML 等待超时，任务仍在后台执行')
  } catch (e: any) {
    ElMessage.error('训练失败: ' + (e.message || ''))
  } finally { loading.value = false }
}

</script>

<style scoped>
.automl-page { padding: 20px; max-width: 900px; margin: 0 auto; }
.page-actions { display: flex; justify-content: flex-end; margin: -8px 0 12px; }
.steps-bar { margin-bottom: 24px; }
.step-card { margin-top: 16px; }
.selected-info { margin-top: 12px; padding: 8px 12px; background: rgba(16,185,129,0.1); border-radius: 6px; font-size: 14px; }
.target-list { display: flex; flex-direction: column; gap: 8px; }
.target-radio { width: 100%; padding: 10px; }
.model-list { display: flex; flex-direction: column; gap: 8px; }
.model-check { width: 100%; padding: 10px; }
.model-desc { display: block; font-size: 11px; color: #94a3b8; margin-top: 2px; }
.selection-hint { margin-top: 10px; font-size: 12px; color: #94a3b8; }
.prediction-preview { padding: 12px 24px; }
.preview-title { margin-bottom: 10px; font-weight: 600; }
.training-progress { padding: 28px 20px; }
.progress-message { margin-top: 12px; text-align: center; color: #94a3b8; font-size: 13px; }
.record-results { padding: 12px 24px; }
.record-sub { margin-top: 4px; color: #94a3b8; font-size: 12px; }
.model-row { display: flex; align-items: center; gap: 8px; }
.model-row .model-check { flex: 1; }
.tune-btn { flex-shrink: 0; }
.tuning-section { padding: 0 4px; }
.preset-row { display: flex; gap: 8px; margin-bottom: 8px; }
.preset-save-row { display: flex; gap: 8px; }
.json-error { color: #ef4444; font-size: 12px; margin-top: 6px; }
.param-text { font-size: 12px; color: #cbd5e1; }
.default-param { font-size: 12px; color: #64748b; }
</style>
