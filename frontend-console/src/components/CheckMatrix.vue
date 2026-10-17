<template>
  <div class="tf-checks">
    <div
      v-for="check in checks"
      :key="check.id"
      class="tf-check-row"
      :class="{ 'is-failed': !isPassed(check) }"
    >
      <span class="tf-check-mark">{{ isPassed(check) ? '✓' : '✗' }}</span>
      <span class="tf-check-name tf-mono">{{ check.id }}</span>
      <StatusBadge :status="check.status" />
    </div>
    <div v-if="!checks.length" class="tf-check-empty">（无检查记录）</div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  checks: Array<{ id: string; status: string }>
}>()

function isPassed(check: { status: string }) {
  return check.status === 'passed'
}
</script>

<style scoped lang="scss">
.tf-checks { display: flex; flex-direction: column; gap: 4px; }
.tf-check-row {
  display: flex; align-items: center; gap: 10px;
  padding: 5px 10px; border-radius: 6px; background: #fafcfe;
  &.is-failed { background: rgba(194, 69, 69, 0.06); }
}
.tf-check-mark { color: var(--tf-mint-600); font-weight: 700; width: 14px; }
.is-failed .tf-check-mark { color: var(--tf-danger-600); }
.tf-check-name { flex: 1; color: var(--tf-ink-900); }
.tf-check-empty { font-size: 13px; color: #a5aeb8; padding: 6px; }
</style>
