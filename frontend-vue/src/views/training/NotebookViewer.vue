<template>
  <div class="nb-viewer">
    <div v-if="loading" class="loading-wrap">
      <el-skeleton :rows="8" animated />
    </div>
    <div v-else-if="error" class="error-wrap">
      <el-result icon="error" title="加载失败" :sub-title="error">
        <template #extra>
          <el-button @click="$router.push('/model/notebook')">返回列表</el-button>
        </template>
      </el-result>
    </div>
    <div v-else class="nb-content">
      <div class="nb-header">
        <el-button @click="$router.push('/model/notebook')" text>
          <el-icon><ArrowLeft /></el-icon> 返回
        </el-button>
        <h2>{{ nb?.name || '笔记本' }}</h2>
        <span class="nb-id">ID: {{ nb?.id }}</span>
      </div>
      <el-card shadow="never" class="nb-info">
        <template #header>笔记本信息</template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="名称">{{ nb?.name }}</el-descriptions-item>
          <el-descriptions-item label="所有者">{{ nb?.owner || 'anonymous' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ nb?.created_at }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ nb?.status || 'stopped' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
      <el-card v-if="nb?.content" shadow="never" class="nb-cells">
        <template #header>单元格 ({{ nb.cells_count || 0 }} 个)</template>
        <div v-for="(cell, idx) in (nb.content?.cells || [])" :key="idx" class="cell-item">
          <el-tag size="small" :type="cell.cell_type === 'code' ? '' : 'info'">{{ cell.cell_type }}</el-tag>
          <pre class="cell-source">{{ cell.source }}</pre>
        </div>
      </el-card>
      <el-card shadow="never" class="nb-actions">
        <template #header>操作</template>
        <div v-if="jupyterRunning" style="display:flex;gap:12px;align-items:center">
          <el-tag type="success">JupyterLab 运行中</el-tag>
          <el-button type="primary" @click="openInJupyter">
            <el-icon><Link /></el-icon> 在 JupyterLab 中打开
          </el-button>
        </div>
        <div v-else>
          <el-tag type="warning">Jupyter 服务未启动</el-tag>
          <p class="text-muted" style="margin-top:8px">启动 Jupyter 后可在线编辑和执行代码。</p>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '@/api/legacy'

const route = useRoute()
const nb = ref<any>(null)
const loading = ref(true)
const error = ref('')
const jupyterRunning = ref(false)

onMounted(async () => {
  try {
    const id = route.params.id as string
    const [nbRes, jpRes] = await Promise.all([
      get(`/notebooks/${id}`),
      get('/notebooks/jupyter/status').catch(() => ({ running: false }))
    ])
    nb.value = nbRes
    jupyterRunning.value = jpRes?.running || false
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
})

function openInJupyter() {
  window.open('/jupyter/lab', '_blank')
}
</script>

<style scoped>
.nb-viewer { max-width: 900px; margin: 0 auto; padding: 20px; }
.nb-header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.nb-id { font-size: 12px; color: var(--el-text-color-placeholder); }
.nb-info, .nb-cells { margin-bottom: 16px; }
.text-muted { color: var(--el-text-color-secondary); font-size: 13px; }
</style>
