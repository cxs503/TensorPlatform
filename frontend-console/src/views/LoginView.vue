<template>
  <div class="tf-login">
    <div class="tf-login-panel">
      <div class="tf-login-brand">
        <span class="tf-login-mark">TP</span>
        <div>
          <div class="tf-login-title">TensorPlatform 科研运行台</div>
          <div class="tf-login-sub">HPC 求解器矩阵 × AI Agent fail-closed 科研循环</div>
        </div>
      </div>
      <el-form @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名" size="large" autofocus>
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="密码" size="large" show-password @keyup.enter="submit">
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-button class="tf-login-btn" type="primary" size="large" :loading="loading" @click="submit">
          进入运行台
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { ensureToken } from '@/api/request'

const username = ref('')
const password = ref('')
const loading = ref(false)
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

onMounted(async () => {
  // 免登录（owner 2026-11-01）：auto-login 可用则直接进入；表单保留作回落
  if (await ensureToken()) {
    router.push((route.query.redirect as string) || '/')
  }
})

async function submit() {
  if (!username.value || !password.value) {
    ElMessage.warning('请输入用户名与密码')
    return
  }
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push((route.query.redirect as string) || '/')
  } catch (e: any) {
    ElMessage.error(e.message || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
.tf-login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(ellipse at 20% 10%, rgba(22, 76, 97, 0.55), transparent 55%),
    linear-gradient(135deg, var(--tf-navy-900), var(--tf-navy-700) 60%, var(--tf-teal-600));
}

.tf-login-panel {
  width: 400px;
  background: rgba(255, 255, 255, 0.97);
  border-radius: 14px;
  padding: 36px 34px 30px;
  box-shadow: 0 18px 60px rgba(6, 16, 32, 0.45);
}

.tf-login-brand { display: flex; gap: 14px; align-items: center; margin-bottom: 26px; }
.tf-login-mark {
  background: var(--tf-navy-900);
  color: var(--tf-mint-400);
  font-weight: 700;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 18px;
}
.tf-login-title { font-size: 17px; font-weight: 600; color: var(--tf-navy-700); }
.tf-login-sub { font-size: 12px; color: var(--tf-ink-500); margin-top: 4px; }
.tf-login-btn { width: 100%; margin-top: 6px; }
</style>
