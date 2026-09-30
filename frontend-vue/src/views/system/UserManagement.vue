<template>
  <div class="user-mgmt">
    <div class="toolbar">
      <h2>用户管理</h2>
      <div class="toolbar-right">
        <el-input
          v-model="keyword"
          placeholder="搜索用户名/邮箱"
          clearable
          style="width: 220px"
          :prefix-icon="Search"
        />
        <el-select v-model="roleFilter" placeholder="全部角色" clearable style="width: 140px">
          <el-option v-for="r in roleOptions" :key="r.value" :label="r.label" :value="r.value" />
        </el-select>
        <el-button :icon="Refresh" @click="loadUsers">刷新</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
      title="勾选每个用户可使用的功能范围; 留空表示不限制。管理员拥有全部权限。"
    />

    <el-table v-loading="loading" :data="filteredUsers" border stripe>
      <el-table-column prop="username" label="用户名" min-width="120" />
      <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
      <el-table-column prop="full_name" label="姓名" min-width="100">
        <template #default="{ row }">{{ row.full_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="角色" width="110">
        <template #default="{ row }">
          <el-tag :type="row.is_admin ? 'danger' : 'info'" effect="plain">
            {{ roleLabel(row.role) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'warning'" effect="plain">
            {{ row.is_active ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="功能范围" width="120">
        <template #default="{ row }">{{ featureScopeText(row) }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170">
        <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="dialogVisible" title="编辑用户权限" width="780px" :close-on-click-modal="false">
      <template v-if="editing">
        <el-form label-width="80px" class="edit-form">
          <el-form-item label="用户名">
            <el-input :model-value="editing.username" disabled />
          </el-form-item>
          <el-form-item label="姓名">
            <el-input v-model="editing.full_name" placeholder="选填" />
          </el-form-item>
          <el-form-item label="角色">
            <el-select v-model="editing.role" style="width: 200px">
              <el-option v-for="r in roleOptions" :key="r.value" :label="r.label" :value="r.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="editing.is_active" :disabled="editing.is_admin" />
            <span v-if="editing.is_admin" class="hint">管理员不可在此停用当前账户</span>
          </el-form-item>
        </el-form>

        <el-divider content-position="left">功能使用范围</el-divider>

        <el-alert
          v-if="editing.is_admin"
          type="warning"
          :closable="false"
          show-icon
          title="该用户是管理员, 自动拥有全部功能, 无需勾选。"
          style="margin-bottom: 12px"
        />

        <div class="tree-actions">
          <el-checkbox
            :model-value="allChecked"
            :indeterminate="allIndeterminate"
            :disabled="editing.is_admin"
            @change="(v: boolean) => toggleAll(!!v)"
          >全选</el-checkbox>
          <el-button link type="primary" :disabled="editing.is_admin" @click="toggleAll(false)">清空</el-button>
          <span class="hint">已选 {{ selectedFeatures.length }} / {{ allPaths.length }} 项; 留空=不限制</span>
        </div>

        <div class="feature-tree" :class="{ disabled: editing.is_admin }">
          <div v-for="mod in featureTree" :key="mod.key" class="ft-module">
            <div class="ft-module-head">
              <el-checkbox
                :model-value="isGroupAllChecked(mod.allPaths)"
                :indeterminate="isGroupIndeterminate(mod.allPaths)"
                :disabled="editing.is_admin"
                @change="(v: boolean) => togglePaths(mod.allPaths, !!v)"
              >{{ mod.title }}</el-checkbox>
            </div>
            <div v-for="g in mod.groups" :key="g.title" class="ft-group">
              <div class="ft-group-head">
                <el-checkbox
                  :model-value="isGroupAllChecked(g.paths)"
                  :indeterminate="isGroupIndeterminate(g.paths)"
                  :disabled="editing.is_admin"
                  @change="(v: boolean) => togglePaths(g.paths, !!v)"
                >{{ g.title }}</el-checkbox>
              </div>
              <div class="ft-items">
                <el-checkbox
                  v-for="it in g.items"
                  :key="it.path"
                  :model-value="selectedFeatures.includes(it.path)"
                  :disabled="editing.is_admin"
                  @change="(v: boolean) => toggleItem(it.path, !!v)"
                >{{ it.title }}</el-checkbox>
              </div>
            </div>
          </div>
        </div>
      </template>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Refresh } from '@element-plus/icons-vue'
import { MODULES, SYSTEM_ITEMS, flattenItems, type Module } from '@/config/modules'
import { userMgmtApi, type UserManageItem, type UserUpdatePayload } from '@/api/users'

const users = ref<UserManageItem[]>([])
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const roleFilter = ref<string>('')
const dialogVisible = ref(false)
const editing = ref<UserManageItem | null>(null)
const selectedFeatures = ref<string[]>([])

const roleOptions = [
  { value: 'admin', label: '管理员' },
  { value: 'manager', label: '经理' },
  { value: 'engineer', label: '工程师' },
  { value: 'viewer', label: '访客' },
]

function roleLabel(role: string): string {
  const r = (role || '').split('.').pop()?.toLowerCase() || ''
  return roleOptions.find(o => o.value === r)?.label || role || '—'
}

function featureScopeText(row: UserManageItem): string {
  if (row.is_admin) return '全部'
  if (!row.features || row.features.length === 0) return '不限'
  return `${row.features.length} 项`
}

function formatDate(s?: string): string {
  if (!s) return '—'
  return s.replace('T', ' ').slice(0, 19)
}

const filteredUsers = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return users.value.filter(u => {
    const matchKw = !kw || u.username.toLowerCase().includes(kw) || (u.email || '').toLowerCase().includes(kw)
    const matchRole = !roleFilter.value || (u.role || '').split('.').pop()?.toLowerCase() === roleFilter.value
    return matchKw && matchRole
  })
})

async function loadUsers() {
  loading.value = true
  try {
    users.value = await userMgmtApi.list()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '加载用户列表失败')
  } finally {
    loading.value = false
  }
}

// ===== 功能授权树(只展示可勾选项: 非 hidden / 非 unavailable; 系统项排除 adminOnly) =====
interface FtItem { path: string; title: string }
interface FtGroup { title: string; paths: string[]; items: FtItem[] }
interface FtModule { key: string; title: string; allPaths: string[]; groups: FtGroup[] }

function grantableItems(m: Module): FtItem[] {
  return flattenItems(m).filter(it => !it.hidden && !it.unavailable).map(it => ({ path: it.path, title: it.title }))
}

const featureTree = computed<FtModule[]>(() => {
  const tree: FtModule[] = MODULES.map(m => {
    const groups: FtGroup[] = (m.groups || []).map(g => {
      const items = g.items.filter(it => !it.hidden && !it.unavailable).map(it => ({ path: it.path, title: it.title }))
      return { title: g.title, paths: items.map(i => i.path), items }
    }).filter(g => g.items.length > 0)
    // 顶层独立 items(如应用中心 AI智能体)归为一个隐式分组
    const topItems = (m.items || []).filter(it => !it.hidden && !it.unavailable).map(it => ({ path: it.path, title: it.title }))
    if (topItems.length > 0) groups.unshift({ title: m.title, paths: topItems.map(i => i.path), items: topItems })
    const allPaths = groups.flatMap(g => g.paths)
    return { key: m.key, title: m.title, allPaths, groups }
  }).filter(m => m.allPaths.length > 0)

  // 系统管理作为最后一个"模块"(排除 adminOnly 的用户管理)
  const sysItems = SYSTEM_ITEMS.filter(it => !it.hidden && !it.adminOnly).map(it => ({ path: it.path, title: it.title }))
  if (sysItems.length > 0) {
    tree.push({ key: 'system', title: '系统管理', allPaths: sysItems.map(i => i.path), groups: [{ title: '系统管理', paths: sysItems.map(i => i.path), items: sysItems }] })
  }
  return tree
})

const allPaths = computed(() => featureTree.value.flatMap(m => m.allPaths))

const allChecked = computed(() => allPaths.value.length > 0 && allPaths.value.every(p => selectedFeatures.value.includes(p)))
const allIndeterminate = computed(() => {
  const c = allPaths.value.filter(p => selectedFeatures.value.includes(p)).length
  return c > 0 && c < allPaths.value.length
})

function isGroupAllChecked(paths: string[]) {
  return paths.length > 0 && paths.every(p => selectedFeatures.value.includes(p))
}
function isGroupIndeterminate(paths: string[]) {
  const c = paths.filter(p => selectedFeatures.value.includes(p)).length
  return c > 0 && c < paths.length
}
function togglePaths(paths: string[], checked: boolean) {
  const set = new Set(selectedFeatures.value)
  paths.forEach(p => (checked ? set.add(p) : set.delete(p)))
  selectedFeatures.value = [...set]
}
function toggleItem(path: string, checked: boolean) {
  const set = new Set(selectedFeatures.value)
  checked ? set.add(path) : set.delete(path)
  selectedFeatures.value = [...set]
}
function toggleAll(checked: boolean) {
  selectedFeatures.value = checked ? [...allPaths.value] : []
}

// ===== 编辑/保存 =====
function openEdit(row: UserManageItem) {
  editing.value = { ...row, role: (row.role || '').split('.').pop()?.toLowerCase() || row.role }
  selectedFeatures.value = Array.isArray(row.features) ? [...row.features] : []
  dialogVisible.value = true
}

async function save() {
  if (!editing.value) return
  saving.value = true
  try {
    const u = editing.value
    const payload: UserUpdatePayload = {
      full_name: u.full_name ?? '',
      role: u.role,
      is_active: u.is_active,
    }
    // 管理员不发 features(后端忽略); 非管理员发当前勾选(空数组=不限制)
    if (!u.is_admin) payload.features = selectedFeatures.value
    const updated = await userMgmtApi.update(u.id, payload)
    const idx = users.value.findIndex(x => x.id === u.id)
    if (idx >= 0) users.value[idx] = updated
    ElMessage.success('保存成功')
    dialogVisible.value = false
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadUsers)
</script>

<style scoped>
.user-mgmt { padding: 16px 20px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.toolbar h2 { margin: 0; font-size: 18px; }
.toolbar-right { display: flex; gap: 8px; align-items: center; }
.hint { color: #909399; font-size: 12px; margin-left: 8px; }
.edit-form { max-width: 460px; }
.tree-actions { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.feature-tree.disabled { opacity: 0.5; pointer-events: none; }
.ft-module { border: 1px solid #ebeef5; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; }
.ft-module-head { font-weight: 600; margin-bottom: 8px; }
.ft-group { margin-left: 18px; margin-bottom: 6px; }
.ft-group-head { margin: 4px 0; }
.ft-items { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-left: 22px; }
</style>
