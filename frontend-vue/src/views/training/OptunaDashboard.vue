<template>
<div class="pg"><div class="hd"><h2>🔍 超参优化面板</h2><el-button type="primary" @click="refreshAll">刷新数据</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="研究数" :value="studies.length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="总试验" :value="totalTrials"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="最佳值" :value="bestValue"/></el-card></el-col>
</el-row>

<el-card v-if="studies.length" style="margin-bottom:16px">
  <template #header>📊 优化趋势</template>
  <div ref="optChart" style="height:300px"/>
</el-card>

<el-card><el-table :data="studies" stripe v-loading="loading">
  <el-table-column prop="name" label="研究名称" width="220"/>
  <el-table-column label="方向" width="100"><template #default="{row}"><el-tag :type="row.direction==='minimize'?'warning':'success'">{{row.direction}}</el-tag></template></el-table-column>
  <el-table-column prop="n_trials" label="试验数" width="80"/>
  <el-table-column prop="best_value" label="最佳值" width="120"/>
  <el-table-column label="最佳参数" min-width="200"><template #default="{row}"><el-tag v-for="(v,k) in (row.best_params||{})" :key="k" size="small" style="margin:2px">{{k}}:{{v}}</el-tag></template></el-table-column>
  <el-table-column label="操作" width="200"><template #default="{row}">
    <el-button size="small" @click="showDetail(row)">参数重要性</el-button>
    <el-button size="small" type="success" @click="showHistory(row)">优化历史</el-button>
  </template></el-table-column>
</el-table></el-card>

<el-dialog v-model="showImportance" title="超参重要性" width="600px">
  <div ref="importanceChart" style="height:350px"/>
  <template #footer><el-button @click="showImportance=false">关闭</el-button></template>
</el-dialog>

<el-dialog v-model="showHistoryDlg" title="优化历史" width="700px">
  <el-table :data="historyTrials" stripe max-height="400">
    <el-table-column prop="number" label="#" width="60"/>
    <el-table-column prop="value" label="目标值" width="120"/>
    <el-table-column label="参数" min-width="300"><template #default="{row}"><el-tag v-for="(v,k) in (row.params||{})" :key="k" size="small" style="margin:2px">{{k}}:{{v}}</el-tag></template></el-table-column>
    <el-table-column label="状态" width="80"><template #default="{row}"><el-tag :type="row.state==='COMPLETE'?'success':'warning'">{{row.state}}</el-tag></template></el-table-column>
  </el-table>
  <template #footer><el-button @click="showHistoryDlg=false">关闭</el-button></template>
</el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
import * as echarts from 'echarts';
const loading=ref(false),showImportance=ref(false),showHistoryDlg=ref(false);
const studies=ref<any[]>([]),historyTrials=ref<any[]>([]);
const optChart=ref(),importanceChart=ref();
const totalTrials=computed(()=>studies.value.reduce((a,s)=>a+(s.n_trials||0),0));
const bestValue=computed(()=>{const d=studies.value.filter(s=>s.best_value!=null);return d.length?Math.min(...d.map(s=>s.best_value||0)).toFixed(4):'0'});

async function refreshAll(){loading.value=true;try{const d=await get('/hyperopt/studies');studies.value=Array.isArray(d)?d:(d.items||d.studies||[])}catch{}finally{loading.value=false};nextTick(()=>drawChart())}
function drawChart(){
  if(!optChart.value||!studies.value.length)return;
  const s=studies.value[0];
  const values=(s.intermediate_values||[]).map((v:any)=>v.value);
  const opt=echarts.init(optChart.value);
  opt.setOption({tooltip:{trigger:'axis'},xAxis:{type:'category',data:values.map((_:any,i:number)=>i)},yAxis:{type:'value'},series:[{type:'line',data:values,smooth:true,areaStyle:{opacity:0.1}}]});
}
async function showDetail(row:any){
  showImportance.value=true;
  await nextTick();
  if(!importanceChart.value)return;
  const p=row.best_params||{};
  const opt=echarts.init(importanceChart.value);
  opt.setOption({tooltip:{},xAxis:{type:'category',data:Object.keys(p)},yAxis:{type:'value'},series:[{type:'bar',data:Object.values(p).map((v:any)=>typeof v==='number'?v:1)},{type:'line',data:Object.keys(p).map((k,i)=>i+1),smooth:false,showSymbol:false}]});
}
async function showHistory(row:any){
  showHistoryDlg.value=true;
  try{const d=await get('/hyperopt/studies/'+row.id+'/trials');historyTrials.value=Array.isArray(d)?d:(d.trials||d.items||[])}catch{historyTrials.value=[]}
}
onMounted(()=>refreshAll());
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
