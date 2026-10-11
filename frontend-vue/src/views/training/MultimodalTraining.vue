<template>
  <div class="page">
    <PageHeader title="工业多模态融合训练" desc="传感器表格 + 巡检文本 · 配对校验 · 特征级融合 · 独立模型产物" />
    <el-alert title="通用模型 V1 使用共享 Transformer 编码器，一次训练同时完成故障诊断、异常检测、能耗预测和能效评分；原随机森林融合模型保留为基线。" type="info" :closable="false" show-icon class="mb" />
    <el-card class="mb"><div class="head"><div><b>空压机多模态故障诊断示范</b><div class="hint">生成3600条物理规律＋噪声＋弱文本数据，并自动填充训练配置。</div></div><el-button type="success" :loading="demoLoading" @click="createDemo">一键生成示范数据</el-button></div></el-card>
    <el-card class="mb"><template #header><div class="head"><div><b>开放底座工业通用模型 V1</b><div class="hint">共享 Transformer + 四任务头 · 部分标签掩码 · 自动登记模型库</div></div><el-button type="primary" :loading="generalCreating" :disabled="!form.dataset_id" @click="createGeneralJob">启动通用模型训练</el-button></div></template>
      <el-row :gutter="16"><el-col :span="8"><el-statistic title="共享骨干" value="Transformer" /></el-col><el-col :span="8"><el-statistic title="联合任务" :value="4" /></el-col><el-col :span="8"><el-statistic title="模型登记" value="自动" /></el-col></el-row>
      <el-table v-if="generalJobs.length" :data="generalJobs" size="small" class="general-table"><el-table-column prop="job_id" label="任务ID" width="160"/><el-table-column prop="name" label="名称"/><el-table-column prop="status" label="状态" width="110"/><el-table-column prop="progress" label="进度" width="90"><template #default="{row}">{{row.progress}}%</template></el-table-column><el-table-column label="模型库" width="110"><template #default="{row}"><span v-if="row.model_id">#{{row.model_id}}</span><span v-else>-</span></template></el-table-column><el-table-column label="指标" min-width="260"><template #default="{row}"><span v-for="(v,k) in (row.metrics||{})" :key="k" class="metric">{{k}}={{Number(v).toFixed(3)}}</span><span v-if="row.error" class="error">{{row.error}}</span></template></el-table-column></el-table>
    </el-card>
    <el-row :gutter="16">
      <el-col :xs="24" :lg="10">
        <el-card header="新建融合训练">
          <el-form :model="form" label-width="100px">
            <el-form-item label="任务名称"><el-input v-model="form.name" placeholder="如：空压机传感器+巡检文本故障诊断" /></el-form-item>
            <el-form-item label="数据集"><el-select v-model="form.dataset_id" filterable style="width:100%" @change="loadSchema"><el-option v-for="d in usableDatasets" :key="d.id" :label="`${d.name} (#${d.id})`" :value="d.id" /></el-select></el-form-item>
            <el-form-item label="任务类型"><el-radio-group v-model="form.task_type"><el-radio value="classification">分类</el-radio><el-radio value="regression">回归</el-radio></el-radio-group></el-form-item>
            <el-form-item label="训练模式"><el-radio-group v-model="form.fusion_mode"><el-radio value="fusion">融合</el-radio><el-radio value="sensor_only">仅传感器</el-radio><el-radio value="text_only">仅文本</el-radio></el-radio-group></el-form-item>
            <el-form-item label="传感器列"><el-select v-model="form.sensor_columns" multiple filterable style="width:100%"><el-option v-for="c in sensorCandidates" :key="c.name" :label="`${c.name} (${c.dtype})`" :value="c.name" /></el-select></el-form-item>
            <el-form-item label="巡检文本"><el-select v-model="form.text_column" style="width:100%"><el-option v-for="c in textCandidates" :key="c.name" :label="c.name" :value="c.name" /></el-select></el-form-item>
            <el-form-item label="目标列"><el-select v-model="form.target_column" filterable style="width:100%"><el-option v-for="c in columns" :key="c.name" :label="`${c.name} (${c.dtype})`" :value="c.name" /></el-select></el-form-item>
            <el-form-item label="树数量"><el-input-number v-model="form.n_estimators" :min="10" :max="1000" /></el-form-item>
            <el-form-item><el-button type="primary" :loading="creating" @click="createJob">开始后台训练</el-button></el-form-item>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="14">
        <el-card><template #header><div class="head"><span>训练任务</span><el-button size="small" @click="loadJobs">刷新</el-button></div></template>
          <el-table :data="jobs" v-loading="loading">
            <el-table-column prop="job_id" label="任务ID" width="145" />
            <el-table-column prop="name" label="名称" min-width="170" />
            <el-table-column label="模态" width="150"><template #default>传感器 + 文本</template></el-table-column>
            <el-table-column prop="fusion_mode" label="模式" width="105" />
            <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='completed'?'success':row.status==='failed'?'danger':'warning'">{{ row.status }}</el-tag></template></el-table-column>
            <el-table-column label="指标" min-width="180"><template #default="{row}"><span v-for="(v,k) in (row.metrics||{})" :key="k" class="metric">{{k}}={{Number(v).toFixed(3)}}</span><span v-if="row.error" class="error">{{row.error}}</span></template></el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const datasets=ref<any[]>([]),columns=ref<any[]>([]),jobs=ref<any[]>([])
const loading=ref(false),creating=ref(false),demoLoading=ref(false),generalCreating=ref(false),generalJobs=ref<any[]>([])
const form=reactive({name:'',dataset_id:null as number|null,task_type:'classification',fusion_mode:'fusion',sensor_columns:[] as string[],text_column:'',target_column:'',n_estimators:100,max_text_features:1000,test_size:0.2})
const usableDatasets=computed(()=>datasets.value.filter(d=>d.has_file))
const sensorCandidates=computed(()=>columns.value.filter(c=>/int|float|double|decimal/i.test(c.dtype)))
const textCandidates=computed(()=>columns.value.filter(c=>!/int|float|double|decimal/i.test(c.dtype)))
async function loadSchema(){columns.value=[];form.sensor_columns=[];form.text_column='';form.target_column='';if(!form.dataset_id)return;try{const r=await get(`/multimodal-training/datasets/${form.dataset_id}/schema`);columns.value=r.columns||[]}catch(e:any){ElMessage.error(e?.message||'数据结构读取失败')}}
async function loadJobs(){loading.value=true;try{const r=await get('/multimodal-training/jobs');jobs.value=r.items||[]}finally{loading.value=false}}
async function loadGeneralJobs(){try{const r=await get('/industrial-foundation/jobs');generalJobs.value=r.items||[]}catch{}}
async function createJob(){if(!form.name||!form.dataset_id||!form.sensor_columns.length||!form.text_column||!form.target_column){ElMessage.warning('请完整配置两种模态与目标列');return}creating.value=true;try{const r=await post('/multimodal-training/jobs',{...form});ElMessage.success(`任务 ${r.job_id} 已启动`);await loadJobs()}catch(e:any){ElMessage.error(e?.message||'任务创建失败')}finally{creating.value=false}}
async function createDemo(){demoLoading.value=true;try{const r=await post('/multimodal-training/demo/air-compressor?rows=3600',{});const ds=await get('/datasets');datasets.value=ds.items||[];form.dataset_id=r.dataset_id;await loadSchema();form.name='空压机传感器+巡检文本故障诊断';form.task_type='classification';form.fusion_mode='fusion';form.sensor_columns=['discharge_pressure','discharge_temperature','vibration_rms','power_kw','current_a','air_flow','ambient_temperature','running_hours'];form.text_column='inspection_text';form.target_column='fault_type';ElMessage.success(`示范数据集 #${r.dataset_id} 已生成并填充配置`)}catch(e:any){ElMessage.error(e?.message||'示范数据生成失败')}finally{demoLoading.value=false}}
async function createGeneralJob(){if(!form.dataset_id||!form.sensor_columns.length){ElMessage.warning('请先生成示范数据或选择传感器列');return}generalCreating.value=true;try{const r=await post('/industrial-foundation/jobs',{name:'开放底座空压机工业通用模型 V1',dataset_id:form.dataset_id,sensor_columns:form.sensor_columns,text_column:form.text_column||'inspection_text',epochs:12,hidden_dim:64,batch_size:64});ElMessage.success(`通用模型任务 ${r.job_id} 已启动`);await loadGeneralJobs()}catch(e:any){ElMessage.error(e?.message||'通用模型训练启动失败')}finally{generalCreating.value=false}}
onMounted(async()=>{try{const r=await get('/datasets');datasets.value=r.items||[]}catch{};await Promise.all([loadJobs(),loadGeneralJobs()]);window.setInterval(()=>{loadJobs();loadGeneralJobs()},5000)})
</script>
<style scoped>.page{padding:20px}.mb{margin-bottom:16px}.head{display:flex;justify-content:space-between;align-items:center}.hint{font-size:12px;color:#909399;margin-top:5px}.metric{display:inline-block;margin-right:8px}.error{color:#f56c6c}.general-table{margin-top:16px}</style>
