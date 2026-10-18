<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>
        任务 #{{ mission?.id ?? '…' }}
        <span class="tf-hero-mint">· {{ mission?.repo || '' }}</span>
      </template>
      <template #sub>{{ mission?.goal || '加载中…' }}</template>
    </HeroPanel>

    <el-row v-if="mission" :gutter="16">
      <el-col :span="8">
        <div class="tf-card" v-if="mission.parent_mission_id || mission.co_repos?.length">
          <div class="tf-card-title">编排关系</div>
          <div class="tf-md-meta">
            <div class="tf-md-row" v-if="mission.parent_mission_id"><span>父任务</span>
              <router-link class="tf-mono tf-link"
                :to="`/missions/${mission.parent_mission_id}`">#{{ mission.parent_mission_id }}</router-link>
            </div>
            <div class="tf-md-row" v-if="mission.co_repos?.length"><span>协同仓库</span>
              <span class="tf-mono">{{ mission.co_repos.join(' + ') }}</span>
            </div>
          </div>
        </div>

        <div class="tf-card">
          <div class="tf-card-title">任务状态</div>
          <div class="tf-md-meta">
            <div class="tf-md-row"><span>状态</span><StatusBadge :status="mission.status" /></div>
            <div class="tf-md-row"><span>分支</span><span class="tf-mono">agent/m{{ mission.id }}</span></div>
            <div class="tf-md-row"><span>基线</span><span class="tf-mono">{{ mission.base_ref }}</span></div>
            <div class="tf-md-row"><span>心跳</span>
              <span :class="['tf-mono', hbClass]">{{ fmtAgo(now, parseT(mission.heartbeat_at)) }}</span>
            </div>
            <div class="tf-md-row"><span>HEAD</span>
              <span class="tf-mono">{{ mission.result?.progress?.head?.slice(0, 10) || '—' }}</span>
            </div>
            <div class="tf-md-row"><span>创建</span><span class="tf-mono">{{ fmtTime(mission.created_at) }}</span></div>
            <div class="tf-md-row" v-if="mission.finished_at"><span>结束</span>
              <span class="tf-mono">{{ fmtTime(mission.finished_at) }}</span>
            </div>
          </div>
          <el-button
            v-if="canStop" size="small" type="danger" plain style="margin-top: 10px"
            :loading="stopping" @click="doStop">终止任务</el-button>
        </div>

        <div class="tf-card" v-if="budget">
          <div class="tf-card-title">预算消耗</div>
          <div v-for="b in budgetBars" :key="b.label" class="tf-md-budget">
            <div class="tf-md-budget-head">
              <span>{{ b.label }}</span>
              <span class="tf-mono tf-dim">{{ b.used }} / {{ b.max }}</span>
            </div>
            <el-progress :percentage="b.pct" :stroke-width="8"
              :color="b.pct >= 100 ? '#c24545' : b.pct >= 80 ? '#e08e45' : '#58c9a9'" />
          </div>
        </div>

        <div class="tf-card" v-if="approvals.length">
          <div class="tf-card-title">验收裁定</div>
          <div v-for="a in approvals" :key="a.id" class="tf-md-approval">
            <div class="tf-rs">
              <span class="tf-mono">#{{ a.id }}</span>
              <StatusBadge :status="a.status === 'pending' ? 'pending' : a.status" />
            </div>
            <div class="tf-md-evidence">{{ a.reason || a.evidence || '（无说明）' }}</div>
            <div v-if="a.status === 'pending' && isAdmin" style="margin-top: 8px">
              <el-input v-model="decisionNote" size="small" placeholder="裁定意见（可选）" />
              <div style="display: flex; gap: 8px; margin-top: 8px">
                <el-button size="small" type="success" :loading="deciding" @click="decide(a.id, 'approved')">
                  批准验收
                </el-button>
                <el-button size="small" type="danger" plain :loading="deciding" @click="decide(a.id, 'rejected')">
                  驳回
                </el-button>
              </div>
            </div>
            <div v-else-if="a.decided_by" class="tf-dim tf-mono" style="margin-top: 6px">
              {{ a.decided_by }} · {{ fmtTime(a.decided_at) }}{{ a.decision_note ? ` · ${a.decision_note}` : '' }}
            </div>
          </div>
        </div>

        <div class="tf-card" v-if="terminalNote">
          <div class="tf-card-title">终态摘要</div>
          <pre class="tf-mono tf-md-json">{{ pretty(terminalNote) }}</pre>
        </div>
      </el-col>

      <el-col :span="16">
        <div class="tf-card" v-if="children.length">
          <div class="tf-card-title">
            子任务树
            <span class="tf-dim" style="margin-left: auto">编排者派发的子任务（agent 派发/服务端包络）</span>
          </div>
          <div v-for="c in children" :key="c.id" class="tf-md-child">
            <div class="tf-rs">
              <router-link class="tf-mono tf-link" :to="`/missions/${c.id}`">#{{ c.id }}</router-link>
              <StatusBadge :status="c.status" />
              <span v-if="c.model" class="tf-mono tf-dim">{{ c.model }}</span>
            </div>
            <div class="tf-md-child-goal">{{ c.goal }}</div>
          </div>
        </div>

        <div class="tf-card">
          <div class="tf-card-title">
            开发过程事件流
            <span class="tf-dim" style="margin-left: auto">
              会话 <router-link class="tf-mono tf-link" :to="`/sessions/${mission.session_id}`">
                #{{ mission.session_id }}</router-link>
              · 增量 after_seq={{ latestSeq }} · 5s 轮询
            </span>
          </div>
          <div v-if="!events.length" class="tf-md-empty">尚无事件（agent 领取后每步工具调用都会实时上屏）。</div>
          <div v-else class="tf-md-stream">
            <div v-for="ev in events" :key="ev.seq" class="tf-md-ev"
              :class="{ 'is-open': openSeqs.has(ev.seq) }" @click="toggle(ev.seq)">
              <div class="tf-md-ev-line">
                <span class="tf-ef-dot" :style="{ background: kindColor(ev.kind) }" />
                <span class="tf-md-ev-kind" :style="{ color: kindColor(ev.kind) }">{{ kindLabel(ev.kind) }}</span>
                <span class="tf-md-ev-sum">{{ summarizeEvent(ev) }}</span>
                <span class="tf-md-ev-ago tf-mono">{{ fmtTime(ev.created_at).slice(11) }}</span>
              </div>
              <pre v-if="openSeqs.has(ev.seq)" class="tf-mono tf-md-json">{{ pretty(ev.payload) }}</pre>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { missionApi, sessionApi, approvalApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import { kindLabel, kindColor, summarizeEvent } from '@/utils/events'
import { fmtAgo, parseT } from '@/utils/time'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const route = useRoute()
const auth = useAuthStore()
const missionId = Number(route.params.id)

const mission = ref<any>(null)
const events = ref<any[]>([])
const approvals = ref<any[]>([])
const children = ref<any[]>([])
const latestSeq = ref(0)
const now = ref(Date.now())
const openSeqs = ref(new Set<number>())
const decisionNote = ref('')
const deciding = ref(false)
const stopping = ref(false)

const isAdmin = computed(() => auth.user?.role === 'ADMIN')
const canStop = computed(() =>
  ['ADMIN', 'MANAGER'].includes(auth.user?.role || '') &&
  ['queued', 'claimed', 'running'].includes(mission.value?.status))
const budget = computed(() => mission.value?.budget || null)
const hbClass = computed(() => {
  const m = mission.value
  if (!m || !['claimed', 'running'].includes(m.status)) return 'tf-dim'
  return now.value - parseT(m.heartbeat_at) < 60_000 ? 'tf-hb-live' : 'tf-hb-stale'
})

const budgetBars = computed(() => {
  const p = mission.value?.result?.progress || {}
  const bars: { label: string; used: number; max: number; pct: number }[] = []
  if (budget.value?.max_tool_calls) {
    const used = p.tool_calls ?? 0
    bars.push({
      label: '工具调用', used, max: budget.value.max_tool_calls,
      pct: Math.min(100, Math.round((used / budget.value.max_tool_calls) * 100)),
    })
  }
  const maxWall = budget.value?.max_wall_seconds ?? p.max_wall_seconds
  if (maxWall) {
    const used = p.elapsed_seconds ?? 0
    bars.push({
      label: '墙钟（分）', used: Math.round(used / 60), max: Math.round(maxWall / 60),
      pct: Math.min(100, Math.round((used / maxWall) * 100)),
    })
  }
  return bars
})

const terminalNote = computed(() => {
  const r = mission.value?.result
  if (!r || !['succeeded', 'failed', 'stopped'].includes(mission.value?.status)) return null
  const { progress, ...rest } = r
  return Object.keys(rest).length ? rest : null
})

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}
function pretty(v: any) {
  return JSON.stringify(v, null, 2)
}
function toggle(seq: number) {
  const s = new Set(openSeqs.value)
  s.has(seq) ? s.delete(seq) : s.add(seq)
  openSeqs.value = s
}

async function refresh() {
  try {
    mission.value = await missionApi.get(missionId)
    const m = mission.value
    if (m.session_id) {
      const s = await sessionApi.get(m.session_id, latestSeq.value)
      if (s.events?.length) {
        events.value.push(...s.events)
        latestSeq.value = s.latest_seq
      }
    }
    // mission detail already carries its (single) approval row — no need to
    // pull the whole approvals list every poll cycle
    approvals.value = m.approval ? [m.approval] : []
    // children tree: only poll while any child is still in flight
    if (m.parent_mission_id === null || m.parent_mission_id === undefined) {
      const inflight = children.value.length === 0 ||
        children.value.some((c: any) =>
          !['succeeded', 'failed', 'stopped', 'awaiting_acceptance'].includes(c.status))
      if (inflight) {
        const ch = await missionApi.children(missionId).catch(() => null)
        if (ch?.items) children.value = ch.items
      }
    }
  } catch { /* transient API failure: keep last known state */ }
  now.value = Date.now()
}

async function decide(id: number, decision: 'approved' | 'rejected') {
  deciding.value = true
  try {
    await approvalApi.decide(id, decision, decisionNote.value || undefined)
    ElMessage.success(decision === 'approved' ? '已批准验收，任务转 succeeded' : '已驳回，任务转 failed')
    decisionNote.value = ''
    await refresh()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    deciding.value = false
  }
}

async function doStop() {
  stopping.value = true
  try {
    await missionApi.stop(missionId, 'owner 手动终止')
    ElMessage.success('已下发停止指令，agent 心跳将感知并收尾')
    await refresh()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    stopping.value = false
  }
}

const { start, stop: stopPolling } = usePolling(refresh, 5000)
let clock: number | undefined
onMounted(async () => {
  await refresh()
  start()
  clock = window.setInterval(() => (now.value = Date.now()), 1000)
})
onUnmounted(() => { stopPolling(); if (clock) clearInterval(clock) })
</script>

<style scoped lang="scss">
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.tf-dim { color: var(--tf-ink-500); font-size: 12px; }
.tf-hb-live { color: #2fae8b; }
.tf-hb-stale { color: #c24545; }
.tf-md-meta { display: flex; flex-direction: column; gap: 9px; }
.tf-md-row {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12.5px;
  span:first-child { color: var(--tf-ink-500); }
}
.tf-md-budget { margin-bottom: 12px; }
.tf-md-budget-head {
  display: flex; justify-content: space-between; font-size: 12px;
  margin-bottom: 4px;
}
.tf-md-approval { border-top: 1px solid var(--tf-line-200); padding-top: 8px; &:first-of-type { border-top: none; } }
.tf-md-child { border-top: 1px solid var(--tf-line-200); padding: 8px 0; &:first-of-type { border-top: none; } }
.tf-md-child .tf-rs { display: flex; align-items: center; gap: 10px; }
.tf-md-child-goal { margin-top: 5px; font-size: 12px; color: var(--tf-ink-900); }
.tf-md-evidence {
  margin-top: 6px; font-size: 12px; color: var(--tf-ink-900);
  white-space: pre-wrap; word-break: break-word; max-height: 180px; overflow: auto;
}
.tf-md-json {
  background: #fafcfe; border-radius: 6px; padding: 8px 10px; margin: 6px 0 0;
  max-height: 260px; overflow: auto; line-height: 1.5; font-size: 11.5px;
}
.tf-md-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.tf-md-stream { max-height: 640px; overflow: auto; }
.tf-md-ev {
  padding: 6px 4px; border-bottom: 1px solid #f0f3f7; cursor: pointer;
  &:hover { background: #f7fafc; }
  &:last-child { border-bottom: none; }
}
.tf-md-ev-line { display: flex; align-items: center; gap: 10px; font-size: 12.5px; }
.tf-ef-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.tf-md-ev-kind { font-weight: 600; flex-shrink: 0; width: 64px; }
.tf-md-ev-sum {
  flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace; font-size: 11.5px;
}
.tf-md-ev-ago { color: #a5aeb8; flex-shrink: 0; }
.tf-rs { display: flex; align-items: center; gap: 8px; }
</style>
