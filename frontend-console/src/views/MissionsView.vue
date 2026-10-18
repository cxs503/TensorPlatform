<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>开发任务 <span class="tf-hero-mint">· autonomous dev missions</span></template>
      <template #sub>
        自主开发智能体（OpenHands harness + 自部署 DeepSeek）按任务领取、worktree 开发、
        测试提交后提请验收；全程事件入 append-only 审计链，终点由 ADMIN 裁定。
      </template>
    </HeroPanel>

    <div class="tf-card">
      <div class="tf-missions-toolbar">
        <el-radio-group v-model="filter" @change="load" size="small">
          <el-radio-button v-for="f in filters" :key="f.value" :value="f.value">{{ f.label }}</el-radio-button>
        </el-radio-group>
        <el-button
          v-if="auth.user && ['ADMIN', 'MANAGER', 'ENGINEER'].includes(auth.user.role)"
          type="primary" size="small" @click="openCreate">新建任务</el-button>
      </div>

      <el-table :data="items" size="small">
        <el-table-column prop="id" label="#" width="56" />
        <el-table-column label="目标" min-width="320">
          <template #default="{ row }">
            <router-link class="tf-mono tf-link" :to="`/missions/${row.id}`">{{ goalBrief(row.goal) }}</router-link>
          </template>
        </el-table-column>
        <el-table-column prop="repo" label="仓库" width="130" />
        <el-table-column label="状态" width="150">
          <template #default="{ row }"><StatusBadge :status="row.status" /></template>
        </el-table-column>
        <el-table-column label="预算（工具/墙钟）" width="170">
          <template #default="{ row }">
            <span class="tf-mono tf-dim">
              {{ row.budget?.max_tool_calls ?? '—' }} 次 / {{ Math.round((row.budget?.max_wall_seconds ?? 0) / 60) }} 分
            </span>
          </template>
        </el-table-column>
        <el-table-column label="心跳" width="92">
          <template #default="{ row }">
            <span :class="['tf-mono', heartbeatClass(row)]">{{ fmtAgo(now, parseT(row.heartbeat_at)) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="会话事件" width="90">
          <template #default="{ row }">
            <router-link v-if="row.session_id" class="tf-mono tf-link" :to="`/sessions/${row.session_id}`">
              #{{ row.session_id }}
            </router-link>
            <span v-else class="tf-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建" width="140">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="createDialog" title="新建开发任务" width="560px">
      <el-form label-position="top">
        <el-form-item label="任务目标（给智能体的完整指令）" required>
          <el-input v-model="form.goal" type="textarea" :rows="5"
            placeholder="例：检索 Couette 流解析 benchmark，在 TensorLBM 实现并用测试验证，全绿后提请验收" />
        </el-form-item>
        <el-form-item label="仓库" required>
          <el-select v-model="form.repo" style="width: 220px">
            <el-option v-for="r in repos" :key="r" :label="r" :value="r" />
          </el-select>
          <span class="tf-form-hint">基于 {{ form.base_ref || 'main' }} 分支开 agent/m&lt;id&gt;</span>
        </el-form-item>
        <el-form-item label="基线分支">
          <el-input v-model="form.base_ref" style="width: 220px" placeholder="main" />
        </el-form-item>
        <el-form-item label="预算">
          <div class="tf-budget-row">
            <span class="tf-dim">工具调用</span>
            <el-input-number v-model="form.max_tool_calls" :min="5" :max="500" size="small" />
            <span class="tf-dim">墙钟（分）</span>
            <el-input-number v-model="form.max_wall_minutes" :min="5" :max="480" size="small" />
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :disabled="form.goal.trim().length < 10" @click="submit">排队任务</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { missionApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import { fmtAgo, parseT } from '@/utils/time'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const auth = useAuthStore()
const router = useRouter()
const items = ref<any[]>([])
const now = ref(Date.now())
const filter = ref('')
const createDialog = ref(false)
const repos = ['TensorPhysics', 'TensorLBM', 'TensorFEM', 'TensorDEM', 'TensorFVM', 'TensorPlatform']
const filters = [
  { label: '全部', value: '' },
  { label: '排队', value: 'queued' },
  { label: '进行中', value: 'running' },
  { label: '待验收', value: 'awaiting_acceptance' },
  { label: '成功', value: 'succeeded' },
  { label: '失败', value: 'failed' },
]
const form = ref({
  goal: '', repo: 'TensorLBM', base_ref: 'main',
  max_tool_calls: 80, max_wall_minutes: 120,
})

function goalBrief(goal: string) {
  return goal.length > 60 ? goal.slice(0, 60) + '…' : goal
}
function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}
function heartbeatClass(row: any) {
  if (!['claimed', 'running'].includes(row.status)) return 'tf-dim'
  return Date.now() - parseT(row.heartbeat_at as any) < 60_000 ? 'tf-hb-live' : 'tf-hb-stale'
}

async function load() {
  const params = filter.value ? { status: filter.value } : {}
  const res = await missionApi.list(params)
  items.value = res.items || []
}

function openCreate() {
  form.value = { goal: '', repo: 'TensorLBM', base_ref: 'main', max_tool_calls: 80, max_wall_minutes: 120 }
  createDialog.value = true
}

async function submit() {
  try {
    const res = await missionApi.create({
      goal: form.value.goal.trim(),
      repo: form.value.repo,
      base_ref: form.value.base_ref.trim() || 'main',
      budget: {
        max_tool_calls: form.value.max_tool_calls,
        max_wall_seconds: form.value.max_wall_minutes * 60,
      },
    })
    ElMessage.success(`任务 #${res.id} 已排队（会话 #${res.session_id}）`)
    createDialog.value = false
    router.push(`/missions/${res.id}`)
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

const { start, stop } = usePolling(load, 8000)
let clock: number | undefined
onMounted(async () => {
  await load().catch(() => {})
  start()
  clock = window.setInterval(() => (now.value = Date.now()), 1000)
})
onUnmounted(() => stop() && clearInterval(clock))
</script>

<style scoped lang="scss">
.tf-missions-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.tf-dim { color: #a5aeb8; font-size: 12px; }
.tf-hb-live { color: #2fae8b; }
.tf-hb-stale { color: #c24545; }
.tf-form-hint { margin-left: 12px; font-size: 12px; color: #a5aeb8; }
.tf-budget-row {
  display: flex; align-items: center; gap: 10px;
  .tf-dim { flex-shrink: 0; }
}
</style>
