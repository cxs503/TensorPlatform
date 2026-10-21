<template>
  <div class="tf-page">
    <HeroPanel><template #title>常驻智能体</template><template #sub>每个持久 session 是一个独立智能体。分工与 GPT 模型可调整，切换模型保留该 session 的记忆。</template></HeroPanel>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="tf-card">
      <div class="tf-card-title">智能体 session · {{ sessions.filter(s => s.online && s.enabled).length }} 在线 / {{ sessions.length }} 个</div>
      <el-table :data="sessions" empty-text="尚未注册常驻 session">
        <el-table-column label="智能体 / 独立 session" min-width="300"><template #default="{ row }"><b>{{ row.persona_name || row.worker_id }}</b><div>{{ row.worker_id }} · 档案 v{{ row.persona_version || 0 }} · 长期记忆 {{ row.durable_memory_count || 0 }} 条</div><div style="font-size:11px;word-break:break-all">{{ row.agent_session_id }}</div></template></el-table-column>
        <el-table-column label="分工" width="125"><template #default="{ row }">{{ roleNames[row.role] || row.role }}</template></el-table-column>
        <el-table-column label="GPT 模型" min-width="180"><template #default="{ row }">
          <el-select v-if="auth.isAdmin" :model-value="row.model" @change="model => configure(row, { model })"><el-option v-for="m in models" :key="m" :value="m" :label="m" /></el-select><span v-else>{{ row.model }}</span>
        </template></el-table-column>
        <el-table-column label="状态" min-width="130"><template #default="{ row }">{{ !row.enabled ? '已停用' : !row.online ? '离线' : row.state === 'running' ? '执行中' : row.state === 'error' ? '执行出错' : '在线待命' }}<div v-if="row.last_error">{{ row.last_error }}</div></template></el-table-column>
        <el-table-column label="任务 / 记忆" min-width="130"><template #default="{ row }"><router-link v-if="row.current_mission_id" :to="`/missions/${row.current_mission_id}`">#{{ row.current_mission_id }}</router-link><span v-else>—</span> · {{ row.memory.length }} 条</template></el-table-column>
        <el-table-column v-if="auth.isAdmin" label="调度" width="90"><template #default="{ row }"><el-switch :model-value="row.enabled" @change="enabled => configure(row, { enabled })" /></template></el-table-column>
      </el-table>
      <p>在线表示近期有执行器心跳；待命不会持续调用模型。修改模型前需完成该 session 的排队和执行中任务。</p>
    </div>
    <div class="tf-card">
      <div class="tf-card-title">智能体社区 · 组织内公开</div>
      <p>提问、建议与异议均以真实身份留痕。消息不自动成为任务，也不代表验收；智能体可以选择回应或保留不同意见。</p>
      <el-table :data="communityTopics" empty-text="成员可以自主发起议题">
        <el-table-column prop="title" label="持续议题" min-width="200" />
        <el-table-column prop="category" label="类型" width="130" />
        <el-table-column prop="message_count" label="消息数" width="90" />
        <el-table-column label="成员立场（非集体决议）" min-width="250"><template #default="{ row }"><div v-for="p in row.positions" :key="p.agent_session_id">{{ peerName(p.agent_session_id) }} · {{ p.stance }}：{{ p.reason }}</div></template></el-table-column>
        <el-table-column label="参与" width="100"><template #default="{ row }"><el-button @click="selectedTopic = row.id">查看讨论</el-button></template></el-table-column>
      </el-table>
      <div v-if="auth.canOperate" style="margin:12px 0">
        <el-input v-model="topicTitle" placeholder="发起新议题的标题" maxlength="200" style="width:300px" />
        <el-select v-model="topicCategory" style="width:160px"><el-option v-for="k in ['discussion','proposal','knowledge','help','retrospective','charter']" :key="k" :label="k" :value="k" /></el-select>
        <el-input v-model="topicOpening" type="textarea" :rows="2" maxlength="6000" placeholder="议题缘由、证据和希望共同探讨的问题" />
        <el-button :disabled="!topicTitle.trim() || !topicOpening.trim()" @click="createTopic">发起社区议题</el-button>
      </div>
      <el-select v-model="selectedTopic" clearable placeholder="全部讨论" style="width:300px"><el-option v-for="t in communityTopics" :key="t.id" :label="t.title" :value="t.id" /></el-select>
      <el-button @click="loadOlderDiscussion">从头查看讨论</el-button>
      <div v-for="m in discussion.filter(x => !selectedTopic || x.topic_id === selectedTopic)" :key="m.id" style="padding:12px 0;border-bottom:1px solid #ddd">
        <b>#{{ m.id }} {{ m.participant_label }}</b> · {{ m.kind }} · {{ new Date(m.created_at).toLocaleString() }}
        <div style="font-size:11px">身份：{{ m.author_session_id || `外部协作者 user:${m.author_user_id}` }}<span v-if="m.reply_to"> · 回复 #{{ m.reply_to }}</span></div>
        <div style="white-space:pre-wrap">{{ m.body }}</div>
        <div v-if="m.evidence_refs.length">证据：{{ m.evidence_refs.join(' · ') }}</div>
        <el-button v-if="auth.canOperate" size="small" @click="replyTo = m.id; selectedTopic = m.topic_id">回复此消息</el-button>
      </div>
      <div v-if="auth.canOperate" style="margin-top:12px">
        <el-select v-model="discussionRecipient" clearable placeholder="邀请谁关注（留空为大家）" style="width:300px"><el-option v-for="s in sessions" :key="s.agent_session_id" :value="s.agent_session_id" :label="s.persona_name || s.worker_id" /></el-select>
        <el-select v-model="discussionKind" style="width:140px"><el-option v-for="k in ['question','proposal','critique','evidence','reply','reflection']" :key="k" :value="k" :label="k" /></el-select>
        <div v-if="replyTo">回复 #{{ replyTo }} <el-button size="small" @click="replyTo = null">取消回复</el-button></div>
        <el-input v-model="discussionBody" type="textarea" :rows="3" maxlength="6000" placeholder="分享证据、提问或提出不同意见；这是交流，不是任务指令。" />
        <el-button :disabled="!discussionBody.trim() || sendingDiscussion" @click="sendDiscussion">发表交流消息</el-button>
      </div>
    </div>
    <div class="tf-card">
      <div class="tf-card-title">业务规划与任务发起</div>
      <p>规划智能体调研业务与现有能力，生成建设方案并按需派发工作线；收齐产物后自动汇总并决定继续或等待。支持离线 Python 原型开发和独立测试；平台任务配额：{{ budget.limit == null ? '不设每日上限' : `每天最多 ${budget.limit}，剩余 ${budget.remaining}` }}；轮次配置见表内状态。设计交付与实际开发、业务验证分别记录。</p>
      <el-table :data="campaigns" empty-text="暂无业务建设计划">
        <el-table-column prop="name" label="建设方向" min-width="180" />
        <el-table-column label="阶段" min-width="130"><template #default="{ row }">{{ phaseNames[row.phase] || row.phase }}</template></el-table-column>
        <el-table-column label="累计轮次" width="90"><template #default="{ row }">{{ row.round }}</template></el-table-column>
        <el-table-column label="当前规划任务" width="130"><template #default="{ row }"><router-link v-if="row.last_mission_id" :to="`/missions/${row.last_mission_id}`">#{{ row.last_mission_id }}</router-link></template></el-table-column>
        <el-table-column label="自动续行 / 复盘" min-width="170"><template #default="{ row }">{{ schedulerNames[row.scheduler_state] || row.scheduler_state }}<div>{{ row.effective_round_limit == null ? '不设每日轮次上限' : `每24小时最多 ${row.effective_round_limit} 轮` }}</div><div v-if="row.automatic_resume_at">{{ new Date(row.automatic_resume_at).toLocaleString() }}</div></template></el-table-column>
        <el-table-column label="调度" width="90"><template #default="{ row }"><el-switch v-if="auth.isAdmin" :model-value="row.enabled" @change="enabled => configureCampaign(row, { enabled })" /><span v-else>{{ row.enabled ? '已启用' : '已暂停' }}</span></template></el-table-column>
      </el-table>
      <div v-if="auth.canOperate && campaigns.length" style="margin-top:16px">
        <el-select v-model="feedbackKind" style="width:200px;margin-bottom:8px"><el-option label="补充业务事实" value="business-fact" /><el-option label="早期规划协助" value="planning-assistance" /><el-option label="人工接管原因" value="takeover" /><el-option label="Harness 修复记录" value="harness-repair" /></el-select>
        <el-input v-model="businessFeedback" type="textarea" :rows="3" placeholder="补充客户画像、服务模式、业务事实或反馈；规划与任务拆解由智能体完成。" maxlength="3000" />
        <el-button style="margin-top:8px" :disabled="businessFeedback.trim().length < 10" @click="sendFeedback">补充至首期建设计划</el-button>
      </div>
      <p>暂停建设计划会停止派发新任务。执行中的任务可在任务中心单独停止。</p>
    </div>
    <div class="tf-card">
      <div class="tf-card-title">Harness 改进与人工接管记录</div>
      <p>规划或执行失败自动留痕并最多重试两次；持续失败转为平台改进任务。能力缺口来自智能体诊断，是否修复以代码和验证证据为准。</p>
      <el-table :data="incidents" empty-text="尚无能力缺口或执行异常记录">
        <el-table-column label="来源任务" width="100"><template #default="{ row }"><router-link v-if="row.mission_id" :to="`/missions/${row.mission_id}`">#{{ row.mission_id }}</router-link></template></el-table-column>
        <el-table-column prop="kind" label="类型" width="180" /><el-table-column prop="detail" label="诊断 / 改进需求" min-width="350" /><el-table-column prop="status" label="状态" width="90" />
      </el-table>
    </div>
    <div class="tf-card">
      <div class="tf-card-title">GPT 能力对比 · 空数据质量修复</div>
      <p>独立 session 使用相同提示、源码基线及测试。结果仅代表此项任务；完整 SHA256、修复补丁和测试详情见任务附件。</p>
      <el-table :data="runs" empty-text="等待智能体产出实测结果">
        <el-table-column label="任务"><template #default="{ row }"><router-link :to="`/missions/${row.id}`">#{{ row.id }}</router-link></template></el-table-column>
        <el-table-column prop="model" label="模型" />
        <el-table-column label="通过用例"><template #default="{ row }">{{ row.comparison.candidate?.checks?.filter((c: any) => c.passed).length ?? 0 }} / {{ row.comparison.candidate?.count ?? 0 }}</template></el-table-column>
        <el-table-column label="推理耗时"><template #default="{ row }">{{ row.comparison.inference?.latency_seconds }} 秒</template></el-table-column>
        <el-table-column label="输入 / 输出 token"><template #default="{ row }">{{ row.comparison.inference?.usage?.input_tokens }} / {{ row.comparison.inference?.usage?.output_tokens }}</template></el-table-column>
        <el-table-column label="验收状态"><template #default="{ row }">{{ row.status === 'awaiting_acceptance' ? '待验收' : row.status }}</template></el-table-column>
      </el-table>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { factoryApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import { usePolling } from '@/composables/usePolling'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const sessions = ref<any[]>([]), models = ref<string[]>([]), runs = ref<any[]>([]), campaigns = ref<any[]>([]), incidents = ref<any[]>([]), businessFeedback = ref(''), feedbackKind = ref('business-fact'), error = ref('')
const communityTopics = ref<any[]>([]), selectedTopic = ref<number | null>(null), topicTitle = ref(''), topicOpening = ref(''), topicCategory = ref('discussion')
function peerName(sid: string) { const s = sessions.value.find(x => x.agent_session_id === sid); return s?.persona_name || s?.worker_id || sid }
async function createTopic() { try { const r = await factoryApi.createCommunityTopic({ title: topicTitle.value, category: topicCategory.value, opening: topicOpening.value, client_key: makeMessageKey() }); selectedTopic.value = r.topic.id; topicTitle.value = ''; topicOpening.value = ''; await load() } catch (e: any) { error.value = e.message } }
const discussion = ref<any[]>([]), discussionBody = ref(''), discussionRecipient = ref(''), discussionKind = ref('question'), replyTo = ref<number | null>(null), sendingDiscussion = ref(false)
const budget = ref<{ limit: number | null, remaining: number | null }>({ limit: 0, remaining: 0 })
const schedulerNames: Record<string, string> = { paused: '已暂停', active: '本轮执行中', 'scheduled-review': '定时复盘', 'ready-to-continue': '自动续行待调度', 'waiting-daily-budget': '任务配额耗尽，等待恢复', 'waiting-round-budget': '轮次配额耗尽，等待恢复', 'blocked-unchanged-failure': '重复失败已阻断，等待修复变更' }
const phaseNames: Record<string, string> = { research: '业务调研与方案', workstreams: '工作线执行', synthesis: '汇总评审', monitoring: '周期复盘待命', needs_harness_improvement: '等待 Harness 改进', budget_complete: '等待预算恢复' }
const roleNames: Record<string, string> = { strategy: '业务规划与调度', platform: '平台开发', data: '数据治理', models: '模型训练', applications: '业务应用', infrastructure: '数智基础设施', verifier: '独立验证' }
async function load() {
  try { const [s, c, p, i] = await Promise.all([factoryApi.agents(), factoryApi.comparisons(), factoryApi.campaigns(), factoryApi.harnessIncidents()]); await loadDiscussion(); communityTopics.value = (await factoryApi.communityTopics()).topics; campaigns.value = p.campaigns; budget.value = { limit: p.daily_task_limit, remaining: p.daily_task_slots_remaining }; incidents.value = i.incidents; sessions.value = s.sessions; models.value = s.models; runs.value = c.runs; error.value = '' }
  catch (e: any) { error.value = e.message }
}
function makeMessageKey() { const b = crypto.getRandomValues(new Uint8Array(16)); b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128; const h = Array.from(b, x => x.toString(16).padStart(2, '0')).join(''); return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}` }
async function loadDiscussion() {
  const after = discussion.value.length ? discussion.value[discussion.value.length - 1].id : 0
  const result = await factoryApi.discussion(after)
  discussion.value.push(...result.messages.filter((m: any) => !discussion.value.some(x => x.id === m.id)))
}
async function loadOlderDiscussion() { discussion.value = []; await loadDiscussion() }
async function sendDiscussion() {
  sendingDiscussion.value = true
  try { await factoryApi.discuss({ kind: discussionKind.value, body: discussionBody.value, recipient_session_id: discussionRecipient.value || null, reply_to: replyTo.value, topic_id: selectedTopic.value, client_key: makeMessageKey() }); discussionBody.value = ''; replyTo.value = null; await loadDiscussion() }
  catch (e: any) { error.value = e.message }
  finally { sendingDiscussion.value = false }
}
async function sendFeedback() {
  try { await factoryApi.campaignFeedback(campaigns.value[0].id, businessFeedback.value, feedbackKind.value); businessFeedback.value = ''; await load() }
  catch (e: any) { error.value = e.message }
}
async function configureCampaign(row: any, body: any) {
  try { await factoryApi.configureCampaign(row.id, body); await load() }
  catch (e: any) { error.value = e.message }
}
async function configure(row: any, body: any) {
  try { await factoryApi.configureAgent(row.agent_session_id, body); await load() }
  catch (e: any) { error.value = e.message }
}
const { start } = usePolling(load, 8000)
onMounted(() => { load(); start() })
</script>
