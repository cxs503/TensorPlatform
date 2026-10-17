<template>
  <div class="tf-card tf-metric">
    <div class="tf-metric-label">{{ label }}</div>
    <div class="tf-metric-value">
      <span class="tf-metric-num">{{ value }}</span>
      <span v-if="suffix" class="tf-metric-suffix">{{ suffix }}</span>
    </div>
    <div v-if="hint" class="tf-metric-hint">{{ hint }}</div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  suffix?: string
  hint?: string
}>()
</script>

<style scoped lang="scss">
.tf-metric-label { font-size: 12px; color: var(--tf-ink-500); }
.tf-metric-value { margin-top: 8px; display: flex; align-items: baseline; gap: 4px; }
.tf-metric-num { font-size: 26px; font-weight: 600; color: var(--tf-navy-700); font-variant-numeric: tabular-nums; }
.tf-metric-suffix { font-size: 13px; color: var(--tf-ink-500); }
.tf-metric-hint { margin-top: 6px; font-size: 12px; color: var(--tf-ink-500); }
</style>
