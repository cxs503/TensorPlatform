<template>
  <div class="research-factory">
    <section class="hero">
      <div class="hero-copy">
        <div class="eyebrow"><span class="pulse"></span> TENSOR RESEARCH INFRASTRUCTURE</div>
        <h1>科研工厂 <span>Research Factory</span></h1>
        <p>让 Agent 以科研主体的方式工作：提出目标、编排求解器、运行实验、核验结果，并将证据沉淀为可复现的科研资产。</p>
        <div class="hero-actions">
          <el-button type="primary" size="large" @click="showCreate = true"><el-icon><Plus /></el-icon>发起科研任务</el-button>
          <el-button size="large" plain @click="scrollToWorkflow"><el-icon><Connection /></el-icon>查看工作流</el-button>
        </div>
      </div>
      <div class="hero-orbit" aria-label="Agent 到科学证据的工作流示意">
        <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div>
        <div class="orbit-core"><el-icon><Cpu /></el-icon><strong>Agent</strong><small>科研主体</small></div>
        <div class="orbit-node node-a"><el-icon><Aim /></el-icon><span>目标</span></div>
        <div class="orbit-node node-b"><el-icon><Connection /></el-icon><span>求解器</span></div>
        <div class="orbit-node node-c"><el-icon><DataAnalysis /></el-icon><span>证据</span></div>
      </div>
      <div class="environment-tag"><span class="dot"></span> 当前执行环境：MOCK 演示 · 非真实 HPC</div>
    </section>

    <section class="principles">
      <div class="principle"><span class="principle-no">01</span><div><strong>Agent 是执行主体</strong><p>从科研目标开始，而不是从孤立工具或聊天窗口开始。</p></div></div>
      <div class="principle"><span class="principle-no">02</span><div><strong>求解器是可调用能力</strong><p>TensorLBM / FEM / DEM 等按统一任务契约接入。</p></div></div>
      <div class="principle"><span class="principle-no">03</span><div><strong>证据决定完成状态</strong><p>日志、参数、产物与验证报告共同构成实验记录。</p></div></div>
    </section>

    <section class="metrics">
      <div class="metric-card"><div class="metric-label">科研任务 <span class="metric-icon"><el-icon><List /></el-icon></span></div><div class="metric-value">{{ tasks.length }}</div><div class="metric-foot">当前工作台记录</div></div>
      <div class="metric-card"><div class="metric-label">已生成实验记录 <span class="metric-icon"><el-icon><DocumentChecked /></el-icon></span></div><div class="metric-value">{{ completedCount }}</div><div class="metric-foot">仅表示演示流程已完成</div></div>
      <div class="metric-card"><div class="metric-label">待验证 / 需关注 <span class="metric-icon amber"><el-icon><Warning /></el-icon></span></div><div class="metric-value">{{ reviewCount }}</div><div class="metric-foot">数值正确性仍需独立验证</div></div>
      <div class="metric-card"><div class="metric-label">真实 HPC 接入 <span class="metric-icon"><el-icon><Cpu /></el-icon></span></div><div class="metric-value status-text">未连接</div><div class="metric-foot">保留调度器适配边界</div></div>
    </section>

    <section id="workflow" class="section-block">
      <div class="section-heading"><div><div class="section-kicker">EXECUTION CONTRACT</div><h2>从科研问题到可复现证据</h2><p>每一步都有明确输入、输出和可追溯状态，不把“任务结束”直接等同于“科学结论成立”。</p></div><el-tag effect="plain" type="info">Harness 工作流</el-tag></div>
      <div class="workflow">
        <div v-for="(step, index) in stages" :key="step.title" class="workflow-step" :class="{ 'stage-active': index === 0 }">
          <div class="stage-top"><span class="stage-index">0{{ index + 1 }}</span><el-icon><component :is="step.icon" /></el-icon></div>
          <strong>{{ step.title }}</strong><p>{{ step.description }}</p><div class="stage-output"><span>输出</span>{{ step.output }}</div>
          <div v-if="index < stages.length - 1" class="stage-arrow"><el-icon><Right /></el-icon></div>
        </div>
      </div>
    </section>

    <section class="section-block solver-section">
      <div class="section-heading"><div><div class="section-kicker">SOLVER ECOSYSTEM</div><h2>求解器与计算资源</h2><p>平台负责编排与资产管理，具体物理模型和数值计算仍由专业求解器承担。</p></div></div>
      <div class="solver-grid">
        <div v-for="solver in solvers" :key="solver.name" class="solver-card">
          <div class="solver-mark" :class="solver.tone">{{ solver.short }}</div>
          <div class="solver-info"><strong>{{ solver.name }}</strong><p>{{ solver.role }}</p><span>{{ solver.status }}</span></div>
          <el-tag size="small" :type="solver.tagType" effect="plain">{{ solver.mode }}</el-tag>
        </div>
      </div>
    </section>

    <section class="section-block task-section">
      <div class="section-heading"><div><div class="section-kicker">EXPERIMENT REGISTRY</div><h2>最近科研任务</h2><p>任务状态、执行环境与验证结论分开展示，防止将模拟演示误认为真实计算结果。</p></div><el-button @click="showCreate = true"><el-icon><Plus /></el-icon>新建任务</el-button></div>
      <el-table :data="tasks" row-key="id" class="task-table" empty-text="还没有科研任务，发起一个目标开始演示闭环。">
        <el-table-column label="科研任务" min-width="260">
          <template #default="{ row }"><div class="task-name">{{ row.title }}</div><div class="task-sub">{{ row.id }} · {{ row.solver }}</div></template>
        </el-table-column>
        <el-table-column label="执行阶段" min-width="130"><template #default="{ row }"><el-tag :type="stateType(row.state)" effect="light">{{ row.state }}</el-tag></template></el-table-column>
        <el-table-column label="执行环境" min-width="130"><template #default="{ row }"><span class="env-pill">{{ row.environment }}</span></template></el-table-column>
        <el-table-column label="验证状态" min-width="140"><template #default="{ row }"><span class="verification" :class="row.verification === '待独立验证' ? 'verify-pending' : 'verify-ready'"><span></span>{{ row.verification }}</span></template></el-table-column>
        <el-table-column label="记录时间" min-width="155"><template #default="{ row }">{{ row.time }}</template></el-table-column>
        <el-table-column label="操作" width="130" fixed="right"><template #default="{ row }"><el-button text type="primary" @click="openTask(row)">查看记录</el-button></template></el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="showCreate" title="发起科研任务" width="min(680px, 94vw)" destroy-on-close>
      <div class="dialog-intro">描述科学问题并选择求解器。当前只创建本地 MOCK 演示记录，不会提交真实计算资源。</div>
      <el-form label-position="top" :model="form">
        <el-form-item label="科研目标" required><el-input v-model="form.title" placeholder="例如：评估键合颗粒冰层的破坏与阻力响应" maxlength="120" show-word-limit /></el-form-item>
        <el-form-item label="研究描述"><el-input v-model="form.goal" type="textarea" :rows="3" placeholder="说明研究对象、关键变量、希望验证的假设或指标。" /></el-form-item>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="求解器"><el-select v-model="form.solver" style="width:100%"><el-option label="TensorDEM · 键合颗粒破冰" value="TensorDEM" /><el-option label="TensorLBM · 流体计算" value="TensorLBM" /><el-option label="TensorFEM · 结构力学" value="TensorFEM" /><el-option label="TensorPhysics · 工作流验证" value="TensorPhysics" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="执行模式"><el-select v-model="form.mode" style="width:100%"><el-option label="MOCK 演示（本地状态）" value="MOCK" /><el-option label="计划接入 HPC（不提交）" value="HPC-PLANNED" /></el-select></el-form-item></el-col>
        </el-row>
        <el-alert title="当前边界" type="warning" :closable="false" show-icon description="此页面的任务与结果保存在当前页面会话状态中；尚未调用后端 Harness、求解器进程或 HPC 调度器。演示数值不能作为科学结论。" />
      </el-form>
      <template #footer><el-button @click="showCreate = false">取消</el-button><el-button type="primary" :disabled="!form.title.trim()" @click="createTask">创建演示任务</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="科研运行记录" size="min(560px, 92vw)">
      <template v-if="selectedTask">
        <div class="detail-banner"><span class="detail-label">RUN MANIFEST · {{ selectedTask.id }}</span><h3>{{ selectedTask.title }}</h3><p>{{ selectedTask.goal || '未提供额外研究描述。' }}</p><el-tag type="info" effect="plain">{{ selectedTask.environment }}</el-tag></div>
        <h4>执行轨迹</h4>
        <el-timeline>
          <el-timeline-item v-for="event in selectedTask.events" :key="event.title" :timestamp="event.time" :type="event.type">
            <strong>{{ event.title }}</strong><p>{{ event.description }}</p>
          </el-timeline-item>
        </el-timeline>
        <h4>产物与证据清单</h4>
        <div class="artifact-row"><el-icon><Document /></el-icon><div><strong>run-manifest.json</strong><p>目标、求解器、模式、时间与状态元数据</p></div><el-tag size="small" type="info">演示元数据</el-tag></div>
        <div class="artifact-row"><el-icon><Document /></el-icon><div><strong>execution-log.txt</strong><p>当前演示流程的步骤记录</p></div><el-tag size="small" type="info">模拟日志</el-tag></div>
        <el-alert class="detail-alert" title="验证声明" type="warning" :closable="false" show-icon description="本记录不包含经求解器实际计算的场数据或独立基准验证。只有完成真实运行、产物校验和物理/数值验证后，才能升级为已验证实验。" />
        <el-button type="primary" plain @click="copyManifest"><el-icon><CopyDocument /></el-icon>复制运行清单 JSON</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Aim, Connection, CopyDocument, Cpu, DataAnalysis, Document, DocumentChecked, List, Monitor, Plus, Right, Warning } from '@element-plus/icons-vue'

type RunEvent = { title: string; description: string; time: string; type: 'primary' | 'success' | 'warning' | 'info' }
type ResearchTask = { id: string; title: string; goal: string; solver: string; state: string; environment: string; verification: string; time: string; events: RunEvent[] }
const now = () => new Date().toLocaleString('zh-CN', { hour12: false })
const event = (title: string, description: string, type: RunEvent['type'] = 'info'): RunEvent => ({ title, description, time: now(), type })
const tasks = ref<ResearchTask[]>([
  { id: 'MOCK-ICE-0001', title: '键合颗粒冰层破坏与阻力响应流程验证', goal: '验证 Agent 目标分解、求解器选择、运行清单与证据登记的工作流契约。', solver: 'TensorDEM', state: '演示完成', environment: 'MOCK / 本地', verification: '待独立验证', time: '2026-10-15 16:20', events: [
    { title: '科研目标已登记', description: '明确研究对象、预期输出与验证边界。', time: '16:20:01', type: 'success' },
    { title: 'Agent 计划已生成（模拟）', description: '拆分为参数准备、求解器执行、产物登记与独立验证。', time: '16:20:03', type: 'primary' },
    { title: '执行轨迹已生成（模拟）', description: '未启动 TensorDEM 进程；未使用真实 HPC 资源。', time: '16:20:04', type: 'warning' },
    { title: '证据状态已标记', description: '仅验证页面流程，不代表物理模型或数值结果通过验证。', time: '16:20:04', type: 'warning' }
  ] }
])
const completedCount = computed(() => tasks.value.filter(t => t.state === '演示完成').length)
const reviewCount = computed(() => tasks.value.filter(t => t.verification !== '已独立验证').length)
const showCreate = ref(false)
const detailVisible = ref(false)
const selectedTask = ref<ResearchTask | null>(null)
const form = reactive({ title: '', goal: '', solver: 'TensorDEM', mode: 'MOCK' })
const stages = [
  { title: '科研目标', description: '解析问题、指标、约束与验收标准。', output: 'ResearchGoal', icon: Aim },
  { title: 'Agent 规划', description: '生成可审计的实验计划与依赖关系。', output: 'ExperimentPlan', icon: Connection },
  { title: '求解器执行', description: '通过适配器调用数值求解器与调度器。', output: 'RunManifest', icon: Cpu },
  { title: '证据验证', description: '校验产物、日志、基准与物理一致性。', output: 'ValidationReport', icon: DocumentChecked },
  { title: '资产沉淀', description: '登记数据、模型、参数和可复现实验。', output: 'ArtifactRef', icon: DataAnalysis }
]
const solvers = [
  { name: 'TensorDEM', short: 'DEM', role: '键合颗粒、接触与断键破坏', status: '破冰研究重点', mode: '待接入 Harness', tagType: 'warning' as const, tone: 'violet' },
  { name: 'TensorLBM', short: 'LBM', role: '流体求解与流固耦合能力', status: '独立求解器', mode: '适配器规划', tagType: 'info' as const, tone: 'blue' },
  { name: 'TensorFEM', short: 'FEM', role: '结构响应与有限元分析', status: '独立求解器', mode: '适配器规划', tagType: 'info' as const, tone: 'green' },
  { name: 'TensorPhysics', short: 'TP', role: '科研契约、编排、清单与验证', status: '科研工作流层', mode: '接口对接中', tagType: 'success' as const, tone: 'amber' }
]
function createTask() {
  if (!form.title.trim()) return
  const id = `MOCK-${form.solver.toUpperCase()}-${String(tasks.value.length + 1).padStart(4, '0')}`
  const environment = form.mode === 'MOCK' ? 'MOCK / 本地' : 'HPC / 未提交'
  tasks.value.unshift({
    id, title: form.title.trim(), goal: form.goal.trim(), solver: form.solver, state: form.mode === 'MOCK' ? '演示完成' : '待接入',
    environment, verification: '待独立验证', time: now(),
    events: [
      event('科研目标已登记', '记录用户目标与验收意图。', 'success'),
      event('Agent 规划阶段', '当前使用工作流模板模拟；尚未调用真实 Agent 后端。', 'primary'),
      event('执行器边界检查', form.mode === 'MOCK' ? '创建演示轨迹，未启动求解器进程。' : '仅记录目标执行环境，未提交任何 HPC 作业。', 'warning'),
      event('验证状态登记', '等待真实运行产物与独立数值验证。', 'warning')
    ]
  })
  showCreate.value = false
  form.title = ''; form.goal = ''; form.solver = 'TensorDEM'; form.mode = 'MOCK'
  ElMessage.success('已创建演示任务记录；未启动真实计算。')
}
function openTask(task: ResearchTask) { selectedTask.value = task; detailVisible.value = true }
function stateType(state: string) { return state === '演示完成' ? 'success' : state === '运行中' ? 'primary' : 'info' }
function scrollToWorkflow() { document.getElementById('workflow')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
async function copyManifest() {
  if (!selectedTask.value) return
  const payload = JSON.stringify({ run_id: selectedTask.value.id, title: selectedTask.value.title, goal: selectedTask.value.goal, solver: selectedTask.value.solver, environment: selectedTask.value.environment, state: selectedTask.value.state, verification: selectedTask.value.verification, events: selectedTask.value.events, scientific_validity: 'not_established' }, null, 2)
  try { await navigator.clipboard.writeText(payload); ElMessage.success('运行清单已复制') }
  catch { ElMessage.info(payload) }
}
</script>

<style scoped lang="scss">
.research-factory { --ink:#17263b; --muted:#708097; --line:#e4eaf2; color:var(--ink); padding-bottom:28px; }
.hero { position:relative; display:flex; min-height:270px; overflow:hidden; border-radius:18px; padding:34px 38px; background:linear-gradient(118deg,#101d36 0%,#142e50 58%,#164c61 100%); color:#f5f9ff; box-shadow:0 14px 34px #12243c18; }
.hero-copy { position:relative; z-index:2; max-width:650px; }
.eyebrow,.section-kicker { font-size:10px; font-weight:800; letter-spacing:1.7px; }
.eyebrow { color:#9bcad8; display:flex; align-items:center; gap:9px; }
.pulse { width:7px; height:7px; border-radius:50%; background:#6de1bf; box-shadow:0 0 0 5px #6de1bf20; }
.hero h1 { font-size:32px; letter-spacing:-.7px; margin:17px 0 10px; font-weight:750; }
.hero h1 span { font-size:14px; letter-spacing:.6px; font-weight:500; color:#9db9cf; margin-left:8px; }
.hero p { max-width:600px; color:#c4d3e2; line-height:1.8; font-size:13px; margin:0; }
.hero-actions { display:flex; gap:10px; margin-top:22px; }
.hero-actions :deep(.el-button--default) { background:#ffffff0d; border-color:#8ca9c2; color:#f2f7fb; }
.hero-orbit { position:absolute; width:280px; height:240px; right:48px; top:8px; opacity:.94; }
.orbit { position:absolute; border:1px solid #b4d9e52b; border-radius:50%; transform:rotate(-24deg); }
.orbit-one { width:260px; height:130px; left:6px; top:50px; }
.orbit-two { width:200px; height:220px; left:36px; top:8px; transform:rotate(42deg); }
.orbit-core { position:absolute; top:77px; left:94px; width:90px; height:90px; border-radius:50%; background:linear-gradient(145deg,#2c6179,#183a59); border:1px solid #8fd3e580; box-shadow:0 0 38px #4bb8d52c; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px; }
.orbit-core .el-icon { font-size:23px; color:#a5f0df; }.orbit-core strong { font-size:14px; }.orbit-core small { font-size:10px; color:#b8d3e2; }
.orbit-node { position:absolute; display:flex; align-items:center; gap:5px; border:1px solid #a8d7e24d; background:#102a43e8; border-radius:20px; padding:7px 10px; font-size:11px; color:#d7edf5; }
.orbit-node .el-icon { color:#86e5d2; }.node-a { top:24px; left:105px; }.node-b { top:151px; right:0; }.node-c { top:174px; left:13px; }
.environment-tag { position:absolute; bottom:15px; right:20px; font-size:10px; color:#c4d7e5; display:flex; align-items:center; gap:7px; }.environment-tag .dot { width:6px; height:6px; border-radius:50%; background:#f2c66d; }
.principles { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin:16px 0 20px; }
.principle { display:flex; gap:13px; padding:17px 18px; border:1px solid var(--line); border-radius:12px; background:#fff; }
.principle-no { color:#4a8eaa; font-size:11px; font-weight:800; letter-spacing:1px; padding-top:2px; }.principle strong { font-size:13px; }.principle p { margin:6px 0 0; font-size:11px; line-height:1.65; color:var(--muted); }
.metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:32px; }
.metric-card { background:#fff; border:1px solid var(--line); border-radius:12px; padding:17px 18px; min-width:0; }
.metric-label { color:#697a91; font-size:12px; display:flex; justify-content:space-between; align-items:center; }.metric-icon { display:grid; place-items:center; width:28px; height:28px; border-radius:8px; background:#eaf4f8; color:#3b829d; font-size:15px; }.metric-icon.amber { background:#fff4df; color:#b17b22; }
.metric-value { font-size:27px; font-weight:750; letter-spacing:-.7px; margin:10px 0 4px; }.metric-foot { font-size:10px; color:#8b98a9; }.status-text { font-size:21px; color:#b07a25; letter-spacing:0; }
.section-block { margin-top:30px; }.section-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:18px; margin-bottom:16px; }.section-kicker { color:#3b8aa5; }.section-heading h2 { font-size:20px; margin:6px 0; letter-spacing:-.4px; }.section-heading p { margin:0; font-size:12px; color:var(--muted); line-height:1.7; }
.workflow { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; }
.workflow-step { position:relative; min-height:192px; border:1px solid var(--line); background:#fff; border-radius:12px; padding:16px 14px; }.workflow-step.stage-active { border-color:#a5d4e1; box-shadow:inset 0 3px #4c9cb6; }
.stage-top { display:flex; align-items:center; justify-content:space-between; color:#408ba4; font-size:18px; }.stage-index { font-size:10px; color:#8b9cb0; font-weight:800; letter-spacing:1px; }.workflow-step strong { display:block; font-size:13px; margin:13px 0 7px; }.workflow-step p { font-size:11px; color:var(--muted); line-height:1.65; margin:0; min-height:55px; }.stage-output { border-top:1px solid #edf0f5; padding-top:10px; margin-top:12px; font-family:ui-monospace,monospace; font-size:10px; color:#367f98; }.stage-output span { font-family:inherit; color:#97a3b2; margin-right:7px; }.stage-arrow { position:absolute; top:42px; right:-17px; z-index:2; width:20px; height:20px; border-radius:50%; display:grid; place-items:center; background:#e8f3f6; color:#4e8fa4; font-size:12px; }
.solver-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }.solver-card { display:flex; align-items:center; gap:13px; padding:15px; background:#fff; border:1px solid var(--line); border-radius:12px; }.solver-mark { display:grid; place-items:center; flex:0 0 44px; height:44px; border-radius:11px; font-size:12px; font-weight:800; }.solver-mark.violet { color:#7555a6; background:#f0eafa; }.solver-mark.blue { color:#3879b7; background:#e8f2fd; }.solver-mark.green { color:#39876a; background:#e7f6ed; }.solver-mark.amber { color:#a77825; background:#fff2d9; }.solver-info { min-width:0; flex:1; }.solver-info strong { font-size:13px; }.solver-info p { font-size:11px; color:var(--muted); margin:5px 0; }.solver-info span { font-size:10px; color:#8a98a9; }
.task-section { margin-top:34px; }.task-table { border:1px solid var(--line); border-radius:12px; overflow:hidden; }.task-name { font-size:12px; font-weight:650; }.task-sub { font-size:10px; color:#8a98a9; margin-top:5px; }.env-pill { background:#fff5df; color:#9c6d1e; border-radius:5px; padding:4px 7px; font-size:10px; }.verification { display:inline-flex; align-items:center; gap:7px; font-size:11px; }.verification span { width:6px; height:6px; border-radius:50%; background:#dca13d; }.verify-ready span { background:#3eaa7a; }
.dialog-intro { font-size:12px; color:#718096; line-height:1.7; margin-bottom:20px; }.detail-banner { padding:18px; border-radius:12px; background:#f0f6fa; margin-bottom:24px; }.detail-label { font-size:10px; letter-spacing:1px; color:#4787a0; font-weight:800; }.detail-banner h3 { font-size:17px; margin:10px 0; }.detail-banner p { font-size:12px; color:#687b90; line-height:1.7; }.research-factory h4 { font-size:13px; margin:24px 0 14px; }.research-factory :deep(.el-timeline-item__content p) { font-size:11px; line-height:1.6; color:#77879b; }.artifact-row { display:flex; align-items:center; gap:11px; padding:12px 0; border-bottom:1px solid #edf0f4; }.artifact-row > .el-icon { font-size:18px; color:#548da3; }.artifact-row div { flex:1; }.artifact-row strong { font-size:12px; }.artifact-row p { font-size:10px; margin:4px 0 0; color:#8996a7; }.detail-alert { margin:22px 0 16px; }
@media (max-width: 1100px) { .hero-orbit { right:4px; opacity:.55; }.hero-copy { max-width:70%; }.workflow { grid-template-columns:repeat(3,minmax(0,1fr)); }.stage-arrow { display:none; } }
@media (max-width: 760px) { .hero { padding:25px 22px 58px; min-height:310px; }.hero h1 { font-size:26px; }.hero h1 span { display:block; margin:6px 0 0; }.hero-orbit { transform:scale(.7); transform-origin:top right; right:-20px; top:90px; opacity:.25; }.hero-copy { max-width:100%; }.environment-tag { left:22px; right:auto; bottom:15px; }.principles,.metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }.principle { padding:13px; }.workflow { grid-template-columns:repeat(2,minmax(0,1fr)); }.solver-grid { grid-template-columns:1fr; }.section-heading { align-items:flex-start; flex-direction:column; }.section-heading h2 { font-size:18px; } }
@media (max-width: 420px) { .principles,.metrics,.workflow { grid-template-columns:1fr; }.hero-actions { flex-wrap:wrap; }.hero-actions :deep(.el-button) { margin-left:0; } }
</style>
