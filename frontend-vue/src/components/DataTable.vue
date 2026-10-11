<template>
  <el-card class="data-table-card" shadow="hover">
    <!-- Header with title and actions -->
    <template v-if="title" #header>
      <div class="dt-header">
        <span class="dt-title">{{ title }}</span>
        <div class="dt-actions">
          <slot name="header-actions" />
        </div>
      </div>
    </template>

    <!-- Search / Filter bar -->
    <div v-if="$slots.filters" class="dt-filters">
      <slot name="filters" />
    </div>

    <!-- Loading -->
    <div v-if="loading" class="dt-state">
      <el-skeleton :rows="5" animated />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="dt-state">
      <el-result icon="error" :title="error">
        <template #extra>
          <el-button type="primary" size="small" @click="$emit('retry')">重试</el-button>
        </template>
      </el-result>
    </div>

    <!-- Empty -->
    <div v-else-if="empty" class="dt-state">
      <EmptyState :description="emptyText" />
    </div>

    <!-- Table -->
    <template v-else>
      <el-table
        :data="data"
        v-bind="$attrs"
        stripe
        border
        style="width: 100%"
        @selection-change="$emit('selection-change', $event)"
      >
        <slot />
      </el-table>

      <!-- Pagination -->
      <div v-if="showPagination && total > pageSize" class="dt-pagination">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="currentPageSize"
          :total="total"
          :page-sizes="pageSizeOptions"
          layout="total, sizes, prev, pager, next"
          background
          @size-change="$emit('size-change', $event)"
          @current-change="$emit('page-change', $event)"
        />
      </div>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import EmptyState from './EmptyState.vue'

const props = withDefaults(defineProps<{
  title?: string
  data?: any[]
  loading?: boolean
  error?: string
  empty?: boolean
  emptyText?: string
  showPagination?: boolean
  total?: number
  pageSize?: number
  pageSizeOptions?: number[]
}>(), {
  data: () => [],
  loading: false,
  error: '',
  empty: false,
  emptyText: '暂无数据',
  showPagination: false,
  total: 0,
  pageSize: 20,
  pageSizeOptions: () => [10, 20, 50, 100],
})

const emit = defineEmits<{
  retry: []
  'selection-change': [value: any]
  'size-change': [size: number]
  'page-change': [page: number]
}>()

const currentPage = ref(1)
const currentPageSize = ref(props.pageSize)

watch(() => props.pageSize, (v) => { currentPageSize.value = v })
</script>

<style scoped>
.data-table-card {
  margin-bottom: 16px;
}

.dt-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.dt-title {
  font-weight: 600;
  font-size: 14px;
}

.dt-actions {
  display: flex;
  gap: 8px;
}

.dt-filters {
  margin-bottom: 16px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}

.dt-state {
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px 0;
}

.dt-pagination {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
