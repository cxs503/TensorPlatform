<template>
  <div class="tf-card">
    <div class="tf-card-title">
      <span>求解器矩阵</span>
      <span class="tf-sm-sub">各腿最近一次注册 Run</span>
    </div>
    <div class="tf-sm-grid">
      <div
        v-for="s in solvers"
        :key="s.leg"
        class="tf-sm-cell"
        :class="{ ok: s.run && s.run.validation_status === 'passed', bad: s.run && s.run.validation_status !== 'passed' }"
        @click="s.run && $router.push(`/runs/${s.run.run_id}`)"
      >
        <div class="tf-sm-name">{{ s.name }}</div>
        <template v-if="s.run">
          <div class="tf-sm-checks">
            <b>{{ s.run.checks_passed ?? '—' }}</b>/<span>{{ s.run.checks_total ?? '—' }}</span>
            <span class="tf-sm-cap">检查</span>
          </div>
          <div class="tf-sm-meta">
            <StatusBadge :status="s.run.validation_status || s.run.status" />
            <span>{{ fmtAgo(now, parseT(s.run.created_at)) }}</span>
          </div>
        </template>
        <div v-else class="tf-sm-none">尚无注册 Run</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'
import { fmtAgo, parseT } from '@/utils/time'

const props = defineProps<{
  runs: any[]
  now: number
}>()

const SOLVERS = [
  { leg: 'lbm', name: 'TensorLBM' },
  { leg: 'fvm', name: 'TensorFVM' },
  { leg: 'fem', name: 'TensorFEM' },
  { leg: 'dem', name: 'TensorDEM' },
]

const solvers = computed(() =>
  SOLVERS.map((s) => ({
    ...s,
    run: (props.runs || []).find((r: any) => r.run_id?.endsWith(`-${s.leg}`)) || null,
  }))
)
</script>

<style scoped lang="scss">
.tf-sm-sub { font-size: 12px; color: var(--tf-ink-500); font-weight: 400; margin-left: auto; }
.tf-sm-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.tf-sm-cell {
  border: 1px solid var(--tf-line-200); border-radius: 8px; padding: 12px 14px;
  cursor: pointer; transition: border-color 0.15s;
  &:hover { border-color: var(--tf-mint-600); }
  &.ok { border-left: 3px solid var(--tf-mint-600); }
  &.bad { border-left: 3px solid var(--tf-danger-600); }
}
.tf-sm-none { font-size: 12.5px; color: #a5aeb8; padding: 8px 0; }
.tf-sm-name { font-weight: 600; color: var(--tf-navy-700); font-size: 13.5px; }
.tf-sm-checks {
  margin-top: 8px; font-size: 22px; font-weight: 600;
  color: var(--tf-navy-700); font-variant-numeric: tabular-nums;
  span { font-size: 14px; color: var(--tf-ink-500); }
  .tf-sm-cap { font-size: 12px; color: var(--tf-ink-500); font-weight: 400; margin-left: 4px; }
}
.tf-sm-meta {
  margin-top: 8px; display: flex; gap: 10px; align-items: center;
  font-size: 12px; color: var(--tf-ink-500);
}
</style>
