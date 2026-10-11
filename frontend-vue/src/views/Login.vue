<template>
  <div class="login">
    <div class="login-card">
      <div class="login-brand">
        <div class="login-title">开放底座<span class="grad">工业能效</span>AI平台</div>
        <div class="login-sub">AI + 能源服务 · ODF</div>
      </div>

      <form class="login-form" @submit.prevent="onSubmit">
        <div class="field">
          <label>用户名</label>
          <el-input v-model="form.username" placeholder="请输入用户名" size="large" :prefix-icon="User" autocomplete="username" />
        </div>
        <div class="field">
          <label>密码</label>
          <el-input v-model="form.password" type="password" placeholder="请输入密码" size="large" :prefix-icon="Lock" show-password autocomplete="current-password" />
        </div>

        <div v-if="error" class="login-error">{{ error }}</div>

        <el-button type="primary" size="large" :loading="loading" native-type="submit" class="login-btn">登录</el-button>

      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const router = useRouter()
const route = useRoute()

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const error = ref('')

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    const r = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: form.username, password: form.password })
    })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) {
      error.value = data.detail || data.message || `登录失败 (${r.status})`
      return
    }
    const token = data.access_token
    if (!token) {
      error.value = '登录响应缺少 access_token'
      return
    }
    localStorage.setItem('foundation_token', token)
    localStorage.setItem('foundation_access_token', token)
    if (data.refresh_token) localStorage.setItem('foundation_refresh_token', data.refresh_token)
    if (data.user) localStorage.setItem('foundation_user', JSON.stringify(data.user))
    ElMessage.success('登录成功')
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e: any) {
    error.value = e?.message || '网络错误,请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
  color: #e2e8f0;
}
.login-card {
  width: 380px;
  background: #1a2332;
  border: 1px solid rgba(99, 102, 241, 0.2);
  border-radius: 16px;
  padding: 40px 36px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}
.login-brand { text-align: center; margin-bottom: 32px; }
.login-title { font-size: 22px; font-weight: 700; color: #fff; }
.login-sub { font-size: 13px; color: #94a3b8; margin-top: 6px; }
.grad { background: linear-gradient(90deg, #6366f1, #06b6d4); -webkit-background-clip: text; background-clip: text; color: transparent; }
.login-form .field { margin-bottom: 18px; }
.login-form label { display: block; font-size: 13px; color: #cbd5e1; margin-bottom: 6px; }
.login-btn { width: 100%; margin-top: 8px; }
.login-error { color: #f87171; font-size: 13px; margin-bottom: 12px; }
</style>
