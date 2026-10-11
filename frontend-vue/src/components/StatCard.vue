<template>
  <el-card class="stat-card" shadow="hover">
    <div class="stat-header">
      <span class="stat-title">{{ title }}</span>
      <div class="stat-icon" :style="{ background: bgColor || defaultBg }">
        <el-icon :size="18" :style="{ color: iconColor || '#818cf8' }">
          <component :is="icon" />
        </el-icon>
      </div>
    </div>
    <div class="stat-body">
      <span class="stat-value">
        <template v-if="loading">
          <el-skeleton animated :rows="1" style="width: 60px" />
        </template>
        <template v-else>
          {{ formattedValue }}
        </template>
      </span>
      <span v-if="unit && !loading" class="stat-unit">{{ unit }}</span>
    </div>
    <div v-if="trend !== undefined && !loading" class="stat-footer">
      <span :class="['stat-trend', trend >= 0 ? 'up' : 'down']">
        <el-icon :size="12">
          <CaretTop v-if="trend >= 0" />
          <CaretBottom v-else />
        </el-icon>
        {{ Math.abs(trend) }}%
      </span>
      <span class="stat-trend-label">较上期</span>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  value?: string | number
  unit?: string
  icon?: string
  iconColor?: string
  bgColor?: string
  trend?: number
  loading?: boolean
}>(), {
  value: '',
  unit: '',
  icon: 'Odometer',
  loading: false,
})

const defaultBg = 'rgba(99,102,241,0.12)'

const formattedValue = computed(() => {
  if (props.value === undefined || props.value === null || props.value === '') return '--'
  if (typeof props.value === 'number') {
    return props.value.toLocaleString()
  }
  return props.value
})
</script>

<style scoped>
.stat-card :deep(.el-card__body) {
  padding: 18px 20px;
}

.stat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.stat-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary, #94a3b8);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stat-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.stat-body {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--el-text-color-primary, #e2e8f0);
  line-height: 1.2;
}

.stat-unit {
  font-size: 13px;
  color: var(--el-text-color-secondary, #64748b);
}

.stat-footer {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}

.stat-trend.up {
  color: #34d399;
}
.stat-trend.down {
  color: #f87171;
}

.stat-trend-label {
  color: var(--el-text-color-secondary, #64748b);
}
</style>
