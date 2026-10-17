<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>人审队列 <span class="tf-hero-mint">· human-in-the-loop</span></template>
      <template #sub>
        Agent 的晋级/停机请求必须由 ADMIN 裁定；agent 账号是 ENGINEER，无法自审。
      </template>
    </HeroPanel>

    <div class="tf-card">
      <el-radio-group v-model="filter" @change="load" style="margin-bottom: 12px">
        <el-radio-button value="pending">待审</el-radio-button>
        <el-radio-button value="approved">已批</el-radio-button>
        <el-radio-button value="rejected">已驳</el-radio-button>
        <el-radio-button value="">全部</el-radio-button>
      </el-radio-group>

      <el-table :data="items" size="small">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="场景" min-width="160">
          <template #default="{ row }">
            <router-link class="tf-mono tf-link" :to="`/scenario/${row.scenario_key}`">{{ row.scenario_key }}</router-link>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kind === 'stage_promotion' ? 'success' : 'danger'" effect="plain">
              {{ row.kind === 'stage_promotion' ? `${row.from_stage} → ${row.to_stage} 晋级` : '停机裁定' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="理由（agent 提交）" min-width="280" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><StatusBadge :status="row.status" /></template>
        </el-table-column>
        <el-table-column label="操作" width="200" v-if="auth.isAdmin">
          <template #default="{ row }">
            <template v-if="row.status === 'pending'">
              <el-button type="success" size="small" @click="decide(row, 'approved')">批准</el-button>
              <el-button type="danger" size="small" @click="decide(row, 'rejected')">驳回</el-button>
            </template>
            <span v-else class="tf-mono tf-dim">{{ row.decided_at ? fmtTime(row.decided_at) : '' }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="!auth.isAdmin" label="" min-width="200">
          <template #default="{ row }">
            <span v-if="row.status === 'pending'" class="tf-dim">仅 ADMIN 可裁定</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="noteDialog" title="裁定意见（可选）" width="440px">
      <el-input v-model="note" type="textarea" :rows="3" placeholder="记录裁定依据…" />
      <template #footer>
        <el-button @click="noteDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmDecide">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { approvalApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const auth = useAuthStore()
const items = ref<any[]>([])
const filter = ref('pending')
const noteDialog = ref(false)
const note = ref('')
let pending: { row: any; decision: 'approved' | 'rejected' } | null = null

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

async function load() {
  const params = filter.value ? { status: filter.value } : {}
  const res = await approvalApi.list(params)
  items.value = res.items || []
}

function decide(row: any, decision: 'approved' | 'rejected') {
  pending = { row, decision }
  note.value = ''
  noteDialog.value = true
}

async function confirmDecide() {
  if (!pending) return
  try {
    const res = await approvalApi.decide(pending.row.id, pending.decision, note.value || undefined)
    ElMessage.success(
      res.promoted_to ? `已批准，场景晋级 ${res.promoted_to}` : `已${pending.decision === 'approved' ? '批准' : '驳回'}`
    )
    noteDialog.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

const { start, stop } = usePolling(load, 8000)
onMounted(async () => {
  await load().catch(() => {})
  start()
})
</script>

<style scoped>
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.tf-dim { color: #a5aeb8; font-size: 12px; }
</style>
