<template>
  <el-container class="main-layout">
    <!-- 侧边栏 -->
    <el-aside :width="sidebarCollapsed ? '64px' : '220px'" class="sidebar">
      <!-- 返回首页(整个 header 可点) -->
      <div class="sidebar-header" @click="goHome">
        <div class="logo">
          <el-icon :size="22">
            <ArrowLeft v-if="!sidebarCollapsed" />
            <foundation v-else />
          </el-icon>
        </div>
        <transition name="fade">
          <div v-if="!sidebarCollapsed" class="logo-text">
            <div class="title">{{ moduleTitle }}</div>
            <div class="subtitle">← 返回首页</div>
          </div>
        </transition>
      </div>

      <!-- 板块内菜单 -->
      <el-menu
        :default-active="activeMenu"
        :default-openeds="openGroups"
        :collapse="sidebarCollapsed"
        :collapse-transition="false"
        unique-opened
        router
        class="sidebar-menu"
      >
        <!-- 顶层独立项: 如 AI智能体 -->
        <el-menu-item v-for="it in visibleItems(currentModule?.items)" :key="it.path" :index="it.path" :disabled="it.unavailable">
          <el-icon><component :is="it.icon" /></el-icon>
          <template #title>
            <span>{{ it.title }}</span>
            <span v-if="it.unavailable" class="menu-unavailable">不可用</span>
            <span v-else-if="getFeatureMaturity(it.path).level !== 'production'" class="menu-maturity">
              {{ getFeatureMaturity(it.path).shortLabel }}
            </span>
          </template>
        </el-menu-item>

        <!-- 有子分组:用 el-sub-menu(模型工厂 / 应用中心) -->
        <template v-if="currentModule?.groups">
          <el-sub-menu
            v-for="(g, gi) in visibleGroups"
            :key="gi"
            :index="`g-${gi}`"
          >
            <template #title>
              <el-icon><FolderOpened /></el-icon>
              <span>{{ g.title }}</span>
            </template>
            <el-menu-item v-for="it in visibleItems(g.items)" :key="it.path" :index="it.path" :disabled="it.unavailable">
              <el-icon><component :is="it.icon" /></el-icon>
              <template #title>
                <span>{{ it.title }}</span>
                <span v-if="it.unavailable" class="menu-unavailable">不可用</span>
                <span v-else-if="getFeatureMaturity(it.path).level !== 'production'" class="menu-maturity">
                  {{ getFeatureMaturity(it.path).shortLabel }}
                </span>
              </template>
            </el-menu-item>
          </el-sub-menu>
        </template>

        <!-- 扁平项:数据中台(items) 或 系统(SYSTEM_ITEMS) -->
        <template v-else>
          <el-menu-item v-for="it in flatMenu" :key="it.path" :index="it.path" :disabled="it.unavailable">
            <el-icon><component :is="it.icon" /></el-icon>
            <template #title>
              <span>{{ it.title }}</span>
              <span v-if="it.unavailable" class="menu-unavailable">不可用</span>
              <span v-else-if="getFeatureMaturity(it.path).level !== 'production'" class="menu-maturity">
                {{ getFeatureMaturity(it.path).shortLabel }}
              </span>
            </template>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <!-- 主内容区 -->
    <el-container>
      <!-- 顶部栏 -->
      <el-header class="header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="toggleSidebar">
            <Fold v-if="!sidebarCollapsed" />
            <Expand v-else />
          </el-icon>
          <span class="header-title">{{ moduleTitle }}</span>
          <el-tag :type="currentMaturity.tagType" size="small" effect="dark">
            {{ currentMaturity.label }}
          </el-tag>
        </div>
        <div class="header-right">
          <el-tooltip v-if="userStore.isAdmin" content="系统管理" placement="bottom">
            <el-icon class="collapse-btn" @click="goSystem"><Setting /></el-icon>
          </el-tooltip>
          <el-dropdown>
            <span class="user-info">
              <el-avatar :size="32" icon="UserFilled" />
              <span class="username">{{ userStore.userInfo?.username || '用户' }}</span>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item>个人设置</el-dropdown-item>
                <el-dropdown-item divided @click="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 内容区 -->
      <el-main class="main">
        <el-alert
          v-if="currentMaturity.level !== 'production'"
          :title="`${currentMaturity.label} 功能`"
          :description="currentMaturity.description"
          :type="currentMaturity.level === 'demo' ? 'info' : 'warning'"
          :closable="false"
          show-icon
          class="maturity-alert"
        />
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import {
  MODULES,
  SYSTEM_ITEMS,
  SYSTEM_ENTRY,
  resolveModuleKey,
  getModule
} from '@/config/modules'
import { getFeatureMaturity } from '@/config/maturity'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const mobileMedia = window.matchMedia('(max-width: 768px)')
const sidebarCollapsed = ref(mobileMedia.matches)
const syncMobileSidebar = (event: MediaQueryListEvent) => {
  sidebarCollapsed.value = event.matches
}
onMounted(() => {
  mobileMedia.addEventListener('change', syncMobileSidebar)
  // 刷新用户信息(含 features), 使管理员对权限的变更及时生效
  if (userStore.isLoggedIn) userStore.fetchUserInfo()
})
onBeforeUnmount(() => mobileMedia.removeEventListener('change', syncMobileSidebar))

const activeMenu = computed(() => route.path)
const currentMaturity = computed(() => getFeatureMaturity(route.path))

const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

// 当前板块(由路由路径推导)
const currentKey = computed(() => resolveModuleKey(route.path))
const currentModule = computed(() =>
  currentKey.value ? getModule(currentKey.value) : undefined
)
const isSystem = computed(() => currentKey.value === 'system')
function visibleItems<T extends { hidden?: boolean; adminOnly?: boolean; path: string }>(items?: T[]): T[] {
  return (items || []).filter(
    (item) => !item.hidden && (!item.adminOnly || userStore.isAdmin) && userStore.canAccessPath(item.path)
  )
}

const visibleGroups = computed(() =>
  (currentModule.value?.groups || [])
    .map(group => ({ ...group, items: visibleItems(group.items) }))
    .filter(group => group.items.length > 0)
)

const moduleTitle = computed(() => {
  if (isSystem.value) return '系统管理'
  return currentModule.value?.title || '开放数字化底座 ODF'
})

// 扁平菜单(无 groups 的板块用):数据中台 items 或 系统 SYSTEM_ITEMS
const flatMenu = computed(() => {
  if (isSystem.value) return visibleItems(SYSTEM_ITEMS)
  return visibleItems(currentModule.value?.items)
})

// 默认展开含当前路由的子分组
const openGroups = computed(() => {
  const groups = visibleGroups.value
  if (!groups) return []
  const idx = groups.findIndex((g) =>
    g.items.some((it) => route.path === it.path || route.path.startsWith(it.path + '/'))
  )
  return idx >= 0 ? [`g-${idx}`] : []
})

const goHome = () => {
  router.push('/')
}

const goSystem = () => {
  router.push(SYSTEM_ENTRY)
}

const logout = () => {
  userStore.logout()
  router.push('/')
}

// 避免 MODULES 未被 tree-shake 警告(实际通过 currentModule 间接使用)
void MODULES
</script>

<style scoped>
.main-layout {
  height: 100vh;
  max-width: 100vw;
  overflow: hidden;
}
.main-layout > :deep(.el-container) {
  min-width: 0;
}
.sidebar {
  background: linear-gradient(180deg, #1a1f36 0%, #252b48 100%);
  transition: width 0.3s;
}
.sidebar-header {
  height: 64px;
  display: flex;
  align-items: center;
  padding: 0 16px;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  cursor: pointer;
  transition: background 0.2s;
}
.sidebar-header:hover {
  background: rgba(255,255,255,0.06);
}
.logo {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #818cf8;
}
.logo-text {
  margin-left: 12px;
}
.title {
  color: #fff;
  font-size: 16px;
  font-weight: bold;
}
.subtitle {
  color: rgba(255,255,255,0.5);
  font-size: 12px;
}
.sidebar-menu {
  border-right: none;
  background: transparent;
}
.sidebar-menu:not(.el-menu--collapse) {
  width: 220px;
}
:deep(.el-menu) {
  background: transparent;
}
:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  color: rgba(255,255,255,0.7);
}
:deep(.el-menu-item:hover),
:deep(.el-sub-menu__title:hover) {
  background: rgba(255,255,255,0.1);
}
:deep(.el-menu-item.is-active) {
  color: #409eff;
  background: rgba(64,158,255,0.1);
}
:deep(.el-menu-item.is-disabled) {
  opacity: 0.72;
  cursor: not-allowed;
}
.menu-unavailable {
  margin-left: auto;
  padding: 0 7px;
  height: 19px;
  line-height: 19px;
  border-radius: 10px;
  border: 1px solid rgba(245, 158, 11, 0.45);
  background: rgba(245, 158, 11, 0.12);
  color: #fbbf24;
  font-size: 10px;
}
.menu-maturity {
  margin-left: auto;
  min-width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.72);
  font-size: 10px;
}
.maturity-alert {
  margin-bottom: 12px;
}
.header {
  background: var(--bg2);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.3);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--tx);
}
.collapse-btn {
  font-size: 20px;
  cursor: pointer;
  color: var(--tx2);
}
.collapse-btn:hover {
  color: var(--a2);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.user-info {
  display: flex;
  align-items: center;
  cursor: pointer;
}
.username {
  margin-left: 8px;
  color: var(--tx);
}
.main {
  background: var(--bg);
  padding: 20px;
  min-width: 0;
  overflow-x: auto;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 768px) {
  .header {
    padding: 0 10px;
  }
  .header-left,
  .header-right {
    gap: 8px;
    min-width: 0;
  }
  .header-title,
  .username {
    display: none;
  }
  .main {
    padding: 10px;
  }
}
</style>
