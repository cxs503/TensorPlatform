<template>
  <div class="page-root">
    <div class="page-header">
      <div><h2>🏢 组织与租户管理</h2><p>使用平台数据库中的组织体系，不再维护单独的内存租户。</p></div>
      <el-button type="primary" @click="openCreate">+ 新建组织</el-button>
    </div>
    <el-card>
      <el-table :data="organizations" stripe v-loading="loading" empty-text="暂无组织">
        <el-table-column prop="id" label="组织ID" width="90" />
        <el-table-column prop="name" label="组织名称" min-width="160" />
        <el-table-column prop="industry" label="行业" width="130" />
        <el-table-column prop="plan" label="套餐" width="100"><template #default="{row}"><el-tag>{{ row.plan }}</el-tag></template></el-table-column>
        <el-table-column prop="contact_email" label="联系邮箱" min-width="180" />
        <el-table-column prop="status" label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='active'?'success':'danger'">{{ row.status==='active'?'启用':'停用' }}</el-tag></template></el-table-column>
        <el-table-column label="创建时间" width="180"><template #default="{row}">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="170" fixed="right"><template #default="{row}">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" :type="row.status==='active'?'danger':'success'" @click="toggleStatus(row)">{{ row.status==='active'?'停用':'启用' }}</el-button>
        </template></el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="showDialog" :title="form.id ? '编辑组织' : '新建组织'" width="520px">
      <el-form label-width="100px">
        <el-form-item label="组织名称" required><el-input v-model="form.name" :disabled="Boolean(form.id)" /></el-form-item>
        <el-form-item v-if="!form.id" label="行业"><el-input v-model="form.industry" /></el-form-item>
        <el-form-item v-if="!form.id" label="联系邮箱"><el-input v-model="form.email" /></el-form-item>
        <el-form-item label="套餐"><el-select v-model="form.plan"><el-option label="免费版" value="free" /><el-option label="专业版" value="pro" /><el-option label="企业版" value="enterprise" /></el-select></el-form-item>
        <el-form-item v-if="form.id" label="状态"><el-switch v-model="form.active" active-text="启用" inactive-text="停用" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showDialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, put } from '@/api/legacy'

const organizations = ref<any[]>([])
const loading = ref(false)
const saving = ref(false)
const showDialog = ref(false)
const emptyForm = () => ({ id: null as number|null, name: '', industry: '', email: '', plan: 'free', active: true })
const form = ref(emptyForm())

async function loadOrganizations() {
  loading.value = true
  try {
    const result = await get<any>('/organizations')
    organizations.value = result?.items || []
  } catch (error: any) {
    organizations.value = []
    ElMessage.error(error?.message || '组织列表加载失败')
  } finally { loading.value = false }
}

function openCreate() { form.value = emptyForm(); showDialog.value = true }
function openEdit(row: any) {
  form.value = { id: row.id, name: row.name, industry: row.industry || '', email: row.contact_email || '', plan: row.plan || 'free', active: row.status === 'active' }
  showDialog.value = true
}
async function save() {
  if (!form.value.name.trim()) return ElMessage.warning('请输入组织名称')
  saving.value = true
  try {
    if (form.value.id) {
      await put(`/organizations/${form.value.id}`, { plan: form.value.plan, status: form.value.active ? 'active' : 'inactive' })
    } else {
      await post('/organizations', { name: form.value.name, industry: form.value.industry, email: form.value.email, plan: form.value.plan })
    }
    showDialog.value = false
    await loadOrganizations()
    ElMessage.success('组织信息已保存')
  } catch (error: any) { ElMessage.error(error?.message || '组织保存失败') }
  finally { saving.value = false }
}
async function toggleStatus(row: any) {
  try {
    await put(`/organizations/${row.id}`, { plan: row.plan || 'free', status: row.status === 'active' ? 'inactive' : 'active' })
    await loadOrganizations()
    ElMessage.success('组织状态已更新')
  } catch (error: any) { ElMessage.error(error?.message || '状态更新失败') }
}
function formatDate(value: string) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }
onMounted(loadOrganizations)
</script>

<style scoped>
.page-root{padding:24px}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.page-header h2{margin:0;font-size:20px}.page-header p{margin:7px 0 0;color:#909399;font-size:13px}
</style>
