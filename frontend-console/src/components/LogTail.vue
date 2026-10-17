<template>
  <div class="tf-logtail tf-card">
    <div class="tf-card-title">
      <span>日志尾迹</span>
      <span class="tf-logtail-size">{{ lines }} 行 · 更新 {{ updatedAt }}</span>
    </div>
    <pre ref="box" class="tf-logtail-pre tf-mono">{{ text || '（暂无输出）' }}</pre>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'

const props = defineProps<{
  text?: string | null
  updatedAt?: string
}>()

const box = ref<HTMLElement | null>(null)
const lines = ref(0)

watch(
  () => props.text,
  async (v) => {
    lines.value = v ? v.split('\n').length : 0
    await nextTick()
    if (box.value) box.value.scrollTop = box.value.scrollHeight
  },
  { immediate: true }
)
</script>

<style scoped lang="scss">
.tf-logtail-pre {
  background: var(--tf-navy-900);
  color: #cfe6dd;
  border-radius: 8px;
  padding: 12px 14px;
  max-height: 360px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  line-height: 1.55;
}
.tf-logtail-size { font-size: 12px; color: var(--tf-ink-500); font-weight: 400; }
</style>
