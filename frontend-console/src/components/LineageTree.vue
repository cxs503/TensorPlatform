<template>
  <div class="tf-lineage">
    <template v-if="parents.length">
      <div class="tf-lineage-label">父 Run（上游证据）</div>
      <div v-for="p in parents" :key="p" class="tf-lineage-node">
        <span class="tf-lineage-arrow">↳</span>
        <router-link class="tf-mono tf-lineage-link" :to="`/runs/${encodeURIComponent(p)}`">{{ p }}</router-link>
      </div>
    </template>
    <div v-else class="tf-lineage-empty">无上游（根 Run）</div>
    <div v-if="selfId" class="tf-lineage-node tf-lineage-self">
      <span class="tf-lineage-mark">◈</span>
      <span class="tf-mono">{{ selfId }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  parents: string[]
  selfId?: string
}>()
</script>

<style scoped lang="scss">
.tf-lineage-label { font-size: 12px; color: var(--tf-ink-500); margin-bottom: 6px; }
.tf-lineage-node { padding: 3px 0; display: flex; align-items: center; gap: 8px; }
.tf-lineage-arrow { color: var(--tf-mint-600); }
.tf-lineage-link { color: var(--tf-navy-700); text-decoration: none; &:hover { text-decoration: underline; } }
.tf-lineage-self { font-weight: 600; color: var(--tf-ink-900); }
.tf-lineage-mark { color: var(--tf-mint-400); }
.tf-lineage-empty { font-size: 13px; color: #a5aeb8; }
</style>
