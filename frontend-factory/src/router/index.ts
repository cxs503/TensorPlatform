import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router'
import { getToken } from '@/api/request'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'),
    meta: { public: true } },
  {
    path: '/',
    component: () => import('@/layouts/ConsoleLayout.vue'),
    children: [
      { path: '', name: 'overview', component: () => import('@/views/FactoryOverviewView.vue'), meta: { title: '平台总览' } },
      { path: 'agents', name: 'agents', component: () => import('@/views/FactoryAgentsView.vue'), meta: { title: '智能体队伍' } },
      { path: 'missions', name: 'missions', component: () => import('@/views/MissionsView.vue'), meta: { title: '任务中心' } },
      { path: 'missions/:id', name: 'mission-detail', component: () => import('@/views/MissionDetailView.vue'), meta: { title: '任务详情' } },
      { path: 'sessions', name: 'sessions', component: () => import('@/views/SessionTimelineView.vue'), meta: { title: '执行追踪' } },
      { path: 'sessions/:id', name: 'session-detail', component: () => import('@/views/SessionTimelineView.vue'), meta: { title: '会话详情' } },
      { path: 'approvals', name: 'approvals', component: () => import('@/views/ApprovalsView.vue'), meta: { title: '交付验收' } },
      { path: 'system', name: 'system', component: () => import('@/views/FactorySettingsView.vue'), meta: { title: '运行配置' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!getToken()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
