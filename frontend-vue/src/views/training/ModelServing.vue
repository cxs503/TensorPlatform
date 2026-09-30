<template>
  <div class="serving-page">
    <PageHeader title="模型服务" desc="快速测试已部署的推理端点">
      <template #actions>
        <el-button @click="refresh"><el-icon><Refresh /></el-icon>刷新</el-button>
      </template>
    </PageHeader>

    <DataTable
      title="运行中端点"
      :data="runningEndpoints"
      :loading="loading"
      :error="error"
      :empty="runningEndpoints.length === 0"
      empty-text="暂无运行中的端点，请在部署管理中创建"
      @retry="refresh"
    >
      <el-table-column prop="model_id" label="模型ID" width="90" />
      <el-table-column prop="model_name" label="模型名称" min-width="180" show-overflow-tooltip />
      <el-table-column prop="endpoint" label="接口路径" min-width="300" show-overflow-tooltip>
        <template #default="{ row }">
          <code class="endpoint-code">{{ row.endpoint }}</code>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="部署时间" width="160">
        <template #default="{ row }">{{ (row.created_at || '').slice(0,19) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="quickTest(row)">快速测试</el-button>
        </template>
      </el-table-column>
    </DataTable>

    <!-- 快速测试弹窗 -->
    <el-dialog v-model="testVisible" title="快速推理" width="550px">
      <template v-if="currentEp">
        <p>端点: <code>{{ currentEp.endpoint }}</code></p>
        <p>模型: <strong>#{{ currentEp.model_id }} {{ currentEp.model_name || '未知模型' }}</strong></p>
        <el-input v-model="testInput" type="textarea" :rows="3" placeholder="[[1.0, 2.0, 3.0, 4.0, 5.0]]" style="margin:12px 0" />
        <el-button type="primary" @click="doTest" :loading="testing">执行推理</el-button>
        <div v-if="testResult" class="result-box">
          <el-alert type="success" :closable="false">
            <template #title>预测结果: {{ JSON.stringify(testResult.predictions) }}</template>
          </el-alert>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { get, post } from '@/api/legacy'
import { useAsyncData } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import DataTable from '@/components/DataTable.vue'

const { data: raw, loading, error, refresh } = useAsyncData<any>(() => get('/model-deployment/deploys'))
const endpoints = computed(() => raw.value?.items || [])
const runningEndpoints = computed(() => endpoints.value.filter((e: any) => e.status === 'running'))

const testVisible = ref(false)
const currentEp = ref<any>(null)
const testInput = ref('[[1.0, 2.0, 3.0, 4.0, 5.0]]')
const testResult = ref<any>(null)
const testing = ref(false)

function quickTest(row: any) { currentEp.value = row; testResult.value = null; testVisible.value = true }
async function doTest() {
  testing.value = true; testResult.value = null
  try {
    const X = JSON.parse(testInput.value)
    testResult.value = await post(`/model-deployment/deploys/${currentEp.value.id}/predict`, { data: X })
  } catch (e: any) { testResult.value = { error: e?.response?.data?.detail || e.message } }
  finally { testing.value = false }
}
</script>

<style lang="scss" scoped>
.serving-page { .endpoint-code { font-size:12px; background:#f5f5f5; padding:2px 6px; border-radius:4px; } .result-box { margin-top:16px; } }
</style>
