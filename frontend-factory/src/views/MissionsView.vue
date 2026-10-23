<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>任务中心 <span class="tf-hero-mint">· 规划与交付</span></template>
      <template #sub>
        明确任务目标、执行步骤与验收标准。GPT 可协助生成规划草稿，执行结果由独立账号验收。
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

    <el-dialog v-model="createDialog" title="新建任务" width="min(560px, 94vw)">
      <el-form label-position="top">
        <el-form-item label="任务目标（给智能体的完整指令）" required>
          <el-input v-model="form.goal" type="textarea" :rows="5"
            placeholder="例：检查指定能耗数据集的完整性，生成质量报告，运行独立检查后提请验收" />
        </el-form-item>
        <el-button :loading="drafting" :disabled="!modelReady || form.goal.trim().length < 10 || form.goal.length > 6000" @click="draftGoal">GPT 规划任务</el-button>
        <p class="tf-form-hint">将当前目标发送给模型生成草稿，确认后可填入目标；不会自动创建或执行任务。</p>
        <div v-if="draft" class="tf-draft-preview">
          <p><b>规划草稿</b> · {{ draftModel }}</p>
          <p>{{ draft.goal }}</p>
          <b>执行步骤</b><ol><li v-for="(step, i) in draft.steps" :key="i">{{ step }}</li></ol>
          <b>验收标准</b><ul><li v-for="(criterion, i) in draft.acceptance_criteria" :key="i">{{ criterion }}</li></ul>
          <el-button size="small" @click="applyDraft">采用草稿</el-button>
        </div>
        <el-form-item label="执行智能体 session" required>
          <el-select v-model="form.agent_session_id" style="width:100%" placeholder="选择常驻智能体">
            <el-option v-for="a in residentSessions.filter(s => s.enabled)" :key="a.agent_session_id" :value="a.agent_session_id" :label="`${a.worker_id} · ${a.model}`" />
          </el-select>
          <p>当前支持脱敏源码审查和开发任务规划；专用修复任务包含隔离测试与补丁附件。</p>
        </el-form-item>
        <el-form-item label="仓库" required>
          <el-select v-model="form.repo" style="width: 220px">
            <el-option v-for="r in repos" :key="r" :label="r" :value="r" />
          </el-select>
          <span class="tf-form-hint">基线：{{ form.base_ref || 'main' }}</span>
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
        <el-button type="primary" :disabled="drafting || form.goal.trim().length < 10 || !form.agent_session_id" @click="submit">排队任务</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { missionApi, factoryApi } from '@/api'
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
const repos = ref<string[]>([])
const residentSessions = ref<any[]>([])
const modelReady = ref(false), drafting = ref(false), draft = ref<any>(null), draftModel = ref('')
const filters = [
  { label: '全部', value: '' },
  { label: '排队', value: 'queued' },
  { label: '进行中', value: 'running' },
  { label: '待验收', value: 'awaiting_acceptance' },
  { label: '成功', value: 'succeeded' },
  { label: '失败', value: 'failed' },
]
const form = ref({
  agent_session_id: residentSessions.value.find(s => s.enabled)?.agent_session_id || '', goal: '', repo: repos.value[0] || '', base_ref: 'main',
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
  draft.value = null
  form.value = { agent_session_id: residentSessions.value.find(s => s.enabled)?.agent_session_id || '', goal: '', repo: repos.value[0] || '', base_ref: 'main', max_tool_calls: 80, max_wall_minutes: 120 }
  createDialog.value = true
}

async function draftGoal() {
  drafting.value = true; draft.value = null
  const originalGoal = form.value.goal
  try {
    const result = await factoryApi.draftTask(originalGoal)
    if (createDialog.value && form.value.goal === originalGoal) {
      draft.value = result.draft; draftModel.value = result.model
    }
  } catch (e: any) { ElMessage.error(e.message) }
  finally { drafting.value = false }
}
function applyDraft() {
  form.value.goal = `${draft.value.goal}\n\n执行步骤：\n${draft.value.steps.map((s: string, i: number) => `${i + 1}. ${s}`).join('\n')}\n\n验收标准：\n${draft.value.acceptance_criteria.map((s: string, i: number) => `${i + 1}. ${s}`).join('\n')}`
  draft.value = null
}

async function submit() {
  try {
    const res = await missionApi.create({
      goal: form.value.goal.trim(),
      repo: form.value.repo,
      base_ref: form.value.base_ref.trim() || 'main',
      runner: 'native',
      agent_session_id: form.value.agent_session_id || undefined,
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
  try {
    const config = await factoryApi.config()
    repos.value = config.projects.map((p: any) => p.id)
    residentSessions.value = (await factoryApi.agents()).sessions
    modelReady.value = (await factoryApi.model()).configured
    await load()
  } catch (e: any) { ElMessage.error(e.message) }
  start()
  clock = window.setInterval(() => (now.value = Date.now()), 1000)
})
onUnmounted(() => { stop(); if (clock) clearInterval(clock) })
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
.tf-draft-preview { margin: 16px 0; padding: 16px; border: 1px solid #d5e1df; border-radius: 8px; overflow-wrap: anywhere; }
</style>
