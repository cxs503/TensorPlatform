<template>
  <div class="landing">
    <header class="topbar">
      <div class="brand">开放数字化底座</div>
      <button v-if="showSystem" class="gear-btn" @click="goSystem">⚙</button>
    </header>

    <main class="hero">
      <div class="hero-title">开放底座<span class="grad">工业能效</span>AI平台</div>
      <div class="hero-sub">AI + 能源服务 · ODF · 让每一份能源都用在刀刃上</div>

      <div class="hero-stats">
        <StatCard v-if="userStore.hasPermission('datasets:read')" title="数据集" :value="stats.datasets" icon="FolderOpened" :loading="statsLoading" />
        <StatCard v-if="userStore.hasPermission('models:read')" title="模型" :value="stats.models" icon="Cpu" :loading="statsLoading" />
        <StatCard v-if="userStore.hasPermission('training:read')" title="训练任务" :value="stats.trainDone" icon="DataAnalysis" :loading="statsLoading" />
      </div>

      <div class="modules">
        <button v-for="m in landingModules" :key="m.key" class="mc" :class="`mc-${m.theme}`" @click="enter(m)">
          <div class="mc-icon"><el-icon :size="40"><component :is="m.icon" /></el-icon></div>
          <h2>{{ m.title }}</h2>
          <p>{{ m.desc }}</p>
          <div class="mc-meta"><span>{{ countOf(m) }} 个功能</span><span class="mc-arrow">→</span></div>
        </button>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { MODULES, SYSTEM_ENTRY, flattenItems, type Module, type MenuItem } from '@/config/modules'
import { get } from '@/api/legacy'
import StatCard from '@/components/StatCard.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
onMounted(() => {
  if (userStore.isLoggedIn) userStore.fetchUserInfo()
})

const { data: statsData, loading: statsLoading } = useAsyncData(() => get<any>('/dashboard/statistics'))
const landingModuleOrder = ['data', 'model', 'academy', 'app']

// 模块下当前用户可访问的菜单项(隐藏项不算)
function moduleVisibleItems(m: Module): MenuItem[] {
  return flattenItems(m).filter(it => !it.hidden && userStore.canAccessPath(it.path))
}

// 仅保留有可访问项的板块(admin / 未受限用户照常全部可见)
const landingModules = computed(() =>
  landingModuleOrder
    .map(key => MODULES.find(module => module.key === key))
    .filter((m): m is Module => Boolean(m))
    .filter(m => moduleVisibleItems(m).length > 0)
)

// 系统管理只对管理员显示。
const showSystem = computed(() => userStore.isAdmin)

const stats = computed(() => ({
  datasets: statsData.value?.datasets?.total || 0,
  models: statsData.value?.models?.total || 0,
  trainDone: statsData.value?.training_jobs?.completed || 0
}))

function enter(m: Module) {
  // 跳到该板块下首个可访问页, 避免落到无权限的默认入口被守卫弹回
  const first = moduleVisibleItems(m)[0]
  router.push(first ? first.path : m.entry)
}
function goSystem() { router.push(SYSTEM_ENTRY) }
function countOf(m: Module) { return moduleVisibleItems(m).length }
</script>

<style scoped>
.landing { min-height: 100vh; background: #0f172a; color: #e2e8f0; }
.topbar { display: flex; justify-content: space-between; align-items: center; padding: 16px 32px; background: #1a2332; border-bottom: 1px solid rgba(99,102,241,0.1); }
.brand { font-size: 20px; font-weight: 700; color: #fff; }
.gear-btn { background: none; border: 1px solid rgba(99,102,241,0.2); color: #94a3b8; padding: 6px 12px; border-radius: 8px; cursor: pointer; font-size: 18px; }
.hero { max-width: 1100px; margin: 0 auto; padding: 60px 24px 48px; text-align: center; }
.hero-title { font-size: 42px; font-weight: 800; margin-bottom: 12px; color: #f1f5f9; }
.grad { background: linear-gradient(135deg, #6366f1, #10b981); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.hero-sub { color: #94a3b8; font-size: 16px; margin-bottom: 36px; }
.hero-stats { display: flex; gap: 16px; justify-content: center; flex-wrap: wrap; margin-bottom: 48px; }
.modules { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; max-width: 760px; margin: 0 auto; }
.mc { width: 100%; min-width: 0; padding: 36px 26px; border-radius: 14px; border: 1px solid rgba(99,102,241,0.12); background: #1a2332; cursor: pointer; transition: all 0.3s; text-align: left; color: #e2e8f0; font-family: inherit; display: flex; flex-direction: column; }
.mc:hover { transform: translateY(-4px); border-color: rgba(99,102,241,0.35); box-shadow: 0 8px 32px rgba(99,102,241,0.15); }
.mc-icon { margin-bottom: 16px; }
.mc h2 { font-size: 22px; margin: 0 0 8px; }
.mc p { color: #94a3b8; font-size: 13px; margin: 0 0 20px; flex: 1; }
.mc-meta { display: flex; justify-content: space-between; align-items: center; font-size: 13px; color: #6366f1; }
.mc-arrow { font-size: 18px; }
@media (max-width: 700px) {
  .modules { grid-template-columns: 1fr; }
}
</style>
