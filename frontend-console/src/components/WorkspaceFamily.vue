<template>
  <div class="tf-card">
    <div class="tf-card-title">
      <span>工作区全景 · Tensor 家族</span>
      <span class="tf-wf-sub">
        {{ capturedLabel }}
      </span>
    </div>
    <div v-if="!repos.length" class="tf-wf-empty">
      尚无工作区快照——宿主执行器上线后每分钟上报六库 git 实况。
    </div>
    <div v-else class="tf-wf-grid">
      <div
        v-for="r in ordered"
        :key="r.name"
        class="tf-wf-cell"
        :class="{ bad: !!r.error }"
      >
        <div class="tf-wf-head">
          <span class="tf-wf-name">{{ r.name }}</span>
          <span class="tf-wf-role" :class="roleClass(r.name)">{{ roleOf(r.name) }}</span>
        </div>
        <template v-if="!r.error">
          <div class="tf-wf-branch tf-mono">
            <span class="tf-wf-rev">{{ r.head_short }}</span>
            <span class="tf-wf-branchname">{{ r.branch }}</span>
            <span v-if="r.dirty_files" class="tf-wf-dirty" :title="`${r.dirty_files} 个未提交改动`">
              ● {{ r.dirty_files }}
            </span>
          </div>
          <div class="tf-wf-subject">{{ r.subject || '—' }}</div>
          <div class="tf-wf-meta">{{ fmtAgo(now, parseT(r.commit_at)) }} 提交</div>
        </template>
        <div v-else class="tf-wf-err">不可读（未克隆或路径异常）</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { fmtAgo, parseT } from '@/utils/time'

const props = defineProps<{
  ws: any
  now: number
}>()

const FAMILY: Record<string, { role: string; kind: 'orchestrator' | 'platform' | 'solver' }> = {
  TensorPhysics: { role: '科研编排', kind: 'orchestrator' },
  TensorPlatform: { role: '平台平面', kind: 'platform' },
  TensorLBM: { role: '求解器 · LBM', kind: 'solver' },
  TensorFVM: { role: '求解器 · FVM', kind: 'solver' },
  TensorFEM: { role: '求解器 · FEM', kind: 'solver' },
  TensorDEM: { role: '求解器 · DEM', kind: 'solver' },
}

const repos = computed<any[]>(() => props.ws?.repos || [])

const ordered = computed(() =>
  Object.keys(FAMILY)
    .map((name) => repos.value.find((r: any) => r.name === name) || { name, error: 'missing' })
)

const capturedLabel = computed(() => {
  const t = parseT(props.ws?.captured_at)
  return t ? `宿主快照 · ${fmtAgo(props.now, t)}` : '等待宿主执行器上报'
})

function roleOf(name: string): string {
  return FAMILY[name]?.role || '成员库'
}
function roleClass(name: string): string {
  return `is-${FAMILY[name]?.kind || 'solver'}`
}
</script>

<style scoped lang="scss">
.tf-wf-sub { font-size: 12px; color: var(--tf-ink-500); font-weight: 400; margin-left: auto; }
.tf-wf-empty { font-size: 13px; color: #a5aeb8; padding: 8px 0; }
.tf-wf-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
  @media (max-width: 1100px) { grid-template-columns: repeat(2, 1fr); }
}
.tf-wf-cell {
  border: 1px solid var(--tf-line-200); border-radius: 8px; padding: 11px 13px;
  &.bad { background: #fafbfc; }
}
.tf-wf-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tf-wf-name { font-weight: 600; color: var(--tf-navy-700); font-size: 13.5px; }
.tf-wf-role {
  font-size: 11px; padding: 1px 8px; border-radius: 999px; white-space: nowrap;
  &.is-orchestrator { background: rgba(122, 162, 247, 0.16); color: #3b5fb8; }
  &.is-platform    { background: rgba(157, 124, 216, 0.14); color: #6d4fa3; }
  &.is-solver      { background: rgba(109, 225, 191, 0.16); color: #1d7a5f; }
}
.tf-wf-branch {
  margin-top: 8px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  color: var(--tf-ink-500);
}
.tf-wf-rev {
  background: var(--tf-navy-900); color: var(--tf-mint-400);
  padding: 1px 6px; border-radius: 4px; font-size: 11px;
}
.tf-wf-branchname { font-size: 11.5px; }
.tf-wf-dirty { color: var(--tf-amber-700); font-size: 11.5px; font-weight: 600; }
.tf-wf-subject {
  margin-top: 6px; font-size: 12px; color: var(--tf-ink-900);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tf-wf-meta { margin-top: 4px; font-size: 11.5px; color: #a5aeb8; }
.tf-wf-err { margin-top: 8px; font-size: 12px; color: #a5aeb8; }
</style>
