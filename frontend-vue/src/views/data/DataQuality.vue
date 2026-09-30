<template>
  <div class="data-quality-page">
    <PageHeader title="数据质量" desc="定义质量规则，监控数据质量，发现并处理异常">
      <template #actions>
        <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" @click="openRunCheckDialog" :disabled="!rulesData?.items?.length">
          <el-icon><VideoPlay /></el-icon>
          执行检查
        </el-button>
      </template>
    </PageHeader>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :sm="6">
        <StatCard
          title="质量规则"
          :value="dashboardData?.rules?.total ?? 0"
          icon="Document"
          icon-color="#818cf8"
          bg-color="rgba(99,102,241,0.12)"
          :loading="dbLoading"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="总检查次数"
          :value="dashboardData?.results?.total_checks ?? 0"
          icon="CircleCheck"
          icon-color="#34d399"
          bg-color="rgba(52,211,153,0.12)"
          :loading="dbLoading"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="待处理异常"
          :value="dashboardData?.anomalies?.open ?? 0"
          icon="Warning"
          icon-color="#f59e0b"
          bg-color="rgba(245,158,11,0.12)"
          :loading="dbLoading"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatCard
          title="平均质量分"
          :value="Math.round(dashboardData?.results?.avg_score ?? 0)"
          unit="分"
          icon="TrendCharts"
          icon-color="#f87171"
          bg-color="rgba(248,113,113,0.12)"
          :loading="dbLoading"
        />
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab" class="quality-tabs" @tab-change="onTabChange">
      <el-tab-pane label="数据质量报告" name="reports">
        <el-card class="report-run-card">
          <template #header><span>生成数据质量报告</span></template>
          <el-form inline label-width="100px">
            <el-form-item label="数据集" required>
              <el-select v-model="reportForm.dataset_id" filterable placeholder="选择数据集" style="width:300px">
                <el-option v-for="dataset in reportDatasets" :key="dataset.id" :label="`#${dataset.id} ${dataset.name}`" :value="dataset.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="检查标准">
              <el-select v-model="reportForm.profile" style="width:220px">
                <el-option v-for="profile in reportProfiles" :key="profile.id" :label="profile.name" :value="profile.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="附加规则">
              <el-select v-model="reportForm.quality_rule_ids" multiple filterable collapse-tags clearable placeholder="可选" style="width:260px">
                <el-option v-for="rule in reportRules" :key="rule.id" :label="rule.name" :value="rule.id" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="reportRunning" @click="runQualityReport">生成报告（含 🤖 AI 解读）</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card v-if="currentQualityReport" class="report-result-card">
          <template #header><span>质量报告详情</span></template>
          <el-descriptions :column="4" border size="small">
            <el-descriptions-item label="数据集" :span="2">#{{ currentQualityReport.dataset_id }} {{ currentQualityReport.dataset_name }}</el-descriptions-item>
            <el-descriptions-item label="检查标准">{{ currentQualityReport.profile_name }}</el-descriptions-item>
            <el-descriptions-item label="结论"><el-tag :type="currentQualityReport.status === 'passed' ? 'success' : 'danger'">{{ currentQualityReport.status === 'passed' ? '通过' : '需处理' }}</el-tag></el-descriptions-item>
            <el-descriptions-item label="质量得分">{{ currentQualityReport.score }}</el-descriptions-item>
            <el-descriptions-item label="通过项">{{ currentQualityReport.checks_passed }}/{{ currentQualityReport.checks_total }}</el-descriptions-item>
            <el-descriptions-item label="数据行数">{{ currentQualityReport.rows }}</el-descriptions-item>
            <el-descriptions-item label="字段数">{{ currentQualityReport.columns }}</el-descriptions-item>
          </el-descriptions>
          <el-alert v-if="currentQualityReport.ai_assistance?.summary" class="quality-ai-alert" type="info" :title="`大模型辅助解读（${currentQualityReport.ai_assistance.provider || 'DeepSeek'}）`" :description="currentQualityReport.ai_assistance.summary" :closable="false" show-icon />
          <el-alert v-else-if="currentQualityReport.ai_assistance?.warning" class="quality-ai-alert" type="warning" :title="currentQualityReport.ai_assistance.warning" :closable="false" show-icon />
          <el-alert v-for="item in qualityManualAttention(currentQualityReport)" :key="item.id" class="quality-ai-check" type="warning" :title="`人工确认：${item.name} · ${item.reason}`" :closable="false" show-icon />
          <el-table :data="currentQualityReport.checks || []" border size="small" max-height="360" class="quality-report-table">
            <el-table-column label="结果" width="80"><template #default="{ row }"><el-tag size="small" :type="row.passed ? 'success' : 'danger'">{{ row.passed ? '通过' : '未通过' }}</el-tag></template></el-table-column>
            <el-table-column prop="name" label="检查项" min-width="150" />
            <el-table-column prop="expected" label="要求" min-width="180" />
            <el-table-column prop="actual" label="实际" min-width="180" />
            <el-table-column prop="score" label="得分" width="90" />
          </el-table>
        </el-card>

        <DataTable title="报告历史" :data="qualityReportHistory" :loading="qualityReportLoading" :empty="!qualityReportLoading && !qualityReportHistory.length" empty-text="暂无质量报告">
          <template #filters><el-button size="small" @click="loadQualityReportHistory"><el-icon><Refresh /></el-icon> 刷新</el-button></template>
          <el-table-column label="数据集" min-width="190"><template #default="{ row }">#{{ row.dataset_id || row.source_dataset_id }} {{ row.dataset_name || row.source_dataset_name || '已删除数据集' }}</template></el-table-column>
          <el-table-column label="来源" width="100"><template #default="{ row }">{{ row.cleaning_run_id ? '清洗后' : '直接检查' }}</template></el-table-column>
          <el-table-column label="结论" width="90"><template #default="{ row }"><el-tag size="small" :type="row.status === 'passed' ? 'success' : 'danger'">{{ row.status === 'passed' ? '通过' : '需处理' }}</el-tag></template></el-table-column>
          <el-table-column prop="score" label="得分" width="90" />
          <el-table-column prop="checked_at" label="生成时间" width="180"><template #default="{ row }">{{ formatTime(row.checked_at) }}</template></el-table-column>
          <el-table-column label="操作" width="80" fixed="right"><template #default="{ row }"><el-button text size="small" type="primary" @click="openQualityReport(row)">查看</el-button></template></el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- 质量规则 -->
      <el-tab-pane label="质量规则" name="rules">
        <DataTable
          title="规则列表"
          :data="rulesData?.items ?? []"
          :loading="rulesLoading"
          :error="rulesError ?? undefined"
          :empty="(rulesData?.items?.length ?? 0) === 0 && !rulesLoading"
          empty-text="暂无质量规则，点击上方按钮创建"
          :show-pagination="(rulesData?.total ?? 0) > rulesPageSize"
          :total="rulesData?.total ?? 0"
          :page-size="rulesPageSize"
          @size-change="handleRulesSizeChange"
          @page-change="handleRulesPageChange"
          @retry="loadRules"
        >
          <template #filters>
            <el-button v-if="userStore.hasPermission('datasets:process')" type="primary" size="small" @click="openCreateRuleDialog">
              <el-icon><Plus /></el-icon>
              新建规则
            </el-button>
            <el-select v-model="rulesFilterType" placeholder="规则类型" clearable size="small" style="width: 140px" @change="handleRulesFilterChange">
              <el-option label="全部" value="" />
              <el-option
                v-for="rt in ruleTypesData"
                :key="rt.type"
                :label="rt.name"
                :value="rt.type"
              />
            </el-select>
          </template>

          <el-table-column prop="name" label="规则名称" min-width="150" />
          <el-table-column label="类型" width="140">
            <template #default="{ row }">
              <el-tag :type="getRuleTypeTag(row.rule_type)" size="small">
                {{ getRuleTypeName(row.rule_type) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="field" label="字段" width="130" />
          <el-table-column label="严重程度" width="100">
            <template #default="{ row }">
              <el-tag :type="getSeverityType(row.severity)" size="small" effect="dark">
                {{ getSeverityText(row.severity) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-switch
                :model-value="row.is_active"
                size="small"
                @change="(val: boolean) => toggleRuleActive(row, val)"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="260" fixed="right">
            <template #default="{ row }">
              <el-button v-if="userStore.hasPermission('datasets:process')" text size="small" type="primary" @click="editRule(row)">编辑</el-button>
              <el-button text size="small" @click="openRevisions(row)">版本</el-button>
              <el-button v-if="userStore.hasPermission('datasets:process')" text size="small" @click="runSingleRule(row)">执行</el-button>
              <el-popconfirm title="确定删除此规则？" @confirm="deleteRule(row)">
                <template #reference>
                  <el-button text size="small" type="danger">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- 检查结果 -->
      <el-tab-pane label="检查结果" name="results">
        <DataTable
          title="检查历史"
          :data="resultsData?.items ?? []"
          :loading="reLoading"
          :error="reError ?? undefined"
          :empty="(resultsData?.items?.length ?? 0) === 0 && !reLoading"
          empty-text="暂无检查记录"
          :show-pagination="(resultsData?.total ?? 0) > resultsPageSize"
          :total="resultsData?.total ?? 0"
          :page-size="resultsPageSize"
          @size-change="handleResultsSizeChange"
          @page-change="handleResultsPageChange"
          @retry="loadResults"
        >
          <template #filters>
            <el-button size="small" @click="loadResults">
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </template>

          <el-table-column prop="rule_name" label="规则" min-width="160">
            <template #default="{ row }">
              <span>{{ row.rule_name || `规则 #${row.rule_id}` }}</span>
            </template>
          </el-table-column>
          <el-table-column label="质量分" width="160">
            <template #default="{ row }">
              <el-progress :percentage="row.score ?? 0" :stroke-width="8" :color="getScoreColor(row.score ?? 0)" />
            </template>
          </el-table-column>
          <el-table-column label="通过/总数" width="140">
            <template #default="{ row }">
              <span class="check-count">
                <span class="passed">{{ row.passed_count }}</span>
                /
                <span>{{ row.total_count }}</span>
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="checked_at" label="检查时间" width="180">
            <template #default="{ row }">
              {{ formatTime(row.checked_at) }}
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- 质量异常 -->
      <el-tab-pane label="质量异常" name="anomalies">
        <DataTable
          title="异常列表"
          :data="anomaliesData?.items ?? []"
          :loading="anLoading"
          :error="anError ?? undefined"
          :empty="(anomaliesData?.items?.length ?? 0) === 0 && !anLoading"
          empty-text="暂无异常记录"
          :show-pagination="(anomaliesData?.total ?? 0) > anomaliesPageSize"
          :total="anomaliesData?.total ?? 0"
          :page-size="anomaliesPageSize"
          @size-change="handleAnomaliesSizeChange"
          @page-change="handleAnomaliesPageChange"
          @retry="loadAnomalies"
        >
          <template #filters>
            <el-select v-model="anomaliesFilterStatus" placeholder="状态筛选" clearable size="small" style="width: 120px" @change="handleAnomaliesFilterChange">
              <el-option label="全部" value="" />
              <el-option label="待处理" value="open" />
              <el-option label="已解决" value="resolved" />
              <el-option label="已忽略" value="ignored" />
            </el-select>
            <el-select v-model="anomaliesFilterSeverity" placeholder="严重程度" clearable size="small" style="width: 120px" @change="handleAnomaliesFilterChange">
              <el-option label="全部" value="" />
              <el-option label="严重" value="critical" />
              <el-option label="警告" value="warning" />
              <el-option label="信息" value="info" />
            </el-select>
          </template>

          <el-table-column label="异常类型" width="140">
            <template #default="{ row }">
              <el-tag size="small">{{ row.anomaly_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="field" label="字段" width="130" />
          <el-table-column label="规则" min-width="140">
            <template #default="{ row }">
              <span>{{ row.rule_name || `规则 #${row.rule_id}` }}</span>
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="220">
            <template #default="{ row }">
              <div class="anomaly-detail">
                <span class="detail-label">期望:</span> {{ row.expected_value }}
                <br />
                <span class="detail-label">实际:</span> {{ row.actual_value }}
                <span v-if="row.record_count" class="detail-count">({{ row.record_count }}条)</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="严重程度" width="100">
            <template #default="{ row }">
              <el-tag :type="getSeverityType(row.severity)" size="small" effect="dark">
                {{ getSeverityText(row.severity) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="getStatusType(row.status)" size="small">
                {{ getStatusText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="userStore.hasPermission('datasets:process') && row.status === 'open'"
                text size="small" type="primary"
                @click="resolveAnomaly(row)"
              >
                解决
              </el-button>
              <el-button
                v-if="userStore.hasPermission('datasets:process') && row.status === 'open'"
                text size="small"
                @click="ignoreAnomaly(row)"
              >
                忽略
              </el-button>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- 数据表浏览 -->
      <el-tab-pane label="数据表浏览" name="tables">
        <DataTable
          title="数据表列表"
          :data="tablesData?.items ?? []"
          :loading="tablesLoading"
          :error="tablesError ?? undefined"
          :empty="(tablesData?.items?.length ?? 0) === 0 && !tablesLoading"
          empty-text="暂无已治理的数据表"
          @retry="loadTables"
        >
          <template #filters>
            <el-button type="primary" size="small" @click="scanSources">
              <el-icon><Search /></el-icon>
              扫描数据源
            </el-button>
          </template>
          <el-table-column prop="display_name" label="表名" min-width="150">
            <template #default="{ row }">
              <span style="font-weight:600">{{ row.display_name || row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="system" label="系统" width="110" />
          <el-table-column prop="database" label="数据库" width="130" />
          <el-table-column prop="records" label="记录数" width="100" align="right">
            <template #default="{ row }">{{ fmt(row.records) }}</template>
          </el-table-column>
          <el-table-column prop="quality_score" label="质量分" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="row.quality_score >= 90 ? 'success' : row.quality_score >= 70 ? 'warning' : 'danger'" size="small">
                {{ row.quality_score ?? '-' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="owner" label="负责人" width="100" />
          <el-table-column label="标签" min-width="150">
            <template #default="{ row }">
              <el-tag v-for="t in (row.tags || [])" :key="t" size="small" style="margin-right:4px">{{ t }}</el-tag>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- 数据标准 -->
      <el-tab-pane label="数据标准" name="standards">
        <DataTable
          title="标准规范"
          :data="standardsData?.items ?? []"
          :loading="stdsLoading"
          :error="stdsError ?? undefined"
          :empty="(standardsData?.items?.length ?? 0) === 0 && !stdsLoading"
          empty-text="暂无数据标准"
          @retry="loadStandards"
        >
          <el-table-column prop="name" label="标准名称" min-width="180">
            <template #default="{ row }">
              <span style="font-weight:600">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="分类" width="100">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ row.category }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="280" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="row.status === 'active' ? 'success' : 'warning'" size="small">
                {{ row.status === 'active' ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
        </DataTable>
      </el-tab-pane>

      <!-- 数据源扫描 -->
      <el-tab-pane label="数据源扫描" name="scan">
        <el-card>
          <template #header>
            <span>数据源扫描</span>
          </template>
          <p style="color:var(--tx2);margin-bottom:16px">自动发现并注册平台中的数据表，构建数据资产全景视图。</p>
          <el-button type="primary" @click="scanSources" :loading="scanning">
            <el-icon><Search /></el-icon>
            开始扫描
          </el-button>
          <div v-if="scanResult" style="margin-top:16px">
            <el-alert
              :title="`扫描完成：发现 ${scanResult.tables_found ?? 0} 个数据表`"
              type="success"
              :closable="false"
              show-icon
            />
            <div v-if="scanResult.details" style="margin-top:12px">
              <el-tag v-for="(info, key) in scanResult.details" :key="key" size="small" style="margin:4px" type="info">
                {{ info.name }}: {{ info.count }} 条
              </el-tag>
            </div>
          </div>
        </el-card>
      </el-tab-pane>

    </el-tabs>

    <!-- 创建/编辑规则对话框 -->
    <el-dialog
      v-model="showRuleDialog"
      :title="editingRule ? '编辑质量规则' : '创建质量规则'"
      width="560px"
      @closed="resetRuleForm"
    >
      <el-form :model="ruleForm" label-width="100px" ref="ruleFormRef">
        <el-form-item label="规则名称" required>
          <el-input v-model="ruleForm.name" placeholder="输入规则名称" maxlength="100" />
        </el-form-item>
        <el-form-item label="规则类型" required>
          <el-select v-model="ruleForm.rule_type" placeholder="选择类型" style="width: 100%" @change="onRuleTypeChange">
            <el-option
              v-for="rt in ruleTypesData"
              :key="rt.type"
              :label="`${rt.name} - ${rt.description}`"
              :value="rt.type"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标字段" required>
          <el-input v-model="ruleForm.field" placeholder="字段名" />
        </el-form-item>
        <el-form-item v-if="ruleForm.rule_type === 'range'" label="最小值">
          <el-input-number v-model="thresholdConfig.min" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="ruleForm.rule_type === 'range'" label="最大值">
          <el-input-number v-model="thresholdConfig.max" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="ruleForm.rule_type === 'pattern'" label="正则表达式">
          <el-input v-model="thresholdConfig.pattern" placeholder="输入正则表达式，如 ^\d{11}$" />
        </el-form-item>
        <el-form-item v-if="ruleForm.rule_type === 'completeness'" label="最小完整度">
          <el-input-number
            v-model="thresholdConfig.min"
            :min="0" :max="1" :step="0.05" :precision="2"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item v-if="ruleForm.rule_type === 'uniqueness'" label="最大重复率">
          <el-input-number
            v-model="thresholdConfig.max_rate"
            :min="0" :max="1" :step="0.05" :precision="2"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="条件表达式">
          <el-input
            v-model="ruleForm.condition"
            type="textarea"
            :rows="2"
            placeholder="可选，SQL表达式或自定义逻辑"
          />
        </el-form-item>
        <el-form-item label="严重程度">
          <el-select v-model="ruleForm.severity" style="width: 100%">
            <el-option label="严重" value="critical" />
            <el-option label="警告" value="warning" />
            <el-option label="信息" value="info" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="ruleForm.description"
            type="textarea"
            :rows="2"
            placeholder="规则描述"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRuleDialog = false">取消</el-button>
        <el-button type="primary" @click="submitRule" :loading="submitting">
          {{ editingRule ? '更新' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRevisionDialog" title="规则版本与发布" width="700px">
      <el-alert title="草稿不会影响当前检查；发布后才替换生效规则。发布行为可审计。" type="info" :closable="false" style="margin-bottom:12px" />
      <el-table :data="revisions" size="small" max-height="360">
        <el-table-column prop="version" label="版本" width="80"><template #default="{row}">v{{ row.version }}</template></el-table-column>
        <el-table-column prop="status" label="状态" width="110" />
        <el-table-column prop="note" label="备注" min-width="180" />
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="170"><template #default="{row}"><el-button text size="small" @click="showRevisionDiff(row)">对比</el-button><el-button v-if="row.status==='draft'" text type="primary" size="small" @click="publishRevision(row)">发布</el-button><el-button v-else-if="row.status==='superseded'" text type="warning" size="small" @click="rollbackRevision(row)">回滚</el-button></template></el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="showDiffDialog" title="规则版本差异（当前生效规则 vs 所选版本）" width="760px">
      <el-empty v-if="!revisionDiff.length" description="没有字段差异" :image-size="60" />
      <el-table v-else :data="revisionDiff" size="small" max-height="400"><el-table-column prop="field" label="字段" width="150"/><el-table-column prop="current" label="当前生效值" min-width="240" show-overflow-tooltip/><el-table-column prop="revision" label="版本值" min-width="240" show-overflow-tooltip/></el-table>
    </el-dialog>

    <!-- 执行检查对话框 -->
    <el-dialog v-model="showRunCheckDialog" title="执行质量检查" width="480px">
      <el-form label-width="100px">
        <el-form-item label="数据集" required>
          <el-select v-model="checkForm.dataset_id" filterable style="width: 100%" placeholder="选择数据集">
            <el-option v-for="d in checkDatasets" :key="d.id" :label="'#' + d.id + ' ' + d.name + (d.row_count!=null?(' ('+d.row_count+'行)'):'')" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="规则范围">
          <el-radio-group v-model="checkForm.scope">
            <el-radio value="all">所有活跃规则</el-radio>
            <el-radio value="selected">指定规则</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="checkForm.scope === 'selected'" label="选择规则">
          <el-select
            v-model="checkForm.rule_ids"
            multiple
            filterable
            placeholder="选择要执行的规则"
            style="width: 100%"
          >
            <el-option
              v-for="r in (rulesData?.items ?? [])"
              :key="r.id"
              :label="`${r.name} (${getRuleTypeName(r.rule_type)})`"
              :value="r.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRunCheckDialog = false">取消</el-button>
        <el-button type="primary" @click="runCheck" :loading="checking">
          开始检查
        </el-button>
      </template>
    </el-dialog>

    <!-- 解决异常对话框 -->
    <el-dialog v-model="showResolveDialog" title="解决异常" width="420px">
      <el-form label-width="100px">
        <el-form-item label="解决说明" required>
          <el-input
            v-model="resolveNote"
            type="textarea"
            :rows="3"
            placeholder="请描述解决方案"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showResolveDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmResolve" :loading="resolving">
          确认解决
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Plus, Refresh, VideoPlay,
} from '@element-plus/icons-vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'
import StatCard from '@/components/StatCard.vue'
import DataTable from '@/components/DataTable.vue'
import { useUserStore } from '@/stores/user'
import { useRoute } from 'vue-router'

const userStore = useUserStore()
const route = useRoute()

const API = '/api/v1'

// ── API helpers for PUT/DELETE ───────────────────────
async function apiPut<T = any>(p: string, d?: any): Promise<T> {
  const r = await fetch(API + p, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(d ?? {}),
  })
  return r.json()
}

async function apiDel<T = any>(p: string): Promise<T> {
  const r = await fetch(API + p, { method: 'DELETE' })
  return r.json()
}

// ── Types ────────────────────────────────────────────
interface QualityRule {
  id: number
  name: string
  description?: string
  rule_type: string
  field: string
  condition?: string
  threshold?: Record<string, any>
  severity: string
  is_active: boolean
  created_at?: string
  updated_at?: string
  created_by?: number
}

interface RuleType {
  type: string
  name: string
  description: string
  threshold_config?: Record<string, string>
}

interface CheckResult {
  id: number
  rule_id: number
  dataset_id: number
  passed_count: number
  failed_count: number
  total_count: number
  score: number
  details?: any
  checked_at?: string
  checked_by?: number
  rule_name?: string
  rule_type?: string
}

interface AnomalyItem {
  id: number
  dataset_id: number
  rule_id?: number
  field?: string
  anomaly_type?: string
  expected_value?: string
  actual_value?: string
  record_ids?: any
  record_count?: number
  severity?: string
  status: string
  detected_at?: string
  resolved_at?: string
  resolved_by?: number
  resolution_note?: string
  rule_name?: string
}

interface PaginatedResponse<T> {
  total: number
  items: T[]
}

interface DashboardData {
  rules: { total: number; active: number; by_type: any[] }
  results: { total_checks: number; avg_score: number; total_passed: number; total_failed: number }
  anomalies: { total: number; open: number; resolved: number; critical: number; warning: number }
  trend: any[]
  anomalies_by_type: any[]
}

// ── Tabs ─────────────────────────────────────────────
const activeTab = ref(route.query.tab === 'reports' ? 'reports' : 'rules')

// ── Data quality reports ────────────────────────────
const reportDatasets = ref<any[]>([])
const reportProfiles = ref<any[]>([])
const reportRules = ref<any[]>([])
const reportRunning = ref(false)
const qualityReportLoading = ref(false)
const qualityReportHistory = ref<any[]>([])
const currentQualityReport = ref<any>(null)
const reportForm = reactive({
  dataset_id: route.query.dataset_id ? Number(route.query.dataset_id) : null as number | null,
  profile: 'basic',
  quality_rule_ids: [] as number[],
})

async function loadQualityReportInputs() {
  try {
    const [datasets, profiles, rules] = await Promise.all([
      get<any>('/datasets?limit=200'),
      get<any>('/dataset-governance/profiles'),
      get<any>('/dataset-governance/quality-rules'),
    ])
    reportDatasets.value = (datasets.items || datasets || []).filter((item: any) => item.has_file)
    reportProfiles.value = profiles.items || []
    reportRules.value = rules.items || []
  } catch (e: any) { ElMessage.error(e?.message || '加载质量报告配置失败') }
}

async function loadQualityReportHistory() {
  qualityReportLoading.value = true
  try {
    const response = await get<any>('/dataset-governance/reports?limit=200')
    qualityReportHistory.value = response.items || []
  } catch (e: any) { ElMessage.error(e?.message || '加载质量报告历史失败') }
  finally { qualityReportLoading.value = false }
}

async function runQualityReport() {
  if (!reportForm.dataset_id) { ElMessage.warning('请选择数据集'); return }
  reportRunning.value = true
  try {
    const response = await post<any>('/dataset-governance/reports/run', {
      dataset_id: reportForm.dataset_id,
      profile: reportForm.profile,
      quality_rule_ids: reportForm.quality_rule_ids,
    })
    currentQualityReport.value = response.report
    ElMessage.success(response.message || '数据质量报告已生成')
    await loadQualityReportHistory()
  } catch (e: any) { ElMessage.error(e?.message || '生成质量报告失败') }
  finally { reportRunning.value = false }
}

function openQualityReport(row: any) {
  currentQualityReport.value = row.summary || row
  if (!currentQualityReport.value.dataset_id) currentQualityReport.value.dataset_id = row.dataset_id || row.source_dataset_id
  if (!currentQualityReport.value.dataset_name) currentQualityReport.value.dataset_name = row.dataset_name || row.source_dataset_name
}

function qualityManualAttention(report: any) {
  const items = report?.ai_assistance?.manual_attention
  if (!Array.isArray(items)) return []
  // 旧报告中的自由文本没有对应本地检查项，不再作为数据问题展示。
  return items.filter((item: any) => item && typeof item === 'object' && item.id && item.name && item.reason)
}

watch(() => route.query.dataset_id, (datasetId) => {
  if (datasetId) reportForm.dataset_id = Number(datasetId)
})

// ── Dashboard ───────────────────────────────────────
const {
  data: dashboardData,
  loading: dbLoading,
  refresh: loadDashboard,
} = useAsyncData<DashboardData>(() => get<DashboardData>('/quality/dashboard'))

// ── Rule types ──────────────────────────────────────
const {
  data: ruleTypesData,
  refresh: loadRuleTypes,
} = useAsyncData<{ rule_types: RuleType[] }>(() => get<{ rule_types: RuleType[] }>('/quality/rule-types'))

const ruleTypes = computed<RuleType[]>(() => ruleTypesData.value?.rule_types ?? [])

// ── Rules list ──────────────────────────────────────
const rulesPageSize = ref(20)
const rulesPage = ref(1)
const rulesFilterType = ref('')

const {
  data: rulesData,
  loading: rulesLoading,
  error: rulesError,
  refresh: loadRules,
} = useAsyncData<PaginatedResponse<QualityRule>>(() => {
  const params = new URLSearchParams()
  params.set('skip', String((rulesPage.value - 1) * rulesPageSize.value))
  params.set('limit', String(rulesPageSize.value))
  if (rulesFilterType.value) params.set('rule_type', rulesFilterType.value)
  return get<PaginatedResponse<QualityRule>>('/quality/rules?' + params.toString())
})

function handleRulesSizeChange(size: number) {
  rulesPageSize.value = size
  rulesPage.value = 1
  loadRules()
}

function handleRulesPageChange(page: number) {
  rulesPage.value = page
  loadRules()
}

function handleRulesFilterChange() {
  rulesPage.value = 1
  loadRules()
}

// ── Results list ────────────────────────────────────
const resultsPageSize = ref(20)
const resultsPage = ref(1)

const {
  data: resultsData,
  loading: reLoading,
  error: reError,
  refresh: loadResults,
} = useAsyncData<PaginatedResponse<CheckResult>>(() => {
  const params = new URLSearchParams()
  params.set('skip', String((resultsPage.value - 1) * resultsPageSize.value))
  params.set('limit', String(resultsPageSize.value))
  return get<PaginatedResponse<CheckResult>>('/quality/results?' + params.toString())
})

function handleResultsSizeChange(size: number) {
  resultsPageSize.value = size
  resultsPage.value = 1
  loadResults()
}

function handleResultsPageChange(page: number) {
  resultsPage.value = page
  loadResults()
}

// ── Anomalies list ──────────────────────────────────
const anomaliesPageSize = ref(20)
const anomaliesPage = ref(1)
// 默认只显示待处理异常；已解决/忽略项仍可通过筛选审计查看。
const anomaliesFilterStatus = ref('open')
const anomaliesFilterSeverity = ref('')

const {
  data: anomaliesData,
  loading: anLoading,
  error: anError,
  refresh: loadAnomalies,
} = useAsyncData<PaginatedResponse<AnomalyItem>>(() => {
  const params = new URLSearchParams()
  params.set('skip', String((anomaliesPage.value - 1) * anomaliesPageSize.value))
  params.set('limit', String(anomaliesPageSize.value))
  if (anomaliesFilterStatus.value) params.set('status', anomaliesFilterStatus.value)
  if (anomaliesFilterSeverity.value) params.set('severity', anomaliesFilterSeverity.value)
  return get<PaginatedResponse<AnomalyItem>>('/quality/anomalies?' + params.toString())
})

function handleAnomaliesSizeChange(size: number) {
  anomaliesPageSize.value = size
  anomaliesPage.value = 1
  loadAnomalies()
}

function handleAnomaliesPageChange(page: number) {
  anomaliesPage.value = page
  loadAnomalies()
}

function handleAnomaliesFilterChange() {
  anomaliesPage.value = 1
  loadAnomalies()
}

// ── Rule CRUD ───────────────────────────────────────
const showRuleDialog = ref(false)
const editingRule = ref<QualityRule | null>(null)
const submitting = ref(false)
const ruleForm = reactive({
  name: '',
  rule_type: 'completeness',
  field: '',
  condition: '',
  severity: 'warning' as string,
  description: '',
})
const thresholdConfig = reactive<Record<string, any>>({})
const showRevisionDialog = ref(false)
const revisionRuleId = ref<number | null>(null)
const revisions = ref<any[]>([])
const showDiffDialog = ref(false)
const revisionDiff = ref<any[]>([])

function resetRuleForm() {
  ruleForm.name = ''
  ruleForm.rule_type = 'completeness'
  ruleForm.field = ''
  ruleForm.condition = ''
  ruleForm.severity = 'warning'
  ruleForm.description = ''
  Object.keys(thresholdConfig).forEach(k => delete thresholdConfig[k])
  editingRule.value = null
}

function onRuleTypeChange() {
  Object.keys(thresholdConfig).forEach(k => delete thresholdConfig[k])
}

function openCreateRuleDialog() {
  resetRuleForm()
  showRuleDialog.value = true
}

function editRule(rule: QualityRule) {
  editingRule.value = rule
  ruleForm.name = rule.name
  ruleForm.rule_type = rule.rule_type
  ruleForm.field = rule.field
  ruleForm.condition = rule.condition || ''
  ruleForm.severity = rule.severity || 'warning'
  ruleForm.description = rule.description || ''
  Object.keys(thresholdConfig).forEach(k => delete thresholdConfig[k])
  if (rule.threshold) {
    Object.assign(thresholdConfig, rule.threshold)
  }
  showRuleDialog.value = true
}

async function submitRule() {
  if (!ruleForm.name || !ruleForm.rule_type || !ruleForm.field) {
    ElMessage.warning('请填写规则名称、类型和字段')
    return
  }
  submitting.value = true
  try {
    const payload: Record<string, any> = {
      name: ruleForm.name,
      rule_type: ruleForm.rule_type,
      field: ruleForm.field,
      condition: ruleForm.condition || null,
      severity: ruleForm.severity,
      description: ruleForm.description || null,
    }
    if (Object.keys(thresholdConfig).length > 0) {
      payload.threshold = { ...thresholdConfig }
    }

    if (editingRule.value) {
      await post(`/quality/rules/${editingRule.value.id}/revisions`, { payload, note: '页面编辑保存的草稿' })
      ElMessage.success('规则草稿已保存，需在版本列表显式发布')
    } else {
      await post('/quality/rules', payload)
      ElMessage.success('规则创建成功')
    }
    showRuleDialog.value = false
    await loadRules()
    await loadDashboard()
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.message || e?.toString?.() || '未知错误'))
  } finally {
    submitting.value = false
  }
}

async function openRevisions(rule: QualityRule) {
  revisionRuleId.value = rule.id
  try { const result = await get<any>(`/quality/rules/${rule.id}/revisions`); revisions.value = result.items || []; showRevisionDialog.value = true }
  catch (e: any) { ElMessage.error(e?.message || '版本记录加载失败') }
}
async function publishRevision(revision: any) {
  if (!revisionRuleId.value) return
  try {
    await post(`/quality/rules/${revisionRuleId.value}/revisions/${revision.id}/publish`)
    ElMessage.success('草稿已发布'); await openRevisions({ id: revisionRuleId.value } as QualityRule); await loadRules()
  } catch (e: any) { ElMessage.error(e?.message || '发布失败') }
}
async function showRevisionDiff(revision: any) {
  if (!revisionRuleId.value) return
  try { const result = await get<any>(`/quality/rules/${revisionRuleId.value}/revisions/${revision.id}/diff`); revisionDiff.value = result.changes || []; showDiffDialog.value = true }
  catch (e: any) { ElMessage.error(e?.message || '版本差异加载失败') }
}
async function rollbackRevision(revision: any) {
  if (!revisionRuleId.value) return
  try { await post(`/quality/rules/${revisionRuleId.value}/revisions/${revision.id}/rollback`); ElMessage.success('历史版本已回滚并生效'); await openRevisions({ id: revisionRuleId.value } as QualityRule); await loadRules() }
  catch (e: any) { ElMessage.error(e?.message || '回滚失败') }
}

async function deleteRule(rule: QualityRule) {
  try {
    await apiDel(`/quality/rules/${rule.id}`)
    ElMessage.success('规则已删除')
    await loadRules()
    await loadDashboard()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.message || e?.toString?.() || '未知错误'))
  }
}

async function toggleRuleActive(rule: QualityRule, active: boolean) {
  try {
    await apiPut(`/quality/rules/${rule.id}`, { is_active: active })
    ElMessage.success(active ? '规则已启用' : '规则已停用')
    loadRules()
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.message || e?.toString?.() || '未知错误'))
  }
}

// ── Check execution ─────────────────────────────────
const showRunCheckDialog = ref(false)
const checking = ref(false)
const checkForm = reactive({
  dataset_id: null as number | null,
  scope: 'all' as 'all' | 'selected',
  rule_ids: [] as number[],
})
const checkDatasets = ref<any[]>([])
async function loadCheckDatasets() {
  try { const d = await get('/datasets?limit=200'); checkDatasets.value = (d.items || []).filter((x: any) => x.has_file) } catch { /* */ }
}

function openRunCheckDialog() {
  checkForm.dataset_id = null
  checkForm.scope = 'all'
  checkForm.rule_ids = []
  loadCheckDatasets()
  showRunCheckDialog.value = true
}

async function runSingleRule(rule: QualityRule) {
  checkForm.dataset_id = null
  checkForm.scope = 'selected'
  checkForm.rule_ids = [rule.id]
  showRunCheckDialog.value = true
}

async function runCheck() {
  if (!checkForm.dataset_id) {
    ElMessage.warning('请输入数据集ID')
    return
  }
  checking.value = true
  try {
    const payload: Record<string, any> = {
      dataset_id: checkForm.dataset_id,
    }
    if (checkForm.scope === 'selected' && checkForm.rule_ids.length > 0) {
      payload.rule_ids = checkForm.rule_ids
    }
    const res = await post<any>('/quality/check', payload)
    ElMessage.success(res.message || '质量检查完成')
    showRunCheckDialog.value = false
    await loadResults()
    await loadAnomalies()
    await loadDashboard()
  } catch (e: any) {
    ElMessage.error('检查失败: ' + (e?.message || e?.toString?.() || '未知错误'))
  } finally {
    checking.value = false
  }
}

// ── Anomaly actions ─────────────────────────────────
const showResolveDialog = ref(false)
const resolvingAnomaly = ref<AnomalyItem | null>(null)
const resolveNote = ref('')
const resolving = ref(false)

function resolveAnomaly(anomaly: AnomalyItem) {
  resolvingAnomaly.value = anomaly
  resolveNote.value = ''
  showResolveDialog.value = true
}

async function confirmResolve() {
  if (!resolveNote.value.trim()) {
    ElMessage.warning('请输入解决说明')
    return
  }
  if (!resolvingAnomaly.value) return
  resolving.value = true
  try {
    await apiPut(`/quality/anomalies/${resolvingAnomaly.value.id}/resolve`, {
      resolution_note: resolveNote.value,
    })
    ElMessage.success('异常已解决')
    showResolveDialog.value = false
    await loadAnomalies()
    await loadDashboard()
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.message || e?.toString?.() || '未知错误'))
  } finally {
    resolving.value = false
  }
}

async function ignoreAnomaly(anomaly: AnomalyItem) {
  try {
    await post(`/quality/anomalies/${anomaly.id}/ignore`)
    ElMessage.success('异常已忽略')
    await loadAnomalies()
    await loadDashboard()
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.message || e?.toString?.() || '未知错误'))
  }
}

// ── Helpers ─────────────────────────────────────────
function getRuleTypeName(type: string) {
  const rt = ruleTypes.value.find(t => t.type === type)
  return rt?.name || type
}

function getRuleTypeTag(type: string) {
  const tags: Record<string, string> = {
    completeness: 'success',
    uniqueness: 'warning',
    range: 'primary',
    pattern: 'info',
    custom: '',
  }
  return tags[type] || ''
}

function getSeverityType(severity: string) {
  const types: Record<string, string> = {
    critical: 'danger',
    warning: 'warning',
    info: 'info',
  }
  return types[severity] || 'info'
}

function getSeverityText(severity: string) {
  const texts: Record<string, string> = {
    critical: '严重',
    warning: '警告',
    info: '信息',
  }
  return texts[severity] || severity
}

function getStatusType(status: string) {
  const types: Record<string, string> = {
    open: 'warning',
    resolved: 'success',
    ignored: 'info',
  }
  return types[status] || 'info'
}

function getStatusText(status: string) {
  const texts: Record<string, string> = {
    open: '待处理',
    resolved: '已解决',
    ignored: '已忽略',
  }
  return texts[status] || status
}

function getScoreColor(score: number) {
  if (score >= 90) return '#67C23A'
  if (score >= 70) return '#E6A23C'
  return '#F56C6C'
}

function formatTime(time: string | undefined) {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

// ── Tab change: reload data ────────────────────────
// ── 数据表浏览 ────────────────────────────────────
const {
  data: tablesData,
  loading: tablesLoading,
  error: tablesError,
  refresh: loadTables,
} = useAsyncData<PaginatedResponse<any>>(() => get<PaginatedResponse<any>>('/data-governance/tables'))

// ── 数据标准 ──────────────────────────────────────
const {
  data: standardsData,
  loading: stdsLoading,
  error: stdsError,
  refresh: loadStandards,
} = useAsyncData<PaginatedResponse<any>>(() => get<PaginatedResponse<any>>('/data-governance/standards'))

// ── 数据源扫描 ────────────────────────────────────
const scanning = ref(false)
const scanResult = ref<any>(null)

async function scanSources() {
  scanning.value = true
  scanResult.value = null
  try {
    const res = await post<any>('/data-governance/scan', {})
    scanResult.value = res
    ElMessage.success(res.message || '扫描完成')
    loadTables()
  } catch (e: any) {
    ElMessage.error(e?.message || '扫描失败')
  } finally {
    scanning.value = false
  }
}

function fmt(n: any): string {
  return n == null ? '-' : Number(n).toLocaleString()
}

function onTabChange(tab: string) {
  if (tab === 'reports') { loadQualityReportInputs(); loadQualityReportHistory() }
  else if (tab === 'results') loadResults()
  else if (tab === 'anomalies') loadAnomalies()
  else if (tab === 'rules') loadRules()
  else if (tab === 'tables') loadTables()
  else if (tab === 'standards') loadStandards()
}

if (activeTab.value === 'reports') {
  void loadQualityReportInputs()
  void loadQualityReportHistory()
}
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.data-quality-page {
  .stats-row {
    margin-bottom: 20px;
  }

  .report-run-card, .report-result-card { margin-bottom: 16px; }
  .quality-ai-alert { margin-top: 16px; }
  .quality-ai-check { margin-top: 8px; }
  .quality-ai-alert :deep(.el-alert__title), .quality-ai-alert :deep(.el-alert__description), .quality-ai-check :deep(.el-alert__title) { line-height: 1.7; }
  .quality-report-table { margin-top: 16px; }

  .quality-tabs {
    :deep(.el-tabs__content) {
      padding: 0;
    }
  }

  .check-count {
    .passed {
      color: #67C23A;
      font-weight: 600;
    }
  }

  .anomaly-detail {
    font-size: 12px;
    line-height: 1.6;

    .detail-label {
      color: $text-secondary;
      margin-right: 4px;
    }

    .detail-count {
      color: $text-secondary;
      margin-left: 4px;
      font-size: 11px;
    }
  }
}
</style>
