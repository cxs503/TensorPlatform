<template>
  <div class="tf-page" v-if="run">
    <HeroPanel>
      <template #title>
        <span class="tf-mono" style="font-size: 16px">{{ run.run_id }}</span>
      </template>
      <template #sub>
        producer {{ manifest.producer?.name }}@{{ (manifest.producer?.revision || '').slice(0, 8) }}
        · 登记 {{ fmtTime(run.created_at) }} · manifest_sha256
        <span class="tf-mono">{{ (run.manifest_sha256 || '').slice(0, 16) }}…</span>
      </template>
      <template #extra>
        <span>状态 <StatusBadge :status="run.status" /></span>
        <span>验证 <StatusBadge :status="run.validation_status" /></span>
        <span>检查
          <b class="tf-hero-mint">{{ manifest.metrics?.checks_passed ?? '—' }}/{{ manifest.metrics?.checks_total ?? '—' }}</b>
        </span>
        <span>工件 <b class="tf-hero-mint">{{ outputs.length }}</b></span>
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="tf-card">
          <div class="tf-card-title">验证检查矩阵 <StatusBadge :status="manifest.validation?.status" /></div>
          <CheckMatrix :checks="manifest.validation?.checks || []" />
        </div>
        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">血缘（Lineage）</div>
          <LineageTree :parents="manifest.parent_run_ids || []" :self-id="run.run_id" />
        </div>
        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">code_revisions</div>
          <div class="tf-mono tf-rev" v-for="(v, k) in manifest.code_revisions || {}" :key="k">
            {{ k }} = {{ v }}
          </div>
        </div>
      </el-col>

      <el-col :span="12">
        <div class="tf-card">
          <div class="tf-card-title">工件清单（S3 · sha256 逐件）</div>
          <ArtifactTable :outputs="outputs" :run-id="run.run_id" />
        </div>
        <div class="tf-card" style="margin-top: 16px">
          <div class="tf-card-title">metrics / goal</div>
          <pre class="tf-mono tf-manifest">{{ pretty({goal: manifest.goal, metrics: manifest.metrics, resources: manifest.resources}) }}</pre>
        </div>
        <ScienceWarning style="margin-top: 16px" :limitations="manifest.limitations || []" />
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { runApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import CheckMatrix from '@/components/CheckMatrix.vue'
import ArtifactTable from '@/components/ArtifactTable.vue'
import LineageTree from '@/components/LineageTree.vue'
import ScienceWarning from '@/components/ScienceWarning.vue'

const route = useRoute()
const runId = route.params.runId as string
const run = ref<any>(null)
const manifest = computed(() => run.value?.manifest || {})
const outputs = computed(() => manifest.value.outputs || [])

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

function pretty(obj: any) {
  return JSON.stringify(obj, null, 2)
}

onMounted(async () => {
  run.value = await runApi.get(decodeURIComponent(runId))
})
</script>

<style scoped>
.tf-rev { color: var(--tf-ink-500); padding: 2px 0; }
.tf-manifest {
  background: #fafcfe; border-radius: 8px; padding: 12px;
  max-height: 300px; overflow: auto; margin: 0; line-height: 1.6;
}
</style>
