<template>
  <el-table :data="outputs" size="small" :show-header="true">
    <el-table-column label="#" type="index" width="46" />
    <el-table-column prop="role" label="角色" width="140">
      <template #default="{ row }">
        <span class="tf-mono">{{ row.role }}</span>
      </template>
    </el-table-column>
    <el-table-column label="对象" min-width="220">
      <template #default="{ row }">
        <span class="tf-mono">{{ fileName(row.uri) }}</span>
      </template>
    </el-table-column>
    <el-table-column prop="media_type" label="类型" width="150">
      <template #default="{ row }">
        <span class="tf-mono tf-dim">{{ row.media_type }}</span>
      </template>
    </el-table-column>
    <el-table-column label="大小" width="90">
      <template #default="{ row }">{{ humanSize(row.size_bytes) }}</template>
    </el-table-column>
    <el-table-column label="sha256（前 16）" width="150">
      <template #default="{ row }">
        <span class="tf-mono tf-dim">{{ (row.checksum_sha256 || '').slice(0, 16) }}…</span>
      </template>
    </el-table-column>
    <el-table-column label="" width="90" v-if="runId">
      <template #default="{ $index }">
        <el-button link type="primary" size="small" @click="download($index)">下载</el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { runApi } from '@/api'
import { authDownload } from '@/api/request'

const props = defineProps<{
  outputs: Array<{ uri: string; role?: string; media_type?: string; size_bytes?: number; checksum_sha256?: string }>
  runId?: string
}>()

function fileName(uri: string) {
  return uri.split('/').pop() || uri
}

function humanSize(n?: number) {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`
  return `${(n / 1024 / 1024).toFixed(2)} MiB`
}

async function download(index: number) {
  if (!props.runId) return
  try {
    await authDownload(runApi.artifactUrl(props.runId, index), fileName(props.outputs[index]?.uri || 'artifact'))
  } catch (e: any) {
    ElMessage.error(`下载失败：${e.message}`)
  }
}
</script>

<style scoped>
.tf-dim { color: var(--tf-ink-500); }
</style>
