<template>
  <div class="page-root"><div class="page-header"><h2>🔐 密钥管理 (KMS)</h2><el-button @click="fetchKeys">刷新</el-button></div>
  <el-alert type="warning" :closable="false" show-icon style="margin-bottom:16px">生产级 KMS / Vault / HSM 尚未配置，内存模拟加密已关闭。接入真实密钥后端后再开放创建、轮换和加解密。</el-alert>
  <el-card><el-table :data="keys" stripe v-loading="loading">
    <el-table-column prop="name" label="名称" width="150"/><el-table-column prop="algorithm" label="算法" width="100"><template #default="{row}"><el-tag size="small">{{row.algorithm}}</el-tag></template></el-table-column>
    <el-table-column prop="purpose" label="用途" width="100"/><el-table-column prop="rotation_days" label="轮换周期" width="100"/><el-table-column prop="created_at" label="创建时间" width="170"/>
    <el-table-column prop="status" label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='active'?'success':'danger'" size="small">{{row.status}}</el-tag></template></el-table-column>
    <el-table-column label="操作" width="120"><template #default><el-button size="small" disabled>未接入</el-button></template></el-table-column>
  </el-table></el-card>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
const loading=ref(false),keys=ref<any[]>([]);
onMounted(()=>{fetchKeys()});
async function fetchKeys(){loading.value=true;try{const d=await get('/kms/keys');keys.value=Array.isArray(d)?d:(d.items||[])}catch(e:any){keys.value=[];ElMessage.error(e?.message||'密钥状态加载失败')}finally{loading.value=false}}
</script>
<style scoped>.page-root{padding:24px}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.page-header h2{margin:0;font-size:20px}</style>
