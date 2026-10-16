<template>
  <div class="ai-agent">
    <div class="page-header">
      <div style="display:flex;align-items:center;gap:12px">
        <div><h2>🤖 AI 智能体</h2><p>智谱 GLM-4-Flash 驱动 · 平台数据问答 · 分析助手</p></div>
      </div>
      <div style="display:flex;gap:8px"><el-select v-model="template" size="small" style="width:180px"><el-option v-for="item in templates" :key="item.key" :label="item.name" :value="item.key" /></el-select><el-button size="small" @click="clearHistory" :disabled="messages.length<=1">🗑️ 清除历史</el-button></div>
    </div>

    <div class="agent-layout">
      <!-- 对话区 -->
      <div class="chat-panel">
        <div class="messages" ref="msgBox">
          <div v-if="messages.length === 0" class="welcome">
            <div class="welcome-icon">🤖</div>
            <h3>开放数字化底座 AI 智能体</h3>
            <p>可尝试提问：</p>
            <div class="quick-actions">
              <button v-for="q in quickQuestions" :key="q" @click="send(q)" class="quick-btn">{{ q }}</button>
            </div>
          </div>

          <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
            <div class="msg-avatar">{{ m.role === 'user' ? '👤' : '🤖' }}</div>
            <div class="msg-content">
              <div v-html="renderMsg(m.content)"></div>
              <div v-if="m.toolTrace?.length" class="evidence">证据工具：<span v-for="item in m.toolTrace" :key="item.tool">{{ item.tool }}（{{ item.access }}）</span> · 仅人工评审，不下发控制</div>
              <div v-if="m.runtime" class="runtime">{{ m.runtime }}</div>
              <div v-if="m.charts && m.charts.length" class="msg-charts">
                <div v-for="(chart, ci) in m.charts" :key="ci" :ref="el => setChartRef(m, ci, el)" class="chart-box" :style="{ height: (chart.height || 300) + 'px' }"></div>
              </div>
            </div>
          </div>

          <div v-if="loading" class="msg assistant">
            <div class="msg-avatar">🤖</div>
            <div class="msg-content typing">
              {{ progressStage }} · {{ elapsedSeconds }}秒<span class="dots">...</span>
              <button class="inline-stop" @click="stopGeneration">停止生成</button>
            </div>
          </div>
        </div>

        <div class="input-area">
          <input
            v-model="input"
            @keyup.enter="send(input)"
            placeholder="输入问题：查询数据集、画曲线图、分析模型..."
            class="chat-input"
            :disabled="loading"
          />
          <button v-if="!loading" @click="send(input)" :disabled="!input.trim()" class="send-btn">发送</button>
          <button v-else @click="stopGeneration" class="stop-btn">停止</button>
        </div>
      </div>

      <!-- 能力侧边栏 -->
      <div class="tools-panel">
        <h4>可用能力</h4>
        <div class="tool-list">
          <div v-for="t in tools" :key="t.name" class="tool-card" @click="send(t.prompt)">
            <div class="tool-icon">{{ t.icon }}</div>
            <div class="tool-info">
              <div class="tool-name">{{ t.name }}</div>
              <div class="tool-desc">{{ t.desc }}</div>
            </div>
          </div>
        </div>

        <h4 style="margin-top: 20px;">知识领域</h4>
        <div class="domain-tags">
          <span v-for="d in domains" :key="d" class="tag" @click="send(`介绍一下${d}领域的常用方法`)">{{ d }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { post } from '@/api/legacy'
import * as echarts from 'echarts'

interface Message {
  role: string
  content: string
  charts?: any[]
  toolTrace?: Array<{ tool: string; access: string }>
  runtime?: string
}

const sessionId = ref('session_'+Date.now());
const sessions = ref<any[]>([]);
const messages = ref<Message[]>([])
const input = ref('')
const loading = ref(false)
const elapsedSeconds = ref(0)
const progressStage = ref('正在理解问题')
let activeController: AbortController | null = null
let elapsedTimer: ReturnType<typeof setInterval> | null = null
let requestTimeout: ReturnType<typeof setTimeout> | null = null
let stoppedByUser = false
let stoppedByTimeout = false
const template = ref('platform_overview')
const templates = [
  { key: 'platform_overview', name: '平台运营日报' },
  { key: 'data_analysis', name: '数据分析助手' },
  { key: 'mlops_review', name: '模型运维助手' },
]
const msgBox = ref<HTMLElement>()
const chartInstances: Map<string, echarts.ECharts> = new Map()

const quickQuestions = [
  '平台有多少数据集和模型？',
  '列出平台所有数据集',
  '当前系统整体运行状况如何？',
  '列出所有模型和训练任务',
]

const tools = [
  { name: '数据中台', icon: '📊', desc: '数据集/目录/血缘/质量', prompt: '查询平台所有数据集，并画一个柱状图' },
  { name: '模型工厂', icon: '🤖', desc: '模型/训练/部署/实验', prompt: '列出所有模型和训练任务' },
  { name: '平台统计', icon: '📈', desc: '数据集/模型/训练任务总览', prompt: '平台有多少数据集和模型' },
]

const domains = ['数据治理', '特征工程', '模型评估', '工作流编排', '数据质量']

function parseCharts(content: string): { text: string; charts: any[] } {
  const charts: any[] = []
  // 匹配 ```chart, ```json, ```javascript 代码块
  const blockRegex = /```(chart|json|javascript)\s*\n([\s\S]*?)\n```/g
  let text = content
  let match: RegExpExecArray | null

  while ((match = blockRegex.exec(content)) !== null) {
    const lang = match[1]
    const code = match[2].trim()

    try {
      const cfg = JSON.parse(code)
      // 确认是 ECharts 配置 (有 series 或 xAxis)
      if (cfg.series || cfg.xAxis || cfg.yAxis) {
        cfg.height = cfg.height || 300
        charts.push(cfg)
        // 从 text 中移除已解析的 block
        text = text.replace(match[0], '')
      }
    } catch (e) {
      // 不是有效的 JSON，跳过
    }
  }

  return { text: text.trim(), charts }
}

function renderMsg(content: string): string {
  // First strip chart blocks for HTML rendering
  const { text } = parseCharts(content)
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function setChartRef(msg: Message, ci: number, el: any) {
  if (!el) return
  const key = `${msg.role}_${ci}_${msg.content.substring(0, 20)}`

  // 延迟初始化确保元素有尺寸
  setTimeout(() => {
    if (chartInstances.has(key)) {
      // 已有实例则 resize（修复初次 0 宽度问题）
      chartInstances.get(key)!.resize()
      return
    }
    const chart = echarts.init(el)
    chart.setOption(msg.charts![ci])
    chartInstances.set(key, chart)
    // 二次 resize 确保正确
    setTimeout(() => chart.resize(), 50)
  }, 100)
}

async function send(text: string) {
  const q = text.trim()
  if (!q || loading.value) return

  input.value = ''
  messages.value.push({ role: 'user', content: q })
  loading.value = true
  elapsedSeconds.value = 0
  progressStage.value = '正在理解问题'
  stoppedByUser = false
  stoppedByTimeout = false
  activeController = new AbortController()
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value += 1
    if (elapsedSeconds.value < 4) progressStage.value = '正在理解问题'
    else if (elapsedSeconds.value < 12) progressStage.value = '正在生成回答'
    else progressStage.value = '模型响应较慢，仍在处理中'
  }, 1000)
  requestTimeout = setTimeout(() => {
    stoppedByTimeout = true
    activeController?.abort()
  }, 50000)
  await scrollDown()

  try {
    const res = await post<any>('/agent/chat', {
      messages: messages.value.map(m => ({ role: m.role, content: m.content })),
      template: template.value,
    }, { signal: activeController.signal })
    const reply = res?.reply || res?.content || '抱歉，暂时无法回答。请稍后再试。'
    const { charts } = parseCharts(reply)
    const runtime = res?.elapsed_ms != null
      ? `${res?.mode === 'tool' ? '平台工具问答' : '通用知识问答'} · ${(Number(res.elapsed_ms) / 1000).toFixed(1)}秒`
      : undefined
    messages.value.push({ role: 'assistant', content: reply, charts: charts.length ? charts : undefined, toolTrace: res?.tool_trace || [], runtime })
    await nextTick()
    // Force chart rendering after DOM update
    await nextTick()
  } catch (e: any) {
    if (stoppedByTimeout) {
      messages.value.push({ role: 'assistant', content: '⚠️ 本次回答超过50秒，已停止等待。请缩短问题或稍后重试。' })
    } else if (stoppedByUser || e?.name === 'AbortError') {
      messages.value.push({ role: 'assistant', content: '⏹️ 已停止本次生成。' })
    } else {
      messages.value.push({ role: 'assistant', content: `⚠️ AI智能体请求失败：${e?.message || '未知错误'}` })
    }
  } finally {
    if (elapsedTimer) clearInterval(elapsedTimer)
    if (requestTimeout) clearTimeout(requestTimeout)
    elapsedTimer = null
    requestTimeout = null
    activeController = null
    loading.value = false
    await scrollDown()
    saveHistory()
  }
}

function stopGeneration() {
  if (!loading.value) return
  stoppedByUser = true
  activeController?.abort()
}

async function scrollDown() {
  await nextTick()
  if (msgBox.value) {
    msgBox.value.scrollTop = msgBox.value.scrollHeight
  }
}

function loadHistory() {
  try {
    const saved = localStorage.getItem('ai_agent_history')
    if (saved) {
      const parsed = JSON.parse(saved)
      if (Array.isArray(parsed) && parsed.length > 0) {
        messages.value = parsed
        return true
      }
    }
  } catch(e) {}
  return false
}
function saveHistory() {
  try {
    // Save last 50 messages to localStorage
    const toSave = messages.value.slice(-50)
    localStorage.setItem('ai_agent_history', JSON.stringify(toSave))
  } catch(e) {}
}
function clearHistory() {
  messages.value = []
  localStorage.removeItem('ai_agent_history')
  messages.value.push({
    role: 'assistant',
    content: '历史已清除！我是**开放数字化底座 AI 智能体** 🤖\n\n可直接输入问题进行咨询。',
  })
  ElMessage.success('聊天历史已清除')
}
onMounted(() => {
  if (!loadHistory()) {
    messages.value.push({
      role: 'assistant',
      content: '你好！我是**开放数字化底座 AI 智能体** 🤖\n\n基于智谱 GLM-4-Flash 大模型，我可以帮你：\n- 📊 查询数据集中台的所有数据集\n- 📈 对数据列画曲线图、柱状图、饼图\n- 🤖 查询模型工厂的模型与训练任务\n- ⚙️ 汇总平台运营统计\n\n可直接输入问题进行咨询。',
    })
  }
})

onUnmounted(() => {
  stoppedByUser = true
  activeController?.abort()
  if (elapsedTimer) clearInterval(elapsedTimer)
  if (requestTimeout) clearTimeout(requestTimeout)
  chartInstances.forEach(c => c.dispose())
  chartInstances.clear()
})
</script>

<style scoped>
.ai-agent { padding: 24px; color: var(--tx); height: calc(100vh - 100px); display: flex; flex-direction: column; }
.page-header { margin-bottom: 16px; display: flex; justify-content: space-between; align-items: flex-start; }
.page-header h2 { font-size: 22px; margin: 0 0 4px; }
.page-header p { color: var(--tx2); margin: 0; font-size: 13px; }

.agent-layout { display: flex; gap: 20px; flex: 1; min-height: 0; }
.chat-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg2); border-radius: 12px; border: 1px solid var(--bd); overflow: hidden; }
.messages { flex: 1; overflow-y: auto; padding: 20px; }
.welcome { text-align: center; padding: 40px 20px; }
.welcome-icon { font-size: 48px; margin-bottom: 12px; }
.welcome h3 { font-size: 20px; margin: 0 0 8px; }
.welcome p { color: var(--tx2); margin: 0 0 16px; }
.quick-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; }
.quick-btn { padding: 8px 16px; background: var(--bg); border: 1px solid var(--bd); border-radius: 8px; color: var(--ac); cursor: pointer; font-size: 13px; transition: all 0.2s; }
.quick-btn:hover { border-color: var(--ac); background: rgba(99,102,241,0.1); }

.msg { display: flex; gap: 10px; margin-bottom: 16px; }
.msg.user { flex-direction: row-reverse; }
.msg-avatar { width: 32px; height: 32px; border-radius: 8px; background: var(--bg); display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
.msg-content { max-width: 80%; padding: 10px 14px; border-radius: 10px; font-size: 14px; line-height: 1.6; word-break: break-word; min-width: 0; }
.evidence{margin-top:8px;padding-top:6px;border-top:1px solid var(--bd);font-size:12px;color:var(--tx2)}.evidence span{margin-right:6px}
.runtime{margin-top:7px;color:var(--tx2);font-size:11px}
.msg.user .msg-content { background: var(--ac); color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .msg-content { background: var(--bg); border: 1px solid var(--bd); border-bottom-left-radius: 4px; }
.typing { color: var(--tx2); font-style: italic; }
.dots { animation: blink 1.4s infinite; }
@keyframes blink { 0%,100%{opacity:0.2} 50%{opacity:1} }

.msg-charts { margin-top: 12px; display: flex; flex-direction: column; gap: 12px; }
.chart-box { width: 100%; min-width: 400px; border-radius: 8px; border: 1px solid var(--bd); background: #fff; }

.input-area { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--bd); background: var(--bg2); }
.chat-input { flex: 1; padding: 10px 14px; background: var(--bg); border: 1px solid var(--bd); border-radius: 8px; color: var(--tx); font-size: 14px; outline: none; }
.chat-input:focus { border-color: var(--ac); }
.send-btn { padding: 10px 20px; background: var(--ac); color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.stop-btn,.inline-stop{border:1px solid #f56c6c;border-radius:7px;background:#fff;color:#f56c6c;cursor:pointer}
.stop-btn{padding:10px 20px;font-size:14px}.inline-stop{margin-left:10px;padding:3px 8px;font-size:12px}

.tools-panel { width: 260px; flex-shrink: 0; }
.tools-panel h4 { font-size: 14px; margin: 0 0 10px; color: var(--tx2); }
.tool-list { display: flex; flex-direction: column; gap: 8px; }
.tool-card { display: flex; gap: 10px; padding: 10px; background: var(--bg2); border: 1px solid var(--bd); border-radius: 8px; cursor: pointer; transition: all 0.2s; }
.tool-card:hover { border-color: var(--ac); transform: translateX(2px); }
.tool-icon { font-size: 20px; flex-shrink: 0; }
.tool-name { font-size: 13px; font-weight: 600; }
.tool-desc { font-size: 11px; color: var(--tx2); margin-top: 2px; }

.domain-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { padding: 4px 10px; background: var(--bg); border: 1px solid var(--bd); border-radius: 6px; font-size: 12px; cursor: pointer; color: var(--tx2); transition: all 0.2s; }
.tag:hover { border-color: var(--ac); color: var(--ac); }
</style>
