<template>
  <div class="ab-page">
    <PageHeader title="过渡测试" desc="已部署模型小流量试用 · 稳定分组 · B失败自动由A兜底 · 真实结果回填" />

    <el-alert
      title="模型A是当前稳定基线，模型B是候选新模型。每个设备/对象会稳定进入同一组；B推理失败时自动改由A处理。"
      type="info"
      :closable="false"
      show-icon
      class="intro"
    />

    <el-row :gutter="16" class="stats">
      <el-col :xs="12" :sm="6"><el-card shadow="never"><el-statistic title="实验总数" :value="exps.length" /></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card shadow="never"><el-statistic title="运行中" :value="runningCount" /></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card shadow="never"><el-statistic title="实验请求" :value="requestCount" /></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card shadow="never"><el-statistic title="B失败回退" :value="fallbackCount" /></el-card></el-col>
    </el-row>

    <el-row :gutter="20">
      <el-col :xs="24" :lg="9">
        <el-card shadow="never" class="section">
          <template #header><span>创建交接实验</span></template>
          <el-alert
            v-if="!deployments.length"
            title="当前没有可用于实验的运行中部署，请先在部署管理中部署两个兼容模型。"
            type="warning"
            :closable="false"
            class="form-alert"
          />
          <el-form :model="form" label-width="92px">
            <el-form-item label="实验名称" required>
              <el-input v-model="form.name" placeholder="例如：生产设备温度模型交接验证" maxlength="256" />
            </el-form-item>
            <el-form-item label="稳定模型A" required>
              <el-select v-model="form.deployment_a_id" filterable style="width:100%" placeholder="选择正在运行的部署">
                <el-option v-for="item in deployments" :key="item.deployment_id" :label="deploymentLabel(item)" :value="item.deployment_id" />
              </el-select>
            </el-form-item>
            <el-form-item label="候选模型B" required>
              <el-select v-model="form.deployment_b_id" filterable style="width:100%" placeholder="选择不同的候选部署">
                <el-option
                  v-for="item in deployments"
                  :key="item.deployment_id"
                  :label="deploymentLabel(item)"
                  :value="item.deployment_id"
                  :disabled="Boolean(selectedA && selectedA.model_id === item.model_id)"
                />
              </el-select>
            </el-form-item>
            <el-form-item v-if="selectedA || selectedB" label="输入检查">
              <div class="compatibility">
                <span>A：{{ featureCount(selectedA) }}</span>
                <span>B：{{ featureCount(selectedB) }}</span>
                <el-tag v-if="compatible === true" size="small" type="success">兼容</el-tag>
                <el-tag v-else-if="compatible === false" size="small" type="danger">不兼容</el-tag>
              </div>
            </el-form-item>
            <el-form-item label="评估指标">
              <el-select v-model="form.metric" style="width:100%" @change="sampleResult=null">
                <el-option label="准确率（越高越好）" value="accuracy" />
                <el-option label="R²（越高越好）" value="r2" />
                <el-option label="F1（越高越好）" value="f1" />
                <el-option label="MAE（越低越好）" value="mae" />
              </el-select>
            </el-form-item>
            <el-form-item label="流量分配">
              <div class="split-row">
                <strong class="a-color">A {{ form.traffic_split_a }}%</strong>
                <el-slider v-model="form.traffic_split_a" :min="10" :max="90" :step="5" />
                <strong class="b-color">B {{ 100-form.traffic_split_a }}%</strong>
              </div>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="creating" :disabled="!deployments.length" @click="createExp">创建实验</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" class="section">
          <template #header><span>建议样本量</span></template>
          <el-form label-width="110px" size="small">
            <el-form-item v-if="isRateMetric" label="当前基线">
              <el-input-number v-model="sampleForm.baseline" :min="0.01" :max="0.98" :step="0.01" />
            </el-form-item>
            <el-form-item :label="isRateMetric ? '最小提升' : '标准化效应'">
              <el-input-number v-model="sampleForm.min_effect" :min="0.01" :max="0.9" :step="0.01" />
            </el-form-item>
            <el-form-item>
              <el-button :loading="calcLoading" @click="calcSample">计算</el-button>
            </el-form-item>
          </el-form>
          <div v-if="sampleResult" class="sample-result">
            <strong>{{ sampleResult.required_per_variant }}</strong> 条/组，共 {{ sampleResult.total }} 条
            <small>{{ sampleResult.method }}</small>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="15">
        <el-card shadow="never" class="section">
          <template #header>
            <div class="card-header"><span>实验列表</span><el-button text type="primary" @click="loadAll">刷新</el-button></div>
          </template>
          <el-table :data="exps" v-loading="loading" stripe @row-click="showDetail">
            <el-table-column prop="name" label="实验" min-width="150" show-overflow-tooltip />
            <el-table-column label="模型A / 模型B" min-width="250">
              <template #default="{row}">
                <div class="model-line a-color">A：#{{ row.model_a_id }} {{ row.model_a_name || '未知模型' }} · 部署 #{{ row.deployment_a_id || '-' }}</div>
                <div class="model-line b-color">B：#{{ row.model_b_id }} {{ row.model_b_name || '未知模型' }} · 部署 #{{ row.deployment_b_id || '-' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="流量" width="90"><template #default="{row}">{{ row.traffic_split_a }}/{{ 100-row.traffic_split_a }}</template></el-table-column>
            <el-table-column label="请求/反馈" width="105"><template #default="{row}">{{ row.request_count }}/{{ row.feedback_count }}</template></el-table-column>
            <el-table-column prop="fallback_count" label="回退" width="65" />
            <el-table-column label="状态" width="90"><template #default="{row}"><el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="胜者" width="65"><template #default="{row}"><el-tag v-if="row.winner" type="success" size="small">{{ row.winner }}</el-tag><span v-else>-</span></template></el-table-column>
            <el-table-column label="操作" width="145" fixed="right">
              <template #default="{row}">
                <el-button v-if="row.status==='created'" size="small" type="success" @click.stop="startExp(row)">启动</el-button>
                <el-button v-if="row.status==='running'" size="small" type="danger" @click.stop="endExp(row)">结束</el-button>
                <el-button size="small" @click.stop="showDetail(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!loading && !exps.length" description="暂无A/B实验" />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="detailVisible" :title="`实验详情：${detailExp?.name || ''}`" width="1120px" top="4vh">
      <div v-if="detailExp" v-loading="detailLoading">
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item label="实验编号">{{ detailExp.experiment_id }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusText(detailExp.status) }}</el-descriptions-item>
          <el-descriptions-item label="指标">{{ metricLabel(detailExp.metric) }}</el-descriptions-item>
          <el-descriptions-item label="流量">A {{ detailExp.traffic_split_a }}% / B {{ 100-detailExp.traffic_split_a }}%</el-descriptions-item>
        </el-descriptions>

        <el-card v-if="detailExp.status==='running'" shadow="never" class="run-card">
          <template #header><span>发送真实实验请求</span></template>
          <el-alert title="对象编号用于稳定分组：同一个设备或对象在本实验中始终进入同一组。" type="info" :closable="false" class="form-alert" />
          <el-form label-width="95px">
            <el-form-item label="对象编号"><el-input v-model="runForm.subject_id" placeholder="例如：blast-furnace-1" /></el-form-item>
            <el-form-item label="特征数组">
              <el-input v-model="runForm.features" type="textarea" :rows="3" :placeholder="featurePlaceholder" />
            </el-form-item>
            <el-form-item><el-button type="primary" :loading="predicting" @click="sendPrediction">发送请求</el-button></el-form-item>
          </el-form>
          <el-result v-if="lastPrediction" icon="success" title="推理完成" class="compact-result">
            <template #sub-title>
              分配 {{ lastPrediction.assigned_variant }}，实际由 {{ lastPrediction.served_variant }} 处理；预测值 {{ formatValue(lastPrediction.prediction) }}；请求号 {{ lastPrediction.request_id }}
            </template>
            <template #extra><el-tag v-if="lastPrediction.fallback_used" type="warning">B失败，已由A兜底</el-tag></template>
          </el-result>
        </el-card>

        <el-row :gutter="12" class="analysis-row">
          <el-col :span="4"><div class="kpi"><span>A指标</span><strong>{{ formatMetric(analysis?.mean_a) }}</strong><small>n={{ analysis?.n_a || 0 }}</small></div></el-col>
          <el-col :span="4"><div class="kpi"><span>B指标</span><strong>{{ formatMetric(analysis?.mean_b) }}</strong><small>n={{ analysis?.n_b || 0 }}</small></div></el-col>
          <el-col :span="4"><div class="kpi"><span>p值</span><strong>{{ formatMetric(analysis?.p_value) }}</strong><small>{{ analysis?.significant ? '差异显著' : '尚无显著差异' }}</small></div></el-col>
          <el-col :span="4"><div class="kpi"><span>实验请求</span><strong>{{ analysis?.request_count || 0 }}</strong><small>反馈 {{ analysis?.feedback_count || 0 }}</small></div></el-col>
          <el-col :span="4"><div class="kpi"><span>B失败回退</span><strong>{{ analysis?.fallback_count || 0 }}</strong><small>{{ percent(analysis?.fallback_rate) }}</small></div></el-col>
          <el-col :span="4"><div class="kpi winner"><span>当前结论</span><strong>{{ analysis?.best_variant || '-' }}</strong><small>{{ analysis?.significant ? '统计显著' : '继续收集' }}</small></div></el-col>
        </el-row>
        <el-alert v-if="analysis?.error" :title="analysis.error" type="warning" :closable="false" class="analysis-alert" />
        <el-alert v-else-if="analysis?.significant" :title="`差异显著，当前胜者为模型${analysis.best_variant}，相对改善 ${analysis.improvement_pct ?? '-'}%`" type="success" :closable="false" class="analysis-alert" />

        <el-card shadow="never" class="requests-card">
          <template #header><div class="card-header"><span>请求与真实结果</span><el-button text type="primary" @click="refreshDetail">刷新</el-button></div></template>
          <el-table :data="requests" size="small" max-height="360" stripe>
            <el-table-column prop="request_id" label="请求编号" min-width="155" show-overflow-tooltip />
            <el-table-column prop="subject_id" label="对象" min-width="120" show-overflow-tooltip />
            <el-table-column label="分配/执行" width="95"><template #default="{row}">{{ row.assigned_variant }} / {{ row.served_variant || '-' }}</template></el-table-column>
            <el-table-column prop="served_model_id" label="模型ID" width="80" />
            <el-table-column label="预测值" width="100"><template #default="{row}">{{ formatValue(row.prediction) }}</template></el-table-column>
            <el-table-column label="真实值" min-width="180">
              <template #default="{row}">
                <span v-if="row.feedback_at">{{ formatValue(row.actual_value) }}</span>
                <div v-else-if="row.status==='success' && detailExp.status==='running'" class="feedback-cell">
                  <el-input v-model="feedbackValues[row.request_id]" size="small" placeholder="真实值" />
                  <el-button size="small" type="primary" :loading="feedbackLoading===row.request_id" @click="submitFeedback(row)">回填</el-button>
                </div>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="误差/得分" width="95"><template #default="{row}">{{ formatMetric(row.metric_value) }}</template></el-table-column>
            <el-table-column label="回退" width="65"><template #default="{row}"><el-tag v-if="row.fallback_used" type="warning" size="small">是</el-tag><span v-else>否</span></template></el-table-column>
            <el-table-column prop="latency_ms" label="耗时ms" width="85" />
            <el-table-column label="状态" width="75"><template #default="{row}"><el-tag :type="row.status==='success'?'success':'danger'" size="small">{{ row.status==='success'?'成功':'失败' }}</el-tag></template></el-table-column>
          </el-table>
          <el-empty v-if="!requests.length" description="实验启动后，通过上方入口或实验API发送请求" :image-size="60" />
        </el-card>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { get, post } from '@/api/legacy'

const exps = ref<any[]>([])
const deployments = ref<any[]>([])
const loading = ref(false)
const creating = ref(false)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detailExp = ref<any>(null)
const analysis = ref<any>(null)
const requests = ref<any[]>([])
const predicting = ref(false)
const lastPrediction = ref<any>(null)
const feedbackValues = reactive<Record<string, string>>({})
const feedbackLoading = ref('')
const calcLoading = ref(false)
const sampleResult = ref<any>(null)

const form = reactive({ name: '', deployment_a_id: null as number|null, deployment_b_id: null as number|null, metric: 'mae', traffic_split_a: 90 })
const sampleForm = reactive({ baseline: 0.8, min_effect: 0.1 })
const runForm = reactive({ subject_id: 'device-001', features: '' })

const selectedA = computed(() => deployments.value.find(item => item.deployment_id === form.deployment_a_id))
const selectedB = computed(() => deployments.value.find(item => item.deployment_id === form.deployment_b_id))
const compatible = computed(() => {
  if (!selectedA.value || !selectedB.value) return null
  const a = selectedA.value.n_features_in, b = selectedB.value.n_features_in
  if (a == null || b == null) return true
  return Number(a) === Number(b)
})
const isRateMetric = computed(() => ['accuracy', 'f1'].includes(form.metric))
const runningCount = computed(() => exps.value.filter(item => item.status === 'running').length)
const requestCount = computed(() => exps.value.reduce((sum, item) => sum + Number(item.request_count || 0), 0))
const fallbackCount = computed(() => exps.value.reduce((sum, item) => sum + Number(item.fallback_count || 0), 0))
const featurePlaceholder = computed(() => {
  const expDeployment = deployments.value.find(item => item.deployment_id === detailExp.value?.deployment_a_id)
  const count = Number(expDeployment?.n_features_in || 0)
  return count ? `输入 ${count} 个数值，例如：[${Array.from({length: Math.min(count, 6)}, (_, i) => i + 1).join(', ')}${count > 6 ? ', ...' : ''}]` : '例如：[1.2, 3.4, 5.6]'
})

function deploymentLabel(item: any) { return `模型 #${item.model_id} ${item.model_name} · 部署 #${item.deployment_id} · ${featureCount(item)}` }
function featureCount(item: any) { return item?.n_features_in != null ? `${item.n_features_in}个特征` : '特征数待运行时校验' }
function statusText(status: string) { return ({created:'待启动',running:'运行中',completed:'已完成'} as any)[status] || status }
function statusType(status: string) { return ({created:'info',running:'warning',completed:'success'} as any)[status] || 'info' }
function metricLabel(metric: string) { return ({accuracy:'准确率',r2:'R²',f1:'F1',mae:'MAE'} as any)[metric] || metric }
function formatMetric(value: any) { return value == null || value === '' ? '-' : Number(value).toFixed(4) }
function formatValue(value: any) { return value == null ? '-' : (typeof value === 'object' ? JSON.stringify(value) : String(value)) }
function percent(value: any) { return `${(Number(value || 0) * 100).toFixed(1)}%` }

async function loadAll() {
  loading.value = true
  try {
    const [experimentsResult, deploymentsResult]: any[] = await Promise.all([
      get('/abtest/experiments'),
      get('/abtest/eligible-models'),
    ])
    exps.value = Array.isArray(experimentsResult) ? experimentsResult : (experimentsResult?.items || [])
    deployments.value = deploymentsResult?.items || []
  } catch (error: any) {
    ElMessage.error(error?.message || 'A/B实验数据加载失败')
  } finally { loading.value = false }
}

async function createExp() {
  if (!form.name.trim()) { ElMessage.warning('请输入实验名称'); return }
  if (!form.deployment_a_id || !form.deployment_b_id) { ElMessage.warning('请选择两个模型部署'); return }
  if (compatible.value === false) { ElMessage.warning('两个模型输入特征数不一致'); return }
  creating.value = true
  try {
    await post('/abtest/experiments', {...form, name: form.name.trim()})
    ElMessage.success('实验已创建，确认后再启动流量分配')
    Object.assign(form, {name:'', deployment_a_id:null, deployment_b_id:null, metric:'mae', traffic_split_a:90})
    await loadAll()
  } catch (error: any) { ElMessage.error(error?.message || '创建失败') }
  finally { creating.value = false }
}

async function startExp(row: any) {
  try { await post(`/abtest/experiments/${row.experiment_id}/start`); ElMessage.success('实验已启动'); await loadAll() }
  catch (error: any) { ElMessage.error(error?.message || '启动失败') }
}

async function endExp(row: any) {
  try {
    await ElMessageBox.confirm('结束后不再接收推理请求或真实值回填，确认结束实验？', '结束实验', {type:'warning'})
    const result: any = await post(`/abtest/experiments/${row.experiment_id}/end`)
    ElMessage.success(result.winner ? `实验结束，显著胜者为模型${result.winner}` : '实验结束，当前没有统计显著的胜者')
    await loadAll()
  } catch (error: any) { if (error !== 'cancel') ElMessage.error(error?.message || '结束失败') }
}

async function showDetail(row: any) {
  detailExp.value = row
  detailVisible.value = true
  lastPrediction.value = null
  await refreshDetail()
}

async function refreshDetail() {
  if (!detailExp.value) return
  detailLoading.value = true
  try {
    const [analysisResult, requestResult]: any[] = await Promise.all([
      get(`/abtest/experiments/${detailExp.value.experiment_id}/analyze`),
      get(`/abtest/experiments/${detailExp.value.experiment_id}/requests`, {limit: 100}),
    ])
    analysis.value = analysisResult
    requests.value = requestResult?.items || []
  } catch (error: any) { ElMessage.error(error?.message || '实验详情加载失败') }
  finally { detailLoading.value = false }
}

function parseFeatures(): number[] {
  let value: any
  try { value = JSON.parse(runForm.features) } catch { throw new Error('特征必须是JSON数组，例如：[1,2,3]') }
  if (!Array.isArray(value) || !value.length || value.some(item => !Number.isFinite(Number(item)))) throw new Error('特征必须是非空数值数组')
  return value.map(Number)
}

async function sendPrediction() {
  if (!detailExp.value || !runForm.subject_id.trim()) { ElMessage.warning('请输入对象编号'); return }
  let features: number[]
  try { features = parseFeatures() } catch (error: any) { ElMessage.warning(error.message); return }
  predicting.value = true
  try {
    lastPrediction.value = await post(`/abtest/experiments/${detailExp.value.experiment_id}/predict`, {subject_id:runForm.subject_id.trim(), features})
    ElMessage.success(lastPrediction.value.fallback_used ? '推理完成，模型B失败后已由A兜底' : '实验推理完成')
    await refreshDetail()
    await loadAll()
  } catch (error: any) { ElMessage.error(error?.message || '实验推理失败') }
  finally { predicting.value = false }
}

function parseActual(raw: string): any {
  const value = String(raw ?? '').trim()
  if (!value) throw new Error('请输入真实值')
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : value
}

async function submitFeedback(row: any) {
  let actual: any
  try { actual = parseActual(feedbackValues[row.request_id]) } catch (error: any) { ElMessage.warning(error.message); return }
  feedbackLoading.value = row.request_id
  try {
    await post(`/abtest/experiments/${detailExp.value.experiment_id}/requests/${row.request_id}/feedback`, {actual_value:actual})
    delete feedbackValues[row.request_id]
    ElMessage.success('真实结果已回填，实验指标已更新')
    await refreshDetail()
    await loadAll()
  } catch (error: any) { ElMessage.error(error?.message || '真实结果回填失败') }
  finally { feedbackLoading.value = '' }
}

async function calcSample() {
  calcLoading.value = true
  try {
    sampleResult.value = await get('/abtest/sample-size', {metric:form.metric, baseline:sampleForm.baseline, min_effect:sampleForm.min_effect})
  } catch (error: any) { ElMessage.error(error?.message || '样本量计算失败') }
  finally { calcLoading.value = false }
}

onMounted(loadAll)
</script>

<style scoped>
.ab-page { padding:20px; max-width:1500px; }
.intro,.stats,.section { margin-bottom:16px; }
.form-alert { margin-bottom:14px; }
.card-header { display:flex; align-items:center; justify-content:space-between; }
.split-row { display:grid; grid-template-columns:56px 1fr 56px; align-items:center; gap:10px; width:100%; }
.split-row strong { font-size:12px; white-space:nowrap; }
.a-color { color:#2563eb; }
.b-color { color:#16a34a; }
.compatibility { display:flex; align-items:center; gap:10px; font-size:13px; }
.sample-result { padding:12px; border-radius:8px; background:#f0f9ff; color:#334155; }
.sample-result strong { color:#2563eb; font-size:22px; }
.sample-result small { display:block; margin-top:5px; color:#64748b; }
.model-line { font-size:12px; line-height:1.7; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.run-card { margin-top:16px; }
.compact-result { padding:8px 0; }
.analysis-row { margin-top:16px; }
.kpi { display:flex; min-height:105px; flex-direction:column; justify-content:center; align-items:center; gap:5px; border:1px solid #e5e7eb; border-radius:9px; background:#fafafa; }
.kpi span,.kpi small { color:#64748b; font-size:12px; }
.kpi strong { font-size:22px; color:#0f172a; }
.kpi.winner strong { color:#16a34a; }
.analysis-alert { margin-top:12px; }
.requests-card { margin-top:16px; }
.feedback-cell { display:flex; gap:6px; align-items:center; }
@media (max-width: 900px) { .analysis-row :deep(.el-col) { width:50%; max-width:50%; flex:0 0 50%; margin-bottom:8px; } }
</style>
