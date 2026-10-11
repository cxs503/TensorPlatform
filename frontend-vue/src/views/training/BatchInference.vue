<template>
<div class="pg"><div class="hd"><h2>📦 批量推理</h2><el-button type="primary" @click="openBatchDialog">+ 新建批量任务</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="批量任务" :value="tasks.length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="已完成" :value="tasks.filter(t=>t.status==='done').length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="累计处理行数" :value="totalRows"/></el-card></el-col>
</el-row>
<el-card><el-table :data="tasks" stripe v-loading="loading" empty-text="暂无批量推理记录">
  <el-table-column prop="id" label="记录 ID" width="90"/>
  <el-table-column prop="name" label="任务名称" min-width="160"/>
  <el-table-column prop="model_id" label="模型ID" width="90"/>
  <el-table-column prop="model_name" label="模型名称" min-width="150"/>
  <el-table-column label="数据集ID" width="100"><template #default="{row}">{{ row.dataset_id || '-' }}</template></el-table-column>
  <el-table-column label="状态" width="100"><template #default="{row}"><el-tag type="success">{{row.status==='done'?'已完成':row.status}}</el-tag></template></el-table-column>
  <el-table-column prop="total_rows" label="处理行数" width="100"/>
  <el-table-column label="漂移行数" width="100"><template #default="{row}">{{ row.drift_count || 0 }}</template></el-table-column>
  <el-table-column label="耗时" width="100"><template #default="{row}">{{row.duration}}s</template></el-table-column>
  <el-table-column prop="created_at" label="时间" min-width="170"/>
  <el-table-column label="操作" width="120"><template #default="{row}">
    <el-button size="small" @click="downloadResult(row)">下载结果</el-button>
  </template></el-table-column>
</el-table></el-card>

<el-dialog v-model="showBatch" title="创建批量推理任务" width="640px">
  <el-form :model="batchForm" label-width="100px">
    <el-form-item label="任务名称"><el-input v-model="batchForm.name" placeholder="批量推理任务"/></el-form-item>
    <el-form-item label="选择模型">
      <el-select v-model="batchForm.model_id" placeholder="选择已训练的模型" filterable style="width:100%" @change="onModelChange">
        <el-option v-for="m in modelList" :key="m.id" :label="'#'+m.id+' '+m.name" :value="m.id"/>
      </el-select>
    </el-form-item>
    <el-alert v-if="expectedFeatureCount" type="info" :closable="false" show-icon class="model-tip">
      <template #title>该模型需要 {{ expectedFeatureCount }} 个特征：{{ modelFeatureNames.join('、') || '请严格按训练顺序排列' }}</template>
    </el-alert>
    <el-form-item label="数据来源">
      <el-radio-group v-model="inputMode" @change="onInputModeChange">
        <el-radio value="dataset">选择平台数据集</el-radio>
        <el-radio value="upload">上传本地文件</el-radio>
      </el-radio-group>
    </el-form-item>
    <el-form-item v-if="inputMode==='dataset'" label="选择数据集">
      <el-select v-model="batchForm.dataset_id" filterable clearable placeholder="输入数据集 ID 或名称搜索" style="width:100%" @change="onDatasetChange">
        <el-option v-for="d in datasetList" :key="d.id" :label="`#${d.id} ${d.name}（${d.row_count || 0} 行）`" :value="d.id" />
      </el-select>
      <div class="upload-tip">将使用该数据集的全部数据行，系统按模型训练记录自动匹配特征列。</div>
    </el-form-item>
    <el-form-item v-else label="上传数据">
      <el-upload ref="uploadRef" drag :auto-upload="false" :on-change="onFileChange" :on-remove="onFileRemove" accept=".json,.csv,application/json,text/csv" :limit="1">
        <el-icon><UploadFilled/></el-icon>
        <div>拖拽 JSON/CSV 文件或点击选择</div>
        <template #tip><div class="upload-tip">JSON 支持 {"features":[[...],[...]]}；CSV 可包含特征名表头。</div></template>
      </el-upload>
    </el-form-item>
    <el-alert v-if="fileSummary" :title="fileSummary" type="success" :closable="false" show-icon class="file-summary"/>
    <el-alert v-if="fileError" :title="fileError" type="error" :closable="false" show-icon class="file-summary"/>
    <el-form-item label="输出格式"><el-radio-group v-model="batchForm.output_format"><el-radio value="csv">CSV</el-radio><el-radio value="json">JSON</el-radio></el-radio-group></el-form-item>
  </el-form>
  <template #footer><el-button @click="showBatch=false">取消</el-button><el-button type="primary" @click="doBatchInference" :loading="batchLoading">开始批量推理</el-button></template>
</el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { UploadFilled } from '@element-plus/icons-vue';
import { downloadFile, get, post } from '@/api/legacy';

const loading=ref(false),batchLoading=ref(false),showBatch=ref(false);
const tasks=ref<any[]>([]),modelList=ref<any[]>([]),datasetList=ref<any[]>([]);
const inputMode=ref<'dataset'|'upload'>('dataset');
const batchForm=ref({name:'',model_id:null as number|null,dataset_id:null as number|null,output_format:'json'});
const uploadFile=ref<File|null>(null),uploadRef=ref<any>(null),parsedRows=ref<number[][]>([]);
const modelFeatureNames=ref<string[]>([]),expectedFeatureCount=ref(0),fileSummary=ref(''),fileError=ref('');
const totalRows=computed(()=>tasks.value.reduce((a,t)=>a+(t.total_rows||0),0));

onMounted(loadData);

async function loadData(){
  loading.value=true;
  try{
    const [modelsResult,logsResult,datasetsResult]=await Promise.all([get('/models'),get('/inference/logs?type=batch&limit=200&summary=true'),get('/datasets')]);
    modelList.value=Array.isArray(modelsResult)?modelsResult:(modelsResult.items||[]);
    datasetList.value=(Array.isArray(datasetsResult)?datasetsResult:(datasetsResult.items||[])).filter((item:any)=>item.has_file);
    const names=new Map(modelList.value.map((m:any)=>[Number(m.id),m.name]));
    const logs=Array.isArray(logsResult)?logsResult:(logsResult.items||[]);
    tasks.value=logs.map((item:any)=>toTask(item,names));
  }catch(e:any){ElMessage.error('批量推理记录加载失败：'+(e?.message||'未知错误'))}
  finally{loading.value=false}
}

function toTask(item:any,names:Map<number,any>){
  const predictions=item.predictions||item.results||[];
  return {
    id:item.id,name:item.name||`批量推理 #${item.id}`,model_id:item.model_id,
    model_name:names.get(Number(item.model_id))||`模型 #${item.model_id}`,
    status:'done',total_rows:item.row_count??item.features?.length??predictions.length??0,progress:100,
    duration:(Number(item.inference_time_ms||0)/1000).toFixed(3),created_at:formatTime(item.created_at),
    features:item.features||[],results:predictions,
    dataset_id:item.dataset_id||null,
    drift_count:predictions.filter((r:any)=>r?.drift||r?.drifted).length,output_format:item.output_format||'json',
    result_download_url:item.result_download_url||null, result_file:item.result_file||null,
  };
}

function openBatchDialog(){
  showBatch.value=true;inputMode.value='dataset';batchForm.value={name:'',model_id:null,dataset_id:null,output_format:'json'};
  uploadFile.value=null;parsedRows.value=[];modelFeatureNames.value=[];expectedFeatureCount.value=0;fileSummary.value='';fileError.value='';
  uploadRef.value?.clearFiles();
}

async function onModelChange(modelId:number){
  modelFeatureNames.value=[];expectedFeatureCount.value=0;
  try{
    const lineage=await get('/inference/lineage/model/'+modelId);
    modelFeatureNames.value=Array.isArray(lineage?.feature_cols)?lineage.feature_cols:[];
    expectedFeatureCount.value=modelFeatureNames.value.length;
  }catch{/* 部分导入模型没有训练血缘，提交时仍由模型本身校验 */}
  if(uploadFile.value) await parseSelectedFile();
}

async function onFileChange(file:any){uploadFile.value=file.raw;await parseSelectedFile()}
function onFileRemove(){uploadFile.value=null;parsedRows.value=[];fileSummary.value='';fileError.value=''}

function onInputModeChange(){
  uploadFile.value=null;parsedRows.value=[];batchForm.value.dataset_id=null;fileSummary.value='';fileError.value='';uploadRef.value?.clearFiles();
}

function onDatasetChange(datasetId:number){
  const dataset=datasetList.value.find((item:any)=>item.id===datasetId);
  fileError.value='';
  fileSummary.value=dataset?`已选择数据集 #${dataset.id} ${dataset.name}，共 ${dataset.row_count || 0} 行`:'';
}

async function parseSelectedFile(){
  fileSummary.value='';fileError.value='';parsedRows.value=[];
  if(!uploadFile.value)return;
  try{
    const name=uploadFile.value.name.toLowerCase();
    if(!name.endsWith('.json')&&!name.endsWith('.csv'))throw new Error('仅支持 JSON 或 CSV 文件');
    const text=(await uploadFile.value.text()).replace(/^\uFEFF/,'').trim();
    if(!text)throw new Error('文件内容为空');
    const rows=name.endsWith('.json')?parseJson(text):parseCsv(text);
    parsedRows.value=validateRows(rows);
    fileSummary.value=`已读取 ${parsedRows.value.length} 行，每行 ${parsedRows.value[0].length} 个特征`;
  }catch(e:any){fileError.value=e?.message||'文件解析失败'}
}

function parseJson(text:string):any[][]{
  let payload:any;
  try{payload=JSON.parse(text)}catch{throw new Error('JSON 格式不正确，请检查括号、逗号和引号')}
  const source=Array.isArray(payload)?payload:(payload?.features??payload?.rows);
  if(!Array.isArray(source)||source.length===0)throw new Error('JSON 需要包含非空 features 二维数组');
  if(!Array.isArray(source[0])&&typeof source[0]!=='object')return [source];
  if(Array.isArray(source[0]))return source;
  const names=modelFeatureNames.value.length?modelFeatureNames.value:Object.keys(source[0]);
  return source.map((row:any,index:number)=>names.map(name=>{
    if(row[name]===undefined)throw new Error(`第 ${index+1} 行缺少特征 ${name}`);
    return row[name];
  }));
}

function parseCsv(text:string):any[][]{
  const records=text.split(/\r?\n/).filter(line=>line.trim()).map(parseCsvLine);
  if(!records.length)throw new Error('CSV 文件没有数据');
  const hasHeader=records[0].some(value=>!isFiniteNumber(value));
  if(!hasHeader)return records;
  const headers=records.shift()!.map(value=>String(value).trim());
  if(!records.length)throw new Error('CSV 只有表头，没有数据行');
  if(modelFeatureNames.value.length){
    const indexes=modelFeatureNames.value.map(name=>headers.indexOf(name));
    const missing=modelFeatureNames.value.filter((_,index)=>indexes[index]<0);
    if(missing.length)throw new Error('CSV 缺少模型特征列：'+missing.join('、'));
    return records.map(row=>indexes.map(index=>row[index]));
  }
  return records;
}

function parseCsvLine(line:string):string[]{
  const result:string[]=[];let value='';let quoted=false;
  for(let i=0;i<line.length;i++){
    const char=line[i];
    if(char==='"'&&quoted&&line[i+1]==='"'){value+='"';i++}
    else if(char==='"'){quoted=!quoted}
    else if(char===','&&!quoted){result.push(value.trim());value=''}
    else value+=char;
  }
  result.push(value.trim());return result;
}

function validateRows(rows:any[][]):number[][]{
  if(!Array.isArray(rows)||!rows.length||!Array.isArray(rows[0]))throw new Error('数据必须是二维数组，每一行代表一条推理样本');
  const width=rows[0].length;
  if(!width)throw new Error('每行至少需要一个特征');
  if(expectedFeatureCount.value&&width!==expectedFeatureCount.value)throw new Error(`当前模型需要 ${expectedFeatureCount.value} 个特征，但文件每行提供了 ${width} 个`);
  return rows.map((row,index)=>{
    if(!Array.isArray(row)||row.length!==width)throw new Error(`第 ${index+1} 行特征数量不一致`);
    return row.map((value,column)=>{
      if(!isFiniteNumber(value))throw new Error(`第 ${index+1} 行第 ${column+1} 个特征不是有效数字`);
      return Number(value);
    });
  });
}

function isFiniteNumber(value:any){return value!==''&&value!==null&&value!==undefined&&Number.isFinite(Number(value))}

async function doBatchInference(){
  if(!batchForm.value.model_id){ElMessage.warning('请选择模型');return}
  if(inputMode.value==='dataset'&&!batchForm.value.dataset_id){ElMessage.warning('请选择平台数据集');return}
  if(inputMode.value==='upload'&&!uploadFile.value){ElMessage.warning('请上传 JSON 或 CSV 文件');return}
  if(inputMode.value==='upload'&&(fileError.value||!parsedRows.value.length)){ElMessage.warning(fileError.value||'文件尚未解析成功');return}
  batchLoading.value=true;
  try{
    const rows=inputMode.value==='upload'?validateRows(parsedRows.value):[];
    const payload:any={name:batchForm.value.name||'批量推理',output_format:batchForm.value.output_format};
    if(inputMode.value==='dataset')payload.dataset_id=batchForm.value.dataset_id;
    else payload.features=rows;
    const result:any=await post('/inference/model/'+batchForm.value.model_id+'/batch',payload);
    const selected=modelList.value.find((m:any)=>m.id===batchForm.value.model_id);
    const localTask={
      id:result.id||'最新',name:result.name||batchForm.value.name||'批量推理',model_id:batchForm.value.model_id,
      model_name:selected?.name||`模型 #${batchForm.value.model_id}`,status:'done',progress:100,
      total_rows:result.count??rows.length,drift_count:result.drift_count||0,dataset_id:result.dataset_id||null,
      duration:(Number(result.inference_time_ms||0)/1000).toFixed(3),created_at:formatTime(new Date().toISOString()),
      features:rows,results:result.results||result.predictions||[],output_format:batchForm.value.output_format,
      result_download_url:result.result_download_url||null,result_file:result.result_file||null,
    };
    showBatch.value=false;await loadData();
    const saved=tasks.value.find((task:any)=>task.model_id===localTask.model_id&&task.total_rows===localTask.total_rows);
    if(saved){saved.name=localTask.name;saved.output_format=localTask.output_format}
    else tasks.value.unshift(localTask);
    ElMessage.success(`批量推理完成，共处理 ${localTask.total_rows} 行，结果文件已生成`);
  }catch(e:any){ElMessage.error('批量推理失败：'+(e?.message||'未知错误'))}
  finally{batchLoading.value=false}
}

async function downloadResult(row:any){
  const format=row.output_format||'json';
  try{
    await downloadFile(row.result_download_url||`/inference/logs/${row.id}/download`, `batch_inference_${row.model_id}_${row.id}.${format}`);
  }catch(e:any){ElMessage.error('结果文件下载失败：'+(e?.message||'未知错误'))}
}

function formatTime(value:any){if(!value)return '-';const date=new Date(value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',{hour12:false})}
</script>

<style scoped>
.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}.model-tip{margin:-4px 0 18px}.file-summary{margin:-6px 0 18px}.upload-tip{color:#667085;font-size:12px}
</style>
