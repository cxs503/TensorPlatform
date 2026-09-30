<template>
<div class="page">
  <div class="page-header">
    <h1>🏭 数字孪生与虚拟数据中心</h1>
    <p>以资产拓扑、规律模型和安全约束生成可复现回放；后续可用只读生产数据校准。</p>
  </div>

  <el-row :gutter="12" class="stats">
    <el-col :xs="12" :sm="3" v-for="s in stats" :key="s.k">
      <el-card shadow="hover"><div class="sv">{{ s.v }}</div><div class="sl">{{ s.l }}</div></el-card>
    </el-col>
  </el-row>

  <!-- Tabs: Steel | Energy -->
  <el-tabs v-model="activeTab">
    <el-tab-pane label="🔥 钢铁样本增强（兼容）" name="steel">
      <el-alert title="基于真实生产过程机数据(44K条)模板生成" type="success" :closable="false" style="margin-bottom:12px"/>

      <el-row :gutter="16">
        <el-col :xs="24" :sm="12" v-for="s in steelScenarios" :key="s.key">
          <el-card :class="['scenario-card', s.stored > 0 ? 'has-data' : '']" style="margin-bottom:16px">
            <template #header>
              <div style="display:flex;justify-content:space-between;align-items:center">
                <span><b>{{ s.icon }} {{ s.name }}</b></span>
                <el-tag size="small" :type="s.stored > 0 ? 'success' : 'info'">
                  {{ s.stored > 0 ? s.stored + '条' : '待生成' }}
                </el-tag>
              </div>
            </template>
            <div class="sc-desc">{{ s.desc }}</div>
            <div style="margin:8px 0;font-size:12px;color:#888">
              特征: {{ s.features }}维 | 目标: {{ (s.targets||[]).join(', ') }}
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-top:12px">
              <el-input-number v-model="s.count" :min="10" :max="10000" :step="100" size="small" style="width:130px"/>
              <el-button type="primary" size="small" @click="genSteel(s)" :loading="s.loading">
                🚀 生成
              </el-button>
              <el-button size="small" @click="validateSteel(s)" :loading="s.validating">
                📊 质量验证
              </el-button>
              <el-button size="small" @click="viewSteel(s)" v-if="s.stored > 0">👁 预览</el-button>
              <el-button size="small" @click="downloadData('steel_'+s.key)" v-if="s.stored > 0">⬇下载</el-button>
            </div>
            <!-- Quality report -->
            <div v-if="s.quality" class="quality-report" style="margin-top:8px;background:#f0f9ff;padding:8px;border-radius:4px;font-size:12px">
              <div>📊 质量: <el-tag :type="s.quality.mean_deviation < 0.15 ? 'success' : 'warning'" size="small">
                {{ s.quality.mean_deviation < 0.15 ? '✅ 优秀' : '⚠️ 需改进' }}
              </el-tag></div>
              <div style="color:#666">均值偏差: {{ (s.quality.mean_deviation*100).toFixed(1) }}% | 
                物理约束: {{ ((s.quality.physical_pass_rate||0)*100).toFixed(0) }}%通过</div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </el-tab-pane>

    <el-tab-pane label="⚡ 基础 Mock 数据（兼容）" name="energy">
      <el-card style="margin-bottom:16px">
        <h3>⚡ 一键生成全部能源数据</h3>
        <p style="color:#888">光伏、储能、设备、能耗、碳排放、负荷预测等全场景</p>
        <el-button type="primary" @click="generateAll" :loading="genAllLoading">🚀 一键生成全部</el-button>
      </el-card>
      <el-row :gutter="16">
        <el-col :xs="24" :sm="12" v-for="dt in energyTypes" :key="dt.key">
          <el-card style="margin-bottom:12px">
            <template #header>
              <div style="display:flex;justify-content:space-between">
                <b>{{ dt.icon }} {{ dt.label }}</b>
                <el-tag size="small" :type="dt.stored>0?'success':'info'">{{ dt.stored>0?dt.stored+'条':'未生成' }}</el-tag>
              </div>
            </template>
            <div style="color:#888;font-size:13px;margin-bottom:8px">{{ dt.desc }}</div>
            <el-input-number v-model="dt.count" :min="10" :max="10000" size="small" style="width:130px;margin-right:8px"/>
            <el-button type="primary" size="small" @click="genEnergy(dt)" :loading="dt.loading">生成</el-button>
            <el-button size="small" @click="downloadData(dt.key)" :disabled="dt.stored<=0">⬇下载</el-button>
          </el-card>
        </el-col>
      </el-row>
    </el-tab-pane>

    <el-tab-pane label="🧪 数字孪生验证" name="industrial">
      <el-alert title="仿真数据包含物理约束、真值标签及建议—审批—执行—回退回执；仅用于离线验证，绝不下发控制指令。" type="warning" :closable="false" style="margin-bottom:12px"/>
      <el-card style="margin-bottom:16px">
        <template #header><b>只读数据接入沙箱</b></template>
        <div class="sc-desc">上传 CSV 验证字段、量程和小时级时间对齐；文件仅在本次请求内存中解析，不保存、不连接 OPC UA/MQTT、不下发控制。默认要求表头使用场景数据契约中的 field 名。</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px">
          <el-select v-model="sandboxScenarioKey" size="small" style="width:220px"><el-option v-for="item in industrialScenarios" :key="item.key" :label="item.name" :value="item.key" /></el-select>
          <el-upload :auto-upload="false" :show-file-list="true" accept=".csv,text/csv" :on-change="selectSandboxFile"><el-button size="small">选择 CSV</el-button></el-upload>
          <el-button size="small" type="primary" :disabled="!sandboxFile" @click="validateSandboxCsv">只读校验</el-button>
        </div>
        <el-collapse style="margin-top:8px"><el-collapse-item title="非标准表头/单位映射（可选 JSON）" name="mapping"><el-input v-model="sandboxMappingJson" type="textarea" :rows="2" placeholder='字段映射，例如 {"timestamp":"ts","grid_import_kw":"grid_w"}' /><el-input v-model="sandboxUnitsJson" type="textarea" :rows="2" style="margin-top:6px" placeholder='单位映射，例如 {"grid_import_kw":"W","pv_power_kw":"W"}' /></el-collapse-item></el-collapse>
        <div v-if="sandboxReport" class="quality-report" style="margin-top:10px;background:#f0f9ff;padding:8px;border-radius:4px">
          <div>校验 {{ sandboxReport.row_count }} 行 · {{ sandboxReport.quality_gate.passed ? '通过，可申请只读历史数据接入评审' : '未通过，需修复数据' }} · 违规 {{ sandboxReport.quality_gate.total_violations }}</div>
          <div>时间对齐：有效时间戳 {{ sandboxReport.time_alignment.valid_timestamp_count }} · 小时级对齐 {{ (sandboxReport.time_alignment.hourly_alignment_rate * 100).toFixed(1) }}%</div>
        </div>
      </el-card>
      <el-row :gutter="16">
        <el-col :xs="24" :sm="12" v-for="scenario in industrialScenarios" :key="scenario.key">
          <el-card style="margin-bottom:16px">
            <template #header><b>{{ scenario.name }}</b></template>
            <p class="sc-desc">{{ scenario.description }}</p>
            <div style="font-size:12px;color:#666;margin:8px 0">拓扑资产：{{ (scenario.topology?.assets || []).map(asset => asset.id).join(' · ') }}</div>
            <div style="font-size:12px;color:#666;margin:8px 0">数据契约：{{ scenario.contract?.points?.length || 0 }} 个标准点位（单位、量程、频率、质量规则）</div>
            <div style="font-size:12px;color:#666;margin:8px 0">输出：{{ (scenario.outputs || []).join(' · ') }}</div>
            <el-collapse style="margin:8px 0">
              <el-collapse-item title="资产与约束参数" name="configuration">
                <el-form label-position="top" size="small" class="config-form">
                  <el-form-item v-for="(_, key) in scenario.configuration" :key="key" :label="String(key)">
                    <el-input-number v-model="scenario.configuration[key]" :step="parameterStep(String(key))" controls-position="right" />
                  </el-form-item>
                </el-form>
              </el-collapse-item>
            </el-collapse>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <el-input-number v-model="scenario.hours" :min="24" :max="8760" :step="24" size="small" style="width:130px"/>
              <el-button type="primary" size="small" :loading="scenario.loading" @click="generateIndustrial(scenario)">生成回放</el-button>
              <el-button v-if="scenario.run" size="small" @click="previewIndustrial(scenario)">预览</el-button>
              <el-button v-if="scenario.run" size="small" @click="evaluateIndustrial(scenario)">质量/约束评估</el-button>
              <el-button v-if="scenario.run" size="small" @click="validateIndustrialContract(scenario)">契约校验</el-button>
              <el-button v-if="scenario.run" size="small" @click="analyzeIndustrial(scenario)">算法/策略分析</el-button>
              <el-button v-if="scenario.run" size="small" @click="benchmarkIndustrial(scenario)">基准门槛</el-button>
              <el-select v-if="scenario.run" v-model="scenario.baselineRunId" placeholder="选择历史基线" size="small" style="width:180px">
                <el-option v-for="item in historicalRuns.filter(item => item.scenario_key === scenario.key && item.run_id !== scenario.run.run_id)" :key="item.run_id" :label="item.run_id + ' · ' + item.generated_at.slice(0, 16)" :value="item.run_id" />
              </el-select>
              <el-button v-if="scenario.run" size="small" :disabled="!scenario.baselineRunId" @click="compareIndustrial(scenario)">与历史基线对比</el-button>
              <el-button v-if="scenario.comparison" size="small" type="success" @click="saveStrategyExperiment(scenario)">保存策略实验</el-button>
              <el-button v-if="scenario.run" size="small" @click="downloadIndustrial(scenario, 'timeseries')">时序CSV</el-button>
              <el-button v-if="scenario.run?.event_count" size="small" @click="downloadIndustrial(scenario, 'events')">回执CSV</el-button>
            </div>
            <div v-if="scenario.run" class="quality-report" style="margin-top:10px">
              <div>生成 {{ scenario.run.record_count }} 条，约束通过率 {{ (scenario.run.constraint_pass_rate * 100).toFixed(1) }}%，回执 {{ scenario.run.event_count }} 条</div>
              <div style="color:#666">真值标签：{{ scenario.run.truth_labels.join('、') }}</div>
            </div>
            <div v-if="scenario.evaluation" class="quality-report" style="margin-top:8px;background:#f0f9ff;padding:8px;border-radius:4px">
              <div>数据质量：{{ scenario.evaluation.data_quality.row_count }} 行 · 缺失率 {{ (scenario.evaluation.data_quality.missing_rate * 100).toFixed(2) }}% · 异常真值 {{ scenario.evaluation.data_quality.anomaly_truth_count }}</div>
              <div>约束审计：通过率 {{ (scenario.evaluation.constraint_audit.pass_rate * 100).toFixed(2) }}% · 违规 {{ scenario.evaluation.constraint_audit.violations }} · 回执 {{ scenario.evaluation.feedback_audit.event_count }}</div>
              <div v-if="scenario.evaluation.simulation_baseline.simulated_saving_rate != null">模拟成本改善：{{ (scenario.evaluation.simulation_baseline.simulated_saving_rate * 100).toFixed(2) }}%（仅虚拟情景）</div>
            </div>
            <div v-if="scenario.contractValidation" class="quality-report" style="margin-top:8px;background:#f6ffed;padding:8px;border-radius:4px">
              <div>数据契约：预测 {{ scenario.contractValidation.applicability.prediction_ready ? '可用' : '不可用' }} · 优化 {{ scenario.contractValidation.applicability.optimization_ready ? '可用' : '不可用' }} · 控制不可用（离线仿真）</div>
              <div>点位检查 {{ scenario.contractValidation.checks.length }} 项 · 失败 {{ scenario.contractValidation.failures.length }} 项</div>
            </div>
            <div v-if="scenario.benchmark" class="quality-report" style="margin-top:8px;background:#eef6ff;padding:8px;border-radius:4px">
              <div>验证基准：{{ scenario.benchmark.passed ? '通过' : '未通过' }} · 分析版本 {{ scenario.benchmark.profile.id }}</div>
              <div v-for="check in scenario.benchmark.checks" :key="check.name">{{ check.name }}：{{ check.passed ? '通过' : '未通过' }}（{{ check.value }}）</div>
            </div>
            <div v-if="scenario.analysis" class="quality-report" style="margin-top:8px;background:#fff7e6;padding:8px;border-radius:4px">
              <div>分析：{{ scenario.analysis.analysis_type }}（{{ scenario.analysis.profile?.id }}）· 准入：{{ scenario.admission?.decision || '待评估' }}</div>
              <div v-if="scenario.analysis.strategy">模拟成本改善：{{ (scenario.analysis.strategy.simulated_saving_rate * 100).toFixed(2) }}% · 购电削减 {{ (scenario.analysis.renewable_and_storage_effect.grid_import_reduction_rate * 100).toFixed(2) }}%</div>
              <div v-if="scenario.analysis.anomaly_evaluation">异常检测：精确率 {{ (scenario.analysis.anomaly_evaluation.precision * 100).toFixed(1) }}% · 召回率 {{ (scenario.analysis.anomaly_evaluation.recall * 100).toFixed(1) }}%</div>
              <div style="color:#666">仅可进入人工审批建议评审，不可下发控制。</div>
            </div>
            <div v-if="scenario.comparison" class="quality-report" style="margin-top:8px;background:#f9f0ff;padding:8px;border-radius:4px">
              <div>历史对比：{{ scenario.comparison.recommendation.candidate_for_manual_review ? '可进入人工评审' : '仅保留对比记录' }}</div>
              <div v-for="item in scenario.comparison.metric_deltas" :key="item.key">{{ item.label }}：基线 {{ item.baseline }} → 候选 {{ item.candidate }} · 变化 {{ item.delta }} · {{ item.improved ? '改善' : '未改善' }}</div>
              <div style="color:#666">{{ scenario.comparison.recommendation.reason }}；不允许自动控制下发。</div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </el-tab-pane>
  </el-tabs>

  <!-- Preview Dialog -->
  <el-dialog v-model="previewVisible" title="数据预览" width="85%">
    <el-table :data="previewData" max-height="500" border size="small">
      <el-table-column v-for="col in previewCols" :key="col" :prop="col" :label="col" width="140" show-overflow-tooltip/>
    </el-table>
  </el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { get, post, postForm } from '@/api/legacy'
import { ElMessage } from 'element-plus'

const activeTab = ref('industrial')

// Steel scenarios
const steelScenarios = ref<any[]>([])
const energyTypes = ref<any[]>([
  {key:'pv_power',icon:'⚡',label:'光伏发电',desc:'光伏功率时序',count:500,stored:0,loading:false},
  {key:'storage_operation',icon:'🔋',label:'储能运行',desc:'充放电时序',count:500,stored:0,loading:false},
  {key:'equipment',icon:'🏗️',label:'设备数据',desc:'运行状态',count:200,stored:0,loading:false},
  {key:'energy_consumption',icon:'📊',label:'能耗数据',desc:'消费记录',count:500,stored:0,loading:false},
  {key:'carbon_emission',icon:'🌿',label:'碳排放',desc:'核算数据',count:500,stored:0,loading:false},
  {key:'load_forecasting',icon:'📈',label:'负荷预测',desc:'预测数据',count:500,stored:0,loading:false},
])
const industrialScenarios = ref<any[]>([])
const historicalRuns = ref<any[]>([])
const sandboxScenarioKey = ref('park_multi_energy')
const sandboxFile = ref<File | null>(null)
const sandboxReport = ref<any>(null)
const sandboxMappingJson = ref('{}')
const sandboxUnitsJson = ref('{}')

const stats = reactive([
  {k:'steel_types',v:0,l:'钢铁场景'},
  {k:'energy_types',v:0,l:'能源场景'},
  {k:'total',v:0,l:'已生成总数'},
  {k:'quality',v:'-',l:'质量评级'},
])

const genAllLoading = ref(false)
const previewVisible = ref(false)
const previewData = ref<any[]>([])
const previewCols = ref<string[]>([])

onMounted(async () => {
  await loadSteelScenarios()
  await loadIndustrialScenarios()
  await refreshStats()
})

async function loadIndustrialScenarios() {
  try {
    const [r, runs] = await Promise.all([get('/virtual-data/industrial-scenarios'), get('/virtual-data/industrial-runs?limit=100')])
    historicalRuns.value = runs.items || []
    industrialScenarios.value = await Promise.all((r.items || []).map(async (s: any) => {
      const [config, contract] = await Promise.all([
        get('/virtual-data/industrial-scenarios/' + s.key + '/configuration'),
        get('/virtual-data/industrial-scenarios/' + s.key + '/contract')
      ])
      return { ...s, hours: 168, loading: false, run: null, evaluation: null, contractValidation: null, benchmark: null, analysis: null, admission: null, comparison: null, baselineRunId: null, contract, configuration: config.defaults || {} }
    }))
  } catch (e) { ElMessage.error('工业仿真场景加载失败') }
}

async function loadSteelScenarios() {
  try {
    const r = await get('/virtual-data/steel-scenarios')
    steelScenarios.value = (r.scenarios || []).map((s: any) => ({
      ...s, count: 500, stored: 0, loading: false, validating: false, quality: null
    }))
  } catch(e) { /* use defaults */ }
}

async function refreshStats() {
  let total = 0
  steelScenarios.value.forEach(s => { total += s.stored || 0 })
  energyTypes.value.forEach(e => { total += e.stored || 0 })
  stats.steel_types = steelScenarios.value.filter(s => s.stored > 0).length
  stats.energy_types = energyTypes.value.filter(e => e.stored > 0).length
  stats.total = total
  const allQuality = steelScenarios.value.filter(s => s.quality?.mean_deviation < 0.15).length
  stats.quality = steelScenarios.value.length > 0 ? `${allQuality}/${steelScenarios.value.length}达标` : '-'
}

async function genSteel(s: any) {
  s.loading = true
  try {
    const r = await post(`/virtual-data/steel-generate/${s.key}`, null, { n_samples: s.count, add_anomalies: true })
    s.stored = r.records || 0
    s.quality = r.quality || null
    ElMessage.success(`${s.name}: 生成 ${r.records} 条 ✅`)
    await refreshStats()
  } catch(e: any) { ElMessage.error(e?.message || '生成失败') }
  finally { s.loading = false }
}

async function validateSteel(s: any) {
  s.validating = true
  try {
    const r = await post(`/virtual-data/steel-validate/${s.key}`, null, { n_samples: s.count })
    s.quality = r.quality || null
    ElMessage.success(`质量验证完成: ${s.quality?.mean_deviation < 0.15 ? '✅达标' : '⚠️需改进'}`)
  } catch(e: any) { ElMessage.error(e?.message || '验证失败') }
  finally { s.validating = false }
}

async function viewSteel(s: any) {
  try {
    const data = await get(`/virtual-data/stored/steel_${s.key}`) || []
    previewData.value = (data || []).slice(0, 50)
    previewCols.value = previewData.value.length > 0 ? Object.keys(previewData.value[0]).slice(0, 8) : []
    previewVisible.value = true
  } catch(e) {}
}

// 下载已生成的虚拟数据(钢铁/能源通用)
function downloadData(key: string) {
  downloadWithAuth(`/api/v1/virtual-data/download/${encodeURIComponent(key)}`, key + '.csv')
}

async function genEnergy(dt: any) {
  dt.loading = true
  try {
    const r = await post(`/virtual-data/generate/${dt.key}`, null, { count: dt.count })
    dt.stored = (r?.count || dt.count)
    ElMessage.success(`${dt.label}: ${dt.stored} 条`)
    await refreshStats()
  } catch(e: any) { ElMessage.error(e?.message || '失败') }
  finally { dt.loading = false }
}

async function generateAll() {
  genAllLoading.value = true
  try {
    await post('/virtual-data/generate-all')
    ElMessage.success('全部能源数据生成完成')
    await refreshStats()
  } catch(e: any) { ElMessage.error(e?.message || '失败') }
  finally { genAllLoading.value = false }
}

async function generateIndustrial(scenario: any) {
  scenario.loading = true
  try {
    scenario.run = await post('/virtual-data/industrial-scenarios/' + scenario.key + '/generate', {
      hours: scenario.hours, configuration: scenario.configuration
    })
    scenario.evaluation = null
    scenario.contractValidation = null
    scenario.benchmark = null
    scenario.analysis = null
    scenario.admission = null
    scenario.comparison = null
    scenario.baselineRunId = null
    const runs = await get('/virtual-data/industrial-runs?limit=100')
    historicalRuns.value = runs.items || []
    ElMessage.success(scenario.name + '：已生成 ' + scenario.run.record_count + ' 条离线回放数据')
  } catch (e: any) { ElMessage.error(e?.message || '工业仿真生成失败') }
  finally { scenario.loading = false }
}
function previewIndustrial(scenario: any) {
  previewData.value = scenario.run?.sample || []
  previewCols.value = previewData.value.length ? Object.keys(previewData.value[0]) : []
  previewVisible.value = true
}
async function evaluateIndustrial(scenario: any) {
  try {
    scenario.evaluation = await get('/virtual-data/industrial-runs/' + scenario.run.run_id + '/evaluation')
    ElMessage.success('运行评估完成')
  } catch (e: any) { ElMessage.error(e?.message || '运行评估失败') }
}
async function validateIndustrialContract(scenario: any) {
  try {
    scenario.contractValidation = await get('/virtual-data/industrial-runs/' + scenario.run.run_id + '/contract-validation')
    ElMessage.success('点位数据契约校验完成')
  } catch (e: any) { ElMessage.error(e?.message || '点位数据契约校验失败') }
}
async function benchmarkIndustrial(scenario: any) {
  try {
    scenario.benchmark = await get('/virtual-data/industrial-runs/' + scenario.run.run_id + '/benchmark-gate')
    ElMessage.success(scenario.benchmark.passed ? '验证基准通过' : '验证基准未通过')
  } catch (e: any) { ElMessage.error(e?.message || '验证基准检查失败') }
}
function selectSandboxFile(file: any) {
  sandboxFile.value = file.raw || null
  sandboxReport.value = null
}
async function validateSandboxCsv() {
  if (!sandboxFile.value) return
  try {
    const form = new FormData()
    form.append('scenario_key', sandboxScenarioKey.value)
    form.append('file', sandboxFile.value)
    form.append('field_mapping_json', sandboxMappingJson.value || '{}')
    form.append('source_units_json', sandboxUnitsJson.value || '{}')
    sandboxReport.value = await postForm('/virtual-data/industrial-sandbox/csv-validation', form)
    ElMessage.success(sandboxReport.value.quality_gate.passed ? '只读 CSV 校验通过' : '只读 CSV 校验未通过')
  } catch (e: any) { ElMessage.error(e?.message || '只读 CSV 校验失败') }
}
async function analyzeIndustrial(scenario: any) {
  try {
    const base = '/virtual-data/industrial-runs/' + scenario.run.run_id
    const [analysis, admission] = await Promise.all([get(base + '/analysis'), get(base + '/admission')])
    scenario.analysis = analysis
    scenario.admission = admission
    ElMessage.success('算法/策略分析完成')
  } catch (e: any) { ElMessage.error(e?.message || '算法/策略分析失败') }
}
async function compareIndustrial(scenario: any) {
  try {
    scenario.comparison = await get('/virtual-data/industrial-run-comparison?baseline_run_id=' + encodeURIComponent(scenario.baselineRunId) + '&candidate_run_id=' + encodeURIComponent(scenario.run.run_id))
    ElMessage.success('历史回放对比完成')
  } catch (e: any) { ElMessage.error(e?.message || '历史回放对比失败') }
}
async function saveStrategyExperiment(scenario: any) {
  try {
    const experiment = await post('/virtual-data/strategy-experiments', {
      baseline_run_id: scenario.baselineRunId, candidate_run_id: scenario.run.run_id,
      name: scenario.name + ' 回放对比', review_note: '由数字孪生页面创建，等待人工评审'
    })
    ElMessage.success('策略实验已保存：' + experiment.experiment_id)
  } catch (e: any) { ElMessage.error(e?.message || '保存策略实验失败') }
}
function downloadIndustrial(scenario: any, artifact: string) {
  downloadWithAuth('/api/v1/virtual-data/industrial-runs/' + scenario.run.run_id + '/download/' + artifact, scenario.key + '_' + artifact + '.csv')
}
async function downloadWithAuth(url: string, filename: string) {
  try {
    const token = localStorage.getItem('foundation_token') || localStorage.getItem('foundation_access_token')
    const response = await fetch(url, { headers: token ? { Authorization: 'Bearer ' + token } : {} })
    if (!response.ok) throw new Error('下载失败：' + response.status)
    const anchor = document.createElement('a')
    anchor.href = URL.createObjectURL(await response.blob()); anchor.download = filename; anchor.click()
    URL.revokeObjectURL(anchor.href)
  } catch (e: any) { ElMessage.error(e?.message || '下载失败') }
}
function parameterStep(key: string) {
  return key.includes('coefficient') ? 0.00000000001 : key.includes('tariff') ? 0.01 : 1
}
</script>

<style scoped>
.page{padding:24px}.page-header{margin-bottom:20px}h1{font-size:24px;margin:0}
.stats{margin-bottom:16px}.sv{font-size:28px;font-weight:700;text-align:center}
.sl{font-size:13px;color:#888;text-align:center;margin-top:4px}
.scenario-card{border-left:4px solid #e2e8f0;transition:all .3s}
.scenario-card.has-data{border-left-color:#10b981}
.sc-desc{color:#666;font-size:13px}
.quality-report{font-size:12px}
.config-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 12px}.config-form :deep(.el-form-item){margin-bottom:8px}
</style>
