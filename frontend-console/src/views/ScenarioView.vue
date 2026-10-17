<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>
        {{ scenario?.display_name || scenarioKey }}
        <span class="tf-hero-mint">· {{ scenario?.current_stage }}</span>
      </template>
      <template #sub>{{ scenario?.description }}</template>
      <template #extra>
        <span v-if="scenario?.stage_status?.S0?.evidence_run_id">
          S0 证据
          <router-link class="tf-hero-mint tf-mono" :to="`/runs/${scenario.stage_status.S0.evidence_run_id}`">
            {{ scenario.stage_status.S0.evidence_run_id }}
          </router-link>
        </span>
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="10">
        <div class="tf-card">
          <div class="tf-card-title">科研阶梯 S0 → S4</div>
          <StageLadder v-if="scenario" :current-stage="scenario.current_stage"
                       :stage-status="scenario.stage_status || {}" />
          <ScienceWarning style="margin-top: 12px" :limitations="[
            '阶梯晋级必须经 owner 人审（stage_promotion 审批），agent 不自审。',
          ]" />
        </div>
      </el-col>

      <el-col :span="14">
        <div class="tf-card">
          <div class="tf-card-title">
            执行历史
            <el-button
              v-if="auth.canOperate"
              type="primary" size="small" style="margin-left: auto"
              @click="dialog = true"
            >触发执行</el-button>
          </div>
          <el-table :data="executions" size="small">
            <el-table-column label="execution_key" min-width="230">
              <template #default="{ row }">
                <router-link class="tf-mono tf-link" :to="`/executions/${row.id}`">{{ row.execution_key }}</router-link>
              </template>
            </el-table-column>
            <el-table-column prop="stage" label="阶段" width="60" />
            <el-table-column label="状态" width="120">
              <template #default="{ row }"><StatusBadge :status="row.status" /></template>
            </el-table-column>
            <el-table-column label="预算" width="90">
              <template #default="{ row }">{{ row.budget?.max_wall_seconds }}s</template>
            </el-table-column>
            <el-table-column label="run 数" width="70">
              <template #default="{ row }">{{ (row.registered_run_ids || []).length }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="触发时间" width="170">
              <template #default="{ row }"><span class="tf-mono">{{ fmtTime(row.created_at) }}</span></template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
    </el-row>

    <el-dialog v-model="dialog" title="触发场景执行" width="480px">
      <el-form label-width="110px">
        <el-form-item label="场景">
          <el-input :model-value="scenarioKey" disabled />
        </el-form-item>
        <el-form-item label="阶段">
          <el-select v-model="triggerStage" style="width: 160px">
            <el-option v-for="s in ['S0','S1','S2','S3','S4']" :key="s" :label="s" :value="s"
                       :disabled="s !== scenario?.current_stage" />
          </el-select>
        </el-form-item>
        <el-form-item label="预算（秒）">
          <el-slider v-model="budget" :min="60" :max="7200" :step="60" show-input
                     :show-input-controls="false" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="triggering" @click="trigger">加入队列</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { scenarioApi, executionApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import HeroPanel from '@/components/HeroPanel.vue'
import StageLadder from '@/components/StageLadder.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ScienceWarning from '@/components/ScienceWarning.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const scenarioKey = route.params.key as string

const scenario = ref<any>(null)
const executions = ref<any[]>([])
const dialog = ref(false)
const triggerStage = ref('S0')
const budget = ref(900)
const triggering = ref(false)

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

async function refresh() {
  const [s, e] = await Promise.all([
    scenarioApi.get(scenarioKey),
    executionApi.list({ scenario_key: scenarioKey, limit: 30 }),
  ])
  scenario.value = s
  triggerStage.value = s.current_stage
  executions.value = e.items || []
}

async function trigger() {
  triggering.value = true
  try {
    const res = await executionApi.trigger({
      scenario_key: scenarioKey,
      stage: triggerStage.value,
      max_wall_seconds: budget.value,
    })
    ElMessage.success(`已入队：${res.execution_key}`)
    dialog.value = false
    router.push(`/executions/${res.id}`)
  } catch (err: any) {
    ElMessage.error(err.message)
  } finally {
    triggering.value = false
  }
}

const { start, stop } = usePolling(refresh, 5000)
onMounted(async () => {
  await refresh().catch(() => {})
  start()
})
</script>

<style scoped>
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
</style>
