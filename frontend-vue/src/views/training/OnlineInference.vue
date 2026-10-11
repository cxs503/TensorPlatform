<template>
<div class="pg"><div class="hd"><h2>🌐 在线推理服务</h2><el-button type="primary" @click="showDeploy=true">+ 部署模型</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="运行中" :value="deployments.filter(d=>d.status==='running').length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="总调用" :value="totalCalls"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="平均延迟" :value="avgLatency" suffix="ms"/></el-card></el-col>
</el-row>
<el-card><el-table :data="deployments" stripe v-loading="loading">
  <el-table-column prop="id" label="ID" width="60"/>
  <el-table-column prop="model_id" label="模型ID" width="90"/>
  <el-table-column prop="model_name" label="模型名称" min-width="150"/>
  <el-table-column prop="version" label="版本" width="100"/>
  <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='running'?'success':'warning'">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column label="端点" min-width="260"><template #default="{row}"><el-tag type="info" style="cursor:pointer" @click="copyEndpoint(row)">/inference/{{row.id}} 📋</el-tag></template></el-table-column>
  <el-table-column label="cURL" width="140"><template #default="{row}"><el-button size="small" @click="showCurl(row)">查看cURL</el-button></template></el-table-column>
  <el-table-column label="测试" width="120"><template #default="{row}"><el-button size="small" type="success" @click="testInference(row)">在线测试</el-button></template></el-table-column>
  <el-table-column label="操作" width="120"><template #default="{row}"><el-button size="small" type="danger" @click="stopDeploy(row)">停止</el-button></template></el-table-column>
</el-table></el-card>

<!-- Deploy Dialog -->
<el-dialog v-model="showDeploy" title="部署模型到在线推理" width="560px">
  <el-form :model="deployForm" label-width="100px">
    <el-form-item label="选择模型"><el-select v-model="deployForm.model_id" placeholder="按ID或名称选择模型" filterable style="width:100%"><el-option v-for="m in modelList" :key="m.id" :label="'#'+m.id+' '+m.name" :value="m.id"/></el-select></el-form-item>
    <el-form-item label="版本号"><el-input v-model="deployForm.version" placeholder="1.0.0"/></el-form-item>
    <el-alert type="info" :closable="false" style="margin-bottom:12px" title="部署后将自动生成 REST API 端点，可通过 HTTP POST 调用"/>
  </el-form>
  <template #footer><el-button @click="showDeploy=false">取消</el-button><el-button type="primary" @click="doDeploy" :loading="deployLoading">部署上线</el-button></template>
</el-dialog>

<!-- cURL Dialog -->
<el-dialog v-model="showCurlDialog" title="API 调用示例" width="620px">
  <el-alert type="success" :closable="false" title="POST {{curlEndpoint}}" style="margin-bottom:12px"/>
  <el-input type="textarea" :rows="10" :model-value="curlExample" readonly style="font-family:monospace;font-size:13px"/>
  <template #footer><el-button type="primary" @click="copyCurl">复制cURL命令</el-button></template>
</el-dialog>

<!-- Test Dialog -->
<el-dialog v-model="showTestDialog" title="在线推理测试" width="760px">
  <el-form label-width="100px">
    <el-form-item label="输入特征"><el-input type="textarea" :rows="6" v-model="testInput" placeholder='{"feature1": 1.5, "feature2": 3.2, ...}'/></el-form-item>
  </el-form>
  <el-alert v-if="testError" :title="testError" type="error" :closable="false" show-icon/>
  <div v-if="testResult" class="inference-report">
    <div class="report-title">
      <div>
        <div class="report-kicker">ONLINE INFERENCE REPORT</div>
        <h3>在线推理结果报表</h3>
      </div>
      <el-tag type="success" effect="dark" size="large">推理成功</el-tag>
    </div>

    <el-descriptions :column="3" border size="small" class="report-meta">
      <el-descriptions-item label="模型名称">{{ testDeployment?.model_name || '-' }}</el-descriptions-item>
      <el-descriptions-item label="模型 ID">{{ testResult.model_id || testDeployment?.model_id || '-' }}</el-descriptions-item>
      <el-descriptions-item label="部署 ID">{{ testDeployment?.id || '-' }}</el-descriptions-item>
      <el-descriptions-item label="模型版本">{{ testDeployment?.model_version || testDeployment?.version || '-' }}</el-descriptions-item>
      <el-descriptions-item label="推理耗时">{{ formatLatency(testResult.inference_time_ms) }}</el-descriptions-item>
      <el-descriptions-item label="数据漂移">
        <el-tag :type="driftDetected ? 'danger' : 'success'" size="small">{{ driftDetected ? '检测到漂移' : '未检测到漂移' }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="完成时间" :span="3">{{ reportTime }}</el-descriptions-item>
    </el-descriptions>

    <el-row :gutter="16" class="report-section">
      <el-col :span="12">
        <div class="section-label">输入特征</div>
        <el-table :data="inputRows" border size="small" max-height="220">
          <el-table-column prop="name" label="特征名称" min-width="150"/>
          <el-table-column prop="value" label="输入值" min-width="110"/>
        </el-table>
      </el-col>
      <el-col :span="12">
        <div class="section-label">预测结果</div>
        <el-table :data="predictionRows" border size="small" max-height="220">
          <el-table-column prop="index" label="序号" width="70"/>
          <el-table-column label="预测值" min-width="140">
            <template #default="{row}"><span class="prediction-value">{{ formatValue(row.value) }}</span></template>
          </el-table-column>
        </el-table>
      </el-col>
    </el-row>

    <el-collapse class="raw-result">
      <el-collapse-item title="查看原始返回数据" name="raw">
        <pre>{{ JSON.stringify(testResult, null, 2) }}</pre>
      </el-collapse-item>
    </el-collapse>
  </div>
  <template #footer><el-button @click="showTestDialog=false">关闭</el-button><el-button type="primary" @click="doTest" :loading="testLoading">发送请求</el-button></template>
</el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage, ElMessageBox } from 'element-plus'; import { get, post } from '@/api/legacy';
const loading=ref(false),deployLoading=ref(false),testLoading=ref(false);
const deployments=ref<any[]>([]),modelList=ref<any[]>([]);
const showDeploy=ref(false),showCurlDialog=ref(false),showTestDialog=ref(false);
const deployForm=ref({model_id:null as number|null});
const curlEndpoint=ref(''),curlExample=ref('');
const testInput=ref('{}'),testResult=ref<any>(null),testError=ref(''),testDeployId=ref<number|null>(null);
const testDeployment=ref<any>(null),testFeatureNames=ref<string[]>([]),testRequest=ref<any>(null),reportTime=ref('');
const totalCalls=computed(()=>deployments.value.reduce((a,d)=>a+(d.calls||0),0));
const avgLatency=computed(()=>{const d=deployments.value.filter(d=>d.latency); return d.length?(d.reduce((a,d)=>a+(d.latency||0),0)/d.length).toFixed(1):'0'});
const inputRows=computed(()=>{
  const features=testRequest.value?.features;
  if(Array.isArray(features)) return features.map((value:any,index:number)=>({name:testFeatureNames.value[index]||`特征 ${index+1}`,value}));
  if(features&&typeof features==='object') return Object.entries(features).map(([name,value])=>({name,value}));
  if(testRequest.value&&typeof testRequest.value==='object') return Object.entries(testRequest.value).map(([name,value])=>({name,value}));
  return [];
});
const predictionRows=computed(()=>{
  const source=testResult.value?.predictions ?? testResult.value?.prediction ?? testResult.value?.result;
  const values=Array.isArray(source)?source:[source];
  return values.filter((value:any)=>value!==undefined&&value!==null).map((value:any,index:number)=>({
    index:value&&typeof value==='object'&&value.index!==undefined?value.index:index,
    value:value&&typeof value==='object'&&value.prediction!==undefined?value.prediction:value,
  }));
});
const driftDetected=computed(()=>Boolean(testResult.value?.drift?.drift_detected ?? testResult.value?.drift_detected));

onMounted(async()=>{await loadData()});
async function loadData(){loading.value=true;try{const d=await get('/deployments');deployments.value=Array.isArray(d)?d:(d.items||d.deployments||[])}catch{}finally{loading.value=false};try{const m=await get('/models');modelList.value=Array.isArray(m)?m:(m.items||m.models||[])}catch{}}
async function doDeploy(){if(!deployForm.value.model_id){ElMessage.warning('请选择模型');return}deployLoading.value=true;try{const r=await post('/deployments',deployForm.value);ElMessage.success('部署成功！端点: /inference/'+r.id);showDeploy.value=false;await loadData()}catch(e:any){ElMessage.error('部署失败: '+(e?.response?.data?.detail||e?.message))}finally{deployLoading.value=false}}
function showCurl(row:any){curlEndpoint.value='/inference/'+row.id;curlExample.value='curl -X POST http://127.0.0.1:18080/api/v1/inference/'+row.id+' \\\n  -H "Content-Type: application/json" \\\n  -d \'{"features": [1.5, 3.2, 0.8, ...]}\'';showCurlDialog.value=true}
function copyEndpoint(row:any){navigator.clipboard.writeText('/api/v1/inference/'+row.id);ElMessage.success('端点已复制')}
function copyCurl(){navigator.clipboard.writeText(curlExample.value);ElMessage.success('cURL已复制')}
async function testInference(row:any){
  testDeployId.value=row.id;testDeployment.value=row;testInput.value='{}';testResult.value=null;testError.value='';testRequest.value=null;testFeatureNames.value=[];showTestDialog.value=true;
  if(row.model_id){try{const lineage=await get('/inference/lineage/model/'+row.model_id);testFeatureNames.value=lineage?.feature_cols||[]}catch{}}
}
async function doTest(){
  if(!testDeployId.value)return;
  testLoading.value=true;testError.value='';testResult.value=null;
  try{
    const input=JSON.parse(testInput.value);testRequest.value=input;
    testResult.value=await post('/inference/'+testDeployId.value,input);
    reportTime.value=new Date().toLocaleString('zh-CN',{hour12:false});
    ElMessage.success('推理成功');
  }catch(e:any){
    testError.value='推理失败：'+(e?.response?.data?.detail||e?.message||'未知错误');
    ElMessage.error('推理失败');
  }finally{testLoading.value=false}
}
function formatLatency(value:any){return value===undefined||value===null?'-':`${Number(value).toFixed(2)} ms`}
function formatValue(value:any){return typeof value==='number'?Number(value.toPrecision(10)).toString():JSON.stringify(value)}
async function stopDeploy(row:any){try{await ElMessageBox.confirm('确定要停止部署 #'+row.id+' 吗？','确认',{type:'warning'});await post('/deployments/'+row.id+'/stop',{});ElMessage.success('已停止');await loadData()}catch{}}
</script>
<style scoped>
.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}
.inference-report{margin-top:16px;padding:20px;border:1px solid #d9e2ef;border-radius:10px;background:linear-gradient(180deg,#f8fbff 0,#fff 150px);box-shadow:0 6px 20px rgba(31,61,100,.08)}
.report-title{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px}.report-title h3{margin:3px 0 0;color:#17233d;font-size:20px}.report-kicker{color:#718096;font-size:11px;letter-spacing:1.2px}.report-meta{margin-bottom:18px}.report-section{margin-top:4px}.section-label{margin-bottom:8px;color:#344054;font-size:13px;font-weight:600}.prediction-value{color:#0969da;font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}.raw-result{margin-top:16px}.raw-result pre{max-height:220px;margin:0;padding:12px;overflow:auto;border-radius:6px;background:#111827;color:#d1e7ff;font-size:12px;line-height:1.55}
</style>
