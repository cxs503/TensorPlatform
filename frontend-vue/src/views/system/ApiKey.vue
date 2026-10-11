<template>
  <div class="apikey-page">
    <div class="page-header">
      <h2>🔑 API 密钥管理</h2>
      <el-button type="primary" @click="showCreate = true">+ 创建密钥</el-button>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
      密钥仅创建时显示一次，请妥善保管。密钥使用 SHA256 哈希存储，平台无法再次查看原始密钥。
    </el-alert>

    <el-card>
      <el-table :data="keys" stripe v-loading="loading">
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="key_prefix" label="密钥前缀" width="180">
          <template #default="{row}">
            <code>{{ row.key_prefix }}****</code>
            <el-button link size="small" @click="copyKey(row)" v-if="row.full_key" style="margin-left:8px">📋</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column prop="last_used_at" label="最后使用" width="170">
          <template #default="{row}">{{ row.last_used_at || '从未使用' }}</template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{row}">
            <el-tag :type="row.is_active?'success':'danger'" size="small">{{ row.is_active?'活跃':'已禁用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{row}">
            <el-button size="small" @click="rotateKey(row.id)">轮换</el-button>
            <el-button size="small" type="danger" @click="deleteKey(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="showCreate" title="创建 API 密钥" width="500px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如: 生产环境密钥" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate=false">取消</el-button>
        <el-button type="primary" @click="createKey" :loading="creating">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showNewKey" title="🔑 新密钥已生成" width="550px">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px">
        此密钥仅显示一次！请立即复制并妥善保管。
      </el-alert>
      <el-input v-model="newKeyValue" readonly size="large" style="margin-bottom:12px">
        <template #append><el-button @click="copyNewKey">复制</el-button></template>
      </el-input>
      <div class="usage-hint">
        <p><strong>使用方式:</strong></p>
        <code>curl -H "Authorization: Bearer {{ newKeyValue.slice(0,16) }}..." http://localhost:8001/api/v1/...</code>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, del } from '@/api/legacy'

const loading = ref(false)
const creating = ref(false)
const showCreate = ref(false)
const showNewKey = ref(false)
const keys = ref<any[]>([])
const newKeyValue = ref('')
const form = ref({ name: '' })

onMounted(() => fetchKeys())

async function fetchKeys() {
  loading.value = true
  try { const d = await get('/api-keys'); keys.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){}
  finally { loading.value = false }
}

async function createKey() {
  creating.value = true
  try {
    const r = await post('/api-keys', { name: form.value.name })
    newKeyValue.value = r.key || r.api_key || ''
    showCreate.value = false
    showNewKey.value = true
    form.value.name = ''
    fetchKeys()
  } catch(e:any) { ElMessage.error(e?.message||'创建失败') }
  finally { creating.value = false }
}

function copyNewKey() {
  navigator.clipboard.writeText(newKeyValue.value)
  ElMessage.success('已复制到剪贴板')
}

function copyKey(row: any) {
  if (row.full_key) { navigator.clipboard.writeText(row.full_key); ElMessage.success('已复制') }
}

async function rotateKey(id: string) {
  await ElMessageBox.confirm('轮换后将生成新密钥，旧密钥立即失效，确认？', '密钥轮换', { type: 'warning' })
  try {
    const r = await post(`/api-keys/${id}/rotate`)
    newKeyValue.value = r.key || r.api_key || ''
    showNewKey.value = true
    fetchKeys()
  } catch(e:any) { ElMessage.error(e?.message||'轮换失败') }
}

async function deleteKey(id: string) {
  await ElMessageBox.confirm('确认删除此密钥？删除后不可恢复。', '警告', { type: 'warning' })
  try { await del(`/api-keys/${id}`); ElMessage.success('已删除'); fetchKeys() }
  catch(e:any) { ElMessage.error(e?.message||'删除失败') }
}
</script>

<style scoped>
.apikey-page { padding: 24px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 20px; }
.usage-hint { background: #f5f7fa; padding: 12px; border-radius: 6px; font-size: 13px; }
.usage-hint code { font-size: 12px; word-break: break-all; }
</style>
