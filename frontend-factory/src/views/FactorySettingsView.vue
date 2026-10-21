<template>
  <div class="tf-page">
    <HeroPanel><template #title>运行配置</template><template #sub>项目和角色由部署配置管理，任务执行状态以实时心跳与验收记录为准。</template></HeroPanel>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="tf-card" v-if="config">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="名称">{{ config.name }}</el-descriptions-item>
        <el-descriptions-item label="允许项目">{{ config.projects.map((p: any) => p.name).join('、') }}</el-descriptions-item>
        <el-descriptions-item label="交付验收">必须由管理员验收</el-descriptions-item>
        <el-descriptions-item label="执行隔离">GPT 生成候选补丁，固定断网容器运行质量回归测试</el-descriptions-item>
        <el-descriptions-item label="自主执行器">{{ residentSessions.filter((s: any) => s.online && s.enabled).length }} 个常驻 session 在线；详细执行结果见任务与验收记录</el-descriptions-item>
      </el-descriptions>
    </div>
    <div class="tf-card" v-if="model">
      <div class="tf-card-title">GPT 模型接入</div>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="接入方式">Codex · Responses API</el-descriptions-item>
        <el-descriptions-item label="模型">{{ model.model || '尚未指定' }}</el-descriptions-item>
        <el-descriptions-item label="配置状态">{{ model.configured ? '配置已就绪，连接以实际测试为准' : '未就绪：' + model.missing.join('、') }}</el-descriptions-item>
        <el-descriptions-item label="凭据">使用服务器 Codex 登录，页面不接收或显示密钥</el-descriptions-item>
        <el-descriptions-item label="可用功能">连接测试、任务规划、常驻源码审查与受限质量修复；成果需验收</el-descriptions-item>
      </el-descriptions>
      <el-button v-if="auth.isAdmin" style="margin-top: 16px" :disabled="!model.configured" :loading="testing" @click="testConnection">测试 GPT 连接</el-button>
      <p v-if="testResult" role="status">{{ testResult }}</p>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { factoryApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const config = ref<any>(null), error = ref('')
const residentSessions = ref<any[]>([])
const model = ref<any>(null), testing = ref(false), testResult = ref('')
async function testConnection() {
  testing.value = true; testResult.value = ''
  try { const result = await factoryApi.testModel(); testResult.value = `${result.model} 连接测试通过；各常驻任务的执行情况请查看任务记录。` }
  catch (e: any) { testResult.value = `连接测试失败：${e.message}` }
  finally { testing.value = false }
}
onMounted(async () => { try { [config.value, model.value] = await Promise.all([factoryApi.config(), factoryApi.model()]); residentSessions.value = (await factoryApi.agents()).sessions } catch (e: any) { error.value = e.message } })
</script>
