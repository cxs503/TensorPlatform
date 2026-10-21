<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>Agent 会话 <span class="tf-hero-mint">· append-only 审计链</span></template>
      <template #sub>
        DB 触发器硬禁 UPDATE/DELETE；事件流是 agent 行为的唯一可信记录。
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="8">
        <div class="tf-card">
          <div class="tf-card-title">会话列表</div>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="tf-session"
            :class="{ 'is-active': s.id === selected }"
            @click="select(s.id)"
          >
            <div class="tf-rs">
              <span class="tf-mono">#{{ s.id }}</span>
              <StatusBadge :status="s.status" />
              <span class="tf-dim">{{ s.event_count }} 事件</span>
            </div>
            <div class="tf-dim tf-mono">{{ fmtTime(s.created_at) }} · {{ s.agent_name }}</div>
          </div>
          <div v-if="!sessions.length" class="tf-ov-empty">（暂无会话）</div>
        </div>
      </el-col>

      <el-col :span="16">
        <div class="tf-card">
          <div class="tf-card-title">
            时间线 <span v-if="selected" class="tf-mono">#{{ selected }}</span>
            <span v-if="selected" class="tf-dim" style="margin-left: auto">
              增量拉取 after_seq={{ latestSeq }}，5s 轮询
            </span>
          </div>
          <el-timeline v-if="events.length" style="padding-left: 4px">
            <el-timeline-item
              v-for="ev in events"
              :key="ev.seq"
              :type="dotType(ev.kind)"
              :timestamp="fmtTime(ev.created_at)"
              placement="top"
            >
              <div class="tf-ev-head">
                <span class="tf-mono tf-ev-seq">#{{ ev.seq }}</span>
                <el-tag size="small" effect="plain">{{ ev.kind }}</el-tag>
              </div>
              <pre class="tf-mono tf-ev-json">{{ pretty(ev.payload) }}</pre>
            </el-timeline-item>
          </el-timeline>
          <div v-else class="tf-ov-empty">（选择左侧会话查看事件流）</div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { sessionApi } from '@/api'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const route = useRoute()
const sessions = ref<any[]>([])
const events = ref<any[]>([])
const selected = ref<number | null>(route.params.id ? Number(route.params.id) : null)
const latestSeq = ref(0)

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

function pretty(payload: any) {
  return JSON.stringify(payload, null, 2)
}

function dotType(kind: string) {
  if (kind === 'error') return 'danger'
  if (kind === 'decision' || kind === 'approval_requested') return 'warning'
  if (kind === 'run_registered') return 'success'
  return 'primary'
}

function select(id: number) {
  selected.value = id
  events.value = []
  latestSeq.value = 0
  refresh()
}

async function refresh() {
  const res = await sessionApi.list({ limit: 50 })
  sessions.value = res.items || []
  if (selected.value === null && sessions.value.length) {
    selected.value = sessions.value[0].id
  }
  if (selected.value !== null) {
    const s = await sessionApi.get(selected.value, latestSeq.value)
    if (s.events?.length) {
      events.value.push(...s.events)
      latestSeq.value = s.latest_seq
    }
  }
}

const { start } = usePolling(refresh, 5000)
onMounted(async () => {
  await refresh().catch(() => {})
  start()
})
</script>

<style scoped>
.tf-ov-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.tf-session {
  border: 1px solid var(--tf-line-200); border-radius: 8px;
  padding: 10px 12px; margin-bottom: 8px; cursor: pointer;
}
.tf-session.is-active { border-color: var(--tf-mint-600); background: rgba(109, 225, 191, 0.06); }
.tf-rs { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.tf-dim { color: var(--tf-ink-500); font-size: 12px; }
.tf-ev-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.tf-ev-seq { color: #a5aeb8; }
.tf-ev-json {
  background: #fafcfe; border-radius: 6px; padding: 8px 10px; margin: 0;
  max-height: 220px; overflow: auto; line-height: 1.5;
}
</style>
