<template>
<div class="pg"><div class="hd"><h2>📊 评估中心</h2><el-button type="primary" @click="showEval=true">+ 新建评估</el-button></div>
<el-row :gutter="16" class="st"><el-col :span="6"><el-card><el-statistic title="评估任务" :value="tasks.length"/></el-card></el-col><el-col :span="6"><el-card><el-statistic title="完成" :value="tasks.filter(t=>t.status==='done').length"/></el-card></el-col><el-col :span="6"><el-card><el-statistic title="平均分" :value="avgScore" :precision="2"/></el-card></el-col></el-row>
<el-card><el-table :data="tasks" stripe v-loading="loading">
  <el-table-column prop="name" label="评估任务" width="180"/><el-table-column label="模型" min-width="180"><template #default="{row}">{{ modelRef(row.model_id) }}</template></el-table-column>
  <el-table-column label="数据集" min-width="180"><template #default="{row}">{{ datasetRef(row.dataset_id) }}</template></el-table-column><el-table-column prop="metrics" label="指标" min-width="180"><template #default="{row}"><el-tag v-for="m in (row.metrics||[])" :key="m" size="small" style="margin:2px">{{m}}</el-tag></template></el-table-column>
  <el-table-column prop="score" label="评分" width="80"/><el-table-column prop="status" label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='done'?'success':'warning'" size="small">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column prop="created_at" label="时间" width="160"/><el-table-column label="操作" width="100"><template #default="{row}"><el-button size="small" @click="view(row)">查看</el-button></template></el-table-column>
</el-table></el-card>
<el-dialog v-model="showEval" title="新建评估" width="520px">
  <el-form :model="evalForm" label-width="85px">
    <el-form-item label="模型"><el-select v-model="evalForm.model_id" placeholder="按ID或名称选择模型" filterable style="width:100%"><el-option v-for="m in modelList" :key="m.id" :label="`#${m.id} ${m.name}`" :value="m.id"/></el-select></el-form-item>
    <el-form-item label="数据集"><el-select v-model="evalForm.dataset_id" placeholder="按ID或名称选择数据集" filterable style="width:100%"><el-option v-for="d in datasetList" :key="d.id" :label="`#${d.id} ${d.name}`" :value="d.id"/></el-select></el-form-item>
    <el-form-item label="任务类型"><el-radio-group v-model="evalForm.task_type"><el-radio label="regression">回归</el-radio><el-radio label="classification">分类</el-radio></el-radio-group></el-form-item>
  </el-form>
  <template #footer><el-button @click="showEval=false">取消</el-button><el-button type="primary" @click="submitEval" :loading="evalLoading">开始评估</el-button></template>
</el-dialog>
</div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get, post } from '@/api/legacy';
const loading=ref(false),showEval=ref(false),tasks=ref<any[]>([]),modelList=ref<any[]>([]),datasetList=ref<any[]>([]),evalLoading=ref(false);
const evalForm=ref({model_id:null as number|null,dataset_id:null as number|null,task_type:'regression'});
const modelRef=(id:any)=>`#${id ?? '-'} ${modelList.value.find((m:any)=>Number(m.id)===Number(id))?.name||'未知模型'}`;
const datasetRef=(id:any)=>`#${id ?? '-'} ${datasetList.value.find((d:any)=>Number(d.id)===Number(id))?.name||'未知数据集'}`;
const avgScore=computed(()=>{const d=tasks.value.filter(t=>t.score!=null);return d.length?(d.reduce((a,t)=>a+(t.score||0),0)/d.length).toFixed(2):'0'});
async function loadTasks(){loading.value=true;try{const d=await get('/model-evaluation/results');tasks.value=Array.isArray(d)?d:(d.items||[])}catch(e){}finally{loading.value=false}}
async function submitEval(){if(!evalForm.value.model_id||!evalForm.value.dataset_id){ElMessage.warning('请选择模型和数据集');return}evalLoading.value=true;try{const r=await post('/model-evaluation/evaluate',evalForm.value);ElMessage.success('评估完成！');showEval.value=false;await loadTasks()}catch(e:any){ElMessage.error('评估失败: '+(e?.response?.data?.detail||e?.message||'未知错误'))}finally{evalLoading.value=false}}
onMounted(async()=>{await loadTasks();try{const m=await get('/models');modelList.value=Array.isArray(m)?m:(m.items||m.models||[])}catch(e){};try{const d=await get('/datasets');datasetList.value=Array.isArray(d)?d:(d.items||d.datasets||[])}catch(e){}});
function view(row:any){ElMessage.info(`查看 ${row.name} 详情`)}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
