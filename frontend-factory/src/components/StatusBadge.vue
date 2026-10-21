<template>
  <el-tag :type="tagType" size="small" effect="light" round>{{ label || status }}</el-tag>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status: string
  label?: string
}>()

const MAP: Record<string, string> = {
  // run / execution 共用族
  succeeded: 'success',
  passed: 'success',
  completed: 'success',
  approved: 'success',
  running: 'primary',
  claimed: 'primary',
  queued: 'info',
  planned: 'info',
  validating: 'warning',
  pending: 'warning',
  not_run: 'info',
  cancel_requested: 'warning',
  incomplete: 'warning',
  stop: 'danger',
  failed: 'danger',
  cancelled: 'info',
  rejected: 'danger',
  preempted: 'warning',
  active: 'primary',
  stopped: 'info',
}

const tagType = computed(() => MAP[props.status] || 'info')
</script>
