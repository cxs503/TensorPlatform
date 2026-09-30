<template>
  <div class="pg">
    <div class="hd">
      <div><h2>因果分析</h2><p>真实 OLS 调整分析，任务、指标与数据来源均持久化</p></div>
      <el-button type="primary" @click="showForm=true">新建分析</el-button>
    </div>
    <el-alert class="notice" type="info" :closable="false"
      title="基础验证模式：可用固定小样本验证完整链路；选择平台数据集时不会回退到虚拟数据。" />
    <el-card>
      <template #header><span>分析任务</span></template>
      <el-table :data="results" stripe v-loading="loading">
        <el-table-column prop="id" label="任务ID" width="90" />
        <el-table-column label="变量" min-width="190">
          <template #default="{row}">{{row.config?.treatment}} → {{row.config?.outcome}}</template>
        </el-table-column>
        <el-table-column label="数据来源" width="150">
          <template #default="{row}"><el-tag size="small">{{sourceLabel(row)}}</el-tag></template>
        </el-table-column>
        <el-table-column label="因果效应" width="120">
          <template #default="{row}">{{number(row.effect)}}</template>
        </el-table-column>
        <el-table-column label="95%置信区间" width="180">
          <template #default="{row}">{{number(row.ci_lower)}} ~ {{number(row.ci_upper)}}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{row}"><el-tag :type="statusType(row.status)" size="small">{{statusText(row.status)}}</el-tag></template>
        </el-table-column>
      </el-table>
    </el-card>
    <el-dialog v-model="showForm" title="新建因果分析" width="540px">
      <el-form :model="f" label-width="110px">
        <el-form-item label="验证数据">
          <el-radio-group v-model="f.fixture_mode">
            <el-radio-button :label="true">固定小样本</el-radio-button>
            <el-radio-button :label="false">平台数据集</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="!f.fixture_mode" label="平台数据集">
          <el-select v-model="f.dataset_id" filterable style="width:100%" placeholder="选择本组织 CSV 数据集">
            <el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name}`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="处理变量"><el-input v-model="f.treatment" /></el-form-item>
        <el-form-item label="结果变量"><el-input v-model="f.outcome" /></el-form-item>
        <el-form-item label="混淆变量"><el-input v-model="f.confoundersText" placeholder="多个字段用逗号分隔" /></el-form-item>
        <el-form-item label="方法"><el-input model-value="线性回归调整（OLS）" disabled /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showForm=false">取消</el-button><el-button type="primary" @click="doAnalyze" :loading="analyzing">提交任务</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'

const loading=ref(false), analyzing=ref(false), showForm=ref(false)
const results=ref<any[]>([]), datasets=ref<any[]>([])
const f=ref({fixture_mode:true,dataset_id:null as number|null,treatment:'X',outcome:'Y',confoundersText:'Z'})
let timer:number|undefined
const terminal=(s:any)=>['completed','failed','cancelled'].includes(String(s||'').toLowerCase())
const number=(v:any)=>Number.isFinite(Number(v))?Number(v).toFixed(4):'--'
const statusText=(s:any)=>({pending:'排队中',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消'} as any)[String(s||'').toLowerCase()]||s||'未知'
const statusType=(s:any)=>({completed:'success',failed:'danger',cancelled:'info'} as any)[String(s||'').toLowerCase()]||'warning'
const sourceLabel=(row:any)=>row.data_source==='fixture'||row.config?.fixture_mode?'固定验证集':'组织数据集'

async function loadDatasets(){try{const r:any=await get('/datasets');datasets.value=(r.items||[]).filter((d:any)=>d.has_file)}catch{}}
async function refresh(){
  loading.value=true
  try{
    const r:any=await get('/causal/status')
    results.value=r.results||[]
    if(results.value.some((x:any)=>!terminal(x.status))) schedule()
  }catch{}finally{loading.value=false}
}
function schedule(){window.clearTimeout(timer);timer=window.setTimeout(refresh,2000)}
async function doAnalyze(){
  if(!f.value.fixture_mode&&!f.value.dataset_id){ElMessage.warning('请选择平台数据集');return}
  analyzing.value=true
  try{
    await post('/causal/analyze',{
      fixture_mode:f.value.fixture_mode,
      dataset_id:f.value.fixture_mode?null:f.value.dataset_id,
      treatment:f.value.treatment,
      outcome:f.value.outcome,
      confounders:f.value.confoundersText.split(',').map(x=>x.trim()).filter(Boolean),
      method:'linear_regression'
    })
    ElMessage.success('因果分析已进入持久化任务队列')
    showForm.value=false
    await refresh()
  }catch(e:any){ElMessage.error(e?.message||'提交失败')}finally{analyzing.value=false}
}
onMounted(()=>{loadDatasets();refresh()})
onBeforeUnmount(()=>window.clearTimeout(timer))
</script>

<style scoped>
.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.hd h2{margin:0;font-size:20px}.hd p{margin:6px 0 0;color:#64748b;font-size:13px}.notice{margin-bottom:16px}
</style>
