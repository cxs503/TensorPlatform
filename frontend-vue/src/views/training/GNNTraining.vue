<template>
  <div class="pg">
    <div class="hd"><div><h2>图神经网络</h2><p>真实 CPU GCN 训练、测试集准确率与持久化模型推理</p></div><el-button type="primary" @click="showTrain=true">新建训练</el-button></div>
    <el-alert class="notice" type="info" :closable="false" title="基础验证仅开放已实装的 GCN 节点分类；固定小图用于验证训练链路，未提供数据时不会生成随机结果。" />
    <el-card>
      <template #header><span>训练任务</span></template>
      <el-table :data="jobs" stripe v-loading="loading">
        <el-table-column prop="id" label="任务ID" width="90"/>
        <el-table-column label="模型" width="100"><template #default>GCN</template></el-table-column>
        <el-table-column label="数据来源" width="140"><template #default="{row}">{{row.config?.fixture_mode?'固定小图':'组织 NPZ'}}</template></el-table-column>
        <el-table-column label="进度" min-width="170"><template #default="{row}"><el-progress :percentage="row.progress||0" :status="lower(row.status)==='completed'?'success':undefined"/></template></el-table-column>
        <el-table-column label="测试准确率" width="130"><template #default="{row}">{{percent(row.metrics?.test_accuracy)}}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="statusType(row.status)" size="small">{{statusText(row.status)}}</el-tag></template></el-table-column>
      </el-table>
    </el-card>
    <el-card class="infer">
      <template #header><span>训练产物节点推理</span></template>
      <el-form inline>
        <el-form-item label="已完成任务"><el-select v-model="pred.job_id" style="width:210px"><el-option v-for="j in completedJobs" :key="j.id" :label="'任务 '+j.id" :value="j.id"/></el-select></el-form-item>
        <el-form-item label="节点"><el-input v-model="pred.nodes" placeholder="node_0,node_1" style="width:230px"/></el-form-item>
        <el-form-item><el-button type="primary" @click="doPredict" :loading="pred.loading">真实推理</el-button></el-form-item>
      </el-form>
      <el-table :data="pred.result" size="small"><el-table-column prop="node" label="节点"/><el-table-column prop="class_id" label="类别"/><el-table-column label="置信度"><template #default="{row}">{{number(row.confidence)}}</template></el-table-column></el-table>
    </el-card>
    <el-dialog v-model="showTrain" title="新建 GCN 训练" width="500px">
      <el-form :model="f" label-width="110px">
        <el-form-item label="验证数据"><el-radio-group v-model="f.fixture_mode"><el-radio-button :label="true">固定小图</el-radio-button><el-radio-button :label="false">平台 NPZ</el-radio-button></el-radio-group></el-form-item>
        <el-form-item v-if="!f.fixture_mode" label="平台数据集"><el-select v-model="f.dataset_id" filterable style="width:100%"><el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name}`" :value="d.id"/></el-select></el-form-item>
        <el-form-item label="任务"><el-input model-value="节点分类" disabled/></el-form-item>
        <el-form-item label="模型"><el-input model-value="GCN" disabled/></el-form-item>
        <el-form-item label="训练轮次"><el-input-number v-model="f.epochs" :min="1" :max="200"/></el-form-item>
        <el-form-item label="隐藏维度"><el-input-number v-model="f.hidden_dim" :min="4" :max="128"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="showTrain=false">取消</el-button><el-button type="primary" @click="doTrain" :loading="training">提交训练</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/api/request'
const loading=ref(false),training=ref(false),showTrain=ref(false),jobs=ref<any[]>([]),datasets=ref<any[]>([])
const f=ref({task_type:'node_classification',model:'gcn',fixture_mode:true,dataset_id:null as number|null,epochs:60,hidden_dim:16})
const pred=reactive({job_id:null as number|null,nodes:'node_0,node_1',loading:false,result:[] as any[]})
let timer:number|undefined
const lower=(v:any)=>String(v||'').toLowerCase()
const completedJobs=computed(()=>jobs.value.filter(j=>lower(j.status)==='completed'))
const terminal=(s:any)=>['completed','failed','cancelled'].includes(lower(s))
const number=(v:any)=>Number.isFinite(Number(v))?Number(v).toFixed(4):'--'
const percent=(v:any)=>Number.isFinite(Number(v))?(Number(v)*100).toFixed(1)+'%':'--'
const statusText=(s:any)=>({pending:'排队中',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消'} as any)[lower(s)]||s
const statusType=(s:any)=>({completed:'success',failed:'danger',cancelled:'info'} as any)[lower(s)]||'warning'
async function loadDatasets(){try{const r:any=await http.get('/datasets');datasets.value=(r.items||[]).filter((d:any)=>d.has_file)}catch{}}
async function refreshJobs(){loading.value=true;try{const r:any=await http.get('/gnn/jobs');jobs.value=r.jobs||[];if(!pred.job_id&&completedJobs.value.length)pred.job_id=completedJobs.value[0].id;if(jobs.value.some(j=>!terminal(j.status)))schedule()}catch{}finally{loading.value=false}}
function schedule(){window.clearTimeout(timer);timer=window.setTimeout(refreshJobs,2000)}
async function doTrain(){if(!f.value.fixture_mode&&!f.value.dataset_id){ElMessage.warning('请选择平台 NPZ 数据集');return}training.value=true;try{await http.post('/gnn/train',{...f.value,dataset_id:f.value.fixture_mode?null:f.value.dataset_id});ElMessage.success('真实 GCN 训练已入队');showTrain.value=false;await refreshJobs()}catch(e:any){ElMessage.error(e?.message||'提交失败')}finally{training.value=false}}
async function doPredict(){if(!pred.job_id){ElMessage.warning('请选择已完成任务');return}pred.loading=true;try{const nodes=pred.nodes.split(',').map(x=>x.trim()).filter(Boolean);const r:any=await http.post('/gnn/predict',{job_id:pred.job_id,nodes});pred.result=r.predictions||[]}catch(e:any){ElMessage.error(e?.message||'推理失败')}finally{pred.loading=false}}
onMounted(()=>{loadDatasets();refreshJobs()});onBeforeUnmount(()=>window.clearTimeout(timer))
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.hd h2{margin:0;font-size:20px}.hd p{margin:6px 0 0;color:#64748b;font-size:13px}.notice{margin-bottom:16px}.infer{margin-top:16px}</style>
