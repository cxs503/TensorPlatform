<template>
  <div class="tf-page" v-if="exec">
    <HeroPanel>
      <template #title>
        执行 <span class="tf-mono" style="font-size: 15px">{{ exec.execution_key }}</span>
      </template>
      <template #sub>
        {{ exec.scenario_key }} · {{ exec.stage }} 阶段 · 预算 {{ exec.budget?.max_wall_seconds }}s
      </template>
      <template #extra>
        <span>状态 <StatusBadge :status="exec.status" /></span>
        <span>心跳 <b class="tf-hero-mint">{{ fmtTime(exec.heartbeat_at) }}</b></span>
        <span v-if="exec.result_summary?.stop_condition">
          stop_condition <b class="tf-hero-mint">{{ exec.result_summary.stop_condition }}</b>
        </span>
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="14">
        <LogTail :text="exec.log_tail" :updated-at="fmtTime(exec.heartbeat_at)" />

        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">Agent 会话事件流（append-only）</div>
          <div class="tf-ev" v-for="ev in events" :key="ev.seq">
            <span class="tf-ev-seq tf-mono">#{{ ev.seq }}</span>
            <el-tag size="small" :type="evType(ev.kind)" effect="plain">{{ ev.kind }}</el-tag>
            <span class="tf-ev-payload tf-mono">{{ compact(ev.payload) }}</span>
            <span class="tf-ev-time tf-mono">{{ fmtTime(ev.created_at) }}</span>
          </div>
          <div v-if="!events.length" class="tf-ov-empty">（尚无事件）</div>
        </div>
      </el-col>

      <el-col :span="10">
        <div class="tf-card">
          <div class="tf-card-title">结果</div>
          <template v-if="isTerminal">
            <div class="tf-metric">
              <div class="tf-metric-label">检查</div>
              <div class="tf-metric-value">
                <span class="tf-metric-num">
                  {{ exec.result_summary?.checks_passed ?? '—' }}/{{ exec.result_summary?.checks_total ?? '—' }}
                </span>
              </div>
            </div>
            <div class="tf-rs" v-if="exec.result_summary?.decision">
              decision <b>{{ exec.result_summary.decision }}</b>
              <StatusBadge v-if="exec.registered_run_ids?.length" status="succeeded"
                           :label="`${exec.registered_run_ids.length} run 已登记`" />
            </div>
            <div v-for="rid in exec.registered_run_ids || []" :key="rid" class="tf-run-link">
              <router-link class="tf-mono tf-link" :to="`/runs/${encodeURIComponent(rid)}`">{{ rid }}</router-link>
            </div>
          </template>
          <div v-else class="tf-ov-empty">
            执行进行中，状态每 5 秒自动刷新；终态自动停止。
          </div>
        </div>

        <div class="tf-card" style="margin-top: 16px" v-if="exec.session">
          <div class="tf-card-title">会话</div>
          <div class="tf-rs">
            <StatusBadge :status="exec.session.status" />
            <span class="tf-mono">#{{ exec.session.id }} · {{ exec.session.event_count }} 事件</span>
            <router-link class="tf-link" :to="`/sessions/${exec.session.id}`">时间线 →</router-link>
          </div>
        </div>

        <div class="tf-card" style="margin-top: 16px" v-if="!isTerminal && auth.canOperate">
          <div class="tf-card-title">操作</div>
          <el-button type="danger" size="small" @click="cancel">请求取消</el-button>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { executionApi, sessionApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import LogTail from '@/components/LogTail.vue'

const route = useRoute()
const auth = useAuthStore()
const id = Number(route.params.id)
const exec = ref<any>(null)
const events = ref<any[]>([])

const TERMINAL = ['succeeded', 'failed', 'cancelled']
const isTerminal = computed(() => TERMINAL.includes(exec.value?.status))

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

function evType(kind: string) {
  if (kind === 'error') return 'danger'
  if (kind === 'decision' || kind === 'approval_requested') return 'warning'
  if (kind === 'run_registered') return 'success'
  return 'info'
}

function compact(payload: any) {
  const s = JSON.stringify(payload)
  return s.length > 140 ? s.slice(0, 140) + '…' : s
}

async function refresh() {
  exec.value = await executionApi.get(id)
  if (exec.value?.session?.id) {
    const s = await sessionApi.get(exec.value.session.id)
    events.value = s.events || []
  }
  if (isTerminal.value) stop()
}

async function cancel() {
  try {
    await executionApi.cancel(id, 'console user cancel')
    ElMessage.success('已请求取消')
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

const { start, stop } = usePolling(refresh, 5000)
onMounted(async () => {
  await refresh().catch(() => {})
  if (!isTerminal.value) start()
})
</script>

<style scoped>
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.tf-metric-label { font-size: 12px; color: var(--tf-ink-500); }
.tf-metric-value { margin: 6px 0 12px; }
.tf-metric-num { font-size: 24px; font-weight: 600; color: var(--tf-navy-700); }
.tf-rs { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px; }
.tf-run-link { margin-top: 8px; }
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.tf-ev { display: flex; align-items: center; gap: 10px; padding: 6px 4px; border-bottom: 1px dashed var(--tf-line-200); }
.tf-ev-seq { color: #a5aeb8; }
.tf-ev-payload { flex: 1; color: var(--tf-ink-500); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tf-ev-time { color: #a5aeb8; }
</style>
