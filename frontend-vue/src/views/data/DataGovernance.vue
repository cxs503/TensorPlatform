<template>
  <div class="page">
    <h1>🔧 数据治理</h1>
    <p>数据标准管理、质量规则配置、数据清洗与治理</p>

    <div class="stats" v-if="stats">
      <div class="stat"><b>{{ stats.total_tables || 0 }}</b> 数据表</div>
      <div class="stat"><b>{{ stats.total_rules || 0 }}</b> 质量规则</div>
      <div class="stat"><b>{{ stats.total_checks || 0 }}</b> 检查记录</div>
      <div class="stat"><b>{{ stats.pass_rate || 0 }}%</b> 通过率</div>
    </div>

    <div class="card">
      <div class="card-header">
        <span>📋 数据表 ({{ tables.length }})</span>
        <button @click="scanTables" :disabled="scanning">{{ scanning ? '扫描中...' : '🔍 扫描' }}</button>
      </div>
      <table v-if="tables.length">
        <thead><tr><th>表名</th><th>行数</th><th>列数</th><th>质量分</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="t in tables" :key="t.table_name">
            <td>{{ t.table_name }}</td>
            <td>{{ t.row_count }}</td>
            <td>{{ t.column_count }}</td>
            <td :style="{color: t.quality_score > 80 ? '#22c55e' : '#f59e0b'}">{{ t.quality_score || '-' }}</td>
            <td><button @click="checkTable(t.table_name)">✅ 检查</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else style="color:#94a3b8">点击"扫描"发现数据表</p>
    </div>

    <div class="card">
      <div class="card-header">
        <span>📐 质量标准 ({{ standards.length }})</span>
      </div>
      <table v-if="standards.length">
        <thead><tr><th>标准名</th><th>类别</th><th>描述</th></tr></thead>
        <tbody>
          <tr v-for="s in standards" :key="s.name">
            <td>{{ s.name }}</td>
            <td>{{ s.category }}</td>
            <td>{{ s.description }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-header">
        <span>🔍 质量规则 ({{ rules.length }})</span>
        <button @click="showRuleForm = !showRuleForm">+ 新建规则</button>
      </div>
      <table v-if="rules.length">
        <thead><tr><th>规则名</th><th>类型</th><th>表</th><th>列</th><th>参数</th></tr></thead>
        <tbody>
          <tr v-for="r in rules" :key="r.name">
            <td>{{ r.name }}</td>
            <td>{{ r.rule_type }}</td>
            <td>{{ r.table_name }}</td>
            <td>{{ r.column_name }}</td>
            <td>{{ r.params }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card" v-if="checkResult">
      <div class="card-header"><span>✅ 检查结果</span></div>
      <pre>{{ JSON.stringify(checkResult, null, 2) }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import http from '@/api/request'

const stats = ref(null)
const tables = ref([])
const standards = ref([])
const rules = ref([])
const scanning = ref(false)
const checkResult = ref(null)
const showRuleForm = ref(false)

onMounted(async () => {
  const summary = await http.get('/data-governance/stats')
  stats.value = { total_tables: summary.tables || 0, total_rules: summary.standards || 0, total_checks: summary.lineages || 0, pass_rate: summary.pass_rate || 0 }
  standards.value = (await http.get('/data-governance/standards')).items || []
  rules.value = (await http.get('/data-governance/quality-rules')).items || []
})

async function scanTables() {
  scanning.value = true
  const res = await http.post('/data-governance/scan')
  const details = res.details || {}
  tables.value = Object.entries(details).map(([table_name, info]) => ({ table_name, row_count: info.count || 0, column_count: info.columns || 0, quality_score: info.quality_score || 0 }))
  scanning.value = false
}

async function checkTable(name) {
  checkResult.value = await http.post('/data-governance/check', { table_name: name })
}
</script>

<style scoped>
.page { padding: 20px; max-width: 1200px; }
h1 { font-size: 24px; margin-bottom: 8px; }
.stats { display: flex; gap: 20px; margin: 16px 0; }
.stat { background: var(--bg-card); padding: 16px 24px; border-radius: 8px; text-align: center; }
.stat b { font-size: 24px; display: block; }
.card { background: var(--bg-card); border-radius: 8px; padding: 16px; margin: 16px 0; }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-weight: 600; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; }
button { background: var(--primary); color: #fff; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
button:disabled { opacity: 0.5; }
pre { font-size: 12px; max-height: 300px; overflow: auto; background: #1e293b; padding: 12px; border-radius: 6px; color: #e2e8f0; }
</style>
