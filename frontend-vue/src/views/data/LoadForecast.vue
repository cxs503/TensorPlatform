<template>
  <div class="pg"><PageHeader title="📈 负荷预测" desc="电力负荷预测 · ARIMA · Prophet · LSTM" />
    <el-row :gutter="16" class="st">
      <el-col :span="6"><el-card><el-statistic title="今日峰值负荷" :value="forecast.peak" suffix="kW" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="预测谷值" :value="forecast.valley" suffix="kW" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="当前负荷" :value="forecast.current" suffix="kW" /></el-card></el-col>
      <el-col :span="6"><el-card><el-statistic title="预测精度(MAPE)" :value="forecast.mape" suffix="%" /></el-card></el-col>
    </el-row>
    <el-card>
      <template #header><span>24小时负荷预测曲线</span><el-tag v-if="dataSource" size="small" type="info" style="margin-left:8px">{{ dataSource }}</el-tag></template>
      <div ref="chartRef" style="width:100%;height:380px"></div>
      <div v-if="loadError" style="color:#F56C6C;margin-top:8px;font-size:13px">⚠ 无法加载预测数据：{{ loadError }}</div>
    </el-card>
    <el-card style="margin-top:16px"><template #header><span>预测模型配置</span></template>
      <el-form :inline="true"><el-form-item label="模型"><el-select v-model="model"><el-option v-for="m in ['Prophet','ARIMA','LSTM','XGBoost']" :key="m" :label="m" :value="m"/></el-select></el-form-item>
        <el-form-item label="预测步长"><el-input-number v-model="horizon" :min="1" :max="168"/></el-form-item>
        <el-form-item><el-button type="primary" :loading="forecasting" @click="doForecast">开始预测</el-button></el-form-item>
      </el-form>
    </el-card>
  </div>
</template>
<script setup lang="ts">import { ref, onMounted } from 'vue'; import * as echarts from 'echarts'; import { get } from '@/api/legacy';
const model=ref('Prophet'),horizon=ref(24),chartRef=ref<HTMLElement>();
let chart: echarts.ECharts | null = null;
// 真实数据默认值来自后端 /load-forecasting/summary
const forecast=ref({peak:0,valley:0,current:0,mape:0});
const dataSource=ref('');
const loadError=ref('');
const forecasting=ref(false);

function drawCurve(data: any[]) {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value)
  const hours = data.map((d:any)=>d.hour)
  const actual = data.map((d:any)=>d.actual ?? d.load_kw)
  const pred = data.map((d:any)=>d.prediction)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['实际负荷','预测负荷'] },
    xAxis: { data: hours },
    yAxis: { name: 'kW' },
    series: [
      { name:'实际负荷', data: actual, type:'line', smooth:true, areaStyle:{}, itemStyle:{color:'#6366f1'} },
      { name:'预测负荷', data: pred, type:'line', smooth:true, lineStyle:{type:'dashed',color:'#f59e0b'}, itemStyle:{color:'#f59e0b'} }
    ]
  })
}

async function loadAll() {
  loadError.value = ''
  try {
    const summary: any = await get('/load-forecasting/summary')
    forecast.value = {
      peak: summary.peak_load ?? 0,
      valley: summary.min_load ?? 0,
      current: summary.current_load ?? 0,
      mape: summary.mape ?? 0,
    }
    dataSource.value = '后端真实数据'
  } catch (e:any) {
    forecast.value = { peak:0, valley:0, current:0, mape:0 }
    loadError.value = e.message || String(e)
    dataSource.value = ''
  }
  try {
    const curve: any = await get('/load-forecasting/curve')
    const data = Array.isArray(curve) ? curve : (curve?.data || [])
    if (data.length) drawCurve(data)
  } catch (e:any) {
    if (!loadError.value) loadError.value = '预测曲线加载失败: ' + (e.message || String(e))
  }
}

async function doForecast(){
  // 真实预测由后端按所选模型/步长生成；前端仅刷新最新曲线与汇总
  forecasting.value = true
  try {
    await loadAll()
  } finally {
    forecasting.value = false
  }
}

onMounted(loadAll)
</script>
<style scoped>.pg{padding:24px}.st{margin-bottom:16px}</style>
