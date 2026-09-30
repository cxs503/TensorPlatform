<template>
  <div class="pg">
    <div class="hd"><div><h2>推荐系统</h2><p>真实矩阵分解训练、验证集 RMSE 与持久化模型推荐</p></div><el-button type="primary" @click="showTrain=true">新建训练</el-button></div>
    <el-alert class="notice" type="info" :closable="false" title="固定小样本用于验证基础设施，不冒充生产交互数据；所有推荐均来自选中的已训练模型产物。" />
    <el-card>
      <template #header><span>训练任务</span></template>
      <el-table :data="jobs" stripe v-loading="loading">
        <el-table-column prop="id" label="任务ID" width="90" />
        <el-table-column label="算法" width="140"><template #default>矩阵分解 MF</template></el-table-column>
        <el-table-column label="数据来源" width="140"><template #default="{row}">{{row.config?.fixture_mode?'固定验证集':'组织数据集'}}</template></el-table-column>
        <el-table-column label="进度" min-width="170"><template #default="{row}"><el-progress :percentage="row.progress||0" :status="lower(row.status)==='completed'?'success':undefined"/></template></el-table-column>
        <el-table-column label="验证 RMSE" width="130"><template #default="{row}">{{number(row.metrics?.rmse)}}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="statusType(row.status)" size="small">{{statusText(row.status)}}</el-tag></template></el-table-column>
      </el-table>
    </el-card>
    <el-card class="infer">
      <template #header><span>训练产物推荐测试</span></template>
      <el-form inline>
        <el-form-item label="已完成任务"><el-select v-model="rec.job_id" style="width:220px" placeholder="选择训练产物"><el-option v-for="j in completedJobs" :key="j.id" :label="'任务 '+j.id" :value="j.id"/></el-select></el-form-item>
        <el-form-item label="用户ID"><el-input v-model="rec.user" style="width:130px"/></el-form-item>
        <el-form-item label="数量"><el-input-number v-model="rec.k" :min="1" :max="20"/></el-form-item>
        <el-form-item><el-button type="primary" @click="doRecommend" :loading="rec.loading">真实推荐</el-button></el-form-item>
      </el-form>
      <el-table :data="rec.result" size="small"><el-table-column prop="item" label="推荐项"/><el-table-column prop="score" label="模型得分"><template #default="{row}">{{number(row.score)}}</template></el-table-column></el-table>
    </el-card>
    <el-dialog v-model="showTrain" title="新建矩阵分解训练" width="520px">
      <el-form :model="f" label-width="110px">
        <el-form-item label="验证数据"><el-radio-group v-model="f.fixture_mode"><el-radio-button :label="true">固定评分集</el-radio-button><el-radio-button :label="false">平台数据集</el-radio-button></el-radio-group></el-form-item>
        <el-form-item v-if="!f.fixture_mode" label="平台数据集"><el-select v-model="f.dataset_id" filterable style="width:100%"><el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name}`" :value="d.id"/></el-select></el-form-item>
        <el-form-item label="算法"><el-input model-value="矩阵分解（SGD）" disabled /></el-form-item>
        <el-form-item label="隐因子数"><el-input-number v-model="f.factors" :min="2" :max="64"/></el-form-item>
        <el-form-item label="训练轮次"><el-input-number v-model="f.epochs" :min="1" :max="200"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="showTrain=false">取消</el-button><el-button type="primary" @click="doTrain" :loading="training">提交训练</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'
const loading=ref(false),training=ref(false),showTrain=ref(false),jobs=ref<any[]>([]),datasets=ref<any[]>([])
const f=ref({algorithm:'mf',fixture_mode:true,dataset_id:null as number|null,factors:16,epochs:30})
const rec=reactive({job_id:null as number|null,user:'u0',k:5,loading:false,result:[] as any[]})
let timer:number|undefined
const lower=(v:any)=>String(v||'').toLowerCase()
const completedJobs=computed(()=>jobs.value.filter(j=>lower(j.status)==='completed'))
const terminal=(s:any)=>['completed','failed','cancelled'].includes(lower(s))
const number=(v:any)=>Number.isFinite(Number(v))?Number(v).toFixed(4):'--'
const statusText=(s:any)=>({pending:'排队中',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消'} as any)[lower(s)]||s
const statusType=(s:any)=>({completed:'success',failed:'danger',cancelled:'info'} as any)[lower(s)]||'warning'
async function loadDatasets(){try{const r:any=await get('/datasets');datasets.value=(r.items||[]).filter((d:any)=>d.has_file)}catch{}}
async function refreshJobs(){loading.value=true;try{const r:any=await get('/recsys/jobs');jobs.value=r.jobs||[];if(!rec.job_id&&completedJobs.value.length)rec.job_id=completedJobs.value[0].id;if(jobs.value.some(j=>!terminal(j.status)))schedule()}catch{}finally{loading.value=false}}
function schedule(){window.clearTimeout(timer);timer=window.setTimeout(refreshJobs,2000)}
async function doTrain(){if(!f.value.fixture_mode&&!f.value.dataset_id){ElMessage.warning('请选择平台数据集');return}training.value=true;try{await post('/recsys/train',{...f.value,dataset_id:f.value.fixture_mode?null:f.value.dataset_id});ElMessage.success('真实矩阵分解任务已入队');showTrain.value=false;await refreshJobs()}catch(e:any){ElMessage.error(e?.message||'提交失败')}finally{training.value=false}}
async function doRecommend(){if(!rec.job_id){ElMessage.warning('请选择已完成任务');return}rec.loading=true;try{const r:any=await get('/recsys/recommend',{job_id:rec.job_id,user_id:rec.user,top_k:rec.k});rec.result=r.recommendations||[]}catch(e:any){ElMessage.error(e?.message||'推荐失败')}finally{rec.loading=false}}
onMounted(()=>{loadDatasets();refreshJobs()});onBeforeUnmount(()=>window.clearTimeout(timer))
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.hd h2{margin:0;font-size:20px}.hd p{margin:6px 0 0;color:#64748b;font-size:13px}.notice{margin-bottom:16px}.infer{margin-top:16px}</style>
