<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>
        谱系追溯 <span class="tf-hero-mint">· Provenance Chain</span>
      </template>
      <template #sub>
        给定 mission_id，渲染 派发→worktree→分支→PR→报告→复现 全链路；
        节点亮色=数据齐全，灰色=断点。
      </template>
      <template #extra>
        <div class="tf-prov-input">
          <el-input v-model="inputId" placeholder="输入 mission_id" style="width: 160px"
            size="small" @keyup.enter="loadProvenance" />
          <el-button size="small" type="primary" @click="loadProvenance" :loading="loading">
            追溯
          </el-button>
          <span v-if="provenance" class="tf-prov-score">
            链路完整度: <strong>{{ provenance.chain_score }}</strong> / {{ provenance.chain_max }}
          </span>
        </div>
      </template>
    </HeroPanel>

    <div v-if="error" class="tf-card tf-prov-error">
      <el-alert :title="error" type="error" show-icon :closable="false" />
    </div>

    <div v-if="provenance" class="tf-prov-chain">
      <!-- Visual chain -->
      <div class="tf-prov-flow">
        <div v-for="(node, idx) in chainNodes" :key="node.key"
          class="tf-prov-node" :class="{ 'is-present': node.present, 'is-absent': !node.present }">
          <div class="tf-prov-node-icon">
            <span v-if="node.present" class="tf-prov-check">✓</span>
            <span v-else class="tf-prov-miss">✗</span>
          </div>
          <div class="tf-prov-node-label">{{ node.label }}</div>
          <div class="tf-prov-node-sub">{{ node.subtitle }}</div>
          <div v-if="idx < chainNodes.length - 1" class="tf-prov-arrow">→</div>
        </div>
      </div>

      <!-- Detail cards -->
      <el-row :gutter="16" style="margin-top: 20px">
        <el-col :span="12">
          <!-- Dispatch -->
          <div class="tf-card" :class="{ 'tf-prov-card-ok': nodes.dispatch.present }">
            <div class="tf-card-title">
              <span class="tf-prov-dot" :class="{ 'is-ok': nodes.dispatch.present }" />
              ① 派发 Dispatch
            </div>
            <div v-if="nodes.dispatch.present" class="tf-prov-detail">
              <div class="tf-prov-row"><span>Mission</span>
                <router-link class="tf-mono tf-link" :to="`/missions/${missionId}`">
                  #{{ missionId }}
                </router-link>
              </div>
              <div class="tf-prov-row"><span>仓库</span>
                <span class="tf-mono">{{ nodes.dispatch.data.repo }}</span>
              </div>
              <div class="tf-prov-row"><span>基线</span>
                <span class="tf-mono">{{ nodes.dispatch.data.base_ref }}</span>
              </div>
              <div class="tf-prov-row"><span>Runner</span>
                <span class="tf-mono">{{ nodes.dispatch.data.runner }}</span>
              </div>
              <div class="tf-prov-row"><span>状态</span>
                <StatusBadge :status="nodes.dispatch.data.status" />
              </div>
              <div class="tf-prov-row"><span>创建</span>
                <span class="tf-mono">{{ fmtTime(nodes.dispatch.data.created_at) }}</span>
              </div>
              <div class="tf-prov-goal">{{ nodes.dispatch.data.goal }}</div>
            </div>
            <div v-else class="tf-prov-empty">无派发数据</div>
          </div>

          <!-- Branch -->
          <div class="tf-card" :class="{ 'tf-prov-card-ok': nodes.branch.present }">
            <div class="tf-card-title">
              <span class="tf-prov-dot" :class="{ 'is-ok': nodes.branch.present }" />
              ③ 分支 Branch
            </div>
            <div v-if="nodes.branch.present" class="tf-prov-detail">
              <div class="tf-prov-row"><span>分支名</span>
                <span class="tf-mono">{{ nodes.branch.data.name }}</span>
              </div>
              <div class="tf-prov-row"><span>HEAD</span>
                <span class="tf-mono">{{ nodes.branch.data.head_commit?.slice(0, 10) || '—' }}</span>
              </div>
              <div class="tf-prov-row"><span>仓库</span>
                <span class="tf-mono">{{ nodes.branch.data.repo }}</span>
              </div>
            </div>
            <div v-else class="tf-prov-empty">无分支数据</div>
          </div>

          <!-- Reports -->
          <div class="tf-card" :class="{ 'tf-prov-card-ok': nodes.reports.present }">
            <div class="tf-card-title">
              <span class="tf-prov-dot" :class="{ 'is-ok': nodes.reports.present }" />
              ⑤ 报告 Reports
              <span v-if="nodes.reports.present" class="tf-dim">
                ({{ nodes.reports.data.length }} 篇)
              </span>
            </div>
            <div v-if="nodes.reports.present" class="tf-prov-detail">
              <div v-for="r in nodes.reports.data" :key="r.id" class="tf-prov-report">
                <div class="tf-prov-row">
                  <span>报告</span>
                  <router-link class="tf-mono tf-link" :to="`/reports`">
                    #{{ r.id }} {{ r.title }}
                  </router-link>
                </div>
                <div class="tf-prov-row"><span>场景</span>
                  <span class="tf-mono">{{ r.scenario_key || '—' }}</span>
                </div>
                <div class="tf-prov-row"><span>状态</span>
                  <StatusBadge :status="r.status" />
                </div>
              </div>
            </div>
            <div v-else class="tf-prov-empty">无关联报告</div>
          </div>
        </el-col>

        <el-col :span="12">
          <!-- Worktree -->
          <div class="tf-card" :class="{ 'tf-prov-card-ok': nodes.worktree.present }">
            <div class="tf-card-title">
              <span class="tf-prov-dot" :class="{ 'is-ok': nodes.worktree.present }" />
              ② Worktree
            </div>
            <div v-if="nodes.worktree.present" class="tf-prov-detail">
              <div v-if="nodes.worktree.data.path" class="tf-prov-row"><span>路径</span>
                <span class="tf-mono">{{ nodes.worktree.data.path }}</span>
              </div>
              <div v-if="nodes.worktree.data.session_id" class="tf-prov-row"><span>会话</span>
                <router-link class="tf-mono tf-link"
                  :to="`/sessions/${nodes.worktree.data.session_id}`">
                  #{{ nodes.worktree.data.session_id }}
                </router-link>
              </div>
            </div>
            <div v-else class="tf-prov-empty">无 worktree 数据</div>
          </div>

          <!-- PR -->
          <div class="tf-card" :class="{ 'tf-prov-card-ok': nodes.pr.present }">
            <div class="tf-card-title">
              <span class="tf-prov-dot" :class="{ 'is-ok': nodes.pr.present }" />
              ④ Pull Request
            </div>
            <div v-if="nodes.pr.present" class="tf-prov-detail">
              <div v-if="nodes.pr.data.url" class="tf-prov-row"><span>URL</span>
                <a :href="nodes.pr.data.url" target="_blank" class="tf-mono tf-link">
                  {{ nodes.pr.data.url }}
                </a>
              </div>
              <div v-if="nodes.pr.data.number" class="tf-prov-row"><span>PR #</span>
                <span class="tf-mono">{{ nodes.pr.data.number }}</span>
              </div>
              <div v-if="Object.keys(nodes.pr.data.checks || {}).length" class="tf-prov-row">
                <span>Checks</span>
                <span class="tf-mono">{{ JSON.stringify(nodes.pr.data.checks) }}</span>
              </div>
            </div>
            <div v-else class="tf-prov-empty">无 PR 数据（可能未推送或无 CI）</div>
          </div>

          <!-- Runs -->
          <div class="tf-card" :class="{ 'tf-prov-card-ok': nodes.runs.present }">
            <div class="tf-card-title">
              <span class="tf-prov-dot" :class="{ 'is-ok': nodes.runs.present }" />
              ⑥ 复现 Runs
              <span v-if="nodes.runs.present" class="tf-dim">
                ({{ nodes.runs.data.length }} 个)
              </span>
            </div>
            <div v-if="nodes.runs.present" class="tf-prov-detail">
              <div v-for="r in nodes.runs.data" :key="r.run_id" class="tf-prov-row">
                <span>Run</span>
                <router-link class="tf-mono tf-link" :to="`/runs/${encodeURIComponent(r.run_id)}`">
                  {{ r.run_id }}
                </router-link>
              </div>
            </div>
            <div v-else class="tf-prov-empty">无复现 Run</div>
          </div>

          <!-- Approval -->
          <div v-if="nodes.approval.present" class="tf-card tf-prov-card-ok">
            <div class="tf-card-title">
              <span class="tf-prov-dot is-ok" />
              验收 Approval
            </div>
            <div class="tf-prov-detail">
              <div class="tf-prov-row"><span>状态</span>
                <StatusBadge :status="nodes.approval.data.status" />
              </div>
              <div v-if="nodes.approval.data.decided_by" class="tf-prov-row"><span>裁定</span>
                <span class="tf-mono">{{ nodes.approval.data.decided_by }}</span>
              </div>
              <div v-if="nodes.approval.data.decision_note" class="tf-prov-row"><span>意见</span>
                <span>{{ nodes.approval.data.decision_note }}</span>
              </div>
            </div>
          </div>

          <!-- Children -->
          <div v-if="nodes.children.present" class="tf-card">
            <div class="tf-card-title">
              子任务 Children
              <span class="tf-dim">({{ nodes.children.data.length }} 个)</span>
            </div>
            <div class="tf-prov-detail">
              <div v-for="c in nodes.children.data" :key="c.id" class="tf-prov-row">
                <span>子任务</span>
                <router-link class="tf-mono tf-link" :to="`/missions/${c.id}`">
                  #{{ c.id }}
                </router-link>
                <StatusBadge :status="c.status" />
              </div>
            </div>
          </div>
        </el-col>
      </el-row>
    </div>

    <div v-if="!provenance && !loading && !error" class="tf-card tf-prov-empty-big">
      <p>输入 mission_id 开始追溯全链路。</p>
      <p class="tf-dim">链路节点：派发 → Worktree → 分支 → PR → 报告 → 复现</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { missionApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const route = useRoute()
const inputId = ref('')
const missionId = ref<number | null>(null)
const provenance = ref<any>(null)
const loading = ref(false)
const error = ref('')

const nodes = computed(() => provenance.value?.nodes || {
  dispatch: { present: false, data: null },
  worktree: { present: false, data: null },
  branch: { present: false, data: null },
  pr: { present: false, data: null },
  reports: { present: false, data: [] },
  runs: { present: false, data: [] },
  approval: { present: false, data: null },
  children: { present: false, data: [] },
})

const chainNodes = computed(() => {
  const n = nodes.value
  return [
    { key: 'dispatch', label: '派发', subtitle: `Mission #${missionId.value}`, present: n.dispatch.present },
    { key: 'worktree', label: 'Worktree', subtitle: n.worktree.data?.session_id ? `会话 #${n.worktree.data.session_id}` : '—', present: n.worktree.present },
    { key: 'branch', label: '分支', subtitle: n.branch.data?.name || '—', present: n.branch.present },
    { key: 'pr', label: 'PR', subtitle: n.pr.data?.number ? `#${n.pr.data.number}` : '—', present: n.pr.present },
    { key: 'reports', label: '报告', subtitle: `${n.reports.data?.length || 0} 篇`, present: n.reports.present },
    { key: 'runs', label: '复现', subtitle: `${n.runs.data?.length || 0} 个`, present: n.runs.present },
  ]
})

function fmtTime(t?: string) {
  return t ? t.replace('T', ' ').replace(/\.\d+.*$/, '') : '—'
}

async function loadProvenance() {
  const id = Number(inputId.value)
  if (!id || isNaN(id)) {
    ElMessage.warning('请输入有效的 mission_id')
    return
  }
  loading.value = true
  error.value = ''
  try {
    provenance.value = await missionApi.provenance(id)
    missionId.value = id
  } catch (e: any) {
    error.value = e.message || '加载失败'
    provenance.value = null
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  const id = route.params.id
  if (id) {
    inputId.value = String(id)
    loadProvenance()
  }
})
</script>

<style scoped lang="scss">
.tf-prov-input {
  display: flex; align-items: center; gap: 12px;
}
.tf-prov-score {
  font-size: 14px;
  color: var(--tf-ink-700);
  strong { color: var(--tf-mint-600); font-size: 18px; }
}
.tf-prov-error { margin-bottom: 16px; }

.tf-prov-flow {
  display: flex; align-items: center; justify-content: center;
  gap: 0; padding: 20px 0; flex-wrap: wrap;
}
.tf-prov-node {
  display: flex; flex-direction: column; align-items: center;
  padding: 12px 16px; min-width: 100px; position: relative;
  border-radius: 8px; transition: all 0.2s;
  &.is-present { background: rgba(47, 174, 139, 0.08); }
  &.is-absent { background: rgba(165, 174, 184, 0.08); opacity: 0.7; }
}
.tf-prov-node-icon {
  width: 32px; height: 32px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; font-weight: bold; margin-bottom: 6px;
}
.tf-prov-check { color: #2fae8b; }
.tf-prov-miss { color: #a5aeb8; }
.tf-prov-node-label {
  font-size: 13px; font-weight: 600; color: var(--tf-ink-900);
}
.tf-prov-node-sub {
  font-size: 11px; color: var(--tf-ink-500); margin-top: 2px;
  font-family: 'JetBrains Mono', monospace;
}
.tf-prov-arrow {
  font-size: 20px; color: var(--tf-mint-400); margin: 0 4px;
  align-self: center;
}

.tf-prov-card-ok {
  border-left: 3px solid #2fae8b;
}
.tf-prov-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: #a5aeb8; margin-right: 8px;
  &.is-ok { background: #2fae8b; }
}
.tf-prov-detail {
  display: flex; flex-direction: column; gap: 6px;
}
.tf-prov-row {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12.5px;
  span:first-child { color: var(--tf-ink-500); }
}
.tf-prov-goal {
  margin-top: 8px; padding: 8px; background: rgba(0,0,0,0.02);
  border-radius: 4px; font-size: 12px; color: var(--tf-ink-700);
  line-height: 1.5;
}
.tf-prov-report {
  border-top: 1px solid var(--tf-line-200); padding-top: 8px;
  &:first-of-type { border-top: none; padding-top: 0; }
}
.tf-prov-empty {
  color: #a5aeb8; font-size: 13px; font-style: italic;
}
.tf-prov-empty-big {
  text-align: center; padding: 40px;
  p { margin: 8px 0; }
}
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.tf-dim { color: var(--tf-ink-500); font-size: 12px; }
</style>
