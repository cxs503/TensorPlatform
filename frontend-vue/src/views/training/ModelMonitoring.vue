<template>
  <div class="mm-page">
    <PageHeader title="模型监控" desc="预测记录 · KS/PSI 漂移检测 · 性能趋势 · 告警" />
    
    <!-- 状态卡片 -->
    <el-row :gutter="16" class="stats">
      <el-col :xs="12" :sm="6"><el-card shadow="hover"><div class="sv">{{ status.total_logs }}</div><div class="sl">总记录</div></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card shadow="hover"><div class="sv" :class="status.active_alerts>0?'warn':'ok'">{{ status.active_alerts }}</div><div class="sl">漂移告警</div></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card shadow="hover"><div class="sv">{{ models.length }}</div><div class="sl">模型总数</div></el-card></el-col>
      <el-col :xs="12" :sm="6"><el-card shadow="hover"><div class="sv ok">{{ autoRefresh?'自动':'手动' }}</div><div class="sl">刷新模式</div></el-card></el-col>
    </el-row>

    <!-- 工具栏 -->
    <el-card shadow="never" class="section">
      <el-form :model="form" inline>
        <el-form-item label="模型">
          <el-select v-model="form.model_id" placeholder="选择模型" clearable filterable style="width:240px">
            <el-option v-for="m in models" :key="m.id" :label="`[${m.id}] ${m.name}`" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="loadPredictions">预测记录</el-button>
          <el-button type="warning" @click="runDriftCheck">漂移检测</el-button>
          <el-button @click="loadTrend">趋势图</el-button>
          <el-button @click="loadAlerts" type="danger" plain>告警</el-button>
        </el-form-item>
        <el-form-item>
          <el-switch v-model="autoRefresh" active-text="自动刷新" @change="toggleAutoRefresh" />
        </el-form-item>
      </el-form>
    </el-card>

    <el-row :gutter="16">
      <!-- 趋势图 -->
      <el-col :span="14">
        <el-card v-if="trendData" shadow="never" class="section">
          <template #header><span>📈 性能趋势</span></template>
          <div ref="trendChartRef" style="height:300px"></div>
        </el-card>
        <el-card v-else shadow="never" class="section">
          <el-empty description="选择模型后查看性能趋势 (R² · 漂移分)" :image-size="80" />
        </el-card>
      </el-col>

      <!-- 漂移检测结果 -->
      <el-col :span="10">
        <el-card v-if="driftResult" shadow="never" class="section">
          <template #header><span>🔬 漂移检测</span></template>
          <el-alert :type="driftResult.has_drift?'error':'success'" :closable="false"
            :title="driftResult.has_drift?'⚠ 检测到数据漂移！模型可能需要重训练':'✓ 分布稳定，未检测到显著漂移'" style="margin-bottom:12px" />
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="KS 统计量">
              <span :style="{color: driftResult.ks_statistic>0.1?'#F56C6C':'#67C23A',fontWeight:'bold'}">{{ driftResult.ks_statistic }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="PSI">
              <span :style="{color: driftResult.psi>0.2?'#F56C6C':'#67C23A',fontWeight:'bold'}">{{ driftResult.psi }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="KS 阈值">0.10</el-descriptions-item>
            <el-descriptions-item label="PSI 阈值">0.20</el-descriptions-item>
            <el-descriptions-item label="当前样本">{{ driftResult.details?.current_samples || '-' }}</el-descriptions-item>
            <el-descriptions-item label="历史样本">{{ driftResult.details?.previous_samples || '-' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <!-- 预测记录表 -->
    <el-card v-if="predictions.length" shadow="never" class="section">
      <template #header>
        <div style="display:flex;align-items:center;gap:12px">
          <span>📋 预测记录 ({{ predictions.length }})</span>
          <el-tag v-for="(v,k) in latestMetrics" :key="k" size="small" type="success">{{ k }}: {{ typeof v==='number'?v.toFixed(4):v }}</el-tag>
        </div>
      </template>
      <el-table :data="predictions" size="small" max-height="350" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="sample_count" label="样本数" width="80" />
        <el-table-column label="评估指标" min-width="220">
          <template #default="{row}">
            <el-tag v-for="(v,k) in row.metrics" :key="k" size="small" style="margin:2px" effect="plain">
              {{ k }}: {{ Number(v).toFixed(3) }}
            </el-tag>
            <span v-if="!row.metrics || Object.keys(row.metrics).length===0" style="color:#c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column label="漂移状态" width="100" align="center">
          <template #default="{row}">
            <el-tag :type="row.drift_detected?'danger':'success'" size="small" effect="dark">
              {{ row.drift_detected?'⚠ 漂移':'✓ 正常' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="漂移分" width="90" align="center">
          <template #default="{row}">{{ row.drift_score!=null?row.drift_score.toFixed(4):'-' }}</template>
        </el-table-column>
        <el-table-column label="记录时间" width="170">
          <template #default="{row}">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 告警列表 -->
    <el-card v-if="alerts.length" shadow="never" class="section">
      <template #header><span>🚨 漂移告警 ({{ alerts.length }})</span></template>
      <el-table :data="alerts" size="small" max-height="300" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="model_id" label="模型ID" width="80" />
        <el-table-column label="漂移分" width="100"><template #default="{row}">{{ row.drift_score?row.drift_score.toFixed(4):'-' }}</template></el-table-column>
        <el-table-column prop="sample_count" label="样本数" width="80" />
        <el-table-column label="时间" width="170"><template #default="{row}">{{ formatTime(row.created_at) }}</template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'
import * as echarts from 'echarts'

const status = reactive({ total_logs: 0, active_alerts: 0, status: '' })
const models = ref<any[]>([])
const predictions = ref<any[]>([])
const driftResult = ref<any>(null)
const trendData = ref<any>(null)
const alerts = ref<any[]>([])
const trendChartRef = ref<HTMLElement | null>(null)
const form = reactive({ model_id: null as number | null })
const autoRefresh = ref(false)
let refreshTimer: any = null
let chartInstance: any = null

const latestMetrics = computed(() => predictions.value[0]?.metrics || {})

async function loadAll() {
  try {
    const [s, m] = await Promise.all([get('/monitoring/status'), get('/models')])
    Object.assign(status, s)
    models.value = m.items || m || []
  } catch { /* ignore */ }
}

async function loadPredictions() {
  if (!form.model_id) { ElMessage.warning('请选择模型'); return }
  try { const r: any = await get(`/monitoring/predictions/${form.model_id}`); predictions.value = r.items || [] }
  catch { ElMessage.error('加载预测记录失败') }
}

async function runDriftCheck() {
  if (!form.model_id) { ElMessage.warning('请选择模型'); return }
  try { driftResult.value = await post('/monitoring/drift/check', { model_id: form.model_id }) }
  catch (e: any) { ElMessage.error(e?.response?.data?.detail || '数据不足，需要至少2条记录') }
}

async function loadTrend() {
  if (!form.model_id) { ElMessage.warning('请选择模型'); return }
  try {
    trendData.value = await get(`/monitoring/trend/${form.model_id}`)
    await nextTick()
    renderTrendChart()
  } catch { ElMessage.error('加载趋势数据失败') }
}

function renderTrendChart() {
  if (!trendChartRef.value || !trendData.value) return
  if (chartInstance) chartInstance.dispose()
  chartInstance = echarts.init(trendChartRef.value)
  chartInstance.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['R²', 'Drift Score'] },
    grid: { left: 50, right: 50, top: 30, bottom: 40 },
    xAxis: { type: 'category', data: trendData.value.timestamps?.map((t: string) => t?.slice(5, 16) || '') || [], axisLabel: { rotate: 45, fontSize: 10 } },
    yAxis: [
      { type: 'value', name: 'R²', min: 0, max: 1, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: 'Drift', min: 0 }
    ],
    series: [
      { name: 'R²', type: 'line', data: trendData.value.r2_scores, smooth: true, color: '#67C23A', lineStyle: { width: 2 }, itemStyle: { color: '#67C23A' }, markLine: { silent: true, data: [{ yAxis: 0.7, label: { formatter: '阈值 0.7' }, lineStyle: { color: '#E6A23C', type: 'dashed' } }] } },
      { name: 'Drift Score', type: 'line', yAxisIndex: 1, data: trendData.value.drift_scores, smooth: true, color: '#F56C6C', lineStyle: { width: 2 }, itemStyle: { color: '#F56C6C' }, markLine: { silent: true, data: [{ yAxis: 0.1, label: { formatter: 'KS阈值 0.1' }, lineStyle: { color: '#909399', type: 'dashed' } }] } }
    ]
  })
}

async function loadAlerts() {
  try { const r: any = await get('/monitoring/alerts'); alerts.value = r.items || [] }
  catch { ElMessage.error('加载告警失败') }
}

function toggleAutoRefresh(v: boolean) {
  if (v) {
    loadAll()
    refreshTimer = setInterval(() => { if (form.model_id) { loadPredictions(); loadTrend() } }, 30000)
  } else {
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null }
  }
}

function formatTime(t: string) { return t ? new Date(t).toLocaleString('zh-CN') : '-' }

onMounted(loadAll)
onUnmounted(() => { if (refreshTimer) clearInterval(refreshTimer); if (chartInstance) chartInstance.dispose() })
</script>

<style scoped>
.mm-page { max-width: 1300px; padding: 20px; }
.stats { margin-bottom: 16px; }
.sv { font-size: 28px; font-weight: 700; text-align: center; }
.sv.ok { color: #67C23A; } .sv.warn { color: #F56C6C; }
.sl { font-size: 12px; color: #909399; margin-top: 4px; text-align: center; }
.section { margin-bottom: 16px; }
</style>
