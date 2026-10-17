<template>
  <section class="tf-hero">
    <div class="tf-hero-title">
      <slot name="title" />
    </div>
    <div class="tf-hero-sub">
      <slot name="sub" />
    </div>
    <div v-if="$slots.extra" class="tf-hero-extra">
      <slot name="extra" />
    </div>
  </section>
</template>

<script setup lang="ts">
</script>

<style scoped lang="scss">
.tf-hero-extra { margin-top: 10px; display: flex; gap: 24px; font-size: 13px; }
</style>
