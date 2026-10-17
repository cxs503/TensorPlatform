<template>
  <div class="tf-card">
    <div class="tf-card-title">
      <span>智能体事件流</span>
      <span class="tf-ef-sub">append-only 审计链 · 新事件置顶</span>
    </div>
    <div v-if="!events.length" class="tf-ef-empty">暂无事件记录。</div>
    <div v-else class="tf-ef-list">
      <div v-for="e in events" :key="e.id" class="tf-ef-row" @click="$router.push(`/sessions/${e.session_id}`)">
        <span class="tf-ef-dot" :style="{ background: kindColor(e.kind) }" />
        <span class="tf-ef-kind" :style="{ color: kindColor(e.kind) }">{{ kindLabel(e.kind) }}</span>
        <span class="tf-ef-agent tf-mono">{{ e.agent_name || 'agent' }}</span>
        <span class="tf-ef-detail">{{ summarizeEvent(e) }}</span>
        <span class="tf-ef-ago">{{ fmtAgo(now, parseT(e.created_at)) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { kindLabel, kindColor, summarizeEvent, type AgentEvent } from '@/utils/events'
import { fmtAgo, parseT } from '@/utils/time'

defineProps<{
  events: AgentEvent[]
  now: number
}>()
</script>

<style scoped lang="scss">
.tf-ef-sub { font-size: 12px; color: var(--tf-ink-500); font-weight: 400; margin-left: auto; }
.tf-ef-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.tf-ef-list { max-height: 300px; overflow: auto; }
.tf-ef-row {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 4px; border-bottom: 1px solid #f0f3f7;
  font-size: 12.5px; cursor: pointer;
  &:hover { background: #f7fafc; }
  &:last-child { border-bottom: none; }
}
.tf-ef-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.tf-ef-kind { font-weight: 600; flex-shrink: 0; width: 64px; }
.tf-ef-agent { color: var(--tf-ink-500); flex-shrink: 0; }
.tf-ef-detail {
  flex: 1; min-width: 0; color: var(--tf-ink-900);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace;
  font-size: 11.5px;
}
.tf-ef-ago { color: #a5aeb8; flex-shrink: 0; font-variant-numeric: tabular-nums; }
</style>
