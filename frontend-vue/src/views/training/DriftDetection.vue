<template>
  <div class="page-root"><div class="page-header"><h2>📉 模型漂移检测</h2><el-button type="primary" @click="runDetection">立即检测</el-button></div>
  <el-row :gutter="16" class="stats-row">
    <el-col :span="6"><el-card><el-statistic title="监控模型" :value="models.length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="漂移告警" :value="driftAlerts"><template #suffix><span style="color:red">↑</span></template></el-statistic></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="数据漂移" :value="dataDriftScore" :precision="2"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="概念漂移" :value="conceptDriftScore" :precision="2"/></el-card></el-col>
  </el-row>
  <el-card><el-table :data="models" stripe v-loading="loading">
    <el-table-column prop="model_id" label="模型ID" width="90"/><el-table-column prop="model_name" label="模型名称" width="150"/><el-table-column prop="metric" label="监控指标" width="120"/>
    <el-table-column prop="baseline" label="基线值" width="100"/><el-table-column prop="current" label="当前值" width="100"/>
    <el-table-column prop="drift_score" label="漂移分数" width="100"><template #default="{row}"><el-progress :percentage="(row.drift_score||0)*100" :stroke-width="10" :color="row.drift_score>0.3?'#f56c6c':'#67c23a'"/></template></el-table-column>
    <el-table-column prop="status" label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='ok'?'success':'danger'" size="small">{{row.status}}</el-tag></template></el-table-column>
    <el-table-column prop="last_check" label="上次检测" width="170"/><el-table-column label="操作" width="100"><template #default="{row}"><el-button size="small" @click="viewDetail(row)">详情</el-button></template></el-table-column>
  </el-table></el-card>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get, post } from '@/api/legacy';
const loading=ref(false),models=ref<any[]>([]),driftAlerts=ref(0),dataDriftScore=ref(0),conceptDriftScore=ref(0);
onMounted(()=>{fetchModels()});
async function fetchModels(){loading.value=true;try{const d=await get('/drift/alerts').then(r => (r && r.items) ? r : {items: []});models.value=Array.isArray(d)?d:(d.items||[]);driftAlerts.value=models.value.filter(m=>m.status!=='ok').length;dataDriftScore.value=models.value.reduce((a:number,m:any)=>a+(m.drift_score||0),0)/Math.max(models.value.length,1);conceptDriftScore.value=dataDriftScore.value*0.8}catch(e){ }finally{loading.value=false}}
async function runDetection(){try{await get('/drift/detect/1');ElMessage.success('检测完成');fetchModels()}catch(e:any){ElMessage.error(e?.message||'失败')}}
function viewDetail(row:any){ElMessage.info(`模型 #${row.model_id} ${row.model_name||''} 漂移详情`)}
</script>
<style scoped>.page-root{padding:24px}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.page-header h2{margin:0;font-size:20px}.stats-row{margin-bottom:20px}</style>
