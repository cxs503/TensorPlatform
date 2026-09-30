<template>
  <div class="chart-card-wrapper">
    <el-card class="chart-card" shadow="hover">
      <template #header>
        <div class="chart-card-header">
          <span>{{ title }}</span>
          <slot name="header-actions" />
        </div>
      </template>

      <!-- Loading -->
      <div v-if="loading" class="chart-card-state">
        <el-skeleton animated :rows="6" />
      </div>

      <!-- Error -->
      <div v-else-if="error" class="chart-card-state">
        <el-result icon="error" :title="error" sub-title="请检查网络连接或稍后重试">
          <template #extra>
            <el-button type="primary" size="small" @click="$emit('retry')">重试</el-button>
          </template>
        </el-result>
      </div>

      <!-- Empty -->
      <div v-else-if="empty" class="chart-card-state">
        <el-empty :description="emptyText" :image-size="80">
          <slot name="empty-actions" />
        </el-empty>
      </div>

      <!-- Content -->
      <div v-else class="chart-card-body" :style="{ height: height }">
        <slot />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  loading?: boolean
  error?: string
  empty?: boolean
  emptyText?: string
  height?: string
}>(), {
  loading: false,
  error: '',
  empty: false,
  emptyText: '暂无数据',
  height: '300px',
})

defineEmits<{
  retry: []
}>()
</script>

<style scoped>
.chart-card-wrapper {
  margin-bottom: 16px;
}

.chart-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 14px;
}

.chart-card-state {
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px 0;
}

.chart-card-body {
  position: relative;
}
</style>
