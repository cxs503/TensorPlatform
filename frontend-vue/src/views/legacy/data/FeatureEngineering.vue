<template>
  <div class="feature-page">
    <PageHeader title="🧮 特征工程" desc="在数据治理环节计算派生特征，产出含特征列的数据集(stage=engineered)，可直接用于训练" />

    <!-- 数据集选择 -->
    <el-card shadow="hover" class="step-card">
      <template #header><span class="step-title">1. 选择数据集</span></template>
      <el-row :gutter="16">
        <el-col :xs="24" :md="12">
          <el-form label-width="140px">
            <el-form-item label="数据集">
              <el-select v-model="datasetId" placeholder="请选择" filterable @change="onDatasetChange" style="min-width: 300px">
                <el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name} (${d.row_count}行 · ${d.stage})`" :value="d.id" />
              </el-select>
            </el-form-item>
            <el-alert v-if="datasetsError" type="error" :title="datasetsError" show-icon :closable="false" />
            <el-form-item label="时间列(可选)">
              <el-select v-model="timeColumn" placeholder="无（仅时序计算需要）" clearable style="min-width: 240px">
                <el-option v-for="c in timeColumns" :key="'tc' + c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
            <el-alert
              v-if="datasetId && !timeColumns.length"
              type="info"
              title="未识别到时间列；普通公式不受影响，时序滚动请先确认数据中存在时间列"
              :closable="false"
              show-icon
            />
            <el-form-item>
              <el-button type="default" @click="applyTemplate" :disabled="!datasetId">✨ 套用 FeO 模板</el-button>
            </el-form-item>
            <div v-if="templateNotes.length" class="template-notes">{{ templateNotes.join('；') }}</div>
          </el-form>
        </el-col>
        <el-col v-if="columns.length" :xs="24" :md="12">
          <label class="form-label">可计算的数值列（点一下插入到当前公式）</label>
          <div class="column-chips">
            <el-button v-for="c in numericColumns" :key="'chip' + c" size="small" @click="insertCol(c)" :title="c" class="chip-btn">
              {{ short(c) }}
            </el-button>
            <span v-if="!numericColumns.length" class="empty-inline">没有识别到可计算的数值列</span>
          </div>
          <div class="column-summary">
            共 {{ columns.length }} 列，识别到 {{ numericColumns.length }} 个数值列、{{ timeColumns.length }} 个时间列
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 转换配置 -->
    <el-card shadow="hover" class="step-card">
      <template #header>
        <div class="card-header-flex">
          <span class="step-title">2. 配置转换</span>
          <div class="card-header-actions">
            <el-button size="small" @click="addTransform('expression')">+ 公式</el-button>
            <el-button size="small" @click="addTransform('rolling')">+ 滚动/时序</el-button>
            <el-button size="small" type="primary" @click="openRecommendations" :disabled="!datasetId">🤖 AI 智能推荐</el-button>
            <el-button size="small" @click="openRules">自定义方案</el-button>
            <el-button size="small" @click="showUploadRuleDialog = true">上传规则</el-button>
          </div>
        </div>
      </template>
      <div v-if="!transforms.length" class="empty-hint">
        <el-empty description="尚未添加转换。可点「+ 公式 / + 滚动」或「套用 FeO 模板」" :image-size="60" />
      </div>
      <div v-for="(t, i) in transforms" :key="i" class="transform-item">
        <el-form label-width="90px" inline>
          <el-form-item label="类型">
            <el-select v-model="t.type" style="min-width: 130px" @change="onTransformTypeChange(t, i)">
              <el-option label="公式 expression" value="expression" />
              <el-option label="滚动 rolling" value="rolling" />
              <el-option label="滞后 lag" value="lag" />
              <el-option label="时间拆分" value="time_component" />
              <el-option label="归一化" value="normalize" />
            </el-select>
          </el-form-item>
          <el-form-item label="新列名">
            <el-input v-model="t.name" style="min-width: 140px" />
          </el-form-item>
          <el-form-item v-if="t.type === 'expression'" label="噪声 σ">
            <el-input-number v-model="t.noise" :precision="2" style="width: 100px" placeholder="0" />
          </el-form-item>
          <el-form-item v-if="t.type === 'lag'" label="源列">
            <el-select v-model="t.source_column" filterable style="min-width: 150px">
              <el-option v-for="c in sourceOptions(i)" :key="'lag' + c + i" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="t.type === 'lag'" label="滞后行数">
            <el-input-number v-model="t.periods" :min="1" :step="1" step-strictly style="width: 90px" />
          </el-form-item>
          <el-form-item v-if="t.type === 'time_component'" label="时间列">
            <el-select v-model="t.source_column" filterable style="min-width: 150px">
              <el-option v-for="c in timeColumns" :key="'time' + c + i" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="t.type === 'time_component'" label="拆分项">
            <el-select v-model="t.component" style="min-width: 110px">
              <el-option label="小时" value="hour" />
              <el-option label="星期" value="dayofweek" />
              <el-option label="日期" value="day" />
              <el-option label="月份" value="month" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="t.type === 'normalize'" label="源列">
            <el-select v-model="t.source_column" filterable style="min-width: 150px">
              <el-option v-for="c in sourceOptions(i)" :key="'norm' + c + i" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="t.type === 'normalize'" label="方式">
            <el-select v-model="t.method" style="min-width: 110px">
              <el-option label="0~1 归一化" value="minmax" />
              <el-option label="Z-score 标准化" value="zscore" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="t.type === 'rolling'" label="源列">
            <el-select v-model="t.source_column" filterable style="min-width: 150px">
              <el-option v-for="c in sourceOptions(i)" :key="'sc' + c + i" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="t.type === 'rolling'" label="窗口">
            <el-input-number v-model="t.window" :min="1" :step="1" step-strictly style="width: 80px" />
          </el-form-item>
          <el-form-item v-if="t.type === 'rolling'" label="聚合">
            <el-select v-model="t.agg" style="min-width: 110px">
              <el-option v-for="a in aggs" :key="a" :label="a" :value="a" />
            </el-select>
          </el-form-item>
          <el-form-item label="空值填充">
            <el-input-number v-model="t.fill_value" :precision="2" style="width: 90px" placeholder="不填" />
          </el-form-item>
          <el-form-item>
            <el-button type="danger" size="small" @click="transforms.splice(i, 1)">✕ 删除</el-button>
          </el-form-item>
        </el-form>
        <div v-if="t.type === 'expression'" class="expression-row">
          <label class="form-label">表达式(列名用 {'{'}列名{'}'} 包裹，仅 + - * / % **)</label>
          <el-input v-model="t.expression" @focus="activeIdx = i" placeholder="例:({'煤粉12#仓反馈值'}+{'焦粉13#仓反馈值'})/({'铁料1#仓反馈值'}+{'铁料2#仓反馈值'})" />
        </div>
      </div>
    </el-card>

    <!-- 操作 -->
    <el-card shadow="hover" class="step-card">
      <el-alert
        v-if="validationErrors.length"
        type="warning"
        :title="validationErrors[0]"
        :description="validationErrors.slice(1).join('；')"
        :closable="false"
        show-icon
        class="validation-alert"
      />
      <el-button type="default" @click="preview" :disabled="!canSubmit || running">👁 预览(前5行)</el-button>
      <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" @click="run" :disabled="!canSubmit || running" :loading="running" class="ml-8">
        {{ running ? '执行中...' : '▶ 执行特征工程' }}
      </el-button>
      <span v-if="runResult" class="run-success">
        ✅ 已生成数据集 #{{ runResult.dataset_id }}「{{ runResult.name }}」，新增列: {{ runResult.new_columns?.join(', ') }}
      </span>
    </el-card>

    <!-- 预览结果 -->
    <el-card v-if="previewData" shadow="hover" class="step-card">
      <template #header>
        <div class="card-header-flex">
          <span class="step-title">预览结果</span>
          <span class="preview-shape">形状 {{ previewData.shape?.join(' × ') }} · 新增列 {{ previewData.new_columns?.join(', ') }}</span>
        </div>
      </template>
      <el-alert
        v-for="(warning, index) in previewData.warnings || []"
        :key="'warning-' + index"
        type="warning"
        :title="warning"
        :closable="false"
        show-icon
        class="quality-warning"
      />
      <el-table v-if="previewData.feature_quality?.length" :data="previewData.feature_quality" size="small" border class="quality-table">
        <el-table-column prop="name" label="新特征" min-width="130" />
        <el-table-column prop="valid_count" label="有效值" width="90" />
        <el-table-column prop="missing_count" label="空值" width="80" />
        <el-table-column prop="unique_count" label="不同值" width="90" />
        <el-table-column prop="min" label="最小值" min-width="100" />
        <el-table-column prop="max" label="最大值" min-width="100" />
        <el-table-column prop="mean" label="平均值" min-width="100" />
      </el-table>
      <div class="preview-table-wrap">
        <el-table :data="previewRows" stripe border style="width: 100%" max-height="360">
          <el-table-column v-for="c in previewData.sample_columns" :key="c" :prop="c" :label="c" min-width="120">
            <template #default="{ row }">
              <span :style="previewData.new_columns?.includes(c) ? 'color: #34d399' : ''">
                {{ fmtCell(row[c]) }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
    <el-dialog v-model="recommendVisible" title="智能推荐特征" width="780px" destroy-on-close>
      <el-skeleton v-if="recommendLoading" :rows="5" animated />
      <template v-else>
        <el-empty v-if="!recommendations.length" description="未发现适合自动推荐的特征，可使用高级配置手动添加。" :image-size="72" />
        <template v-else>
          <div class="recommend-profile">
            已识别 {{ recommendationProfile.numeric_columns?.length || 0 }} 个数值列、{{ recommendationProfile.time_columns?.length || 0 }} 个时间列
          </div>
          <el-alert
            v-if="recommendationAi?.summary"
            type="info"
            :title="`大模型辅助说明（${recommendationAi.provider || 'DeepSeek'}）`"
            :description="recommendationAi.summary"
            :closable="false"
            show-icon
            class="ai-assistance"
          />
          <el-alert
            v-else-if="recommendationAi?.warning"
            type="warning"
            :title="recommendationAi.warning"
            :closable="false"
            show-icon
            class="ai-assistance"
          />
          <el-checkbox-group v-model="selectedRecommendationKeys" @change="recommendationPreviewed = false" class="recommend-list">
            <el-card v-for="item in recommendations" :key="item.key" shadow="never" class="recommend-item">
              <div class="recommend-head">
                <el-checkbox :label="item.key">{{ item.title }}</el-checkbox>
                <span class="recommend-column">新增：{{ item.transform.name }}</span>
              </div>
              <div class="recommend-desc">{{ item.description }}</div>
              <div v-if="recommendationPriority(item.key)" class="recommend-ai-note">
                建议优先级：{{ recommendationPriority(item.key).priority === 'high' ? '高' : recommendationPriority(item.key).priority === 'low' ? '低' : '中' }}
                <span v-if="recommendationPriority(item.key).reason"> · {{ recommendationPriority(item.key).reason }}</span>
              </div>
              <div v-if="selectedRecommendationKeys.includes(item.key)" class="recommend-params">
                <template v-if="item.transform.type === 'rolling'">
                  <span>窗口</span>
                  <el-input-number v-model="item.transform.window" :min="1" :step="1" step-strictly size="small" @change="recommendationPreviewed = false" />
                </template>
                <template v-else-if="item.transform.type === 'lag'">
                  <span>滞后行数</span>
                  <el-input-number v-model="item.transform.periods" :min="1" :step="1" step-strictly size="small" @change="recommendationPreviewed = false" />
                </template>
                <template v-else-if="item.transform.type === 'normalize'">
                  <span>方式</span>
                  <el-select v-model="item.transform.method" size="small" style="width: 150px" @change="recommendationPreviewed = false">
                    <el-option label="0~1 归一化" value="minmax" />
                    <el-option label="Z-score 标准化" value="zscore" />
                  </el-select>
                </template>
              </div>
            </el-card>
          </el-checkbox-group>
          <el-alert v-for="note in recommendationNotes" :key="note" :title="note" type="info" :closable="false" show-icon />
          <el-alert v-for="risk in recommendationAi?.risks || []" :key="risk" :title="`人工确认：${risk}`" type="warning" :closable="false" show-icon class="ai-risk" />
        </template>
      </template>
      <template #footer>
        <el-button @click="recommendVisible = false">取消</el-button>
        <el-button @click="openSourceDatasetPreview" :disabled="recommendLoading || !datasetId">预览原始数据集</el-button>
        <el-button @click="previewRecommendations" :disabled="recommendLoading || !selectedRecommendationKeys.length">预览新增字段</el-button>
        <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" :loading="running" @click="runRecommendations" :disabled="recommendLoading || !selectedRecommendationKeys.length">确认生成数据集</el-button>

      </template>
    </el-dialog>
    <el-dialog v-model="sourceDatasetPreviewVisible" :title="'数据集预览：#' + (datasetId || '-') + ' ' + selectedDatasetName" width="920px" append-to-body>
      <el-skeleton v-if="sourceDatasetPreviewLoading" :rows="6" animated />
      <template v-else>
        <el-empty v-if="!sourceDatasetPreviewRows.length" description="暂无可预览的数据行" :image-size="68" />
        <template v-else>
          <div class="source-preview-meta">
            <span>展示前 {{ sourceDatasetPreviewRows.length }} 行原始数据，仅用于确认字段与数据内容。</span>
            <el-select v-model="sourceDatasetPreviewLimit" size="small" :disabled="sourceDatasetPreviewLoading" @change="loadSourceDatasetPreview" style="width:110px">
              <el-option v-for="limit in sourceDatasetPreviewLimitOptions" :key="limit" :label="limit + ' 行'" :value="limit" />
            </el-select>
          </div>
          <el-table :data="sourceDatasetPreviewRows" stripe border max-height="480" style="width:100%">
            <el-table-column v-for="column in sourceDatasetPreviewColumns" :key="column" :prop="column" :label="column" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ fmtCell(row[column]) }}</template>
            </el-table-column>
          </el-table>
        </template>
      </template>
      <template #footer><el-button type="primary" @click="sourceDatasetPreviewVisible = false">返回推荐</el-button></template>
    </el-dialog>
    <el-dialog v-model="showRulesDialog" title="自定义特征方案" width="820px" destroy-on-close>
      <el-form inline class="rule-save-form">
        <el-form-item label="方案名称" required><el-input v-model="ruleForm.name" placeholder="例如：温度时序特征" style="width:220px" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="ruleForm.description" placeholder="可选" style="width:240px" /></el-form-item>
        <el-form-item><el-button type="primary" :loading="savingRule" @click="saveCurrentRule" :disabled="!transforms.length">保存当前配置</el-button></el-form-item>
        <el-form-item><el-button @click="openLowCodeBuilder">低代码新建步骤</el-button></el-form-item>
      </el-form>
      <el-table :data="featureRules" border size="small" max-height="360">
        <el-table-column prop="name" label="方案名称" min-width="170" />
        <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="来源" width="100"><template #default="{ row }">{{ row.source_mode === 'code' ? '规则文件' : '界面配置' }}</template></el-table-column>
        <el-table-column prop="transformation_count" label="步骤数" width="80" />
        <el-table-column label="操作" width="160" fixed="right"><template #default="{ row }">
          <el-button link type="primary" @click="loadRule(row)">加载</el-button>
          <el-button v-if="userStore.hasPermission('datasets:process')" link type="danger" @click="deleteRule(row)">删除</el-button>
        </template></el-table-column>
      </el-table>
      <el-empty v-if="!featureRules.length" description="还没有保存的自定义方案" :image-size="60" />
      <template #footer><el-button @click="showRulesDialog = false">关闭</el-button></template>
    </el-dialog>

    <el-dialog v-model="lowCodeVisible" title="低代码新建特征步骤" width="640px" destroy-on-close>
      <el-form label-width="105px">
        <el-form-item label="处理方式" required>
          <el-select v-model="lowCodeForm.type" style="width:260px" @change="resetLowCodeFields">
            <el-option label="两个字段计算" value="expression" />
            <el-option label="滑动统计" value="rolling" />
            <el-option label="滞后值" value="lag" />
            <el-option label="时间拆分" value="time_component" />
            <el-option label="归一化" value="normalize" />
          </el-select>
        </el-form-item>
        <el-form-item label="新字段名称" required><el-input v-model="lowCodeForm.name" placeholder="例如：温度_5行均值" /></el-form-item>
        <template v-if="lowCodeForm.type === 'expression'">
          <el-form-item label="第一个字段" required><el-select v-model="lowCodeForm.left" filterable style="width:100%"><el-option v-for="c in numericColumns" :key="'left'+c" :label="c" :value="c" /></el-select></el-form-item>
          <el-form-item label="计算方式" required><el-select v-model="lowCodeForm.operator" style="width:180px"><el-option label="相加" value="+" /><el-option label="相减" value="-" /><el-option label="相乘" value="*" /><el-option label="相除" value="/" /></el-select></el-form-item>
          <el-form-item label="第二个字段" required><el-select v-model="lowCodeForm.right" filterable style="width:100%"><el-option v-for="c in numericColumns" :key="'right'+c" :label="c" :value="c" /></el-select></el-form-item>
        </template>
        <template v-else-if="lowCodeForm.type === 'rolling'">
          <el-form-item label="源字段" required><el-select v-model="lowCodeForm.source_column" filterable style="width:100%"><el-option v-for="c in numericColumns" :key="'roll'+c" :label="c" :value="c" /></el-select></el-form-item>
          <el-form-item label="统计方式"><el-select v-model="lowCodeForm.agg" style="width:180px"><el-option label="平均值" value="mean" /><el-option label="最大值" value="max" /><el-option label="最小值" value="min" /><el-option label="标准差" value="std" /><el-option label="变化量" value="diff" /><el-option label="变化趋势" value="slope" /></el-select></el-form-item>
          <el-form-item label="窗口大小"><el-input-number v-model="lowCodeForm.window" :min="1" :step="1" step-strictly /></el-form-item>
        </template>
        <template v-else-if="lowCodeForm.type === 'lag'">
          <el-form-item label="源字段" required><el-select v-model="lowCodeForm.source_column" filterable style="width:100%"><el-option v-for="c in numericColumns" :key="'lag'+c" :label="c" :value="c" /></el-select></el-form-item>
          <el-form-item label="滞后行数"><el-input-number v-model="lowCodeForm.periods" :min="1" :step="1" step-strictly /></el-form-item>
        </template>
        <template v-else-if="lowCodeForm.type === 'time_component'">
          <el-form-item label="时间字段" required><el-select v-model="lowCodeForm.source_column" filterable style="width:100%"><el-option v-for="c in timeColumns" :key="'time'+c" :label="c" :value="c" /></el-select></el-form-item>
          <el-form-item label="拆分内容"><el-select v-model="lowCodeForm.component" style="width:180px"><el-option label="小时" value="hour" /><el-option label="星期" value="dayofweek" /><el-option label="日期" value="day" /><el-option label="月份" value="month" /></el-select></el-form-item>
        </template>
        <template v-else>
          <el-form-item label="源字段" required><el-select v-model="lowCodeForm.source_column" filterable style="width:100%"><el-option v-for="c in numericColumns" :key="'norm'+c" :label="c" :value="c" /></el-select></el-form-item>
          <el-form-item label="处理方式"><el-select v-model="lowCodeForm.method" style="width:180px"><el-option label="0~1 归一化" value="minmax" /><el-option label="Z-score 标准化" value="zscore" /></el-select></el-form-item>
        </template>
      </el-form>
      <template #footer><el-button @click="lowCodeVisible = false">取消</el-button><el-button type="primary" @click="addLowCodeStep">添加到当前方案</el-button></template>
    </el-dialog>

    <el-dialog v-model="showUploadRuleDialog" title="上传自定义特征规则" width="620px" destroy-on-close>
      <el-alert type="info" :closable="false" show-icon title="规则文件只声明 FEATURE_RULES 步骤，平台不会执行其中的任意 Python 代码。" style="margin-bottom:16px" />
      <el-form label-width="92px">
        <el-form-item label="方案名称" required><el-input v-model="uploadRuleForm.name" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="uploadRuleForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="规则文件" required>
          <el-upload :auto-upload="false" :limit="1" accept=".py,.json" :on-change="onRuleFileChange" :on-remove="onRuleFileRemove">
            <el-button>选择 .py 或 .json 文件</el-button>
          </el-upload>
          <el-button link type="primary" @click="downloadRuleTemplate">下载示例模板</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUploadRuleDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploadingRule" @click="uploadRule">解析并保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>



<script setup lang="ts">
import { ref, watch, computed, onMounted } from 'vue'
import { del, downloadFile, get, post, postForm } from '@/api/legacy'
import { useAsyncData } from '@/composables/useAsyncData'
import { ElMessage } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { useUserStore } from '@/stores/user'

type ColumnMeta = {
  name: string
  dtype: string
  numeric: boolean
  time: boolean
}

const { data: datasetsRaw, error: datasetsError } = useAsyncData<any>(
  () => get<any>('/datasets')
)
const datasets = ref<any[]>([])
// Derive datasets from raw data
watch(
  datasetsRaw,
  (v) => { datasets.value = (v?.items || []).filter((i: any) => i.row_count > 0 && i.has_file) },
  { immediate: true }
)

const userStore = useUserStore()

const datasetId = ref<number | null>(null)
const columns = ref<string[]>([])
const columnMeta = ref<ColumnMeta[]>([])
const timeColumn = ref<string | null>(null)
const transforms = ref<any[]>([])
const activeIdx = ref(-1)
const aggs = ['slope', 'diff', 'mean', 'std', 'var', 'sum', 'min', 'max']
const templateNotes = ref<string[]>([])
const previewData = ref<any>(null)
const runResult = ref<any>(null)
const running = ref(false)
const recommendVisible = ref(false)
const recommendLoading = ref(false)
const recommendations = ref<any[]>([])
const recommendationProfile = ref<any>({})
const recommendationNotes = ref<string[]>([])
const recommendationAi = ref<any>(null)
const selectedRecommendationKeys = ref<string[]>([])
const recommendationPreviewed = ref(false)
const sourceDatasetPreviewVisible = ref(false)
const sourceDatasetPreviewLoading = ref(false)
const sourceDatasetPreview = ref<any>(null)
const sourceDatasetPreviewLimit = ref(50)
const sourceDatasetPreviewLimitOptions = [10, 20, 50, 70, 100]
const showRulesDialog = ref(false)
const showUploadRuleDialog = ref(false)
const featureRules = ref<any[]>([])
const savingRule = ref(false)
const uploadingRule = ref(false)
const ruleForm = ref({ name: '', description: '' })
const uploadRuleForm = ref({ name: '', description: '', file: null as File | null })
const lowCodeVisible = ref(false)
const lowCodeForm = ref<any>({ type: 'expression', name: '', left: '', right: '', operator: '+', source_column: '', window: 5, agg: 'mean', periods: 1, component: 'hour', method: 'minmax' })
const numericColumns = computed(() => columnMeta.value.filter((item) => item.numeric).map((item) => item.name))
const timeColumns = computed(() => columnMeta.value.filter((item) => item.time).map((item) => item.name))

const short = (c: string) => (c.length > 10 ? c.slice(0, 9) + '…' : c)
const fmtCell = (v: any) => {
  if (v == null) return ''
  return typeof v === 'number' ? (Math.abs(v) < 1 ? v.toFixed(4) : v.toFixed(2)) : v
}

const recommendationPriority = (key: string) =>
  (recommendationAi.value?.priorities || []).find((item: any) => item.id === key)

// 后端预览返回的 sample 是「数组的数组」[[v1,v2,...],...] 并配 sample_columns。
// el-table 对数组行渲染不稳定(易出现 [object Object]),这里统一规整成对象行,
// 让 <el-table-column :prop="c"> 能按列名稳定取值。兼容后端将来返回对象数组的情况。
const previewRows = computed<any[]>(() => {
  const pd = previewData.value
  if (!pd) return []
  const cols: string[] = pd.sample_columns || []
  const sample = pd.sample || []
  return (sample as any[]).map((row) => {
    // 已经是对象(按列名取值)则直接用
    if (row && typeof row === 'object' && !Array.isArray(row)) return row
    // 数组行:按 sample_columns 下标映射成对象
    if (Array.isArray(row)) {
      const o: Record<string, any> = {}
      cols.forEach((c, i) => { o[c] = row[i] })
      return o
    }
    // 标量(理论上不会出现)包一层
    return { [cols[0]]: row }
  })
})

const selectedDatasetName = computed(() => datasets.value.find((item: any) => item.id === datasetId.value)?.name || '')
const sourceDatasetPreviewColumns = computed<string[]>(() => (sourceDatasetPreview.value?.columns || []).map((item: any) => String(item)))
const sourceDatasetPreviewRows = computed<any[]>(() => {
  const columns = sourceDatasetPreviewColumns.value
  return (sourceDatasetPreview.value?.rows || []).map((row: any) => {
    if (row && typeof row === 'object' && !Array.isArray(row)) return row
    const mapped: Record<string, any> = {}
    if (Array.isArray(row)) columns.forEach((column, index) => { mapped[column] = row[index] })
    return mapped
  })
})

const loadDatasets = async () => {
  try {
    const data = await get<any>('/datasets')
    datasets.value = (data?.items || data || []).filter((i: any) => i.row_count > 0 && i.has_file)
  } catch (e: any) {
    ElMessage.error('加载数据集失败: ' + (e.message || '未知错误'))
  }
}

const resetLowCodeFields = () => {
  const type = lowCodeForm.value.type
  lowCodeForm.value = {
    type, name: '', left: numericColumns.value[0] || '', right: numericColumns.value[1] || numericColumns.value[0] || '', operator: '+',
    source_column: type === 'time_component' ? (timeColumns.value[0] || '') : (numericColumns.value[0] || ''),
    window: 5, agg: 'mean', periods: 1, component: 'hour', method: 'minmax'
  }
}

const openLowCodeBuilder = () => {
  if (datasetId.value == null || !columns.value.length) { ElMessage.warning('请先选择数据集并等待字段加载完成'); return }
  resetLowCodeFields()
  lowCodeVisible.value = true
}

const addLowCodeStep = () => {
  const form = lowCodeForm.value
  const name = String(form.name || '').trim()
  if (!name) { ElMessage.warning('请填写新字段名称'); return }
  if ([...columns.value, ...transforms.value.map((item) => String(item.name || '').trim())].includes(name)) { ElMessage.warning('新字段名称不能与已有字段重复'); return }
  let transform: any
  if (form.type === 'expression') {
    if (!form.left || !form.right) { ElMessage.warning('请选择两个计算字段'); return }
    transform = { type: 'expression', name, expression: `{${form.left}}${form.operator}{${form.right}}`, fill_value: form.operator === '/' ? 0 : null }
  } else if (form.type === 'rolling') {
    if (!form.source_column) { ElMessage.warning('请选择源字段'); return }
    transform = { type: 'rolling', name, source_column: form.source_column, window: form.window, agg: form.agg, fill_value: null }
  } else if (form.type === 'lag') {
    if (!form.source_column) { ElMessage.warning('请选择源字段'); return }
    transform = { type: 'lag', name, source_column: form.source_column, periods: form.periods }
  } else if (form.type === 'time_component') {
    if (!form.source_column) { ElMessage.warning('请选择时间字段'); return }
    transform = { type: 'time_component', name, source_column: form.source_column, component: form.component }
    timeColumn.value = form.source_column
  } else {
    if (!form.source_column) { ElMessage.warning('请选择源字段'); return }
    transform = { type: 'normalize', name, source_column: form.source_column, method: form.method }
  }
  transforms.value.push(transform)
  previewData.value = null
  runResult.value = null
  lowCodeVisible.value = false
  ElMessage.success('已添加到当前方案；可继续添加步骤、预览或保存为常用方案')
}

const loadFeatureRules = async () => {
  try {
    const data = await get<any>('/feature-engineering/rules')
    featureRules.value = data.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || '加载自定义方案失败')
  }
}

const openRules = async () => {
  showRulesDialog.value = true
  await loadFeatureRules()
}

const saveCurrentRule = async () => {
  if (!transforms.value.length) { ElMessage.warning('请先配置至少一个特征步骤'); return }
  if (!ruleForm.value.name.trim()) { ElMessage.warning('请填写方案名称'); return }
  savingRule.value = true
  try {
    await post('/feature-engineering/rules', {
      name: ruleForm.value.name.trim(),
      description: ruleForm.value.description || undefined,
      definition: { time_column: timeColumn.value || undefined, transformations: cleanTransforms() }
    })
    ElMessage.success('自定义方案已保存')
    ruleForm.value = { name: '', description: '' }
    await loadFeatureRules()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存方案失败')
  }
  savingRule.value = false
}

const loadRule = (rule: any) => {
  const definition = rule.definition || {}
  transforms.value = (definition.transformations || []).map((item: any) => JSON.parse(JSON.stringify(item)))
  timeColumn.value = definition.time_column || null
  previewData.value = null
  runResult.value = null
  showRulesDialog.value = false
  ElMessage.success(`已加载方案「${rule.name}」，请先预览再生成数据集`)
}

const deleteRule = async (rule: any) => {
  try {
    await del(`/feature-engineering/rules/${rule.id}`)
    ElMessage.success('自定义方案已删除')
    await loadFeatureRules()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除方案失败')
  }
}

const onRuleFileChange = (file: any) => { uploadRuleForm.value.file = file.raw || null }
const onRuleFileRemove = () => { uploadRuleForm.value.file = null }

const downloadRuleTemplate = async () => {
  try { await downloadFile('/feature-engineering/rule-template', 'foundation_feature_rule.py') }
  catch (e: any) { ElMessage.error(e?.message || '下载模板失败') }
}

const uploadRule = async () => {
  const form = uploadRuleForm.value
  if (!form.name.trim()) { ElMessage.warning('请填写方案名称'); return }
  if (!form.file) { ElMessage.warning('请选择规则文件'); return }
  uploadingRule.value = true
  try {
    const data = new FormData()
    data.append('name', form.name.trim())
    data.append('description', form.description || '')
    data.append('file', form.file)
    const result = await postForm<any>('/feature-engineering/rules/upload', data)
    transforms.value = (result.definition?.transformations || []).map((item: any) => JSON.parse(JSON.stringify(item)))
    timeColumn.value = result.definition?.time_column || null
    uploadRuleForm.value = { name: '', description: '', file: null }
    showUploadRuleDialog.value = false
    ElMessage.success('规则文件已保存并加载，可先预览新增字段')
    await loadFeatureRules()
  } catch (e: any) {
    ElMessage.error(e?.message || '上传规则失败')
  }
  uploadingRule.value = false
}

const isNumericDtype = (dtype: string) => /(^|\b)(u?int|float|double|decimal|number)(\d+)?(\b|$)/i.test(dtype)
const isTimeDtype = (dtype: string) => /(datetime|timestamp|date|time)/i.test(dtype)
const isTimeName = (name: string) => /(^|[_\-])(time|date|timestamp)($|[_\-])|时间|日期|时刻/i.test(name)

const inferColumnMeta = (names: string[], rows: any[]): ColumnMeta[] => names.map((name, index) => {
  const values = rows
    .map((row: any) => Array.isArray(row) ? row[index] : row?.[name])
    .filter((value: any) => value !== null && value !== undefined && String(value).trim() !== '')
  const numeric = values.length > 0 && values.every((value: any) => Number.isFinite(Number(value)))
  const parsedTimes = values.filter((value: any) => !Number.isNaN(Date.parse(String(value)))).length
  const time = isTimeName(name) || (values.length > 0 && parsedTimes / values.length >= 0.8)
  return { name, dtype: numeric ? 'number' : (time ? 'datetime' : 'text'), numeric, time }
})

const onDatasetChange = async () => {
  columns.value = []
  columnMeta.value = []
  transforms.value = []
  timeColumn.value = null
  templateNotes.value = []
  previewData.value = null
  runResult.value = null
  recommendations.value = []
  selectedRecommendationKeys.value = []
  recommendationPreviewed.value = false
  if (datasetId.value == null) return
  try {
    const detail = await get<any>(`/datasets/${datasetId.value}`)
    const info = detail?.columns_info
    if (info) {
      const parsed = typeof info === 'string' ? JSON.parse(info) : info
      if (Array.isArray(parsed)) {
        columnMeta.value = parsed.map((c: any) => {
          const name = String(c?.name ?? c)
          const dtype = String(c?.dtype || '')
          return {
            name,
            dtype,
            numeric: isNumericDtype(dtype),
            time: isTimeDtype(dtype) || isTimeName(name)
          }
        })
        columns.value = columnMeta.value.map((item) => item.name)
      }
    }

    // /rows 是数据集模块真实存在的预览接口。除回退列名外，也用于纠正
    // CSV 数字字符串被元数据标成 object 的情况。
    try {
      const p = await get<any>(`/datasets/${datasetId.value}/rows?limit=20`)
      const rowColumns = (p.columns || []).map((c: any) => String(c))
      const inferred = inferColumnMeta(rowColumns, p.rows || [])
      if (!columnMeta.value.length) {
        columnMeta.value = inferred
      } else {
        const inferredByName = new Map(inferred.map((item) => [item.name, item]))
        columnMeta.value = columnMeta.value.map((item) => {
          const sample = inferredByName.get(item.name)
          return {
            ...item,
            numeric: item.numeric || Boolean(sample?.numeric),
            time: item.time || Boolean(sample?.time)
          }
        })
      }
      columns.value = columnMeta.value.map((item) => item.name)
    } catch {
      // 详情中的 columns_info 已足够时不影响继续使用。
    }
    if (!columns.value.length) throw new Error('无法读取该数据集的列信息，请检查数据文件')
  } catch (e: any) {
    columns.value = []
    columnMeta.value = []
    ElMessage.error(e?.message || '加载列信息失败')
  }
}

const nextFeatureName = () => {
  const used = new Set([...columns.value, ...transforms.value.map((item) => String(item.name || '').trim())])
  let index = transforms.value.length + 1
  while (used.has('特征' + index)) index += 1
  return '特征' + index
}

const addTransform = (type: string) => {
  const base = { type, name: nextFeatureName(), fill_value: null as number | null }
  if (type === 'expression') transforms.value.push({ ...base, expression: '', noise: null })
  else if (type === 'rolling') transforms.value.push({ ...base, fill_value: 0, source_column: numericColumns.value[0] || '', window: 10, agg: 'slope' })
  else if (type === 'lag') transforms.value.push({ ...base, source_column: numericColumns.value[0] || '', periods: 1 })
  else if (type === 'time_component') transforms.value.push({ ...base, source_column: timeColumns.value[0] || '', component: 'hour' })
  else if (type === 'normalize') transforms.value.push({ ...base, source_column: numericColumns.value[0] || '', method: 'minmax' })
}

const sourceOptions = (index: number) => {
  const priorDerived = transforms.value.slice(0, index).map((item) => String(item.name || '').trim()).filter(Boolean)
  return Array.from(new Set([...numericColumns.value, ...priorDerived]))
}

const onTransformTypeChange = (transform: any, index: number) => {
  transform.name = transform.name || nextFeatureName()
  transform.fill_value = transform.fill_value ?? null
  if (transform.type === 'expression') {
    transform.expression = transform.expression || ''
    transform.noise = transform.noise ?? null
  } else if (transform.type === 'rolling') {
    transform.source_column = transform.source_column || sourceOptions(index)[0] || ''
    transform.window = Number.isInteger(transform.window) && transform.window > 0 ? transform.window : 10
    transform.agg = aggs.includes(transform.agg) ? transform.agg : 'mean'
  } else if (transform.type === 'lag') {
    transform.source_column = transform.source_column || sourceOptions(index)[0] || ''
    transform.periods = Number.isInteger(transform.periods) && transform.periods > 0 ? transform.periods : 1
  } else if (transform.type === 'time_component') {
    transform.source_column = transform.source_column || timeColumns.value[0] || ''
    transform.component = ['hour', 'dayofweek', 'day', 'month'].includes(transform.component) ? transform.component : 'hour'
  } else if (transform.type === 'normalize') {
    transform.source_column = transform.source_column || sourceOptions(index)[0] || ''
    transform.method = ['minmax', 'zscore'].includes(transform.method) ? transform.method : 'minmax'
  }
}

const insertCol = (c: string) => {
  const idx = activeIdx.value >= 0 ? activeIdx.value : transforms.value.findIndex((t) => t.type === 'expression')
  if (idx < 0 || transforms.value[idx].type !== 'expression') { ElMessage.warning('请先添加一个「公式」转换并点入表达式框'); return }
  transforms.value[idx].expression += '{' + c + '}'
}

const applyTemplate = async () => {
  if (datasetId.value == null) return
  try {
    const d = await get<any>(`/feature-engineering/templates?dataset_id=${datasetId.value}`)
    const t = (d.templates || []).find((x: any) => x.key === 'feo')
    if (!t || !t.transformations?.length) { ElMessage.warning('未匹配到 FeO 模板列'); return }
    transforms.value = t.transformations.map((x: any) => ({ ...x }))
    templateNotes.value = t.notes || []
  } catch (e) { ElMessage.error('模板获取失败') }
}

const loadSourceDatasetPreview = async () => {
  if (datasetId.value == null) return
  sourceDatasetPreviewLoading.value = true
  sourceDatasetPreview.value = null
  try {
    sourceDatasetPreview.value = await get<any>('/datasets/' + datasetId.value + '/rows?limit=' + sourceDatasetPreviewLimit.value)
  } catch (e: any) {
    ElMessage.error(e?.message || '读取数据集预览失败')
  } finally {
    sourceDatasetPreviewLoading.value = false
  }
}

const openSourceDatasetPreview = async () => {
  if (datasetId.value == null) return
  sourceDatasetPreviewVisible.value = true
  await loadSourceDatasetPreview()
}

const openRecommendations = async () => {
  if (datasetId.value == null) { ElMessage.warning('请先选择数据集'); return }
  recommendVisible.value = true
  recommendLoading.value = true
  recommendationPreviewed.value = false
  try {
    const data = await post<any>('/feature-engineering/recommend', { source_dataset_id: datasetId.value })
    recommendations.value = data.recommendations || []
    recommendationProfile.value = data.profile || {}
    recommendationNotes.value = data.notes || []
    recommendationAi.value = data.ai_assistance || null
    selectedRecommendationKeys.value = recommendations.value.filter((item) => item.default_selected).map((item) => item.key)
  } catch (e: any) {
    ElMessage.error(e?.message || '获取推荐失败')
    recommendVisible.value = false
  }
  recommendLoading.value = false
}

const applyRecommendations = () => {
  const selected = recommendations.value.filter((item) => selectedRecommendationKeys.value.includes(item.key))
  transforms.value = selected.map((item) => JSON.parse(JSON.stringify(item.transform)))
  timeColumn.value = recommendationProfile.value?.time_columns?.[0] || null
  templateNotes.value = []
}

const previewRecommendations = async () => {
  if (!selectedRecommendationKeys.value.length) { ElMessage.warning('请至少选择一个推荐特征'); return }
  applyRecommendations()
  await preview()
  recommendationPreviewed.value = Boolean(previewData.value)
  if (recommendationPreviewed.value) ElMessage.success('已预览新增字段；确认无误后即可生成新数据集')
}

const runRecommendations = async () => {
  if (!recommendationPreviewed.value) { ElMessage.warning('请先预览新增字段，再确认生成数据集'); return }
  applyRecommendations()
  runResult.value = null
  await run()
  if (runResult.value) recommendVisible.value = false
}

const validationErrors = computed(() => {
  const errors: string[] = []
  if (datasetId.value == null) errors.push('请先选择数据集')
  if (datasetId.value != null && !columns.value.length) errors.push('当前数据集没有可读取的列信息')
  if (!transforms.value.length) errors.push('请至少添加一个特征转换')
  if (timeColumn.value && !columns.value.includes(timeColumn.value)) errors.push('选择的时间列不存在于当前数据集')

  const available = new Set(columns.value)
  const availableNumeric = new Set(numericColumns.value)
  const availableTime = new Set(timeColumns.value)
  const names = new Set(columns.value)
  transforms.value.forEach((transform, index) => {
    const position = `第 ${index + 1} 个转换`
    const name = String(transform.name || '').trim()
    if (!name) errors.push(`${position}的新列名不能为空`)
    else if (names.has(name)) errors.push(`${position}的新列名“${name}”与已有列或其他新列重复`)
    if (transform.fill_value != null && !Number.isFinite(Number(transform.fill_value))) errors.push(`${position}的空值填充必须是有效数字`)

    if (transform.type === 'expression') {
      const expression = String(transform.expression || '').trim()
      if (!expression) errors.push(`${position}的表达式不能为空`)
      else {
        const references = Array.from(expression.matchAll(/\{([^{}]+)\}/g)).map((match) => match[1].trim())
        if (!references.length) errors.push(`${position}的表达式至少需要引用一个 {数值列}`)
        references.forEach((column) => {
          if (!available.has(column)) errors.push(`${position}引用了不存在或尚未生成的列“${column}”`)
          else if (!availableNumeric.has(column)) errors.push(`${position}引用的“${column}”不是数值列`)
        })
        const arithmeticOnly = expression.replace(/\{[^{}]+\}/g, '1')
        if (!/^[\d+\-*/%().\s]+$/.test(arithmeticOnly)) errors.push(`${position}含有不支持的字符，只允许数字、括号和 + - * / % **`)
      }
      if (transform.noise != null && Number(transform.noise) < 0) errors.push(`${position}的噪声 σ 不能小于 0`)
    } else if (transform.type === 'rolling') {
      if (!transform.source_column || !availableNumeric.has(transform.source_column)) errors.push(`${position}的源列必须是数值列或前面生成的特征`)
      if (!Number.isInteger(Number(transform.window)) || Number(transform.window) < 1) errors.push(`${position}的窗口必须是大于 0 的整数`)
      if (!aggs.includes(transform.agg)) errors.push(`${position}的聚合方式无效`)
    } else if (transform.type === 'lag') {
      if (!transform.source_column || !availableNumeric.has(transform.source_column)) errors.push(`${position}的源列必须是数值列或前面生成的特征`)
      if (!Number.isInteger(Number(transform.periods)) || Number(transform.periods) < 1) errors.push(`${position}的滞后行数必须是大于 0 的整数`)
    } else if (transform.type === 'time_component') {
      if (!transform.source_column || !availableTime.has(transform.source_column)) errors.push(`${position}必须选择可用的时间列`)
      if (!['hour', 'dayofweek', 'day', 'month'].includes(transform.component)) errors.push(`${position}的时间拆分项无效`)
    } else if (transform.type === 'normalize') {
      if (!transform.source_column || !availableNumeric.has(transform.source_column)) errors.push(`${position}的源列必须是数值列或前面生成的特征`)
      if (!['minmax', 'zscore'].includes(transform.method)) errors.push(`${position}的归一化方式无效`)
    } else {
      errors.push(`${position}的转换类型无效`)
    }

    if (name) {
      names.add(name)
      available.add(name)
      availableNumeric.add(name)
    }
  })
  return errors
})
const canSubmit = computed(() => validationErrors.value.length === 0)

const ensureValid = () => {
  if (canSubmit.value) return true
  ElMessage.warning(validationErrors.value[0] || '请检查特征配置')
  return false
}

const cleanTransforms = () => transforms.value.map((t) => {
  const o: any = { type: t.type, name: String(t.name || '').trim() }
  if (t.fill_value != null) o.fill_value = t.fill_value
  if (t.type === 'expression') {
    o.expression = t.expression
    if (t.noise != null && t.noise !== 0) o.noise = t.noise
  } else if (t.type === 'rolling') {
    o.source_column = t.source_column
    o.window = t.window
    o.agg = t.agg
  } else if (t.type === 'lag') {
    o.source_column = t.source_column
    o.periods = t.periods
  } else if (t.type === 'time_component') {
    o.source_column = t.source_column
    o.component = t.component
  } else if (t.type === 'normalize') {
    o.source_column = t.source_column
    o.method = t.method
  }
  return o
})

const preview = async () => {
  if (!ensureValid()) return
  try {
    previewData.value = await post('/feature-engineering/preview', {
      source_dataset_id: datasetId.value,
      time_column: timeColumn.value || undefined,
      rows: 5,
      transformations: cleanTransforms()
    })
    runResult.value = null
  } catch (e: any) {
    previewData.value = null
    ElMessage.error(e?.message || '预览失败，请检查表达式/列名')
  }
}

const run = async () => {
  if (!ensureValid()) return
  running.value = true
  try {
    runResult.value = await post('/feature-engineering/run', {
      source_dataset_id: datasetId.value,
      name: (datasets.value.find((d) => d.id === datasetId.value)?.name || '数据集') + '(特征工程)',
      time_column: timeColumn.value || undefined,
      transformations: cleanTransforms()
    })
    previewData.value = null
    loadDatasets()
    ElMessage.success('特征工程执行成功')
    if (runResult.value?.warnings?.length) ElMessage.warning(runResult.value.warnings[0])
  } catch (e: any) {
    ElMessage.error(e?.message || '执行失败')
  }
  running.value = false
}

onMounted(() => { loadDatasets() })
</script>

<style scoped>
.feature-page {
  padding: 0;
}
.step-card {
  margin-bottom: 16px;
}
.step-title {
  font-weight: 600;
  font-size: 14px;
}
.card-header-flex {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-header-actions {
  display: flex;
  gap: 8px;
}
.form-label {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.column-chips {
  max-height: 140px;
  overflow: auto;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 6px;
  background: var(--el-bg-color);
}
.chip-btn {
  margin: 2px;
}
.empty-inline,
.column-summary {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.column-summary {
  margin-top: 6px;
}
.transform-item {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 10px;
  background: var(--el-bg-color);
}
.expression-row {
  margin-top: 10px;
}
.expression-row .form-label {
  margin-bottom: 4px;
}
.template-notes {
  margin-top: 8px;
  color: #fbbf24;
  font-size: 11px;
}
.run-success {
  margin-left: 14px;
  color: #34d399;
  font-size: 13px;
}
.preview-shape {
  font-weight: 400;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.validation-alert,
.quality-warning,
.quality-table {
  margin-bottom: 12px;
}
.preview-table-wrap {
  max-height: 360px;
  overflow: auto;
}
.source-preview-meta {
  margin-bottom: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.empty-hint {
  padding: 20px 0;
  display: flex;
  justify-content: center;
}
.recommend-profile {
  margin: 2px 0 16px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.7;
}
.recommend-list {
  display: block;
  max-height: 500px;
  overflow: auto;
  padding: 2px 4px 2px 0;
}
.recommend-item {
  margin-bottom: 14px;
}
.recommend-item :deep(.el-card__body) {
  padding: 14px 16px;
}
.recommend-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  line-height: 1.55;
}
.recommend-head :deep(.el-checkbox) {
  height: auto;
  white-space: normal;
  line-height: 1.55;
}
.recommend-column {
  max-width: 45%;
  font-size: 12px;
  line-height: 1.55;
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
.recommend-desc {
  margin: 10px 0 0 24px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}
.ai-assistance {
  margin-bottom: 16px;
}
.ai-assistance :deep(.el-alert__title),
.ai-assistance :deep(.el-alert__description),
.ai-risk :deep(.el-alert__title) {
  line-height: 1.7;
}
.recommend-ai-note {
  margin: 10px 0 0 24px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--el-color-primary);
}
.ai-risk {
  margin-top: 10px;
}
.recommend-params {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 0 0 24px;
  font-size: 12px;
  line-height: 1.5;
}
.ml-8 {
  margin-left: 8px;
}
</style>
