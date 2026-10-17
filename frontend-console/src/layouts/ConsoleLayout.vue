<template>
  <div class="tf-shell">
    <header class="tf-topbar">
      <div class="tf-brand" @click="$router.push('/')">
        <span class="tf-brand-mark">TP</span>
        <span class="tf-brand-name">TensorPlatform</span>
        <span class="tf-brand-sub">科研运行台 · HPC + AI Agent</span>
      </div>
      <nav class="tf-nav">
        <router-link to="/">总览</router-link>
        <router-link to="/runs">Run 登记</router-link>
        <router-link to="/approvals">人审队列
          <el-badge v-if="pendingCount > 0" :value="pendingCount" class="tf-nav-badge" />
        </router-link>
        <router-link to="/sessions">Agent 会话</router-link>
        <router-link to="/system">系统健康</router-link>
      </nav>
      <div class="tf-user">
        <span class="tf-user-name">{{ auth.user?.username }}</span>
        <el-tag size="small" effect="plain" round>{{ auth.user?.role }}</el-tag>
        <el-button link size="small" class="tf-logout" @click="auth.logout(); $router.push('/login')">退出</el-button>
      </div>
    </header>
    <main class="tf-main">
      <router-view />
    </main>
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
  } catch { /* 静默 */ }
}

const { start } = usePolling(refreshPending, 10000)
onMounted(() => { refreshPending(); start() })
</script>

<style scoped lang="scss">
.tf-shell { min-height: 100vh; display: flex; flex-direction: column; }

.tf-topbar {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 0 24px;
  height: 56px;
  background: linear-gradient(90deg, var(--tf-navy-900), var(--tf-navy-700));
  color: #eef3f8;
  position: sticky;
  top: 0;
  z-index: 20;
}

.tf-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.tf-brand-mark {
  background: var(--tf-mint-400);
  color: var(--tf-navy-900);
  font-weight: 700;
  border-radius: 6px;
  padding: 2px 7px;
  font-size: 14px;
}
.tf-brand-name { font-weight: 600; font-size: 15px; }
.tf-brand-sub { font-size: 12px; opacity: 0.65; }

.tf-nav {
  display: flex;
  gap: 4px;
  flex: 1;

  a {
    color: #c3cfdd;
    text-decoration: none;
    font-size: 13.5px;
    padding: 6px 14px;
    border-radius: 6px;
    position: relative;

    &.router-link-active { color: var(--tf-mint-400); background: rgba(109, 225, 191, 0.08); }
    &:hover { color: #fff; }
  }
}

.tf-user { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.tf-logout { color: #9fb2c4; }

.tf-main { flex: 1; }
.tf-nav-badge { margin-left: 4px; transform: translateY(-2px); }
</style>
