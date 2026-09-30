<template>
<div class="academy" v-if="currentModule">
  <aside class="sidebar">
    <div class="sb-header" @click="currentModule=null">← 课程大纲</div>
    <div class="sb-progress"><div class="prog-bar"><div class="prog-fill" :style="{width:progressPct+'%'}"/></div><span>{{completed}}/{{academyModules.length}} 完成</span></div>
    <button v-if="lastModule && lastModule.id!==currentModule?.id" class="sidebar-resume" @click="selectModule(lastModule)">继续学习：{{ lastModule.title }}</button>
    <div v-for="m in academyModules" :key="m.id" class="sb-item" :class="{active:currentModule?.id===m.id,done:m.done}" @click="selectModule(m)">
      <span class="sb-num">{{m.id}}</span><span class="sb-title">{{m.title}}</span><span v-if="m.done" class="sb-check">✓</span>
    </div>
  </aside>
  <main class="content" ref="contentRef">
    <div class="lesson-header"><span class="lh-badge">模块{{currentModule.id}}</span><h1>{{currentModule.title}}</h1><p>{{currentModule.desc}}</p></div>
    <template v-for="(layer,li) in currentModule.layers" :key="'l'+li">
      <div class="layer-divider">
        <span class="layer-badge" :class="'layer-'+layer.level">{{ layerLabel(layer.level) }}</span>
        <span class="layer-sub">{{ layerSub(layer.level) }}</span>
      </div>
      <section class="section" v-for="(sec,si) in layer.sections" :key="si">
        <h3>{{sec.title}}</h3>
        <div class="sec-body" v-html="sec.content"/>
        <div v-if="sec.code" class="code-block"><div class="code-header"><span>{{sec.codeLang||'python'}}</span><button class="copy-btn" @click="copyCode(sec.code)">📋</button></div><pre><code>{{sec.code}}</code></pre></div>
        <div v-if="sec.dataRef" class="sec-data-ref">📊 使用数据：{{sec.dataRef}}</div>
        <div v-if="sec.modelRef" class="sec-model-ref">🤖 使用模型：{{sec.modelRef}}</div>
        <div v-if="sec.note" class="sec-note" v-html="sec.note"/>
        <div v-if="sec.action" class="sec-action">
          <el-button :type="sec.action.primary?'primary':'default'" size="small" :disabled="sec.action.available===false" @click="doAction(sec.action)">{{sec.action.label}}</el-button>
          <el-tag v-if="sec.action.available===false" size="small" type="info">当前暂不可用</el-tag>
          <span class="sec-action-hint">{{sec.action.unavailableHint || sec.action.hint}}</span>
        </div>
      </section>
    </template>
    <section class="section quiz" v-if="currentModule.quiz">
      <h3>📝 知识测验</h3>
      <div v-for="(q,qi) in currentModule.quiz" :key="qi" class="quiz-item">
        <p class="quiz-q">{{qi+1}}. {{q.question}}</p>
        <div class="quiz-options">
          <button v-for="(opt,oi) in q.options" :key="oi" class="quiz-opt" :class="{correct:q.answered===oi&&oi===q.answer,wrong:q.answered===oi&&oi!==q.answer}" @click="answerQuiz(currentModule,qi,oi)" :disabled="q.answered===q.answer">{{opt}}</button>
        </div>
        <div v-if="q.answered!==undefined" class="quiz-result" :class="{pass:q.answered===q.answer}">{{q.answered===q.answer?'✅ 正确！':'❌ 未正确，可继续选择。'}}</div>
      </div>
    </section>
    <div class="complete-section"><el-button type="success" size="large" @click="markDone(currentModule)" :disabled="currentModule.done">{{currentModule.done?'✅ 已完成':'🎉 标记完成'}}</el-button></div>
  </main>
</div>

<div class="academy-overview" v-else>
  <div class="hero">
    <h1>🏭 开放底座工业AI学院</h1>
    <p>围绕数据处理、模型训练、评估与部署的实操学习路线</p>
    <div class="hero-stats">
      <div class="hs"><span class="hs-n">{{academyModules.length}}</span><span class="hs-l">学习模块</span></div>
      <div class="hs"><span class="hs-n">{{completed}}</span><span class="hs-l">已完成</span></div>
      <div class="hs"><span class="hs-n">{{totalSections}}</span><span class="hs-l">课程小节</span></div>
      <div class="hs"><span class="hs-n">{{totalCodeBlocks}}</span><span class="hs-l">代码示例</span></div>
    </div>
  </div>
  <div v-if="lastModule" class="resume-card" @click="selectModule(lastModule)">
    <div><span class="resume-label">继续学习</span><strong>模块{{lastModule.id}}：{{lastModule.title}}</strong><p>从上次浏览的课程继续。</p></div>
    <el-button type="primary" @click.stop="selectModule(lastModule)">继续</el-button>
  </div>
  <div class="data-showcase">
    <h2>🧭 常用实操入口</h2>
    <div class="ds-grid">
      <button class="ds-card" @click="navigate('/data/new-datasets')"><span class="ds-icon">🗂️</span><strong>数据集管理</strong><small>上传、清洗分析与数据分割</small></button>
      <button class="ds-card" @click="navigate('/data/analysis')"><span class="ds-icon">📊</span><strong>算法数据分析</strong><small>运行统计、时序与关系分析</small></button>
      <button class="ds-card" @click="navigate('/model/algorithm-store')"><span class="ds-icon">🧩</span><strong>算法商店</strong><small>选择算法并配置训练</small></button>
      <button class="ds-card" @click="navigate('/deploy/deployment')"><span class="ds-icon">🚀</span><strong>部署管理</strong><small>管理已部署模型与版本</small></button>
    </div>
  </div>
  <div class="roadmap">
    <h2>🗺️ AI全栈学习路线</h2>
    <div class="rm-steps">
      <div v-for="m in academyModules" :key="m.id" class="rm-step" :class="{done:m.done}" @click="selectModule(m)">
        <div class="rm-num">{{m.id}}</div>
        <div class="rm-info"><div class="rm-icon">{{m.icon}}</div><strong>{{m.title}}</strong><p>{{m.desc}}</p></div>
      </div>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { academyModules as _academyModules, type Module, type Section, type LayerSection, type QuizItem } from './AcademyData'
const ACADEMY_PROGRESS_KEY = 'foundation-academy-progress-v1'
const academyModules = ref<Module[]>(JSON.parse(JSON.stringify(_academyModules)))
const contentRef = ref<HTMLElement | null>(null)

const router = useRouter()
const currentModule = ref<Module | null>(null)
const lastModuleId = ref<number | null>(null)

function restoreLearningProgress() {
  try {
    const saved = JSON.parse(localStorage.getItem(ACADEMY_PROGRESS_KEY) || '{}')
    const completedIds = new Set<number>(saved.completedIds || [])
    academyModules.value.forEach((module) => {
      module.done = completedIds.has(module.id)
      module.quiz?.forEach((quiz, index) => {
        const answer = saved.answers?.[`${module.id}:${index}`]
        if (typeof answer === 'number') quiz.answered = answer
      })
    })
    if (academyModules.value.some((module) => module.id === saved.lastModuleId)) lastModuleId.value = saved.lastModuleId
  } catch {
    // 本地进度无效时从空进度开始，不影响课程浏览。
  }
}
function persistLearningProgress() {
  const answers: Record<string, number> = {}
  academyModules.value.forEach((module) => module.quiz?.forEach((quiz, index) => {
    if (typeof quiz.answered === 'number') answers[`${module.id}:${index}`] = quiz.answered
  }))
  localStorage.setItem(ACADEMY_PROGRESS_KEY, JSON.stringify({
    completedIds: academyModules.value.filter((module) => module.done).map((module) => module.id),
    answers,
    lastModuleId: lastModuleId.value
  }))
}
restoreLearningProgress()

const lastModule = computed(() => academyModules.value.find((module) => module.id === lastModuleId.value) || null)
const completed = computed(() => academyModules.value.filter((m: Module) => m.done).length)
const progressPct = computed(() => Math.round((completed.value / academyModules.value.length) * 100))
const totalSections = computed(() => academyModules.value.reduce((a: number, m: Module) => a + m.layers.reduce((b: number, l: LayerSection) => b + l.sections.length, 0), 0))
const totalCodeBlocks = computed(() => academyModules.value.reduce((a: number, m: Module) => a + m.layers.reduce((b: number, l: LayerSection) => b + l.sections.filter((s: Section) => s.code).length, 0), 0))
// modules is now academyModules (reactive ref)

function layerLabel(level: string): string {
  const m: Record<string, string> = { principle: '🧠 原理层', scenario: '🏭 场景层', practice: '🛠️ 实践层' }
  return m[level] || level
}
function layerSub(level: string): string {
  const m: Record<string, string> = { principle: '通用理论 · 数学基础 · 算法原理', scenario: '工业场景 · 真实数据 · 业务落地', practice: '平台操作 · 代码实战 · 模型部署' }
  return m[level] || ''
}
function selectModule(m: Module) {
  currentModule.value = m
  lastModuleId.value = m.id
  persistLearningProgress()
  // 切换课题时把内容区滚回顶部,避免沿用上一章的滚动位置
  nextTick(() => { contentRef.value?.scrollTo({ top: 0 }) })
}
function markDone(m: Module) { m.done = true; persistLearningProgress(); ElMessage.success('完成模块：' + m.title) }
function answerQuiz(m: Module, qi: number, oi: number) { if (m.quiz) { m.quiz[qi].answered = oi; persistLearningProgress() } }
function copyCode(code: string) { navigator.clipboard.writeText(code).then(() => ElMessage.success('代码已复制')) }
function navigate(path: string) { router.push(path) }
function doAction(a: any) {
  if (a.available === false) return ElMessage.info(a.unavailableHint || '该实操功能当前暂不可用')
  if (a.action === 'nav' && a.path) navigate(a.path)
}
</script>

<style scoped>
.academy{display:flex;height:calc(100vh - 60px);background:#f8fafc;font-family:'Inter',-apple-system,sans-serif}
.sidebar{width:280px;background:#1e293b;color:#e2e8f0;padding:20px 0;overflow-y:auto;flex-shrink:0}
.sb-header{padding:12px 20px;cursor:pointer;font-size:14px;color:#94a3b8;transition:color .2s}
.sb-header:hover{color:#fff}
.sb-progress{display:flex;align-items:center;gap:10px;padding:0 20px 12px;font-size:12px;color:#94a3b8}
.sidebar-resume{width:calc(100% - 40px);margin:0 20px 14px;padding:8px 10px;border:1px solid #475569;border-radius:6px;background:#334155;color:#cbd5e1;text-align:left;font-size:12px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sidebar-resume:hover{border-color:#818cf8;color:#fff}
.prog-bar{flex:1;height:4px;background:#334155;border-radius:2px;overflow:hidden}
.prog-fill{height:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);transition:width .4s}
.sb-item{display:flex;align-items:center;gap:10px;padding:10px 20px;cursor:pointer;font-size:13px;transition:all .2s;border-left:3px solid transparent}
.sb-item:hover{background:#334155}
.sb-item.active{background:#1d4ed8;color:#fff;border-left-color:#60a5fa}
.sb-item.done{color:#10b981}
.sb-num{width:24px;height:24px;border-radius:50%;background:#475569;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0}
.sb-item.active .sb-num{background:#3b82f6}
.sb-item.done .sb-num{background:#10b981;color:#fff}
.sb-title{flex:1}.sb-check{color:#10b981;font-weight:700}
.content{flex:1;padding:32px 48px;overflow-y:auto;max-width:960px}
.lesson-header{margin-bottom:24px}.lh-badge{display:inline-block;padding:4px 12px;background:#dbeafe;color:#1d4ed8;border-radius:12px;font-size:12px;font-weight:600;margin-bottom:8px}
.lesson-header h1{font-size:28px;margin:8px 0 4px;color:#1e293b}.lesson-header p{color:#64748b;font-size:15px}
.layer-divider{display:flex;align-items:center;gap:12px;margin:32px 0 16px;padding:12px 16px;background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.layer-badge{font-size:15px;font-weight:700}.layer-badge.layer-principle{color:#6366f1}.layer-badge.layer-scenario{color:#f59e0b}.layer-badge.layer-practice{color:#10b981}
.layer-sub{font-size:13px;color:#94a3b8}
.section{background:#fff;border-radius:10px;padding:20px 24px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.section h3{font-size:17px;color:#1e293b;margin:0 0 10px;padding-bottom:8px;border-bottom:2px solid #f1f5f9}
.sec-body{font-size:14px;line-height:1.8;color:#475569}.sec-body b{color:#1e293b}
.code-block{margin-top:12px;background:#1e1e1e;border-radius:8px;overflow:hidden}.code-header{display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:#333;color:#aaa;font-size:12px}
.copy-btn{background:none;border:none;color:#aaa;cursor:pointer;font-size:14px}.copy-btn:hover{color:#fff}
.code-block pre{margin:0;padding:12px 16px;overflow-x:auto}.code-block code{color:#d4d4d4;font-size:12px;line-height:1.6;font-family:'Fira Code',monospace}
.sec-data-ref,.sec-model-ref{font-size:12px;color:#6366f1;margin-top:8px;padding:4px 8px;background:#eef2ff;border-radius:4px;display:inline-block}
.sec-model-ref{color:#d97706;background:#fffbeb}
.sec-note{margin-top:10px;padding:10px 14px;background:#fffbeb;border-left:3px solid #f59e0b;border-radius:4px;font-size:13px;line-height:1.7;color:#92400e}
.sec-action{margin-top:12px;display:flex;align-items:center;gap:10px}.sec-action-hint{font-size:12px;color:#94a3b8}
.quiz{margin-top:8px}.quiz-item{margin-bottom:16px}.quiz-q{font-weight:600;font-size:14px;color:#1e293b;margin-bottom:8px}
.quiz-options{display:flex;gap:8px;flex-wrap:wrap}
.quiz-opt{padding:6px 14px;border:1px solid #e2e8f0;border-radius:6px;background:#fff;cursor:pointer;font-size:13px;transition:all .2s}.quiz-opt:hover:not(:disabled){border-color:#6366f1;color:#6366f1}
.quiz-opt.correct{background:#dcfce7;border-color:#10b981;color:#166534}.quiz-opt.wrong{background:#fee2e2;border-color:#ef4444;color:#991b1b}
.quiz-result{font-size:13px;margin-top:6px;font-weight:600}.quiz-result.pass{color:#10b981}
.complete-section{margin-top:24px;text-align:center}
.academy-overview{max-width:1100px;margin:0 auto;padding:40px 24px}
.hero{text-align:center;margin-bottom:48px}.hero h1{font-size:36px;color:#1e293b;margin:0 0 8px}.hero p{color:#64748b;font-size:17px}
.hero-stats{display:flex;justify-content:center;gap:40px;margin-top:24px}.hs{text-align:center}.hs-n{font-size:28px;font-weight:800;color:#6366f1;display:block}.hs-l{font-size:13px;color:#94a3b8}
.resume-card{display:flex;align-items:center;justify-content:space-between;gap:20px;margin:0 auto 32px;max-width:760px;padding:18px 22px;background:linear-gradient(120deg,#eef2ff,#f5f3ff);border:1px solid #ddd6fe;border-radius:12px;cursor:pointer}.resume-label{display:block;font-size:12px;color:#6366f1;margin-bottom:4px}.resume-card strong{display:block;color:#312e81}.resume-card p{margin:5px 0 0;color:#64748b;font-size:13px}
.data-showcase{margin-bottom:48px}.data-showcase h2{font-size:20px;color:#1e293b;margin-bottom:16px}
.ds-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.ds-card{border:0;background:#fff;border-radius:12px;padding:20px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.06);font:inherit;cursor:pointer;transition:transform .2s,box-shadow .2s}.ds-card:hover{transform:translateY(-2px);box-shadow:0 5px 14px rgba(0,0,0,.1)}.ds-icon{font-size:32px;display:block;margin-bottom:8px}
.ds-card strong{display:block;font-size:14px;color:#1e293b;margin-bottom:4px}.ds-card p{color:#6366f1;font-weight:600;font-size:13px;margin:0}.ds-card small{color:#94a3b8;font-size:11px}
.roadmap h2{font-size:20px;color:#1e293b;margin-bottom:16px}
.rm-steps{display:flex;flex-direction:column;gap:12px}
.rm-step{display:flex;align-items:center;gap:16px;padding:16px 20px;background:#fff;border-radius:12px;cursor:pointer;transition:all .2s;box-shadow:0 1px 4px rgba(0,0,0,.04)}.rm-step:hover{transform:translateX(4px);box-shadow:0 4px 12px rgba(0,0,0,.08)}
.rm-step.done{opacity:.7;border-left:4px solid #10b981}
.rm-num{width:40px;height:40px;border-radius:50%;background:#eef2ff;color:#6366f1;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;flex-shrink:0}
.rm-step.done .rm-num{background:#dcfce7;color:#10b981}
.rm-icon{font-size:24px}.rm-info strong{display:block;font-size:15px;color:#1e293b}.rm-info p{margin:2px 0 0;font-size:13px;color:#64748b}
@media(max-width:768px){.academy{flex-direction:column}.sidebar{width:100%}.content{padding:20px}.ds-grid{grid-template-columns:repeat(2,1fr)}}
</style>
