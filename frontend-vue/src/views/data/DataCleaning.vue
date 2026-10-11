<template>
  <div class="cleaning-page">
    <PageHeader title="数据清洗" desc="定义清洗规则并绑定治理检查；只有检查通过才生成可用结果">
      <template #actions>
        <el-button v-if="userStore.hasPermission('datasets:process')" @click="downloadTemplate">
          <el-icon><Download /></el-icon>下载代码模板
        </el-button>
        <el-button v-if="userStore.hasPermission('datasets:process')" @click="openUploadDialog">
          <el-icon><Upload /></el-icon>上传规则代码
        </el-button>
        <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" @click="openCreateDialog">
          <el-icon><Plus /></el-icon>界面新建规则
        </el-button>
        <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" plain @click="openRecommendDialog">🤖 AI 推荐清洗方案</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="清洗不会覆盖原始数据。系统会在清洗后立即执行所选治理检查；通过才生成结果，未通过只保存报告。"
      class="notice"
    />

    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :sm="6"><StatCard title="清洗规则" :value="rules.length" icon="Document" /></el-col>
      <el-col :xs="12" :sm="6"><StatCard title="界面规则" :value="guidedCount" icon="Operation" /></el-col>
      <el-col :xs="12" :sm="6"><StatCard title="代码规则" :value="codeCount" icon="Cpu" /></el-col>
      <el-col :xs="12" :sm="6"><StatCard title="成功执行" :value="successRunCount" icon="CircleCheck" /></el-col>
    </el-row>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="清洗规则" name="rules">
        <el-card shadow="never">
          <el-table :data="rules" v-loading="loadingRules" stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="name" label="规则名称" min-width="180" show-overflow-tooltip />
            <el-table-column label="构建方式" width="110">
              <template #default="{ row }">
                <el-tag :type="row.source_mode === 'code' ? 'warning' : 'primary'">
                  {{ row.source_mode === 'code' ? '代码上传' : '界面编排' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="清洗步骤" width="100" align="center">
              <template #default="{ row }">{{ row.operation_count }}</template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-switch :disabled="!userStore.hasPermission('datasets:process')" :model-value="row.is_active" @change="toggleRule(row, $event)" />
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="165">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="300" fixed="right">
              <template #default="{ row }">
                <el-button v-if="userStore.hasPermission('datasets:process')" text type="primary" @click="openRunDialog(row)">预览/执行</el-button>
                <el-button v-if="userStore.hasPermission('datasets:process') && row.source_mode === 'guided'" text @click="editRule(row)">编辑</el-button>
                <el-button v-else text @click="viewCode(row)">查看代码</el-button>
                <el-popconfirm v-if="userStore.hasPermission('datasets:process')" title="删除规则？历史执行记录会保留。" @confirm="removeRule(row)">
                  <template #reference><el-button text type="danger">删除</el-button></template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!loadingRules && !rules.length" description="暂无清洗规则" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="执行记录" name="runs">
        <el-card shadow="never">
          <el-table :data="runs" v-loading="loadingRuns" stripe>
            <el-table-column prop="id" label="运行ID" width="135" show-overflow-tooltip />
            <el-table-column prop="rule_name" label="规则" min-width="150" />
            <el-table-column label="源数据集" min-width="170">
              <template #default="{ row }">#{{ row.source_dataset_id }} {{ row.source_dataset_name || '' }}</template>
            </el-table-column>
            <el-table-column label="结果数据集" min-width="180">
              <template #default="{ row }">
                <span v-if="row.output_dataset_id">#{{ row.output_dataset_id }} {{ row.output_dataset_name || '' }}</span>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="行数变化" width="130">
              <template #default="{ row }">
                <span v-if="row.summary">{{ row.summary.rows_before }} → {{ row.summary.rows_after }}</span>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="runStatusType(row.status)">{{ runStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="开始时间" width="165">
              <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
            </el-table-column>
            <el-table-column prop="error" label="错误" min-width="180" show-overflow-tooltip />
          </el-table>
          <el-empty v-if="!loadingRuns && !runs.length" description="暂无执行记录" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="showRuleDialog"
      :title="editingRuleId ? '编辑界面规则' : '界面新建清洗规则'"
      width="860px"
      destroy-on-close
    >
      <el-form label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="规则名称" required>
              <el-input v-model="ruleForm.name" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="参考数据集">
              <el-select
                v-model="designDatasetId"
                filterable clearable
                placeholder="选择后可直接选择字段"
                style="width:100%"
                @change="loadDesignColumns"
              >
                <el-option v-for="ds in datasets" :key="ds.id" :label="'#' + ds.id + ' ' + ds.name" :value="ds.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="说明">
          <el-input v-model="ruleForm.description" type="textarea" :rows="2" maxlength="2000" />
        </el-form-item>
      </el-form>

      <div class="operation-header">
        <div>
          <b>清洗步骤</b>
          <span class="hint">按从上到下的顺序执行</span>
        </div>
        <el-button type="primary" plain size="small" @click="addOperation"><el-icon><Plus /></el-icon>添加步骤</el-button>
      </div>

      <div v-for="(op, index) in ruleForm.operations" :key="op.key" class="operation-card">
        <div class="operation-title">
          <span class="step-number">{{ index + 1 }}</span>
          <el-input v-model="op.name" placeholder="步骤名称" style="width:220px" />
          <el-select v-model="op.type" style="width:190px" @change="resetOperation(op)">
            <el-option v-for="item in operationTypes" :key="item.type" :label="item.name" :value="item.type" />
          </el-select>
          <span class="grow" />
          <el-switch v-model="op.enabled" active-text="启用" />
          <el-button text :disabled="index === 0" @click="moveOperation(index, -1)">上移</el-button>
          <el-button text :disabled="index === ruleForm.operations.length - 1" @click="moveOperation(index, 1)">下移</el-button>
          <el-button text type="danger" @click="removeOperation(index)">删除</el-button>
        </div>

        <div class="operation-config">
          <template v-if="op.type === 'drop_empty_columns'">
            <el-alert type="info" :closable="false" show-icon title="自动识别并删除所有值都为空的字段，无需额外配置。" />
          </template>

          <template v-else-if="op.type === 'drop_sparse_rows'">
            <div class="config-item">
              <label>最小有效字段比例</label>
              <el-input-number v-model="op.config.min_non_null_ratio" :min="0.01" :max="1" :step="0.05" :precision="2" style="width:100%" />
            </div>
          </template>

          <template v-else-if="op.type === 'drop_missing'">
            <FieldSelect v-model="op.config.columns" :columns="availableColumns" multiple label="检查字段" />
            <ConfigSelect v-model="op.config.how" label="删除条件" :options="[
              { label: '任一字段为空', value: 'any' },
              { label: '全部字段为空', value: 'all' }
            ]" />
          </template>

          <template v-else-if="op.type === 'fill_missing'">
            <FieldSelect v-model="op.config.columns" :columns="availableColumns" multiple label="填充字段" />
            <ConfigSelect v-model="op.config.method" label="填充方式" :options="fillMethods" />
            <div v-if="op.config.method === 'constant'" class="config-item">
              <label>固定值</label><el-input v-model="op.config.value" placeholder="支持文本、数字、true/false" />
            </div>
          </template>

          <template v-else-if="op.type === 'drop_duplicates'">
            <FieldSelect v-model="op.config.columns" :columns="availableColumns" multiple label="判重字段" hint="留空表示整行判重" />
            <ConfigSelect v-model="op.config.keep" label="保留记录" :options="[
              { label: '保留第一条', value: 'first' },
              { label: '保留最后一条', value: 'last' },
              { label: '重复项全部删除', value: 'none' }
            ]" />
          </template>

          <template v-else-if="op.type === 'filter_rows'">
            <FieldSelect v-model="op.config.column" :columns="availableColumns" label="条件字段" />
            <ConfigSelect v-model="op.config.operator" label="运算符" :options="operators" />
            <div v-if="!['is_null','not_null'].includes(op.config.operator)" class="config-item wide">
              <label>条件值</label>
              <el-input v-model="op.config.value" :placeholder="['in','not_in'].includes(op.config.operator) ? '多个值用英文逗号分隔' : '输入比较值'" />
            </div>
            <ConfigSelect v-model="op.config.keep_matching" label="处理方式" :options="[
              { label: '保留符合条件的行', value: true },
              { label: '删除符合条件的行', value: false }
            ]" />
          </template>

          <template v-else-if="op.type === 'replace_values'">
            <FieldSelect v-model="op.config.column" :columns="availableColumns" label="目标字段" />
            <div class="config-item mapping">
              <label>替换映射（JSON）</label>
              <el-input v-model="op.config.mapping_text" type="textarea" :rows="3" placeholder='{"旧值": "新值", "-999": null}' />
            </div>
          </template>

          <template v-else-if="op.type === 'clip'">
            <FieldSelect v-model="op.config.column" :columns="availableColumns" label="数值字段" />
            <div class="config-item"><label>最小值</label><el-input-number v-model="op.config.min" style="width:100%" /></div>
            <div class="config-item"><label>最大值</label><el-input-number v-model="op.config.max" style="width:100%" /></div>
          </template>

          <template v-else-if="op.type === 'convert_type'">
            <FieldSelect v-model="op.config.column" :columns="availableColumns" label="目标字段" />
            <ConfigSelect v-model="op.config.dtype" label="目标类型" :options="dataTypes" />
            <ConfigSelect v-model="op.config.errors" label="错误处理" :options="[
              { label: '无法转换时设为空', value: 'coerce' },
              { label: '遇到错误停止', value: 'raise' },
              { label: '保留原值', value: 'ignore' }
            ]" />
          </template>

          <template v-else-if="op.type === 'trim'">
            <FieldSelect v-model="op.config.columns" :columns="availableColumns" multiple label="文本字段" />
            <ConfigSelect v-model="op.config.mode" label="清理位置" :options="[
              { label: '两侧空格', value: 'both' },
              { label: '左侧空格', value: 'left' },
              { label: '右侧空格', value: 'right' }
            ]" />
          </template>

          <template v-else-if="op.type === 'rename_columns'">
            <FieldSelect v-model="op.config.column" :columns="availableColumns" label="原字段" />
            <div class="config-item"><label>新字段名</label><el-input v-model="op.config.new_name" /></div>
          </template>

          <template v-else-if="op.type === 'drop_columns'">
            <FieldSelect v-model="op.config.columns" :columns="availableColumns" multiple label="删除字段" />
          </template>
        </div>
      </div>
      <el-empty v-if="!ruleForm.operations.length" description="请添加至少一个清洗步骤" :image-size="60" />

      <template #footer>
        <el-button @click="showRuleDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingRule" @click="saveRule">保存规则</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRecommendDialog" title="推荐清洗方案" width="820px" destroy-on-close>
      <el-alert type="info" :closable="false" show-icon title="系统只分析数据样本并提出保守建议；不会直接修改数据。请勾选需要的步骤，再进入预览确认。" style="margin-bottom:16px" />
      <el-form inline>
        <el-form-item label="数据集" required>
          <el-select v-model="recommendDatasetId" filterable placeholder="选择需要分析的数据集" style="width:360px" @change="recommendResult = null">
            <el-option v-for="ds in runnableDatasets" :key="ds.id" :label="'#' + ds.id + ' ' + ds.name" :value="ds.id" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" :loading="recommending" @click="loadRecommendations">分析并生成建议</el-button></el-form-item>
      </el-form>
      <template v-if="recommendResult">
        <el-row :gutter="12" class="preview-summary">
          <el-col :span="6"><el-statistic title="分析行数" :value="recommendResult.profile?.rows || 0" /></el-col>
          <el-col :span="6"><el-statistic title="字段数" :value="recommendResult.profile?.columns || 0" /></el-col>
          <el-col :span="6"><el-statistic title="缺失单元格" :value="recommendResult.profile?.missing_cells || 0" /></el-col>
          <el-col :span="6"><el-statistic title="重复记录" :value="recommendResult.profile?.duplicate_rows || 0" /></el-col>
        </el-row>
        <el-card v-if="recommendResult.ai_assistance" shadow="never" class="ai-recommendation-card">
          <template #header><div class="ai-recommendation-header"><b>大模型辅助说明</b><el-tag :type="recommendResult.ai_assistance.available ? 'success' : 'info'">{{ recommendResult.ai_assistance.provider || '本地规则' }}</el-tag></div></template>
          <template v-if="recommendResult.ai_assistance.available">
            <p v-if="recommendResult.ai_assistance.summary" class="ai-recommendation-summary">{{ recommendResult.ai_assistance.summary }}</p>
            <el-table v-if="recommendResult.ai_assistance.priorities?.length" :data="recommendResult.ai_assistance.priorities" size="small" border>
              <el-table-column label="建议" min-width="180"><template #default="{row}">{{ recommendationTitle(row.id) }}</template></el-table-column>
              <el-table-column label="优先级" width="100"><template #default="{row}"><el-tag :type="priorityTagType(row.priority)">{{ priorityLabel(row.priority) }}</el-tag></template></el-table-column>
              <el-table-column prop="reason" label="辅助说明" min-width="340" />
            </el-table>
            <el-alert v-for="(message, index) in recommendResult.ai_assistance.manual_attention || []" :key="`ai-${index}`" type="warning" :closable="false" show-icon :title="message" class="recommendation-warning" />
          </template>
          <el-alert v-else type="info" :closable="false" show-icon :title="recommendResult.ai_assistance.warning || '当前使用本地规则推荐。'" />
        </el-card>
        <el-checkbox-group v-model="selectedRecommendationIds" class="recommendation-list">
          <el-card v-for="item in recommendResult.recommendations || []" :key="item.id" shadow="never" class="recommendation-card">
            <el-checkbox :label="item.id"><b>{{ item.title }}</b></el-checkbox>
            <div class="recommendation-reason">{{ item.reason }}</div>
          </el-card>
        </el-checkbox-group>
        <el-alert v-if="!(recommendResult.recommendations || []).length" type="success" :closable="false" show-icon title="当前样本没有发现可安全自动处理的问题" />
        <el-alert v-for="(message, index) in recommendResult.manual_attention || []" :key="index" type="warning" :closable="false" show-icon :title="message" class="recommendation-warning" />
      </template>
      <el-empty v-else description="选择数据集后生成推荐方案" :image-size="70" />
      <template #footer>
        <el-button @click="showRecommendDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!recommendResult || !selectedRecommendationIds.length" :loading="applyingRecommendation" @click="applyRecommendations">应用所选方案并预览</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showUploadDialog" title="上传代码规则" width="600px" destroy-on-close>
      <el-alert
        type="warning" :closable="false" show-icon class="dialog-alert"
        title="平台只静态解析 CLEANING_RULES，不执行 import、函数、循环或任意 Python 代码。"
      />
      <el-form label-width="100px">
        <el-form-item label="规则名称" required><el-input v-model="uploadForm.name" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="uploadForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="规则文件" required>
          <el-upload
            :auto-upload="false" :limit="1" accept=".py,.json"
            :on-change="onCodeFileChange" :on-remove="onCodeFileRemove"
          >
            <el-button><el-icon><Upload /></el-icon>选择 .py 或 .json 文件</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="uploadRule">解析并保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRunDialog" :title="'预览并执行：' + (currentRule?.name || '')" width="1000px" destroy-on-close>
      <el-form inline>
        <el-form-item label="数据集" required>
          <el-select v-model="runDatasetId" filterable :filter-method="filterRunDatasets" placeholder="按ID或名称搜索" style="width:320px" @change="previewResult = null">
            <el-option v-for="ds in displayedRunDatasets" :key="ds.id" :label="'#' + ds.id + ' ' + ds.name" :value="ds.id" />
            <template #footer>
              <div class="select-pagination" @click.stop>
                <span>{{ filteredRunDatasets.length }} 个数据集</span>
                <el-pagination small background layout="prev, pager, next" :page-size="dropdownPageSize" :total="filteredRunDatasets.length" :current-page="runDatasetPage" @current-change="runDatasetPage = $event" />
              </div>
            </template>
          </el-select>
        </el-form-item>
        <el-form-item label="结果名称">
          <el-input v-model="outputName" placeholder="默认：源数据集_清洗结果" style="width:260px" />
        </el-form-item>
        <el-form-item label="验收标准" required>
          <el-select v-model="runGovernanceProfile" style="width:280px" @change="previewResult = null">
            <el-option v-for="profile in governanceProfiles" :key="profile.id" :label="profile.name" :value="profile.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="附加验收项">
          <el-select
            v-model="runQualityRuleIds" multiple filterable collapse-tags clearable
            :filter-method="filterRunQualityRules"
            placeholder="可选质量规则" style="width:320px" @change="previewResult = null"
          >
            <el-option v-for="rule in displayedRunQualityRules" :key="rule.id" :label="'#' + rule.id + ' ' + rule.name + '（' + rule.field + '）'" :value="rule.id" />
            <template #footer>
              <div class="select-pagination" @click.stop>
                <span>{{ filteredRunQualityRules.length }} 条规则</span>
                <el-pagination small background layout="prev, pager, next" :page-size="dropdownPageSize" :total="filteredRunQualityRules.length" :current-page="runQualityRulePage" @current-change="runQualityRulePage = $event" />
              </div>
            </template>
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" plain :loading="previewing" @click="previewRule">预览前500行</el-button>
        </el-form-item>
      </el-form>

      <template v-if="previewResult">
        <el-alert
          v-if="executionResult"
          :type="executionResult.status === 'success' ? 'success' : 'warning'"
          :title="executionResult.status === 'success' ? `全量清洗完成，已生成数据集 #${executionResult.output_dataset?.id}` : '全量清洗完成，但验收未通过，未生成结果数据集'"
          :closable="false" show-icon class="execution-result-alert"
        />
        <el-row :gutter="12" class="preview-summary">
          <el-col :span="4"><el-statistic title="清洗前行数" :value="previewResult.effect_summary?.rows_before ?? previewResult.summary.rows_before" /></el-col>
          <el-col :span="4"><el-statistic title="清洗后行数" :value="previewResult.effect_summary?.rows_after ?? previewResult.summary.rows_after" /></el-col>
          <el-col :span="4"><el-statistic title="删除行数" :value="previewResult.effect_summary?.rows_removed ?? previewResult.summary.rows_removed" /></el-col>
          <el-col :span="4"><el-statistic title="清洗前缺失" :value="previewResult.effect_summary?.missing_before ?? 0" /></el-col>
          <el-col :span="4"><el-statistic title="清洗后缺失" :value="previewResult.effect_summary?.missing_after ?? 0" /></el-col>
          <el-col :span="4"><el-statistic title="清洗后重复行" :value="previewResult.effect_summary?.duplicate_rows_after ?? 0" /></el-col>
        </el-row>
        <el-tabs>
          <el-tab-pane label="清洗后预览">
            <el-table :data="previewResult.after.rows" max-height="330" border size="small">
              <el-table-column v-for="column in previewResult.after.columns" :key="column" :prop="column" :label="column" min-width="130" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="清洗前预览">
            <el-table :data="previewResult.before.rows" max-height="330" border size="small">
              <el-table-column v-for="column in previewResult.before.columns" :key="column" :prop="column" :label="column" min-width="130" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="步骤摘要">
            <el-table :data="previewResult.summary.operations" border size="small">
              <el-table-column prop="name" label="步骤" />
              <el-table-column prop="rows_removed" label="删除行" width="100" />
              <el-table-column prop="columns_removed" label="删除列" width="100" />
              <el-table-column prop="changed_cells" label="修改单元格" width="120" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="清洗效果摘要">
            <el-alert
              :type="previewResult.governance_report?.passed ? 'success' : 'error'"
              :closable="false" show-icon
              :title="previewResult.governance_report?.passed ? '清洗结果通过验收' : '清洗结果未通过验收'"
              :description="'得分 ' + (previewResult.governance_report?.score ?? '—') + '；通过 ' + (previewResult.governance_report?.checks_passed ?? 0) + '/' + (previewResult.governance_report?.checks_total ?? 0) + ' 项'"
              style="margin-bottom:12px"
            />
            <el-table :data="previewResult.governance_report?.checks || []" border size="small" max-height="300">
              <el-table-column label="结果" width="75"><template #default="{ row }"><el-tag size="small" :type="row.passed ? 'success' : 'danger'">{{ row.passed ? '通过' : '未通过' }}</el-tag></template></el-table-column>
              <el-table-column prop="name" label="检查项" min-width="140" />
              <el-table-column prop="expected" label="要求" min-width="180" />
              <el-table-column prop="actual" label="实际" min-width="170" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
      <el-empty v-else description="选择数据集并先执行预览，确认规则影响后再进行全量清洗" :image-size="70" />
      <template #footer>
        <el-button @click="showRunDialog = false">关闭</el-button>
        <el-button type="success" :disabled="!previewResult || !!executionResult" :loading="executing" @click="executeRule">执行全量清洗并验收</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showCodeDialog" title="代码规则" width="760px">
      <el-alert type="info" :closable="false" show-icon title="该文件仅作为声明式规则解析，平台不会执行其中的 Python 代码。" />
      <pre class="code-view">{{ selectedCode }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Plus, Upload } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { useUserStore } from '@/stores/user'
import StatCard from '@/components/StatCard.vue'
import { del, downloadFile, get, post, postForm, put } from '@/api/legacy'

type Option = { label: string; value: any }
type RuleOperation = { key: number; type: string; name: string; enabled: boolean; config: Record<string, any> }

const FieldSelect = defineComponent({
  props: {
    modelValue: { type: [String, Array], default: '' },
    columns: { type: Array, default: () => [] },
    label: { type: String, default: '字段' },
    hint: { type: String, default: '' },
    multiple: { type: Boolean, default: false }
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => h('div', { class: 'config-item' }, [
      h('label', props.hint ? props.label + '（' + props.hint + '）' : props.label),
      h(resolveComponent('el-select') as any, {
        modelValue: props.modelValue,
        'onUpdate:modelValue': (value: any) => emit('update:modelValue', value),
        multiple: props.multiple,
        filterable: true,
        allowCreate: true,
        defaultFirstOption: true,
        collapseTags: true,
        placeholder: props.multiple ? '选择一个或多个字段' : '选择字段',
        style: 'width:100%'
      }, () => (props.columns as string[]).map(column =>
        h(resolveComponent('el-option') as any, { key: column, label: column, value: column })
      ))
    ])
  }
})

const ConfigSelect = defineComponent({
  props: {
    modelValue: { default: '' },
    label: { type: String, default: '' },
    options: { type: Array, default: () => [] }
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => h('div', { class: 'config-item' }, [
      h('label', props.label),
      h(resolveComponent('el-select') as any, {
        modelValue: props.modelValue,
        'onUpdate:modelValue': (value: any) => emit('update:modelValue', value),
        style: 'width:100%'
      }, () => (props.options as Option[]).map(option =>
        h(resolveComponent('el-option') as any, { key: String(option.value), label: option.label, value: option.value })
      ))
    ])
  }
})

import { resolveComponent } from 'vue'

const userStore = useUserStore()
const activeTab = ref('rules')
const rules = ref<any[]>([])
const runs = ref<any[]>([])
const datasets = ref<any[]>([])
const runnableDatasets = computed(() => datasets.value.filter(dataset =>
  ['raw', 'engineered', 'labeled'].includes(dataset.stage)
))
const dropdownPageSize = 8
const runDatasetQuery = ref('')
const runDatasetPage = ref(1)
const runQualityRuleQuery = ref('')
const runQualityRulePage = ref(1)
const filteredRunDatasets = computed(() => {
  const query = runDatasetQuery.value.trim().toLowerCase()
  if (!query) return runnableDatasets.value
  return runnableDatasets.value.filter(dataset =>
    String(dataset.id).includes(query) || String(dataset.name || '').toLowerCase().includes(query)
  )
})
const displayedRunDatasets = computed(() => {
  const start = (runDatasetPage.value - 1) * dropdownPageSize
  const pageItems = filteredRunDatasets.value.slice(start, start + dropdownPageSize)
  const selected = runnableDatasets.value.find(dataset => dataset.id === runDatasetId.value)
  return selected && !pageItems.some(dataset => dataset.id === selected.id) ? [selected, ...pageItems] : pageItems
})
const operationTypes = ref<any[]>([])
const operators = ref<Option[]>([])
const governanceProfiles = ref<any[]>([])
const governanceQualityRules = ref<any[]>([])
const filteredRunQualityRules = computed(() => {
  const query = runQualityRuleQuery.value.trim().toLowerCase()
  if (!query) return governanceQualityRules.value
  return governanceQualityRules.value.filter(rule =>
    String(rule.id).includes(query)
    || String(rule.name || '').toLowerCase().includes(query)
    || String(rule.field || '').toLowerCase().includes(query)
  )
})
const displayedRunQualityRules = computed(() => {
  const start = (runQualityRulePage.value - 1) * dropdownPageSize
  const pageItems = filteredRunQualityRules.value.slice(start, start + dropdownPageSize)
  const selectedItems = governanceQualityRules.value.filter(rule => runQualityRuleIds.value.includes(rule.id))
  return [...selectedItems, ...pageItems].filter((rule, index, items) => items.findIndex(candidate => candidate.id === rule.id) === index)
})
function filterRunDatasets(query: string) {
  runDatasetQuery.value = query
  runDatasetPage.value = 1
}
function filterRunQualityRules(query: string) {
  runQualityRuleQuery.value = query
  runQualityRulePage.value = 1
}
const loadingRules = ref(false)
const loadingRuns = ref(false)

const guidedCount = computed(() => rules.value.filter(rule => rule.source_mode === 'guided').length)
const codeCount = computed(() => rules.value.filter(rule => rule.source_mode === 'code').length)
const successRunCount = computed(() => runs.value.filter(run => run.status === 'success').length)

const fillMethods: Option[] = [
  { label: '固定值', value: 'constant' }, { label: '均值', value: 'mean' },
  { label: '中位数', value: 'median' }, { label: '众数', value: 'mode' },
  { label: '前一个值', value: 'forward' }, { label: '后一个值', value: 'backward' }
]
const dataTypes: Option[] = [
  { label: '数值', value: 'numeric' }, { label: '整数', value: 'integer' },
  { label: '文本', value: 'string' }, { label: '日期时间', value: 'datetime' },
  { label: '布尔值', value: 'boolean' }
]

async function loadRules() {
  loadingRules.value = true
  try { rules.value = (await get<any>('/data-cleaning/rules')).items || [] }
  finally { loadingRules.value = false }
}
async function loadRuns() {
  loadingRuns.value = true
  try { runs.value = (await get<any>('/data-cleaning/runs')).items || [] }
  finally { loadingRuns.value = false }
}
async function loadDatasets() {
  datasets.value = (await get<any>('/datasets')).items || []
}
async function loadTypes() {
  const result = await get<any>('/data-cleaning/operation-types')
  operationTypes.value = result.items || []
  operators.value = result.operators || []
}
async function loadGovernanceOptions() {
  const [profiles, rulesResult] = await Promise.all([
    get<any>('/dataset-governance/profiles'),
    get<any>('/dataset-governance/quality-rules'),
  ])
  governanceProfiles.value = profiles.items || []
  governanceQualityRules.value = rulesResult.items || []
}

let operationKey = 0
function defaultsFor(type: string): Record<string, any> {
  const defaults: Record<string, Record<string, any>> = {
    drop_empty_columns: {},
    drop_sparse_rows: { min_non_null_ratio: 0.5 },
    drop_missing: { columns: [], how: 'any' },
    fill_missing: { columns: [], method: 'constant', value: '' },
    drop_duplicates: { columns: [], keep: 'first' },
    filter_rows: { column: '', operator: 'eq', value: '', keep_matching: true },
    replace_values: { column: '', mapping_text: '{}' },
    clip: { column: '', min: undefined, max: undefined },
    convert_type: { column: '', dtype: 'numeric', errors: 'coerce' },
    trim: { columns: [], mode: 'both' },
    rename_columns: { column: '', new_name: '' },
    drop_columns: { columns: [] }
  }
  return { ...(defaults[type] || {}) }
}
function operationLabel(type: string): string {
  return operationTypes.value.find(item => item.type === type)?.name || type
}
function newOperation(type = 'drop_missing'): RuleOperation {
  return { key: ++operationKey, type, name: operationLabel(type), enabled: true, config: defaultsFor(type) }
}

const showRuleDialog = ref(false)
const showRecommendDialog = ref(false)
const recommendDatasetId = ref<number | null>(null)
const recommendResult = ref<any>(null)
const selectedRecommendationIds = ref<string[]>([])
const recommending = ref(false)
const applyingRecommendation = ref(false)
const savingRule = ref(false)
const editingRuleId = ref<number | null>(null)
const designDatasetId = ref<number | null>(null)
const availableColumns = ref<string[]>([])
const ruleForm = reactive({ name: '', description: '', operations: [] as RuleOperation[] })

function openRecommendDialog() {
  recommendDatasetId.value = null
  recommendResult.value = null
  selectedRecommendationIds.value = []
  showRecommendDialog.value = true
}
async function loadRecommendations() {
  if (!recommendDatasetId.value) { ElMessage.warning('请选择数据集'); return }
  recommending.value = true
  try {
    recommendResult.value = await post('/data-cleaning/recommend', { dataset_id: recommendDatasetId.value })
    selectedRecommendationIds.value = (recommendResult.value.recommendations || [])
      .filter((item: any) => item.selected !== false).map((item: any) => item.id)
  } catch (error: any) { recommendResult.value = null; ElMessage.error(error.message || '推荐方案生成失败') }
  finally { recommending.value = false }
}
function recommendationTitle(id: string) { return (recommendResult.value?.recommendations || []).find((item: any) => item.id === id)?.title || id }
function priorityLabel(priority: string) { return ({ high: '高', medium: '中', low: '低' } as Record<string, string>)[priority] || '中' }
function priorityTagType(priority: string) { return priority === 'high' ? 'danger' : priority === 'low' ? 'info' : 'warning' }

async function applyRecommendations() {
  if (!recommendDatasetId.value || !recommendResult.value) return
  const operations = (recommendResult.value.recommendations || [])
    .filter((item: any) => selectedRecommendationIds.value.includes(item.id))
    .map((item: any) => item.operation)
  if (!operations.length) { ElMessage.warning('请至少选择一个推荐步骤'); return }
  applyingRecommendation.value = true
  try {
    const dataset = runnableDatasets.value.find(item => item.id === recommendDatasetId.value)
    const result = await post<any>('/data-cleaning/rules', {
      name: `${dataset?.name || '数据集'}_推荐清洗`,
      description: `由系统基于 ${recommendResult.value.sample_size} 行样本生成；执行前请先预览确认。`,
      definition: { version: 1, operations },
    })
    await loadRules()
    const rule = await get<any>('/data-cleaning/rules/' + result.id)
    showRecommendDialog.value = false
    openRunDialog(rule, recommendDatasetId.value)
    ElMessage.success('已生成可编辑的清洗规则，请先预览确认')
  } catch (error: any) { ElMessage.error(error.message || '推荐方案应用失败') }
  finally { applyingRecommendation.value = false }
}

function openCreateDialog() {
  editingRuleId.value = null
  designDatasetId.value = null
  availableColumns.value = []
  ruleForm.name = ''
  ruleForm.description = ''
  ruleForm.operations = [newOperation()]
  showRuleDialog.value = true
}
function addOperation() { ruleForm.operations.push(newOperation()) }
function removeOperation(index: number) { ruleForm.operations.splice(index, 1) }
function moveOperation(index: number, direction: number) {
  const target = index + direction
  if (target < 0 || target >= ruleForm.operations.length) return
  const [item] = ruleForm.operations.splice(index, 1)
  ruleForm.operations.splice(target, 0, item)
}
function resetOperation(op: RuleOperation) {
  op.name = operationLabel(op.type)
  op.config = defaultsFor(op.type)
}
async function loadDesignColumns() {
  availableColumns.value = []
  if (!designDatasetId.value) return
  try {
    const result = await get<any>('/datasets/' + designDatasetId.value + '/rows', { offset: 0, limit: 1 })
    availableColumns.value = result.columns || []
  } catch (error: any) { ElMessage.error(error.message || '读取字段失败') }
}

function smartValue(value: any): any {
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (!trimmed) return ''
  try { return JSON.parse(trimmed) } catch { return value }
}
function buildDefinition() {
  return {
    version: 1,
    operations: ruleForm.operations.map(op => {
      const result: Record<string, any> = { type: op.type, name: op.name || operationLabel(op.type), enabled: op.enabled }
      Object.assign(result, op.config)
      if (op.type === 'fill_missing' && op.config.method === 'constant') result.value = smartValue(op.config.value)
      if (op.type === 'filter_rows') {
        result.value = ['in', 'not_in'].includes(op.config.operator)
          ? String(op.config.value || '').split(',').map((item: string) => smartValue(item.trim())).filter((item: any) => item !== '')
          : smartValue(op.config.value)
      }
      if (op.type === 'replace_values') {
        try { result.mapping = JSON.parse(op.config.mapping_text || '{}') }
        catch { throw new Error('替换值步骤的映射必须是合法 JSON') }
        delete result.mapping_text
      }
      return result
    })
  }
}
async function saveRule() {
  if (!ruleForm.name.trim()) { ElMessage.warning('请输入规则名称'); return }
  if (!ruleForm.operations.length) { ElMessage.warning('请添加至少一个清洗步骤'); return }
  savingRule.value = true
  try {
    const payload = { name: ruleForm.name.trim(), description: ruleForm.description || null, definition: buildDefinition() }
    if (editingRuleId.value) await put('/data-cleaning/rules/' + editingRuleId.value, payload)
    else await post('/data-cleaning/rules', payload)
    ElMessage.success(editingRuleId.value ? '规则更新成功' : '规则创建成功')
    showRuleDialog.value = false
    await loadRules()
  } catch (error: any) { ElMessage.error(error.message || '规则保存失败') }
  finally { savingRule.value = false }
}
function definitionToOperation(item: any): RuleOperation {
  const config = { ...item }
  delete config.type; delete config.name; delete config.enabled
  if (item.type === 'replace_values') {
    config.mapping_text = JSON.stringify(config.mapping || {}, null, 2)
    delete config.mapping
  }
  return { key: ++operationKey, type: item.type, name: item.name || operationLabel(item.type), enabled: item.enabled !== false, config: { ...defaultsFor(item.type), ...config } }
}
function editRule(rule: any) {
  editingRuleId.value = rule.id
  designDatasetId.value = null
  availableColumns.value = []
  ruleForm.name = rule.name
  ruleForm.description = rule.description || ''
  ruleForm.operations = (rule.definition?.operations || []).map(definitionToOperation)
  showRuleDialog.value = true
}

const showUploadDialog = ref(false)
const uploading = ref(false)
const uploadForm = reactive({ name: '', description: '', file: null as File | null })
function openUploadDialog() {
  uploadForm.name = ''; uploadForm.description = ''; uploadForm.file = null
  showUploadDialog.value = true
}
function onCodeFileChange(file: any) { uploadForm.file = file.raw }
function onCodeFileRemove() { uploadForm.file = null }
async function uploadRule() {
  if (!uploadForm.name.trim() || !uploadForm.file) { ElMessage.warning('请填写名称并选择规则文件'); return }
  uploading.value = true
  try {
    const form = new FormData()
    form.append('name', uploadForm.name.trim())
    form.append('description', uploadForm.description || '')
    form.append('file', uploadForm.file)
    const result = await postForm<any>('/data-cleaning/rules/upload', form)
    ElMessage.success(result.message + '，共 ' + result.operation_count + ' 个步骤')
    showUploadDialog.value = false
    await loadRules()
  } catch (error: any) { ElMessage.error(error.message || '上传失败') }
  finally { uploading.value = false }
}
async function downloadTemplate() {
  try { await downloadFile('/data-cleaning/template', 'foundation_cleaning_rule.py') }
  catch (error: any) { ElMessage.error(error.message || '模板下载失败') }
}

const showCodeDialog = ref(false)
const selectedCode = ref('')
async function viewCode(rule: any) {
  try {
    const detail = await get<any>('/data-cleaning/rules/' + rule.id)
    selectedCode.value = detail.code_text || ''
    showCodeDialog.value = true
  } catch (error: any) { ElMessage.error(error.message || '代码读取失败') }
}
async function toggleRule(rule: any, active: boolean) {
  try { await put('/data-cleaning/rules/' + rule.id, { is_active: active }); rule.is_active = active }
  catch (error: any) { ElMessage.error(error.message || '状态更新失败') }
}
async function removeRule(rule: any) {
  try { await del('/data-cleaning/rules/' + rule.id); ElMessage.success('规则已删除'); await loadRules() }
  catch (error: any) { ElMessage.error(error.message || '删除失败') }
}

const showRunDialog = ref(false)
const currentRule = ref<any>(null)
const runDatasetId = ref<number | null>(null)
const outputName = ref('')
const runGovernanceProfile = ref('basic')
const runQualityRuleIds = ref<number[]>([])
const previewResult = ref<any>(null)
const executionResult = ref<any>(null)
const previewing = ref(false)
const executing = ref(false)
function openRunDialog(rule: any, presetDatasetId: number | null = null) {
  currentRule.value = rule
  runDatasetId.value = presetDatasetId
  outputName.value = ''
  runGovernanceProfile.value = 'basic'
  runQualityRuleIds.value = []
  runDatasetQuery.value = ''
  runDatasetPage.value = 1
  runQualityRuleQuery.value = ''
  runQualityRulePage.value = 1
  previewResult.value = null
  executionResult.value = null
  showRunDialog.value = true
}
async function previewRule() {
  if (!runDatasetId.value || !currentRule.value) { ElMessage.warning('请选择数据集'); return }
  previewing.value = true
  try {
    previewResult.value = await post('/data-cleaning/preview', {
      dataset_id: runDatasetId.value,
      rule_id: currentRule.value.id,
      sample_size: 500,
      governance_profile: runGovernanceProfile.value,
      quality_rule_ids: runQualityRuleIds.value,
    })
  } catch (error: any) { previewResult.value = null; ElMessage.error(error.message || '预览失败') }
  finally { previewing.value = false }
}
async function executeRule() {
  if (!runDatasetId.value || !currentRule.value || !previewResult.value) return
  try {
    await ElMessageBox.confirm('将清洗完整数据并立即执行治理检查；检查未通过时只保存报告，不生成结果。是否继续？', '确认执行', { type: 'warning' })
  } catch { return }
  executing.value = true
  try {
    const result = await post<any>('/data-cleaning/execute', {
      dataset_id: runDatasetId.value,
      rule_id: currentRule.value.id,
      output_name: outputName.value || null,
      governance_profile: runGovernanceProfile.value,
      quality_rule_ids: runQualityRuleIds.value,
    })
    previewResult.value = {
      ...previewResult.value,
      summary: result.summary || previewResult.value.summary,
      effect_summary: result.effect_summary || result.summary?.effect_summary || previewResult.value.effect_summary,
      governance_report: result.report || previewResult.value.governance_report,
    }
    executionResult.value = result
    if (result.status === 'rejected') ElMessage.warning(result.message)
    else ElMessage.success(result.message + '，结果数据集 ID：' + result.output_dataset.id)
    await Promise.all([loadRuns(), loadDatasets()])
  } catch (error: any) { ElMessage.error(error.message || '执行失败') }
  finally { executing.value = false }
}

function formatTime(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function runStatusType(status: string) { return status === 'success' ? 'success' : status === 'failed' ? 'danger' : 'warning' }
function runStatusLabel(status: string) { return status === 'success' ? '成功' : status === 'rejected' ? '检查未通过' : status === 'failed' ? '失败' : '执行中' }

onMounted(async () => {
  try { await Promise.all([loadTypes(), loadRules(), loadRuns(), loadDatasets(), loadGovernanceOptions()]) }
  catch (error: any) { ElMessage.error(error.message || '数据清洗页面加载失败') }
})
</script>

<style scoped lang="scss">
.cleaning-page { padding: 20px; }
.notice { margin-bottom: 16px; }
.execution-result-alert { margin-bottom: 12px; }
.stats-row { margin-bottom: 18px; }
.operation-header { display:flex; justify-content:space-between; align-items:center; margin:18px 0 10px; }
.hint { margin-left:10px; color:var(--tx3); font-size:12px; }
.operation-card { border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:12px; background:var(--bg-card); }
.operation-title { display:flex; align-items:center; gap:10px; }
.step-number { width:26px; height:26px; border-radius:50%; background:var(--primary); color:#fff; display:inline-flex; align-items:center; justify-content:center; font-weight:700; }
.grow { flex:1; }
.operation-config { display:flex; flex-wrap:wrap; gap:14px; margin-top:14px; padding:12px; background:var(--bg-page); border-radius:6px; }
:deep(.config-item) { width:220px; }
:deep(.config-item.wide) { width:300px; }
:deep(.config-item.mapping) { width:500px; }
:deep(.config-item label) { display:block; font-size:12px; color:var(--tx2); margin-bottom:6px; }
.dialog-alert { margin-bottom:16px; }
.preview-summary { margin:12px 0 18px; }
.preview-summary :deep(.el-col) { text-align:center; border-right:1px solid var(--border); }
.code-view { max-height:500px; overflow:auto; padding:16px; border-radius:8px; background:#111827; color:#d1fae5; line-height:1.55; white-space:pre-wrap; }
.select-pagination { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:4px 2px; color:var(--el-text-color-secondary); font-size:12px; }
@media (max-width: 900px) {
  .cleaning-page { padding:12px; }
  .operation-title { flex-wrap:wrap; }
  :deep(.config-item), :deep(.config-item.wide), :deep(.config-item.mapping) { width:100%; }
}
</style>
