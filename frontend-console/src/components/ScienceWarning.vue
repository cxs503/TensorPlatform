<template>
  <div v-if="limitations.length" class="tf-science-warning">
    <div class="tf-sw-title">⚠ 科学有效性边界（limitations，机器生成，逐条保留）</div>
    <ul class="tf-sw-list">
      <li v-for="(item, i) in limitations" :key="i">{{ item }}</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  limitations: string[]
}>()
</script>

<style scoped lang="scss">
.tf-sw-title { font-weight: 600; margin-bottom: 6px; }
.tf-sw-list { margin: 0; padding-left: 18px; }
</style>
