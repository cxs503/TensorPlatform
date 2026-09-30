<template>
  <div class="billing-page">
    <div class="page-header">
      <h2>💰 成本计费</h2>
      <el-date-picker v-model="dateRange" type="daterange" range-separator="至" start-placeholder="开始" end-placeholder="结束" size="small" style="width:260px" />
    </div>

    <el-row :gutter="16" class="stats-row">
      <el-col :span="6">
        <el-card><el-statistic title="本月费用 (¥)" :value="stats.total_cost" :precision="2" /></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><el-statistic title="GPU 时长 (h)" :value="stats.gpu_hours" :precision="1" /></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><el-statistic title="存储用量 (GB)" :value="stats.storage_gb" :precision="1" /></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><el-statistic title="API 调用次数" :value="stats.api_calls" /></el-card>
      </el-col>
    </el-row>

    <el-card class="section-card">
      <template #header><span>计费记录</span></template>
      <el-table :data="records" stripe v-loading="loading">
        <el-table-column prop="date" label="日期" width="120" />
        <el-table-column prop="resource_type" label="资源类型" width="120">
          <template #default="{row}"><el-tag size="small">{{ row.resource_type }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="usage" label="用量" width="100" />
        <el-table-column prop="unit" label="单位" width="80" />
        <el-table-column prop="unit_price" label="单价 (¥)" width="100" />
        <el-table-column prop="cost" label="费用 (¥)" width="100">
          <template #default="{row}"><strong>{{ row.cost?.toFixed(2) }}</strong></template>
        </el-table-column>
        <el-table-column prop="project" label="项目" min-width="130" />
        <el-table-column prop="description" label="说明" min-width="150" show-overflow-tooltip />
      </el-table>
    </el-card>

    <el-dialog v-model="showCharge" title="手动计费" width="450px">
      <el-form :model="chargeForm" label-width="90px">
        <el-form-item label="资源类型"><el-select v-model="chargeForm.resource_type" style="width:100%"><el-option v-for="t in ['gpu','cpu','storage','network','api']" :key="t" :label="t" :value="t" /></el-select></el-form-item>
        <el-form-item label="用量"><el-input-number v-model="chargeForm.usage" :min="0" :precision="2" style="width:100%" /></el-form-item>
        <el-form-item label="单位"><el-input v-model="chargeForm.unit" placeholder="小时/GB/次" /></el-form-item>
        <el-form-item label="单价 (¥)"><el-input-number v-model="chargeForm.unit_price" :min="0" :precision="4" style="width:100%" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="chargeForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCharge=false">取消</el-button>
        <el-button type="primary" @click="submitCharge" :loading="submitting">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'

const loading = ref(false)
const submitting = ref(false)
const showCharge = ref(false)
const dateRange = ref<any>(null)
const records = ref<any[]>([])
const stats = ref({ total_cost: 0, gpu_hours: 0, storage_gb: 0, api_calls: 0 })
const chargeForm = ref({ resource_type:'gpu', usage:0, unit:'小时', unit_price:0, description:'' })

onMounted(() => { fetchRecords(); fetchStats() })

async function fetchRecords() {
  loading.value = true
  try { const d = await get('/billing/records'); records.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){}
  finally { loading.value = false }
}

async function fetchStats() {
  try { const d = await get('/billing/stats'); if(d) stats.value = { ...stats.value, ...d } }
  catch(e:any){}
}

async function submitCharge() {
  submitting.value = true
  try { await post('/billing/charge', chargeForm.value); ElMessage.success('计费记录已添加'); showCharge.value=false; fetchRecords(); fetchStats() }
  catch(e:any) { ElMessage.error(e?.message||'提交失败') }
  finally { submitting.value = false }
}
</script>

<style scoped>
.billing-page { padding: 24px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 20px; }
.stats-row { margin-bottom: 20px; }
.section-card { margin-bottom: 20px; }
</style>
