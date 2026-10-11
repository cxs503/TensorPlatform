<template>
<div class="pg"><div class="hd"><h2>🔬 实验对比追踪</h2><el-button type="primary" @click="addExp">+ 添加实验</el-button></div>
<el-row :gutter="16" class="st"><el-col :span="4" v-for="s in stats" :key="s.k"><el-card><el-statistic :title="s.t" :value="s.v"/></el-card></el-col></el-row>
<el-card><div v-if="experiments.length===0 && !loading" class="empty-hint">📋 暂无实验数据，点击右上角"+ 添加实验"创建或进入 <a href="/model/automl" style="color:#6366f1">AutoML</a> 自动训练</div>
<el-table v-else :data="experiments" stripe @selection-change="onSelect" v-loading="loading">
  <el-table-column type="selection" width="50"/><el-table-column prop="name" label="实验名" width="180"/>
  <el-table-column prop="run_count" label="运行数" width="80"/>
  <el-table-column prop="status" label="状态" width="90"/>
  <el-table-column prop="model" label="模型" width="120"/><el-table-column prop="dataset" label="数据集" width="130"/>
  <el-table-column prop="accuracy" label="准确率" width="90"/><el-table-column prop="f1" label="F1" width="80"/>
  <el-table-column prop="auc" label="AUC" width="80"/><el-table-column prop="loss" label="Loss" width="90"/>
  <el-table-column prop="duration" label="耗时" width="90"/><el-table-column prop="created_at" label="时间" width="160"/>
</el-table></el-card>
<el-card v-if="selected.length>=2" header="📊 对比分析" style="margin-top:16px">
  <el-row :gutter="16">
    <el-col :span="8"><div ref="accChart" style="height:250px"/></el-col>
    <el-col :span="8"><div ref="lossChart" style="height:250px"/></el-col>
    <el-col :span="8"><div ref="timeChart" style="height:250px"/></el-col>
  </el-row>
  <el-table :data="selected" stripe style="margin-top:12px">
    <el-table-column prop="name" label="实验" width="180"/><el-table-column prop="accuracy" label="准确率"/><el-table-column prop="f1" label="F1"/>
    <el-table-column prop="auc" label="AUC"/><el-table-column prop="loss" label="Loss"/><el-table-column prop="duration" label="耗时"/>
    <el-table-column label="最佳" width="80"><template #default="{row}"><el-tag v-if="bestId===row.id" type="success">🏆</el-tag></template></el-table-column>
  </el-table>
</el-card></div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'; import { useRouter } from 'vue-router'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
import * as echarts from 'echarts';
const loading=ref(false),experiments=ref<any[]>([]),selected=ref<any[]>([]),accChart=ref(),lossChart=ref(),timeChart=ref();
const stats=computed(()=>[{k:'total',t:'总实验',v:experiments.value.length},{k:'best',t:'最高准确率',v:Math.max(...experiments.value.map(e=>e.accuracy||0),0).toFixed(3)},{k:'avg',t:'平均准确率',v:experiments.value.length?(experiments.value.reduce((a,e)=>a+(e.accuracy||0),0)/experiments.value.length).toFixed(3):0}]);
const bestId=computed(()=>{let b=selected.value[0];selected.value.forEach(e=>{if((e.accuracy||0)>(b?.accuracy||0))b=e});return b?.id});
onMounted(async()=>{
  loading.value=true
  try {
    const response:any = await get('/mlflow/experiments')
    const source = response.items || []
    experiments.value = await Promise.all(source.map(async (experiment:any) => {
      const runsResponse:any = await get(`/mlflow/experiments/${experiment.id}/runs`)
      const runs = runsResponse.items || []
      const best = [...runs].sort((a:any,b:any) =>
        Number(b.metrics?.accuracy ?? b.metrics?.r2 ?? -Infinity) -
        Number(a.metrics?.accuracy ?? a.metrics?.r2 ?? -Infinity)
      )[0]
      const metrics = best?.metrics || {}
      const params = best?.params || {}
      const durationMs = best?.end_time && best?.start_time ? best.end_time-best.start_time : 0
      return {
        ...experiment,
        model: params.model || params.model_name || params.algorithm || '-',
        dataset: params.dataset || params.dataset_name || params.dataset_id || '-',
        accuracy: metrics.accuracy ?? metrics.r2 ?? 0,
        f1: metrics.f1 ?? metrics.f1_score ?? 0,
        auc: metrics.auc ?? metrics.roc_auc ?? 0,
        loss: metrics.loss ?? metrics.val_loss ?? 0,
        duration: durationMs ? (durationMs/1000).toFixed(1) : '-',
        created_at: experiment.created_at ? new Date(experiment.created_at).toLocaleString('zh-CN') : '-',
      }
    }))
  } catch(e:any) {
    ElMessage.error(e?.message || 'MLflow 实验加载失败')
  } finally { loading.value=false }
});
function onSelect(v:any){selected.value=v;nextTick(()=>{drawCharts()})}
function drawCharts(){
  const names=selected.value.map(e=>e.name);
  const opt=(data:number[],title:string)=>({title:{text:title,left:'center',textStyle:{fontSize:14}},tooltip:{},xAxis:{data:names,axisLabel:{rotate:15}},yAxis:{},series:[{type:'bar',data,itemStyle:{color:'#409EFF'}}]});
  if(accChart.value)echarts.init(accChart.value).setOption(opt(selected.value.map(e=>e.accuracy||0),'准确率'));
  if(lossChart.value)echarts.init(lossChart.value).setOption(opt(selected.value.map(e=>e.loss||0),'Loss'));
  if(timeChart.value)echarts.init(timeChart.value).setOption(opt(selected.value.map(e=>parseFloat(e.duration)||0),'耗时(s)'));
}
function addExp(){ router.push('/model/automl') }
const router = useRouter();
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}.empty-hint{text-align:center;padding:40px;color:#94a3b8;font-size:15px}</style>
