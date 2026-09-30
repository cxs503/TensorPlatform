<template>
  <div class="pg">
    <PageHeader title="🧠 知识图谱" desc="工业工艺规则库 · 智能工况匹配 · 结构化经验知识" />

    <el-tabs v-model="tab">
      <!-- ═══ 知识浏览 ═══ -->
      <el-tab-pane label="知识浏览" name="browse">
        <el-row :gutter="16" class="st">
          <el-col :span="6"><el-card><el-statistic title="规则总数" :value="stats.total_rules" /></el-card></el-col>
          <el-col :span="6"><el-card><el-statistic title="知识领域" :value="stats.domains" /></el-card></el-col>
        </el-row>

        <el-card>
          <template #header><div class="card-hd"><span>知识领域</span></div></template>
          <el-table :data="domains" stripe>
            <el-table-column prop="name" label="领域" width="180"><template #default="{row}"><span style="font-weight:600">{{ row.name }}</span></template></el-table-column>
            <el-table-column prop="count" label="规则数" width="100" />
            <el-table-column label="操作" width="120"><template #default="{row}"><el-button size="small" @click="filterDomain(row.name)">查看规则</el-button></template></el-table-column>
          </el-table>
        </el-card>

        <el-card style="margin-top:16px">
          <template #header><div class="card-hd"><span>搜索知识</span><el-button type="primary" size="small" @click="openRule()">+ 添加规则</el-button></div></template>
          <div style="display:flex;gap:8px;margin-bottom:12px">
            <el-input v-model="keyword" placeholder="搜索规则/原因/措施" style="width:240px" @keyup.enter="search" />
            <el-select v-model="domainFilter" placeholder="领域筛选" clearable style="width:140px" @change="search"><el-option v-for="d in domainNames" :key="d" :label="d" :value="d" /></el-select>
            <el-button type="primary" @click="search">搜索</el-button>
          </div>
          <el-table :data="results" stripe v-loading="searching">
            <el-table-column prop="domain" label="领域" width="130"><template #default="{row}"><el-tag size="small">{{ row.domain }}</el-tag></template></el-table-column>
            <el-table-column prop="rule" label="规则名称" min-width="180" />
            <el-table-column prop="cause" label="原因" min-width="200" show-overflow-tooltip />
            <el-table-column prop="action" label="措施" min-width="220" show-overflow-tooltip />
            <el-table-column prop="confidence" label="置信度" width="90" align="center">
              <template #default="{row}"><el-progress :percentage="Math.round(row.confidence * 100)" :stroke-width="6" :color="row.confidence>0.85?'#67c23a':row.confidence>0.7?'#e6a23c':'#f56c6c'" /></template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ═══ 工况匹配 ═══ -->
      <el-tab-pane label="工况匹配" name="match">
        <el-card>
          <template #header><span>根据当前工况智能匹配知识规则</span></template>
          <p style="color:var(--tx2);margin-bottom:16px">输入实时传感器参数，系统自动匹配适用的工艺规则</p>
          <el-form :model="cond" label-width="120px" inline>
            <el-form-item label="温度 (°C)"><el-input-number v-model="cond.temperature" :min="0" :max="2000" /></el-form-item>
            <el-form-item label="压差 (kPa)"><el-input-number v-model="cond.pressure_diff" :min="0" :max="100" :precision="1" /></el-form-item>
            <el-form-item label="风压 (kPa)"><el-input-number v-model="cond.wind_pressure" :min="0" :max="500" :precision="1" /></el-form-item>
            <el-form-item label="流量 (m³/h)"><el-input-number v-model="cond.flow_rate" :min="0" :max="10000" /></el-form-item>
            <el-form-item><el-button type="primary" @click="doMatch" :loading="matching">开始匹配</el-button></el-form-item>
          </el-form>
          <div v-if="matchResult" style="margin-top:16px">
            <el-alert :title="`匹配到 ${matchResult.total} 条规则`" :type="matchResult.total>0?'warning':'info'" :closable="false" show-icon />
            <el-table :data="matchResult.matches||[]" stripe style="margin-top:12px">
              <el-table-column prop="domain" label="领域" width="130" />
              <el-table-column prop="rule" label="规则" min-width="180" />
              <el-table-column prop="match_reason" label="匹配原因" min-width="180"><template #default="{row}"><el-tag type="warning" size="small">{{ row.match_reason }}</el-tag></template></el-table-column>
              <el-table-column prop="cause" label="原因" min-width="160" />
              <el-table-column prop="action" label="建议措施" min-width="200" />
              <el-table-column prop="confidence" label="置信度" width="90" />
            </el-table>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 添加规则对话框 -->
    <el-dialog v-model="dlg.show" title="添加知识规则" width="520px">
      <el-form :model="dlg.data" label-width="100px">
        <el-form-item label="知识领域"><el-select v-model="dlg.data.domain" filterable allow-create style="width:100%"><el-option v-for="d in domainNames" :key="d" :label="d" :value="d" /></el-select></el-form-item>
        <el-form-item label="规则名称"><el-input v-model="dlg.data.rule" placeholder="如: 终点温度偏高" /></el-form-item>
        <el-form-item label="原因"><el-input v-model="dlg.data.cause" type="textarea" :rows="2" placeholder="导致该现象的原因" /></el-form-item>
        <el-form-item label="措施"><el-input v-model="dlg.data.action" type="textarea" :rows="2" placeholder="应采取的措施" /></el-form-item>
        <el-form-item label="置信度"><el-slider v-model="dlg.data.confidence" :min="0.5" :max="1.0" :step="0.01" show-input /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dlg.show=false">取消</el-button><el-button type="primary" @click="saveRule">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import http from '@/api/request'
import { ElMessage } from 'element-plus'

const tab = ref('browse')
const stats = ref({ total_rules: 0, domains: 0 })
const domains = ref<any[]>([])
const results = ref<any[]>([])
const keyword = ref('')
const domainFilter = ref('')
const searching = ref(false)
const matching = ref(false)
const matchResult = ref<any>(null)
const domainNames = ref<string[]>([])
const cond = reactive({ temperature: 480, pressure_diff: 1.8, wind_pressure: 0, flow_rate: 0 })
const dlg = reactive({ show: false, data: { domain: '', rule: '', cause: '', action: '', confidence: 0.9 } })

async function loadDomains() {
  try {
    const d = await http.get('/knowledge-graph/domains')
    const items = d.categories || d.domains || []
    domainNames.value = items.map((x: any) => typeof x === 'string' ? x : x.name)
    domains.value = items.map((x: any) => ({ name: typeof x === 'string' ? x : x.name, count: typeof x === 'object' ? x.count : '-' }))
    const all = await http.get('/knowledge-graph/search')
    stats.value = { total_rules: all.total || 0, domains: domainNames.value.length }
  } catch (e) {}
}

async function search() {
  searching.value = true
  try {
    let kw = keyword.value.trim()
    if (domainFilter.value) kw = kw || domainFilter.value
    const r = await http.get('/knowledge-graph/search', { keyword: kw || undefined, domain: domainFilter.value || undefined })
    results.value = r.results || []
  } catch (e) {} finally { searching.value = false }
}

function filterDomain(name: string) { domainFilter.value = name; search() }

async function doMatch() {
  matching.value = true
  try {
    const res = await http.post('/knowledge-graph/match', { values: {
      temperature: cond.temperature,
      pressure_diff: cond.pressure_diff,
      wind_pressure: cond.wind_pressure,
      flow_rate: cond.flow_rate,
    }})
    matchResult.value = res
  } catch (e) {} finally { matching.value = false }
}

function openRule() { dlg.data = { domain: '', rule: '', cause: '', action: '', confidence: 0.9 }; dlg.show = true }

async function saveRule() {
  const d = dlg.data
  if (!d.domain || !d.rule || !d.cause || !d.action) { ElMessage.warning('请填写完整'); return }
  try {
    await http.post('/knowledge-graph/rules', d)
    ElMessage.success('规则已添加')
    dlg.show = false
    loadDomains()
  } catch (e: any) { ElMessage.error(e?.message || '添加失败') }
}

onMounted(loadDomains)
</script>

<style scoped>
.pg { padding: 24px; }
.st { margin-bottom: 20px; }
.card-hd { display: flex; justify-content: space-between; align-items: center; }
</style>