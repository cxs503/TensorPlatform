<template>
  <div class="page-header">
    <div class="page-header-left">
      <h1 class="page-title">
        {{ title }}
        <el-button circle size="small" class="help-btn" @click="showGuide = true" title="查看使用说明">
          <el-icon><QuestionFilled /></el-icon>
        </el-button>
      </h1>
      <p v-if="desc" class="page-desc">{{ desc }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header-actions">
      <slot name="actions" />
    </div>

    <!-- Guide Dialog -->
    <el-dialog v-model="showGuide" :title="guide?.title || '使用说明'" width="600px" destroy-on-close>
      <template v-if="guide">
        <p class="guide-desc">{{ guide.desc }}</p>

        <div v-if="guide.steps?.length" class="guide-section">
          <h4>📋 操作步骤</h4>
          <ol class="guide-list">
            <li v-for="s in guide.steps" :key="s">{{ s }}</li>
          </ol>
        </div>

        <div v-if="guide.tips?.length" class="guide-section">
          <h4>💡 使用技巧</h4>
          <ul class="guide-list">
            <li v-for="t in guide.tips" :key="t">{{ t }}</li>
          </ul>
        </div>

        <div v-if="guide.api?.length" class="guide-section">
          <h4>🔌 相关API</h4>
          <ul class="guide-list api-list">
            <li v-for="a in guide.api" :key="a"><code>{{ a }}</code></li>
          </ul>
        </div>
      </template>
      <template v-else>
        <el-empty description="暂无使用说明" />
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { moduleGuides } from '@/config/guides'
import { QuestionFilled } from '@element-plus/icons-vue'

defineProps<{ title: string; desc?: string }>()

const route = useRoute()
const showGuide = ref(false)

const guide = computed(() => {
  const path = route.path
  // Exact match first
  if (moduleGuides[path]) return moduleGuides[path]
  // Prefix match
  for (const key of Object.keys(moduleGuides)) {
    if (path.startsWith(key + '/')) return moduleGuides[key]
  }
  return null
})
</script>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
.page-header-left { flex: 1; }
.page-title { font-size: 22px; font-weight: 700; color: var(--el-text-color-primary,#e2e8f0); margin: 0 0 6px 0; display: flex; align-items: center; gap: 6px; }
.page-desc { font-size: 13px; color: var(--el-text-color-secondary,#94a3b8); margin: 0; }
.page-header-actions { display: flex; gap: 8px; flex-shrink: 0; margin-top: 2px; }
.help-btn { border: 1px dashed var(--el-color-primary); color: var(--el-color-primary); }
.guide-desc { font-size: 14px; color: var(--el-text-color-regular); margin-bottom: 16px; line-height: 1.6; }
.guide-section { margin-bottom: 16px; }
.guide-section h4 { font-size: 14px; margin-bottom: 8px; color: var(--el-color-primary); }
.guide-list { padding-left: 20px; font-size: 13px; color: var(--el-text-color-secondary); line-height: 2; }
.api-list li { line-height: 1.8; }
code { background: rgba(99,102,241,.1); color: #818cf8; padding: 2px 6px; border-radius: 3px; font-size: 12px; }
</style>
