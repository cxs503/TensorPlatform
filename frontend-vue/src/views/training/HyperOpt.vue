<template>
  <div class="hyperopt-page">
    <PageHeader title="超参搜索" desc="创建研究 → 定义参数空间 → 自动训练优化 → 输出最佳参数">
      <template #actions>
        <el-button type="primary" @click="showCreate=true"><el-icon><Plus /></el-icon>新建研究</el-button>
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>刷新</el-button>
      </template>
    </PageHeader>

    <el-row :gutter="16" class="stats-row">
      <el-col :xs="6" v-for="s in stats" :key="s.k">
        <el-card shadow="hover"><div class="sv">{{ s.v }}</div><div class="sl">{{ s.l }}</div></el-card>
      </el-col>
    </el-row>

    <!-- 研究列表 -->
    <el-card shadow="never">
      <template #header><span><el-icon><Search /></el-icon> 超参搜索研究</span></template>
      <el-table :data="studies" v-loading="loading" stripe>
        <el-table-column prop="study_id" label="ID" width="130"><template #default="{row}"><code>{{ row.study_id?.slice(-8) }}</code></template></el-table-column>
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="search_algorithm" label="算法" width="100" />
        <el-table-column label="进度" width="140">
          <template #default="{row}">
            <el-progress :percentage="row._progress||0" :status="row.status==='completed'?'success':row.status==='failed'?'exception':''" />
          </template>
        </el-table-column>
        <el-table-column prop="best_value" label="最佳值" width="90">
          <template #default="{row}">{{ row.best_value?.toFixed(4) || '-' }}</template>
        </el-table-column>
        <el-table-column label="最佳模型" width="100">
          <template #default="{row}">
            <el-link v-if="row.best_model_id" type="primary" @click="goModel(row.best_model_id)" :underline="false">#{{ row.best_model_id }}</el-link>
            <span v-else style="color:#999">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{row}"><el-tag :type="row.status==='completed'?'success':row.status==='running'?'warning':'info'" size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{row}">
            <el-button size="small" @click="viewTrials(row)">试验</el-button>
            <el-button size="small" type="success" @click="viewBest(row)" v-if="row.status==='completed'">最佳参数</el-button>
            <el-button size="small" type="warning" @click="stopStudy(row)" v-if="row.status==='running'">停止</el-button>
            <el-button size="small" type="danger" @click="deleteStudy(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !studies.length" description="暂无研究，点击「新建研究」开始" />
    </el-card>

    <!-- 新建研究弹窗 -->
    <el-dialog v-model="showCreate" title="新建超参搜索研究" width="650px" destroy-on-close>
      <el-form :model="form" label-width="110px">
        <el-form-item label="研究名称" required><el-input v-model="form.name" placeholder="如: RF超参优化-生产过程机" /></el-form-item>
        <el-form-item label="选择数据集" required>
          <el-select v-model="form.dataset_id" placeholder="选择训练数据集" style="width:100%" filterable>
            <el-option v-for="d in datasets" :key="d.id" :label="`#${d.id} ${d.name} (${d.row_count}行)`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="优化指标"><el-select v-model="form.objective_metric" style="width:100%"><el-option label="R²" value="r2" /><el-option label="RMSE" value="rmse" /><el-option label="MAE" value="mae" /></el-select></el-form-item>
        <el-form-item label="优化方向"><el-radio-group v-model="form.optimization_direction"><el-radio value="maximize">最大化</el-radio><el-radio value="minimize">最小化</el-radio></el-radio-group></el-form-item>
        <el-form-item label="搜索算法"><el-select v-model="form.search_algorithm" style="width:100%"><el-option label="贝叶斯优化" value="bayesian" /><el-option label="随机搜索" value="random" /></el-select></el-form-item>
        <el-form-item label="试验轮数"><el-input-number v-model="form.n_trials" :min="5" :max="200" /></el-form-item>
        <el-divider content-position="left">参数空间</el-divider>
        <div v-for="(p, k, i) in form.parameters" :key="k" class="param-row">
          <el-select v-model="p.name" placeholder="参数名" style="width:150px" size="small" clearable filterable allow-create>
            <el-option-group v-for="g in PRESET_PARAMS" :key="g.group" :label="g.group">
              <el-option v-for="n in g.items" :key="n" :label="n" :value="n" />
            </el-option-group>
          </el-select>
          <el-select v-model="p.type" style="width:110px" size="small">
            <el-option label="连续均匀" value="uniform" /><el-option label="对数均匀" value="loguniform" />
            <el-option label="整数" value="int" /><el-option label="类别" value="categorical" />
          </el-select>
          <template v-if="p.type !== 'categorical'">
            <el-input-number v-model="p.low" :step="p.type==='int'?1:0.01" size="small" style="width:100px" placeholder="最小值" />
            <span>~</span>
            <el-input-number v-model="p.high" :step="p.type==='int'?1:0.01" size="small" style="width:100px" placeholder="最大值" />
          </template>
          <el-input v-else v-model="p.choices" placeholder="选项,逗号分隔" size="small" style="width:200px" />
          <el-button text type="danger" size="small" @click="delete form.parameters[k]">✕</el-button>
        </div>
        <el-button size="small" @click="addParam" style="margin-top:8px">+ 添加参数</el-button>
        <div style="margin-top:8px;font-size:12px;color:#909399">常用: n_estimators(10-200,int) | max_depth(3-30,int) | min_samples_split(2-20,int)</div>
      </el-form>
      <template #footer>
        <el-button @click="showCreate=false">取消</el-button>
        <el-button type="primary" @click="createStudy" :loading="creating">创建并启动</el-button>
      </template>
    </el-dialog>

    <!-- 试验详情弹窗 -->
    <el-dialog v-model="showTrials" :title="`试验列表 - ${currentStudy?.name}`" width="800px" destroy-on-close>
      <el-table :data="trials" size="small" max-height="400" stripe>
        <el-table-column prop="trial_number" label="#" width="50" />
        <el-table-column label="参数" min-width="200"><template #default="{row}">{{ JSON.stringify(row.parameters) }}</template></el-table-column>
        <el-table-column prop="objective_value" label="目标值" width="100"><template #default="{row}">{{ row.objective_value?.toFixed(4) || '-' }}</template></el-table-column>
        <el-table-column prop="status" label="状态" width="80"><template #default="{row}"><el-tag :type="row.status==='completed'?'success':'info'" size="small">{{ row.status }}</el-tag></template></el-table-column>
      </el-table>
    </el-dialog>

    <!-- 最佳参数弹窗 -->
    <el-dialog v-model="showBest" title="最佳参数" width="500px">
      <template v-if="currentStudy">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="最佳值">{{ currentStudy.best_value?.toFixed(4) || '-' }}</el-descriptions-item>
          <el-descriptions-item label="最优参数"><pre style="margin:0;font-size:12px">{{ JSON.stringify(currentStudy.best_params, null, 2) }}</pre></el-descriptions-item>
          <el-descriptions-item v-if="currentStudy.best_model_id" label="最佳模型">
            <el-link type="primary" @click="goModel(currentStudy.best_model_id)">模型 #{{ currentStudy.best_model_id }}</el-link>
          </el-descriptions-item>
        </el-descriptions>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { get, post, del } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const studies = ref<any[]>([])
const datasets = ref<any[]>([])
const loading = ref(false)
const showCreate = ref(false)
const creating = ref(false)
const showTrials = ref(false)
const showBest = ref(false)
const currentStudy = ref<any>(null)
const trials = ref<any[]>([])

const form = reactive({
  name: '', dataset_id: null as number|null,
  objective_metric: 'r2', optimization_direction: 'maximize',
  search_algorithm: 'bayesian', n_trials: 30,
  parameters: {} as Record<string, any>,
})

const stats = computed(() => [
  { k:'total', v: String(studies.value.length), l:'研究总数' },
  { k:'running', v: String(studies.value.filter(s=>s.status==='running').length), l:'运行中' },
  { k:'completed', v: String(studies.value.filter(s=>s.status==='completed').length), l:'已完成' },
  { k:'best', v: studies.value.filter(s=>s.best_value).length ? Math.max(...studies.value.filter(s=>s.best_value).map(s=>s.best_value||0)).toFixed(3) : '-', l:'全局最佳' },
])

const PRESET_PARAMS = [
  { group: '树模型', items: ['n_estimators','max_depth','min_samples_split','min_samples_leaf'] },
  { group: '梯度提升', items: ['learning_rate','subsample','colsample_bytree'] },
  { group: '神经网络', items: ['epochs','batch_size','dropout','hidden_layers'] },
  { group: '正则化', items: ['C','alpha','l1_ratio','reg_lambda'] },
]
function addParam() {
  const k = `param_${Object.keys(form.parameters).length + 1}`
  form.parameters[k] = { name: '', type: 'uniform', low: 0, high: 1, choices: '' }
}

async function refresh() {
  loading.value = true
  try {
    const r = await get('/hyperopt/studies')
    studies.value = (r?.items || []).map((s: any) => ({
      ...s,
      _progress: s.status === 'completed' ? 100 : s.status === 'running' ? 50 : 0,
    }))
  } catch { studies.value = [] }
  try {
    const ds = await get('/datasets')
    datasets.value = ds?.items || []
  } catch { datasets.value = [] }
  loading.value = false
}

async function createStudy() {
  if (!form.name || !form.dataset_id) { ElMessage.warning('请填写名称和选择数据集'); return }
  creating.value = true
  try {
    // 转换参数格式
    const params: any = {}
    for (const [k, v] of Object.entries(form.parameters)) {
      const p = v as any
      if (!p.name) continue
      const cfg: any = { type: p.type }
      if (p.type === 'categorical') {
        cfg.choices = (p.choices || '').split(',').map((x: string) => { const n = Number(x.trim()); return isNaN(n) ? x.trim() : n })
      } else {
        cfg.low = Number(p.low) || 0; cfg.high = Number(p.high) || 1
      }
      params[p.name] = cfg
    }
    await post('/hyperopt/studies', { ...form, parameters: params })
    ElMessage.success('研究已创建并启动')
    showCreate.value = false
    form.name = ''; form.dataset_id = null; form.parameters = {}
    refresh()
  } catch (e: any) { ElMessage.error('创建失败: ' + (e?.response?.data?.detail || e.message)) }
  finally { creating.value = false }
}

async function stopStudy(row: any) {
  try {
    await ElMessageBox.confirm('确定停止该研究？', '确认', { type: 'warning' })
    await post(`/hyperopt/studies/${row.study_id}/stop`)
    ElMessage.success('已停止'); refresh()
  } catch {}
}

async function deleteStudy(row: any) {
  try {
    await ElMessageBox.confirm('确定删除？', '确认', { type: 'warning' })
    await del(`/hyperopt/studies/${row.study_id}`)
    ElMessage.success('已删除'); refresh()
  } catch {}
}

async function viewTrials(row: any) {
  currentStudy.value = row; showTrials.value = true
  try {
    const r = await get(`/hyperopt/studies/${row.study_id}/trials`)
    trials.value = r?.items || []
  } catch { trials.value = [] }
}

function viewBest(row: any) { currentStudy.value = row; showBest.value = true }
function goModel(id: number) { window.location.hash = `#/model/new-models?highlight=${id}` }

onMounted(refresh)
</script>

<style lang="scss" scoped>
.hyperopt-page {
  .stats-row { margin-bottom:20px; .sv { font-size:24px;font-weight:700;color:#8b5cf6; } .sl { font-size:12px;color:#999;margin-top:4px; } }
  .param-row { display:flex;align-items:center;gap:6px;margin-bottom:8px; }
  code { font-size:11px;background:#f0f0f0;padding:2px 6px;border-radius:3px; }
}
</style>
