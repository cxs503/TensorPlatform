<template>
  <div class="not-found-page">
    <div class="not-found-content">
      <h1 class="not-found-code">404</h1>
      <p class="not-found-text">页面未找到</p>
      <p class="not-found-hint">您访问的页面不存在或已被移除</p>
      <el-button type="primary" @click="$router.push('/')">返回首页</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
onMounted(() => {
  document.title = '404 - 开放数字化底座'
})
</script>

<style scoped>
.not-found-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: var(--el-bg-color, #f5f7fa);
}
.not-found-content {
  text-align: center;
}
.not-found-code {
  font-size: 96px;
  font-weight: 700;
  color: var(--el-color-primary, #409eff);
  margin: 0 0 16px;
}
.not-found-text {
  font-size: 20px;
  color: #606266;
  margin: 0 0 8px;
}
.not-found-hint { font-size: 14px; color: #909399; margin: 0 0 24px; }
</style>
