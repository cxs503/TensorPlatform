<template>
  <div class="tf-ladder">
    <div
      v-for="(stage, i) in stages"
      :key="stage"
      class="tf-ladder-step"
      :class="stepClass(stage, i)"
    >
      <div class="tf-ladder-dot">{{ i }}</div>
      <div class="tf-ladder-body">
        <div class="tf-ladder-stage">{{ stage }} · {{ STAGE_LABELS[stage] }}</div>
        <div class="tf-ladder-meta">
          <template v-if="statusOf(stage)">
            <StatusBadge :status="statusOf(stage).state || 'pending'" />
            <span v-if="statusOf(stage)?.checks_total" class="tf-ladder-checks">
              {{ statusOf(stage).checks_passed }}/{{ statusOf(stage).checks_total }} 检查
            </span>
            <span v-if="statusOf(stage)?.evidence_run_id" class="tf-mono tf-ladder-run">
              {{ statusOf(stage).evidence_run_id }}
            </span>
          </template>
          <span v-else class="tf-ladder-locked">未解锁</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{
  currentStage: string
  stageStatus: Record<string, any>
}>()

const STAGES = ['S0', 'S1', 'S2', 'S3', 'S4']
const STAGE_LABELS: Record<string, string> = {
  S0: '可复现基线',
  S1: '物理引入',
  S2: '标定',
  S3: '外推验证',
  S4: '工程交付',
}

const stages = STAGES
const currentIndex = computed(() => STAGES.indexOf(props.currentStage))

function statusOf(stage: string) {
  return props.stageStatus?.[stage]
}

function stepClass(stage: string, i: number) {
  const st = statusOf(stage)?.state
  return {
    'is-current': i === currentIndex.value,
    'is-done': st === 'completed' || i < currentIndex.value,
    'is-failed': st === 'failed',
  }
}
</script>

<style scoped lang="scss">
.tf-ladder { display: flex; flex-direction: column; gap: 0; }
.tf-ladder-step {
  display: flex;
  gap: 14px;
  padding: 10px 6px;
  border-radius: 8px;
  position: relative;

  &.is-current { background: rgba(109, 225, 191, 0.10); outline: 1px solid rgba(109, 225, 191, 0.5); }
  &.is-failed { background: rgba(194, 69, 69, 0.06); }
}
.tf-ladder-dot {
  width: 30px; height: 30px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600;
  background: var(--tf-line-200); color: var(--tf-ink-500);
  flex-shrink: 0;
}
.is-done .tf-ladder-dot { background: var(--tf-mint-600); color: #fff; }
.is-current .tf-ladder-dot { background: var(--tf-navy-700); color: var(--tf-mint-400); }
.is-failed .tf-ladder-dot { background: var(--tf-danger-600); color: #fff; }

.tf-ladder-stage { font-size: 13.5px; font-weight: 600; color: var(--tf-navy-700); }
.tf-ladder-meta { margin-top: 6px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.tf-ladder-checks { font-size: 12.5px; color: var(--tf-ink-500); font-variant-numeric: tabular-nums; }
.tf-ladder-run { color: var(--tf-ink-500); }
.tf-ladder-locked { font-size: 12.5px; color: #a5aeb8; }
</style>
