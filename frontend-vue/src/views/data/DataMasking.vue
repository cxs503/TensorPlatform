<template>
  <div class="pg">
    <PageHeader title="🔒 数据脱敏" desc="脱敏规则 · 访问策略 · 审计日志 · 敏感度分析" />

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <!-- ═══ 脱敏规则 ═══ -->
      <el-tab-pane label="脱敏规则" name="rules">
        <el-row :gutter="16" class="st">
          <el-col :span="6"><el-card><el-statistic title="规则总数" :value="rulesTotal" /></el-card></el-col>
          <el-col :span="6"><el-card><el-statistic title="启用中" :value="rulesActive" /></el-card></el-col>
        </el-row>
        <el-card>
          <template #header><div class="card-hd"><span>规则列表</span><el-button type="primary" size="small" @click="openRule()">+ 新建规则</el-button></div></template>
          <el-table :data="rules" stripe v-loading="rLoading">
            <el-table-column prop="name" label="名称" min-width="140" />
            <el-table-column prop="masking_type" label="脱敏类型" width="110"><template #default="{row}"><el-tag size="small">{{ typeLabel(row.masking_type) }}</el-tag></template></el-table-column>
            <el-table-column prop="masking_strategy" label="策略" width="90"><template #default="{row}"><el-tag size="small" type="info">{{ row.masking_strategy }}</el-tag></template></el-table-column>
            <el-table-column prop="field_pattern" label="匹配字段" min-width="150" />
            <el-table-column prop="priority" label="优先级" width="80" />
            <el-table-column label="状态" width="80"><template #default="{row}"><el-tag :type="row.is_active?'success':'info'" size="small">{{ row.is_active?'启用':'停用' }}</el-tag></template></el-table-column>
            <el-table-column label="操作" width="150"><template #default="{row}"><el-button size="small" @click="openRule(row)">编辑</el-button><el-button size="small" type="danger" @click="delRule(row)">删除</el-button></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ═══ 访问策略 ═══ -->
      <el-tab-pane label="访问策略" name="policies">
        <el-card>
          <template #header><div class="card-hd"><span>访问策略</span><el-button type="primary" size="small" @click="openPolicy()">+ 新建策略</el-button></div></template>
          <el-table :data="policies" stripe v-loading="pLoading">
            <el-table-column prop="name" label="名称" min-width="140" />
            <el-table-column prop="resource_type" label="资源类型" width="100"><template #default="{row}"><el-tag size="small">{{ row.resource_type }}</el-tag></template></el-table-column>
            <el-table-column prop="resource_pattern" label="资源匹配" min-width="150" />
            <el-table-column label="操作" width="90"><template #default="{row}"><el-tag size="small" type="warning">{{ row.action }}</el-tag></template></el-table-column>
            <el-table-column label="效果" width="80"><template #default="{row}"><el-tag :type="row.effect==='allow'?'success':'danger'" size="small">{{ row.effect }}</el-tag></template></el-table-column>
            <el-table-column label="状态" width="80"><template #default="{row}"><el-tag :type="row.is_active?'success':'info'" size="small">{{ row.is_active?'启用':'停用' }}</el-tag></template></el-table-column>
            <el-table-column label="操作" width="150"><template #default="{row}"><el-button size="small" @click="openPolicy(row)">编辑</el-button><el-button size="small" type="danger" @click="delPolicy(row)">删除</el-button></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ═══ 审计日志 ═══ -->
      <el-tab-pane label="审计日志" name="audit">
        <el-card>
          <template #header>
            <div class="card-hd">
              <span>审计日志</span>
              <div style="display:flex;gap:8px">
                <el-input v-model="auditFilter.user_id" placeholder="用户ID" size="small" style="width:100px" clearable @change="loadAudit" />
                <el-select v-model="auditFilter.result" placeholder="结果" size="small" style="width:100px" clearable @change="loadAudit"><el-option label="成功" value="success" /><el-option label="拒绝" value="denied" /></el-select>
              </div>
            </div>
          </template>
          <el-table :data="auditLogs" stripe v-loading="aLoading">
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="username" label="用户" width="100" />
            <el-table-column prop="resource_type" label="资源类型" width="90" />
            <el-table-column prop="resource" label="资源" min-width="140" />
            <el-table-column prop="action" label="操作" width="80" />
            <el-table-column label="结果" width="80"><template #default="{row}"><el-tag :type="row.result==='success'?'success':'danger'" size="small">{{ row.result }}</el-tag></template></el-table-column>
            <el-table-column prop="ip_address" label="IP" width="130" />
            <el-table-column prop="created_at" label="时间" width="160" />
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ═══ 敏感度分析 ═══ -->
      <el-tab-pane label="敏感度分析" name="analyze">
        <el-card>
          <template #header><span>字段敏感度分析</span></template>
          <p style="color:var(--tx2);margin-bottom:12px">输入 JSON 数据，自动识别敏感字段并推荐脱敏规则</p>
          <el-input v-model="analyzeInput" type="textarea" :rows="6" placeholder='[{"姓名":"张三","手机":"13800138000","邮箱":"zhang@example.com","部门":"技术部"}]' style="margin-bottom:12px" />
          <el-button type="primary" @click="doAnalyze" :loading="analyzing">分析敏感度</el-button>
          <div v-if="analyzeResult" style="margin-top:16px">
            <el-alert :title="`发现 ${analyzeResult.sensitive_fields}/${analyzeResult.total_fields} 个敏感字段`" :type="analyzeResult.sensitive_fields>0?'warning':'success'" :closable="false" show-icon />
            <el-table :data="Object.entries(analyzeResult.field_details||{}).map(([k,v])=>({field:k,...v}))" stripe style="margin-top:12px">
              <el-table-column prop="field" label="字段" width="150" />
              <el-table-column label="敏感" width="80"><template #default="{row}"><el-tag :type="row.sensitive?'danger':'success'" size="small">{{ row.sensitive?'是':'否' }}</el-tag></template></el-table-column>
              <el-table-column prop="masking_type" label="推荐类型" width="130" />
              <el-table-column prop="recommended_rule" label="推荐规则" min-width="150" />
            </el-table>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ═══ 执行脱敏 ═══ -->
      <el-tab-pane label="执行脱敏" name="execute">
        <el-card>
          <template #header><span>选择数据集执行脱敏</span></template>
          <el-form label-width="100px">
            <el-form-item label="数据集"><el-select v-model="execForm.dataset_id" placeholder="按ID或名称选择数据集" style="width:100%" filterable><el-option v-for="ds in datasets" :key="ds.id" :label="`#${ds.id} ${ds.name}`" :value="ds.id" /></el-select></el-form-item>
            <el-form-item label="脱敏规则"><el-select v-model="execForm.rule_id" placeholder="选择规则（可选，留空用全部启用规则）" style="width:100%" clearable filterable><el-option v-for="r in rules" :key="r.id" :label="`${r.name} (${r.masking_type})`" :value="r.id" /></el-select></el-form-item>
            <el-form-item label="输出方式">
              <el-radio-group v-model="execForm.output_mode">
                <el-radio value="preview">仅预览（前100条）</el-radio>
                <el-radio value="new_dataset">后台全量生成新数据集</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item v-if="execForm.output_mode==='new_dataset'" label="输出名称"><el-input v-model="execForm.output_name" placeholder="默认：源数据集名称_脱敏版" /></el-form-item>
            <el-form-item>
              <el-button type="primary" @click="executeMasking" :loading="execLoading">🚀 开始脱敏</el-button>
            </el-form-item>
          </el-form>
          <div v-if="execResult" style="margin-top:16px">
            <el-alert :title="execResult.message || `脱敏完成：处理 ${execResult.total_records} 条记录，${execResult.masked_fields} 个字段被脱敏`" :type="execResult.status==='error'?'error':'success'" :closable="false" show-icon />
            <el-table :data="(execResult.preview||[]).slice(0,5)" stripe style="margin-top:12px" max-height="300">
              <el-table-column v-for="col in execColumns" :key="col" :prop="col" :label="col" min-width="120" />
            </el-table>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 规则/策略编辑对话框 -->
    <el-dialog v-model="dlg.show" :title="dlg.title" width="560px" @closed="resetDialog">
      <el-form :model="dlg.data" label-width="100px">
        <template v-if="dlg.type==='rule'">
          <el-form-item label="规则名称"><el-input v-model="dlg.data.name" /></el-form-item>
          <el-form-item label="脱敏类型"><el-select v-model="dlg.data.masking_type" style="width:100%"><el-option v-for="t in maskTypes" :key="t.value" :label="t.label" :value="t.value" /></el-select></el-form-item>
          <el-form-item label="脱敏策略"><el-select v-model="dlg.data.masking_strategy" style="width:100%"><el-option v-for="s in strategies" :key="s" :label="s" :value="s" /></el-select></el-form-item>
          <el-form-item label="匹配模式"><el-input v-model="dlg.data.field_pattern" placeholder="正则，如 .*phone.*" /></el-form-item>
          <el-form-item label="优先级"><el-input-number v-model="dlg.data.priority" :min="1" :max="1000" /></el-form-item>
          <el-form-item label="启用"><el-switch v-model="dlg.data.is_active" :active-value="1" :inactive-value="0" /></el-form-item>
        </template>
        <template v-else>
          <el-form-item label="策略名称"><el-input v-model="dlg.data.name" /></el-form-item>
          <el-form-item label="资源类型"><el-select v-model="dlg.data.resource_type" style="width:100%"><el-option v-for="r in ['table','column','api','file']" :key="r" :label="r" :value="r" /></el-select></el-form-item>
          <el-form-item label="资源匹配"><el-input v-model="dlg.data.resource_pattern" /></el-form-item>
          <el-form-item label="操作"><el-select v-model="dlg.data.action" style="width:100%"><el-option v-for="a in ['read','write','delete','admin','*']" :key="a" :label="a" :value="a" /></el-select></el-form-item>
          <el-form-item label="效果"><el-radio-group v-model="dlg.data.effect"><el-radio value="allow">允许</el-radio><el-radio value="deny">拒绝</el-radio></el-radio-group></el-form-item>
          <el-form-item label="启用"><el-switch v-model="dlg.data.is_active" :active-value="1" :inactive-value="0" /></el-form-item>
        </template>
      </el-form>
      <template #footer><el-button @click="dlg.show=false">取消</el-button><el-button type="primary" @click="saveDlg">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { get, post, put, del as delApi } from '@/api/legacy'
import { ElMessage, ElMessageBox } from 'element-plus'

const tab = ref('rules')
const rLoading = ref(false), pLoading = ref(false), aLoading = ref(false), analyzing = ref(false)
const rules = ref<any[]>([]), policies = ref<any[]>([]), auditLogs = ref<any[]>([])
const rulesTotal = computed(() => rules.value.length)
const rulesActive = computed(() => rules.value.filter(r => r.is_active).length)
const auditFilter = reactive({ user_id: '', result: '' })
const analyzeInput = ref(''), analyzeResult = ref<any>(null)
const execForm = reactive({ dataset_id: '', rule_id: '', output_mode: 'preview', output_name: '' })
const execLoading = ref(false), execResult = ref<any>(null), execColumns = ref<string[]>([])
const datasets = ref<any[]>([])
const dlg = reactive<{show:boolean,title:string,type:string,data:any}>({show:false,title:'',type:'',data:{}})

function resetDialog() {
  Object.assign(dlg, { show: false, title: '', type: '', data: {} })
}

const maskTypes = [
  { value: 'phone', label: '手机号' }, { value: 'id_card', label: '身份证' },
  { value: 'bank_card', label: '银行卡' }, { value: 'email', label: '邮箱' },
  { value: 'name', label: '姓名' }, { value: 'address', label: '地址' },
  { value: 'custom', label: '自定义' },
]
const strategies = ['mask', 'hash', 'encrypt', 'replace', 'shuffle', 'truncate', 'fpe']

function typeLabel(t: string) { return maskTypes.find(x => x.value === t)?.label || t }

async function loadRules() { rLoading.value = true; try { const d = await get('/masking/rules'); rules.value = d.items || [] } catch (e) {} finally { rLoading.value = false } }
async function loadDatasets() { try { const d = await get('/datasets?limit=200'); datasets.value = d.items || [] } catch (e) { ElMessage.error('数据集列表加载失败') } }
async function loadPolicies() { pLoading.value = true; try { const d = await get('/masking/policies'); policies.value = d.items || [] } catch (e) {} finally { pLoading.value = false } }
async function loadAudit() { aLoading.value = true; try { const p = new URLSearchParams(); if (auditFilter.user_id) p.set('user_id', auditFilter.user_id); if (auditFilter.result) p.set('result', auditFilter.result); const d = await get('/masking/audit?' + p.toString()); auditLogs.value = d.items || [] } catch (e) {} finally { aLoading.value = false } }

function openRule(row?: any) { dlg.type = 'rule'; dlg.title = row ? '编辑规则' : '新建规则'; dlg.data = row ? { ...row } : { name: '', masking_type: 'phone', masking_strategy: 'mask', field_pattern: '', priority: 100, is_active: 1 }; dlg.show = true }
function openPolicy(row?: any) { dlg.type = 'policy'; dlg.title = row ? '编辑策略' : '新建策略'; dlg.data = row ? { ...row } : { name: '', resource_type: 'table', resource_pattern: '', action: 'read', effect: 'deny', priority: 100, is_active: 1 }; dlg.show = true }

async function saveDlg() {
  try {
    if (dlg.type === 'rule') {
      if (dlg.data.id) await put(`/masking/rules/${dlg.data.id}`, dlg.data)
      else await post('/masking/rules', dlg.data)
      ElMessage.success('规则已保存'); loadRules()
    } else {
      if (dlg.data.id) await put(`/masking/policies/${dlg.data.id}`, dlg.data)
      else await post('/masking/policies', dlg.data)
      ElMessage.success('策略已保存'); loadPolicies()
    }
    dlg.show = false
  } catch (e: any) { ElMessage.error(e?.message || '保存失败') }
}

async function delRule(row: any) {
  try { await ElMessageBox.confirm(`删除规则 "${row.name}"？`, '确认', { type: 'warning' }); await delApi(`/masking/rules/${row.id}`); ElMessage.success('已删除'); loadRules() } catch (e) {}
}
async function delPolicy(row: any) {
  try { await ElMessageBox.confirm(`删除策略 "${row.name}"？`, '确认', { type: 'warning' }); await delApi(`/masking/policies/${row.id}`); ElMessage.success('已删除'); loadPolicies() } catch (e) {}
}

async function doAnalyze() {
  if (!analyzeInput.value.trim()) return
  analyzing.value = true
  try {
    const data = JSON.parse(analyzeInput.value)
    const res = await post('/masking/analyze', { data: Array.isArray(data) ? data : [data] })
    analyzeResult.value = res
  } catch (e: any) { ElMessage.error(e?.message || '分析失败') }
  finally { analyzing.value = false }
}

function onTabChange(t: string) {
  if (t === 'rules') loadRules()
  else if (t === 'policies') loadPolicies()
  else if (t === 'audit') loadAudit()
}

async function executeMasking() {
  if (!execForm.dataset_id) { ElMessage.warning('请选择数据集'); return }
  execLoading.value = true; execResult.value = null
  try {
    if (execForm.output_mode === 'new_dataset') {
      const job = await post('/masking/dataset-jobs', { dataset_id: Number(execForm.dataset_id), rule_id: execForm.rule_id ? Number(execForm.rule_id) : null, output_name: execForm.output_name || null })
      execResult.value = { status: 'running', message: '后台全量脱敏任务已启动，页面可关闭后再回来查看。' }
      const poll = async () => {
        const current = await get(`/masking/dataset-jobs/${job.id}`)
        if (current.status === 'running') { setTimeout(poll, 1200); return }
        execLoading.value = false
        execResult.value = current.status === 'success'
          ? { ...current, total_records: current.processed_rows, masked_fields: current.masked_cells, message: `全量脱敏完成：处理 ${current.processed_rows} 条记录，已生成数据集 #${current.dataset_id}` }
          : { ...current, message: `脱敏任务失败：${current.error || '未知错误'}` }
        if (current.status === 'success') { ElMessage.success('全量脱敏完成，已生成新的数据集'); loadDatasets() }
      }
      setTimeout(poll, 500)
      return
    }
    const source = await get(`/datasets/${execForm.dataset_id}/rows?offset=0&limit=1000`)
    const rows = source.rows || []
    const columns = source.columns || []
    if (!rows.length) throw new Error('数据集没有可脱敏的记录')
    // 数据集预览接口返回二维行数组；脱敏接口需要“字段名: 值”的记录。
    // 显式恢复字段名，Excel、CSV 等格式的预览都走同一条路径。
    const records = rows.map((row: any[]) => Object.fromEntries(columns.map((column: string, index: number) => [column, row[index]])))
    const result = await post('/masking/apply', {
      data: records,
      rule_id: execForm.rule_id ? Number(execForm.rule_id) : undefined,
    })
    const preview = result.data || []
    const maskedFields = columns.filter((column: string) => preview.some((row: any, index: number) => String(row?.[column] ?? '') !== String(records[index]?.[column] ?? ''))).length
    execResult.value = { total_records: records.length, masked_fields: maskedFields, preview }
    execColumns.value = columns
    ElMessage.success('脱敏预览完成；未覆盖原始数据集')
  } catch (e: any) { ElMessage.error(e?.message || '脱敏执行失败') }
  finally { execLoading.value = false }
}

onMounted(() => { loadRules(); loadDatasets() })
</script>

<style scoped>
.pg { padding: 24px; }
.st { margin-bottom: 20px; }
.card-hd { display: flex; justify-content: space-between; align-items: center; }
</style>
