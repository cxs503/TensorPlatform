<template>
  <div class="inference-dashboard">
    <PageHeader title="推理服务" desc="模型选择 → Schema化输入 → 单次/批量推理 → 日志监控">
      <template #actions>
        <el-button @click="refreshAll"><el-icon><Refresh /></el-icon>刷新</el-button>
      </template>
    </PageHeader>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="12" :sm="6" v-for="s in stats" :key="s.key">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-icon" :style="{background:s.bg}"><el-icon :size="20"><component :is="s.icon"/></el-icon></div>
          <div class="stat-body"><div class="stat-val">{{ s.value }}</div><div class="stat-label">{{ s.label }}</div></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <!-- 左栏：推理表单 -->
      <el-col :xs="24" :lg="14">
        <el-card shadow="never">
          <template #header><span><el-icon><Cpu /></el-icon> 推理测试</span></template>

          <!-- 模型选择 -->
          <el-form label-width="100px" size="default">
            <el-form-item label="选择模型">
              <el-select v-model="selectedModelId" placeholder="请选择已训练模型" style="width:100%" @change="onModelChange" filterable>
                <el-option v-for="m in models" :key="m.id" :label="`#${m.id} ${m.name} (${m.framework} · R²=${Number(m.metrics?.r2||0).toFixed(3)})`" :value="m.id" />
              </el-select>
            </el-form-item>
          </el-form>

          <!-- 模型信息卡片 -->
          <el-descriptions v-if="currentModel" :column="3" border size="small" style="margin-bottom:16px">
            <el-descriptions-item label="模型ID">{{ currentModel.id }}</el-descriptions-item>
            <el-descriptions-item label="框架">{{ currentModel.framework }}</el-descriptions-item>
            <el-descriptions-item label="状态"><el-tag :type="currentModel.status==='READY'?'success':'warning'" size="small">{{ currentModel.status }}</el-tag></el-descriptions-item>
            <el-descriptions-item label="任务">{{ currentModel.job_id }}</el-descriptions-item>
            <el-descriptions-item label="R²">{{ currentModel.r2 }}</el-descriptions-item>
            <el-descriptions-item label="RMSE">{{ currentModel.rmse }}</el-descriptions-item>
            <el-descriptions-item label="特征数">{{ currentModel.featureCount }}</el-descriptions-item>
          </el-descriptions>

          <!-- 输入数据：手动JSON 或 上传JSON文件（一组数据） -->
          <div class="single-input-area">
            <el-tabs v-model="singleMode" class="single-tabs">
              <el-tab-pane label="手动输入 JSON" name="json">
                <el-input v-model="jsonInput" type="textarea" :rows="4"
                  placeholder='一组数据，如 {"features":[1.0,2.0,3.0,4.0]}' />
              </el-tab-pane>
              <el-tab-pane label="上传 JSON 文件" name="file">
                <el-upload drag :auto-upload="false" :on-change="onSingleFileChange" accept=".json" :limit="1">
                  <el-icon :size="36"><UploadFilled /></el-icon>
                  <div>拖拽或点击上传 JSON 文件（一组数据）</div>
                  <template #tip><div class="el-upload__tip">格式：{"features":[1,2,3,4]}</div></template>
                </el-upload>
                <div v-if="singleFile" style="margin-top:8px;color:#909399">已选: {{ singleFile.name }}</div>
              </el-tab-pane>
            </el-tabs>
            <div style="margin-top:8px;display:flex;align-items:center;gap:10px">
              <span style="white-space:nowrap">推理次数</span>
              <el-input-number v-model="inferTimes" :min="1" :max="100" :step="1" controls-position="right" style="width:140px" />
              <span class="hint">对同一组数据重复推理 N 次，看结果稳定性与平均延迟</span>
            </div>
          </div>

          <!-- 操作按钮 -->
          <div style="margin-top:12px">
            <el-button type="primary" @click="doInference" :loading="inferring" :disabled="!selectedModelId">
              <el-icon><Connection /></el-icon> 执行推理
            </el-button>
          </div>

          <!-- 结果 -->
          <div v-if="inferResult" class="result-box">
            <el-alert :type="inferResult.error?'error':'success'" :closable="false">
              <template #title>
                <span v-if="inferResult.error">错误: {{ inferResult.error }}</span>
                <span v-else>共 {{ inferResult.times }} 次推理 · 平均延迟: {{ inferResult.avg_latency?.toFixed(1) }}ms</span>
              </template>
            </el-alert>
            <el-table v-if="inferResult.predictions" size="small" style="margin-top:8px" max-height="240"
              :data="inferResult.predictions.map((p:any,i:number)=>({ idx:i+1, pred:p, lat:inferResult.latencies[i] }))">
              <el-table-column label="#" width="60" prop="idx" />
              <el-table-column label="预测值"><template #default="{row}">{{ formatPred(row.pred) }}</template></el-table-column>
              <el-table-column label="延迟(ms)" width="110" prop="lat" />
            </el-table>
          </div>
        </el-card>

        <!-- 批量推理 -->
        <el-card shadow="never" style="margin-top:16px">
          <template #header><span><el-icon><Files /></el-icon> 批量推理</span></template>
          <el-tabs v-model="batchMode">
            <el-tab-pane label="贴入JSON" name="json">
              <el-input v-model="batchJsonInput" type="textarea" :rows="4" placeholder='{"features":[[1,2,3],[4,5,6]]}' />
            </el-tab-pane>
            <el-tab-pane label="上传文件" name="file">
              <el-upload drag :auto-upload="false" :on-change="onFileChange" accept=".json,.csv" :limit="1">
                <el-icon :size="40"><UploadFilled /></el-icon>
                <div>拖拽或点击上传 JSON 或 CSV 文件</div>
                <template #tip><div class="el-upload__tip">JSON：{"features":[[1,2,3],[4,5,6]]}；CSV：首行表头，后续每行一组特征</div></template>
              </el-upload>
              <div v-if="batchFile" style="margin-top:8px;color:#909399">已选: {{ batchFile.name }} ({{ (batchFile.size/1024).toFixed(1) }}KB)</div>
            </el-tab-pane>
            <el-tab-pane label="选择已有数据集" name="dataset">
              <el-select
                v-model="batchDatasetId"
                filterable
                clearable
                placeholder="输入数据集 ID 或名称搜索"
                style="width:100%"
              >
                <el-option
                  v-for="dataset in datasets"
                  :key="dataset.id"
                  :label="`#${dataset.id} ${dataset.name}（${dataset.row_count || 0} 行）`"
                  :value="dataset.id"
                />
              </el-select>
              <div class="dataset-tip">
                直接使用数据集管理中的全部数据，系统会按当前模型的训练记录自动匹配特征列。
              </div>
            </el-tab-pane>
          </el-tabs>
          <el-button type="primary" @click="doBatchInference" :loading="batchInferring" :disabled="!selectedModelId" style="margin-top:12px">
            开始批量推理
          </el-button>
          <div v-if="batchResult" class="result-box">
            <el-alert type="success" :closable="false">
              <template #title>完成 {{ batchResult.results?.length || batchResult.count || batchResult.total || 0 }} 条预测 | 漂移: {{ batchResult.drift_count || 0 }}条 | 耗时: {{ batchResult.inference_time_ms || '-' }}ms</template>
            </el-alert>
            <div v-if="batchResult.result_download_url" style="margin-top:10px">
              <el-button type="primary" plain @click="downloadBatchResult">下载完整结果文件（{{ batchResult.output_format?.toUpperCase() || 'JSON' }}）</el-button>
            </div>
            <el-table v-if="batchResult.results" :data="batchResult.results.slice(0,20)" size="small" style="margin-top:8px" max-height="300">
              <el-table-column prop="index" label="#" width="50" />
              <el-table-column label="预测值"><template #default="{row}">{{ formatPred(row.prediction) }}</template></el-table-column>
              <el-table-column label="漂移" width="60"><template #default="{row}"><el-tag :type="row.drifted?'warning':'success'" size="small">{{ row.drifted?'是':'否' }}</el-tag></template></el-table-column>
            </el-table>
          </div>
        </el-card>
      </el-col>

      <!-- 右栏：日志+端点 -->
      <el-col :xs="24" :lg="10">
        <!-- 推理日志 -->
        <el-card shadow="never">
          <template #header><span><el-icon><Document /></el-icon> 推理日志</span></template>
          <el-table :data="inferenceLogs" size="small" max-height="350" v-loading="logsLoading">
            <el-table-column prop="id" label="ID" width="50" />
            <el-table-column prop="model_id" label="模型ID" width="80" />
            <el-table-column prop="dataset_id" label="数据集ID" width="90">
              <template #default="{row}">{{ row.dataset_id || '-' }}</template>
            </el-table-column>
            <el-table-column label="预测值" min-width="100" show-overflow-tooltip>
              <template #default="{row}">{{ formatPred(row.predictions) }}</template>
            </el-table-column>
            <el-table-column prop="inference_time_ms" label="耗时" width="70">
              <template #default="{row}">{{ row.inference_time_ms }}ms</template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="150">
              <template #default="{row}">{{ (row.created_at||'').slice(0,19) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!logsLoading && inferenceLogs.length===0" description="暂无推理记录" :image-size="60" />
        </el-card>

        <!-- 部署端点 -->
        <el-card shadow="never" style="margin-top:16px">
          <template #header>
            <span><el-icon><Position /></el-icon> 运行中端点</span>
            <el-button text size="small" style="float:right" @click="$router.push('/deploy/deployment')">部署管理 →</el-button>
          </template>
          <el-table :data="runningEndpoints" size="small" max-height="250" v-loading="epLoading">
            <el-table-column prop="model_id" label="模型ID" width="80" />
            <el-table-column prop="model_name" label="模型名称" show-overflow-tooltip />
            <el-table-column label="端点" min-width="180" show-overflow-tooltip>
              <template #default="{row}"><code class="ep-code">{{ row.endpoint }}</code></template>
            </el-table-column>
            <el-table-column label="操作" width="60">
              <template #default="{row}">
                <el-button size="small" @click="quickTest(row)">测试</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!epLoading && runningEndpoints.length===0" description="暂无运行端点" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 监控报表 -->
    <el-row :gutter="16" style="margin-top:16px">
      <el-col :span="8">
        <el-card shadow="never">
          <template #header><span>QPS (每分钟请求数)</span></template>
          <div ref="qpsChart" style="height:240px"></div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <template #header><span>推理延迟</span></template>
          <div ref="latencyChart" style="height:240px"></div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <template #header><span>错误率</span></template>
          <div ref="errorChart" style="height:240px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 快速测试弹窗 -->
    <el-dialog v-model="testVisible" title="端点快速测试" width="500px">
      <template v-if="testEp">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="端点">{{ testEp.endpoint }}</el-descriptions-item>
          <el-descriptions-item label="模型">#{{ testEp.model_id }} {{ testEp.model_name || '未知模型' }}</el-descriptions-item>
        </el-descriptions>
        <el-input v-model="quickTestInput" type="textarea" :rows="3" placeholder="[[1,2,3,4,5]]" style="margin:12px 0" />
        <el-button type="primary" @click="doQuickTest" :loading="quickTesting">执行</el-button>
        <div v-if="quickTestResult" class="result-box">
          <el-alert :type="quickTestResult.error?'error':'success'" :closable="false">
            <template #title>{{ quickTestResult.error || '预测: '+JSON.stringify(quickTestResult.predictions || quickTestResult.prediction) }}</template>
          </el-alert>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Cpu, Connection, Files, Document, Position, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { downloadFile, get, post } from '@/api/legacy'
import * as echarts from 'echarts'
import PageHeader from '@/components/PageHeader.vue'

// ===== 模型列表 =====
const models = ref<any[]>([])
const selectedModelId = ref<number | null>(null)
const currentModel = ref<any>(null)
const featureValues = ref<number[]>([])
const jsonInput = ref('{"features":[1,2,3,4,5,6,7,8]}')
const singleMode = ref<'json' | 'file'>('json')
const singleFile = ref<any>(null)
const inferTimes = ref(1)
function onSingleFileChange(file: any) { singleFile.value = file.raw || null }

async function loadModels() {
  try {
    const res = await get('/models')
    const items = res?.items || res?.models || []
    // 为每个模型获取详细信息
    const enriched = await Promise.all(items.map(async (m: any) => {
      try {
        const lineage = await get(`/inference/lineage/model/${m.id}`)
        // feature_cols 可能是字符串数组 ["a","b"] 或对象数组;统一规范化为 {name,std}
        const rawFeats: any[] = lineage?.feature_cols || lineage?.feature_names || []
        const features = rawFeats.map((f: any, i: number) =>
          typeof f === 'object' && f !== null
            ? { name: f.name || `特征${i + 1}`, std: f.std, mean: f.mean }
            : { name: String(f) || `特征${i + 1}`, std: null, mean: null }
        )
        return {
          ...m,
          metrics: lineage?.metrics || {},
          r2: (lineage?.metrics?.r2 != null ? Number(lineage.metrics.r2).toFixed(3) : '-'),
          rmse: (lineage?.metrics?.rmse != null ? Number(lineage.metrics.rmse).toFixed(1) : '-'),
          job_id: lineage?.job_id || '-',
          features,
          featureCount: features.length,
          target_col: lineage?.target_col || '-',
          baseline: null,
        }
      } catch { return { ...m, features: [], r2:'-', rmse:'-', job_id:'-', featureCount:0 } }
    }))
    // 保留所有 READY 模型(即便 lineage 没拿到特征,也允许用 JSON 输入兜底)
    models.value = enriched.filter((m: any) => m.features?.length > 0 || m.status === 'READY')
  } catch (e) { console.error('加载模型失败', e) }
}

function onModelChange(id: number | null) {
  currentModel.value = models.value.find(m => m.id === id) || null
  if (currentModel.value?.features?.length) {
    featureValues.value = currentModel.value.features.map(() => 1.0)
  } else {
    featureValues.value = []
  }
}

// ===== 推理 =====
const inferring = ref(false)
const inferResult = ref<any>(null)

async function doInference() {
  if (!selectedModelId.value) return
  inferring.value = true; inferResult.value = null
  try {
    let raw: any
    if (singleMode.value === 'file') {
      if (!singleFile.value) { ElMessage.warning('请上传 JSON 文件'); return }
      const text = await singleFile.value.text()
      try { raw = JSON.parse(text) }
      catch { ElMessage.error('JSON 文件格式有误：属性名需双引号。示例：{"features":[1,2,3,4]}'); return }
    } else {
      try { raw = JSON.parse(jsonInput.value) }
      catch { ElMessage.error('JSON 格式有误：属性名需双引号。示例：{"features":[1,2,3,4]}'); return }
    }
    const features = raw.features || raw.data || raw
    const times = Math.max(1, Math.min(100, Number(inferTimes.value) || 1))
    const predictions: any[] = []
    const latencies: number[] = []
    for (let i = 0; i < times; i++) {
      const res = await post(`/inference/model/${selectedModelId.value}`, { features })
      const preds = res.predictions || res.prediction
      const predVal = Array.isArray(preds)
        ? (preds[0]?.prediction != null ? preds[0].prediction : preds[0])
        : preds
      predictions.push(predVal)
      latencies.push(Number(res.inference_time_ms || res.latency_ms || 0))
    }
    inferResult.value = {
      predictions,
      latencies,
      avg_latency: latencies.reduce((a: number, b: number) => a + b, 0) / latencies.length,
      times,
    }
    ElMessage.success(`完成 ${times} 次推理`)
    await loadLogs()
  } catch (e: any) {
    inferResult.value = { error: e?.response?.data?.detail || e.message }
    ElMessage.error('推理失败: ' + (e?.response?.data?.detail || e.message))
  } finally { inferring.value = false }
}

// ===== 批量推理 =====
const batchMode = ref('json')
const batchJsonInput = ref('{"features":[[1,2,3,4,5,6,7,8],[2,3,4,5,6,7,8,9]]}')
const batchFile = ref<any>(null)
const batchDatasetId = ref<number | null>(null)
const datasets = ref<any[]>([])
const batchInferring = ref(false)
const batchResult = ref<any>(null)

function onFileChange(file: any) { batchFile.value = file.raw }

async function loadDatasets() {
  try {
    const res = await get('/datasets')
    datasets.value = (res?.items || res || []).filter((dataset: any) => dataset.has_file)
  } catch {
    datasets.value = []
  }
}

async function downloadBatchResult() {
  if (!batchResult.value?.result_download_url) return
  try {
    await downloadFile(batchResult.value.result_download_url, `batch_inference_${selectedModelId.value}_${batchResult.value.id || 'result'}.${batchResult.value.output_format || 'json'}`)
  } catch (e: any) {
    ElMessage.error('结果文件下载失败: ' + (e?.message || '未知错误'))
  }
}

async function doBatchInference() {
  if (!selectedModelId.value) return
  batchInferring.value = true; batchResult.value = null
  try {
    let body: any
    if (batchMode.value === 'json') {
      try {
        body = JSON.parse(batchJsonInput.value)
      } catch {
        ElMessage.error('JSON 格式有误：属性名必须用双引号、整体为合法 JSON。示例：{"features": [[1,2,3],[4,5,6]]}')
        batchInferring.value = false
        return
      }
    } else if (batchMode.value === 'dataset') {
      if (!batchDatasetId.value) {
        ElMessage.warning('请选择数据集管理中的数据集')
        batchInferring.value = false
        return
      }
      body = { dataset_id: batchDatasetId.value, name: '平台数据集批量推理' }
    } else if (batchFile.value) {
      const text = await batchFile.value.text()
      const fname = (batchFile.value.name || '').toLowerCase()
      if (fname.endsWith('.json')) {
        try {
          body = JSON.parse(text)
        } catch {
          ElMessage.error('JSON 文件格式有误：属性名需双引号。示例：{"features":[[1,2,3],[4,5,6]]}')
          batchInferring.value = false
          return
        }
      } else {
        const lines = text.trim().split('\n')
        const features = lines.slice(1).map((l: string) => l.split(',').map(Number))
        body = { features }
      }
    } else {
      ElMessage.warning('请选择文件或输入数据'); batchInferring.value = false; return
    }
    batchResult.value = await post(`/inference/model/${selectedModelId.value}/batch`, body)
    const cnt = batchResult.value?.results?.length || batchResult.value?.count || batchResult.value?.total || 0
    ElMessage.success(`完成 ${cnt} 条预测`)
  } catch (e: any) {
    ElMessage.error('批量推理失败: '+(e?.response?.data?.detail||e.message))
  } finally { batchInferring.value = false }
}

// ===== 推理日志 =====
const inferenceLogs = ref<any[]>([])
const logsLoading = ref(false)

async function loadLogs() {
  logsLoading.value = true
  try {
    const res = await get('/inference/logs?limit=50')
    inferenceLogs.value = res?.items || []
  } catch { inferenceLogs.value = [] }
  finally { logsLoading.value = false; await nextTick(); drawCharts() }
}

function formatPred(pred: any) {
  if (pred == null || pred === '') return '-'
  const f = (v: any) => {
    if (v == null) return '-'
    if (typeof v === 'object') {
      // {index, prediction} / {prediction: ...}
      return f(v.prediction != null ? v.prediction : v.value)
    }
    return (typeof v === 'number' && isFinite(v)) ? v.toFixed(4) : String(v)
  }
  if (Array.isArray(pred)) return pred.map(f).join(', ')
  return f(pred)
}

// ===== 端点 =====
const runningEndpoints = ref<any[]>([])
const epLoading = ref(false)

async function loadEndpoints() {
  epLoading.value = true
  try {
    const res = await get('/model-deployment/deploys')
    runningEndpoints.value = (res?.items || []).filter((e: any) => e.status === 'running')
  } catch { runningEndpoints.value = [] }
  finally { epLoading.value = false }
}

// ===== 快速测试 =====
const testVisible = ref(false); const testEp = ref<any>(null)
const quickTestInput = ref('[[1,2,3,4,5]]'); const quickTestResult = ref<any>(null); const quickTesting = ref(false)

// ===== 三个监控图表的 DOM ref(template 用 ref="qpsChart/latencyChart/errorChart") =====
const qpsChart = ref<HTMLElement | null>(null)
const latencyChart = ref<HTMLElement | null>(null)
const errorChart = ref<HTMLElement | null>(null)

function quickTest(row: any) { testEp.value = row; quickTestResult.value = null; testVisible.value = true }
async function doQuickTest() {
  quickTesting.value = true; quickTestResult.value = null
  try {
    let X: any
    try {
      X = JSON.parse(quickTestInput.value)
    } catch {
      quickTestResult.value = { error: 'JSON 格式有误：属性名需双引号、整体为合法 JSON。示例：[[1,2,3],[4,5,6]]' }
      return
    }
    quickTestResult.value = await post(`/model-deployment/deploys/${testEp.value.id}/predict`, { data: X })
  } catch (e: any) { quickTestResult.value = { error: e?.response?.data?.detail || e.message } }
  finally { quickTesting.value = false }
}

// ===== 统计 =====
const stats = computed(() => [
  { key:'models', label:'可用模型', value: String(models.value.length), icon:'Cpu', bg:'rgba(59,130,246,0.15)' },
  { key:'eps', label:'运行端点', value: String(runningEndpoints.value.length), icon:'Connection', bg:'rgba(16,185,129,0.15)' },
  { key:'logs', label:'推理日志', value: String(inferenceLogs.value.length), icon:'Document', bg:'rgba(245,158,11,0.15)' },
  { key:'features', label:'当前模型特征', value: currentModel.value?.featureCount ? String(currentModel.value.featureCount)+'维' : '-', icon:'DataAnalysis', bg:'rgba(139,92,246,0.15)' },
])

function refreshAll() { loadModels(); loadDatasets(); loadLogs(); loadEndpoints() }


function drawCharts(){
  const logs=[...inferenceLogs.value].reverse() // 按时间正序(旧→新)
  // ── 延迟图:最近20条推理耗时(inference_time_ms) ──
  const recent=logs.slice(-20)
  const latLabels=recent.map((l:any)=>String((l.created_at||'').slice(11,19)||l.id||''))
  const latData=recent.map((l:any)=>Number(l.inference_time_ms)||0)

  // ── QPS图:按 created_at 的"分"分组,统计每分钟请求数 ──
  const byMin:Record<string,number>={}
  for(const l of logs){
    const k=(l.created_at||'').slice(0,16) // YYYY-MM-DDTHH:MM
    if(k)byMin[k]=(byMin[k]||0)+1
  }
  const qpsKeys=Object.keys(byMin).sort().slice(-20)
  const qpsLabels=qpsKeys.map(k=>k.slice(11,16))
  const qpsData=qpsKeys.map(k=>byMin[k])

  // ── 错误率图:统计 status/error 字段;无错误数据则画 0% 基线 ──
  const errKeys=Object.keys(byMin).sort().slice(-20)
  const errLabels=errKeys.map(k=>k.slice(11,16))
  const errData=errKeys.map(k=>{
    const bucket=logs.filter((l:any)=>(l.created_at||'').slice(0,16)===k)
    const bad=bucket.filter((l:any)=>l.status&&String(l.status).toLowerCase()!=='success'&&String(l.status)!=='ok'&&String(l.status)!=='200').length
    return bucket.length?(bad/bucket.length*100):0
  })
  const hasErrorField=logs.some((l:any)=>l.status||l.error)

  const makeOpt=(title:string,labels:string[],data:number[],unit:string,color:string,extra:any={})=>({
    title:{text:title,left:'center',textStyle:{fontSize:13}},
    grid:{top:35,bottom:20,left:45,right:15},
    xAxis:{data:labels.length?labels:['-'],axisLabel:{fontSize:10}},
    yAxis:{name:unit,axisLabel:{fontSize:10}},
    series:[{type:'line',data:data.length?data:[0],smooth:true,areaStyle:{opacity:0.15},lineStyle:{color},itemStyle:{color}}],
    tooltip:{trigger:'axis'},
    ...extra
  })

  const noDataOpts=(msg:string)=>({title:{text:msg,left:'center',top:'middle',textStyle:{fontSize:12,color:'#909399',fontWeight:'normal'}},grid:{top:35,bottom:20,left:40,right:15}})

  if(qpsChart.value){
    const inst=echarts.init(qpsChart.value)
    qpsData.length
      ? inst.setOption(makeOpt('QPS(每分钟请求数)',qpsLabels,qpsData,'次',  '#409EFF'))
      : inst.setOption(noDataOpts('暂无请求量数据'))
  }
  if(latencyChart.value){
    const inst=echarts.init(latencyChart.value)
    latData.length
      ? inst.setOption(makeOpt('推理延迟',latLabels,latData,'ms','#67C23A'))
      : inst.setOption(noDataOpts('暂无延迟数据'))
  }
  if(errorChart.value){
    const inst=echarts.init(errorChart.value)
    hasErrorField&&errData.length
      ? inst.setOption(makeOpt('错误率',errLabels,errData,'%','#F56C6C'))
      : inst.setOption(noDataOpts('错误率 0%(日志无 status/error 字段)'))
  }
}

onMounted(() => { drawCharts(); loadModels(); loadDatasets(); loadLogs(); loadEndpoints() })
</script>

<style lang="scss" scoped>
.inference-dashboard {
  .stats-row { margin-bottom: 20px; }
  .stat-card {
    :deep(.el-card__body) { display:flex; align-items:center; gap:14px; padding:16px; }
    .stat-icon { width:44px; height:44px; border-radius:10px; display:flex; align-items:center; justify-content:center; color:#3b82f6; }
    .stat-body { .stat-val { font-size:22px; font-weight:700; } .stat-label { font-size:12px; color:#94a3b8; } }
  }
  .feature-inputs { background:#f8fafc; padding:14px; border-radius:8px; margin:12px 0;
    .section-title { font-weight:600; margin-bottom:10px; font-size:14px; }
    .hint { font-size:12px; color:#94a3b8; margin-top:8px; }
  }
  .result-box { margin-top:16px; }
  .dataset-tip { margin-top:8px; font-size:12px; color:#909399; line-height:1.6; }
  .ep-code { font-size:11px; background:#f5f5f5; padding:2px 6px; border-radius:3px; word-break:break-all; }
}
</style>
