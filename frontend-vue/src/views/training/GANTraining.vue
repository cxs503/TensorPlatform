<template>
  <div class="pg">
    <div class="hd"><div><h2>生成对抗网络</h2><p>真实 PyTorch 对抗训练、真实损失与持久化生成器采样</p></div><el-button type="primary" @click="showTrain=true">新建训练</el-button></div>
    <el-alert class="notice" type="info" :closable="false" title="基础验证当前学习二维固定分布，用于证明 GAN 反向传播和模型产物链路；不宣称已具备工业图像生成能力。" />
    <el-card>
      <template #header><span>训练任务</span></template>
      <el-table :data="jobs" stripe v-loading="loading">
        <el-table-column prop="id" label="任务ID" width="90"/>
        <el-table-column label="模型" width="100"><template #default>轻量 GAN</template></el-table-column>
        <el-table-column label="数据来源" width="140"><template #default="{row}">{{row.config?.fixture_mode?'固定二维分布':'组织 NPZ'}}</template></el-table-column>
        <el-table-column label="进度" min-width="170"><template #default="{row}"><el-progress :percentage="row.progress||0" :status="lower(row.status)==='completed'?'success':undefined"/></template></el-table-column>
        <el-table-column label="生成器损失" width="130"><template #default="{row}">{{number(row.metrics?.generator_loss)}}</template></el-table-column>
        <el-table-column label="判别器损失" width="130"><template #default="{row}">{{number(row.metrics?.discriminator_loss)}}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="statusType(row.status)" size="small">{{statusText(row.status)}}</el-tag></template></el-table-column>
      </el-table>
    </el-card>
    <el-card class="infer">
      <template #header><span>已训练生成器采样</span></template>
      <el-form inline>
        <el-form-item label="已完成任务"><el-select v-model="gen.job_id" style="width:210px"><el-option v-for="j in completedJobs" :key="j.id" :label="'任务 '+j.id" :value="j.id"/></el-select></el-form-item>
        <el-form-item label="样本数"><el-input-number v-model="gen.count" :min="1" :max="16"/></el-form-item>
        <el-form-item><el-button type="primary" @click="doGenerate" :loading="gen.loading">从训练产物采样</el-button></el-form-item>
      </el-form>
      <el-table :data="sampleRows" size="small"><el-table-column prop="index" label="#" width="70"/><el-table-column prop="values" label="生成向量"/></el-table>
    </el-card>
    <el-dialog v-model="showTrain" title="新建轻量 GAN 训练" width="500px">
      <el-form :model="f" label-width="110px">
        <el-form-item label="验证数据"><el-radio-group v-model="f.fixture_mode"><el-radio-button :label="true">固定二维分布</el-radio-button><el-radio-button :label="false">平台 NPZ</el-radio-button></el-radio-group></el-form-item>
        <el-form-item v-if="!f.fixture_mode" label="平台数据集"><el-select v-model="f.dataset_id" filterable style="width:100%"><el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name}`" :value="d.id"/></el-select></el-form-item>
        <el-form-item label="模型"><el-input model-value="轻量 GAN（MLP）" disabled/></el-form-item>
        <el-form-item label="训练轮次"><el-input-number v-model="f.epochs" :min="1" :max="100"/></el-form-item>
        <el-form-item label="潜变量维度"><el-input-number v-model="f.latent_dim" :min="2" :max="64"/></el-form-item>
        <el-form-item label="隐藏维度"><el-input-number v-model="f.hidden_dim" :min="8" :max="128"/></el-form-item>
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
const f=ref({gan_type:'gan',fixture_mode:true,dataset_id:null as number|null,epochs:40,latent_dim:8,hidden_dim:32,batch_size:32})
const gen=reactive({job_id:null as number|null,count:8,loading:false,samples:[] as number[][]})
let timer:number|undefined
const lower=(v:any)=>String(v||'').toLowerCase()
const completedJobs=computed(()=>jobs.value.filter(j=>lower(j.status)==='completed'))
const sampleRows=computed(()=>gen.samples.map((values,index)=>({index:index+1,values:values.map(v=>Number(v).toFixed(5)).join(', ')})))
const terminal=(s:any)=>['completed','failed','cancelled'].includes(lower(s))
const number=(v:any)=>Number.isFinite(Number(v))?Number(v).toFixed(4):'--'
const statusText=(s:any)=>({pending:'排队中',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消'} as any)[lower(s)]||s
const statusType=(s:any)=>({completed:'success',failed:'danger',cancelled:'info'} as any)[lower(s)]||'warning'
async function loadDatasets(){try{const r:any=await get('/datasets');datasets.value=(r.items||[]).filter((d:any)=>d.has_file)}catch{}}
async function refreshJobs(){loading.value=true;try{const r:any=await get('/gan/jobs');jobs.value=r.jobs||[];if(!gen.job_id&&completedJobs.value.length)gen.job_id=completedJobs.value[0].id;if(jobs.value.some(j=>!terminal(j.status)))schedule()}catch{}finally{loading.value=false}}
function schedule(){window.clearTimeout(timer);timer=window.setTimeout(refreshJobs,2000)}
async function doTrain(){if(!f.value.fixture_mode&&!f.value.dataset_id){ElMessage.warning('请选择平台 NPZ 数据集');return}training.value=true;try{await post('/gan/train',{...f.value,dataset_id:f.value.fixture_mode?null:f.value.dataset_id});ElMessage.success('真实 GAN 训练已入队');showTrain.value=false;await refreshJobs()}catch(e:any){ElMessage.error(e?.message||'提交失败')}finally{training.value=false}}
async function doGenerate(){if(!gen.job_id){ElMessage.warning('请选择已完成任务');return}gen.loading=true;try{const r:any=await post('/gan/generate',{job_id:gen.job_id,count:gen.count,seed:42});gen.samples=r.samples||[]}catch(e:any){ElMessage.error(e?.message||'采样失败')}finally{gen.loading=false}}
onMounted(()=>{loadDatasets();refreshJobs()});onBeforeUnmount(()=>window.clearTimeout(timer))
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.hd h2{margin:0;font-size:20px}.hd p{margin:6px 0 0;color:#64748b;font-size:13px}.notice{margin-bottom:16px}.infer{margin-top:16px}</style>
