<template>
  <div class="analysis-page">
    <PageHeader title="智能数据分析" desc="面向数据集本身计算质量、分布、关联和时序趋势，不预设业务场景、预测目标或算法" />

    <el-alert type="info" :closable="false" show-icon class="privacy-alert">
      <template #title>隐私说明：原始数据始终在平台本地处理，大模型只接收字段类型、缺失率、分布和相关性等统计摘要，不发送任何原始数据行。</template>
    </el-alert>

    <el-card shadow="never" class="source-card">
      <el-tabs v-model="sourceMode">
        <el-tab-pane label="选择平台数据集" name="dataset">
          <el-form label-width="100px" class="source-form">
            <el-form-item label="数据集">
              <el-select v-model="datasetId" filterable clearable placeholder="输入名称或编号搜索" style="width:100%">
                <el-option v-for="item in usableDatasets" :key="item.id" :label="`#${item.id} ${item.name}`" :value="item.id">
                  <span>#{{ item.id }} {{ item.name }}</span><span class="option-meta">{{ item.row_count || 0 }} 行</span>
                </el-option>
              </el-select>
            </el-form-item>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="上传文件分析" name="upload">
          <el-upload drag :auto-upload="false" :limit="1" accept=".csv,.txt,.tsv" :on-change="onFileChange" :on-remove="onFileRemove">
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">拖入 CSV、TXT 或 TSV 文件，或<em>点击选择</em></div>
            <template #tip><div class="el-upload__tip">最大 50MB；仅用于本次分析，不会自动加入数据集管理。</div></template>
          </el-upload>
        </el-tab-pane>
      </el-tabs>

      <div class="analysis-options">
        <div><strong>分析范围</strong><span>自动分析全部字段；如识别到时间字段，将自动生成时序走势图</span></div>
        <div><strong>大模型输入</strong><span>仅发送本地计算的统计摘要，不发送数据样本</span></div>
      </div>
      <div class="run-row">
        <el-button v-if="userStore.hasPermission('datasets:analyze')" type="primary" size="large" :loading="analyzing" @click="analyze">开始智能分析（含 🤖 AI 解读）</el-button>
        <span v-if="analyzing" class="run-status">正在计算统计、生成图表并请求大模型解释，请稍候…</span>
      </div>
      <el-progress v-if="analyzing" :percentage="analysisProgress" :stroke-width="8" striped striped-flow class="progress" />
    </el-card>

    <template v-if="current">
      <el-alert v-if="current.warning" type="warning" :closable="false" :title="current.warning" class="result-warning" />
      <div class="stat-grid">
        <div class="stat"><span>数据行数</span><strong>{{ formatNumber(profile.total_rows) }}</strong></div>
        <div class="stat"><span>字段数量</span><strong>{{ profile.total_columns }}</strong></div>
        <div class="stat"><span>整体缺失率</span><strong>{{ profile.missing_rate }}%</strong></div>
        <div class="stat"><span>重复数据行</span><strong>{{ formatNumber(profile.duplicate_rows) }}</strong></div>
      </div>

      <el-card shadow="never" class="report-card">
        <template #header><div class="card-title"><span>大模型分析报告</span><el-tag effect="plain">{{ current.llm_provider || '本地摘要' }}</el-tag></div></template>
        <div class="narrative">{{ current.narrative }}</div>
      </el-card>

      <div class="chart-grid">
        <el-card v-if="hasMissing" shadow="never"><template #header>字段缺失率（%）</template><div ref="missingChart" class="chart" /></el-card>
        <el-card v-if="hasAnomalies" shadow="never"><template #header>数值异常率（IQR，%）</template><div ref="anomalyChart" class="chart" /></el-card>
        <el-card v-if="hasCorrelation" shadow="never"><template #header>数值字段相关性</template><div ref="correlationChart" class="chart tall" /></el-card>
        <el-card v-if="hasTimeSeries" shadow="never" class="wide"><template #header>时序走势（{{ profile.charts.time_series.time_column }}）</template><div ref="timeChart" class="chart tall" /></el-card>
        <template v-if="hasDistribution">
          <el-card v-for="d in distributions" :key="`dist-${d.column}`" shadow="never">
            <template #header>{{ d.column }} 分布</template>
            <div :ref="(el) => collectDist(el, d.column)" class="chart" />
          </el-card>
        </template>
        <template v-if="hasCategories">
          <el-card v-for="c in categories" :key="`cat-${c.column}`" shadow="never">
            <template #header>{{ c.column }} 分布（Top 10）</template>
            <div :ref="(el) => collectCategory(el, c.column)" class="chart" />
          </el-card>
        </template>
      </div>

      <el-card shadow="never" class="field-card">
        <template #header>字段分析明细</template>
        <el-table :data="profile.columns" stripe max-height="430">
          <el-table-column prop="name" label="字段" min-width="150" show-overflow-tooltip />
          <el-table-column prop="kind" label="类型" width="100" />
          <el-table-column prop="dtype" label="数据类型" width="120" />
          <el-table-column prop="missing_rate" label="缺失率" width="100"><template #default="{ row }">{{ row.missing_rate }}%</template></el-table-column>
          <el-table-column prop="unique_count" label="唯一值" width="100" />
          <el-table-column label="敏感字段" width="100"><template #default="{ row }"><el-tag :type="row.sensitive ? 'warning' : 'info'" size="small">{{ row.sensitive ? '是' : '否' }}</el-tag></template></el-table-column>
          <el-table-column label="均值" width="110"><template #default="{ row }">{{ row.statistics?.mean ?? '—' }}</template></el-table-column>
          <el-table-column label="异常数" width="100"><template #default="{ row }">{{ row.statistics?.anomaly_count ?? '—' }}</template></el-table-column>
        </el-table>
      </el-card>
    </template>

    <el-card shadow="never" class="history-card">
      <template #header><div class="card-title"><span>历史分析报告</span><el-button text type="primary" @click="loadHistory">刷新</el-button></div></template>
      <el-table :data="history" v-loading="historyLoading" stripe>
        <el-table-column prop="id" label="报告ID" width="90" />
        <el-table-column prop="source_name" label="数据来源" min-width="200" show-overflow-tooltip />
        <el-table-column label="数据集ID" width="100"><template #default="{ row }">{{ row.dataset_id ? `#${row.dataset_id}` : '上传文件' }}</template></el-table-column>
        <el-table-column prop="llm_provider" label="分析方式" width="120"><template #default="{ row }">{{ row.llm_provider || '本地摘要' }}</template></el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="150" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openReport(row.id)">查看</el-button><el-button v-if="userStore.hasPermission('datasets:analyze')" link type="danger" @click="removeReport(row)">删除</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!historyLoading && !history.length" description="暂无分析报告" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import PageHeader from '@/components/PageHeader.vue'
import { useUserStore } from '@/stores/user'
import { del, get, post, postForm } from '@/api/legacy'

const userStore = useUserStore()
const sourceMode = ref<'dataset' | 'upload'>('dataset')
const datasetId = ref<number | null>(null), selectedFile = ref<File | null>(null)
const datasets = ref<any[]>([]), history = ref<any[]>([]), historyLoading = ref(false)
const analyzing = ref(false), analysisProgress = ref(0), current = ref<any>(null)
let progressTimer: number | undefined

const profile = computed(() => current.value?.profile || {})
const usableDatasets = computed(() => datasets.value.filter(item => item.has_file !== false))
const distributions = computed<any[]>(() => profile.value?.charts?.distributions || [])
const categories = computed<any[]>(() => profile.value?.charts?.categories || [])
const hasMissing = computed(() => profile.value?.charts?.missing?.some((item:any) => item.value > 0))
const hasAnomalies = computed(() => profile.value?.charts?.anomalies?.length > 0)
const hasDistribution = computed(() => distributions.value.some((d:any) => d?.counts?.length))
const hasCategories = computed(() => categories.value.some((c:any) => c?.values?.length))
const hasCorrelation = computed(() => Boolean(profile.value?.charts?.correlation?.columns?.length))
const hasTimeSeries = computed(() => Boolean(profile.value?.charts?.time_series?.series?.length))

const missingChart = ref<HTMLElement>(), anomalyChart = ref<HTMLElement>()
const correlationChart = ref<HTMLElement>(), timeChart = ref<HTMLElement>()
const distRefs: Record<string, HTMLElement> = {}
const categoryRefs: Record<string, HTMLElement> = {}
const collectDist = (el: any, column: string) => { if (el) distRefs[column] = el as HTMLElement; else delete distRefs[column] }
const collectCategory = (el: any, column: string) => { if (el) categoryRefs[column] = el as HTMLElement; else delete categoryRefs[column] }
const charts: echarts.ECharts[] = []

function onFileChange(file:any) { selectedFile.value = file.raw || null }
function onFileRemove() { selectedFile.value = null }
function formatNumber(value:any) { return Number(value || 0).toLocaleString() }
function formatDate(value:string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12:false }) : '—' }
function startProgress() { analysisProgress.value = 8; progressTimer = window.setInterval(() => { if (analysisProgress.value < 88) analysisProgress.value += Math.max(1, Math.round((90 - analysisProgress.value) / 8)) }, 800) }
function stopProgress() { if (progressTimer) clearInterval(progressTimer); analysisProgress.value = 100; window.setTimeout(() => { analysisProgress.value = 0 }, 500) }

async function analyze() {
  if (sourceMode.value === 'dataset' && !datasetId.value) return ElMessage.warning('请先选择平台数据集')
  if (sourceMode.value === 'upload' && !selectedFile.value) return ElMessage.warning('请先选择上传文件')
  analyzing.value = true; startProgress()
  try {
    let result:any
    if (sourceMode.value === 'dataset') {
      result = await post(`/intelligent-analysis/datasets/${datasetId.value}/analyze`)
    } else {
      const form = new FormData(); form.append('file', selectedFile.value!)
      result = await postForm('/intelligent-analysis/upload', form)
    }
    current.value = result; await loadHistory(); await nextTick(); renderCharts(); ElMessage.success('分析报告已生成并保存')
  } catch (error:any) { ElMessage.error(error?.message || '分析失败') }
  finally { analyzing.value = false; stopProgress() }
}

function initChart(element:HTMLElement | undefined, option:any) { if (!element) return; const chart = echarts.init(element); chart.setOption(option); charts.push(chart) }
function renderCharts() {
  charts.splice(0).forEach(chart => chart.dispose())
  const data = profile.value.charts || {}
  if (hasMissing.value) initChart(missingChart.value, { tooltip:{trigger:'axis'}, grid:{left:55,right:20,bottom:80,top:20}, xAxis:{type:'category',data:data.missing.map((x:any)=>x.name),axisLabel:{rotate:40}}, yAxis:{type:'value',name:'%'}, series:[{type:'bar',data:data.missing.map((x:any)=>x.value),itemStyle:{color:'#f59e0b'}}] })
  if (hasAnomalies.value) initChart(anomalyChart.value, { tooltip:{trigger:'axis'}, grid:{left:55,right:20,bottom:80,top:20}, xAxis:{type:'category',data:data.anomalies.map((x:any)=>x.name),axisLabel:{rotate:40}}, yAxis:{type:'value',name:'%'}, series:[{type:'bar',data:data.anomalies.map((x:any)=>x.value),itemStyle:{color:'#ef4444'}}] })
  distributions.value.forEach((d:any) => {
    const el = distRefs[d.column]
    if (!el || !d.counts?.length) return
    const labels = d.counts.map((_:any, i:number) => `${d.bins[i]}~${d.bins[i+1]}`)
    initChart(el, { tooltip:{trigger:'axis'}, grid:{left:55,right:20,bottom:95,top:20}, xAxis:{type:'category', data:labels, axisLabel:{rotate:45}}, yAxis:{type:'value'}, series:[{type:'bar', data:d.counts, itemStyle:{color:'#6366f1'}}] })
  })
  categories.value.forEach((c:any) => {
    const el = categoryRefs[c.column]
    if (!el || !c.values?.length) return
    const labels = [...c.labels].reverse(), values = [...c.values].reverse()
    initChart(el, { tooltip:{trigger:'axis'}, grid:{left:10,right:30,bottom:20,top:20, containLabel:true}, xAxis:{type:'value'}, yAxis:{type:'category', data:labels, axisLabel:{width:140, overflow:'truncate'}}, series:[{type:'bar', data:values, itemStyle:{color:'#10b981'}}] })
  })
  if (hasCorrelation.value) { const c=data.correlation; const values:any[]=[]; c.matrix.forEach((row:any[],y:number)=>row.forEach((value:any,x:number)=>{if(value!=null)values.push([x,y,value])})); initChart(correlationChart.value,{tooltip:{formatter:(p:any)=>`${c.columns[p.value[1]]} × ${c.columns[p.value[0]]}<br/>相关系数：${p.value[2]}`},grid:{left:90,right:50,bottom:90,top:20},xAxis:{type:'category',data:c.columns,axisLabel:{rotate:40}},yAxis:{type:'category',data:c.columns},visualMap:{min:-1,max:1,calculable:true,orient:'horizontal',left:'center',bottom:0},series:[{type:'heatmap',data:values}]}) }
  if (hasTimeSeries.value) { const t=data.time_series; initChart(timeChart.value,{tooltip:{trigger:'axis'},legend:{top:0},grid:{left:60,right:25,bottom:65,top:40},xAxis:{type:'category',data:t.timestamps,axisLabel:{hideOverlap:true}},yAxis:{type:'value',scale:true},dataZoom:[{type:'inside'},{type:'slider',bottom:10}],series:t.series.map((s:any)=>({name:s.name,type:'line',showSymbol:false,data:s.values,connectNulls:false}))}) }
}

async function loadDatasets() { try { const result:any = await get('/datasets'); datasets.value = result.items || result || [] } catch (error:any) { ElMessage.error(error?.message || '数据集加载失败') } }
async function loadHistory() { historyLoading.value=true; try { const result:any=await get('/intelligent-analysis/reports'); history.value=result.items || [] } catch(error:any){ ElMessage.error(error?.message || '历史报告加载失败') } finally { historyLoading.value=false } }
async function openReport(id:number) { try { const result:any=await get(`/intelligent-analysis/reports/${id}`); current.value={...result,profile:typeof result.profile==='string'?JSON.parse(result.profile):result.profile}; await nextTick(); renderCharts(); window.scrollTo({top:500,behavior:'smooth'}) } catch(error:any){ ElMessage.error(error?.message || '报告加载失败') } }
async function removeReport(row:any) { try { await ElMessageBox.confirm(`确定删除报告 #${row.id}「${row.source_name}」吗？`,'删除报告',{type:'warning'}); await del(`/intelligent-analysis/reports/${row.id}`); if(current.value?.report_id===row.id || current.value?.id===row.id)current.value=null; await loadHistory(); ElMessage.success('报告已删除') } catch(error:any){ if(error!=='cancel')ElMessage.error(error?.message || '删除失败') } }
function resizeCharts(){charts.forEach(chart=>chart.resize())}
onMounted(()=>{loadDatasets();loadHistory();window.addEventListener('resize',resizeCharts)})
onBeforeUnmount(()=>{if(progressTimer)clearInterval(progressTimer);window.removeEventListener('resize',resizeCharts);charts.forEach(chart=>chart.dispose())})
</script>

<style scoped>
.analysis-page{padding:20px}.privacy-alert{margin-bottom:16px}.source-card,.report-card,.field-card,.history-card{margin-bottom:18px}.source-form{max-width:1000px}.option-meta{float:right;color:var(--el-text-color-secondary);font-size:12px}.analysis-options{display:flex;gap:28px;flex-wrap:wrap;border-top:1px solid var(--el-border-color-lighter);padding:18px 0}.analysis-options>div{display:flex;align-items:center;gap:10px}.analysis-options span{color:var(--el-text-color-secondary);font-size:12px}.run-row{display:flex;align-items:center;gap:14px}.run-status{color:var(--el-color-primary);font-size:13px}.progress{margin-top:14px}.result-warning{margin:16px 0}.stat-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0}.stat{background:var(--el-bg-color);border:1px solid var(--el-border-color-lighter);border-radius:10px;padding:18px}.stat span{display:block;color:var(--el-text-color-secondary);font-size:13px;margin-bottom:8px}.stat strong{font-size:25px;color:var(--el-text-color-primary)}.card-title{display:flex;justify-content:space-between;align-items:center}.narrative{white-space:pre-wrap;line-height:1.85;font-size:14px;color:var(--el-text-color-regular)}.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:18px}.chart-grid .wide{grid-column:1/-1}.chart{height:340px}.chart.tall{height:410px}@media(max-width:900px){.stat-grid,.chart-grid{grid-template-columns:1fr 1fr}.chart-grid .wide{grid-column:auto}}@media(max-width:600px){.stat-grid,.chart-grid{grid-template-columns:1fr}.analysis-options>div{align-items:flex-start;flex-direction:column}}
</style>
