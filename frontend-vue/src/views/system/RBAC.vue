<template>
  <div class="rbac-page">
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><Lock /></el-icon>
        RBAC权限管理
      </h1>
      <p class="page-desc">角色管理、权限配置、用户分配、权限审计</p>
    </div>

    <el-tabs v-model="activeTab" class="rbac-tabs">
      <!-- 角色管理 -->
      <el-tab-pane label="角色管理" name="roles">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>角色列表</span>
              <el-button type="primary" size="small" @click="showRoleDialog = true">
                <el-icon><Plus /></el-icon>
                新建角色
              </el-button>
            </div>
          </template>
          <el-table :data="roles" v-loading="rolesLoading" style="width: 100%">
            <el-table-column prop="id" label="角色编码" min-width="150" />
            <el-table-column prop="name" label="角色名称" min-width="150" />
            <el-table-column prop="description" label="描述" min-width="200" />
            <el-table-column label="权限数" width="100">
              <template #default="{ row }">
                <el-tag type="info" size="small">{{ (row.permissions || []).length }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <el-button text size="small" @click="editRole(row)">
                  <el-icon><Edit /></el-icon>
                  编辑
                </el-button>
                <el-button text size="small" @click="configPermissions(row)">
                  <el-icon><Setting /></el-icon>
                  权限
                </el-button>
                <el-button text size="small" type="danger" @click="deleteRole(row)">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- 权限配置 -->
      <el-tab-pane label="权限配置" name="permissions">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>权限列表</span>
              <el-button type="primary" size="small" @click="showPermissionDialog = true">
                <el-icon><Plus /></el-icon>
                新建权限
              </el-button>
            </div>
          </template>
          <el-table :data="permissions" v-loading="permissionsLoading" style="width: 100%">
            <el-table-column prop="id" label="权限编码" min-width="220" />
            <el-table-column prop="name" label="权限名称" min-width="200" />
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button text size="small" @click="editPermission(row)">编辑</el-button>
                <el-button text size="small" type="danger" @click="deletePermission(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- 用户分配 -->
      <el-tab-pane label="用户分配" name="assignments">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>用户角色分配</span>
              <el-button type="primary" size="small" @click="showAssignDialog = true">
                <el-icon><Plus /></el-icon>
                分配角色
              </el-button>
            </div>
          </template>
          <el-table :data="userRoles" v-loading="assignmentsLoading" style="width: 100%">
            <el-table-column prop="user_id" label="用户ID" width="100" />
            <el-table-column prop="username" label="用户名" width="150" />
            <el-table-column prop="email" label="邮箱" width="200" />
            <el-table-column prop="roles" label="角色" min-width="250">
              <template #default="{ row }">
                <el-tag v-for="role in row.roles" :key="role.id" style="margin-right: 4px" type="primary" size="small">
                  {{ role.name }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="assigned_at" label="分配时间" width="160">
              <template #default="{ row }">
                {{ formatDate(row.assigned_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button text size="small" @click="editUserRoles(row)">
                  <el-icon><Edit /></el-icon>
                  编辑
                </el-button>
                <el-button text size="small" type="danger" @click="removeAllRoles(row)">移除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- 权限审计 -->
      <el-tab-pane label="权限审计" name="audit">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>审计日志</span>
              <el-button size="small" @click="exportAuditLogs">
                <el-icon><Download /></el-icon>
                导出
              </el-button>
            </div>
          </template>
          <el-form :inline="true" class="filter-form">
            <el-form-item label="操作类型">
              <el-select v-model="auditFilter.action" placeholder="全部" clearable style="width: 150px">
                <el-option label="全部" value="" />
                <el-option label="创建" value="create" />
                <el-option label="更新" value="update" />
                <el-option label="删除" value="delete" />
                <el-option label="分配" value="assign" />
                <el-option label="撤销" value="revoke" />
              </el-select>
            </el-form-item>
            <el-form-item label="资源类型">
              <el-select v-model="auditFilter.resource_type" placeholder="全部" clearable style="width: 150px">
                <el-option label="全部" value="" />
                <el-option label="角色" value="role" />
                <el-option label="权限" value="permission" />
                <el-option label="用户" value="user" />
              </el-select>
            </el-form-item>
            <el-form-item label="时间范围">
              <el-date-picker
                v-model="auditFilter.dateRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                style="width: 260px"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="loadAuditLogs">查询</el-button>
            </el-form-item>
          </el-form>
          <el-table :data="auditLogs" v-loading="auditLoading" style="width: 100%">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="action" label="操作" width="100">
              <template #default="{ row }">
                <el-tag :type="getAuditActionType(row.action)" size="small">
                  {{ getAuditActionLabel(row.action) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="resource_type" label="资源类型" width="120" />
            <el-table-column prop="resource_id" label="资源ID" width="120" />
            <el-table-column prop="username" label="操作人" width="120">
              <template #default="{ row }">{{ row.username || row.user_name || '—' }}</template>
            </el-table-column>
            <el-table-column prop="detail" label="详情" min-width="200">
              <template #default="{ row }">
                <el-text truncated>{{ row.detail || row.details || '—' }}</el-text>
              </template>
            </el-table-column>
            <el-table-column prop="result" label="结果" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.result" :type="row.result === 'success' ? 'success' : 'danger'" size="small">
                  {{ row.result === 'success' ? '成功' : '失败' }}
                </el-tag>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="操作时间" width="160">
              <template #default="{ row }">
                {{ formatDate(row.created_at || row.timestamp) }}
              </template>
            </el-table-column>
          </el-table>
          <div class="pagination-container">
            <el-pagination
              v-model:current-page="auditPage"
              v-model:page-size="auditPageSize"
              :page-sizes="[10, 20, 50]"
              :total="auditTotal"
              layout="total, sizes, prev, pager, next"
              @size-change="loadAuditLogs"
              @current-change="loadAuditLogs"
            />
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 角色对话框 -->
    <el-dialog v-model="showRoleDialog" :title="editingRole ? '编辑角色' : '新建角色'" width="500px" destroy-on-close>
      <el-form :model="roleForm" label-width="100px">
        <el-form-item label="角色名称" required>
          <el-input v-model="roleForm.name" placeholder="请输入角色名称" />
        </el-form-item>
        <el-form-item label="角色编码" required>
          <el-input v-model="roleForm.code" placeholder="请输入角色编码（如：admin）" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="roleForm.description" type="textarea" rows="3" placeholder="请输入角色描述" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="roleForm.status" active-value="active" inactive-value="inactive" active-text="启用" inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRoleDialog = false">取消</el-button>
        <el-button type="primary" @click="saveRole" :loading="savingRole">保存</el-button>
      </template>
    </el-dialog>

    <!-- 权限对话框 -->
    <el-dialog v-model="showPermissionDialog" :title="editingPermission ? '编辑权限' : '新建权限'" width="500px" destroy-on-close>
      <el-form :model="permissionForm" label-width="100px">
        <el-form-item label="权限名称" required>
          <el-input v-model="permissionForm.name" placeholder="请输入权限名称" />
        </el-form-item>
        <el-form-item label="权限编码" required>
          <el-input v-model="permissionForm.code" placeholder="如：user:create" />
        </el-form-item>
        <el-form-item label="资源类型" required>
          <el-select v-model="permissionForm.resource_type" style="width: 100%">
            <el-option label="用户" value="user" />
            <el-option label="角色" value="role" />
            <el-option label="权限" value="permission" />
            <el-option label="模型" value="model" />
            <el-option label="数据集" value="dataset" />
            <el-option label="训练任务" value="training" />
            <el-option label="部署" value="deployment" />
            <el-option label="系统" value="system" />
          </el-select>
        </el-form-item>
        <el-form-item label="操作类型" required>
          <el-select v-model="permissionForm.action" style="width: 100%">
            <el-option label="创建 (create)" value="create" />
            <el-option label="读取 (read)" value="read" />
            <el-option label="更新 (update)" value="update" />
            <el-option label="删除 (delete)" value="delete" />
            <el-option label="执行 (execute)" value="execute" />
            <el-option label="管理 (admin)" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="permissionForm.description" type="textarea" rows="3" placeholder="请输入权限描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPermissionDialog = false">取消</el-button>
        <el-button type="primary" @click="savePermission" :loading="savingPermission">保存</el-button>
      </template>
    </el-dialog>

    <!-- 角色权限配置对话框 -->
    <el-dialog v-model="showPermissionConfigDialog" title="配置角色权限" width="700px" destroy-on-close>
      <el-alert type="info" :closable="false" style="margin-bottom: 16px">
        <div>选择角色“{{ currentRole?.name }}”可以执行的操作。</div>
        <div style="margin-top:4px">数据集列表和详情对所有登录用户可见，这里只控制上传、接入、下载、编辑、清洗加工、分析和删除等具体操作。</div>
      </el-alert>
      <el-tree
        ref="permissionTreeRef"
        :data="permissionTree"
        show-checkbox
        node-key="id"
        :default-checked-keys="selectedPermissions"
        :props="{ label: 'name', children: 'children' }"
        default-expand-all
      />
      <template #footer>
        <el-button @click="showPermissionConfigDialog = false">取消</el-button>
        <el-button type="primary" @click="saveRolePermissions" :loading="savingRolePermissions">保存</el-button>
      </template>
    </el-dialog>

    <!-- 分配角色对话框 -->
    <el-dialog v-model="showAssignDialog" title="分配用户角色" width="600px" destroy-on-close>
      <el-form :model="assignForm" label-width="100px">
        <el-form-item label="选择用户" required>
          <el-select v-model="assignForm.user_id" placeholder="请选择用户" filterable style="width: 100%">
            <el-option v-for="user in users" :key="user.id" :label="user.username" :value="user.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="选择角色" required>
          <el-checkbox-group v-model="assignForm.role_ids">
            <el-checkbox v-for="role in roles.filter(r => r.status === 'active')" :key="role.id" :label="role.id">
              {{ role.name }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAssignDialog = false">取消</el-button>
        <el-button type="primary" @click="assignRoles" :loading="assigning">分配</el-button>
      </template>
    </el-dialog>

    <!-- 编辑用户角色对话框 -->
    <el-dialog v-model="showEditUserRolesDialog" title="编辑用户角色" width="500px" destroy-on-close>
      <el-form label-width="100px">
        <el-form-item label="用户">
          <el-input :value="editingUserRoles?.username" disabled />
        </el-form-item>
        <el-form-item label="角色">
          <el-checkbox-group v-model="editUserRolesForm.role_ids">
            <el-checkbox v-for="role in roles.filter(r => r.status === 'active')" :key="role.id" :label="role.id">
              {{ role.name }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditUserRolesDialog = false">取消</el-button>
        <el-button type="primary" @click="saveUserRoles" :loading="savingUserRoles">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Lock,
  Plus,
  Edit,
  Delete,
  Setting,
  Download
} from '@element-plus/icons-vue'
import { api } from '@/api'
import dayjs from 'dayjs'
import { useAsyncData } from '@/composables/useAsyncData'

// 状态
const activeTab = ref('roles')
const savingRole = ref(false)
const savingPermission = ref(false)
const savingRolePermissions = ref(false)
const assigning = ref(false)
const savingUserRoles = ref(false)

const showRoleDialog = ref(false)
const showPermissionDialog = ref(false)
const showPermissionConfigDialog = ref(false)
const showAssignDialog = ref(false)
const showEditUserRolesDialog = ref(false)

// 使用 useAsyncData 管理数据获取
// 后端 /rbac/roles 返回 {total, items}, 每项形如 {id, name, description, permissions}
const { data: roles, loading: rolesLoading, refresh: refreshRoles } = useAsyncData(async () => {
  const res = await api.rbac.getRoles()
  return res.items || res.roles || []
})

// 后端 /rbac/permissions 返回 {total, items}, 每项形如 {id, name}
const { data: permissions, loading: permissionsLoading, refresh: refreshPermissions } = useAsyncData(async () => {
  const res = await api.rbac.getPermissions()
  return res.items || res.permissions || []
})

// 后端无 /rbac/user-roles 列表端点 → 返回空数组(展示空态, 不报错)
const { data: userRoles, loading: assignmentsLoading, refresh: refreshUserRoles } = useAsyncData(async () => {
  try {
    const res = await api.rbac.getUserRoles()
    return res.items || res.assignments || []
  } catch {
    return []
  }
})

// RBAC 审计标签页: /rbac/audit-logs 不存在 → 改用 /audit/search (真实日志)
const { data: auditLogs, loading: auditLoading, refresh: refreshAuditLogs } = useAsyncData(async () => {
  const res = await api.audit.search({
    page: auditPage.value,
    page_size: auditPageSize.value,
    action: auditFilter.value.action || undefined,
    resource_type: auditFilter.value.resource_type || undefined
  })
  auditTotal.value = res.total || 0
  return res.items || res.logs || []
}, { immediate: false })

const users = ref<any[]>([])
const permissionTree = ref<any[]>([])
const selectedPermissions = ref<string[]>([])
const currentRole = ref<any>(null)
const editingRole = ref<any>(null)
const editingPermission = ref<any>(null)
const editingUserRoles = ref<any>(null)
const permissionTreeRef = ref()

const auditPage = ref(1)
const auditPageSize = ref(10)
const auditTotal = ref(0)

const auditFilter = ref({
  action: '',
  resource_type: '',
  dateRange: null as any
})

const roleForm = ref({
  name: '',
  code: '',
  description: '',
  status: 'active'
})

const permissionForm = ref({
  name: '',
  code: '',
  resource_type: '',
  action: '',
  description: ''
})

const assignForm = ref({
  user_id: null as number | null,
  role_ids: [] as string[]
})

const editUserRolesForm = ref({
  role_ids: [] as string[]
})

// 方法
function formatDate(date: string) {
  if (!date) return '-'
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

function getAuditActionType(action: string) {
  const map: Record<string, string> = {
    create: 'success',
    update: 'warning',
    delete: 'danger',
    assign: 'primary',
    revoke: 'info'
  }
  return map[action] || 'info'
}

function getAuditActionLabel(action: string) {
  const map: Record<string, string> = {
    create: '创建',
    update: '更新',
    delete: '删除',
    assign: '分配',
    revoke: '撤销'
  }
  return map[action] || action
}

// 委托 useAsyncData refresh
function loadRoles() { refreshRoles() }
function loadPermissions() { refreshPermissions() }
function loadUserRoles() { refreshUserRoles() }
function loadAuditLogs() { refreshAuditLogs() }

// 加载用户列表
async function loadUsers() {
  try {
    const res = await api.user.getProfile()
    users.value = [
      { id: 1, username: 'admin' },
      { id: 2, username: 'scientist1' },
      { id: 3, username: 'ops1' },
      { id: 4, username: 'user1' }
    ]
  } catch (error) {
    users.value = [
      { id: 1, username: 'admin' },
      { id: 2, username: 'scientist1' },
      { id: 3, username: 'ops1' }
    ]
  }
}

function buildPermissionTree() {
  // 后端权限项形如 {id: "project:read", name: "查看项目"}; 按 id 前缀(资源)分组
  const resourceGroups: Record<string, any[]> = {}
  permissions.value.forEach(p => {
    const code: string = p.id || p.code || ''
    const type = code.includes(':') ? code.split(':')[0] : 'other'
    if (!resourceGroups[type]) resourceGroups[type] = []
    resourceGroups[type].push(p)
  })

  permissionTree.value = Object.entries(resourceGroups).map(([type, perms]) => ({
    id: `group_${type}`,
    name: getResourceTypeLabel(type),
    children: perms
  }))
}

// 监听 permissions 变化，自动构建权限树
watch(permissions, (val) => {
  if (val && val.length > 0) {
    buildPermissionTree()
  }
}, { immediate: true })

function getResourceTypeLabel(type: string) {
  const map: Record<string, string> = {
    user: '用户管理',
    role: '角色管理',
    permission: '权限管理',
    model: '模型管理',
    dataset: '数据集管理',
    models: '模型管理',
    datasets: '数据集管理',
    experiments: '实验评估',
    training: '训练任务',
    deployment: '部署管理',
    system: '系统管理'
  }
  return map[type] || type
}

// 初始化加载
loadRoles()
loadPermissions()
loadUserRoles()
loadUsers()

async function saveRole() {
  if (!roleForm.value.name || !roleForm.value.code) {
    ElMessage.warning('请填写必填项')
    return
  }

  savingRole.value = true
  try {
    if (editingRole.value) {
      await api.rbac.updateRole(editingRole.value.id, roleForm.value)
      ElMessage.success('更新成功')
    } else {
      await api.rbac.createRole(roleForm.value)
      ElMessage.success('创建成功')
    }
    showRoleDialog.value = false
    resetRoleForm()
    loadRoles()
  } catch (error: any) {
    ElMessage.error('操作失败: ' + error.message)
  } finally {
    savingRole.value = false
  }
}

function resetRoleForm() {
  roleForm.value = {
    name: '',
    code: '',
    description: '',
    status: 'active'
  }
  editingRole.value = null
}

function editRole(row: any) {
  editingRole.value = row
  roleForm.value = {
    name: row.name,
    code: row.code || row.id,
    description: row.description || '',
    status: row.status || 'active'
  }
  showRoleDialog.value = true
}

async function toggleRoleStatus(row: any) {
  try {
    await api.rbac.updateRole(row.id, { status: row.status })
    ElMessage.success('状态更新成功')
  } catch (error: any) {
    row.status = row.status === 'active' ? 'inactive' : 'active'
    ElMessage.error('操作失败: ' + error.message)
  }
}

async function deleteRole(row: any) {
  try {
    await ElMessageBox.confirm(`确定要删除角色"${row.name}"吗？`, '确认删除', { type: 'warning' })
    await api.rbac.deleteRole(row.id)
    ElMessage.success('删除成功')
    loadRoles()
  } catch (error) {
    // 取消
  }
}

function configPermissions(row: any) {
  currentRole.value = row
  // 后端 role.permissions 为权限编码字符串数组(如 ["project:read"]); 兼容对象数组
  const perms = row.permissions || []
  selectedPermissions.value = perms.map((p: any) => (typeof p === 'string' ? p : p.id)).filter(Boolean)
  showPermissionConfigDialog.value = true
}

async function saveRolePermissions() {
  savingRolePermissions.value = true
  try {
    const checkedKeys = permissionTreeRef.value?.getCheckedKeys() || []
    // 权限 id 为权限编码字符串(如 "project:read"); 排除组节点(以 "group_" 开头)
    const permissionIds = checkedKeys.filter((k: any) => typeof k === 'string' && !String(k).startsWith('group_'))
    await api.rbac.assignPermissions(currentRole.value.id, permissionIds)
    ElMessage.success('权限配置成功')
    showPermissionConfigDialog.value = false
    loadRoles()
  } catch (error: any) {
    ElMessage.error('操作失败: ' + error.message)
  } finally {
    savingRolePermissions.value = false
  }
}

async function savePermission() {
  if (!permissionForm.value.name || !permissionForm.value.code || !permissionForm.value.resource_type || !permissionForm.value.action) {
    ElMessage.warning('请填写必填项')
    return
  }

  savingPermission.value = true
  try {
    if (editingPermission.value) {
      await api.rbac.updatePermission(editingPermission.value.id, permissionForm.value)
      ElMessage.success('更新成功')
    } else {
      await api.rbac.createPermission(permissionForm.value)
      ElMessage.success('创建成功')
    }
    showPermissionDialog.value = false
    resetPermissionForm()
    loadPermissions()
  } catch (error: any) {
    ElMessage.error('操作失败: ' + error.message)
  } finally {
    savingPermission.value = false
  }
}

function resetPermissionForm() {
  permissionForm.value = {
    name: '',
    code: '',
    resource_type: '',
    action: '',
    description: ''
  }
  editingPermission.value = null
}

function editPermission(row: any) {
  editingPermission.value = row
  permissionForm.value = {
    name: row.name,
    code: row.code,
    resource_type: row.resource_type,
    action: row.action,
    description: row.description || ''
  }
  showPermissionDialog.value = true
}

async function deletePermission(row: any) {
  try {
    await ElMessageBox.confirm(`确定要删除权限"${row.name}"吗？`, '确认删除', { type: 'warning' })
    await api.rbac.deletePermission(row.id)
    ElMessage.success('删除成功')
    loadPermissions()
  } catch (error) {
    // 取消
  }
}

async function assignRoles() {
  if (!assignForm.value.user_id || assignForm.value.role_ids.length === 0) {
    ElMessage.warning('请选择用户和角色')
    return
  }

  assigning.value = true
  try {
    await api.rbac.assignRoles(assignForm.value.user_id, assignForm.value.role_ids)
    ElMessage.success('角色分配成功')
    showAssignDialog.value = false
    assignForm.value = { user_id: null, role_ids: [] }
    loadUserRoles()
  } catch (error: any) {
    ElMessage.error('操作失败: ' + error.message)
  } finally {
    assigning.value = false
  }
}

function editUserRoles(row: any) {
  editingUserRoles.value = row
  editUserRolesForm.value.role_ids = row.roles?.map((r: any) => r.id) || []
  showEditUserRolesDialog.value = true
}

async function saveUserRoles() {
  savingUserRoles.value = true
  try {
    await api.rbac.assignRoles(editingUserRoles.value.user_id, editUserRolesForm.value.role_ids)
    ElMessage.success('角色更新成功')
    showEditUserRolesDialog.value = false
    loadUserRoles()
  } catch (error: any) {
    ElMessage.error('操作失败: ' + error.message)
  } finally {
    savingUserRoles.value = false
  }
}

async function removeAllRoles(row: any) {
  try {
    await ElMessageBox.confirm(`确定要移除用户"${row.username}"的所有角色吗？`, '确认移除', { type: 'warning' })
    await api.rbac.assignRoles(row.user_id, [])
    ElMessage.success('角色已移除')
    loadUserRoles()
  } catch (error) {
    // 取消
  }
}

async function exportAuditLogs() {
  try {
    ElMessage.success('导出功能开发中...')
  } catch (error) {
    console.error('导出失败:', error)
  }
}
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.rbac-page {
  .page-header {
    margin-bottom: 24px;

    .page-title {
      font-size: 24px;
      font-weight: 600;
      color: $text-primary;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .page-desc {
      color: $text-secondary;
      font-size: 14px;
    }
  }

  .rbac-tabs {
    :deep(.el-tabs__content) {
      padding: 0;
    }
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .filter-form {
    margin-bottom: 16px;
    padding: 16px;
    background: $bg-dark;
    border-radius: $radius-md;
  }

  .pagination-container {
    margin-top: 20px;
    display: flex;
    justify-content: flex-end;
  }
}
</style>
