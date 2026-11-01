<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>论文生产线 <span class="tf-hero-mint">· paper production line</span></template>
      <template #sub>
        独立于进展报告的正式学术论文战线：选题挖矿 → 成稿 → 对抗盲审 → 回合 master。
        每个数值须可溯源（tensor_reports id / 工件路径 / git commit），编造即整稿 FAIL。
      </template>
    </HeroPanel>

    <div class="tf-card pp-stats">
      <div class="pp-stat"><b>{{ s.topics }}</b><span>登记课题</span></div>
      <div class="pp-stat"><b>{{ s.drafts_active }}</b><span>在写成稿</span></div>
      <div class="pp-stat ok"><b>{{ s.verdict_pass }}</b><span>评审 PASS</span></div>
      <div class="pp-stat warn"><b>{{ s.verdict_minor }}</b><span>MINOR 修订</span></div>
      <div class="pp-stat bad"><b>{{ s.verdict_fail }}</b><span>FAIL（含编造否决）</span></div>
      <div class="pp-stat"><b>{{ s.mat_total }}</b><span>素材实验单</span></div>
      <div class="pp-meta">registry @ {{ line.registry_master || '—' }}</div>
    </div>

    <div class="tf-card">
      <h3 class="pp-h">成稿链（draft → 对抗盲审）</h3>
      <el-table :data="line.drafts || []" size="small">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column prop="slug" label="课题" min-width="200" />
        <el-table-column label="成稿状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)" effect="plain">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="claimed_by" label="执行席" width="130" />
        <el-table-column prop="created" label="派发" width="110" />
        <el-table-column label="盲审单" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.review" size="small" effect="plain" type="info">
              {{ row.review.status }}（{{ row.review.claimed_by || '—' }}）
            </el-tag>
            <span v-else class="pp-dim">看门狗待派</span>
          </template>
        </el-table-column>
        <el-table-column label="判决" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.verdict === 'PASS'" type="success" size="small">PASS</el-tag>
            <el-tag v-else-if="row.verdict === 'MINOR'" type="warning" size="small">MINOR</el-tag>
            <el-tag v-else-if="row.verdict === 'FAIL'" type="danger" size="small">FAIL</el-tag>
            <el-tag v-else-if="row.verdict === 'SELF'" type="info" size="small">自评回避</el-tag>
            <span v-else class="pp-dim">—</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="tf-card">
      <h3 class="pp-h">课题登记簿（append-only）</h3>
      <el-table :data="line.topics || []" size="small" :max-height="440">
        <el-table-column prop="slug" label="课题" min-width="230" />
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag size="small" :type="topicType(row.status)" effect="plain">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注 / 证据与判决" min-width="380" show-overflow-tooltip />
      </el-table>
    </div>

    <div class="tf-card">
      <h3 class="pp-h">挖矿波次 · 素材实验单</h3>
      <el-table :data="line.mines || []" size="small" style="margin-bottom: 10px">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column prop="task_key" label="波次" min-width="170" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)" effect="plain">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="claimed_by" label="执行席" width="140" />
        <el-table-column prop="created" label="派发" width="110" />
      </el-table>
      <el-table :data="line.mats || []" size="small">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column prop="task_key" label="素材单" min-width="220" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)" effect="plain">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="claimed_by" label="执行席" width="140" />
        <el-table-column prop="created" label="派发" width="110" />
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { paperApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'

const line = ref<any>({})
const s = computed(() => line.value.stats || {})

const statusType = (v: string) =>
  v === 'succeeded' ? 'success'
    : (['queued', 'claimed', 'running'].includes(v) ? 'warning' : 'danger')

const topicType = (v: string) =>
  (v.includes('pass') || v.includes('accepted')) ? 'success'
    : (v.includes('reject') || v.includes('fail')) ? 'danger'
      : (v.includes('candidate') || v.includes('ready') || v.includes('open')) ? 'info' : 'warning'

async function load() {
  try {
    line.value = (await paperApi.line()).data
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '论文生产线加载失败')
  }
}
onMounted(load)
</script>

<style scoped>
.pp-stats { display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }
.pp-stat { display: flex; flex-direction: column; gap: 2px; }
.pp-stat b { font-size: 24px; }
.pp-stat span { font-size: 12px; opacity: 0.65; }
.pp-stat.ok b { color: #67c23a; }
.pp-stat.warn b { color: #e6a23c; }
.pp-stat.bad b { color: #f56c6c; }
.pp-meta { margin-left: auto; font-size: 12px; opacity: 0.55; }
.pp-h { margin: 0 0 10px; font-size: 15px; }
.pp-dim { opacity: 0.45; font-size: 12px; }
</style>
