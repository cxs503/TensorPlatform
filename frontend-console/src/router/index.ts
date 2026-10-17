import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router'
import { getToken } from '@/api/request'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'),
    meta: { public: true } },
  {
    path: '/',
    component: () => import('@/layouts/ConsoleLayout.vue'),
    children: [
      { path: '', name: 'overview', component: () => import('@/views/OverviewView.vue'),
        meta: { title: '总览' } },
      { path: 'scenario/:key', name: 'scenario', component: () => import('@/views/ScenarioView.vue'),
        meta: { title: '科研场景' } },
      { path: 'runs', name: 'runs', component: () => import('@/views/RunListView.vue'),
        meta: { title: 'Run 登记' } },
      { path: 'runs/:runId', name: 'run-detail', component: () => import('@/views/RunDetailView.vue'),
        meta: { title: 'Run 详情' } },
      { path: 'executions/:id', name: 'execution-detail',
        component: () => import('@/views/ExecutionDetailView.vue'), meta: { title: '执行详情' } },
      { path: 'approvals', name: 'approvals', component: () => import('@/views/ApprovalsView.vue'),
        meta: { title: '人审队列' } },
      { path: 'sessions', name: 'sessions', component: () => import('@/views/SessionTimelineView.vue'),
        meta: { title: 'Agent 会话' } },
      { path: 'sessions/:id', name: 'session-detail',
        component: () => import('@/views/SessionTimelineView.vue'), meta: { title: 'Agent 会话' } },
      { path: 'system', name: 'system', component: () => import('@/views/SystemHealthView.vue'),
        meta: { title: '系统健康' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
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
