<template>
  <div class="report-page">
    <div class="page-header">
      <h2>📊 报表中心</h2>
      <el-button type="primary" @click="showCreate = true">+ 生成报表</el-button>
    </div>

    <el-row :gutter="16" class="stats-row">
      <el-col :span="8">
        <el-card><el-statistic title="已生成报表" :value="reports.length" /></el-card>
      </el-col>
      <el-col :span="8">
        <el-card><el-statistic title="可用模板" :value="templates.length" /></el-card>
      </el-col>
      <el-col :span="8">
        <el-card><el-statistic title="今日下载" :value="todayDownloads" /></el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="8">
        <el-card header="快速报表">
          <el-button v-for="qr in quickReports" :key="qr.key" style="width:100%;margin-bottom:8px" @click="generateQuick(qr.key)">{{ qr.label }}</el-button>
        </el-card>
      </el-col>
      <el-col :span="16">
        <el-card>
          <el-table :data="reports" stripe v-loading="loading">
            <el-table-column prop="name" label="报表名称" min-width="180" />
            <el-table-column prop="report_type" label="类型" width="120">
              <template #default="{row}"><el-tag size="small">{{ row.report_type }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="report_format" label="格式" width="80">
              <template #default="{row}"><el-tag size="small" type="info">{{ row.report_format?.toUpperCase() }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="created_at" label="生成时间" width="170" />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{row}">
                <el-tag :type="row.status==='completed'?'success':row.status==='processing'?'warning':'info'" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160">
              <template #default="{row}">
                <el-button size="small" @click="downloadReport(row)" :disabled="row.status!=='completed'">下载</el-button>
                <el-button size="small" type="danger" @click="deleteReport(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="showCreate" title="生成报表" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="报表名称"><el-input v-model="form.title" placeholder="月度训练报告" /></el-form-item>
        <el-form-item label="报表模板">
          <el-select v-model="form.template" style="width:100%">
            <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="输出格式">
          <el-select v-model="form.format" style="width:100%">
            <el-option v-for="f in ['pdf','csv','xlsx','html']" :key="f" :label="f.toUpperCase()" :value="f" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期范围">
          <el-date-picker v-model="form.dateRange" type="daterange" range-separator="至" start-placeholder="开始" end-placeholder="结束" style="width:100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate=false">取消</el-button>
        <el-button type="primary" @click="createReport" :loading="generating">生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, del } from '@/api/legacy'

const loading = ref(false)
const generating = ref(false)
const showCreate = ref(false)
const reports = ref<any[]>([])
const templates = ref<any[]>([])
const todayDownloads = ref(0)

const quickReports = [
  { key: 'equipment-status', label: '⚙️ 设备状态报表' },
  { key: 'energy-consumption', label: '⚡ 能耗统计报表' },
  { key: 'alert-statistics', label: '🚨 告警统计报表' },
]
const form = ref({ title:'', template:'', format:'pdf', dateRange: null as any })

onMounted(() => { fetchReports(); fetchTemplates() })

async function fetchReports() {
  loading.value = true
  try { const d = await get('/reports'); reports.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){ }
  finally { loading.value = false }
}
async function fetchTemplates() {
  try { const d = await get('/reports/templates'); templates.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){}
}

async function createReport() {
  generating.value = true
  try {
    const f = form.value
    // 映射到后端契约:{name, report_type, report_format, date_from, date_to}
    const payload: any = {
      name: f.title || '报表',
      report_type: f.template || 'equipment_status',
      report_format: f.format || 'pdf',
      date_from: f.dateRange ? f.dateRange[0] : null,
      date_to: f.dateRange ? f.dateRange[1] : null,
    }
    const r = await post('/reports', payload)
    ElMessage.success('报表生成任务已创建')
    showCreate.value = false
    if (r?.id) {
      await post(`/reports/${r.id}/generate`)
      ElMessage.success('报表已生成')
    }
    fetchReports()
  } catch(e:any) { ElMessage.error(e?.message||'操作失败') }
  finally { generating.value = false }
}

async function generateQuick(key: string) {
  try {
    const r = await get(`/reports/quick/${key}`)
    if (r?.download_url) window.open(r.download_url)
    else ElMessage.success(`${key} 报表已生成`)
    fetchReports()
  } catch(e:any) { ElMessage.error(e?.message||'生成失败') }
}

async function downloadReport(row: any) {
  try {
    const r = await get(`/reports/${row.id}/download`)
    if (r?.url) window.open(r.url)
    else ElMessage.success('下载已开始')
    todayDownloads.value++
  } catch(e:any) { ElMessage.error('下载失败') }
}

async function deleteReport(id: string) {
  await ElMessageBox.confirm('确认删除？', '警告', {type:'warning'})
  try { await del(`/reports/${id}`); ElMessage.success('已删除'); fetchReports() }
  catch(e:any){ ElMessage.error(e?.message||'删除失败') }
}
</script>

<style scoped>
.report-page { padding: 24px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 20px; }
.stats-row { margin-bottom: 20px; }
</style>
