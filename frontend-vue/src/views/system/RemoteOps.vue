<template>
<div class="pg"><div class="hd"><h2>🛰️ 远程运维</h2><el-button type="primary" @click="fetchOps">刷新</el-button></div>
<el-alert title="远程设备代理尚未配置，模拟执行已关闭；接入真实设备代理后才会开放重启、诊断和远程会话。" type="warning" :closable="false" show-icon class="notice" />
<el-row :gutter="16" class="st"><el-col :span="6"><el-card><el-statistic title="操作总数" :value="ops.length"/></el-card></el-col><el-col :span="6"><el-card><el-statistic title="进行中" :value="ops.filter(o=>o.status==='running'||o.status==='pending').length"/></el-card></el-col><el-col :span="6"><el-card><el-statistic title="失败" :value="ops.filter(o=>o.status==='failed').length"/></el-card></el-col></el-row>
<el-card><el-table :data="ops" stripe v-loading="loading">
  <el-table-column prop="id" label="操作ID" width="170"/><el-table-column prop="device_id" label="目标设备" width="160"/>
  <el-table-column prop="operation_type" label="类型" width="110"><template #default="{row}"><el-tag size="small">{{row.operation_type}}</el-tag></template></el-table-column>
  <el-table-column prop="operator" label="操作人" width="110"/><el-table-column prop="status" label="状态" width="110"><template #default="{row}"><el-tag :type="row.status==='success'?'success':row.status==='failed'?'danger':row.status==='running'?'warning':'info'" size="small">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column label="进度" width="160"><template #default="{row}"><el-progress :percentage="row.progress||0"/></template></el-table-column>
  <el-table-column prop="created_at" label="创建时间" width="180"/><el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip/>
</el-table></el-card></div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
const loading=ref(false),ops=ref<any[]>([]);
onMounted(()=>{fetchOps()});
async function fetchOps(){loading.value=true;try{const d=await get('/remote-ops/operations');ops.value=Array.isArray(d)?d:(d.items||[])}catch(e:any){ElMessage.error(e?.message||'加载失败')}finally{loading.value=false}}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.notice,.st{margin-bottom:20px}</style>
