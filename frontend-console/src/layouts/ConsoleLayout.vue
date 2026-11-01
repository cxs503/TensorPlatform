<template>
  <div class="tf-shell">
    <header class="tf-topbar">
      <button class="tf-brand" type="button" aria-label="返回科研总览" @click="$router.push('/')">
        <span class="tf-brand-mark">T</span>
        <span class="tf-brand-copy">
          <span class="tf-brand-name">TensorPlatform</span>
          <span class="tf-brand-sub">自主科研基础设施 <i /> HPC + AI Agent</span>
        </span>
      </button>

      <nav class="tf-nav" aria-label="主导航">
        <router-link to="/" exact-active-class="is-active">运行总览</router-link>
        <router-link to="/runs">计算结果</router-link>
        <router-link to="/reports">进展报告</router-link>
        <router-link to="/papers">论文生产线</router-link>
        <router-link to="/approvals" class="tf-nav-approval">
          人工审批
          <el-badge v-if="pendingCount > 0" :value="pendingCount" class="tf-nav-badge" />
        </router-link>
        <router-link to="/sessions">Agent 活动</router-link>
        <router-link to="/missions" class="tf-nav-mission">开发任务</router-link>
        <router-link to="/provenance">谱系追溯</router-link>
        <router-link to="/system">系统状态</router-link>
      </nav>

      <div class="tf-user">
        <span class="tf-user-presence" aria-hidden="true" />
        <span class="tf-user-name">{{ auth.user?.username || '研究用户' }}</span>
        <el-tag size="small" effect="dark" round>{{ auth.user?.role || 'VIEWER' }}</el-tag>
        <el-button link size="small" class="tf-logout" @click="auth.logout(); $router.push('/login')">退出</el-button>
      </div>
    </header>
    <main class="tf-main">
      <router-view />
    </main>
    <footer class="tf-footer">
      <span><b>TensorPlatform</b> · Agent-first research operations</span>
      <span>结果以已登记的计算与验证证据为准</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { approvalApi } from '@/api'
import { usePolling } from '@/composables/usePolling'

const auth = useAuthStore()
const pendingCount = ref(0)

async function refreshPending() {
  try {
    const res = await approvalApi.list({ status: 'pending' })
    pendingCount.value = res.total ?? res.items?.length ?? 0
  } catch { /* API unavailable: keep the last known count without inventing a value */ }
}

const { start } = usePolling(refreshPending, 10000)
onMounted(() => { refreshPending(); start() })
</script>

<style scoped lang="scss">
.tf-shell { min-height: 100vh; display: flex; flex-direction: column; }
.tf-topbar {
  display: flex;
  align-items: center;
  gap: clamp(14px, 2vw, 30px);
  min-height: 68px;
  padding: 0 clamp(14px, 2.2vw, 32px);
  background: rgba(11, 23, 48, 0.97);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  color: #eef3f8;
  position: sticky;
  top: 0;
  z-index: 20;
  backdrop-filter: blur(16px);
}
.tf-brand {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 11px;
  border: 0;
  padding: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.tf-brand-mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 11px;
  background: linear-gradient(145deg, #9af0d5, #58c9a9);
  color: var(--tf-navy-900);
  font-size: 19px;
  font-weight: 900;
  box-shadow: 0 4px 14px rgba(117, 228, 194, 0.18);
}
.tf-brand-copy { display: flex; flex-direction: column; gap: 4px; }
.tf-brand-name { font-size: 15px; font-weight: 750; letter-spacing: -0.2px; white-space: nowrap; }
.tf-brand-sub { display: flex; align-items: center; gap: 7px; color: #9fb2c7; font-size: 10px; white-space: nowrap; }
.tf-brand-sub i { width: 3px; height: 3px; border-radius: 50%; background: #75e4c2; }
.tf-nav {
  display: flex;
  align-items: center;
  gap: 3px;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.tf-nav::-webkit-scrollbar { display: none; }
.tf-nav a {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #b8c7d8;
  text-decoration: none;
  font-size: 12.5px;
  font-weight: 550;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 8px;
  transition: color 150ms ease, background 150ms ease, border-color 150ms ease;
}
.tf-nav a.is-active {
  color: #9af0d5;
  background: rgba(117, 228, 194, 0.1);
  border-color: rgba(117, 228, 194, 0.13);
}
.tf-nav a:hover { color: #fff; background: rgba(255, 255, 255, 0.07); }
.tf-user { flex: 0 0 auto; display: flex; align-items: center; gap: 9px; font-size: 12px; }
.tf-user-presence { width: 7px; height: 7px; border-radius: 50%; background: #75e4c2; box-shadow: 0 0 0 3px rgba(117, 228, 194, 0.12); }
.tf-user-name { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #e1eaf3; }
.tf-user :deep(.el-tag) { border: 1px solid rgba(255,255,255,.12); background: rgba(255,255,255,.08); color: #cbd8e6; }
.tf-logout { color: #9fb2c4; }
.tf-logout:hover { color: #fff; }
.tf-main { flex: 1; min-width: 0; }
.tf-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px clamp(14px, 2.2vw, 32px);
  color: #8190a3;
  border-top: 1px solid var(--tf-line-200);
  font-size: 11px;
}
.tf-footer b { color: #4e6077; font-weight: 700; }
.tf-nav-badge { margin-left: 1px; transform: translateY(-1px); }

@media (max-width: 1100px) {
  .tf-topbar { flex-wrap: wrap; gap: 8px 16px; padding-top: 10px; padding-bottom: 9px; }
  .tf-brand { flex: 1 1 220px; }
  .tf-user { margin-left: auto; }
  .tf-nav { order: 3; flex-basis: 100%; }
  .tf-nav a { padding: 8px 10px; }
}
@media (max-width: 560px) {
  .tf-topbar { gap: 8px; }
  .tf-brand { flex-basis: auto; }
  .tf-brand-mark { width: 32px; height: 32px; }
  .tf-brand-name { font-size: 13px; }
  .tf-brand-sub { display: none; }
  .tf-user { gap: 6px; }
  .tf-user-name, .tf-user-presence { display: none; }
  .tf-nav { margin: 0 -2px; }
  .tf-nav a { font-size: 12px; padding: 8px 9px; }
  .tf-footer { align-items: flex-start; flex-direction: column; gap: 5px; }
}
</style>
