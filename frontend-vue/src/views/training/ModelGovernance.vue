<template>
  <div class="mg-page">
    <PageHeader title="模型治理" desc="审批流程 · 审计日志 · 合规管理" />

    <!-- 仪表盘 -->
    <el-row :gutter="16" class="stats">
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv warn">{{ dash.pending_approvals }}</div><div class="sl">待审批</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv ok">{{ dash.approved }}</div><div class="sl">已批准</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv">{{ dash.total_audits }}</div><div class="sl">审计记录</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv">{{ approvals.length }}</div><div class="sl">审批单</div></el-card></el-col>
    </el-row>

    <!-- 最近活动 -->
    <el-card v-if="dash.recent_activity?.length" shadow="never" class="section">
      <template #header><span>📌 最近活动</span></template>
      <el-timeline>
        <el-timeline-item v-for="a in dash.recent_activity" :key="a.time"
          :timestamp="formatTime(a.time)" placement="top" :color="a.action.includes('approved')?'#67C23A':a.action.includes('rejected')?'#F56C6C':'#409EFF'">
          {{ a.action }} — {{ a.user }}
        </el-timeline-item>
      </el-timeline>
    </el-card>

    <el-row :gutter="16">
      <!-- 审批列表 -->
      <el-col :span="14">
        <el-card shadow="never" class="section">
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span>📋 审批列表</span>
              <el-button type="primary" size="small" @click="showCreateDialog = true">新建审批</el-button>
            </div>
          </template>
          <el-table :data="approvals" size="small" max-height="400" stripe>
            <el-table-column prop="request_id" label="审批号" width="130" />
            <el-table-column prop="model_id" label="模型ID" width="80" />
            <el-table-column prop="action" label="操作" width="100" />
            <el-table-column prop="requester" label="申请人" width="100" />
            <el-table-column label="状态" width="100">
              <template #default="{row}">
                <el-tag :type="row.status==='approved'?'success':row.status==='rejected'?'danger':'warning'" size="small">
                  {{ row.status==='approved'?'已批准':row.status==='rejected'?'已拒绝':'待审批' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="comments" label="备注" min-width="120" show-overflow-tooltip />
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{row}">
                <el-button v-if="row.status==='pending'" type="success" size="small" @click="approve(row.request_id)">批准</el-button>
                <el-button v-if="row.status==='pending'" type="danger" size="small" @click="reject(row.request_id)">拒绝</el-button>
                <span v-else style="color:#c0c4cc">-</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 审计日志 -->
      <el-col :span="10">
        <el-card shadow="never" class="section">
          <template #header><span>📝 审计日志</span></template>
          <div class="audit-list" v-if="auditLogs.length">
            <div v-for="a in auditLogs" :key="a.id" class="audit-item">
              <div class="audit-action">{{ a.action }}</div>
              <div class="audit-meta">模型 #{{ a.model_id }} · {{ a.user }} · {{ formatTime(a.created_at) }}</div>
            </div>
          </div>
          <el-empty v-else description="暂无审计记录" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 新建审批弹窗 -->
    <el-dialog v-model="showCreateDialog" title="新建审批" width="450px">
      <el-form :model="createForm" label-width="80px">
        <el-form-item label="模型ID"><el-input-number v-model="createForm.model_id" :min="1" style="width:100%" /></el-form-item>
        <el-form-item label="操作"><el-select v-model="createForm.action" style="width:100%"><el-option label="部署上线" value="deploy" /><el-option label="下线" value="undeploy" /><el-option label="更新" value="update" /><el-option label="删除" value="delete" /></el-select></el-form-item>
        <el-form-item label="申请人"><el-input v-model="createForm.requester" placeholder="admin" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="createForm.comments" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showCreateDialog=false">取消</el-button><el-button type="primary" @click="createApproval" :loading="creating">提交</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const dash = reactive({ pending_approvals: 0, approved: 0, total_audits: 0, recent_activity: [] as any[] })
const approvals = ref<any[]>([])
const auditLogs = ref<any[]>([])
const showCreateDialog = ref(false)
const creating = ref(false)
const createForm = reactive({ model_id: 1, action: 'deploy', requester: 'admin', comments: '' })

async function loadAll() {
  try {
    const [d, a, l] = await Promise.all([
      get('/governance/dashboard'), get('/governance/approvals'), get('/governance/audit-logs', { limit: 20 })
    ])
    Object.assign(dash, d)
    approvals.value = a || []
    auditLogs.value = l || []
  } catch { /* */ }
}

async function createApproval() {
  creating.value = true
  try {
    await post('/governance/approvals', { ...createForm })
    ElMessage.success('审批已提交')
    showCreateDialog.value = false
    loadAll()
  } catch { ElMessage.error('提交失败') }
  finally { creating.value = false }
}

async function approve(rid: string) {
  try { await post(`/governance/approvals/${rid}/approve`); ElMessage.success('已批准'); loadAll() }
  catch { ElMessage.error('操作失败') }
}

async function reject(rid: string) {
  try {
    const { value } = await ElMessageBox.prompt('请输入拒绝原因', '拒绝审批', { inputType: 'textarea' })
    await post(`/governance/approvals/${rid}/reject`, { reason: value || '' })
    ElMessage.success('已拒绝')
    loadAll()
  } catch { /* cancel */ }
}

function formatTime(t: string) { return t ? new Date(t).toLocaleString('zh-CN') : '-' }

onMounted(loadAll)
</script>

<style scoped>
.mg-page { max-width: 1200px; padding: 20px; }
.stats { margin-bottom: 16px; }
.sv { font-size: 28px; font-weight: 700; text-align: center; }
.sv.ok { color: #67C23A; } .sv.warn { color: #E6A23C; }
.sl { font-size: 12px; color: #909399; margin-top: 4px; text-align: center; }
.section { margin-bottom: 16px; }
.audit-list { max-height: 400px; overflow-y: auto; }
.audit-item { padding: 10px 0; border-bottom: 1px solid #ebeef5; }
.audit-action { font-weight: 600; font-size: 14px; }
.audit-meta { font-size: 12px; color: #909399; margin-top: 2px; }
</style>
