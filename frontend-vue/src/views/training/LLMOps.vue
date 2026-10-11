<template>
  <div class="llmops-page">
    <PageHeader title="LLMOps" desc="统一管理平台的大模型连接、真实调用记录、Prompt版本和RAG知识库">
      <template #actions><el-button :loading="loading" @click="loadAll">刷新数据</el-button></template>
    </PageHeader>

    <el-alert
      :type="provider.configured ? 'success' : 'warning'" :closable="false" show-icon
      :title="provider.configured ? `${provider.provider} · ${provider.model} 已连接` : provider.message || '外部大模型未配置'"
      description="调用追踪只保存功能来源、模型、Token、耗时和状态，不保存Prompt、代码、回答或数据内容。"
      class="provider-alert"
    />

    <div class="stat-grid">
      <div class="stat-card"><span>近30天调用</span><strong>{{ number(overview.total_calls) }}</strong><small>真实API请求</small></div>
      <div class="stat-card"><span>成功率</span><strong>{{ number(overview.success_rate, 2) }}%</strong><small>{{ number(overview.failed_calls) }} 次失败</small></div>
      <div class="stat-card"><span>平均响应</span><strong>{{ latency(overview.avg_latency_ms) }}</strong><small>服务端实际等待时间</small></div>
      <div class="stat-card"><span>Token消耗</span><strong>{{ number(overview.total_tokens) }}</strong><small>来自供应商usage</small></div>
      <div class="stat-card"><span>Prompt版本</span><strong>{{ number(overview.prompt_versions) }}</strong><small>数据库持久化</small></div>
      <div class="stat-card"><span>知识库文档</span><strong>{{ number(overview.rag_documents) }}</strong><small>当前可见范围</small></div>
    </div>

    <el-tabs v-model="activeTab" class="main-tabs">
      <el-tab-pane label="调用概览" name="overview">
        <div class="overview-grid">
          <el-card shadow="never">
            <template #header><strong>每日调用与Token</strong></template>
            <div v-if="overview.daily?.length" ref="dailyChartRef" class="daily-chart"></div>
            <el-empty v-else description="暂无已追踪调用；使用代码生成器或沙箱AI后会自动记录" />
          </el-card>
          <el-card shadow="never">
            <template #header><strong>功能来源</strong></template>
            <el-table :data="overview.sources || []" stripe max-height="340">
              <el-table-column prop="source" label="来源" min-width="150" />
              <el-table-column prop="calls" label="调用" width="75" />
              <el-table-column label="成功率" width="90"><template #default="{row}">{{ sourceSuccessRate(row) }}%</template></el-table-column>
              <el-table-column label="平均耗时" width="105"><template #default="{row}">{{ latency(row.avg_latency_ms) }}</template></el-table-column>
              <el-table-column prop="total_tokens" label="Token" width="90" />
            </el-table>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="调用记录" name="calls">
        <el-card shadow="never">
          <div class="toolbar">
            <el-select v-model="callFilters.source" clearable placeholder="全部功能来源" style="width:190px" @change="loadCalls">
              <el-option v-for="item in sourceOptions" :key="item" :label="item" :value="item" />
            </el-select>
            <el-select v-model="callFilters.status" clearable placeholder="全部状态" style="width:140px" @change="loadCalls">
              <el-option label="成功" value="success" /><el-option label="失败" value="error" />
            </el-select>
            <span class="privacy-note">不记录请求和回答正文</span>
          </div>
          <el-table :data="calls" v-loading="callsLoading" stripe>
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="source" label="功能来源" min-width="145" />
            <el-table-column prop="operation" label="操作" min-width="145" />
            <el-table-column prop="provider" label="供应商" width="95" />
            <el-table-column prop="model" label="模型" min-width="130" />
            <el-table-column label="状态" width="85"><template #default="{row}"><el-tag :type="row.status==='success'?'success':'danger'" size="small">{{ row.status==='success'?'成功':'失败' }}</el-tag></template></el-table-column>
            <el-table-column label="Token" width="90"><template #default="{row}">{{ row.total_tokens ?? '未返回' }}</template></el-table-column>
            <el-table-column label="耗时" width="105"><template #default="{row}">{{ latency(row.latency_ms) }}</template></el-table-column>
            <el-table-column prop="username" label="用户" width="110" />
            <el-table-column label="时间" width="175"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
            <el-table-column prop="error_message" label="错误" min-width="180" show-overflow-tooltip />
          </el-table>
          <el-empty v-if="!callsLoading && !calls.length" description="暂无调用记录" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="Prompt管理" name="prompts">
        <div class="prompt-grid">
          <el-card shadow="never">
            <template #header><strong>保存Prompt新版本</strong></template>
            <el-form label-width="85px">
              <el-form-item label="名称" required><el-input v-model="promptForm.name" maxlength="200" placeholder="例如：industrial_code_review" /></el-form-item>
              <el-form-item label="说明"><el-input v-model="promptForm.description" maxlength="2000" placeholder="该Prompt的用途" /></el-form-item>
              <el-form-item label="模板" required><el-input v-model="promptForm.template" type="textarea" :rows="9" maxlength="50000" placeholder="请分析设备 {device} 的异常：{question}" /></el-form-item>
              <el-form-item label="变量"><el-input v-model="promptForm.variables" placeholder="device, question（英文逗号分隔）" /></el-form-item>
              <el-button type="primary" :loading="promptSaving" @click="savePrompt">保存新版本</el-button>
            </el-form>
          </el-card>
          <el-card shadow="never">
            <template #header><div class="card-header"><strong>Prompt版本记录</strong><span>同名保存自动递增版本</span></div></template>
            <el-table :data="prompts" stripe max-height="550">
              <el-table-column prop="id" label="ID" width="70" />
              <el-table-column prop="name" label="名称" min-width="160" />
              <el-table-column label="版本" width="75"><template #default="{row}">v{{ row.version }}</template></el-table-column>
              <el-table-column label="变量" min-width="150"><template #default="{row}">{{ (row.variables || []).join(', ') || '无' }}</template></el-table-column>
              <el-table-column label="创建时间" width="170"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
              <el-table-column label="操作" width="145" fixed="right"><template #default="{row}"><el-button link type="primary" @click="openPromptTest(row)">真实测试</el-button><el-button link type="danger" @click="removePrompt(row)">删除</el-button></template></el-table-column>
            </el-table>
            <el-empty v-if="!prompts.length" description="暂无Prompt版本" />
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="RAG知识库" name="rag">
        <div class="rag-grid">
          <el-card shadow="never">
            <template #header><strong>添加知识文档</strong></template>
            <el-form label-width="85px">
              <el-form-item label="文档名称" required><el-input v-model="ragForm.name" maxlength="255" /></el-form-item>
              <el-form-item label="来源"><el-input v-model="ragForm.source" maxlength="255" placeholder="manual / 文件名 / 业务系统" /></el-form-item>
              <el-form-item label="导入文件">
                <el-upload :auto-upload="false" :limit="1" accept=".txt,.md,.csv" :on-change="readRagFile" :on-remove="clearRagFile">
                  <el-button>选择TXT、Markdown或CSV</el-button>
                </el-upload>
              </el-form-item>
              <el-form-item label="文档内容" required><el-input v-model="ragForm.content" type="textarea" :rows="10" maxlength="200000" show-word-limit placeholder="输入需要检索的知识内容" /></el-form-item>
              <el-button type="primary" :loading="ragSaving" @click="saveRagDocument">加入知识库</el-button>
            </el-form>
          </el-card>
          <div class="rag-right">
            <el-card shadow="never">
              <template #header><strong>检索验证</strong></template>
              <div class="search-row"><el-input v-model="ragQuery" placeholder="输入问题验证召回结果" @keyup.enter="searchRag" /><el-button type="primary" :loading="ragSearching" @click="searchRag">检索</el-button></div>
              <div v-if="ragResults.length" class="rag-results">
                <div v-for="item in ragResults" :key="item.id" class="rag-result"><div><strong>{{ item.name }}</strong><el-tag size="small">{{ item.score }}</el-tag></div><p>{{ item.preview }}</p></div>
              </div>
              <el-empty v-else description="尚未执行检索" :image-size="70" />
            </el-card>
            <el-card shadow="never">
              <template #header><strong>知识文档</strong></template>
              <el-table :data="ragDocuments" stripe max-height="330">
                <el-table-column prop="id" label="ID" width="70" />
                <el-table-column prop="name" label="名称" min-width="170" />
                <el-table-column prop="source" label="来源" min-width="120" />
                <el-table-column prop="characters" label="字符数" width="90" />
                <el-table-column label="时间" width="170"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
                <el-table-column label="操作" width="75"><template #default="{row}"><el-button link type="danger" @click="removeRagDocument(row)">删除</el-button></template></el-table-column>
              </el-table>
            </el-card>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="后续能力" name="future">
        <el-card shadow="never">
          <el-alert title="本地大模型微调与本地推理部署属于第二阶段，当前没有开放。" type="warning" :closable="false" show-icon />
          <div class="future-grid">
            <div><strong>本地LoRA/QLoRA微调</strong><p>需要确认可运行的基座模型、依赖和GPU/CPU资源，不再展示模拟训练进度。</p><el-tag type="info">第二阶段</el-tag></div>
            <div><strong>本地大模型服务端点</strong><p>需要真实加载微调产物并启动推理进程，不再创建内存假端点。</p><el-tag type="info">第二阶段</el-tag></div>
            <div><strong>外部观测平台</strong><p>Langfuse尚未部署；当前先使用平台数据库完成真实调用追踪。</p><el-tag type="info">未部署</el-tag></div>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="showPromptTest" :title="`测试Prompt：${testingPrompt?.name || ''} v${testingPrompt?.version || ''}`" width="720px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item v-for="name in testingPrompt?.variables || []" :key="name" :label="name" required><el-input v-model="promptTestVariables[name]" /></el-form-item>
        <el-alert v-if="!(testingPrompt?.variables || []).length" title="该Prompt没有变量，将直接使用模板内容进行测试" type="info" :closable="false" />
      </el-form>
      <el-card v-if="promptTestResult" shadow="never" class="prompt-result"><template #header>智谱返回结果</template><div>{{ promptTestResult }}</div></el-card>
      <template #footer><el-button @click="showPromptTest=false">关闭</el-button><el-button type="primary" :loading="promptTesting" @click="runPromptTest">调用大模型测试</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import PageHeader from '@/components/PageHeader.vue'
import { del, get, post } from '@/api/legacy'

const activeTab = ref('overview'), loading = ref(false)
const provider = ref<any>({}), overview = ref<any>({}), calls = ref<any[]>([])
const prompts = ref<any[]>([]), ragDocuments = ref<any[]>([])
const callsLoading = ref(false), promptSaving = ref(false), ragSaving = ref(false), ragSearching = ref(false)
const callFilters = reactive({ source:'', status:'' })
const promptForm = reactive({ name:'', description:'', template:'', variables:'' })
const ragForm = reactive({ name:'', source:'manual', content:'' })
const ragQuery = ref(''), ragResults = ref<any[]>([])
const showPromptTest = ref(false), testingPrompt = ref<any>(null), promptTestVariables = reactive<Record<string,string>>({})
const promptTesting = ref(false), promptTestResult = ref('')
const dailyChartRef = ref<HTMLElement | null>(null)
let dailyChart: echarts.ECharts | null = null

const sourceOptions = computed(() => Array.from(new Set((overview.value.sources || []).map((item:any) => item.source))))
function number(value:any, precision=0){const numeric=Number(value||0);return precision?numeric.toFixed(precision):numeric.toLocaleString()}
function latency(value:any){const ms=Number(value||0);return ms>=1000?`${(ms/1000).toFixed(2)} s`:`${ms.toFixed(0)} ms`}
function dateTime(value:string){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'}
function sourceSuccessRate(row:any){return ((Number(row.success_calls||0)/Math.max(Number(row.calls||0),1))*100).toFixed(1)}

async function loadAll(){
  loading.value=true
  try{
    const [providerResult,overviewResult,promptResult,ragResult]=await Promise.all([
      get('/llmops/provider'),get('/llmops/overview',{days:30}),get('/llmops/prompts'),get('/llmops/rag/documents')
    ])
    provider.value=providerResult;overview.value=overviewResult;prompts.value=promptResult.items||[];ragDocuments.value=ragResult.items||[]
    await loadCalls();await nextTick();renderDailyChart()
  }catch(error:any){ElMessage.error(error?.message||'LLMOps数据加载失败')}
  finally{loading.value=false}
}
async function loadCalls(){callsLoading.value=true;try{const params:any={limit:200};if(callFilters.source)params.source=callFilters.source;if(callFilters.status)params.status=callFilters.status;const result:any=await get('/llmops/calls',params);calls.value=result.items||[]}catch(error:any){ElMessage.error(error?.message||'调用记录加载失败')}finally{callsLoading.value=false}}
function renderDailyChart(){dailyChart?.dispose();dailyChart=null;if(!dailyChartRef.value||!overview.value.daily?.length)return;dailyChart=echarts.init(dailyChartRef.value);const rows=overview.value.daily;dailyChart.setOption({tooltip:{trigger:'axis'},legend:{data:['调用次数','Token']},grid:{left:55,right:60,top:40,bottom:45},xAxis:{type:'category',data:rows.map((r:any)=>String(r.day).slice(0,10))},yAxis:[{type:'value',name:'调用'},{type:'value',name:'Token'}],series:[{name:'调用次数',type:'bar',data:rows.map((r:any)=>Number(r.calls||0)),itemStyle:{color:'#6366f1'}},{name:'Token',type:'line',yAxisIndex:1,smooth:true,data:rows.map((r:any)=>Number(r.total_tokens||0)),itemStyle:{color:'#10b981'}}]})}

async function savePrompt(){if(!promptForm.name.trim()||!promptForm.template.trim())return ElMessage.warning('请填写Prompt名称和模板');promptSaving.value=true;try{const variables=promptForm.variables.split(/[,，]/).map(v=>v.trim()).filter(Boolean);const result:any=await post('/llmops/prompts',{name:promptForm.name.trim(),description:promptForm.description.trim(),template:promptForm.template,variables});ElMessage.success(`已保存 ${result.name} v${result.version}`);Object.assign(promptForm,{name:'',description:'',template:'',variables:''});await loadAll()}catch(error:any){ElMessage.error(error?.message||'保存失败')}finally{promptSaving.value=false}}
function openPromptTest(row:any){testingPrompt.value=row;promptTestResult.value='';Object.keys(promptTestVariables).forEach(key=>delete promptTestVariables[key]);(row.variables||[]).forEach((name:string)=>promptTestVariables[name]='');showPromptTest.value=true}
async function runPromptTest(){promptTesting.value=true;try{const result:any=await post(`/llmops/prompts/${testingPrompt.value.id}/test`,{variables:{...promptTestVariables}});promptTestResult.value=result.response||'';ElMessage.success('Prompt真实调用完成');await loadAll()}catch(error:any){ElMessage.error(error?.message||'Prompt测试失败')}finally{promptTesting.value=false}}
async function removePrompt(row:any){try{await ElMessageBox.confirm(`删除 ${row.name} v${row.version}？`,'删除Prompt版本',{type:'warning'});await del(`/llmops/prompts/${row.id}`);await loadAll();ElMessage.success('已删除')}catch(error:any){if(error!=='cancel')ElMessage.error(error?.message||'删除失败')}}

async function readRagFile(fileItem:any){try{const file=fileItem.raw as File;if(file.size>1024*1024)throw new Error('第一阶段单个知识文件不能超过1MB');ragForm.content=await file.text();ragForm.name=ragForm.name||file.name;ragForm.source=file.name;ElMessage.success('文件内容已读取，请确认后加入知识库')}catch(error:any){ElMessage.error(error?.message||'文件读取失败')}}
function clearRagFile(){ragForm.content='';ragForm.source='manual'}
async function saveRagDocument(){if(!ragForm.name.trim()||!ragForm.content.trim())return ElMessage.warning('请填写文档名称和内容');ragSaving.value=true;try{await post('/llmops/rag/documents',{name:ragForm.name.trim(),source:ragForm.source.trim()||'manual',content:ragForm.content});Object.assign(ragForm,{name:'',source:'manual',content:''});await loadAll();ElMessage.success('文档已加入知识库')}catch(error:any){ElMessage.error(error?.message||'保存失败')}finally{ragSaving.value=false}}
async function searchRag(){if(!ragQuery.value.trim())return ElMessage.warning('请输入检索问题');ragSearching.value=true;try{const result:any=await post('/llmops/rag/search',{query:ragQuery.value.trim(),top_k:5});ragResults.value=result.items||[]}catch(error:any){ElMessage.error(error?.message||'检索失败')}finally{ragSearching.value=false}}
async function removeRagDocument(row:any){try{await ElMessageBox.confirm(`删除知识文档「${row.name}」？`,'删除文档',{type:'warning'});await del(`/llmops/rag/documents/${row.id}`);await loadAll();ElMessage.success('已删除')}catch(error:any){if(error!=='cancel')ElMessage.error(error?.message||'删除失败')}}
function resize(){dailyChart?.resize()}
onMounted(()=>{loadAll();window.addEventListener('resize',resize)})
onBeforeUnmount(()=>{window.removeEventListener('resize',resize);dailyChart?.dispose()})
</script>

<style scoped>
.llmops-page{padding:20px}.provider-alert{margin-bottom:16px}.stat-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:18px}.stat-card{padding:16px;border:1px solid var(--el-border-color-lighter);border-radius:9px;background:var(--el-bg-color)}.stat-card span,.stat-card small{display:block;color:var(--el-text-color-secondary);font-size:12px}.stat-card strong{display:block;margin:7px 0;font-size:23px;color:var(--el-text-color-primary)}.main-tabs{min-height:500px}.overview-grid{display:grid;grid-template-columns:1.35fr 1fr;gap:16px}.daily-chart{height:340px}.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px}.privacy-note{margin-left:auto;color:var(--el-text-color-secondary);font-size:12px}.prompt-grid,.rag-grid{display:grid;grid-template-columns:minmax(330px,.8fr) minmax(520px,1.4fr);gap:16px}.card-header{display:flex;justify-content:space-between}.card-header span{color:var(--el-text-color-secondary);font-size:12px}.prompt-result{margin-top:14px}.prompt-result div{white-space:pre-wrap;line-height:1.7}.rag-right{display:flex;flex-direction:column;gap:16px}.search-row{display:flex;gap:8px}.rag-results{display:flex;flex-direction:column;gap:8px;margin-top:12px;max-height:300px;overflow:auto}.rag-result{padding:10px;border:1px solid var(--el-border-color-lighter);border-radius:7px}.rag-result>div{display:flex;justify-content:space-between}.rag-result p{margin:6px 0 0;color:var(--el-text-color-secondary);font-size:12px;line-height:1.55;white-space:pre-wrap}.future-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px}.future-grid>div{padding:18px;border:1px solid var(--el-border-color-lighter);border-radius:8px}.future-grid p{min-height:45px;color:var(--el-text-color-secondary);font-size:13px;line-height:1.6}@media(max-width:1200px){.stat-grid{grid-template-columns:repeat(3,1fr)}.prompt-grid,.rag-grid,.overview-grid{grid-template-columns:1fr}}@media(max-width:700px){.stat-grid,.future-grid{grid-template-columns:1fr 1fr}}
</style>
