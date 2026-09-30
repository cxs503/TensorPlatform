<template>
<div class="pg">
  <div class="hd"><h2>🖼️ 计算机视觉</h2>
    <div class="hd-actions">
      <el-button type="primary" @click="showTrain=true">+ 新建训练</el-button>
      <el-button @click="showPredict=true">🔍 图像预测</el-button>
      <el-button @click="showGenerate=true">🎨 文生图</el-button>
      <el-button @click="showSegment=true">✂️ 语义分割</el-button>
    </div>
  </div>

  <!-- 统计卡片 -->
  <el-row :gutter="16" class="st">
    <el-col :span="6"><el-card><el-statistic title="总任务" :value="jobs.length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="完成" :value="jobs.filter(t=>t.status==='completed').length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="进行中" :value="jobs.filter(t=>t.status==='training').length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="可用模型" :value="models.length"/></el-card></el-col>
  </el-row>

  <!-- 模型列表 -->
  <el-card style="margin-bottom:20px">
    <template #header><span>📦 预训练模型库</span></template>
    <el-tabs v-model="modelTab">
      <el-tab-pane label="分类模型" name="classification">
        <el-table :data="models.filter(m=>m.category==='classification')" size="small">
          <el-table-column prop="name" label="模型" width="200"/>
          <el-table-column prop="params" label="参数量" width="100"/>
          <el-table-column prop="top1_acc" label="Top-1准确率" width="120"/>
          <el-table-column prop="size_mb" label="大小(MB)" width="100"/>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="检测模型" name="detection">
        <el-table :data="models.filter(m=>m.category==='detection')" size="small">
          <el-table-column prop="name" label="模型" width="240"/>
          <el-table-column prop="params" label="参数量" width="100"/>
          <el-table-column prop="map" label="mAP" width="100"/>
          <el-table-column prop="size_mb" label="大小(MB)" width="100"/>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="分割模型" name="segmentation">
        <el-table :data="models.filter(m=>m.category==='segmentation')" size="small">
          <el-table-column prop="name" label="模型" width="240"/>
          <el-table-column prop="params" label="参数量" width="100"/>
          <el-table-column prop="miou" label="mIoU" width="100"/>
          <el-table-column prop="size_mb" label="大小(MB)" width="100"/>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-card>

  <!-- 训练任务列表 -->
  <el-card>
    <template #header><span>📋 训练任务</span><el-button size="small" style="float:right" @click="refreshJobs">刷新</el-button></template>
    <el-table :data="jobs" stripe v-loading="loading">
      <el-table-column prop="job_id" label="ID" width="100"/>
      <el-table-column label="配置" min-width="200">
        <template #default="{row}">{{ row.config?.model }} | {{ row.config?.dataset }} | Epochs: {{ row.config?.epochs }}</template>
      </el-table-column>
      <el-table-column label="进度" width="200">
        <template #default="{row}">
          <el-progress :percentage="row.progress||0" :status="row.status==='completed'?'success':row.status==='failed'?'exception':''" />
        </template>
      </el-table-column>
      <el-table-column label="指标" width="150">
        <template #default="{row}">
          <span v-if="row.metrics">准确率: {{ (row.metrics.accuracy*100).toFixed(1) }}%</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{row}">
          <el-tag :type="row.status==='completed'?'success':row.status==='failed'?'danger':row.status==='training'?'warning':'info'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="170"><template #default="{row}">{{ row.created_at?.slice(0,19) }}</template></el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{row}"><el-button size="small" @click="viewJob(row)">详情</el-button></template>
      </el-table-column>
    </el-table>
  </el-card>

  <!-- 训练对话框 -->
  <el-dialog v-model="showTrain" title="创建CV训练任务" width="600px">
    <el-form :model="trainForm" label-width="100px">
      <el-form-item label="任务类型">
        <el-radio-group v-model="trainForm.task_type"><el-radio label="classification">图像分类</el-radio><el-radio label="detection">目标检测</el-radio></el-radio-group>
      </el-form-item>
      <el-form-item label="模型"><el-select v-model="trainForm.model" style="width:100%"><el-option v-for="m in trainModels" :key="m" :label="m" :value="m"/></el-select></el-form-item>
      <el-form-item label="数据集"><el-select v-model="trainForm.dataset" style="width:100%"><el-option label="CIFAR-10 (10类)" value="cifar10"/><el-option label="CIFAR-100 (100类)" value="cifar100"/><el-option label="STL-10" value="stl10"/></el-select></el-form-item>
      <el-form-item label="Epochs"><el-input-number v-model="trainForm.epochs" :min="1" :max="200"/></el-form-item>
      <el-form-item label="Batch Size"><el-input-number v-model="trainForm.batch_size" :min="4" :max="256"/></el-form-item>
      <el-form-item label="学习率"><el-input-number v-model="trainForm.learning_rate" :min="0.0001" :max="0.1" :step="0.0001" :precision="4"/></el-form-item>
    </el-form>
    <template #footer><el-button @click="showTrain=false">取消</el-button><el-button type="primary" @click="doTrain" :loading="tl">开始训练</el-button></template>
  </el-dialog>

  <!-- 图像预测对话框 -->
  <el-dialog v-model="showPredict" title="🔍 图像预测" width="650px">
    <el-form label-width="80px">
      <el-form-item label="任务"><el-radio-group v-model="predictForm.task"><el-radio label="classification">分类</el-radio><el-radio label="detection">检测</el-radio></el-radio-group></el-form-item>
      <el-form-item label="模型"><el-select v-model="predictForm.model_name" style="width:200px"><el-option v-for="m in trainModels" :key="m" :label="m" :value="m"/></el-select></el-form-item>
      <el-form-item label="图片"><el-upload :auto-upload="false" :on-change="handleImageUpload" :limit="1" accept="image/*"><el-button>选择图片</el-button></el-upload></el-form-item>
      <div v-if="predictUrl" style="text-align:center;margin:10px 0"><img :src="predictUrl" style="max-width:300px;max-height:300px;border-radius:8px"/></div>
      <el-form-item><el-button type="primary" @click="doPredict" :loading="pl">开始预测</el-button></el-form-item>
    </el-form>
    <div v-if="predictResult" style="margin-top:16px">
      <h4>预测结果</h4>
      <div v-if="predictResult.predictions">
        <el-table :data="predictResult.predictions" size="small"><el-table-column prop="class" label="类别"/><el-table-column prop="confidence" label="置信度" width="120"/></el-table>
      </div>
      <div v-else-if="predictResult.detections">
        <el-table :data="predictResult.detections" size="small"><el-table-column prop="label" label="检测目标"/><el-table-column prop="score" label="置信度" width="120"/></el-table>
      </div>
    </div>
  </el-dialog>

  <!-- 文生图对话框 -->
  <el-dialog v-model="showGenerate" title="🎨 AI文生图" width="600px">
    <el-form label-width="100px">
      <el-form-item label="描述词"><el-input v-model="genForm.prompt" type="textarea" :rows="3" placeholder="描述你想生成的画面..."/></el-form-item>
      <el-form-item label="负面词"><el-input v-model="genForm.negative_prompt" placeholder="不想出现的内容"/></el-form-item>
      <el-form-item label="步数"><el-input-number v-model="genForm.num_steps" :min="5" :max="50"/></el-form-item>
      <el-form-item label="尺寸"><el-select v-model="genSize" style="width:200px"><el-option label="384x384" value="384x384"/><el-option label="512x512" value="512x512"/></el-select></el-form-item>
      <el-form-item><el-button type="primary" @click="doGenerate" :loading="gl">生成图片</el-button></el-form-item>
    </el-form>
    <div v-if="genImage" style="text-align:center"><img :src="'data:image/png;base64,'+genImage" style="max-width:100%;border-radius:8px"/></div>
    <div v-if="genError" style="color:#f56c6c;margin-top:10px">{{ genError }}</div>
  </el-dialog>

  <!-- 语义分割对话框 -->
  <el-dialog v-model="showSegment" title="✂️ 语义分割" width="550px">
    <el-form label-width="80px">
      <el-form-item label="图片"><el-upload :auto-upload="false" :on-change="handleSegUpload" :limit="1" accept="image/*"><el-button>选择图片</el-button></el-upload></el-form-item>
      <div v-if="segUrl" style="text-align:center;margin:10px 0"><img :src="segUrl" style="max-width:300px;border-radius:8px"/></div>
      <el-form-item><el-button type="primary" @click="doSegment" :loading="sl">开始分割</el-button></el-form-item>
    </el-form>
    <div v-if="segResult" style="margin-top:16px">
      <h4>分割结果</h4>
      <el-table :data="segResult.segments" size="small"><el-table-column prop="class_id" label="类别ID" width="100"/><el-table-column prop="pixel_count" label="像素数" width="120"/><el-table-column prop="percentage" label="占比(%)" width="100"/></el-table>
    </div>
  </el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, postForm } from '@/api/legacy'

const loading=ref(false),tl=ref(false),pl=ref(false),gl=ref(false),sl=ref(false)
const showTrain=ref(false),showPredict=ref(false),showGenerate=ref(false),showSegment=ref(false)
const jobs=ref<any[]>([]), models=ref<any[]>([])
const modelTab=ref('classification')
const trainModels = ['resnet18','resnet50','mobilenet_v2','efficientnet_b0']
const trainForm=ref({task_type:'classification',model:'resnet18',dataset:'cifar10',epochs:5,batch_size:16,learning_rate:0.001})
const predictForm=ref({task:'classification',model_name:'resnet18'})
const predictUrl=ref(''),predictResult=ref<any>(null),predictB64=ref('')
const genForm=ref({prompt:'a beautiful landscape with mountains and lake',negative_prompt:'',num_steps:15})
const genImage=ref(''),genError=ref(''),genSize=ref('384x384')
const segUrl=ref(''),segResult=ref<any>(null),segB64=ref('')

async function refreshJobs() {
  loading.value=true
  try { const r=await get('/cv/jobs'); jobs.value=r.jobs||[] }
  catch(e) { console.error(e) }
  finally { loading.value=false }
}

async function loadModels() {
  try { const data=await get('/cv/models')
    models.value = [
      ...(data.classification||[]).map((m:any)=>({...m,category:'classification'})),
      ...(data.detection||[]).map((m:any)=>({...m,category:'detection'})),
      ...(data.segmentation||[]).map((m:any)=>({...m,category:'segmentation'})),
    ]
  } catch(e) { console.error(e) }
}

onMounted(()=>{refreshJobs();loadModels()})

async function doTrain() {
  tl.value=true
  try { const r=await post('/cv/train', trainForm.value)
    ElMessage.success('CV训练已启动! ID: '+r.job_id)
    showTrain.value=false
    refreshJobs()
  } catch(e:any) { ElMessage.error('训练失败: '+e.message) }
  finally { tl.value=false }
}

function handleImageUpload(file:any) {
  const reader=new FileReader()
  reader.onload=(e)=>{ predictUrl.value=e.target?.result as string; predictB64.value=(e.target?.result as string).split(',')[1] }
  reader.readAsDataURL(file.raw)
}

async function doPredict() {
  if(!predictB64.value) { ElMessage.warning('请选择图片'); return }
  pl.value=true
  try { const r=await post('/cv/predict', {image_base64:predictB64.value,...predictForm.value})
    predictResult.value=r
  } catch(e:any) { ElMessage.error('预测失败: '+e.message) }
  finally { pl.value=false }
}

async function doGenerate() {
  const [w,h]=genSize.value.split('x').map(Number)
  gl.value=true; genError.value=''; genImage.value=''
  try {
    const fd=new FormData()
    fd.append('prompt',genForm.value.prompt)
    fd.append('negative_prompt',genForm.value.negative_prompt)
    fd.append('num_steps',String(genForm.value.num_steps))
    fd.append('width',String(w)); fd.append('height',String(h))
    const r=await postForm('/cv/generate', fd)
    if(r.image_base64) genImage.value=r.image_base64
    else genError.value=r.error||'生成失败'
  } catch(e:any) { genError.value='生成失败: '+e.message }
  finally { gl.value=false }
}

function handleSegUpload(file:any) {
  const reader=new FileReader()
  reader.onload=(e)=>{ segUrl.value=e.target?.result as string; segB64.value=(e.target?.result as string).split(',')[1] }
  reader.readAsDataURL(file.raw)
}

async function doSegment() {
  if(!segB64.value) { ElMessage.warning('请选择图片'); return }
  sl.value=true
  try {
    const fd=new FormData(); fd.append('image_base64',segB64.value)
    const r=await postForm('/cv/segment', fd)
    segResult.value=r
  } catch(e:any) { ElMessage.error('分割失败: '+e.message) }
  finally { sl.value=false }
}

function viewJob(row:any) {
  const m=row.metrics
  const h=row.history
  if(m) {
    let msg=`准确率:${(m.accuracy*100).toFixed(1)}% | 训练准确率:${(m.train_accuracy*100).toFixed(1)}% | Loss:${m.loss?.toFixed(4)} | 设备:${m.device}`
    if(h) msg+=`\n训练历史: ${row.config?.epochs||'?'}轮 | 数据集: ${row.dataset_size?.train||'?'}训练/${row.dataset_size?.test||'?'}测试`
    ElMessage.info(msg)
  } else { ElMessage.info('暂无详情') }
}
</script>

<style scoped>
.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.hd-actions{display:flex;gap:8px}.st{margin-bottom:20px}
</style>
